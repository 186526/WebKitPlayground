#ifndef IDEVICE_WEBKIT_TYPES_H
#define IDEVICE_WEBKIT_TYPES_H

/* The dylib is compiled without the iOS SDK, so the few types it needs are
   declared here with the sizes the arm64e ABI gives them. */

typedef unsigned long usize;
typedef long ssize;
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long u64;
typedef int i32;
typedef long i64;

#ifndef NULL
#define NULL ((void *)0)
#endif

#ifndef true
#define true 1
#define false 0
typedef int bool;
#endif

extern void _Exit(int status);

#endif
