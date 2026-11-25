#ifndef IDEVICE_WEBKIT_DYLD_LOOKUP_H
#define IDEVICE_WEBKIT_DYLD_LOOKUP_H

#include "types.h"

/* The three dyld functions the dylib needs, declared with the signatures of
   libdyld on arm64e. RTLD_NOLOAD finds a library without loading it. */

void *library_find(const char *path);

/* Loads a library lazily; the returned handle is the evidence that dlopen
   accepted the path inside the host process. */

void *library_load(const char *path);

/* Loads a library with the mode the caller names.

   The mode is a parameter rather than a constant because the two callers need
   different ones and the difference is the point. The framework load that reads
   a version wants RTLD_LAZY on its own and no effect on the process's symbol
   scopes. The seven-image chain needs RTLD_NOW|RTLD_GLOBAL: NOW so a missing
   symbol fails the load that is being attempted instead of faulting at the first
   call through it, GLOBAL so the image's exports are visible to the loads that
   follow. Passing the mode in keeps that decision in the one file that explains
   why it is what it is, instead of in the loader helper. */

void *library_load_with_mode(const char *path, i32 mode);

/* Darwin declares dladdr in <dlfcn.h>, which the test build leaves out in favour
   of its own stubs, so the record and the function are declared here once. The
   guard is the one the test fixture also uses, which keeps a single definition
   in a translation unit that includes both.

   dladdr takes any address inside an image and answers the path dyld recorded
   for that image, which is the path it resolved rather than the one a caller
   asked for. Both users need that reading: the chain to tell which build is
   resident under a path, the constructor to name its own image. */

#ifndef IDEVICE_WEBKIT_DL_INFO
#define IDEVICE_WEBKIT_DL_INFO
typedef struct dl_info {
  const char *dli_fname;
  void *dli_fbase;
  const char *dli_sname;
  void *dli_saddr;
} dl_info;
#endif

extern int dladdr(const void *address, dl_info *info);

/* Looks a name up in the global symbol table, the way dlsym(RTLD_DEFAULT, …)
   does: the process's own symbols and every loaded image, with no library
   handle. objc_getClass lives in libobjc, which the host loads separately from
   Foundation, so it can only be reached this way. */

void *symbol_find_global(const char *name);
void *symbol_find(void *library, const char *name);
char *library_last_error(void);

#endif
