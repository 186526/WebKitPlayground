# idevice-webkit

Builds and packages the dylib ellekit injects into the listed apps so a
self-built WebKit is loaded in their processes. The directory holds the dylib
sources (`src/`), the build and packaging scripts, the ellekit filter, the host
manifests, and the tests (`tests/`).

Two device measurements, both taken on iPad14,4 / iPadOS 16.1, decide what this
directory does. They are written here because they are not inferable from the
sources.

## The seven 18.3 images load as a chain, not as one dlopen

The tweak used to `dlopen` WebKit alone. The device answered:

```
dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001):
  Symbol not found: _WTFSignpostIndirectLoggingEnabled
  Referenced from: <787DFAB1-...> .../WebKit.framework/WebKit
  Expected in:     <D309AA36-...> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
```

The entry image was 18.3; the dependency it bound was 16.1. `src/load_chain.c`
loads all seven in the measured order (JavaScriptCore first, WebKit last), each
by explicit `/var/jb` path with `RTLD_NOW|RTLD_GLOBAL`, and stops at the first
failure so the report names the step rather than the last image that happened to
be tried.

The order is `build-state.md` 第八轮's measurement: with
JavaScriptCore → libwebrtc → libANGLE-shared → WebCore → WebKitLegacy → WebGPU →
WebKit the duplicate-image count fell from 31 to 0 and all seven loaded; any
other order made WebCore fail with `Symbol not found:
__ZN3WTF10StringImpl28createWithoutCopyingNonEmptyE...`. JavaScriptCore is first
because it exports the WTF symbols the rest bind, and WebKit is last because it
reexports WebKitLegacy and loads JavaScriptCore.

`RTLD_NOW` turns a missing symbol into a failed step instead of a fault at first
call. `RTLD_GLOBAL` is the flag whose absence the device recorded as `0x0001`:
without it each image is a separate scope, and WebCore's `LC_LOAD_WEAK_DYLIB`
bindings to `libwebrtc`, `libANGLE-shared` and `WebGPU` resolve against the
global scope, so those two must be GLOBAL for the measured order to mean
anything.

The paths name `/var/jb/Library/WebKit18`, which `rewrite-payload.py` produces and
repairs the signature of. Without that rewrite the seven keep their system install
names and bind the resident 16.1 siblings. See `docs/rewrite-payload.md`.

The report states two readings separately, because they are different questions:
the seven UUIDs are what the chain loaded, and the three system UUIDs are what the
application's own `/System/.../WebKit` request had already resolved. Equal sets
mean the substitution happened; disjoint sets mean the payload works and the
browser is still on 16.1. Do not read "seven loaded" as "the browser runs 18.3".

## The filter's lists sit in the top-level Filter dictionary

`WebSafeWebKitFilter.plist` has one top-level key, `Filter`, and `Classes` and
`Bundles` are keys of that subdictionary:

```
{"Filter": {"Classes": ["UIApplication"], "Bundles": [...]}}
```

Reading the file dictionary for `Classes` answers nil; reading the `Filter`
subdictionary answers the array. A probe in the same Safari process showed the
pair directly: `objectForKey:"Classes"` on the top-level dictionary returned
nil, while `objectForKey:"Filter"` followed by `objectForKey:"Classes"` returned
`{UIApplication}` with count 1.

`src/filter_match.c` therefore reads the `Filter` key first and looks the two
lists up in that subdictionary. A file without a `Filter` key keeps the older
flat shape, where the lists are the file dictionary's own keys, so such a filter
still reads. Before 1.0.14 the reader asked the file dictionary alone, so
`matchVia` was `none` in every process on the device and the report could not
tell a Classes match from a Bundles match.

`tests/fixtures/fake_objc.c` models the device shape: its file dictionary holds
`Filter` and nothing else, and `fake_filters_flat()` opts a case into the older
shape. Two cases in `tests/test_main.c` pin the product behaviour — a class and
a bundle listed only in the subdictionary still match — and both are red against
the 1.0.13 reader.

## Safari loads only a dylib that holds an arm64e slice

Safari is an arm64e process, and ellekit injects a dylib only when it carries an
arm64e slice. A thin arm64 dylib is skipped. The control on the device: the same
filter given to a thin arm64e dylib made Safari load it, and given to a thin
arm64 dylib it did not.

The arm64e slice must be compiled and linked as arm64e. Rewriting the arm64
slice's `cpusubtype` to `0x80000002` with `dd`, which 1.0.11 did, is not a
substitute: dyld then treats arm64 code as arm64e, signs every fixed-up pointer
including the function addresses `dlsym` answers, and the first `br` to one of
them faults.

`build.sh` builds one slice at a time:

```
ARCH=arm64  ./build.sh     # build/arm64/WebSafeWebKit.dylib, on any host with clang and ld64.lld
ARCH=arm64e ./build.sh     # build/arm64e/WebSafeWebKit.dylib, on the device
```

The arm64e slice cannot be linked on a Linux host. `ld64.lld` refuses
`-fixup_chains` for an arm64e target (`error: -fixup_chains is only supported on
x86_64 and arm64 targets`) and writes `LC_DYLD_INFO_ONLY` without it; the device
loads such an image and runs no constructor. The arm64e slice is linked on the
device with Apple ld64 (`/var/jb/usr/bin/ld64`) and the SDK in
`/var/jb/usr/share/SDKs/iPhoneOS.sdk`, which produces `cpusubtype 0x80000002`,
`LC_DYLD_CHAINED_FIXUPS` and a non-empty `__init_offsets`. `build.sh` fails
rather than writing an arm64e-named slice with any other properties.

`package-deb.sh` signs each slice on its own under the install name
`WebSafeWebKitFilter.dylib`, joins the two with `lipo`, and checks the fat file
holds `arm64` and `arm64e`. ldid takes the file's base name as the
CodeDirectory identifier, so a slice signed under a build name such as
`t2.dylib` is rejected by the kernel with SIGKILL CODESIGNING / "Invalid Page";
`check-signature.py` reads each slice back and fails the package unless the
identifier is the install name and the flags word is `0x0`.

`check-slice.py` reads a thin slice and holds it to `cpusubtype`,
`LC_DYLD_CHAINED_FIXUPS`, no `LC_DYLD_INFO_ONLY`, a non-empty `__init_offsets`,
and no `_dyld_`/`___dyld_` undefined name. It parses the Mach-O itself, because
the device carries no `llvm-objdump` to run the checks `tests/run.sh` makes on
the arm64 slice.

## Commands

```
ARCH=arm64  ./build.sh                       # the arm64 slice
ARCH=arm64e ./build.sh                       # the arm64e slice, on the device
LIPO=llvm-lipo ./package-deb.sh              # both slices into one .deb
bash tests/run.sh                            # the C suite, the slice checks, the signer
./simulate-install.sh                        # postinst/prerm through dpkg, 11 cases
```

`package-deb.sh` reads `build/arm64/WebSafeWebKit.dylib` and
`build/arm64e/WebSafeWebKit.dylib` unless `--arm64-slice` and `--arm64e-slice`
name them. A missing slice stops the packaging: one slice loads only in a
process of its own architecture. On Linux, `lipo` is usually `llvm-lipo`, so
pass `LIPO=llvm-lipo`.
