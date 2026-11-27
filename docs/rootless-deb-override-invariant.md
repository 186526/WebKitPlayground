# Rootless deb override directory

These three source literals must resolve to `/var/jb/Library/WebKitOverride`:

- [`scripts/install-webkit-override.sh:66`](../scripts/install-webkit-override.sh#L66): `OVERRIDE_ROOT=/var/jb/Library/WebKitOverride`.
- [`dopamine-basebin/0001-systemhook-set-search-paths.patch:92`](../dopamine-basebin/0001-systemhook-set-search-paths.patch#L92): `JBROOT_PATH("/Library/WebKitOverride")`, relative to the jailbreak root. The corresponding dylib search directory is `usr/lib` below the override.
- [`patches/ios18-public-sdk-xpc-main.patch:16`](../patches/ios18-public-sdk-xpc-main.patch#L16): `WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride;`, the first search directory embedded in the XPC helpers.

`scripts/package-webkit-device-deb.sh --rootless` checks this agreement during both build and `--verify-deb`. The payload stays under `/var/jb/Library/Frameworks` and `/var/jb/usr/lib`. On configure, the package creates the override directory and adds relative links only to its WebKit frameworks and dylibs. Upgrades replace the package payload at the link targets without duplicating bytes.

Before changing the override directory, configure checks that all five framework binaries exist and reports every missing path. The package only manages an override directory with its ownership marker. Within that directory, configure replaces stale links only if their targets resolve under the installed framework or dylib directory; it reports and preserves other entries. Rootless packages depend on `coreutils` for `realpath -m`, which resolves stale targets even when the old files no longer exist. If configure reports an unmanaged directory, inspect its contents, move it aside using the command in the error, and retry configuration. Removal only unlinks matching managed links and removes empty directories; unrelated contents remain. Rootful and roothide packages do not create the override directory.

Run `python3 scripts/test-package-webkit-device-deb.py` to exercise the generated maintainer scripts in a scratch installation tree.
