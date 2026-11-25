#!/usr/bin/env python3
"""Inspect and check the code signature a Mach-O image ships.

Subcommands:

  survey <root>
      Print one line per Mach-O image under <root>, tab separated:
        path  slice  cdhash  seal  hashType  flags  sealedBytes  sigBytes
      where seal is valid, broken, or absent, and cdhash is empty when there is
      no CodeDirectory to hash.

  compare --before <survey.txt> <root>
      Re-survey <root> and reconcile it against the earlier survey.  Exits 1
      when an image would ship with a seal that does not cover it, no seal at
      all, or a seal this run removed.

What decides whether a shop image loads is not the signature shape.  Measured
on the device 2026-11-25, all under `jbctl proc_set_debugged <pid>`:

  installed WebGPU, Apple linker signature, alone ......... FAILED (symbol error)
  rcodesign-signed WebGPU, alone ......................... FAILED (symbol error)
  installed WebGPU, after JavaScriptCore ................. LOADED
  rcodesign-signed WebGPU, after JavaScriptCore .......... LOADED
  WebGPU with LC_CODE_SIGNATURE stripped, authorized ..... FAILED
  WebGPU with LC_CODE_SIGNATURE stripped, unauthorized ... FAILED

Two conditions, and signature shape is neither of them: the loading process must
be authorized, and the images must be opened in dependency order starting at
JavaScriptCore, because the WTF symbols WebCore and WebKit need come from it.
A re-signed image therefore loads, which is why changing a cdhash is reported
rather than failed.

There is a real trap the second row of that table does not show, and the reason
this tool exists: outside an authorized process, admission is keyed on the
cdhash.  Measured then: libiosexec.1.dylib loaded from its own path and from a
byte-identical copy elsewhere with the same cdhash, and failed with "code
signature invalid" as soon as one copy was re-signed into a new cdhash.  So a
re-sign is only safe in the authorized flow, and the survey records the cdhashes
this run produced so the report can say which ones they are.

The cdhash is SHA-256 of the whole CodeDirectory blob truncated to 20 bytes,
whatever page hash algorithm that CodeDirectory uses.  Checked against the
device: SHA-256 of the shipped JavaScriptCore CodeDirectory begins
556026313fd1df27, the cdhash the device reported for the image it loaded.
"""
import hashlib
import os
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

LC_CODE_SIGNATURE = 0x1D
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0

# hashType -> (hashlib name, digest bytes stored per page).  Type 3 is the
# SHA-256 CodeDirectory that keeps 20 bytes per slot.
HASH_TYPES = {1: ("sha1", 20), 2: ("sha256", 32), 3: ("sha256", 20), 4: ("sha384", 48)}


class Slice:
    __slots__ = ("offset", "size", "label")

    def __init__(self, offset, size, label):
        self.offset = offset
        self.size = size
        self.label = label


class NotMachO(Exception):
    """The file is not a Mach-O image, so it carries no code signature."""


class Damaged(Exception):
    """The image is Mach-O but its signature data cannot be parsed."""


def image_slices(data):
    if len(data) < 8:
        raise NotMachO("shorter than a Mach-O header")
    big = struct.unpack(">I", data[:4])[0]
    if big in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack(">I", data[4:8])[0]
        step, width = (32, 24) if big == FAT_MAGIC_64 else (20, 16)
        slices = []
        for index in range(count):
            entry = 8 + index * step
            if entry + width > len(data):
                raise Damaged("fat header runs past the file")
            if big == FAT_MAGIC_64:
                _cpu, _sub, offset, size = struct.unpack(">iiQQ", data[entry:entry + width])
            else:
                _cpu, _sub, offset, size = struct.unpack(">iiII", data[entry:entry + width])
            slices.append(Slice(offset, size, f"fat{index}"))
        return slices
    if struct.unpack("<I", data[:4])[0] != MH_MAGIC_64:
        raise NotMachO("not a 64-bit little-endian Mach-O")
    return [Slice(0, len(data), "thin")]


