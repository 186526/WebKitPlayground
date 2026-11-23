# Dopamine basebin: framework override patch

This directory holds one patch against `opa334/Dopamine` tag `3.0.13`. It adds
two hooks to `BaseBin/dyldhook` so a framework installed outside the system path
can override the copy inside the dyld shared cache.

| File | Purpose |
| --- | --- |
| `0001-dyldhook-allow-framework-override.patch` | Adds `src/framework_override.c` and its trampoline symbols in `src/main.S`. Applies to tag `3.0.13`. |
| `verify-dyldhook-merge.sh` | Static assertions on the built `dyldhook_merge.*.dylib` or the merged `gen/dyld`. Needs only `llvm-nm`. |
| `test-verify-dyldhook-merge.py` | Regression tests for the verifier. Needs only `python3`. |

The rationale, the dyld call sites each hook covers, and the install and
rollback steps are in `../docs/dopamine-basebin-webkit-override.md`.

## What the patch changes

`BaseBin/dyldhook/src/framework_override.c` (new) defines two hooks, both of
which return `true` and call nothing:

```c
bool HOOK(_ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc)(const void *dyldCache, const char *dylibPath);
bool HOOK(_ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc)(const char *dylibPath);
```

Both bodies are a bare `return true`, because `BaseBin/dyldhook/Makefile` runs
`nm -u` on every `dyldhook_merge.*.dylib` it produces and fails the build when
any undefined symbol remains. The Makefile already collects `src/*.c`, so the
new file is built without touching the file list.

`BaseBin/dyldhook/src/main.S` (modified) gains the matching
`_MACHOMERGER_ORIG_*` and `_MACHOMERGER_HOOKTRAMPOLINE_*` symbol pairs that
MachOMerger looks for: it replaces the `nop` with the original first
instruction and the `b .` with a branch to the hook.

Both additions sit inside `#if IOS >= 16`. On iOS 15 the predicate is
`dyld4::DyldSharedCache::isOverridablePath(char const*) const`, declared in
`common/DyldSharedCache.h:207` of `dyld-957`; the
`dyld4::ProcessConfig::DyldCache` form the patch hooks first appears in the
`dyld-1042.x` sources. Hooking the iOS 16-17 name on an iOS 15 dyld would
resolve to nothing and fail the MachOMerger step.

## Build on macOS

Every command below runs as written on macOS. The build is the upstream one;
the patch only adds a source file and assembler symbols.

### 1. Tools

```sh
brew install make lld ldid libarchive
```

`gmake` from `brew install make` is required: the upstream Makefiles use GNU
Make features that the system `make` does not provide. The upstream CI
(`.github/workflows/main.yml`) uses the same set.

### 2. theos

```sh
export BASEDIR="$(pwd)"
export THEOS="${BASEDIR}/theos"
mkdir -p "$THEOS"
curl -fsSL https://raw.githubusercontent.com/theos/theos/master/bin/install-theos > install-theos.sh
```

The stock `install-theos` script downloads Apple's SDKs, which needs an Apple ID.
The upstream CI replaces that step with a scripted download of `theos/sdks`.
Use the same trick:

```sh
gsed -E "/^\s*get_theos\s*$/,+1 s/^(\s*)(get_sdks)\s*$/\1mkdir -p \${THEOS}\/sdks\n\1touch \${THEOS}\/sdks\/sdk\n\1\2/g" -i install-theos.sh
bash install-theos.sh
curl -L https://github.com/theos/sdks/releases/latest/download/iPhoneOS16.5.sdk.tar.xz --output "$THEOS/sdks/iPhoneOS16.5.sdk.tar.xz"
gunzip "$THEOS/sdks/iPhoneOS16.5.sdk.tar.xz"
tar -xf "$THEOS/sdks/iPhoneOS16.5.sdk.tar" -C "$THEOS/sdks"
rm "$THEOS/sdks/iPhoneOS16.5.sdk.tar"
```

