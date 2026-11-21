#!/usr/bin/env bash
# Compiles the same sources the dylib is built from together with the test
# fixtures that replace the dyld image table, the Info.plist lookup and the
# Darwin file writer, so the host decisions can be checked without a device.
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
# The dyld of the device exports no __dyld_* function, and one reference to one
# of them keeps the image from binding: dyld loads it, runs no initializer, and
# says nothing unless DYLD_PRINT_ERRORS is set. The build refuses such a
# reference, and this run refuses it on the slice as well, so a product built
# elsewhere is caught here too.
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

if llvm-nm -u "$slice" | grep -q '^__dyld_'; then
  echo "the arm64 slice refers to a dyld symbol the device does not export:" >&2
  llvm-nm -u "$slice" | grep '^__dyld_' >&2
  exit 1
fi
echo "the arm64 slice refers to no dyld symbol the device does not export pass"

# The two handles this file hands to dlsym decide whether a lookup reaches the
# process's own symbols or dies on an invalid handle, so they get their own
# binary: test_main.c stubs the lookups whole and could not see either value.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" \
  "$here/dyld_lookup_test.c" -o "$work/dyld_lookup_test"
"$work/dyld_lookup_test"

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
