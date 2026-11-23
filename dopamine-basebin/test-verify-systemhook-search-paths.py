#!/usr/bin/env python3
"""Regression tests for dopamine-basebin/verify-systemhook-search-paths.py.

The check exists to fail when a built systemhook.dylib does not carry the four search-path
variables, or when the patch dropped the DYLD_INSERT_LIBRARIES write Dopamine already relied on.
The case that matters most is the stock dylib: it must not pass, because a patch hunk that never
landed is exactly what a silent pass would hide.

Each case builds its own Mach-O in memory, so nothing here needs clang, LLVM, or a device.  The
fixtures are written by encoding a minimal but valid 64-bit image: a mach header, one
__TEXT segment holding a __cstring section, and a symtab-free body.

Usage: python3 dopamine-basebin/test-verify-systemhook-search-paths.py
"""
from __future__ import annotations

import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "verify-systemhook-search-paths.py"

LC_SEGMENT_64 = 0x19
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64E = 2
MH_EXECUTE_DYLIB = 6
MH_DYLIB = 6

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


def build_macho(strings: tuple[str, ...], arch: str = "arm64e") -> bytes:
    """Encode a thin 64-bit Mach-O whose __cstring section holds the given strings."""
    payload = b"\0".join(s.encode() for s in strings) + b"\0"
    cstring_offset = 0x1000
    header_size = 32
    segment_cmdsize = 72 + 80  # segment_command_64 plus one section_64
    total_file_size = cstring_offset + len(payload)

    cputype = CPU_TYPE_ARM64
    cpusubtype = CPU_SUBTYPE_ARM64E if arch == "arm64e" else 0

    header = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,  # MH_MAGIC_64 in host byte order, little endian on disk
        cputype,
        cpusubtype,
        MH_DYLIB,
        1,  # ncmds
        segment_cmdsize,  # sizeofcmds
        0,  # flags
        0,  # reserved
    )
    segment = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64,
        segment_cmdsize,
        b"__TEXT",
        0,  # vmaddr
        total_file_size,  # vmsize
        0,  # fileoff
        total_file_size,  # filesize
        5,  # maxprot r-x
        5,  # initprot r-x
        1,  # nsects
        0,  # flags
    )
    section = struct.pack(
        "<16s16sQQIIIIIIII",
        b"__cstring",
        b"__TEXT",
        0,  # addr
        len(payload),  # size
        cstring_offset,  # offset
        2,  # align
        0,  # reloff
        0,  # nreloc
        2,  # flags (S_CSTRING_LITERALS)
        0,
        0,
        0,
    )
    body = header + segment + section
    body += b"\0" * (cstring_offset - len(body))
    return body + payload


def build_fat(slices: list[bytes]) -> bytes:
    """Wrap slices in a fat header so the universal build is exercised too."""
    alignment = 0x1000
    header_size = 8 + 20 * len(slices)
    offsets = []
    cursor = header_size
    for slice_bytes in slices:
        cursor = (cursor + alignment - 1) // alignment * alignment
        offsets.append(cursor)
        cursor += len(slice_bytes)

    out = struct.pack(">II", 0xCAFEBABE, len(slices))
    for slice_bytes, offset in zip(slices, offsets):
        cputype, cpusubtype = struct.unpack("<ii", slice_bytes[4:12])
        out += struct.pack(">iiIII", cputype, cpusubtype, offset, len(slice_bytes), 12)

    out += b"\0" * (offsets[0] - len(out))
    for index, slice_bytes in enumerate(slices):
        if index:
            out += b"\0" * (offsets[index] - len(out))
        out += slice_bytes
    return out


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

        # --- the patched dylib passes and names every variable with its directory
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

        # --- variables present but pointing at the wrong directory must fail: the whole point of
        # the dedicated paths is that /var/jb/usr/lib is never named.
        wrong_dir = root / "wrong-dir" / "systemhook.dylib"
        wrong_dir.parent.mkdir(parents=True)
        wrong_dir.write_bytes(build_macho(REQUIRED + LEGACY + (FRAMEWORK_DIR, "/usr/lib")))
        rc, out = run_verify(wrong_dir)
        check("a library path pointing at the shared dir fails", rc == 1, out)
        check(
            "the wrong library path is named",
            f"FAIL: arm64e: __XPC_DYLD_LIBRARY_PATH names {LIBRARY_DIR}" in out,
            out,
        )

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

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
