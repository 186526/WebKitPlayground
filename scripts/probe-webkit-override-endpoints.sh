#!/usr/bin/env bash
#
# probe-webkit-override-endpoints.sh — run the M63 A-or-B experiment against a
# jailbroken iPad from the host, over ssh.
#
# The question is the one unmeasured link in the "run the whole WebKit 18.3 on
# iOS 16.1" route: once an 18.3 WebKit.framework sits at a path launchd did not
# index, does launchd still register the four WebKit XPC endpoints in the app's
# per-pid domain? The design, the readings that already exist and the two
# verdicts are in WebKitPlayground/docs/ios16-all-18.3-xpc-endpoint-audit.md,
# section 4 and section 8 item 1. This script executes that design; the
# reasoning and the meaning of each verdict are in
# WebKitPlayground/docs/ios16-override-endpoint-probe.md.
#
# What it writes on the device: /var/jb/Library/WebKitOverride, created by the
# already-installed /var/mobile/install-webkit-override.sh and removed again by
# /var/mobile/uninstall-webkit-override.sh. Nothing else. No system file, no
# basebin file, no package. The removal also runs from an EXIT trap, so an abort
# in the middle of the run still leaves the device as it was found.
#
# The run is seven steps. Each step is bracketed by a CrashReporter listing, so
# a report that appears during a step is attributed to that step instead of
# being guessed at from timestamps afterwards. A new report whose
# termination.namespace is DYLD or PAC_EXCEPTION means the process died before
# it could ask launchd for anything: that step is marked INVALID, and a run
# whose reading steps are invalid cannot conclude A or B.
#
# Usage:
#   DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh
#   DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh \
#       --ssh-target mobile@192.168.2.135 --log-dir /tmp/wk-probe
#
# Host requirements: bash 3.2 or newer, ssh, and the usual sed, awk, grep, sort,
# comm, wc, tee. Works on macOS and Linux host bash alike.
#
# Device requirements: a login shell, python3, launchctl, killall, uiopen, ps,
# and root through sudo. python3 is required because an .ips file is one JSON
# header line followed by a JSON body, and AGENTS.md's device tool list has no
# plist reader to split them.
#
# Exit status: 0 for verdict A or B, 4 for UNKNOWN, 3 when SpringBoard's job
# under user/501 is missing or not running, 2 for a usage or setup error.
# gui/501 is read as a diagnostic and does not affect the exit status: on
# iOS 16.1 under Dopamine it is empty with exit 125 on a healthy device.
#
set -u

SSH_TARGET=${PROBE_SSH_TARGET:-mobile@192.168.2.135}
WAIT_SECONDS=${PROBE_WAIT_SECONDS:-5}
LOG_DIR=${PROBE_LOG_DIR:-}
DEVICE_SUDO_PASSWORD=${DEVICE_SUDO_PASSWORD:-}

JBROOT=/var/jb
OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride
INSTALL_SCRIPT=/var/mobile/install-webkit-override.sh
UNINSTALL_SCRIPT=/var/mobile/uninstall-webkit-override.sh
DEVICE_BASH=/var/jb/usr/bin/bash
CRASH_DIR=/var/mobile/Library/Logs/CrashReporter

# Safari is launched with a URL, because a bare exec of MobileSafari produces a
# UI process that never asks for a page, and a process that never asks for a
# page never asks libxpc for WebContent (build-state.md:450). Each attempt is
# tried in order and the first that yields a MobileSafari pid wins; the command,
# exit code and pid of every attempt reach the log, so the reading is always
# traceable to the launch that produced it.
#
# The --path form comes first because `uiopen <url>` opens the default browser
# on this device and that is Chrome, not Safari (AGENTS.md "设备" item 2,
# build-state.md:448). The mission's plain `uiopen https://example.com` is kept
# as the last attempt so its result is recorded rather than assumed.
SAFARI_LAUNCH_ATTEMPTS=(
    'uiopen --path /Applications/MobileSafari.app https://example.com'
    'uiopen --path /Applications/MobileSafari.app'
    'uiopen https://example.com'
)

SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=20)

LOG_FILE=''
WORK_DIR=''
STEPS_FILE=''
PYTHON_PROGRAM=''
REMOTE_OUTPUT=''
REMOTE_RC=0
RUN_RC=0

SUDO_MODE=''
OVERRIDE_INSTALLED=0
UNINSTALL_CONFIRMED=0

GUI_LINES=0
GUI_RC=0
GUI_OUTPUT=''
OVERRIDE_PRESENT_BEFORE=unknown
INSTALL_RC=not-run
# Whether the installed override carries WebKit.framework/XPCServices. This is
# read from the device after the install rather than assumed from the install
# script's name: two copies of that script exist and they disagree, the device's
# /var/mobile one copies the tree whole while the repo's scripts/ one prunes it.
# The verdict narrative below depends on which one this run used.
OVERRIDE_XPC_SERVICES=unknown
OVERRIDE_PRESENT_AFTER=unknown
UNINSTALL_RC=not-run
SAFARI_PID=''
SAFARI_LAUNCH_MODE=none
SVC_WEBCONTENT=0
SVC_GPU=0
SVC_NETWORKING=0
SVC_CAPTIVEPORTAL=0
SVC_BRAILLE=0
SVC_MTL=0
SVC_DISTINCT=0
# The helper tally covers the helpers this launch produced, not every WebKit
# helper on the device: the device carries stale ones from earlier experiments
# with ppid 1 and /System paths (M66 measured three), and counting them would
# turn a working override into a system-only UNKNOWN. See step 3 and step 5.
HELPER_NEW_TOTAL=0
HELPER_NEW_OVERRIDE=0
HELPER_NEW_JB_FRAMEWORKS=0
HELPER_NEW_SYSTEM=0
HELPER_NEW_OTHER=0
HELPER_STALE_TOTAL=0
HELPER_STALE_OVERRIDE=0
HELPER_STALE_JB_FRAMEWORKS=0
HELPER_STALE_SYSTEM=0
HELPER_STALE_OTHER=0
HELPER_KILLED=0
BASELINE_HELPERS_RECORDED=0
# Steps whose "before" CrashReporter listing failed. A step in this list is
# INVALID for a reason the crash gate has to name as such: the report that
# appeared during it cannot be told from one already on the device, which is a
# different fact from "a report could not be read".
BASELINE_MISSING_STEPS=''
BASELINE_MISSING_SEPARATOR=''
# comm names each helper's executable, but a helper can run from the override
# .xpc bundle (when the override carries WebKit.framework/XPCServices) or from
# the system .xpc bundle (when it does not), so comm alone cannot say which
# WebKit framework it loaded. Which shape this run installed is measured in
# step 2 and recorded in OVERRIDE_XPC_SERVICES. The framework path comes from
# lldb's image list, one attach per new helper, because that is the only reader
# the device has.
HELPER_FRAMEWORK_PATHS_RECORDED=0
HELPER_FRAMEWORK_OVERRIDE=0
HELPER_FRAMEWORK_JB=0
HELPER_FRAMEWORK_SYSTEM=0
HELPER_FRAMEWORK_UNKNOWN=0
# A fires when a new helper loaded a /var/jb framework, but two mechanisms can
# produce that and only one of them is launchd resolving a path it had not
# indexed. If the helper's own executable (comm) is under the override tree,
# launchd started the override's .xpc; if it is the system stub, launchd started
# the indexed stub and the dyld environment handed the override framework over.
# Both are reachable whenever the override carries XPCServices, so the counters
# are read from the comm paths rather than assumed from the install script.
HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC=0
HELPER_JB_FRAMEWORK_THROUGH_STUB=0
VERDICT=UNKNOWN
PROBE_REASON=not-decided

usage() {
    cat <<'USAGE'
Run the M63 A-or-B endpoint experiment on a jailbroken iPad from the host, over
ssh. Creates and then removes /var/jb/Library/WebKitOverride, and writes nothing
else on the device.

Usage:
  DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh
  DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh \
      --ssh-target mobile@192.168.2.135 --log-dir /tmp/wk-probe

Options:
  --ssh-target <[user@]host>  default mobile@192.168.2.135
  --log-dir <dir>             default a new directory under $TMPDIR
  --wait <seconds>            pause after each launch attempt, default 5
  --help                      print this text

Environment:
  DEVICE_SUDO_PASSWORD  device account password for sudo, read from stdin
  PROBE_SSH_TARGET      same as --ssh-target
  PROBE_WAIT_SECONDS    same as --wait
  PROBE_LOG_DIR         same as --log-dir

Exit status: 0 for verdict A or B, 4 for UNKNOWN, 3 when SpringBoard's job under
user/501 is missing or not running, 2 for a usage or setup error. gui/501 is
read as a diagnostic only: on iOS 16.1 under Dopamine it is empty with exit 125
on a healthy device.
USAGE
}

# LOG_FILE is empty until prepare_log runs, and a usage error is reported before
# that, so the log copy happens only once there is a log to append to.
die() {
    printf 'Error: %s\n' "$1" >&2
    if [ -n "${LOG_FILE}" ]; then
        printf 'Error: %s\n' "$1" >>"${LOG_FILE}"
    fi
    exit 2
}

# Every line the script prints goes through here, so the log file is the stdout
# of the run, verbatim and in order. One argument; it may contain newlines.
emit() {
    printf '%s\n' "$1" | tee -a "${LOG_FILE}"
}

# ---------------------------------------------------------------------------
# Device calls
# ---------------------------------------------------------------------------

# ssh reads its local stdin and throws it away when the remote command does not
# want it, so a call inside a `while read` loop would eat the rest of the list.
# `-n` closes that path. The two sudo modes cannot both take it: the password one
# feeds the password through stdin, and `-n` would discard the password before
# sudo ever saw it, so that path keeps the pipe and is instead protected by the
# loops reading their list from a file descriptor of their own.
remote() {
    REMOTE_OUTPUT=$(ssh -n "${SSH_OPTIONS[@]}" "${SSH_TARGET}" "$1" 2>&1)
    REMOTE_RC=$?
}

# sudo reads its password from stdin and ssh forwards that pipe, so the password
# is never part of a command line on either host and never reaches the log.
remote_sudo() {
    if [ "${SUDO_MODE}" = passwordless ]; then
        REMOTE_OUTPUT=$(ssh -n "${SSH_OPTIONS[@]}" "${SSH_TARGET}" "sudo -n $1" 2>&1)
    else
        REMOTE_OUTPUT=$(printf '%s\n' "${DEVICE_SUDO_PASSWORD}" \
            | ssh "${SSH_OPTIONS[@]}" "${SSH_TARGET}" "sudo -S -p '' $1" 2>&1)
    fi
    REMOTE_RC=$?
}

