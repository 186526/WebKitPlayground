#include "fake_bundle.h"
#include "fake_dyld.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The dylib sources have no libc dependency, so they are compiled into the
   test directly; the fixtures provide the image table, the Info.plist lookup
   and the file writer in place of the device. */

#include "../src/textkit.c"
#include "fixtures/host_file_writer.c"
#include "../src/stage_clock.c"
#include "../src/dylib_report.c"
#include "../src/macho_image.c"
#include "fixtures/fake_bundle.c"
#include "fixtures/fake_dyld.c"
#include "../src/idevice_webkit.c"

#define maximum_report_size 4096
#define work_directory_name "/idevice-webkit-test"
static char work_directory[256];

void *library_load(const char *path) {
  (void)path;
  return 0;
}
char *library_last_error(void) {
  return "test: no framework";
}

static int failures = 0;

static void show(const char *format, ...) {
  char line[4096];
  va_list arguments;
  va_start(arguments, format);
  vsnprintf(line, sizeof(line), format, arguments);
  va_end(arguments);
  printf("%s", line);
}

static void expect(const char *name, int condition) {
  printf("%-58s %s\n", name, condition ? "pass" : "FAIL");
  if (!condition) {
    failures++;
  }
}

/* The assertions below search for fragments of the report. The search is a
   loop of its own rather than a library call, because the report text and the
   fragment travel through the same string functions the code under test uses,
   and keeping the assertion out of those calls keeps the two apart. */

static int found_in(const char *text, const char *fragment) {
  usize fragmentLength = strlen(fragment);
  for (const char *scan = text; *scan != 0; scan++) {
    usize matched = 0;
    while (matched < fragmentLength && scan[matched] == fragment[matched]) {
      matched++;
    }
    if (matched == fragmentLength) {
      return 1;
    }
  }
  return 0;
}

static char *read_report(const char *path) {
  static char text[maximum_report_size];
  FILE *file = fopen(path, "r");
  if (file == NULL) {
    return NULL;
  }
  usize length = fread(text, 1, sizeof(text) - 1, file);
  text[length] = 0;
  fclose(file);
  return text;
}

/* The dylib drops the leading slash and turns every other slash into a dash,
   so a host that starts at /Applications has a file name starting with "-". */

#define manifest_path_for(host) "/var/jb/usr/lib/TweakInject/com.websafe.idevice.host." host ".plist"

/* Each case registers the host app bundle and the manifest with the same
   bundle identifier, which is what the device has once the manifest is in
   place. The bundle identifier of a host is read from its .app directory, the
   way NSBundle.bundleWithPath: reads it, so every case registers the directory
   in place of the executable inside it. */

static void prepare(const char *host, const char *self, int withWebKit) {
  fake_reset(host, "/private/var/mobile/Library/Preferences/com.apple.prefs.plist",
             withWebKit ? "/var/jb/Library/Frameworks/WebKit.framework/WebKit" : 0, self);

  fake_bundles_reset();
  stage_reset();
  remove(idevice_webkit_report_path);
  remove(idevice_webkit_marker_path);

}

static void add_bundle(const char *path, const char *identifier) {
  fake_bundle_add(path, identifier);
}

/* The manifest is a plist file, not a bundle directory, so it is registered
   in the plist table. */
static void add_manifest(const char *path, const char *identifier) {
  fake_plist_add(path, identifier);
}

/* A process outside the host set leaves a placeholder report behind: stage 1,
   an empty bundle identifier and no handle. The marker holds the decision of
   the dylib, so a placeholder is no marker. */

static void expect_placeholder(const char *name) {
  char *report = read_report(idevice_webkit_report_path);
  expect(name, report != NULL && found_in(report, "<integer>1</integer>") &&
                   found_in(report, "<key>bundleId</key>\n\t<string></string>") &&
                   found_in(report, "<key>handleOk</key>\n\t<false/>") &&
                   read_report(idevice_webkit_marker_path) == NULL);
}

/* The paths of this dylib that the breadcrumb is checked through, each in the
   form dyld reports it on the device. */

#define installed_self "/var/jb/usr/lib/TweakInject/WebSafeWebKitFilter.dylib"
#define preboot_self "/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus/usr/lib/TweakInject/WebSafeWebKitFilter.dylib"
#define test_self "/tmp/WebSafeWebKitTest/WebSafeWebKitFilter.dylib"

