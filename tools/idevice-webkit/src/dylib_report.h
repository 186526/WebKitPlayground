#ifndef IDEVICE_WEBKIT_DYLIB_REPORT_H
#define IDEVICE_WEBKIT_DYLIB_REPORT_H

#include "types.h"

typedef struct claim_result claim_result;

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

/* The path dyld gave for this dylib and the answer of installed_copy_reason,
   which is empty only when the dylib is the installed copy. */

void dylib_report_set_self(const char *imagePath, bool installedCopy, const char *reason);
void dylib_report_set_match(const char *via);
void dylib_report_set_stage(u32 stage);

/* The claim of the seven /var/jb images: whether jbctl marked this process,
   what the marking failed with, how many images loaded, and which one
   stopped the sequence. Every field is written on the claim's own write, so a
   process whose marking failed and an image that would not load are both
   readable from the report. */

void dylib_report_set_claim(const claim_result *claim);

/* Writes the report and answers whether the file was written. */

bool dylib_report_write(const char *path);

#endif
