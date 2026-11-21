#include "fake_dyld.h"

#include "types.h"

/* Replaces the dyld image table functions the constructor uses, so a test can
   describe a host without running on a device. */

#define maximum_images 8

static const char *image_paths[maximum_images];
static u32 image_count_value = 0;

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self) {
  image_count_value = 0;
  image_paths[image_count_value++] = first;
  image_paths[image_count_value++] = preferences;
  if (loaded != 0) {
    image_paths[image_count_value++] = loaded;
  }
  image_paths[image_count_value++] = self;
}

u32 _dyld_image_count(void) {
  return image_count_value;
}

const char *_dyld_get_image_name(u32 imageIndex) {
  return imageIndex < image_count_value ? image_paths[imageIndex] : 0;
}

const void *_dyld_get_image_header(u32 imageIndex) {
  (void)imageIndex;
  return 0;
}
