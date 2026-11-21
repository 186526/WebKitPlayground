#!/usr/bin/env bash
# Builds the idevice-webkit dylib without Xcode.
#
# clang from LLVM produces the Mach-O object; ld64.lld links it into a dynamic
# library whose undefined symbols are resolved by the host process at load
# time. Nothing from the iOS SDK is compiled in, so no SDK is needed.
#
#   ./build.sh     build/arm64/WebSafeWebKit.dylib, the one slice the package
#                  ships
#
# The output name here is a local build name. package-deb.sh signs this file
# and installs it under the name WebSafeWebKitFilter.dylib.
#
# The image has to keep the CPU subtype ld64.lld writes, ARM64 ALL (0x00000000).
# clang gives its arm64e objects the subtype ARM64E (0x80000002), and 1.0.0
# through 1.0.7 restored that subtype into the linked arm64e slice with a dd
# patch. On the device that slice is loaded and its initializers never run; a
# working image with the header's offset 8 changed to 0x80000002 and nothing
# else touched is killed with signal 9. The same source built as arm64 with the
# subtype left at 0 runs. The check below holds the subtype at 0, and the
# package ships the arm64 slice alone: two slices with subtype 0 are the same
# architecture to lipo, which refuses to put them in one file.
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

mach_header_subtype_offset=8

if [ "$arch" != "arm64" ]; then
  # A second slice cannot be shipped beside the arm64 one: with the subtype
  # held at 0 both are the same architecture to lipo, which refuses to put them
  # in one file, and the package carries a single slice.
  echo "only the arm64 slice is built: a second slice would carry the same" >&2
  echo "cpusubtype 0x00000000 and lipo cannot join the two into one file" >&2
  exit 1
fi

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

# dyld on the device runs no initializer of an image whose header carries the
# ARM64E subtype (0x80000002), and kills one whose content works under another
# subtype once the byte is set. ld64.lld writes 0, and 0 is what the device
# accepted, so a build that changes it has to stop here.
subtype_bytes="$(od -An -t x1 -j "$mach_header_subtype_offset" -N 4 "$output_dir/WebSafeWebKit.dylib" | tr -d ' \n')"
if [ "$subtype_bytes" != "00000000" ]; then
  subtype_le="$(printf '%s' "$subtype_bytes" | sed 's/\(..\)\(..\)\(..\)\(..\)/\4\3\2\1/')"
  echo "the dylib carries a CPU subtype the device runs no initializer of:" >&2
  echo "cpusubtype 0x$subtype_le, expected 0x00000000" >&2
  exit 1
fi

# The device exports the dyld image table calls, and their symbol table name
# carries one leading underscore for the C name plus the assembler's own:
# C `_dyld_image_count` becomes `__dyld_image_count`. A device probe confirmed
# which shape binds: a dylib referring to `__dyld_image_count` ran and read the
# image table (count 97), while one referring to `_dyld_image_count` died with
# "symbol not found in flat namespace '_dyld_image_count'" and one referring to
# `___dyld_image_count` died the same way. The symbol table name therefore has
# exactly two leading underscores, and a C declaration with one too few or one
# too many is the failure. Both of those shapes are refused here; the correct
# one passes.
undefined="$(llvm-nm -u "$output_dir/WebSafeWebKit.dylib")"
if printf '%s\n' "$undefined" | grep -q -e '^_dyld_' -e '^___dyld_'; then
  echo "the dylib refers to a dyld symbol name the device does not export:" >&2
  printf '%s\n' "$undefined" | grep -e '^_dyld_' -e '^___dyld_' >&2
  echo "the symbol table name has two leading underscores: __dyld_image_count" >&2
  exit 1
fi

# The report writer has to pass its mode the variadic way. Darwin's open reads
# the mode from the stack; a fixed three-parameter declaration makes the
# compiler put it in w2 instead, and the device then creates the report with a
# mode nobody asked for, 0504 among them. A write_file that assigns w2 before
# its call to open is that declaration come back.
write_file_body="$(llvm-objdump -d --no-show-raw-insn "$output_dir/WebSafeWebKit.dylib" 2>/dev/null |
  sed -n '/<_write_file>:/,/^$/p')"
if printf '%s\n' "$write_file_body" | grep -q 'mov[[:space:]]*w2,'; then
  echo "the report writer passes the file mode in a register, which the device drops:" >&2
  printf '%s\n' "$write_file_body" | grep 'mov[[:space:]]*w2,' >&2
  exit 1
fi

echo "arch: $arch"
echo "built: $output_dir/WebSafeWebKit.dylib"