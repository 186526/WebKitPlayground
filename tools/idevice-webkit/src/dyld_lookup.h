#ifndef IDEVICE_WEBKIT_DYLD_LOOKUP_H
#define IDEVICE_WEBKIT_DYLD_LOOKUP_H

#include "types.h"

/* The three dyld functions the dylib needs, declared with the signatures of
   libdyld on arm64e. RTLD_NOLOAD finds a library without loading it. */

void *library_find(const char *path);

/* Loads a library lazily; the returned handle is the evidence that dlopen
   accepted the path inside the host process. */

void *library_load(const char *path);
/* Looks a name up in the global symbol table, the way dlsym(RTLD_DEFAULT, …)
   does: the process's own symbols and every loaded image, with no library
   handle. objc_getClass lives in libobjc, which the host loads separately from
   Foundation, so it can only be reached this way. */

void *symbol_find_global(const char *name);
void *symbol_find(void *library, const char *name);
char *library_last_error(void);

#endif
