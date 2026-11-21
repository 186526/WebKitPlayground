#include "textkit.h"

extern void *memset(void *memory, int value, usize length);
extern usize strlen(const char *text);

void text_set(void *memory, u8 value, usize length) {
  memset(memory, (int)value, length);
}

usize text_length(const char *text) {
  return text == NULL ? 0 : strlen(text);
}

void text_copy(char *out, const char *text, usize capacity) {
  if (out == NULL || capacity == 0) {
    return;
  }
  usize written = 0;
  while (text != NULL && text[written] != 0 && written + 1 < capacity) {
    out[written] = text[written];
    written++;
  }
  out[written] = 0;
}

bool text_equal(const char *left, const char *right) {
  if (left == NULL || right == NULL) {
    return false;
  }
  usize index = 0;
  while (left[index] != 0 && left[index] == right[index]) {
    index++;
  }
  return left[index] == right[index];
}

bool text_begins_with(const char *text, const char *prefix) {
  if (text == NULL || prefix == NULL) {
    return false;
  }
  usize index = 0;
  while (prefix[index] != 0) {
    if (text[index] != prefix[index]) {
      return false;
    }
    index++;
  }
  return true;
}
