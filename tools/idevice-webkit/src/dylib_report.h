#ifndef IDEVICE_WEBKIT_DYLIB_REPORT_H
#define IDEVICE_WEBKIT_DYLIB_REPORT_H

#include "load_chain.h"
#include "types.h"

#define plist_value_capacity 2048

/* How the ellekit filter of this process matched. The filter names a class and
   a list of bundle identifiers, and libinjector loads the dylib when either
   one matches, so a report has to say which of the two answered: a single
   answer for all hosts would hide a host that only the bundle list reached.
   match_none is the entry report, written before the process was classified.
   match_classes is the class match, which says the main application matched
   because it has a UIApplication; the WebKit subprocesses of the same app
   have none. match_bundles is the bundle list match, which says the filter
   named this host by its CFBundleIdentifier. */

#define match_none "none"
#define match_classes "classes"
#define match_bundles "bundles"

/* The report is the only way to read what the constructor did without a
   debugger, so every value it learns before the next write goes into it. */

void dylib_report_begin(const char *bundleIdentifier);
void dylib_report_set_dlopen(const char *path, void *handle, const char *error);
void dylib_report_set_image(const char *imagePath, const char *version);

/* The seven-image chain, one record per step.

   The report used to describe a single dlopen. That was the wrong shape for the
   failure the device recorded: the entry image loaded and a dependency did not,
   and a single record could not say which of seven edges broke. Each step is
   written with its own path, outcome, image count for that step and the uuid of
   whatever dyld registered for that path, so a chain that stopped at step 4
   names step 4 rather than reporting one failure for the whole run.

   The three system uuids are written beside the seven, because they answer a
   different question and a reader has to be able to compare them. Dedupe follows
   file identity, so the rewritten images are different files from the system
   ones: they map, they bind to each other, and they do not become what a request
   for /System/Library/Frameworks/WebKit.framework/WebKit answers. Equal uuid
   sets mean the substitution happened; disjoint sets mean the payload loaded and
   the application is still on 16.1, which is a partial result and reads as one. */

void dylib_report_set_chain(const load_result *result);
void dylib_report_set_system_images(const system_images *images);

/* The path dyld gave for this dylib and the answer of installed_copy_reason,
   which is empty only when the dylib is the installed copy. */

void dylib_report_set_self(const char *imagePath, bool installedCopy, const char *reason);
void dylib_report_set_match(const char *via);
void dylib_report_set_stage(u32 stage);

/* Writes the report and answers whether the file was written. */

bool dylib_report_write(const char *path);

#endif
