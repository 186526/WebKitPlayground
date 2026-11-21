#include "file_writer.h"

#include "types.h"

#define write_only 0x0001
#define create_mode 0x0200
#define truncate_mode 0x0400
/* The mode a report file is created with. It is 0644 rather than the 0600 of
   1.0.6 because the defect those versions shipped was a file whose owner had no
   write bit: the app process rewrites the report at every stage, and a refused
   second write stops the run at stage 1. The report holds a host path and a
   bundle identifier and nothing secret, so 0644 keeps the owner able to rewrite
   it and every diagnostic process able to read it. */
#define file_mode 0644

/* open is variadic in Darwin's headers. A fixed three-parameter declaration is
   not the prototype of that function even though it links: the caller puts the
   mode in a register, the callee reads it from the stack, and the mode is lost.
   The device then creates the file with a mode nobody asked for — 0504 for the
   shipped 1.0.8 copy, which left the owner unable to write the report a second
   time and stopped the run at stage 1. The declaration therefore keeps the
   ellipsis, and a two-probe comparison in one image showed the difference
   directly: 600 through the variadic call, 700 through the fixed one. */
extern int open(const char *path, int flags, ...);
extern int close(int descriptor);
extern ssize write(int descriptor, const void *bytes, usize count);
extern int fchmod(int descriptor, int mode);

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
  /* The mode the open asked for is not what a new file ends up with even when
     it arrives: six different requested values all produced 0740 on this
     device, and a file created ahead of time keeps whatever mode it had. This
     call is what makes the result the mode this code asks for. It runs before
     the write, so a process that dies mid-write leaves no wider file. */
  fchmod(descriptor, file_mode);
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
