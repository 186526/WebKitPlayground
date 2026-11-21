#ifndef IDEVICE_WEBKIT_FAKE_DYLD_H
#define IDEVICE_WEBKIT_FAKE_DYLD_H

/* Fills the image table the constructor walks. `self` is the path of this
   dylib and stays the last image, as dyld reports it; a NULL `self` leaves an
   image dyld cannot name, which is what the constructor has to answer for
   without reading any path. The same path, or nothing, is what dladdr answers
   for the address of the constructor in this binary. */

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self);

/* Adds an image behind the ones fake_reset filled, so the last slot holds a
   neighbour rather than this dylib. */

void fake_load_after_self(const char *path);

/* The record dladdr fills in, as Darwin declares it. Guarded so the source file
   can carry the same definition when the test build compiles both together. */

#ifndef IDEVICE_WEBKIT_DL_INFO
#define IDEVICE_WEBKIT_DL_INFO
typedef struct dl_info {
  const char *dli_fname;
  void *dli_fbase;
  const char *dli_sname;
  void *dli_saddr;
} dl_info;
#endif

/* Makes every write the dylib attempts fail, as a device whose report file
   cannot be created does. */

void fake_refuse_writes(int refuse);

#endif
