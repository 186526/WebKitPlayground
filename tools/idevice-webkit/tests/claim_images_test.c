/* Checks the order of the seven images and the two failure answers of the
   claim, without a device.

   src/claim_images.c declares the five platform calls itself (the dylib is
   built without the iOS SDK), so renaming them before the include turns the
   declarations and the calls into the recorders below. The recorders answer
   the way the device does: dlopen answers a handle for every path except the
   one a case is told to fail, and posix_spawn answers 0 with a child pid
   unless a case is told to answer an errno. Everything else — the order
   array, the stop-at-first-failure rule, the marking arguments — is the code
   under test, unchanged.

   The expected paths are written out again rather than read from the array,
   because a test that compared the array with itself would pass whatever the
   array held. Each one is the path the mission measured on the device, in the
   position it was measured at. */

#include <stdio.h>
#include <string.h>

#include "../src/types.h"

#define recorded_paths 16

static const char *expected_order[] = {
    "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
    "/var/jb/usr/lib/libwebrtc.dylib",
    "/var/jb/usr/lib/libANGLE-shared.dylib",
    "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
    "/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
    "/var/jb/Library/Frameworks/WebGPU.framework/WebGPU",
    "/var/jb/Library/Frameworks/WebKit.framework/WebKit",
};
#define expected_count (sizeof(expected_order) / sizeof(expected_order[0]))

static char opened_paths[recorded_paths][256];
static int opened_modes[recorded_paths];
static int open_calls;
static int fail_at = -1;
static char error_text[256];

static char spawned_path[256];
static char spawned_arguments[4][256];
static int spawned_argument_count;
static i32 spawn_answer;
static i32 wait_answer_pid;
static i32 wait_status;
static int wait_calls;
static int wait_running_steps;
static i32 reported_pid = 4242;
static int attribute_init_calls;
static int attribute_destroy_calls;
static int action_init_calls;
static int action_destroy_calls;
static int sleep_calls;

static void test_reset(void) {
  open_calls = 0;
  fail_at = -1;
  spawn_answer = 0;
  wait_calls = 0;
  wait_answer_pid = 900;
  wait_status = 0;
  wait_running_steps = 0;
  spawned_argument_count = 0;
  spawned_path[0] = 0;
  error_text[0] = 0;
  attribute_init_calls = 0;
  attribute_destroy_calls = 0;
  action_init_calls = 0;
  action_destroy_calls = 0;
  sleep_calls = 0;
  for (int index = 0; index < recorded_paths; index++) {
    opened_paths[index][0] = 0;
    opened_modes[index] = 0;
  }
}

static void *test_dlopen(const char *path, int mode) {
  int index = open_calls++;
  if (index < recorded_paths) {
    snprintf(opened_paths[index], sizeof(opened_paths[index]), "%s", path);
    opened_modes[index] = mode;
  }
  if (index == fail_at) {
    return 0;
  }
  static char handles[recorded_paths];
  return &handles[index < recorded_paths ? index : 0];
}

static char *test_dlerror(void) {
  return error_text;
}

static i32 test_posix_spawn(i32 *pid, const char *path, const void *fileActions,
                            const void *attributes, char *const arguments[],
                            char *const environment[]) {
  (void)fileActions;
  (void)attributes;
  (void)environment;
  snprintf(spawned_path, sizeof(spawned_path), "%s", path);
  spawned_argument_count = 0;
  for (int index = 0; index < 4 && arguments[index] != NULL; index++) {
    snprintf(spawned_arguments[index], sizeof(spawned_arguments[index]), "%s", arguments[index]);
    spawned_argument_count++;
  }
  if (spawn_answer != 0) {
    return spawn_answer;
  }
  *pid = wait_answer_pid;
  return 0;
}

static i32 test_waitpid(i32 pid, i32 *status, i32 options) {
  (void)pid;
  (void)options;
  wait_calls++;
  /* A child told to stay running answers 0, the way a poll with WNOHANG does
     while the child is alive; the status is only written once it exits. */
  if (wait_running_steps > 0) {
    wait_running_steps--;
    return 0;
  }
  *status = wait_status;
  return wait_answer_pid;
}

static i32 test_getpid(void) {
  return reported_pid;
}

static i32 test_posix_spawnattr_init(void *attributes) {
  attribute_init_calls++;
  (void)attributes;
  return 0;
}

static i32 test_posix_spawnattr_destroy(void *attributes) {
  attribute_destroy_calls++;
  (void)attributes;
  return 0;
}

