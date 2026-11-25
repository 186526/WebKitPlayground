#!/usr/bin/env python3
# Checks rewrite-payload.py against a payload this file builds, so the properties
# the rewrite depends on are read back from a written tree rather than taken from
# the script's own report.
#
# The payload is synthetic for the same reason tests/signature_test.py builds its
# dylib: the real one is a 50 MB install from a package, and a test that needed
# it could not run on a fresh checkout. The synthetic images carry the same
# fifteen load commands the seven real ones do where it matters — an
# LC_ID_DYLIB claiming the system identity, an LC_LOAD_DYLIB on the system
# JavaScriptCore, a dependency on a real system framework that must be left
# alone, and an LC_CODE_SIGNATURE past the load commands.
#
# What is checked, and the failure each one would cause on the device:
#
#   the seven paths here and in src/load_chain.c are one list
#                                      the tweak would load a file the rewrite
#                                      never wrote, and dyld would answer the
#                                      dlopen with the system twin
#   a replacement longer than its slot is refused
#                                      a write past the slot overwrites the next
#                                      load command and the image stops parsing
#   the file length is unchanged
#   LC_CODE_SIGNATURE keeps its offset and size
#                                      the signature moves and dyld refuses the
#                                      image before its constructor runs
#   only the bytes inside the rewritten slots differ
#                                      anything else changed is a byte nobody
#                                      planned, and the CodeDirectory covers it
#   the source payload is untouched
#                                      the package's own tree would be corrupted
#                                      in place instead of copied
#   each image names itself by the path it occupies
#                                      dyld matches install name first, so an
#                                      image that keeps the system name binds the
#                                      resident 16.1 twin
#   --deps-only leaves the identity alone and says so
#                                      the probe must measure the thing it claims
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

LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_CODE_SIGNATURE = 0x1D
MH_MAGIC_64 = 0xFEEDFACF
MH_DYLIB = 6
MACH_HEADER_SIZE_64 = 32
NAME_OFFSET = 24
SIGNATURE_OFFSET = 0x4000
SIGNATURE_SIZE = 0x100

