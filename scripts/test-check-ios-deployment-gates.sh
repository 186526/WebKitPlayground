#!/bin/zsh
#
# test-check-ios-deployment-gates.sh — regression tests for
# scripts/check-ios-deployment-gates.sh.
#
# Builds minimal synthetic Mach-O images, so nothing here needs a WebKit build,
# a real .deb or a device. The images carry only an LC_BUILD_VERSION, which is
# all the checker reads; lipo (Xcode's `lipo`, or Linux's `llvm-lipo`) turns
# them into universal binaries. Set LIPO to pick the tool explicitly.
#
# The case that matters is "unlabelled first slice": llvm-objdump omits its
# "(architecture ...)" banner for a cputype it cannot name, and an earlier
# version of the checker keyed slice boundaries on that banner. A fat file with
# an unnameable 16.1 slice first and an arm64e 18.5 slice second then reported
# the later slice with the earlier slice's minos and printed PASS. These tests
# pin the fix: every slice is reported, and the 18.5 slice fails the run.
#
# Usage: zsh scripts/test-check-ios-deployment-gates.sh
#        LIPO=llvm-lipo zsh scripts/test-check-ios-deployment-gates.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECKER="${SCRIPT_DIR}/check-ios-deployment-gates.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/check-gates-test.XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT

# Xcode ships lipo but not llvm-lipo, and Linux ships llvm-lipo; ask xcrun
# first so a macOS run picks up Xcode's tool, then fall back to PATH. LIPO
# overrides the search, matching tools/idevice-webkit/package-deb.sh.
find_lipo() {
    local candidate=""
    if [[ -n "${LIPO:-}" ]]; then
        printf '%s\n' "${LIPO}"
        return 0
    fi
    if command -v xcrun >/dev/null 2>&1; then
        candidate="$(xcrun -f lipo 2>/dev/null || true)"
    fi
    if [[ -z "${candidate}" ]]; then
        candidate="$(command -v lipo 2>/dev/null || true)"
    fi
    if [[ -z "${candidate}" ]]; then
        candidate="$(command -v llvm-lipo 2>/dev/null || true)"
    fi
    printf '%s\n' "${candidate}"
}

LIPO_BIN="$(find_lipo)"
if [[ -z "${LIPO_BIN}" ]]; then
    printf 'Error: no lipo found; tried xcrun -f lipo, lipo and llvm-lipo.\n' >&2
    printf 'Install Xcode command line tools, or set LIPO to the tool to use.\n' >&2
    exit 2
fi

pass=0
fail=0
C_RED=""
C_GREEN=""
C_RESET=""
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_RESET=$'\033[0m'
fi

# check <name> <want-exit> <want-text> <command> [args...]
check() {
    local name="$1"
    local want_exit="$2"
    local want_text="$3"
    shift 3
    local got_exit=0
    local out
    out="$( "$@" 2>&1 )" || got_exit=$?
    if [[ "${got_exit}" -ne "${want_exit}" ]]; then
        printf "%sFAIL%s %s: exit %d, wanted %d\n%s\n" \
            "${C_RED}" "${C_RESET}" "${name}" "${got_exit}" "${want_exit}" "${out}"
        fail=$(( fail + 1 ))
        return 0
    fi
    if [[ -n "${want_text}" && "${out}" != *"${want_text}"* ]]; then
        printf "%sFAIL%s %s: output does not contain %q\n%s\n" \
            "${C_RED}" "${C_RESET}" "${name}" "${want_text}" "${out}"
        fail=$(( fail + 1 ))
        return 0
    fi
    printf "%sok%s %s\n" "${C_GREEN}" "${C_RESET}" "${name}"
    pass=$(( pass + 1 ))
}

