#include "dylib_report.h"

#include "file_writer.h"
#include "textkit.h"
#include "types.h"

#define document_capacity 9216

typedef struct report_record {
  char bundle_identifier[plist_value_capacity];
  char dlopen_path[plist_value_capacity];
  char dlopen_status[8];
  char dlopen_error[plist_value_capacity];
  char image_path[plist_value_capacity];
  char image_version[plist_value_capacity];
  char timestamp[24];
  char stage[16];
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
  if (value <= 0) {
    text_copy(out, "0", capacity);
    return;
  }
  while (value > 0 && count < sizeof(digits)) {
    digits[count++] = (char)('0' + (int)(value % 10));
    value /= 10;
  }
  usize written = 0;
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

void dylib_report_begin(const char *bundleIdentifier) {
  clock_value now = {0, 0};
  text_set(&record, 0, sizeof(record));
  text_copy(record.bundle_identifier, bundleIdentifier, sizeof(record.bundle_identifier));
  text_copy(record.dlopen_status, "false", sizeof(record.dlopen_status));
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

void dylib_report_set_stage(u32 stage) {
  render_number(record.stage, sizeof(record.stage), (i64)stage);
}

void dylib_report_write(const char *path) {
  char document[document_capacity];
  usize position = 0;
  document[0] = 0;
  append(document, &position,
         "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
         "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
         "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
         "<plist version=\"1.0\">\n<dict>\n");
  append_text(document, &position, "bundleId", record.bundle_identifier);
  append(document, &position, "\t<key>handleOk</key>\n\t<");
  append(document, &position, record.dlopen_status);
  append(document, &position, "/>\n");
  append_text(document, &position, "path", record.dlopen_path);
  append_text(document, &position, "error", record.dlopen_error);
  append_text(document, &position, "imagePath", record.image_path);
  append_text(document, &position, "imageVersion", record.image_version);
  append_text(document, &position, "timestamp", record.timestamp);
  append(document, &position, "\t<key>stage</key>\n\t<integer>");
  append(document, &position, record.stage);
  append(document, &position, "</integer>\n</dict>\n</plist>\n");
  write_file(path, document, text_length(document));
}
