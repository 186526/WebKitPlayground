#!/bin/zsh
#
# check-ios-deployment-gates.sh — fail when a shipped Mach-O image cannot load
# on the running iOS version.
#
# Modes:
#   --build-dir <dir>   (default) read an Xcode build directory and check the
#                       shipped WebKit, WebCore, JavaScriptCore and WebKitLegacy
#                       binaries.
#   --deb <path>        unpack a .deb with dpkg-deb -x and check every Mach-O
#                       image inside it — the frameworks, the XPC services,
#                       webpushd, adattributiond, jsc and the dylibs under
#                       usr/lib — then print one table row per image.
#
# Both modes check the iOS device platform, CPU and minos in every slice, and
# fail when a load command makes BrowserEngineKit a load-time
# requirement (LC_LOAD_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB). A weak
# BrowserEngineKit load (LC_LOAD_WEAK_DYLIB) is reported without failing in
# --deb mode; --build-dir mode keeps its original behaviour and counts every
# BrowserEngineKit load command as a failure.
#
# Every architecture slice of a universal image is gated, and a slice that
# cannot be read, or that the fat header declares but llvm-objdump does not
# return, fails the run instead of being skipped.
#
# One-line usage:
#   scripts/check-ios-deployment-gates.sh --deb /tmp/artdl/webkit-rootless.deb
#
# Regression tests (synthetic Mach-O, no build or device needed):
#   zsh scripts/test-check-ios-deployment-gates.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCRIPT_NAME="$(basename "$0")"

DEFAULT_BUILD_DIR="${ROOT_DIR}/WebKit/WebKitBuild/Release-iphoneos"
if [[ ! -d "${DEFAULT_BUILD_DIR}" ]]; then
    DEFAULT_BUILD_DIR="${ROOT_DIR}/WebKitBuild/Release-iphoneos"
fi

MAX_MINOS="16.1"
EXPECTED_ARCH=""
REQUIRE_WEBKIT_LAYOUT=0
FORBIDDEN_FRAMEWORK="BrowserEngineKit"

MODE="build-dir"
BUILD_DIR="${DEFAULT_BUILD_DIR}"
BUILD_DIR_GIVEN=0
DEB_PATH=""
DEB_UNPACK_DIR=""
typeset -a DEB_FAILURES=()
typeset -a DEB_WARNINGS=()

# The device deb installs these four; a minos above the running OS or a strong
# link to a framework the OS lacks is exactly what made the shipped deb unusable.
typeset -a CHECKED_BINARIES=(
    "WebKit.framework/WebKit"
    "WebCore.framework/WebCore"
    "JavaScriptCore.framework/JavaScriptCore"
)
typeset -a OPTIONAL_BINARIES=(
    "WebKitLegacy.framework/WebKitLegacy"
)

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
else
    C_RESET=""
    C_BOLD=""
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
fi

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [--build-dir <dir>] [--max-minos <version>] [--arch arm64|arm64e]
       ${SCRIPT_NAME} --deb <path> [--max-minos <version>] [--arch arm64|arm64e] [--require-webkit-layout]

Default build dir:
  ${DEFAULT_BUILD_DIR}

Both modes require iOS device arm64 or arm64e slices with minos at most
--max-minos (default 16.1). With --arch, every slice must match the requested
architecture. --deb checks every Mach-O and reports
weak ${FORBIDDEN_FRAMEWORK} links without failing. --require-webkit-layout
also requires the four rootless WebKit framework binaries and libwebrtc.

Example:
  scripts/${SCRIPT_NAME} --deb /tmp/artdl/webkit-rootless.deb
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-dir)
            BUILD_DIR="${2:?missing value for --build-dir}"
            BUILD_DIR_GIVEN=1
            shift 2
            ;;
        --deb)
            DEB_PATH="${2:?missing value for --deb}"
            MODE="deb"
            shift 2
            ;;
        --max-minos)
            MAX_MINOS="${2:?missing value for --max-minos}"
            shift 2
            ;;
        --arch)
            EXPECTED_ARCH="${2:?missing value for --arch}"
            shift 2
            ;;
        --require-webkit-layout)
            REQUIRE_WEBKIT_LAYOUT=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 64
            ;;
    esac
