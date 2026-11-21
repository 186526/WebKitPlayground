#include "bundle_lookup.h"

#include "dyld_lookup.h"
#include "textkit.h"
#include "types.h"

/* Foundation is already loaded in every UIKit host process, so its
   Objective-C accessors are resolved at run time from the loaded image. */

static void *foundation_image(void) {
  return library_find("/System/Library/Frameworks/Foundation.framework/Foundation");
}

static void *foundation_symbol(const char *name) {
  void *foundation = foundation_image();
  return foundation == NULL ? NULL : symbol_find(foundation, name);
}

/* Every value below travels as an Objective-C object whose one method used
   here is UTF8String, so the same function reads them. */

typedef char *(*utf8_method)(void *object);

static void *dictionary_for_file(const char *path) {
  void *(*dictionaryWithContentsOfFile)(const char *) =
      (void *(*)(const char *))foundation_symbol("NSDictionary.dictionaryWithContentsOfFile:");
  return dictionaryWithContentsOfFile == NULL ? NULL : dictionaryWithContentsOfFile(path);
}

static void *object_for_key(void *dictionary, const char *key) {
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

static bool copy_text_of(void *object, char *out, usize capacity) {
  if (object == NULL || capacity == 0) {
    return false;
  }
  out[0] = 0;
  utf8_method utf8Text = (utf8_method)foundation_symbol("NSString.UTF8String");
  if (utf8Text == NULL) {
    return false;
  }
  const char *text = utf8Text(object);
  if (text == NULL) {
    return false;
  }
  text_copy(out, text, capacity);
  return text_length(out) > 0;
}

/* NSBundle.bundleWithPath: reads the Info.plist of a directory. A directory
   without one is not a bundle and answers nothing. */

static void *bundle_at_path(const char *path) {
  void *(*bundleWithPath)(const char *) =
      (void *(*)(const char *))foundation_symbol("NSBundle.bundleWithPath:");
  return bundleWithPath == NULL ? NULL : bundleWithPath(path);
}

static void *bundle_identifier_object(void *bundle) {
  if (bundle == NULL) {
    return NULL;
  }
  void *(*bundleIdentifier)(void *) =
      (void *(*)(void *))foundation_symbol("NSBundle.bundleIdentifier");
  return bundleIdentifier == NULL ? NULL : bundleIdentifier(bundle);
}

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  return copy_text_of(bundle_identifier_object(bundle_at_path(bundlePath)), out, capacity);
}

/* A manifest is a plist file, not a directory, and NSBundle reads only the
   Info.plist of a directory. Its identifier is therefore read from its own
   CFBundleIdentifier key. */

bool plist_file_identifier(const char *plistPath, char *out, usize capacity) {
  return copy_text_of(object_for_key(dictionary_for_file(plistPath), "CFBundleIdentifier"), out,
                      capacity);
}
