#!/usr/bin/env python3
# Rewrites the load commands of the 18.3 WebKit payload so that the seven images
# name themselves and each other by their /var/jb paths, then writes the result
# as copies under a staging directory.
#
#   ./rewrite-payload.py plan      SOURCE_ROOT STAGING_ROOT [--deps-only]
#   ./rewrite-payload.py write     SOURCE_ROOT STAGING_ROOT [--deps-only]
#   ./rewrite-payload.py verify           STAGING_ROOT [--deps-only]
#
# --deps-only is a MECHANISM PROBE, not a fix. It rewrites only the dependency
# strings and leaves each image's LC_ID_DYLIB claiming its /System identity.
#
# Why that is not a fix: dyld matches a request against resident images by install
# name first and by file identity second, and the install name wins. An image that
# keeps the system identity therefore matches the resident system twin — which in
# any application that links WebKit is already mapped before an injected
# constructor runs — so the rewritten /var/jb dependency string resolves to the
# 16.1 image anyway. Measured on the device by worker-t1-r2: loading the /var/jb
# copy while it still claimed the system install name made a later /System request
# return that copy (same handle, 1 image); the reverse order produces two images
# and duplicate-class warnings.
#
# The probe exists to answer one question with a reading rather than with this
# reasoning: does an image that keeps the system identity and is loaded beside a
# resident system twin get returned for a later /System request? Reasoning says it
# binds the resident twin; the probe either confirms that or contradicts it, and
# a contradiction would be the better route. Ship shape (i), the full rewrite.
#
# SOURCE_ROOT is the payload as com.82flex.custom-webkit installed it, i.e. the
# directory that corresponds to /var/jb on the device. On this host a deb unpacked
# at /tmp/newdeb/x puts it at /tmp/newdeb/x/var/jb.
#
# STAGING_ROOT is a local directory that stands for the device's /. A file whose
# device path is DESTINATION_ROOT/WebKit.framework/WebKit is written to
# STAGING_ROOT/var/jb/Library/WebKit18/WebKit.framework/WebKit and is copied
# there unchanged. The split exists so the strings inside the binaries are always
# the device's own paths while the bytes can still be produced and checked on the
# host: a staging directory is a place to build a tree, not a second opinion
# about where it gets installed.
#
# `plan` parses everything and prints what it would change without writing a
# byte, which is the form to run first against a payload that came from
# somewhere else. `write` performs the same plan after copying. `verify` reads a
# staging root back and holds it to the properties the rewrite exists to produce.
#
# WHY THE REWRITE IS NEEDED
#
# The 18.3 images as that payload ships them name the system paths:
#
#   WebKit          LC_ID_DYLIB   /System/Library/Frameworks/WebKit.framework/WebKit
#                   LC_LOAD_DYLIB /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
#   JavaScriptCore  LC_ID_DYLIB   /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
#   ... and likewise for WebCore, WebKitLegacy, WebGPU, libwebrtc, libANGLE-shared
#
# Those strings are what dyld resolves. An 18.3 WebKit opened by its /var/jb path
# still binds its dependencies by the literal /System strings inside it, so it
# binds the 16.1 JavaScriptCore. The device recorded exactly that:
#
#   dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001):
#     Symbol not found: _WTFSignpostIndirectLoggingEnabled
#     Referenced from: <787DFAB1-...> .../WebKit.framework/WebKit
#     Expected in:     <D309AA36-...> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
#
# The same install names also make dyld treat a /var/jb image and its /System
# twin as one identity, so in a process where the system copy arrived first a
# later dlopen of the /var/jb path only adds a reference to it. Rewriting
# LC_ID_DYLIB to the /var/jb path gives the 18.3 image an identity of its own;
# rewriting the dependency strings makes its binds resolve to the 18.3 siblings.
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
# because of one property of this payload, which `write` asserts: the
# signature's dataoff equals its CodeDirectory's codeLimit in all seven images.
# The signature therefore lies entirely past the last hashed page, so rewriting
# page hashes cannot disturb the signature itself and no loop can run.
#
# The three special slots are what would break a naive recomputation, and none of
# them is a page hash:
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
# been loaded from a repaired copy yet, so `write` reports the shape it produced
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

