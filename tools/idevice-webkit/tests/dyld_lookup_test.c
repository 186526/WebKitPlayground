/* Checks the two handles dyld_lookup.c hands to dlsym.

   The device answers on the strength of these two values alone: dyld compares
   the handle against its own RTLD_* constants and treats anything else as a
   handle dlopen returned, so a wrong number reaches the loader-from-handle
   path and comes back NULL with «invalid handle». The host build cannot link
   libdyld, so dlsym and dlopen are compiled out under other names and the
   calls are recorded here. */

#include <stdio.h>
#include <string.h>

#include "../src/types.h"

static void *last_handle;
static const char *last_name;
static int dlsym_calls;
static int dlopen_calls;

/* Darwin's dlfcn.h: RTLD_DEFAULT is ((void *)-2), RTLD_NOLOAD is 0x10. */
#define darwin_rtld_default ((void *)-2)
#define darwin_rtld_noload 0x10

static void *test_dlsym(void *library, const char *name) {
  last_handle = library;
  last_name = name;
  dlsym_calls++;
  return NULL;
}

static void *test_dlopen(const char *path, int mode) {
  (void)path;
  (void)mode;
  dlopen_calls++;
  return NULL;
}

static char *test_dlerror(void) {
  return "test: none";
}

/* dyld_lookup.c declares the three libdyld functions itself; renaming them
   before the include turns both the declarations and the calls into the
   recorders above. */
#define dlopen test_dlopen
#define dlsym test_dlsym
#define dlerror test_dlerror
#include "../src/dyld_lookup.c"
#undef dlopen
#undef dlsym
#undef dlerror

static int failures;

static void check(const char *what, int ok) {
  printf("%-58s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

int main(void) {
  dlsym_calls = 0;
  last_handle = NULL;
  last_name = NULL;

  symbol_find_global("objc_getClass");
  check("the global lookup hands dlsym Darwin's RTLD_DEFAULT",
        last_handle == darwin_rtld_default);
  check("the global lookup passes the name through unchanged",
        last_name != NULL && strcmp(last_name, "objc_getClass") == 0);

  int before = dlsym_calls;
  void *answer = symbol_find(NULL, "objc_getClass");
  check("an absent library answers nothing without asking dlsym",
        answer == NULL && dlsym_calls == before);

  before = dlsym_calls;
  symbol_find((void *)0x1234, "CFRelease");
  check("a real handle is passed to dlsym as given",
        dlsym_calls == before + 1 && last_handle == (void *)0x1234);

  dlopen_calls = 0;
  library_find("/var/jb/usr/lib/libobjc.dylib");
  check("finding a library without loading it asks for RTLD_NOLOAD",
        dlopen_calls == 1);

  if (failures != 0) {
    printf("\n%d lookup check(s) failed\n", failures);
    return 1;
  }
  printf("\nlookup: every check passed\n");
  return 0;
}
