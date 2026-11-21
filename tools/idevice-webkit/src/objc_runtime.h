#ifndef IDEVICE_WEBKIT_OBJC_RUNTIME_H
#define IDEVICE_WEBKIT_OBJC_RUNTIME_H

#include "types.h"

/* Sends Objective-C messages, which is the only way to read a bundle
   identifier: "NSBundle.bundleIdentifier" is a method name, not a C symbol, so
   dlsym answers NULL for it and the host could never be identified.

   Three C symbols of the runtime make a message: objc_getClass turns a class
   name into the class object, sel_registerName turns a method name into a
   selector, and objc_msgSend delivers the call. All three are looked up in the
   process's own symbol table.

   objc_msgSend has no single signature: it forwards whatever the method takes,
   so each call site has to hand it a pointer of that method's own type. On
   arm64 the arguments travel in the same registers either way, so the cast is
   exact rather than a workaround, and a variadic declaration is the one thing
   that must not be used: it would put the arguments somewhere the callee does
   not read. The typed wrappers below are the only places that cast. */

void *objc_class(const char *name);
void *objc_selector(const char *name);

/* nil when the runtime is not reachable, which is what a process without
   libobjc looks like; every wrapper answers NULL or 0 in that case. */

void *objc_send_object(void *receiver, void *selector);

/* [receiver selector:text] with the argument as a C string. Only
   stringWithUTF8String: takes its text this way; every other Foundation method
   that names a path takes an NSString object and needs
   objc_send_object_object, which is the mistake the device found: a C string
   there is read as an object and its first bytes as an isa. */

void *objc_send_object_text(void *receiver, void *selector, const char *text);

/* [receiver selector:object] with the argument already an object, the form
   objectForKey: and bundleWithPath: and dictionaryWithContentsOfFile: take. */

void *objc_send_object_object(void *receiver, void *selector, void *argument);

/* [receiver selector] answering a C string, the form UTF8String takes. */

const char *objc_send_text(void *receiver, void *selector);

/* [receiver selector] answering NSUInteger, the form count takes. */

u64 objc_send_count(void *receiver, void *selector);

/* [receiver selector:index] with an NSUInteger argument, the form
   objectAtIndex: takes. */

void *objc_send_object_index(void *receiver, void *selector, u64 index);

#endif
