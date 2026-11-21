#include <stdio.h>

#include "file_writer.h"

#include "types.h"

/* The dylib writes the report with the Darwin open flags 0x601 and a mode.
   On a Linux host the kernel reads 0x601 as O_WRONLY | O_TRUNC | O_APPEND and
   refuses to create the file, so the test uses stdio to reach the same paths
   and leaves the order of the stage writes to the code under test. Every
   attempt is reported, so a silently skipped write cannot pass as a pass. */

bool write_file(const char *path, const char *bytes, usize length) {
  FILE *file = fopen(path, "wb");
  if (file == NULL) {
    perror(path);
    return false;
  }
  usize written = length == 0 ? 0 : fwrite(bytes, 1, length, file);
  int closed = fclose(file);
  if (written != length || closed != 0) {
    printf("write failed: %s\n", path);
    return false;
  }
  return true;
}
