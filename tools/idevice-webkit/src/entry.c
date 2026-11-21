#include "idevice_webkit.h"

/* dlopen runs every __mod_init_func entry before it returns the handle, so the
   constructor reaches idevice_webkit_start in the host process as soon as
   ellekit loads this dylib. The entry is called through a plain pointer from
   that table, which is why an arm64-only build still runs in an arm64e
   process. */

__attribute__((constructor)) static void idevice_webkit_entry(void) {
  idevice_webkit_start();
}
