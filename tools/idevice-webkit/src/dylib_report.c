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
   prefix: the header, then six keys whose values are at most 24 bytes each.
   That prefix is under a tenth of the buffer and does not depend on any
   free-form text, so no later field can push a decision field out. The long
   texts are written after them and are what a truncation can cut. */

#define document_capacity 12288

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

static void render_number(char *out, usize capacity, i64 value) {
  char digits[24];
  usize count = 0;
  out[0] = 0;
  /* The magnitude goes through the unsigned type, so the most negative value
     renders without the negation overflowing. A negative value used to render
     as "0", which made a failed call indistinguishable from a successful one
     reporting zero; the sign is written now, so no negative reads as zero. */
  bool negative = value < 0;
  u64 magnitude = negative ? (u64)0 - (u64)value : (u64)value;
  while (magnitude > 0 && count < sizeof(digits)) {
    digits[count++] = (char)('0' + (int)(magnitude % 10));
    magnitude /= 10;
  }
  if (count == 0) {
    digits[count++] = '0';
  }
  usize written = 0;
  if (negative && written + 1 < capacity) {
    out[written++] = '-';
  }
  out[written] = 0;
  while (count > 0 && written + 1 < capacity) {
    out[written++] = digits[--count];
  }
  out[written] = 0;
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
    render_number(record.timestamp, sizeof(record.timestamp), now.seconds);
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
  render_number(record.stage, sizeof(record.stage), (i64)stage);
}

void dylib_report_set_claim(const claim_result *claim) {
  text_copy(record.claim_skipped, claim->skipped ? "true" : "false", sizeof(record.claim_skipped));
  text_copy(record.claim_marked, claim->marked ? "true" : "false", sizeof(record.claim_marked));
  render_number(record.claim_mark_status, sizeof(record.claim_mark_status), (i64)claim->mark_status);
  text_copy(record.claim_mark_error, claim->mark_error, sizeof(record.claim_mark_error));
  render_number(record.claim_loaded, sizeof(record.claim_loaded), (i64)claim->loaded);
  render_number(record.claim_failed_index, sizeof(record.claim_failed_index),
                (i64)claim->failed_index);
  text_copy(record.claim_failed_path, claim->failed_path, sizeof(record.claim_failed_path));
  text_copy(record.claim_error, claim->error, sizeof(record.claim_error));
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
  /* The long free-form texts last: they are the fields a truncation may cut,
     and losing the tail of an error string is better than losing the state. */
  append_text(document, &position, "claimMarkError", record.claim_mark_error);
  append_text(document, &position, "claimFailedPath", record.claim_failed_path);
  append_text(document, &position, "claimError", record.claim_error);
  append(document, &position, "</dict>\n</plist>\n");
  return write_file(path, document, text_length(document));
}
