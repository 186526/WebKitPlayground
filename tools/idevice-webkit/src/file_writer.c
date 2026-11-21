#include "file_writer.h"

#include "types.h"

#define write_only 0x0001
#define create_mode 0x0200
#define truncate_mode 0x0400
#define file_mode 0600

extern int open(const char *path, int flags, int mode);
extern int close(int descriptor);
extern ssize write(int descriptor, const void *bytes, usize count);

/* The Darwin flag values are written out by the linker as a variable length
   instruction when they are not a constant expression for the compiler, and
   the kernel then sees a different number. An empty function keeps them literal. */

__attribute__((noinline, used)) static int write_flags(void) {
  return write_only | create_mode | truncate_mode;
}

bool write_file(const char *path, const char *bytes, usize length) {
  int descriptor = open(path, write_flags(), file_mode);
  if (descriptor < 0) {
    return false;
  }
  usize written = 0;
  while (written < length) {
    ssize count = write(descriptor, bytes + written, length - written);
    if (count <= 0) {
      close(descriptor);
      return false;
    }
    written += (usize)count;
  }
  return close(descriptor) == 0;
}