# The destination root as the device sees it. It is the value inside the
# binaries and the value the tweak's load chain uses, so both name one root and a
# reader never has to reconcile two. Nothing here writes into
# /var/jb/Library/Frameworks, which belongs to the package.
DESTINATION_ROOT = "/var/jb/Library/WebKit18"

# The seven images: name, directory in the payload, directory under the
# destination root, binary leaf. The payload keeps the frameworks under
# Library/Frameworks beside a usr/lib for the two bare dylibs; the destination is
# flat because the destination prefix is longer while the load-command slots are
# fixed, and a nested Library/Frameworks there overflows most of them.
#
# The order is the load order build-state.md 第八轮 measured: JavaScriptCore
# first, WebKit last. This script does not depend on the order, but stating the
# seven in the order they are loaded keeps the three places that spell them — here,
# the tweak, and its test — readable side by side.
IMAGES = (
    ("JavaScriptCore", "Library/Frameworks/JavaScriptCore.framework", "JavaScriptCore.framework", "JavaScriptCore"),
    ("WebCore", "Library/Frameworks/WebCore.framework", "WebCore.framework", "WebCore"),
    ("WebKitLegacy", "Library/Frameworks/WebKitLegacy.framework", "WebKitLegacy.framework", "WebKitLegacy"),
    ("WebGPU", "Library/Frameworks/WebGPU.framework", "WebGPU.framework", "WebGPU"),
    ("libwebrtc", "usr/lib", "usr/lib", "libwebrtc.dylib"),
    ("libANGLE", "usr/lib", "usr/lib", "libANGLE-shared.dylib"),
    ("WebKit", "Library/Frameworks/WebKit.framework", "WebKit.framework", "WebKit"),
)

# The system strings the 18.3 images use for the seven, mapped to the path each
# becomes under the destination root. Anything absent here is a dependency on a
# real system framework and keeps pointing at /System: Foundation,
# CoreFoundation, Security, Metal, IOKit and the roughly fifty others are left
# byte for byte alone, which is what makes them keep resolving to the system
# copies the process already has.
SYSTEM_TO_DESTINATION = {
    "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore":
        DESTINATION_ROOT + "/JavaScriptCore.framework/JavaScriptCore",
    "/System/Library/Frameworks/WebKit.framework/WebKit":
        DESTINATION_ROOT + "/WebKit.framework/WebKit",
    "/System/Library/PrivateFrameworks/WebCore.framework/WebCore":
        DESTINATION_ROOT + "/WebCore.framework/WebCore",
    "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy":
        DESTINATION_ROOT + "/WebKitLegacy.framework/WebKitLegacy",
    "/System/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy":
        DESTINATION_ROOT + "/WebKitLegacy.framework/WebKitLegacy",
    "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU":
        DESTINATION_ROOT + "/WebGPU.framework/WebGPU",
    "/System/Library/Frameworks/WebGPU.framework/WebGPU":
        DESTINATION_ROOT + "/WebGPU.framework/WebGPU",
    "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib":
        DESTINATION_ROOT + "/usr/lib/libwebrtc.dylib",
    "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib":
        DESTINATION_ROOT + "/usr/lib/libANGLE-shared.dylib",
}

