#ifndef IDEVICE_WEBKIT_MACHO_IMAGE_H
#define IDEVICE_WEBKIT_MACHO_IMAGE_H

#include "types.h"

/* Reads what a loaded Mach-O image says about itself: the segment table of
   its headers and the version string its __cfstring section carries. */

typedef struct macho_header {
  u32 magic;
  u32 cpu_type;
  u32 cpu_subtype;
  u32 file_type;
  u32 command_count;
  u32 command_size;
  u32 flags;
  u32 reserved;
} macho_header;

bool image_is_mach_header(const void *header);
const char *image_segment(const void *header, const char *segment, const char *section,
                          u64 *size, u64 *offset);
/* The install name recorded in LC_ID_DYLIB, which is the /System path the
   library was built against even when it runs from /var/jb. */
const char *image_install_name(const void *header, char *out, usize capacity);
/* The LC_UUID of a loaded image as 8-4-4-4-12 upper-case hex, or an empty
   string when the image carries none. Read from the mapped header, so it
   answers which build is resident rather than which build is on disk: two
   images of the same name differ here and nowhere else in a path string. */
void image_uuid_text(const void *header, char *out, usize capacity);
/* Scans __DATA.__cfstring for the CFBundleVersion key and copies the text of
   the value that follows it. */
void macho_version_text(const void *header, char *out, usize capacity);

#endif