done

if [[ -n "${EXPECTED_ARCH}" && "${EXPECTED_ARCH}" != arm64 && "${EXPECTED_ARCH}" != arm64e ]]; then
    printf 'Error: --arch must be arm64 or arm64e\n' >&2
    exit 64
fi
if [[ "${MAX_MINOS}" != <->.<-> && "${MAX_MINOS}" != <->.<->.<-> ]]; then
    printf 'Error: --max-minos must be a numeric version\n' >&2
    exit 64
fi
if [[ "${REQUIRE_WEBKIT_LAYOUT}" == 1 && "${MODE}" != deb ]]; then
    printf 'Error: --require-webkit-layout requires --deb\n' >&2
    exit 64
fi

if [[ "${MODE}" == "deb" && "${BUILD_DIR_GIVEN}" == "1" ]]; then
    printf "%sError: --build-dir and --deb select different modes; pass only one%s\n" "${C_RED}" "${C_RESET}" >&2
    exit 64
fi

if [[ "${MODE}" == "deb" ]]; then
    if [[ ! -f "${DEB_PATH}" ]]; then
        printf "%sError: .deb not found: %s%s\n" "${C_RED}" "${DEB_PATH}" "${C_RESET}" >&2
        exit 2
    fi
    if ! command -v dpkg-deb >/dev/null 2>&1; then
        printf "%sError: dpkg-deb not found in PATH; --deb needs it to unpack the package%s\n" "${C_RED}" "${C_RESET}" >&2
        exit 2
    fi
elif [[ ! -d "${BUILD_DIR}" ]]; then
    printf "%sError: build directory not found: %s%s\n" "${C_RED}" "${BUILD_DIR}" "${C_RESET}" >&2
    exit 2
fi

find_objdump() {
    # Xcode's llvm-objdump understands --macho; the GNU objdump does not.
    local candidate=""
    if command -v xcrun >/dev/null 2>&1; then
        candidate="$(xcrun --find llvm-objdump 2>/dev/null || true)"
    fi
    if [[ -z "${candidate}" ]] && command -v llvm-objdump >/dev/null 2>&1; then
        candidate="$(command -v llvm-objdump)"
    fi
    printf '%s\n' "${candidate}"
}

OBJDUMP="$(find_objdump)"
if [[ -z "${OBJDUMP}" ]]; then
    printf "%sError: no llvm-objdump found (xcrun --find llvm-objdump failed)%s\n" "${C_RED}" "${C_RESET}" >&2
    exit 2
fi

read_headers() {
    local binary="$1" declared_slices="$2"
    if [[ "${declared_slices}" -gt 0 ]]; then
        "${OBJDUMP}" --macho --non-verbose --arch all --private-headers "${binary}"
    else
        "${OBJDUMP}" --macho --non-verbose --private-headers "${binary}"
    fi
}

# Prints the number of architecture slices in the fat header of $1, or 0 when
# $1 is not a universal image.
read_fat_slice_count() {
    local binary="$1"
    local magic order hex b1 b2 b3 b4
    magic="$(od -An -tx1 -N4 "${binary}" 2>/dev/null | tr -d ' \n')"
    case "${magic}" in
        cafebabe|cafebabf) order="big" ;;
        bebafeca|bfbafeca) order="little" ;;
        *) printf '0\n'; return 0 ;;
    esac
    hex="$(od -An -tx1 -j4 -N4 "${binary}" 2>/dev/null | tr -d ' \n')"
    if [[ "${order}" == "big" ]]; then
        printf '%d\n' "$(( 16#${hex} ))"
    else
        b1="${hex[1,2]}"; b2="${hex[3,2]}"; b3="${hex[5,2]}"; b4="${hex[7,2]}"
        printf '%d\n' "$(( 16#${b4}${b3}${b2}${b1} ))"
    fi
}

