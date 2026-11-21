#!/usr/bin/env python3
# Writes an ad-hoc code signature onto a thin arm64/arm64e Mach-O dylib.
#
#   ./sign-adhoc.py SLICE [IDENTIFIER]
#
# The signature is the one ldid -S produces, minus the entitlements and the
# requirements blob: a SuperBlob holding one CodeDirectory, hashed with SHA-256
# over 4 KiB pages, flagged ad-hoc. It is appended past the end of __LINKEDIT,
# and __LINKEDIT is grown to cover it. LC_CODE_SIGNATURE is placed in the unused
# room between the existing load commands and the first section's data, so no
# offset inside the file moves.
#
# The SuperBlob index entry the signature starts with carries two numbers: a
# slot type and a blob offset. The slot type is 0 for the CodeDirectory
# (CSSLOT_CODEDIRECTORY), not the blob magic: dyld looks up slot 0 and skips the
# whole image, without a message, when the entry names another slot. The magic
# appears only inside the blob itself, at the offset the entry points to.
#
# The CodeDirectory also carries an executable segment record: execSegBase is
# where the code starts inside __TEXT, execSegLimit is how far it runs, and
# execSegFlags says how that range may be entered. dyld and the kernel hand
# this record to the memory system, and a limit of 0 leaves __TEXT outside it,
# so the very first instruction of the constructor dies with SIGBUS at __TEXT
# (KERN_PROTECTION_FAILURE) although the image loaded fine. It is 0 unless the
# signer says otherwise, which is what 1.0.0 through 1.0.5 shipped.
#
# The script exits non-zero when the input has no room for the load command, no
# __LINKEDIT or __TEXT, an existing signature, or is not a thin little-endian
# dylib.
import hashlib
import struct
import sys

LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0
SUPERBLOB_HEADER_SIZE = 12
SUPERBLOB_INDEX_SIZE = 8
SUPERBLOB_BLOBS_OFFSET = SUPERBLOB_HEADER_SIZE + SUPERBLOB_INDEX_SIZE
CS_ADHOC = 0x2
CD_VERSION = 0x20400
CD_HEADER_SIZE = 88
HASH_SIZE = 32
HASH_TYPE_SHA256 = 2
PAGE_SHIFT = 12
PAGE_SIZE = 1 << PAGE_SHIFT
SIGNATURE_ALIGN = 16
SEGMENT_ALIGN = 0x1000
MH_DYLIB = 6

MACH_HEADER_SIZE = 32
SEGMENT_COMMAND_SIZE = 72
SECTION_OFFSET_FIELD = 48
SEGMENT_VMADDR_FIELD = 24
SEGMENT_VMSIZE_FIELD = 32
SEGMENT_FILEOFF_FIELD = 40
SEGMENT_FILESIZE_FIELD = 48

# Byte offsets of the executable segment record inside the CodeDirectory
# header. execSegBase sits at 64, execSegLimit at 72, execSegFlags at 80.
CD_EXEC_SEG_BASE_OFFSET = 64
CD_EXEC_SEG_LIMIT_OFFSET = 72


def fail(message):
    sys.exit(f"sign-adhoc: {message}")


