# Swap the WebKit payload for an 18.3 tree

`rewrite-payload.py` turns the `com.82flex.custom-webkit` payload into a tree the
seven images can be loaded from, at `/var/jb/Library/WebKit18`, and repairs the
signature the rewrite invalidates. It runs on the host, against the payload a
.deb installs, before anything reaches the device.

```
python3 rewrite-payload.py plan   SOURCE_ROOT STAGING_ROOT [--deps-only]
python3 rewrite-payload.py write  SOURCE_ROOT STAGING_ROOT [--deps-only]
python3 rewrite-payload.py verify        STAGING_ROOT [--deps-only]
```

`SOURCE_ROOT` is the payload as the package installs it — a deb unpacked at
`/tmp/newdeb/x` puts it at `/tmp/newdeb/x/var/jb`. `STAGING_ROOT` stands for the
device's `/`, so the file whose device path is
`/var/jb/Library/WebKit18/WebKit.framework/WebKit` is written to
`STAGING_ROOT/var/jb/Library/WebKit18/WebKit.framework/WebKit`. The strings
inside the binaries are always the device's own paths; only the bytes can be
produced and checked on a host.

`plan` parses and reports without writing a byte. `write` copies the seven
framework directories and the two bare dylibs, rewrites their load commands,
repairs both CodeDirectories, and reads the result back. `verify` reads a
staging root and holds it to the properties it exists for.

## What the rewrite changes, and what it leaves alone

The payload's images name the system paths. An 18.3 WebKit opened by its
`/var/jb` path still binds its dependencies by the literal `/System` strings
inside it, so it binds the 16.1 JavaScriptCore. The device recorded exactly that:

```
dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001):
  Symbol not found: _WTFSignpostIndirectLoggingEnabled
  Referenced from: <787DFAB1-...> .../WebKit.framework/WebKit
  Expected in:     <D309AA36-...> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
```

The same install names also make dyld treat a `/var/jb` image and its `/System`
twin as one identity, and dyld matches by install name before file identity. So
an image that kept the system `LC_ID_DYLIB` would be answered from the resident
16.1 copy even after `dlopen` of the `/var/jb` path succeeded. `write` therefore
rewrites both, and `--deps-only` — which leaves the identity alone — is a
mechanism probe, not a fix.

Measured on the payload (620.2.4): **18 load commands rewritten** — seven
`LC_ID_DYLIB` and eleven dependencies among the seven — and **100 `/System`
references left byte for byte alone**. Those 100 are dependencies on real system
libraries (Foundation, CoreFoundation, Security, Metal, IOSurface and the rest).
Leaving them is what keeps them binding the copies the process already has.

`--deps-only` rewrites 11 and leaves 107: the seven difference is the identities it
stops short of, every one of which names one of the seven.

## Why absolute paths, not @rpath

The payload carries zero `LC_RPATH`. An `@rpath` rewrite would have to append a
load command into the header pad of all seven images. An absolute path appends
nothing, and it is handed straight to the loader: an `@rpath` entry is a searched
path, and dyld's `isOverridablePath` gate (`dyld/Loader.cpp:565`) discards
non-cache candidates for a cached framework unless the jailbreak's dyld hooks are
installed. On this device they are not — `/var/jb/basebin/gen/dyld` scores
`1 0 0 1` on the hook-token filter, which means both override hooks are absent.

A literal `/var/jb` path needs no hook and no environment variable. When
`DYLD_FRAMEWORK_PATH` is empty the candidate list is the original path alone;
`/var/jb` is not in the shared-cache index, so `originalPathIsInDyldCache` is
false and the gate never engages. The reasoning is in
`docs/dopamine-basebin-webkit-override.md`, 第三节 and 第九节.

## What the signature repair is, and what it is not

A modified image is killed even though Dopamine admits unsigned code. The control
measurement: the same file, path and size, one byte changed inside
`LC_ID_DYLIB` → `exit 137`; the untouched copy loads. Admission comes from
`pmap_cs_allow_invalid` (`BaseBin/libjailbreak/src/kernel.c:311`), applied to
every checked-in process, and it is not `proc_set_debugged` — `CS_DEBUGGED` is
false in the process that loads all seven. Signature validity is the barrier.

The payload's own signature shape, parsed on the device, is what an acceptable
one looks like:

| image | superblob | CD flags | hashType | pages |
| --- | --- | --- | --- | --- |
| `WebKit` | `fade0cc0` count 3, 636800 | `0x0` | 1 | 12237 |
| `JavaScriptCore` | `fade0cc0` count 3, 479296 | `0x0` | 1 | 9208 |
| `WebGPU` | `fade0cc0` count 3, 40464 | `0x0` | 1 | 769 |