# Bind metadata to Mach headers, because llvm-objdump omits the architecture
# banner for CPU types it cannot name. Numeric fields also cover unknown CPUs.
read_slice_versions() {
    awk -v fat="$1" '
        function flush_slice() {
            if (!slice_index) return
            name = (slice_name != "") ? slice_name : (fat ? "slice-" slice_index : "-")
            print name, (platform != "" ? platform : "none"), (minos != "" ? minos : "none"), cpu, versions
        }
        / \(architecture / {
            banner = $0
            sub(/.*\(architecture /, "", banner)
            sub(/\).*/, "", banner)
            next
        }
        /^Mach header[[:space:]]*$/ {
            flush_slice()
            slice_index++
            slice_name = banner
            banner = platform = minos = ""
            in_build = in_legacy = versions = 0
            cpu = "unknown"
            next
        }
        $1 == "0xfeedfacf" {
            if ($2 != 16777228) cpu = "type-" $2
            else if ($3 == 0 || $3 == 1) cpu = "arm64"
            else if ($3 == 2) cpu = "arm64e"
            else cpu = "arm64-subtype-" $3
            next
        }
        $1 == "cmd" {
            in_build = ($2 == "LC_BUILD_VERSION")
            in_legacy = ($2 == "LC_VERSION_MIN_IPHONEOS")
            if (in_build || $2 ~ /^LC_VERSION_MIN_/) versions++
            next
        }
        in_build && $1 == "platform" { platform = ($2 == 2) ? "ios" : $2 }
        in_build && $1 == "minos" { minos = $2; in_build = 0 }
        in_legacy && $1 == "version" { platform = "ios"; minos = $2; in_legacy = 0 }
        END { flush_slice() }
    '
}

# Fails when $1 is greater than $2, comparing dot-separated numeric components.
version_gt() {
    local left="$1"
    local right="$2"
    awk -v a="${left}" -v b="${right}" 'BEGIN {
        na = split(a, av, ".")
        nb = split(b, bv, ".")
        n = (na > nb) ? na : nb
        for (i = 1; i <= n; i++) {
            x = (i <= na) ? av[i] + 0 : 0
            y = (i <= nb) ? bv[i] + 0 : 0
            if (x > y) exit 0
            if (x < y) exit 1
        }
        exit 1
    }'
}

list_framework_links() {
    awk -v needle="${FORBIDDEN_FRAMEWORK}" '
        /^[[:space:]]*cmd LC_LOAD_DYLIB[[:space:]]*$/ { kind = "strong"; next }
        /^[[:space:]]*cmd LC_LOAD_WEAK_DYLIB[[:space:]]*$/ { kind = "weak"; next }
        /^[[:space:]]*cmd LC_REEXPORT_DYLIB[[:space:]]*$/ { kind = "re-export"; next }
        /^[[:space:]]*cmd LC_LOAD_UPWARD_DYLIB[[:space:]]*$/ { kind = "upward"; next }
        /^[[:space:]]*cmd / { kind = ""; next }
        /^[[:space:]]*name[[:space:]]/ && kind != "" && index($2, needle) > 0 { print kind, $2 }
    '
}

# Succeeds when the first four bytes of $1 are a thin or fat Mach-O magic.
# file(1) descriptions are locale dependent, so the bytes are read directly.
is_macho_image() {
    local binary="$1"
    local magic
    magic="$(od -An -tx1 -N4 "${binary}" 2>/dev/null | tr -d ' \n')"
    case "${magic}" in
        feedface|cefaedfe|feedfacf|cffaedfe|bebafeca|cafebabe|cafebabf|bfbafeca)
            return 0
            ;;
    esac
    return 1
}

cleanup_deb_unpack() {
    if [[ -n "${DEB_UNPACK_DIR}" && -d "${DEB_UNPACK_DIR}" ]]; then
        rm -rf "${DEB_UNPACK_DIR}"
    fi
}
trap cleanup_deb_unpack EXIT

