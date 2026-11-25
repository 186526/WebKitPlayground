#include "claim_images.h"

#include "textkit.h"
#include "types.h"

/* The two conditions below were measured on the device (iPad14,4 / iPadOS
   16.1 / Dopamine rootless, mobile@192.168.2.136, uid 501). Each of them is
   the reason a position in the list is fixed, and each of them is invisible
   from the file names.

   1. libwebrtc and libANGLE must be loaded before WebCore.

   The /var/jb WebCore records both as weak dependencies whose install names
   point at the system paths, because the payload was built with those names:

     /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib (weak)
     /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib (weak)

   dyld resolves a weak dependency when the image is loaded, so loading WebCore
   first brings the system copies of both into the process from the shared
   cache. A later dlopen of the /var/jb copies then maps a second image of
   each, and the process holds two libwebrtc and two libANGLE. The probe's
   reading of that order was the same 7/7 loads with 31 lines of

     Class WK_RTC... is implemented in both ... One of the two will be used.

   Loading the two first makes dyld resolve WebCore's weak dependencies to the
   images already in the process. The measured count in that order is 0.

   2. The order cannot be reversed after a system WebKit has loaded.

   A process that asks for /System/Library/Frameworks/WebKit.framework/WebKit
   first gets the 16.1 framework. Asking for the /var/jb copy afterwards fails
   with this text, which is quoted as the probe printed it:

     Symbol not found: _WTFSignpostIndirectLoggingEnabled
     Referenced from: 787DFAB1-... /procursus/Library/Frameworks/WebKit.framework/WebKit
     Expected in:     D309AA36-... /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore

   The 18.x WebKit exports a WTF signpost variable that only the 18.x
   JavaScriptCore defines; the 16.1 system JavaScriptCore has no such symbol,
   and dyld refuses to leave an undefined data symbol. The reverse direction
   works only because the /var/jb JavaScriptCore is claimed first: WebKit then
   binds against it, and a later request for the system WebKit is answered with
   the already loaded image.

   3. dyld dedupes by install name, which is what makes the claim hold.

   The /var/jb images claim the system install names (that is the rewrite
   another mission makes in the payload), so after the seven loads a request
   for /System/Library/Frameworks/WebKit.framework/WebKit returns the image
   already loaded from /var/jb, not a second one. The probe read
   `handle equality: SAME` and an image count that did not move, and the
   after-claim image was the /var/jb one (UUID 787DFAB1).

   So the claim has to run in the constructor, before the host's own code asks
   for WebKit; a claim made afterwards is the failure text above. */

const char *const claim_image_paths[claim_image_count] = {
    "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
    "/var/jb/usr/lib/libwebrtc.dylib",
    "/var/jb/usr/lib/libANGLE-shared.dylib",
    "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
    "/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
    "/var/jb/Library/Frameworks/WebGPU.framework/WebGPU",
    "/var/jb/Library/Frameworks/WebKit.framework/WebKit",
};

/* RTLD_NOW resolves every symbol at load time, so a missing symbol fails this
   dlopen instead of faulting at the first call; RTLD_GLOBAL puts the image's
   symbols in the global scope, which is what a later load of a sibling image
   resolves against. Darwin's values: LAZY 1, NOW 2, GLOBAL 8. */

#define rtld_now 2
#define rtld_global 8

/* The dylib is built without the iOS SDK, so the spawn calls are declared here
   with the arm64e ABI's types. The attribute and file-action objects are
   opaque; both are initialised below rather than passed as NULL, because the
   initialised form is the one Darwin documents and a NULL there is an argument
   the implementation would have to special-case. */

extern void *dlopen(const char *path, int mode);
extern char *dlerror(void);
extern i32 posix_spawn(i32 *pid, const char *path, const void *fileActions,
                       const void *attributes, char *const arguments[],
                       char *const environment[]);
extern i32 posix_spawnattr_init(void *attributes);
extern i32 posix_spawnattr_destroy(void *attributes);
extern i32 posix_spawn_file_actions_init(void *actions);
extern i32 posix_spawn_file_actions_destroy(void *actions);
extern i32 waitpid(i32 pid, i32 *status, i32 options);
extern i32 getpid(void);
extern i32 usleep(u32 microseconds);

/* The opaque objects are 336 bytes in Darwin's headers; a wider buffer keeps
   the two initialisers within what they are given whatever the header says. */

typedef struct spawn_attributes {
  u64 words[64];
} spawn_attributes;

typedef struct spawn_file_actions {
  u64 words[16];
} spawn_file_actions;

#define wait_no_hang 0x00000001

/* A child that exits non-zero has no errno of its own; its wait status is
   recorded instead, and stdout/stderr are left where the host pointed them.
   The environment is one empty entry rather than a NULL pointer, so the child
   is started with an empty environment instead of a pointer the call would
   have to argue about. */

static char *const empty_environment[] = {NULL};