# The python program travels on ssh's stdin, so nothing has to survive quoting
# through two shells. Paths arrive as arguments.
remote_python() {
    local arguments=''
    local argument
    for argument in "$@"; do
        arguments="${arguments} ${argument}"
    done
    # shellcheck disable=SC2086 # intentional word splitting over known paths
    REMOTE_OUTPUT=$(ssh "${SSH_OPTIONS[@]}" "${SSH_TARGET}" "python3 - ${arguments}" \
        <"${PYTHON_PROGRAM}" 2>&1)
    REMOTE_RC=$?
}

# One command, its output, its exit status, and what the command was for. This
# is the only shape in which a device command runs, so the log is a complete
# record of the exchange and a reading can never be separated from the exit code
# that produced it.
run() {
    local purpose=$1
    local command=$2
    emit ""
    emit "\$ ${command}   [${purpose}]"
    remote "${command}"
    RUN_RC=${REMOTE_RC}
    emit "${REMOTE_OUTPUT}"
    emit "exit=${RUN_RC}"
}

run_sudo() {
    local purpose=$1
    local command=$2
    emit ""
    emit "\$ sudo ${command}   [${purpose}]"
    remote_sudo "${command}"
    RUN_RC=${REMOTE_RC}
    emit "${REMOTE_OUTPUT}"
    emit "exit=${RUN_RC}"
}

# ---------------------------------------------------------------------------
# CrashReporter snapshots, diffs and parsing
# ---------------------------------------------------------------------------

# find recurses, so Retired/ is included: a report launchd retired during the
# run must still be attributed to the step that caused it.
crash_snapshot() {
    local path_list=$1
    remote "find ${CRASH_DIR} -type f -name '*.ips'"
    if [ "${REMOTE_RC}" -ne 0 ]; then
        return 1
    fi
    printf '%s\n' "${REMOTE_OUTPUT}" | grep '\.ips$' | sort >"${path_list}" || true
    wc -l <"${path_list}" | tr -d '[:space:]'
}

crash_new_list() {
    local before=$1
    local after=$2
    local new_list=$3
    comm -13 "${before}" "${after}" >"${new_list}"
    wc -l <"${new_list}" | tr -d '[:space:]'
}

install_python_program() {
    PYTHON_PROGRAM=${WORK_DIR}/parse-ips.py
    cat >"${PYTHON_PROGRAM}" <<'PYTHON'
import json
import sys

PREFIX = "/var/mobile/Library/Logs/CrashReporter/"
ROOTS = ("/var/jb/", "/procursus/")


def describe(path):
    name = path[len(PREFIX):] if path.startswith(PREFIX) else path
    try:
        with open(path, "rb") as handle:
            handle.readline()
            document = json.loads(handle.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as error:
        return "IPS %s unreadable: %s" % (name, error), False
    termination = document.get("termination") or {}
    exception = document.get("exception") or {}
    images = [
        image.get("path") or ""
        for image in document.get("usedImages") or []
        if any(root in (image.get("path") or "") for root in ROOTS)
    ]
    return "IPS %s namespace=%s code=%s exception=%s jbimages=%d jbimage=%s procPath=%s" % (
        name,
        termination.get("namespace") or "-",
        termination.get("code"),
        exception.get("type") or "-",
        len(images),
        images[0] if images else "-",
        document.get("procPath") or "-",
    ), True


# Every report is printed, including the unreadable ones, so the log keeps the
# names. The exit status is what carries the verdict: any unreadable report in
# this step is an exclusion that could not be established, and the caller turns
# a nonzero status into INVALID. Writing "unreadable" and exiting 0 would let the
# step claim it had ruled crashes out while the file said otherwise.
unreadable = 0
for report_path in sys.argv[1:]:
    line, parsed = describe(report_path)
    print(line)
    if not parsed:
        unreadable += 1
if unreadable:
    sys.stderr.write("IPS %d report(s) in this step could not be read\n" % unreadable)
    sys.exit(3)
PYTHON
}

# A DYLD or PAC_EXCEPTION namespace is the failure that made earlier readings
# unattributable (build-state.md:415-429): the process died before launchd could
# be asked for anything. SIGNAL/SIGTRAP is printed but does not invalidate a
# step, because the design names only the first two.
crash_invalidates() {
    case $1 in
        *' namespace=DYLD '*) return 0 ;;
        *' namespace=PAC_EXCEPTION '*) return 0 ;;
        *) return 1 ;;
    esac
}

# Every new report in the step is printed and then judged, and the two failures
# are ranked. The parser exits 3 as soon as one report's body could not be read
# (CrashReporter may still be writing it when the snapshot taken seconds after the
# crash lists it), but it prints the reports it did read first, so a DYLD or
# PAC_EXCEPTION report in that output is a stronger fact than a later unreadable
# file: the crash happened, whether or not everything else could be read.
#
# Returns 0 when every report was read and none invalidates the step, 1 when one
# did, 2 when the exclusion could not be established.
describe_new_reports() {
    local new_file=$1
    local arguments=''
    local line invalidating=no
    [ -s "${new_file}" ] || return 0
    while IFS= read -r line; do
        arguments="${arguments} ${line}"
    done <"${new_file}"
    # shellcheck disable=SC2086 # intentional word splitting over known paths
    remote_python ${arguments}
    emit "${REMOTE_OUTPUT}"
    while IFS= read -r line; do
        crash_invalidates "${line}" && invalidating=yes
    done <<EOF
${REMOTE_OUTPUT}
EOF
    if [ "${invalidating}" = yes ]; then
        return 1
    fi
    if [ "${REMOTE_RC}" -ne 0 ]; then
        emit "  Error: at least one of this step's new reports could not be read (exit ${REMOTE_RC}), so a DYLD or PAC_EXCEPTION crash cannot be ruled out."
        return 2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Step bookkeeping
# ---------------------------------------------------------------------------

STEP_NUMBER=0
STEP_TITLE=''
# Set by step_begin and read by step_end. 1 means this step's "before" listing
# succeeded and can serve as a baseline; 0 means there is nothing to diff
# against, and the "after" list on its own cannot separate the reports that
# appeared during the step from the ones that were already on the device.
CRASH_BASELINE_ESTABLISHED=0

step_begin() {
    STEP_NUMBER=$1
    STEP_TITLE=$2
    emit ""
    emit "===== step ${STEP_NUMBER}: ${STEP_TITLE} ====="
    local count count_file="${WORK_DIR}/crashes-${STEP_NUMBER}-before.count"
    CRASH_BASELINE_ESTABLISHED=0
    # The count travels through a file rather than $(...): command substitution
    # runs crash_snapshot in a subshell, which discards the REMOTE_RC it sets and
    # reports a failed listing as exit 0. Reading it back here keeps both the
    # count and the real status.
    if crash_snapshot "${WORK_DIR}/crashes-${STEP_NUMBER}-before" >"${count_file}"; then
        read -r count <"${count_file}"
        CRASH_BASELINE_ESTABLISHED=1
        emit "  crash reports before this step: ${count}"
    else
        emit "  crash reports before this step: unavailable (exit ${REMOTE_RC}), so this step has no baseline and a report appearing during it cannot be told from one already on the device."
    fi
    rm -f "${count_file}"
}

record_step() {
    printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >>"${STEPS_FILE}"
}

# Records the step exactly once, on every path out of a step body including the
# failure paths, so no step can be missing from the summary.
#
# The fourth column is one of three values and both readers match all three by
# name, so a step the log prints as INVALID cannot be dropped from the summary by
# a matcher that happens to know only two of them:
#   no         every new report was read and none of them invalidates the step
#   crash      a DYLD or PAC_EXCEPTION report appeared during the step
#   unreadable the crash exclusion could not be established: a report's body could
#              not be read, the "after" listing failed, or the "before" listing
#              failed and the step therefore had no baseline
#
# "unreadable" covers a missing baseline as well as an unreadable file, so the
# three-value contract both readers rely on is unchanged. A step in that state
# is still INVALID for a distinct reason, and the log says which one.
step_end() {
    local count new_count invalid=no parsed=clean parsed_rc=0 detail=''
    local new_file="${WORK_DIR}/crashes-${STEP_NUMBER}-new"
    local count_file="${WORK_DIR}/crashes-${STEP_NUMBER}-after.count"
    # Read directly, not through $(...), so REMOTE_RC below is this listing's own
    # status and the log never pairs a failure with a stale exit code.
    if crash_snapshot "${WORK_DIR}/crashes-${STEP_NUMBER}-after" >"${count_file}"; then
        read -r count <"${count_file}"
        if [ "${CRASH_BASELINE_ESTABLISHED}" -eq 1 ]; then
            new_count=$(crash_new_list "${WORK_DIR}/crashes-${STEP_NUMBER}-before" \
                "${WORK_DIR}/crashes-${STEP_NUMBER}-after" "${new_file}")
            emit "  crash reports after this step: ${count}; new: ${new_count}"
        else
            # comm would read the absent "before" file as an empty one, and every
            # report already on the device would come out as new. A step with no
            # baseline establishes nothing about what appeared during it, so no
            # diff is taken and nothing on the device is called new.
            new_count=unavailable
            parsed=no_baseline
            : >"${new_file}"
            emit "  crash reports after this step: ${count}, but this step's before-listing failed, so there is no baseline: none of these ${count} report(s) can be called new or pre-existing."
        fi
    else
        # Without the "after" list there is no diff, so the step cannot show that
        # no DYLD or PAC_EXCEPTION report appeared. That is the same state as an
        # unreadable report, not a clean step.
        new_count=unavailable
        parsed=unreadable
        : >"${new_file}"
        emit "  crash reports after this step: unavailable (exit ${REMOTE_RC}), so this step's crash exclusion is not established."
    fi
    rm -f "${count_file}"
    if [ -s "${new_file}" ]; then
        describe_new_reports "${new_file}"
        parsed_rc=$?
        if [ "${parsed_rc}" -eq 1 ]; then
            parsed=crash
        elif [ "${parsed_rc}" -eq 2 ]; then
            parsed=unreadable
        fi
    fi
    case ${parsed} in
        crash) invalid=crash ;;
        unreadable)
            invalid=unreadable
            detail="a new report could not be read, or the CrashReporter listing failed"
            ;;
        no_baseline)
            invalid=unreadable
            detail="this step's before-listing failed, so there was no baseline to diff against and a report appearing during it cannot be told from one already on the device"
            BASELINE_MISSING_STEPS="${BASELINE_MISSING_STEPS}${BASELINE_MISSING_SEPARATOR}${STEP_NUMBER}"
            BASELINE_MISSING_SEPARATOR=','
            ;;
    esac
    case ${invalid} in
        crash)
            emit "  step ${STEP_NUMBER} is INVALID: a DYLD or PAC_EXCEPTION crash appeared during this step, so its reading cannot be attributed to how launchd resolved the path."
            ;;
        unreadable)
            emit "  step ${STEP_NUMBER} is INVALID: its crash exclusion is not established (${detail}), so a DYLD or PAC_EXCEPTION crash cannot be ruled out."
            ;;
    esac
    record_step "${STEP_NUMBER}" "${STEP_TITLE}" "${new_count}" "${invalid}"
}

