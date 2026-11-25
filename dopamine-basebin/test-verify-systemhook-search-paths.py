#!/usr/bin/env python3
"""Regression tests for dopamine-basebin/verify-systemhook-search-paths.py.

The check exists to fail when a built systemhook.dylib does not carry the four search-path
variables, or when the patch dropped the DYLD_INSERT_LIBRARIES write Dopamine already relied on.
The case that matters most is the stock dylib: it must not pass, because a patch hunk that never
landed is exactly what a silent pass would hide.

Each case builds its own Mach-O in memory via macho_fixture.py, the shared encoder both suites
use, so nothing here needs clang, LLVM, or a device.

The launchdhook cases exist because that binary is built from the same common.c and is the
interposer that runs inside launchd, the process that performs an app's final exec.  A tar that
ships a stock launchdhook passes every other gate and still leaves the device with an app whose
WebKit comes from the shared cache, so the stock image must fail here.

Usage: python3 dopamine-basebin/test-verify-systemhook-search-paths.py
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from macho_fixture import build_fat, build_macho  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "verify-systemhook-search-paths.py"

FRAMEWORK_DIR = "/Library/WebKitOverride"
LIBRARY_DIR = "/Library/WebKitOverride/usr/lib"

REQUIRED = (
    "DYLD_FRAMEWORK_PATH",
    "__XPC_DYLD_FRAMEWORK_PATH",
    "DYLD_LIBRARY_PATH",
    "__XPC_DYLD_LIBRARY_PATH",
)
LEGACY = ("DYLD_INSERT_LIBRARIES",)
# A patched dylib carries these directories; a stale one does not.
VALUES = (FRAMEWORK_DIR, LIBRARY_DIR)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS: {name}")
        return
    failures.append(name)
    print(f"FAIL: {name}" + (f" ({detail})" if detail else ""))


def run_verify(binary: Path, extra: list[str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(binary)] + (extra or []),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    if not SCRIPT.is_file():
        print(f"missing script: {SCRIPT}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="verify-systemhook-") as tmp:
        root = Path(tmp)

        # --- the patched dylib passes and carries every variable and both directories
        patched = root / "patched" / "systemhook.dylib"
        patched.parent.mkdir(parents=True)
        patched.write_bytes(build_macho(REQUIRED + LEGACY + VALUES))
        rc, out = run_verify(patched)
        check("patched dylib passes", rc == 0, out)
        check("patched dylib reports every variable", all(name in out for name in REQUIRED), out)
        check("patched dylib reports both directories", all(value in out for value in VALUES), out)

        # --- the stock dylib must fail; this is the case a silent pass would hide
        stock = root / "stock" / "systemhook.dylib"
        stock.parent.mkdir(parents=True)
        stock.write_bytes(build_macho(LEGACY))
        rc, out = run_verify(stock)
        check("stock dylib fails instead of passing silently", rc == 1, out)
        check("stock dylib names the missing framework path", "FAIL: arm64e: contains DYLD_FRAMEWORK_PATH" in out, out)
        check(
            "stock dylib names the missing xpc framework path",
            "FAIL: arm64e: contains __XPC_DYLD_FRAMEWORK_PATH" in out,
            out,
        )

        # --- a patch that adds the paths but drops the insert write regresses Dopamine itself
        lost_insert = root / "lost" / "systemhook.dylib"
        lost_insert.parent.mkdir(parents=True)
        lost_insert.write_bytes(build_macho(REQUIRED + VALUES))
        rc, out = run_verify(lost_insert)
        check("losing DYLD_INSERT_LIBRARIES fails", rc == 1, out)
        check("lost DYLD_INSERT_LIBRARIES is named", "FAIL: arm64e: keeps DYLD_INSERT_LIBRARIES" in out, out)

        # --- one variable short is still a failure, so a single dropped hunk is caught.
        # Each of the four is dropped in turn, which is the reverse of the pass case above.
        for index, missing in enumerate(REQUIRED):
            short = root / f"short-{index}" / "systemhook.dylib"
            short.parent.mkdir(parents=True)
            short.write_bytes(build_macho(tuple(v for v in REQUIRED if v != missing) + LEGACY + VALUES))
            rc, out = run_verify(short)
            check(f"a missing {missing} fails", rc == 1, out)
            check(
                f"the missing {missing} is named",
                f"FAIL: arm64e: contains {missing}" in out,
                out,
            )

        # --- naming a shared root must fail even when both dedicated directories are present.
        # This is the case a positive-only check let through: all four names and both dedicated
        # directories in place, plus /Library/Frameworks and /usr/lib, and it exited 0.
        shared_roots = root / "shared-roots" / "systemhook.dylib"
        shared_roots.parent.mkdir(parents=True)
        shared_roots.write_bytes(
            build_macho(REQUIRED + LEGACY + VALUES + ("/Library/Frameworks", "/usr/lib"))
        )
        rc, out = run_verify(shared_roots)
        check("naming a shared root fails", rc == 1, out)
        check(
            "the shared roots are named in the failure",
            "FAIL: arm64e: names no shared search root" in out,
            out,
        )

        # --- a legitimate file path under /usr/lib must not be mistaken for the directory itself
        file_under_shared = root / "file-under-shared" / "systemhook.dylib"
        file_under_shared.parent.mkdir(parents=True)
        file_under_shared.write_bytes(
            build_macho(REQUIRED + LEGACY + VALUES + ("/usr/lib/systemhook.dylib",))
        )
        rc, out = run_verify(file_under_shared)
        check("a file path under /usr/lib does not trip the root check", rc == 0, out)

        # --- a universal build is checked slice by slice
        universal = root / "universal" / "systemhook.dylib"
        universal.parent.mkdir(parents=True)
        universal.write_bytes(
            build_fat(
                [
                    build_macho(REQUIRED + LEGACY + VALUES, "arm64e"),
                    build_macho(REQUIRED + LEGACY + VALUES, "arm64"),
                ]
            )
        )
        rc, out = run_verify(universal)
        check("universal build with both slices patched passes", rc == 0, out)
        check("both slices are reported", "arm64e:" in out and "arm64:" in out, out)

        # --- the arm64 slice alone missing the variables must fail the universal build
        half = root / "half" / "systemhook.dylib"
        half.parent.mkdir(parents=True)
        half.write_bytes(
            build_fat(
                [
                    build_macho(REQUIRED + LEGACY + VALUES, "arm64e"),
                    build_macho(LEGACY, "arm64"),
                ]
            )
        )
        rc, out = run_verify(half)
        check("a universal build with one stale slice fails", rc == 1, out)

        # --- something that is not a Mach-O is rejected rather than skipped
        junk = root / "junk" / "systemhook.dylib"
        junk.parent.mkdir(parents=True)
        junk.write_bytes(b"this is not a Mach-O\n")
        rc, out = run_verify(junk)
        check("a non Mach-O file is rejected", rc == 1, out)

        # --- a missing path is an error, not a pass
        rc, out = run_verify(root / "does-not-exist.dylib")
        check("a missing file is rejected", rc == 1, out)

        # --- --self-contained is opt-in; systemhook ships no such rule
        rc, out = run_verify(patched, ["--self-contained"])
        check("--self-contained runs without nm and does not crash", rc in (0, 1), out)

        # --- launchdhook, built from the same common.c and the interposer inside launchd.
        # The patched image passes, the stock one fails, and the two differ only in the
        # search-path names, so the gate cannot pass on a tar that ships the stock binary.
        patched_hook = root / "launchdhook-patched" / "launchdhook.dylib"
        patched_hook.parent.mkdir(parents=True)
        patched_hook.write_bytes(build_macho(REQUIRED + LEGACY + VALUES))
        rc, out = run_verify(patched_hook, ["--kind", "launchdhook"])
        check("patched launchdhook passes", rc == 0, out)
        check(
            "patched launchdhook reports every variable",
            all(name in out for name in REQUIRED),
            out,
        )

        stock_hook = root / "launchdhook-stock" / "launchdhook.dylib"
        stock_hook.parent.mkdir(parents=True)
        # The stock launchdhook carries no search-path name at all; that absence is the defect.
        stock_hook.write_bytes(build_macho(LEGACY))
        rc, out = run_verify(stock_hook, ["--kind", "launchdhook"])
        check("stock launchdhook fails the gate", rc == 1, out)
        check(
            "stock launchdhook names the missing framework path",
            "FAIL: arm64e: contains DYLD_FRAMEWORK_PATH" in out,
            out,
        )

        # spawn_hook.c:36-37 statfs()es the bare /usr/lib, so launchdhook may carry that entry.
        hook_statfs = root / "launchdhook-statfs" / "launchdhook.dylib"
        hook_statfs.parent.mkdir(parents=True)
        hook_statfs.write_bytes(build_macho(REQUIRED + LEGACY + VALUES + ("/usr/lib",)))
        rc, out = run_verify(hook_statfs, ["--kind", "launchdhook"])
        check("launchdhook may carry the bare /usr/lib it statfs()es", rc == 0, out)

        # ...but the broader search roots stay forbidden for it, exactly as for systemhook.
        hook_broad = root / "launchdhook-broad" / "launchdhook.dylib"
        hook_broad.parent.mkdir(parents=True)
        hook_broad.write_bytes(
            build_macho(REQUIRED + LEGACY + VALUES + ("/usr/lib", "/Library/Frameworks"))
        )
        rc, out = run_verify(hook_broad, ["--kind", "launchdhook"])
        check("launchdhook still may not name /Library/Frameworks", rc == 1, out)

        # --- the excuse is scoped to launchdhook: systemhook may not carry the bare root
        hook_root = root / "systemhook-root" / "systemhook.dylib"
        hook_root.parent.mkdir(parents=True)
        hook_root.write_bytes(build_macho(REQUIRED + LEGACY + VALUES + ("/usr/lib",)))
        rc, out = run_verify(hook_root)
        check("systemhook still may not carry the bare /usr/lib", rc == 1, out)

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