def code_signature_range(data, image):
    if image.offset + 32 > len(data):
        raise Damaged(f"{image.label}: truncated header")
    if struct.unpack("<I", data[image.offset:image.offset + 4])[0] != MH_MAGIC_64:
        raise Damaged(f"{image.label}: not a 64-bit Mach-O slice")
    ncmds = struct.unpack("<I", data[image.offset + 16:image.offset + 20])[0]
    position = image.offset + 32
    limit = image.offset + image.size
    for _ in range(ncmds):
        if position + 8 > limit:
            raise Damaged(f"{image.label}: load commands run past the slice")
        command, command_size = struct.unpack("<II", data[position:position + 8])
        if command == LC_CODE_SIGNATURE:
            if command_size < 16:
                raise Damaged(f"{image.label}: LC_CODE_SIGNATURE is {command_size} bytes")
            data_off, data_size = struct.unpack("<II", data[position + 8:position + 16])
            return image.offset + data_off, data_size
        if command_size < 8:
            raise Damaged(f"{image.label}: load command at {position} has size {command_size}")
        position += command_size
    return None


def read_codedirectory(blob):
    if len(blob) < 12:
        raise Damaged("signature blob is shorter than a superblob header")
    magic = struct.unpack(">I", blob[:4])[0]
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise Damaged(f"signature magic is 0x{magic:08x}, not an embedded signature")
    count = struct.unpack(">I", blob[8:12])[0]
    directory = None
    for index in range(count):
        entry = 12 + index * 8
        if entry + 8 > len(blob):
            raise Damaged("superblob index runs past the blob")
        slot, offset = struct.unpack(">II", blob[entry:entry + 8])
        if slot == CSSLOT_CODEDIRECTORY:
            directory = offset
            break
    if directory is None:
        raise Damaged("superblob carries no CodeDirectory")
    if directory + 40 > len(blob):
        raise Damaged("CodeDirectory starts past the end of the blob")
    cd = blob[directory:]
    if struct.unpack(">I", cd[:4])[0] != CSMAGIC_CODEDIRECTORY:
        raise Damaged("CodeDirectory magic is missing")
    length = struct.unpack(">I", cd[4:8])[0]
    if length < 40 or length > len(cd):
        raise Damaged(f"CodeDirectory declares {length} bytes of a {len(cd)}-byte blob")
    hash_type = cd[37]
    if hash_type not in HASH_TYPES:
        raise Damaged(f"CodeDirectory hashType {hash_type} is not one this checker knows")
    algo, stored = HASH_TYPES[hash_type]
    hash_size = cd[36]
    if hash_size != stored:
        raise Damaged(f"hashType {hash_type} needs {stored}-byte slots but declares {hash_size}")
    return {
        "raw": cd[:length],
        "flags": struct.unpack(">I", cd[12:16])[0],
        "hash_offset": struct.unpack(">I", cd[16:20])[0],
        "n_code": struct.unpack(">I", cd[28:32])[0],
        "code_limit": struct.unpack(">I", cd[32:36])[0],
        "hash_type": hash_type, "hash_size": hash_size,
        "algo": algo, "stored": stored,
        "page": 1 << cd[39],
    }


def cdhash_of(cd):
    return hashlib.sha256(cd["raw"]).digest()[:20].hex()


def seal_state(data, image, span, cd):
    """Return valid or broken for a signature whose header parsed."""
    data_off, data_size = span
    if data_size == 0:
        return "broken"
    if data_off + data_size != image.offset + image.size:
        return "broken"
    if cd["code_limit"] != data_off - image.offset:
        return "broken"
    page = cd["page"]
    if page == 0 or page & (page - 1):
        return "broken"
    expected = (cd["code_limit"] + page - 1) // page
    if cd["n_code"] != expected:
        return "broken"
    for slot in range(cd["n_code"]):
        start = slot * page
        end = min(start + page, cd["code_limit"])
        digest = hashlib.new(cd["algo"], data[image.offset + start:image.offset + end]).digest()
        stored_at = cd["hash_offset"] + slot * cd["hash_size"]
        if cd["raw"][stored_at:stored_at + cd["stored"]] != digest[:cd["stored"]]:
            return "broken"
    return "valid"