# The system identity each of the seven carries as the payload ships, and the
# directory it occupies in a payload unpacked from the deb.
IMAGES = (
    ("JavaScriptCore", "Library/Frameworks/JavaScriptCore.framework", "JavaScriptCore",
     "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
    ("WebCore", "Library/Frameworks/WebCore.framework", "WebCore",
     "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"),
    ("WebKitLegacy", "Library/Frameworks/WebKitLegacy.framework", "WebKitLegacy",
     "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy"),
    ("WebGPU", "Library/Frameworks/WebGPU.framework", "WebGPU",
     "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU"),
    ("libwebrtc", "usr/lib", "libwebrtc.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
    ("libANGLE", "usr/lib", "libANGLE-shared.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib"),
    ("WebKit", "Library/Frameworks/WebKit.framework", "WebKit",
     "/System/Library/Frameworks/WebKit.framework/WebKit"),
)

JAVASCRIPTCORE_SYSTEM = "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"
FOUNDATION_SYSTEM = "/System/Library/Frameworks/Foundation.framework/Foundation"

# Wide enough for every replacement the rewrite writes, and the same for all
# seven so a failure is about the name rather than about a slot chosen to fit.
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
    size = NAME_OFFSET + slot
    body = struct.pack("<IIIIII", command, size, NAME_OFFSET, 0, 0x10000, 0x10000)
    return body + encoded + b"\0" * (slot - len(encoded))


def signature_command(dataoff, size):
    return struct.pack("<IIII", LC_CODE_SIGNATURE, 16, dataoff, size)


def build_signature(code, code_limit, hash_types=(1, 2), page=0x1000, flags=0):
    """A SuperBlob holding a requirements blob and one CodeDirectory per hashType.

    It mirrors the payload's shape: slot 2 with the requirements, then a slot per
    algorithm, both covering the same codeLimit with the same page count and the
    same flags word. A real payload puts SHA-1 at slot 0 and SHA-256 at slot 4096,
    so the test does too.

    The CodeDirectory is the 44-byte version (0x20001): nine 32-bit header fields,
    then hashSize, hashType, platform and pageSize as bytes, then spare2. Both
    special slots are written as zeros, as the payload writes them.
    """
    algorithms = {1: hashlib.sha1, 2: hashlib.sha256}
    n_code = (code_limit + page - 1) // page
    blobs = {2: struct.pack(">III", 0xFADE0C01, 12, 0)}
    for hash_type in hash_types:
        algorithm = algorithms[hash_type]
        hash_size = len(algorithm(b"").digest())
        identifier = b"test\0"
        header_size = 44
        hash_offset = header_size + 2 * hash_size
        ident_offset = hash_offset + n_code * hash_size
        body = b"".join(
            algorithm(code[index * page:min(index * page + page, code_limit)]).digest()
            for index in range(n_code))
        blob = struct.pack(">9IBBBB I", 0xFADE0C02, header_size + 2 * hash_size +
                           n_code * hash_size + len(identifier),
                           0x20001, flags, hash_offset, ident_offset, 2, n_code, code_limit,
                           hash_size, hash_type, 0, page.bit_length() - 1, 0)
        blob += b"\0" * (2 * hash_size) + body + identifier
        blobs[0 if hash_type == 1 else 4096] = blob
    count = len(blobs)
    index_size = 12 + 8 * count
    offset = index_size
    entries = b""
    bodies = b""
    for slot in sorted(blobs):
        entries += struct.pack(">II", slot, offset)
        bodies += blobs[slot]
        offset += len(blobs[slot])
    return struct.pack(">III", 0xFADE0CC0, index_size + len(bodies), count) + entries + bodies


def build_image(path, identity, dependencies, slot=SLOT, sign=True):
    """A thin Mach-O whose signature covers every byte before its own offset.

    The CodeDirectory lands at a fixed offset in all seven images, and the test's
    invariants compare the signature command before and after a rewrite, so the
    blob's own size has to be the same every time it is built. `build_signature`
    gives that: its length depends only on codeLimit and the page size.
    """
    commands = [name_command(LC_ID_DYLIB, identity, slot)]
    for dependency in dependencies:
        commands.append(name_command(LC_LOAD_DYLIB, dependency, slot))
    if sign:
        head = b"".join(commands) + signature_command(0, 0)
        header = struct.pack("<IiIIIIII", MH_MAGIC_64, 0x0100000C, 0x0, MH_DYLIB,
                             len(commands) + 1, len(head), 0, 0)
        data = bytearray(header + head)
        if len(data) > SIGNATURE_OFFSET:
            raise RuntimeError("the load commands do not fit before the signature")
        data.extend(b"\0" * (SIGNATURE_OFFSET - len(data)))
        data.extend(build_signature(bytes(data), SIGNATURE_OFFSET))
        blob_length = len(data) - SIGNATURE_OFFSET
        marker = struct.pack("<II", LC_CODE_SIGNATURE, 16)
        index = bytes(data).index(marker)
        data[index:index + 16] = signature_command(SIGNATURE_OFFSET, blob_length)
        # The datasize field is inside the hashed region, so the signature is built
        # once more over the header's final form.
        del data[SIGNATURE_OFFSET:]
        data.extend(build_signature(bytes(data), SIGNATURE_OFFSET))
    else:
        commands.append(signature_command(SIGNATURE_OFFSET, SIGNATURE_SIZE))
        head = b"".join(commands)
        header = struct.pack("<IiIIIIII", MH_MAGIC_64, 0x0100000C, 0x0, MH_DYLIB,
                             len(commands), len(head), 0, 0)
        data = bytearray(header + head)
        data.extend(b"\0" * (SIGNATURE_OFFSET - len(data)))
        data.extend(b"\0" * SIGNATURE_SIZE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(bytes(data))


def build_payload(root, slot=SLOT):
    for _, directory, leaf, identity in IMAGES:
        dependencies = [FOUNDATION_SYSTEM]
        if identity != JAVASCRIPTCORE_SYSTEM:
            dependencies.append(JAVASCRIPTCORE_SYSTEM)
        build_image(os.path.join(root, directory, leaf), identity, dependencies, slot)


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def digest(path):
    return hashlib.sha256(read(path)).hexdigest()


def run(*arguments):
    return subprocess.run([sys.executable, SCRIPT] + list(arguments),
                          capture_output=True, text=True)


def source_paths(module):
    """The path each image occupies under the destination root, keyed by name.

    IMAGES is ordered for reading, not for loading, so it answers which file
    lives where rather than which comes first. The order is DESTINATION_PATHS,
    and both are compared against the chain: this one as a set, that one in
    sequence.
    """
    return {name: module.DESTINATION_ROOT + "/" + destination_directory + "/" + leaf
            for name, _, destination_directory, leaf in module.IMAGES}


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


def main():
    spec = importlib.util.spec_from_file_location("rewrite_payload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    paths = source_paths(module)
    chain = load_chain_paths()
    check("every path src/load_chain.c spells is one of the seven files",
          set(chain) == set(paths.values()),
          "chain %r, files %r" % (chain, sorted(paths.values())))
    check("the chain names the seven in the order rewrite-payload.py lists them",
          chain == module.DESTINATION_PATHS)

    with tempfile.TemporaryDirectory() as work:
        payload = os.path.join(work, "payload")
        staging = os.path.join(work, "staging")
        build_payload(payload)
        before = {path: digest(path) for path in walk(payload)}

        result = run("plan", payload, staging)
        check("plan exits 0 on a payload that fits", result.returncode == 0, result.stderr)
        check("plan writes nothing", not os.path.exists(staging))
        check("plan counts the rewrites and the preserved /System references apart",
              "Planned 13 rewrite(s)" in result.stdout
              and "7 /System reference(s) left byte for byte alone" in result.stdout,
              result.stdout)

        result = run("write", payload, staging)
        check("write exits 0", result.returncode == 0, result.stderr)
        check("write leaves the source payload byte for byte as it was",
              before == {path: digest(path) for path in walk(payload)})

        # Thirteen edges in this synthetic tree: seven identities and six
        # dependencies on the system JavaScriptCore — every image except
        # JavaScriptCore itself. The one dependency on a real system framework
        # each image carries is what the preserved count above stands for.
        plans = module.plan_payload(payload)
        total = sum(len(rewrites) for _, _, _, _, _, rewrites in plans)
        check("every planned rewrite is an identity or a dependency on the seven",
              total == 13, str(total))

        problems, signed, identity_kept = module.verify(staging)
        check("verify finds no problem in the written tree", not problems, repr(problems))
        check("all seven still carry LC_CODE_SIGNATURE", len(signed) == 7, repr(signed))
        check("the full shape leaves no identity claiming a system path",
              not identity_kept, repr(identity_kept))

        for name, _, destination_directory, leaf, _, rewrites in plans:
            device_path = module.DESTINATION_ROOT + "/" + destination_directory + "/" + leaf
            original = read(os.path.join(payload, source_directory(name), leaf))
            rewritten = read(os.path.join(staging, device_path.lstrip("/")))
            check("%s: length unchanged" % name, len(original) == len(rewritten),
                  "%d vs %d" % (len(original), len(rewritten)))
            check("%s: LC_CODE_SIGNATURE offset and size unchanged" % name,
                  module.signature_command(original) == module.signature_command(rewritten))
            touched = set()
            for _, _, _, _, start, capacity in rewrites:
                touched.update(range(start, start + capacity))
            # The repair writes page hashes, so those byte ranges are expected to
            # change too. Nothing else may: a byte changed outside the rewritten
            # slots and the page-hash arrays is a byte nobody planned, and the
            # CodeDirectory covers it.
            for base, _, hash_offset, hash_size, _, n_code, _, _ in module.code_directories(original):
                touched.update(range(base + hash_offset,
                                     base + hash_offset + n_code * hash_size))
            outside = [index for index in range(len(original))
                       if index not in touched and original[index] != rewritten[index]]
            check("%s: nothing changed outside the rewritten slots and the page hashes"
                  % name, not outside, "%d byte(s), first at %s" % (len(outside), outside[:4]))
            check("%s: every CodeDirectory describes the bytes written" % name,
                  not module.signature_stale(rewritten))
            check("%s: both CodeDirectories are still present" % name,
                  len(list(module.code_directories(rewritten))) ==
                  len(list(module.code_directories(original))))
            if rewrites:
                check("%s: every byte after its name inside the slot is null" % name,
                      all_all_zero_after_name(rewritten, rewrites))

        # The identity check reads the written file the way dyld does.
        for name, _, destination_directory, leaf in module.IMAGES:
            device_path = module.DESTINATION_ROOT + "/" + destination_directory + "/" + leaf
            identity = identity_of(read(os.path.join(staging, device_path.lstrip("/"))))
            check("%s: calls itself the path it occupies" % name,
                  identity == device_path, repr(identity))

        # The capacity guard, exercised in process because the seven real
        # replacements are all strictly shorter than the names they replace: no
        # payload built from the real strings can reach it. The guard is what
        # stands between a longer rewrite and a write that runs into the next
        # load command, so it is held to a replacement that does not fit.
        tight = os.path.join(work, "tight")
        build_image(os.path.join(tight, "Library/Frameworks/WebKit.framework", "WebKit"),
                    IMAGES[6][3], [FOUNDATION_SYSTEM], slot=SLOT)
        inflated = dict(module.SYSTEM_TO_DESTINATION)
        inflated[IMAGES[6][3]] = module.DESTINATION_ROOT + "/" + "W" * SLOT
        original_mapping = module.SYSTEM_TO_DESTINATION
        module.SYSTEM_TO_DESTINATION = inflated
        try:
            module.plan_image(os.path.join(tight, "Library/Frameworks/WebKit.framework",
                                           "WebKit"), "WebKit", True)
        except module.PayloadError as error:
            check("a replacement longer than its slot is refused",
                  "refusing to write" in str(error), str(error))
            check("the refusal names the slot and the name",
                  "slot" in str(error) and str(SLOT) in str(error), str(error))
        else:
            check("a replacement longer than its slot is refused", False, "it was accepted")
        finally:
            module.SYSTEM_TO_DESTINATION = original_mapping

        # Refusal happens while planning, so the tree is never copied.
        short = os.path.join(work, "short")
        build_image(os.path.join(short, "Library/Frameworks/WebKit.framework", "WebKit"),
                    IMAGES[6][3], [FOUNDATION_SYSTEM], slot=SLOT)
        module.SYSTEM_TO_DESTINATION = inflated
        try:
            module.plan_payload(short)
        except module.PayloadError:
            blocked = os.path.join(work, "blocked")
            check("planning stops before anything is copied",
                  not os.path.exists(blocked))
        finally:
            module.SYSTEM_TO_DESTINATION = original_mapping

        # --deps-only is the probe: it must leave the identity claiming the system
        # name, and verify must report that as the probe's intent rather than as a
        # problem.
        probe = os.path.join(work, "probe")
        result = run("write", payload, probe, "--deps-only")
        check("--deps-only exits 0", result.returncode == 0, result.stderr)
        problems, _, identity_kept = module.verify(probe, rewrite_identity=False)
        check("--deps-only rewrites the six dependencies and no identity",
              count_rewrites(probe, module) == 6, str(count_rewrites(probe, module)))
        check("--deps-only is reported as keeping all seven system identities",
              len(identity_kept) == 7, repr(identity_kept))
        check("--deps-only raises no problem of its own", not problems, repr(problems))
        strict, _, kept = module.verify(probe)
        check("the full-shape verify rejects the same tree",
              len(strict) == 7 and not kept, repr(strict[:2]))

        # Writing onto the payload itself would corrupt the package's tree.
        result = run("write", payload, payload)
        check("write refuses to use the payload as its own destination",
              result.returncode == 2, "exit %d" % result.returncode)

        # A signature with one altered page hash is a different failure from a
        # missing one, and the repair must not be able to paper over it: the check
        # below makes `verify` report it as a hash problem.
        broken = os.path.join(work, "broken", "var", "jb", "Library", "WebKit18",
                              "WebKit.framework")
        broken_image = os.path.join(broken, "WebKit")
        build_image(broken_image, IMAGES[6][3], [FOUNDATION_SYSTEM])
        data = bytearray(read(broken_image))
        base, _, hash_offset, _, _, _, _, _ = next(iter(module.code_directories(data)))
        data[base + hash_offset] ^= 0xFF
        with open(broken_image, "wb") as handle:
            handle.write(bytes(data))
        check("a signature with one altered page hash is reported",
              bool(module.signature_stale(bytes(data))))

        # The rewrite is what makes pages stale, and the repair clears exactly that.
        # This is the direct reading the write path's assertion rests on: before the
        # repair the stale set is the rewritten pages, after it the set is empty.
        original = read(os.path.join(payload, "Library/Frameworks/WebKit.framework", "WebKit"))
        webkit_plan = next(plan for plan in module.plan_payload(payload) if plan[0] == "WebKit")
        rewrites = webkit_plan[5]
        staged_copy = bytearray(original)
        for _, _, _, replacement, start, capacity in rewrites:
            encoded = replacement.encode()
            staged_copy[start:start + capacity] = encoded + b"\0" * (capacity - len(encoded))
        repaired, stale_seen = module.repair_signature(bytes(staged_copy))
        page = next(iter(module.code_directories(bytes(staged_copy))))[7]
        expected = set()
        for _, _, _, _, start, capacity in rewrites:
            for index in range(start, start + capacity):
                expected.add(index // page)
        found = set(index for _, index in stale_seen)
        check("the pages stale before the repair are the pages the rewrite changed",
              found == expected, "%s vs %s" % (sorted(found), sorted(expected)))
        check("the repair leaves every page hash matching", not module.signature_stale(repaired))
        check("the repair changes nothing when the signature already matches",
              module.repair_signature(original)[0] == original)

    return report()


def walk(root):
    for current, _, files in os.walk(root):
        for name in sorted(files):
            yield os.path.join(current, name)


def source_directory(name):
    for image_name, directory, _, _ in IMAGES:
        if image_name == name:
            return directory
    raise KeyError(name)


def identity_of(data):
    offset = MACH_HEADER_SIZE_64
    count, _ = struct.unpack_from("<II", data, 16)
    for _ in range(count):
        command, size = struct.unpack_from("<II", data, offset)
        if command == LC_ID_DYLIB:
            name_offset, = struct.unpack_from("<I", data, offset + 8)
            start = offset + name_offset
            end = data.index(b"\0", start)
            return data[start:end].decode()
        offset += size
    return None


def all_all_zero_after_name(data, rewrites):
    for _, _, _, replacement, start, capacity in rewrites:
        encoded = replacement.encode()
        tail = data[start + len(encoded):start + capacity]
        if tail.strip(b"\0"):
            return False
    return True


def count_rewrites(root, module):
    """How many load commands in a written tree still name a system path they
    should not, inverted: how many of the nine no longer do."""
    rewritten = 0
    for _, directory, destination_directory, leaf in module.IMAGES:
        device_path = module.DESTINATION_ROOT + "/" + destination_directory + "/" + leaf
        data = read(os.path.join(root, device_path.lstrip("/")))
        offset = MACH_HEADER_SIZE_64
        count, _ = struct.unpack_from("<II", data, 16)
        for _ in range(count):
            command, size = struct.unpack_from("<II", data, offset)
            if command in (LC_LOAD_DYLIB, LC_ID_DYLIB):
                name_offset, = struct.unpack_from("<I", data, offset + 8)
                start = offset + name_offset
                end = data.index(b"\0", start)
                name = data[start:end].decode()
                if name not in module.SYSTEM_TO_DESTINATION and name.startswith(
                        module.DESTINATION_ROOT):
                    rewritten += 1
            offset += size
    return rewritten


def report():
    if failures:
        print("\n%d rewrite-payload check(s) failed" % len(failures))
        return 1
    print("\nrewrite-payload: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
