#include <stdio.h>

#include "file_writer.h"

#include "types.h"

/* The dylib writes the report with the Darwin open flags 0x601 and a mode.
   On a Linux host the kernel reads 0x601 as O_WRONLY | O_TRUNC | O_APPEND and
   refuses to create the file, so the test uses stdio to reach the same paths
   and leaves the order of the stage writes to the code under test. Every
   attempt is reported, so a silently skipped write cannot pass as a pass.
   A refused write stands in for the device giving no file: the constructor
   reads that answer and stops. */

#include <string.h>

/* Only the last write of a process survives in the file, so each write is also
   logged with its path and the stage it carries. A case that checks the order
   of the stages reads this log; the file itself cannot show it. */

#define write_log_entries 8
#define write_log_stage 16

typedef struct write_log_entry {
  const char *path;
  char stage[write_log_stage];
} write_log_entry;

static write_log_entry write_log[write_log_entries];
static int write_log_count = 0;

void fake_write_log_reset(void) {
  write_log_count = 0;
}

int fake_write_log_length(void) {
  return write_log_count;
}

const char *fake_write_log_path(int index) {
  return index < 0 || index >= write_log_count ? 0 : write_log[index].path;
}

const char *fake_write_log_stage(int index) {
  return index < 0 || index >= write_log_count ? 0 : write_log[index].stage;
}

/* The stage is the integer between the stage key and the end of its line. */

static void log_write(const char *path, const char *bytes) {
  if (write_log_count >= write_log_entries) {
    return;
  }
  write_log_entry *entry = &write_log[write_log_count++];
  entry->path = path;
  entry->stage[0] = 0;
  const char *key = "<key>stage</key>";
  const char *at = strstr(bytes, key);
  if (at == 0) {
    return;
  }
  at = strstr(at, "<integer>");
  if (at == 0) {
    return;
  }
  at += strlen("<integer>");
  usize written = 0;
  while (at[written] != 0 && at[written] != '<' && written + 1 < write_log_stage) {
    entry->stage[written] = at[written];
    written++;
  }
  entry->stage[written] = 0;
}

static int refuse_writes = 0;

void fake_refuse_writes(int refuse) {
  refuse_writes = refuse;
}

bool write_file(const char *path, const char *bytes, usize length) {
  if (refuse_writes) {
    return false;
  }
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
  log_write(path, bytes);
  return true;
}
