#!/usr/bin/env bash
# Builds the idevice-webkit dylib for arm64e iOS 16 without Xcode.
#
# clang from LLVM produces the arm64e Mach-O object; ld64.lld links it into a
# dynamic library whose undefined symbols are resolved by the host process at
# load time. Nothing from the iOS SDK is compiled in, so no SDK is needed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
clang="${CLANG:-clang}"
linker="${LD64:-ld64.lld}"
output_dir="${OUTPUT_DIR:-$here/build}"
install_name="${INSTALL_NAME:-/var/jb/usr/lib/TweakInject/WebSafeWebKit.dylib}"
minimum_ios="${MINIMUM_IOS:-16.0}"

compile_flags=(
  -target "arm64e-apple-ios${minimum_ios}"
  -arch arm64e
  "-miphoneos-version-min=${minimum_ios}"
  -O2
  -fPIC
  -fno-stack-protector

  -Wall -Wextra -Werror
  -nostdinc
)

link_flags=(
  -dylib
  -arch arm64e
  -platform_version ios "${minimum_ios}" "${minimum_ios}"
  -undefined dynamic_lookup
  -install_name "${install_name}"
)

mkdir -p "$output_dir/obj"

command -v "$clang" >/dev/null || { echo "missing compiler: $clang" >&2; exit 1; }
command -v "$linker" >/dev/null || { echo "missing linker: $linker" >&2; exit 1; }

objects=()
for source in "$here"/src/*.c; do
  object="$output_dir/obj/$(basename "${source%.c}").arm64e.o"
  "$clang" "${compile_flags[@]}" -I"$here/src" -c "$source" -o "$object"
  objects+=("$object")
done

"$linker" "${link_flags[@]}" -o "$output_dir/WebSafeWebKit.dylib" "${objects[@]}"

echo "built: $output_dir/WebSafeWebKit.dylib"
