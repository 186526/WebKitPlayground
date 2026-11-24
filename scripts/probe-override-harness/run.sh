#!/usr/bin/env bash
# Drives scripts/probe-webkit-override-endpoints.sh against a fake ssh, so every
# device-facing branch runs with no device present. The fake ssh in this
# directory mirrors the one behaviour of the real binary that matters here: it
# consumes its local stdin unless -n was passed.
#
# Nothing is written inside the repository: the fake ssh, the cases and the logs
# all go to a temporary directory that is removed on exit. Set HARNESS_KEEP=1 to
# keep it and print the path.
set -u

HARNESS_DIR=$(cd "$(dirname "$0")" && pwd)
SCRIPTS_DIR=$(cd "${HARNESS_DIR}/.." && pwd)
PROBE=${SCRIPTS_DIR}/probe-webkit-override-endpoints.sh

WORK_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/probe-harness.XXXXXX") || exit 2
BIN_DIR=${WORK_ROOT}/bin
CASE_ROOT=${WORK_ROOT}/cases

cleanup_work_root() {
    if [ "${HARNESS_KEEP:-0}" = 1 ]; then
        printf 'harness artifacts kept in %s\n' "${WORK_ROOT}"
    else
        rm -rf "${WORK_ROOT}"
    fi
}
trap cleanup_work_root EXIT

PASS=0
FAIL=0

fail() { printf 'FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
pass() { printf 'ok    %s\n' "$1"; PASS=$((PASS + 1)); }

expect_contains() {
    if grep -Fq -- "$3" "$2"; then pass "$1"; else
        fail "$1: '$3' not in $2"; sed -n '1,120p' "$2" | sed 's/^/      /'; fi
}
expect_not_contains() {
    if grep -Fq -- "$3" "$2"; then
        fail "$1: '$3' unexpectedly in $2"; else pass "$1"; fi
}
expect_status() {
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1: exit $2, expected $3"; fi
}
expect_count() {
    local actual
    actual=$(grep -Fc -- "$3" "$2" || true)
    if [ "${actual:-0}" = "$4" ]; then pass "$1"; else
        fail "$1: $3 appears ${actual:-0} time(s) in $2, expected $4"; fi
}

new_case() {
    CASE_DIR=${CASE_ROOT}/$1
    rm -rf "${CASE_DIR}"
    mkdir -p "${CASE_DIR}/state/crash" "${CASE_DIR}/log"
    printf '%s\n' "$2" >"${CASE_DIR}/state/scenario.json"
    # Fixture reports live beside the case so the stub can read them by name.
    cp "${HARNESS_DIR}"/fixture-*.ips "${CASE_DIR}/" 2>/dev/null || true
    export FAKE_DEVICE_STATE=${CASE_DIR}/state
    export PATH=${BIN_DIR}:${PATH}
    export PROBE_WAIT_SECONDS=0
    export DEVICE_SUDO_PASSWORD=${FAKE_SUDO_PASSWORD:-}
}

run_probe() {
    "${PROBE}" --ssh-target mobile@192.168.2.1 --log-dir "${CASE_DIR}/log" \
        >"${CASE_DIR}/stdout.txt" 2>&1
    CASE_STATUS=$?
}

mkdir -p "${BIN_DIR}"
cp "${HARNESS_DIR}/ssh" "${BIN_DIR}/ssh"
chmod +x "${BIN_DIR}/ssh"
if [ ! -x "${PROBE}" ]; then
    printf 'FAIL  %s is not executable\n' "${PROBE}"
    exit 2
fi

SERVICES='[
 "com.apple.accessibility.BrailleTranslation.BrailleTranslationService",
 "com.apple.WebKit.WebContent",
 "com.apple.MTLCompilerService",
 "com.apple.WebKit.GPU",
 "com.apple.WebKit.Networking",
 "com.apple.WebKit.WebContent.CaptivePortal"
]'
TWO='[
 "com.apple.accessibility.BrailleTranslation.BrailleTranslationService",
 "com.apple.MTLCompilerService"
]'

