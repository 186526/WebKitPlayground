#!/bin/sh
#
# verify-basebin-tar.sh — assert a built basebin.tar ships the patched dyldhook
# dylibs under the names the device asks for, and keeps the hooks Dopamine
# already relied on.
#
# Usage:
#   dopamine-basebin/verify-basebin-tar.sh <BaseBin/basebin.tar>
#
# What the tar holds: BaseBin/Makefile's basebin.tar target archives .build
# after the subprojects and the dyldhook dylibs are copied in.  It does not
# hold gen/dyld.  That file is produced on the device: jbctl update basebin
# runs libjailbreak/src/basebin_gen.m, which merges /usr/lib/dyld with the one
# dyldhook_merge.<arch>.<profile>.dylib that matches the running iOS version.
# So the merged dyld's symbols depend on the tar in two ways only: the dylib has
# to be present under the exact name dyldhook_dylib_for_platform() returns, and
# the dylib has to define the hooks.  This script checks both, then runs
# verify-dyldhook-merge.sh on the unpacked dylibs so the hook assertions read
# the bytes that ship rather than the bytes in the build tree.
#
# Exit status: 0 when every assertion holds, 1 otherwise.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERIFY_HOOKS="${HERE}/verify-dyldhook-merge.sh"

fail_count=0

fail() {
	echo "FAIL: $*" >&2
	fail_count=$((fail_count + 1))
}

pass() {
	echo "PASS: $*"
}

if [ "$#" -ne 1 ]; then
	sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
fi

tar_file=$1
if [ ! -f "$tar_file" ]; then
	echo "No such file: $tar_file" >&2
	exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/verify-basebin-tar.XXXXXX")
trap 'rm -rf "$work"' EXIT

# bsdtar on macOS and GNU tar on Linux both read a GNU-format archive here.
tar -xf "$tar_file" -C "$work"

if [ ! -d "$work/basebin" ]; then
	fail "$tar_file has no basebin/ prefix; the tar --transform step did not run"
	echo "Result: FAILED ($fail_count assertion(s))" >&2
	exit 1
fi

# dyldhook_dylib_for_platform() in libjailbreak/src/basebin_gen.m returns one of
# these six names based on hw.cpusubtype and the running iOS version.  A missing
# name makes jbctl update basebin abort with "Failed to locate dyldhook.dylib",
# so every name has to be in the tar even when this device never uses it.
for name in \
	dyldhook_merge.arm64e.iOS15.dylib \
	dyldhook_merge.arm64.iOS15.dylib \
	dyldhook_merge.arm64e.iOS16-17.dylib \
	dyldhook_merge.arm64.iOS16-17.dylib \
	dyldhook_merge.arm64e.iOS18+.dylib \
	dyldhook_merge.arm64.iOS18+.dylib
do
	if [ -f "$work/basebin/$name" ]; then
		pass "tar ships basebin/$name"
	else
		fail "tar is missing basebin/$name"
	fi
done

# basebin.tc is what makes the dylibs loadable on the device; jbctl refuses a
# tar without it.
if [ -f "$work/basebin/basebin.tc" ]; then
	pass "tar ships basebin/basebin.tc"
else
	fail "tar is missing basebin/basebin.tc"
fi

if [ -f "$work/basebin/systemhook.dylib" ]; then
	pass "tar ships basebin/systemhook.dylib"
else
	fail "tar is missing basebin/systemhook.dylib"
fi

# The hooks themselves, read from the shipped bytes.  verify-dyldhook-merge.sh
# asserts both new hooks on the iOS 16-17 and iOS 18+ dylibs, their absence on
# the iOS 15 dylib, both legacy hooks on all six, and an empty llvm-nm -u.  All
# six names are copied, not just the arm64e three: devices older than A12 run
# arm64, so an arm64 variant that lost a hook would ship uncaught otherwise.
hook_dir="$work/hookcheck"
mkdir -p "$hook_dir"
for name in \
	dyldhook_merge.arm64e.iOS15.dylib \
	dyldhook_merge.arm64.iOS15.dylib \
	dyldhook_merge.arm64e.iOS16-17.dylib \
	dyldhook_merge.arm64.iOS16-17.dylib \
	dyldhook_merge.arm64e.iOS18+.dylib \
	dyldhook_merge.arm64.iOS18+.dylib
do
	[ -f "$work/basebin/$name" ] && cp "$work/basebin/$name" "$hook_dir/"
done
echo "hookcheck inputs: $(ls "$hook_dir" | wc -l | tr -d ' ') of 6 dylibs"

if [ -f "$VERIFY_HOOKS" ]; then
	if sh "$VERIFY_HOOKS" "$hook_dir"; then
		pass "shipped dyldhook dylibs satisfy verify-dyldhook-merge.sh"
	else
		fail "shipped dyldhook dylibs violate verify-dyldhook-merge.sh (see the FAIL lines above)"
	fi
else
	fail "cannot find $VERIFY_HOOKS to check the shipped hooks"
fi

# gen/dyld is device-generated, so its absence is correct and worth stating out
# loud: a tar that carried one would hand every device the same merged dyld,
# built from whichever /usr/lib/dyld the CI host happened to have.
if [ -e "$work/basebin/gen/dyld" ]; then
	fail "tar ships basebin/gen/dyld; that file belongs to jbctl update basebin on the device"
else
	pass "tar carries no basebin/gen/dyld (jbctl update basebin generates it from /usr/lib/dyld)"
fi

if [ "$fail_count" -ne 0 ]; then
	echo "Result: FAILED ($fail_count assertion(s))" >&2
	exit 1
fi

echo "Result: PASS"
