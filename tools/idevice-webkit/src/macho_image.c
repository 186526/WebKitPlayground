#include "macho_image.h"

#include "textkit.h"
#include "types.h"

#define mach_header_magic 0xfeedfacfu
/* LC_SEGMENT_64, not LC_SEGMENT: every image this reads is 64-bit, and the
   32-bit command number matches none of their load commands. */
#define segment_command_64 0x19u
#define id_dylib_command 0xdu
#define uuid_command 0x1bu

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

/* The section record of a 64-bit image is 80 bytes: two 16-byte names, two
   u64 fields, and six u32 fields after the file offset. Declaring it short
   makes the scan of a segment step by the wrong amount and read a name that
   is not there, which is how the __cfstring section stopped being found. */

typedef struct section64 {
  char section_name[16];
  char segment_name[16];
  u64 address;
  u64 size;
  u32 offset;
  u32 alignment;
  u32 relocation_offset;
  u32 relocation_count;
  u32 flags;
  u32 reserved1;
  u32 reserved2;
  u32 reserved3;
} section64;

typedef struct dylib_command {
  u32 command;
  u32 size;
  u32 name_offset;
  u32 timestamp;
  u32 current_version;
  u32 compatibility_version;
} dylib_command;

/* A CFString constant is 32 bytes: the class pointer, the flags word, the
   pointer to the characters, and the length. The length is not read here, but
   it is part of the record and leaving it out makes the scan step 24 bytes and
   read the next record's flags word as text. */

typedef struct cf_string {
  void *isa;
  u64 flags;
  char *text;
  u64 length;
} cf_string;

bool image_is_mach_header(const void *header) {
  if (header == NULL) {
    return false;
  }
  return ((const macho_header *)header)->magic == mach_header_magic;
}

/* The header of a loaded image sits at the base dyld mapped it to, while the
   section records name virtual addresses from the image's own base. Turning a
   section address into a pointer therefore means subtracting the virtual
   address of the segment the header starts, not using the address as one: the
   address is a small number such as 0x8188 and reading it would fault. */

static bool text_vmaddr(const void *header, u64 *vmaddr) {
  const macho_header *mach = (const macho_header *)header;
  const u8 *cursor = (const u8 *)header + sizeof(macho_header);
  for (u32 index = 0; index < mach->command_count; index++) {
    const load_command *command = (const load_command *)cursor;
    if (command->size == 0) {
      return false;
    }
    if (command->command == segment_command_64) {
      const segment_command64 *segmentHeader = (const segment_command64 *)command;
      if (text_equal(segmentHeader->segment_name, "__TEXT")) {
        *vmaddr = segmentHeader->address;
        return true;
      }
    }
    cursor += command->size;
  }
  return false;
}

const char *image_segment(const void *header, const char *segment, const char *section, u64 *size,
                          u64 *offset) {
  if (!image_is_mach_header(header)) {
    return NULL;
  }
  u64 textAddress = 0;
  if (!text_vmaddr(header, &textAddress)) {
    return NULL;
  }
  const macho_header *mach = (const macho_header *)header;
  const u8 *cursor = (const u8 *)header + sizeof(macho_header);
  for (u32 index = 0; index < mach->command_count; index++) {
    const load_command *command = (const load_command *)cursor;
    if (command->size == 0) {
      return NULL;
    }
    if (command->command == segment_command_64) {
      const segment_command64 *segmentHeader = (const segment_command64 *)command;
      if (text_equal(segmentHeader->segment_name, segment)) {
        const section64 *sections = (const section64 *)(cursor + sizeof(segment_command64));
        for (u32 sectionIndex = 0; sectionIndex < segmentHeader->section_count; sectionIndex++) {
          if (text_equal(sections[sectionIndex].section_name, section)) {
            *size = sections[sectionIndex].size;
            *offset = sections[sectionIndex].offset;
            return (const char *)header + (sections[sectionIndex].address - textAddress);
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

/* A __cfstring section is not always in __DATA: an image built for iOS 16 keeps
   it in __DATA_CONST, and one linked for an older deployment target keeps it in
   __DATA. Both are read, and the segments are tried in order, because the
   version of a framework is in whichever one its own build produced. */

static const char *cfstring_section(const void *header, u64 *size) {
  static const char *const segments[] = {"__DATA_CONST", "__DATA"};
  for (usize index = 0; index < sizeof(segments) / sizeof(segments[0]); index++) {
    u64 offset = 0;
    const char *base = image_segment(header, segments[index], "__cfstring", size, &offset);
    if (base != NULL && *size != 0) {
      return base;
    }
  }
  return NULL;
}

void macho_version_text(const void *header, char *out, usize capacity) {
  out[0] = 0;
  u64 size = 0;
  const char *base = cfstring_section(header, &size);
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

/* LC_UUID carries a 16-byte value at offset 8 of its command. It is printed the
   way Apple prints it, 8-4-4-4-12 in upper case, because that is the form the
   dyld error text uses and the report has to be comparable with a device message
   without a conversion step: the recorded failure names
   787DFAB1-4DE3-3568-9923-C9409791B728 for the 18.3 WebKit and
   D309AA36-B494-3228-BE7D-44E5674ED663 for the 16.1 JavaScriptCore. */

static const char hex_digits[] = "0123456789ABCDEF";

void image_uuid_text(const void *header, char *out, usize capacity) {
  out[0] = 0;
  if (!image_is_mach_header(header)) {
    return;
  }
  const macho_header *mach = (const macho_header *)header;
  const u8 *cursor = (const u8 *)header + sizeof(macho_header);
  for (u32 index = 0; index < mach->command_count; index++) {
    const load_command *command = (const load_command *)cursor;
    if (command->size == 0) {
      return;
    }
    if (command->command == uuid_command && command->size >= 24) {
      const u8 *bytes = cursor + 8;
      usize written = 0;
      for (usize byte = 0; byte < 16 && written + 2 < capacity; byte++) {
        if (byte == 4 || byte == 6 || byte == 8 || byte == 10) {
          if (written + 1 < capacity) {
            out[written++] = '-';
          }
        }
        out[written++] = hex_digits[bytes[byte] >> 4];
        out[written++] = hex_digits[bytes[byte] & 0x0f];
      }
      out[written] = 0;
      return;
    }
    cursor += command->size;
  }
}