SYS_XPC='/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent'
SYS_GPU='/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU'
OVR_XPC='/private/preboot/UUID/dopamine-ML/procursus/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU'
OVR_FW='/private/preboot/UUID/dopamine-ML/procursus/Library/WebKitOverride/WebKit.framework/WebKit'
JB_FW='/var/jb/Library/Frameworks/WebKit.framework/WebKit'
SYS_FW='/System/Library/Frameworks/WebKit.framework/WebKit'

# --- P1-1: every helper in the list is reached, in both sudo modes -----------
#
# Three stale helpers must all be signalled and four new ones must all be
# attached. A stub that consumes stdin (like real ssh) makes an unprotected
# loop stop after the first entry.

make_helpers() {
    local count=$1 framework=$2 base=$3 index pid entries=''
    for index in $(seq 1 "${count}"); do
        pid=$((base + index))
        [ -n "${entries}" ] && entries="${entries},"
        entries="${entries}{\"pid\": ${pid}, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${framework}\"]}"
    done
    printf '[%s]' "${entries}"
}

STALE='[
 {"pid": 70750, "ppid": 1, "path": "'"${SYS_XPC}"'"},
 {"pid": 70751, "ppid": 1, "path": "'"${SYS_GPU}"'"},
 {"pid": 70753, "ppid": 1, "path": "'"${SYS_XPC}"'"}
]'

printf '\n== P1-1 no-password mode: four attaches, three stale kills\n'
new_case loop-nopw "{\"services\": ${SERVICES}, \"helpers\": $(make_helpers 4 "${OVR_FW}" 8000), \"stale_helpers\": ${STALE}}"
run_probe
expect_status 'loop-nopw exit' "${CASE_STATUS}" 0
expect_contains 'loop-nopw verdict A' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'
expect_contains 'loop-nopw all four helpers counted' "${CASE_DIR}/log/probe.log" 'PROBE_NEW_HELPERS=4'
expect_contains 'loop-nopw all three stale killed' "${CASE_DIR}/log/probe.log" 'PROBE_STALE_HELPERS_KILLED=3'
expect_count 'loop-nopw four attaches logged' "${CASE_DIR}/log/probe.log" 'sudo lldb ... image list -o -f   [helper' 4
expect_contains 'loop-nopw every helper yielded a path' "${CASE_DIR}/log/probe.log" 'PROBE_NEW_HELPER_FRAMEWORK_UNKNOWN=0'

printf '\n== P1-1 password mode: the same four attaches and three kills\n'
FAKE_SUDO_PASSWORD=pw-for-test
new_case loop-pw "{\"services\": ${SERVICES}, \"helpers\": $(make_helpers 4 "${OVR_FW}" 8000), \"stale_helpers\": ${STALE}}"
DEVICE_SUDO_PASSWORD=pw-for-test run_probe
expect_status 'loop-pw exit' "${CASE_STATUS}" 0
expect_contains 'loop-pw all four helpers counted' "${CASE_DIR}/log/probe.log" 'PROBE_NEW_HELPERS=4'
expect_contains 'loop-pw all three stale killed' "${CASE_DIR}/log/probe.log" 'PROBE_STALE_HELPERS_KILLED=3'
expect_count 'loop-pw four attaches logged' "${CASE_DIR}/log/probe.log" 'sudo lldb ... image list -o -f   [helper' 4

printf '\n== P1-1 the reviewer device state: 3 jb helpers, no password mode\n'
# Endpoints gone, three helpers loading the /var/jb override framework. Reading
# only the first gives zero jb frameworks and a clean B, which is the answer
# inverted.
new_case loop-false-b "{\"services\": ${TWO}, \"helpers\": $(make_helpers 3 "${OVR_FW}" 5001)}"
run_probe
expect_status 'loop-false-b exit' "${CASE_STATUS}" 4
expect_contains 'loop-false-b not B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'loop-false-b reason names the jb framework' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=services_gone_but_a_new_helper_loaded_a_var_jb_framework'
expect_contains 'loop-false-b three override frameworks counted' "${CASE_DIR}/log/probe.log" 'PROBE_NEW_HELPER_FRAMEWORK_OVERRIDE=3'

# --- P1-2: two same-named images in one helper ------------------------------

printf '\n== P1-2 both WebKit images in one helper, system line first\n'
new_case two-images-system-first "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8101, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${SYS_FW}\", \"${OVR_FW}\"]}]}"
run_probe
expect_status 'two-images-system-first exit' "${CASE_STATUS}" 0
expect_contains 'two-images-system-first verdict A' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'

