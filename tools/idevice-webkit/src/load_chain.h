#ifndef IDEVICE_WEBKIT_LOAD_CHAIN_H
#define IDEVICE_WEBKIT_LOAD_CHAIN_H

#include "types.h"

/* The seven 18.3 images, loaded by explicit /var/jb path, in the order the
   device was measured to accept.

   WHY A CHAIN AND NOT ONE IMAGE

   The tweak used to dlopen WebKit and nothing else. The device answered:

     dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001):
       Symbol not found: _WTFSignpostIndirectLoggingEnabled
       Referenced from: <787DFAB1-...> .../WebKit.framework/WebKit
       Expected in:     <D309AA36-...> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore

   The entry image was the 18.3 one; the dependency it bound was not. A sibling
   worker measured a second instance of the same shape, with the missing symbol
   coming from WebCore instead:

     Symbol not found: __ZN3PAL16getNSShadowClassE
       Expected in: <D243ECF0-...> /System/Library/PrivateFrameworks/WebCore.framework/WebCore

   So the failure is not one bad edge. Every dependency one of the seven has on
   another of the seven has to resolve to the 18.3 copy, which is what
   rewrite-payload.py produces and what tests/load_chain_test.c asserts.

   WHY THESE PATHS AND NOT THE PACKAGE'S

   load_chain_paths names the rewritten tree under /var/jb/Library/WebKit18, not
   the payload under /var/jb/Library/Frameworks. The two differ in a way that
   matters: the payload's images carry the system install names and system
   dependency strings, so loading them binds the 16.1 siblings. The rewritten
   copies name themselves and each other by /var/jb paths. Loading the payload's
   paths would reproduce the recorded failure exactly.

   WHY THIS WORKS WITH NO HOOK AND NO ENVIRONMENT VARIABLE

   The device's basebin carries neither override hook, and the tweak sets no
   DYLD_* variable. Neither is needed for a literal path. dyld resolves a
   dependency by reducing it with ProcessConfig::PathOverrides::getFrameworkPartialPath
   and joining the result onto each DYLD_FRAMEWORK_PATH directory; when that
   variable is empty the candidate list is the original path alone, and a
   /var/jb path exists on disk. The shared cache is indexed by /System paths, so
   /var/jb is not in it and the Loader.cpp:565 gate that discards disk
   candidates for cached frameworks never engages. See
   docs/dopamine-basebin-webkit-override.md 第三节 and 第九节 for the
   getFrameworkPartialPath reimplementation and the reading of this very failure. */

#define load_chain_count 7

/* RTLD_GLOBAL, and why it is not optional.

   Darwin's values: RTLD_LAZY 1, RTLD_NOW 2, RTLD_GLOBAL 8. The tweak used
   RTLD_LAZY alone, which is the 0x0001 in the recorded error.

   RTLD_NOW makes a missing symbol fail the dlopen that is being attempted,
   instead of faulting at the first call through it. That turns an unreported
   crash into a step index and a symbol name in the report.

   RTLD_GLOBAL puts the image's exports in the global scope, where a later load
   resolves against them. Without it each image is a separate island and a
   sibling's lookup does not see it. This is the flag whose absence the device
   telemetry records, and it is what makes the 18.3 JavaScriptCore's exports
   reachable by the 18.3 WebCore and WebKit that follow it. */

#define load_mode_now 2
#define load_mode_global 8
#define load_chain_mode (load_mode_now | load_mode_global)

extern const char *const load_chain_paths[load_chain_count];

/* The step whose failure stops the chain.

   JavaScriptCore is position 1 and it is the one that must not be skipped over.
   It exports the WTF symbols the other six bind: WebCore and WebGPU both fail
   with Symbol not found: __ZN3WTF10StringImpl28createWithoutCopyingNonEmptyE...
   when loaded alone, and succeed once JavaScriptCore is resident. So a process
   whose 18.3 JavaScriptCore did not load has no version of the rest to try, and
   the six later steps are not attempted. */

#define load_chain_first 0

#define load_error_capacity 768
#define uuid_capacity 40

/* What one step did. The report carries one of these per step, filled in whether
   the load succeeded or failed, so a partial chain can be read back without a
   debugger.

     path      the path this step asked for, always absolute and under /var/jb
     loaded    dlopen answered a handle
     images    how many images the process gained across this step. 1 means the
               18.3 copy was mapped as an image of its own; 0 means dyld answered
               the request from an image already in the process, which is the
               dedupe case, and the uuid below then names the resident copy and
               not the one asked for.
     uuid      the LC_UUID of the image dyld registered for this path, or empty
               when no image carries it. It is read from the mapped header, not
               from the file, so it answers which build is resident rather than
               which build is on disk.
     error     dlerror's text when the load failed, empty otherwise

   A chain that reports seven loaded steps with seven distinct 18.3 uuids has
   done what this file can do. Whether an application then uses those images is
   a separate question the report does not answer. */

typedef struct load_step {
  bool loaded;
  u32 images;
  char error[load_error_capacity];
  char uuid[uuid_capacity];
} load_step;

/* Ran is false when the chain was skipped, which happens only when a caller
   decided not to start it. Stopped is the 1-based index of the step whose
   failure ended the chain, 0 when all seven loaded. */

typedef struct load_result {
  bool ran;
  u32 loaded;
  u32 stopped;
  load_step steps[load_chain_count];
} load_result;

void load_chain_run(load_result *result);

/* What the application's own WebKit request resolves to.

   This is the distinction the whole change turns on, and it is not the same
   question as whether the seven loaded. Dedupe follows file identity, so the
   rewritten images are different files from the system ones: they map as
   additional images, and they do not become what a later request for the system
   path answers. A run can therefore load all seven, hold seven consistent 18.3
   uuids, and still leave the browser running 16.1, because the browser's own
   LC_LOAD_DYLIB names /System/Library/Frameworks/WebKit.framework/WebKit.

   So the report states both readings separately, and a reader compares them:

     the seven uuids       what this tweak loaded
     the app uuids         what the process had already resolved for WebKit,
                           WebCore and JavaScriptCore from /System

   Equal sets mean the substitution happened. Disjoint sets mean the payload
   works and the substitution did not, which is a partial success and must not be
   reported as the browser running 18.3. */

#define system_webkit_path "/System/Library/Frameworks/WebKit.framework/WebKit"
#define system_webcore_path "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"
#define system_javascriptcore_path "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"

typedef struct resident_image {
  bool present;
  char uuid[uuid_capacity];
} resident_image;

typedef struct system_images {
  resident_image web_kit;
  resident_image web_core;
  resident_image javascript_core;
} system_images;

/* Reads the three system images that are in the process. A path that is not
   resident answers present false, which is what a host that has not loaded
   WebKit yet looks like. */

void load_chain_read_system(system_images *images);

/* The 1-based position a path occupies, or 0 when it is not one of the seven.
   The report uses it so a step is named by its position in the measured order
   rather than by the order it happened to run in. */

u32 load_chain_position(const char *path);

#endif