skip_step() {
    step_begin "$1" "$2"
    emit "  skipped: $3"
    step_end
}

# ---------------------------------------------------------------------------
# Step 1: preconditions
# ---------------------------------------------------------------------------

# The health gate is SpringBoard's own job, not the gui/501 domain.
#
# On iOS 16.1 under Dopamine there is no gui/501 domain at all, and there is
# nothing wrong with the device when that is so: the graphics session lives in
# user/501. Measured 2026-11-25 20:41 CST on the healthy device (uptime 10 h,
# Safari 622 and Chrome 626 both started at boot, 23 WebKit helpers):
#
#   launchctl print gui/501                  -> rc=125, 0 lines, stderr empty
#   launchctl print gui/501  (under sudo)    -> rc=125, 0 lines, stderr empty
#   launchctl print system  | grep '^\s*\(gui\|user\)/'
#                                            -> user/501 only, no gui/501
#   launchctl print user/501                 -> rc=0, 2135 lines, active 573
#   launchctl print user/501 | grep -c 'com\.apple\.SpringBoard'
#                                            -> 1 (pid 345)
#   launchctl print user/501/com.apple.SpringBoard
#                                            -> rc=0, state = running
#
# Both readings of gui/501 are the same observation and neither means the device
# is broken: exit 125 with empty output. An earlier revision of this script
# aborted on exactly that, so it could never run on this device at all. The
# domain name is still read, because a log that omits it would leave a reader
# unable to tell this configuration from one where the domain does exist, but
# the reading is now a diagnostic and never a gate.
#
# The gate that does mean something is SpringBoard's job under user/501. It is
# the process that owns the graphics session, and reading its `state = running`
# is what separates a working device from a degraded one. A device whose
# SpringBoard job is missing or not running cannot start an application, so no
# per-pid domain would carry the endpoints and every reading after this point
# would be the environment's zero rather than launchd's answer.
read_gui_domain_facts() {
    remote 'launchctl print gui/501'
    GUI_RC=${REMOTE_RC}
    if [ "${REMOTE_RC}" -eq 255 ]; then
        die "ssh to ${SSH_TARGET} failed (exit 255): ${REMOTE_OUTPUT}"
    fi
    GUI_OUTPUT=${REMOTE_OUTPUT}
    GUI_LINES=0
    if [ -n "${REMOTE_OUTPUT}" ]; then
        GUI_LINES=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -c '' || true)
    fi
    case ${GUI_LINES} in
        ''|*[!0-9]*) GUI_LINES=0 ;;
    esac
}

# One reading, three facts: whether launchctl would describe the system domain at
# all, whether that domain lists user/501, and whether SpringBoard's job under it
# is running. `launchctl print user/501/com.apple.SpringBoard` is asked directly
# and its `state = running` is the gate, so a missing user domain, a missing job
# and a stopped job are three distinct reasons in the log rather than one.
SPRINGBOARD_STATE=''
SPRINGBOARD_RC=0

read_springboard_facts() {
    remote 'launchctl print user/501/com.apple.SpringBoard'
    SPRINGBOARD_RC=${REMOTE_RC}
    if [ "${REMOTE_RC}" -eq 255 ]; then
        die "ssh to ${SSH_TARGET} failed (exit 255): ${REMOTE_OUTPUT}"
    fi
    SPRINGBOARD_STATE=$(printf '%s\n' "${REMOTE_OUTPUT}" \
        | grep -E '^[[:space:]]+state = ' | head -n 1 \
        | sed -E 's/^[[:space:]]*state = //; s/[[:space:]]+$//')
    SPRINGBOARD_STATE=${SPRINGBOARD_STATE:-unknown}
}

graphics_session_healthy() {
    [ "${SPRINGBOARD_RC}" -eq 0 ] && [ "${SPRINGBOARD_STATE}" = running ]
}

abort_graphics_session_missing() {
    emit ""
    emit '需要先重启设备，图形会话缺失'
    emit "SpringBoard's job under user/501 is not running: launchctl exited ${SPRINGBOARD_RC} and reported state=${SPRINGBOARD_STATE}. SpringBoard owns the graphics session, so no application can start and every endpoint reading after this point would be the environment's zero rather than launchd's answer. Reboot the device, then run this script again. sbreload and launchctl kickstart system/com.apple.runningboardd were both observed failing to restore a lost graphics session, so a reboot is the only known fix."
    emit "  launchctl print gui/501, recorded as a diagnostic and not a gate: exit=${GUI_RC}, ${GUI_LINES} line(s). On iOS 16.1 under Dopamine this is empty with exit 125 on a healthy device, because the graphics session lives in user/501 and there is no gui/501 domain."
    if [ "${SPRINGBOARD_RC}" -ne 0 ]; then
        emit "  the SpringBoard command's output, which is where launchd explains the refusal: ${REMOTE_OUTPUT}"
    fi
    emit 'PROBE_VERDICT=ABORTED'
    emit 'PROBE_REASON=graphics_session_missing_reboot_required'
    exit 3
}

step_preconditions() {
    step_begin 1 'record the preconditions'
    # The gate already ran in main, before any device write; this only reports the
    # same reading, so the log keeps the fact without asking launchd again.
    emit "  launchctl print gui/501: exit=${GUI_RC}, ${GUI_LINES} line(s) (diagnostic, not a gate)"
    emit "  launchctl print user/501/com.apple.SpringBoard: exit=${SPRINGBOARD_RC}, state=${SPRINGBOARD_STATE} (this is the gate)"
    graphics_session_healthy || abort_graphics_session_missing

    run 'override directory before' "[ -d ${OVERRIDE_ROOT} ] && echo present || echo absent"
    OVERRIDE_PRESENT_BEFORE=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -E '^(present|absent)$' | tail -n 1)
    OVERRIDE_PRESENT_BEFORE=${OVERRIDE_PRESENT_BEFORE:-unknown}
    emit "  ${OVERRIDE_ROOT}: ${OVERRIDE_PRESENT_BEFORE}"

    run 'both scripts must exist or there is no rollback' "ls -l ${INSTALL_SCRIPT} ${UNINSTALL_SCRIPT}"
    run 'device uid' 'id -u'

    record_baseline_helpers

    if [ "${OVERRIDE_PRESENT_BEFORE}" = present ]; then
        emit '  Warning: the override directory was already present. That is not the state this experiment starts from, and step 2 will refuse to run.'
    fi
    step_end
}

# The helper population before the run. Step 5 subtracts this list by pid, so a
# helper launchd started for an earlier experiment cannot be counted as one this
# launch produced. Two ps spellings are tried because the ppid column is what the
# stale-helper cleanup filters on; whichever works is recorded, and a failure is
# logged rather than fatal, because the services-section reading does not need it.
record_baseline_helpers() {
    if ! process_table 'helper baseline' 1; then
        if process_table 'helper baseline without a ppid column' 0; then
            emit '  ps -Ao pid,ppid,comm was rejected; the baseline has no ppid column, so the stale-helper cleanup below cannot use its ppid filter.'
        else
            emit '  Error: neither ps spelling worked, so there is no baseline and a new helper cannot be told from a stale one. Step 5 will report that rather than guess.'
            return
        fi
    fi
    BASELINE_HELPERS_RECORDED=1
    cp "${WORK_DIR}/processes.txt" "${WORK_DIR}/ps-before.txt"
    awk '$3 ~ /\.xpc\/com\.apple\.WebKit\./ { print $1, $2, $3 }' "${WORK_DIR}/ps-before.txt" \
        >"${WORK_DIR}/helpers-baseline.txt"

    # The stale population is tallied here, from the baseline, because step 3
    # kills most of it and a tally taken afterwards would read zero.
    local pid ppid path kind
    while read -r pid ppid path; do
        [ -n "${path:-}" ] || continue
        kind=$(classify_helper_path "${path}")
        HELPER_STALE_TOTAL=$((HELPER_STALE_TOTAL + 1))
        case ${kind} in
            override) HELPER_STALE_OVERRIDE=$((HELPER_STALE_OVERRIDE + 1)) ;;
            jb_frameworks) HELPER_STALE_JB_FRAMEWORKS=$((HELPER_STALE_JB_FRAMEWORKS + 1)) ;;
            system) HELPER_STALE_SYSTEM=$((HELPER_STALE_SYSTEM + 1)) ;;
            *) HELPER_STALE_OTHER=$((HELPER_STALE_OTHER + 1)) ;;
        esac
        emit "  stale  ${kind}  pid=${pid} ppid=${ppid}  ${path}"
    done <"${WORK_DIR}/helpers-baseline.txt"

    emit "  WebKit helpers already running before the run: ${HELPER_STALE_TOTAL}"
    if [ "${HELPER_STALE_TOTAL}" -ge 1 ]; then
        emit '  Those are stale helpers from an earlier experiment. Step 3 kills the ones launchd owns (ppid 1), and step 5 counts only helpers absent from this list.'
    fi
}

