#ifndef IDEVICE_WEBKIT_FILE_WRITER_H
#define IDEVICE_WEBKIT_FILE_WRITER_H

#include "textkit.h"

/* The report is written in one pass: the file is truncated first, so a
   process killed half way through leaves a shorter file, never a mixture of
   two writers. */

bool write_file(const char *path, const char *bytes, usize length);

#endif

#if defined(IDEVICE_WEBKIT_TEST_FILE_WRITER)
/* The Linux host running the tests reads the Darwin open flags as different
   numbers, so the test compiles its own implementation of this function to
   still exercise the report and stage code. The device build never sees it. */
bool write_file(const char *path, const char *bytes, usize length);
#endif