printf '\n== P1-2 the same helper with the override image first\n'
new_case two-images-override-first "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8101, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${OVR_FW}\", \"${SYS_FW}\"]}]}"
run_probe
expect_status 'two-images-override-first exit' "${CASE_STATUS}" 0
expect_contains 'two-images-override-first verdict A' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'
expect_contains 'two-images-order irrelevant: both counted' "${CASE_DIR}/log/probe.log" 'PROBE_NEW_HELPER_FRAMEWORK_OVERRIDE=1'

# --- P1-3: B needs framework evidence ---------------------------------------

printf '\n== P1-3 zero endpoints and every attach failing is not B\n'
new_case b-no-evidence "{\"services\": ${TWO}, \"helpers\": $(make_helpers 4 "${SYS_FW}" 8200), \"lldb_fails\": true}"
run_probe
expect_status 'b-no-evidence exit' "${CASE_STATUS}" 4
expect_contains 'b-no-evidence verdict' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'b-no-evidence reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=services_gone_but_no_helper_framework_path_read'
expect_contains 'b-no-evidence recorded flag' "${CASE_DIR}/log/probe.log" 'PROBE_HELPER_FRAMEWORK_PATHS_RECORDED=0'

printf '\n== P1-3 zero endpoints with real system frameworks is still B\n'
new_case b-with-evidence "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8200)}"
run_probe
expect_status 'b-with-evidence exit' "${CASE_STATUS}" 0
expect_contains 'b-with-evidence verdict' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=B'
expect_contains 'b-with-evidence reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=services_gone_or_at_most_two_and_no_new_helper_loaded_a_var_jb_framework'

# --- P1-4: the gui/501 gate ------------------------------------------------

printf '\n== P1-4 launchd refuses gui/501 on stderr, nothing is written\n'
new_case gui-error "{\"mode\": \"gui_error\"}"
run_probe
expect_status 'gui-error exit' "${CASE_STATUS}" 3
expect_contains 'gui-error Chinese message' "${CASE_DIR}/stdout.txt" '需要先重启设备，图形域缺失'
expect_contains 'gui-error aborted verdict' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=ABORTED'
expect_not_contains 'gui-error never installs' "${CASE_DIR}/log/probe.log" 'install-webkit-override.sh'
expect_contains 'gui-error only one device command' "${CASE_DIR}/state/calls.log" 'launchctl print gui/501'
expect_count 'gui-error single call' "${CASE_DIR}/state/calls.log" '' 1

printf '\n== gui/501 empty still aborts before any write\n'
new_case gui-empty "{\"gui_empty\": true}"
run_probe
expect_status 'gui-empty exit' "${CASE_STATUS}" 3
expect_contains 'gui-empty Chinese message' "${CASE_DIR}/stdout.txt" '需要先重启设备，图形域缺失'
expect_not_contains 'gui-empty never installs' "${CASE_DIR}/log/probe.log" 'install-webkit-override.sh'

# --- p2: A must say which mechanism ----------------------------------------

printf '\n== A through the system stub names the handover, not launchd scanning\n'
new_case a-stub "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8301, \"path\": \"${SYS_GPU}\", \"frameworks\": [\"${OVR_FW}\"]}]}"
run_probe
expect_status 'a-stub exit' "${CASE_STATUS}" 0
expect_contains 'a-stub verdict' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'
expect_contains 'a-stub reason names the stub handover' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=all_four_services_present_and_a_helper_started_by_the_system_stub_loaded_the_override_framework'
expect_contains 'a-stub counter' "${CASE_DIR}/log/probe.log" 'PROBE_JB_FRAMEWORK_THROUGH_STUB=1'
expect_contains 'a-stub says A cannot settle the question here' "${CASE_DIR}/log/probe.log" 'A cannot come from launchd scanning the override tree'
expect_contains 'a-stub points at the unpruned run' "${CASE_DIR}/log/probe.log" 'docs/ios16-branch-a-deployment-path.md'

