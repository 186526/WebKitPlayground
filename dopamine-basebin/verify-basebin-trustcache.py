#!/usr/bin/env python3
"""Check that basebin/basebin.tc holds exactly the cdhashes of the Mach-O members the tar ships.

`jbctl update basebin` uploads basebin/basebin.tc as the basebin trustcache
before it copies the tar's members into /var/jb/basebin (update.m:32-39), and
Dopamine uploads the same file at every jailbreak (DOJailbreaker.m:348). Nothing
recomputes it afterwards, so a member whose cdhash is absent from that file is
a binary the kernel trustcache does not cover.

BaseBin/Makefile:96-98 builds the file with `trustcache create .build/basebin.tc
.build`, which emits one cdhash per Mach-O slice. Which digest that cdhash is
comes from the CodeDirectory's own hashType byte, not from a choice the build
makes: upstream's own basebin.tc keeps the 0x1000 SHA-256 alternate for a
dual-CodeDirectory slice and none of the 0x0 SHA-1 values. A tar that replaces a
member but keeps the stock basebin.tc therefore describes binaries that are no
longer in the tar, and says nothing about the ones that are. That mismatch is
what this script fails on.

The comparison is set equality over `(cdhash, hash_type)` pairs. Three departures each
fail on their own line: a shipped slice with no entry; an entry no shipped slice
produces — a cdhash the kernel never computes; and a right cdhash under the wrong
label. The first two are not harmless. The patched members carry a SHA-1
CodeDirectory beside the SHA-256 one, so a generator that hashed every blob with
SHA-256 produced four entries no kernel computes next to the 28 correct ones, and a
check that only asked whether every slice was covered passed that file. The label
matters because `trustcache_file_build_from_path` copies the byte straight into the
uploaded file.

dyldhook_merge.*.dylib members are exempt because BaseBin/Makefile:100 says
they do not belong in the basebin trustcache; they are handled by the dyldhook
merge path instead.

Usage:
  verify-basebin-trustcache.py <directory holding basebin/, or basebin itself>

Exit status: 0 when basebin.tc holds exactly the cdhashes of the shipped members, 1 otherwise.
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

# Byte 37 of a CodeDirectory is its hashType. The kernel hashes the blob with the
# algorithm that byte names, so the cdhash of a SHA-1 CodeDirectory is a SHA-1 digest.
HASH_TYPE_OFFSET = 37
CDHASH_ALGORITHMS = {1: hashlib.sha1, 2: hashlib.sha256}


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
    entries = set()
    for index in range(length):
        start = HEADER_SIZE + index * TRUSTCACHE_ENTRY_SIZE
        entries.add((blob[start:start + CDHASH_LEN], blob[start + CDHASH_LEN]))
    return entries


def code_directory_cdhash(blob):
    """Return (cdhash, hashType) for one CodeDirectory blob, or None.

    The digest is chosen by the blob's own hashType byte at offset 37, not by this
    script: a CodeDirectory that declares SHA-1 has a SHA-1 cdhash, and hashing it
    with SHA-256 produces a value no kernel ever computes. The same byte is the
    trustcache entry's own hash_type field, so both travel together.
    """
    if len(blob) < HASH_TYPE_OFFSET + 1:
        return None
    hash_type = blob[HASH_TYPE_OFFSET]
    algorithm = CDHASH_ALGORITHMS.get(hash_type)
    if algorithm is None:
        return None
    return algorithm(blob).digest()[:CDHASH_LEN], hash_type


def slice_cdhash(slice_bytes):
    """Return one cdhash for this slice, preferring the 0x1000 alternate.

    Every slice carries one cdhash in the trustcache, so a slice that ships both a
    0x0 and a 0x1000 CodeDirectory contributes only the latter. The alternate is
    the modern one; upstream's own trustcache create output keeps the 0x1000 pair
    for a dual-CodeDirectory slice and nothing else.
    """
    if len(slice_bytes) < 32:
        return None
    if struct.unpack_from("<I", slice_bytes, 0)[0] != MH_MAGIC_64:
        return None
    ncmds = struct.unpack_from("<I", slice_bytes, 16)[0]
    offset = 32
    sig_offset = sig_size = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", slice_bytes, offset)
        if cmdsize < 8:
            return None
        if cmd == LC_CODE_SIGNATURE:
            sig_offset, sig_size = struct.unpack_from("<II", slice_bytes, offset + 8)
        offset += cmdsize
    if sig_offset is None:
        return None
    superblob = slice_bytes[sig_offset:sig_offset + sig_size]
    if len(superblob) < 12:
        return None
    _magic, _length, count = struct.unpack_from(">III", superblob, 0)
    cursor = 12
    primary = None
    alternate = None
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
        cdhash = code_directory_cdhash(blob)
        if cdhash is None:
            continue
        if blob_type == CSSLOT_ALTERNATE_CODEDIRECTORIES:
            alternate = cdhash
        else:
            primary = cdhash
    return alternate if alternate is not None else primary


def member_cdhashes(path):
    """Return one cdhash per Mach-O slice in this member."""
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
            cdhash = slice_cdhash(data[offset:offset + size])
            if cdhash is not None:
                hashes.append(cdhash)
        return hashes
    cdhash = slice_cdhash(data)
    return [cdhash] if cdhash is not None else []


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

    expected = {}
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
            for cdhash, hash_type in hashes:
                expected[(cdhash, hash_type)] = relative

    # The entry's own hash_type byte is part of the tuple: `update.m:32-39` hands the
    # tar's file to trustcache_file_build_from_path, which reads it verbatim and uploads
    # it, so a SHA-1 digest labelled 2 reaches the kernel labelled 2. A slice whose hash
    # is present under the wrong label is reported apart from an entry no slice produces.
    expected_by_hash = {}
    for (cdhash, hash_type), relative in expected.items():
        expected_by_hash[cdhash] = (relative, hash_type)

    missing = [(relative, cdhash.hex(), hash_type)
               for (cdhash, hash_type), relative in expected.items()
               if (cdhash, hash_type) not in covered]
    mislabelled = []
    orphans = []
    for cdhash, hash_type in sorted(covered):
        if (cdhash, hash_type) in expected:
            continue
        if cdhash in expected_by_hash:
            relative, wanted = expected_by_hash[cdhash]
            mislabelled.append((relative, cdhash.hex(), wanted, hash_type))
        else:
            orphans.append((cdhash.hex(), hash_type))

    for relative, cdhash, hash_type in sorted(missing):
        sys.stderr.write(
            "FAIL: %s slice cdhash %s (hash_type %d) is not in basebin.tc\n"
            % (relative, cdhash, hash_type)
        )
    for relative, cdhash, wanted, found in sorted(mislabelled):
        sys.stderr.write(
            "FAIL: %s slice cdhash %s is labelled hash_type %d in basebin.tc, "
            "but its CodeDirectory declares %d\n" % (relative, cdhash, found, wanted)
        )
    for cdhash, hash_type in orphans:
        sys.stderr.write(
            "FAIL: basebin.tc entry %s (hash_type %d) matches no slice the tar ships\n"
            % (cdhash, hash_type)
        )

    # Exact equality, not mere coverage. An entry the kernel never computes is the
    # exact defect this gate exists to catch, and a superset hides it: 28 correct
    # entries plus 4 SHA-256-over-a-SHA-1-CodeDirectory values passes a covers-only
    # check while shipping four entries no kernel computes.
    if missing or mislabelled or orphans:
        sys.stderr.write(
            "FAIL: basebin.tc and the shipped slices disagree: %d uncovered slice(s), "
            "%d mislabelled entry/entries, %d orphan entry/entries, "
            "%d members checked, %d entries in the file\n"
            % (len(missing), len(mislabelled), len(orphans), checked, len(covered))
        )
        return 1

    print("PASS: basebin.tc is exactly the %d cdhashes of the %d Mach-O members that need it"
          % (len(expected), checked))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
