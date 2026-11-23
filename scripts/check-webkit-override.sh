#!/bin/sh
#
# Report the state of the rootless WebKit override directory without writing
# anything: whether /var/jb/Library/WebKitOverride exists, the byte size and
# mtime of each of the seven target files, and whether each one is a Mach-O
# image that carries an arm64e slice.
#
# Usage, on the device, as any user that can read the directory:
#   sh /var/mobile/check-webkit-override.sh
#
# Reads only, so it runs as mobile without root. Run it before and after
# installing and compare the two outputs. Exits 0 when all seven files are
# present and arm64e, 1 otherwise, so it can gate a scripted install.
#
# Everything is reported through python3 rather than stat or ls: the stat on
# this device is GNU coreutils even though uname says Darwin, so BSD's
# `stat -f '%Sm'` fails with "cannot read file system information for '%Sm'",
# and ls formats differ per install.
#
# JBROOT sets the root prefix (default /var/jb).
#
set -u

JBROOT=${JBROOT:-/var/jb}
JBROOT=${JBROOT%/}

OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride
SOURCE_FRAMEWORKS=${JBROOT}/Library/Frameworks
SOURCE_LIBRARIES=${JBROOT}/usr/lib

FRAMEWORK_NAMES='WebKit WebCore JavaScriptCore WebKitLegacy WebGPU'
LIBRARY_NAMES='libwebrtc.dylib libANGLE-shared.dylib'

printf 'WebKitOverride state\n'

if [ -d "${OVERRIDE_ROOT}" ]; then
    printf '  directory   present: %s\n' "${OVERRIDE_ROOT}"
else
    printf '  directory   absent:  %s\n' "${OVERRIDE_ROOT}"
fi

PYTHON_BIN=
for candidate in /var/jb/usr/bin/python3 /usr/bin/python3; do
    if [ -x "${candidate}" ]; then
        PYTHON_BIN=${candidate}
        break
    fi
done
if [ -z "${PYTHON_BIN}" ]; then
    PYTHON_BIN=$(command -v python3 2>/dev/null || true)
fi
if [ -z "${PYTHON_BIN}" ]; then
    printf '\nResult: python3 not found, so no sizes, mtimes or Mach-O state could be read.\n' >&2
    exit 1
fi

"${PYTHON_BIN}" - "${OVERRIDE_ROOT}" "${SOURCE_FRAMEWORKS}" "${SOURCE_LIBRARIES}" \
    ${FRAMEWORK_NAMES} -- ${LIBRARY_NAMES} <<'PYTHON'
import datetime
import os
import struct
import sys

ARM64_CPU_TYPE = 0x0100000C
ARM64_SUBTYPES = {0: "arm64", 2: "arm64e"}
FAT_MAGICS = {b"\xca\xfe\xba\xbe": ">", b"\xbe\xba\xfe\xca": "<"}
THIN_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",
    b"\xce\xfa\xed\xfe": "<",
    b"\xfe\xed\xfa\xcf": ">",
    b"\xfe\xed\xfa\xce": ">",
}
NOT_ARM64E = (
    "not arm64e: dyld substitutes this file for the arm64e image in the shared "
    "cache, and an arm64 slice cannot be loaded into an arm64e process"
)


def read_arches(header):
    magic = header[:4]
    if magic in THIN_MAGICS:
        endian = THIN_MAGICS[magic]
        if len(header) < 12:
            return None
        return [struct.unpack(endian + "II", header[4:12])]
    if magic in FAT_MAGICS:
        endian = FAT_MAGICS[magic]
        if len(header) < 8:
            return None
        count = struct.unpack(endian + "I", header[4:8])[0]
        arches = []
        for index in range(count):
            entry = header[8 + index * 20:8 + (index + 1) * 20]
            if len(entry) < 8:
                return None
            arches.append(struct.unpack(endian + "II", entry[:8]))
        return arches or None
    return None


def arch_name(cputype, cpusubtype):
    if cputype != ARM64_CPU_TYPE:
        return "cputype-0x%08x" % cputype
    subtype = cpusubtype & 0x00FFFFFF
    return ARM64_SUBTYPES.get(subtype, "arm64-subtype-%d" % subtype)


def describe(path):
    if not os.path.isfile(path):
        return ("-", "-", "missing")
    size = os.path.getsize(path)
    stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "rb") as handle:
            arches = read_arches(handle.read(4096))
    except OSError as error:
        return (str(size), stamp, "unreadable: %s" % error)
    if not arches:
        return (str(size), stamp, "not Mach-O")
    names = [arch_name(cputype, cpusubtype) for cputype, cpusubtype in arches]
    if "arm64e" in names:
        return (str(size), stamp, "arm64e (%s)" % ",".join(names))
    return (str(size), stamp, ",".join(names) + " - " + NOT_ARM64E)


override_root, source_frameworks, source_libraries = sys.argv[1:4]
separator = sys.argv.index("--")
frameworks = sys.argv[4:separator]
libraries = sys.argv[separator + 1:]

rows = []
for name in frameworks:
    rows.append(
        (
            name,
            os.path.join(source_frameworks, name + ".framework", name),
            os.path.join(override_root, name + ".framework", name),
        )
    )
for name in libraries:
    rows.append(
        (
            name,
            os.path.join(source_libraries, name),
            os.path.join(override_root, "usr", "lib", name),
        )
    )

print("\nSources")
print("  %-20s %12s  %-19s  %s" % ("file", "bytes", "mtime", "state"))
for name, path_source, _ in rows:
    size, stamp, state = describe(path_source)
    print("  %-20s %12s  %-19s  %s" % (name, size, stamp, state))

print("\nTargets")
print("  %-20s %12s  %-19s  %s" % ("file", "bytes", "mtime", "state"))
missing = 0
not_arm64e = 0
for name, _, path_target in rows:
    size, stamp, state = describe(path_target)
    if state == "missing":
        missing += 1
    elif "not arm64e" in state or state == "not Mach-O":
        not_arm64e += 1
    print("  %-20s %12s  %-19s  %s" % (name, size, stamp, state))

print("")
if missing or not_arm64e:
    print("Result: %d of %d missing, %d not arm64e." % (missing, len(rows), not_arm64e))
    sys.exit(1)
print("Result: all %d files present and arm64e." % len(rows))
PYTHON
