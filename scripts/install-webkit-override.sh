#!/bin/sh
#
# Build the rootless WebKit override directory:
#
#   /var/jb/Library/WebKitOverride/            <- DYLD_FRAMEWORK_PATH
#       <name>.framework/                       the five framework directories,
#           <name>                              binary plus every resource
#           Info.plist
#           <lang>.lproj/
#           Frameworks/                         (WebKit only)
#       usr/lib/<leaf>                          the two dylibs, top level
#
# The resources are not optional. Each framework directory is copied whole,
# because a bare binary in <name>.framework/ has no Info.plist and CFBundle
# returns nil for it: the first WebCore::localizedString call then
# dereferences a null bundle and takes the host process down with
# EXC_BAD_ACCESS at 0x8. That was the crash in WeChat-2026-11-24-212444.ips.
#
# XPCServices/ is deliberately excluded from WebKit.framework. launchd resolves
# each WebKit helper through DYLD_FRAMEWORK_PATH, so a present
# WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc makes it launch the
# copied stub rather than the system one. The system stubs are the right
# partners: each is a four-instruction tail call into WKXPCServiceMain, which
# DYLD_FRAMEWORK_PATH already points at the overridden WebKit, so the override
# still takes effect and no second copy of every .xpc bundle enters the search
# path.
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
# Safe to re-run: a target whose bytes are identical to its source is left
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

# The mtime in one fixed format, so the two sides of a comparison read the same
# way on every device. python3 is already required for the arm64e check, and it
# avoids the GNU `stat -c` versus BSD `stat -f` split that has already bitten
# this project on the device.
file_mtime() {
    "${PYTHON_BIN}" -c 'import os, sys, time
sys.stdout.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(os.stat(sys.argv[1]).st_mtime))))
' "$1"
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

# The same lookup order as find_python. root's PATH need not contain
# /var/jb/usr/bin, so the device copy has to be named explicitly.
find_cmp() {
    for candidate in /var/jb/usr/bin/cmp /usr/bin/cmp; do
        if [ -x "${candidate}" ]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    found=$(command -v cmp 2>/dev/null || true)
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
        if [ ! -f "${SOURCE_FRAMEWORKS}/${name}.framework/Info.plist" ]; then
            print_error "missing source ${SOURCE_FRAMEWORKS}/${name}.framework/Info.plist (without it CFBundle resolves to nil and WebCore::localizedString faults)"
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
    # Content, not size: a rebuilt binary can land on exactly the same byte
    # count as the copy already in place, and a size check would then keep the
    # previous bytes while dyld goes on loading them.
    if [ -f "${path_target}" ] && "${CMP_BIN}" -s "${path_source}" "${path_target}"; then
        printf '  unchanged  %s (kept, same content as source; source mtime %s, copy mtime %s, %s bytes)\n' \
            "${path_target}" \
            "$(file_mtime "${path_source}")" \
            "$(file_mtime "${path_target}")" \
            "$(file_size "${path_target}")"
        return 0
    fi
    cp -p "${path_source}" "${path_target}"
    printf '  copied     %s (%s bytes)\n' "${path_target}" "$(file_size "${path_target}")"
}

copy_framework_tree() {
    path_source=$1
    path_target=$2
    if [ -L "${path_target}" ]; then
        die "${path_target} is a symlink; refusing to write through it"
    fi
    if [ -f "${path_target}" ] && [ ! -d "${path_target}" ]; then
        die "${path_target} is a file; remove ${OVERRIDE_ROOT} and re-run"
    fi
    # The trailing /. copies the contents into the existing directory; without
    # it cp would nest a second <name>.framework inside the first. -a keeps
    # modes and timestamps. A real copy, not a hard link, so that stripping or
    # re-signing a file in the override can never rewrite the deb-installed
    # source it came from.
    # Resources travel with the binary on purpose: a framework without its
    # Info.plist makes CFBundle return nil, and WebCore::localizedString then
    # faults on it.
    mkdir -p "${path_target}"
    cp -a "${path_source}/." "${path_target}/"
    printf '  copied     %s (%s bytes)\n' "${path_target}" "$(du -sk "${path_target}" | awk '{print $1*1024}')"
}

prune_xpc_services() {
    # launchd resolves the WebKit helpers through DYLD_FRAMEWORK_PATH, so an
    # XPCServices directory inside the override would make it launch the copied
    # stubs instead of the system ones. The system stubs are the right partners:
    # each is a tail call into WKXPCServiceMain, which DYLD_FRAMEWORK_PATH
    # already points at the overridden WebKit, and they leave the helper's
    # environment alone. Keeping the copied stubs would also put a second copy of
    # every .xpc bundle in the search path with no benefit.
    path_target=${OVERRIDE_ROOT}/WebKit.framework/XPCServices
    if [ -L "${path_target}" ]; then
        die "${path_target} is a symlink; refusing to remove through it"
    fi
    if [ -d "${path_target}" ]; then
        rm -rf "${path_target}"
        printf '  pruned     %s\n' "${path_target}"
    fi
}

copy_all() {
    for name in ${FRAMEWORK_NAMES}; do
        copy_framework_tree "${SOURCE_FRAMEWORKS}/${name}.framework" \
            "${OVERRIDE_ROOT}/${name}.framework"
    done
    prune_xpc_services
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

# A framework directory without Info.plist resolves to a nil CFBundle, and the
# first WebCore::localizedString call then faults at 0x8. The binary check
# above cannot see that, so the plist is verified separately.
no_plist = 0
for name in frameworks:
    plist = os.path.join(override_root, name + ".framework", "Info.plist")
    if os.path.isfile(plist):
        print("  plist      %s" % plist)
    else:
        print("  no-plist   %s (CFBundle would return nil; WebCore::localizedString crashes)" % plist)
        no_plist += 1

if missing:
    print("Error: %d of the %d target files are missing" % (missing, len(targets)))
if wrong_arch:
    print("Error: %d of the %d target files are not arm64e" % (wrong_arch, len(targets)))
if no_plist:
    print("Error: %d of the %d framework directories have no Info.plist" % (no_plist, len(frameworks)))
sys.exit(1 if (missing or wrong_arch or no_plist) else 0)
PYTHON
}

PYTHON_BIN=$(find_python) || die "python3 not found; the arm64e check cannot run"
CMP_BIN=$(find_cmp) || die "cmp not found; the copy step cannot tell an unchanged target from a stale one"

printf 'WebKitOverride install\n'
printf '  root        %s\n' "${OVERRIDE_ROOT}"
printf '  frameworks  %s\n' "${SOURCE_FRAMEWORKS}"
printf '  libraries   %s\n' "${SOURCE_LIBRARIES}"
printf '  python      %s\n' "${PYTHON_BIN}"
printf '  cmp         %s\n' "${CMP_BIN}"

printf '\nChecking sources\n'
preflight_sources

printf '\nCreating the layout\n'
create_layout

printf '\nCopying\n'
copy_all

printf '\nSetting permissions\n'
set_permissions

printf '\nVerifying the target files and framework plists\n'
verify_layout

printf '\nDone. %s is in place; nothing was restarted and no system or basebin file was touched.\n' "${OVERRIDE_ROOT}"
printf 'Load a new process (open Safari, or run any app) for dyld to search it.\n'
printf 'Remove it again with scripts/uninstall-webkit-override.sh.\n'
