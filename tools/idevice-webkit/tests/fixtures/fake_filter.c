#include "fake_filter.h"

#include "bundle_lookup.h"

#include "textkit.h"
#include "types.h"

/* Everything here is a table the dylib receives as an object pointer, and each
   method answers by finding its receiver's address in that table: the same way
   an Objective-C object is found by its isa. The tables copy their text, so a
   case can hand over a stack buffer. A dictionary holds the one filter and an
   array holds either the Classes or the Bundles list, so an array is told
   apart from an element by its address. */

#define fake_table 8
#define fake_filter_text 512
#define lowercase_room (fake_filter_text * 2)

typedef struct filter_case {
  char class_names[fake_table][fake_filter_text];
  u32 class_count;
  char bundle_names[fake_table][fake_filter_text];
  u32 bundle_count;
  char dictionary[16];
  char class_array[16];
  char bundle_array[16];
  u32 keys_asked;
} filter_case;

static filter_case filter;
static char filter_file[fake_filter_text];
static bool filter_present = false;

static char string_table[fake_table * 2][fake_filter_text];
static u32 element_count = 0;

static char registered_class[fake_table][fake_filter_text];
static u32 registered_class_count = 0;


static char registered_bundle[fake_table][fake_filter_text];
static u32 registered_bundle_count = 0;

/* Written as functions rather than as initialisers, because C has no
   designated initialiser for an empty element, and this table has to start
   empty: the compiler may otherwise emit a constructor. */

static void filter_case_init(void) __attribute__((constructor));

static void filter_case_init(void) {
  text_set(&filter, 0, sizeof(filter));
  text_set(string_table, 0, sizeof(string_table));
  text_set(registered_class, 0, sizeof(registered_class));
  text_set(registered_bundle, 0, sizeof(registered_bundle));
}

/* The path is copied before the tables are cleared: the case passes the
   buffer the constructor uses, and that buffer is one of them. */

void fake_filters_reset(const char *filterPath) {
  char path[fake_filter_text];
  text_copy(path, filterPath, fake_filter_text);
  filter_present = false;
  text_set(&filter, 0, sizeof(filter));
  text_set(string_table, 0, sizeof(string_table));
  element_count = 0;
  text_set(registered_class, 0, sizeof(registered_class));
  registered_class_count = 0;
  text_set(registered_bundle, 0, sizeof(registered_bundle));
  registered_bundle_count = 0;
  text_copy(filter_file, path, fake_filter_text);
  /* An empty path names no file, so a reset with one leaves the filter
     unreadable until a case writes the filter it wants; a path given here is
     the file the dylib reads back. */
  filter_present = filter_file[0] != 0;
}

void fake_filter_add_class(const char *class_name) {

  filter_present = true;
  if (filter.class_count < fake_table) {
    text_copy(filter.class_names[filter.class_count++], class_name, fake_filter_text);
  }
}

void fake_filter_add_bundle(const char *identifier) {
  filter_present = true;
  if (filter.bundle_count < fake_table) {
    text_copy(filter.bundle_names[filter.bundle_count++], identifier, fake_filter_text);
  }
}

void fake_class_register(const char *class_name) {
  if (registered_class_count < fake_table) {
    text_copy(registered_class[registered_class_count++], class_name, fake_filter_text);
  }
}

void fake_bundle_register(const char *identifier) {
  if (registered_bundle_count < fake_table) {
    text_copy(registered_bundle[registered_bundle_count++], identifier, fake_filter_text);
  }
}

/* An object the dylib receives is a slot of the string table: the key it asks
   the dictionary with, or an element it reads out of a list. A key pays its
   slot back once the method returns, because a table with two free slots
   would hand the older one out every time, and the dylib then holds a key
   the table no longer knows. An element keeps its slot for the run. */

static void forget_string(void *object) {
  if (object == 0) {
    return;
  }
  for (u32 index = 0; index < fake_table * 2; index++) {
    if ((void *)string_table[index] == object) {
      if (element_count > index && index + 1 == element_count) {
        element_count = index;
      }
      string_table[index][0] = 0;
      return;
    }
  }
}

static void *string_for(const char *text) {
  if (element_count >= fake_table * 2) {
    return 0;
  }
  text_copy(string_table[element_count], text, fake_filter_text);
  return string_table[element_count++];
}

static const char *text_of(void *object) {
  for (u32 index = 0; index < fake_table * 2; index++) {
    if ((void *)string_table[index] == object) {
      return string_table[index];
    }
  }
  return 0;
}

static void *dictionary_with_contents_of_file(const char *path) {
  return filter_present && text_equal(filter_file, path) ? (void *)filter.dictionary : 0;
}

static void *object_for_key(void *dictionary, void *key) {
  const char *name = text_of(key);
  if (dictionary != (void *)filter.dictionary || name == 0) {
    return 0;
  }
  void *value = 0;
  if (text_equal(name, "Classes") && filter.class_count > 0) {
    value = (void *)filter.class_array;
  } else if (text_equal(name, "Bundles") && filter.bundle_count > 0) {
    value = (void *)filter.bundle_array;
  }
  forget_string(key);
  return value;
}

/* Every date, number and string the dylib builds as a key becomes one slot
   while the method it is passed to runs; it is paid back here, so the table
   never fills up with keys the dylib has already finished with. */