# One process table in one shape: "pid ppid comm", with ppid "-" when the device
# rejected the ppid column. Every later reader therefore indexes the same fields.
process_table() {
    local purpose=$1
    local want_ppid=$2
    local command='ps -Ao pid,comm'
    if [ "${want_ppid}" -eq 1 ]; then
        command='ps -Ao pid,ppid,comm'
    fi
    run "${purpose}" "${command}"
    if [ "${RUN_RC}" -ne 0 ]; then
        return 1
    fi
    if [ "${want_ppid}" -eq 1 ]; then
        awk 'NF >= 3 { print $1, $2, $3 }' <<EOF >"${WORK_DIR}/processes.txt"
${REMOTE_OUTPUT}
EOF
    else
        awk 'NF >= 2 { print $1, "-", $2 }' <<EOF >"${WORK_DIR}/processes.txt"
${REMOTE_OUTPUT}
EOF
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Step 2: install
# ---------------------------------------------------------------------------

step_install() {
    step_begin 2 'install the override directory'
    if [ "${OVERRIDE_PRESENT_BEFORE}" = present ]; then
        emit '  refusing to install: the directory already exists, so this run would not measure the transition.'
        emit "  remove it first: sudo ${DEVICE_BASH} ${UNINSTALL_SCRIPT}"
        INSTALL_RC=skipped-preexisting
        step_end
        return 1
    fi
    run_sudo 'install the override directory' "${DEVICE_BASH} ${INSTALL_SCRIPT}"
    INSTALL_RC=${RUN_RC}
    printf '%s\n' "${REMOTE_OUTPUT}" >"${WORK_DIR}/install-output.txt"
    emit "  install exit: ${INSTALL_RC}"
    emit "  manifest lines the install script printed: $(grep -cE '^  (copied|unchanged|pruned) ' "${WORK_DIR}/install-output.txt" || true)"
    if [ "${INSTALL_RC}" -eq 0 ]; then
        OVERRIDE_INSTALLED=1
        run 'override directory after install' "[ -d ${OVERRIDE_ROOT} ] && echo present || echo absent"
        emit "  ${OVERRIDE_ROOT}: $(printf '%s\n' "${REMOTE_OUTPUT}" | grep -E '^(present|absent)$' | tail -n 1)"
        read_override_xpc_services
    fi
    step_end
    [ "${INSTALL_RC}" -eq 0 ]
}

# The install script's name does not say whether the result carries XPCServices:
# the device's /var/mobile copy copies each framework tree whole, while the repo's
# scripts/install-webkit-override.sh calls prune_xpc_services. Read it instead of
# assuming it, because the verdict narrative is different for the two shapes.
#
# Existence and count are two calls on purpose. A single
# `ls -1 dir 2>/dev/null | wc -l` prints a number for every input: the pipeline's
# status is wc's, which is always 0, and a denied or racing directory becomes 0.
# Zero is the positive claim "the install pruned XPCServices", so that form turns
# a failed read into an assertion. Here a failed existence check or a failed
# listing leaves the shape unknown instead.
#
# The existence check is three-way for the same reason. `[ -d path ]` is false
# both for a missing path and for one whose parent cannot be traversed, so a
# two-way check still reports a permission refusal as `absent`. `-x` on the
# containing directories is what separates the two, and a directory it cannot
# vouch for reports unreadable.
read_override_xpc_services() {
    OVERRIDE_XPC_SERVICES=unknown
    local presence listing
    run 'is WebKit.framework/XPCServices present in the installed override' \
        "p=${OVERRIDE_ROOT}/WebKit.framework/XPCServices
framework=${OVERRIDE_ROOT}/WebKit.framework
if [ -d \"\$p\" ]; then echo present
elif [ ! -e ${OVERRIDE_ROOT} ]; then echo unreadable
elif [ -x \"\$framework\" ]; then echo absent
elif [ -e \"\$framework\" ]; then echo unreadable
elif [ -x ${OVERRIDE_ROOT} ]; then echo absent
else echo unreadable; fi"
    presence=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -E '^(present|absent|unreadable)$' | tail -n 1)
    case ${presence:-none} in
        absent)
            OVERRIDE_XPC_SERVICES=absent
            ;;
        present)
            # Counted on this host from the listing itself, so REMOTE_RC is ls's
            # own status rather than wc's. A listing that failed reports nothing
            # and the shape stays unknown.
            run 'list the XPC bundles in the installed override' \
                "ls -1 ${OVERRIDE_ROOT}/WebKit.framework/XPCServices"
            if [ "${REMOTE_RC}" -eq 0 ]; then
                listing=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -cE '[^[:space:]]' || true)
                case ${listing} in
                    ''|*[!0-9]*) OVERRIDE_XPC_SERVICES=unknown ;;
                    # An existing but empty XPCServices directory gives launchd no
                    # bundle to resolve, which is the pruned configuration's shape
                    # in every respect the verdict turns on.
                    0) OVERRIDE_XPC_SERVICES=absent ;;
                    *) OVERRIDE_XPC_SERVICES="present (${listing} bundles)" ;;
                esac
            fi
            ;;
        *)
            # Either the framework directory exists but the check could not look
            # inside it, or the check itself did not answer. Neither presence nor
            # absence is known, so the shape is not claimed.
            OVERRIDE_XPC_SERVICES=unknown
            ;;
    esac
    emit "  WebKit.framework/XPCServices in the override: ${OVERRIDE_XPC_SERVICES}"
}

# ---------------------------------------------------------------------------
# Step 3: launch Safari
# ---------------------------------------------------------------------------

safari_pid_in() {
    awk '$3 ~ /[\/]MobileSafari$/ { print $1; exit }' "$1"
}

# Kill the WebKit helpers that were already running when the run started and
# that launchd owns (ppid 1). They are the leftovers of an earlier experiment:
# they hold pages and keep their services in the domain, so leaving them alive
# would mix stale helpers into the reading. ppid 1 is the filter that keeps the
# cleanup to launchd-spawned processes; a helper whose parent is a live app is
# that app's, and killing it would be a change this experiment did not ask for.
# One kill -9 per pid, because the device has no pkill. The list arrives on fd 3
# so that the ssh call inside the loop cannot consume it.
kill_stale_helpers() {
    local pid ppid path
    HELPER_KILLED=0
    [ -s "${WORK_DIR}/ps-before.txt" ] || return 0
    while read -r pid ppid path <&3; do
        case ${path} in
            *'.xpc/com.apple.WebKit.'*) ;;
            *) continue ;;
        esac
        if [ "${ppid}" != 1 ]; then
            emit "    kept ${pid} (ppid ${ppid}): not owned by launchd"
            continue
        fi
        remote "kill -9 ${pid}"
        emit "    kill -9 ${pid} (${path}) exit=${REMOTE_RC}"
        case ${REMOTE_RC} in
            0|1) HELPER_KILLED=$((HELPER_KILLED + 1)) ;;
            *) emit "    Error: kill -9 ${pid} exited ${REMOTE_RC}" ;;
        esac
    done 3<"${WORK_DIR}/ps-before.txt"
    emit "  stale helpers signalled: ${HELPER_KILLED}"
}

step_launch_safari() {
    step_begin 3 'kill Safari, launch it with a URL, take its pid'
    run 'kill Safari' 'killall -9 MobileSafari'
    case ${RUN_RC} in
        0) emit '  killall exit 0: a Safari was running and is gone.' ;;
        1) emit '  killall exit 1: there was no Safari to kill, which is a fine starting point.' ;;
        *)
            emit "  Error: killall exited ${RUN_RC}, so the old Safari may still be running and any launch below would measure it."
            emit "  ${REMOTE_OUTPUT}"
            step_end
            return 1
            ;;
    esac

    emit '  stale helper cleanup, so only helpers from the new launch are counted:'
    kill_stale_helpers
    if process_table 'processes after the stale-helper cleanup' 0; then
        emit '  MobileSafari processes left after killall:'
        emit "$(awk '$3 ~ /[\/]MobileSafari$/ { print "    " $0 }' "${WORK_DIR}/processes.txt")"
        local survivors
        survivors=$(awk '$3 ~ /[\/]MobileSafari$/' "${WORK_DIR}/processes.txt" | grep -c '' || true)
        survivors=${survivors:-0}
        if [ "${survivors}" -gt 0 ]; then
            emit '  Error: Safari survived killall -9, so any launch below would measure the old process.'
            step_end
            return 1
        fi
    else
        emit '  Warning: could not re-read the process table after the cleanup; the survivors check is skipped.'
    fi

    local attempt=0
    local command
    for command in "${SAFARI_LAUNCH_ATTEMPTS[@]}"; do
        attempt=$((attempt + 1))
        emit ""
        emit "  launch attempt ${attempt}: ${command}"
        remote "${command}"
        emit "    exit=${REMOTE_RC}"
        emit "${REMOTE_OUTPUT}"
        sleep "${WAIT_SECONDS}"
        process_table "processes ${WAIT_SECONDS}s after launch attempt ${attempt}" 1 \
            || process_table "processes ${WAIT_SECONDS}s after launch attempt ${attempt}" 0 \
            || continue
        cp "${WORK_DIR}/processes.txt" "${WORK_DIR}/ps-launch-${attempt}.txt"
        SAFARI_PID=$(safari_pid_in "${WORK_DIR}/ps-launch-${attempt}.txt")
        if [ -n "${SAFARI_PID}" ]; then
            SAFARI_LAUNCH_MODE="attempt ${attempt}: ${command}"
            break
        fi
        emit "    no MobileSafari pid ${WAIT_SECONDS}s after this attempt"
    done

    if [ -z "${SAFARI_PID}" ]; then
        emit '  Error: Safari did not start, so there is no per-pid domain to read. This run concludes UNKNOWN and the device is rolled back below.'
        emit '  If the reading is needed, start Safari by hand, wait for a page, and run the probe again while it is in the foreground.'
        step_end
        return 1
    fi
    emit "  Safari pid: ${SAFARI_PID} via ${SAFARI_LAUNCH_MODE}"
    emit '  Chrome processes visible in the same sample:'
    emit "$(awk '$3 ~ /Chrome/ { print "    " $0 }' "${WORK_DIR}/ps-launch-${attempt}.txt")"
    step_end
    return 0
}

# ---------------------------------------------------------------------------
# Step 4: the per-pid services section
# ---------------------------------------------------------------------------

extract_services_block() {
    awk '
        /^[[:space:]]*services = \{/ { inside = 1 }
        inside { print }
        inside && /^\t\}[[:space:]]*$/ { exit }
    ' "$1"
}

# Counts whole-token occurrences of one service name in the block. The
# neighbours must not be name characters, so WebContent does not match inside
# WebContent.CaptivePortal and a name is not matched as the tail of a longer
# identifier. This is how launchctl spells the names whether they appear as
# `"name" = {` or as a bare listing.
count_service() {
    awk -v name="$2" '
        BEGIN { length_of_name = length(name) }
        {
            # skip is the number of characters already consumed, so offset stays
            # an absolute position in the line and several occurrences on one
            # line are all counted. The character before offset 1 is the start of
            # the line, which is a boundary: substr($0, 0, 1) would return the
            # first character instead, so a name at the head of a line would
            # never count.
            skip = 0
            while ((position = index(substr($0, skip + 1), name)) > 0) {
                offset = skip + position
                before = (offset > 1) ? substr($0, offset - 1, 1) : ""
                after = substr($0, offset + length_of_name, 1)
                if (before !~ /[A-Za-z0-9_.-]/ && after !~ /[A-Za-z0-9_.-]/) {
                    count += 1
                }
                skip = offset + length_of_name - 1
            }
        }
        END { print count + 0 }
    ' "$1"
}

