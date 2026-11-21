#!/usr/bin/env python3
# Reads a thin Mach-O slice and holds it to the properties the device checks
# before it runs the constructor.
#
#   ./check-slice.py SLICE EXPECTED_SUBTYPE
#
# EXPECTED_SUBTYPE is the four header bytes at offset 8 as od prints them,
# little-endian, so the arm64 slice is 00000000 and the arm64e one is 02000080
# (the 0x80000002 the linker writes, ARM64E | the pointer-authentication
# capability bits). It is the same spelling build.sh reads with od, so the two
# checks cannot drift into different byte orders.
#
#   LC_DYLD_CHAINED_FIXUPS   without it dyld writes nothing to fix up, and an
#                            image carrying LC_DYLD_INFO_ONLY instead is loaded
#                            with no initializer run
#   __init_offsets           the constructor lives there; in __mod_init_func
#                            instead, the device runs none of it
#   subtype                  an arm64 image stamped 0x80000002 makes dyld sign
#                            the pointers dlsym answers, which plain `br`
#                            faults on; an arm64e image stamped 0x0 is treated
#                            as arm64 and gets none of arm64e's guarantees
#   dyld names               the device exports _dyld_*; the symbol table name
#                            is __dyld_*, and one underscore too few or too many
#                            fails to bind
#
# This file reads the Mach-O structures directly rather than calling otool or
# objdump, because the device that links the arm64e slice carries neither:
# /var/jb/usr/bin has llvm-otool-16 and llvm-nm-16 but no llvm-objdump.
# Exits 0 when every property holds, non-zero with the failing one otherwise.
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022
LC_DYLD_CHAINED_FIXUPS = 0x80000034
MACH_HEADER_SIZE = 32

ARM64 = 0x0100000C

failures = []


def fail(message):
    failures.append(message)


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: check-slice.py SLICE EXPECTED_SUBTYPE")
    path = sys.argv[1]
    expected = sys.argv[2]
    try:
        expected_bytes = bytes.fromhex(expected)
    except ValueError:
        sys.exit(f"check-slice: expected subtype {expected!r} is not four hex bytes")
    if len(expected_bytes) != 4:
        sys.exit(f"check-slice: expected subtype {expected!r} is not four hex bytes")

    with open(path, "rb") as source:
        data = source.read()

    magic, cputype, cpusubtype, filetype, ncmds, _, _ = struct.unpack_from("<IiiIIII", data, 0)
    if magic != MH_MAGIC_64:
        sys.exit(f"check-slice: {path} is not a thin little-endian 64-bit Mach-O")
    if cputype != ARM64:
        sys.exit(f"check-slice: {path} is not an ARM64 image (cputype {cputype:#x})")

    if data[8:12] != expected_bytes:
        fail(f"cpusubtype is 0x{data[8:12].hex()}, expected 0x{expected}")

    commands = []
    offset = MACH_HEADER_SIZE
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", data, offset)
        commands.append((offset, command, size))
        offset += size

    has_chained = any(command == LC_DYLD_CHAINED_FIXUPS for _, command, _ in commands)
    has_dyld_info = any(command in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY) for _, command, _ in commands)
    if not has_chained:
        fail("LC_DYLD_CHAINED_FIXUPS is missing, so the device runs no initializer")
    if has_dyld_info:
        fail("LC_DYLD_INFO_ONLY is present; chained fixups are the format the device runs")

    found_init_offsets = False
    undefined_names = []
    for command_offset, command, _ in commands:
        if command == LC_SEGMENT_64:
            section_count = struct.unpack_from("<I", data, command_offset + 64)[0]
            for index in range(section_count):
                section = command_offset + 72 + index * 80
                sectname = data[section:section + 16].rstrip(b"\0").decode()
                size = struct.unpack_from("<Q", data, section + 40)[0]
                if sectname == "__init_offsets" and size > 0:
                    found_init_offsets = True
        elif command == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, command_offset + 8)
            for index in range(nsyms):
                entry = symoff + index * 16
                strx, ntype, _, _, _ = struct.unpack_from("<IBbhQ", data, entry)
                # N_UNDF is 0x0 and N_EXT is 0x1: an import of another image.
                if ntype & 0x0E or not ntype & 0x01:
                    continue
                end = data.index(b"\0", stroff + strx)
                undefined_names.append(data[stroff + strx:end].decode(errors="replace"))
    if not found_init_offsets:
        fail("__init_offsets is missing or empty, so the constructor is not registered")

    bad = [name for name in undefined_names
           if name.startswith("_dyld_") or name.startswith("___dyld_")]
    if bad:
        fail("the image refers to a dyld symbol name the device does not export, "
             "which has two leading underscores: " + ", ".join(bad))

    if failures:
        for message in failures:
            print(f"check-slice: {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