`flags=0x0`, not `CS_ADHOC` (`0x2`). All seven carry a second CodeDirectory at
slot 4096 with `hashType` 2 over the same pages, and the repair recomputes both.

The repair is a page-hash recomputation, not a re-sign, and one property makes
that sufficient: **`LC_CODE_SIGNATURE`'s `dataoff` equals the CodeDirectory's
`codeLimit` in all seven images.** The signature therefore lies entirely past the
last hashed page, so writing page hashes cannot disturb the signature that holds
them and one pass is enough. The two special slots are not page hashes either:
slot −1 (info.plist) is all zeros, and slot −2 is the SHA-1 of the slot 2
requirements blob, which is not rewritten.

`verify` prints, per image, `LC_CODE_SIGNATURE`'s offset, size and code limit,
whether the signature is past the hashed region, and each CodeDirectory's
`hashType` and page count. It fails if any page hash does not match the bytes.

That is a self-consistent signature, not a proof a device admits it. **Nothing
has been loaded from a repaired copy**, so the script reports the shape it
produced and does not call it verified on a device.

## What this does and does not achieve

Two facts, both read on the device this round, bound the result.

**Helpers can receive a search-path substitution; applications never can.**
Only helper processes go through `xpcproxy` — `launchd.log` this boot has
`xpcproxy spawned with pid N` for every WebKit helper and for zero apps. The
writer of the four `DYLD_` names is the *patched* `systemhook`, in
`spawn_exec_hook_common`, guarded by `shouldInsertJBEnv`
(`dopamine-basebin/0001-systemhook-set-search-paths.patch:99-104`). The device
carries the stock one: 233520 bytes, `6f16c545…`, all six names at count 0,
byte-identical to `Dopamine.app/basebin.tar`. The patched build is 234336,
`d7045cd4…`. A patched `launchdhook` does not help either way: `launchd` injects
only `DYLD_INSERT_LIBRARIES`, and stock `launchdhook` has zero hits for the four
names.

Only the never-executed `/var/jb` copy of WebContent carries `LC_DYLD_ENVIRONMENT`;
the Cryptex copy that actually runs carries none, so helper redirection rests
entirely on environment variables plus the dyldhook patch.

**The applications bind a shared-cache path that no file replacement can reach.**
WeChat's main binary has a direct `LC_LOAD_DYLIB` on
`/System/Library/Frameworks/WebKit.framework/WebKit`. That path does not exist on
disk — it is shared-cache only, and `/System/Library/Frameworks/WebKit.framework`
is a symlink into `Cryptexes/OS` — so it resolves before any injected constructor
runs, and by install name to the resident 16.1 image. The same holds for
`JavaScriptCore` and `WebCore`.

So the honest reading of this tool is: **it produces a self-consistent 18.3 tree
that loads and binds to itself.** It does not give WeChat, Chrome or Safari 18.3.
Whether that substitution is even attempted is reported separately, with UUIDs:
the seven UUIDs are what the chain loaded, and the three system UUIDs are what the
application's own request had already resolved. Equal sets mean the substitution
happened; disjoint sets mean the payload works and the substitution did not.

Do not read "the seven load" as "the browser runs 18.3". They are two different
statements, and only the first is within this tool's reach.

## Telling a signature failure from a bind failure

The device reports a load failure as a symbol error, not as a path error, so the
report separates the two by `error`:

```
empty error, process killed     the signature does not describe the bytes
non-empty error with
  "Symbol not found:"           a bind resolved somewhere other than the tree
```

An empty `error` beside a killed process is never a bind problem: dyld refused the
image before it could bind anything. A `Symbol not found:` means the image was
admitted and then bound the wrong thing, which is the case the rewritten
`LC_ID_DYLIB` and dependency strings exist to prevent.

## Tests

`tests/rewrite_payload_test.py` builds a synthetic payload of seven Mach-O files
and holds the script to the properties a device would otherwise report as a
symbol error:

- the seven paths in `src/load_chain.c` and in `rewrite-payload.py` are one list,
  in one order;
- length unchanged, `LC_CODE_SIGNATURE` offset and size unchanged, no byte
  outside a rewritten slot changed;
- the source payload is byte-identical before and after;
- every `LC_ID_DYLIB` names the path it occupies;
- WebCore's three `LC_LOAD_WEAK_DYLIB` commands on the other six come back naming
  `/var/jb`, none still naming `/System` — the set the load order exists to
  satisfy, read out of the written file rather than taken from the plan;
- a slot too small is refused before anything is copied;
- `--deps-only` keeps the identity and `verify` treats that as the probe's intent;
- both CodeDirectories describe the bytes after the repair.

It runs in `tests/run.sh` right after the signer check, so the tree the tweak
loads and the tree the rewrite writes cannot drift apart without a red test.