# The device paths the seven must end up naming, in load order. load_chain.c
# spells the same seven and tests/load_chain_test.c asserts that the two agree,
# so a change here without a change there fails on the host rather than on the
# device.
DESTINATION_PATHS = (
    DESTINATION_ROOT + "/JavaScriptCore.framework/JavaScriptCore",
    DESTINATION_ROOT + "/usr/lib/libwebrtc.dylib",
    DESTINATION_ROOT + "/usr/lib/libANGLE-shared.dylib",
    DESTINATION_ROOT + "/WebCore.framework/WebCore",
    DESTINATION_ROOT + "/WebKitLegacy.framework/WebKitLegacy",
    DESTINATION_ROOT + "/WebGPU.framework/WebGPU",
    DESTINATION_ROOT + "/WebKit.framework/WebKit",
)


class PayloadError(Exception):
    """A payload this script refuses to write."""


def staged(staging_root, device_path):
    """Where a device path lives inside a staging root that stands for /."""
    return os.path.join(staging_root, device_path.lstrip("/"))


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
CD_HEADER_SIZE = 44


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


def signature_report(data):
    """What `verify` prints per image, so a reader judges the signature by its
    shape rather than by this script's word for it."""
    signature = signature_command(data)
    if signature is None:
        return None, []
    dataoff, datasize = signature
    magic, length, count = struct.unpack_from(">III", data, dataoff)
    slot_types = []
    for index in range(count):
        slot_type, entry = struct.unpack_from(">II", data, dataoff + 12 + 8 * index)
        blob_magic, = struct.unpack_from(">I", data, dataoff + entry)
        slot_types.append((slot_type, blob_magic))
    return (dataoff, datasize, magic, length), slot_types


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


def plan_image(path, display, rewrite_identity):
    """The rewrites one image needs, with every slot checked before any write.

    The identity command is only a rewrite target for the full shape. Under
    --deps-only it stays as it is, because keeping the system install name is what
    that probe is measuring.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    rewrites = []
    for offset, command, size, name in load_commands(data):
        replacement = SYSTEM_TO_DESTINATION.get(name)
        if replacement is None:
            continue
        if command == LC_ID_DYLIB and not rewrite_identity:
            continue
        start, capacity = name_slot(data, offset, size)
        if len(replacement) + 1 > capacity:
            raise PayloadError(
                "%s: %s needs %d bytes in the %d-byte slot that held %r; refusing to write"
                % (display, name, len(replacement) + 1, capacity, replacement))
        rewrites.append((offset, command, name, replacement, start, capacity))
    return data, rewrites


def plan_payload(source_root, rewrite_identity=True):
    """Plans all seven images. Nothing is written and nothing is copied."""
    plans = []
    for name, directory, destination_directory, leaf in IMAGES:
        source = os.path.join(source_root, directory, leaf)
        if not os.path.exists(source):
            raise PayloadError("%s: %s is not in the payload" % (name, source))
        data, rewrites = plan_image(source, name, rewrite_identity)
        plans.append((name, source, destination_directory, leaf, data, rewrites))
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
    claims to be when both are right. The rewrites are the seven identities plus
    the eleven edges between the seven; the preserved count is every dependency on
    a real system library, which must keep naming /System so it keeps binding the
    copies the process already has. A run that rewrote more, or preserved fewer,
    would be changing something this shape does not touch.
    """
    total = 0
    preserved = 0
    for name, _, _, _, data, rewrites in plans:
        rewritten = set(start for _, _, _, _, start, _ in rewrites)
        kept = []
        for offset, _, size, current in load_commands(data):
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
        print("  %-15s %d load command(s), %d /System reference(s) kept"
              % (name, len(rewrites), len(kept)))
        for _, command, current, replacement, start, capacity in rewrites:
            total += 1
            print("    %-8s slot %3d  now %3d  ->  %3d  %s"
                  % (describe(command), capacity, len(current), len(replacement), replacement))
    print("Planned %d rewrite(s) across %d image(s); %d /System reference(s) left byte "
          "for byte alone." % (total, len(plans), preserved))
    print("Nothing was written.")