printf '\n== A from an override .xpc is named as its own mechanism\n'
new_case a-override-xpc "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8302, \"path\": \"${OVR_XPC}\", \"frameworks\": [\"${OVR_FW}\"]}]}"
run_probe
expect_status 'a-override-xpc exit' "${CASE_STATUS}" 0
expect_contains 'a-override-xpc reason names the resolved bundle' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=all_four_services_present_and_a_helper_started_from_the_override_xpc_loaded_a_var_jb_framework'
expect_contains 'a-override-xpc counter' "${CASE_DIR}/log/probe.log" 'PROBE_JB_FRAMEWORK_FROM_OVERRIDE_XPC=1'

printf '\n== A from the /var/jb Frameworks copy is named separately again\n'
new_case a-jb-copy "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8303, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${JB_FW}\"]}]}"
run_probe
expect_contains 'a-jb-copy reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=all_four_services_present_and_a_helper_loaded_the_var_jb_frameworks_copy'

printf '\n== p2 a state that qualifies for A is still refused when a reading step has no baseline\n'
# The same four-services-plus-override-framework state as a-stub, which reads A,
# run with step 3's before-listing failing. Step 3 is a reading step, so a run
# whose baseline is missing there must not reach A: the report that would appear
# during it cannot be told from one already on the device. The verdict has to
# stay UNKNOWN, and the reason must name the baseline, not a crash.
new_case a-but-no-baseline "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8301, \"path\": \"${SYS_GPU}\", \"frameworks\": [\"${OVR_FW}\"]}], \"crash_initial\": {\"MobileSafari-2026-11-24.ips\": \"fixture-dyld.ips\"}, \"find_timing\": {\"fail_calls\": [6]}}"
run_probe
expect_status 'a-but-no-baseline exit' "${CASE_STATUS}" 4
expect_not_contains 'a-but-no-baseline never says A' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'
expect_contains 'a-but-no-baseline verdict is UNKNOWN' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'a-but-no-baseline reason names the baseline' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_baseline_missing_in_reading_window'
expect_contains 'a-but-no-baseline step 3 marked INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'a-but-no-baseline listed in the summary' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3'
expect_contains 'a-but-no-baseline says the before-listing failed' "${CASE_DIR}/log/probe.log" "this step's before-listing failed, so there was no baseline"
expect_not_contains 'a-but-no-baseline does not diff against an empty file' "${CASE_DIR}/log/probe.log" 'crash reports after this step: 1; new: 1'

# --- p2: one line with two occurrences of a service ------------------------