write_macho() {
    python3 - "$@" <<'PY'
import argparse
import struct
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('path', type=Path)
parser.add_argument('minos')
parser.add_argument('cpu_type', type=lambda value: int(value, 0))
parser.add_argument('cpu_subtype', type=lambda value: int(value, 0))
parser.add_argument('--platform', type=int, default=2)
parser.add_argument('--dylib', action='store_true')
image = parser.parse_args()
major, minor = map(int, image.minos.split('.'))
MH_MAGIC_64 = 0xfeedfacf
LC_BUILD_VERSION = 0x32
TOOL_LD = 3
commands = [struct.pack('<8I', LC_BUILD_VERSION, 32, image.platform,
                        (major << 16) | (minor << 8), 18 << 16, 1, TOOL_LD, 0)]
if image.dylib:
    name = b'@rpath/test.dylib\0'
    name += b'\0' * (-len(name) % 8)
    commands.append(struct.pack('<6I', 0xd, 24 + len(name), 24, 0, 0, 0) + name)
header = struct.pack('<8I', MH_MAGIC_64, image.cpu_type, image.cpu_subtype,
                     6 if image.dylib else 2, len(commands), sum(map(len, commands)), 0, 0)
image.path.write_bytes(header + b''.join(commands))
PY
}

# make_deb <out> <relative-image-path> <image>
make_deb() {
    local out="$1"
    local rel="$2"
    local image="$3"
    local root="${WORK}/root.$$.$RANDOM"
    mkdir -p "$(dirname "${root}/${rel}")" "${root}/DEBIAN"
    cp "${image}" "${root}/${rel}"
    cat > "${root}/DEBIAN/control" <<'CONTROL'
Package: test.gates
Version: 1
Architecture: iphoneos-arm64
Maintainer: test <test@example.com>
Description: regression fixture for the deployment gate checker
CONTROL
    printf '#!/bin/sh\nexit 0\n' > "${root}/DEBIAN/postinst"
    printf '#!/bin/sh\nexit 0\n' > "${root}/DEBIAN/prerm"
    chmod 755 "${root}/DEBIAN" "${root}/DEBIAN/postinst" "${root}/DEBIAN/prerm"
    chmod 644 "${root}/DEBIAN/control"
    dpkg-deb --build --root-owner-group -Zgzip "${root}" "${out}" >/dev/null 2>&1
}

WEBKIT_REL="var/jb/Library/Frameworks/WebKit.framework/WebKit"

write_macho "${WORK}/thin-16.1" 16.1 0x0100000C 0
make_deb "${WORK}/thin-good.deb" "${WEBKIT_REL}" "${WORK}/thin-16.1"
check "thin 16.1 image passes" 0 "Result: PASS" \
    "${CHECKER}" --deb "${WORK}/thin-good.deb"

write_macho "${WORK}/thin-18.5" 18.5 0x0100000C 0
make_deb "${WORK}/thin-bad.deb" "${WEBKIT_REL}" "${WORK}/thin-18.5"
check "thin 18.5 image fails and names 18.5" 1 \
    "minos ios 18.5 is newer than 16.1" \
    "${CHECKER}" --deb "${WORK}/thin-bad.deb"

write_macho "${WORK}/fat-a" 16.1 0x0100000C 0
write_macho "${WORK}/fat-b" 18.5 0x0100000C 0x80000002
chmod 755 "${WORK}/fat-a" "${WORK}/fat-b"
"${LIPO_BIN}" -create "${WORK}/fat-a" "${WORK}/fat-b" -output "${WORK}/fat-ordered" >/dev/null
make_deb "${WORK}/fat-ordered.deb" "${WEBKIT_REL}" "${WORK}/fat-ordered"
check "universal image with a later 18.5 slice fails" 1 \
    "minos ios 18.5 is newer than 16.1 in the arm64e slice" \
    "${CHECKER}" --deb "${WORK}/fat-ordered.deb"

# The regression: the first slice carries a cputype llvm-objdump cannot name,
# so it prints no banner for that slice, while the second slice is named. This
# reported PASS before the fix because the first slice's 16.1 leaked forward.
write_macho "${WORK}/fat-unknown" 16.1 30 0
chmod 755 "${WORK}/fat-unknown"
"${LIPO_BIN}" -create "${WORK}/fat-unknown" "${WORK}/fat-b" -output "${WORK}/fat-unlabelled" >/dev/null
make_deb "${WORK}/fat-unlabelled.deb" "${WEBKIT_REL}" "${WORK}/fat-unlabelled"
check "unlabelled first slice does not leak its minos into the next" 1 \
    "minos ios 18.5 is newer than 16.1 in the arm64e slice" \
    "${CHECKER}" --deb "${WORK}/fat-unlabelled.deb"
