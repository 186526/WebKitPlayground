#include "dyld_lookup.h"

#include "types.h"

#define lookup_only 0x10
#define load_lazy 1

/* dlsym's handle for the global symbol table. Its value is zero, the same
   value an absent library has, so a lookup that means «no library given» and
   one that means «every loaded image» are kept as two functions. */
#define rtld_default ((void *)0)

extern void *dlopen(const char *path, int mode);
extern void *dlsym(void *library, const char *name);
extern char *dlerror(void);

void *library_find(const char *path) {
  return dlopen(path, lookup_only);
}

void *library_load(const char *path) {
  return dlopen(path, load_lazy);
}

void *symbol_find(void *library, const char *name) {
  return library == NULL ? NULL : dlsym(library, name);
}

void *symbol_find_global(const char *name) {
  return dlsym(rtld_default, name);
}

char *library_last_error(void) {
  return dlerror();
}