def align_up(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def segment_name(data, command_offset):
    return data[command_offset + 8:command_offset + 24].rstrip(b"\0").decode()


def load_commands(data, ncmds):
    commands = []
    offset = MACH_HEADER_SIZE
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", data, offset)
        commands.append((offset, command, size))
        offset += size
    return commands


def first_section_offset(data, commands):
    offsets = []
    for command_offset, command, _ in commands:
        if command != LC_SEGMENT_64:
            continue
        section_count = struct.unpack_from("<I", data, command_offset + 64)[0]
        for index in range(section_count):
            section = command_offset + SEGMENT_COMMAND_SIZE + index * 80
            offset = struct.unpack_from("<I", data, section + SECTION_OFFSET_FIELD)[0]
            if offset:
                offsets.append(offset)
    if not offsets:
        fail("the slice has no section with file data")
    return min(offsets)


def text_segment_size(data, commands, path):
    """Returns vmsize of __TEXT, the range the executable segment record covers.

    The record's base stays 0, so the limit has to be the whole segment: the
    kernel maps the code pages inside this range and refuses to execute a
    constructor that falls outside it."""
    for command_offset, command, _ in commands:
        if command == LC_SEGMENT_64 and segment_name(data, command_offset) == "__TEXT":
            return struct.unpack_from("<Q", data, command_offset + SEGMENT_VMSIZE_FIELD)[0]
    fail(f"{path} has no __TEXT segment")
    return 0


def check_signature(data, dataoff, superblob_length, code_directory_length, code_slots,
                    text_size):
    """Reads the signature back and refuses to write a file the device rejects.

    dyld finds the CodeDirectory by slot type 0 in the SuperBlob index. An
    entry that names another slot, which is what writing the blob magic here
    produces, makes dyld drop the whole image without printing anything.

    The executable segment record has to cover __TEXT. A limit of 0, which is
    what the field holds when nobody writes it, leaves the code pages outside
    the range the kernel will execute from, and the constructor dies with
    SIGBUS on its first instruction although dyld loaded the image."""
    magic, length, count = struct.unpack_from(">III", data, dataoff)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        fail(f"internal error: SuperBlob magic is {magic:#010x}")
    if length != superblob_length:
        fail(f"internal error: SuperBlob length is {length}, expected {superblob_length}")
    if count != 1:
        fail(f"internal error: SuperBlob holds {count} blobs, expected 1")
    slot_type, offset = struct.unpack_from(">II", data, dataoff + SUPERBLOB_HEADER_SIZE)
    if slot_type != CSSLOT_CODEDIRECTORY:
        fail(f"internal error: SuperBlob slot type is {slot_type:#x}, "
             f"expected {CSSLOT_CODEDIRECTORY}")
    blob = dataoff + offset
    blob_magic, blob_length = struct.unpack_from(">II", data, blob)
    if blob_magic != CSMAGIC_CODEDIRECTORY:
        fail(f"internal error: the blob in slot 0 has magic {blob_magic:#010x}")
    if blob_length != code_directory_length:
        fail(f"internal error: CodeDirectory length is {blob_length}, "
             f"expected {code_directory_length}")
    stored_slots = struct.unpack_from(">I", data, blob + 28)[0]
    if stored_slots != code_slots:
        fail(f"internal error: CodeDirectory lists {stored_slots} slots, "
             f"expected {code_slots}")
    exec_seg_base = struct.unpack_from(">Q", data, blob + CD_EXEC_SEG_BASE_OFFSET)[0]
    exec_seg_limit = struct.unpack_from(">Q", data, blob + CD_EXEC_SEG_LIMIT_OFFSET)[0]
    if exec_seg_base != 0:
        fail(f"internal error: execSegBase is {exec_seg_base:#x}, expected 0")
    if exec_seg_limit == 0:
        fail("internal error: execSegLimit is 0, so __TEXT would not be executable")
    if exec_seg_limit != text_size:
        fail(f"internal error: execSegLimit is {exec_seg_limit:#x}, "
             f"expected the __TEXT size {text_size:#x}")


def main():
    if len(sys.argv) < 2:
        fail("usage: sign-adhoc.py SLICE [IDENTIFIER]")
    path = sys.argv[1]
    identifier = sys.argv[2] if len(sys.argv) > 2 else path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    with open(path, "rb") as source:
        data = bytearray(source.read())

    magic, _, _, filetype, ncmds, sizeofcmds, _ = struct.unpack_from("<IiIIIII", data, 0)
    if magic != 0xFEEDFACF:
        fail(f"{path} is not a thin little-endian 64-bit Mach-O")
    if filetype != MH_DYLIB:
        fail(f"{path} is not a dylib (filetype {filetype})")

    commands = load_commands(data, ncmds)
    if any(command == LC_CODE_SIGNATURE for _, command, _ in commands):
        fail(f"{path} already carries LC_CODE_SIGNATURE")

    linkedit = next(
        (offset for offset, command, _ in commands
         if command == LC_SEGMENT_64 and segment_name(data, offset) == "__LINKEDIT"),
        None,
    )
    if linkedit is None:
        fail(f"{path} has no __LINKEDIT segment")

    command_area = MACH_HEADER_SIZE + sizeofcmds
    if command_area + 16 > first_section_offset(data, commands):
        fail(f"{path} has no room for LC_CODE_SIGNATURE in its load commands")
    text_size = text_segment_size(data, commands, path)

    # Layout: the file keeps its bytes, is padded to the signature alignment,
    # and the signature follows. Everything before dataoff is hashed, so the
    # header and the load commands have to be final before the hashes are.
    dataoff = align_up(len(data), SIGNATURE_ALIGN)
    identifier_bytes = identifier.encode() + b"\0"
    hash_offset = CD_HEADER_SIZE + len(identifier_bytes)
    code_slots = (dataoff + PAGE_SIZE - 1) // PAGE_SIZE
    code_directory_length = hash_offset + code_slots * HASH_SIZE
    superblob_length = align_up(SUPERBLOB_BLOBS_OFFSET + code_directory_length, SIGNATURE_ALIGN)
    data.extend(b"\0" * (dataoff + superblob_length - len(data)))

    struct.pack_into("<IIII", data, command_area, LC_CODE_SIGNATURE, 16, dataoff, superblob_length)
    struct.pack_into("<II", data, 16, ncmds + 1, sizeofcmds + 16)

    # __LINKEDIT grows to cover the signature; no other segment is touched.
    fileoff = struct.unpack_from("<Q", data, linkedit + SEGMENT_FILEOFF_FIELD)[0]
    filesize = len(data) - fileoff
    struct.pack_into("<Q", data, linkedit + SEGMENT_FILESIZE_FIELD, filesize)
    struct.pack_into("<Q", data, linkedit + SEGMENT_VMSIZE_FIELD, align_up(filesize, SEGMENT_ALIGN))

    signature = dataoff
    struct.pack_into(">III", data, signature, CSMAGIC_EMBEDDED_SIGNATURE, superblob_length, 1)
    struct.pack_into(">II", data, signature + SUPERBLOB_HEADER_SIZE, CSSLOT_CODEDIRECTORY,
                     SUPERBLOB_BLOBS_OFFSET)
    header = struct.pack(
        ">IIIIIIIIIBBBBIIIIQQQQ",
        CSMAGIC_CODEDIRECTORY,
        code_directory_length,
        CD_VERSION,
        CS_ADHOC,
        hash_offset,
        CD_HEADER_SIZE,  # identOffset, the name follows the header
        0,  # nSpecialSlots
        code_slots,
        dataoff,  # codeLimit
        HASH_SIZE,
        HASH_TYPE_SHA256,
        0,  # platform
        PAGE_SHIFT,
        0,  # spare2
        0,  # scatterOffset
        0,  # teamOffset
        0,  # spare3
        0,  # codeLimit64
        0,  # execSegBase: the code starts at the front of __TEXT
        text_size,  # execSegLimit: the whole of __TEXT is executable
        0,  # execSegFlags: not the main binary
    )
    if len(header) != CD_HEADER_SIZE:
        fail(f"internal error: CodeDirectory header is {len(header)} bytes")
    code_directory_offset = dataoff + SUPERBLOB_BLOBS_OFFSET
    data[code_directory_offset:code_directory_offset + CD_HEADER_SIZE] = header
    data[code_directory_offset + CD_HEADER_SIZE:code_directory_offset + hash_offset] = identifier_bytes
    for slot in range(code_slots):
        start = slot * PAGE_SIZE
        digest = hashlib.sha256(bytes(data[start:min(start + PAGE_SIZE, dataoff)])).digest()
        offset = code_directory_offset + hash_offset + slot * HASH_SIZE
        data[offset:offset + HASH_SIZE] = digest
    if len(data) != dataoff + superblob_length:
        fail(f"internal error: file is {len(data)} bytes, expected {dataoff + superblob_length}")

    check_signature(data, dataoff, superblob_length, code_directory_length, code_slots, text_size)

    with open(path, "wb") as target:
        target.write(data)
    print(f"signed: {path} ({superblob_length} bytes of signature at {dataoff})")


if __name__ == "__main__":
    main()
