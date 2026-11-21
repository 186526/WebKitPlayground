#!/usr/bin/env bash
# Builds the idevice-webkit dylib for one iOS slice without Xcode.
#
# clang from LLVM produces the Mach-O object; ld64.lld links it into a dynamic
# library whose undefined symbols are resolved by the host process at load
# time. Nothing from the iOS SDK is compiled in, so no SDK is needed.
#
#   ARCH=arm64  ./build.sh     one slice for the arm64 hosts
#   ARCH=arm64e ./build.sh     one slice for the arm64e hosts
#   lipo -create build/arm64/... build/arm64e/... -output build/WebSafeWebKit.dylib
#
# The output name here is a local build name. package-deb.sh lipos the two
# slices under the installed name, WebSafeWebKitFilter.dylib.
#
# The installable artifact is the lipo output. A slice on its own only loads in
# a process of its own architecture.
#
# clang writes the arm64e CPU subtype ARM64E (0x80000002) into its objects, but
# ld64.lld stamps every output as ARM64 ALL (0x00000000). Two slices with that
# subtype are indistinguishable to lipo, which refuses to put them in one file,
# and a device has no way to tell the arm64e slice from the arm64 one. The
# header subtype is therefore restored to ARM64E in the arm64e output.
#
# CLANG may hold a command with spaces, such as "xcrun -sdk iphoneos clang".
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
read -r -a clang_command <<<"${CLANG:-clang}"
linker="${LD64:-ld64.lld}"
arch="${ARCH:-arm64}"
output_dir="${OUTPUT_DIR:-$here/build/$arch}"
install_name="${INSTALL_NAME:-/var/jb/usr/lib/TweakInject/WebSafeWebKitFilter.dylib}"
minimum_ios="${MINIMUM_IOS:-16.0}"

compile_flags=(
  -target "${arch}-apple-ios${minimum_ios}"
  -arch "${arch}"
  "-miphoneos-version-min=${minimum_ios}"
  -O2
  -fPIC
  -fno-stack-protector

  -Wall -Wextra -Werror
  -nostdinc
)

link_flags=(
  -dylib
  -arch "${arch}"
  -platform_version ios "${minimum_ios}" "${minimum_ios}"
  -undefined dynamic_lookup
  -install_name "${install_name}"
)

arm64e_subtype=0x80000002
mach_header_subtype_offset=8

mkdir -p "$output_dir/obj"

command -v "${clang_command[0]}" >/dev/null || {
  echo "missing compiler: ${clang_command[0]}" >&2
  exit 1
}
command -v "$linker" >/dev/null || { echo "missing linker: $linker" >&2; exit 1; }

objects=()
for source in "$here"/src/*.c; do
  object="$output_dir/obj/$(basename "${source%.c}").${arch}.o"
  "${clang_command[@]}" "${compile_flags[@]}" -I"$here/src" -c "$source" -o "$object"
  objects+=("$object")
done

"$linker" "${link_flags[@]}" -o "$output_dir/WebSafeWebKit.dylib" "${objects[@]}"

if [ "$arch" = "arm64e" ]; then
  subtype="$(od -An -t u4 -j "$mach_header_subtype_offset" -N 4 "$output_dir/WebSafeWebKit.dylib" | tr -d ' ')"
  if [ "$subtype" != "0" ]; then
    echo "unexpected arm64e cpusubtype: $subtype" >&2
    exit 1
  fi
  printf "\002\000\000\200" | dd of="$output_dir/WebSafeWebKit.dylib" bs=1 seek="$mach_header_subtype_offset" conv=notrunc status=none
fi

echo "arch: $arch"
echo "built: $output_dir/WebSafeWebKit.dylib"
