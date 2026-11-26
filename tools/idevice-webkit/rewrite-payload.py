#!/usr/bin/env python3
# Rewrites eleven dependency edges inside the seven 18.3 images so each binds its
# 18.3 siblings instead of the system copies, in the directory that is already the
# install location. Every LC_ID_DYLIB keeps the /System name it carries.
#
#   ./rewrite-payload.py plan   ROOT [--prefix /var/jb]
#   ./rewrite-payload.py rewrite ROOT [--prefix /var/jb]
#   ./rewrite-payload.py verify ROOT [--prefix /var/jb]
#
# ROOT is a directory that stands for the device's /. A payload unpacked from a
# .deb at /tmp/x has its files at /tmp/x/var/jb/..., so ROOT is /tmp/x. `rewrite`
# edits those files where they are; there is no second output tree.
#
# WHY THIS REWRITE EXISTS
#
# An 18.3 WebKit opened by its /var/jb path still binds its dependencies by the
# literal /System strings inside it, so it binds the 16.1 JavaScriptCore. The
# device recorded exactly that:
#
#   dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001):
#     Symbol not found: _WTFSignpostIndirectLoggingEnabled
#     Referenced from: <787DFAB1-...> .../WebKit.framework/WebKit
#     Expected in:     <D309AA36-...> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
#
# Eleven load commands account for every edge one of the seven has on another of
# the seven. The measured table is in docs/ios16-framework-load-chain.md; the
# eleven are WebCore's four (JavaScriptCore, WebGPU, libwebrtc, libANGLE), both of
# WebKitLegacy's, WebGPU's one, and WebKit's four (WebKitLegacy through an
# LC_REEXPORT_DYLIB, plus JavaScriptCore, WebCore and libwebrtc). JavaScriptCore,
# libwebrtc and libANGLE depend on none of the seven.
#
# WHY THE IDENTITY IS NOT REWRITTEN
#
# dyld matches a request against resident images by install name first and by file
# identity second, and the install name wins. Keeping LC_ID_DYLIB at the /System
# name is therefore what lets dyld's install-name dedup answer a later
# /System/.../WebKit request with this rewritten image: once the /var/jb copy is
# resident under the system install name, a request spelled the system way finds
# it. Measured on the device by worker-t1-r2: loading the /var/jb copy while it
# claimed the system install name made a later /System request return that copy —
# same handle, one image. The reverse order produces two images and duplicate-class
# warnings, and an identity rewritten to the /var/jb path can never be returned for
# any /System request at all.
#
# That property says nothing about whether the rewritten image is resident early
# enough in an application process. An application's main binary carries
# LC_LOAD_DYLIB /System/Library/Frameworks/WebKit.framework/WebKit as a static
# binding that dyld resolves at launch, before any injected constructor runs, so in
# an application that arrives first and the dedup answers with the 16.1 image. This
# script establishes the first property only. See "What this does and does not
# achieve" in docs/rewrite-payload.md.
#
# WHY ABSOLUTE PATHS AND NOT @rpath
#
# An @rpath entry is a searched path. dyld's isOverridablePath gate
# (dyld/Loader.cpp:565) discards non-cache candidates for a cached framework
# unless the jailbreak's dyld hooks are installed, and on the device they are
# not: /var/jb/basebin/gen/dyld scores `1 0 0 1` on the hook token filter, which
# means the two override hooks are absent. An absolute path is handed straight
# to the loader and never consults that gate. The payload also carries zero
# LC_RPATH, so an @rpath rewrite would have to append load commands into the
# header pad of all seven images; an absolute rewrite appends nothing.
#
# WHAT THE REWRITE DOES TO THE SIGNATURE, AND HOW IT IS REPAIRED
#
# Every rewritten name sits in the load commands, and those lie inside the region
# LC_CODE_SIGNATURE covers. Rewriting therefore invalidates the CodeDirectory
# page hashes, and a modified image is killed even though Dopamine admits unsigned
# code: a device measurement changed one byte inside LC_ID_DYLIB and got exit 137
# for the same file, at the same path, at the same size, while the untouched copy
# loaded. Signature validity is the barrier, so the hashes are written again here
# rather than left to a signer.
#
# The repair is a page-hash recomputation, not a re-sign, and that is enough
# because of one property of this payload, which `rewrite` asserts: the
# signature's dataoff equals its CodeDirectory's codeLimit in all seven images.
# The signature therefore lies entirely past the last hashed page, so rewriting
# page hashes cannot disturb the signature itself and no loop can run.
#
# The two special slots are what would break a naive recomputation, and neither is
# a page hash:
#
#   slot -1  the info.plist hash     all zeros in this payload
#   slot -2  the requirements hash   the SHA-1 of the slot 2 blob, which is not
#                                    rewritten, so the value stays valid
#
# Both CodeDirectories are recomputed, slot 0 (hashType 1, SHA-1) and slot 4096
# (hashType 2, SHA-256), because both name the same codeLimit and a checker may
# read either. The CodeDirectory header's own fields are left exactly as they
# were: the `flags=0x0` the package ships is reported by `verify`, not changed to
# an ad-hoc value, and the identifiers, the page size and the slot counts are
# untouched.
#
# WHAT THIS DOES NOT DO
#
# It does not produce a signature that a stock kernel would accept as Apple's. It
# produces a self-consistent one, which is the property the device measurement
# showed matters for mapping under Dopamine's pmap_cs_allow_invalid. Nothing has
# been loaded from a repaired copy yet, so `rewrite` reports the shape it produced
# and does not call it proven.
import hashlib
import os
import struct
import sys

LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_RPATH = 0x8000001C
LC_CODE_SIGNATURE = 0x1D

MACH_HEADER_SIZE_64 = 32
MH_MAGIC_64 = 0xFEEDFACF

# Every load command whose first field after cmdsize is an lc_str offset naming a
# path. LC_RPATH is read so the report can say the payload has none; it is never
# a rewrite target.
NAME_COMMANDS = (LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_RPATH)

# The install prefix this payload targets. Dopamine is rootless and the tweak's
# seven paths are spelled with /var/jb, so that is the default. The flag exists
# because the packager supports a rootful prefix, and a package that wrote /var/jb
# strings while installing under /Library would name files it never installs.
DEFAULT_PREFIX = "/var/jb"

# The seven images: name, path relative to the install prefix, and the identity the
# binary carries today. The identity is what `verify` holds the file to, so "the
# LC_ID_DYLIB did not change" is a check rather than a claim, and the identity is
# never written by this script.
#
# The paths are where com.82flex.custom-webkit already installs the seven, and the
# five frameworks are where the tweak's filter loads WebKit from. They are not a
# new tree: rewriting in place is what makes the deployed files the rewritten ones
# without a second copy for the loader to pick between.
IMAGES = (
    ("JavaScriptCore", "Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
     "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
    ("WebCore", "Library/Frameworks/WebCore.framework/WebCore",
     "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"),
    ("WebKitLegacy", "Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
     "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy"),
    ("WebGPU", "Library/Frameworks/WebGPU.framework/WebGPU",
     "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU"),
    ("libwebrtc", "usr/lib/libwebrtc.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
    ("libANGLE", "usr/lib/libANGLE-shared.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib"),
    ("WebKit", "Library/Frameworks/WebKit.framework/WebKit",
     "/System/Library/Frameworks/WebKit.framework/WebKit"),
)

# The system spellings the seven use for one another, mapped to the member each
# becomes. Both WebKitLegacy spellings are here because WebKit re-exports it from
# PrivateFrameworks while WebCore loads it from Frameworks; WebGPU and WebCore
# likewise have two each. Anything absent is a dependency on a real system
# framework — Foundation, CoreFoundation, Security, Metal, IOKit and the fifty
# others — and is left byte for byte alone, which is what keeps it resolving to the
# copy the process already has.
SYSTEM_ALIASES = {
    "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore": "JavaScriptCore",
    "/System/Library/Frameworks/WebKit.framework/WebKit": "WebKit",
    "/System/Library/PrivateFrameworks/WebCore.framework/WebCore": "WebCore",
    "/System/Library/Frameworks/WebCore.framework/WebCore": "WebCore",
    "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy": "WebKitLegacy",
    "/System/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy": "WebKitLegacy",
    "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU": "WebGPU",
    "/System/Library/Frameworks/WebGPU.framework/WebGPU": "WebGPU",
    "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib": "libwebrtc",
    "/System/Library/Frameworks/WebCore.framework/Frameworks/libwebrtc.dylib": "libwebrtc",
    "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib": "libANGLE",
    "/System/Library/Frameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib": "libANGLE",
}

# The order the chain loads them in, measured in build-state.md 第八轮: JavaScriptCore
# first because it exports the WTF symbols the rest bind, WebKit last because it
# re-exports WebKitLegacy. src/load_chain.c spells this order and
# tests/load_chain_test.c asserts the two agree, so a change here without a change
# there fails on the host rather than on the device.
LOAD_ORDER = ("JavaScriptCore", "libwebrtc", "libANGLE", "WebCore", "WebKitLegacy",
              "WebGPU", "WebKit")

# How many edges one of the seven has on another, read off the device on
# 2026-11-26 02:03 CST. `verify` holds a rewritten tree to this number so a tree
# that was only partly written, or one from a different payload, is a failure
# rather than a smaller success.
MEASURED_EDGES = 11


class PayloadError(Exception):
    """A payload this script refuses to write."""


def device_path(prefix, relative):
    """A member's path as the device spells it."""
    return prefix + "/" + relative


def staged(root, path):
    """Where a device path lives inside a root that stands for the device's /."""
    return os.path.join(root, path.lstrip("/"))


def members(prefix):
    """The seven as (name, device path, identity), in the order IMAGES lists them."""
    return [(name, device_path(prefix, relative), identity)
            for name, relative, identity in IMAGES]


def destination_of(name, prefix):
    for member_name, path, _ in members(prefix):
        if member_name == name:
            return path
    raise PayloadError("no member is named %r" % name)


def command_name(data, offset, size):
    """The lc_str of one command, read the way the loader reads it."""
    name_offset = struct.unpack_from("<I", data, offset + 8)[0]
    if name_offset < 12 or name_offset >= size:
        raise PayloadError("a load command at %d has lc_str %d outside its %d bytes"
                           % (offset, name_offset, size))
    start = offset + name_offset
    end = offset + size
    terminator = data.find(b"\0", start, end)
    if terminator < 0:
        raise PayloadError("a load command at %d has an lc_str with no terminator" % offset)
    return data[start:terminator].decode("utf-8", "replace")


def load_commands(data):
    """Yields (offset, command, size, name) for every command in the header."""
    if len(data) < MACH_HEADER_SIZE_64:
        raise PayloadError("shorter than a Mach-O header")
    magic, = struct.unpack_from("<I", data, 0)
    if magic != MH_MAGIC_64:
        raise PayloadError("not a thin little-endian 64-bit Mach-O")
    command_count, command_bytes = struct.unpack_from("<II", data, 16)
    if MACH_HEADER_SIZE_64 + command_bytes > len(data):
        raise PayloadError("the load commands run past the end of the file")
    offset = MACH_HEADER_SIZE_64
    end = MACH_HEADER_SIZE_64 + command_bytes
    for _ in range(command_count):
        if offset + 8 > end:
            raise PayloadError("a load command starts past the end of the command region")
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > end:
            raise PayloadError("a load command at %d has size %d, outside its region"
                               % (offset, size))
        name = command_name(data, offset, size) if command in NAME_COMMANDS else None
        yield offset, command, size, name
        offset += size


def name_slot(data, offset, size):
    """The byte range a command's lc_str occupies, and how many bytes fit there.

    lc_str is a field, not a constant: the payload carries one 68-byte
    LC_LOAD_DYLIB whose lc_str is 24 beside its 24-byte ones, so a writer that
    assumed a fixed header size would write at the wrong offset. The capacity is
    the tail of the command, and a name of length L needs L+1 bytes for its own
    terminator.
    """
    name_offset = struct.unpack_from("<I", data, offset + 8)[0]
    start = offset + name_offset
    return start, offset + size - start


def signature_command(data):
    """The LC_CODE_SIGNATURE (dataoff, datasize), or None when there is none."""
    for offset, command, size, _ in load_commands(data):
        if command == LC_CODE_SIGNATURE:
            return struct.unpack_from("<II", data, offset + 8)
    return None


CODE_DIRECTORY_MAGIC = 0xFADE0C02
EMBEDDED_SIGNATURE_MAGIC = 0xFADE0CC0
HASH_ALGORITHMS = {1: hashlib.sha1, 2: hashlib.sha256}


def code_directories(data):
    """Yields (offset, hashType, hashOffset, hashSize, nSpecial, nCode, codeLimit,
    pageSize) for each CodeDirectory in the embedded signature.

    A payload carries two: slot 0 with SHA-1 and slot 4096 with SHA-256, over the
    same code limit. Both are rewritten because a checker may read either, and a
    signature that verifies under one algorithm and not the other is the kind of
    half-repaired file that is worse than an untouched one.
    """
    signature = signature_command(data)
    if signature is None:
        return
    dataoff, _ = signature
    magic, _, count = struct.unpack_from(">III", data, dataoff)
    if magic != EMBEDDED_SIGNATURE_MAGIC:
        raise PayloadError("the signature at %d is not a SuperBlob" % dataoff)
    for index in range(count):
        _, entry = struct.unpack_from(">II", data, dataoff + 12 + 8 * index)
        base = dataoff + entry
        blob_magic, = struct.unpack_from(">I", data, base)
        if blob_magic != CODE_DIRECTORY_MAGIC:
            continue
        _, _, hash_offset, _, n_special, n_code, code_limit = struct.unpack_from(
            ">IIIIIII", data, base + 8)
        hash_size, hash_type, _, page_size = struct.unpack_from(">BBBB", data, base + 36)
        if hash_type not in HASH_ALGORITHMS:
            raise PayloadError("a CodeDirectory uses hashType %d, which this script "
                               "cannot recompute" % hash_type)
        yield (base, hash_type, hash_offset, hash_size, n_special, n_code,
               code_limit, 1 << page_size)


def repair_signature(data):
    """Returns (image, stale) with both CodeDirectories' page hashes recomputed.

    One pass does both jobs: each page is hashed, the digest is compared against
    the stored one to count what was stale, and the digest is written back. A
    second pass to count the mismatches would read the whole file again for an
    answer the first pass already had.

    The signature lies entirely past codeLimit in this payload — asserted here
    before the first page is hashed — so the pages hashed are the pages the
    rewrite changed plus every other page, and the signature that holds the
    results is not among them. That is what makes a single pass enough.

    `stale` lists (hashType, page index) for every page whose stored hash did not
    match before the repair. That is the reading that proves the rewrite is what
    broke the signature: those pages must be exactly the ones holding the bytes
    that were changed, and `write_image` says so.

    An image with no signature is returned unchanged with no stale pages: nothing
    covers the bytes the rewrite changed, so there is nothing to repair.
    """
    directories = list(code_directories(data))
    if not directories:
        return data, []
    signature = signature_command(bytes(data))
    if signature is not None:
        for _, _, _, _, _, _, code_limit, _ in directories:
            if signature[0] < code_limit:
                raise PayloadError("the signature starts at %d, before the last hashed "
                                   "byte %d, so recomputing would invalidate itself"
                                   % (signature[0], code_limit))
    repaired = bytearray(data)
    stale = []
    for base, hash_type, hash_offset, hash_size, _, n_code, code_limit, page in directories:
        if code_limit > len(data):
            raise PayloadError("a CodeDirectory hashes to %d, past the file's %d bytes"
                               % (code_limit, len(data)))
        algorithm = HASH_ALGORITHMS[hash_type]
        for index in range(n_code):
            start = index * page
            end = min(start + page, code_limit)
            digest = algorithm(bytes(data[start:end])).digest()
            if len(digest) != hash_size:
                raise PayloadError("hashType %d produces %d bytes, but the slot is %d"
                                   % (hash_type, len(digest), hash_size))
            position = base + hash_offset + index * hash_size
            if data[position:position + hash_size] != digest:
                stale.append((hash_type, index))
            repaired[position:position + hash_size] = digest
    return bytes(repaired), stale


def signature_stale(data):
    """The CodeDirectory pages whose stored hash does not describe the bytes.

    Each entry is (hashType, page index). Empty for a signature that describes
    this file. It says nothing about whether a kernel would accept the signer,
    which is a different question and one this script cannot answer on a host.
    """
    stale = []
    for base, hash_type, hash_offset, hash_size, _, n_code, code_limit, page in code_directories(data):
        algorithm = HASH_ALGORITHMS[hash_type]
        for index in range(n_code):
            start = index * page
            end = min(start + page, code_limit)
            digest = algorithm(bytes(data[start:end])).digest()
            stored = data[base + hash_offset + index * hash_size:
                         base + hash_offset + (index + 1) * hash_size]
            if digest != stored:
                stale.append((hash_type, index))
    return stale


def edges_of(data, prefix):
    """Yields (offset, command, size, current name, member) for every edge on one
    of the seven that the image carries today.

    `member` is the name of the sibling the string means, so a caller does not look
    the alias table up twice.
    """
    for offset, command, size, name in load_commands(data):
        if command == LC_ID_DYLIB or name is None:
            continue
        member = SYSTEM_ALIASES.get(name)
        if member is not None:
            yield offset, command, size, name, member


def plan_image(path, display, prefix):
    """The rewrites one image needs, with every slot checked before any write.

    Only dependency commands are targets. LC_ID_DYLIB is never rewritten: keeping
    the system identity is the property that lets dyld's install-name dedup answer
    a later /System request with this image.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    rewrites = []
    for offset, command, size, name, member in edges_of(data, prefix):
        replacement = destination_of(member, prefix)
        if name == replacement:
            continue
        start, capacity = name_slot(data, offset, size)
        if len(replacement) + 1 > capacity:
            raise PayloadError(
                "%s: %s needs %d bytes in the %d-byte slot that held %r; refusing to write"
                % (display, replacement, len(replacement) + 1, capacity, name))
        rewrites.append((offset, command, name, replacement, start, capacity))
    return data, rewrites


def plan_payload(root, prefix):
    """Plans all seven images. Nothing is written."""
    plans = []
    for name, relative, identity in IMAGES:
        path = staged(root, device_path(prefix, relative))
        if not os.path.exists(path):
            raise PayloadError("%s: %s is not in the payload" % (name, path))
        data, rewrites = plan_image(path, name, prefix)
        plans.append((name, path, identity, device_path(prefix, relative), data, rewrites))
    return plans


def describe(command):
    return {
        LC_LOAD_DYLIB: "LOAD",
        LC_ID_DYLIB: "ID",
        LC_LOAD_WEAK_DYLIB: "WEAK",
        LC_REEXPORT_DYLIB: "REEXPORT",
        LC_RPATH: "RPATH",
    }.get(command, "0x%08x" % command)


def print_plan(plans):
    """Prints every planned rewrite and counts the /System references it leaves.

    The two counts answer different questions, and the plan is only the shape it
    claims to be when both are right. The rewrites are the eleven edges between the
    seven; the preserved count is every dependency of those images, on a real
    system library or on a stale spelling of a sibling, that is not rewritten. A
    run that rewrote more, or preserved fewer, would be changing something this
    shape does not touch.
    """
    total = 0
    preserved = 0
    identities = 0
    for name, _, identity, _, data, rewrites in plans:
        rewritten = set(start for _, _, _, _, start, _ in rewrites)
        kept = []
        for offset, command, size, current in load_commands(data):
            if command == LC_ID_DYLIB:
                identities += 1 if current == identity else 0
                continue
            if current is None or not current.startswith("/System/"):
                continue
            start, _ = name_slot(data, offset, size)
            if start not in rewritten:
                kept.append(current)
        preserved += len(kept)
        if not rewrites:
            print("  %-15s no rewrite needed, %d /System reference(s) kept"
                  % (name, len(kept)))
            continue
        print("  %-15s %d edge(s), %d /System reference(s) kept"
              % (name, len(rewrites), len(kept)))
        for _, command, current, replacement, start, capacity in rewrites:
            total += 1
            print("    %-8s slot %3d  now %3d  ->  %3d  %s"
                  % (describe(command), capacity, len(current), len(replacement), replacement))
    print("Planned %d edge(s) across %d image(s); %d /System reference(s) left byte "
          "for byte alone; %d of %d identities match the recorded table."
          % (total, len(plans), preserved, identities, len(plans)))
    print("Nothing was written.")


def write_image(data, rewrites, destination, name):
    """Writes one rewritten image in place and repairs its signature, holding both
    to the invariants they rely on.

    Each name is written at the offset its own command names and padded with
    nulls to the end of that command, so no other byte of the image moves and no
    load command grows. The result is then read back the way the loader reads it,
    so a write at a wrong offset fails here rather than producing an image that
    names a file which does not exist.

    The signature repair comes after the names, because it hashes the bytes the
    names changed. Two properties are asserted before it runs, and both are what
    make a single pass correct: the file length is unchanged, so every page keeps
    its boundaries and the two CodeDirectories keep their offsets, and the
    signature lies past the last hashed byte, so writing page hashes cannot
    disturb the signature that holds them.

    Returns the number of bytes changed, so a second run over an already rewritten
    tree reports zero instead of claiming work it did not do.
    """
    rewritten = bytearray(data)
    for _, _, _, replacement, start, capacity in rewrites:
        encoded = replacement.encode("utf-8")
        rewritten[start:start + capacity] = encoded + b"\0" * (capacity - len(encoded))
    for _, _, current, replacement, start, capacity in rewrites:
        read_back = bytes(rewritten[start:start + capacity])
        terminator = read_back.index(b"\0")
        resolved = read_back[:terminator].decode("utf-8", "replace")
        if resolved != replacement:
            raise PayloadError("%s: wrote %r at %d but the command now resolves %r"
                               % (name, replacement, start, resolved))
        if len(replacement) + 1 > capacity:
            raise PayloadError("%s: %r does not fit the %d-byte slot it was written into"
                               % (name, replacement, capacity))
    if len(rewritten) != len(data):
        raise PayloadError("%s: the rewrite changed the file length" % name)
    if signature_command(bytes(rewritten)) != signature_command(data):
        raise PayloadError("%s: the rewrite moved or resized LC_CODE_SIGNATURE" % name)
    repaired, stale = repair_signature(bytes(rewritten))
    # The pages whose stored hash disagreed with the bytes must be exactly the
    # pages the rewrite wrote into. That is what proves the rewrite is the cause of
    # the stale signature, and that the file being hashed is this run's output:
    # a copy that had already been rewritten would report no stale page at all, and
    # one from another run would report a different set.
    pages = {}
    for _, _, _, _, _, _, _, page in code_directories(bytes(rewritten)):
        pages.setdefault(page, None)
    if pages:
        page = min(pages)
        touched = set()
        for _, _, _, _, start, capacity in rewrites:
            for index in range(start, start + capacity):
                touched.add(index // page)
        found = set(index for _, index in stale)
        if found != touched:
            raise PayloadError("%s: %d page(s) whose hash disagreed are not the %d page(s) "
                               "the rewrite changed (first disagreement: %s)"
                               % (name, len(found), len(touched), sorted(found)[:8]))
    changed = sum(1 for left, right in zip(data, repaired) if left != right)
    with open(destination, "wb") as handle:
        handle.write(repaired)
    return changed


def verify(root, prefix=DEFAULT_PREFIX):
    """Reads a rewritten tree back and holds it to the properties it exists for.

    Each problem below is a failure the device would report as a symbol or path
    error rather than as a rewrite error, which is how earlier rounds were lost:

      1. No image still names one of the seven by a system spelling. That string
         is the bug the rewrite removes.
      2. Every /var/jb path an image names exists in the tree. A rewrite pointing
         at a file that is not there fails at bind time with the same
         Symbol not found text as the bug it replaced.
      3. Every LC_ID_DYLIB still carries the identity recorded in IMAGES, so
         "the identity did not change" is checked rather than asserted.
      4. The number of rewritten edges is the number the device's load commands
         carry, so a tree that was only partly written fails instead of passing
         with a smaller count.
      5. Every CodeDirectory describes the bytes in the file, or the device kills
         the image on load.

    The return value is (problems, report) where report carries the counts a
    reader judges the tree by: rewritten edges, identities matching, identities
    that do not, and whether the signature sits past the last hashed byte.
    `problems` is the only list of what is wrong; a missing image is reported
    there and nowhere else, so it prints once.
    """
    problems = []
    signatures = []
    rewritten_edges = 0
    identity_matches = 0
    identity_mismatches = []
    if not os.path.isdir(root):
        return ["%s does not exist" % root], {
            "edges": 0, "identities": 0, "mismatched": [], "signatures": []}
    # Membership in this set, not a prefix test, is what makes "this command names
    # one of the seven" true: a rootful tree has no /var/jb in it at all, and a
    # prefix test would count every absolute dependency as an edge.
    seven = set(device_path(prefix, relative) for _, relative, _ in IMAGES)
    for name, relative, identity in IMAGES:
        path = device_path(prefix, relative)
        local = staged(root, path)
        if not os.path.exists(local):
            # The packager decides whether to ship a tree by this function's exit
            # code, so a missing image belongs in `problems`, which is the only
            # place the caller reads problems from.
            problems.append("%s: missing, expected at %s" % (name, path))
            continue
        with open(local, "rb") as handle:
            data = handle.read()
        own_identity = None
        for offset, command, size, current in load_commands(data):
            if command == LC_ID_DYLIB:
                own_identity = current
                continue
            if current is None:
                continue
            if current in SYSTEM_ALIASES:
                problems.append("%s: %s still names the system path %s"
                                % (name, describe(command), current))
                continue
            if current in seven:
                rewritten_edges += 1
                if not os.path.exists(staged(root, current)):
                    problems.append("%s: names %s, which is not in the tree" % (name, current))
        if own_identity is None:
            problems.append("%s: carries no LC_ID_DYLIB" % name)
        elif own_identity == identity:
            identity_matches += 1
        else:
            identity_mismatches.append("%s: calls itself %s, the recorded identity is %s"
                                       % (name, own_identity, identity))
        directories = list(code_directories(data))
        if signature_command(data) is None:
            problems.append("%s: carries no LC_CODE_SIGNATURE, so nothing covers the "
                            "bytes the rewrite changed" % name)
            continue
        stale = signature_stale(data)
        if stale:
            problems.append("%s: %d CodeDirectory page hash(es) do not describe the file, "
                            "first at %s" % (name, len(stale), stale[0]))
        signatures.append((name, {
            "directories": [(hash_type, n_code) for _, hash_type, _, _, _, n_code, _, _
                            in directories],
            "flags": code_directory_flags(data),
            "limit": directories[0][6] if directories else 0,
            "signature": signature_command(data),
        }))
    problems.extend(identity_mismatches)
    # Both counts are asserted even when a file is missing. A tree with one image
    # gone has fewer edges and fewer identities, so skipping the assertion here
    # would make that tree the one case where the two numbers go unchecked.
    if rewritten_edges != MEASURED_EDGES:
        problems.append("%d /var/jb edge(s) rewritten, the device's images carry %d"
                        % (rewritten_edges, MEASURED_EDGES))
    if identity_matches != len(IMAGES):
        problems.append("%d of %d identities match the recorded table"
                        % (identity_matches, len(IMAGES)))
    return problems, {
        "edges": rewritten_edges,
        "identities": identity_matches,
        "mismatched": identity_mismatches,
        "signatures": signatures,
    }


def code_directory_flags(data):
    """The flags word of each CodeDirectory, which is what tells an ad-hoc
    signature (0x2) from the one this payload ships (0x0)."""
    signature = signature_command(data)
    if signature is None:
        return []
    dataoff, _ = signature
    magic, _, count = struct.unpack_from(">III", data, dataoff)
    flags = []
    for index in range(count):
        _, entry = struct.unpack_from(">II", data, dataoff + 12 + 8 * index)
        base = dataoff + entry
        blob_magic, = struct.unpack_from(">I", data, base)
        if blob_magic == CODE_DIRECTORY_MAGIC:
            value, = struct.unpack_from(">I", data, base + 12)
            flags.append(value)
    return flags


def describe_signature(name, shape):
    """One image's signature, in the terms the device measurement used: which
    algorithms it carries, over how many pages each, and whether the signature
    sits past the last hashed byte."""
    dataoff, datasize = shape["signature"]
    parts = ["%s: LC_CODE_SIGNATURE at %d size %d, codeLimit %d%s"
             % (name, dataoff, datasize, shape["limit"],
                ", past the hashed region" if dataoff >= shape["limit"]
                else ", INSIDE the hashed region")]
    for hash_type, n_code in shape["directories"]:
        parts.append("hashType %d over %d page(s)" % (hash_type, n_code))
    if shape["flags"]:
        parts.append("flags %s" % ", ".join("0x%x" % value for value in shape["flags"]))
    return "; ".join(parts)


def print_report(report):
    """The three counts a reader judges a rewritten tree by, then the signatures.

    What is wrong with the tree is printed by the caller from `problems` alone. A
    second loop over the missing images here made every absent image print twice,
    in two different wordings, and nothing read the copy.
    """
    print("  %d /var/jb edge(s) among the seven, %d expected"
          % (report["edges"], MEASURED_EDGES))
    print("  %d of %d LC_ID_DYLIB still carry the recorded /System identity"
          % (report["identities"], len(IMAGES)))
    for name, shape in report["signatures"]:
        print("  %s" % describe_signature(name, shape))


def main(argv):
    usage = ("usage: rewrite-payload.py plan    ROOT [--prefix /var/jb]\n"
             "       rewrite-payload.py rewrite ROOT [--prefix /var/jb]\n"
             "       rewrite-payload.py verify  ROOT [--prefix /var/jb]\n"
             "\nROOT stands for the device's /. A deb unpacked at /tmp/x has its\n"
             "files at /tmp/x/var/jb/..., so ROOT is /tmp/x.\n")
    if len(argv) < 3:
        sys.stderr.write(usage)
        return 2
    action = argv[1]
    prefix = DEFAULT_PREFIX
    arguments = []
    remaining = argv[2:]
    while remaining:
        value = remaining.pop(0)
        if value == "--prefix":
            if not remaining:
                sys.stderr.write("--prefix needs a value\n")
                return 2
            prefix = remaining.pop(0).rstrip("/")
            continue
        arguments.append(value)
    if len(arguments) != 1:
        sys.stderr.write(usage)
        return 2
    root = os.path.abspath(arguments[0])
    if prefix.startswith("/") is False and prefix != "":
        sys.stderr.write("--prefix must be an absolute path or empty, not %r\n" % prefix)
        return 2
    try:
        if action == "plan":
            print_plan(plan_payload(root, prefix))
            return 0
        if action == "rewrite":
            # The whole plan is built and every slot is checked before the first
            # byte is written, so a payload that cannot be rewritten is left
            # untouched instead of half written.
            plans = plan_payload(root, prefix)
            total = 0
            changed_bytes = 0
            for name, path, _, _, data, rewrites in plans:
                changed = write_image(data, rewrites, path, name)
                changed_bytes += changed
                total += len(rewrites)
                print("  %-15s %2d edge(s) rewritten, %d byte(s) changed, signature "
                      "page hashes recomputed" % (name, len(rewrites), changed))
            print("Rewrote %d edge(s) across %d image(s) under %s; %d byte(s) changed."
                  % (total, len(plans), staged(root, prefix), changed_bytes))
            print("LC_ID_DYLIB is unchanged in all seven, so dyld's install-name dedup can")
            print("answer a later /System request with these images. `verify` reads that back.")
            return 0
        if action == "verify":
            problems, report = verify(root, prefix)
            print_report(report)
            for problem in problems:
                print("  FAIL %s" % problem)
            if problems:
                return 1
            print("  every edge among the seven names a /var/jb path that exists, every "
                  "identity is unchanged, and every signature describes its own bytes")
            return 0
        sys.stderr.write("unknown action %r\n" % action)
        sys.stderr.write(usage)
        return 2
    except PayloadError as error:
        sys.stderr.write("rewrite-payload: %s\n" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
