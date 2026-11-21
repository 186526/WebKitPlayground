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
# Both modes fail when an image requests a minos above --max-minos (default
# 16.1), and when a load command makes BrowserEngineKit a load-time
# requirement (LC_LOAD_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB). A weak
# BrowserEngineKit load (LC_LOAD_WEAK_DYLIB) is reported without failing in
# --deb mode; --build-dir mode keeps its original behaviour and counts every
# BrowserEngineKit load command as a failure.
#
# One-line usage:
#   scripts/check-ios-deployment-gates.sh --deb /tmp/artdl/webkit-rootless.deb
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
Usage: ${SCRIPT_NAME} [--build-dir <dir>] [--max-minos <version>]
       ${SCRIPT_NAME} --deb <path> [--max-minos <version>]

Default build dir:
  ${DEFAULT_BUILD_DIR}

--deb unpacks the package with dpkg-deb -x and checks every Mach-O image in
it; the default mode checks the four shipped binaries of a build directory.
Both modes fail when an image requests a minos above --max-minos (default
16.1), or when a load command makes ${FORBIDDEN_FRAMEWORK} a load-time
requirement. --deb reports a weak ${FORBIDDEN_FRAMEWORK} load without failing.

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

# Prints "<platform> <minos>" for the first LC_BUILD_VERSION, or
# "iphoneos <version>" for a legacy LC_VERSION_MIN_IPHONEOS, or "" when absent.
read_platform_and_minos() {
    local binary="$1"
    "${OBJDUMP}" --macho --private-headers "${binary}" 2>/dev/null | awk '
        /^[[:space:]]*cmd LC_BUILD_VERSION[[:space:]]*$/ { in_build = 1; next }
        /^[[:space:]]*cmd LC_VERSION_MIN_IPHONEOS[[:space:]]*$/ { in_legacy = 1; next }
        /^[[:space:]]*cmd / { in_build = 0; in_legacy = 0; next }
        in_build && /^[[:space:]]*platform[[:space:]]/ { platform = $2 }
        in_build && /^[[:space:]]*minos[[:space:]]/ { if (platform != "") print platform, $2; exit }
        in_legacy && /^[[:space:]]*version[[:space:]]/ { print "ios", $2; exit }
    '
}

