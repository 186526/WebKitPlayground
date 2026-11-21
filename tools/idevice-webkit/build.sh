#!/usr/bin/env bash
# Builds the idevice-webkit dylib for one iOS slice.
#
#   ARCH=arm64  ./build.sh     one slice for the arm64 hosts (the Linux path)
#   ARCH=arm64e ./build.sh     one slice for the arm64e hosts (needs Apple ld64
#                              and an iOS SDK; on a Linux host, run it there)
#   lipo -create build/arm64/WebSafeWebKit.dylib \
#                build/arm64e/WebSafeWebKit.dylib -output build/WebSafeWebKit.dylib
#
# The output name here is a local build name. package-deb.sh lipos the two
# slices under the installed name, WebSafeWebKitFilter.dylib.
#
# The installable artifact is the lipo output. A slice on its own only loads in
# a process of its own architecture, and Safari is an arm64e process: ellekit
# injects a dylib only when it holds an arm64e slice, so an arm64-only package
# is never loaded there.
#
# Why arm64e cannot be linked on a Linux host, measured on this tree's Linux
# toolchain (LLD 21.1.8):
#
#   ld64.lld -dylib -arch arm64e ... -fixup_chains
#     error: -fixup_chains is only supported on x86_64 and arm64 targets
#
# Without -fixup_chains, ld64.lld writes LC_DYLD_INFO_ONLY and puts the
# constructor in __mod_init_func, and stamps cpusubtype 0x0. The device loads
# such an image and runs no initializer of it, so it does nothing. The only way
# to an arm64e slice with LC_DYLD_CHAINED_FIXUPS is a linker that writes chained
# fixups for an arm64e target, which is Apple's ld64 (ld-classic). On the device
# it is /var/jb/usr/bin/ld64 with /var/jb/usr/share/SDKs/iPhoneOS.sdk; on macOS
# it is the system ld with Xcode's iPhoneOS SDK. This script therefore refuses
# to produce an arm64e slice with ld64.lld instead of quietly writing one the
# device ignores.
#
# cpusubtype is part of the check rather than a byte that gets rewritten:
#
#   arm64   the value ld64.lld writes, 0x00000000. It has to stay there: 1.0.11
#           rewrote it to 0x80000002 on an arm64-compiled image, dyld then signed
#           the pointers it fixed up, and the first message crashed on the plain
#           `br` the arm64 code uses.
#   arm64e  the value Apple ld64 writes for a real arm64e target, 0x80000002,
#           together with LC_DYLD_CHAINED_FIXUPS and __init_offsets. Rewriting
#           0x0 to 0x80000002 with dd is what 1.0.11 did and it is not a
#           substitute: dyld then signs pointers in code compiled for a plain
#           arm64 ABI. The arm64e branch compiles for arm64e, so the subtype the
#           linker wrote is the one that is checked, and nothing patches it.
#
# CLANG may hold a command with spaces, such as "xcrun -sdk iphoneos clang".
# LD64 is Apple ld64; it is only consulted for ARCH=arm64e.
# SDKROOT is the iPhoneOS SDK holding usr/lib/libSystem.tbd (for -lSystem).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
read -r -a clang_command <<<"${CLANG:-clang}"
arch="${ARCH:-arm64}"
output_dir="${OUTPUT_DIR:-$here/build/$arch}"
install_name="${INSTALL_NAME:-/var/jb/usr/lib/TweakInject/WebSafeWebKitFilter.dylib}"
minimum_ios="${MINIMUM_IOS:-16.0}"

arm64e_subtype="02000080"
arm64_subtype="00000000"
mach_header_subtype_offset=8

if [ "$arch" != "arm64" ] && [ "$arch" != "arm64e" ]; then
  echo "unknown ARCH: $arch (expected arm64 or arm64e)" >&2
  exit 1
fi

compile_flags=(
  -target "${arch}-apple-ios${minimum_ios}"
  -arch "${arch}"
  "-miphoneos-version-min=${minimum_ios}"
  -O2
  -fPIC
  -fno-stack-protector

  -Wall -Wextra -Werror
)

link_flags=(
  -dylib
  -arch "${arch}"
  -platform_version ios "${minimum_ios}" "${minimum_ios}"
  -undefined dynamic_lookup
  -install_name "${install_name}"
)

if [ "$arch" = "arm64" ]; then
  # The Linux path: no SDK is compiled in, so no headers and no libSystem.
  compile_flags+=(-nostdinc)
  linker="${LD64:-ld64.lld}"
  link_flags+=(-fixup_chains)
  subtype_bytes="$arm64_subtype"
