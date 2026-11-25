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

/* The seven images, in the only order that loads. The list is the single place
   the order is written; claim_images.c explains why each position is fixed. */

extern const char *const claim_image_paths[claim_image_count];

/* What one run of the sequence did, field by field, so a failed claim can be
   read out of the report without a debugger.

   marked           the jbctl child exited 0; every other value means no
                    marking, and the images below are then still attempted so
                    the report shows what the absence of the marking did.
   mark_status      0 when marked; the positive errno when posix_spawn could
                    not start the child; the raw wait status when it started
                    and did not exit 0.
   mark_error       the text written beside mark_status, with the path that
                    failed to start where that is the answer.
   loaded           how many of the seven loaded before the loop stopped;
                    equals claim_image_count exactly when nothing failed.
   failed_index     1-based position of the image whose dlopen answered NULL,
                    0 when all seven loaded.
   failed_path      that image's path as this file spells it, empty when no
                    image failed.
   error            dlerror's text for that dlopen. The two messages that were
                    measured on the device are reproduced in claim_images.c,
                    because they are the reason the order is fixed. */

typedef struct claim_result {
  bool marked;
  i32 mark_status;
  char mark_error[claim_error_capacity];
  u32 loaded;
  u32 failed_index;
  char failed_path[claim_path_capacity];
  char error[claim_error_capacity];
} claim_result;

/* Marks this process debugged and then loads the seven images in order. The
   first image that fails stops the loop: the images after it are not tried,
   because a later image loaded over a missing predecessor is the mixed state
   the sequence exists to prevent. The caller decides whether to run this at
   all; this function does not look at the filter.

   This does not make a hard-linked host use 18.3. An application that links
   WebKit as a load-time dependency has the system image mapped before any
   constructor runs, and this sequence then loads three of the seven and fails
   the rest with Symbol not found. A loaded count of 3 with failed_index 4 is
   that shape, not a broken order; the fix for it is the dyld override patch,
   which claim_images.c describes. In a host that lazily dlopen()s WebKit the
   sequence wins the dedupe and loads all seven. */

void claim_images_run(claim_result *result);

#endif
