#include "fake_dyld.h"

#include "textkit.h"
#include "types.h"

/* Replaces the dyld image table functions the constructor uses, so a test can
   describe a host without running on a device. */

#define maximum_images 8

static const char *image_paths[maximum_images];
static u32 image_count_value = 0;

/* The table keeps its own copies of the first and the last image, because the
   test builds those paths in a buffer that later calls reuse, while dyld keeps
   the paths it resolved. */

#define fake_path_text 512

static char first_text[fake_path_text];
static char self_text[fake_path_text];
static char later_text[fake_path_text];

/* dladdr answers for the image this binary is in. The test picks the answer,
   including none at all, because the path of the test binary is not what the
   cases are about. */

static const char *dladdr_answer = 0;

static void fake_dladdr_answers(const char *path) {
  dladdr_answer = path;
}

int dladdr(const void *address, dl_info *info) {
  (void)address;
  if (dladdr_answer == 0 || info == 0) {
    return 0;
  }
  info->dli_fname = dladdr_answer;
  info->dli_fbase = (void *)0;
  info->dli_sname = 0;
  info->dli_saddr = (void *)0;
  return 1;
}

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self) {
  image_count_value = 0;
  text_copy(first_text, first, fake_path_text);
  image_paths[image_count_value++] = first_text;
  image_paths[image_count_value++] = preferences;
  if (loaded != 0) {
    image_paths[image_count_value++] = loaded;
  }
  if (self == 0) {
    image_paths[image_count_value++] = 0;
    fake_dladdr_answers(0);
    return;
  }
  text_copy(self_text, self, fake_path_text);
  image_paths[image_count_value++] = self_text;
  fake_dladdr_answers(self_text);
}

/* Adds an image after this dylib, the way the Swift runtime libraries arrive
   after ellekit finishes loading us. The last slot then holds a neighbour, so
   a lookup that reads it sees the wrong image. */

void fake_load_after_self(const char *path) {
  if (path != 0 && image_count_value < maximum_images) {
    text_copy(later_text, path, fake_path_text);
    image_paths[image_count_value++] = later_text;
  }
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
