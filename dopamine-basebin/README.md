# Dopamine basebin: framework override patch

This directory holds one patch against `opa334/Dopamine` tag `3.0.13`. It adds
two hooks to `BaseBin/dyldhook` so a framework installed outside the system path
can override the copy inside the dyld shared cache.

| File | Purpose |
| --- | --- |
| `0001-dyldhook-allow-framework-override.patch` | Adds `src/framework_override.c` and its trampoline symbols in `src/main.S`. Applies to tag `3.0.13`. |
| `verify-dyldhook-merge.sh` | Static assertions on the built `dyldhook_merge.*.dylib` or the merged `gen/dyld`. Needs only `llvm-nm`. |
| `test-verify-dyldhook-merge.py` | Regression tests for the verifier. Needs only `python3`. |
| `verify-basebin-tar.sh` | Unpacks a built `basebin.tar` and asserts the six dyldhook dylibs are under the names the device asks for, that `systemhook.dylib` and `launchdhook.dylib` both carry the search-path names, that `basebin.tc` covers every member the tar ships, then re-runs `verify-dyldhook-merge.sh` on the shipped bytes. |
| `verify-basebin-trustcache.py` | Fails when a member's per-slice cdhash is missing from `basebin.tc`, which is what `jbctl update basebin` uploads as the basebin trustcache. Exempts `dyldhook_merge.*.dylib`. Needs only `python3`. |
| `verify-merged-dyld.sh` | Runs `MachOMerger` the way `jbctl update basebin` does and asserts the merged dyld carries all four hooks. |
| `test-verify-basebin-artifacts.py` | Regression tests for both new verifiers. Needs only `python3`. |
| `verify-systemhook-search-paths.py` | Asserts the four search-path names and both dedicated directories in a built `systemhook.dylib`, or in `launchdhook.dylib` with `--kind launchdhook`. |
| `test-verify-systemhook-search-paths.py` | Regression tests for that verifier, including the stock `launchdhook` that must fail. Needs only `python3`. |
| `macho_fixture.py` | Builds the minimal Mach-O images both test suites use, so neither needs clang or a device. |

A second patch, `0001-systemhook-set-search-paths.patch`, comes from the
systemhook work and is applied from the same directory when present. It adds
`DYLD_FRAMEWORK_PATH`, `__XPC_DYLD_FRAMEWORK_PATH`, `DYLD_LIBRARY_PATH` and
`__XPC_DYLD_LIBRARY_PATH` to every process Dopamine spawns, so dyld knows where
`/var/jb/Library/WebKitOverride` is. That directory also carries its own
`verify-systemhook-search-paths.py`.

That patch reaches two binaries, not one. `BaseBin/launchdhook/Makefile` lists
`../systemhook/src/common/*.[cSm]` as a dependency, so `launchdhook.dylib` compiles
the same `common.c` and gets the same four variables. `launchdhook` is the
interposer that runs inside `launchd`, the process that performs an app's final
exec, which is why it is the binary that decides whether an app launched from the
home screen sees the override. A stock `launchdhook` writes only
`DYLD_INSERT_LIBRARIES`, so such an app inherits the `__XPC_` spellings from
launchd's environment but never a name dyld reads, and its WebKit comes from the
shared cache. `verify-basebin-tar.sh` therefore asserts the four names in
`basebin/launchdhook.dylib` as well, through
`verify-systemhook-search-paths.py --kind launchdhook`.

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
gmake -j"$(sysctl -n hw.logicalcpu)" -C BaseBin BUILD_STANDALONE=0
```

`BUILD_STANDALONE=0` is not optional when `-C BaseBin` is used. Nine subproject
Makefiles put `-D BUILD_STANDALONE=$(BUILD_STANDALONE)` into their CFLAGS and
none of them defaults it; the only default is `export BUILD_STANDALONE ?= 0` in
the repository root Makefile, which `-C BaseBin` skips. Without the argument the
macro expands to nothing and the build stops at
`BaseBin/dopamine/src/main.m:80: #if BUILD_STANDALONE` with "expected value in
expression". `0` is what the root Makefile defaults to and what the shipped
basebin uses; `1` is the standalone build. Running `gmake` from the repository
root instead needs no argument because that Makefile exports the default to the
sub-makes.

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
must define both new hooks and the iOS 15 dylib must not; `__fcntl` must be
defined on all three; none may have an undefined symbol. It exits non-zero on any
violation.

