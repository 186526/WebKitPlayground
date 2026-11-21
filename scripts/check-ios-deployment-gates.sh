#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCRIPT_NAME="$(basename "$0")"

DEFAULT_BUILD_DIR="${ROOT_DIR}/WebKit/WebKitBuild/Release-iphoneos"
if [[ ! -d "${DEFAULT_BUILD_DIR}" ]]; then
    DEFAULT_BUILD_DIR="${ROOT_DIR}/WebKitBuild/Release-iphoneos"
fi

MAX_MINOS="16.1"
BUILD_DIR="${DEFAULT_BUILD_DIR}"
FORBIDDEN_FRAMEWORK="BrowserEngineKit"

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

Default build dir:
  ${DEFAULT_BUILD_DIR}

Fails when a shipped binary requests a minos above --max-minos (default 16.1),
or when any Mach-O load command names ${FORBIDDEN_FRAMEWORK}.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-dir)
            BUILD_DIR="${2:?missing value for --build-dir}"
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

if [[ ! -d "${BUILD_DIR}" ]]; then
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
            local path="${line##* }"
            local consequence="dyld refuses to load the binary on this OS"
            if [[ "${kind}" != "strong" ]]; then
                consequence="weak link, so the BrowserEngineKit gate did not close"
            fi
            printf "%sFORBIDDEN FRAMEWORK (%s): %s links %s -- %s%s\n" \
                "${C_RED}" "${kind}" "${relative}" "${path}" "${consequence}" "${C_RESET}"
            failures=$(( failures + 1 ))
        done <<< "${links}"
    else
        printf "%sno %s link: %s%s\n" "${C_GREEN}" "${FORBIDDEN_FRAMEWORK}" "${relative}" "${C_RESET}"
    fi
}

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