`gsed` comes from `brew install gnu-sed`. This is an iPhoneOS SDK, so
`BaseBin/dyldhook/Makefile`'s `xcrun --sdk iphoneos --show-sdk-path` has
something to return.

### 3. trustcache

`BaseBin/Makefile`'s `basebin.tc` target runs `trustcache create`, and that tool
is not part of the repo:

```sh
git clone https://github.com/CRKatri/trustcache
cd trustcache
export CFLAGS="$CFLAGS -I$(brew --prefix openssl)/include -arch arm64"
export LDFLAGS="$LDFLAGS -L$(brew --prefix openssl)/lib -arch arm64"
gmake -j"$(sysctl -n hw.logicalcpu)" OPENSSL=1
mkdir -p "$BASEDIR/bin"
cp trustcache "$BASEDIR/bin/"
cd "$BASEDIR"
export PATH="$BASEDIR/bin:$PATH"
```

### 4. Source

```sh
git clone --recursive --branch 3.0.13 https://github.com/opa334/Dopamine.git
cd Dopamine
git describe --tags
```

`git describe --tags` must print `3.0.13`. `--recursive` is required: `ChOma`,
`XPF`, `opainject` and `litehook` are submodules and `BaseBin/Makefile` builds
them.

### 5. Apply the patch

```sh
git apply --check /path/to/WebKitPlayground/dopamine-basebin/0001-dyldhook-allow-framework-override.patch
git apply /path/to/WebKitPlayground/dopamine-basebin/0001-dyldhook-allow-framework-override.patch
git status --short
```

`git status --short` must show ` M BaseBin/dyldhook/src/main.S` and
`?? BaseBin/dyldhook/src/framework_override.c`.

### 6. Build

```sh
export THEOS="${BASEDIR}/theos"
export PATH="$BASEDIR/bin:$(brew --prefix make)/libexec/gnubin:$PATH"
gmake -j"$(sysctl -n hw.logicalcpu)" -C BaseBin
```

The result is `BaseBin/basebin.tar`. The six
`BaseBin/dyldhook/dyldhook_merge.*.dylib` files stay in place afterwards, so
step 7 can check them without unpacking the tar.

`gmake -C BaseBin dyldhook` does not shorten the build. That target depends on
`basebin.tc`, which depends on `subprojects`, so the same work runs and the tar
is simply never assembled.

### 7. Verify before touching the device

```sh
sh /path/to/WebKitPlayground/dopamine-basebin/verify-dyldhook-merge.sh BaseBin/dyldhook
```

The verifier checks the three arm64e dylibs. The iOS 16-17 and iOS 18+ dylibs
must define both new hooks, the iOS 15 dylib must not, all three must keep
`dlopen_from` and `__fcntl`, and none may have an undefined symbol. It exits
non-zero on any violation.

Alternatively point it at the merged dyld the device will run:

```sh
BaseBin/MachOMerger gen/dyld.orig BaseBin/dyldhook/dyldhook_merge.arm64e.iOS16-17.dylib /tmp/gen-dyld
sh /path/to/WebKitPlayground/dopamine-basebin/verify-dyldhook-merge.sh /tmp/gen-dyld
```

## The patch is format-checked, not built here

`git apply --check` against a fresh clone of tag `3.0.13` passes. The two hook
bodies compile with `clang -target arm64e-apple-ios16.0 -DIOS=16` and leave
`llvm-nm -u` empty. `main.S` assembles with the new block for `IOS=16` and
`IOS=18`, and omits it for `IOS=15`.

The full `gmake -C BaseBin` run has not been executed: it needs macOS, theos,
an iPhoneOS SDK and the trustcache tool. The verifier's own cases are covered by
`test-verify-dyldhook-merge.py`, which runs anywhere with `python3`:

```sh
python3 /path/to/WebKitPlayground/dopamine-basebin/test-verify-dyldhook-merge.py
```