# Prints "<slice> <platform> <minos>" once per architecture slice, where
# <slice> is the llvm-objdump architecture name for a universal image and "-"
# for a thin one. Prints "<slice> none none" when a slice carries no
# LC_BUILD_VERSION and no LC_VERSION_MIN_IPHONEOS. Every slice is listed so a
# universal image cannot pass on the minos of its first slice alone.
read_slice_versions() {
    local binary="$1"
    local magic
    magic="$(od -An -tx1 -N4 "${binary}" 2>/dev/null | tr -d ' \n')"
    local fat=0
    case "${magic}" in
        cafebabe|cafebabf|bebafeca|bfbafeca) fat=1 ;;
    esac
    # Only a universal image needs --arch all; a thin image reads the same
    # either way, so its check does not depend on that flag being supported.
    typeset -a objdump_args=(--macho --private-headers)
    if [[ "${fat}" == "1" ]]; then
        objdump_args=(--macho --arch all --private-headers)
    fi
    "${OBJDUMP}" "${objdump_args[@]}" "${binary}" 2>/dev/null | awk -v fat="${fat}" '
        function flush() {
            if (arch == "") return
            if (minos != "") print arch, platform, minos
            else print arch, "none", "none"
            platform = ""; minos = ""; in_build = 0; in_legacy = 0
        }
        BEGIN { arch = (fat == 1) ? "" : "-" }
        / \(architecture / {
            flush()
            arch = $0
            sub(/.*\(architecture /, "", arch)
            sub(/\).*/, "", arch)
            next
        }
        END { flush() }
        /^[[:space:]]*cmd LC_BUILD_VERSION[[:space:]]*$/ { in_build = 1; in_legacy = 0; next }
        /^[[:space:]]*cmd LC_VERSION_MIN_IPHONEOS[[:space:]]*$/ { in_legacy = 1; in_build = 0; next }
        /^[[:space:]]*cmd / { in_build = 0; in_legacy = 0; next }
        in_build && /^[[:space:]]*platform[[:space:]]/ { platform = $2; next }
        in_build && /^[[:space:]]*minos[[:space:]]/ {
            if (platform != "" && minos == "") minos = $2
            in_build = 0
            next
        }
        in_legacy && /^[[:space:]]*version[[:space:]]/ {
            if (minos == "") { platform = "ios"; minos = $2 }
            in_legacy = 0
            next
        }
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

# Prints "<kind> <path>" for every dylib load command naming the framework.
list_framework_links() {
    local binary="$1"
    "${OBJDUMP}" --macho --private-headers "${binary}" 2>/dev/null | awk -v needle="${FORBIDDEN_FRAMEWORK}" '
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

failures=0
check_binary() {
    local relative="$1"
    local required="$2"
    local binary="${BUILD_DIR}/${relative}"

    if [[ ! -f "${binary}" ]]; then
        if [[ "${required}" == "1" ]]; then
            printf "%sMISSING: %s%s\n" "${C_RED}" "${relative}" "${C_RESET}"
            failures=$(( failures + 1 ))
        else
            printf "%sSkip: %s not present%s\n" "${C_YELLOW}" "${relative}" "${C_RESET}"
        fi
        return 0
    fi

    local read_back
    read_back="$(read_platform_and_minos "${binary}")"
    if [[ -z "${read_back}" ]]; then
        printf "%sNO LC_BUILD_VERSION / LC_VERSION_MIN_IPHONEOS: %s%s\n" "${C_RED}" "${relative}" "${C_RESET}"
        failures=$(( failures + 1 ))
        return 0
    fi

    local platform="${read_back%% *}"
    local minos="${read_back##* }"
    if version_gt "${minos}" "${MAX_MINOS}"; then
        printf "%sMINOS TOO NEW: %s reports %s %s, allowed at most %s%s\n" \
            "${C_RED}" "${relative}" "${platform}" "${minos}" "${MAX_MINOS}" "${C_RESET}"
        failures=$(( failures + 1 ))
    else
        printf "%sminos ok: %s %s %s%s\n" "${C_GREEN}" "${relative}" "${platform}" "${minos}" "${C_RESET}"
    fi

    local links
    links="$(list_framework_links "${binary}")"
    if [[ -n "${links}" ]]; then
        while IFS= read -r line; do
            local kind="${line%% *}"
            # Not named `path`: zsh ties that name to PATH, so assigning it
            # would hide awk and dpkg-deb from the rest of the run.
            local link_path="${line##* }"
            local consequence="dyld refuses to load the binary on this OS"
            if [[ "${kind}" != "strong" ]]; then
                consequence="weak link, so the BrowserEngineKit gate did not close"
            fi
            printf "%sFORBIDDEN FRAMEWORK (%s): %s links %s -- %s%s\n" \
                "${C_RED}" "${kind}" "${relative}" "${link_path}" "${consequence}" "${C_RESET}"
            failures=$(( failures + 1 ))
        done <<< "${links}"
    else
        printf "%sno %s link: %s%s\n" "${C_GREEN}" "${FORBIDDEN_FRAMEWORK}" "${relative}" "${C_RESET}"
    fi
}

# Unpacks ${deb}, walks every Mach-O image inside it, prints one table row per
# image and exits 1 when any image fails a gate.
check_deb() {
    local deb="$1"
    local unpack_dir
    unpack_dir="$(mktemp -d "${TMPDIR:-/tmp}/webkit-deployment-gates.XXXXXX")"
    DEB_UNPACK_DIR="${unpack_dir}"

    printf "%sUnpacking %s with dpkg-deb -x%s\n" "${C_BOLD}${C_CYAN}" "${deb}" "${C_RESET}"
    if ! dpkg-deb -x "${deb}" "${unpack_dir}"; then
        printf "%sError: dpkg-deb could not unpack %s%s\n" "${C_RED}" "${deb}" "${C_RESET}" >&2
        exit 2
    fi

    typeset -a candidates=()
    local file
    while IFS= read -r -d '' file; do
        candidates+=("${file}")
    done < <(find "${unpack_dir}" -type f -print0)
    candidates=("${(@o)candidates}")

    typeset -a images=()
    for file in "${candidates[@]}"; do
        if is_macho_image "${file}"; then
            images+=("${file}")
        fi
    done

    local prefix_len=$(( ${#unpack_dir} + 1 ))
    typeset -a row_path=() row_minos=() row_bek=() row_result=() row_level=()
    local image rel slice_rows platform minos links line kind link_path
    local weak_links=0
    local level result image_weak image_fatal bek_cell link_kinds arch

    for image in "${images[@]}"; do
        rel="${image:${prefix_len}}"
        level="ok"
        result="ok"
        image_weak=0
        image_fatal=0
        bek_cell="-"

        typeset -a slice_lines=()
        while IFS= read -r line; do
            [[ -n "${line}" ]] && slice_lines+=("${line}")
        done < <(read_slice_versions "${image}")
        if [[ "${#slice_lines}" -eq 0 ]]; then
            slice_lines=("- none none")
        fi

        typeset -a slice_minos=()
        for line in "${slice_lines[@]}"; do
            arch="${line%% *}"
            platform="${line#* }"
            minos="${platform#* }"
            platform="${platform%% *}"
            if [[ "${arch}" == "-" ]]; then
                slice_minos+=("${platform} ${minos}")
            else
                slice_minos+=("${arch}: ${platform} ${minos}")
            fi
            if [[ "${minos}" == "none" ]]; then
                level="fail"
                result="FAIL"
                DEB_FAILURES+=("${rel}: no LC_BUILD_VERSION or LC_VERSION_MIN_IPHONEOS, so the minos is unknown")
            elif version_gt "${minos}" "${MAX_MINOS}"; then
                level="fail"
                result="FAIL"
                if [[ "${arch}" == "-" ]]; then
                    DEB_FAILURES+=("${rel}: minos ${platform} ${minos} is newer than ${MAX_MINOS}")
                else
                    DEB_FAILURES+=("${rel}: minos ${platform} ${minos} is newer than ${MAX_MINOS} in the ${arch} slice")
                fi
            fi
        done
        slice_rows="${(j:, :)slice_minos}"
        row_minos+=("${slice_rows}")

        links="$(list_framework_links "${image}")"
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
            weak_links=$(( weak_links + 1 ))
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
        printf "\n%sResult: FAILED (%d image(s) checked, %d failed check(s), %d weak link(s) reported)%s\n" \
            "${C_BOLD}${C_RED}" "${#images}" "${#DEB_FAILURES}" "${weak_links}" "${C_RESET}"
        exit 1
    fi
    printf "\n%sResult: PASS (%d image(s) checked, %d weak link(s) reported)%s\n" \
        "${C_BOLD}${C_GREEN}" "${#images}" "${weak_links}" "${C_RESET}"
    exit 0
}

if [[ "${MODE}" == "deb" ]]; then
    check_deb "${DEB_PATH}"
fi

for relative in "${CHECKED_BINARIES[@]}"; do
    check_binary "${relative}" 1
done
for relative in "${OPTIONAL_BINARIES[@]}"; do
    check_binary "${relative}" 0
done

if [[ "${failures}" -ne 0 ]]; then
    printf "%sResult: FAILED (%s failed check(s))%s\n" "${C_BOLD}${C_RED}" "${failures}" "${C_RESET}"
    exit 1
fi

printf "%sResult: PASS%s\n" "${C_BOLD}${C_GREEN}" "${C_RESET}"