/* Everything the report says about the copy that was called: a nil image path
   leaves both strings empty, while a path outside TweakInject is written as
   dyld gave it beside the reason the check failed. */

static void expect_self(const char *name, const char *imagePath, const char *reason) {
  char *report = read_report(idevice_webkit_report_path);
  char expectations[1024];
  snprintf(expectations, sizeof(expectations),
           "\t<key>selfImage</key>\n\t<string>%s</string>\n"
           "\t<key>selfError</key>\n\t<string>%s</string>\n",
           imagePath == NULL ? "" : imagePath, reason);
  expect(name, report != NULL && found_in(report, expectations));
}

/* A copy that dyld reports outside TweakInject is called, finds no installed
   directory to write under, and leaves its own path and the failed check as
   the report. The stage stays below the host work: no identifier, no handle,
   no marker. */

static void expect_not_installed(const char *name, const char *imagePath, const char *reason) {
  char *report = read_report(idevice_webkit_report_path);
  expect(name, report != NULL && found_in(report, "<integer>2</integer>") &&
                   found_in(report, "<key>bundleId</key>\n\t<string></string>") &&
                   found_in(report, "<key>handleOk</key>\n\t<false/>") &&
                   read_report(idevice_webkit_marker_path) == NULL);
  expect_self("  report names the self image and the failed check", imagePath, reason);
}

static void list_host(const char *host, const char *manifest, const char *identifier) {
  add_bundle(host, identifier);
  add_manifest(manifest, identifier);
}

/* A listed host leaves the holding stage behind, the last stage the
   constructor reaches, and names the copy that was called with no failed
   check beside it. */

static void case_host(const char *name, const char *host, const char *manifest,
                      const char *bundle, const char *identifier, const char *self) {
  prepare(host, self, 1);

  list_host(bundle, manifest, identifier);
  idevice_webkit_start();
  char *report = read_report(idevice_webkit_report_path);
  expect(name, report != NULL);
  if (report != NULL) {
    expect("  report reaches the holding stage", found_in(report, "<integer>6</integer>"));
    expect("  report records the bundle identifier", found_in(report, identifier));
    expect("  marker is written next to the report", read_report(idevice_webkit_marker_path) != NULL);
    expect_self("  report names the installed copy with no failed check", self, "");
  }
}

