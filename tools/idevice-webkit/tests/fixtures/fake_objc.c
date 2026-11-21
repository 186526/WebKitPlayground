#include "fake_objc.h"

#include "bundle_lookup.h"

#include <stdarg.h>

#include "textkit.h"
#include "types.h"

/* A Foundation object here is a slot of one table, and every answer is found
   by the slot's address the way an object is found by its isa. Messages are
   dispatched on the selector name, so a dylib that hands over a C-symbol name
   where a selector belongs gets nil, exactly as the device does.

   Two questions are kept apart on purpose. “Which class names does this
   process have?” is answered by the class table, so a WebKit subprocess
   without UIApplication is a case that does not register it. “Which
   identifiers does LaunchServices know?” is answered by the registry table, so
   a system application can be in the filter and not in the registry. */

#define fake_text 512
#define fake_table fake_objc_entries

typedef struct class_slot {
  char name[fake_text];
  u32 in_use;
} class_slot;

/* The identifier is handed out as an object of its own, so a caller can send
   it another message; the bytes are the same copy, and text_of below knows
   both tables. */
typedef struct named_entry {
  char path[fake_text];
  char identifier[fake_text];
  char identifier_object[fake_text];
  u32 in_use;
} named_entry;

static class_slot class_slots[fake_table];

/* Strings the dylib builds: manifest keys, and the lowercased copy. Elements
   of the filter lists are handed out as slots of this table too, so an object
   is always a known address. */

static char string_text[fake_table * 4][fake_text];
static u32 string_count;

static named_entry bundles[fake_table];
static named_entry plists[fake_table];

/* The filter file and its two lists. */

static char filter_file[fake_text];
static bool filter_present;
static char filter_classes[fake_table][fake_text];
static u32 filter_class_count;
static char filter_bundles[fake_table][fake_text];
static u32 filter_bundle_count;

static char registered_bundles[fake_table][fake_text];
static u32 registered_bundle_count;

/* The filter dictionary and its two arrays are three fixed objects, so an
   array is told apart from a string by its address. */

static char filter_dictionary_slot[8];
static char class_array_slot[8];
static char bundle_array_slot[8];

static const char *const selector_names[] = {
    "bundleWithPath:",       "bundleIdentifier",      "dictionaryWithContentsOfFile:",
    "objectForKey:",         "stringWithUTF8String:", "UTF8String",
    "count",                 "objectAtIndex:",
};

static char selector_storage[sizeof(selector_names) / sizeof(selector_names[0])][64];

static bool ends_with(const char *text, const char *suffix) {
  usize textLength = text_length(text);
  usize suffixLength = text_length(suffix);
  return textLength >= suffixLength && text_equal(text + (textLength - suffixLength), suffix);
}

static void reset_strings(void) {
  for (u32 index = 0; index < fake_table * 4; index++) {
    string_text[index][0] = 0;
  }
  string_count = 0;
}

static void reset_named(named_entry *table) {
  for (u32 index = 0; index < fake_table; index++) {
    table[index].path[0] = 0;
    table[index].identifier[0] = 0;
    table[index].identifier_object[0] = 0;
    table[index].in_use = 0;
  }
}

/* A host process has these four classes and nothing else until a case
   registers more, because the dylib asks the runtime which classes exist. */

static void register_foundation_classes(void) {
  static const char *const names[] = {"NSBundle", "NSDictionary", "NSString", "NSArray"};
  for (u32 index = 0; index < 4; index++) {
    text_copy(class_slots[index].name, names[index], fake_text);
    class_slots[index].in_use = 1;
  }
}

void fake_objc_reset(void) {
  for (u32 index = 0; index < fake_table; index++) {
    class_slots[index].name[0] = 0;
    class_slots[index].in_use = 0;
  }
  register_foundation_classes();
  reset_strings();
  reset_named(bundles);
  reset_named(plists);
  filter_file[0] = 0;
  filter_present = false;
  filter_class_count = 0;
  filter_bundle_count = 0;
  registered_bundle_count = 0;
}

void fake_class_register(const char *class_name) {
  for (u32 index = 0; index < fake_table; index++) {
    if (!class_slots[index].in_use) {
      text_copy(class_slots[index].name, class_name, fake_text);
      class_slots[index].in_use = 1;
      return;
    }
  }
}

static void add_named(named_entry *table, const char *path, const char *identifier) {
  for (u32 index = 0; index < fake_table; index++) {
    if (!table[index].in_use) {
      text_copy(table[index].path, path, fake_text);
      text_copy(table[index].identifier, identifier, fake_text);
      text_copy(table[index].identifier_object, identifier, fake_text);
      table[index].in_use = 1;
      return;
    }
  }
}

void fake_bundle_add(const char *path, const char *identifier) {
  add_named(bundles, path, identifier);
}

void fake_plist_add(const char *path, const char *identifier) {
  add_named(plists, path, identifier);
}

