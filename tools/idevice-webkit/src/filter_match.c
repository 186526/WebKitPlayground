#include "filter_match.h"

#include "bundle_lookup.h"
#include "dyld_lookup.h"
#include "dylib_report.h"
#include "objc_runtime.h"
#include "textkit.h"
#include "types.h"

/* The filter is a plist file, and its Objective-C parts are read through the
   runtime: NSDictionary for the file, NSArray for the two lists, NSString for
   their text, and objc_getClass for the class branch. Those are method names
   and not C symbols, so dlsym answers NULL for every one of them: the earlier
   version resolved them that way and every process read as matched by none.

   The CF calls below are ordinary C functions that CoreFoundation exports, so
   they are looked up in the process's own symbol table rather than through a
   message or through the Foundation handle. */

static void *value_for_key(void *dictionary, const char *key) {
  return object_for_key(dictionary, key);
}

/* The array is walked from the first element to the last. libinjector tries
   every element of the list it is given, and the filter lists four bundles, so
   a process named by the fourth must not read as a process named by none.
   count answers NSUInteger, which is 64 bits on arm64; reading it into a
   narrower integer would leave the upper half undefined. */

typedef bool (*element_predicate)(void *element, void *context);

static bool any_element(void *array, element_predicate matches, void *context) {
  if (array == NULL) {
    return false;
  }
  void *countSelector = objc_selector("count");
  void *elementSelector = objc_selector("objectAtIndex:");
  u64 elements = objc_send_count(array, countSelector);
  for (u64 index = 0; index < elements; index++) {
    void *element = objc_send_object_index(array, elementSelector, index);
    if (matches(element, context)) {
      return true;
    }
  }
  return false;
}

static bool element_text(void *element, char *out, usize capacity) {
  const char *text =
      element == NULL ? NULL : objc_send_text(element, objc_selector("UTF8String"));
  if (text == NULL || text[0] == 0 || text_length(text) >= capacity) {
    return false;
  }
  text_copy(out, text, capacity);
  return true;
}

/* The class branch is objc_getClass with the element as its C string: a class
   that exists in this process answers with the class object, and the runtime
   answers nil for a name it does not know. objc_getClass is in libobjc, which
   the host loads on its own, so objc_class reaches it through the global
   symbol table. */

typedef struct class_probe {
  void *(*get_class)(const char *);
} class_probe;

static bool class_present(void *element, void *context) {
  class_probe *lookup = context;
  char name[bundle_identifier_capacity];
  return element_text(element, name, sizeof(name)) && lookup->get_class(name) != NULL;
}

static bool class_matches(void *filter) {
  void *array = value_for_key(filter, "Classes");
  if (array == NULL) {
    return false;
  }
  class_probe lookup = {objc_class};
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
      (void *(*)(void *))symbol_find_global("CFBundleGetBundleWithIdentifier");
  void *(*allocator_default)(void) =
      (void *(*)(void))symbol_find_global("CFAllocatorGetDefault");
  lookup.mutable_copy =
      (void *(*)(void *, i64, void *))symbol_find_global("CFStringCreateMutableCopy");
  lookup.lowercase = (void (*)(void *, void *))symbol_find_global("CFStringLowercase");
  lookup.release_object = (void (*)(void *))symbol_find_global("CFRelease");
  lookup.length_of = (i64 (*)(void *))symbol_find_global("CFStringGetLength");
  if (lookup.bundle_for_identifier == NULL || allocator_default == NULL ||
      lookup.mutable_copy == NULL || lookup.lowercase == NULL ||
      lookup.release_object == NULL || lookup.length_of == NULL) {
    return false;
  }
  lookup.allocator = allocator_default();
  return any_element(array, bundle_named, &lookup);
}

void filter_match_report(const char *filterPath) {
  void *filter = dictionary_for_file(filterPath);
  if (class_matches(filter)) {
    dylib_report_set_match(match_classes);
  } else if (bundle_matches(filter)) {
    dylib_report_set_match(match_bundles);
  } else {
    dylib_report_set_match(match_none);
  }
}
