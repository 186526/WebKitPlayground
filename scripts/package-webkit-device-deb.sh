#!/bin/zsh
# ──────────────────────────────────────────────────────────────
# package-webkit-device-deb.sh
#
# Package WebKit device build artifacts into a .deb for
# jailbroken iOS devices.  Installation effect mirrors
# push-webkit-device-artifacts.sh:
#   Frameworks -> /Library/Frameworks  (rootful)
#              -> /var/jb/Library/Frameworks  (rootless)
#   Dylibs    -> /usr/lib              (rootful)
#              -> /var/jb/usr/lib       (rootless)
#
# libWebKitSwift.dylib is the one dylib that stays inside the
# framework bundle: WebKit opens it at runtime through the
# bundle-relative path "Frameworks/libWebKitSwift.dylib".
#
# STEP 5 rewrites the framework load paths and STEP 7 verifies
# them.  A built WebKit links against the system frameworks by
# absolute path, and production dyld answers an absolute path
# from the shared cache without ever looking for a file on disk,
# so a package that keeps those paths never loads its own copies.
# Each rewritten image instead names its dependency
# "@rpath/<Name>.framework/<Name>" and carries the two LC_RPATH
# entries that resolve it inside the install prefix.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCRIPT_NAME="$(basename "$0")"
SOURCE_DIR="${ROOT_DIR}/WebKit"
PRODUCT_DIR="${SOURCE_DIR}/WebKitBuild/Debug-iphoneos"
OUTPUT_DEB=""
ROOTLESS=0
ROOTHIDE=0
PACKAGE_ID="com.82flex.custom-webkit"
PACKAGE_NAME="Custom WebKit"
PACKAGE_VERSION=""
PACKAGE_AUTHOR="WebKitPlayground Builder"
PACKAGE_DESCRIPTION="Custom-built WebKit frameworks for iOS device injection."
STEP_INDEX=0
TOTAL_STEPS=0

# WebKit opens the Swift overlay through the bundle-relative path
# "Frameworks/libWebKitSwift.dylib", so the file has to sit next to the
# frameworks rather than in usr/lib like the other dylibs.
SWIFT_OVERLAY_NAME="libWebKitSwift.dylib"
SWIFT_OVERLAY_REL="WebKit.framework/Frameworks/${SWIFT_OVERLAY_NAME}"

# ── colours ──────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
else
    C_RESET=""; C_BOLD=""
    C_CYAN=""; C_GREEN=""
    C_YELLOW=""; C_RED=""
fi

# ── logging helpers ──────────────────────────────────────────
log_step()    { STEP_INDEX=$((STEP_INDEX + 1)); printf "%s%s[%d/%d]%s %s\n" "${C_BOLD}" "${C_CYAN}" "${STEP_INDEX}" "${TOTAL_STEPS}" "${C_RESET}" "$1"; }
log_info()    { printf "%sInfo:%s %s\n"    "${C_CYAN}"   "${C_RESET}" "$1"; }
log_note()    { printf "%sNote:%s %s\n"    "${C_CYAN}"   "${C_RESET}" "$1"; }
log_warn()    { printf "%sWarning:%s %s\n" "${C_YELLOW}" "${C_RESET}" "$1" >&2; }
log_error()   { printf "%sError:%s %s\n"   "${C_RED}"    "${C_RESET}" "$1" >&2; }
log_success() { printf "%s%s%s\n"          "${C_GREEN}"  "$1" "${C_RESET}"; }

# ── usage ────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Options:
  --product <dir>         Build product directory (default: WebKit/WebKitBuild/Debug-iphoneos)
  --output  <deb-path>    Output .deb file path (auto-generated if omitted)
  --rootless              Target rootless jailbreak (e.g. Dopamine/palera1n rootless)
                          Install prefix becomes /var/jb; Architecture: iphoneos-arm64
  --roothide              Target roothide jailbreak (arm64e devices, e.g. Dopamine roothide)
                          No prefix change; Architecture: iphoneos-arm64e
  --id      <bundle-id>   Debian package identifier  (default: ${PACKAGE_ID})
  --name    <name>        Debian package display name (default: ${PACKAGE_NAME})
  --version <ver>         Debian package version      (default: auto from WebKit Version.xcconfig)
  --author  <author>      Maintainer field            (default: ${PACKAGE_AUTHOR})
  -h, --help              Show this help

Notes:
  Requires 'ldid' in PATH for ad-hoc signing.
  Requires 'dpkg-deb' in PATH (install via: "brew install dpkg").
  Requires 'python3' in PATH: it rewrites the Mach-O load commands.
  Uses 'rabin2' as a second opinion when it is in PATH.

  Checking a package that is already built, without a device.  Unpack it
  first ("dpkg-deb -x pkg.deb root"), then scan every image for a load
  command that still names a system framework.  Replace <root> with the
  directory dpkg-deb wrote; a rootless package's images are under
  <root>/var/jb.  No line printed means no image names a system path:
    cd <root> && find . -type f | while read -r f; do
      case "\$(od -An -tx1 -N4 "\$f" | tr -d ' \\n')" in
        cffaedfe|cefaedfe) ;;
        *) continue;;
      esac
      rabin2 -l "\$f" 2>/dev/null | grep -E '^/System/Library/(Frameworks/\\
(WebKit|WebCore|JavaScriptCore|WebKitLegacy|WebGPU)\\.framework|\\
PrivateFrameworks/(WebCore|WebKitLegacy)\\.framework)' \\
        && echo "STILL SYSTEM: \$f"
    done
  One caveat: rabin2 -l does not list LC_REEXPORT_DYLIB, and the shipped
  WebKit re-exports WebKitLegacy through one.  This script's own STEP 7
  reads the load commands directly and does catch it; the one-liner above
  cannot.
EOF
}