void fake_filters_reset(const char *filterPath) {
  text_copy(filter_file, filterPath, fake_text);
  filter_present = false;
  filter_class_count = 0;
  filter_bundle_count = 0;
  registered_bundle_count = 0;
}

void fake_filter_add_class(const char *class_name) {
  filter_present = true;
  if (filter_class_count < fake_table) {
    text_copy(filter_classes[filter_class_count++], class_name, fake_text);
  }
}

void fake_filter_add_bundle(const char *identifier) {
  filter_present = true;
  if (filter_bundle_count < fake_table) {
    text_copy(filter_bundles[filter_bundle_count++], identifier, fake_text);
  }
}

void fake_bundle_register(const char *identifier) {
  if (registered_bundle_count < fake_table) {
    text_copy(registered_bundles[registered_bundle_count++], identifier, fake_text);
  }
}

/* objc_getClass answers only the names the class table holds. */

void *fake_objc_class_named(const char *name) {
  if (name == 0) {
    return 0;
  }
  for (u32 index = 0; index < fake_table; index++) {
    if (class_slots[index].in_use && text_equal(class_slots[index].name, name)) {
      return &class_slots[index];
    }
  }
  return 0;
}

/* sel_registerName answers only the names the runtime knows, so a misspelt
   method answers nothing rather than a selector nobody implements. */

void *fake_objc_selector_named(const char *name) {
  if (name == 0) {
    return 0;
  }
  u32 count = (u32)(sizeof(selector_names) / sizeof(selector_names[0]));
  for (u32 index = 0; index < count; index++) {
    if (text_equal(name, selector_names[index])) {
      text_copy(selector_storage[index], name, sizeof(selector_storage[index]));
      return selector_storage[index];
    }
  }
  return 0;
}

static void *string_for(const char *text) {
  if (string_count >= fake_table * 4) {
    return 0;
  }
  text_copy(string_text[string_count], text, fake_text);
  return string_text[string_count++];
}

static const char *text_of(void *object) {
  if (object == 0) {
    return 0;
  }
  for (u32 index = 0; index < string_count; index++) {
    if ((void *)string_text[index] == object) {
      return string_text[index];
    }
  }
  for (u32 index = 0; index < fake_table; index++) {
    if (bundles[index].in_use && (void *)bundles[index].identifier_object == object) {
      return bundles[index].identifier;
    }
    if (plists[index].in_use && (void *)plists[index].identifier_object == object) {
      return plists[index].identifier;
    }
  }
  return 0;
}

/* A path of the wrong shape names nothing: NSBundle reads only the Info.plist
   of a directory and NSDictionary only a plist file. */

static named_entry *bundle_for_path(const char *path) {
  if (path == 0 || !ends_with(path, ".app")) {
    return 0;
  }
  for (u32 index = 0; index < fake_table; index++) {
    if (bundles[index].in_use && text_equal(path, bundles[index].path)) {
      return &bundles[index];
    }
  }
  return 0;
}

static named_entry *plist_for_path(const char *path) {
  if (path == 0 || !ends_with(path, ".plist")) {
    return 0;
  }
  for (u32 index = 0; index < fake_table; index++) {
    if (plists[index].in_use && text_equal(path, plists[index].path)) {
      return &plists[index];
    }
  }
  return 0;
}

static void *filter_value_for_key(void *dictionary, const char *key) {
  if (dictionary != (void *)filter_dictionary_slot || key == 0) {
    return 0;
  }
  if (text_equal(key, "Classes") && filter_class_count > 0) {
    return class_array_slot;
  }
  if (text_equal(key, "Bundles") && filter_bundle_count > 0) {
    return bundle_array_slot;
  }
  return 0;
}

static u64 array_count(void *array) {
  if (array == (void *)class_array_slot) {
    return filter_class_count;
  }
  return array == (void *)bundle_array_slot ? filter_bundle_count : 0;
}

static void *array_at_index(void *array, u64 position) {
  if (array == (void *)class_array_slot && position < filter_class_count) {
    return string_for(filter_classes[position]);
  }
  if (array == (void *)bundle_array_slot && position < filter_bundle_count) {
    return string_for(filter_bundles[position]);
  }
  return 0;
}

/* CFStringCreateMutableCopy takes the allocator, the capacity and the string.
   The stub records the first two so a case can hold the call to its three
   arguments, and copies the bytes so the lowercase step below has a place to
   write. */

#define lowercase_room (fake_text * 2)

static char lowered_text[lowercase_room];
static void *lowered_allocator;
static i64 lowered_capacity;

static void *mutable_copy(void *allocator, i64 capacity, void *text) {
  lowered_allocator = allocator;
  lowered_capacity = capacity;
  const char *source = text_of(text);
  if (source == 0) {
    return 0;
  }
  usize index = 0;
  while (source[index] != 0 && index + 1 < lowercase_room) {
    lowered_text[index] = source[index];
    index++;
  }
  lowered_text[index] = 0;
  return lowered_text;
}

void *fake_filter_last_copy_allocator(void) {
  return lowered_allocator;
}

