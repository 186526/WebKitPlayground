#include "fake_bundle.h"
#include "fake_dyld.h"
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

static void expect(const char *name, int condition) {
  printf("%-58s %s\n", name, condition ? "pass" : "FAIL");
  if (!condition) {
    failures++;
  }
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

static void prepare(const char *host, const char *self, int withWebKit) {
  fake_reset(host, "/private/var/mobile/Library/Preferences/com.apple.prefs.plist",
             withWebKit ? "/var/jb/Library/Frameworks/WebKit.framework/WebKit" : 0, self);
  stage_reset();
  remove(idevice_webkit_report_path);
  remove(idevice_webkit_marker_path);
}

const char *host_main = "/private/preboot/UUID/procursus/demo.app/demo";
const char *host_manifest =
    "/var/jb/usr/lib/TweakInject/com.websafe.idevice.host.UUID-procursus-demo.app-demo.plist";

/* run.sh cleans the same directory before the run, so the test follows TMPDIR
   rather than a fixed path. */

static void use_work_directory(void) {
  const char *root = getenv("TMPDIR");
  snprintf(work_directory, sizeof(work_directory), "%s%s", root == NULL ? "/tmp" : root,
           work_directory_name);
  while (work_directory[0] != 0 &&
         work_directory[strlen(work_directory) - 1] == '/') {
    work_directory[strlen(work_directory) - 1] = 0;
  }
}

int main(void) {
  use_work_directory();
  snprintf(idevice_webkit_report_path, sizeof(idevice_webkit_report_path),
           "%s/com.websafe.idevice.plist", work_directory);
  snprintf(idevice_webkit_marker_path, sizeof(idevice_webkit_marker_path),
           "%s/com.websafe.idevice.ready", work_directory);
  char command[300];
  snprintf(command, sizeof(command), "mkdir -p %s", work_directory);
  system(command);
  printf("stages: entered=%u bundle=%u attempted=%u written=%u holding=%u\n",
         (unsigned)stage_entered, (unsigned)stage_bundle_resolved, (unsigned)stage_attempted,
         (unsigned)stage_report_written, (unsigned)stage_holding);

  prepare(host_main, self_path, 1);
  fake_manifest_path = host_manifest;
  idevice_webkit_start();
  char *report = read_report(idevice_webkit_report_path);
  expect("target host writes a report", report != NULL);
  if (report != NULL) {
    char shown[maximum_report_size];
    snprintf(shown, sizeof(shown), "%s", report);
    printf("\n--- report written for the target host ---\n%s\n", shown);
    expect("report names the host container",
           strstr(report, "UUID-procursus-demo.app-demo") != NULL);
    expect("report records handleOk", strstr(report, "<key>handleOk</key>") != NULL);
    expect("report records imageVersion key", strstr(report, "<key>imageVersion</key>") != NULL);
    expect("report records timestamp", strstr(report, "<key>timestamp</key>") != NULL);
    expect("report reaches the holding stage", strstr(report, "<integer>5</integer>") != NULL);
    expect("marker is written next to the report",
           read_report(idevice_webkit_marker_path) != NULL);
  }

  prepare(host_main, self_path, 1);
  fake_manifest_path = NULL;
  idevice_webkit_start();
  expect("host without a manifest writes no report",
         read_report(idevice_webkit_report_path) == NULL);

  prepare(host_main, "/tmp/WebSafeWebKit.dylib", 1);
  fake_manifest_path = host_manifest;
  idevice_webkit_start();
  expect("dylib loaded from a test path writes no report",
         read_report(idevice_webkit_report_path) == NULL);

  prepare("/private/preboot/UUID/procursus/other.app/other", self_path, 1);
  fake_manifest_path = host_manifest;
  idevice_webkit_start();
  expect("manifest of another host does not match",
         read_report(idevice_webkit_report_path) == NULL);

  prepare("/private/preboot/UUID/dopamine-X/procursus/usr/bin/tool", self_path, 1);
  fake_manifest_path =
      "/var/jb/usr/lib/TweakInject/com.websafe.idevice.host.UUID-dopamine-X-procursus-usr-bin-tool.plist";
  idevice_webkit_start();
  expect("a bootstrap tool is not taken for a host",
         read_report(idevice_webkit_report_path) == NULL);

  prepare(host_main, self_path, 0);
  fake_manifest_path = host_manifest;
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("report still written when WebKit was not loaded yet", report != NULL);
  if (report != NULL) {
    expect("missing WebKit leaves imagePath empty",
           strstr(report, "<key>imagePath</key>\n\t<string></string>") != NULL);
  }

  printf("\nfailures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