static void *string_with_utf8_string(const char *text) {
  return string_for(text);
}

/* The two answers are typed the way the dylib declares them, because a call
   through a pointer of another type is what the sanitizer reports. */

static void *utf8_string_text(void *object) {
  return (void *)text_of(object);
}

static i32 array_count(void *array) {
  if (array == (void *)filter.class_array) {
    return (i32)filter.class_count;
  }
  return array == (void *)filter.bundle_array ? (i32)filter.bundle_count : 0;
}

static void *object_at_index(void *array, usize position) {
  if (array == (void *)filter.class_array && position < filter.class_count) {
    return string_for(filter.class_names[position]);
  }
  if (array == (void *)filter.bundle_array && position < filter.bundle_count) {
    return string_for(filter.bundle_names[position]);
  }
  return 0;
}

/* objc_getClass is looked up in the process rather than in Foundation, so its
   stub is not part of the table below; a case registers a class by name. */

void *fake_class_named(const char *name) {
  for (u32 index = 0; index < registered_class_count; index++) {
    if (text_equal(registered_class[index], name)) {
      return registered_class[index];
    }
  }
  return 0;
}

/* The bundle branch asks for a lowercased copy of the identifier, so
   mutable_copy stands in for the copy and lowercase for the call that fills
   it. The copy remembers the element it was made from, which is what the
   call after it reads; the table it writes holds room for every element.

   The signature is CFStringCreateMutableCopy's, three arguments and all: the
   allocator, the capacity, and the string. A stub that took two arguments
   would let a two-argument call compile and run here while the device reads
   the third register as garbage, which is how the call survived review once
   already. */

static char lowered_text[lowercase_room];
static void *lowered_source = 0;
static void *lowered_allocator = 0;
static i64 lowered_capacity = 0;

static void *mutable_copy(void *allocator, i64 capacity, void *text) {
  lowered_allocator = allocator;
  lowered_capacity = capacity;
  lowered_source = text;
  return text == 0 ? 0 : (void *)lowered_text;
}

/* What the last copy was made with, so a case can hold the call to the three
   arguments CFStringCreateMutableCopy declares. */

void *fake_filter_last_copy_allocator(void) {
  return lowered_allocator;
}

i64 fake_filter_last_copy_capacity(void) {
  return lowered_capacity;
}

static void lowercase(void *copy, void *locale) {
  (void)locale;
  (void)copy;
  const char *source = text_of(lowered_source);
  usize index = 0;
  while (source != 0 && source[index] != 0 && index + 1 < lowercase_room) {
    char symbol = source[index];
    lowered_text[index++] = symbol >= 'A' && symbol <= 'Z' ? (char)(symbol - 'A' + 'a') : symbol;
  }
  lowered_text[index] = 0;
}

/* A registered identifier answers by its bytes, so a case can register the
   lowercase form of an identifier and nothing else. */

static void *bundle_for_identifier(void *identifier) {
  const char *name = text_of(identifier);
  if (identifier == (void *)lowered_text) {
    name = lowered_text;
  }
  if (name == 0) {
    return 0;
  }
  for (u32 index = 0; index < registered_bundle_count; index++) {
    if (text_equal(registered_bundle[index], name)) {
      return registered_bundle[index];
    }
  }
  return 0;
}

static void release_object(void *object) {
  (void)object;
}

/* A value that is neither null nor a function pointer, so a case can prove the
   allocator reached the copy as the object CFAllocatorGetDefault returns and
   not as the function itself. */

static int default_allocator_object;

static void *default_allocator(void) {
  return (void *)&default_allocator_object;
}

/* CFStringGetLength answers the number of UTF-16 units; the stub strings are
   ASCII, so the byte length is the same number. */

static i64 string_length(void *string) {
  const char *text = text_of(string);
  i64 length = 0;
  while (text != 0 && text[length] != 0) {
    length++;
  }
  return length;
}

void *fake_foundation_symbol(const char *name) {
  if (text_equal(name, "NSDictionary.dictionaryWithContentsOfFile:")) {
    return (void *)dictionary_with_contents_of_file;
  }
  if (text_equal(name, "NSDictionary.objectForKey:")) {
    return (void *)object_for_key;
  }
  if (text_equal(name, "NSString.stringWithUTF8String:")) {
    return (void *)string_with_utf8_string;
  }
  if (text_equal(name, "NSString.UTF8String")) {
    return (void *)utf8_string_text;
  }
  if (text_equal(name, "NSArray.count")) {
    return (void *)array_count;
  }
  if (text_equal(name, "NSArray.objectAtIndex:")) {
    return (void *)object_at_index;
  }
  if (text_equal(name, "CFBundleGetBundleWithIdentifier")) {
    return (void *)bundle_for_identifier;
  }
  if (text_equal(name, "CFStringCreateMutableCopy")) {
    return (void *)mutable_copy;
  }
  if (text_equal(name, "CFStringLowercase")) {
    return (void *)lowercase;
  }
  if (text_equal(name, "CFRelease")) {
    return (void *)release_object;
  }
  if (text_equal(name, "CFAllocatorGetDefault")) {
    return (void *)default_allocator;
  }
  if (text_equal(name, "CFStringGetLength")) {
    return (void *)string_length;
  }
  return 0;
}
