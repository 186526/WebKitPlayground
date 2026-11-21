#ifndef IDEVICE_WEBKIT_TEXTKIT_H
#define IDEVICE_WEBKIT_TEXTKIT_H

#include "types.h"

/* Small C string helpers: the dylib has no libc headers, and the only libc
   functions it may call are the ones already loaded in the host process. */

void text_set(void *memory, u8 value, usize length);
usize text_length(const char *text);
void text_copy(char *out, const char *text, usize capacity);
bool text_equal(const char *left, const char *right);
bool text_begins_with(const char *text, const char *prefix);

#endif
