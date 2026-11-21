#ifndef IDEVICE_WEBKIT_FAKE_DYLD_H
#define IDEVICE_WEBKIT_FAKE_DYLD_H

/* Fills the answers the constructor reads from the process: the executable
   path, the handle of the framework, and what dladdr says about an address.
   `self` is the path dladdr reports for this dylib; a NULL `self` leaves an
   image dladdr cannot name, which is what the constructor has to answer for
   without reading any path. `loaded` is the framework path, or NULL when the
   process has not loaded it. */

void fake_reset(const char *executable, const char *preferences, const char *loaded,
                const char *self);

/* The handle library_find answers for the framework, and the address a class
   it defines resolves to. Both are NULL until fake_reset is told the
   framework is loaded. */

void *fake_framework_handle(void);
const void *fake_framework_symbol(const char *name);

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
