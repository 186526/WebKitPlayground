#include "fake_dyld.h"
#include "fake_objc.h"
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
#include "../src/objc_runtime.c"
#include "../src/bundle_lookup.c"
#include "fixtures/fake_objc.c"
#include "fixtures/fake_dyld.c"
#include "../src/filter_match.c"
#include "../src/idevice_webkit.c"

#include "../src/claim_images.h"
#define maximum_report_size 4096
#define work_directory_name "/idevice-webkit-test"
static char work_directory[256];


/* The load is the moment the framework image appears, so the test answers the
   load by telling the fixture, and RTLD_NOLOAD keeps its device meaning: it
   answers a handle only for an image already loaded, and the constructor asks
   for the image after the load rather than before it. */

/* The seven-image claim is stubbed here, not run: it spawns jbctl and dlopens
   /var/jb images that no host test has, and tests/claim_images_test.c checks
   the sequence itself against recorders. This stub records whether the
   constructor reached the claim, which is what the two cases below read: it
   runs once for a process that passed the host check and never for one that
   did not.

   The answer it gives is a whole claim_result, because the report copies every
   field of it. The default is the success shape; claim_stub_failure switches
   it to the shape of a run that stopped at WebCore, which is what
   case_claim_report needs to see all seven fields written rather than their
   defaults. */

static int claim_runs;
static int claim_stub_failure;

void claim_images_run(claim_result *result) {
  claim_runs++;
  memset(result, 0, sizeof(*result));
  if (!claim_stub_failure) {
    result->marked = 1;
    result->mark_status = mark_done;
    result->loaded = claim_image_count;
    return;
  }
  result->marked = 0;
  result->mark_status = mark_child_nonzero;
  text_copy(result->mark_error, "jbctl exited with wait status 512", sizeof(result->mark_error));
  result->loaded = 3;
  result->failed_index = 4;
  text_copy(result->failed_path, "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
            sizeof(result->failed_path));
  text_copy(result->error, "Symbol not found: _WTFSignpostIndirectLoggingEnabled",
            sizeof(result->error));
}

void *library_load(const char *path) {
  (void)path;
  return fake_framework_load();
}
char *library_last_error(void) {
  return "test: no framework";
}
/* The constructor asks three things of the process: where the executable is
   (_NSGetExecutablePath), whether the framework is loaded (a dlopen handle for
   its literal path, then the same path without loading it) and what address a
   class it defines has (dladdr). The fixture answers all three; these two
   wrappers route the lookups to it so no part of the code under test is
   replaced. */

void *library_find(const char *path) {
  (void)path;
  return fake_framework_handle();
}

/* The constructor reaches the Objective-C runtime and CoreFoundation through
   the process's own symbol table, so both go through symbol_find_global here
   with the device's meaning: a name that is not a C symbol answers nothing.
   objc_getClass, sel_registerName and objc_msgSend reach the fixture's object
   tables, which is what makes a case fail when a method name is looked up as
   though it were a C symbol. */

void *symbol_find(void *library, const char *name) {
  if (library == 0) {
    return 0;
  }
  /* The framework handle is the one object whose exports are C symbols:
     OBJC_CLASS_$_WKWebView is what dlopen's image exports, and it is the
     address dladdr turns into the image base. A name that is not one of those
     exports still answers nothing, as it does on the device. */
  if (strncmp(name, "OBJC_CLASS_$_", 13) == 0) {
    return (void *)fake_framework_symbol(name);
  }
  return 0;
}