# ── argument parsing ─────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --product)   PRODUCT_DIR="${2:?missing value for --product}"; SOURCE_DIR="$(dirname "$(dirname "${PRODUCT_DIR}")")"; shift 2 ;;
        --output)    OUTPUT_DEB="${2:?missing value for --output}"; shift 2 ;;
        --rootless)  ROOTLESS=1; shift ;;
        --roothide)  ROOTHIDE=1; shift ;;
        --id)        PACKAGE_ID="${2:?missing value for --id}"; shift 2 ;;
        --name)      PACKAGE_NAME="${2:?missing value for --name}"; shift 2 ;;
        --version)   PACKAGE_VERSION="${2:?missing value for --version}"; shift 2 ;;
        --author)    PACKAGE_AUTHOR="${2:?missing value for --author}"; shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *)           log_error "Unknown argument: $1"; usage >&2; exit 1 ;;
    esac
done

# ── validate prerequisites ───────────────────────────────────
if [[ ! -d "${PRODUCT_DIR}" ]]; then
    log_error "Product directory does not exist: ${PRODUCT_DIR}"
    exit 1
fi

if ! command -v ldid >/dev/null 2>&1; then
    log_error "ldid not found in PATH. Install it first (brew install ldid)."
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    log_error "dpkg-deb not found in PATH. Install it first (brew install dpkg)."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    log_error "python3 not found in PATH. The load-path rewrite and its check are written in it."
    exit 1
fi

# ── auto-detect version ─────────────────────────────────────
if [[ -z "${PACKAGE_VERSION}" ]]; then
    # Probe candidate paths in priority order:
    #   1. top-level Configurations/ (e.g. open-source main branch)
    #   2. Source/WebKit/Configurations/ (e.g. iOS 16.3.x drop)
    #   3. Source/JavaScriptCore/Configurations/
    VERSION_XCCONFIG=""
    for _candidate in \
        "${SOURCE_DIR}/Configurations/Version.xcconfig" \
        "${SOURCE_DIR}/Source/WebKit/Configurations/Version.xcconfig" \
        "${SOURCE_DIR}/Source/JavaScriptCore/Configurations/Version.xcconfig"
    do
        if [[ -f "${_candidate}" ]]; then
            VERSION_XCCONFIG="${_candidate}"
            break
        fi
    done

    if [[ -n "${VERSION_XCCONFIG}" ]]; then
        major="$(grep '^MAJOR_VERSION' "${VERSION_XCCONFIG}" | head -1 | sed 's/.*= *//;s/[;[:space:]]*$//')"
        minor="$(grep '^MINOR_VERSION' "${VERSION_XCCONFIG}" | head -1 | sed 's/.*= *//;s/[;[:space:]]*$//')"
        tiny="$(grep  '^TINY_VERSION'  "${VERSION_XCCONFIG}" | head -1 | sed 's/.*= *//;s/[;[:space:]]*$//')"
        PACKAGE_VERSION="${major:-0}.${minor:-0}.${tiny:-0}"
        log_info "Version detected from: ${VERSION_XCCONFIG} -> ${PACKAGE_VERSION}"
    else
        PACKAGE_VERSION="0.0.1"
        log_warn "Version.xcconfig not found; falling back to ${PACKAGE_VERSION}"
    fi
    # Append a timestamp suffix to distinguish rebuild iterations.
    PACKAGE_VERSION="${PACKAGE_VERSION}-$(date +%Y%m%d%H%M%S)"
fi

# ── determine install prefixes ───────────────────────────────
if [[ "${ROOTHIDE}" == "1" && "${ROOTLESS}" == "1" ]]; then
    log_error "--rootless and --roothide are mutually exclusive."
    exit 1
fi

if [[ "${ROOTLESS}" == "1" ]]; then
    PREFIX="/var/jb"
    PACKAGE_ARCH="iphoneos-arm64"
    log_info "Rootless mode: install prefix = ${PREFIX}, arch = ${PACKAGE_ARCH}"
elif [[ "${ROOTHIDE}" == "1" ]]; then
    PREFIX=""
    PACKAGE_ARCH="iphoneos-arm64e"
    log_info "Roothide mode: install prefix = /, arch = ${PACKAGE_ARCH}"
else
    PREFIX=""
    PACKAGE_ARCH="iphoneos-arm"
    log_info "Rootful mode: install prefix = /, arch = ${PACKAGE_ARCH}"
fi

INSTALL_FRAMEWORKS_DIR="${PREFIX}/Library/Frameworks"
INSTALL_USRLIB_DIR="${PREFIX}/usr/lib"

# ── output path ──────────────────────────────────────────────
timestamp="$(date +%Y%m%d-%H%M%S)"
if [[ -z "${OUTPUT_DEB}" ]]; then
    OUTPUT_PKG_DIR="${ROOT_DIR}/packages"
    mkdir -p "${OUTPUT_PKG_DIR}"
    OUTPUT_DEB="${OUTPUT_PKG_DIR}/${PACKAGE_ID}_${PACKAGE_VERSION}_${PACKAGE_ARCH}.deb"
fi

TOTAL_STEPS=9

# ── working directory ────────────────────────────────────────
WORK_BASE_DIR="$(cd "$(dirname "${PRODUCT_DIR}")" && pwd)"
WORK_DIR="$(mktemp -d "${WORK_BASE_DIR}/.webkit-deb-package.XXXXXX")"
DEB_ROOT="${WORK_DIR}/deb-root"
PAYLOAD_DIR="${DEB_ROOT}${INSTALL_FRAMEWORKS_DIR}"
USRLIB_DIR="${DEB_ROOT}${INSTALL_USRLIB_DIR}"
PAYLOAD_REALPATH="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${PAYLOAD_DIR}" 2>/dev/null || echo "${PAYLOAD_DIR}")"

cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

# ══════════════════════════════════════════════════════════════
# STEP 1 — Collect artifacts
# ══════════════════════════════════════════════════════════════
log_step "Collecting required/recommended/on-demand artifacts..."
mkdir -p "${PAYLOAD_DIR}" "${USRLIB_DIR}"

