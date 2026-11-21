#!/usr/bin/env python3
# Checks the signature sign-adhoc.py writes, on a Mach-O dylib this file
# builds, because the rest of the suite compiles C for the host and has no
# Mach-O to sign.
#
# dyld finds the CodeDirectory through the first SuperBlob index entry, which
# carries a slot type and an offset. The slot type is 0 (CSSLOT_CODEDIRECTORY);
# an entry that names any other slot makes dyld drop the image without a word,
# and 1.0.0 through 1.0.4 shipped exactly that. The assertions below read the
# written file with a parser of their own, so a regression in the writer is not
# hidden by the writer's own check.
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "sign-adhoc.py")

MACH_HEADER_SIZE = 32
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
MH_DYLIB = 6
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0
SUPERBLOB_HEADER_SIZE = 12

failures = []


def check(name, condition, detail=""):
    if condition:
        print("%-58s pass" % name)
    else:
        print("%-58s FAIL %s" % (name, detail))
        failures.append(name)


def segment(name, vmaddr, vmsize, fileoff, filesize, sections):
    body = b""
    for section_name, section_offset in sections:
        body += (section_name.encode().ljust(16, b"\0")[:16] +
                 b"__TEXT".ljust(16, b"\0") +
                 struct.pack("<QQIIIIIIII", vmaddr + section_offset, 8,
                             section_offset, 4, 0, 0, 0, 0, 0, 0))
    size = 72 + len(body)
    command = struct.pack("<II", LC_SEGMENT_64, size)
    command += name.encode().ljust(16, b"\0")[:16]
    command += struct.pack("<QQQQIIII", vmaddr, vmsize, fileoff, filesize,
                           5, 5, len(sections), 0)
    return command + body


def build_dylib(path):
    # One section with file data, so the signer can place LC_CODE_SIGNATURE in
    # the room between the load commands and the first section.
    text_offset = 0x200
    linkedit_offset = 0x400
    commands = [
        segment("__TEXT", 0, 0x4000, 0, 0x200, [("__text", text_offset)]),
        segment("__LINKEDIT", 0, 0x4000, linkedit_offset, 0x10, []),
    ]
    sizeofcmds = sum(len(command) for command in commands)
    header = struct.pack("<IiIIIIII", 0xFEEDFACF, 0x0100000C, 0x80000002,
                         MH_DYLIB, len(commands), sizeofcmds, 0, 0)
    data = bytearray(header + b"".join(commands))
    data.extend(b"\0" * (linkedit_offset - len(data)))
    data.extend(b"\0" * 0x10)
    with open(path, "wb") as target:
        target.write(data)
    return path


def code_signature(blob):
    count = struct.unpack_from("<I", blob, 16)[0]
    offset = MACH_HEADER_SIZE
    for _ in range(count):
        command, size = struct.unpack_from("<II", blob, offset)
        if command == LC_CODE_SIGNATURE:
            dataoff, length = struct.unpack_from("<II", blob, offset + 8)
            return dataoff, length
        offset += size
    return None, None


def superblob_entry(blob, dataoff):
    magic, length, count = struct.unpack_from(">III", blob, dataoff)
    slot_type, entry_offset = struct.unpack_from(">II", blob, dataoff + SUPERBLOB_HEADER_SIZE)
    return magic, length, count, slot_type, entry_offset


def main():
    with tempfile.TemporaryDirectory() as work:
        slice_path = build_dylib(os.path.join(work, "slice.dylib"))
        result = subprocess.run([sys.executable, SCRIPT, slice_path, "WebSafeWebKitFilter"],
                                capture_output=True, text=True)
        check("the signer accepts a thin dylib", result.returncode == 0, result.stderr.strip())
        if result.returncode != 0:
            return report()

        blob = open(slice_path, "rb").read()
        dataoff, length = code_signature(blob)
        check("the file carries LC_CODE_SIGNATURE", dataoff is not None)
        if dataoff is None:
            return report()

        magic, superblob_length, count, slot_type, entry_offset = superblob_entry(blob, dataoff)
        check("the signature starts with a SuperBlob",
              magic == CSMAGIC_EMBEDDED_SIGNATURE, "%#010x" % magic)
        check("the SuperBlob length matches LC_CODE_SIGNATURE",
              superblob_length == length, "%d vs %d" % (superblob_length, length))
        check("the SuperBlob holds one blob", count == 1, str(count))
        check("the index entry names slot 0",
              slot_type == CSSLOT_CODEDIRECTORY, "%#x" % slot_type)

        blob_magic = struct.unpack_from(">I", blob, dataoff + entry_offset)[0]
        check("the slot 0 offset lands on the CodeDirectory",
              blob_magic == CSMAGIC_CODEDIRECTORY, "%#010x" % blob_magic)
        check("the index entry does not carry the blob magic",
              slot_type != CSMAGIC_CODEDIRECTORY)

        # The guard inside the signer has to reject the file 1.0.0-1.0.4 wrote.
        spec = importlib.util.spec_from_file_location("sign_adhoc", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        good = bytearray(blob)
        module.check_signature(good, dataoff, length, length - entry_offset,
                               struct.unpack_from(">I", good, dataoff + entry_offset + 28)[0])

        broken = bytearray(blob)
        struct.pack_into(">I", broken, dataoff + SUPERBLOB_HEADER_SIZE, CSMAGIC_CODEDIRECTORY)
        try:
            module.check_signature(broken, dataoff, length, length - entry_offset, 1)
        except SystemExit as error:
            check("the guard rejects the 1.0.0-1.0.4 slot type",
                  "slot type" in str(error), str(error))
        else:
            check("the guard rejects the 1.0.0-1.0.4 slot type", False, "it accepted the file")

    return report()


def report():
    if failures:
        print("\n%d signature check(s) failed" % len(failures))
        return 1
    print("\nsignature: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
