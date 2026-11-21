#ifndef IDEVICE_WEBKIT_FAKE_FILTER_H
#define IDEVICE_WEBKIT_FAKE_FILTER_H

#include "types.h"

/* Stands in for the ellekit filter as the dylib reads it back, and for the
   two registries the branches ask: the Objective-C runtime that answers
   whether a class exists, and the LaunchServices registry that answers
   whether a bundle identifier names a registered bundle. Adding a bundle to
   the filter and registering it are separate calls, because a system
   application such as MobileSafari is in a filter and not in the registry;
   the lowercased form of an identifier is registered on its own, so a case
   can tell the second lookup of the bundle branch from the first. */

void fake_filters_reset(const char *filterPath);
void fake_filter_add_class(const char *class_name);
void fake_filter_add_bundle(const char *identifier);
void fake_class_register(const char *class_name);
void fake_bundle_register(const char *identifier);

/* What the last lowercased copy was made with, so a case can hold the call to
   the three arguments CFStringCreateMutableCopy declares. The header comment
   above the stub repeats this; keep the two together. */

void *fake_filter_last_copy_allocator(void);
i64 fake_filter_last_copy_capacity(void);

/* The two calls the dylib looks up in the process and in Foundation, and the
   table every Foundation selector is answered from. */

void *fake_class_named(const char *name);
void *fake_foundation_symbol(const char *name);

#endif
