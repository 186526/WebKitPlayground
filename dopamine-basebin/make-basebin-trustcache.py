#!/usr/bin/env python3
"""Regenerate basebin/basebin.tc from the members a staging tree actually ships.

`BaseBin/Makefile:96-98` builds this file with `trustcache create .build/basebin.tc
.build`, which is not in the repo (see README section 3), so a tar assembled by hand
needs another way to keep the trustcache and the members in step. This does what that
tool does: hash every Mach-O slice's CodeDirectory, keep the first 20 bytes, write
hash_type 2, sort by cdhash.

Why it has to be regenerated rather than reused: `update.m:32-39` uploads this file as
the basebin trustcache before it copies the tar's members in, and nothing recomputes it
from /var/jb/basebin afterwards. Reusing a stock file after replacing a member leaves
that member outside the trustcache and describes slices that are no longer shipped.

The UUID is derived from the sorted cdhash set so two builds of the same members produce
the same bytes. Upstream generates a random UUID each build; the kernel uses it as a
label only, so a derived one is equally valid and makes the output reproducible.

Usage:
  make-basebin-trustcache.py <dir containing basebin/> [output path]

The output path defaults to <dir>/basebin/basebin.tc.

Exit status: 0 on success, 1 when a member cannot be read as Mach-O.
"""

import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from importlib.machinery import SourceFileLoader

_verifier = SourceFileLoader(
    "verify_basebin_trustcache",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify-basebin-trustcache.py"),
).load_module()

TRUSTCACHE_VERSION = 1
ENTRY_SUFFIX = b"\x02\x00"


def collect_cdhashes(basebin):
    """Return the sorted cdhash set for every member that belongs in the trustcache."""
    hashes = []
    unreadable = []
    for directory, _subdirs, files in os.walk(basebin):
        for name in sorted(files):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, basebin)
            if not _verifier.is_trustcached_member(relative):
                continue
            member = _verifier.member_cdhashes(path)
            if not member:
                unreadable.append(relative)
                continue
            hashes.extend(member)
    return sorted(hashes), unreadable


def encode(cdhashes):
    uuid = hashlib.sha256(b"".join(cdhashes)).digest()[:16]
    out = struct.pack("<I16sI", TRUSTCACHE_VERSION, uuid, len(cdhashes))
    out += b"".join(hash_ + ENTRY_SUFFIX for hash_ in cdhashes)
    return out, uuid


def main(argv):
    if len(argv) < 2 or len(argv) > 3:
        sys.stderr.write(__doc__)
        return 2
    root = argv[1]
    basebin = os.path.join(root, "basebin") if os.path.isdir(os.path.join(root, "basebin")) else root
    if not os.path.isdir(basebin):
        sys.stderr.write("FAIL: %s is not a directory\n" % basebin)
        return 1
    output = argv[2] if len(argv) == 3 else os.path.join(basebin, "basebin.tc")

    cdhashes, unreadable = collect_cdhashes(basebin)
    if unreadable:
        for relative in unreadable:
            sys.stderr.write("FAIL: %s has no code signature to hash\n" % relative)
        return 1
    if not cdhashes:
        sys.stderr.write("FAIL: no members found under %s\n" % basebin)
        return 1

    blob, uuid = encode(cdhashes)
    with open(output, "wb") as handle:
        handle.write(blob)
    print("%s: %d entries, %d bytes, uuid %s, sha256 %s"
          % (output, len(cdhashes), len(blob), uuid.hex(),
             hashlib.sha256(blob).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
