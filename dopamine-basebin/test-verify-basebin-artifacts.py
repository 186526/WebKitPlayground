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

import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

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

ARM64E_15 = "dyldhook_merge.arm64e.iOS15.dylib"
ARM64E_16 = "dyldhook_merge.arm64e.iOS16-17.dylib"
ARM64E_18 = "dyldhook_merge.arm64e.iOS18+.dylib"
ALL_SIX = [
    ARM64E_15,
    "dyldhook_merge.arm64.iOS15.dylib",
    ARM64E_16,
    "dyldhook_merge.arm64.iOS16-17.dylib",
    ARM64E_18,
    "dyldhook_merge.arm64.iOS18+.dylib",
]

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
              omit: tuple[str, ...] = ()) -> Path:
    """Write a tar the way BaseBin/Makefile's basebin.tar target lays it out."""
    staging = root / "staging"
    base = staging / "basebin" if prefixed else staging
    base.mkdir(parents=True)
    for name, symbols in dylibs.items():
        if name in omit:
            continue
        write_symbol_file(base / name, symbols)
    (base / "basebin.tc").write_text("trustcache\n", encoding="utf-8")
    write_symbol_file(base / "systemhook.dylib", LEGACY)
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
        "dyldhook_merge.arm64.iOS15.dylib": LEGACY,
        ARM64E_16: PATCHED,
        "dyldhook_merge.arm64.iOS16-17.dylib": PATCHED,
        ARM64E_18: PATCHED_18,
        "dyldhook_merge.arm64.iOS18+.dylib": PATCHED_18,
    }


def test_tar_script(root: Path, bin_dir: Path) -> None:
    good = build_tar(root / "good", healthy_dylibs())
    rc, out = run(TAR_SCRIPT, [str(good)], bin_dir)
    check("tar: a healthy tar passes", rc == 0, out)
    check("tar: healthy tar states gen/dyld is absent by design",
          "jbctl update basebin generates it" in out, out)

    for name in ALL_SIX:
        short = build_tar(root / f"omit-{name}", healthy_dylibs(), omit=(name,))
        rc, out = run(TAR_SCRIPT, [str(short)], bin_dir)
        check(f"tar: omitting {name} fails", rc == 1, out)
        check(f"tar: omitting {name} names the file", f"missing basebin/{name}" in out, out)

    for name in (ARM64E_16, ARM64E_18):
        dylibs = healthy_dylibs()
        dylibs[name] = LEGACY
        lost = build_tar(root / f"lost-hook-{name}", dylibs)
        rc, out = run(TAR_SCRIPT, [str(lost)], bin_dir)
        check(f"tar: {name} without the new hooks fails", rc == 1, out)
        check(f"tar: {name} names the missing hook", f"does not define {OVERRIDABLE}" in out, out)

    extra_hooks = healthy_dylibs()
    extra_hooks[ARM64E_15] = PATCHED
    wrong15 = build_tar(root / "wrong15", extra_hooks)
    rc, out = run(TAR_SCRIPT, [str(wrong15)], bin_dir)
    check("tar: hooks on the iOS 15 dylib fail", rc == 1, out)

    undef = healthy_dylibs()
    baked = build_tar(root / "with-gen", healthy_dylibs(), extra={"gen/dyld": "sym:x\n"})
    rc, out = run(TAR_SCRIPT, [str(baked)], bin_dir)
    check("tar: a baked-in gen/dyld fails", rc == 1, out)
    check("tar: baked-in gen/dyld is explained", "belongs to jbctl update basebin" in out, out)

    stripped = build_tar(root / "no-prefix", healthy_dylibs(), prefixed=False)
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
