"""Encode minimal but valid Mach-O images for the verifier regression tests.

Both test suites need a dylib whose __cstring section holds a chosen set of strings, and
neither may require clang, an iPhoneOS SDK or a device.  The encoders live here so the two
suites build the same image shape: a mach header, one __TEXT segment carrying a single
__cstring section, and a body padded to the section's file offset.

Nothing here is a substitute for a real build.  What it buys is that
verify-systemhook-search-paths.py can be driven over every accept and refuse case, including
the stock dylib that must fail, using only python3.
"""
from __future__ import annotations

import struct

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64E = 2
MH_DYLIB = 6
LC_SEGMENT_64 = 0x19

CSTRING_OFFSET = 0x1000


def build_macho(strings: tuple[str, ...], arch: str = "arm64e") -> bytes:
    """Encode a thin 64-bit Mach-O whose __cstring section holds the given strings."""
    payload = b"\0".join(s.encode() for s in strings) + b"\0"
    header_size = 32
    segment_cmdsize = 72 + 80  # segment_command_64 plus one section_64
    total_file_size = CSTRING_OFFSET + len(payload)

    cpusubtype = CPU_SUBTYPE_ARM64E if arch == "arm64e" else 0

    header = struct.pack(
        "<IiiIIIII",
        MH_MAGIC_64,
        CPU_TYPE_ARM64,
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
        CSTRING_OFFSET,  # offset
        2,  # align
        0,  # reloff
        0,  # nreloc
        2,  # flags (S_CSTRING_LITERALS)
        0,
        0,
        0,
    )
    body = header + segment + section
    body += b"\0" * (CSTRING_OFFSET - len(body))
    return body + payload


def build_fat(slices: list[bytes]) -> bytes:
    """Wrap slices in a fat header so a universal build is exercised too."""
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
