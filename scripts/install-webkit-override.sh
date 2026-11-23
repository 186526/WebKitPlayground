#!/bin/sh
#
# Build the rootless WebKit override directory:
#
#   /var/jb/Library/WebKitOverride/            <- DYLD_FRAMEWORK_PATH
#       <name>.framework/<name>                 the five framework binaries
#       usr/lib/<leaf>                          the two dylibs, top level
#
# Every file is copied from the copy already installed under
# /var/jb/Library/Frameworks and /var/jb/usr/lib. Nothing under those two
# source directories is moved, rewritten or removed, and nothing outside
# the override directory is written.
#
# Must run as root: /var/jb/Library is root:wheel 775, so an unprivileged run
# fails with "Permission denied" on the first mkdir. Copy the script to the
# device and run it there:
#   sudo sh /var/mobile/install-webkit-override.sh
#
# Safe to re-run: a target whose byte size already matches its source is left
# alone, every path is absolute (so $HOME, ~ and a different working directory
# cannot shift it), and nothing nests one WebKitOverride inside another.
#
# JBROOT sets the root prefix (default /var/jb) so the flow can be replayed
# against a scratch directory without a device.
#
set -eu

JBROOT=${JBROOT:-/var/jb}
JBROOT=${JBROOT%/}

SOURCE_FRAMEWORKS=${JBROOT}/Library/Frameworks
SOURCE_LIBRARIES=${JBROOT}/usr/lib
OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride
OVERRIDE_LIBRARIES=${OVERRIDE_ROOT}/usr/lib

FRAMEWORK_NAMES='WebKit WebCore JavaScriptCore WebKitLegacy WebGPU'
LIBRARY_NAMES='libwebrtc.dylib libANGLE-shared.dylib'

DIRECTORY_MODE=755
FILE_MODE=644

print_error() {
    printf 'Error: %s\n' "$1" >&2
}

die() {
    print_error "$1"
    exit 1
}

file_size() {
    wc -c < "$1" | tr -d '[:space:]'
}

find_python() {
    for candidate in /var/jb/usr/bin/python3 /usr/bin/python3; do
        if [ -x "${candidate}" ]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    found=$(command -v python3 2>/dev/null || true)
    if [ -n "${found}" ]; then
        printf '%s\n' "${found}"
        return 0
    fi
    return 1
}

preflight_sources() {
    missing=0
    for name in ${FRAMEWORK_NAMES}; do
        if [ ! -f "${SOURCE_FRAMEWORKS}/${name}.framework/${name}" ]; then
            print_error "missing source ${SOURCE_FRAMEWORKS}/${name}.framework/${name}"
            missing=1
        fi
    done
    for name in ${LIBRARY_NAMES}; do
        if [ ! -f "${SOURCE_LIBRARIES}/${name}" ]; then
            print_error "missing source ${SOURCE_LIBRARIES}/${name}"
            missing=1
        fi
    done
    if [ "${missing}" -ne 0 ]; then
        die "nothing was written: fix the sources above first"
    fi
}

refuse_symlinked_directory() {
    if [ -L "$1" ]; then
        die "$1 is a symlink; refusing to create directories through it, because that would write outside ${OVERRIDE_ROOT}"
    fi
    if [ -e "$1" ] && [ ! -d "$1" ]; then
        die "$1 exists and is not a directory"
    fi
}

# The directories the layout needs, one per line. The list lives here once so
# the pre-check pass and the create pass cannot drift apart.
layout_directories() {
    printf '%s\n' "${OVERRIDE_ROOT}"
    printf '%s\n' "${OVERRIDE_ROOT}/usr"
    printf '%s\n' "${OVERRIDE_LIBRARIES}"
    for name in ${FRAMEWORK_NAMES}; do
        printf '%s\n' "${OVERRIDE_ROOT}/${name}.framework"
    done
}

create_layout() {
    if [ -d "${OVERRIDE_ROOT}/WebKitOverride" ]; then
        die "${OVERRIDE_ROOT}/WebKitOverride exists: an earlier run nested the directory. Remove ${OVERRIDE_ROOT} with scripts/uninstall-webkit-override.sh and run this again."
    fi
    # mkdir -p walks through an existing symlinked directory without a word, so
    # every directory is checked before any is created: a symlink anywhere must
    # leave the tree untouched, not half-built. A symlinked OVERRIDE_ROOT would
    # scatter the files elsewhere and leave a directory uninstall refuses to
    # remove; a symlinked <name>.framework would overwrite a file that is not
    # ours. Both passes run in this shell, not in a pipeline, so die() really
    # stops the script.
    #
    # IFS is newline-only for both loops: a JBROOT containing a space would
    # otherwise split into two path fragments, create trees at each fragment
    # outside the override root, and then fail every copy. It stays narrowed
    # until the second loop ends, because restoring it in between would make
    # ${FRAMEWORK_NAMES} collapse into a single name.
    directories=$(layout_directories)
    saved_ifs=${IFS}
    IFS='
'
    for path_directory in ${directories}; do
        refuse_symlinked_directory "${path_directory}"
    done
    for path_directory in ${directories}; do
        mkdir -p "${path_directory}"
    done
    IFS=${saved_ifs}
}