int main(void) {
  const char *root = getenv("TMPDIR");
  snprintf(work_directory, sizeof(work_directory), "%s%s", root == NULL ? "/tmp/" : root,
           work_directory_name);
  while (work_directory[0] != 0 && work_directory[strlen(work_directory) - 1] == '/') {
    work_directory[strlen(work_directory) - 1] = 0;
  }
  snprintf(idevice_webkit_report_path, sizeof(idevice_webkit_report_path),
           "%s/com.websafe.idevice.plist", work_directory);
  snprintf(idevice_webkit_marker_path, sizeof(idevice_webkit_marker_path),
           "%s/com.websafe.idevice.ready", work_directory);
  char command[300];
  snprintf(command, sizeof(command), "mkdir -p %s", work_directory);
  system(command);
  printf("stages: entered=%u not-installed=%u bundle=%u attempted=%u written=%u holding=%u\n",
         (unsigned)stage_entered, (unsigned)stage_not_installed_copy,
         (unsigned)stage_bundle_resolved, (unsigned)stage_attempted,
         (unsigned)stage_report_written, (unsigned)stage_holding);

  /* The three host paths below were read from the device. */

  const char *safari = "/Applications/MobileSafari.app/MobileSafari";
  const char *chrome =
      "/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app/Chrome";
  const char *wechat =
      "/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app/WeChat";
  const char *wechat_private_container =
      "/private/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app/WeChat";
  const char *wechat_in_bootstrap =
      "/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus/Applications/WeChat.app/WeChat";

  /* The installed copy is judged by its directory, so both forms of
     TweakInject reach the host work; the same dylib built into a test
     directory stops at the entry report. The generator list is split between
     the two forms so both are exercised through a listed host. */

  case_host("safari at /Applications is a host", safari,
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "/Applications/MobileSafari.app", "com.apple.mobilesafari", installed_self);
  case_host("chrome container is a host", chrome,
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            "com.google.chrome.ios", installed_self);
  case_host("wechat container is a host", wechat,
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            "com.tencent.xin", installed_self);
  /* The container is also reached through its /private/var path, which the
     mapping strips down to /var, so the manifest name is the one the
     /var/containers form above already uses. */

  case_host("wechat container below /private/var is a host", wechat_private_container,
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            "com.tencent.xin", preboot_self);
  case_host("wechat below the bootstrap is a host", wechat_in_bootstrap,
            manifest_path_for("-Applications-WeChat.app-WeChat"), "/Applications/WeChat.app",
            "com.tencent.xin", preboot_self);

  char *report = read_report(idevice_webkit_report_path);
  if (report != NULL) {
    char shown[maximum_report_size];
    snprintf(shown, sizeof(shown), "%s", report);
    show("\n--- report written for the bootstrap host, from the test fixtures ---\n%s\n",
         shown);
  }

  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_placeholder("host without a manifest leaves a placeholder");

  prepare(safari, preboot_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  add_manifest(manifest_path_for("-Applications-MobileSafari.app-MobileSafari"), "com.tencent.xin");
  idevice_webkit_start();
  expect_placeholder("manifest naming another bundle leaves a placeholder");

  /* NSBundle.bundleWithPath: answers for the .app directory, not for the
     executable inside it, so an executable registered with an identifier is
     still not a bundle and the host cannot be named. */

  prepare(safari, installed_self, 1);
  list_host(safari, manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_placeholder("an executable path is not a bundle");

  prepare(safari, test_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_not_installed("a dylib loaded from a test directory reports itself",
                       test_self, "self image is not in the TweakInject directory");

  /* A neighbour of TweakInject is another directory: the check compares the
     whole last component, not its beginning. */

  prepare(safari, "/var/jb/usr/lib/TweakInject-backup/WebSafeWebKitFilter.dylib", 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_not_installed("a dylib beside TweakInject reports itself",
                       "/var/jb/usr/lib/TweakInject-backup/WebSafeWebKitFilter.dylib",
                       "self image is not in the TweakInject directory");

  /* dyld can leave an image unnamed; the constructor then has no path to
     judge, writes the entry report anyway and names the missing path. */

  prepare(safari, NULL, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_not_installed("a dylib dyld cannot name reports the entry stage", NULL,
                       "no self image path");

  /* The host is image 0 even when the dylib is not the last image, so the run
     below puts a second framework after it and keeps the host first. */

  prepare(chrome, installed_self, 1);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("the host is image 0, not the last image",
         report != NULL && found_in(report, "<integer>6</integer>"));

  /* The same Chrome process and manifest, with the dylib called from a test
     directory: the entry report is the only file written, and its stage 2
     with an empty identifier is what tells this run from a host that never
     reached the constructor. */

  prepare(chrome, test_self, 1);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  expect_not_installed("a listed host with a test copy still reports the call", test_self,
                       "self image is not in the TweakInject directory");

  prepare("/private/preboot/UUID/dopamine-ML13U0/procursus/usr/bin/tool", installed_self, 1);
  add_bundle("/private/preboot/UUID/dopamine-ML13U0/procursus/usr/bin/tool", "com.tencent.xin");
  add_manifest(manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
             "com.tencent.xin");
  idevice_webkit_start();
  expect_placeholder("a process that is not an app leaves a placeholder");

  prepare(chrome, installed_self, 0);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("report still written when WebKit was not loaded yet", report != NULL);
  if (report != NULL) {
    expect("  missing WebKit leaves imagePath empty",
           found_in(report, "<key>imagePath</key>\n\t<string></string>"));
    expect("  handleOk is false", found_in(report, "<key>handleOk</key>\n\t<false/>"));
  }

  /* A fresh process of the same host starts with the stage at zero; the entry
     report is then refused by the file writer, as a device without a writable
     Preferences directory does, and the report the installed copy wrote
     earlier stays as it was. */

  prepare(chrome, installed_self, 1);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  {
    char *kept = read_report(idevice_webkit_report_path);
    char before[maximum_report_size];
    snprintf(before, sizeof(before), "%s", kept == NULL ? "" : kept);
    stage_reset();
    fake_refuse_writes(1);
    idevice_webkit_start();
    fake_refuse_writes(0);
    char *after = read_report(idevice_webkit_report_path);
    expect("a refused entry report stops the constructor",
           after != NULL && strcmp(before, after) == 0 &&
               found_in(after, "<string>com.google.chrome.ios</string>") &&
               read_report(idevice_webkit_marker_path) != NULL);
    stage_reset();
  }

  printf("\nfailures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
