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
# The image has to carry three things the device checks together, and 1.0.9
# proved that a build missing any one of them is loaded without its
# initializer ever running:
#
#   cpusubtype      0x80000002 (ARM64E), not the 0x0 ld64.lld writes
#   fixup format    LC_DYLD_CHAINED_FIXUPS, with the constructor in
#                   __init_offsets rather than __mod_init_func
#   signature       written by ldid, after the two above are in place
#
# The device experiments behind this, all with /var/jb/usr/bin/true and Safari:
#
#   subtype    fixups                 constructor
#   0x0        LC_DYLD_INFO_ONLY      does not run
#   0x80000002 LC_DYLD_INFO_ONLY      image loads, constructor does not run
#   0x0        LC_DYLD_CHAINED_FIXUPS runs
#   0x80000002 LC_DYLD_CHAINED_FIXUPS runs, and an abort() inside it takes
#                                     Safari down, with the crash report naming
#                                     abort() called
#
# An earlier reading of the first two rows said the device rejects the ARM64E
# subtype. It does not: those images carried LC_DYLD_INFO_ONLY, and once the
# header byte was changed the signature no longer matched the page it covers,
# which is what the device acted on. The same subtype with chained fixups and a
# signature written after the change runs.
#
# ld64.lld refuses -fixup_chains on an arm64e target ("only supported on x86_64
# and arm64 targets"), so the link is done as arm64 with -fixup_chains and the
# subtype is written into the header afterwards, before signing.
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
  -fixup_chains
  -install_name "${install_name}"
)

# The link is arm64 so that ld64.lld accepts -fixup_chains, and the subtype is
# written into the header afterwards. Offsets are from the start of the image,
# which is also the start of the slice: the package carries one slice.
mach_header_subtype_offset=8

if [ "$arch" != "arm64" ]; then
  echo "only the arm64 slice is built: ld64.lld accepts -fixup_chains on arm64" >&2
  echo "and x86_64 alone, and the subtype is written into the header afterwards" >&2
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

# The subtype is written before the signature, which package-deb.sh writes: the
# signature covers the header page, so setting this byte afterwards would leave
# a signature that does not match and the device acts on the mismatch rather
# than loading the image.
printf '\x02\x00\x00\x80' |
  dd of="$output_dir/WebSafeWebKit.dylib" bs=1 seek="$mach_header_subtype_offset" \
     conv=notrunc status=none

subtype_bytes="$(od -An -t x1 -j "$mach_header_subtype_offset" -N 4 "$output_dir/WebSafeWebKit.dylib" | tr -d ' \n')"
if [ "$subtype_bytes" != "02000080" ]; then
  echo "the dylib does not carry cpusubtype 0x80000002:" >&2
  echo "read 0x$subtype_bytes, expected 0x02000080" >&2
  exit 1
fi

# -fixup_chains is what makes the constructor reachable. Without it ld64.lld
# writes LC_DYLD_INFO_ONLY and puts the constructor in __mod_init_func, and the
# device loads such an image without running any of it. The two checks below
# read the linked slice, so a linker that quietly ignored the flag cannot
# produce a package.
headers="$(llvm-objdump --macho --private-headers "$output_dir/WebSafeWebKit.dylib")"
if ! printf '%s\n' "$headers" | grep -q 'LC_DYLD_CHAINED_FIXUPS'; then
  echo "the dylib carries no LC_DYLD_CHAINED_FIXUPS, so the device runs no" >&2
  echo "initializer of it; -fixup_chains was not honoured by $linker" >&2
  exit 1
fi
if printf '%s\n' "$headers" | grep -q 'LC_DYLD_INFO_ONLY'; then
  echo "the dylib carries LC_DYLD_INFO_ONLY beside the chained fixups:" >&2
  printf '%s\n' "$headers" | grep 'LC_DYLD_INFO_ONLY' >&2
  exit 1
fi
if ! llvm-objdump --macho -h "$output_dir/WebSafeWebKit.dylib" | grep -q '__init_offsets'; then
  echo "the constructor is not in __init_offsets, so the device runs none of it" >&2
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