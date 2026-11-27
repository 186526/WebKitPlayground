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

# write_macho <path> <minos> <cputype> <cpusubtype> [<platform>]
write_macho() {
    python3 - "$@" <<'PY'
import struct, sys
path, minos, cputype, cpusubtype = sys.argv[1:5]
major, minor = (int(x) for x in minos.split('.'))
MH_MAGIC_64 = 0xfeedfacf
LC_BUILD_VERSION = 0x32
platform = int(sys.argv[5]) if len(sys.argv) > 5 else 2
TOOL_LD = 3
header = struct.pack('<IIIIIIII', MH_MAGIC_64, int(cputype, 0) & 0xffffffff,
                     int(cpusubtype, 0) & 0xffffffff, 2, 1, 32, 0, 0)
command = struct.pack('<IIIIII', LC_BUILD_VERSION, 32, platform,
                      (major << 16) | (minor << 8), 18 << 16, 1)
command += struct.pack('<II', TOOL_LD, 0)
open(path, 'wb').write(header + command)
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

write_macho "${WORK}/simulator" 16.1 0x0100000C 0 7
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

write_macho "${WORK}/fat-simulator" 16.1 0x0100000C 0x80000002 7
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
