#include "idevice_webkit.h"

#include "bundle_lookup.h"
#include "dylib_report.h"
#include "dyld_lookup.h"
#include "file_writer.h"
#include "filter_match.h"
#include "macho_image.h"
#include "stage_clock.h"
#include "textkit.h"
#include "types.h"

extern u32 _dyld_image_count(void);
extern const char *_dyld_get_image_name(u32 imageIndex);
extern const void *_dyld_get_image_header(u32 imageIndex);
extern void usleep(u32 microseconds);

typedef struct host_images {
  char host_path[path_capacity];
  char host_name[path_capacity];
  char bundle_path[path_capacity];
  char identifier[bundle_identifier_capacity];
  bool web_kit_seen;
  const void *web_kit_header;
  char web_kit_path[path_capacity];
} host_images;

char idevice_webkit_report_path[256];
char idevice_webkit_marker_path[256];
char idevice_webkit_filter_path[256];
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
    host_path_from(hostPath, images->host_path, sizeof(images->host_path));
  }
}

/* The code that runs is not the bundle. NSBundle.bundleWithPath: reads the
   Info.plist of a directory, so the identifier of a host is read from its
   .app directory and not from the executable inside it. The checks before
   this point already proved that the directory before the last component
   ends in .app, so the bundle directory is the part before the last slash. */

static void bundle_directory(const char *hostPath, char *out, usize capacity) {
  usize length = 0;
  while (hostPath[length] != 0) {
    length++;
  }
  while (length > 0 && hostPath[length - 1] != '/') {
    length--;
  }
  while (length > 1 && hostPath[length - 1] == '/') {
    length--;
  }
  if (length >= capacity) {
    length = capacity - 1;
  }
  for (usize index = 0; index < length; index++) {
    out[index] = hostPath[index];
  }
  out[length] = 0;
}

static bool write_report(void) {
  return dylib_report_write(idevice_webkit_report_path);
}

/* The placeholder decides whether the dylib writes a report, not whether it
   loads WebKit, so it is always read: a process outside the host set leaves
   behind only its own placeholder report. The stage is the one the
   constructor had already reached, so a process the filter did not match
   reports the entry stage while a UIApplication process reports the filter
   stage even though it named no host. */

static void write_placeholder(const char *bundleIdentifier) {
  dylib_report_begin(bundleIdentifier);
  stage_reach(stage_current());
  write_report();
}

/* Each host has its own manifest, and the manifest has to name the bundle
   identifier that this process really runs as. The manifest name comes from
   the mapped path, so a container has one name in every namespace, while the
   identifier is read from the bundle directory of the mapped path. Matching
   the two proves both that the manifest belongs to this host and that the
   host is listed. The bundle directory is taken after the mapping, so both
   the container form and the bootstrap form of the same app reach the same
   directory below /Applications. */

