#ifndef IDEVICE_WEBKIT_FAKE_DYLD_H
#define IDEVICE_WEBKIT_FAKE_DYLD_H

/* Fills the image table the constructor walks. `self` is the path of this
   dylib and stays the last image, as dyld reports it; a NULL `self` leaves an
   image dyld cannot name, which is what the constructor has to answer for
   without reading any path. */

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self);

/* Makes every write the dylib attempts fail, as a device whose report file
   cannot be created does. */

void fake_refuse_writes(int refuse);

#endif
