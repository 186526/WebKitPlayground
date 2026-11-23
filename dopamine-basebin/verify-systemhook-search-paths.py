#!/usr/bin/env python3
"""Assert that a built systemhook.dylib carries the jailbreak search-path variables.

Dopamine 3.0.13's systemhook sets only DYLD_INSERT_LIBRARIES, so dyld is never told where a
WebKit built outside the shared cache lives.  The patch in this directory adds two variables;
this script proves they are in the produced dylib, that the one variable Dopamine already relied
on is still there, and that the patch did not start writing DYLD_LIBRARY_PATH.  A search path is
a directory dyld appends a leaf or framework name to, so the layout it expects is

  /var/jb/Library/WebKitOverride/WebKit.framework/WebKit
  /var/jb/Library/WebKitOverride/WebCore.framework/WebCore
  /var/jb/Library/WebKitOverride/JavaScriptCore.framework/JavaScriptCore
  /var/jb/Library/WebKitOverride/WebGPU.framework/WebGPU
  /var/jb/Library/WebKitOverride/WebKitLegacy.framework/WebKitLegacy

libwebrtc.dylib and libANGLE-shared.dylib keep the install name the system WebCore links against,
/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/<leaf>, and dyld resolves a
non-framework leaf from DYLD_LIBRARY_PATH only by appending it to the directory, which cannot
reach a nested path.  They ship as /var/jb/usr/lib/libwebrtc.dylib in the deb, and that location
is not on any search path this patch sets, so a WebCore built against the system name loads the
cached copies instead.  Leave them where the deb installs them; do not copy them under
WebKitOverride expecting them to be found.

The strings are read from the __cstring section of every architecture slice, by parsing the
Mach-O here rather than shelling out, so the script runs anywhere python3 does.  The undefined
symbol check is separate: systemhook/Makefile has no CHECK_SELF_CONTAINED rule (only
dyldhook/Makefile does), so undefined symbols are normal and are reported, and with
--baseline they are compared against a stock build to catch a symbol the patch introduced.

Usage:
  dopamine-basebin/verify-systemhook-search-paths.py <systemhook.dylib>
  dopamine-basebin/verify-systemhook-search-paths.py --baseline stock/systemhook.dylib new/systemhook.dylib
  dopamine-basebin/verify-systemhook-search-paths.py --self-contained new/systemhook.dylib

Exit status: 0 when every assertion holds, 1 otherwise.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# On disk a little-endian 64-bit image starts cf fa ed fe, so MH_MAGIC_64 is that byte order.
MH_MAGIC = b"\xce\xfa\xed\xfe"
MH_CIGAM = b"\xfe\xed\xfa\xce"
MH_MAGIC_64 = b"\xcf\xfa\xed\xfe"
MH_CIGAM_64 = b"\xfe\xed\xfa\xcf"
FAT_MAGIC = b"\xca\xfe\xba\xbe"
FAT_MAGIC_64 = b"\xca\xfe\xba\xbf"

LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19

# What the patch must have added, and the variable Dopamine already wrote.
REQUIRED_STRINGS = (
    "DYLD_FRAMEWORK_PATH",
    "__XPC_DYLD_FRAMEWORK_PATH",
)
PRESERVED_STRINGS = ("DYLD_INSERT_LIBRARIES",)
# A library path was considered and rejected: it adds one directory to every process's dylib
# search, and /var/jb/usr/lib shares the liblzma.5.dylib and libmrc.dylib leaf names with the
# shared cache.  The __XPC_ spelling appears nowhere in the stock source, so finding it in the
# built dylib is proof a library path was added.
FORBIDDEN_STRINGS = ("__XPC_DYLD_LIBRARY_PATH",)


def read_slices(data: bytes) -> list[bytes]:
    """Split a thin or universal file into its architecture slices."""
    magic = data[:4]
    if magic in (MH_MAGIC, MH_MAGIC_64, MH_CIGAM, MH_CIGAM_64):
        return [data]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack(">I", data[4:8])[0]
        entry_size = 32 if magic == FAT_MAGIC_64 else 20
        slices = []
        for index in range(count):
            start = 8 + index * entry_size
            entry = data[start : start + entry_size]
            if len(entry) < entry_size:
                raise ValueError("truncated fat header")
            if entry_size == 32:
                offset, size = struct.unpack(">QQ", entry[8:24])
            else:
                offset, size = struct.unpack(">II", entry[8:16])
            slices.append(data[offset : offset + size])
        return slices
    raise ValueError("not a Mach-O file")


def cstring_sections(slice_bytes: bytes) -> list[bytes]:
    """Return the raw contents of every __cstring section in one slice."""
    magic = slice_bytes[:4]
    if magic == MH_MAGIC_64:
        endian, is_64 = "<", True
    elif magic == MH_CIGAM_64:
        endian, is_64 = ">", True
    elif magic == MH_MAGIC:
        endian, is_64 = "<", False
    elif magic == MH_CIGAM:
        endian, is_64 = ">", False
    else:
        raise ValueError("slice is not a Mach-O image")

    ncmds = struct.unpack(endian + "I", slice_bytes[16:20])[0]
    offset = 32 if is_64 else 28
    contents: list[bytes] = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack(endian + "II", slice_bytes[offset : offset + 8])
        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack(endian + "I", slice_bytes[offset + 64 : offset + 68])[0]
            sect = offset + 72
            for _ in range(nsects):
                sectname = slice_bytes[sect : sect + 16].rstrip(b"\0")
                # section_64 puts size (uint64) at +40 and offset (uint32) at +48.
                size = struct.unpack(endian + "Q", slice_bytes[sect + 40 : sect + 48])[0]
                file_offset = struct.unpack(endian + "I", slice_bytes[sect + 48 : sect + 52])[0]
                if sectname == b"__cstring":
                    contents.append(slice_bytes[file_offset : file_offset + size])
                sect += 80
        elif cmd == LC_SEGMENT:
            nsects = struct.unpack(endian + "I", slice_bytes[offset + 48 : offset + 52])[0]
            sect = offset + 56
            for _ in range(nsects):
                sectname = slice_bytes[sect : sect + 16].rstrip(b"\0")
                file_offset, size = struct.unpack(endian + "II", slice_bytes[sect + 40 : sect + 48])
                if sectname == b"__cstring":
                    contents.append(slice_bytes[file_offset : file_offset + size])
                sect += 68
        offset += cmdsize
    return contents


def arch_name(slice_bytes: bytes) -> str:
    cputype, cpusubtype = struct.unpack("<II", slice_bytes[4:12])
    if cputype == 0x0100000C:
        return "arm64e" if cpusubtype & 0xFF == 2 else "arm64"
    return f"cputype-{cputype:#x}"


def undefined_symbols(binary: Path) -> list[str] | None:
    """Undefined dynamic symbols via llvm-nm, or None when no nm is installed."""
    nm = shutil.which("llvm-nm") or shutil.which("nm")
    if nm is None:
        return None
    completed = subprocess.run([nm, "-u", str(binary)], capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    return sorted(line.strip() for line in completed.stdout.splitlines() if line.strip())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path, help="the built systemhook.dylib")
    parser.add_argument("--baseline", type=Path, help="stock systemhook.dylib to compare undefined symbols against")
    parser.add_argument(
        "--self-contained",
        action="store_true",
        help="also fail when any undefined symbol exists; systemhook/Makefile does not require this",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"PASS: {name}")
            return
        failures.append(name)
        print(f"FAIL: {name}" + (f" ({detail})" if detail else ""))

    if not args.binary.is_file():
        print(f"FAIL: {args.binary} is not a file", file=sys.stderr)
        return 1

    data = args.binary.read_bytes()
    try:
        slices = read_slices(data)
    except ValueError as error:
        print(f"FAIL: {args.binary}: {error}", file=sys.stderr)
        return 1

    check("file is Mach-O", bool(slices))
    for slice_bytes in slices:
        arch = arch_name(slice_bytes)
        try:
            blobs = b"\0".join(cstring_sections(slice_bytes))
        except ValueError as error:
            check(f"{arch}: cstring section is readable", False, str(error))
            continue
        if not blobs:
            check(f"{arch}: has a __cstring section", False, "no __cstring section in this slice")
            continue
        check(f"{arch}: has a __cstring section", True)
        for wanted in REQUIRED_STRINGS:
            check(f"{arch}: contains {wanted}", wanted.encode() in blobs)
        for wanted in PRESERVED_STRINGS:
            check(f"{arch}: keeps {wanted}", wanted.encode() in blobs)
        for unwanted in FORBIDDEN_STRINGS:
            check(f"{arch}: does not add {unwanted}", unwanted.encode() not in blobs)

    symbols = undefined_symbols(args.binary)
    if symbols is None:
        print("SKIP: undefined-symbol check (no llvm-nm or nm on PATH)")
    elif args.self_contained:
        check("dylib defines everything it references", not symbols, ", ".join(symbols[:5]))
    else:
        # systemhook/Makefile ships no CHECK_SELF_CONTAINED rule, so undefined symbols are
        # expected.  What the patch must not do is add one; --baseline is how that is checked.
        print(f"INFO: {len(symbols)} undefined symbol(s); systemhook/Makefile does not require none")
        if args.baseline is not None:
            baseline = undefined_symbols(args.baseline)
            if baseline is None:
                print("SKIP: baseline undefined-symbol comparison (no llvm-nm or nm on PATH)")
            else:
                added = sorted(set(symbols) - set(baseline))
                check("no undefined symbol added versus the baseline", not added, ", ".join(added[:5]))

    if failures:
        print(f"\n{len(failures)} assertion(s) failed", file=sys.stderr)
        return 1
    print("\nall assertions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
