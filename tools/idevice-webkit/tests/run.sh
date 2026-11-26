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

# The rewrite the seven images need is checked against a synthetic payload, so
# it runs before the slice checks and without the package's 50 MB install. Its
# first two checks read the seven paths out of src/load_chain.c, which is what
# keeps the tree the tweak loads and the tree the rewrite writes one list.
"$python" "$here/rewrite_payload_test.py"

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
# Two properties of the slice decide whether the device runs the constructor,
# and it needs both. They were measured on the device with /var/jb/usr/bin/true
# and Safari:
#
#   fixups                 constructor
#   LC_DYLD_INFO_ONLY      does not run
#   LC_DYLD_CHAINED_FIXUPS runs, and an abort() in it takes Safari down with
#                          the crash report naming abort()
#
# So the link has to produce chained fixups with the constructor in
# __init_offsets, and the signature has to be written after that, because it
# covers the header page.
#
# A third value used to be required here, cpusubtype 0x80000002, and 1.0.11
# crashed on the device because of it: dyld then fixes up every pointer as
# arm64e, which signs the function addresses dlsym answers, and this arm64 code
# branches to them with a plain br. The control experiment changed that header
# word alone, back to 0x0, and the crash stopped while the constructor still
# ran. The check below therefore holds the value down at 0x0.
#
# The arm64e slice is the other way round: 0x80000002 is right there because it
# is a real arm64e link, not a rewritten arm64 one, so its pointers are arm64e
# pointers and dyld may sign them. build.sh writes it on the device, with Apple
# ld64 and -fixup_chains; ld64.lld refuses that flag for an arm64e target and
# writes LC_DYLD_INFO_ONLY instead. When build/arm64e/WebSafeWebKit.dylib is
# present it is held to the same four properties, through check-slice.py, the
# reader build.sh uses: the device carries no llvm-objdump to run the checks
# below. The two slices are what package-deb.sh lipos into one dylib, and
# Safari, an arm64e process, loads only the arm64e one.
slice="$here/../build/arm64/WebSafeWebKit.dylib"
[ -f "$slice" ] || { echo "missing slice: $slice" >&2; exit 1; }

subtype_bytes="$(od -An -t x1 -j 8 -N 4 "$slice" | tr -d ' \n')"
if [ "$subtype_bytes" != "00000000" ]; then
  echo "the arm64 slice does not carry cpusubtype 0x0:" >&2
  echo "read 0x${subtype_bytes}, the header has 00 00 00 00 when the device runs it" >&2
  exit 1
fi
echo "the arm64 slice keeps cpusubtype 0x0, so no pointer is signed pass"

slice_headers="$(llvm-objdump --macho --private-headers "$slice")"
if ! printf '%s\n' "$slice_headers" | grep -q 'LC_DYLD_CHAINED_FIXUPS'; then
  echo "the arm64 slice carries no LC_DYLD_CHAINED_FIXUPS:" >&2
  echo "the device loads such an image and runs no initializer of it" >&2
  exit 1
fi
if printf '%s\n' "$slice_headers" | grep -q 'LC_DYLD_INFO_ONLY'; then
  echo "the arm64 slice carries LC_DYLD_INFO_ONLY beside the chained fixups:" >&2
  exit 1
fi
echo "the arm64 slice carries LC_DYLD_CHAINED_FIXUPS and no LC_DYLD_INFO_ONLY pass"

if ! llvm-objdump --macho -h "$slice" | grep -q '__init_offsets'; then
  echo "the constructor is not in __init_offsets:" >&2
  echo "with the constructor in __mod_init_func the device runs none of it" >&2
  exit 1
fi
echo "the constructor is registered in __init_offsets pass"

if llvm-nm -u "$slice" | grep -q -e '^_dyld_' -e '^___dyld_'; then
  echo "the arm64 slice refers to a dyld symbol name the device does not export:" >&2
  llvm-nm -u "$slice" | grep -e '^_dyld_' -e '^___dyld_' >&2
  echo "the symbol table name has two leading underscores: __dyld_image_count" >&2
  exit 1
fi
echo "the arm64 slice refers to no dyld symbol name the device does not export pass"

# The arm64e slice, when it has been built, is held to the same four properties
# through the reader that runs where no objdump does. It is optional here
# because it takes the device to build one; package-deb.sh refuses to package
# without it, which is where its absence is caught.
arm64e_slice="$here/../build/arm64e/WebSafeWebKit.dylib"
if [ -f "$arm64e_slice" ]; then
  "${PYTHON:-python3}" "$here/../check-slice.py" "$arm64e_slice" 02000080
  echo "the arm64e slice carries cpusubtype 0x80000002, chained fixups and __init_offsets pass"
else
  echo "no arm64e slice at $arm64e_slice; package-deb.sh refuses to build without it"
fi

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

# The runtime lookup is the one that failed on the device: every Foundation
# accessor used to be resolved by dlsym with its method name, and a method name
# is not a C symbol, so each answered NULL and no process was ever identified
# as a host. This binary records dlsym the way the device answers it and holds
# the method-name lookup and the typed msgSend wrappers apart from each other.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" \
  "$here/objc_runtime_test.c" "$here/../src/textkit.c" -o "$work/objc_runtime_test"
"$work/objc_runtime_test"

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
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" \
  "$here/macho_image_test.c" "$here/../src/macho_image.c" "$here/../src/textkit.c" \
  -o "$work/macho_image_test"
"$work/macho_image_test"

# The seven-image chain is what the tweak does on the device, so it is held to
# the order, the flags, the absolute paths and the stop-on-JavaScriptCore rule
# on the host, with dlopen recorded instead of called.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" \
  "$here/load_chain_test.c" -o "$work/load_chain_test"
"$work/load_chain_test"

# The report's chain section is the only place a device failure is readable
# without a debugger, and nothing else in this suite calls
# dylib_report_set_chain. It is checked here against hand-built load_result
# values: a failure at step 1 has to record both its loaded=false and its error
# text, and a step the chain never reached has to record neither.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here/fixtures" \
  "$here/report_chain_test.c" -o "$work/report_chain_test"
"$work/report_chain_test"

clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" -I"$here" -I"$here/fixtures" \
  "$here/test_main.c" -o "$work/test_main"
# -fno-sanitize=function: objc_msgSend is one address called through a pointer
# of each method's own type, which the function sanitizer reports as a type
# mismatch by design. Every other sanitizer check stays on, and the address,
# bounds and alignment checks that matter for the message arguments are those.
clang -std=c11 -g -fsanitize=address,undefined -fno-sanitize=function \
  -I"$here/../src" -I"$here" -I"$here/fixtures" "$here/test_main.c" -o "$work/test_asan"

"$work/test_main"

# The image claim is checked against recorders rather than a device: the seven
# paths and their order, the stop-at-first-failure rule, and the jbctl spawn.
# test_main.c stubs claim_images_run, so the sequence itself has no other test.
clang -std=c11 -Wall -Wextra -Werror -I"$here/../src" \
  "$here/claim_images_test.c" "$here/../src/textkit.c" -o "$work/claim_images_test"
"$work/claim_images_test"

status=0
"$work/test_asan" >>"$work/asan.log" 2>&1 || status=$?
if [ "$status" -ne 0 ] ||
   grep -q -e "ERROR: AddressSanitizer" -e "runtime error" "$work/asan.log"; then
  echo "sanitizer run failed, log in $work/asan.log"
  cat "$work/asan.log"
  exit 1
fi
echo "sanitizer build: no report, exit 0"
