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
WebKit the duplicate-image count fell from 31 to 0 and all seven loaded. What the
readings do and do not support matters here. `WebCore` ahead of `JavaScriptCore`
fails with `Symbol not found:
__ZN3WTF10StringImpl28createWithoutCopyingNonEmptyE...`, recorded when `WebCore`
is loaded alone (doc 2.2), because that symbol comes from `JavaScriptCore`.
`WebCore` ahead of the two dylibs but still after `JavaScriptCore` is a different
case: it loaded 7/7 and only raised duplicate-class warnings (doc 2.3), which the
5.2 table shows as the same 7/7 with a count of 31 instead of 0. So the constraint
these readings establish is that `WebCore` cannot precede `JavaScriptCore`; the
reorder against the two dylibs is what the count measures, and the one such
sequence that was run loaded all seven. JavaScriptCore is first because it exports
the WTF symbols the rest bind, and WebKit is last because it reexports WebKitLegacy
and loads JavaScriptCore.

Doc 6.3 answers a different question. There the chain ran in this same order with
`JavaScriptCore` first, and `JavaScriptCore`, `libwebrtc` and `libANGLE-shared` all
loaded; the four remaining steps then failed, on three distinct `WTF` symbols: `WebCore` and
`WebKit` both on `_WTFSignpostIndirectLoggingEnabled`, `WebKitLegacy` on
`WTF::Thread::s_key`, and `WebGPU` on the `StringImpl` symbol quoted above. All three
are exported by the self-built `JavaScriptCore` alone (`llvm-nm -gU` finds them as
`S`/`D`/`T` definitions there and in none of the other four images), and each of the
four failing images refers to its own one as an undefined import. The cause is
the identity rule in doc 4.3: a host that links `WebKit` already has the system copy
resident, and it keeps the identity, so the `dlopen` of the self-built
`JavaScriptCore` returned the resident handle instead of replacing it. That is a
statement about which copy owns the identity, not about where `WebCore` sits in the
chain.

WebCore is why the two dylibs sit where they do. Its load commands carry
`LC_LOAD_WEAK_DYLIB` on three of the seven — `libwebrtc`, `libANGLE-shared` and
`WebGPU`. The other nine weak commands in that image name system libraries
(`BrowserEngineCore`, `libxslt.1`, `libAccessibility` and six `libswift*`), so the
three are the whole of the set this chain relates to. A weak import does not block
the load. In the payload as shipped the three strings are `/System` paths, so a
WebCore loaded first bound the resident 16.1 copies rather than the `/var/jb`
ones, and the later `/var/jb` load of a dylib that had already been bound this way
mapped a second copy. Two of the three were then reordered and the third was not:
`libwebrtc` and `libANGLE-shared` are steps 2 and 3 against WebCore's step 4, and
moving them ahead is what took the duplicate count to 0, while `WebGPU` is step 6,
after WebCore, so no run has varied it. Both of the reordered two belong in the
experiment because both are weak imports WebCore would otherwise bind to the
system copies; the count is what only one of them can report. The 31 belongs to
`libwebrtc` alone — it is `Class WK_RTC... is implemented in both` warnings, and
`libwebrtc` carries 466 `WK_RTC` strings, so two copies of it in one process must
produce them. `WebGPU` carries no `WK_RTC` name, so it cannot contribute to that
count. `libANGLE-shared` defines no classes at all (it has `__objc_classname` and
`__objc_classrefs` but no `__objc_classlist`), so two copies of it would raise no
warning and this count cannot decide whether it ever had a second one. Doc 4.3's
rule bears on that question — identity is decided by install name and the resident
copy wins, and the `/var/jb` copy shares its install name with the shared-cache
one — but its row for that case is marked not directly measured. So the 31 proves
a second copy for `libwebrtc`, and for neither of the other two either way. In the
rewritten tree all three strings are `/var/jb/.../WebKit18`, so the System string
is gone and no weak import of the three can name the system image — which is why
the rewrite, not the ordering alone, is what makes the weak set safe.

`RTLD_NOW` turns a missing symbol into a failed step instead of a fault at first
call, and every step asks for it.

About `RTLD_GLOBAL`: the order measurement above is not evidence for it. The
probe that produced those counts calls `dlopen(kImages[i], RTLD_NOW)` — see
`docs/ios16-dyld-identity-and-load-conditions.md` 8.1 — with no `RTLD_GLOBAL`, so
the 31 → 0 reading was taken with every image in its own scope, and no device run
has compared the two modes. The flag is kept because it is the permissive
placement: it cannot hide a sibling behind a private scope, and this dylib's own
class and function lookups go through the global table
(`symbol_find_global`). Whatever `RTLD_GLOBAL` is worth is unmeasured, and the
chain's own edges do not need it: each of the seven names its siblings by
absolute `/var/jb` path.

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