copy_from_source() {
    path_source=$1
    path_target=$2
    if [ -L "${path_target}" ]; then
        die "${path_target} is a symlink; refusing to write through it"
    fi
    if [ -d "${path_target}" ]; then
        die "${path_target} is a directory; remove ${OVERRIDE_ROOT} and re-run"
    fi
    if [ -f "${path_target}" ] && [ "$(file_size "${path_target}")" = "$(file_size "${path_source}")" ]; then
        printf '  unchanged  %s (%s bytes)\n' "${path_target}" "$(file_size "${path_target}")"
        return 0
    fi
    cp -p "${path_source}" "${path_target}"
    printf '  copied     %s (%s bytes)\n' "${path_target}" "$(file_size "${path_target}")"
}

copy_all() {
    for name in ${FRAMEWORK_NAMES}; do
        copy_from_source "${SOURCE_FRAMEWORKS}/${name}.framework/${name}" \
            "${OVERRIDE_ROOT}/${name}.framework/${name}"
    done
    for name in ${LIBRARY_NAMES}; do
        copy_from_source "${SOURCE_LIBRARIES}/${name}" "${OVERRIDE_LIBRARIES}/${name}"
    done
}

set_permissions() {
    chmod "${DIRECTORY_MODE}" "${OVERRIDE_ROOT}" "${OVERRIDE_ROOT}/usr" "${OVERRIDE_LIBRARIES}"
    for name in ${FRAMEWORK_NAMES}; do
        chmod "${DIRECTORY_MODE}" "${OVERRIDE_ROOT}/${name}.framework"
        chmod "${FILE_MODE}" "${OVERRIDE_ROOT}/${name}.framework/${name}"
    done
    for name in ${LIBRARY_NAMES}; do
        chmod "${FILE_MODE}" "${OVERRIDE_LIBRARIES}/${name}"
    done
}

verify_layout() {
    "${PYTHON_BIN}" - "${OVERRIDE_ROOT}" ${FRAMEWORK_NAMES} -- ${LIBRARY_NAMES} <<'PYTHON'
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
EXPLANATION = (
    "dyld substitutes this file for the arm64e image in the shared cache; "
    "an arm64 slice cannot be loaded into an arm64e process, so the override "
    "would fail to load or behave incorrectly"
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
    return ARM64_SUBTYPES.get(cpusubtype & 0x00FFFFFF, "arm64-subtype-%d" % (cpusubtype & 0x00FFFFFF))


override_root = sys.argv[1]
separator = sys.argv.index("--")
frameworks = sys.argv[2:separator]
libraries = sys.argv[separator + 1:]

targets = [os.path.join(override_root, name + ".framework", name) for name in frameworks]
targets += [os.path.join(override_root, "usr", "lib", name) for name in libraries]

missing = 0
wrong_arch = 0
for path in targets:
    if not os.path.isfile(path):
        print("  missing    %s" % path)
        missing += 1
        continue
    with open(path, "rb") as handle:
        arches = read_arches(handle.read(4096))
    if not arches:
        print("  not-macho  %s" % path)
        wrong_arch += 1
        continue
    names = [arch_name(cputype, cpusubtype) for cputype, cpusubtype in arches]
    if "arm64e" in names:
        print("  arm64e     %s (%s)" % (path, ",".join(names)))
    else:
        print("  wrong-arch %s (%s): %s" % (path, ",".join(names), EXPLANATION))
        wrong_arch += 1

if missing:
    print("Error: %d of the %d target files are missing" % (missing, len(targets)))
if wrong_arch:
    print("Error: %d of the %d target files are not arm64e" % (wrong_arch, len(targets)))
sys.exit(1 if (missing or wrong_arch) else 0)
PYTHON
}

PYTHON_BIN=$(find_python) || die "python3 not found; the arm64e check cannot run"

printf 'WebKitOverride install\n'
printf '  root        %s\n' "${OVERRIDE_ROOT}"
printf '  frameworks  %s\n' "${SOURCE_FRAMEWORKS}"
printf '  libraries   %s\n' "${SOURCE_LIBRARIES}"
printf '  python      %s\n' "${PYTHON_BIN}"

printf '\nChecking sources\n'
preflight_sources

printf '\nCreating the layout\n'
create_layout

printf '\nCopying\n'
copy_all

printf '\nSetting permissions\n'
set_permissions

printf '\nVerifying the seven target files\n'
verify_layout

printf '\nDone. %s is in place; nothing was restarted and no system or basebin file was touched.\n' "${OVERRIDE_ROOT}"
printf 'Load a new process (open Safari, or run any app) for dyld to search it.\n'
printf 'Remove it again with scripts/uninstall-webkit-override.sh.\n'
