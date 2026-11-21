#include "idevice_webkit.h"

#include "bundle_lookup.h"
#include "dylib_report.h"
#include "dyld_lookup.h"
#include "file_writer.h"
#include "macho_image.h"
#include "stage_clock.h"
#include "textkit.h"
#include "types.h"

extern u32 _dyld_image_count(void);
extern const char *_dyld_get_image_name(u32 imageIndex);
extern const void *_dyld_get_image_header(u32 imageIndex);
extern void usleep(u32 microseconds);

typedef struct host_images {
  bool guest_seen;
  char guest_path[path_capacity];
  bool web_kit_seen;
  const void *web_kit_header;
  char web_kit_path[path_capacity];
} host_images;

char idevice_webkit_report_path[256];
char idevice_webkit_marker_path[256];
static char manifest_path[path_capacity];
static char manifest_name[path_capacity];
static char configured_identifier[bundle_identifier_capacity];

static void join(char *out, usize capacity, const char *left, const char *right) {
  usize used = 0;
  usize index = 0;
  while (left[index] != 0 && used + 1 < capacity) {
    out[used++] = left[index++];
  }
  index = 0;
  while (right[index] != 0 && used + 1 < capacity) {
    out[used++] = right[index++];
  }
  out[used] = 0;
}

/* A manifest file name cannot contain a slash, so the container path of a
   host becomes the file name with slashes replaced by dashes. A dash followed
   by a bracketed group such as -[9A73726D-...] collapses into a single dash,
   which keeps UUIDs compact and unambiguous. */

static void host_file_name(const char *hostPath, char *out, usize capacity) {
  usize written = 0;
  for (usize source = 0; hostPath[source] != 0 && written + 1 < capacity; source++) {
    if (hostPath[source] == '-' && hostPath[source + 1] == '[') {
      while (hostPath[source] != 0 && hostPath[source] != ']') {
        source++;
      }
      out[written++] = '-';
      continue;
    }
    out[written++] = hostPath[source] == '/' ? '-' : hostPath[source];
  }
  out[written] = 0;
}

/* The main executable of a container app is reached at
   /private/preboot/<uuid>/<container path>/<bundle>.app/<exe>. An app bundle
   is a single directory under its container, while the jailbreak bootstrap
   at /private/preboot/<uuid>/dopamine-XXX/procursus contains directories and
   files of its own, so the image with one component below its container is
   the main executable. The framework copy under /var/jb is reported by its
   dyld path, while its install name stays the /System path it was built
   against. */

static void collect_images(host_images *images) {
  text_set(images, 0, sizeof(*images));
  u32 imageCount = _dyld_image_count();
  for (u32 index = 0; index < imageCount; index++) {
    const char *imagePath = _dyld_get_image_name(index);
    if (imagePath == NULL) {
      continue;
    }
    if (!images->guest_seen && text_begins_with(imagePath, guest_prefix)) {
      /* The container UUID stays in the name: only /private/preboot/ is the
         fixed prefix, and everything after it identifies the host. */
      const char *component = imagePath + text_length(guest_prefix);
      u32 separators = 0;
      for (const char *cursor = component; *cursor != 0; cursor++) {
        if (*cursor == '/') {
          separators++;
        }
      }
      if (separators == 3) {
        host_file_name(component, images->guest_path, sizeof(images->guest_path));
        images->guest_seen = text_length(images->guest_path) > 0;
      }
    }
    if (!images->web_kit_seen && text_equal(imagePath, framework_path)) {
      images->web_kit_seen = true;
      images->web_kit_header = _dyld_get_image_header(index);
      text_copy(images->web_kit_path, imagePath, sizeof(images->web_kit_path));
    }
  }
}

/* Each host has its own manifest, so several hosts can each enable the dylib
   without sharing a key. The manifest holds the bundle identifier the filter
   must follow; reading it means the host is one of the targets. */

static bool host_is_target(const host_images *images) {
  if (!images->guest_seen) {
    return false;
  }
  join(manifest_name, sizeof(manifest_name), "com.websafe.idevice.host.",
       images->guest_path);
  join(manifest_path, sizeof(manifest_path), tweak_directory, manifest_name);
  join(manifest_path, sizeof(manifest_path), manifest_path, manifest_suffix);
  return bundle_path_identifier(manifest_path, configured_identifier,
                                sizeof(configured_identifier));
}

static void write_report(void) {
  dylib_report_write(idevice_webkit_report_path);
}

static void attempt_load(host_images *images) {
  void *handle = library_load(framework_path);
  dylib_report_set_dlopen(framework_path, handle, handle == NULL ? library_last_error() : NULL);
  if (images->web_kit_seen) {
    char version[version_capacity];
    version[0] = 0;
    macho_version_text(images->web_kit_header, version, sizeof(version));
    dylib_report_set_image(images->web_kit_path, version);
  }
}

/* The report and the marker are only written by the installed copy, so a copy
   loaded from a test directory cannot rewrite the result of the installed
   one. */

static bool loaded_as_installed_copy(void) {
  u32 imageCount = _dyld_image_count();
  const char *selfPath = imageCount == 0 ? NULL : _dyld_get_image_name(imageCount - 1);
  if (selfPath == NULL) {
    return false;
  }
  return text_equal(selfPath, self_path);
}

/* The pause after each write lets a second launch write a later stage over
   the report of the first one; without it the file keeps the stage of the
   first write, because the following writes happen after the last read. */

static void report_stage(void) {
  write_report();
  usleep(stage_pause_microseconds);
}

void idevice_webkit_start(void) {
  if (stage_current() != 0) {
    return;
  }
  stage_reach(stage_entered);
  if (!loaded_as_installed_copy()) {
    return;
  }
  if (idevice_webkit_report_path[0] == 0) {
    join(idevice_webkit_report_path, sizeof(idevice_webkit_report_path), report_directory,
         report_file_name);
    join(idevice_webkit_marker_path, sizeof(idevice_webkit_marker_path), report_directory,
         marker_file_name);
  }
  host_images images;
  collect_images(&images);
  stage_reach(stage_bundle_resolved);
  if (!host_is_target(&images)) {
    return;
  }
  dylib_report_begin(images.guest_path);
  report_stage();
  stage_reach(stage_attempted);
  attempt_load(&images);
  stage_reach(stage_report_written);
  report_stage();
  write_file(idevice_webkit_marker_path, "ready\n", 6);
  stage_reach(stage_holding);
  report_stage();
}
