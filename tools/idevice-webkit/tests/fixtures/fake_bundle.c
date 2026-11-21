#include "fake_bundle.h"

#include "bundle_lookup.h"

#include "textkit.h"
#include "types.h"

/* Replaces the Info.plist lookup with a single path the test decides to make
   present or absent. */

const char *fake_manifest_path = 0;

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  if (fake_manifest_path == 0 || !text_equal(bundlePath, fake_manifest_path)) {
    return false;
  }
  text_copy(out, "com.example.demo", capacity);
  return true;
}
