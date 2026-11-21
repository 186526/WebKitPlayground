#include "bundle_lookup.h"

#include "dyld_lookup.h"
#include "textkit.h"
#include "types.h"

/* Foundation is already loaded in every UIKit host process, so its
   Objective-C accessors are resolved at run time from the loaded image. */

static void *foundation_image(void) {
  return library_find("/System/Library/Frameworks/Foundation.framework/Foundation");
}

static void *bundle_at_path(const char *path) {
  void *foundation = foundation_image();
  if (foundation == NULL) {
    return NULL;
  }
  void *(*bundleWithPath)(const char *) =
      (void *(*)(const char *))symbol_find(foundation, "NSBundle.bundleWithPath:");
  return bundleWithPath == NULL ? NULL : bundleWithPath(path);
}

static bool copy_bundle_identifier(void *bundle, char *out, usize capacity) {
  if (bundle == NULL || capacity == 0) {
    return false;
  }
  out[0] = 0;
  void *foundation = foundation_image();
  if (foundation == NULL) {
    return false;
  }
  void *(*bundleIdentifier)(void *) =
      (void *(*)(void *))symbol_find(foundation, "NSBundle.bundleIdentifier");
  char *(*utf8_text)(void *) = (char *(*)(void *))symbol_find(foundation, "NSString.UTF8String");
  if (bundleIdentifier == NULL || utf8_text == NULL) {
    return false;
  }
  void *identifier = bundleIdentifier(bundle);
  if (identifier == NULL) {
    return false;
  }
  const char *text = utf8_text(identifier);
  if (text == NULL) {
    return false;
  }
  text_copy(out, text, capacity);
  return text_length(out) > 0;
}

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  return copy_bundle_identifier(bundle_at_path(bundlePath), out, capacity);
}
