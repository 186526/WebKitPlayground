/* Checks the report's seven-step chain, which nothing else in the suite reaches.

   Why this file exists: the report is the only way to read what the constructor
   did on a device without a debugger, and the one step whose outcome a reader
   needs is the step that failed. A test that stops at "the chain loaded seven"
   never writes a failure into the report, so the whole path that records one was
   green while it was wrong.

   The defect this pins: the writer used `index + 1 < result->stopped` to decide
   whether a step had an outcome. For the step that ran and failed,
   `index + 1 == result->stopped`, so the strict comparison excluded exactly that
   step and the diagnosis was written as an empty `loaded` and an empty `error`.
   `chainStopped` kept the position, so what was lost was the reason rather than
   the location.

   The cases below call dylib_report_set_chain with hand-built load_result values
   and then read the document back, so they fail against the strict comparison and
   pass against `<=`.

   The report is written through write_file, which tests/fixtures/host_file_writer.c
   replaces for the host: the dylib calls open/write/close with the Darwin flags,
   and a Linux kernel reads those as a different set. The fixture writes to the
   path it is given, so each case writes a file and reads it back. */

#include <stdio.h>
#include <string.h>

#include "../src/types.h"

#include "fixtures/host_file_writer.c"
#include "../src/textkit.c"
#include "../src/macho_image.c"
#include "../src/load_chain.c"
#include "../src/dylib_report.c"

/* load_chain.c is compiled in for one reason: it owns load_chain_paths, which the
   report copies into each step. Using the real table is what keeps the paths here
   from drifting from the ones the chain actually loads. The four calls that file
   makes are never reached, because no case runs the chain, so they are stubbed
   rather than linked. */

void *library_load_with_mode(const char *path, i32 mode) {
  (void)path;
  (void)mode;
  return 0;
}

char *library_last_error(void) {
  return "test: not loaded";
}

u32 _dyld_image_count(void) {
  return 0;
}

const void *_dyld_get_image_header(u32 index) {
  (void)index;
  return 0;
}


#define report_path "/tmp/idevice-webkit-report-chain.plist"

/* What the writer wrote. The document is small, and reading it back is the same
   check the device's plist parser makes, so a plain read is the honest one. */

static char document[document_capacity];

static int load_document(void) {
  document[0] = 0;
  FILE *file = fopen(report_path, "rb");
  if (file == NULL) {
    return 0;
  }
  usize read = fread(document, 1, sizeof(document) - 1, file);
  fclose(file);
  document[read] = 0;
  return read > 0;
}

static int failures;

