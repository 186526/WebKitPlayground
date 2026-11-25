/* Checks the order of the seven images, the gate that decides whether the
   sequence runs, and the failure answers of the claim, without a device.

   src/claim_images.c declares the platform calls it makes itself (the dylib is
   built without the iOS SDK), so renaming them before the include turns the
   declarations and the calls into the recorders below. The recorders answer
   the way the device does: library_find answers a handle only for a case that
   says the image is already mapped, dlopen answers a handle for every path
   except the one a case is told to fail, and posix_spawn answers 0 with a
   child pid unless a case is told to answer an errno. Everything else — the
   order array, the gate, the stop-at-first-failure rule, the marking
   arguments — is the code under test, unchanged.

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
static i32 spawned_pid;
static i32 wait_answer_pid;
static i32 wait_status;
static int wait_calls;
static int wait_running_steps;
static i32 reported_pid = 4242;
static int attribute_init_calls;
static int attribute_init_answer;
static int attribute_destroy_calls;
static int action_init_calls;
static int action_init_answer;
static int action_destroy_calls;
static int sleep_calls;
static int find_calls;
static char find_path[256];
static int find_present;
static i32 last_waited_pid;

static void test_reset(void) {
  open_calls = 0;
  fail_at = -1;
  spawn_answer = 0;
  wait_calls = 0;
  spawned_pid = 900;
  wait_answer_pid = 900;
  last_waited_pid = 0;
  wait_status = 0;
  wait_running_steps = 0;
  spawned_argument_count = 0;
  spawned_path[0] = 0;
  error_text[0] = 0;
  attribute_init_calls = 0;
  attribute_init_answer = 0;
  attribute_destroy_calls = 0;
  action_init_calls = 0;
  action_init_answer = 0;
  action_destroy_calls = 0;
  sleep_calls = 0;
  find_calls = 0;
  find_path[0] = 0;
  find_present = 0;
  for (int index = 0; index < recorded_paths; index++) {
    opened_paths[index][0] = 0;
    opened_modes[index] = 0;
  }
}

/* The gate's question, answered the way dyld answers it: a handle only when the
   case says the image is already in the process. The path is recorded because
   the gate is only worth anything if it asks about the system path — a
   non-NULL answer for any path would gate on the wrong question. */

static void *test_library_find(const char *path) {
  find_calls++;
  snprintf(find_path, sizeof(find_path), "%s", path);
  if (!find_present) {
    return 0;
  }
  static char found;
  return &found;
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
  *pid = spawned_pid;
  return 0;
}

static i32 test_waitpid(i32 pid, i32 *status, i32 options) {
  (void)options;
  wait_calls++;
  last_waited_pid = pid;
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
  return attribute_init_answer;
}

static i32 test_posix_spawnattr_destroy(void *attributes) {
  attribute_destroy_calls++;
  (void)attributes;
  return 0;
}

