/* Reads a synthetic image laid out the way dyld maps a real one, so the things
   a loaded image decides are checked without a device: which load command a
   segment is, how wide a section record is, and that a section's address is a
   virtual address that has to be turned into a pointer rather than used as
   one.

   Every real image this code meets is 64-bit, so its segments are
   LC_SEGMENT_64 (0x19) and its section records are 80 bytes. Reading the
   32-bit command number or a short section record makes the scan step over the
   section it is looking for, which is how the version text came back empty. */

#include <stdio.h>
#include <string.h>

#include "../src/macho_image.h"
#include "../src/types.h"

#define image_bytes 0x4000
#define text_vmaddr 0x100000000ull
#define text_section 0x100001000ull
#define const_section 0x100002000ull

static unsigned char image[image_bytes];

static void put32(usize at, u32 value) {
  memcpy(image + at, &value, 4);
}

static void put64(usize at, u64 value) {
  memcpy(image + at, &value, 8);
}

static void put_name(usize at, const char *name) {
  usize length = strlen(name);
  memcpy(image + at, name, length);
}

/* Writes one LC_SEGMENT_64 with a single section, and answers where the next
   load command goes. The section's address is a virtual address; its content
   lives at the same offset from the start of the image, which is where dyld
   would have mapped it. */

static usize put_segment(usize at, const char *segment, const char *section, u64 address,
                         u64 size) {
  put32(at, 0x19);
  put32(at + 4, 72 + 160);
  put_name(at + 8, segment);
  put64(at + 24, text_vmaddr);
  put64(at + 32, image_bytes);
  put64(at + 40, 0);
  put64(at + 48, image_bytes);
  put32(at + 64, 2);
  usize record = at + 72;
  /* A section before __cfstring, as a real segment has: with only one section
     a wrong record width still lands on the name by luck. */
  put_name(record, "__first");
  put_name(record + 16, segment);
  put64(record + 32, address);
  put64(record + 40, size);
  put32(record + 48, address - text_vmaddr);
  usize second = record + 80;
  put_name(second, section);
  put_name(second + 16, segment);
  put64(second + 32, address + 0x20);
  put64(second + 40, size);
  put32(second + 48, address + 0x20 - text_vmaddr);
  return at + 72 + 160;
}

/* The records as dyld leaves them in a loaded image: the class pointer and the
   text pointer are real addresses, already fixed up, which is the state the
   constructor reads. The section's own address stays a virtual address,
   because that is what the section record holds. */

static void cf_record(usize at, usize text_offset, u64 length) {
  put64(at, 0x7ff8000000000000ull);
  put64(at + 8, 0x7c8);
  put64(at + 16, (u64)(usize)(image + text_offset));
  put64(at + 24, length);
}

/* A __cfstring section in __DATA_CONST, which is where an image built for the
   iOS 16 deployment target keeps it, and the version behind the key. */

static void build_const_image(void) {
  memset(image, 0, sizeof(image));
  put32(0, 0xfeedfacf);
  put32(4, 0x0100000c);
  put32(12, 6);
  put32(16, 2);
  usize after = put_segment(32, "__TEXT", "__text", text_vmaddr, 0x1000);
  put_segment(after, "__DATA_CONST", "__cfstring", const_section, 64);
  cf_record(const_section - text_vmaddr + 0x20, 0x1100, 15);
  cf_record(const_section - text_vmaddr + 0x20 + 32, 0x1110, 7);
  memcpy(image + 0x1000 + 0x100, "CFBundleVersion", 16);
  memcpy(image + 0x1000 + 0x110, "620.2.4", 8);
}

/* The same section in __DATA, which is where an image linked for an older
   deployment target keeps it. */

static void build_data_image(void) {
  memset(image, 0, sizeof(image));
  put32(0, 0xfeedfacf);
  put32(4, 0x0100000c);
  put32(12, 6);
  put32(16, 2);
  usize after = put_segment(32, "__TEXT", "__text", text_vmaddr, 0x1000);
  put_segment(after, "__DATA", "__cfstring", const_section, 64);
  cf_record(const_section - text_vmaddr + 0x20, 0x1100, 15);
  cf_record(const_section - text_vmaddr + 0x20 + 32, 0x1110, 7);
  memcpy(image + 0x1000 + 0x100, "CFBundleVersion", 16);
  memcpy(image + 0x1000 + 0x110, "620.2.4", 8);
}

/* An image whose __cfstring section holds no version key, as the WebKit build
   this dylib loads does: its version lives in Info.plist, not in the text
   records of the binary. */

static void build_image_without_version(void) {
  memset(image, 0, sizeof(image));
  put32(0, 0xfeedfacf);
  put32(4, 0x0100000c);
  put32(12, 6);
  put32(16, 2);
  usize after = put_segment(32, "__TEXT", "__text", text_vmaddr, 0x1000);
  put_segment(after, "__DATA_CONST", "__cfstring", const_section, 32);
  cf_record(const_section - text_vmaddr + 0x20, 0x1100, 9);
  memcpy(image + 0x1000 + 0x100, "something", 10);
}

static int failures;

static void check(const char *what, int ok) {
  printf("%-58s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

int main(void) {
  u64 size = 0;
  u64 offset = 0;
  char version[128];

  build_const_image();
  const char *found = image_segment(image, "__DATA_CONST", "__cfstring", &size, &offset);
  check("a 64-bit segment command is recognised", found != NULL);
  check("the section record is read at its real width", size == 64);
  check("the second section address becomes a pointer into the image",
        found == (const char *)image + (const_section - text_vmaddr) + 0x20);
  macho_version_text(image, version, sizeof(version));
  check("the version is read from __DATA_CONST", strcmp(version, "620.2.4") == 0);

  build_data_image();
  macho_version_text(image, version, sizeof(version));
  check("the version is read from __DATA as well", strcmp(version, "620.2.4") == 0);

  build_image_without_version();
  macho_version_text(image, version, sizeof(version));
  check("an image without the key answers an empty version", version[0] == 0);

  memset(image, 0, sizeof(image));
  macho_version_text(image, version, sizeof(version));
  check("an image that is not a Mach-O answers an empty version", version[0] == 0);

  printf("failures: %d\n", failures);
  return failures == 0 ? 0 : 1;
}