static void check(const char *what, int ok) {
  printf("%-66s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

/* The text of a <key> entry in the document, as the plist writer emits it: the
   key on its own line and the value on the next, both tab-indented. */

static int value_of(const char *document, const char *key, char *out, usize capacity) {
  char needle[128];
  snprintf(needle, sizeof(needle), "<key>%s</key>\n\t<string>", key);
  const char *start = strstr(document, needle);
  if (start == NULL) {
    return 0;
  }
  start += strlen(needle);
  const char *end = strstr(start, "</string>");
  if (end == NULL) {
    return 0;
  }
  usize length = (usize)(end - start);
  if (length + 1 > capacity) {
    return 0;
  }
  memcpy(out, start, length);
  out[length] = 0;
  return 1;
}

/* The Nth <dict> inside <array>, so a case can name one step's fields. The chain
   array is the only array in the document. */

static const char *step_dict(const char *document, u32 step) {
  const char *cursor = strstr(document, "<key>chain</key>");
  if (cursor == NULL) {
    return NULL;
  }
  cursor = strstr(cursor, "<array>");
  if (cursor == NULL) {
    return NULL;
  }
  for (u32 index = 0; index <= step; index++) {
    cursor = strstr(cursor, "<dict>");
    if (cursor == NULL) {
      return NULL;
    }
    if (index < step) {
      cursor += 6;
    }
  }
  return cursor;
}

static int field_of_step(const char *document, u32 step, const char *key, char *out,
                         usize capacity) {
  const char *dict = step_dict(document, step);
  if (dict == NULL) {
    return 0;
  }
  char needle[128];
  snprintf(needle, sizeof(needle), "<key>%s</key>\n\t<string>", key);
  const char *start = strstr(dict, needle);
  if (start == NULL) {
    return 0;
  }
  start += strlen(needle);
  const char *end = strstr(start, "</string>");
  if (end == NULL) {
    return 0;
  }
  usize length = (usize)(end - start);
  if (length + 1 > capacity) {
    return 0;
  }
  memcpy(out, start, length);
  out[length] = 0;
  return 1;
}

/* How many step dictionaries the array holds. The chain has seven steps, so this
   is the count a reader iterates. */

static u32 step_count(const char *document) {
  const char *cursor = strstr(document, "<key>chain</key>");
  if (cursor == NULL) {
    return 0;
  }
  cursor = strstr(cursor, "<array>");
  if (cursor == NULL) {
    return 0;
  }
  const char *end = strstr(cursor, "</array>");
  if (end == NULL) {
    return 0;
  }
  u32 count = 0;
  while ((cursor = strstr(cursor, "<dict>")) != NULL && cursor < end) {
    count++;
    cursor += 6;
  }
  return count;
}

static void fresh(void) {
  dylib_report_begin("com.apple.mobilesafari");
  fake_write_log_reset();
}

/* A chain that failed at step 1, which is the shape the device recorded: the
   18.3 JavaScriptCore did not load, so the six behind it were never attempted. */

static void case_failure_at_first_step(void) {
  fresh();
  load_result result;
  text_set(&result, 0, sizeof(result));
  result.ran = true;
  result.loaded = 0;
  result.stopped = 1;
  result.steps[0].loaded = false;
  text_copy(result.steps[0].error,
            "dlopen(/var/jb/Library/WebKit18/JavaScriptCore.framework/JavaScriptCore, "
            "0x000a): Symbol not found: _WTFSignpostIndirectLoggingEnabled",
            sizeof(result.steps[0].error));
  dylib_report_set_chain(&result);

  if (!dylib_report_write(report_path) || !load_document()) {
    check("the report is written", 0);
    return;
  }

  char field[plist_value_capacity];
  /* The step that ran and failed has to carry both its outcome and its reason.
     This is the case the strict comparison wrote as empty. */
  check("the failing step records loaded=false",
        field_of_step(document, 0, "loaded", field, sizeof(field)) &&
            strcmp(field, "false") == 0);
  check("the failing step records its error text",
        field_of_step(document, 0, "error", field, sizeof(field)) &&
            strstr(field, "Symbol not found: _WTFSignpostIndirectLoggingEnabled") != NULL);
  check("the failing step records its path",
        field_of_step(document, 0, "path", field, sizeof(field)) &&
            strstr(field, "JavaScriptCore.framework/JavaScriptCore") != NULL);
  check("chainStopped names the failing step",
        value_of(document, "chainStopped", field, sizeof(field)) && strcmp(field, "1") == 0);
  check("chainLoaded is zero",
        value_of(document, "chainLoaded", field, sizeof(field)) && strcmp(field, "0") == 0);

  /* The six the chain never reached have no outcome, so they must not be written
     as failures: a reader counts loaded=false as an attempted load. */
  int later_empty = 1;
  for (u32 step = 1; step < load_chain_count; step++) {
    if (field_of_step(document, step, "loaded", field, sizeof(field)) && field[0] != 0) {
      later_empty = 0;
    }
  }
  check("the steps the chain never reached carry no outcome", later_empty);
}

/* A chain that failed at step 4: three loaded, the fourth failed, and the
   three behind it were never attempted. */

static void case_failure_at_fourth_step(void) {
  fresh();
  load_result result;
  text_set(&result, 0, sizeof(result));
  result.ran = true;
  result.loaded = 3;
  result.stopped = 4;
  for (u32 index = 0; index < 3; index++) {
    result.steps[index].loaded = true;
    result.steps[index].images = 1;
    text_copy(result.steps[index].uuid, "787DFAB1-4DE3-3568-9923-C9409791B728",
              sizeof(result.steps[index].uuid));
  }
  result.steps[3].loaded = false;
  text_copy(result.steps[3].error, "Symbol not found: __ZN3PAL16getNSShadowClassE",
            sizeof(result.steps[3].error));
  dylib_report_set_chain(&result);

  if (!dylib_report_write(report_path) || !load_document()) {
    check("the report is written", 0);
    return;
  }

  char field[plist_value_capacity];
  check("the step that failed at position 4 records loaded=false",
        field_of_step(document, 3, "loaded", field, sizeof(field)) &&
            strcmp(field, "false") == 0);
  check("the step that failed at position 4 records its error",
        field_of_step(document, 3, "error", field, sizeof(field)) &&
            strstr(field, "getNSShadowClass") != NULL);
  check("the three before it record loaded=true",
        field_of_step(document, 0, "loaded", field, sizeof(field)) &&
            strcmp(field, "true") == 0);
  check("a loaded step keeps its uuid",
        field_of_step(document, 0, "uuid", field, sizeof(field)) &&
            strcmp(field, "787DFAB1-4DE3-3568-9923-C9409791B728") == 0);
  check("a loaded step keeps its image count",
        field_of_step(document, 0, "images", field, sizeof(field)) &&
            strcmp(field, "1") == 0);
}

/* A chain that loaded all seven: every step has an outcome, and stopped is 0. */

static void case_every_step_loaded(void) {
  fresh();
  load_result result;
  text_set(&result, 0, sizeof(result));
  result.ran = true;
  result.loaded = load_chain_count;
  result.stopped = 0;
  for (u32 index = 0; index < load_chain_count; index++) {
    result.steps[index].loaded = true;
    result.steps[index].images = 1;
    text_copy(result.steps[index].uuid, "787DFAB1-4DE3-3568-9923-C9409791B728",
              sizeof(result.steps[index].uuid));
  }
  dylib_report_set_chain(&result);

  if (!dylib_report_write(report_path) || !load_document()) {
    check("the report is written", 0);
    return;
  }

  char field[plist_value_capacity];
  int every_true = 1;
  for (u32 step = 0; step < load_chain_count; step++) {
    if (!field_of_step(document, step, "loaded", field, sizeof(field)) ||
        strcmp(field, "true") != 0) {
      every_true = 0;
    }
  }
  check("a complete chain records every step as loaded", every_true);
  check("chainStopped is zero for a complete chain",
        value_of(document, "chainStopped", field, sizeof(field)) && strcmp(field, "0") == 0);
  check("chainLoaded is the step count for a complete chain",
        value_of(document, "chainLoaded", field, sizeof(field)) &&
            strcmp(field, "7") == 0);
}

/* The array holds one dictionary per step and no more. A trailing empty one
   would read as an eighth step the chain does not have. */

static void case_array_length(void) {
  fresh();
  load_result result;
  text_set(&result, 0, sizeof(result));
  result.ran = true;
  dylib_report_set_chain(&result);

  if (!dylib_report_write(report_path) || !load_document()) {
    check("the report is written", 0);
    return;
  }

  check("the chain array holds seven step dictionaries",
        step_count(document) == load_chain_count);
}

int main(void) {
  case_failure_at_first_step();
  case_failure_at_fourth_step();
  case_every_step_loaded();
  case_array_length();

  if (failures != 0) {
    printf("\n%d report chain check(s) failed\n", failures);
    return 1;
  }
  printf("\nreport chain: every check passed\n");
  return 0;
}
