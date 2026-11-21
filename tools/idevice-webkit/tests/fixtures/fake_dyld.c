#include "fake_dyld.h"

#include "textkit.h"
#include "types.h"

/* Replaces the three calls the constructor makes to ask where it is and what
   it loaded: the executable path, the two dlopen modes and the record dladdr
   fills in. None of the __dyld_* image table functions is replaced, because the
   dylib no longer refers to one.

   The framework is `registered` by fake_reset, which stands for a framework
   file that is on disk, and `loaded` only once the load has been asked for:
   RTLD_NOLOAD answers a handle for an image already in the process and NULL
   for one that is only on disk, which is the difference a lookup made before
   the load gets wrong. dladdr reports the path dyld resolved, which on the
   device is the /private form of the /var/jb path the load was given. */

#define fake_path_text 512

static char executable_text[fake_path_text];
static char self_text[fake_path_text];
static char framework_text[fake_path_text];
static char resolved_text[fake_path_text];

static const void *framework_class = 0;
static const void *framework_base = 0;
static const void *framework_handle = 0;
static int framework_registered = 0;
static int framework_loaded = 0;

/* A byte that is not a Mach-O header: the image is there, but it says nothing
   about its version. */
static const u8 framework_header_text[8] = {0};

/* The handle RTLD_NOLOAD answers, and NULL while the image is only on disk. */

void *fake_framework_handle(void) {
  return framework_loaded ? (void *)framework_handle : 0;
}

/* The handle dlopen answers. A path that names no framework file answers
   nothing, the way the device does with a framework that is not installed. */

void *fake_framework_load(void) {
  if (!framework_registered) {
    return 0;
  }
  framework_loaded = 1;
  return (void *)framework_handle;
}

/* Answers the address of a class the framework defines. The constructor hands
   that address to dladdr, which is where the image base comes from. An image
   that is not loaded defines no class. */

const void *fake_framework_symbol(const char *name) {
  (void)name;
  return framework_loaded ? framework_class : 0;
}

void fake_reset(const char *executable, const char *framework, const char *self) {
  text_copy(executable_text, executable, fake_path_text);
  if (self == 0) {
    self_text[0] = 0;
  } else {
    text_copy(self_text, self, fake_path_text);
  }
  framework_loaded = 0;
  framework_registered = 0;
  framework_class = 0;
  framework_base = 0;
  framework_handle = 0;
  resolved_text[0] = 0;
  if (framework != 0) {
    text_copy(framework_text, framework, fake_path_text);
    text_copy(resolved_text, "/private", fake_path_text);
    usize used = text_length(resolved_text);
    for (usize index = 0; framework[index] != 0 && used + 1 < fake_path_text; index++) {
      resolved_text[used++] = framework[index];
    }
    resolved_text[used] = 0;
    framework_class = (const void *)&framework_header_text;
    framework_base = (const void *)framework_header_text;
    framework_handle = (void *)&framework_text;
    framework_registered = 1;
  }
}

int _NSGetExecutablePath(char *buffer, u32 *size) {
  usize length = text_length(executable_text);
  if (buffer == 0 || size == 0 || *size < length + 1) {
    if (size != 0) {
      *size = (u32)(length + 1);
    }
    return -1;
  }
  text_copy(buffer, executable_text, *size);
  *size = (u32)(length + 1);
  return 0;
}

int dladdr(const void *address, dl_info *info) {
  if (address == 0 || info == 0) {
    return 0;
  }
  if (framework_class != 0 && address == framework_class) {
    info->dli_fname = resolved_text;
    info->dli_fbase = (void *)framework_base;
    info->dli_sname = 0;
    info->dli_saddr = (void *)0;
    return 1;
  }
  if (self_text[0] == 0) {
    return 0;
  }
  info->dli_fname = self_text;
  info->dli_fbase = (void *)0;
  info->dli_sname = 0;
  info->dli_saddr = (void *)0;
  return 1;
}
