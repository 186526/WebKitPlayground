#ifndef IDEVICE_WEBKIT_IDEVICE_WEBKIT_H
#define IDEVICE_WEBKIT_IDEVICE_WEBKIT_H

#include "textkit.h"

#define tweak_directory "/var/jb/usr/lib/TweakInject/"
#define report_directory "/var/jb/var/mobile/Library/Preferences/"
#define guest_prefix "/private/preboot/"
#define host_manifest_prefix "com.websafe.idevice.host."
#define manifest_suffix ".plist"
#define report_file_name "com.websafe.idevice.plist"
#define marker_file_name "com.websafe.idevice.ready"
#define framework_path "/var/jb/Library/Frameworks/WebKit.framework/WebKit"
#define self_path "/var/jb/usr/lib/TweakInject/WebSafeWebKit.dylib"
#define stage_pause_microseconds 5000u
#define path_capacity 2048
#define version_capacity 128

/* The two paths are writable so a test can point them at a directory it owns;
   the device build leaves them at their default. */
extern char idevice_webkit_report_path[256];
extern char idevice_webkit_marker_path[256];

void idevice_webkit_start(void);

#endif