i64 fake_filter_last_copy_capacity(void) {
  return lowered_capacity;
}

static void lowercase(void *copy, void *locale) {
  (void)locale;
  if (copy != (void *)lowered_text) {
    return;
  }
  for (usize index = 0; lowered_text[index] != 0; index++) {
    char symbol = lowered_text[index];
    lowered_text[index] = symbol >= 'A' && symbol <= 'Z' ? (char)(symbol - 'A' + 'a') : symbol;
  }
}

/* A registered identifier answers by its bytes, so a case can register the
   lowercase form alone and tell the second lookup of the bundle branch from
   the first. */

static void *bundle_for_identifier(void *identifier) {
  const char *name = identifier == (void *)lowered_text ? lowered_text : text_of(identifier);
  if (name == 0) {
    return 0;
  }
  for (u32 index = 0; index < registered_bundle_count; index++) {
    if (text_equal(registered_bundles[index], name)) {
      return registered_bundles[index];
    }
  }
  return 0;
}

static int default_allocator_object;

static void *default_allocator(void) {
  return &default_allocator_object;
}

static i64 string_length(void *string) {
  const char *text = text_of(string);
  i64 length = 0;
  while (text != 0 && text[length] != 0) {
    length++;
  }
  return length;
}

static void release_object(void *object) {
  (void)object;
}

void *fake_cf_symbol(const char *name) {
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

static void *message_without_argument(void *receiver, const char *selector) {
  if (text_equal(selector, "bundleIdentifier")) {
    for (u32 index = 0; index < fake_table; index++) {
      if ((void *)&bundles[index] == receiver) {
        return bundles[index].identifier_object;
      }
    }
    return 0;
  }
  if (text_equal(selector, "UTF8String")) {
    return (void *)text_of(receiver);
  }
  if (text_equal(selector, "count")) {
    return (void *)array_count(receiver);
  }
  return 0;
}

static void *message_with_object(void *receiver, const char *selector, void *argument) {
  if (text_equal(selector, "objectForKey:")) {
    for (u32 index = 0; index < fake_table; index++) {
      if ((void *)&plists[index] == receiver) {
        const char *key = text_of(argument);
        return key != 0 && text_equal(key, "CFBundleIdentifier") ? plists[index].identifier_object
                                                                : 0;
      }
    }
    return filter_value_for_key(receiver, text_of(argument));
  }
  return 0;
}

/* +bundleWithPath: and +dictionaryWithContentsOfFile: declare an NSString *
   argument, so their argument is looked up in the object tables and a C string
   answers nil. That is the shape the device ran into: a path handed over as
   bytes makes Foundation read the first eight of them as an isa. Only
   stringWithUTF8String: takes bytes. */

static void *message_with_path(void *receiver, const char *selector, void *text) {
  (void)receiver;
  const char *argument = text_of(text);
  if (argument == 0) {
    return 0;
  }
  if (text_equal(selector, "bundleWithPath:")) {
    /* The bundle is the table slot, and bundleIdentifier reads its identifier
       from it; handing back the identifier here would leave the next message
       with no receiver to find. */
    return (void *)bundle_for_path(argument);
  }
  if (text_equal(selector, "dictionaryWithContentsOfFile:")) {
    if (filter_file[0] != 0 && text_equal(argument, filter_file)) {
      filter_present = true;
      return filter_dictionary_slot;
    }
    return plist_for_path(argument);
  }
  return 0;
}

static void *message_with_text(void *receiver, const char *selector, const char *text) {
  (void)receiver;
  if (text_equal(selector, "stringWithUTF8String:")) {
    return string_for(text);
  }
  return 0;
}

/* objc_msgSend is variadic here only because a recorder has to read whatever
   the wrapper passes; the wrapper the device runs hands over a pointer of the
   method's own type, and arm64 passes the arguments in the same registers
   either way. */

void *fake_objc_msg_send(void *receiver, void *selector, ...) {
  const char *name = (const char *)selector;
  if (name == 0) {
    return 0;
  }
  if (text_equal(name, "stringWithUTF8String:")) {
    va_list arguments;
    va_start(arguments, selector);
    const char *text = va_arg(arguments, const char *);
    va_end(arguments);
    return message_with_text(receiver, name, text);
  }
  if (text_equal(name, "bundleWithPath:") || text_equal(name, "dictionaryWithContentsOfFile:")) {
    va_list arguments;
    va_start(arguments, selector);
    void *text = va_arg(arguments, void *);
    va_end(arguments);
    return message_with_path(receiver, name, text);
  }
  if (text_equal(name, "objectForKey:")) {
    va_list arguments;
    va_start(arguments, selector);
    void *argument = va_arg(arguments, void *);
    va_end(arguments);
    return message_with_object(receiver, name, argument);
  }
  if (text_equal(name, "objectAtIndex:")) {
    va_list arguments;
    va_start(arguments, selector);
    u64 position = va_arg(arguments, u64);
    va_end(arguments);
    return array_at_index(receiver, position);
  }
  return message_without_argument(receiver, name);
}