void *symbol_find_global(const char *name) {
  if (strcmp(name, "objc_getClass") == 0) {
    return (void *)fake_objc_class_named;
  }
  if (strcmp(name, "sel_registerName") == 0) {
    return (void *)fake_objc_selector_named;
  }
  if (strcmp(name, "objc_msgSend") == 0) {
    return (void *)fake_objc_msg_send;
  }
  return fake_cf_symbol(name);
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

/* The value of one key, as the report spells it: the text between the string
   tags, or the digits between the integer tags. */

static int field_value(const char *report, const char *key, char *out, usize capacity) {
  char opening[128];
  snprintf(opening, sizeof(opening), "\t<key>%s</key>\n\t<", key);
  const char *at = strstr(report, opening);
  if (at == NULL) {
    return 0;
  }
  at += strlen(opening);
  const char *closing = NULL;
  if (strncmp(at, "string>", 7) == 0) {
    at += 7;
    closing = "</string>";
  } else if (strncmp(at, "integer>", 8) == 0) {
    at += 8;
    closing = "</integer>";
  } else {
    return 0;
  }
  const char *end = strstr(at, closing);
  if (end == NULL || (usize)(end - at) >= capacity) {
    return 0;
  }
  memcpy(out, at, (usize)(end - at));
  out[end - at] = 0;
  return 1;
}

/* The fields the two runs of a placeholder are read through. A host that
   passed the copy check and then failed the host check leaves the path of its
   copy here, while a host that passed both leaves the fields empty. */

typedef struct placeholder_view {
  char stage[16];
  char matchVia[32];
  char bundleId[plist_value_capacity];
  char selfImage[plist_value_capacity];
  char selfError[plist_value_capacity];
} placeholder_view;

static int read_placeholder(const char *report, placeholder_view *view) {
  return report != NULL &&
         field_value(report, "stage", view->stage, sizeof(view->stage)) &&
         field_value(report, "matchVia", view->matchVia, sizeof(view->matchVia)) &&
         field_value(report, "bundleId", view->bundleId, sizeof(view->bundleId)) &&
         field_value(report, "selfImage", view->selfImage, sizeof(view->selfImage)) &&
         field_value(report, "selfError", view->selfError, sizeof(view->selfError));
}

/* Each read has its own buffer, because a case reads the report, the file of
   the others and the marker in one expression, and one static buffer would
   hand the second read over the first one's text. */

static char report_text[maximum_report_size];
static char others_text[maximum_report_size];
static char marker_text[maximum_report_size];

static char *read_report(const char *path) {
  char *text = path == idevice_webkit_marker_path  ? marker_text
               : path == idevice_webkit_others_path ? others_text
                                                    : report_text;
  FILE *file = fopen(path, "r");
  if (file == NULL) {
    return NULL;
  }
  usize length = fread(text, 1, maximum_report_size - 1, file);
  text[length] = 0;
  fclose(file);
  return text;
}

/* The three buffers above take 4 KiB, which is more than any report the writer
   produces at normal field sizes but less than the writer's own limit. A case
   that has to judge truncation therefore reads the file twice: once whole, into
   a buffer of the writer's capacity, and once as a length taken from the file
   itself. Comparing that length against the writer's capacity is the only way
   to tell "the document was cut" from "the test's buffer was filled". */

static char whole_text[document_capacity];

static char *read_report_whole(const char *path) {
  FILE *file = fopen(path, "r");
  if (file == NULL) {
    return NULL;
  }
  usize length = fread(whole_text, 1, document_capacity - 1, file);
  whole_text[length] = 0;
  fclose(file);
  return whole_text;
}

static usize report_file_length(const char *path) {
  FILE *file = fopen(path, "rb");
  if (file == NULL) {
    return 0;
  }
  if (fseek(file, 0, SEEK_END) != 0) {
    fclose(file);
    return 0;
  }
  long length = ftell(file);
  fclose(file);
  return length < 0 ? 0 : (usize)length;
}

/* The dylib drops the leading slash and turns every other slash into a dash,
   so a host that starts at /Applications has a file name starting with "-". */

#define manifest_path_for(host) "/var/jb/usr/lib/TweakInject/com.websafe.idevice.host." host ".plist"

/* Each case registers the host app bundle and the manifest with the same
   bundle identifier, which is what the device has once the manifest is in
   place. The bundle identifier of a host is read from its .app directory, the
   way NSBundle.bundleWithPath: reads it, so every case registers the directory
   in place of the executable inside it. */


static void prepare_keeping_reports(const char *host, const char *self, int withWebKit) {
  fake_reset(host, withWebKit ? framework_path : 0, self);

  fake_objc_reset();
  fake_filters_reset(idevice_webkit_filter_path);

  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  stage_reset();
  claim_runs = 0;
  claim_stub_failure = 0;
}

/* A case starts a fresh process, so the files of the previous run are
   cleared. A case that reads the main report after a second process starts
   uses prepare_keeping_reports, because the main report is what it checks. */

static void prepare(const char *host, const char *self, int withWebKit) {
  prepare_keeping_reports(host, self, withWebKit);
  remove(idevice_webkit_report_path);
  remove(idevice_webkit_others_path);
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

/* A process outside the host set leaves a placeholder report behind, in the
   file of the others: an empty bundle identifier, no handle and no marker,
   and whichever branch of the filter was given; a subprocess the filter does
   not name stops at the entry stage, while a UIApplication process goes on to
   name its match. A placeholder keeps the class match, because the filter of
   such a process named UIApplication and the report must say so. The main
   report is not written by such a process, so it still holds what it held
   before. */

static void expect_placeholder(const char *name, int stage, const char *via) {
  char *report = read_report(idevice_webkit_others_path);
  char stageText[32];
  char matchText[80];
  snprintf(stageText, sizeof(stageText), "<integer>%d</integer>", stage);
  snprintf(matchText, sizeof(matchText), "<key>matchVia</key>\n\t<string>%s</string>", via);
  expect(name, report != NULL && found_in(report, stageText) &&
                   found_in(report, "\t<key>bundleId</key>\n\t<string></string>") &&
                   found_in(report, "\t<key>handleOk</key>\n\t<false/>") &&
                   found_in(report, matchText) &&
                   read_report(idevice_webkit_report_path) == NULL &&
                   read_report(idevice_webkit_marker_path) == NULL);
}

/* The paths of this dylib that the breadcrumb is checked through, each in the
   form dyld reports it on the device. */

#define installed_self "/var/jb/usr/lib/TweakInject/WebSafeWebKitFilter.dylib"
#define preboot_self "/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus/usr/lib/TweakInject/WebSafeWebKitFilter.dylib"
#define test_self "/tmp/WebSafeWebKitTest/WebSafeWebKitFilter.dylib"

/* Everything the others report says about the copy that was called: a nil
   image path leaves both strings empty, while a path outside TweakInject is
   written as dyld gave it beside the reason the check failed. */

static void expect_self(const char *name, const char *imagePath, const char *reason) {
  char *report = read_report(idevice_webkit_others_path);
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
   no marker, and nothing in the main report, because such a copy cannot know
   whether it runs inside one of the hosts. */

static void expect_not_installed(const char *name, const char *imagePath, const char *reason) {
  char *report = read_report(idevice_webkit_others_path);
  expect(name, report != NULL && found_in(report, "\t<integer>3</integer>") &&
                   found_in(report, "\t<key>bundleId</key>\n\t<string></string>") &&
                   found_in(report, "\t<key>handleOk</key>\n\t<false/>") &&
                   read_report(idevice_webkit_report_path) == NULL &&
                   read_report(idevice_webkit_marker_path) == NULL);
  expect_self("  report names the self image and the failed check", imagePath, reason);
}

static void list_host(const char *host, const char *manifest, const char *identifier) {
  add_bundle(host, identifier);
  add_manifest(manifest, identifier);
}

/* A listed host leaves the holding stage behind, the last stage the
   constructor reaches, and names the copy that was called with no failed
   check beside it. The class branch is read first, as libinjector reads it,
   so a UIApplication process that is also listed in Bundles reports classes. */

static void case_host(const char *name, const char *host, const char *manifest,
                      const char *bundle, const char *identifier, const char *self) {
  prepare(host, self, 1);

  list_host(bundle, manifest, identifier);
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  fake_filter_add_bundle(identifier);
  fake_bundle_register(identifier);
  idevice_webkit_start();
  char *report = read_report(idevice_webkit_report_path);
  char expectations[1024];
  snprintf(expectations, sizeof(expectations),
           "\t<key>selfImage</key>\n\t<string>%s</string>\n"
           "\t<key>selfError</key>\n\t<string></string>",
           self);
  expect(name, report != NULL);
  if (report != NULL) {
    expect("  report reaches the holding stage", found_in(report, "\t<integer>7</integer>"));
    expect("  report records the bundle identifier", found_in(report, identifier));
    expect("  the class branch is read first, so matchVia is classes",
           found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>"));
    expect("  report names the installed copy with no failed check",
           found_in(report, expectations));
    expect("  marker is written next to the report",
           read_report(idevice_webkit_marker_path) != NULL);
    expect("  the seven images are claimed for this host", claim_runs == 1);
    expect("  the claim's answer reaches the report",
           found_in(report, "\t<key>claimMarked</key>\n\t<true/>") &&
               found_in(report, "\t<key>claimLoaded</key>\n\t<integer>7</integer>"));
    expect("  a run that was not skipped says so",
           found_in(report, "\t<key>claimSkipped</key>\n\t<false/>"));
    expect("  a marking that succeeded says so with its own state",
           found_in(report, "\t<key>claimMarkStatus</key>\n\t<integer>1</integer>"));
  }
}

/* Every field the claim answers with has to reach the report, not only the two
   that a success happens to fill. This case runs the stub in the failure shape
   the device measured in a hard-linked host — three loaded, stop at WebCore —
   and reads each of the eight keys out of the written document. The two long
   texts are the ones a truncation would cut first, so they are read whole. */

static void case_claim_report(void) {
  prepare("/Applications/MobileSafari.app/MobileSafari", installed_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  fake_filter_add_bundle("com.apple.mobilesafari");
  fake_bundle_register("com.apple.mobilesafari");
  claim_stub_failure = 1;
  idevice_webkit_start();
  char *report = read_report(idevice_webkit_report_path);
  expect("a failed claim writes a report", report != NULL);
  if (report == NULL) {
    return;
  }
  char value[plist_value_capacity];
  expect("  claimSkipped is false for a run that was attempted",
         found_in(report, "\t<key>claimSkipped</key>\n\t<false/>"));
  expect("  claimMarked says the marking failed",
         found_in(report, "\t<key>claimMarked</key>\n\t<false/>"));
  expect("  claimMarkStatus carries the state, not a zero",
         field_value(report, "claimMarkStatus", value, sizeof(value)) &&
             strcmp(value, "5") == 0);
  expect("  claimLoaded carries the count reached",
         field_value(report, "claimLoaded", value, sizeof(value)) && strcmp(value, "3") == 0);
  expect("  claimFailedIndex carries the position",
         field_value(report, "claimFailedIndex", value, sizeof(value)) &&
             strcmp(value, "4") == 0);
  expect("  claimFailedPath names the image that failed",
         field_value(report, "claimFailedPath", value, sizeof(value)) &&
             strcmp(value, "/var/jb/Library/Frameworks/WebCore.framework/WebCore") == 0);
  expect("  claimError carries the dlerror text",
         field_value(report, "claimError", value, sizeof(value)) &&
             strcmp(value, "Symbol not found: _WTFSignpostIndirectLoggingEnabled") == 0);
  expect("  claimMarkError names the call that failed",
         field_value(report, "claimMarkError", value, sizeof(value)) &&
             strcmp(value, "jbctl exited with wait status 512") == 0);
  expect("  the document is closed", found_in(report, "</dict>\n</plist>\n"));
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
  snprintf(idevice_webkit_filter_path, sizeof(idevice_webkit_filter_path),
           "%s/WebSafeWebKitFilter.plist", work_directory);
  snprintf(idevice_webkit_others_path, sizeof(idevice_webkit_others_path),
           "%s/com.websafe.idevice.others.plist", work_directory);
  snprintf(idevice_webkit_marker_path, sizeof(idevice_webkit_marker_path),
           "%s/com.websafe.idevice.ready", work_directory);
  char command[300];
  snprintf(command, sizeof(command), "mkdir -p %s", work_directory);
  system(command);
  printf("stages: entered=%u filter=%u not-installed=%u bundle=%u attempted=%u written=%u holding=%u\n",
         (unsigned)stage_entered, (unsigned)stage_filter_read,
         (unsigned)stage_not_installed_copy, (unsigned)stage_bundle_resolved,
         (unsigned)stage_attempted, (unsigned)stage_report_written,
         (unsigned)stage_holding);

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

  case_claim_report();

  char *report = read_report(idevice_webkit_report_path);
  if (report != NULL) {
    char shown[maximum_report_size];
    snprintf(shown, sizeof(shown), "%s", report);
    show("\n--- report written for the bootstrap host, from the test fixtures ---\n%s\n",
         shown);
  }

  /* The host writes the report five times, and only the last write survives in
     the file, so the order is read from the log the writer fixture keeps. The
     first write to the main report is the one that begins it, and it has to
     carry stage 4: a begin() that runs after stage_reach(4) resets the stage to
     0 and leaves the first record unreadable by the table that has no 0 row.
     Stage 5 has to reach the disk on its own, because it is the write that
     carries handleOk and error. */

  prepare(safari, installed_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  fake_write_log_reset();
  idevice_webkit_start();
  {
    int firstMain = -1;
    int stage5 = -1;
    for (int index = 0; index < fake_write_log_length(); index++) {
      if (fake_write_log_path(index) == idevice_webkit_report_path && firstMain < 0) {
        firstMain = index;
      }
      if (fake_write_log_path(index) == idevice_webkit_report_path &&
          strcmp(fake_write_log_stage(index), "5") == 0) {
        stage5 = index;
      }
    }
    expect("the first write to the main report carries stage 4",
           firstMain >= 0 && strcmp(fake_write_log_stage(firstMain), "4") == 0);
    expect("stage 5 reaches the main report", stage5 >= 0);
    expect("  stage 5 comes after stage 4", stage5 > firstMain);
  }

  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_placeholder("host without a manifest leaves a placeholder", 2, "classes");

  prepare(safari, preboot_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  add_manifest(manifest_path_for("-Applications-MobileSafari.app-MobileSafari"), "com.tencent.xin");
  idevice_webkit_start();
  expect_placeholder("manifest naming another bundle leaves a placeholder", 2, "classes");

  /* The filter names a class and a bundle list, and every case below hands
     the constructor one combination of the two. The class of the template is
     the one a main application has; a WebKit subprocess has none of it and is
     not in the bundle list either, so it stops at the entry report. */

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  fake_filter_add_bundle("com.apple.mobilesafari");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("Safari is matched through Classes though Bundles cannot resolve it",
         report != NULL &&
             !found_in(report, "<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.apple.mobilesafari");
  fake_bundle_register("com.apple.mobilesafari");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a filter without Classes is matched through Bundles",
         report != NULL &&
             found_in(report, "<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  prepare(wechat, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.tencent.xin");
  fake_bundle_register("com.tencent.xin");
  list_host("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("an identifier in the registry is matched through Bundles",
         report != NULL &&
             found_in(report, "<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  prepare(wechat, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  fake_filter_add_bundle("com.tencent.xin");
  fake_bundle_register("another.application");
  list_host("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("an identifier the registry does not name falls back to Classes",
         report != NULL &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  /* The device's filter has one top-level key, Filter, and the two lists sit
     inside it. Asking the file dictionary for Classes answers nil and asking
     the Filter dictionary answers the list, which is the pair every report of
     1.0.13 and earlier got wrong: the file dictionary was asked alone, so
     matchVia came out none in every process on the device. */

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  {
    void *file = dictionary_for_file(idevice_webkit_filter_path);
    void *filter = object_for_key(file, "Filter");
    void *class_array = object_for_key(filter, "Classes");
    void *first = class_array == NULL
                      ? NULL
                      : objc_send_object_index(class_array, objc_selector("objectAtIndex:"), 0);
    const char *first_text = first == NULL ? NULL : objc_send_text(first, objc_selector("UTF8String"));
    expect("the file dictionary answers no Classes of its own",
           file != NULL && object_for_key(file, "Classes") == NULL);
    expect("  and no Bundles of its own", object_for_key(file, "Bundles") == NULL);
    expect("  the Filter dictionary answers the one-element Classes list",
           filter != NULL && class_array != NULL &&
               objc_send_count(class_array, objc_selector("count")) == 1 && first_text != NULL &&
               strcmp(first_text, "UIApplication") == 0);
  }

  /* A class named only inside the Filter subdictionary still matches. The
     reader of 1.0.13 asked the file dictionary alone, so this filter came out
     as matchVia none; the case is the product-level half of the shape pair
     above, and it is red whenever the Filter key is skipped. */

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a class listed only in the Filter subdictionary still matches",
         report != NULL &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  /* The same for the bundle branch: with no class to match and the identifier
     in the subdictionary's Bundles list, matchVia is bundles. Read from the
     file dictionary this filter would say none as well. */

  prepare(wechat, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.tencent.xin");
  fake_bundle_register("com.tencent.xin");
  list_host("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a bundle listed only in the Filter subdictionary still matches",
         report != NULL &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  /* A filter with no Filter key at all is the older flat shape: the file
     dictionary answers nil for "Filter" and carries the two lists as its own
     keys, so the reader's fallback is the only path that reaches them. The
     fixture answers nil for "Filter" in flat mode, which is what makes this
     case red for a reader whose filter_lists() dropped the fallback. */

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filters_flat();
  fake_filter_add_class("UIApplication");
  fake_class_register("UIApplication");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a filter without a Filter key keeps the flat shape",
         report != NULL &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_class("AWebContentProcessClass");
  fake_filter_add_bundle("com.apple.WebKit.WebContent");
  idevice_webkit_start();
  expect_placeholder("a WebKit subprocess stops after the filter with matchVia none", 2, "none");

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.apple.mobilesafari");
  fake_bundle_register("com.tencent.xin");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a system app outside the registry is not matched through Bundles",
         report != NULL &&
             found_in(report, "<key>matchVia</key>\n\t<string>none</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  /* The template lists the two Safari identifiers before the container apps,
     so WeChat is the third element. Reading only the first element would
     report this process as matched by none. */

  prepare(wechat, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.apple.mobilesafari");
  fake_filter_add_bundle("com.apple.SafariViewService");
  fake_filter_add_bundle("com.tencent.xin");
  fake_filter_add_bundle("com.google.chrome.ios");
  fake_bundle_register("com.tencent.xin");
  list_host("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a bundle third in the list is matched through Bundles",
         report != NULL &&
             found_in(report, "<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<integer>7</integer>"));

  /* CFStringCreateMutableCopy takes the allocator, the capacity and the
     string. The filter names the identifier in capitals and the registry
     answers only the lowercased form, so the first lookup fails and the case
     reads the copy the second lookup is made from. The stub records the first
     two arguments, and calling CFAllocatorGetDefault is the only way to know
     the allocator handed over is the object it returned, not the function. */

  prepare(wechat, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("COM.TENCENT.XIN");
  fake_bundle_register("com.tencent.xin");
  list_host("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
            manifest_path_for("-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat"),
            "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a capitalised identifier is matched through its lowercased copy",
         report != NULL &&
             found_in(report, "<key>matchVia</key>\n\t<string>bundles</string>") &&
             found_in(report, "\t<integer>7</integer>"));
  void *(*allocator_default)(void) =
      (void *(*)(void))fake_cf_symbol("CFAllocatorGetDefault");
  expect("the lowercase copy is made with the default allocator",
         allocator_default != NULL &&
             fake_filter_last_copy_allocator() == allocator_default() &&
             fake_filter_last_copy_allocator() != NULL);
  expect("the lowercase copy has room for the identifier and its end",
         fake_filter_last_copy_capacity() == (i64)strlen("COM.TENCENT.XIN") + 1);


  /* The four hosts below lose their manifest one at a time. The report is
     still written, and it says how the process was matched, because the match
     was decided before the manifest was read. */
  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_others_path);
  expect("Safari without its manifest still reports the class match",
         report != NULL && found_in(report, "\t<integer>2</integer>") &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>"));

  prepare(wechat, installed_self, 1);
  add_bundle("/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app",
             "com.tencent.xin");
  idevice_webkit_start();
  report = read_report(idevice_webkit_others_path);
  expect("WeChat without its manifest still reports the class match",
         report != NULL && found_in(report, "\t<integer>2</integer>") &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>"));

  prepare(chrome, installed_self, 1);
  add_bundle("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
             "com.google.chrome.ios");
  idevice_webkit_start();
  report = read_report(idevice_webkit_others_path);
  expect("Chrome without its manifest still reports the class match",
         report != NULL && found_in(report, "\t<integer>2</integer>") &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>"));

  prepare("/Applications/SafariViewService.app/SafariViewService", installed_self, 1);
  add_bundle("/Applications/SafariViewService.app", "com.apple.SafariViewService");
  idevice_webkit_start();
  report = read_report(idevice_webkit_others_path);
  expect("SafariViewService without its manifest still reports the class match",
         report != NULL && found_in(report, "\t<integer>2</integer>") &&
             found_in(report, "\t<key>matchVia</key>\n\t<string>classes</string>"));

  /* NSBundle.bundleWithPath: answers for the .app directory, not for the
     executable inside it, so an executable registered with an identifier is
     still not a bundle and the host cannot be named. */

  prepare(safari, installed_self, 1);
  list_host(safari, manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_placeholder("an executable path is not a bundle", 2, "classes");

  /* The two path methods declare an NSString argument, so the lookup has to
     build one; a C string there is read as an isa by Foundation and the device
     stops on it. These cases read that lookup at its entry, so the bytes
     handed to the message are the ones the lookup itself builds, not an
     argument a case chooses. The third case holds the fixture to the same
     rule: an object it holds is answered and a C string is not, which is what
     makes the two above them fail when the lookup goes back to handing bytes
     over. */

  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  add_manifest(manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
               "com.apple.mobilesafari");
  {
    char identifier[plist_value_capacity];
    expect("a bundle directory answers its identifier at the entry",
           bundle_path_identifier("/Applications/MobileSafari.app", identifier,
                                  sizeof(identifier)) &&
               strcmp(identifier, "com.apple.mobilesafari") == 0);
    expect("  a manifest file answers its identifier the same way",
           plist_file_identifier(manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
                                 identifier, sizeof(identifier)) &&
               strcmp(identifier, "com.apple.mobilesafari") == 0);
    expect("  a C string at the path method answers no bundle",
           objc_send_object_text(objc_class("NSBundle"), objc_selector("bundleWithPath:"),
                                 "/Applications/MobileSafari.app") == NULL);
  }

  /* The file of the others is the only one a process that is not a host leaves
     behind, so it has to say which check stopped that process. A host that
     passed both checks writes that file before the copy check and leaves both
     self fields empty; a host that passed the copy check and then failed the
     host check writes its own path there. The two records are the same at the
     filter stage and differ in the copy check alone. */

  prepare(safari, installed_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  placeholder_view accepted;
  placeholder_view holding;
  int accepted_read = read_placeholder(read_report(idevice_webkit_others_path), &accepted);
  int holding_read = read_placeholder(read_report(idevice_webkit_report_path), &holding);
  expect("a host that passed both checks leaves the copy check empty in the others file",
         accepted_read && strcmp(accepted.stage, "2") == 0 &&
             strcmp(accepted.matchVia, "classes") == 0 && accepted.bundleId[0] == 0 &&
             accepted.selfImage[0] == 0 && accepted.selfError[0] == 0 && holding_read &&
             strcmp(holding.stage, "7") == 0);

  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  idevice_webkit_start();
  placeholder_view rejected;
  int rejected_read = read_placeholder(read_report(idevice_webkit_others_path), &rejected);
  expect("a host that failed the host check names its copy in the others file",
         rejected_read && strcmp(rejected.stage, "2") == 0 &&
             strcmp(rejected.matchVia, "classes") == 0 && rejected.bundleId[0] == 0 &&
             strcmp(rejected.selfImage, installed_self) == 0 && rejected.selfError[0] == 0);
  expect("the two records differ in the copy check alone",
         accepted_read && rejected_read && strcmp(accepted.stage, rejected.stage) == 0 &&
             strcmp(accepted.matchVia, rejected.matchVia) == 0 &&
             strcmp(accepted.bundleId, rejected.bundleId) == 0 &&
             strcmp(accepted.selfError, rejected.selfError) == 0 &&
             strcmp(accepted.selfImage, rejected.selfImage) != 0);

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

  /* The process has images besides this dylib: the host executable, the
     framework this tweak loads, and the Foundation it asks its questions of.
     None of them may be taken for the copy that was called. The framework is
     the one that matters, because its class address is what dladdr is asked
     about when the version text is read. */

  prepare(chrome, installed_self, 1);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("the host executable is not the copy that was called",
         report != NULL && found_in(report, "\t<integer>7</integer>") &&
             found_in(report, "\t<key>selfImage</key>\n\t<string>" installed_self "</string>"));

  prepare(safari, installed_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("a loaded framework does not take the self image's place", report != NULL);
  if (report != NULL) {
    expect("  the report still reaches the holding stage",
           found_in(report, "\t<integer>7</integer>"));
    expect("  the self image is still the installed copy",
           found_in(report, "\t<key>selfImage</key>\n\t<string>" installed_self "</string>"));
    expect("  no failed copy check is recorded",
           found_in(report, "\t<key>selfError</key>\n\t<string></string>"));
    expect("  the framework is named by the path dladdr resolved",
           found_in(report, "\t<key>imagePath</key>\n\t<string>/private" framework_path "</string>"));
    expect("  and not by the literal path the load was given",
           !found_in(report, "\t<key>imagePath</key>\n\t<string>" framework_path "</string>"));
  }

  prepare(safari, test_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect_not_installed("a loaded framework does not hide a test copy either", test_self,
                       "self image is not in the TweakInject directory");

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
  expect_placeholder("a process that is not an app leaves a placeholder", 2, "classes");

  prepare(chrome, installed_self, 0);
  list_host("/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app",
            manifest_path_for("-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome"),
            "com.google.chrome.ios");
  idevice_webkit_start();
  report = read_report(idevice_webkit_report_path);
  expect("report still written when WebKit was not loaded yet", report != NULL);
  if (report != NULL) {
    expect("  missing WebKit leaves imagePath empty",
           found_in(report, "\t<key>imagePath</key>\n\t<string></string>"));
    expect("  handleOk is false", found_in(report, "\t<key>handleOk</key>\n\t<false/>"));
  }

  /* The claim runs only for a process the host check accepted. The three cases
     below reach the constructor and are stopped by a different check each: a
     process that failed the copy check, a process that passed it and failed
     the host check, and a process the filter did not match at all. A claim
     that runs before either check maps seven images into every app the
     filter names, which is the whole system, so none of the three may reach
     it. */

  prepare(safari, test_self, 1);
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect("a copy outside TweakInject never reaches the claim", claim_runs == 0);

  prepare(safari, installed_self, 1);
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  idevice_webkit_start();
  expect("a host without its manifest never reaches the claim", claim_runs == 0);

  prepare(safari, installed_self, 1);
  fake_filters_reset(idevice_webkit_filter_path);
  fake_filter_add_bundle("com.apple.WebKit.WebContent");
  add_bundle("/Applications/MobileSafari.app", "com.apple.mobilesafari");
  list_host("/Applications/MobileSafari.app",
            manifest_path_for("-Applications-MobileSafari.app-MobileSafari"),
            "com.apple.mobilesafari");
  idevice_webkit_start();
  expect("a process the filter did not match never reaches the claim", claim_runs == 0);

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
    char kept[sizeof(report_text)];
    char before[maximum_report_size];
    snprintf(kept, sizeof(kept), "%s", read_report(idevice_webkit_report_path));
    snprintf(before, sizeof(before), "%s", kept);
    stage_reset();
    fake_refuse_writes(1);
    idevice_webkit_start();
    fake_refuse_writes(0);
    char *after = read_report(idevice_webkit_report_path);
    char kept_text[maximum_report_size];
    snprintf(kept_text, sizeof(kept_text), "%s", after == NULL ? "" : after);
    expect("a refused entry report stops the constructor",
           strcmp(kept_text, before) == 0 &&
               found_in(kept_text, "<string>com.google.chrome.ios</string>") &&
               read_report(idevice_webkit_marker_path) != NULL);
    stage_reset();
  }

  /* The filter names UIApplication, so every app on the device is loaded
     with this dylib. An app that is not one of the four hosts writes the file
     of the others and leaves the main report alone, so the record of the last
     host stays readable while unrelated apps start and stop. Maps has no
     manifest here, which is how every app outside the four looks. */

  {
    char kept[sizeof(report_text)];
    snprintf(kept, sizeof(kept), "%s", read_report(idevice_webkit_report_path));
    prepare_keeping_reports("/Applications/Maps.app/Maps", installed_self, 1);
    add_bundle("/Applications/Maps.app", "com.apple.Maps");
    idevice_webkit_start();
    char *after = read_report(idevice_webkit_report_path);
    char *others = read_report(idevice_webkit_others_path);
    expect("an unrelated app does not overwrite the host report",
           kept[0] != 0 && after != NULL && strcmp(kept, after) == 0 &&
               found_in(after, "<string>com.google.chrome.ios</string>") &&
               found_in(after, "\t<integer>7</integer>"));
    expect("  the unrelated app leaves its own report",
           others != NULL && found_in(others, "\t<integer>2</integer>") &&
               found_in(others, "\t<key>matchVia</key>\n\t<string>classes</string>"));
  }

  /* The number renderer is reached through the report, but its sign handling
     is easier to read directly. A negative value used to come out as "0" — the
     value a successful call reports — so a failed call was indistinguishable
     from one that answered zero. The cases below are the ones the report's
     integers take: small positive, zero, negative, and both ends of the type. */
  {
    char rendered[32];
    text_number(rendered, sizeof(rendered), 0);
    expect("a zero renders as \"0\"", strcmp(rendered, "0") == 0);
    text_number(rendered, sizeof(rendered), 7);
    expect("a positive renders as its digits", strcmp(rendered, "7") == 0);
    text_number(rendered, sizeof(rendered), -1);
    expect("a negative keeps its sign", strcmp(rendered, "-1") == 0);
    text_number(rendered, sizeof(rendered), 512);
    expect("a wait status renders as given", strcmp(rendered, "512") == 0);
    text_number(rendered, sizeof(rendered), -512);
    expect("a negative wait status keeps its sign", strcmp(rendered, "-512") == 0);
    text_number(rendered, sizeof(rendered), (i64)0 - (i64)9000000000000000000LL);
    expect("the most negative magnitude does not overflow",
           strcmp(rendered, "-9000000000000000000") == 0);
    text_number(rendered, 4, -1234);
    expect("a capacity too small truncates without losing the sign",
           strcmp(rendered, "-12") == 0);
  }

  /* A saturating record: the free-form fields at their full capacity, each one
     full of '&' so the escaping multiplies it. The document cannot fit, which
     is the measured limit of the buffer rather than something the writer can
     rule out. What must still hold is the part the writer does control: the
     decision fields are in the prefix, so they are all present and correct
     even though the text after them is cut. */
  {
    static char amplifier[plist_value_capacity];
    size_t at = 0;
    while (at + 1 < sizeof(amplifier)) {
      amplifier[at++] = '&';
    }
    amplifier[at] = 0;
    dylib_report_begin("com.example.saturate");
    dylib_report_set_dlopen(amplifier, NULL, amplifier);
    dylib_report_set_image(amplifier, amplifier);
    dylib_report_set_self(amplifier, false, amplifier);
    dylib_report_set_stage(3);
    claim_result saturating;
    memset(&saturating, 0, sizeof(saturating));
    saturating.skipped = true;
    saturating.marked = false;
    saturating.mark_status = mark_init_failed;
    saturating.loaded = 0;
    saturating.failed_index = 0;
    text_copy(saturating.mark_error, amplifier, sizeof(saturating.mark_error));
    text_copy(saturating.error, amplifier, sizeof(saturating.error));
    dylib_report_set_claim(&saturating);
    char saturate_path[512];
    snprintf(saturate_path, sizeof(saturate_path), "%s/saturate.plist", work_directory);
    expect("a saturated record still writes", dylib_report_write(saturate_path));
    /* The length is read from the file, not from a read into a buffer this
       test sized: the writer's limit is document_capacity and the read buffer
       above is smaller, so a strlen of the read buffer would answer 4095 for
       any document at least that long and would still pass if the writer grew
       its buffer and nothing was truncated at all. */
    usize written = report_file_length(saturate_path);
    expect("  the document is cut at the writer's limit, not at the read buffer",
           written == document_capacity - 1);
    expect("  and it is longer than the buffer the reader above uses",
           written > maximum_report_size - 1);
    const char *report = read_report_whole(saturate_path);
    expect("  the cut document is what the whole read found",
           report != NULL && strlen(report) == document_capacity - 1);
    char value[64];
    expect("  the stage survives the truncation",
           report != NULL && field_value(report, "stage", value, sizeof(value)) &&
               strcmp(value, "3") == 0);
    expect("  claimSkipped survives the truncation",
           report != NULL && found_in(report, "\t<key>claimSkipped</key>\n\t<true/>"));
    expect("  claimMarked survives the truncation",
           report != NULL && found_in(report, "\t<key>claimMarked</key>\n\t<false/>"));
    expect("  claimMarkStatus survives as a number that is not zero",
           report != NULL && field_value(report, "claimMarkStatus", value, sizeof(value)) &&
               strcmp(value, "6") == 0);
    expect("  claimLoaded survives the truncation",
           report != NULL && field_value(report, "claimLoaded", value, sizeof(value)) &&
               strcmp(value, "0") == 0);
    expect("  claimFailedIndex survives the truncation",
           report != NULL && field_value(report, "claimFailedIndex", value, sizeof(value)) &&
               strcmp(value, "0") == 0);
    expect("  the decision fields are all inside the cut document",
           report != NULL && found_in(report, "\t<key>claimFailedIndex</key>"));
    remove(saturate_path);
  }

  printf("\nfailures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
