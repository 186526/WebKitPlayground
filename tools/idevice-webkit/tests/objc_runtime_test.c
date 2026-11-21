/* Checks objc_runtime.c against the behaviour the device showed.

   No Objective-C method name is a C symbol, so dlsym answers NULL for every
   one of them: a probe in Safari read dlsym("NSBundle.bundleIdentifier") as
   NULL while objc_getClass, sel_registerName and objc_msgSend all resolved.
   The dylib used to look the method names up that way, so every lookup failed
   and no process was ever identified as a host.

   The three runtime symbols are therefore recorded here the way the device
   answers them, and the checks below hold two things apart: the method-name
   lookup still fails, and each typed wrapper reaches the same objc_msgSend
   address with the arguments its method takes.

   objc_msgSend is variadic in this recorder only because a recorder has to
   read whatever the wrapper passes; the wrapper is what the device runs, and
   it hands over a pointer of the method's own type. */

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "../src/types.h"

static const char *method_names[] = {
    "bundleIdentifier",      "bundleWithPath:",   "dictionaryWithContentsOfFile:",
    "stringWithUTF8String:", "objectForKey:",     "UTF8String",
    "count",                 "objectAtIndex:",
};

static int is_method_name(const char *name) {
  for (unsigned index = 0; index < sizeof(method_names) / sizeof(method_names[0]); index++) {
    if (strcmp(name, method_names[index]) == 0) {
      return 1;
    }
  }
  return 0;
}

/* The class object and the selectors are opaque here: what matters is that
   the wrapper passes the ones it was handed. */

static int class_object;
static char selector_text[64];

static void *test_objc_getClass(const char *name) {
  return strcmp(name, "NSBundle") == 0 || strcmp(name, "NSDictionary") == 0 ||
                 strcmp(name, "NSString") == 0 || strcmp(name, "NSArray") == 0
             ? (void *)&class_object
             : NULL;
}

static void *test_sel_registerName(const char *name) {
  if (!is_method_name(name)) {
    return NULL;
  }
  for (unsigned index = 0; index < sizeof(selector_text); index++) {
    selector_text[index] = 0;
  }
  strncpy(selector_text, name, sizeof(selector_text) - 1);
  return selector_text;
}

static int message_calls;
static void *last_receiver;
static const char *last_selector;
static int last_argument_count;
static const char *last_text_argument;
static void *last_object_argument;
static u64 last_index_argument;

/* Answers each method as Foundation would: an object for everything except
   UTF8String, which answers a C string, and count, which answers an
   NSUInteger. count deliberately answers more than 32 bits' worth, so a
   wrapper that read it into an i32 would show up here. */

static void *test_objc_msgSend(void *receiver, void *operation, ...) {
  va_list arguments;
  va_start(arguments, operation);
  message_calls++;
  last_receiver = receiver;
  last_selector = (const char *)operation;
  last_argument_count = 0;
  last_text_argument = NULL;
  last_object_argument = NULL;
  last_index_argument = 0;

  void *answer = (void *)0x2000;
  if (operation == NULL) {
    va_end(arguments);
    return NULL;
  }
  if (strcmp((const char *)operation, "bundleWithPath:") == 0 ||
      strcmp((const char *)operation, "dictionaryWithContentsOfFile:") == 0 ||
      strcmp((const char *)operation, "stringWithUTF8String:") == 0) {
    last_argument_count = 1;
    last_text_argument = va_arg(arguments, const char *);
  } else if (strcmp((const char *)operation, "objectForKey:") == 0) {
    last_argument_count = 1;
    last_object_argument = va_arg(arguments, void *);
  } else if (strcmp((const char *)operation, "objectAtIndex:") == 0) {
    last_argument_count = 1;
    last_index_argument = va_arg(arguments, u64);
  } else if (strcmp((const char *)operation, "UTF8String") == 0) {
    answer = (void *)"com.apple.mobilesafari";
  } else if (strcmp((const char *)operation, "count") == 0) {
    answer = (void *)(usize)0x100000001u;
  }
  va_end(arguments);
  return answer;
}

/* dyld_lookup.c declares the three libdyld functions itself, so renaming
   dlsym before including it turns both its declaration and its calls into the
   recorder above; that is how the device's answer for a method name is
   reproduced. objc_runtime.c then reaches the recorder through
   symbol_find_global, exactly as it does on the device. */

static const char *last_dlsym_name;

