#!/bin/sh
#
# verify-merged-dyld.sh — run Dopamine's MachOMerger the way jbctl update
# basebin runs it on the device, then assert the merged dyld carries the four
# hooks this repository's patches add or rely on.
#
# Usage:
#   dopamine-basebin/verify-merged-dyld.sh <MachOMerger> <dyld> <dyldhook.dylib>
#
# The device path this reproduces: libjailbreak/src/basebin_gen.m calls
#   MachOMerger <gen/dyld.inflight> <dyldhook_merge.arm64e.iOS16-17.dylib> <gen/dyld.inflight>
# where the first argument is a copy of the device's /usr/lib/dyld.  The merged
# output is what boots, so the hook symbols have to survive the merge: MachOMerger
# rewrites every _MACHOMERGER_HOOK_* symbol in the dylib into a branch at the
# matching dyld function, and renames the pair into the output's symbol table.
#
# The dyld handed to this script stands in for the device's, because CI has no
# device.  It has to export the two functions the new hooks attach to; when it
# does not, MachOMerger exits with "Cannot resolve symbol" and this script
# reports that verbatim instead of pretending the hooks were verified.
#
# Needs llvm-nm in PATH.
#
# Exit status:
#   0  every assertion holds
#   1  an assertion failed, so the merged dyld is wrong
#   3  MachOMerger could not run on this input, so nothing was merged and no
#      assertion about a merged dyld was made.  A caller has to decide whether
#      that is a limitation of its stand-in input or a real problem, and must
#      say which in its log.

set -eu

HOOK_OVERRIDABLE='_MACHOMERGER_HOOK__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc'
HOOK_ALWAYS_OVERRIDABLE='_MACHOMERGER_HOOK__ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc'
HOOK_DLOPEN_FROM='_MACHOMERGER_HOOK__ZN5dyld44APIs11dlopen_fromEPKciPv'
HOOK_FCNTL='_MACHOMERGER_HOOK___fcntl'

fail_count=0

fail() {
	echo "FAIL: $*" >&2
	fail_count=$((fail_count + 1))
}

pass() {
	echo "PASS: $*"
}

if [ "$#" -ne 3 ]; then
	sed -n '3,24p' "$0" | sed 's/^# \{0,1\}//'
	exit 1
fi

merger=$1
input_dyld=$2
dyldhook=$3

for f in "$merger" "$input_dyld" "$dyldhook"; do
	if [ ! -e "$f" ]; then
		echo "No such file: $f" >&2
		exit 1
	fi
done

if ! command -v llvm-nm >/dev/null 2>&1; then
	echo "llvm-nm not found in PATH" >&2
	exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/verify-merged-dyld.XXXXXX")
trap 'rm -rf "$work"' EXIT

has_symbol() {
	llvm-nm -g "$1" 2>/dev/null | awk -v want="$2" '$NF == want { found = 1 } END { exit found ? 0 : 1 }'
}

# The two new hooks must come from the dylib, so the input dyld must not already
# carry them.  dlopen_from and __fcntl are not asserted here: a stock dyld is
# free to define either, and only the merged output matters for them.
for sym in "$HOOK_OVERRIDABLE" "$HOOK_ALWAYS_OVERRIDABLE"; do
	if has_symbol "$input_dyld" "$sym"; then
		fail "input dyld already defines $sym; the merge would not prove injection"
	else
		pass "input dyld does not define $sym"
	fi
done

merged="$work/dyld"
if ! "$merger" "$input_dyld" "$dyldhook" "$merged" > "$work/merger.log" 2>&1; then
	echo "MachOMerger failed; it printed:" >&2
	sed 's/^/  /' "$work/merger.log" >&2
	echo "Result: could not merge (exit 3)" >&2
	exit 3
fi
pass "MachOMerger merged $dyldhook into the dyld"

for sym in "$HOOK_OVERRIDABLE" "$HOOK_ALWAYS_OVERRIDABLE" "$HOOK_DLOPEN_FROM" "$HOOK_FCNTL"; do
	if has_symbol "$merged" "$sym"; then
		pass "merged dyld carries $sym"
	else
		fail "merged dyld does not carry $sym"
	fi
done

if [ "$fail_count" -ne 0 ]; then
	echo "Result: FAILED ($fail_count assertion(s))" >&2
	exit 1
fi

echo "Result: PASS"
