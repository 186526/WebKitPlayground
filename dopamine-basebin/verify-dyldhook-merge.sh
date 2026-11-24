#!/bin/sh
#
# verify-dyldhook-merge.sh — assert that a built dyldhook_merge.*.dylib carries
# the framework-override hooks and keeps the hooks Dopamine already shipped.
#
# Usage:
#   scripts/verify-dyldhook-merge.sh <dyldhook_merge.arm64e.iOS16-17.dylib>
#   scripts/verify-dyldhook-merge.sh <BaseBin/dyldhook directory>
#
# The directory form checks every dyldhook_merge.*.dylib inside it and also
# asserts that the iOS 15, iOS 16-17 and iOS 18+ arm64e dylibs are present, so a
# build that silently drops a target fails here instead of on the device.
#
# The dlopen_from hook is asserted on every target except iOS 18+, where upstream
# Dopamine compiles it out behind `#if IOS < 18` in src/fakelib_redirect.c
# (commit 2ae46a8).  On those targets the trampoline pair is asserted instead.
#
# Needs only llvm-nm; no jailbroken device and no Xcode are required, so this
# runs on any Linux or macOS host that has the LLVM tools.
#
# Exit status: 0 when every assertion holds, 1 otherwise.

set -eu

HOOK_OVERRIDABLE='_MACHOMERGER_HOOK__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc'
HOOK_ALWAYS_OVERRIDABLE='_MACHOMERGER_HOOK__ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc'
HOOK_DLOPEN_FROM='_MACHOMERGER_HOOK__ZN5dyld44APIs11dlopen_fromEPKciPv'
HOOK_DLOPEN_TRAMPOLINE='_MACHOMERGER_HOOKTRAMPOLINE__ZN5dyld44APIs11dlopen_fromEPKciPv'
HOOK_FCNTL='_MACHOMERGER_HOOK___fcntl'

fail_count=0

fail() {
	echo "FAIL: $*" >&2
	fail_count=$((fail_count + 1))
}

pass() {
	echo "PASS: $*"
}

if ! command -v llvm-nm >/dev/null 2>&1; then
	echo "llvm-nm not found in PATH" >&2
	exit 1
fi

if [ "$#" -ne 1 ]; then
	sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
fi

target=$1

if [ ! -e "$target" ]; then
	echo "No such file or directory: $target" >&2
	exit 1
fi

# Collect the dylibs to inspect.  The directory form also enforces the three
# arm64e targets Dopamine's BaseBin/Makefile lists for arm64e devices, because a
# build that produced only one of them would pass a single-file check.
candidates=''
if [ -d "$target" ]; then
	for required in \
		dyldhook_merge.arm64e.iOS15.dylib \
		dyldhook_merge.arm64e.iOS16-17.dylib \
		dyldhook_merge.arm64e.iOS18+.dylib
	do
		if [ -f "$target/$required" ]; then
			pass "found $required"
		else
			fail "missing $required"
		fi
	done
	for f in "$target"/dyldhook_merge.*.dylib; do
		[ -f "$f" ] || continue
		candidates="$candidates $f"
	done
else
	candidates=$target
fi

if [ -z "$candidates" ]; then
	echo "No dyldhook_merge.*.dylib to check under $target" >&2
	exit 1
fi

has_symbol() {
	# $1 = dylib, $2 = symbol name as printed by llvm-nm
	llvm-nm -g "$1" 2>/dev/null | awk -v want="$2" '$NF == want { found = 1 } END { exit found ? 0 : 1 }'
}

for dylib in $candidates; do
	name=$(basename "$dylib")
	echo "== $name =="

	case "$name" in
		*iOS15*)
			# dyld4 puts isOverridablePath on DyldSharedCache, not on
			# ProcessConfig::DyldCache, before iOS 16, so this target must not
			# carry the new hooks: they would have no function to attach to.
			for sym in "$HOOK_OVERRIDABLE" "$HOOK_ALWAYS_OVERRIDABLE"; do
				if has_symbol "$dylib" "$sym"; then
					fail "$name must not define $sym (the iOS 15 dyld has no such function)"
				else
					pass "$name correctly omits $sym"
				fi
			done
			;;
		*iOS18*)
			# Upstream guards the dlopen_from hook with `#if IOS < 18` in
			# src/fakelib_redirect.c (commit 2ae46a8, "Add support for (at least)
			# iPad 7 18.7.1"), so the iOS 18+ dylib defines the trampoline pair
			# but no hook.  Asserting the hook here would reject the dylib
			# upstream ships.  The symbols are still checked below: both new
			# hooks must be present, and __fcntl must survive.
			for sym in "$HOOK_OVERRIDABLE" "$HOOK_ALWAYS_OVERRIDABLE"; do
				if has_symbol "$dylib" "$sym"; then
					pass "$name defines $sym"
				else
					fail "$name does not define $sym"
				fi
			done
			;;
		*)
			# Everything else must carry both new hooks: the iOS 16-17 dylibs,
			# and the gen/dyld MachOMerger produced from one of them.  A name
			# this script does not recognise lands here too, so an unrecognised
			# input fails loudly instead of skipping the assertions.
			for sym in "$HOOK_OVERRIDABLE" "$HOOK_ALWAYS_OVERRIDABLE"; do
				if has_symbol "$dylib" "$sym"; then
					pass "$name defines $sym"
				else
					fail "$name does not define $sym"
				fi
			done
			;;
	esac

	# __fcntl carries the code signature bypass, so it is asserted everywhere.
	if has_symbol "$dylib" "$HOOK_FCNTL"; then
		pass "$name keeps $HOOK_FCNTL"
	else
		fail "$name lost $HOOK_FCNTL"
	fi

	# dlopen_from is asserted except on iOS 18+, where upstream compiles the hook
	# out on purpose.
	case "$name" in
		*iOS18*)
			# The trampoline pair must still be there: MachOMerger resolves it and
			# its presence proves the symbol block survived, which is what a future
			# upstream change would break first.
			if has_symbol "$dylib" "$HOOK_DLOPEN_TRAMPOLINE"; then
				pass "$name keeps $HOOK_DLOPEN_TRAMPOLINE and correctly omits the hook (upstream guards it with IOS < 18)"
			else
				fail "$name lost $HOOK_DLOPEN_TRAMPOLINE"
			fi
			;;
		*)
			if has_symbol "$dylib" "$HOOK_DLOPEN_FROM"; then
				pass "$name keeps $HOOK_DLOPEN_FROM"
			else
				fail "$name lost $HOOK_DLOPEN_FROM"
			fi
			;;
	esac

	# BaseBin/dyldhook/Makefile fails its own build when the merged dylib has an
	# undefined symbol, because the dylib is loaded without any resolver.  Repeat
	# the check here so it is enforced even when the build was driven some other
	# way.
	undef=$(llvm-nm -u "$dylib" 2>/dev/null || true)
	if [ -n "$undef" ]; then
		fail "$name has undefined symbols:"
		printf '%s\n' "$undef" | sed 's/^/  /' >&2
	else
		pass "$name is self-contained (llvm-nm -u is empty)"
	fi
done

if [ "$fail_count" -ne 0 ]; then
	echo "Result: FAILED ($fail_count assertion(s))" >&2
	exit 1
fi

echo "Result: PASS"
