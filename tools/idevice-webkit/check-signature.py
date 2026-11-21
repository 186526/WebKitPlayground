#!/usr/bin/env python3
# Reads the code signature ldid wrote onto a thin Mach-O slice and holds it to
# the two properties the kernel checks before it maps the image.
#
#   ./check-signature.py SLICE EXPECTED_IDENTIFIER
#
# The identifier is the name the dylib is installed under, because ldid takes
# the file's own name as the CodeDirectory identifier. A slice signed under a
# build name such as t2.dylib loads nowhere: the kernel finds the installed
# name in the CodeDirectory and kills the process with SIGKILL CODESIGNING /
# "Invalid Page", which is what the device did to Safari. The flags word has to
# stay 0, the value ldid writes for an ad-hoc signature; a nonzero flags word is
# a signature shaped for some other purpose.
#
# The slice also has to carry LC_CODE_SIGNATURE with a nonzero size. ldid's
# `-S file` form with the file among its arguments writes nothing, and the
# check catches that before a package is built out of the result.
#
# Exits 0 on a matching slice and non-zero with the reason otherwise.
import struct
import sys

LC_CODE_SIGNATURE = 0x1D
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0
MACH_HEADER_SIZE = 32
SUPERBLOB_HEADER_SIZE = 12


def fail(message):
    sys.exit(f"check-signature: {message}")


def code_signature_command(data):
    magic, _, _, _, ncmds, _, _ = struct.unpack_from("<IiIIIII", data, 0)
    if magic != 0xFEEDFACF:
        fail("the slice is not a thin little-endian 64-bit Mach-O")
    offset = MACH_HEADER_SIZE
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", data, offset)
        if command == LC_CODE_SIGNATURE:
            _, _, dataoff, datasize = struct.unpack_from("<IIII", data, offset)
            return dataoff, datasize
        offset += size
    return None


def code_directory(data, dataoff):
    magic, length, count = struct.unpack_from(">III", data, dataoff)
    if magic != 0xFADE0CC0:
        fail(f"the signature SuperBlob magic is {magic:#010x}")
    for index in range(count):
        slot_type, offset = struct.unpack_from(">II", data, dataoff + SUPERBLOB_HEADER_SIZE +
                                               index * 8)
        if slot_type != CSSLOT_CODEDIRECTORY:
            continue
        blob = data[dataoff + offset:dataoff + offset + length]
        blob_magic, blob_length = struct.unpack_from(">II", blob, 0)
        if blob_magic != CSMAGIC_CODEDIRECTORY:
            fail(f"the blob in slot 0 has magic {blob_magic:#010x}")
        version, flags = struct.unpack_from(">II", blob, 8)
        identifier_offset, = struct.unpack_from(">I", blob, 20)
        end = blob.index(b"\0", identifier_offset)
        return blob[identifier_offset:end].decode(), flags
    fail("the SuperBlob holds no CodeDirectory")


def main():
    if len(sys.argv) != 3:
        fail("usage: check-signature.py SLICE EXPECTED_IDENTIFIER")
    path, expected = sys.argv[1:3]
    with open(path, "rb") as source:
        data = source.read()

    command = code_signature_command(data)
    if command is None:
        fail(f"{path} carries no LC_CODE_SIGNATURE, so it is not signed")
    dataoff, datasize = command
    if datasize == 0:
        fail(f"{path} carries an empty signature; ldid wrote nothing")

    identifier, flags = code_directory(data, dataoff)
    if identifier != expected:
        fail(f"{path} is signed as {identifier!r}, expected {expected!r}")
    if flags != 0:
        fail(f"{path} carries signature flags {flags:#x}, expected 0x0")


if __name__ == "__main__":
    main()