def write_image(data, rewrites, destination, name):
    """Writes one rewritten copy and repairs its signature, holding both to the
    invariants they rely on.

    Each name is written at the offset its own command names and padded with
    nulls to the end of that command, so no other byte of the image moves and no
    load command grows. The result is then read back the way the loader reads it,
    so a write at a wrong offset fails here rather than producing a copy that
    names a file which does not exist.

    The signature repair comes after the names, because it hashes the bytes the
    names changed. Two properties are asserted before it runs, and both are what
    make a single pass correct: the file length is unchanged, so every page keeps
    its boundaries and the two CodeDirectories keep their offsets, and the
    signature lies past the last hashed byte, so writing page hashes cannot
    disturb the signature that holds them.
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
        if len(current) <= len(replacement):
            raise PayloadError("%s: %r is not strictly shorter than the name it replaces"
                               % (name, replacement))
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
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "wb") as handle:
        handle.write(repaired)


def copy_tree(source_root, staging_root):
    """Copies each framework directory wholesale, then the two bare dylibs.

    A framework is not only its Mach-O. WebKit carries Assets.car and localized
    strings, WebCore carries images and the blocked-page HTML, JavaScriptCore
    carries a sandbox profile and its Helpers directory. A destination holding
    only the seven binaries is not a framework tree, so the rest travels with
    them. The seven binaries are copied too and then overwritten by their
    rewritten forms, so nothing in the tree is a file this script invented.

    Nothing under the source root is written, opened for writing, or removed:
    the payload belongs to the package, and the destination root is a separate
    directory so that uninstalling this tree is removing one directory.
    """
    placed = 0
    for _, directory, destination_directory, _ in IMAGES:
        source_directory = os.path.join(source_root, directory)
        destination_directory = staged(staging_root, DESTINATION_ROOT + "/" + destination_directory)
        for current, _, files in os.walk(source_directory):
            relative = os.path.relpath(current, source_directory)
            target_directory = destination_directory if relative == "." else os.path.join(
                destination_directory, relative)
            os.makedirs(target_directory, exist_ok=True)
            for name in sorted(files):
                target = os.path.join(target_directory, name)
                if os.path.exists(target):
                    continue
                with open(os.path.join(current, name), "rb") as handle:
                    content = handle.read()
                with open(target, "wb") as handle:
                    handle.write(content)
                placed += 1
    return placed


def verify(staging_root, rewrite_identity=True):
    """Reads a staging root back and holds it to the properties it exists for.

    Each problem below is a failure the device would report as a symbol error
    rather than as a path error, which is how the last several rounds were lost:

      1. No image still names one of the seven by a /System path. That string is
         the bug the rewrite removes.
      2. Every destination path an image names exists in the tree. A rewrite
         pointing at a file that is not there fails at bind time with the same
         Symbol not found text as the bug it replaced.
      3. Under the full shape, the binary names itself by the path it occupies, so
         LC_ID_DYLIB and the location cannot disagree. Under --deps-only the
         identity is deliberately left as the system path, so the difference is
         reported and not failed: the check is a consistency requirement, not a
         demand that identity drift.

    The signature is the other property, and it is a failure rather than a note
    now that the repair happens here: every CodeDirectory must describe the bytes
    in the file, or the device kills the image on load. Where the signature sits
    relative to the last hashed byte is reported beside it, because that is what
    makes the one-pass repair correct.
    """
    problems = []
    signatures = []
    identity_kept = []
    if not os.path.isdir(staging_root):
        return ["%s does not exist" % staging_root], signatures, identity_kept
    for name, _, destination_directory, leaf in IMAGES:
        device_path = DESTINATION_ROOT + "/" + destination_directory + "/" + leaf
        path = staged(staging_root, device_path)
        if not os.path.exists(path):
            problems.append("%s: missing, expected at %s" % (name, device_path))
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        identity = None
        for _, command, _, current in load_commands(data):
            if current is None:
                continue
            if command == LC_ID_DYLIB:
                identity = current
                continue
            if current in SYSTEM_TO_DESTINATION:
                problems.append("%s: %s still names the system path %s"
                                % (name, describe(command), current))
                continue
            if current.startswith(DESTINATION_ROOT) and not os.path.exists(staged(staging_root, current)):
                problems.append("%s: names %s, which is not in the tree" % (name, current))
        if identity is None:
            problems.append("%s: carries no LC_ID_DYLIB" % name)
        elif identity != device_path:
            if rewrite_identity:
                problems.append("%s: calls itself %s but lives at %s" % (name, identity, device_path))
            else:
                identity_kept.append(name)
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
    return problems, signatures, identity_kept


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


def main(argv):
    usage = ("usage: rewrite-payload.py plan   SOURCE_ROOT STAGING_ROOT [--deps-only]\n"
             "       rewrite-payload.py write  SOURCE_ROOT STAGING_ROOT [--deps-only]\n"
             "       rewrite-payload.py verify        STAGING_ROOT [--deps-only]\n")
    if len(argv) < 2:
        sys.stderr.write(usage)
        return 2
    action = argv[1]
    arguments = [value for value in argv[2:] if value != "--deps-only"]
    rewrite_identity = "--deps-only" not in argv
    try:
        if action in ("plan", "write"):
            if len(arguments) != 2:
                sys.stderr.write(usage)
                return 2
            source_root, staging_root = arguments[0], os.path.abspath(arguments[1])
            if os.path.abspath(source_root) == staging_root:
                sys.stderr.write("%s is the payload itself; the destination root is "
                                 "%s, which is a separate directory\n"
                                 % (staging_root, DESTINATION_ROOT))
                return 2
            # The whole plan is built and every slot is checked before the first
            # byte is copied, so a payload that cannot fit leaves the staging
            # directory untouched instead of half written.
            plans = plan_payload(source_root, rewrite_identity)
            if action == "plan":
                if not rewrite_identity:
                    print("--deps-only: LC_ID_DYLIB is left claiming the system name, so the\n"
                          "seven match the resident system images by install name. This is a\n"
                          "mechanism probe, not a fix.")
                print_plan(plans)
                return 0
            placed = copy_tree(source_root, staging_root)
            total = 0
            for name, _, destination_directory, leaf, data, rewrites in plans:
                destination = staged(staging_root, DESTINATION_ROOT + "/"
                                     + destination_directory + "/" + leaf)
                write_image(data, rewrites, destination, name)
                total += len(rewrites)
                print("  %-15s %2d load command(s) rewritten, signature page hashes "
                      "recomputed" % (name, len(rewrites)))
            print("Wrote %d rewrite(s) across %d image(s) under %s; copied %d other file(s)."
                  % (total, len(plans), staged(staging_root, DESTINATION_ROOT), placed))
            print("Every copy's CodeDirectory page hashes describe the bytes written, which")
            print("`verify` reads back. That is a self-consistent signature, not a claim that")
            print("a device admits it: nothing has been loaded from a repaired copy yet.")
            return 0
        if action == "verify":
            if len(arguments) != 1:
                sys.stderr.write(usage)
                return 2
            problems, signatures, identity_kept = verify(os.path.abspath(arguments[0]),
                                                         rewrite_identity)
            for problem in problems:
                print("  FAIL %s" % problem)
            if identity_kept:
                print("  --deps-only: %s still claim their system identity, as this probe"
                      % ", ".join(identity_kept))
                print("        intends; the seven will match the resident system images first")
            for name, shape in signatures:
                print("  %s" % describe_signature(name, shape))
            if problems:
                return 1
            print("  no image names one of the seven by a system path, every named path "
                  "exists, and every signature describes its own bytes")
            return 0
        sys.stderr.write("unknown action %r\n" % action)
        sys.stderr.write(usage)
        return 2
    except PayloadError as error:
        sys.stderr.write("rewrite-payload: %s\n" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
