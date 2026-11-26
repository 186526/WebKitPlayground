#include "dylib_report.h"

#include "claim_images.h"
#include "file_writer.h"
#include "textkit.h"
#include "types.h"

/* The record's field capacities sum to more than this buffer, and escape_text
   multiplies a field by up to five when it is full of & < >, so a document
   built from every field at capacity cannot fit: append() stops at the limit
   and the worst case ends mid-value with no </dict> and no </plist>. A bigger
   number alone does not remove that, because the fields are unbounded in the
   same dimension the buffer is.

   What is removable is which fields a truncation can cost. The decision fields
   are written first, immediately after the plist header, so they occupy a fixed
   prefix: the header, then the claim's six keys whose values are at most 24
   bytes each, then the chain's three counters, its seven step dictionaries
   whose texts are the fixed /var/jb paths, and the three system image
   readings. That prefix depends on no free-form text, so no later field can
   push a decision field out. The long texts are written after them and are what
   a truncation can cut.

   The size is the chain's 16384 rather than the claim's 12288: a report now
   carries both record shapes, so it is strictly larger than either side was
   sized for. */

#define document_capacity 16384

/* One record per chain step. The seven are written as a plist array of dicts so
   a reader can see a partial chain as a prefix rather than as a single verdict,
   and each carries its own index so a step that did not run is still countable.

   A step's uuid is the LC_UUID dyld registered for the image at that path, read
   from the mapped header. That is the reading which separates the two outcomes
   the device has been unable to tell apart: an 18.3 image that mapped, and a
   request dyld answered from the resident 16.1 image of the same name. The
   images field tells them apart a second way, because the dedupe case adds no
   image to the process. */

/* One record per chain step, one per step and no spare: a trailing entry would be
   written as an eighth step dictionary, which reads as a step the chain does not
   have. The count comes from the chain rather than a literal so the two cannot
   disagree. */
#define chain_step_capacity load_chain_count

typedef struct report_step {
  char index[8];
  char path[plist_value_capacity];
  char loaded[8];
  char images[8];
  char uuid[uuid_capacity];
  char error[plist_value_capacity];
} report_step;

typedef struct report_record {
  char bundle_identifier[plist_value_capacity];
  char dlopen_path[plist_value_capacity];
  char dlopen_status[8];
  char dlopen_error[plist_value_capacity];
  char image_path[plist_value_capacity];
  char image_version[plist_value_capacity];
  char self_image[plist_value_capacity];
  char self_error[plist_value_capacity];
  char match_via[16];
  char timestamp[24];
  char stage[16];
  char claim_skipped[8];
  char claim_marked[8];
  char claim_mark_status[24];
  char claim_mark_error[claim_error_capacity];
  char claim_loaded[16];
  char claim_failed_index[16];
  char claim_failed_path[claim_path_capacity];
  char claim_error[claim_error_capacity];
  char chain_ran[8];
  char chain_loaded[8];
  char chain_stopped[8];
  report_step chain[chain_step_capacity];
  char system_web_kit[8];
  char system_web_kit_uuid[uuid_capacity];
  char system_web_core[8];
  char system_web_core_uuid[uuid_capacity];
  char system_javascript_core[8];
  char system_javascript_core_uuid[uuid_capacity];
} report_record;

typedef struct clock_value {
  i64 seconds;
  i32 microseconds;
} clock_value;

extern int gettimeofday(clock_value *value, void *timezone);

static report_record record;

static char *escape_text(const char *text, char *out, usize capacity) {
  usize read = 0;
  usize written = 0;
  while (text[read] != 0 && written + 8 < capacity) {
    const char *replacement = NULL;
    if (text[read] == '&') {
      replacement = "&amp;";
    } else if (text[read] == '<') {
      replacement = "&lt;";
    } else if (text[read] == '>') {
      replacement = "&gt;";
    }
    if (replacement != NULL) {
      text_copy(out + written, replacement, capacity - written);
      written += text_length(replacement);
    } else {
      out[written++] = text[read];
    }
    read++;
  }
  out[written] = 0;
  return out;
}

