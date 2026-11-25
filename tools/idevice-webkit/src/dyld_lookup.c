#include "dyld_lookup.h"

#include "types.h"

#define lookup_only 0x10
#define load_lazy 1

/* dlsym's special handle for «search every loaded image», the value Darwin's
   dlfcn.h gives RTLD_DEFAULT. It is not zero: dyld compares the handle against
   this constant and treats any other value as a handle dlopen returned, so a
   zero here reaches the loader-from-handle path, fails the validity check and
   answers NULL with «invalid handle». */
#define rtld_default ((void *)-2)

/* dlsym's handle for an absent library. Kept apart from rtld_default so that
   «no library given» and «every loaded image» stay two different lookups. */
#define no_library ((void *)0)

extern void *dlopen(const char *path, int mode);
extern void *dlsym(void *library, const char *name);
extern char *dlerror(void);

void *library_find(const char *path) {
  return dlopen(path, lookup_only);
}

void *library_load(const char *path) {
  return dlopen(path, load_lazy);
}

void *library_load_with_mode(const char *path, i32 mode) {
  return dlopen(path, mode);
}

void *symbol_find(void *library, const char *name) {
  return library == no_library ? NULL : dlsym(library, name);
}

void *symbol_find_global(const char *name) {
  return dlsym(rtld_default, name);
}

char *library_last_error(void) {
  return dlerror();
}