typeset -a required_items=(
    "WebKit.framework"
    "WebCore.framework"
    "WebKitLegacy.framework"
    "JavaScriptCore.framework"
    "libwebrtc.dylib"
)

typeset -a recommended_items=(
    "libANGLE-shared.dylib"
    "WebGPU.framework"
    "com.apple.WebKit.WebContent.xpc"
    "com.apple.WebKit.Networking.xpc"
    "com.apple.WebKit.GPU.xpc"
    "com.apple.WebKit.WebContent.CaptivePortal.xpc"
    "com.apple.WebKit.WebContent.Crashy.xpc"
    "com.apple.WebKit.WebContent.Development.xpc"
)

typeset -a ondemand_items=(
    "webpushd"
    "adattributiond"
    "com.apple.WebKit.WebAuthn.xpc"
)

copy_item_if_exists() {
    local item="$1" dst_dir="$2"
    local src="${PRODUCT_DIR}/${item}"
    local dst="${dst_dir}/${item}"
    if [[ -e "${src}" ]]; then
        ditto "${src}" "${dst}"
        return 0
    fi
    return 1
}

item_is_already_covered_in_framework() {
    local item="$1"
    [[ -e "${PAYLOAD_DIR}/WebKit.framework/XPCServices/${item}" ]] && return 0
    [[ -e "${PAYLOAD_DIR}/WebKit.framework/Daemons/${item}" ]]    && return 0
    return 1
}

for item in "${required_items[@]}"; do
    if ! copy_item_if_exists "${item}" "${PAYLOAD_DIR}"; then
        log_error "Missing required artifact: ${item}"
        exit 1
    fi
done

# The build product keeps the real overlay at top level and puts a symlink to it
# at WebKit.framework/Frameworks/libWebKitSwift.dylib.  Copying WebKit.framework
# alone therefore carries a link whose target never enters the payload, and the
# symlink pass below would delete it.  Copy the real file over that link instead.
install_swift_overlay() {
    local source="${PRODUCT_DIR}/${SWIFT_OVERLAY_NAME}"
    local destination="${PAYLOAD_DIR}/${SWIFT_OVERLAY_REL}"
    [[ -e "${source}" ]] || return 1
    mkdir -p "$(dirname "${destination}")"
    rm -f "${destination}"
    cp -fL "${source}" "${destination}"
    log_info "Placed Swift overlay at ${SWIFT_OVERLAY_REL}"
}

install_swift_overlay || log_note "Swift overlay not in the product: ${SWIFT_OVERLAY_NAME}"

# A symlink satisfies -e but not the check below: dyld opens the path at runtime and a relative
# link in the payload has no sibling file to reach.  This runs before STEP 2 so the build stops
# on the real defect rather than on the deletion STEP 2 would make of it.
verify_swift_overlay() {
    local payload="${PAYLOAD_DIR}/${SWIFT_OVERLAY_REL}"
    [[ -e "${PRODUCT_DIR}/${SWIFT_OVERLAY_NAME}" ]] || return 0
    if [[ -L "${payload}" ]]; then
        log_error "Payload has ${SWIFT_OVERLAY_REL} as a symlink; WebKit needs a regular file there."
        return 1
    fi
    if [[ ! -f "${payload}" ]]; then
        log_error "Payload is missing ${SWIFT_OVERLAY_REL}: the product has ${SWIFT_OVERLAY_NAME} but the package does not ship it."
        return 1
    fi
    log_info "Verified ${SWIFT_OVERLAY_REL} is a regular file in the payload."
}

verify_swift_overlay || exit 1

for item in "${recommended_items[@]}"; do
    if ! copy_item_if_exists "${item}" "${PAYLOAD_DIR}"; then
        if item_is_already_covered_in_framework "${item}"; then
            log_info "${item} already covered via WebKit.framework subdirectory."
            continue
        fi
        log_warn "recommended artifact not found: ${item}"
    fi
done

for item in "${ondemand_items[@]}"; do
    if ! copy_item_if_exists "${item}" "${PAYLOAD_DIR}"; then
        if item_is_already_covered_in_framework "${item}"; then
            log_info "${item} already covered via WebKit.framework subdirectory."
            continue
        fi
        log_note "on-demand artifact not found: ${item}"
    fi
done