static void append(char *document, usize *position, const char *text) {
  usize index = 0;
  while (text[index] != 0 && *position + 1 < document_capacity) {
    document[(*position)++] = text[index++];
  }
  document[*position] = 0;
}

static void append_text(char *document, usize *position, const char *key, const char *value) {
  char escaped[plist_value_capacity * 6];
  append(document, position, "\t<key>");
  append(document, position, key);
  append(document, position, "</key>\n\t<string>");
  append(document, position, escape_text(value, escaped, sizeof(escaped)));
  append(document, position, "</string>\n");
}

/* The match belongs to the process, not to one write of the report, so a
   begin() that runs after the filter was read keeps the answer it holds. The
   entry report runs begin() first, when the field is empty, and that is the
   one report that says match_none. */

void dylib_report_begin(const char *bundleIdentifier) {
  clock_value now = {0, 0};
  char match[sizeof(record.match_via)];
  bool matched = record.match_via[0] != 0;
  text_copy(match, record.match_via, sizeof(match));
  text_set(&record, 0, sizeof(record));
  text_copy(record.match_via, matched ? match : match_none, sizeof(record.match_via));
  text_copy(record.bundle_identifier, bundleIdentifier, sizeof(record.bundle_identifier));
  text_copy(record.dlopen_status, "false", sizeof(record.dlopen_status));
  text_copy(record.stage, "0", sizeof(record.stage));
  text_copy(record.claim_marked, "false", sizeof(record.claim_marked));
  text_copy(record.claim_skipped, "false", sizeof(record.claim_skipped));
  text_copy(record.claim_mark_status, "0", sizeof(record.claim_mark_status));
  text_copy(record.claim_loaded, "0", sizeof(record.claim_loaded));
  text_copy(record.claim_failed_index, "0", sizeof(record.claim_failed_index));
  if (gettimeofday(&now, NULL) == 0) {
    text_number(record.timestamp, sizeof(record.timestamp), now.seconds);
  } else {
    text_copy(record.timestamp, "0", sizeof(record.timestamp));
  }
}

void dylib_report_set_dlopen(const char *path, void *handle, const char *error) {
  text_copy(record.dlopen_path, path, sizeof(record.dlopen_path));
  text_copy(record.dlopen_status, handle == NULL ? "false" : "true",
            sizeof(record.dlopen_status));
  text_copy(record.dlopen_error, error == NULL ? "" : error, sizeof(record.dlopen_error));
}

void dylib_report_set_image(const char *imagePath, const char *version) {
  text_copy(record.image_path, imagePath == NULL ? "" : imagePath, sizeof(record.image_path));
  text_copy(record.image_version, version == NULL ? "" : version, sizeof(record.image_version));
}

void dylib_report_set_self(const char *imagePath, bool installedCopy, const char *reason) {
  text_copy(record.self_image, imagePath == NULL ? "" : imagePath, sizeof(record.self_image));
  text_copy(record.self_error, installedCopy ? "" : reason, sizeof(record.self_error));
}

void dylib_report_set_match(const char *via) {
  text_copy(record.match_via, via, sizeof(record.match_via));
}

void dylib_report_set_stage(u32 stage) {
  text_number(record.stage, sizeof(record.stage), (i64)stage);
}

void dylib_report_set_claim(const claim_result *claim) {
  text_copy(record.claim_skipped, claim->skipped ? "true" : "false", sizeof(record.claim_skipped));
  text_copy(record.claim_marked, claim->marked ? "true" : "false", sizeof(record.claim_marked));
  text_number(record.claim_mark_status, sizeof(record.claim_mark_status), (i64)claim->mark_status);
  text_copy(record.claim_mark_error, claim->mark_error, sizeof(record.claim_mark_error));
  text_number(record.claim_loaded, sizeof(record.claim_loaded), (i64)claim->loaded);
  text_number(record.claim_failed_index, sizeof(record.claim_failed_index),
                (i64)claim->failed_index);
  text_copy(record.claim_failed_path, claim->failed_path, sizeof(record.claim_failed_path));
  text_copy(record.claim_error, claim->error, sizeof(record.claim_error));
}