`dlopen_from` is the exception, and running the build is what surfaced it:
`BaseBin/dyldhook/src/fakelib_redirect.c` wraps that hook in `#if IOS < 18`
(upstream commit `2ae46a8`, "Add support for (at least) iPad 7 18.7.1"), so the
iOS 18+ dylib ships the trampoline pair and deliberately no hook. The verifier
therefore asserts the trampoline on iOS 18+ and the hook everywhere else. It
accepts the hook on iOS 18+ too, so whichever direction upstream moves, the
dylib that carries it passes and the one that drops the symbols fails.

Then check the tar the device will actually receive:

```sh
sh /path/to/WebKitPlayground/dopamine-basebin/verify-basebin-tar.sh BaseBin/basebin.tar
```

That script unpacks the tar and asserts the six names
`dyldhook_dylib_for_platform()` can ask for (`dyldhook_merge.<arch>.<profile>.dylib`
for arm64e and arm64, over iOS15, iOS16-17 and iOS18+), `basebin.tc`,
`systemhook.dylib`, and the same hook assertions as above read from the shipped
bytes. It copies all six dylibs into a scratch directory and hands that
directory to `verify-dyldhook-merge.sh`, so the arm64 variants are checked for
their symbols too, and an arm64 dylib that lost a hook fails the gate. It also
asserts that the tar carries no `gen/dyld`.

`verify-basebin-trustcache.py` runs on the unpacked tar as part of that gate.
`basebin.tc` is not a description of the tar: `update.m:32-39` uploads it as the
basebin trustcache before it copies the members in, `DOJailbreaker.m:348` does
the same at every jailbreak, and nothing recomputes it from `/var/jb/basebin`
afterwards. A tar that replaces a member but keeps the stock file therefore
hands the kernel a trustcache describing binaries it no longer ships. The script
computes each member's per-slice cdhash the way `BaseBin/Makefile:96-98`
(`trustcache create .build/basebin.tc .build`) does and fails on any slice that
is absent from `basebin.tc`. It exempts `dyldhook_merge.*.dylib`, which
`BaseBin/Makefile:100` keeps out of that trustcache on purpose.

`gen/dyld` is deliberately not in the tar. `libjailbreak/src/basebin_gen.m`
builds it on the device: `jbctl update basebin` copies `/usr/lib/dyld`, patches
its `getAMFI` and `LC_UUID`, runs `MachOMerger` over it with the one dyldhook
dylib matching the running iOS version, and writes the result to
`/var/jb/basebin/gen/dyld`. A tar that shipped one would hand every device the
same dyld, built from whichever `/usr/lib/dyld` the build host had.

To check that step, run the merger yourself and assert the hooks survive it:

```sh
gmake -C BaseBin/MachOMerger MACOS=1
xcrun lipo -thin arm64e /usr/lib/dyld -output /tmp/host-dyld.arm64e
sh /path/to/WebKitPlayground/dopamine-basebin/verify-merged-dyld.sh \
   BaseBin/MachOMerger/MachOMerger /tmp/host-dyld.arm64e \
   BaseBin/dyldhook/dyldhook_merge.arm64e.iOS16-17.dylib
```

`xcrun lipo -thin` is required on a macOS host, and it has to be the Xcode
`lipo`: a macos-15 runner has no `llvm-lipo` on PATH, so that name fails with
exit 127. `xcrun lipo -archs /usr/lib/dyld` prints the slices (`x86_64` and
`arm64e`) when you need to confirm the second name. `/usr/lib/dyld` is a universal
binary there, and `MachOMerger` reads a thin Mach-O: handed the fat file it aborts
with `SwiftMachO.MachOError.FatNotMachO` and merges nothing, which the script
reports as exit 3. Its arm64e slice is a genuine arm64e dyld that defines
`isOverridablePath`, `isAlwaysOverridablePath` and `__fcntl`, so the merge has
real functions to attach the hooks to.

Those three are local symbols in the host dyld, which `nm` marks `t` rather than
`T`, so `llvm-nm -g` prints nothing for them even though they are in the file.
A probe that counts them with `-g` therefore reports zero and concludes the dyld
lacks them. Read the full symbol table instead: `llvm-nm /usr/lib/dyld` without
`-g` lists all three, and `MachOMerger/Symbols.swift:51` reads the complete table
through `localStart` and `localCount`, so the merger itself never had this
problem. Dropping `-g` from the probe is the fix, and putting it back will make
the step skip a merge that works.

The assertion is that the merged output carries all four
`_MACHOMERGER_HOOK_*` symbols: `isOverridablePath`, `isAlwaysOverridablePath`,
`dlopen_from` and `__fcntl`, which is the full set when the dylib handed to
`MachOMerger` is the iOS 16-17 one this command names. The host's `/usr/lib/dyld` is not the iOS dyld, so
the merger can fail to attach the hooks to it at all; in that case the script
exits 3 and says it could not merge, rather than reporting the hooks verified.
Point the second argument at a device's `/usr/lib/dyld` pulled over scp to check
that exact dyld, and the exit status becomes meaningful for it too.