print_rule() {
    printf -- '-%.0s' {1..$1}
    printf '\n'
}

check_slices() {
    local rel="$1" declared_slices="$2" headers="$3"
    local rows arch platform minos cpu versions
    local expected_slices=$(( declared_slices > 0 ? declared_slices : 1 ))
    typeset -a slice_minos=()
    rows="$(read_slice_versions "${declared_slices}" <<< "${headers}")"
    if [[ -n "${rows}" ]]; then
        while read -r arch platform minos cpu versions; do
            if [[ "${arch}" == "-" ]]; then
                slice_minos+=("${platform} ${minos} (${cpu})")
            else
                slice_minos+=("${arch}: ${platform} ${minos} (${cpu})")
            fi
            if [[ "${cpu}" != arm64 && "${cpu}" != arm64e ]]; then
                DEB_FAILURES+=("${rel}: ${arch} has unsupported CPU ${cpu}")
            elif [[ -n "${EXPECTED_ARCH}" && "${cpu}" != "${EXPECTED_ARCH}" ]]; then
                DEB_FAILURES+=("${rel}: ${arch} expected ${EXPECTED_ARCH} CPU, got ${cpu}")
            fi
            if [[ "${versions}" != 1 || "${minos}" != <->.<-> && "${minos}" != <->.<->.<-> ]]; then
                DEB_FAILURES+=("${rel}: ${arch} requires one readable iOS deployment command, found ${versions} and minos ${minos}")
                continue
            fi
            if [[ "${platform}" != ios ]]; then
                DEB_FAILURES+=("${rel}: ${arch} expected iOS device platform (2), got ${platform}")
            fi
            if version_gt "${minos}" "${MAX_MINOS}"; then
                if [[ "${arch}" == "-" ]]; then
                    DEB_FAILURES+=("${rel}: minos ${platform} ${minos} is newer than ${MAX_MINOS}")
                else
                    DEB_FAILURES+=("${rel}: minos ${platform} ${minos} is newer than ${MAX_MINOS} in the ${arch} slice")
                fi
            fi
        done <<< "${rows}"
    fi
    if [[ "${#slice_minos}" -ne "${expected_slices}" ]]; then
        DEB_FAILURES+=("${rel}: expected ${expected_slices} slice(s) but only ${#slice_minos} could be read")
    fi
    row_minos+=("${(j:, :)slice_minos}")
}

check_build() {
    local relative binary declared_slices headers links
    typeset -a row_minos=()
    for relative in "${CHECKED_BINARIES[@]}" "${OPTIONAL_BINARIES[@]}"; do
        binary="${BUILD_DIR}/${relative}"
        if [[ ! -f "${binary}" ]]; then
            if [[ "${relative}" != "${OPTIONAL_BINARIES[1]}" ]]; then
                DEB_FAILURES+=("MISSING: ${relative}")
            fi
            continue
        fi
        declared_slices="$(read_fat_slice_count "${binary}")"
        if ! headers="$(read_headers "${binary}" "${declared_slices}")"; then
            printf 'Error: llvm-objdump failed to read %s\n' "${relative}" >&2
            exit 2
        fi
        check_slices "${relative}" "${declared_slices}" "${headers}"
        printf '%s: %s\n' "${relative}" "${row_minos[-1]}"
        links="$(list_framework_links <<< "${headers}")"
        if [[ -n "${links}" ]]; then
            DEB_FAILURES+=("${relative}: FORBIDDEN FRAMEWORK ${FORBIDDEN_FRAMEWORK}: ${links}")
        fi
    done
}

require_packaged_image() {
    local relative="$2" binary="$1/$2"
    if [[ ! -f "${binary}" || -L "${binary}" || ! -x "${binary}" ]] || ! is_macho_image "${binary}"; then
        DEB_FAILURES+=("${relative}: missing executable Mach-O file at required package path")
    fi
}

