#include "objc_runtime.h"

#include "dyld_lookup.h"
#include "types.h"

/* The three runtime entry points, looked up once and kept. libobjc is loaded
   in every process that runs a UIKit application, and its symbols are
   reachable from the process's own symbol table rather than from Foundation,
   which the host loads separately. A name that does not resolve leaves the
   slot NULL, and every wrapper below then answers nil instead of calling
   through it. */

typedef void *(*class_resolver)(const char *);
typedef void *(*selector_register)(const char *);

typedef struct runtime {
  class_resolver class_of;
  selector_register selector_named;
  void *message_send;
  bool looked_up;
} runtime;

static runtime run_time;

/* objc_msgSend's type is the method's own, so each caller casts this address
   to the type it is about to call. It is kept as `void *` because a function
   pointer cannot be converted to another function pointer type by a cast the
   standard allows; storing the address first and casting per call is the form
   the compiler accepts without a warning, and the ABI is the same. */

static void resolve(void) {
  if (run_time.looked_up) {
    return;
  }
  run_time.looked_up = true;
  run_time.class_of = (class_resolver)symbol_find_global("objc_getClass");
  run_time.selector_named = (selector_register)symbol_find_global("sel_registerName");
  run_time.message_send = symbol_find_global("objc_msgSend");
}

void *objc_class(const char *name) {
  resolve();
  return name == NULL || run_time.class_of == NULL ? NULL : run_time.class_of(name);
}

void *objc_selector(const char *name) {
  resolve();
  return name == NULL || run_time.selector_named == NULL ? NULL : run_time.selector_named(name);
}

/* [receiver selector] answering an object. */

void *objc_send_object(void *receiver, void *selector) {
  if (receiver == NULL || selector == NULL || run_time.message_send == NULL) {
    return NULL;
  }
  void *(*message)(void *, void *) = (void *(*)(void *, void *))run_time.message_send;
  return message(receiver, selector);
}

/* [receiver selector:text] with a C string argument. */

void *objc_send_object_text(void *receiver, void *selector, const char *text) {
  if (receiver == NULL || selector == NULL || text == NULL || run_time.message_send == NULL) {
    return NULL;
  }
  void *(*message)(void *, void *, const char *) =
      (void *(*)(void *, void *, const char *))run_time.message_send;
  return message(receiver, selector, text);
}

/* [receiver selector:object] with an object argument. */

void *objc_send_object_object(void *receiver, void *selector, void *argument) {
  if (receiver == NULL || selector == NULL || run_time.message_send == NULL) {
    return NULL;
  }
  void *(*message)(void *, void *, void *) = (void *(*)(void *, void *, void *))run_time.message_send;
  return message(receiver, selector, argument);
}

/* [receiver selector] answering a C string. */

const char *objc_send_text(void *receiver, void *selector) {
  if (receiver == NULL || selector == NULL || run_time.message_send == NULL) {
    return NULL;
  }
  const char *(*message)(void *, void *) = (const char *(*)(void *, void *))run_time.message_send;
  return message(receiver, selector);
}

/* [receiver selector] answering NSUInteger. */

u64 objc_send_count(void *receiver, void *selector) {
  if (receiver == NULL || selector == NULL || run_time.message_send == NULL) {
    return 0;
  }
  u64 (*message)(void *, void *) = (u64 (*)(void *, void *))run_time.message_send;
  return message(receiver, selector);
}

/* [receiver selector:index] with an NSUInteger argument. */

void *objc_send_object_index(void *receiver, void *selector, u64 index) {
  if (receiver == NULL || selector == NULL || run_time.message_send == NULL) {
    return NULL;
  }
  void *(*message)(void *, void *, u64) = (void *(*)(void *, void *, u64))run_time.message_send;
  return message(receiver, selector, index);
}
