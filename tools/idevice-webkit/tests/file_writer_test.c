/* Checks the two things the report writer has to get right about the device:
   the mode is passed the way Darwin's variadic open expects it, and the mode
   is set on the descriptor afterwards, because the device does not keep the
   one the open asked for.

   src/file_writer.c declares `open` itself. Renaming the four calls before the
   include turns the declaration and the calls into the recorders below, and
   the variadic recorder is the guard: a reverted declaration of
   `open(path, flags, mode)` meets `int test_open(const char *, int, ...)` as a
   conflicting type and the build fails, so the ellipsis cannot quietly
   disappear again. */

#include <stdarg.h>
#include <stdio.h>

#include "../src/types.h"

static int last_flags;
static int last_mode;
static int open_calls;
static int fchmod_calls;
static int close_calls;
static int fchmod_mode;
static int fchmod_after_close;

static int test_open(const char *path, int flags, ...) {
  va_list arguments;
  va_start(arguments, flags);
  last_mode = va_arg(arguments, int);
  va_end(arguments);
  (void)path;
  last_flags = flags;
  open_calls++;
  return 7;
}

static int test_close(int descriptor) {
  (void)descriptor;
  close_calls++;
  return 0;
}

static ssize test_write(int descriptor, const void *bytes, usize count) {
  (void)descriptor;
  (void)bytes;
  return (ssize)count;
}

static int test_fchmod(int descriptor, int mode) {
  (void)descriptor;
  fchmod_calls++;
  fchmod_mode = mode;
  if (close_calls != 0) {
    fchmod_after_close = 1;
  }
  return 0;
}

#define open test_open
#define close test_close
#define write test_write
#define fchmod test_fchmod
#include "../src/file_writer.c"
#undef open
#undef close
#undef write
#undef fchmod

static int failures;

static void check(const char *what, int ok) {
  printf("%-58s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

int main(void) {
  const char body[] = "report";

  open_calls = 0;
  fchmod_calls = 0;
  close_calls = 0;
  fchmod_mode = 0;
  fchmod_after_close = 0;
  last_flags = 0;
  last_mode = 0;

  bool written = write_file("/tmp/idevice-webkit-test/report.plist", body, 6);

  check("the write succeeds", written == 1);
  check("the flags are the Darwin create-and-truncate set", last_flags == 0x601);
  check("the mode reaches the variadic call as 0644", last_mode == 0644);
  check("the descriptor's mode is set as well", fchmod_calls == 1);
  check("the descriptor is set to 0644", fchmod_mode == 0644);
  check("the mode is set before the file is closed", fchmod_after_close == 0);
  check("the descriptor is closed once", close_calls == 1);

  printf("failures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