# A proxy for the size of the section, comparable with the "six entries" reading
# in M63 appendix A. The verbatim block in the log is the authority.
count_distinct_names() {
    awk '{ while (match($0, /[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+/)) {
               print substr($0, RSTART, RLENGTH)
               $0 = substr($0, RSTART + RLENGTH)
           } }' "$1" | sort -u | wc -l | tr -d '[:space:]'
}

log_launchctl_output() {
    local print_file=$1
    local attempt=$2
    emit ""
    emit "\$ launchctl print pid/${SAFARI_PID}   [${attempt}]"
    emit "$(cat "${print_file}")"
}

read_services_section() {
    local print_file=$1
    remote "launchctl print pid/${SAFARI_PID}"
    printf '%s\n' "${REMOTE_OUTPUT}" >"${print_file}"
    log_launchctl_output "${print_file}" "exit=${REMOTE_RC}"
    if grep -q 'services = {' "${print_file}"; then
        return 0
    fi
    emit '  the unprivileged print had no services section; retrying under sudo'
    remote_sudo "launchctl print pid/${SAFARI_PID}"
    printf '%s\n' "${REMOTE_OUTPUT}" >"${print_file}"
    log_launchctl_output "${print_file}" "sudo retry, exit=${REMOTE_RC}"
    grep -q 'services = {' "${print_file}"
}

report_service() {
    if [ "$2" -ge 1 ]; then
        emit "    present  x$2  $1"
    else
        emit "    absent         $1"
    fi
}

# The line count the older documents published as "8", and the reason it is not a
# constant. launchctl lists one line per registered instance, and WebContent and
# Networking also register a UUID-suffixed entry per live page, so a naive
# `grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'` over the section
# grows and shrinks with the page count. Measured 2026-11-25 20:41 CST on Chrome
# pid 626: 9 lines, of which 5 carried a UUID suffix; the bounded count of the
# same section was 4 (one per base name) and the harness documents still said 8.
#
# The script's own count_service is already bounded and never used the line
# count, so the two can disagree by any number of instances. Both are printed so
# a new log can be compared with an old reading: the bounded count is the
# criterion, the line count is what the old documents called the baseline.
count_webkit_lines() {
    grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)' "$1" || true
}

count_webkit_instances() {
    grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)\.[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}' "$1" || true
}

step_read_endpoints() {
    step_begin 4 'read the Safari per-pid services section'
    if [ -z "${SAFARI_PID}" ]; then
        emit '  skipped: there is no Safari pid from step 3.'
        step_end
        return 1
    fi
    local print_file="${WORK_DIR}/launchctl-print-${SAFARI_PID}.txt"
    if ! read_services_section "${print_file}"; then
        emit '  Error: launchctl print produced no services section. Counting endpoints from this would be a guess, so the run cannot conclude A or B.'
        step_end
        return 1
    fi
    local block="${WORK_DIR}/services-${SAFARI_PID}.txt"
    extract_services_block "${print_file}" >"${block}"
    emit ""
    emit '  services section, verbatim:'
    emit "$(cat "${block}")"

    SVC_WEBCONTENT=$(count_service "${block}" com.apple.WebKit.WebContent)
    SVC_GPU=$(count_service "${block}" com.apple.WebKit.GPU)
    SVC_NETWORKING=$(count_service "${block}" com.apple.WebKit.Networking)
    SVC_CAPTIVEPORTAL=$(count_service "${block}" com.apple.WebKit.WebContent.CaptivePortal)
    SVC_BRAILLE=$(count_service "${block}" com.apple.accessibility.BrailleTranslation.BrailleTranslationService)
    SVC_MTL=$(count_service "${block}" com.apple.MTLCompilerService)
    SVC_DISTINCT=$(count_distinct_names "${block}")

    emit ""
    emit '  the four WebKit services:'
    report_service com.apple.WebKit.WebContent "${SVC_WEBCONTENT}"
    report_service com.apple.WebKit.GPU "${SVC_GPU}"
    report_service com.apple.WebKit.Networking "${SVC_NETWORKING}"
    report_service com.apple.WebKit.WebContent.CaptivePortal "${SVC_CAPTIVEPORTAL}"
    emit '  the two services section 4 (B) says survive when the WebKit endpoints are gone:'
    report_service com.apple.accessibility.BrailleTranslation.BrailleTranslationService "${SVC_BRAILLE}"
    report_service com.apple.MTLCompilerService "${SVC_MTL}"
    emit "  distinct dotted names in the section: ${SVC_DISTINCT} (M63 appendix A read six with no override directory)"
    emit "  naive line count, the reading older documents called the baseline: $(count_webkit_lines "${block}"); of those, UUID-instance lines: $(count_webkit_instances "${block}")"
    emit "  the criterion is the four bounded counts above, not this line count: it grows with the number of live pages, so it is not a constant and the old baseline of 8 was one sample of it"
    step_end
    return 0
}

# ---------------------------------------------------------------------------
# Step 5: helper executable paths and the framework each helper loaded
# ---------------------------------------------------------------------------

