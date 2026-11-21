#include "fake_bundle.h"

#include "bundle_lookup.h"

#include "textkit.h"
#include "types.h"

/* Replaces the two Foundation lookups with a table the test fills in, so a
   path can be a bundle with one identifier, a bundle with another identifier,
   or no bundle at all. The table keeps its own copies of both strings, so an
   entry stays the same C string from one read to the next, as an Info.plist
   value does, even when the test passes a stack buffer.

   The table answers the way Foundation does: a bundle is a directory holding
   an Info.plist, so a path inside a bundle, such as the executable or its
   Info.plist itself, is not a bundle; and a manifest is a plist file, so a
   directory is not a manifest. A case that registers the wrong kind of path
   therefore fails instead of passing by string equality. */

#define fake_bundle_text 2048

typedef struct fake_entry {
  const char *path;
  const char *identifier;
} fake_entry;

static char bundle_path_text[fake_bundle_entries][fake_bundle_text];
static char bundle_identifier_text[fake_bundle_entries][fake_bundle_text];
static fake_entry bundles[fake_bundle_entries];

static char plist_path_text[fake_bundle_entries][fake_bundle_text];
static char plist_identifier_text[fake_bundle_entries][fake_bundle_text];
static fake_entry plists[fake_bundle_entries];

static void reset_table(fake_entry *table, char paths[][fake_bundle_text],
                        char identifiers[][fake_bundle_text]) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    table[index].path = NULL;
    table[index].identifier = NULL;
    paths[index][0] = 0;
    identifiers[index][0] = 0;
  }
}

void fake_bundles_reset(void) {
  reset_table(bundles, bundle_path_text, bundle_identifier_text);
  reset_table(plists, plist_path_text, plist_identifier_text);
}

static void add_entry(fake_entry *table, char paths[][fake_bundle_text],
                      char identifiers[][fake_bundle_text], const char *path,
                      const char *identifier) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    if (table[index].path == NULL) {
      text_copy(paths[index], path, fake_bundle_text);
      text_copy(identifiers[index], identifier, fake_bundle_text);
      table[index].path = paths[index];
      table[index].identifier = identifiers[index];
      return;
    }
  }
}

void fake_bundle_add(const char *path, const char *identifier) {
  add_entry(bundles, bundle_path_text, bundle_identifier_text, path, identifier);
}

void fake_plist_add(const char *path, const char *identifier) {
  add_entry(plists, plist_path_text, plist_identifier_text, path, identifier);
}

static bool ends_with(const char *text, const char *suffix) {
  usize textLength = text_length(text);
  usize suffixLength = text_length(suffix);
  return textLength >= suffixLength &&
         text_equal(text + (textLength - suffixLength), suffix);
}

/* A bundle is a directory holding an Info.plist. The table only knows the
   directory itself, so a path that names a file inside a bundle, such as the
   executable or its Info.plist, never answers. */

static bool is_bundle_directory(const char *path) {
  return ends_with(path, ".app");
}

static bool lookup(const fake_entry *table, const char *path, char *out, usize capacity) {
  for (u32 index = 0; index < fake_bundle_entries; index++) {
    if (table[index].path != NULL && text_equal(path, table[index].path)) {
      text_copy(out, table[index].identifier, capacity);
      return true;
    }
  }
  return false;
}

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity) {
  if (!is_bundle_directory(bundlePath)) {
    return false;
  }
  return lookup(bundles, bundlePath, out, capacity);
}

bool plist_file_identifier(const char *plistPath, char *out, usize capacity) {
  if (!ends_with(plistPath, ".plist")) {
    return false;
  }
  return lookup(plists, plistPath, out, capacity);
}
