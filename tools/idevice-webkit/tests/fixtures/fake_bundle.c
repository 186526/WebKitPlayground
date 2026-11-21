#include "fake_bundle.h"

#include "bundle_lookup.h"

#include "textkit.h"
#include "types.h"

/* Replaces the Info.plist lookup with a table the test fills in, so a path can
   be a bundle with one identifier, a bundle with another identifier, or no
   bundle at all. The table keeps its own copies of both strings, so an entry
   stays the same C string from one read to the next, as an Info.plist value
   does, even when the test passes a stack buffer. */

#define fake_bundle_text 2048

static char path_text[fake_bundle_entries][fake_bundle_text];
static char identifier_text[fake_bundle_entries][fake_bundle_text];

const char *fake_bundle_paths[fake_bundle_entries];
const char *fake_bundle_identifiers[fake_bundle_entries];

void fake_bundles_reset(void) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    fake_bundle_paths[index] = NULL;
    fake_bundle_identifiers[index] = NULL;
  }
}

void fake_bundle_add(const char *path, const char *identifier) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    if (fake_bundle_paths[index] == NULL) {
      text_copy(path_text[index], path, fake_bundle_text);
      text_copy(identifier_text[index], identifier, fake_bundle_text);
      fake_bundle_paths[index] = path_text[index];
      fake_bundle_identifiers[index] = identifier_text[index];
      return;
    }
  }
}

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    if (fake_bundle_paths[index] != NULL && text_equal(bundlePath, fake_bundle_paths[index])) {
      text_copy(out, fake_bundle_identifiers[index], capacity);
      return true;
    }
  }
  return false;
}