## The same build on CI

`.github/workflows/basebin-build.yml` runs the seven steps above on `macos-15`.
It is triggered by `workflow_dispatch`, and by a push that touches this
directory or the workflow file. The patches always come from the workflow's own
checkout, so a build reproduces the branch it ran on.

Inputs:

| Input | Default | Meaning |
| --- | --- | --- |
| `dopamine_ref` | `3.0.13` | Tag, branch or commit to clone. |
| `patches` | `all` | `all` applies every `*.patch` in this directory. A space-separated list of file names applies exactly those, and a name under this directory that does not exist stops the step at `git apply --check` with a non-zero exit. |

The two branches are separate in step 7: `all` reads the directory with
`ls *.patch`, anything else is taken as the file list verbatim. Either way the
step records what was asked for and what it selected in
`$LOGS/patch-request.txt`, and the names it applied in
`$LOGS/applied-patches.txt`.

Because the default is `all`, a patch that has not merged yet is simply absent
from the list instead of breaking the build. The systemhook assertions read the
list back: when no patch whose name contains `systemhook` was applied, the job
prints a `SKIPPED` notice for `DYLD_FRAMEWORK_PATH` and
`__XPC_DYLD_FRAMEWORK_PATH` and still checks `DYLD_INSERT_LIBRARIES`. It never
passes that check silently.

The single-patch branch is verified by local rehearsal, not by a CI run: the
step's `run:` block was extracted from the workflow with `yaml.safe_load` and
executed against a throwaway git repository holding two stand-in patches. Under
both `bash` and `dash`, `PATCHES=all` applied both files, a single name applied
only that file, and a name that does not exist exited 128 from `git apply
--check` and applied nothing. The green CI run exercised the `all` branch only.

Artifacts:

| Artifact | Contents |
| --- | --- |
| `basebin-tar` | `basebin.tar`, its `.sha256`, the `.version` Dopamine wrote into it, and the Dopamine commit it was built from. |
| `basebin-build-logs` | `basebin-build.log` (the whole `gmake` output), `nm/` with `llvm-nm -g` and `-u` for each of the six dylibs, every verifier's output, `systemhook-strings.txt`, and `theos-install.log`. Uploaded with `if: always()`, so it survives a failed build. |

Download with `gh api /repos/186526/WebKitPlayground/actions/artifacts/<artifact-id>/zip`
(`gh run download` refuses these with "would result in path traversal"), or from
the run page.

### Installing on the device

`jbctl update basebin` rewrites `/var/jb/basebin` and restarts userspace, which
drops every running app and re-springs the device. A human runs it; CI does not,
and neither does any agent working in this repository.

```sh
scp basebin.tar root@<device>:/var/mobile/Documents/basebin.tar
ssh root@<device> "/var/jb/basebin/jbctl update basebin /var/mobile/Documents/basebin.tar"
```

`jbctl` regenerates `gen/dyld` as part of that command, which is why the tar
does not carry one. If the update refuses with "Failed to locate dyldhook.dylib",
the tar is missing the dylib name for the device's architecture and iOS version;
`verify-basebin-tar.sh` is the check that catches that before the device does.

## Builds that have actually run