# ══════════════════════════════════════════════════════════════
# STEP 2 — Resolve external/broken symlinks
# ══════════════════════════════════════════════════════════════
log_step "Resolving external/broken symlinks inside payload..."
while IFS= read -r -d '' link_path; do
    target_path="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${link_path}")"
    if [[ ! -e "${target_path}" ]]; then
        log_warn "removing broken symlink: ${link_path} -> ${target_path}"
        rm "${link_path}"
        continue
    fi
    case "${target_path}" in
        "${PAYLOAD_REALPATH}"/*) continue ;;
    esac
    rm "${link_path}"
    ditto "${target_path}" "${link_path}"
done < <(find "${PAYLOAD_DIR}" -type l -print0)

# ══════════════════════════════════════════════════════════════
# STEP 3 — Prune non-runtime files
# ══════════════════════════════════════════════════════════════
log_step "Pruning non-runtime files..."
rm -rf "${PAYLOAD_DIR}/DerivedSources"
find "${PAYLOAD_DIR}" -type d \( -name Headers -o -name PrivateHeaders \) -prune -exec rm -rf {} +
find "${PAYLOAD_DIR}" -type f -name '*.tbd' -delete

# Move top-level .xpc bundles into WebKit.framework/XPCServices.
XPC_FALLBACK_DIR="${PAYLOAD_DIR}/WebKit.framework/XPCServices"
mkdir -p "${XPC_FALLBACK_DIR}"
while IFS= read -r -d '' top_level_xpc; do
    xpc_name="$(basename "${top_level_xpc}")"
    dst="${XPC_FALLBACK_DIR}/${xpc_name}"
    if [[ -L "${dst}" ]]; then
        rm -f "${dst}"; mv "${top_level_xpc}" "${dst}"; continue
    fi
    if [[ -e "${dst}" ]]; then
        rm -rf "${top_level_xpc}"; continue
    fi
    mv "${top_level_xpc}" "${dst}"
done < <(find "${PAYLOAD_DIR}" -mindepth 1 -maxdepth 1 -type d -name '*.xpc' -print0)

# ══════════════════════════════════════════════════════════════
# STEP 4 — Move dylibs to usr/lib
# ══════════════════════════════════════════════════════════════
log_step "Moving dylibs to ${INSTALL_USRLIB_DIR}..."
while IFS= read -r -d '' dylib; do
    base="$(basename "${dylib}")"
    if [[ "${base}" == "${SWIFT_OVERLAY_NAME}" ]]; then
        # The bundle-relative copy is the one WebKit loads.  A top-level copy
        # would only add a second file the loader never reads.
        log_note "Keeping ${base} inside WebKit.framework only; dropping the top-level copy."
        rm -f "${dylib}"
        continue
    fi
    cp -f "${dylib}" "${USRLIB_DIR}/${base}"
    rm -f "${dylib}"
done < <(find "${PAYLOAD_DIR}" -mindepth 1 -maxdepth 1 -type f -name '*.dylib' -print0)

# ══════════════════════════════════════════════════════════════
# STEP 5 — Rewrite framework load paths to @rpath
# ══════════════════════════════════════════════════════════════
# Runs after STEP 4 so every destination in the install table already exists,
# and before STEP 6 so the existing ldid pass re-signs the rewritten images.
# Editing a load command invalidates the existing LC_CODE_SIGNATURE: the
# command area of the shipped WebKit spans 8.8 KB, inside the code pages the
# signature hashes, so a rewrite that is not followed by a re-sign ships a
# broken seal.  Re-signing is ldid's job here rather than a hand-recomputed
# LC_CODE_SIGNATURE because ldid is already required and already runs over
# every Mach-O in this payload, and because the XPC services need their
# entitlements on the new seal.
#
# llvm-install-name-tool is not used: it rejects every image in this payload
# with "unsupported load command", and it has no way to leave a command it does
# not know alone.  Measured on the shipped payload, the rejected command is
# 0x1e (LC_SEGMENT_SPLIT_INFO) in WebKit.framework/WebKit and 0x27
# (LC_DYLD_ENVIRONMENT) in the XPC helpers.  LC_LAZY_LOAD_DYLIB is 0x20 and
# appears zero times in this payload, so it is not what stops that tool.  The
# rewrite is a small Mach-O reader below instead, which needs no tool beyond the
# python3 this script already requires.
log_step "Rewriting framework load paths to @rpath..."

MACHO_LOAD_PATHS_TOOL="${WORK_DIR}/macho-load-paths.py"

write_macho_load_path_tool() {
    cat > "${MACHO_LOAD_PATHS_TOOL}" <<'MACHO_LOAD_PATHS_PYTHON'
"""Rewrite and verify the framework load paths inside a packaged payload.

Modes:
  rewrite <root> <prefix>   Replace every load command that names one of the
                            system frameworks this package ships with its
                            @rpath form, then give the image the LC_RPATH
                            entries that resolve it.
  verify  <root> <prefix>   Fail unless every Mach-O under <root> is free of
                            the system framework prefixes, and unless every
                            @rpath dependency resolves to a file inside <root>.

<root> is an unpacked package tree, <prefix> the install prefix it targets:
"/var/jb" for rootless, "" for rootful and roothide.

An image this program cannot parse, a truncated image, a system path with no row
in the install table, a row whose destination is missing from the payload, and
an image whose header pad cannot take the LC_RPATH entries all exit non-zero.
Skipping an image is never a success.  Every check runs before the first write,
so a rejected payload is left byte-identical.
"""

import os
import shutil
import struct
import subprocess
import sys

HEADER_SIZE_64 = 32
SEGMENT64_SECTIONS_OFFSET = 72
SECTION64_SIZE = 80
SECTION64_FILE_OFFSET = 48

MH_MAGIC_64 = 0xFEEDFACF
THIN_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce")
FAT_MAGICS = (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca")

LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0xC
LC_LAZY_LOAD_DYLIB = 0x20
LC_REEXPORT_DYLIB = 0x8000001F
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_RPATH = 0x8000001C
DYLIB_COMMAND_KINDS = (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB)

# Where each command kind keeps its string offset, and the smallest offset the
# string can have.  LC_RPATH's fixed part is 12 bytes, a dylib command's is 24.
# These are floors for validation only; the actual offset is always read from
# the command's own lc_str field.
NAME_OFFSETS = {LC_RPATH: 12, LC_LOAD_DYLIB: 24, LC_LOAD_WEAK_DYLIB: 24,
                LC_REEXPORT_DYLIB: 24, LC_LAZY_LOAD_DYLIB: 24}

# Every install name a linked image can carry.  The check in verify_image uses
# it as a format test, not a whitelist: a name outside these forms is the tail
# of a name that a wrong-offset write left behind.
INSTALL_NAME_PREFIXES = ("/", "@rpath/", "@executable_path/", "@loader_path/")

FORBIDDEN_PREFIXES = (
    "/System/Library/Frameworks/WebKit.framework/",
    "/System/Library/Frameworks/WebCore.framework/",
    "/System/Library/Frameworks/JavaScriptCore.framework/",
    "/System/Library/Frameworks/WebKitLegacy.framework/",
    "/System/Library/Frameworks/WebGPU.framework/",
    "/System/Library/PrivateFrameworks/WebCore.framework/",
    "/System/Library/PrivateFrameworks/WebKitLegacy.framework/",
)

# (system install name, @rpath name, path relative to the install prefix).
# Exact match on purpose: the system copy of WebCore lives under
# PrivateFrameworks while this package installs it under Library/Frameworks, and
# libwebrtc/libANGLE sit under the system WebCore bundle while the package lays
# them flat in usr/lib.  No prefix rewrite expresses those two moves.  WebKitLegacy
# likewise appears under PrivateFrameworks, as the re-export target of
# WebKit.framework/WebKit, and under Frameworks, as a plain dependency of WebCore.
INSTALL_PATHS = (
    ("/System/Library/Frameworks/WebKit.framework/WebKit",
     "@rpath/WebKit.framework/WebKit",
     "Library/Frameworks/WebKit.framework/WebKit"),
    ("/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
     "@rpath/JavaScriptCore.framework/JavaScriptCore",
     "Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
    ("/System/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
     "@rpath/WebKitLegacy.framework/WebKitLegacy",
     "Library/Frameworks/WebKitLegacy.framework/WebKitLegacy"),
    ("/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy",
     "@rpath/WebKitLegacy.framework/WebKitLegacy",
     "Library/Frameworks/WebKitLegacy.framework/WebKitLegacy"),
    ("/System/Library/Frameworks/WebGPU.framework/WebGPU",
     "@rpath/WebGPU.framework/WebGPU",
     "Library/Frameworks/WebGPU.framework/WebGPU"),
    ("/System/Library/PrivateFrameworks/WebCore.framework/WebCore",
     "@rpath/WebCore.framework/WebCore",
     "Library/Frameworks/WebCore.framework/WebCore"),
    ("/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib",
     "@rpath/libwebrtc.dylib",
     "usr/lib/libwebrtc.dylib"),
    ("/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib",
     "@rpath/libANGLE-shared.dylib",
     "usr/lib/libANGLE-shared.dylib"),
)

# Both are needed: the frameworks resolve through the first, the two flat dylibs
# through the second.  Procursus uses the same shape in its own binaries, where
# /var/jb/usr/bin/true carries a single LC_RPATH of /var/jb/usr/lib.
RPATH_DIRECTORIES = ("Library/Frameworks", "usr/lib")


class PayloadError(Exception):
    """A payload this program refuses to ship."""


class Command:
    __slots__ = ("kind", "offset", "size")

    def __init__(self, kind, offset, size):
        self.kind = kind
        self.offset = offset
        self.size = size


class Image:
    """One thin 64-bit Mach-O, parsed far enough to move its load paths."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as handle:
            self.data = bytearray(handle.read())
        if len(self.data) < HEADER_SIZE_64:
            raise PayloadError("%s is shorter than a Mach-O header" % path)
        magic = struct.unpack_from("<I", self.data, 0)[0]
        if magic in (0xCAFEBABE, 0xCAFEBABF, 0xBEBAFECA, 0xBFBAFECA):
            raise PayloadError("%s is a universal image; this packager ships thin images only" % path)
        if magic != MH_MAGIC_64:
            raise PayloadError("%s is not a 64-bit little-endian Mach-O (magic 0x%08x)" % (path, magic))
        self.command_count = struct.unpack_from("<I", self.data, 16)[0]
        self.command_bytes = struct.unpack_from("<I", self.data, 20)[0]
        if len(self.data) < HEADER_SIZE_64 + self.command_bytes:
            raise PayloadError("%s is truncated: sizeofcmds says %d command bytes but the file holds %d"
                               % (path, self.command_bytes, len(self.data) - HEADER_SIZE_64))
        self.commands = self._parse_commands()

    def _parse_commands(self):
        commands = []
        cursor = HEADER_SIZE_64
        limit = HEADER_SIZE_64 + self.command_bytes
        for index in range(self.command_count):
            if cursor + 8 > limit:
                raise PayloadError("%s: load command %d runs past the command area" % (self.path, index))
            kind, size = struct.unpack_from("<II", self.data, cursor)
            if size < 8 or cursor + size > limit:
                raise PayloadError("%s: load command %d has an impossible size %d" % (self.path, index, size))
            commands.append(Command(kind, cursor, size))
            cursor += size
        if cursor != limit:
            raise PayloadError("%s: load commands end at %d but sizeofcmds says %d" % (self.path, cursor, limit))
        return commands

    def _c_string(self, start, end):
        raw = bytes(self.data[start:end]).split(b"\0", 1)[0]
        if not raw:
            raise PayloadError("%s: a load command carries an empty string" % self.path)
        return raw.decode("utf-8", "surrogateescape")

    def _string_offset(self, command):
        # The offset is a field, not a constant: the shipped WebKit carries a
        # 28-byte LC_LOAD_DYLIB beside 24-byte ones, so it is read and bounded
        # rather than assumed.
        floor = NAME_OFFSETS[command.kind]
        if command.size <= floor:
            raise PayloadError("%s: load command 0x%08x at %d is too small to carry its string"
                               % (self.path, command.kind, command.offset))
        offset = struct.unpack_from("<I", self.data, command.offset + 8)[0]
        if offset < floor or offset >= command.size:
            raise PayloadError("%s: load command 0x%08x at %d puts its string at %d, outside %d..%d"
                               % (self.path, command.kind, command.offset, offset, floor, command.size - 1))
        return offset

    def install_name_commands(self):
        entries = []
        for command in self.commands:
            if command.kind not in DYLIB_COMMAND_KINDS:
                continue
            offset = self._string_offset(command)
            entries.append((command, self._c_string(command.offset + offset,
                                                    command.offset + command.size)))
        return entries

    def rpaths(self):
        values = []
        for command in self.commands:
            if command.kind != LC_RPATH:
                continue
            offset = self._string_offset(command)
            values.append(self._c_string(command.offset + offset, command.offset + command.size))
        return values

    def name_slot(self, command):
        """The byte range a dylib command's name occupies, read from its own field.

        Both the reader and the writer use this: `lc_str` is a field, not a
        constant.  The shipped WebKit carries one 28-byte LC_LOAD_DYLIB beside
        its 24-byte ones, so a writer that assumed 24 would move the name and
        leave the field pointing at the tail of it.
        """
        start = command.offset + self._string_offset(command)
        return start, command.offset + command.size

    def replace_name(self, command, name):
        encoded = name.encode("utf-8")
        start, end = self.name_slot(command)
        if len(encoded) >= end - start:
            raise PayloadError("%s: %r does not fit in the %d-byte slot that named a longer path"
                               % (self.path, name, end - start))
        self.data[start:end] = encoded + b"\0" * (end - start - len(encoded))
        # Read the name back the way the loader will, so a write at the wrong
        # offset fails here rather than shipping an image whose load command
        # names a file that does not exist.
        if self._c_string(start, end) != name:
            raise PayloadError("%s: wrote %r at %d but the command now resolves %r"
                               % (self.path, name, start, self._c_string(start, end)))

    def _header_pad_end(self):
        # The gap between the end of the load commands and the first byte of
        # section data is what can take another command without moving anything.
        limit = None
        for command in self.commands:
            if command.kind != LC_SEGMENT_64:
                continue
            sections = struct.unpack_from("<I", self.data, command.offset + 64)[0]
            cursor = command.offset + SEGMENT64_SECTIONS_OFFSET
            for _ in range(sections):
                offset = struct.unpack_from("<I", self.data, cursor + SECTION64_FILE_OFFSET)[0]
                if offset:
                    limit = offset if limit is None else min(limit, offset)
                cursor += SECTION64_SIZE
        if limit is None:
            raise PayloadError("%s: no section carries a file offset, so the header pad has no known end"
                               % self.path)
        return limit

    def rpath_blob(self, value):
        encoded = value.encode("utf-8") + b"\0"
        size = (NAME_OFFSETS[LC_RPATH] + len(encoded) + 7) // 8 * 8
        blob = struct.pack("<III", LC_RPATH, size, NAME_OFFSETS[LC_RPATH]) + encoded
        return blob + b"\0" * (size - len(blob))

    def missing_rpath_blobs(self, values):
        """The LC_RPATH blobs this image still needs, in the order they are appended."""
        present = self.rpaths()
        return [self.rpath_blob(value) for value in values if value not in present]

    def check_rpath_capacity(self, blobs):
        # Checked while planning, before any image is written, so a payload whose
        # header pad is too small leaves every image untouched.
        if not blobs:
            return
        start = HEADER_SIZE_64 + self.command_bytes
        end = self._header_pad_end()
        needed = sum(len(blob) for blob in blobs)
        if start + needed > end:
            raise PayloadError("%s: no room for %d LC_RPATH byte(s) at %d; the first section starts at %d"
                               % (self.path, needed, start, end))

    def append_rpaths(self, blobs):
        start = HEADER_SIZE_64 + self.command_bytes
        for blob in blobs:
            self.data[start:start + len(blob)] = blob
            start += len(blob)
            self.command_count += 1
            self.command_bytes += len(blob)
        if blobs:
            struct.pack_into("<II", self.data, 16, self.command_count, self.command_bytes)

    def save(self):
        mode = os.stat(self.path).st_mode
        os.chmod(self.path, mode | 0o200)
        with open(self.path, "wb") as handle:
            handle.write(self.data)
        os.chmod(self.path, mode)


def is_macho(path):
    try:
        with open(path, "rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in THIN_MAGICS or magic in FAT_MAGICS


def payload_images(root):
    for directory, _, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(directory, name)
            if is_macho(path):
                yield path


def install_mapping(system_path):
    for row in INSTALL_PATHS:
        if row[0] == system_path:
            return row[1], row[2]
    raise PayloadError("%s has no row in the install table; add one before shipping this payload"
                       % system_path)


def rewrite(root, prefix):
    # Planning parses every image, resolves every destination, and checks the
    # header pad of every image that needs new LC_RPATH bytes, before the first
    # write.  A payload this program rejects therefore stays byte-identical.
    plan = []
    for path in payload_images(root):
        image = Image(path)
        replacements = []
        for command, name in image.install_name_commands():
            if not name.startswith(FORBIDDEN_PREFIXES):
                continue
            rpath_name, relative = install_mapping(name)
            destination = prefix + "/" + relative
            if not os.path.exists(os.path.join(root, destination.lstrip("/"))):
                raise PayloadError("%s names %s, and its install row points at %s, which is not in the payload"
                                   % (path, name, destination))
            replacements.append((command, rpath_name))
        if replacements:
            blobs = image.missing_rpath_blobs([prefix + "/" + directory for directory in RPATH_DIRECTORIES])
            image.check_rpath_capacity(blobs)
            plan.append((path, image, replacements, blobs))
    if not plan:
        raise PayloadError("no Mach-O under %s names a system framework path; the install table matched nothing"
                           % root)
    total = 0
    for path, image, replacements, blobs in plan:
        for command, rpath_name in replacements:
            image.replace_name(command, rpath_name)
        image.append_rpaths(blobs)
        image.save()
        total += len(replacements)
        print("  %s: rewrote %d load command(s)" % (os.path.relpath(path, root), len(replacements)))
    print("Rewrote %d load command(s) across %d image(s)." % (total, len(plan)))


def verify_image(image, root, relative, prefix):
    problems = []
    entries = image.install_name_commands()
    for command, name in entries:
        if name.startswith(FORBIDDEN_PREFIXES):
            problems.append("%s still names the system path %s" % (relative, name))
            continue
        # An install name is an absolute path or an @-prefixed one.  Anything
        # else is the tail of a name some step overwrote from the wrong offset
        # (writing "@rpath/..." at 24 into a command whose lc_str is 28 yields
        # "th/WebKit.framework/WebKit"), and the prefix and resolution checks
        # below would all wave it through.
        if not name.startswith(INSTALL_NAME_PREFIXES):
            problems.append("%s: load command 0x%08x at %d resolves a name that is neither an "
                            "absolute path nor @-prefixed: %r"
                            % (relative, command.kind, command.offset, name))
    dependencies = [name for _, name in entries if name.startswith("@rpath/")]
    reexports = {name for command, name in entries if command.kind == LC_REEXPORT_DYLIB}
    if not dependencies:
        return problems, dependencies, reexports
    rpaths = image.rpaths()
    for directory in RPATH_DIRECTORIES:
        wanted = prefix + "/" + directory
        if wanted not in rpaths:
            problems.append("%s loads @rpath dependencies but has no LC_RPATH %s" % (relative, wanted))
    for dependency in dependencies:
        tail = dependency[len("@rpath/"):]
        if not any(os.path.exists(os.path.join(root, value.lstrip("/"), tail))
                   for value in rpaths if value.startswith("/")):
            problems.append("%s: %s resolves to no file inside the payload" % (relative, dependency))
    return problems, dependencies, reexports


def verify_with_rabin2(tool, path, relative, dependencies, reexports):
    # rabin2 -l writes "ERROR: Missing bin header libs" to stderr and still
    # exits 0 on a file it cannot read, so neither empty output nor the exit
    # status is a pass signal; both are checked here.  It also never lists an
    # LC_REEXPORT_DYLIB, so those names are compared against the image's own
    # parse only: requiring them from rabin2 would fail every real WebKit.
    result = subprocess.run([tool, "-l", path], capture_output=True, text=True)
    reported = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or "ERROR" in result.stderr:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return ["%s: rabin2 -l could not read the image: %s" % (relative, detail[0] if detail else "no output")]
    problems = ["%s: rabin2 -l still reports %s" % (relative, name)
                for name in reported if name.startswith(FORBIDDEN_PREFIXES)]
    problems += ["%s: rabin2 -l does not report the rewritten dependency %s" % (relative, name)
                 for name in dependencies if name not in reexports and name not in reported]
    return problems


def verify(root, prefix):
    tool = shutil.which("rabin2")
    problems = []
    images = 0
    for path in payload_images(root):
        images += 1
        relative = os.path.relpath(path, root)
        image = Image(path)
        image_problems, dependencies, reexports = verify_image(image, root, relative, prefix)
        problems += image_problems
        if tool:
            problems += verify_with_rabin2(tool, path, relative, dependencies, reexports)
        if not image_problems:
            print("  %s: no system load paths, %d @rpath dependency(ies)" % (relative, len(dependencies)))
    if images == 0:
        problems.append("no Mach-O image found under %s" % root)
    if not tool:
        print("Note: rabin2 is not in PATH, so this check used its own Mach-O reader only.")
    for problem in problems:
        print("Error: %s" % problem, file=sys.stderr)
    if problems:
        return 1
    print("Verified %d Mach-O image(s) carry no system framework load path." % images)
    return 0


def main(argv):
    if len(argv) != 4 or argv[1] not in ("rewrite", "verify"):
        print("usage: macho-load-paths.py rewrite|verify <payload-root> <install-prefix>", file=sys.stderr)
        return 2
    try:
        if argv[1] == "rewrite":
            rewrite(argv[2], argv[3])
            return 0
        return verify(argv[2], argv[3])
    except PayloadError as error:
        print("Error: %s" % error, file=sys.stderr)
        return 1
    except OSError as error:
        print("Error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
MACHO_LOAD_PATHS_PYTHON
}

write_macho_load_path_tool
python3 "${MACHO_LOAD_PATHS_TOOL}" rewrite "${DEB_ROOT}" "${PREFIX}"

# ══════════════════════════════════════════════════════════════
# STEP 6 — Sign Mach-O binaries with ldid
# ══════════════════════════════════════════════════════════════
log_step "Signing Mach-O binaries with ldid..."

WEBKIT_PROCESS_ENTITLEMENTS="${SOURCE_DIR}/Source/WebKit/Scripts/process-entitlements.sh"
JSC_PROCESS_ENTITLEMENTS="${SOURCE_DIR}/Source/JavaScriptCore/Scripts/process-entitlements.sh"
ENTITLEMENTS_WORK_DIR="${WORK_DIR}/entitlements"
mkdir -p "${ENTITLEMENTS_WORK_DIR}"

entitlements_script_for_binary() {
    local rel="$1"
    case "${rel}" in
        JavaScriptCore.framework/Helpers/jsc) echo "${JSC_PROCESS_ENTITLEMENTS}" ;;
        WebKit.framework/XPCServices/*.xpc/*|WebKit.framework/Daemons/webpushd|WebKit.framework/Daemons/adattributiond)
            echo "${WEBKIT_PROCESS_ENTITLEMENTS}" ;;
        *) echo "" ;;
    esac
}

product_name_for_binary() {
    local binary="$1" rel="$2"
    case "${rel}" in
        JavaScriptCore.framework/Helpers/jsc) echo "jsc" ;;
        WebKit.framework/XPCServices/*.xpc/*) basename "$(dirname "${binary}")" .xpc ;;
        WebKit.framework/Daemons/*)           basename "${binary}" ;;
        *) echo "" ;;
    esac
}

sign_macho() {
    local binary="$1"
    local rel="${binary#${PAYLOAD_DIR}/}"
    # Also handle binaries living under USRLIB_DIR.
    if [[ "${rel}" == "${binary}" ]]; then
        rel="${binary#${DEB_ROOT}/}"
    fi

    local entitlements_script product_name entitlements_path
    entitlements_script="$(entitlements_script_for_binary "${rel}")"
    product_name="$(product_name_for_binary "${binary}" "${rel}")"
    entitlements_path=""

    if [[ -n "${entitlements_script}" && -n "${product_name}" && -x "${entitlements_script}" ]]; then
        entitlements_path="${ENTITLEMENTS_WORK_DIR}/${rel//\//_}.entitlements"
        if ! WK_PLATFORM_NAME=iphoneos PRODUCT_NAME="${product_name}" WK_PROCESSED_XCENT_FILE="${entitlements_path}" "${entitlements_script}" >/dev/null 2>&1; then
            log_warn "failed to generate entitlements for ${rel}; signing without explicit entitlements."
            entitlements_path=""
        fi
    fi

    if [[ -n "${entitlements_path}" && -f "${entitlements_path}" ]]; then
        ldid "-S${entitlements_path}" "${binary}"
    else
        ldid -S "${binary}"
    fi
}

while IFS= read -r -d '' candidate; do
    if /usr/bin/file -b "${candidate}" | grep -q 'Mach-O'; then
        sign_macho "${candidate}"
    fi
done < <(find "${DEB_ROOT}" -type f -print0)

# ══════════════════════════════════════════════════════════════
# STEP 7 — Verify the shipped load paths
# ══════════════════════════════════════════════════════════════
# This runs on the bytes that will be shipped, after the re-sign, so it also
# catches a signing step that put a system path back.  The positive signal is
# "the file was identified as a Mach-O and its hit count is 0", never "rabin2
# printed nothing": rabin2 -l exits 0 with an empty stdout and an error on
# stderr for a file it cannot parse.
log_step "Verifying no Mach-O loads a system framework path..."
python3 "${MACHO_LOAD_PATHS_TOOL}" verify "${DEB_ROOT}" "${PREFIX}"

# ══════════════════════════════════════════════════════════════
# STEP 8 — Generate DEBIAN metadata
# ══════════════════════════════════════════════════════════════
log_step "Generating DEBIAN control files..."
DEBIAN_DIR="${DEB_ROOT}/DEBIAN"
mkdir -p "${DEBIAN_DIR}"

# Calculate installed size (in KiB).
INSTALLED_SIZE="$(du -sk "${DEB_ROOT}" | awk '{print $1}')"

cat > "${DEBIAN_DIR}/control" <<CTRL
Package: ${PACKAGE_ID}
Name: ${PACKAGE_NAME}
Version: ${PACKAGE_VERSION}
Architecture: ${PACKAGE_ARCH}
Maintainer: ${PACKAGE_AUTHOR}
Depends: firmware (>= 14.0)
Section: System
Priority: optional
Installed-Size: ${INSTALLED_SIZE}
Description: ${PACKAGE_DESCRIPTION}
CTRL

# ── postinst: ensure proper permissions & refresh caches ─────
cat > "${DEBIAN_DIR}/postinst" <<'POSTINST'
#!/bin/sh
set -e

FRAMEWORKS_DIR="/Library/Frameworks"
USRLIB_DIR="/usr/lib"
if [ -d "/var/jb/Library/Frameworks" ]; then
    FRAMEWORKS_DIR="/var/jb/Library/Frameworks"
    USRLIB_DIR="/var/jb/usr/lib"
fi

# Fix ownership (root:wheel).
chown -R 0:0 "${FRAMEWORKS_DIR}/WebKit.framework" 2>/dev/null || true
chown -R 0:0 "${FRAMEWORKS_DIR}/WebCore.framework" 2>/dev/null || true
chown -R 0:0 "${FRAMEWORKS_DIR}/WebKitLegacy.framework" 2>/dev/null || true
chown -R 0:0 "${FRAMEWORKS_DIR}/JavaScriptCore.framework" 2>/dev/null || true

# Trigger uicache refresh if available.
if command -v uicache >/dev/null 2>&1; then
    uicache --all 2>/dev/null || true
fi

exit 0
POSTINST
chmod 0755 "${DEBIAN_DIR}/postinst"

# ── prerm: inform user; no automatic rollback ────────────────
cat > "${DEBIAN_DIR}/prerm" <<'PRERM'
#!/bin/sh
# Uninstalling custom WebKit.  The stock system dyld shared cache
# will resume providing the original frameworks automatically.
exit 0
PRERM
chmod 0755 "${DEBIAN_DIR}/prerm"

# ── Ensure directory permissions are correct for dpkg-deb ────
find "${DEB_ROOT}" -type d -exec chmod 0755 {} +
# Binaries need 0755; other files 0644.
find "${DEB_ROOT}" -type f -print0 | while IFS= read -r -d '' f; do
    if /usr/bin/file -b "${f}" | grep -q 'Mach-O'; then
        chmod 0755 "${f}"
    else
        chmod 0644 "${f}"
    fi
done
# Maintainer scripts must be executable.
chmod 0755 "${DEBIAN_DIR}/postinst" "${DEBIAN_DIR}/prerm"

# ══════════════════════════════════════════════════════════════
# STEP 9 — Build .deb
# ══════════════════════════════════════════════════════════════
log_step "Building .deb package..."
mkdir -p "$(dirname "${OUTPUT_DEB}")"

# Use DEBIAN_BINARY env to silence dpkg-deb warnings on macOS.
COPYFILE_DISABLE=1 dpkg-deb --build --root-owner-group -Zgzip "${DEB_ROOT}" "${OUTPUT_DEB}" 2>&1 \
    | grep -v 'root-owner-group' || true

if [[ ! -f "${OUTPUT_DEB}" ]]; then
    log_error "dpkg-deb failed to produce: ${OUTPUT_DEB}"
    exit 1
fi

DEB_SIZE="$(du -sh "${OUTPUT_DEB}" | awk '{print $1}')"
log_success "Done.  Package created (${DEB_SIZE}): ${OUTPUT_DEB}"
log_info "Install on device:  dpkg -i $(basename "${OUTPUT_DEB}")"
log_info "Remove from device: dpkg -r ${PACKAGE_ID}"