# Classifies a path by which framework tree it sits in. Used for the executable
# path from comm and for the WebKit framework path from lldb. /var/jb resolves
# through /private/preboot/<uuid>/dopamine-*/procursus, and comm and lldb both
# report the resolved form when the process started through it, so the two
# spellings are classified as the same directory.
classify_helper_path() {
    case $1 in
        */procursus/Library/WebKitOverride/*|/var/jb/Library/WebKitOverride/*)
            printf 'override' ;;
        */procursus/Library/Frameworks/*|/var/jb/Library/Frameworks/*)
            printf 'jb_frameworks' ;;
        /System/Library/Frameworks/*)
            printf 'system' ;;
        *)
            printf 'other' ;;
    esac
}

# comm names each helper's executable, which lands under the system .xpc bundle
# when the override carries no XPCServices and under the override .xpc bundle
# when it does; OVERRIDE_XPC_SERVICES records which shape this run installed.
# Which WebKit framework that helper actually loaded is a separate fact and only
# lldb's image list reports it. The attach needs root, and the ObjC expression
# parser on this device cannot install its checkers, so `image list -o -f` is the
# whole vocabulary used here.
#
# Two same-named WebKit images in one process is normal here (outer AGENTS.md,
# 设备 section): the system copy and the override copy both stay loaded. So every
# WebKit.framework/WebKit line is collected and classified, not just the first
# one. A helper counts as override-backed when any of its paths is, and the log
# carries all of them, because picking one by line order would let lldb's
# ordering decide the verdict.
#
# The pid list arrives on fd 3 rather than stdin: a command run inside the loop
# must not be able to consume the list, and keeping the list off stdin makes that
# true no matter which accessor the body grows later.
record_helper_framework_paths() {
    [ "${HELPER_NEW_TOTAL}" -ge 1 ] || return 0
    HELPER_FRAMEWORK_PATHS_RECORDED=0
    local pid exec_path
    while read -r pid exec_path <&3; do
        [ -n "${pid}" ] || continue
        remote_sudo "lldb -b -o 'process attach --pid ${pid}' -o 'image list -o -f' -o detach -o quit 2>/dev/null"
        emit ""
        emit "\$ sudo lldb ... image list -o -f   [helper ${pid}, exit=${REMOTE_RC}]"
        printf '%s\n' "${REMOTE_OUTPUT}" >"${WORK_DIR}/lldb-${pid}.txt"
        if [ "${REMOTE_RC}" -ne 0 ]; then
            emit "    Error: lldb could not attach to ${pid}; the framework this helper loaded stays unknown."
            HELPER_FRAMEWORK_UNKNOWN=$((HELPER_FRAMEWORK_UNKNOWN + 1))
            continue
        fi
        # Each line of image list is "<head> 0x<addr> <path>" or "[ n] <uuid>
        # 0x<addr> <path>"; the path is always the last field, and may itself
        # contain spaces.
        local framework_paths
        framework_paths="${WORK_DIR}/lldb-${pid}-paths.txt"
        awk '
            /WebKit\.framework\/WebKit$/ {
                path = ""
                for (field = 1; field <= NF; field += 1) {
                    if ($field ~ /WebKit\.framework\/WebKit$/) { path = substr($0, index($0, $field)) }
                }
                if (path != "" && !seen[path]++) { print path }
            }
        ' "${WORK_DIR}/lldb-${pid}.txt" >"${framework_paths}"
        if [ ! -s "${framework_paths}" ]; then
            emit "    no WebKit.framework/WebKit line in the image list; the framework this helper loaded stays unknown."
            emit "$(grep -E 'WebKit|error|warning' "${WORK_DIR}/lldb-${pid}.txt" | head -n 10)"
            HELPER_FRAMEWORK_UNKNOWN=$((HELPER_FRAMEWORK_UNKNOWN + 1))
            continue
        fi
        HELPER_FRAMEWORK_PATHS_RECORDED=1
        local framework_path framework_kind helper_kinds='' jb_framework_seen=no
        while read -r framework_path <&4; do
            framework_kind=$(classify_helper_path "${framework_path}")
            case ${framework_kind} in
                override)
                    HELPER_FRAMEWORK_OVERRIDE=$((HELPER_FRAMEWORK_OVERRIDE + 1))
                    jb_framework_seen=yes
                    ;;
                jb_frameworks)
                    HELPER_FRAMEWORK_JB=$((HELPER_FRAMEWORK_JB + 1))
                    jb_framework_seen=yes
                    ;;
                system) HELPER_FRAMEWORK_SYSTEM=$((HELPER_FRAMEWORK_SYSTEM + 1)) ;;
                *)
                    HELPER_FRAMEWORK_UNKNOWN=$((HELPER_FRAMEWORK_UNKNOWN + 1))
                    framework_kind=other
                    ;;
            esac
            emit "    ${framework_kind}  ${framework_path}"
            helper_kinds="${helper_kinds} ${framework_kind}"
        done 4<"${framework_paths}"
        # Which mechanism put a /var/jb framework in this helper. The helper's own
        # executable decides it: under the override tree means launchd started the
        # override's .xpc bundle, under /System means launchd started the indexed
        # stub and the dyld environment handed the framework over. Both load a
        # /var/jb framework, and only the first answers M63's question.
        if [ "${jb_framework_seen}" = yes ]; then
            case $(classify_helper_path "${exec_path}") in
                override) HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC=$((HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC + 1)) ;;
                *) HELPER_JB_FRAMEWORK_THROUGH_STUB=$((HELPER_JB_FRAMEWORK_THROUGH_STUB + 1)) ;;
            esac
        fi
        emit "    helper ${pid} classified by the above:${helper_kinds:- unknown}"
    done 3<"${WORK_DIR}/helpers-new-pids.txt"
}

# Tally counts the helpers this launch produced. The baseline pid list from
# step 1 is subtracted, so a stale helper from an earlier experiment cannot be
# read as evidence about what launchd did with the override directory. Both
# populations are printed, because the stale count is itself a device fact worth
# seeing, and a reader must be able to check the subtraction.
step_tally_helpers() {
    step_begin 5 'tally the WebKit helper executable paths'
    if ! process_table 'processes for the helper tally' 1; then
        if ! process_table 'processes for the helper tally without a ppid column' 0; then
            emit '  Error: neither ps spelling worked, so the helper paths could not be read.'
            step_end
            return 1
        fi
    fi
    cp "${WORK_DIR}/processes.txt" "${WORK_DIR}/ps-helpers.txt"
    awk '$3 ~ /\.xpc\/com\.apple\.WebKit\./ { print $1, $2, $3 }' "${WORK_DIR}/ps-helpers.txt" \
        >"${WORK_DIR}/helpers.txt"
    # The pid and the executable path together: the pid drives the lldb attach,
    # and the path, which is where launchd started the helper from, is what
    # separates the two mechanisms A can describe.
    : >"${WORK_DIR}/helpers-new-pids.txt"

    local pid ppid path kind in_baseline
    while read -r pid ppid path <&3; do
        [ -n "${path:-}" ] || continue
        kind=$(classify_helper_path "${path}")
        # Stale is decided by pid membership in the step 1 baseline. A stale
        # helper that survived the step 3 kill is reported but not counted
        # again: its contribution is already in the step 1 tally.
        in_baseline=$(awk -v wanted="${pid}" '$1 == wanted { found = 1 } END { print found ? "stale" : "new" }' \
            "${WORK_DIR}/helpers-baseline.txt")
        if [ "${in_baseline}" = stale ] || [ "${BASELINE_HELPERS_RECORDED}" -eq 0 ]; then
            in_baseline=stale-survivor
            emit "    ${in_baseline}  ${kind}  pid=${pid} ppid=${ppid}  ${path}"
            continue
        fi
        HELPER_NEW_TOTAL=$((HELPER_NEW_TOTAL + 1))
        printf '%s %s\n' "${pid}" "${path}" >>"${WORK_DIR}/helpers-new-pids.txt"
        case ${kind} in
            override) HELPER_NEW_OVERRIDE=$((HELPER_NEW_OVERRIDE + 1)) ;;
            jb_frameworks) HELPER_NEW_JB_FRAMEWORKS=$((HELPER_NEW_JB_FRAMEWORKS + 1)) ;;
            system) HELPER_NEW_SYSTEM=$((HELPER_NEW_SYSTEM + 1)) ;;
            *) HELPER_NEW_OTHER=$((HELPER_NEW_OTHER + 1)) ;;
        esac
        emit "    new  ${kind}  pid=${pid} ppid=${ppid}  ${path}"
    done 3<"${WORK_DIR}/helpers.txt"

    if [ "${BASELINE_HELPERS_RECORDED}" -eq 0 ]; then
        emit '  Warning: there was no baseline, so every helper alive now is treated as stale and none as new. The helper half of the verdict is therefore unusable.'
    fi
    emit ""
    emit '  helper executables from this launch, classified by comm (the .xpc bundle):'
    emit "    ${OVERRIDE_ROOT}: ${HELPER_NEW_OVERRIDE}"
    emit "    /var/jb/Library/Frameworks, also spelled /private/preboot/.../procursus/Library/Frameworks: ${HELPER_NEW_JB_FRAMEWORKS}"
    emit "    /System/Library/Frameworks: ${HELPER_NEW_SYSTEM}"
    emit "    other: ${HELPER_NEW_OTHER}"
    emit "    total: ${HELPER_NEW_TOTAL}"
    emit '  stale helpers from before the run, tallied at step 1 and excluded from the verdict:'
    emit "    override: ${HELPER_STALE_OVERRIDE}"
    emit "    /var/jb/Library/Frameworks: ${HELPER_STALE_JB_FRAMEWORKS}"
    emit "    /System/Library/Frameworks: ${HELPER_STALE_SYSTEM}"
    emit "    other: ${HELPER_STALE_OTHER}"
    emit "    total: ${HELPER_STALE_TOTAL}"
    emit "    signalled with kill -9 in step 3: ${HELPER_KILLED}"

    # The comm classification alone cannot answer the question the verdict asks:
    # comm gives the executable path, not the framework the process loaded. So
    # each new helper is attached once and its WebKit framework path is read
    # from lldb.
    emit ""
    emit '  WebKit framework each new helper loaded (lldb image list):'
    if [ "${HELPER_NEW_TOTAL}" -eq 0 ]; then
        emit '    no new helper, so there is nothing to attach to.'
    else
        record_helper_framework_paths
        emit ""
        emit "    ${OVERRIDE_ROOT}: ${HELPER_FRAMEWORK_OVERRIDE}"
        emit "    /var/jb/Library/Frameworks: ${HELPER_FRAMEWORK_JB}"
        emit "    /System/Library/Frameworks: ${HELPER_FRAMEWORK_SYSTEM}"
        emit "    unknown: ${HELPER_FRAMEWORK_UNKNOWN}"
        if [ "${HELPER_FRAMEWORK_PATHS_RECORDED}" -eq 0 ]; then
            emit '    Warning: no helper yielded a framework path, so the verdict can only use "the four services are present", not "a helper loaded the overridden framework". The report must say so.'
        fi
    fi
    step_end
}

# ---------------------------------------------------------------------------
# Step 6: uninstall
# ---------------------------------------------------------------------------

step_uninstall() {
    step_begin 6 'remove the override directory'
    run_sudo 'remove the override directory' "${DEVICE_BASH} ${UNINSTALL_SCRIPT}"
    UNINSTALL_RC=${RUN_RC}
    emit "  uninstall exit: ${UNINSTALL_RC}"
    run 'override directory after uninstall' "[ -d ${OVERRIDE_ROOT} ] && echo present || echo absent"
    OVERRIDE_PRESENT_AFTER=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -E '^(present|absent)$' | tail -n 1)
    OVERRIDE_PRESENT_AFTER=${OVERRIDE_PRESENT_AFTER:-unknown}
    emit "  ${OVERRIDE_ROOT}: ${OVERRIDE_PRESENT_AFTER}"
    if [ "${OVERRIDE_PRESENT_AFTER}" = absent ]; then
        UNINSTALL_CONFIRMED=1
        OVERRIDE_INSTALLED=0
        emit '  rollback confirmed: the directory is gone and dyld falls back to the shared cache for processes started from now on.'
    else
        emit '  Error: the override directory is still present. Remove it by hand before any further experiment.'
    fi
    process_table 'processes after the uninstall' 0 || emit '  Warning: could not re-read the process table after the uninstall.'
    step_end
    [ "${OVERRIDE_PRESENT_AFTER}" = absent ]
}

# ---------------------------------------------------------------------------
# Step 7: summary and verdict
# ---------------------------------------------------------------------------

# Both selectors match "crash" and "unreadable", the two values step_end writes
# for a step whose crash exclusion did not hold. "no" is the only clean value, and
# it is listed explicitly so a fourth value can never be silently ignored: a step
# the log prints as INVALID must reach both the summary and the verdict gate.
invalid_steps() {
    awk -F'\t' '$4 == "crash" || $4 == "unreadable" { printf "%s%s", separator, $1; separator = "," }' "${STEPS_FILE}"
}

# Answers crash, unestablished or none. The two failures are kept apart because
# they mean different things to a reader of the log: crash says a DYLD or
# PAC_EXCEPTION report really appeared, unestablished says the step could not
# show that none did. Collapsing them would put "a crash appeared" into the
# PROBE_REASON of a run that may have had no crash at all.
invalid_in_reading_window() {
    awk -F'\t' '
        ($1 == 3 || $1 == 4 || $1 == 5) && $4 == "crash" { crash = 1 }
        ($1 == 3 || $1 == 4 || $1 == 5) && $4 == "unreadable" { unestablished = 1 }
        END {
            if (crash) print "crash"
            else if (unestablished) print "unestablished"
            else print "none"
        }
    ' "${STEPS_FILE}"
}

webkit_service_count() {
    printf '%s' $((SVC_WEBCONTENT + SVC_GPU + SVC_NETWORKING + SVC_CAPTIVEPORTAL))
}

# Answers 0 or 1: did one of the reading steps lose its baseline? A reading step
# with no before-listing is INVALID for a reason the crash gate must not describe
# as a crash, and the reason it prints has to say which failure it was. The list
# holds only the steps whose before-listing failed, so the two facts cannot
# drift apart.
baseline_missing_in_reading_window() {
    local step
    [ -n "${BASELINE_MISSING_STEPS}" ] || return 1
    for step in ${BASELINE_MISSING_STEPS//,/ }; do
        case ${step} in
            3|4|5) return 0 ;;
        esac
    done
    return 1
}

# Which /var/jb framework the new helpers loaded. The executable path cannot
# answer this on its own, so this counts the lldb framework paths only.
new_helper_framework_jb_count() {
    printf '%s' $((HELPER_FRAMEWORK_OVERRIDE + HELPER_FRAMEWORK_JB))
}

# The three verdicts, exactly as M63 and this mission define them:
#   A  all four WebKit services are in the Safari section, and at least one new
#      helper loaded a WebKit.framework from a /var/jb path
#   B  at most two of the four remain, no DYLD or PAC_EXCEPTION report appeared
#      in the reading steps, and every new helper whose framework was read loaded
#      the system copy
#   UNKNOWN  everything else
#
# The helper reasons are separate on purpose. No new helper means the launch did
# not produce one, which says nothing about paths; every new helper loading the
# system framework is the positive evidence for (B). Collapsing them would let a
# launch that never spawned anything read as evidence about launchd's path rules.
#
# A needs the framework path, not the executable path: the two point into
# different trees here, and only the framework path says whether the override
# took effect in the helper. When no framework path could be read, A is not
# reachable, because "four services are present" alone is also what a run with a
# partially broken override produces.
#
# B needs the same evidence as A, for the mirror reason: "no new helper loaded a
# /var/jb framework" cannot be concluded from a run where no helper's framework
# was read. Without the guard, a run whose lldb attaches all failed declares B on
# zero evidence -- and a run that would have shown /var/jb frameworks reads as
# clean B, which is the experiment's answer inverted rather than merely missing.
decide_verdict() {
    local webkit_count=$1
    if [ -z "${SAFARI_PID}" ]; then
        VERDICT=UNKNOWN
        PROBE_REASON=safari_not_running
        return
    fi
    case $(invalid_in_reading_window) in
        crash)
            VERDICT=UNKNOWN
            PROBE_REASON=dyld_or_pac_crash_in_reading_window
            return
            ;;
        unestablished)
            VERDICT=UNKNOWN
            # Same state, same exit, same "unestablished" branch: only the reason
            # differs, so a reader can tell a baseline that never existed from a
            # report whose body could not be read.
            if baseline_missing_in_reading_window; then
                PROBE_REASON=crash_baseline_missing_in_reading_window
            else
                PROBE_REASON=crash_exclusion_not_established_in_reading_window
            fi
            return
            ;;
    esac
    if [ "${SVC_DISTINCT}" -eq 0 ] && [ "${webkit_count}" -eq 0 ]; then
        VERDICT=UNKNOWN
        PROBE_REASON=services_section_empty
        return
    fi
    if [ "${HELPER_NEW_TOTAL}" -eq 0 ]; then
        VERDICT=UNKNOWN
        PROBE_REASON=no_new_helper_after_launch
        return
    fi
    local jb_frameworks
    jb_frameworks=$(new_helper_framework_jb_count)
    case ${webkit_count} in
        4)
            if [ "${HELPER_FRAMEWORK_PATHS_RECORDED}" -eq 0 ]; then
                VERDICT=UNKNOWN
                PROBE_REASON=four_services_present_but_no_helper_framework_path_read
            elif [ "${jb_frameworks}" -ge 1 ]; then
                VERDICT=A
                if [ "${HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC}" -ge 1 ]; then
                    PROBE_REASON=all_four_services_present_and_a_helper_started_from_the_override_xpc_loaded_a_var_jb_framework
                elif [ "${HELPER_FRAMEWORK_OVERRIDE}" -ge 1 ]; then
                    PROBE_REASON=all_four_services_present_and_a_helper_started_by_the_system_stub_loaded_the_override_framework
                else
                    PROBE_REASON=all_four_services_present_and_a_helper_loaded_the_var_jb_frameworks_copy
                fi
            else
                VERDICT=UNKNOWN
                PROBE_REASON=four_services_but_every_new_helper_loaded_the_system_framework
            fi
            ;;
        0|1|2)
            if [ "${HELPER_FRAMEWORK_PATHS_RECORDED}" -eq 0 ]; then
                VERDICT=UNKNOWN
                PROBE_REASON=services_gone_but_no_helper_framework_path_read
            elif [ "${jb_frameworks}" -eq 0 ]; then
                VERDICT=B
                PROBE_REASON=services_gone_or_at_most_two_and_no_new_helper_loaded_a_var_jb_framework
            else
                VERDICT=UNKNOWN
                PROBE_REASON=services_gone_but_a_new_helper_loaded_a_var_jb_framework
            fi
            ;;
        *)
            VERDICT=UNKNOWN
            PROBE_REASON="intermediate_service_count_${webkit_count}_of_4"
            ;;
    esac
}

step_summary() {
    step_begin 7 'summary'
    # Step 7 is ended before anything is printed, so its own crash diff is
    # recorded and the table below can list all seven rows, including this one.
    step_end

    local webkit_count invalid
    webkit_count=$(webkit_service_count)
    decide_verdict "${webkit_count}"
    invalid=$(invalid_steps)
    invalid=${invalid:-none}

    emit ""
    emit 'Steps, with the crash reports each one added:'
    emit "$(awk -F'\t' '{
        printf "  step %s  new=%-12s invalid=%-9s %s\n", $1, $3, $4, $2
    }' "${STEPS_FILE}")"

    emit ""
    emit 'Facts:'
    emit "  gui/501 at step 1: exit=${GUI_RC}, ${GUI_LINES} line(s) (diagnostic, not a gate)"
    emit "  SpringBoard under user/501 at step 1: exit=${SPRINGBOARD_RC}, state=${SPRINGBOARD_STATE} (this is the gate)"
    emit "  override directory before: ${OVERRIDE_PRESENT_BEFORE}; install exit: ${INSTALL_RC}; XPCServices in the override: ${OVERRIDE_XPC_SERVICES}"
    emit "  Safari pid: ${SAFARI_PID:-none}; launch: ${SAFARI_LAUNCH_MODE}"
    emit "  WebKit services present: ${webkit_count} of 4 (WebContent=${SVC_WEBCONTENT}, GPU=${SVC_GPU}, Networking=${SVC_NETWORKING}, CaptivePortal=${SVC_CAPTIVEPORTAL})"
    emit "  BrailleTranslation=${SVC_BRAILLE}, MTLCompilerService=${SVC_MTL}, distinct dotted names=${SVC_DISTINCT}"
    emit "  new helpers from this launch: override=${HELPER_NEW_OVERRIDE}, /var/jb Frameworks=${HELPER_NEW_JB_FRAMEWORKS}, system=${HELPER_NEW_SYSTEM}, other=${HELPER_NEW_OTHER}, total=${HELPER_NEW_TOTAL}"
    emit "  stale helpers from before the run, excluded from the verdict: override=${HELPER_STALE_OVERRIDE}, /var/jb Frameworks=${HELPER_STALE_JB_FRAMEWORKS}, system=${HELPER_STALE_SYSTEM}, other=${HELPER_STALE_OTHER}, total=${HELPER_STALE_TOTAL}"
    emit "  stale helpers signalled with kill -9 in step 3: ${HELPER_KILLED}"
    emit "  WebKit framework loaded by the new helpers: override=${HELPER_FRAMEWORK_OVERRIDE}, /var/jb Frameworks=${HELPER_FRAMEWORK_JB}, system=${HELPER_FRAMEWORK_SYSTEM}, unknown=${HELPER_FRAMEWORK_UNKNOWN}"
    emit "  of the helpers that loaded a /var/jb framework: started from the override .xpc=${HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC}, started by the system stub and handed the framework=${HELPER_JB_FRAMEWORK_THROUGH_STUB}"
    emit "  uninstall exit: ${UNINSTALL_RC}; override directory after: ${OVERRIDE_PRESENT_AFTER}"
    emit "  steps that could not rule out a crash (a DYLD or PAC_EXCEPTION report appeared, or the exclusion could not be established): ${invalid}"

    emit ""
    case ${OVERRIDE_XPC_SERVICES} in
        absent)
            emit 'Comparison. build-state.md:506 is this configuration: "再加只含 WebKit 二进制的'
            emit 'WebKit.framework", endpoints 0, spawn 0, lookup failures 265. This run measured no'
            emit 'WebKit.framework/XPCServices in the override, so it installs the Info.plist and the'
            emit 'binary and nothing else. A reading of 0 endpoints here is therefore a reproduction of'
            emit 'a known result, and only a divergence from that row is new information.'
            emit ''
            emit 'What A can and cannot mean here. With XPCServices absent from the override, launchd'
            emit 'has no override .xpc bundle to resolve, so A cannot come from launchd scanning the override tree.'
            emit 'The only route left is the system stub plus the dyld framework handover: launchd'
            emit 'starts the indexed /System stub, and the dyld environment the UI process carries'
            emit 'loads the override WebKit into it. PROBE_REASON names which of the two happened for'
            emit 'this run, and the two counters above say how many helpers each.'
            emit 'Separating them definitively needs an unpruned run; docs/ios16-branch-a-deployment-path.md'
            emit 'section 第 2 步 describes one. Existing partial (A) evidence for that configuration'
            emit 'is at build-state.md:333-347, where launchd launched GPU from the override .xpc.'
            ;;
        present*)
            emit 'Comparison. This run is NOT the build-state.md:506 configuration. That row is the'
            emit 'pruned shape (Info.plist plus the WebKit binary, no XPCServices), while this run'
            emit 'measured WebKit.framework/XPCServices inside the override. A 0-endpoint reading here'
            emit 'is a new observation about the unpruned shape rather than a reproduction, and it is'
            emit 'directly comparable with build-state.md:333-347 instead.'
            emit ''
            emit 'What A can mean here. With XPCServices present in the override, both routes are live'
            emit 'and the two counters above separate them: a helper whose own executable (comm) is'
            emit 'under the override tree was started by launchd from the override .xpc, which is (A)'
            emit 'directly; a helper that is the /System stub but loaded the override framework came'
            emit 'through the dyld handover, which does not answer whether launchd scans paths it has'
            emit 'not indexed. PROBE_REASON names which one this run actually saw.'
            ;;
        *)
            emit 'Comparison withheld: the run could not establish whether the installed override'
            emit 'carries WebKit.framework/XPCServices, so it cannot say which configuration'
            emit 'build-state.md:506 corresponds to. Read the step 2 lines above: either the existence'
            emit 'check did not answer with present or absent, or the listing of the directory failed.'
            emit 'Re-run before citing this log as a reproduction of that row.'
            ;;
    esac

    emit ""
    emit 'Verdict. M63 docs/ios16-all-18.3-xpc-endpoint-audit.md section 4 (情形的判据) and section 8 item 1:'
    case ${VERDICT} in
        A)
            emit '  A: all four WebKit services are in the Safari section and a new helper loaded a'
            emit '  /var/jb WebKit framework.'
            case ${OVERRIDE_XPC_SERVICES} in
                absent)
                    emit '  That contradicts build-state.md:506 (same pruned configuration, endpoints 0),'
                    emit '  so that reading was affected by a variable it did not record and has to be'
                    emit '  redone. It does not by itself show that launchd resolved an unindexed path:'
                    emit '  see the paragraph above.'
                    ;;
                present*)
                    emit '  This run installed XPCServices inside the override, so it is not the'
                    emit '  build-state.md:506 configuration and does not contradict that row. Read the'
                    emit '  reason line to see whether launchd started the override .xpc itself or the'
                    emit '  /System stub handed the framework over.'
                    ;;
                *)
                    emit '  The installed override shape could not be established, so whether this'
                    emit '  contradicts build-state.md:506 is not decided here.'
                    ;;
            esac
            emit '  The whole-18.3 route stays open; what remains is the helper-to-UI protocol and'
            emit '  ABI work.'
            ;;
        B)
            emit '  B: every new helper whose framework was read loaded the system copy, and at most'
            emit '  two of the four services remain.'
            case ${OVERRIDE_XPC_SERVICES} in
                absent)
                    emit '  This agrees with build-state.md:506 (endpoints 0, spawn 0, 265 lookup'
                    emit '  failures): under this configuration the endpoints going to zero is a known'
                    emit '  result, and this run is a reproduction that adds a CrashReporter diff and the'
                    emit '  helper framework paths rather than a new attribution.'
                    ;;
                present*)
                    emit '  This is NOT the build-state.md:506 configuration: that row is the pruned'
                    emit '  shape, and this run measured XPCServices inside the override. Endpoints going'
                    emit '  to zero here is a new reading about the unpruned shape rather than a'
                    emit '  reproduction of that row; it is comparable with build-state.md:333-347, where'
                    emit '  the unpruned override did get GPU started from its own .xpc before that'
                    emit '  process died in xpc_dictionary_apply.'
                    ;;
                *)
                    emit '  The installed override shape could not be established, so whether this'
                    emit '  reproduces build-state.md:506 is withheld. Read the step 2 lines above.'
                    ;;
            esac
            emit '  Replacing the UI process on 16.1 cannot work, because the UI process has no code'
            emit '  to register those names itself (section 4, 若 (B) 成立); work moves to the 16.1 UI'
            emit '  plus 18.3 helper translation route.'
            ;;
        *)
            emit '  UNKNOWN: this run does not answer (A) or (B). The PROBE_REASON line names the condition that blocked it.'
            ;;
    esac
    emit ""
    emit "PROBE_VERDICT=${VERDICT}"
    emit "PROBE_REASON=${PROBE_REASON}"
    emit "PROBE_INVALID_STEPS=${invalid}"
    emit "PROBE_WEBKIT_SERVICES=${webkit_count}"
    emit "PROBE_SERVICE_BRAILLE=${SVC_BRAILLE}"
    emit "PROBE_SERVICE_MTL=${SVC_MTL}"
    emit "PROBE_DISTINCT_SERVICES=${SVC_DISTINCT}"
    emit "PROBE_NEW_HELPER_OVERRIDE=${HELPER_NEW_OVERRIDE}"
    emit "PROBE_NEW_HELPER_JB_FRAMEWORKS=${HELPER_NEW_JB_FRAMEWORKS}"
    emit "PROBE_NEW_HELPER_SYSTEM=${HELPER_NEW_SYSTEM}"
    emit "PROBE_NEW_HELPER_OTHER=${HELPER_NEW_OTHER}"
    emit "PROBE_NEW_HELPERS=${HELPER_NEW_TOTAL}"
    emit "PROBE_NEW_HELPER_FRAMEWORK_OVERRIDE=${HELPER_FRAMEWORK_OVERRIDE}"
    emit "PROBE_NEW_HELPER_FRAMEWORK_JB=${HELPER_FRAMEWORK_JB}"
    emit "PROBE_NEW_HELPER_FRAMEWORK_SYSTEM=${HELPER_FRAMEWORK_SYSTEM}"
    emit "PROBE_NEW_HELPER_FRAMEWORK_UNKNOWN=${HELPER_FRAMEWORK_UNKNOWN}"
    emit "PROBE_HELPER_FRAMEWORK_PATHS_RECORDED=${HELPER_FRAMEWORK_PATHS_RECORDED}"
    emit "PROBE_JB_FRAMEWORK_FROM_OVERRIDE_XPC=${HELPER_JB_FRAMEWORK_FROM_OVERRIDE_XPC}"
    emit "PROBE_JB_FRAMEWORK_THROUGH_STUB=${HELPER_JB_FRAMEWORK_THROUGH_STUB}"
    emit "PROBE_STALE_HELPERS=${HELPER_STALE_TOTAL}"
    emit "PROBE_STALE_HELPERS_KILLED=${HELPER_KILLED}"
    emit "PROBE_SAFARI_PID=${SAFARI_PID:-none}"
    emit "PROBE_OVERRIDE_AFTER=${OVERRIDE_PRESENT_AFTER}"
    emit "PROBE_LOG=${LOG_FILE}"
}

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ "${OVERRIDE_INSTALLED}" -eq 1 ] && [ "${UNINSTALL_CONFIRMED}" -eq 0 ]; then
        emit ""
        emit 'the run ended before step 6 confirmed the removal; removing the override directory now'
        remote_sudo "${DEVICE_BASH} ${UNINSTALL_SCRIPT}"
        emit "${REMOTE_OUTPUT}"
        remote "[ -d ${OVERRIDE_ROOT} ] && echo present || echo absent"
        OVERRIDE_PRESENT_AFTER=$(printf '%s\n' "${REMOTE_OUTPUT}" | grep -E '^(present|absent)$' | tail -n 1)
        emit "  ${OVERRIDE_ROOT} after rollback: ${OVERRIDE_PRESENT_AFTER:-unknown}"
        emit "PROBE_OVERRIDE_AFTER=${OVERRIDE_PRESENT_AFTER:-unknown}"
        if [ "${OVERRIDE_PRESENT_AFTER}" != absent ]; then
            emit "Error: the rollback did not remove ${OVERRIDE_ROOT}. Run this on the device before anything else: sudo ${DEVICE_BASH} ${UNINSTALL_SCRIPT}"
        fi
    fi
    exit "${status}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

parse_arguments() {
    while [ $# -gt 0 ]; do
        case $1 in
            --ssh-target)
                [ $# -ge 2 ] || die '--ssh-target needs a value'
                SSH_TARGET=$2
                shift 2
                ;;
            --log-dir)
                [ $# -ge 2 ] || die '--log-dir needs a value'
                LOG_DIR=$2
                shift 2
                ;;
            --wait)
                [ $# -ge 2 ] || die '--wait needs a value'
                WAIT_SECONDS=$2
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                die "unknown argument: $1 (try --help)"
                ;;
        esac
    done
    case ${WAIT_SECONDS} in
        ''|*[!0-9]*) die "wait must be a whole number of seconds, got '${WAIT_SECONDS}'" ;;
    esac
}

prepare_log() {
    if [ -z "${LOG_DIR}" ]; then
        LOG_DIR=${TMPDIR:-/tmp}
        LOG_DIR=${LOG_DIR%/}/webkit-override-probe-$(date '+%Y%m%d-%H%M%S')
    fi
    mkdir -p "${LOG_DIR}" || die "cannot create ${LOG_DIR}"
    LOG_FILE=${LOG_DIR}/probe.log
    WORK_DIR=${LOG_DIR}/work
    STEPS_FILE=${WORK_DIR}/steps.tsv
    mkdir -p "${WORK_DIR}" || die "cannot create ${WORK_DIR}"
    : >"${LOG_FILE}"
    : >"${STEPS_FILE}"
    {
        printf '%s\n' 'WebKit override endpoint probe'
        printf '  date      %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
        printf '  target    %s\n' "${SSH_TARGET}"
        printf '  log       %s\n' "${LOG_FILE}"
    } >>"${LOG_FILE}"
    printf 'Probe log: %s\n' "${LOG_FILE}"
    install_python_program
}

probe_sudo() {
    if [ -n "${DEVICE_SUDO_PASSWORD}" ]; then
        SUDO_MODE=password
    else
        SUDO_MODE=passwordless
    fi
    remote_sudo 'true'
    if [ "${REMOTE_RC}" -ne 0 ]; then
        die "sudo on ${SSH_TARGET} failed (exit ${REMOTE_RC}: ${REMOTE_OUTPUT}); steps 2 and 6 need root, so set DEVICE_SUDO_PASSWORD"
    fi
}

main() {
    parse_arguments "$@"
    prepare_log
    trap cleanup EXIT INT TERM

    # Graphics-session check, the first device action and before anything is
    # written anywhere. The gate is SpringBoard's job under user/501; gui/501 is
    # read too, because a log that omits it could not be told apart from a run on
    # a configuration that does have that domain, but it never gates.

    read_gui_domain_facts
    printf 'Precondition: launchctl print gui/501 exited %s and printed %s line(s) (diagnostic).\n' \
        "${GUI_RC}" "${GUI_LINES}" >>"${LOG_FILE}"
    read_springboard_facts
    printf 'Precondition: launchctl print user/501/com.apple.SpringBoard exited %s, state=%s (gate).\n' \
        "${SPRINGBOARD_RC}" "${SPRINGBOARD_STATE}" >>"${LOG_FILE}"
    graphics_session_healthy || abort_graphics_session_missing

    probe_sudo
    remote "test -f ${INSTALL_SCRIPT} && test -f ${UNINSTALL_SCRIPT} && echo scripts-present"
    if [ "${REMOTE_OUTPUT}" != 'scripts-present' ]; then
        die "${INSTALL_SCRIPT} and ${UNINSTALL_SCRIPT} must both exist on the device; without both there is no rollback"
    fi

    if ! crash_snapshot "${WORK_DIR}/crashes-baseline" >/dev/null; then
        die "cannot list the crash reports on the device (exit ${REMOTE_RC}): ${REMOTE_OUTPUT}"
    fi
    cp "${WORK_DIR}/crashes-baseline" "${WORK_DIR}/crashes-1-before"

    step_preconditions
    if step_install; then
        if step_launch_safari; then
            step_read_endpoints || true
            step_tally_helpers
        else
            skip_step 4 'read the Safari per-pid services section' 'no Safari pid, so there is no per-pid domain to read'
            skip_step 5 'tally the WebKit helper executable paths' 'no Safari pid, so no helper population belongs to this launch'
        fi
    else
        skip_step 3 'kill Safari, launch it with a URL, take its pid' 'the install did not succeed, so there is no override directory to measure'
        skip_step 4 'read the Safari per-pid services section' 'the install did not succeed'
        skip_step 5 'tally the WebKit helper executable paths' 'the install did not succeed'
    fi
    step_uninstall || emit 'step 6 did not confirm the rollback; the EXIT trap retries the removal.'

    step_summary

    case ${VERDICT} in
        A|B) exit 0 ;;
        *) exit 4 ;;
    esac
}

main "$@"
