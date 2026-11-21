#ifndef IDEVICE_WEBKIT_BUNDLE_LOOKUP_H
#define IDEVICE_WEBKIT_BUNDLE_LOOKUP_H

#include "types.h"

#define bundle_identifier_capacity 256

/* Reads the CFBundleIdentifier of the bundle at a path. A path holding an
   Info.plist in the bundle layout answers with its identifier; a path that is
   not a bundle does not answer at all. */

bool bundle_path_identifier(const char *bundlePath, char *out, usize capacity);

#endif