printf '\n== one services line holding a name twice counts twice\n'
new_case two-per-line "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8401, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${SYS_FW}\"]}]}"
mkdir -p "${CASE_DIR}/log"
printf '"com.apple.WebKit.GPU" and com.apple.WebKit.WebContent.com.apple.WebKit.GPU\n' >"${CASE_DIR}/line.txt"
count=$(awk -v name="com.apple.WebKit.GPU" '
    BEGIN { length_of_name = length(name) }
    {
        skip = 0
        while ((position = index(substr($0, skip + 1), name)) > 0) {
            offset = skip + position
            before = (offset > 1) ? substr($0, offset - 1, 1) : ""
            after = substr($0, offset + length_of_name, 1)
            if (before !~ /[A-Za-z0-9_.-]/ && after !~ /[A-Za-z0-9_.-]/) { count += 1 }
            skip = offset + length_of_name - 1
        }
    }
    END { print count + 0 }' "${CASE_DIR}/line.txt")
if [ "${count}" = 1 ]; then
    pass 'two-per-line counts the bare name once and not the longer identifier'
else
    fail "two-per-line counted ${count}, expected 1"
fi
printf 'com.apple.WebKit.GPU com.apple.WebKit.GPU\n' >"${CASE_DIR}/line2.txt"
count2=$(awk -v name="com.apple.WebKit.GPU" '
    BEGIN { length_of_name = length(name) }
    {
        skip = 0
        while ((position = index(substr($0, skip + 1), name)) > 0) {
            offset = skip + position
            before = (offset > 1) ? substr($0, offset - 1, 1) : ""
            after = substr($0, offset + length_of_name, 1)
            if (before !~ /[A-Za-z0-9_.-]/ && after !~ /[A-Za-z0-9_.-]/) { count += 1 }
            skip = offset + length_of_name - 1
        }
    }
    END { print count + 0 }' "${CASE_DIR}/line2.txt")
if [ "${count2}" = 2 ]; then
    pass 'two-per-line counts two occurrences on one line'
else
    fail "two-per-line counted ${count2} for two occurrences, expected 2"
fi

# --- p1: the crash exclusion must be established, not assumed ----------------

# The report bodies are fixture-*.ips next to this script, not inline JSON: they
# carry quotes and nesting, which a second level of escaping in scenario.json
# would make unreadable.

printf '\n== p1 a report that appears in step 3 and cannot be read is not a clean B\n'
# Step 3 is a reading step. Its new report exists but its body is truncated, the
# shape CrashReporter produces while it is still writing. The step must print
# INVALID, PROBE_INVALID_STEPS must list it, and the verdict must not be B.
new_case unreadable-report "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_timing\": {\"after_call\": 6, \"files\": {\"MobileSafari-2026-11-25.ips\": \"fixture-truncated.ips\"}}}"
run_probe
expect_status 'unreadable-report exit' "${CASE_STATUS}" 4
expect_contains 'unreadable-report not B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'unreadable-report reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_exclusion_not_established_in_reading_window'
expect_contains 'unreadable-report step marked INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'unreadable-report invalid steps list it' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3'
expect_contains 'unreadable-report names the unreadable file' "${CASE_DIR}/log/probe.log" 'unreadable'
expect_contains 'unreadable-report summary counts it' "${CASE_DIR}/log/probe.log" 'steps that could not rule out a crash (a DYLD or PAC_EXCEPTION report appeared, or the exclusion could not be established): 3'
expect_not_contains 'unreadable-report never claims none' "${CASE_DIR}/log/probe.log" 'exclusion could not be established): none'

printf '\n== p1 a crash listing that fails during a reading step is not a clean B\n'
# The "after" listing of step 3 fails, so no diff exists for that step. That is
# an exclusion nobody established, which is the same state as an unreadable file.
new_case snapshot-fails "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"find_timing\": {\"fail_from_call\": 7}}"
run_probe
expect_status 'snapshot-fails exit' "${CASE_STATUS}" 4
expect_contains 'snapshot-fails not B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'snapshot-fails reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_exclusion_not_established_in_reading_window'
expect_contains 'snapshot-fails step marked INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'snapshot-fails invalid steps list it' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3'
expect_contains 'snapshot-fails says the listing failed' "${CASE_DIR}/log/probe.log" 'crash reports after this step: unavailable'

printf '\n== p2 a step whose before-listing failed has no baseline, so nothing is called new\n'
# Call numbering: 1 is the baseline taken in main, 6 is step 3's before-listing,
# 7 its after-listing. Only call 6 fails, so step 3 has an "after" list and no
# baseline. A DYLD report is already on the device before the run, so a diff
# against an empty baseline (the old behaviour) would report it as new and mark
# the step as a crash that never happened. The step must instead say it has no
# baseline, and the reason must name the baseline rather than a crash.
new_case missing-baseline "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_initial\": {\"MobileSafari-2026-11-24.ips\": \"fixture-dyld.ips\"}, \"find_timing\": {\"fail_calls\": [6]}}"
run_probe
expect_status 'missing-baseline exit' "${CASE_STATUS}" 4
expect_contains 'missing-baseline not A or B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'missing-baseline reason names the baseline' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_baseline_missing_in_reading_window'
expect_not_contains 'missing-baseline never claims a crash appeared' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=dyld_or_pac_crash_in_reading_window'
expect_contains 'missing-baseline step marked INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'missing-baseline invalid steps list it' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3'
expect_contains 'missing-baseline says the before-listing failed' "${CASE_DIR}/log/probe.log" 'crash reports before this step: unavailable (exit 1)'
expect_contains 'missing-baseline names the absent baseline' "${CASE_DIR}/log/probe.log" 'no baseline'
expect_not_contains 'missing-baseline does not diff against an empty file' "${CASE_DIR}/log/probe.log" 'crash reports after this step: 1; new: 1'
expect_not_contains 'missing-baseline does not call the old report new' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID: a DYLD or PAC_EXCEPTION crash appeared during this step'

printf '\n== p2 a failed before-listing now reports its real exit status\n'
# The old construct ran crash_snapshot inside $(...), so REMOTE_RC was discarded
# and the failure path printed "unavailable (exit 0)". The status has to be the
# listing's own.
expect_not_contains 'missing-baseline never reports a failed listing as exit 0' "${CASE_DIR}/log/probe.log" 'crash reports before this step: unavailable (exit 0)'

printf '\n== p2 an after-listing that fails also reports its real exit status\n'
new_case snapshot-fails-status "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"find_timing\": {\"fail_from_call\": 7}}"
run_probe
expect_contains 'snapshot-fails-status real status' "${CASE_DIR}/log/probe.log" 'crash reports after this step: unavailable (exit 1)'
expect_not_contains 'snapshot-fails-status not exit 0' "${CASE_DIR}/log/probe.log" 'crash reports after this step: unavailable (exit 0)'

printf '\n== p1 a readable DYLD report in step 3 still reports the crash reason\n'
new_case dyld-report "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_timing\": {\"after_call\": 6, \"files\": {\"MobileSafari-2026-11-25.ips\": \"fixture-dyld.ips\"}}}"
run_probe
expect_status 'dyld-report exit' "${CASE_STATUS}" 4
expect_contains 'dyld-report reason names the crash' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=dyld_or_pac_crash_in_reading_window'
expect_contains 'dyld-report invalid steps list it' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3'

printf '\n== p1 a report outside the reading window is listed but does not block B\n'
# Step 6 is not a reading step, so a report there must not turn a clean B into
# UNKNOWN. It must still show in PROBE_INVALID_STEPS: a step the log prints as
# INVALID may not be silently dropped from the summary.
new_case report-after-window "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_timing\": {\"after_call\": 12, \"files\": {\"MobileSafari-2026-11-25.ips\": \"fixture-dyld.ips\"}}}"
run_probe
expect_contains 'report-after-window step 6 marked INVALID' "${CASE_DIR}/log/probe.log" 'step 6 is INVALID'
expect_contains 'report-after-window listed in the summary' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=6'
expect_contains 'report-after-window verdict still B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=B'

# --- unchanged behaviour ---------------------------------------------------

printf '\n== regression: no new helper, no framework path read, stale exclusion\n'
new_case no-new-helpers "{\"services\": ${TWO}, \"helpers\": [], \"stale_helpers\": ${STALE}}"
run_probe
expect_status 'no-new-helpers exit' "${CASE_STATUS}" 4
expect_contains 'no-new-helpers reason' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=no_new_helper_after_launch'
expect_contains 'no-new-helpers stale counted' "${CASE_DIR}/log/probe.log" 'PROBE_STALE_HELPERS=3'

printf '\n== regression: ps without ppid degrades and kills nothing\n'
new_case no-ppid "{\"services\": ${SERVICES}, \"helpers\": [{\"pid\": 8501, \"path\": \"${SYS_XPC}\", \"frameworks\": [\"${OVR_FW}\"]}], \"ps_rejects_ppid\": true}"
run_probe
expect_status 'no-ppid exit' "${CASE_STATUS}" 0
expect_contains 'no-ppid degraded notice' "${CASE_DIR}/log/probe.log" 'ps -Ao pid,ppid,comm was rejected'
expect_contains 'no-ppid verdict still A' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=A'

printf '\n== regression: install failure still rolls back\n'
new_case install-fails "{\"install_fails\": true}"
run_probe
expect_status 'install-fails exit' "${CASE_STATUS}" 4
expect_contains 'install-fails skipped step 3' "${CASE_DIR}/log/probe.log" 'skipped: the install did not succeed'

printf '\n== p2 the baseline reason needs a successful after-listing on that same step\n'
# Both cases put a truncated report on the device during step 4's reading window:
# the stub writes the fixture at the first listing past call 8, and call 9 is
# step 4's "after" listing, so step 4 sees it as a new report it cannot read. The
# only difference is whether step 3's own after-listing (call 7) succeeded.
#
#   baseline-and-unreadable  call 6 fails, call 7 works  -> step 3 has no
#       baseline but does have an "after" list, so it enters BASELINE_MISSING_STEPS
#       and the reason names the baseline. Step 4 is unreadable too; the baseline
#       reason wins, which is the priority this documents.
#   baseline-and-after-fail  calls 6 and 7 both fail -> step 3 has neither, so it
#       walks the "after-listing failed" branch and never enters
#       BASELINE_MISSING_STEPS. No step qualifies for the baseline reason, so the
#       run reports the weaker one.
#
# Without the conjunction in the doc, the second shape would read as a
# contradiction of it: the before-listing did fail on a reading step, yet the
# baseline reason is absent. Both cases assert step 4 is INVALID as well, so
# deleting the report from the device turns them red instead of leaving the
# fixture unused.

printf '\n== p2 a failed before-listing plus a readable after names the baseline, even with another step unreadable\n'
new_case baseline-and-unreadable "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_timing\": {\"after_call\": 8, \"files\": {\"MobileSafari-2026-11-25.ips\": \"fixture-truncated.ips\"}}, \"find_timing\": {\"fail_calls\": [6]}}"
run_probe
expect_status 'baseline-and-unreadable exit' "${CASE_STATUS}" 4
expect_contains 'baseline-and-unreadable not A or B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'baseline-and-unreadable reason names the baseline' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_baseline_missing_in_reading_window'
expect_contains 'baseline-and-unreadable step 3 INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'baseline-and-unreadable step 4 INVALID' "${CASE_DIR}/log/probe.log" 'step 4 is INVALID'
expect_contains 'baseline-and-unreadable both steps listed' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3,4'
expect_contains 'baseline-and-unreadable step 3 says no baseline' "${CASE_DIR}/log/probe.log" "step 3 is INVALID: its crash exclusion is not established (this step's before-listing failed"
expect_contains 'baseline-and-unreadable step 4 says unreadable' "${CASE_DIR}/log/probe.log" 'step 4 is INVALID: its crash exclusion is not established (a new report could not be read'

printf '\n== p2 without that step'"'"'s own after-listing the baseline reason cannot appear\n'
new_case baseline-and-after-fail "{\"services\": ${TWO}, \"helpers\": $(make_helpers 2 "${SYS_FW}" 8600), \"crash_timing\": {\"after_call\": 8, \"files\": {\"MobileSafari-2026-11-25.ips\": \"fixture-truncated.ips\"}}, \"find_timing\": {\"fail_calls\": [6, 7]}}"
run_probe
expect_status 'baseline-and-after-fail exit' "${CASE_STATUS}" 4
expect_contains 'baseline-and-after-fail not A or B' "${CASE_DIR}/log/probe.log" 'PROBE_VERDICT=UNKNOWN'
expect_contains 'baseline-and-after-fail reason is the weaker one' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_exclusion_not_established_in_reading_window'
expect_not_contains 'baseline-and-after-fail never names the baseline' "${CASE_DIR}/log/probe.log" 'PROBE_REASON=crash_baseline_missing_in_reading_window'
expect_contains 'baseline-and-after-fail step 3 INVALID' "${CASE_DIR}/log/probe.log" 'step 3 is INVALID'
expect_contains 'baseline-and-after-fail says the after-listing failed' "${CASE_DIR}/log/probe.log" 'crash reports after this step: unavailable'
expect_not_contains 'baseline-and-after-fail never claims a baseline it did not get' "${CASE_DIR}/log/probe.log" "no baseline: none of these"
# The truncated report has to reach a reader for this case to be the shape the
# comment above describes: step 3 loses its baseline while step 4 has a report it
# cannot read. Both facts are asserted, so dropping the fixture fails the case.
expect_contains 'baseline-and-after-fail step 4 INVALID' "${CASE_DIR}/log/probe.log" 'step 4 is INVALID'
expect_contains 'baseline-and-after-fail both steps listed' "${CASE_DIR}/log/probe.log" 'PROBE_INVALID_STEPS=3,4'
expect_contains 'baseline-and-after-fail step 4 says unreadable' "${CASE_DIR}/log/probe.log" 'step 4 is INVALID: its crash exclusion is not established (a new report could not be read'

printf '\n== regression: preexisting override refuses to install\n'
new_case preexisting "{\"override_initial\": \"present\", \"services\": ${SERVICES}, \"helpers\": []}"
run_probe
expect_contains 'preexisting refused' "${CASE_DIR}/log/probe.log" 'refusing to install'

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
