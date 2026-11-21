#ifndef IDEVICE_WEBKIT_BUNDLE_LOOKUP_H
#define IDEVICE_WEBKIT_BUNDLE_LOOKUP_H

#include "types.h"

#define bundle_identifier_capacity 256

/* Reads the CFBundleIdentifier of the bundle at a path. A directory holding
   an Info.plist answers with its identifier; a path that is not a bundle, an
   executable inside one included, does not answer at all. */

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity);

/* Reads the CFBundleIdentifier key of a plist file, which is how the host
   manifest is written. */

bool plist_file_identifier(const char *plistPath, char *out, usize capacity);

#endif