static i32 test_posix_spawn_file_actions_init(void *actions) {
  action_init_calls++;
  (void)actions;
  return action_init_answer;
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
#define library_find test_library_find
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
#undef library_find
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

  /* The gate: the system WebKit image is already mapped, which is the
     hard-linked host. The sequence must not run at all — no jbctl spawn and no
     dlopen — because three of the seven do load, and the first of them is
     JavaScriptCore. The ask is recorded so the test also holds the gate to the
     system path rather than to whatever the seven list happens to hold. */
  claim_result result;
  test_reset();
  find_present = 1;
  claim_images_run(&result);
  check("the gate asks about the system WebKit path",
        find_calls == 1 && strcmp(find_path, "/System/Library/Frameworks/WebKit.framework/WebKit") == 0);
  check("a mapped system image skips the sequence", result.skipped == 1);
  check("  no image is loaded", result.loaded == 0);
  check("  no image is named as failed", result.failed_index == 0);
  check("  no marking is recorded as done", result.marked == 0 && result.mark_status == 0);
  check("  no path is left behind", result.failed_path[0] == 0);
  check("  no error text is left behind", result.error[0] == 0);
  check("  nothing is dlopened", open_calls == 0);
  check("  jbctl is not spawned", spawned_path[0] == 0 && attribute_init_calls == 0);

  /* The gate answering NULL is the host the sequence fits: it runs, and the
     skip stays false so the report says the sequence was attempted. */
  test_reset();
  claim_images_run(&result);
  check("a host without the system image runs the sequence", result.skipped == 0);
  check("  and the marking is attempted", attribute_init_calls == 1);

  /* A full run: the marking succeeds and every image loads. The modes are
     checked too, because RTLD_LAZY (1) would defer the symbol resolution this
     sequence exists to force, and a load without RTLD_GLOBAL leaves a later
     sibling unable to bind against the images already claimed. */
  test_reset();
  claim_images_run(&result);
  check("the marking is recorded as done",
        result.marked == 1 && result.mark_status == mark_done);
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

  /* An attribute object that would not initialise stops the marking at once:
     nothing is spawned, and the error names that call rather than the pair. */
  test_reset();
  attribute_init_answer = 12;
  claim_images_run(&result);
  check("a refused attribute init is not recorded as a marking", result.marked == 0);
  check("  the state is the init failure", result.mark_status == mark_init_failed);
  check("  the error names posix_spawnattr_init",
        strstr(result.mark_error, "posix_spawnattr_init failed") != NULL);
  check("  the file actions are never initialised", action_init_calls == 0);
  check("  nothing is spawned", spawned_path[0] == 0);
  check("  and the images are still claimed", result.loaded == 7);

  /* A failure of the second initialiser leaves the first object allocated. The
     path has to destroy it and say which of the two calls failed, since the
     message used to name the attribute object for both. */
  test_reset();
  action_init_answer = 22;
  claim_images_run(&result);
  check("a refused file-actions init is not recorded as a marking", result.marked == 0);
  check("  the state is the init failure", result.mark_status == mark_init_failed);
  check("  the error names posix_spawn_file_actions_init",
        strstr(result.mark_error, "posix_spawn_file_actions_init failed") != NULL);
  check("  the attribute object is destroyed", attribute_destroy_calls == 1);
  check("  the file actions are not destroyed", action_destroy_calls == 0);
  check("  nothing is spawned", spawned_path[0] == 0);
  check("  and the images are still claimed", result.loaded == 7);

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

  /* The load outcome the device measured in a hard-linked host, as the loop
     would report it if the gate let the run through: JavaScriptCore, libwebrtc
     and libANGLE load, then WebCore fails with the WTF signpost symbol missing
     because the system JavaScriptCore is what dyld already bound. The gate is
     what keeps the sequence out of this shape; the test pins the fields that
     would identify it if the gate were ever wrong. The recorder answers exactly
     as the device did. */
  test_reset();
  fail_at = 3;
  snprintf(error_text, sizeof(error_text), "%s",
           "Symbol not found: _WTFSignpostIndirectLoggingEnabled");
  claim_images_run(&result);
  check("that shape loads three and stops at WebCore", result.loaded == 3);
  check("  the stop is recorded at position 4", result.failed_index == 4);
  check("  the failing image is WebCore",
        strcmp(result.failed_path,
               "/var/jb/Library/Frameworks/WebCore.framework/WebCore") == 0);
  check("  the missing symbol is the one that shape reports",
        strstr(result.error, "_WTFSignpostIndirectLoggingEnabled") != NULL);

  /* posix_spawn's return value is the failing errno. A marking that never
     started is recorded as its own state, and the errno is kept in the error
     text where it can still be read. */
  test_reset();
  spawn_answer = 2;
  claim_images_run(&result);
  check("a refused spawn is not recorded as a marking", result.marked == 0);
  check("  the state is the spawn failure", result.mark_status == mark_spawn_failed);
  check("  the error names the call and its answer",
        strstr(result.mark_error, "posix_spawn") != NULL &&
            strstr(result.mark_error, "/var/jb/basebin/jbctl") != NULL &&
            strstr(result.mark_error, "2") != NULL);
  check("  the images are still claimed without the marking", result.loaded == 7);

  /* A child that ran and exited non-zero is a different answer from one that
     never started, and it must not be recorded as a marking either. The wait
     status lives in the error text now that the state is an enum. */
  test_reset();
  wait_status = 512;
  claim_images_run(&result);
  check("a non-zero child exit is not recorded as a marking", result.marked == 0);
  check("  the state is the non-zero exit", result.mark_status == mark_child_nonzero);
  check("  the wait status is recorded", strstr(result.mark_error, "512") != NULL);
  check("  waitpid was called once", wait_calls == 1);
  check("  the error says the child ran", strstr(result.mark_error, "jbctl") != NULL);

  /* A child that never exits must not hold the host at startup. The wait is
     polled a bounded number of times and then gives up: the marking is
     recorded as absent with its own state, the report says the child was left
     running, and the images are still claimed. The step count is the module's
     own bound, read from what the poll actually did rather than a number
     written here. */
  test_reset();
  wait_running_steps = 1000000;
  claim_images_run(&result);
  check("a child that never exits does not hold the constructor",
        wait_calls > 0 && wait_calls <= 100 && sleep_calls + 1 >= wait_calls);
  check("  the marking is not recorded as done", result.marked == 0);
  check("  the state is the child still running", result.mark_status == mark_child_running);
  check("  the report says the child was left running",
        strstr(result.mark_error, "still running") != NULL);
  check("  the images are still claimed", result.loaded == 7);

  /* waitpid answering a pid that is not the child is its own failure: the call
     did not report the child, so nothing about the marking is known. The
     recorder answers 901 for a child started as 900, which is the only way to
     reach this branch — with both numbers equal the condition is never true.
     The case pins the state, so setting it to mark_done the way the errno
     states once were set cannot pass. */
  test_reset();
  spawned_pid = 900;
  wait_answer_pid = 901;
  claim_images_run(&result);
  check("a wait answer for another pid is not recorded as a marking", result.marked == 0);
  check("  the state is the wait failure", result.mark_status == mark_wait_failed);
  check("  the error names waitpid and the pid it answered",
        strstr(result.mark_error, "waitpid returned") != NULL &&
            strstr(result.mark_error, "901") != NULL);
  check("  the child asked about was the one spawned", last_waited_pid == 900);
  check("  the images are still claimed", result.loaded == 7);

  /* The states are distinct numbers, and none of them is zero: a zero state
     would read as the success value in any report that printed it. */
  check("the states are distinct and none is zero",
        mark_done != 0 && mark_spawn_failed != 0 && mark_wait_failed != 0 &&
            mark_child_running != 0 && mark_child_nonzero != 0 && mark_init_failed != 0 &&
            mark_done != mark_spawn_failed && mark_done != mark_wait_failed &&
            mark_done != mark_child_running && mark_done != mark_child_nonzero &&
            mark_done != mark_init_failed && mark_spawn_failed != mark_wait_failed &&
            mark_spawn_failed != mark_child_running && mark_spawn_failed != mark_child_nonzero &&
            mark_spawn_failed != mark_init_failed && mark_wait_failed != mark_child_running &&
            mark_wait_failed != mark_child_nonzero && mark_wait_failed != mark_init_failed &&
            mark_child_running != mark_child_nonzero &&
            mark_child_running != mark_init_failed && mark_child_nonzero != mark_init_failed);

  printf("failures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
