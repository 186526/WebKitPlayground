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

static void *first_element(void *array) {
  i32 (*count)(void *) = (i32 (*)(void *))foundation_symbol("NSArray.count");
  void *(*objectAtIndex)(void *, usize) =
      (void *(*)(void *, usize))foundation_symbol("NSArray.objectAtIndex:");
  if (array == NULL || count == NULL || objectAtIndex == NULL || count(array) < 1) {
    return NULL;
  }
  return objectAtIndex(array, 0);
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
   nil for a name it does not know. */

static bool class_matches(void *filter, char *scratch, usize capacity) {
  void *(*get_class)(const char *) =
      (void *(*)(const char *))symbol_find(NULL, "objc_getClass");
  if (get_class == NULL) {
    return false;
  }
  return element_text(first_element(value_for_key(filter, "Classes")), scratch, capacity) &&
         get_class(scratch) != NULL;
}

/* The bundle branch is CFBundleGetBundleWithIdentifier, first with the bytes
   of the element and then with a lowercased copy, as libinjector does. A
   system application such as MobileSafari is not in the LaunchServices
   registry this function reads, so only the class branch can match it. */

static bool bundle_matches(void *filter, char *scratch, usize capacity) {
  void *(*bundle_for_identifier)(void *) =
      (void *(*)(void *))foundation_symbol("CFBundleGetBundleWithIdentifier");
  void *(*mutable_copy)(void *(*allocator)(void), void *) = (void *(*)(void *(*)(void), void *))
      foundation_symbol("CFStringCreateMutableCopy");
  void (*lowercase)(void *, void *) =
      (void (*)(void *, void *))foundation_symbol("CFStringLowercase");
  void (*release_object)(void *) = (void (*)(void *))foundation_symbol("CFRelease");
  void *(*allocator)(void) = (void *(*)(void))foundation_symbol("CFAllocatorGetDefault");
  if (bundle_for_identifier == NULL || mutable_copy == NULL || lowercase == NULL ||
      release_object == NULL || allocator == NULL) {
    return false;
  }
  void *element = first_element(value_for_key(filter, "Bundles"));
  if (!element_text(element, scratch, capacity)) {
    return false;
  }
  if (bundle_for_identifier(element) != NULL) {
    return true;
  }
  void *lowered = mutable_copy(allocator, element);
  if (lowered == NULL) {
    return false;
  }
  lowercase(lowered, NULL);
  bool matched = bundle_for_identifier(lowered) != NULL;
  release_object(lowered);
  return matched;
}

void filter_match_report(const char *filterPath) {
  char scratch[bundle_identifier_capacity];
  void *filter = filter_dictionary(filterPath);
  if (class_matches(filter, scratch, sizeof(scratch))) {
    dylib_report_set_match(match_classes);
  } else if (bundle_matches(filter, scratch, sizeof(scratch))) {
    dylib_report_set_match(match_bundles);
  } else {
    dylib_report_set_match(match_none);
  }
}
