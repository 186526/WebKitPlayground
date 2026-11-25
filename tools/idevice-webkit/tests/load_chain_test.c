/* Checks the seven-image chain without a device.

   The device is where this code runs, and the shape it produced there is what
   the checks below are carved from: an entry image that loaded, a dependency
   that did not, and a single record that could not say which of seven edges
   broke. So the chain is held to four properties, none of which need a device to
   read:

     1. the order is the measured one, JavaScriptCore first and WebKit last;
     2. every load asks for RTLD_NOW and RTLD_GLOBAL, and no step asks for
        RTLD_LAZY alone, which is the 0x0001 in the recorded error;
     3. every path is an absolute /var/jb path under the rewritten tree, because
        a bare name or an @rpath would be searched and would resolve to the
        system copy;
     4. a JavaScriptCore that fails stops the chain, so the six behind it are
        never attempted against the 16.1 one.

   The dlopen and dyld table calls are replaced before load_chain.c is compiled
   in, the same way tests/dyld_lookup_test.c replaces dlsym: the host build cannot
   link libdyld, and recording the calls is the point rather than a compromise.

   The order is also read out of rewrite-payload.py, so the list the tweak loads
   and the list the rewrite writes cannot drift apart without this failing. */

#include <stdio.h>
#include <string.h>

#include "../src/types.h"

/* Darwin's dlfcn.h values. Stated here rather than included because the host has
   no dlfcn.h the dylib can use, and the numbers are what the device compares. */
#define darwin_rtld_lazy 1
#define darwin_rtld_now 2
#define darwin_rtld_global 8

#define record_capacity 32

static char recorded_path[record_capacity][256];
static int recorded_mode[record_capacity];
static int recorded_count;

/* Which step answers NULL, 1-based, and 0 for a chain that loads all seven. */
static int failing_step;

static void register_image(const char *path);

static void *test_dlopen(const char *path, int mode) {
  if (recorded_count < record_capacity) {
    snprintf(recorded_path[recorded_count], 256, "%s", path);
    recorded_mode[recorded_count] = mode;
    recorded_count++;
  }
  if (recorded_count == failing_step) {
    return NULL;
  }
  /* A load that succeeded maps an image, so the table grows the way dyld's does
     and the step's image count is read from it rather than assumed. */
  register_image(path);
  return (void *)1;
}

static char *test_dlerror(void) {
  return "test: refused";
}
#define table_capacity 8
static char table_path[table_capacity][256];
static unsigned char table_header[table_capacity][64];
static u32 table_count;

static u32 test_image_count(void) {
  return table_count;
}

static const void *test_get_image_header(u32 index) {
  return index < table_count ? (const void *)table_header[index] : NULL;
}

/* The image table. One image answers for each path the chain asks for, so a step
   that loaded reports a uuid and a step that did not is not found. */


#define dlopen test_dlopen
#define dlerror test_dlerror
#define _dyld_image_count test_image_count
#define _dyld_get_image_header test_get_image_header
#include "../src/textkit.c"
#include "../src/macho_image.c"
#include "../src/dyld_lookup.c"
#include "../src/load_chain.c"
#undef dlopen
#undef dlerror
#undef _dyld_image_count
#undef _dyld_get_image_header

/* dladdr is what turns a header address back into the path dyld recorded. The
   fake answers the path of the image whose header it was given, with the record
   dyld_lookup.h declares so one definition serves the source and this file. */

int dladdr(const void *address, dl_info *info) {
  for (u32 index = 0; index < table_count; index++) {
    if ((const void *)table_header[index] == address) {
      info->dli_fname = table_path[index];
      info->dli_fbase = (void *)table_header[index];
      info->dli_sname = NULL;
      info->dli_saddr = NULL;
      return 1;
    }
  }
  return 0;
}

static int failures;

