#ifndef IDEVICE_WEBKIT_FAKE_OBJC_H
#define IDEVICE_WEBKIT_FAKE_OBJC_H

#include "types.h"

/* Stands in for the Objective-C runtime and the Foundation objects the dylib
   messages. Everything the dylib reads is a message: a class name resolves to
   a class object, a method name to a selector, and the selector decides which
   answer a message gets. The fixture therefore models objects rather than a
   table of C functions, which is what makes a case fail when the dylib looks a
   method name up as though it were a C symbol. */

#define fake_objc_entries 8

/* The process's classes. objc_getClass answers these and nothing else, so a
   case registers the class names a process has. */

void fake_objc_reset(void);
void fake_class_register(const char *class_name);

/* An application bundle directory and its CFBundleIdentifier, which
   NSBundle.bundleWithPath: answers for. The directory has to end in .app: a
   path inside a bundle is not a bundle. */

void fake_bundle_add(const char *path, const char *identifier);

/* A manifest file and its CFBundleIdentifier, which
   NSDictionary.dictionaryWithContentsOfFile: answers for. The path has to end
   in .plist: a directory is not a plist file. */

void fake_plist_add(const char *path, const char *identifier);

/* The filter file the dylib reads back. Its shape is the device's: one
   top-level key, Filter, holding the Classes and Bundles arrays. Adding to
   either list is what makes the file present. */

void fake_filters_reset(const char *filterPath);
void fake_filter_add_class(const char *class_name);
void fake_filter_add_bundle(const char *identifier);

/* Switches the file back to the older flat shape, where Classes and Bundles
   are the file dictionary's own keys. Only the cases that pin the
   compatibility branch call it. */

void fake_filters_flat(void);

/* The LaunchServices registry the bundle branch asks: an identifier that is
   registered names a bundle, and a system application is in the filter and not
   in the registry. */

void fake_bundle_register(const char *identifier);

/* The runtime entry points, which the test routes into dyld_lookup.c the way
   the device exports them. */

void *fake_objc_class_named(const char *name);
void *fake_objc_selector_named(const char *name);
void *fake_objc_msg_send(void *receiver, void *selector, ...);

/* What the last lowercased copy was made with, so a case can hold the call to
   the three arguments CFStringCreateMutableCopy declares. */

void *fake_filter_last_copy_allocator(void);
i64 fake_filter_last_copy_capacity(void);

/* The CF functions the bundle branch asks by name; they are ordinary C
   symbols on the device, so the test answers them from the same table. */

void *fake_cf_symbol(const char *name);

#endif