# Unpacks ${deb}, walks every Mach-O image inside it, prints one table row per
# image and exits 1 when any image fails a gate.
check_deb() {
    local deb="$1"
    DEB_UNPACK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/webkit-deployment-gates.XXXXXX")"
    local unpack_dir="${DEB_UNPACK_DIR}/root"
    mkdir "${unpack_dir}"

    printf "%sUnpacking %s with dpkg-deb -x%s\n" "${C_BOLD}${C_CYAN}" "${deb}" "${C_RESET}"
    if ! dpkg-deb -x "${deb}" "${unpack_dir}"; then
        printf "%sError: dpkg-deb could not unpack %s%s\n" "${C_RED}" "${deb}" "${C_RESET}" >&2
        exit 2
    fi

    if [[ "${REQUIRE_WEBKIT_LAYOUT}" == 1 ]]; then
        local required_image
        for required_image in "${CHECKED_BINARIES[@]}" "${OPTIONAL_BINARIES[@]}"; do
            require_packaged_image "${unpack_dir}" "var/jb/Library/Frameworks/${required_image}"
        done
        require_packaged_image "${unpack_dir}" var/jb/usr/lib/libwebrtc.dylib
    fi

    typeset -a candidates=()
    local file
    local inventory="${DEB_UNPACK_DIR}/files"
    find "${unpack_dir}" -type f -print0 > "${inventory}"
    while IFS= read -r -d '' file; do
        candidates+=("${file}")
    done < "${inventory}"
    rm -f "${inventory}"
    candidates=("${(@o)candidates}")

    typeset -a images=()
    for file in "${candidates[@]}"; do
        if is_macho_image "${file}"; then
            images+=("${file}")
        fi
    done

    local prefix_len=$(( ${#unpack_dir} + 1 ))
    typeset -a row_path=() row_minos=() row_bek=() row_result=() row_level=()
    local image rel headers links line kind link_path
    local weak_linked_images=0
    local level result image_weak image_fatal bek_cell link_kinds declared_slices previous_failures

    for image in "${images[@]}"; do
        rel="${image:${prefix_len}}"
        level="ok"
        result="ok"
        image_weak=0
        image_fatal=0
        bek_cell="-"
        previous_failures="${#DEB_FAILURES}"
        declared_slices="$(read_fat_slice_count "${image}")"
        if ! headers="$(read_headers "${image}" "${declared_slices}")"; then
            printf 'Error: llvm-objdump failed to read %s\n' "${rel}" >&2
            exit 2
        fi
        check_slices "${rel}" "${declared_slices}" "${headers}"
        if [[ "${#DEB_FAILURES}" -gt "${previous_failures}" ]]; then
            image_fatal=1
        fi

        links="$(list_framework_links <<< "${headers}")"
        if [[ -n "${links}" ]]; then
            link_kinds=""
            while IFS= read -r line; do
                kind="${line%% *}"
                link_path="${line#* }"
                if [[ -n "${link_kinds}" ]]; then
                    link_kinds="${link_kinds},${kind}"
                else
                    link_kinds="${kind}"
                fi
                if [[ "${kind}" == "weak" ]]; then
                    image_weak=1
                    DEB_WARNINGS+=("${rel}: weak link ${link_path}")
                else
                    image_fatal=1
                    DEB_FAILURES+=("${rel}: ${kind} link ${link_path}")
                fi
            done <<< "${links}"
            bek_cell="${link_kinds}"
        fi

        if [[ "${image_fatal}" == "1" ]]; then
            level="fail"
            result="FAIL"
        elif [[ "${image_weak}" == "1" && "${level}" != "fail" ]]; then
            level="warn"
            result="weak ok"
        fi
        if [[ "${image_weak}" == "1" ]]; then
            weak_linked_images=$(( weak_linked_images + 1 ))
        fi

        row_path+=("${rel}")
        row_bek+=("${bek_cell}")
        row_result+=("${result}")
        row_level+=("${level}")
    done

    if [[ "${#images}" -eq 0 ]]; then
        printf "%sError: no Mach-O image found in %s, so nothing was gated%s\n" \
            "${C_BOLD}${C_RED}" "${deb}" "${C_RESET}"
        printf "%sResult: FAILED (0 image(s) checked)%s\n" "${C_BOLD}${C_RED}" "${C_RESET}"
        exit 1
    fi

    local w_path=5 w_minos=5 w_bek=16 w_result=6
    local index
    for index in {1..${#row_path}}; do
        if [[ "${#row_path[index]}" -gt "${w_path}" ]]; then
            w_path="${#row_path[index]}"
        fi
        if [[ "${#row_minos[index]}" -gt "${w_minos}" ]]; then
            w_minos="${#row_minos[index]}"
        fi
        if [[ "${#row_bek[index]}" -gt "${w_bek}" ]]; then
            w_bek="${#row_bek[index]}"
        fi
        if [[ "${#row_result[index]}" -gt "${w_result}" ]]; then
            w_result="${#row_result[index]}"
        fi
    done

    local header
    printf -v header "%-${w_path}s  %-${w_minos}s  %-${w_bek}s  %s" \
        "IMAGE" "MINOS" "${FORBIDDEN_FRAMEWORK}" "RESULT"
    printf "\n%sChecked %d Mach-O image(s) in %s%s\n\n" "${C_BOLD}" "${#images}" "${deb}" "${C_RESET}"
    printf "%s\n" "${header}"
    print_rule "${#header}"

    local row_color
    for index in {1..${#row_path}}; do
        case "${row_level[index]}" in
            fail) row_color="${C_RED}" ;;
            warn) row_color="${C_YELLOW}" ;;
            *) row_color="${C_GREEN}" ;;
        esac
        printf "%-${w_path}s  %-${w_minos}s  %-${w_bek}s  %s%s%s\n" \
            "${row_path[index]}" "${row_minos[index]}" "${row_bek[index]}" \
            "${row_color}" "${row_result[index]}" "${C_RESET}"
    done

    local detail
    if [[ "${#DEB_WARNINGS}" -gt 0 ]]; then
        printf "\n%sWeak %s loads (reported, not fatal):%s\n" \
            "${C_YELLOW}" "${FORBIDDEN_FRAMEWORK}" "${C_RESET}"
        for detail in "${DEB_WARNINGS[@]}"; do
            printf "  %s%s%s\n" "${C_YELLOW}" "${detail}" "${C_RESET}"
        done
    fi

    if [[ "${#DEB_FAILURES}" -gt 0 ]]; then
        printf "\n%sFailed checks:%s\n" "${C_BOLD}${C_RED}" "${C_RESET}"
        for detail in "${DEB_FAILURES[@]}"; do
            printf "  %s%s%s\n" "${C_RED}" "${detail}" "${C_RESET}"
        done
        printf "\n%sResult: FAILED (%d image(s) checked, %d failed check(s), %d image(s) with a weak link)%s\n" \
            "${C_BOLD}${C_RED}" "${#images}" "${#DEB_FAILURES}" "${weak_linked_images}" "${C_RESET}"
        exit 1
    fi
    printf "\n%sResult: PASS (%d image(s) checked, %d image(s) with a weak link)%s\n" \
        "${C_BOLD}${C_GREEN}" "${#images}" "${weak_linked_images}" "${C_RESET}"
    exit 0
}

if [[ "${MODE}" == "deb" ]]; then
    check_deb "${DEB_PATH}"
fi

check_build
if [[ "${#DEB_FAILURES}" -ne 0 ]]; then
    printf '%s\n' "${DEB_FAILURES[@]}" >&2
    printf "%sResult: FAILED (%s failed check(s))%s\n" "${C_BOLD}${C_RED}" "${#DEB_FAILURES}" "${C_RESET}"
    exit 1
fi

printf "%sResult: PASS%s\n" "${C_BOLD}${C_GREEN}" "${C_RESET}"