static void *test_dlsym(void *library, const char *name) {
  (void)library;
  last_dlsym_name = name;
  if (strcmp(name, "objc_getClass") == 0) {
    return (void *)test_objc_getClass;
  }
  if (strcmp(name, "sel_registerName") == 0) {
    return (void *)test_sel_registerName;
  }
  if (strcmp(name, "objc_msgSend") == 0) {
    return (void *)test_objc_msgSend;
  }
  return NULL;
}

#define dlsym test_dlsym
#include "../src/dyld_lookup.c"
#undef dlsym
#include "../src/objc_runtime.c"

static int failures;

static void check(const char *what, int ok) {
  printf("%-58s %s\n", what, ok ? "pass" : "FAIL");
  if (!ok) {
    failures++;
  }
}

static void reset(void) {
  message_calls = 0;
  last_receiver = NULL;
  last_selector = NULL;
  last_argument_count = 0;
  last_text_argument = NULL;
  last_object_argument = NULL;
  last_index_argument = 0;
}

int main(void) {
  /* The reason this file exists: a method name is not a C symbol. */

  last_dlsym_name = NULL;
  check("a method name resolves to nothing through dlsym",
        symbol_find_global("NSBundle.bundleIdentifier") == NULL);
  check("the failed lookup was made with the method name itself",
        last_dlsym_name != NULL && strcmp(last_dlsym_name, "NSBundle.bundleIdentifier") == 0);

  check("objc_getClass resolves in the process's own symbol table",
        symbol_find_global("objc_getClass") != NULL);
  check("sel_registerName resolves in the process's own symbol table",
        symbol_find_global("sel_registerName") != NULL);
  check("objc_msgSend resolves in the process's own symbol table",
        symbol_find_global("objc_msgSend") != NULL);

  /* The class and selector lookups go through the resolved runtime. */

  check("a class name answers a class object", objc_class("NSBundle") != NULL);
  check("a class the runtime does not know answers nothing", objc_class("NSNotAClass") == NULL);
  check("a method name answers a selector",
        objc_selector("bundleIdentifier") != NULL &&
            strcmp((const char *)objc_selector("bundleIdentifier"), "bundleIdentifier") == 0);
  check("a selector the runtime does not register answers nothing",
        objc_selector("notAMethod") == NULL);

  /* Each wrapper calls the one objc_msgSend address with its own method's
     arguments, which is the whole point of the typed wrappers. */

  reset();
  void *classAnswer = objc_send_object((void *)&class_object, objc_selector("bundleIdentifier"));
  check("a no-argument message passes the receiver and the selector",
        classAnswer != NULL && last_receiver == (void *)&class_object &&
            last_argument_count == 0);
  check("  the selector handed over is the one asked for",
        last_selector != NULL && strcmp(last_selector, "bundleIdentifier") == 0);

  reset();
  objc_send_object_text((void *)&class_object, objc_selector("bundleWithPath:"), "/Applications/X.app");
  check("a text argument reaches the message as the string given",
        last_argument_count == 1 && last_text_argument != NULL &&
            strcmp(last_text_argument, "/Applications/X.app") == 0);

  reset();
  objc_send_object_object((void *)&class_object, objc_selector("objectForKey:"), (void *)0x3000);
  check("an object argument reaches the message as the object given",
        last_argument_count == 1 && last_object_argument == (void *)0x3000);

  reset();
  const char *text = objc_send_text((void *)&class_object, objc_selector("UTF8String"));
  check("a message answering text returns the string the method gave",
        text != NULL && strcmp(text, "com.apple.mobilesafari") == 0);

  /* NSUInteger is 64 bits on arm64. A wrapper declared with a 32-bit return
     would drop the upper half here. */

  reset();
  u64 count = objc_send_count((void *)&class_object, objc_selector("count"));
  check("a message answering NSUInteger keeps all 64 bits",
        count == (u64)0x100000001u);

  reset();
  objc_send_object_index((void *)&class_object, objc_selector("objectAtIndex:"), (u64)0x100000002u);
  check("an index argument reaches the message as the 64-bit value given",
        last_argument_count == 1 && last_index_argument == (u64)0x100000002u);

  /* A nil receiver or selector answers nil without calling through the
     pointer, which is what a process without libobjc looks like. */

  reset();
  check("a nil receiver answers nil without sending a message",
        objc_send_object(NULL, objc_selector("bundleIdentifier")) == NULL && message_calls == 0);
  check("a nil selector answers nil without sending a message",
        objc_send_object((void *)&class_object, NULL) == NULL && message_calls == 0);

  if (failures != 0) {
    printf("\n%d runtime check(s) failed\n", failures);
    return 1;
  }
  printf("\nruntime: every check passed\n");
  return 0;
}
