#ifndef IDEVICE_WEBKIT_FAKE_DYLD_H
#define IDEVICE_WEBKIT_FAKE_DYLD_H

/* Fills the image table the constructor walks. `self` is the path of this
   dylib and stays the last image, as dyld reports it. */

void fake_reset(const char *first, const char *preferences, const char *loaded,
                const char *self);

#endif