| Run | Result | What it established |
| --- | --- | --- |
| [45221890249](https://github.com/186526/WebKitPlayground/actions/runs/45221890249) | build failed | `gmake -C BaseBin` stopped at `BaseBin/dopamine/src/main.m:80` with "expected value in expression" because `BUILD_STANDALONE` was empty. Step 6 now passes `BUILD_STANDALONE=0`. |
| [45224133796](https://github.com/186526/WebKitPlayground/actions/runs/45224133796) | gate failed | BaseBin compiled for the first time. The gate then rejected the iOS 18+ dylib for lacking the `dlopen_from` hook, which upstream compiles out on purpose. `verify-dyldhook-merge.sh` now asserts the trampoline there. |
| [45225831431](https://github.com/186526/WebKitPlayground/actions/runs/45225831431) | success | Every step passed and `basebin.tar` shipped. The merged-dyld step reported exit 3 (`FatNotMachO`): `/usr/lib/dyld` is universal and `MachOMerger` reads a thin Mach-O. |
| [45227991502](https://github.com/186526/WebKitPlayground/actions/runs/45227991502), [45230084681](https://github.com/186526/WebKitPlayground/actions/runs/45230084681) | cancelled | Queued, then cancelled by the concurrency group when the next push landed. No result of their own. |
| [45234771514](https://github.com/186526/WebKitPlayground/actions/runs/45234771514) | merge step failed | The merged-dyld step called `llvm-lipo`, which a `macos-15` runner does not have, and stopped with exit 127. The step now slices the host dyld with `xcrun lipo`. |
| [45235853001](https://github.com/186526/WebKitPlayground/actions/runs/45235853001) | merge step failed | The probe counted the hook targets with `llvm-nm -g` and printed `input dyld defines ...isOverridablePath: 0`, then failed the job. The symbols are local, so `-g` cannot see them; the probe now reads the full table. |
| [45237030318](https://github.com/186526/WebKitPlayground/actions/runs/45237030318) | success | 17 of 17 steps passed. The merge really ran: the probe printed 1 for all three targets, `MachOMerger` merged `dyldhook_merge.arm64e.iOS16-17.dylib` into the host dyld's arm64e slice, and the output carried all four `_MACHOMERGER_HOOK_*` symbols. `SYSTEMHOOK_PATCH_APPLIED` was `1`, and the systemhook assertions printed 8 passes for each of the two slices. |

The artifact from run `45237030318` is artifact `14849117614` (`basebin-tar`):

| Field | Value |
| --- | --- |
| Size | 6144000 bytes |
| SHA256 | `7fd379340780e245c86fd0afe15ee55173bbb22d8a623bd60e2e34aa644afbc6` |
| Entries | 31, all under `basebin/`, none under `basebin/gen/` |
| `basebin/.version` | `3.0.13` |
| `dopamine-commit.txt` | `5b1d2c1 3.0.13` |
| Build logs | artifact `14848269568` (`basebin-build-logs`) |

Its contents are `basebin.tar`, `basebin.tar.sha256`, `version` and
`dopamine-commit.txt`. Fetch it with
`gh api /repos/186526/WebKitPlayground/actions/artifacts/14849117614/zip`; the
`gh run download` shorthand fails on this artifact with "would result in path
traversal".

Verified on the downloaded tar, beyond what the workflow asserts: the six
`dyldhook_merge.*.dylib` are present, `dyldhook_merge.arm64e.iOS16-17.dylib`
defines `isOverridablePath`, `isAlwaysOverridablePath`, `dlopen_from` and
`__fcntl`, `llvm-nm -u` is empty for every dylib, and the tar carries no
`gen/dyld`.

`systemhook.dylib` is the one library in the tar with undefined symbols: 131 in
the arm64 slice, which is why the systemhook check prints
`131 undefined symbol(s); systemhook/Makefile does not require none`. Those
resolve at load time from `/usr/lib/libSystem.B.dylib`, which each slice lists as
an `LC_LOAD_DYLIB`, so the count is expected rather than a defect.
`dyldhook/Makefile` runs `nm -u` on its own output and fails the build on any
undefined symbol; `systemhook/Makefile` has no such rule, so those symbols stay
undefined on purpose. The two Makefiles differ in this, and only this, respect.

`git apply --check` against a fresh clone of tag `3.0.13` passes for this patch.
The hook bodies compile with `clang -target arm64e-apple-ios16.0 -DIOS=16` and
leave `llvm-nm -u` empty; `main.S` assembles with the new block for `IOS=16` and
`IOS=18` and omits it for `IOS=15`.

Not established by any of these runs: whether the patched dyld makes a WebKit
installed at `/var/jb/Library/WebKitOverride` load instead of the shared-cache
copy. That needs the tar installed on a device and a process started under the
new dyld, which no CI job can do.

The verifier scripts' own cases are covered by two regression suites, which run
anywhere with `python3`:

```sh
python3 /path/to/WebKitPlayground/dopamine-basebin/test-verify-dyldhook-merge.py
python3 /path/to/WebKitPlayground/dopamine-basebin/test-verify-basebin-artifacts.py
```

The first drives `verify-dyldhook-merge.sh` with a fake `llvm-nm`. The second
does the same for `verify-basebin-tar.sh` and `verify-merged-dyld.sh`, including
the cases where each must refuse: a tar missing one of the six dylibs, either
architecture's dylib losing a hook, hooks appearing on either iOS 15 dylib, a
baked-in `gen/dyld`, a merge whose output lost `dlopen_from`, and a merger that
cannot run at all. The arm64 cases are what keep
`verify-basebin-tar.sh` copying all six names: with only the arm64e three
copied, an arm64 dylib that lost a hook passed the gate.
