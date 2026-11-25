#!/usr/bin/env python3
"""Check that basebin/basebin.tc covers every Mach-O the tar ships.

`jbctl update basebin` uploads basebin/basebin.tc as the basebin trustcache
before it copies the tar's members into /var/jb/basebin (update.m:32-39), and
Dopamine uploads the same file at every jailbreak (DOJailbreaker.m:348). Nothing
recomputes it afterwards, so a member whose cdhash is absent from that file is
a binary the kernel trustcache does not cover.

BaseBin/Makefile:96-98 builds the file with `trustcache create .build/basebin.tc
.build`, which hashes every Mach-O in the build output. A tar that replaces a
member but keeps the stock basebin.tc therefore describes binaries that are no
longer in the tar, and says nothing about the ones that are. That mismatch is
what this script fails on.

dyldhook_merge.*.dylib members are exempt because BaseBin/Makefile:100 says
they do not belong in the basebin trustcache; they are handled by the dyldhook
merge path instead.

Usage:
  verify-basebin-trustcache.py <directory holding basebin/, or basebin itself>

Exit status: 0 when every covered member's cdhash is in basebin.tc, 1 otherwise.
"""

import hashlib
import os
import struct
import sys

TRUSTCACHE_VERSION = 1
TRUSTCACHE_ENTRY_SIZE = 22  # 20-byte cdhash + hash_type + flags
HEADER_SIZE = 24  # version + uuid + length
CDHASH_LEN = 20

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_CODE_SIGNATURE = 0x1D
CSSLOT_CODEDIRECTORY = 0x0
CSSLOT_ALTERNATE_CODEDIRECTORIES = 0x1000


class TrustcacheError(Exception):
    pass


def read_trustcache(path):
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER_SIZE:
        raise TrustcacheError("%s is shorter than a trustcache header" % path)
    version, _uuid, length = struct.unpack_from("<I16sI", blob, 0)
    if version != TRUSTCACHE_VERSION:
        raise TrustcacheError("%s has trustcache version %d, expected 1" % (path, version))
    expected = HEADER_SIZE + TRUSTCACHE_ENTRY_SIZE * length
    if expected != len(blob):
        raise TrustcacheError(
            "%s declares %d entries (%d bytes) but is %d bytes" % (path, length, expected, len(blob))
        )
    return {
        blob[HEADER_SIZE + i * TRUSTCACHE_ENTRY_SIZE: HEADER_SIZE + i * TRUSTCACHE_ENTRY_SIZE + CDHASH_LEN]
        for i in range(length)
    }


def code_directory_hashes(slice_bytes):
    if len(slice_bytes) < 32:
        return []
    if struct.unpack_from("<I", slice_bytes, 0)[0] != MH_MAGIC_64:
        return []
    ncmds = struct.unpack_from("<I", slice_bytes, 16)[0]
    offset = 32
    sig_offset = sig_size = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", slice_bytes, offset)
        if cmdsize < 8:
            return []
        if cmd == LC_CODE_SIGNATURE:
            sig_offset, sig_size = struct.unpack_from("<II", slice_bytes, offset + 8)
        offset += cmdsize
    if sig_offset is None:
        return []
    superblob = slice_bytes[sig_offset:sig_offset + sig_size]
    if len(superblob) < 12:
        return []
    _magic, _length, count = struct.unpack_from(">III", superblob, 0)
    cursor = 12
    hashes = []
    for _ in range(count):
        if cursor + 8 > len(superblob):
            break
        blob_type, blob_offset = struct.unpack_from(">II", superblob, cursor)
        cursor += 8
        if blob_type not in (CSSLOT_CODEDIRECTORY, CSSLOT_ALTERNATE_CODEDIRECTORIES):
            continue
        if blob_offset + 8 > len(superblob):
            continue
        cd_length = struct.unpack_from(">I", superblob, blob_offset + 4)[0]
        blob = superblob[blob_offset:blob_offset + cd_length]
        if len(blob) != cd_length:
            continue
        hashes.append(hashlib.sha256(blob).digest()[:CDHASH_LEN])
    return hashes


def member_cdhashes(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 8:
        return []
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack_from(">I", data, 4)[0]
        hashes = []
        for index in range(count):
            _cpu, _sub, offset, size, _align = struct.unpack_from(">iiIII", data, 8 + index * 20)
            hashes.extend(code_directory_hashes(data[offset:offset + size]))
        return hashes
    return code_directory_hashes(data)


def is_trustcached_member(relative_path):
    if relative_path in ("basebin.tc", ".version"):
        return False
    if relative_path.startswith("dyldhook_merge."):
        return False
    if relative_path.endswith(".plist"):
        return False
    if "this_is_ellekit_not_substrate" in relative_path:
        return False
    return True


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: verify-basebin-trustcache.py <dir containing basebin/>\n")
        return 2
    root = argv[1]
    basebin = os.path.join(root, "basebin") if os.path.isdir(os.path.join(root, "basebin")) else root
    trustcache_path = os.path.join(basebin, "basebin.tc")
    if not os.path.isfile(trustcache_path):
        sys.stderr.write("FAIL: %s does not exist\n" % trustcache_path)
        return 1
    try:
        covered = read_trustcache(trustcache_path)
    except TrustcacheError as error:
        sys.stderr.write("FAIL: %s\n" % error)
        return 1

    missing = []
    checked = 0
    for directory, _subdirs, files in os.walk(basebin):
        for name in sorted(files):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, basebin)
            if not is_trustcached_member(relative):
                continue
            hashes = member_cdhashes(path)
            if not hashes:
                continue
            checked += 1
            for cdhash in hashes:
                if cdhash not in covered:
                    missing.append((relative, cdhash.hex()))

    for relative, cdhash in missing:
        sys.stderr.write(
            "FAIL: %s slice cdhash %s is not in basebin.tc\n" % (relative, cdhash)
        )
    if missing:
        sys.stderr.write(
            "FAIL: basebin.tc does not cover %d slice(s) of %d members; jbctl would upload a "
            "trustcache that describes different binaries than the tar ships\n"
            % (len(missing), checked)
        )
        return 1

    print("PASS: basebin.tc covers all %d Mach-O members that need it (%d entries)"
          % (checked, len(covered)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
