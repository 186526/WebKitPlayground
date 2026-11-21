#include "fake_dyld.h"

#include "textkit.h"
#include "types.h"

/* Replaces the three calls the constructor makes to ask where it is and what
   it loaded: the executable path, the handle of the framework and the record
   dladdr fills in. None of the __dyld_* image table functions is replaced,
   because the dylib no longer refers to one: they are not exported by the dyld
   of the device, and a reference to one keeps the whole image from binding.

   The test picks the answers, because the path of the test binary is not what
   the cases are about. The framework is named `loaded` in fake_reset; it is
   present only when that argument is not NULL. */

#define fake_path_text 512

static char executable_text[fake_path_text];
static char self_text[fake_path_text];
static char framework_text[fake_path_text];

static const void *framework_class = 0;
static const void *framework_base = 0;
static const void *framework_handle = 0;

/* A byte that is not a Mach-O header: the image is there, but it says nothing
   about its version. */
static const u8 framework_header_text[8] = {0};

void *fake_framework_handle(void) {
  return (void *)framework_handle;
}

/* Answers the address of a class the framework defines. The constructor hands
   that address to dladdr, which is where the image base comes from. */

const void *fake_framework_symbol(const char *name) {
  (void)name;
  return framework_class;
}

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self) {
  (void)preferences;
  text_copy(executable_text, first, fake_path_text);
  if (self == 0) {
    self_text[0] = 0;
  } else {
    text_copy(self_text, self, fake_path_text);
  }
  framework_handle = 0;
  framework_class = 0;
  framework_base = 0;
  if (loaded != 0) {
    text_copy(framework_text, loaded, fake_path_text);
    framework_class = (const void *)&framework_header_text;
    framework_base = (const void *)framework_header_text;
    framework_handle = (void *)&framework_text;
  }
}

/* The framework answers a class only while it is loaded, so a handle for an
   image that is not there answers nothing. */

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
    info->dli_fname = framework_text;
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