check "unlabelled first slice is still reported" 1 \
    "slice-1: ios 16.1" \
    "${CHECKER}" --deb "${WORK}/fat-unlabelled.deb"

write_macho "${WORK}/simulator" 16.1 0x0100000C 0 --platform 7
make_deb "${WORK}/simulator.deb" "${WEBKIT_REL}" "${WORK}/simulator"
check "an arm64 simulator image fails despite matching minos" 1 "expected iOS device platform" \
    "${CHECKER}" --deb "${WORK}/simulator.deb"

write_macho "${WORK}/x86-ios" 16.1 0x01000007 3
make_deb "${WORK}/x86-ios.deb" "${WEBKIT_REL}" "${WORK}/x86-ios"
check "an x86_64 iOS image fails despite matching platform and minos" 1 "unsupported CPU" \
    "${CHECKER}" --deb "${WORK}/x86-ios.deb"

check "the declared target architecture must match" 1 "expected arm64e CPU, got arm64" \
    "${CHECKER}" --deb "${WORK}/thin-good.deb" --arch arm64e
write_macho "${WORK}/fat-good-e" 16.1 0x0100000C 0x80000002
"${LIPO_BIN}" -create "${WORK}/fat-a" "${WORK}/fat-good-e" -output "${WORK}/fat-good"
make_deb "${WORK}/fat-good.deb" "${WEBKIT_REL}" "${WORK}/fat-good"
check "both supported architectures pass without an explicit target" 0 "Result: PASS" \
    "${CHECKER}" --deb "${WORK}/fat-good.deb"
check "a matching slice does not excuse a slice outside the declared target" 1 "expected arm64e CPU, got arm64" \
    "${CHECKER}" --deb "${WORK}/fat-good.deb" --arch arm64e
check "an incomplete WebKit layout is rejected" 1 "missing executable Mach-O file at required package path" \
    "${CHECKER}" --deb "${WORK}/thin-good.deb" --require-webkit-layout

# Match platform, architecture and deployment version in both slices, so only
# the filetype distinguishes the good package from the broken ones.
write_macho "${WORK}/dylib-a" 16.1 0x0100000C 0 --dylib
write_macho "${WORK}/dylib-e" 16.1 0x0100000C 0x80000002 --dylib
"${LIPO_BIN}" -create "${WORK}/dylib-a" "${WORK}/dylib-e" -output "${WORK}/fat-dylib"
"${LIPO_BIN}" -create "${WORK}/dylib-a" "${WORK}/fat-good-e" -output "${WORK}/fat-wrong-filetype"

layout="${WORK}/layout"
mkdir -p "${layout}/DEBIAN" "${layout}/var/jb/usr/lib"
for framework in WebKit WebCore WebKitLegacy JavaScriptCore; do
    mkdir -p "${layout}/var/jb/Library/Frameworks/${framework}.framework"
    cp "${WORK}/fat-dylib" "${layout}/var/jb/Library/Frameworks/${framework}.framework/${framework}"