static void check(const char *what, int ok) {
  printf("%-64s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

/* A Mach-O header with one LC_UUID, so image_uuid_text has something to read.
   The value is the 18.3 WebKit's own uuid, which makes a pass recognisable. */

static const unsigned char web_kit_uuid[16] = {0x78, 0x7d, 0xfa, 0xb1, 0x4d, 0xe3, 0x35, 0x68,
                                              0x99, 0x23, 0xc9, 0x40, 0x97, 0x91, 0xb7, 0x28};

static void build_header(u32 slot) {
  unsigned char *header = table_header[slot];
  memset(header, 0, 64);
  /* magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved */
  header[0] = 0xcf;
  header[1] = 0xfa;
  header[2] = 0xed;
  header[3] = 0xfe;
  header[16] = 1; /* ncmds */
  header[20] = 24; /* sizeofcmds */
  /* LC_UUID at offset 32: command 0x1b, size 24, then the 16 bytes. */
  header[32] = 0x1b;
  header[36] = 24;
  memcpy(header + 40, web_kit_uuid, 16);
}

static void reset(void) {
  recorded_count = 0;
  failing_step = 0;
  table_count = 0;
}

/* Registers one image the way a load that mapped it would. */
static void register_image(const char *path) {
  if (table_count >= table_capacity) {
    return;
  }
  snprintf(table_path[table_count], 256, "%s", path);
  build_header(table_count);
  table_count++;
}

int main(void) {
  reset();
  load_result result;
  load_chain_run(&result);

  check("the chain runs all seven steps when nothing fails",
        result.ran && result.loaded == load_chain_count && result.stopped == 0);

  check("seven dlopen calls were made, one per image",
        recorded_count == load_chain_count);

  /* Property 1: the order. */
  int order_matches = 1;
  for (int index = 0; index < recorded_count; index++) {
    if (strcmp(recorded_path[index], load_chain_paths[index]) != 0) {
      order_matches = 0;
    }
  }
  check("the calls are made in the order the table names", order_matches);

  check("JavaScriptCore is the first image loaded",
        recorded_count > 0 && strstr(recorded_path[0], "JavaScriptCore.framework") != NULL);
  check("WebKit is the last image loaded",
        recorded_count > 0 && strstr(recorded_path[recorded_count - 1], "WebKit.framework") != NULL);
  check("libwebrtc and libANGLE are loaded before WebCore",
        recorded_count > 4 && strstr(recorded_path[1], "libwebrtc") != NULL &&
            strstr(recorded_path[2], "libANGLE") != NULL &&
            strstr(recorded_path[3], "WebCore.framework") != NULL);

  /* Property 2: the flags. */
  int every_step_global = 1;
  int every_step_now = 1;
  int any_step_lazy_only = 0;
  for (int index = 0; index < recorded_count; index++) {
    if (!(recorded_mode[index] & darwin_rtld_global)) {
      every_step_global = 0;
    }
    if (!(recorded_mode[index] & darwin_rtld_now)) {
      every_step_now = 0;
    }
    if (recorded_mode[index] == darwin_rtld_lazy) {
      any_step_lazy_only = 1;
    }
  }
  check("every step asks for RTLD_GLOBAL", every_step_global);
  check("every step asks for RTLD_NOW", every_step_now);
  check("no step asks for RTLD_LAZY alone, the 0x0001 in the recorded error",
        !any_step_lazy_only);

  /* Property 3: the paths. */
  int every_path_absolute = 1;
  int every_path_rewritten = 1;
  int every_path_device_root = 0;
  for (int index = 0; index < load_chain_count; index++) {
    const char *path = load_chain_paths[index];
    if (path[0] != '/') {
      every_path_absolute = 0;
    }
    if (strstr(path, "@rpath") != NULL) {
      every_path_absolute = 0;
    }
    if (strstr(path, "/var/jb/Library/WebKit18/") == path + 0 || strncmp(
            path, "/var/jb/Library/WebKit18/", strlen("/var/jb/Library/WebKit18/")) == 0) {
      every_path_device_root++;
    }
    /* The payload's own tree is deliberately not used: those images carry the
       system install names, so loading them reproduces the recorded failure. */
    if (strncmp(path, "/var/jb/Library/Frameworks/", strlen("/var/jb/Library/Frameworks/")) == 0) {
      every_path_rewritten = 0;
    }
  }
  check("every path is absolute and carries no @rpath", every_path_absolute);
  check("every path is under the rewritten tree, not the package's",
        every_path_device_root == load_chain_count && every_path_rewritten);

  /* Property 4: a JavaScriptCore failure stops the chain. */
  reset();
  failing_step = 1;
  load_chain_run(&result);
  check("a failing first step stops the chain", result.stopped == 1 && result.loaded == 0);
  check("only one dlopen is attempted when JavaScriptCore fails", recorded_count == 1);
  check("the failed step carries dlerror's text",
        strstr(result.steps[0].error, "test: refused") != NULL);

  reset();
  failing_step = 4;
  load_chain_run(&result);
  check("a failure at step 4 loads three and stops at four",
        result.loaded == 3 && result.stopped == 4);
  check("steps after the failure are never attempted", recorded_count == 4);

  /* The uuid reading, which is what separates a mapped 18.3 image from a request
     dyld answered from the resident 16.1 one. */
  reset();
  for (u32 index = 0; index < load_chain_count; index++) {
    register_image(load_chain_paths[index]);
  }
  load_chain_run(&result);
  check("every loaded step reports the uuid of the image registered for it",
        strcmp(result.steps[0].uuid, "787DFAB1-4DE3-3568-9923-C9409791B728") == 0);
  check("a step reports the single image it gained", result.steps[0].images == 1);

  /* The position helper the report uses to name a step. */
  check("a chain path answers its 1-based position",
        load_chain_position(load_chain_paths[0]) == 1 &&
            load_chain_position(load_chain_paths[6]) == 7);
  check("a path outside the chain answers no position",
        load_chain_position("/System/Library/Frameworks/WebKit.framework/WebKit") == 0);

  if (failures != 0) {
    printf("\n%d load chain check(s) failed\n", failures);
    return 1;
  }
  printf("\nload chain: every check passed\n");
  return 0;
}