static i32 test_posix_spawn_file_actions_init(void *actions) {
  action_init_calls++;
  (void)actions;
  return 0;
}

static i32 test_posix_spawn_file_actions_destroy(void *actions) {
  action_destroy_calls++;
  (void)actions;
  return 0;
}

static i32 test_usleep(u32 microseconds) {
  (void)microseconds;
  sleep_calls++;
  return 0;
}

#define dlopen test_dlopen
#define dlerror test_dlerror
#define posix_spawn test_posix_spawn
#define posix_spawnattr_init test_posix_spawnattr_init
#define posix_spawnattr_destroy test_posix_spawnattr_destroy
#define posix_spawn_file_actions_init test_posix_spawn_file_actions_init
#define posix_spawn_file_actions_destroy test_posix_spawn_file_actions_destroy
#define waitpid test_waitpid
#define getpid test_getpid
#define usleep test_usleep
#include "../src/claim_images.c"
#undef dlopen
#undef dlerror
#undef posix_spawn
#undef posix_spawnattr_init
#undef posix_spawnattr_destroy
#undef posix_spawn_file_actions_init
#undef posix_spawn_file_actions_destroy
#undef waitpid
#undef getpid
#undef usleep

static int failures;

static void check(const char *what, int ok) {
  printf("%-62s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

/* The index a path holds in the array under test, or -1. The first occurrence
   answers, which is what a duplicate would hide; the duplicate itself is
   caught by the position-by-position comparison below. */

static int index_of(const char *path) {
  for (int index = 0; index < claim_image_count; index++) {
    if (strcmp(claim_image_paths[index], path) == 0) {
      return index;
    }
  }
  return -1;
}

int main(void) {
  check("the sequence holds exactly seven images",
        claim_image_count == 7 && expected_count == 7);

  /* The order is compared entry by entry against the list written above, in
     the spelling the mission recorded. A reordering, a renamed path or a
     dropped entry fails here. */
  for (int index = 0; index < (int)claim_image_count && index < (int)expected_count; index++) {
    char name[160];
    snprintf(name, sizeof(name), "position %d is %s", index + 1, expected_order[index]);
    check(name, strcmp(claim_image_paths[index], expected_order[index]) == 0);
  }

  /* The two dependencies that make the order more than a preference. WebCore's
     weak dependencies point at the system paths, so WebCore has to come after
     both, or dyld pulls the system copies in and the process ends up with two
     libwebrtc and two libANGLE. The measured counts are 0 duplicate-class
     warnings in this order and 31 with WebCore first; libwebrtc is in front of
     WebCore. */
  int webcore = index_of("/var/jb/Library/Frameworks/WebCore.framework/WebCore");
  int webrtc = index_of("/var/jb/usr/lib/libwebrtc.dylib");
  int angle = index_of("/var/jb/usr/lib/libANGLE-shared.dylib");
  int javascriptcore =
      index_of("/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore");
  int webkit = index_of("/var/jb/Library/Frameworks/WebKit.framework/WebKit");
  check("libwebrtc sits before WebCore", webrtc >= 0 && webcore >= 0 && webrtc < webcore);
  check("libANGLE-shared sits before WebCore", angle >= 0 && webcore >= 0 && angle < webcore);
  check("JavaScriptCore sits before WebKit", javascriptcore >= 0 && webkit >= 0 &&
                                                 javascriptcore < webkit);
  check("JavaScriptCore is claimed first", javascriptcore == 0);
  check("WebKit is claimed last", webkit == (int)claim_image_count - 1);

  /* A full run: the marking succeeds and every image loads. The modes are
     checked too, because RTLD_LAZY (1) would defer the symbol resolution this
     sequence exists to force, and a load without RTLD_GLOBAL leaves a later
     sibling unable to bind against the images already claimed. */
  test_reset();
  claim_result result;
  claim_images_run(&result);
  check("the marking is recorded as done", result.marked == 1 && result.mark_status == 0);
  check("  the marking records no error", result.mark_error[0] == 0);
  check("all seven images load", result.loaded == 7);
  check("  no image is named as failed", result.failed_index == 0);
  check("  no failed path is left behind", result.failed_path[0] == 0);
  check("  no dlerror text is left behind", result.error[0] == 0);
  check("every image is opened with RTLD_NOW|RTLD_GLOBAL",
        open_calls == 7 && opened_modes[0] == (2 | 8) && opened_modes[6] == (2 | 8));
  int opened_in_order = open_calls == 7;
  for (int index = 0; index < 7 && opened_in_order; index++) {
    opened_in_order = strcmp(opened_paths[index], expected_order[index]) == 0;
  }
  check("the seven loads are made in the listed order", opened_in_order);

  /* jbctl is spawned with three arguments and the process's own pid. A wildcard
     subcommand or a pid taken from the shell would mark the wrong process. */
  check("jbctl is spawned from its installed path",
        strcmp(spawned_path, "/var/jb/basebin/jbctl") == 0);
  check("  the subcommand is proc_set_debugged",
        spawned_argument_count >= 2 && strcmp(spawned_arguments[1], "proc_set_debugged") == 0);
  check("  the pid handed over is this process's",
        spawned_argument_count >= 3 && strcmp(spawned_arguments[2], "4242") == 0);
  /* The two opaque objects are initialised and destroyed around the spawn.
     Passing NULL there is what the implementation would have to special-case;
     an initialised pair is what Darwin documents, and a destroy that is missed
     leaks the objects into a constructor that never runs again. */
  check("the spawn attributes are initialised once", attribute_init_calls == 1);
  check("  and destroyed once", attribute_destroy_calls == 1);
  check("the file actions are initialised once", action_init_calls == 1);
  check("  and destroyed once", action_destroy_calls == 1);

  /* The first failure stops the sequence. A later image loaded over a missing
     predecessor is the mixed state the order exists to prevent, so the loop
     must not continue and must not report the images it never tried. */
  test_reset();
  fail_at = 2;
  snprintf(error_text, sizeof(error_text), "%s",
           "Symbol not found: _WTFSignpostIndirectLoggingEnabled");
  claim_images_run(&result);
  check("a failed image stops the sequence", open_calls == 3);
  check("  the loaded count is the images before it", result.loaded == 2);
  check("  the failure is named by its 1-based position", result.failed_index == 3);
  check("  the failed path is the image that answered nothing",
        strcmp(result.failed_path, "/var/jb/usr/lib/libANGLE-shared.dylib") == 0);
  check("  the dlerror text is recorded as it was given",
        strcmp(result.error, "Symbol not found: _WTFSignpostIndirectLoggingEnabled") == 0);
  check("  the images after it are not tried", open_calls == 3);

  /* The first image is a position of its own: no image loaded, and the
     failure is position 1 rather than 0. */
  test_reset();
  fail_at = 0;
  claim_images_run(&result);
  check("a failure at the first image records position 1", result.failed_index == 1);
  check("  and no image as loaded", result.loaded == 0);

  /* posix_spawn's return value is the failing errno. A marking that never
     started is recorded with that value instead of being read as success. */
  test_reset();
  spawn_answer = 2;
  claim_images_run(&result);
  check("a refused spawn is not recorded as a marking", result.marked == 0);
  check("  the errno posix_spawn returned is recorded", result.mark_status == 2);
  check("  the error names the call and its answer",
        strstr(result.mark_error, "posix_spawn") != NULL &&
            strstr(result.mark_error, "/var/jb/basebin/jbctl") != NULL &&
            strstr(result.mark_error, "2") != NULL);
  check("  the images are still claimed without the marking", result.loaded == 7);

  /* A child that ran and exited non-zero is a different answer from one that
     never started, and it must not be recorded as a marking either. */
  test_reset();
  wait_status = 512;
  claim_images_run(&result);
  check("a non-zero child exit is not recorded as a marking", result.marked == 0);
  check("  the wait status is recorded", result.mark_status == 512);
  check("  waitpid was called once", wait_calls == 1);
  check("  the error says the child ran", strstr(result.mark_error, "jbctl") != NULL);

  /* A child that never exits must not hold the host at startup. The wait is
     polled a bounded number of times and then gives up: the marking is
     recorded as absent, the report says the child was left running, and the
     images are still claimed. The step count is the module's own bound, read
     from what the poll actually did rather than a number written here. */
  test_reset();
  wait_running_steps = 1000000;
  claim_images_run(&result);
  check("a child that never exits does not hold the constructor",
        wait_calls > 0 && wait_calls <= 100 && sleep_calls + 1 >= wait_calls);
  check("  the marking is not recorded as done", result.marked == 0);
  check("  the report says the child was left running",
        strstr(result.mark_error, "still running") != NULL);
  check("  the images are still claimed", result.loaded == 7);

  printf("failures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