/* jbctl marks a process and exits; it has no reason to take long. The wait is
   polled with WNOHANG up to this many five-millisecond steps (half a second),
   because the constructor runs inside the host's own launch and an unbounded
   wait on a child that never exits would hold the application at startup. A
   child still running at the end is left as it is: it is a marking this
   process does not have, and the process goes on to the images anyway. */

#define wait_step_microseconds (5u * 1000u)
#define wait_steps 100u

/* errno values are positive, and posix_spawn returns the failing errno, so a
   failed spawn is recorded as the number it returned. The report then names
   the call that failed and the value; a missing file and a refused permission
   differ in that value alone. */

static void number_text(char *out, usize capacity, i64 value) {
  char digits[24];
  usize count = 0;
  out[0] = 0;
  if (value < 0) {
    value = -value;
    if (capacity > 1) {
      out[0] = '-';
      out[1] = 0;
    }
  }
  while (value > 0 && count < sizeof(digits)) {
    digits[count++] = (char)('0' + (int)(value % 10));
    value /= 10;
  }
  if (count == 0) {
    digits[count++] = '0';
  }
  usize written = text_length(out);
  while (count > 0 && written + 1 < capacity) {
    out[written++] = digits[--count];
  }
  out[written] = 0;
}

static void append(char *out, usize capacity, const char *text) {
  usize used = text_length(out);
  usize index = 0;
  while (text[index] != 0 && used + 1 < capacity) {
    out[used++] = text[index++];
  }
  out[used] = 0;
}

static void append_number(char *out, usize capacity, i64 value) {
  char number[24];
  number_text(number, sizeof(number), value);
  append(out, capacity, number);
}

/* The marking is attempted first and its outcome is recorded either way. A
   process whose marking failed still runs the seven loads, because the report
   then shows which images loaded without it; skipping them would leave the
   report saying nothing about the images at all. */

static void mark_this_process(claim_result *result) {
  char pid_text[16];
  number_text(pid_text, sizeof(pid_text), (i64)getpid());
  char *const arguments[] = {(char *)jbctl_path, (char *)jbctl_argument, pid_text, NULL};
  spawn_attributes attributes;
  spawn_file_actions actions;
  if (posix_spawnattr_init(&attributes) != 0 || posix_spawn_file_actions_init(&actions) != 0) {
    result->marked = false;
    result->mark_status = -1;
    append(result->mark_error, sizeof(result->mark_error), "posix_spawn attribute init failed");
    return;
  }
  i32 child = 0;
  i32 spawned =
      posix_spawn(&child, jbctl_path, &actions, &attributes, arguments, empty_environment);
  posix_spawn_file_actions_destroy(&actions);
  posix_spawnattr_destroy(&attributes);
  if (spawned != 0) {
    result->marked = false;
    result->mark_status = spawned;
    append(result->mark_error, sizeof(result->mark_error), "posix_spawn ");
    append(result->mark_error, sizeof(result->mark_error), jbctl_path);
    append(result->mark_error, sizeof(result->mark_error), " returned ");
    append_number(result->mark_error, sizeof(result->mark_error), spawned);
    return;
  }
  /* The wait is polled so a child that never exits cannot hold the host at
     startup. The last answer of each step is kept: waitpid reports a child
     that exited, and -1 an error; 0 is the child still running. */
  i32 status = 0;
  i32 waited = 0;
  for (u32 step = 0; step < wait_steps && waited == 0; step++) {
    waited = waitpid(child, &status, wait_no_hang);
    if (waited == 0) {
      usleep(wait_step_microseconds);
    }
  }
  if (waited == 0) {
    result->marked = false;
    result->mark_status = 0;
    append(result->mark_error, sizeof(result->mark_error),
           "jbctl still running after the wait; not waited for");
    return;
  }
  if (waited != child) {
    result->marked = false;
    result->mark_status = waited;
    append(result->mark_error, sizeof(result->mark_error), "waitpid returned ");
    append_number(result->mark_error, sizeof(result->mark_error), waited);
    return;
  }
  result->mark_status = status;
  result->marked = status == 0;
  if (!result->marked) {
    append(result->mark_error, sizeof(result->mark_error), "jbctl exited with wait status ");
    append_number(result->mark_error, sizeof(result->mark_error), status);
  }
}

void claim_images_run(claim_result *result) {
  text_set(result, 0, sizeof(*result));
  mark_this_process(result);
  for (u32 index = 0; index < claim_image_count; index++) {
    const char *path = claim_image_paths[index];
    void *handle = dlopen(path, rtld_now | rtld_global);
    if (handle == NULL) {
      result->failed_index = index + 1;
      text_copy(result->failed_path, path, sizeof(result->failed_path));
      const char *error = dlerror();
      text_copy(result->error, error == NULL ? "" : error, sizeof(result->error));
      return;
    }
    result->loaded = index + 1;
  }
}