/* The chain, written as it stands. A step is copied whether it ran or not, and
   the copy is what separates "this step failed" from "this step was never
   attempted", because a step that did not run has no path and no outcome while a
   step that failed has both. */

void dylib_report_set_chain(const load_result *result) {
  if (result == NULL) {
    return;
  }
  text_copy(record.chain_ran, result->ran ? "true" : "false", sizeof(record.chain_ran));
  text_number(record.chain_loaded, sizeof(record.chain_loaded), (i64)result->loaded);
  text_number(record.chain_stopped, sizeof(record.chain_stopped), (i64)result->stopped);
  for (u32 index = 0; index < load_chain_count && index < chain_step_capacity; index++) {
    const load_step *step = &result->steps[index];
    report_step *entry = &record.chain[index];
    text_number(entry->index, sizeof(entry->index), (i64)(index + 1));
    text_copy(entry->path, load_chain_paths[index], sizeof(entry->path));
    /* A step with no outcome is one the chain never reached. It is left with an
       empty loaded field rather than written as false, so a reader does not
       count it as a failure.

       The comparison is `<=` because `stopped` is 1-based and is the position of
       the step that ran and failed: at that step `index + 1 == result->stopped`,
       and a strict `<` excluded exactly the one step whose outcome the report
       exists to carry. Read as `index + 1 <= result->stopped`, it says the steps
       up to and including the failing one have an outcome and the rest do not. */
    if (index + 1 <= result->stopped || (result->stopped == 0 && result->ran)) {
      text_copy(entry->loaded, step->loaded ? "true" : "false", sizeof(entry->loaded));
      text_number(entry->images, sizeof(entry->images), (i64)step->images);
      text_copy(entry->uuid, step->uuid, sizeof(entry->uuid));
      text_copy(entry->error, step->error, sizeof(entry->error));
    }
  }
}

/* The three system images the process holds, beside the seven. Compared with the
   chain's uuids, these answer whether the substitution reached the application or
   only this tweak's own loads. */

void dylib_report_set_system_images(const system_images *images) {
  if (images == NULL) {
    return;
  }
  text_copy(record.system_web_kit, images->web_kit.present ? "true" : "false",
            sizeof(record.system_web_kit));
  text_copy(record.system_web_kit_uuid, images->web_kit.uuid,
            sizeof(record.system_web_kit_uuid));
  text_copy(record.system_web_core, images->web_core.present ? "true" : "false",
            sizeof(record.system_web_core));
  text_copy(record.system_web_core_uuid, images->web_core.uuid,
            sizeof(record.system_web_core_uuid));
  text_copy(record.system_javascript_core, images->javascript_core.present ? "true" : "false",
            sizeof(record.system_javascript_core));
  text_copy(record.system_javascript_core_uuid, images->javascript_core.uuid,
            sizeof(record.system_javascript_core_uuid));
}

/* One chain step as a plist dictionary. A field whose text is empty is written
   as an empty string rather than left out, so every step has the same keys and a
   reader can iterate positions instead of testing for a key. */

static void append_step(char *document, usize *position, const report_step *entry) {
  append(document, position, "\t\t<dict>\n");
  append_text(document, position, "index", entry->index);
  append_text(document, position, "path", entry->path);
  append_text(document, position, "loaded", entry->loaded);
  append_text(document, position, "images", entry->images);
  append_text(document, position, "uuid", entry->uuid);
  append_text(document, position, "error", entry->error);
  append(document, position, "\t\t</dict>\n");
}

static void append_system_image(char *document, usize *position, const char *key,
                                const char *present, const char *uuidKey, const char *uuid) {
  append_text(document, position, key, present);
  append_text(document, position, uuidKey, uuid);
}