else
  # The arm64e path needs Apple ld64 and an SDK. ld64.lld is refused here,
  # because the slice it writes is one the device loads without running.
  linker="${LD64:-}"
  if [ -z "$linker" ]; then
    if command -v ld64 >/dev/null && [ "$(basename "$(command -v ld64)")" = "ld64" ]; then
      linker="$(command -v ld64)"
    else
      echo "ARCH=arm64e needs Apple ld64, which ld64.lld is not: the chained" >&2
      echo "fixups ld64.lld refuses for an arm64e target are the reason the" >&2
      echo "constructor runs at all. Set LD64 to Apple's ld64 (on the device:" >&2
      echo "/var/jb/usr/bin/ld64)." >&2
      exit 1
    fi
  fi
  if [ "$(basename "$linker")" = "ld64.lld" ]; then
    echo "ARCH=arm64e refuses ld64.lld: it rejects -fixup_chains for an arm64e" >&2
    echo "target and writes LC_DYLD_INFO_ONLY, which the device loads without" >&2
    echo "running the constructor. Use Apple ld64 and an iOS SDK." >&2
    exit 1
  fi
  sdk="${SDKROOT:-}"
  if [ -z "$sdk" ]; then
    for candidate in /var/jb/usr/share/SDKs/iPhoneOS.sdk /usr/share/SDKs/iPhoneOS.sdk; do
      if [ -d "$candidate/usr/lib" ]; then
        sdk="$candidate"
        break
      fi
    done
  fi
  if [ -z "$sdk" ] || [ ! -d "$sdk/usr/lib" ]; then
    echo "ARCH=arm64e needs an iPhoneOS SDK for -lSystem; set SDKROOT to one" >&2
    echo "with usr/lib/libSystem.tbd" >&2
    exit 1
  fi

  # -fixup_chains is not optional here. Without it ld64 writes LC_DYLD_INFO_ONLY
  # and __mod_init_func for this image even though it is an arm64e target, and
  # the device loads such an image and runs no initializer of it. The measured
  # pair on the device's own ld64 is:
  #
  #   ld64 ... obj/*.o                       -> LC_DYLD_INFO_ONLY, no __init_offsets
  #   ld64 ... -fixup_chains obj/*.o         -> LC_DYLD_CHAINED_FIXUPS,
  #                                             __init_offsets, cpusubtype 0x80000002
  #
  # so the flag is passed and the checks below hold the result to it.
  compile_flags+=(-nostdinc -isystem "$sdk/usr/include")
  link_flags+=(-fixup_chains -L"$sdk/usr/lib" -lSystem -lobjc)
  subtype_bytes="$arm64e_subtype"
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

# The subtype the linker wrote has to be the one this architecture needs. An
# arm64e image carrying 0x0 is one dyld treats as arm64 and signs no pointer of;
# an arm64 image carrying 0x80000002 is the 1.0.11 crash. Neither is patched
# here, and neither passes.
linked_subtype="$(od -An -t x1 -j "$mach_header_subtype_offset" -N 4 "$output_dir/WebSafeWebKit.dylib" | tr -d ' \n')"
if [ "$linked_subtype" != "$subtype_bytes" ]; then
  echo "the $arch slice carries cpusubtype 0x$linked_subtype, expected 0x$subtype_bytes" >&2
  echo "the subtype is not rewritten after the link; see the note at the top" >&2
  exit 1
fi

# -fixup_chains is what makes the constructor reachable, and the reader below
# holds the linked slice to it. It reads the Mach-O structures itself, because
# the device that links the arm64e slice has neither llvm-objdump nor llvm-nm:
# /var/jb/usr/bin carries llvm-otool-16 and llvm-nm-16, and a script that
# called llvm-objdump stopped there with "command not found". The four checks
# are LC_DYLD_CHAINED_FIXUPS, no LC_DYLD_INFO_ONLY, a non-empty __init_offsets,
# and no dyld symbol name with one or three leading underscores.
python="${PYTHON:-python3}"
command -v "$python" >/dev/null || { echo "missing python for the slice checks: $python" >&2; exit 1; }
"$python" "$here/check-slice.py" "$output_dir/WebSafeWebKit.dylib" "$subtype_bytes"

# The report writer has to pass its mode the variadic way. Darwin's open reads
# the mode from the stack; a fixed three-parameter declaration makes the
# compiler put it in w2 instead, and the device then creates the report with a
# mode nobody asked for, 0504 among them. A write_file that assigns w2 before
# its call to open is that declaration come back. The check reads the
# disassembly, so it only runs where a disassembler exists; the arm64e slice is
# linked on the device, which carries none, and tests/run.sh reads the arm64
# slice's disassembly on the build host instead.
if command -v llvm-objdump >/dev/null && llvm-objdump --help 2>/dev/null | grep -q -- '--macho'; then
  write_file_body="$(llvm-objdump -d --no-show-raw-insn "$output_dir/WebSafeWebKit.dylib" 2>/dev/null |
    sed -n '/<_write_file>:/,/^$/p')"
  if printf '%s\n' "$write_file_body" | grep -q 'mov[[:space:]]*w2,'; then
    echo "the report writer passes the file mode in a register, which the device drops:" >&2
    printf '%s\n' "$write_file_body" | grep 'mov[[:space:]]*w2,' >&2
    exit 1
  fi
fi

echo "arch: $arch"
echo "built: $output_dir/WebSafeWebKit.dylib"