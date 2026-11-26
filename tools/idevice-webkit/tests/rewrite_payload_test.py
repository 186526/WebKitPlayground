#!/usr/bin/env python3
# Checks rewrite-payload.py against a payload this file builds, so the properties
# the rewrite depends on are read back from a written tree rather than taken from
# the script's own report.
#
# The payload is synthetic for the same reason tests/signature_test.py builds its
# dylib: the real one is a 200 MB install from a package, and a test that needed
# it could not run on a fresh checkout. The synthetic images carry the commands
# the seven real ones carry where it matters — the recorded LC_ID_DYLIB, edges on
# the siblings, a dependency on a real system framework that must be left alone,
# and an LC_CODE_SIGNATURE past the load commands.
#
# What is checked, and the failure each one would cause on the device:
#
#   the seven paths here and in src/load_chain.c are one list
#                                      the tweak would load a file the rewrite
#                                      never touched, and dyld would answer the
#                                      dlopen with the system twin
#   the tree is rewritten in place, and no second tree appears
#                                      a copy at a path the filter does not name
#                                      is a tree nothing loads
#   eleven edges are rewritten
#                                      an edge left naming /System binds the 16.1
#                                      sibling, which is the recorded failure
#   every LC_ID_DYLIB is unchanged
#                                      an identity rewritten to /var/jb can never
#                                      be returned for a /System request, so the
#                                      dedup that makes the image reachable stops
#   a replacement longer than its slot is refused
#                                      a write past the slot overwrites the next
#                                      load command and the image stops parsing
#   the file length is unchanged
#   LC_CODE_SIGNATURE keeps its offset and size
#                                      the signature moves and dyld refuses the
#                                      image before its constructor runs
#   every CodeDirectory describes the bytes written
#                                      the device kills a modified image, exit 137
#   a rewritten tree is a fixed point
#                                      a second run must ship the same bytes the
#                                      first one produced
import hashlib
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "rewrite-payload.py")
LOAD_CHAIN = os.path.join(HERE, "..", "src", "load_chain.c")

LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_CODE_SIGNATURE = 0x1D
MH_MAGIC_64 = 0xFEEDFACF
MH_DYLIB = 6
MACH_HEADER_SIZE_64 = 32
NAME_OFFSET = 24
SIGNATURE_OFFSET = 0x4000
SIGNATURE_SIZE = 0x1000

DYLIB_COMMANDS = (LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB)

# The seven as the device carries them on 2026-11-26: path relative to /var/jb and
# the identity each binary claims. The five frameworks sit under
# Library/Frameworks and the two bare dylibs under usr/lib, which is where the
# installed package puts them.
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

