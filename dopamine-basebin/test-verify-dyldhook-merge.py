#!/usr/bin/env python3
"""Regression tests for dopamine-basebin/verify-dyldhook-merge.sh.

The script's job is to fail when a built dyldhook dylib or a merged gen/dyld does not carry the two
framework-override hooks.  The case that matters most is the one where it must not pass silently:
a file whose name matches no profile used to fall through every branch and print PASS on the stock
dyld, which is exactly the artifact the mission asks to inspect.

Each case writes a fake llvm-nm first on PATH, so the test pins the classification and the
assertions without needing LLVM, Mach-O objects, or a device.  The stub answers with a symbol list
per file name, so a case states the symbols its fixture has and the expected verdict.

Usage: python3 dopamine-basebin/test-verify-dyldhook-merge.py
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "verify-dyldhook-merge.sh"

OVERRIDABLE = "_MACHOMERGER_HOOK__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc"
ALWAYS_OVERRIDABLE = "_MACHOMERGER_HOOK__ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc"
DLOPEN_FROM = "_MACHOMERGER_HOOK__ZN5dyld44APIs11dlopen_fromEPKciPv"
FCNTL = "_MACHOMERGER_HOOK___fcntl"

LEGACY = [DLOPEN_FROM, FCNTL]
OVERRIDE = [OVERRIDABLE, ALWAYS_OVERRIDABLE]

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS: {name}")
        return
    failures.append(name)
    print(f"FAIL: {name}" + (f" ({detail})" if detail else ""))


# llvm-nm prints one line per symbol; the script reads `-g` (external symbols) and `-u`
# (undefined symbols).  The stub decides from the file name plus an embedded symbol list, so the
# fixtures are plain text files and no Mach-O is needed.
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


def write_fixture(path: Path, symbols: list[str], undefined: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"sym:{sym}" for sym in symbols]
    lines += [f"undef:{sym}" for sym in undefined or []]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_verify(bin_dir: Path, target: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        ["sh", str(SCRIPT), str(target)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    if not SCRIPT.is_file():
        print(f"missing script: {SCRIPT}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="verify-dyldhook-") as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        nm = bin_dir / "llvm-nm"
        nm.write_text(NM_STUB, encoding="utf-8")
        nm.chmod(nm.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        patched = LEGACY + OVERRIDE

        # --- single file: a merged gen/dyld that was built from the patched dyldhook
        good_dyld = root / "gen" / "dyld"
        write_fixture(good_dyld, patched)
        rc, out = run_verify(bin_dir, good_dyld)
        check("patched gen/dyld passes", rc == 0, out)
        check("patched gen/dyld asserts both new hooks", OVERRIDABLE in out and ALWAYS_OVERRIDABLE in out)

        # --- single file: the stock dyld, the regression this test exists for
        stock_dyld = root / "stock" / "dyld"
        write_fixture(stock_dyld, LEGACY)
        rc, out = run_verify(bin_dir, stock_dyld)
        check("stock gen/dyld fails instead of passing silently", rc == 1, out)
        check("stock gen/dyld names the missing isOverridablePath", f"does not define {OVERRIDABLE}" in out, out)
        check(
            "stock gen/dyld names the missing isAlwaysOverridablePath",
            f"does not define {ALWAYS_OVERRIDABLE}" in out,
            out,
        )

        # --- single file: an unrecognised name must not silently skip the assertions
        odd_name = root / "odd" / "something-else"
        write_fixture(odd_name, LEGACY)
        rc, out = run_verify(bin_dir, odd_name)
        check("unrecognised file name still asserts the new hooks", rc == 1, out)

        # --- directory: the three arm64e targets the Makefile lists
        patched_dir = root / "patched"
        write_fixture(patched_dir / "dyldhook_merge.arm64e.iOS15.dylib", LEGACY)
        write_fixture(patched_dir / "dyldhook_merge.arm64e.iOS16-17.dylib", patched)
        write_fixture(patched_dir / "dyldhook_merge.arm64e.iOS18+.dylib", patched)
        rc, out = run_verify(bin_dir, patched_dir)
        check("patched build directory passes", rc == 0, out)

        # --- directory: a stock build has no new hooks
        stock_dir = root / "stock-build"
        write_fixture(stock_dir / "dyldhook_merge.arm64e.iOS15.dylib", LEGACY)
        write_fixture(stock_dir / "dyldhook_merge.arm64e.iOS16-17.dylib", LEGACY)
        write_fixture(stock_dir / "dyldhook_merge.arm64e.iOS18+.dylib", LEGACY)
        rc, out = run_verify(bin_dir, stock_dir)
        check("stock build directory fails", rc == 1, out)
        check(
            "stock build directory reports both 16-17 hooks missing",
            out.count(f"does not define {OVERRIDABLE}") >= 2,
            out,
        )

        # --- directory: a missing target fails even when the others are right
        short_dir = root / "short"
        write_fixture(short_dir / "dyldhook_merge.arm64e.iOS16-17.dylib", patched)
        rc, out = run_verify(bin_dir, short_dir)
        check("missing iOS15 and iOS18+ targets fail", rc == 1, out)
        check("missing iOS15 target is named", "missing dyldhook_merge.arm64e.iOS15.dylib" in out, out)

        # --- directory: hooks on the iOS 15 target are wrong, that dyld has no such function
        wrong15_dir = root / "wrong15"
        write_fixture(wrong15_dir / "dyldhook_merge.arm64e.iOS15.dylib", patched)
        write_fixture(wrong15_dir / "dyldhook_merge.arm64e.iOS16-17.dylib", patched)
        write_fixture(wrong15_dir / "dyldhook_merge.arm64e.iOS18+.dylib", patched)
        rc, out = run_verify(bin_dir, wrong15_dir)
        check("hooks on the iOS15 target fail", rc == 1, out)
        check("iOS15 case explains why", "the iOS 15 dyld has no such function" in out, out)

        # --- undefined symbols fail the self-contained check
        undef_dyld = root / "undef" / "dyld"
        write_fixture(undef_dyld, patched, undefined=["_strcmp"])
        rc, out = run_verify(bin_dir, undef_dyld)
        check("undefined symbol fails the self-contained check", rc == 1, out)
        check("undefined symbol is included in the report", "_strcmp" in out, out)

        # --- losing a legacy hook fails
        lost_fcntl = root / "lost" / "dyld"
        write_fixture(lost_fcntl, [OVERRIDABLE, ALWAYS_OVERRIDABLE, DLOPEN_FROM])
        rc, out = run_verify(bin_dir, lost_fcntl)
        check("losing __fcntl fails", rc == 1, out)
        check("lost __fcntl is named", f"lost {FCNTL}" in out, out)

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
