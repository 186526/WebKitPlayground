#ifndef IDEVICE_WEBKIT_DYLIB_REPORT_H
#define IDEVICE_WEBKIT_DYLIB_REPORT_H

#include "types.h"

#define plist_value_capacity 2048

/* The report is the only way to read what the constructor did without a
   debugger, so every value it learns before the next write goes into it. */

void dylib_report_begin(const char *bundleIdentifier);
void dylib_report_set_dlopen(const char *path, void *handle, const char *error);
void dylib_report_set_image(const char *imagePath, const char *version);

/* The path dyld gave for this dylib and the answer of installed_copy_reason,
   which is empty only when the dylib is the installed copy. */

void dylib_report_set_self(const char *imagePath, bool installedCopy, const char *reason);
void dylib_report_set_stage(u32 stage);

/* Writes the report and answers whether the file was written. */

bool dylib_report_write(const char *path);

#endif
