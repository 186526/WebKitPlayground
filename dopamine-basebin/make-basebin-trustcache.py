#!/usr/bin/env python3
"""Regenerate basebin/basebin.tc from the members a staging tree actually ships.

`BaseBin/Makefile:96-98` builds this file with `trustcache create .build/basebin.tc
.build`, which is not in the repo (see README section 3), so a tar assembled by hand
needs another way to keep the trustcache and the members in step. This does what that
tool does: one cdhash per Mach-O slice, the digest taken by the slice's CodeDirectory
own hashType byte (1 is SHA-1, 2 is SHA-256), the 0x1000 alternate kept when a slice
carries both, entries sorted by cdhash.

The entry's own hash_type byte carries that same value rather than a constant 2, so the
digest and the label always agree. `update.m:32-39` reads this file with
trustcache_file_build_from_path and uploads it unchanged, so the label reaches the
kernel as written; a SHA-1 digest labelled 2 would be a mismatch nothing downstream
repairs.

Why the digest must follow hashType rather than being fixed at SHA-256: the two
patched members are dual-CodeDirectory, with a SHA-1 blob at slot 0x0 and a SHA-256
blob at 0x1000. Hashing the SHA-1 blob with SHA-256 yields a value no kernel computes,
so a trustcache built that way carries entries that never match while the slice it was
meant to cover stays outside. Upstream's own basebin.tc keeps the 0x1000 pair for
those members and none of the SHA-1 values.

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
import importlib.util
import os
import struct
import sys

# The verifier is a sibling file, not an importable package member, so it is loaded by
# path. An earlier revision used `importlib.machinery.SourceFileLoader`, whose deprecated
# loader method the macOS runner's Python no longer provides: basebin-build.yml run
# 45752216760 died on that AttributeError before any assertion ran, and it was the run's
# only failure. spec_from_file_location plus exec_module is the supported shape; the
# module is registered under its own name first because exec_module does not do that.
_VERIFIER_MODULE = "verify_basebin_trustcache"
_VERIFIER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "verify-basebin-trustcache.py"
)
_verifier_spec = importlib.util.spec_from_file_location(_VERIFIER_MODULE, _VERIFIER_PATH)
if _verifier_spec is None or _verifier_spec.loader is None:
    sys.stderr.write("FAIL: cannot load %s as a module\n" % _VERIFIER_PATH)
    raise SystemExit(1)
_verifier = importlib.util.module_from_spec(_verifier_spec)
sys.modules[_VERIFIER_MODULE] = _verifier
_verifier_spec.loader.exec_module(_verifier)

TRUSTCACHE_VERSION = 1
ENTRY_FLAGS = 0


def collect_entries(basebin):
    """Return the sorted (cdhash, hash_type) entries for every member needing trust.

    The hash_type is the one the chosen CodeDirectory declares, not a constant: a
    slice whose only CodeDirectory is SHA-1 gets a SHA-1 digest and the label 1, so
    the digest and the label cannot disagree. `update.m:32-39` uploads this file
    verbatim through trustcache_file_build_from_path, which copies the byte through.
    """
    entries = []
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
            entries.extend(member)
    return sorted(entries), unreadable


def encode(entries):
    uuid = hashlib.sha256(b"".join(cdhash for cdhash, _hash_type in entries)).digest()[:16]
    out = struct.pack("<I16sI", TRUSTCACHE_VERSION, uuid, len(entries))
    for cdhash, hash_type in entries:
        out += cdhash + bytes([hash_type, ENTRY_FLAGS])
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

    entries, unreadable = collect_entries(basebin)
    if unreadable:
        for relative in unreadable:
            sys.stderr.write("FAIL: %s has no code signature to hash\n" % relative)
        return 1
    if not entries:
        sys.stderr.write("FAIL: no members found under %s\n" % basebin)
        return 1

    blob, uuid = encode(entries)
    with open(output, "wb") as handle:
        handle.write(blob)
    print("%s: %d entries, %d bytes, uuid %s, sha256 %s"
          % (output, len(entries), len(blob), uuid.hex(),
             hashlib.sha256(blob).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
