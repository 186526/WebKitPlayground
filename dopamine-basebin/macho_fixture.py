"""Encode minimal but valid Mach-O images for the verifier regression tests.

Both test suites need a dylib whose __cstring section holds a chosen set of strings, and
neither may require clang, an iPhoneOS SDK or a device.  The encoders live here so the two
suites build the same image shape: a mach header, one __TEXT segment carrying a single
__cstring section, and a body padded to the section's file offset.

Two later encoders exist for verify-basebin-trustcache.py: a Mach-O can carry an
LC_CODE_SIGNATURE superblob, and a trustcache file can be written from a set of cdhashes.
Together they let a test state "this member's cdhash is in basebin.tc" and "this one is not"
without a signed binary.

Nothing here is a substitute for a real build.  What it buys is that
verify-systemhook-search-paths.py can be driven over every accept and refuse case, including
the stock dylib that must fail, using only python3.
"""
from __future__ import annotations

import hashlib
import struct

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64E = 2
MH_DYLIB = 6
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D

CSTRING_OFFSET = 0x1000

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0

TRUSTCACHE_VERSION = 1
CDHASH_LEN = 20


def build_code_directory(payload: bytes) -> bytes:
    """Encode a CodeDirectory blob whose sha256 is the member's cdhash.

    Only the magic and the big-endian length matter to the hash: a real signature carries
    version, flags, identifier and per-page hashes, none of which the cdhash calculation
    reads separately.  The payload distinguishes one fixture's cdhash from another's.
    """
    body = payload
    return struct.pack(">II", CSMAGIC_CODEDIRECTORY, 8 + len(body)) + body


def build_superblob(code_directory: bytes) -> bytes:
    """Wrap one CodeDirectory in an embedded-signature superblob."""
    index_size = 12 + 8
    total = index_size + len(code_directory)
    index = struct.pack(">II", CSSLOT_CODEDIRECTORY, index_size)
    return struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, total, 1) + index + code_directory


def cdhash_of(code_directory: bytes) -> bytes:
    return hashlib.sha256(code_directory).digest()[:CDHASH_LEN]


def build_trustcache(cdhashes: list[bytes]) -> bytes:
    """Encode basebin/basebin.tc the way `trustcache create` lays it out."""
    out = struct.pack("<I16sI", TRUSTCACHE_VERSION, b"\x01" * 16, len(cdhashes))
    for cdhash in cdhashes:
        if len(cdhash) != CDHASH_LEN:
            raise ValueError("cdhash must be %d bytes" % CDHASH_LEN)
        out += cdhash + b"\x02\x00"
    return out


def build_macho(strings: tuple[str, ...], arch: str = "arm64e",
                code_directory: bytes | None = None) -> bytes:
    """Encode a thin 64-bit Mach-O whose __cstring section holds the given strings.

    When code_directory is given the image also carries an LC_CODE_SIGNATURE whose
    superblob wraps that CodeDirectory, which is what makes cdhash_of(code_directory)
    the image's cdhash.
    """
    payload = b"\0".join(s.encode() for s in strings) + b"\0"
    signature = build_superblob(code_directory) if code_directory is not None else b""

    segment_cmdsize = 72 + 80  # segment_command_64 plus one section_64
    signature_cmdsize = 16 if signature else 0
    ncmds = 2 if signature else 1
    sizeofcmds = segment_cmdsize + signature_cmdsize

    signature_offset = CSTRING_OFFSET + len(payload)
    total_file_size = signature_offset + len(signature)

    cpusubtype = CPU_SUBTYPE_ARM64E if arch == "arm64e" else 0

    header = struct.pack(
        "<IiiIIIII",
        MH_MAGIC_64,
        CPU_TYPE_ARM64,
        cpusubtype,
        MH_DYLIB,
        ncmds,
        sizeofcmds,
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
    if signature:
        body += struct.pack("<IIII", LC_CODE_SIGNATURE, signature_cmdsize,
                            signature_offset, len(signature))
    body += b"\0" * (CSTRING_OFFSET - len(body))
    return body + payload + signature


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
