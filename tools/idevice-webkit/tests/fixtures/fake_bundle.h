#ifndef IDEVICE_WEBKIT_FAKE_BUNDLE_H
#define IDEVICE_WEBKIT_FAKE_BUNDLE_H

#include "types.h"

/* Stands in for the Info.plist lookup: every path added here answers with its
   identifier, and a path that was not added holds no bundle. */

#define fake_bundle_entries 8

void fake_bundles_reset(void);
void fake_bundle_add(const char *path, const char *identifier);
void fake_plist_add(const char *path, const char *identifier);

#endif
