#include "macho_image.h"

#include "textkit.h"
#include "types.h"

#define mach_header_magic 0xfeedfacfu
#define segment_command 0x1u
#define id_dylib_command 0xdu
#define section_record_size 80u

typedef struct load_command {
  u32 command;
  u32 size;
} load_command;

typedef struct segment_command64 {
  u32 command;
  u32 size;
  char segment_name[16];
  u64 address;
  u64 memory_size;
  u64 file_offset;
  u64 file_size;
  u32 maximum_protection;
  u32 initial_protection;
  u32 section_count;
  u32 flags;
} segment_command64;

typedef struct section64 {
  char section_name[16];
  char segment_name[16];
  u64 address;
  u64 size;
  u64 offset;
} section64;

typedef struct dylib_command {
  u32 command;
  u32 size;
  u32 name_offset;
  u32 timestamp;
  u32 current_version;
  u32 compatibility_version;
} dylib_command;

typedef struct cf_string {
  void *isa;
  u64 flags;
  char *text;
} cf_string;

bool image_is_mach_header(const void *header) {
  if (header == NULL) {
    return false;
  }
  return ((const macho_header *)header)->magic == mach_header_magic;
}

const char *image_segment(const void *header, const char *segment, const char *section, u64 *size,
                          u64 *offset) {
  if (!image_is_mach_header(header)) {
    return NULL;
  }
  const macho_header *mach = (const macho_header *)header;
  const u8 *cursor = (const u8 *)header + sizeof(macho_header);
  for (u32 index = 0; index < mach->command_count; index++) {
    const load_command *command = (const load_command *)cursor;
    if (command->size == 0) {
      return NULL;
    }
    if (command->command == segment_command) {
      const segment_command64 *segmentHeader = (const segment_command64 *)command;
      if (text_equal(segmentHeader->segment_name, segment)) {
        const section64 *sections = (const section64 *)(cursor + sizeof(segment_command64));
        for (u32 sectionIndex = 0; sectionIndex < segmentHeader->section_count; sectionIndex++) {
          if (text_equal(sections[sectionIndex].section_name, section)) {
            *size = sections[sectionIndex].size;
            *offset = sections[sectionIndex].offset;
            return (const char *)sections[sectionIndex].address;
          }
        }
      }
    }
    cursor += command->size;
  }
  return NULL;
}

const char *image_install_name(const void *header, char *out, usize capacity) {
  if (!image_is_mach_header(header)) {
    return NULL;
  }
  const macho_header *mach = (const macho_header *)header;
  const u8 *cursor = (const u8 *)header + sizeof(macho_header);
  for (u32 index = 0; index < mach->command_count; index++) {
    const load_command *command = (const load_command *)cursor;
    if (command->size == 0) {
      return NULL;
    }
    if (command->command == id_dylib_command) {
      const dylib_command *record = (const dylib_command *)command;
      text_copy(out, (const char *)cursor + record->name_offset, capacity);
      return out;
    }
    cursor += command->size;
  }
  return NULL;
}

/* A __cfstring record holds the isa pointer, the flags word and the pointer to
   the characters. The keys live in a CFDictionary whose values follow the
   keys in memory, so the version is taken from the first text record after the
   CFBundleVersion key. Empty slots have a null isa or a null text pointer and
   are skipped. */

void macho_version_text(const void *header, char *out, usize capacity) {
  out[0] = 0;
  u64 size = 0;
  u64 offset = 0;
  const char *base = image_segment(header, "__DATA", "__cfstring", &size, &offset);
  if (base == NULL || size == 0) {
    return;
  }
  usize recordCount = (usize)(size / sizeof(cf_string));
  const cf_string *records = (const cf_string *)base;
  bool key_seen = false;
  for (usize index = 0; index < recordCount; index++) {
    if (records[index].isa == NULL || records[index].text == NULL) {
      continue;
    }
    if (!key_seen) {
      key_seen = text_equal(records[index].text, "CFBundleVersion");
      continue;
    }
    text_copy(out, records[index].text, capacity);
    return;
  }
}
