#!/usr/bin/env bash
# Compiles the same sources the dylib is built from together with the test
# fixtures that replace the dyld calls of the process, the Info.plist lookup and
# the Darwin file writer, so the host decisions can be checked without a device.
# The plain build checks the expectations; the sanitizer build repeats the run
# under ASan and UBSan, and its log is searched because ASan normally exits 1
# on its own while UBSan only prints.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
python="${PYTHON:-python3}"
work="${TMPDIR:-/tmp}/idevice-webkit-test"
mkdir -p "$work"
rm -f "$work"/com.websafe.idevice.plist "$work"/com.websafe.idevice.ready \
  "$work"/com.websafe.idevice.others.plist "$work"/asan.log

# The signature is checked before the C suite: a dylib dyld will not load makes
# every host decision below moot, so a broken signer stops the run first.
"$python" "$here/signature_test.py"

# The package carries one arm64 slice, and two properties of it decide whether
# the device runs the constructor at all.
#
# The device exports the dyld image table calls, and which spelling binds was
# measured rather than assumed: a probe dylib referring to __dyld_image_count
# ran and read the table (count 97, first image systemhook.dylib), while one
# referring to _dyld_image_count died with "symbol not found in flat namespace
# '_dyld_image_count'" and one referring to ___dyld_image_count died the same
# way on ___dyld_get_image_name. So the symbol table name has exactly two
# leading underscores. A C declaration of `_dyld_image_count` produces it; one
# leading underscore too few or too many is the failure, and both are refused
# here.
#
# The header's cpusubtype has to stay 0x00000000, the value ld64.lld writes.
# An image carrying the ARM64E subtype 0x80000002 is loaded on the device and
# runs no initializer; a working image with those four bytes set is killed with
# signal 9.
slice="$here/../build/arm64/WebSafeWebKit.dylib"
[ -f "$slice" ] || { echo "missing slice: $slice" >&2; exit 1; }

subtype_bytes="$(od -An -t x1 -j 8 -N 4 "$slice" | tr -d ' \n')"
if [ "$subtype_bytes" != "00000000" ]; then
  subtype_le="$(printf '%s' "$subtype_bytes" | sed 's/\(..\)\(..\)\(..\)\(..\)/\4\3\2\1/')"
  echo "the arm64 slice carries a CPU subtype the device runs no initializer of:" >&2
  echo "cpusubtype 0x$subtype_le, expected 0x00000000" >&2
  exit 1
fi
echo "the arm64 slice carries cpusubtype 0, the one the device runs pass"

if llvm-nm -u "$slice" | grep -q -e '^_dyld_' -e '^___dyld_'; then
  echo "the arm64 slice refers to a dyld symbol name the device does not export:" >&2
  llvm-nm -u "$slice" | grep -e '^_dyld_' -e '^___dyld_' >&2
  echo "the symbol table name has two leading underscores: __dyld_image_count" >&2
  exit 1
fi
echo "the arm64 slice refers to no dyld symbol name the device does not export pass"

# The mode of a new report file has to travel the variadic way. With the fixed
# three-parameter declaration the compiler writes it into w2, and the device's
# open never reads w2: the file came out 0504 and its owner could not write it
# again, which stopped the report at stage 1. A `write_file` that assigns w2
# before its call is that declaration come back, and the check is on the linked
# slice so it also catches a stale build.
write_file_body="$(llvm-objdump -d --no-show-raw-insn "$slice" 2>/dev/null |
  sed -n '/<_write_file>:/,/^$/p')"
if printf '%s\n' "$write_file_body" | grep -q 'mov[[:space:]]*w2,'; then
  echo "the report writer passes the file mode in a register, which the device drops:" >&2
  printf '%s\n' "$write_file_body" | grep 'mov[[:space:]]*w2,' >&2
  exit 1
fi
echo "the report writer passes the file mode the variadic way pass"

# The two handles this file hands to dlsym decide whether a lookup reaches the
# process's own symbols or dies on an invalid handle, so they get their own
# binary: test_main.c stubs the lookups whole and could not see either value.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" \
  "$here/dyld_lookup_test.c" -o "$work/dyld_lookup_test"
"$work/dyld_lookup_test"

# The report writer is replaced for the host cases, so the one function that
# reaches the device is checked here. The device ignores the mode an open asks
# for, and the report came back as 0504 because of it, so the writer sets the
# mode on the descriptor as well; the compiler also refuses a fixed-arity
# declaration of the variadic open.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" \
  "$here/file_writer_test.c" -o "$work/file_writer_test"
"$work/file_writer_test"

# The image reader is what turns the handle dladdr reports into a version
# string. The host cases replace it, so the scan itself is checked here against
# a synthetic image: which load command a segment is, how wide a section record
# is, and that a section's address is a virtual address rather than a pointer.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" \
  "$here/macho_image_test.c" "$here/../src/macho_image.c" "$here/../src/textkit.c" \
  -o "$work/macho_image_test"
"$work/macho_image_test"

clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" -I"$here/fixtures" \
  "$here/test_main.c" -o "$work/test_main"
clang -std=c11 -g -fsanitize=address,undefined -I"$here/../src" -I"$here" -I"$here/fixtures" \
  "$here/test_main.c" -o "$work/test_asan"

"$work/test_main"

status=0
"$work/test_asan" >>"$work/asan.log" 2>&1 || status=$?
if [ "$status" -ne 0 ] ||
   grep -q -e "ERROR: AddressSanitizer" -e "runtime error" "$work/asan.log"; then
  echo "sanitizer run failed, log in $work/asan.log"
  cat "$work/asan.log"
  exit 1
fi
echo "sanitizer build: no report, exit 0"
