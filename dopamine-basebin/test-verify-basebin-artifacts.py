#!/usr/bin/env python3
"""Regression tests for verify-basebin-tar.sh and verify-merged-dyld.sh.

Both scripts decide whether a build artifact is shippable, so the cases that matter are the ones
where they must refuse: a tar missing one of the six dyldhook names, a dyldhook dylib that lost a
hook, a gen/dyld baked into the tar, and a merge whose output lost a hook.  A passing case for each
script pins the other direction.

Each case builds a real tar with `tar` and a fake `llvm-nm` first on PATH, so the assertions run
without LLVM, Mach-O objects, a device, or a macOS host.  The fake MachOMerger writes the symbol
list its output should carry, which is what the real merger's output would answer to llvm-nm.

Usage: python3 dopamine-basebin/test-verify-basebin-artifacts.py
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from macho_fixture import (  # noqa: E402
    CDHASH_LEN,
    CSSLOT_ALTERNATE_CODEDIRECTORIES,
    CSSLOT_CODEDIRECTORY,
    HASH_TYPE_SHA1,
    HASH_TYPE_SHA256,
    build_code_directory,
    build_macho,
    build_trustcache,
    cdhash_of,
)

HERE = Path(__file__).resolve().parent
TAR_SCRIPT = HERE / "verify-basebin-tar.sh"
MERGE_SCRIPT = HERE / "verify-merged-dyld.sh"

H = "_MACHOMERGER_HOOK_"
OVERRIDABLE = H + "_ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc"
ALWAYS_OVERRIDABLE = H + "_ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc"
DLOPEN_FROM = H + "_ZN5dyld44APIs11dlopen_fromEPKciPv"
DLOPEN_TRAMPOLINE = "_MACHOMERGER_HOOKTRAMPOLINE__ZN5dyld44APIs11dlopen_fromEPKciPv"
FCNTL = H + "__fcntl"

LEGACY = [DLOPEN_FROM, FCNTL]
PATCHED = LEGACY + [OVERRIDABLE, ALWAYS_OVERRIDABLE]
# Upstream guards the dlopen_from hook with `#if IOS < 18` in fakelib_redirect.c,
# so the iOS 18+ dylib carries the trampoline pair and __fcntl but no hook.
PATCHED_18 = [DLOPEN_TRAMPOLINE, FCNTL] + [OVERRIDABLE, ALWAYS_OVERRIDABLE]

# launchdhook is built from the same common.c, so a healthy tar carries the same four
# search-path names and both dedicated directories in it.  The stock binary carries only
# the insert write and neither directory.
LAUNCHDHOOK_PATCHED = (
    "DYLD_FRAMEWORK_PATH",
    "__XPC_DYLD_FRAMEWORK_PATH",
    "DYLD_LIBRARY_PATH",
    "__XPC_DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "/Library/WebKitOverride",
    "/Library/WebKitOverride/usr/lib",
)
LAUNCHDHOOK_STOCK = ("DYLD_INSERT_LIBRARIES",)

ARM64E_15 = "dyldhook_merge.arm64e.iOS15.dylib"
ARM64E_16 = "dyldhook_merge.arm64e.iOS16-17.dylib"
ARM64E_18 = "dyldhook_merge.arm64e.iOS18+.dylib"
ARM64_15 = "dyldhook_merge.arm64.iOS15.dylib"
ARM64_16 = "dyldhook_merge.arm64.iOS16-17.dylib"
ARM64_18 = "dyldhook_merge.arm64.iOS18+.dylib"
ALL_SIX = [ARM64E_15, ARM64_15, ARM64E_16, ARM64_16, ARM64E_18, ARM64_18]

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS: {name}")
        return
    failures.append(name)
    print(f"FAIL: {name}" + (f" ({detail})" if detail else ""))


NM_STUB = r'''#!/bin/sh
mode=defined
case "$1" in
    -g) shift ;;
    -u) mode=undefined; shift ;;
esac
file=$1
if [ ! -f "$file" ]; then
    echo "llvm-nm: error: $file: No such file or directory" >&2
    exit 1
fi
if [ "$mode" = undefined ]; then
    while IFS= read -r line; do
        case "$line" in
            undef:*) echo "${line#undef:}" ;;
        esac
    done < "$file"
    exit 0
fi
addr=4000
while IFS= read -r line; do
    case "$line" in
        sym:*)  printf '%016x T %s\n' "$addr" "${line#sym:}"; addr=$((addr + 8)) ;;
    esac
done < "$file"
exit 0
'''


def write_symbol_file(path: Path, symbols: list[str], undefined: list[str] | None = None) -> None:
    lines = [f"sym:{s}" for s in symbols] + [f"undef:{s}" for s in undefined or []]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_tar(root: Path, dylibs: dict[str, list[str]], *, prefixed: bool = True,
              extra: dict[str, str] | None = None,
              omit: tuple[str, ...] = (),
              launchdhook_strings: tuple[str, ...] | None = None,
              tc_covers_launchdhook: bool = True,
              dual_code_directories: bool = False,
              tc_has_sha256_over_sha1: bool = False,
              tc_has_sha1_primary_slot: bool = False) -> Path:
    """Write a tar the way BaseBin/Makefile's basebin.tar target lays it out.

    launchdhook.dylib is a real Mach-O because verify-basebin-tar.sh hands it to
    verify-systemhook-search-paths.py.  Passing None leaves the member out, which is the case
    for the tars that predate it; a tuple writes a thin image carrying exactly those strings.

    basebin.tc is written from that image's cdhash, the way `trustcache create` derives it
    from the build output.  tc_covers_launchdhook=False writes an entry-less tc instead, which
    is the shape of the tar that replaced a member but kept the stock file.

    dual_code_directories=True signs launchdhook the way the patched member really is signed:
    a SHA-1 CodeDirectory at slot 0x0 and a SHA-256 one at 0x1000.  The correct entry for a
    slice is the 0x1000 alternate's cdhash, so with that shape:
      tc_has_sha256_over_sha1=True writes the SHA-256 of the SHA-1 blob as well — a value no
      kernel computes — which is the superset defect round 3's P1 named;
      tc_has_sha1_primary_slot=True writes only the SHA-1 blob's own cdhash, the
      right-algorithm-but-wrong-slot mistake.
    """
    staging = root / "staging"
    base = staging / "basebin" if prefixed else staging
    base.mkdir(parents=True)
    for name, symbols in dylibs.items():
        if name in omit:
            continue
        write_symbol_file(base / name, symbols)
    write_symbol_file(base / "systemhook.dylib", LEGACY)
    covered: list[bytes] = []
    if launchdhook_strings is not None:
        if dual_code_directories:
            sha1_cd = build_code_directory(launchdhook_strings[0].encode(),
                                          hash_type=HASH_TYPE_SHA1)
            sha256_cd = build_code_directory(b"alternate" + launchdhook_strings[0].encode(),
                                             hash_type=HASH_TYPE_SHA256)
            image = build_macho(
                launchdhook_strings,
                code_directories=[(CSSLOT_CODEDIRECTORY, sha1_cd),
                                  (CSSLOT_ALTERNATE_CODEDIRECTORIES, sha256_cd)],
            )
            correct = cdhash_of(sha256_cd)  # the 0x1000 alternate is the one that is kept
            orphan = hashlib.sha256(sha1_cd).digest()[:CDHASH_LEN]
            if tc_has_sha1_primary_slot:
                covered.extend([cdhash_of(sha1_cd)])
            else:
                covered.extend([correct])
                if tc_has_sha256_over_sha1:
                    covered.append(orphan)
        else:
            code_directory = build_code_directory(launchdhook_strings[0].encode())
            image = build_macho(launchdhook_strings, code_directory=code_directory)
            covered.append(cdhash_of(code_directory))
        (base / "launchdhook.dylib").write_bytes(image)
        if not tc_covers_launchdhook:
            covered = []
    (base / "basebin.tc").write_bytes(build_trustcache(covered))
    for rel, content in (extra or {}).items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    out = root / "basebin.tar"
    with tarfile.open(out, "w") as tf:
        tf.add(staging, arcname=".")
    return out


def run(script: Path, args: list[str], bin_dir: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        ["sh", str(script), *args], capture_output=True, text=True, env=env, check=False
    )
    return proc.returncode, proc.stdout + proc.stderr


def healthy_dylibs() -> dict[str, list[str]]:
    return {
        ARM64E_15: LEGACY,
        ARM64_15: LEGACY,
        ARM64E_16: PATCHED,
        ARM64_16: PATCHED,
        ARM64E_18: PATCHED_18,
        ARM64_18: PATCHED_18,
    }


def test_tar_script(root: Path, bin_dir: Path) -> None:
    good = build_tar(root / "good", healthy_dylibs(), launchdhook_strings=LAUNCHDHOOK_PATCHED)
    rc, out = run(TAR_SCRIPT, [str(good)], bin_dir)
    check("tar: a healthy tar passes", rc == 0, out)
    check("tar: healthy tar states gen/dyld is absent by design",
          "jbctl update basebin generates it" in out, out)
    check("tar: healthy tar reports launchdhook carries the search paths",
          "launchdhook.dylib carries the four search-path names" in out, out)
    check("tar: healthy tar reports basebin.tc matches the shipped members",
          "basebin.tc holds exactly the cdhashes of the Mach-O members the tar ships" in out, out)

    # A tar that replaces a member but keeps the stock trustcache is the state the
    # basebin tar was in before this gate existed: the file is present, the members
    # are patched, and nothing ties one to the other.
    uncovered = build_tar(root / "uncovered", healthy_dylibs(),
                          launchdhook_strings=LAUNCHDHOOK_PATCHED,
                          tc_covers_launchdhook=False)
    rc, out = run(TAR_SCRIPT, [str(uncovered)], bin_dir)
    check("tar: a trustcache that misses a shipped member fails", rc == 1, out)
    check("tar: an uncovered member is named",
          "launchdhook.dylib slice cdhash" in out and "is not in basebin.tc" in out, out)

    # The patched members are dual-CodeDirectory: a SHA-1 blob at slot 0x0 and a SHA-256
    # blob at 0x1000. The kernel takes the cdhash digest from the blob's own hashType, so
    # the 0x0 blob's cdhash is SHA-1. Hashing it with SHA-256 produces a value no kernel
    # computes, which is what the delivered tar shipped before round 3's fix.
    dual_ok = build_tar(root / "dual-ok", healthy_dylibs(),
                        launchdhook_strings=LAUNCHDHOOK_PATCHED,
                        dual_code_directories=True)
    rc, out = run(TAR_SCRIPT, [str(dual_ok)], bin_dir)
    check("tar: a dual-CodeDirectory member with its 0x1000 cdhash passes", rc == 0, out)

    dual_wrong = build_tar(root / "dual-wrong", healthy_dylibs(),
                           launchdhook_strings=LAUNCHDHOOK_PATCHED,
                           dual_code_directories=True,
                           tc_has_sha256_over_sha1=True)
    rc, out = run(TAR_SCRIPT, [str(dual_wrong)], bin_dir)
    check("tar: a SHA-256-over-SHA-1-cdhash entry is rejected", rc == 1, out)
    check("tar: the SHA-256-over-SHA-1 entry is named as an orphan",
          "matches no slice the tar ships" in out, out)

    # Right algorithm, wrong slot: the 0x0 blob's own cdhash is a correct SHA-1 digest of a
    # real CodeDirectory, but the slice's cdhash is the 0x1000 alternate's.  Only the slot
    # choice separates this case from the passing one, so it pins the preference.
    dual_primary = build_tar(root / "dual-primary", healthy_dylibs(),
                             launchdhook_strings=LAUNCHDHOOK_PATCHED,
                             dual_code_directories=True,
                             tc_has_sha1_primary_slot=True)
    rc, out = run(TAR_SCRIPT, [str(dual_primary)], bin_dir)
    check("tar: a dual-CodeDirectory entry taken from the 0x0 slot is rejected", rc == 1, out)

    # launchdhook is the one member whose absence leaves every other gate content: it is
    # built from the same common.c, and the stock build writes only DYLD_INSERT_LIBRARIES.
    # This is the state the device was actually in, so both directions are pinned.
    stock_hook = build_tar(root / "stock-launchdhook", healthy_dylibs(),
                           launchdhook_strings=LAUNCHDHOOK_STOCK)
    rc, out = run(TAR_SCRIPT, [str(stock_hook)], bin_dir)
    check("tar: a stock launchdhook fails", rc == 1, out)
    check("tar: stock launchdhook is explained",
          "does not set the search paths dyld reads" in out, out)

    missing_hook = build_tar(root / "no-launchdhook", healthy_dylibs())
    rc, out = run(TAR_SCRIPT, [str(missing_hook)], bin_dir)
    check("tar: omitting launchdhook fails", rc == 1, out)
    check("tar: omitting launchdhook names the file",
          "missing basebin/launchdhook.dylib" in out, out)

    for name in ALL_SIX:
        short = build_tar(root / f"omit-{name}", healthy_dylibs(), omit=(name,),
                          launchdhook_strings=LAUNCHDHOOK_PATCHED)
        rc, out = run(TAR_SCRIPT, [str(short)], bin_dir)
        check(f"tar: omitting {name} fails", rc == 1, out)
        check(f"tar: omitting {name} names the file", f"missing basebin/{name}" in out, out)

    # Both architectures lose a hook: an arm64 variant that regressed would
    # otherwise ship uncaught, because devices older than A12 run arm64.
    for name in (ARM64E_16, ARM64E_18, ARM64_16, ARM64_18):
        dylibs = healthy_dylibs()
        dylibs[name] = LEGACY
        lost = build_tar(root / f"lost-hook-{name}", dylibs,
                         launchdhook_strings=LAUNCHDHOOK_PATCHED)
        rc, out = run(TAR_SCRIPT, [str(lost)], bin_dir)
        check(f"tar: {name} without the new hooks fails", rc == 1, out)
        check(f"tar: {name} names the missing hook", f"does not define {OVERRIDABLE}" in out, out)

    extra_hooks = healthy_dylibs()
    extra_hooks[ARM64E_15] = PATCHED
    extra_hooks[ARM64_15] = PATCHED
    wrong15 = build_tar(root / "wrong15", extra_hooks, launchdhook_strings=LAUNCHDHOOK_PATCHED)
    rc, out = run(TAR_SCRIPT, [str(wrong15)], bin_dir)
    check("tar: hooks on either iOS 15 dylib fail", rc == 1, out)

    baked = build_tar(root / "with-gen", healthy_dylibs(), extra={"gen/dyld": "sym:x\n"},
                      launchdhook_strings=LAUNCHDHOOK_PATCHED)
    rc, out = run(TAR_SCRIPT, [str(baked)], bin_dir)
    check("tar: a baked-in gen/dyld fails", rc == 1, out)
    check("tar: baked-in gen/dyld is explained", "belongs to jbctl update basebin" in out, out)

    stripped = build_tar(root / "no-prefix", healthy_dylibs(), prefixed=False,
                         launchdhook_strings=LAUNCHDHOOK_PATCHED)
    rc, out = run(TAR_SCRIPT, [str(stripped)], bin_dir)
    check("tar: a tar without the basebin/ prefix fails", rc == 1, out)

    no_tc = build_tar(root / "no-tc", healthy_dylibs())
    unpacked = root / "no-tc-unpacked"
    unpacked.mkdir()
    with tarfile.open(no_tc) as tf:
        tf.extractall(unpacked)
    (unpacked / "basebin" / "basebin.tc").unlink()
    retarred = root / "retarred.tar"
    with tarfile.open(retarred, "w") as tf:
        tf.add(unpacked, arcname=".")
    rc, out = run(TAR_SCRIPT, [str(retarred)], bin_dir)
    check("tar: a tar without basebin.tc fails", rc == 1, out)
    check("tar: a missing basebin.tc is named", "missing basebin/basebin.tc" in out, out)

    rc, out = run(TAR_SCRIPT, [str(root / "absent.tar")], bin_dir)
    check("tar: a missing file is rejected", rc == 1, out)


MERGER_STUB = r'''#!/bin/sh
symbols_file="$1"
cat "$2" > "$3.placeholder" 2>/dev/null || true
cp "$symbols_file" "$3"
'''


def make_merger(path: Path, symbols: list[str], exit_code: int = 0, message: str = "") -> Path:
    syms = path.parent / f"{path.name}.symbols"
    write_symbol_file(syms, symbols)
    path.write_text(
        "#!/bin/sh\n"
        f'if [ {exit_code} -ne 0 ]; then echo "{message}"; exit {exit_code}; fi\n'
        f'cp "{syms}" "$3"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_merge_script(root: Path, bin_dir: Path) -> None:
    work = root / "merge"
    work.mkdir()
    hook = work / "dyldhook.dylib"
    write_symbol_file(hook, PATCHED)
    input_dyld = work / "dyld.inflight"
    write_symbol_file(input_dyld, ["_some_dyld_symbol"])

    good_merger = make_merger(work / "merger-good", PATCHED)
    rc, out = run(MERGE_SCRIPT, [str(good_merger), str(input_dyld), str(hook)], bin_dir)
    check("merge: a merger that keeps all four hooks passes", rc == 0, out)
    for sym in (OVERRIDABLE, ALWAYS_OVERRIDABLE, DLOPEN_FROM, FCNTL):
        check(f"merge: passing output reports {sym}", sym in out, out)

    for lost in (OVERRIDABLE, DLOPEN_FROM, FCNTL):
        kept = [s for s in PATCHED if s != lost]
        merger = make_merger(work / f"merger-lost-{abs(hash(lost))}", kept)
        rc, out = run(MERGE_SCRIPT, [str(merger), str(input_dyld), str(hook)], bin_dir)
        check(f"merge: output without {lost} fails", rc == 1, out)
        check(f"merge: output without {lost} names it", f"does not carry {lost}" in out, out)

    contaminated = work / "dyld.contaminated"
    write_symbol_file(contaminated, PATCHED)
    rc, out = run(MERGE_SCRIPT, [str(good_merger), str(contaminated), str(hook)], bin_dir)
    check("merge: an input dyld that already has the hooks fails", rc == 1, out)

    broken = make_merger(
        work / "merger-broken", [], exit_code=255,
        message="Cannot resolve symbol _ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc",
    )
    rc, out = run(MERGE_SCRIPT, [str(broken), str(input_dyld), str(hook)], bin_dir)
    check("merge: a merger that cannot run exits 3, not 0 and not 1", rc == 3, out)
    check("merge: a failed merge repeats the merger's message", "Cannot resolve symbol" in out, out)

    rc, out = run(MERGE_SCRIPT, [str(good_merger), str(input_dyld)], bin_dir)
    check("merge: wrong argument count is rejected", rc == 1, out)


def main() -> int:
    for script in (TAR_SCRIPT, MERGE_SCRIPT):
        if not script.is_file():
            print(f"missing script: {script}", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory(prefix="verify-artifacts-") as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        nm = bin_dir / "llvm-nm"
        nm.write_text(NM_STUB, encoding="utf-8")
        nm.chmod(nm.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        test_tar_script(root, bin_dir)
        test_merge_script(root, bin_dir)

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
