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

/* The load mode, and what each half of it rests on.

   Darwin's values: RTLD_LAZY 1, RTLD_NOW 2, RTLD_GLOBAL 8. The tweak used
   RTLD_LAZY alone, which is the 0x0001 in the recorded error. That reading says
   the old call passed neither flag; it does not say which of the two its failure
   needed, because the failure it recorded was a dependency bound to the 16.1
   copy rather than a symbol looked up at run time.

   RTLD_NOW makes a missing symbol fail the dlopen that is being attempted,
   instead of faulting at the first call through it. That turns an unreported
   crash into a step index and a symbol name in the report, which is why every
   step asks for it.

   RTLD_GLOBAL puts the image's exports in the global scope, where a lookup that
   falls back to that scope resolves against them; without it the exports are
   reachable only through a handle to the image. Nothing in the chain needs that
   for the order to work: each of the seven names its siblings by absolute /var/jb
   path, and dyld opens the path rather than searching a scope. GLOBAL is kept as
   the wider of two placements — a late lookup through the global table is then
   answered by the 18.3 export instead of missing — and no run has shown it to
   hurt. It is not a measured requirement.

   WHY THE ORDER IS WHAT IT IS. WebCore carries LC_LOAD_WEAK_DYLIB on three of
   the seven: libwebrtc, libANGLE-shared and WebGPU. Those three are read out of
   the payload's own WebCore by parsing its load commands; the other nine weak
   commands in that image name system libraries (BrowserEngineCore, libxslt.1,
   libAccessibility, six libswift*). A weak dependency does not stop the load.
   Its target is whichever file the string names: with the payload's original
   /System strings, a WebCore loaded before the two dylibs bound the resident
   16.1 copies and the later /var/jb dlopen mapped a second copy. The reorder that
   took the count to 0 moved those two dylibs ahead of WebCore, and the count
   itself is libwebrtc's duplicate-class warnings alone: libwebrtc carries the
   WK_RTC names, WebGPU carries none, and libANGLE-shared defines no classes. So
   WebGPU, the third weak dependency and step 6, is untested as a variable. In the
   rewritten tree the three strings are /var/jb/.../WebKit18, so the /System string
   WebCore used to name is gone and no weak import of the three can reach a 16.1
   image.

   WHAT IS MEASURED AND WHAT IS NOT. The order is measured: build-state.md 第八轮
   took the duplicate count from 31 to 0 at 7/7 with this sequence. What that
   measurement covers is narrow, and the constraint it establishes is one edge, not
   the whole permutation. WebCore ahead of JavaScriptCore fails on a StringImpl
   symbol — recorded for WebCore loaded alone (doc 2.2) and for a host that links
   WebKit (doc 6.3) — because JavaScriptCore provides that symbol. WebCore ahead of
   the two dylibs but still after JavaScriptCore is not a failure: doc 2.3 and the
   5.2 table both give 7/7 for it, differing only in the duplicate count (31 against
   0). So JavaScriptCore must come before WebCore, and the reorder against the two
   dylibs is what removes the duplicates.

   The flag is not measured: the probe that produced those counts calls
   `dlopen(kImages[i], RTLD_NOW)` — docs/ios16-dyld-identity-and-load-conditions.md
   8.1 — with no RTLD_GLOBAL anywhere, so the count fell with every image in its
   own scope, and no device run compares the two modes. */

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
