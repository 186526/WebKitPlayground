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

/* Writes a decimal integer and a terminating NUL, truncating to the capacity
   the way text_copy does. The magnitude is taken through the unsigned type so
   the most negative value renders without the negation overflowing, and the
   sign is written before the digits so a negative value can never read as the
   zero a successful call reports. */

void text_number(char *out, usize capacity, i64 value);

#endif
