# idevice-webkit

Builds and packages the dylib ellekit injects into the listed apps so a
self-built WebKit is loaded in their processes. The directory holds the dylib
sources (`src/`), the build and packaging scripts, the ellekit filter, the host
manifests, and the tests (`tests/`).

Two device measurements, both taken on iPad14,4 / iPadOS 16.1, decide what this
directory does. They are written here because they are not inferable from the
sources.

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

## The seven images are claimed in a fixed order

The dylib marks its own process debugged and then loads the seven `/var/jb`
images itself, in the order `src/claim_images.c` holds:

```
/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
/var/jb/usr/lib/libwebrtc.dylib
/var/jb/usr/lib/libANGLE-shared.dylib
/var/jb/Library/Frameworks/WebCore.framework/WebCore
/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy
/var/jb/Library/Frameworks/WebGPU.framework/WebGPU
/var/jb/Library/Frameworks/WebKit.framework/WebKit
```

The marking is `/var/jb/basebin/jbctl proc_set_debugged <pid>`, run with
`posix_spawn` because `system()` is not usable here and `/bin/sh` does not
exist on the device. It needs no root and lasts only for the process's life.
The wait for that child is polled for at most half a second, because the
constructor runs inside the host's launch and an unbounded wait there would
hold the application at startup.

### What the sequence cannot do

The sequence loads all seven only in a host that does not link WebKit at load
time. An application that does — MobileSafari links WebKit as a load-time
dependency — has the system 16.1 WebKit mapped while dyld prepares the main
executable, and `__mod_init_func` entries run after that. Measured on the
device with a host linked against a stub WebKit that no runtime search path
reached:

```
[ctor] entered; WebKit images already present = 1
[ctor]   already: SYSTEM
Successfully marked proc of pid 32399 as debugged
[ctor] JavaScriptCore LOADED
[ctor] libwebrtc.dylib LOADED
[ctor] libANGLE-shared.dylib LOADED
[ctor] WebCore        FAILED  Symbol not found: _WTFSignpostIndirectLoggingEnabled
[ctor] WebKitLegacy   FAILED  Symbol not found: __ZN3WTF6Thread5s_keyE
[ctor] WebGPU         FAILED  Symbol not found: __ZN3WTF10StringImpl28createWithoutCopyingNonEmptyE...
[ctor] WebKit         FAILED  Symbol not found: _WTFSignpostIndirectLoggingEnabled
[ctor] after claim, WebKit images = 1
```

Three load and four fail, because the system `JavaScriptCore` is what dyld has
already bound for them. The sequence does not replace that image and no order,
flag or timing change in a constructor can: a constructor runs after the main
executable's dependencies exist. A loaded count of 3 with `failed_index` 4 is
this shape rather than a broken order.

The sequence is still the right action in the two hosts it fits. In a host that
lazily `dlopen`s WebKit it wins the dedupe and loads all seven, which is how the
7/7 reading was obtained. In a hard-linked host it fails harmlessly and its
failure is the diagnosis. The path that covers the hard-linked case is the dyld
layer, not this dylib: the override hooks `isOverridablePath` and
`isAlwaysOverridablePath` make dyld consult an override directory during
dependency resolution, before any constructor runs and before the system image
is mapped. On the device those two hooks are absent — `/var/jb/basebin/gen/dyld`
and `/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib` both score `1 0 0 1`
on the token criterion for `dlopen_from`, `isOverridablePath`,
`isAlwaysOverridablePath` and `__fcntl`, while the hook dylib inside
`basebin.tar` scores `1 1 1 1`. Installing that hook is what a hard-linked host
needs, and it is a basebin change rather than a change to this directory.

### Why each position is fixed

Three conditions fix the order and none is visible from the file names:

- `libwebrtc` and `libANGLE-shared` must come before `WebCore`. The `/var/jb`
  `WebCore` records both as weak dependencies whose install names point at the
  system paths, so loading it first pulls the system copies in, and the later
  `/var/jb` loads map a second image of each. On the device that produced 31
  `Class WK_RTC... is implemented in both` lines; this order produces 0.
- The order cannot be reversed once a system WebKit has loaded. Asking for
  `/System/Library/Frameworks/WebKit.framework/WebKit` first then loading the
  `/var/jb` copy fails with `Symbol not found:
  _WTFSignpostIndirectLoggingEnabled`, because the 16.1 system
  `JavaScriptCore` does not define the symbol the 18.x `WebKit` needs.
- The dedupe works because the two identities are equal. The `/var/jb` images
  already carry the system install names; `/var/jb`'s `WebKit` names itself
  `/System/Library/Frameworks/WebKit.framework/WebKit`. That equality is what
  lets a later request for the system path answer with the image already
  loaded. A rewrite of `LC_ID_DYLIB` that gave the `/var/jb` copy its own
  identity would map a second image and produce the failure above, which is why
  the payload keeps the system names.

The claim runs only for a process that passed the filter match, the copy check
and the host check; a process that failed any of them returns before it loads
anything.

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
