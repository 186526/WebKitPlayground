#include "load_chain.h"

#include "dyld_lookup.h"
#include "macho_image.h"
#include "textkit.h"
#include "types.h"

/* The seven, in the order build-state.md 第八轮 measured. The three facts that
   fix a position are in load_chain.h; this file is the run of them.

   The paths are where the package installs the seven and where the filter loads
   WebKit, so the rewritten images and the loaded files are the same files.
   rewrite-payload.py rewrites them in place, and tests/load_chain_test.c asserts
   this list against that script's IMAGES so the two cannot drift. */

const char *const load_chain_paths[load_chain_count] = {
    "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
    "/var/jb/usr/lib/libwebrtc.dylib",
    "/var/jb/usr/lib/libANGLE-shared.dylib",
    "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
    "/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
    "/var/jb/Library/Frameworks/WebGPU.framework/WebGPU",
    "/var/jb/Library/Frameworks/WebKit.framework/WebKit",
};

/* The image table is how the run counts what it gained. dyld exports these with
   two leading underscores in the symbol table, which is what a C declaration of
   `_dyld_image_count` produces; check-slice.py refuses a slice referring to any
   other spelling, and a device probe settled which spelling binds by reading a
   count of 97 from a dylib that called the two-underscore form. */

extern u32 _dyld_image_count(void);
extern const void *_dyld_get_image_header(u32 index);

/* How many images the process holds. Counted around each load rather than
   inferred from the handle, because the handle answers only whether dyld
   accepted the request, not whether it mapped anything new: a request answered
   from an image already in the process returns a handle and adds no image, and
   that difference is the whole question of whether the 18.3 copy is resident. */

static u32 image_count(void) {
  return _dyld_image_count();
}

/* dyld's own name for an image, which is the path it resolved rather than the
   one a caller asked for. dladdr takes any address inside an image and answers
   the path of that image, so the header is enough. The record and the function
   are declared once in dyld_lookup.h, which this file includes. */

static const char *image_path(u32 index) {
  const void *header = _dyld_get_image_header(index);
  dl_info info;
  if (header == NULL || dladdr(header, &info) == 0 || info.dli_fname == NULL) {
    return NULL;
  }
  return info.dli_fname;
}

/* The header of the image dyld registered under a path, found by comparing the
   path dyld recorded for each image. It is the mapped header, so the uuid read
   from it names the build that is resident. A process that already held an
   image under the same path answers that one, which is what makes this an
   answer about residency rather than about the file on disk. */

static const void *image_header_at(const char *path) {
  u32 count = image_count();
  for (u32 index = 0; index < count; index++) {
    const char *name = image_path(index);
    if (name != NULL && text_equal(name, path)) {
      return _dyld_get_image_header(index);
    }
  }
  return NULL;
}

u32 load_chain_position(const char *path) {
  for (u32 index = 0; index < load_chain_count; index++) {
    if (text_equal(load_chain_paths[index], path)) {
      return index + 1;
    }
  }
  return 0;
}

/* One step: load, count, and record the uuid of whatever is now resident under
   that path. The count is taken before and after because dyld may answer a load
   without mapping anything, and the two readings mean different things: a step
   that mapped an image is one byte of evidence that the 18.3 build is in the
   process, while a step that was answered from a resident image means the
   request did not replace it. */

static void run_step(u32 index, load_result *result) {
  const char *path = load_chain_paths[index];
  load_step *step = &result->steps[index];
  u32 before = image_count();
  void *handle = library_load_with_mode(path, load_chain_mode);
  u32 after = image_count();
  step->loaded = handle != NULL;
  step->images = after > before ? after - before : 0;
  if (handle == NULL) {
    const char *error = library_last_error();
    text_copy(step->error, error == NULL ? "" : error, sizeof(step->error));
    return;
  }
  const void *header = image_header_at(path);
  if (header != NULL) {
    image_uuid_text(header, step->uuid, sizeof(step->uuid));
  }
}

/* The run stops at the first failure, and the position it stopped at is the
   answer a reader needs: a chain that failed at step 1 has no 18.3
   JavaScriptCore, so the six behind it would bind against the 16.1 one and
   reproduce the recorded symbol error with a confusing step number. */

void load_chain_run(load_result *result) {
  text_set(result, 0, sizeof(*result));
  result->ran = true;
  for (u32 index = 0; index < load_chain_count; index++) {
    run_step(index, result);
    if (!result->steps[index].loaded) {
      result->stopped = index + 1;
      return;
    }
    result->loaded = index + 1;
  }
}

/* Records one resident image by path. `present` distinguishes "the process has
   this image" from "the process has an image here whose uuid could not be read";
   a reader that treated both as absent would lose the case this report exists
   for. The path is compared against what dyld recorded, so a /System request
   answered by the 18.3 copy under a dedupe would show up as the 18.3 uuid here
   rather than as absent. */

static void read_one(const char *path, resident_image *image) {
  const void *header = image_header_at(path);
  image->present = header != NULL;
  image->uuid[0] = 0;
  if (header != NULL) {
    image_uuid_text(header, image->uuid, sizeof(image->uuid));
  }
}

void load_chain_read_system(system_images *images) {
  text_set(images, 0, sizeof(*images));
  read_one(system_webkit_path, &images->web_kit);
  read_one(system_webcore_path, &images->web_core);
  read_one(system_javascriptcore_path, &images->javascript_core);
}
