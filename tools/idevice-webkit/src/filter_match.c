#include "filter_match.h"

#include "bundle_lookup.h"
#include "dyld_lookup.h"
#include "dylib_report.h"
#include "textkit.h"
#include "types.h"

/* The filter is read through the resolver bundle_lookup.c already uses: it
   answers the Foundation selectors, and the CF names below because
   CoreFoundation is loaded with Foundation. */

static void *filter_dictionary(const char *filterPath) {
  void *(*dictionaryWithContentsOfFile)(const char *) =
      (void *(*)(const char *))foundation_symbol("NSDictionary.dictionaryWithContentsOfFile:");
  return dictionaryWithContentsOfFile == NULL ? NULL : dictionaryWithContentsOfFile(filterPath);
}

/* The key objects are the strings CFStringCreateWithCString would build; the
   dictionary answers them by their bytes. */

static void *value_for_key(void *dictionary, const char *key) {
  void *(*objectForKey)(void *, void *) =
      (void *(*)(void *, void *))foundation_symbol("NSDictionary.objectForKey:");
  void *(*stringWithUTF8String)(const char *) =
      (void *(*)(const char *))foundation_symbol("NSString.stringWithUTF8String:");
  if (dictionary == NULL || objectForKey == NULL || stringWithUTF8String == NULL) {
    return NULL;
  }
  void *keyObject = stringWithUTF8String(key);
  return keyObject == NULL ? NULL : objectForKey(dictionary, keyObject);
}

/* The array is walked from the first element to the last. libinjector tries
   every element of the list it is given, and the filter lists four bundles, so
   a process named by the fourth must not read as a process named by none. */

typedef bool (*element_predicate)(void *element, void *context);

static bool any_element(void *array, element_predicate matches, void *context) {
  i32 (*count)(void *) = (i32 (*)(void *))foundation_symbol("NSArray.count");
  void *(*objectAtIndex)(void *, usize) =
      (void *(*)(void *, usize))foundation_symbol("NSArray.objectAtIndex:");
  if (array == NULL || count == NULL || objectAtIndex == NULL) {
    return false;
  }
  i32 elements = count(array);
  for (i32 index = 0; index < elements; index++) {
    if (matches(objectAtIndex(array, (usize)index), context)) {
      return true;
    }
  }
  return false;
}

static bool element_text(void *element, char *out, usize capacity) {
  void *(*utf8Text)(void *) = (void *(*)(void *))foundation_symbol("NSString.UTF8String");
  const char *text = element == NULL || utf8Text == NULL ? NULL : utf8Text(element);
  if (text == NULL || text[0] == 0 || text_length(text) >= capacity) {
    return false;
  }
  text_copy(out, text, capacity);
  return true;
}

/* The class branch is objc_getClass with the element as its C string: a class
   that exists in this process answers with its name, and the runtime answers
   nil for a name it does not know. objc_getClass is in libobjc, which the host
   loads on its own, so it is looked up in the global table rather than in
   Foundation. */

typedef struct class_lookup {
  void *(*get_class)(const char *);
} class_lookup;

static bool class_present(void *element, void *context) {
  class_lookup *lookup = context;
  char name[bundle_identifier_capacity];
  return element_text(element, name, sizeof(name)) && lookup->get_class(name) != NULL;
}

static bool class_matches(void *filter) {
  void *array = value_for_key(filter, "Classes");
  if (array == NULL) {
    return false;
  }
  void *(*get_class)(const char *) =
      (void *(*)(const char *))symbol_find_global("objc_getClass");
  if (get_class == NULL) {
    return false;
  }
  class_lookup lookup = {get_class};
  return any_element(array, class_present, &lookup);
}

/* The bundle branch is CFBundleGetBundleWithIdentifier, first with the bytes
   of the element and then with a lowercased copy, as libinjector does. A
   system application such as MobileSafari is not in the LaunchServices
   registry this function reads, so only the class branch can match it.

   CFStringCreateMutableCopy takes three arguments: the allocator to use, a
   capacity the copy may grow to, and the string to copy. CFAllocatorGetDefault
   is itself a function, so its return value is the allocator to hand over. */

typedef struct bundle_lookup {
  void *(*bundle_for_identifier)(void *);
  void *allocator;
  void *(*mutable_copy)(void *, i64, void *);
  void (*lowercase)(void *, void *);
  void (*release_object)(void *);
  i64 (*length_of)(void *);
} bundle_lookup;

static bool bundle_named(void *identifier, void *context) {
  bundle_lookup *lookup = context;
  if (lookup->bundle_for_identifier(identifier) != NULL) {
    return true;
  }
  i64 length = lookup->length_of(identifier);
  void *copy = lookup->mutable_copy(lookup->allocator, length + 1, identifier);
  if (copy == NULL) {
    return false;
  }
  lookup->lowercase(copy, NULL);
  bool matched = lookup->bundle_for_identifier(copy) != NULL;
  lookup->release_object(copy);
  return matched;
}

static bool bundle_matches(void *filter) {
  void *array = value_for_key(filter, "Bundles");
  if (array == NULL) {
    return false;
  }
  bundle_lookup lookup;
  lookup.bundle_for_identifier =
      (void *(*)(void *))foundation_symbol("CFBundleGetBundleWithIdentifier");
  void *(*allocator_default)(void) =
      (void *(*)(void))foundation_symbol("CFAllocatorGetDefault");
  lookup.mutable_copy =
      (void *(*)(void *, i64, void *))foundation_symbol("CFStringCreateMutableCopy");
  lookup.lowercase = (void (*)(void *, void *))foundation_symbol("CFStringLowercase");
  lookup.release_object = (void (*)(void *))foundation_symbol("CFRelease");
  lookup.length_of = (i64 (*)(void *))foundation_symbol("CFStringGetLength");
  if (lookup.bundle_for_identifier == NULL || allocator_default == NULL ||
      lookup.mutable_copy == NULL || lookup.lowercase == NULL ||
      lookup.release_object == NULL || lookup.length_of == NULL) {
    return false;
  }
  lookup.allocator = allocator_default();
  return any_element(array, bundle_named, &lookup);
}

void filter_match_report(const char *filterPath) {
  void *filter = filter_dictionary(filterPath);
  if (class_matches(filter)) {
    dylib_report_set_match(match_classes);
  } else if (bundle_matches(filter)) {
    dylib_report_set_match(match_bundles);
  } else {
    dylib_report_set_match(match_none);
  }
}
