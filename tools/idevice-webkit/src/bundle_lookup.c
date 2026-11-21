#include "bundle_lookup.h"

#include "objc_runtime.h"
#include "textkit.h"
#include "types.h"

/* A bundle identifier is read by sending messages to Foundation objects:
   NSBundle for a directory holding an Info.plist, NSDictionary for a manifest
   file. The class objects and selectors are looked up once per call rather
   than kept, because a dylib that is loaded and unloaded in one process would
   otherwise hold a selector the runtime has released.

   Every selector name below is a method name, not a C symbol. The earlier
   version of this file resolved them with dlsym, which answers only C symbols
   and therefore answered NULL for all of them: the host could never be
   identified and every process was reported as an unmatched one. */

static void *class_object(const char *name) {
  return objc_class(name);
}

static void *selector(const char *name) {
  return objc_selector(name);
}

/* The value is an NSString, and UTF8String is the one method read from it.
   The bytes belong to the object, so the text is copied out before the
   autoreleased objects of the lookup go away. */

static bool copy_text_of(void *object, char *out, usize capacity) {
  if (object == NULL || capacity == 0) {
    return false;
  }
  out[0] = 0;
  const char *text = objc_send_text(object, selector("UTF8String"));
  if (text == NULL) {
    return false;
  }
  text_copy(out, text, capacity);
  return text_length(out) > 0;
}

/* NSBundle.bundleWithPath: reads the Info.plist of a directory. A directory
   without one is not a bundle and answers nil. */

static void *bundle_at_path(const char *path) {
  void *bundleClass = class_object("NSBundle");
  return objc_send_object_text(bundleClass, selector("bundleWithPath:"), path);
}

static void *bundle_identifier_object(void *bundle) {
  return objc_send_object(bundle, selector("bundleIdentifier"));
}

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  return copy_text_of(bundle_identifier_object(bundle_at_path(bundlePath)), out, capacity);
}

/* A manifest is a plist file, not a directory, and NSBundle reads only the
   Info.plist of a directory. Its identifier is therefore read from its own
   CFBundleIdentifier key. The key is built as an NSString, because
   objectForKey: compares objects and not C strings. */

void *dictionary_for_file(const char *path) {
  void *dictionaryClass = class_object("NSDictionary");
  return objc_send_object_text(dictionaryClass, selector("dictionaryWithContentsOfFile:"), path);
}

void *object_for_key(void *dictionary, const char *key) {
  if (dictionary == NULL) {
    return NULL;
  }
  void *stringClass = class_object("NSString");
  void *keyObject = objc_send_object_text(stringClass, selector("stringWithUTF8String:"), key);
  return objc_send_object_object(dictionary, selector("objectForKey:"), keyObject);
}

bool plist_file_identifier(const char *plistPath, char *out, usize capacity) {
  return copy_text_of(object_for_key(dictionary_for_file(plistPath), "CFBundleIdentifier"), out,
                      capacity);
}