# The command each edge uses on the device, measured 2026-11-26 02:03 CST. WebKit
# reaches WebKitLegacy through LC_REEXPORT_DYLIB, not a load, which is why the
# test builds one: a rewrite that skipped re-exports would leave the 16.1
# WebKitLegacy in the process.
EDGES = {
    "JavaScriptCore": (),
    "WebCore": (
        (LC_LOAD_WEAK_DYLIB, "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU"),
        (LC_LOAD_WEAK_DYLIB,
         "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib"),
        (LC_LOAD_WEAK_DYLIB,
         "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
        (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
    ),
    "WebKitLegacy": (
        (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
        (LC_LOAD_DYLIB, "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"),
    ),
    "WebGPU": (
        (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
    ),
    "libwebrtc": (),
    "libANGLE": (),
    "WebKit": (
        (LC_REEXPORT_DYLIB,
         "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy"),
        (LC_LOAD_WEAK_DYLIB,
         "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
        (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
        (LC_LOAD_DYLIB, "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"),
    ),
}

EDGE_COUNT = sum(len(commands) for commands in EDGES.values())

FOUNDATION_SYSTEM = "/System/Library/Frameworks/Foundation.framework/Foundation"

# The line `verify` prints only when it has no problem left to report. A tree it
# refuses must not print it, whatever else it prints.
SUCCESS_LINE = "every edge among the seven names a /var/jb path that exists"

# Wide enough for every replacement the rewrite writes, and the same for all seven
# so a failure is about the name rather than about a slot chosen to fit.
SLOT = 128

failures = []


def check(name, condition, detail=""):
    if condition:
        print("%-62s pass" % name)
    else:
        print("%-62s FAIL %s" % (name, detail))
        failures.append(name)


def name_command(command, name, slot=SLOT):
    encoded = name.encode() + b"\0"
    assert len(encoded) <= slot, "%r does not fit the %d-byte slot this builds" % (name, slot)
    size = NAME_OFFSET + slot
    body = struct.pack("<IIIIII", command, size, NAME_OFFSET, 0, 0x10000, 0x10000)
    return body + encoded + b"\0" * (slot - len(encoded))


def signature_command(dataoff, size):
    return struct.pack("<IIII", LC_CODE_SIGNATURE, 16, dataoff, size)


def code_directory(algorithm, hash_type, slot, code, code_limit, page, flags=0):
    """One CodeDirectory over `code`, laid out as the payload's are.

    Two special slots are written as zeros, as the payload writes them; neither is
    a page hash, and the repair in rewrite-payload.py does not recompute them.
    """
    hash_size = algorithm().digest_size
    pages = (code_limit + page - 1) // page
    special_slots = 2
    hash_offset = 44 + special_slots * hash_size
    body = bytearray(44)
    struct.pack_into(">IIIIIIIII", body, 0, 0xFADE0C02, 0, 0x20001, flags, hash_offset,
                     0, special_slots, pages, code_limit)
    struct.pack_into(">BBBB", body, 36, hash_size, hash_type, 0, page.bit_length() - 1)
    body += b"\0" * (special_slots * hash_size)
    for index in range(pages):
        body += algorithm(code[index * page:min((index + 1) * page, code_limit)]).digest()
    struct.pack_into(">II", body, 4, len(body), slot)
    return bytes(body)


def build_signature(code, code_limit, page=0x1000, flags=0):
    """A SuperBlob holding a requirements blob and one CodeDirectory per hashType.

    It mirrors the payload's shape: slot 2 with the requirements, then a slot per
    algorithm, both covering the same codeLimit with the same page count and the
    same flags word. A real payload puts SHA-1 at slot 0 and SHA-256 at slot 4096,
    so the test does too.
    """
    import hashlib

    blobs = [
        (2, b"\xfa\xde\x0c\x01" + struct.pack(">I", 16) + b"\0" * 8),
        (0, code_directory(hashlib.sha1, 1, 0, code, code_limit, page, flags)),
        (4096, code_directory(hashlib.sha256, 2, 4096, code, code_limit, page, flags)),
    ]
    offset = 12 + 8 * len(blobs)
    index = b""
    body = b""
    for slot, blob in blobs:
        index += struct.pack(">II", slot, offset + len(body))
        body += blob
    return struct.pack(">III", 0xFADE0CC0,
                       12 + len(index) + len(body), len(blobs)) + index + body


def build_image(path, identity, dependencies, slot=SLOT, dependency_slot=None):
    """One thin little-endian 64-bit image with the commands the test needs.

    `dependency_slot` lets one case give the dependencies a slot narrower than the
    identities, which is how the capacity guard is reached: the guard is about the
    name being replaced, not about every command in the image.
    """
    if dependency_slot is None:
        dependency_slot = slot
    commands = [name_command(LC_ID_DYLIB, identity, slot)]
    for command, name in dependencies:
        commands.append(name_command(command, name, dependency_slot))
    commands.append(name_command(LC_LOAD_DYLIB, FOUNDATION_SYSTEM, slot))
    body = b"".join(commands)
    segment_and_section = struct.pack("<II16sQQQQiiII", LC_SEGMENT_64, 72 + 80, b"__TEXT",
                                      0, 0x10000, 0, 0x10000, 5, 5, 1, 0) + struct.pack(
        "<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0x1000, 0x10, 0x1000, 4, 0, 0, 0, 0, 0, 0)
    # LC_CODE_SIGNATURE is the last command so its dataoff and datasize are known
    # before the code region is hashed.
    signature_text = signature_command(SIGNATURE_OFFSET, SIGNATURE_SIZE)
    command_bytes = body + signature_text + segment_and_section
    image = struct.pack("<IiiIIIII", MH_MAGIC_64, 0x0100000C, 0, MH_DYLIB,
                        len(commands) + 2, len(command_bytes), 0, 0) + command_bytes
    assert len(image) <= SIGNATURE_OFFSET
    image += b"\0" * (SIGNATURE_OFFSET - len(image))
    signature = build_signature(image, SIGNATURE_OFFSET)
    assert len(signature) <= SIGNATURE_SIZE, len(signature)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(image + signature + b"\0" * (SIGNATURE_SIZE - len(signature)))


def build_payload(root, prefix="/var/jb", slot=SLOT):
    for name, relative, identity in IMAGES:
        build_image(os.path.join(root, (prefix + "/" + relative).lstrip("/")),
                    identity, EDGES[name], slot)


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def state(root):
    """Every file under a root with its size and digest, for before/after reads."""
    return {os.path.relpath(path, root): (os.path.getsize(path),
                                         hashlib.sha256(read(path)).hexdigest())
            for path in walk(root)}


def walk(root):
    for current, _, files in os.walk(root):
        for name in sorted(files):
            yield os.path.join(current, name)


def run(*arguments):
    return subprocess.run([sys.executable, SCRIPT] + list(arguments),
                          capture_output=True, text=True)


def member_paths(prefix="/var/jb"):
    return {name: prefix + "/" + relative for name, relative, _ in IMAGES}


def load_chain_paths():
    """The seven strings src/load_chain.c spells, read out of the C source."""
    with open(LOAD_CHAIN, "r") as handle:
        text = handle.read()
    begin = text.index("load_chain_paths[load_chain_count] = {")
    end = text.index("};", begin)
    paths = []
    for line in text[begin:end].splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        paths.append(line[line.index('"') + 1:line.rindex('"')])
    return tuple(paths)


def commands_of(data):
    """(offset, command, size, name) for every dylib command, read from the header."""
    count, _ = struct.unpack_from("<II", data, 16)
    offset = MACH_HEADER_SIZE_64
    for _ in range(count):
        command, size = struct.unpack_from("<II", data, offset)
        name = None
        if command in DYLIB_COMMANDS:
            name_offset, = struct.unpack_from("<I", data, offset + 8)
            start = offset + name_offset
            name = data[start:data.index(b"\0", start)].decode()
        yield offset, command, size, name
        offset += size


def identity_of(data):
    for _, command, _, name in commands_of(data):
        if command == LC_ID_DYLIB:
            return name
    return None


def dependency_names(data):
    return [name for _, command, _, name in commands_of(data)
            if command != LC_ID_DYLIB and name is not None]


def put_name(data, offset, size, replacement):
    """Writes a name into a command's slot, the way the rewrite does, and returns it."""
    name_offset, = struct.unpack_from("<I", data, offset + 8)
    start = offset + name_offset
    capacity = offset + size - start
    encoded = replacement.encode() + b"\0"
    assert len(encoded) <= capacity
    data[start:start + capacity] = encoded + b"\0" * (capacity - len(encoded))
    return data


def main():
    spec = importlib.util.spec_from_file_location("rewrite_payload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The script, this file and the C source agree on the layout, then on the
    # identities. A path only one of the three knows is a tree nobody loads.
    script_paths = {name: module.device_path(module.DEFAULT_PREFIX, relative)
                    for name, relative, _ in module.IMAGES}
    check("rewrite-payload.py lists the seven where the device carries them",
          script_paths == member_paths(), "%r" % script_paths)
    check("rewrite-payload.py records the identity each binary carries",
          [(name, identity) for name, _, identity in module.IMAGES]
          == [(name, identity) for name, _, identity in IMAGES])
    check("the test's edge table is the eleven the device carries",
          module.MEASURED_EDGES == EDGE_COUNT and EDGE_COUNT == 11, str(EDGE_COUNT))

    chain = load_chain_paths()
    check("every path src/load_chain.c spells is one of the seven files",
          set(chain) == set(script_paths.values()),
          "chain %r, files %r" % (chain, sorted(script_paths.values())))
    # The chain's order is not IMAGES' order: IMAGES is grouped for reading, while
    # the chain follows the measured load order, so it is compared against
    # LOAD_ORDER. A path only one of the two knows already failed above.
    by_name = {name: path for name, path in script_paths.items()}
    check("the chain names the seven in the measured load order",
          list(chain) == [by_name[name] for name in module.LOAD_ORDER],
          "chain %r" % (chain,))

    with tempfile.TemporaryDirectory() as work:
        root = os.path.join(work, "payload")
        build_payload(root)
        before = state(root)

        result = run("plan", root)
        check("plan exits 0 on a payload that fits", result.returncode == 0, result.stderr)
        check("plan writes nothing", before == state(root))
        check("plan counts eleven edges", "Planned 11 edge(s)" in result.stdout, result.stdout)
        check("plan counts the preserved system references apart",
              "7 /System reference(s) left byte for byte alone" in result.stdout,
              result.stdout)

        result = run("rewrite", root)
        check("rewrite exits 0", result.returncode == 0, result.stderr)
        check("rewrite reports eleven edges", "Rewrote 11 edge(s)" in result.stdout,
              result.stdout)
        after = state(root)
        check("rewrite adds no file and removes none", sorted(before) == sorted(after))
        check("rewrite keeps every file's length",
              all(before[name][0] == after[name][0] for name in before),
              [name for name in before if before[name][0] != after[name][0]])
        check("rewrite leaves the signature past the hashed region",
              all(module.signature_command(read(os.path.join(root, name)))[0]
                  >= min(limit for _, _, _, _, _, _, limit, _
                         in module.code_directories(read(os.path.join(root, name))))
                  for name in after))

        problems, report = module.verify(root)
        check("verify finds no problem in the rewritten tree", not problems, repr(problems))
        check("verify counts eleven /var/jb edges", report["edges"] == 11, str(report["edges"]))
        check("verify finds all seven identities unchanged",
              report["identities"] == 7 and not report["mismatched"],
              repr(report["mismatched"]))
        check("all seven still carry LC_CODE_SIGNATURE", len(report["signatures"]) == 7)

        for name, relative, identity in IMAGES:
            path = os.path.join(root, (module.DEFAULT_PREFIX + "/" + relative).lstrip("/"))
            data = read(path)
            check("%s: still calls itself %s" % (name, identity),
                  identity_of(data) == identity, repr(identity_of(data)))
            check("%s: no dependency on a sibling still names /System" % name,
                  not [current for current in dependency_names(data)
                       if current in module.SYSTEM_ALIASES],
                  [current for current in dependency_names(data)
                   if current in module.SYSTEM_ALIASES])
            check("%s: every edge names a file in the tree" % name,
                  all(os.path.exists(os.path.join(root, current.lstrip("/")))
                      for current in dependency_names(data)
                      if current in set(member_paths().values())))
            check("%s: Foundation keeps its system path" % name,
                  FOUNDATION_SYSTEM in dependency_names(data))
            check("%s: every CodeDirectory describes the bytes written" % name,
                  not module.signature_stale(data))
            check("%s: LC_CODE_SIGNATURE offset and size unchanged" % name,
                  module.signature_command(data) == (SIGNATURE_OFFSET, SIGNATURE_SIZE))

        # The capacity guard. The real replacements all fit, so the guard is
        # exercised with a short source name mapped to a member: the replacement
        # cannot fit the slot that held it, and a write that overran would
        # overwrite the next load command.
        tight = os.path.join(work, "tight", "var/jb/usr/lib/libwebrtc.dylib")
        build_image(tight, IMAGES[4][2], [(LC_LOAD_DYLIB, "/S/JSC")],
                    slot=SLOT, dependency_slot=16)
        module.SYSTEM_ALIASES["/S/JSC"] = "JavaScriptCore"
        try:
            module.plan_image(tight, "libwebrtc", module.DEFAULT_PREFIX)
        except module.PayloadError as error:
            check("a replacement longer than its slot is refused",
                  "refusing to write" in str(error), str(error))
            check("the refusal names the slot", "slot" in str(error), str(error))
        else:
            check("a replacement longer than its slot is refused", False, "it was accepted")
        finally:
            del module.SYSTEM_ALIASES["/S/JSC"]

        # A tree with one edge back on /System is refused, which is the reading
        # the device gives as Symbol not found.
        partial = os.path.join(work, "partial")
        shutil.copytree(root, partial)
        path = os.path.join(partial, "var/jb/Library/Frameworks/WebKit.framework/WebKit")
        data = bytearray(read(path))
        for offset, command, size, name in commands_of(bytes(data)):
            if name == "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore":
                put_name(data, offset, size,
                         "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore")
                break
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        problems, _ = module.verify(partial)
        check("a tree with one edge back on /System is refused",
              any("still names the system path" in problem for problem in problems),
              repr(problems[:3]))

        # A tree whose identity moved is refused: an identity rewritten to the
        # /var/jb path can never answer a /System request.
        moved = os.path.join(work, "moved")
        shutil.copytree(root, moved)
        path = os.path.join(moved,
                            "var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore")
        data = bytearray(read(path))
        for offset, command, size, name in commands_of(bytes(data)):
            if command == LC_ID_DYLIB:
                put_name(data, offset, size,
                         "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore")
                break
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        problems, _ = module.verify(moved)
        check("a tree whose identity moved off the system name is refused",
              any("the recorded identity is" in problem for problem in problems),
              repr(problems[:3]))

        # A signature whose page hash no longer describes the bytes is reported.
        broken = os.path.join(work, "broken")
        shutil.copytree(root, broken)
        path = os.path.join(broken, "var/jb/Library/Frameworks/WebKit.framework/WebKit")
        data = bytearray(read(path))
        base, _, hash_offset, _, _, _, _, _ = next(iter(module.code_directories(bytes(data))))
        data[base + hash_offset] ^= 0xFF
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        problems, _ = module.verify(broken)
        check("a tree with one altered page hash is refused",
              any("do not describe the file" in problem for problem in problems),
              repr(problems[:3]))

        # A tree with one image gone must fail the reading the packager gates on.
        # The counts fall with the image, so checking them only when nothing is
        # missing left this one tree passing with a smaller number.
        short = os.path.join(work, "short")
        shutil.copytree(root, short)
        os.remove(os.path.join(short, "var/jb/Library/Frameworks/WebKit.framework/WebKit"))
        problems, report = module.verify(short)
        check("a tree missing WebKit is refused",
              any("WebKit: missing" in problem for problem in problems), repr(problems[:3]))
        # WebKit carries four of the eleven edges, so the count a reader sees is
        # the seven the other six images still name.
        check("a tree missing WebKit falls short of eleven edges",
              report["edges"] == 7, str(report["edges"]))
        check("a tree missing WebKit fails the edge count",
              any("7 /var/jb edge(s)" in problem for problem in problems),
              repr(problems[:3]))
        check("a tree missing WebKit fails the identity count",
              any("6 of 7 identities" in problem for problem in problems),
              repr(problems[:3]))
        result = run("verify", short)
        check("verify exits 1 on a tree missing WebKit", result.returncode == 1,
              "%d %s" % (result.returncode, result.stdout))
        check("verify prints no success line for a tree missing WebKit",
              SUCCESS_LINE not in result.stdout, result.stdout)

        # An empty tree is the same failure at its extreme, and the one a caller
        # reaches by pointing at the wrong directory.
        empty = os.path.join(work, "empty")
        os.mkdir(empty)
        result = run("verify", empty)
        check("verify exits 1 on an empty tree", result.returncode == 1,
              "%d %s" % (result.returncode, result.stdout))
        check("verify names all seven as missing in an empty tree",
              result.stdout.count("missing, expected at") == 7,
              str(result.stdout.count("missing, expected at")))
        # One line per missing image, whatever its wording: seven images that are
        # all absent must produce seven lines and not fourteen. Counting a wording
        # instead was what let the duplicate go unnoticed, since the second copy
        # said "FAIL missing X: expected at" and the first said "X: missing".
        missing_lines = [line for line in result.stdout.splitlines()
                         if "expected at" in line and "missing" in line]
        check("verify prints each of the seven missing images on one line",
              len(missing_lines) == 7, "%d line(s): %r" % (len(missing_lines), missing_lines))
        check("verify prints no second wording for a missing image",
              "FAIL missing " not in result.stdout, result.stdout)
        check("verify fails both counts on an empty tree",
              "0 /var/jb edge(s) among the seven, 11 expected" in result.stdout
              and "0 of 7 LC_ID_DYLIB" in result.stdout, result.stdout)
        check("verify prints no success line for an empty tree",
              SUCCESS_LINE not in result.stdout, result.stdout)

        # A second run over a rewritten tree changes nothing: the tree a package
        # ships is the fixed point of the rewrite.
        settled = state(root)
        result = run("rewrite", root)
        check("rewriting a rewritten tree exits 0", result.returncode == 0, result.stderr)
        check("rewriting a rewritten tree changes no byte", settled == state(root))
        check("rewriting a rewritten tree reports no edge",
              "Rewrote 0 edge(s)" in result.stdout, result.stdout)

    return report_all()


def report_all():
    if failures:
        print("\n%d rewrite-payload check(s) failed" % len(failures))
        return 1
    print("\nrewrite-payload: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