static bool host_is_target(host_images *images) {
  host_file_name(images->host_path, images->host_name, sizeof(images->host_name));
  bundle_directory(images->host_path, images->bundle_path, sizeof(images->bundle_path));
  if (!is_host_path(images->host_path) ||
      !bundle_path_identifier(images->bundle_path, images->identifier,
                              sizeof(images->identifier))) {
    write_placeholder("");
    return false;
  }
  join(manifest_name, sizeof(manifest_name), host_manifest_prefix, images->host_name);
  join(manifest_path, sizeof(manifest_path), tweak_directory, manifest_name);
  join(manifest_path, sizeof(manifest_path), manifest_path, manifest_suffix);
  if (!plist_file_identifier(manifest_path, guest_path, sizeof(guest_path)) ||
      !text_equal(guest_path, images->identifier)) {
    write_placeholder("");
    return false;
  }
  return true;
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

/* The path is a directory when its last component is the whole of it:
   /var/jb/usr/lib/TweakInject qualifies, /var/jb/usr/lib/TweakInjectX does
   not. Both forms of the directory end the same way, so one check reads
   /var/jb/usr/lib/TweakInject and its /private/preboot form alike. */

static bool ends_with_directory(const char *directory, const char *suffix) {
  usize directoryLength = text_length(directory);
  usize suffixLength = text_length(suffix);
  if (directoryLength < suffixLength) {
    return false;
  }
  return text_equal(directory + directoryLength - suffixLength, suffix);
}

/* The installed copy is the dylib in the TweakInject directory of the
   jailbreak root. That directory is reached through /var/jb on the device and
   through /private/preboot/<uuid>/<name>/procursus on the root filesystem,
   and dyld reports whichever form it resolved, so the check is not a compare
   against one literal path. A dylib built into a test directory has another
   directory and answers a reason instead. */

static bool installed_directory_path(const char *imagePath) {
  if (ends_with_directory(imagePath, installed_directory_tail)) {
    return true;
  }
  if (text_begins_with(imagePath, installed_directory)) {
    return ends_with_directory(imagePath, installed_directory_tail);
  }
  return false;
}

/* A path ending in a slash names a directory itself, and dyld never reports
   one, so the trailing slashes are dropped before the directory is taken. A
   path left with no directory at all then answers a reason. */

static void parent_directory(const char *path, char *out, usize capacity) {
  usize length = text_length(path);
  while (length > 0 && path[length - 1] == '/') {
    length--;
  }
  if (length >= capacity) {
    length = capacity - 1;
  }
  for (usize index = 0; index < length; index++) {
    out[index] = path[index];
  }
  out[length] = 0;
  if (length > 0) {
    bundle_directory(out, out, capacity);
  }
}

/* The report and the marker are only written by the installed copy under its
   own directory, so a copy loaded from a test directory cannot rewrite the
   result of the installed one. An answer other than NULL names the check the
   path failed, and each of them is written into the entry report. */

static const char *installed_copy_reason(const char *selfPath) {
  if (selfPath == NULL || selfPath[0] == 0) {
    return "no self image path";
  }
  char directory[path_capacity];
  parent_directory(selfPath, directory, sizeof(directory));
  if (directory[0] == 0) {
    return "self image path has no directory";
  }
  if (!installed_directory_path(directory)) {
    return "self image is not in the TweakInject directory";
  }
  return NULL;
}

/* The last image is this dylib on the device, but the reason above judges the
   path and not its position, so a copy that dyld reports elsewhere in the
   table is still recognised by the directory it sits in. */

static const char *self_image_path(void) {
  u32 imageCount = _dyld_image_count();
  return imageCount == 0 ? NULL : _dyld_get_image_name(imageCount - 1);
}

/* The report path is the default until a test points it at a directory it
   owns, so both the entry report and the later ones reach the same file. */

static void default_report_paths(void) {
  if (idevice_webkit_report_path[0] == 0) {
    join(idevice_webkit_report_path, sizeof(idevice_webkit_report_path), report_directory,
         report_file_name);
    join(idevice_webkit_marker_path, sizeof(idevice_webkit_marker_path), report_directory,
         marker_file_name);
  }
}

/* The filter and the dylib sit in the same directory, and libinjector finds
   the filter by that neighbourhood. The path below is the one a test points
   at a file it owns, and the default when it is empty. */

static void filter_file_path(char *out, usize capacity) {
  if (idevice_webkit_filter_path[0] != 0) {
    text_copy(out, idevice_webkit_filter_path, capacity);
    return;
  }
  join(out, capacity, tweak_directory, filter_file_name);
}

/* The pause after each write lets a second launch write a later stage over
   the report of the first one; without it the file keeps the stage of the
   first write, because the following writes happen after the last read. */

static void report_stage(void) {
  write_report();
  usleep(stage_pause_microseconds);
}

/* The first write happens before the dylib judges its own path, so a process
   that loaded the dylib at all leaves a report behind: the question «was the
   constructor called» and the question «was it the installed copy» then have
   different answers on disk. When the entry report cannot be written, nothing
   later would be readable either, and the dylib stops instead of overwriting
   a report the installed copy already wrote.

   The write after it names how the filter matched, because ellekit loads the
   dylib into every process the filter hits and the WebKit subprocesses of a
   host carry no UIApplication and no identifier this dylib can read. A
   subprocess therefore stops at the filter stage with matchVia at none, while
   the main application goes on through the stages above and names its match.
   The filter is read again from disk, as an ordinary dictionary, because
   ellekit keeps the matching inside libinjector. */

void idevice_webkit_start(void) {
  if (stage_current() != 0) {
    return;
  }
  char filterPath[path_capacity];
  default_report_paths();
  filter_file_path(filterPath, sizeof(filterPath));
  dylib_report_begin("");
  stage_reach(stage_entered);
  if (!write_report()) {
    return;
  }
  usleep(stage_pause_microseconds);
  filter_match_report(filterPath);
  stage_reach(stage_filter_read);
  report_stage();
  const char *selfPath = self_image_path();
  const char *reason = installed_copy_reason(selfPath);
  dylib_report_set_self(selfPath, reason == NULL, reason);
  if (reason != NULL) {
    stage_reach(stage_not_installed_copy);
    write_report();
    return;
  }
  host_images images;
  collect_images(&images);
  if (!host_is_target(&images)) {
    return;
  }
  stage_reach(stage_bundle_resolved);
  dylib_report_begin(images.identifier);
  dylib_report_set_self(selfPath, true, NULL);
  report_stage();
  stage_reach(stage_attempted);
  attempt_load(&images);
  stage_reach(stage_report_written);
  report_stage();
  write_file(idevice_webkit_marker_path, "ready\n", 6);
  stage_reach(stage_holding);
  report_stage();
}