done
dpkg-deb -e "${WORK}/thin-good.deb" "${layout}/DEBIAN"
webrtc="${layout}/var/jb/usr/lib/libwebrtc.dylib"
cp "${WORK}/fat-dylib" "${webrtc}"
chmod +x "${webrtc}" "${layout}"/var/jb/Library/Frameworks/*.framework/*
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-good.deb" >/dev/null
check "all required paths contain dylibs in every slice" 0 "Result: PASS" \
    "${CHECKER}" --deb "${WORK}/layout-good.deb" --require-webkit-layout
cp "${WORK}/fat-wrong-filetype" "${webrtc}"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-wrong-dylib.deb" >/dev/null
check "libwebrtc rejects an executable slice despite a matching dylib slice" 1 \
    "var/jb/usr/lib/libwebrtc.dylib: arm64e expected MH_DYLIB (6), got filetype 2" \
    "${CHECKER}" --deb "${WORK}/layout-wrong-dylib.deb" --require-webkit-layout
cp "${WORK}/fat-dylib" "${webrtc}"
cp "${WORK}/thin-16.1" "${layout}/${WEBKIT_REL}"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-wrong-framework.deb" >/dev/null
check "a framework path rejects an executable Mach-O" 1 \
    "${WEBKIT_REL}: - expected MH_DYLIB (6), got filetype 2" \
    "${CHECKER}" --deb "${WORK}/layout-wrong-framework.deb" --require-webkit-layout
cp "${WORK}/fat-dylib" "${layout}/${WEBKIT_REL}"

frameworks="${layout}/var/jb/Library/Frameworks"
ln -s WebKit.framework "${frameworks}/Optional.framework"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-optional-link.deb" >/dev/null
check "strict layout allows an optional framework symlink" 0 "Result: PASS" \
    "${CHECKER}" --deb "${WORK}/layout-optional-link.deb" --require-webkit-layout

mv "${frameworks}/WebCore.framework" "${frameworks}/WebCore-data"
ln -s WebCore-data "${frameworks}/WebCore.framework"
cp "${WORK}/fat-wrong-filetype" "${frameworks}/WebCore-data/WebCore"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-aliased-framework.deb" >/dev/null
check "strict layout rejects a required framework directory symlink" 1 \
    "var/jb/Library/Frameworks/WebCore.framework is a symlink" \
    "${CHECKER}" --deb "${WORK}/layout-aliased-framework.deb" --require-webkit-layout
check "non-strict inspection still checks the alias target by its own path" 0 "WebCore-data/WebCore" \
    "${CHECKER}" --deb "${WORK}/layout-aliased-framework.deb"

rm "${frameworks}/WebCore.framework"
mv "${frameworks}/WebCore-data" "${frameworks}/WebCore.framework"
cp "${WORK}/fat-dylib" "${frameworks}/WebCore.framework/WebCore"
mv "${frameworks}" "${frameworks}-data"
ln -s Frameworks-data "${frameworks}"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-aliased-parent.deb" >/dev/null
check "strict layout rejects a shared parent directory symlink" 1 \
    "required directory var/jb/Library/Frameworks is a symlink" \
    "${CHECKER}" --deb "${WORK}/layout-aliased-parent.deb" --require-webkit-layout

rm "${frameworks}"
mv "${frameworks}-data" "${frameworks}"
mv "${webrtc}" "${webrtc}.real"
ln -s libwebrtc.dylib.real "${webrtc}"
dpkg-deb --build --root-owner-group -Zgzip "${layout}" "${WORK}/layout-aliased-library.deb" >/dev/null
check "strict layout rejects a symlink at a required library path" 1 \
    "var/jb/usr/lib/libwebrtc.dylib: required file is a symlink" \
    "${CHECKER}" --deb "${WORK}/layout-aliased-library.deb" --require-webkit-layout

write_macho "${WORK}/fat-simulator" 16.1 0x0100000C 0x80000002 --platform 7
"${LIPO_BIN}" -create "${WORK}/fat-a" "${WORK}/fat-simulator" -output "${WORK}/fat-wrong-platform"
make_deb "${WORK}/fat-wrong-platform.deb" "${WEBKIT_REL}" "${WORK}/fat-wrong-platform"
check "a later simulator slice still fails the iOS device gate" 1 "arm64e expected iOS device platform" \
    "${CHECKER}" --deb "${WORK}/fat-wrong-platform.deb"

mkdir -p "${WORK}/stub"
cat > "${WORK}/stub/llvm-objdump" <<'STUB'
#!/bin/sh
printf 'Mach header\n 0xfeedfacf 16777228 0 0x00 2 1 32 0x00000000\n cmd LC_BUILD_VERSION\n platform 2\n minos 16.1\n'
exit 19
STUB
printf '#!/bin/sh\nexit 1\n' > "${WORK}/stub/xcrun"
chmod +x "${WORK}/stub/llvm-objdump" "${WORK}/stub/xcrun"
check "nonzero inspection tool status fails despite valid-looking output" 2 "llvm-objdump failed to read" \
    env PATH="${WORK}/stub:${PATH}" "${CHECKER}" --deb "${WORK}/thin-good.deb"

printf '\n%d passed, %d failed\n' "${pass}" "${fail}"
[[ "${fail}" -eq 0 ]]