def survey_image(path):
    """Return readings for every slice, or None when the file is not a Mach-O image."""
    data = open(path, "rb").read()
    try:
        slices = image_slices(data)
    except NotMachO:
        return None
    readings = []
    for image in slices:
        try:
            span = code_signature_range(data, image)
        except Damaged:
            readings.append((image.label, "", "broken", "", "", "", ""))
            continue
        if span is None:
            readings.append((image.label, "", "absent", "", "", "", ""))
            continue
        if span[1] == 0:
            # The command is present but points at no signature, which is what a stripped or
            # never-signed image looks like.  Calling that "absent" keeps the message honest.
            readings.append((image.label, "", "absent", "", "", "", ""))
            continue
        try:
            cd = read_codedirectory(data[span[0]:span[0] + span[1]])
        except Damaged:
            readings.append((image.label, "", "broken", "", "", "", ""))
            continue
        readings.append((
            image.label, cdhash_of(cd), seal_state(data, image, span, cd),
            str(cd["hash_type"]), f"0x{cd['flags']:x}",
            str(cd["code_limit"]), str(span[1]),
        ))
    return readings


def survey(root):
    lines = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            readings = survey_image(path)
            if readings is None:
                continue
            for label, cdhash, state, hash_type, flags, sealed, sigbytes in readings:
                lines.append("\t".join([
                    os.path.relpath(path, root), label, cdhash, state,
                    hash_type, flags, sealed, sigbytes,
                ]))
    return lines


def parse_survey(lines):
    table = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        while len(parts) < 8:
            parts.append("")
        path, label, cdhash, state, hash_type, flags, sealed, sigbytes = parts[:8]
        table[(path, label)] = {
            "cdhash": cdhash, "state": state, "hash_type": hash_type,
            "flags": flags, "sealed": sealed, "sigbytes": sigbytes,
        }
    return table


def compare(before_lines, root):
    """Return (violations, unchanged_count, resigned).

    The failure condition is a seal this run left broken or missing, not a
    changed cdhash: measured under `proc_set_debugged`, a re-signed WebGPU loads
    and an Apple-signed WebGPU loads, so the signature shape is not what decides
    admission.  A removed signature is a failure, because a stripped image did
    not load even when authorized.

    `resigned` lists images whose cdhash this run changed.  That is expected
    after STEP 5's rewrite, and it is printed so the operator knows the payload
    carries signatures the build did not produce.  `unchanged` counts images the
    run left byte-identical, which is how a signer that silently did nothing
    shows up.
    """
    before = parse_survey(before_lines)
    after = parse_survey(survey(root))
    violations = []
    resigned = []
    unchanged = 0

    for key in sorted(after):
        path, label = key
        where = path if label == "thin" else f"{path}({label})"
        now = after[key]
        was = before.get(key)
        if was is None:
            violations.append(f"{where}: not present in the pre-signing survey")
            continue
        if now["state"] == "broken":
            violations.append(
                f"{where}: the seal does not cover the bytes being shipped; a rewrite ran "
                f"without a successful re-sign")
            continue
        if now["state"] == "absent":
            if was["state"] == "absent":
                # Never signed, still unsigned.  This is the shape that failed to
                # load on the device, so it cannot be shipped as verified.
                violations.append(f"{where}: no code signature, so the device will reject it")
            else:
                violations.append(f"{where}: the signature was removed")
            continue
        # now["state"] == "valid"
        if was["cdhash"] == now["cdhash"]:
            unchanged += 1
        else:
            resigned.append((where, was["cdhash"] or "(none)", now["cdhash"]))

    for key in sorted(set(before) - set(after)):
        path, label = key
        violations.append(f"{path}({label}): vanished between the two surveys")

    return violations, unchanged, resigned


def main(argv):
    if len(argv) < 3:
        print("usage: macho-code-signature.py survey <root> | compare --before <file> <root>",
              file=sys.stderr)
        return 2
    command = argv[1]
    if command == "survey":
        for line in survey(argv[2]):
            print(line)
        return 0
    if command == "compare":
        rest = argv[2:]
        if rest[:1] != ["--before"] or len(rest) < 3:
            print("compare needs --before <file> <root>", file=sys.stderr)
            return 2
        with open(rest[1], "r", encoding="utf-8") as handle:
            before_lines = handle.readlines()
        violations, unchanged, resigned = compare(before_lines, rest[2])
        for where, old, new in resigned:
            print(f"resigned\t{where}\t{old}\t{new}")
        for problem in violations:
            print(f"violation: {problem}", file=sys.stderr)
        if not violations and unchanged and not resigned:
            print(
                f"note: all {unchanged} signed images are byte-identical to the pre-signing "
                f"survey, so this run cannot tell that a signer ran", file=sys.stderr)
        return 1 if violations else 0
    print(f"unknown subcommand: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