bool dylib_report_write(const char *path) {
  char document[document_capacity];
  usize position = 0;
  document[0] = 0;
  append(document, &position,
         "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
         "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
         "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
         "<plist version=\"1.0\">\n<dict>\n");
  /* The decision fields first, in a fixed prefix of at most a few hundred
     bytes, so a truncation later in the document cannot reach them. They are
     the fields a reader needs to tell one host shape from another; the
     free-form texts below are what a truncation may cut. */
  append(document, &position, "\t<key>stage</key>\n\t<integer>");
  append(document, &position, record.stage);
  append(document, &position, "</integer>\n");
  append(document, &position, "\t<key>claimSkipped</key>\n\t<");
  append(document, &position, record.claim_skipped);
  append(document, &position, "/>\n");
  append(document, &position, "\t<key>claimMarked</key>\n\t<");
  append(document, &position, record.claim_marked);
  append(document, &position, "/>\n");
  append(document, &position, "\t<key>claimMarkStatus</key>\n\t<integer>");
  append(document, &position, record.claim_mark_status);
  append(document, &position, "</integer>\n");
  append(document, &position, "\t<key>claimLoaded</key>\n\t<integer>");
  append(document, &position, record.claim_loaded);
  append(document, &position, "</integer>\n");
  append(document, &position, "\t<key>claimFailedIndex</key>\n\t<integer>");
  append(document, &position, record.claim_failed_index);
  append(document, &position, "</integer>\n");
  append_text(document, &position, "bundleId", record.bundle_identifier);
  append(document, &position, "\t<key>handleOk</key>\n\t<");
  append(document, &position, record.dlopen_status);
  append(document, &position, "/>\n");
  append_text(document, &position, "path", record.dlopen_path);
  append_text(document, &position, "error", record.dlopen_error);
  append_text(document, &position, "imagePath", record.image_path);
  append_text(document, &position, "imageVersion", record.image_version);
  append_text(document, &position, "selfImage", record.self_image);
  append_text(document, &position, "selfError", record.self_error);
  append_text(document, &position, "matchVia", record.match_via);
  append_text(document, &position, "timestamp", record.timestamp);
  /* The chain, then the three system images it is compared against. The keys are
     spelled so the two readings sit together: chainLoaded is what this tweak
     loaded, systemWebKit is what the application's own dependency resolved to.
     A run can be complete on the first and unchanged on the second.

     Stage is written once, at the top of the decision prefix above; this side
     used to write it here as well, and writing it twice would give the reader
     two keys of the same name to disagree about. */
  append(document, &position, "\t<key>chainRan</key>\n\t<");
  append(document, &position, record.chain_ran[0] == 0 ? "false" : record.chain_ran);
  append(document, &position, "/>\n");
  append_text(document, &position, "chainLoaded", record.chain_loaded);
  append_text(document, &position, "chainStopped", record.chain_stopped);
  append(document, &position, "\t<key>chain</key>\n\t<array>\n");
  for (u32 index = 0; index < chain_step_capacity; index++) {
    append_step(document, &position, &record.chain[index]);
  }
  append(document, &position, "\t</array>\n");
  append_system_image(document, &position, "systemWebKit", record.system_web_kit,
                      "systemWebKitUuid", record.system_web_kit_uuid);
  append_system_image(document, &position, "systemWebCore", record.system_web_core,
                      "systemWebCoreUuid", record.system_web_core_uuid);
  append_system_image(document, &position, "systemJavaScriptCore",
                      record.system_javascript_core, "systemJavaScriptCoreUuid",
                      record.system_javascript_core_uuid);
  /* The long free-form texts last: they are the fields a truncation may cut,
     and losing the tail of an error string is better than losing the state. */
  append_text(document, &position, "claimMarkError", record.claim_mark_error);
  append_text(document, &position, "claimFailedPath", record.claim_failed_path);
  append_text(document, &position, "claimError", record.claim_error);
  append(document, &position, "</dict>\n</plist>\n");
  return write_file(path, document, text_length(document));
}
