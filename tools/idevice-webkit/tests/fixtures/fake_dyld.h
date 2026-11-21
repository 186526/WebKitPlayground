#ifndef IDEVICE_WEBKIT_FAKE_DYLD_H
#define IDEVICE_WEBKIT_FAKE_DYLD_H

/* Fills the answers the constructor reads from the process: the executable
   path, whether the framework is on disk, and what dladdr says about an
   address. `framework` is the path of the framework file, or NULL when it is
   not installed; the image counts as loaded only after fake_framework_load has
   been answered, which is when the constructor's dlopen runs. `self` is the
   path dladdr reports for this dylib; a NULL `self` leaves an image dladdr
   cannot name, which is what the constructor has to answer for without reading
   any path. */

void fake_reset(const char *executable, const char *framework, const char *self);

/* The handle RTLD_NOLOAD answers: the framework only while it is loaded. */

void *fake_framework_handle(void);

/* The handle the load answers, which is also the moment the image appears. */

void *fake_framework_load(void);

/* The address a class the framework defines resolves to, NULL while the image
   is not loaded. */

const void *fake_framework_symbol(const char *name);

/* The path dladdr reports for the loaded framework: the framework path with
   /private in front, the way dyld names the /var/jb image on the device.

   The record dladdr fills in, as Darwin declares it. Guarded so the source file
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
