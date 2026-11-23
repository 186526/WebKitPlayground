#!/usr/bin/env python3
"""Assert that a built systemhook.dylib carries the jailbreak search-path variables.

Dopamine 3.0.13's systemhook sets only DYLD_INSERT_LIBRARIES, so dyld is never told where a
WebKit built outside the shared cache lives.  The patch in this directory adds four variables;
this script proves all four names are in the produced dylib, that both search directories are
there, and that the variable Dopamine already relied on is still there.

A search path is a directory that dyld appends a relative name to, and which name it appends
depends on the install name.  The layout the patch expects is therefore two directories:

  /var/jb/Library/WebKitOverride/            <- DYLD_FRAMEWORK_PATH
      WebKit.framework/WebKit
      WebCore.framework/WebCore
      JavaScriptCore.framework/JavaScriptCore
      WebGPU.framework/WebGPU
      WebKitLegacy.framework/WebKitLegacy
  /var/jb/Library/WebKitOverride/usr/lib/    <- DYLD_LIBRARY_PATH
      libwebrtc.dylib
      libANGLE-shared.dylib

The second pair is a bare file name, not WebCore.framework/Frameworks/<leaf>.  Those two dylibs
carry the install name /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/<leaf>, and
getFrameworkPartialPath returns NULL for it because the directory before .framework is
"Frameworks", not the leaf name.  dyld then falls back to the library branch and appends only the
leaf, so the file must sit at the top of the library directory.

The values must not point at /var/jb/usr/lib: it shares liblzma.5.dylib and libmrc.dylib leaf
names with the shared cache, and every process would treat those as candidates.

What a string dump can and cannot show: it proves the four names and the two directories are
present, and it proves none of the shared roots (/Library/Frameworks, /usr/lib and their /var/jb
forms) appears as a whole entry, but it cannot pair a name with its value.  In a real build the
four names sit in one run of __cstring and the two directories in another, because the patch
stores them as an array of {name, value} whose value fields are the run-time pointers
jbFrameworkPath and jbLibraryPath -- the directory literals themselves are the JBROOT_PATH
argument strings elsewhere in the same section.  Pairing therefore has to be checked where the
pairs are written, in common.c; logic-harness/run.py does that by driving the extracted code
against a stock envbuf.c and asserting which variable receives which directory.

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

FRAMEWORK_DIR = "/Library/WebKitOverride"
LIBRARY_DIR = "/Library/WebKitOverride/usr/lib"

# The four variables the patch writes, each with the exact directory it must name.  The value is
# stored as its suffix under the jailbreak root, because JBROOT_PATH prefixes it at run time.
REQUIRED_STRINGS = (
    "DYLD_FRAMEWORK_PATH",
    "__XPC_DYLD_FRAMEWORK_PATH",
    "DYLD_LIBRARY_PATH",
    "__XPC_DYLD_LIBRARY_PATH",
)
# The two directories the array must name, in either order.  Pairing a name with its directory
# is checked in logic-harness/run.py; see the module docstring for why a string dump cannot.
EXPECTED_DIRS = (FRAMEWORK_DIR, LIBRARY_DIR)
# Directory strings a search-path value must never be.  These are the shared roots the dedicated
# directories exist to avoid: /Library/Frameworks holds other packages' frameworks, and /usr/lib
# shares liblzma.5.dylib and libmrc.dylib leaf names with the shared cache.  The /var/jb/ forms are
# listed as well so a patch that hardcodes the resolved root instead of using JBROOT_PATH fails
# here rather than at run time.  Matched as whole entries, so a legitimate path such as
# /usr/lib/systemhook.dylib does not trip them.
FORBIDDEN_STRINGS = (
    "/Library/Frameworks",
    "/usr/lib",
    "/var/jb/Library/Frameworks",
    "/var/jb/usr/lib",
)
PRESERVED_STRINGS = ("DYLD_INSERT_LIBRARIES",)


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
        # Exact NUL-delimited entries, not substrings: DYLD_FRAMEWORK_PATH is a suffix of
        # __XPC_DYLD_FRAMEWORK_PATH, so a substring test would find the bare name in the
        # prefixed one and miss a dropped variable.
        entries = {chunk.decode("utf-8", "replace") for chunk in blobs.split(b"\0") if chunk}
        for wanted in REQUIRED_STRINGS:
            check(f"{arch}: contains {wanted}", wanted in entries)
        for wanted in PRESERVED_STRINGS:
            check(f"{arch}: keeps {wanted}", wanted in entries)
        for wanted in EXPECTED_DIRS:
            check(f"{arch}: contains the directory {wanted}", wanted in entries)
        # The dedicated directories exist so the shared roots are never named.  Exact entries, so a
        # legitimate file path under /usr/lib is not mistaken for the directory itself.
        forbidden = [name for name in FORBIDDEN_STRINGS if name in entries]
        check(
            f"{arch}: names no shared search root",
            not forbidden,
            ", ".join(forbidden),
        )

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
