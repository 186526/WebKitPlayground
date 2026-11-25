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
CSSLOT_ALTERNATE_CODEDIRECTORIES = 0x1000

# Offset 37 of a CodeDirectory is its hashType. A dual-CodeDirectory signature puts a
# SHA-1 (hashType 1) blob at slot 0x0 and a SHA-256 (hashType 2) blob at slot 0x1000,
# and the cdhash of each is the digest that byte names.
HASH_TYPE_OFFSET = 37
HASH_TYPE_SHA1 = 1
HASH_TYPE_SHA256 = 2

TRUSTCACHE_VERSION = 1
CDHASH_LEN = 20
MINIMUM_CODE_DIRECTORY = 512


def build_code_directory(payload: bytes,
                         hash_type: int = HASH_TYPE_SHA256) -> bytes:
    """Encode a CodeDirectory blob with the given hashType at offset 37.

    The kernel picks the cdhash algorithm from that byte, so a fixture that wants a
    SHA-1 cdhash has to carry hashType 1 there. The fields between the header and
    that byte (version, flags, hashOffset, identOffset, slot counts, codeLimit,
    hashSize) are zero-filled: cdhash_of hashes the whole blob, and nothing reads
    them separately. The payload distinguishes one fixture from another.
    """
    # A real CodeDirectory is hundreds of bytes; padding to that size keeps the
    # fixture above the length any reader checks before trusting offset 37.
    total = max(MINIMUM_CODE_DIRECTORY, HASH_TYPE_OFFSET + 1)
    body = bytearray(total - 8)
    body[0:len(payload)] = payload
    body[HASH_TYPE_OFFSET - 8 - 1] = CDHASH_LEN  # hashSize, immediately before hashType
    body[HASH_TYPE_OFFSET - 8] = hash_type
    return struct.pack(">II", CSMAGIC_CODEDIRECTORY, total) + bytes(body)


def build_superblob(code_directories: list[tuple[int, bytes]]) -> bytes:
    """Wrap (slot, CodeDirectory) pairs in an embedded-signature superblob.

    A dual-CodeDirectory signature passes [(CSSLOT_CODEDIRECTORY, sha1_cd),
    (CSSLOT_ALTERNATE_CODEDIRECTORIES, sha256_cd)], which is the shape the patched
    launchdhook and systemhook ship.
    """
    index_size = 12 + 8 * len(code_directories)
    total = index_size + sum(len(cd) for _slot, cd in code_directories)
    index = b""
    cursor = index_size
    for slot, cd in code_directories:
        index += struct.pack(">II", slot, cursor)
        cursor += len(cd)
    out = struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, total, len(code_directories))
    out += index
    for _slot, cd in code_directories:
        out += cd
    return out


CDHASH_ALGORITHMS = {1: hashlib.sha1, 2: hashlib.sha256}


def cdhash_of(code_directory: bytes) -> bytes:
    """The cdhash the kernel computes: the algorithm comes from the hashType byte."""
    algorithm = CDHASH_ALGORITHMS[code_directory[HASH_TYPE_OFFSET]]
    return algorithm(code_directory).digest()[:CDHASH_LEN]


def build_trustcache(entries: list[tuple[bytes, int]]) -> bytes:
    """Encode basebin/basebin.tc the way `trustcache create` lays it out.

    Each entry is (cdhash, hash_type): the label is the chosen CodeDirectory's own
    hashType, so a SHA-1 cdhash carries 1 rather than a constant 2.
    """
    out = struct.pack("<I16sI", TRUSTCACHE_VERSION, b"\x01" * 16, len(entries))
    for cdhash, hash_type in entries:
        if len(cdhash) != CDHASH_LEN:
            raise ValueError("cdhash must be %d bytes" % CDHASH_LEN)
        out += cdhash + bytes([hash_type, 0])
    return out


def build_macho(strings: tuple[str, ...], arch: str = "arm64e",
                code_directory: bytes | None = None,
                code_directories: list[tuple[int, bytes]] | None = None) -> bytes:
    """Encode a thin 64-bit Mach-O whose __cstring section holds the given strings.

    code_directory signs the image with one CodeDirectory at slot 0x0.
    code_directories signs it with the given (slot, blob) list instead, which is how
    a dual-CodeDirectory image — SHA-1 at 0x0, SHA-256 at 0x1000 — gets built; passing
    both is an error.
    """
    if code_directory is not None and code_directories is not None:
        raise ValueError("pass code_directory or code_directories, not both")
    payload = b"\0".join(s.encode() for s in strings) + b"\0"
    if code_directories is not None:
        signature = build_superblob(code_directories)
    elif code_directory is not None:
        signature = build_superblob([(CSSLOT_CODEDIRECTORY, code_directory)])
    else:
        signature = b""

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
