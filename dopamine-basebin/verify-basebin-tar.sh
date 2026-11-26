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
# tar without it.  Shipping the file is not enough: update.m:32-39 uploads it as
# the basebin trustcache and nothing recomputes it from the members it just
# copied, so a stock tc paired with a replaced member describes binaries that
# are no longer in the tar and leaves the shipped ones uncovered.  Keep the
# stock tc in such a tar and this assertion is the one that catches it.  The
# file must match the shipped slices exactly in both directions: an entry no
# slice produces is a cdhash the kernel never computes, which a covers-only
# check would let through.
if [ -f "$work/basebin/basebin.tc" ]; then
	pass "tar ships basebin/basebin.tc"
	if [ -f "$HERE/verify-basebin-trustcache.py" ]; then
		if python3 "$HERE/verify-basebin-trustcache.py" "$work" > "$work/verify-trustcache.txt" 2>&1; then
			pass "basebin.tc holds exactly the cdhashes of the Mach-O members the tar ships"
			sed -n 's/^/    /p' "$work/verify-trustcache.txt"
		else
			fail "basebin.tc and the members this tar ships disagree; jbctl would upload the wrong trustcache"
			sed -n 's/^/    /p' "$work/verify-trustcache.txt"
		fi
	else
		fail "cannot find $HERE/verify-basebin-trustcache.py to check basebin.tc coverage"
	fi
else
	fail "tar is missing basebin/basebin.tc"
fi

if [ -f "$work/basebin/systemhook.dylib" ]; then
	pass "tar ships basebin/systemhook.dylib"
else
	fail "tar is missing basebin/systemhook.dylib"
fi

# launchdhook is the other binary built from systemhook/src/common/common.c: its
# Makefile lists ../systemhook/src/common/*.[cSm] as a dependency, and
# spawn_hook.c:197 installs the posix_spawn interposer that runs inside launchd,
# the process that performs an app's final exec.  A stock launchdhook writes only
# DYLD_INSERT_LIBRARIES, so an app launched through launchd inherits the __XPC_
# spellings from launchd's environ but never the plain name dyld reads, and its
# WebKit comes from the shared cache.  That is the state this device was in, so a
# tar that ships the stock binary has to fail here rather than on the device.
if [ -f "$work/basebin/launchdhook.dylib" ]; then
	pass "tar ships basebin/launchdhook.dylib"
	if [ -f "$HERE/verify-systemhook-search-paths.py" ]; then
		if python3 "$HERE/verify-systemhook-search-paths.py" --kind launchdhook \
			"$work/basebin/launchdhook.dylib" > "$work/verify-launchdhook.txt" 2>&1; then
			pass "launchdhook.dylib carries the four search-path names"
			sed -n 's/^/    /p' "$work/verify-launchdhook.txt"
		else
			fail "launchdhook.dylib does not set the search paths dyld reads; this tar would leave app processes on the shared-cache WebKit"
			sed -n 's/^/    /p' "$work/verify-launchdhook.txt"
		fi
	else
		fail "cannot find $HERE/verify-systemhook-search-paths.py to check launchdhook.dylib"
	fi
else
	fail "tar is missing basebin/launchdhook.dylib"
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
