# Device deb helper-spawn check

`com.82flex.custom-webkit` version `620.2.4-20261126231806` shipped a WebKit binary built from `66879e8`, before `ae0a207` added the registered-port helper-spawn patches. A text scan of the installed binary found none of the helper-spawn markers below, while the `WKWebsiteDataTypeCookies` control matched twice. That package could not exercise the new helper-spawn path.

Before creating a `.deb` from either a build product or `--payload`, `scripts/package-webkit-device-deb.sh` scans the staged `WebKit.framework/WebKit` using a fixed-string `grep -aFq` search. It needs no Apple SDK or symbol-table utilities. Both independently attributable markers must be present. If either is missing, packaging stops and reports the file, the missing side, and both searched markers:

- UI spawn: `posix_spawnattr_set_registered_ports_np`, the symbol name passed to `dlsym` by the UI patch.
- Helper client: `Exiting: no XPC client connection from registered ports`, a helper-only `RELEASE_LOG_FAULT` format. The target iOS release build uses `os_log_fault` for this macro.

`WK_HELPER_LAUNCH_MODE` appears in both patches, so it cannot distinguish them. Matching both required strings passes this presence check only. It does not verify the default launch mode, every architecture slice, or successful launches on iOS 16. If the check fails, confirm the build consumed the registered-port patches and rebuild the product before packaging it again.

Run `python3 scripts/test-package-webkit-helper-spawn.py` from the inner repository to exercise the check with synthetic binaries.
