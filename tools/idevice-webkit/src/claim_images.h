#ifndef IDEVICE_WEBKIT_CLAIM_IMAGES_H
#define IDEVICE_WEBKIT_CLAIM_IMAGES_H

#include "types.h"

#define claim_image_count 7
#define claim_path_capacity 256
#define claim_error_capacity 768

/* jbctl marks this process debugged for as long as it lives. The marking is
   what lets the /var/jb frameworks be loaded below the platform's own dyld
   checks, and it needs no root: the device answered
   `Successfully marked proc of pid 31660 as debugged` to a process running as
   uid 501. Nothing is written to disk and no kernel state is changed; the
   marking stops existing when the process exits. system() is not available in
   these processes, and would run a shell that does not exist on the device
   (`/bin/sh` is absent), so the call is made with posix_spawn. */

#define jbctl_path "/var/jb/basebin/jbctl"
#define jbctl_argument "proc_set_debugged"

/* The path a hard-linked application names at load time. It is not in the
   seven below because the seven are the /var/jb copies; this one is asked for
   with RTLD_NOLOAD and answers the same way, since the /var/jb WebKit carries
   this install name. */

#define system_webkit_path "/System/Library/Frameworks/WebKit.framework/WebKit"

/* The seven images, in the only order that loads. The list is the single place
   the order is written; claim_images.c explains why each position is fixed. */

extern const char *const claim_image_paths[claim_image_count];

/* What one run of the sequence did, field by field, so a failed claim can be
   read out of the report without a debugger.

   marked           the jbctl child exited 0. False on every other outcome,
                    and mark_status says which one.
   mark_status      One of the mark_* values below, or 0 when no marking was
                    attempted — which happens on a skipped run and nowhere
                    else, so 0 there means "not tried" rather than "tried and
                    answered zero". It is a state rather than
                    the errno posix_spawn returned or the child's wait status,
                    because those come from two number spaces that overlap: a
                    spawn that failed with 3 and a wait that failed with 3 are
                    different outcomes under one number. The errno and the wait
                    status are kept in mark_error as text.
   mark_error       the text written beside mark_status, naming the call that
                    produced the state, with the number that call returned.
   skipped          the sequence did not run because the system WebKit image was
                    already mapped; loaded is then 0 and failed_index 0.
   loaded           how many of the seven loaded before the loop stopped.
   failed_index     1-based position of the image whose dlopen answered NULL,
                    0 when all seven loaded.
   failed_path      that image's path as this file spells it, empty when no
                    image failed.
   error            dlerror's text for that dlopen.

   The mark_* values are distinct so a reader can tell "the child was never
   started" from "the child ran and failed" from "the wait timed out" without
   parsing text. */

#define mark_done 1
#define mark_spawn_failed 2
#define mark_wait_failed 3
#define mark_child_running 4
#define mark_child_nonzero 5
#define mark_init_failed 6

typedef struct claim_result {
  bool marked;
  i32 mark_status;
  char mark_error[claim_error_capacity];
  bool skipped;
  u32 loaded;
  u32 failed_index;
  char failed_path[claim_path_capacity];
  char error[claim_error_capacity];
} claim_result;

/* Answers whether the system WebKit image is already mapped in this process.
   The claim is gated on the answer, so library_find has a production caller:
   the path is asked for with RTLD_NOLOAD, and because the /var/jb WebKit
   carries the system install name, a non-NULL handle means the system image is
   resident. The path is a parameter rather than a read of system_webkit_path
   so the function is answerable about any path on its own, which is what lets
   a test drive it without a device. */

bool claim_system_webkit_present(const char *systemWebKitPath);

/* Marks this process debugged and then loads the seven images in order. The
   first image that fails stops the loop: the images after it are not tried,
   because a later image loaded over a missing predecessor is the mixed state
   the sequence exists to prevent. The caller decides whether to run this at
   all; this function does not look at the filter.

   The sequence is skipped, not attempted, when claim_system_webkit_present is
   true. That is the hard-linked host: an application that links WebKit as a
   load-time dependency has the system image mapped before any constructor
   runs, and starting the seven loads there would enter a state whose effects
   are not measured. Three of them do load — JavaScriptCore, libwebrtc and
   libANGLE — so the process receives an 18.3 JavaScriptCore with RTLD_GLOBAL
   beside the 16.1 one it is already running on, and the four that follow fail
   to bind against it. The probe that produced those three load results read
   load outcomes and a WebKit image count of 1; it did not count duplicate
   JavaScriptCore classes and it did not test whether the process survives with
   both mapped. A skipped run records itself, so the report still says the
   decision was made and which host shape produced it. What a hard-linked host
   needs is the dyld override patch, which claim_images.c describes.

   In a host that has not mapped WebKit yet the sequence runs and, as measured,
   wins the dedupe by install name and loads all seven. */

void claim_images_run(claim_result *result);

#endif
