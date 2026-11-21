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
  char image_path[path_capacity];
  char host_path[path_capacity];
  char host_name[path_capacity];
  char identifier[bundle_identifier_capacity];
  bool web_kit_seen;
  const void *web_kit_header;
  char web_kit_path[path_capacity];
} host_images;

char idevice_webkit_report_path[256];
char idevice_webkit_marker_path[256];
static char manifest_name[path_capacity];
static char manifest_path[path_capacity];
static char guest_path[path_capacity];

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

/* A file name cannot contain a slash, so the main executable path of a host
   becomes its manifest name with slashes replaced by dashes. A dash followed
   by a bracketed group such as -[9A73726D-...] collapses into a single dash,
   which keeps container UUIDs compact and unambiguous. */

static void host_file_name(const char *hostPath, char *out, usize capacity) {
  usize written = 0;
  for (usize source = 0; hostPath[source] != 0 && written + 1 < capacity; source++) {
    char symbol = hostPath[source];
    if (symbol == '-' && hostPath[source + 1] == '[') {
      while (hostPath[source] != 0 && hostPath[source] != ']') {
        source++;
      }
      symbol = '-';
    } else if (symbol == '/') {
      symbol = '-';
    }
    out[written++] = symbol;
  }
  out[written] = 0;
}

/* The host is image 0 of the process, the main executable. On the device it
   is reached through one of two paths: the system applications directory
   /Applications/<Name>.app/<exe>, or the application container
   /var/containers/Bundle/Application/<UUID>/<Name>.app/<exe>. Inside the
   jailbreak bootstrap the same container appears below
   /private/preboot/<uuid>/<name>-XXX/procursus/Applications/<Name>.app/<exe>,
   and dyld reports whichever form it resolved. The third form is mapped back
   to the first one, so a host has the same manifest name in every namespace
   and the second UUID of the bootstrap path never enters the name. */

static const char *segment_after(const char *path, const char *segment) {
  const char *found = NULL;
  for (const char *scan = path; *scan != 0; scan++) {
    if (text_begins_with(scan, segment)) {
      found = scan + text_length(segment);
    }
  }
  return found;
}

/* A process inside the bootstrap reaches the container through
   /private/preboot/<uuid>/<name>-XXX/procursus/Applications/<Name>.app/<exe>.
   The container itself is one directory below /Applications there, so the
   host keeps a single name across the two namespaces. */

static void host_path_from(const char *imagePath, char *out, usize capacity) {
  const char *bootstrap = text_begins_with(imagePath, guest_prefix)
                              ? segment_after(imagePath, "/procursus/Applications/")
                              : NULL;
  if (bootstrap != NULL) {
    text_copy(out, "/Applications/", capacity);
    usize used = text_length(out);
    for (usize index = 0; bootstrap[index] != 0 && used + 1 < capacity; index++) {
      out[used++] = bootstrap[index];
    }
    out[used] = 0;
    return;
  }
  if (text_begins_with(imagePath, "/private/var/containers/")) {
    text_copy(out, imagePath + text_length("/private"), capacity);
    return;
  }
  text_copy(out, imagePath, capacity);
}

/* The path is an application executable when the directory before its last
   component ends in .app and the component before that is not empty:
   /Applications/MobileSafari.app/MobileSafari qualifies, while
   /private/preboot/<uuid>/dopamine-X/procursus/usr/bin/tool does not. The
   check compares against a byte array so no string literal is scanned. */

static const u8 app_suffix[4] = {'.', 'a', 'p', 'p'};

static bool is_host_path(const char *hostPath) {
  const char *lastSeparator = NULL;
  const char *previousSeparator = NULL;
  for (const char *cursor = hostPath; *cursor != 0; cursor++) {
    if (*cursor == '/') {
      previousSeparator = lastSeparator;
      lastSeparator = cursor;
    }
  }
  if (previousSeparator == NULL || lastSeparator == NULL) {
    return false;
  }
  if (lastSeparator == previousSeparator + 1 || lastSeparator[1] == 0) {
    return false;
  }
  const char *directory = previousSeparator + 1;
  usize directoryLength = 0;
  while (directory + directoryLength < lastSeparator) {
    directoryLength++;
  }
  if (directoryLength < sizeof(app_suffix)) {
    return false;
  }
  const u8 *end = (const u8 *)(lastSeparator - sizeof(app_suffix));
  for (usize index = 0; index < sizeof(app_suffix); index++) {
    if (end[index] != app_suffix[index]) {
      return false;
    }
  }
  return true;
}

/* The framework copy under /var/jb is reported by its dyld path, while its
   install name stays the /System path it was built against. */

static void collect_images(host_images *images) {
  text_set(images, 0, sizeof(*images));
  u32 imageCount = _dyld_image_count();
  for (u32 index = 0; index < imageCount; index++) {
    if (images->web_kit_seen) {
      break;
    }
    const char *imagePath = _dyld_get_image_name(index);
    if (imagePath != NULL && text_equal(imagePath, framework_path)) {
      images->web_kit_seen = true;
      images->web_kit_header = _dyld_get_image_header(index);
      text_copy(images->web_kit_path, imagePath, sizeof(images->web_kit_path));
    }
  }
  const char *hostPath = imageCount == 0 ? NULL : _dyld_get_image_name(0);
  if (hostPath != NULL) {
    text_copy(images->image_path, hostPath, sizeof(images->image_path));
    host_path_from(hostPath, images->host_path, sizeof(images->host_path));
  }
}

/* Each host has its own manifest, and the manifest has to name the bundle
   identifier that this process really runs as. The manifest name comes from
   the mapped path, so a container has one name in every namespace, while the
   identifier is read from the path dyld reported, because that is the path
   the bundle lookup reaches. Matching the two proves both that the manifest
   belongs to this host and that the host is listed. */

static bool host_is_target(host_images *images) {
  if (!is_host_path(images->host_path)) {
    return false;
  }
  host_file_name(images->host_path, images->host_name, sizeof(images->host_name));
  join(manifest_name, sizeof(manifest_name), host_manifest_prefix, images->host_name);
  join(manifest_path, sizeof(manifest_path), tweak_directory, manifest_name);
  join(manifest_path, sizeof(manifest_path), manifest_path, manifest_suffix);
  if (!bundle_path_identifier(manifest_path, images->identifier, sizeof(images->identifier))) {
    return false;
  }
  if (!bundle_path_identifier(images->image_path, guest_path, sizeof(guest_path))) {
    return false;
  }
  return text_equal(guest_path, images->identifier);
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
  dylib_report_begin(images.identifier);
  report_stage();
  stage_reach(stage_attempted);
  attempt_load(&images);
  stage_reach(stage_report_written);
  report_stage();
  write_file(idevice_webkit_marker_path, "ready\n", 6);
  stage_reach(stage_holding);
  report_stage();
}
