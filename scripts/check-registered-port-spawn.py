#!/usr/bin/env python3
"""Check named-XPC fallback and XPC connection cleanup in the registered-port patches."""

import argparse
from pathlib import Path
import re
import sys


PATCHES = Path(__file__).resolve().parents[1] / "patches"
UI_PATCH = PATCHES / "ios18-xpc-registered-port-ui.patch"
HELPER_PATCH = PATCHES / "ios18-xpc-registered-port-helper.patch"
LAUNCH_BLOCK = """
#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR)
if (auto mode = getenv("WK_HELPER_SPAWN"); !mode || strcmp(mode, "launchd")) {
    if (registeredPortsFunction() && launchWithRegisteredPort(name))
        return;
    RELEASE_LOG_ERROR(IPC, "Registered-port spawn unavailable or failed; falling back to named XPC launch for %s", name.characters());
}
#endif
m_xpcConnection = adoptOSObject(xpc_connection_create(name, nullptr));
finishLaunchingProcess(name);
"""


def patched_source(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line[1:] for line in lines if line.startswith(("+", " "))
                     and not line.startswith("+++") and not line[1:].lstrip().startswith("//"))


def check_connection_uses(source, connection, allowed_lines):
    name = re.compile(rf"\b{connection}\b")
    for line in source.splitlines():
        if name.search(line) and line.strip() not in allowed_lines:
            raise ValueError(f"unexpected {connection} use before cancel or transfer: {line}")


def check_no_early_return(source, connection):
    if re.search(r"\breturn\b", source):
        raise ValueError(f"unexpected return before {connection} cancel or transfer")


def check_ui(source):
    normalized = " ".join(source.split())
    if normalized.count('getenv("WK_HELPER_SPAWN")') != 1 or " ".join(LAUNCH_BLOCK.split()) not in normalized:
        raise ValueError("expected registered-port selection with launchd opt-out and named-XPC fallback")
    if "bool launchWithRegisteredPort(ASCIILiteral);" not in source:
        raise ValueError("expected a boolean launchWithRegisteredPort declaration")
    launch = re.search(r"^bool ProcessLauncher::launchWithRegisteredPort\(ASCIILiteral name\)\n\{(.*?)^\}",
                       source, re.MULTILINE | re.DOTALL)
    if not launch:
        raise ValueError("expected a boolean launchWithRegisteredPort implementation")
    body = launch[1]
    acquisition = "auto listener = adoptOSObject(xpc_connection_create(nullptr, dispatch_get_main_queue()));"
    transfer = "m_registeredPortListener = WTFMove(listener);"
    before_acquisition, acquired, after_acquisition = body.partition(acquisition)
    held, transferred, after_transfer = after_acquisition.partition(transfer)
    if not acquired or not transferred or body.count("WTFMove(listener)") != 1:
        raise ValueError("expected listener creation followed by a single ownership transfer")
    if re.search(r"\blistener\s*=(?!=)", before_acquisition + after_transfer):
        raise ValueError("unexpected listener assignment outside its ownership interval")
    branches = re.findall(r"^    if \(([^\n]*)\) \{\n(.*?)^    \}", held, re.MULTILINE | re.DOTALL)
    failure_conditions = (
        "result != KERN_SUCCESS || !(portType & MACH_PORT_TYPE_SEND)",
        "auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)",
        "!childExitSource",
    )
    cancel = "xpc_connection_cancel(listener.get());"
    check_connection_uses(held, "listener", {
        cancel,
        "if (listener)",
        "auto endpoint = listener ? adoptOSObject(xpc_endpoint_create(listener.get())) : OSObjectPtr<xpc_endpoint_t> { };",
    })
    if body.count("return false;") != len(failure_conditions) or body.count(cancel) != len(failure_conditions):
        raise ValueError("expected one listener cancel per synchronous fallback")
    branches_by_condition = dict(branches)
    expected_prefixes = (
        ['        RELEASE_LOG_ERROR(IPC, "WK_HELPER_SPAWN_FALLBACK: no registered send right for %s; using named XPC", name.characters());',
         "        if (listener)"],
        ['        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));'],
        ["        kill(helperPID, SIGKILL);",
         "        dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{ reapSpawnedHelper(helperPID); });"],
    )
    for condition, prefix in zip(failure_conditions, expected_prefixes):
        branch = branches_by_condition.get(condition, "")
        cancel_indent = "            " if condition == failure_conditions[0] else "        "
        if branch.splitlines() != [*prefix, f"{cancel_indent}{cancel}", "        return false;"]:
            raise ValueError(f"expected cancel before named-XPC fallback in branch: {condition}")
        held = held.replace(branch, "", 1)
    check_no_early_return(held, "listener")
    if re.search(r"\bxpc_connection_cancel\s*\(", after_transfer):
        raise ValueError("unexpected connection cancel after listener transfer")
    if not re.search(r"std::exchange\(m_registeredPortListener, nullptr\)\)\s+" + re.escape(cancel), source):
        raise ValueError("expected the transferred listener to be cleared and cancelled once")


def check_helper(source):
    cleanup = re.search(r"^        auto placeholder = adoptOSObject\(xpc_connection_create\(nullptr, nullptr\)\);\n"
                        r"(.*?)^    \}$", source, re.MULTILINE | re.DOTALL)
    expected_tail = [
        "        connection = adoptOSObject(xpc_connection_create_from_endpoint(endpoint.get()));",
        "        *endpointPort = placeholderPort;",
        "        endpoint = nullptr;",
        "        xpc_connection_cancel(placeholder.get());",
    ]
    if not cleanup or cleanup[1].splitlines()[-len(expected_tail):] != expected_tail:
        raise ValueError("expected restoration of the endpoint right and cancel before placeholder scope exit")
    before_cleanup = "\n".join(cleanup[1].splitlines()[:-len(expected_tail)])
    check_connection_uses(before_cleanup, "placeholder", {
        "auto endpoint = adoptOSObject(xpc_endpoint_create(placeholder.get()));",
    })
    check_no_early_return(before_cleanup, "placeholder")
    if 'XPCServiceMain: launchd listener (WK_HELPER_LAUNCH_MODE did not select registered-port)' not in source:
        raise ValueError("expected the helper's existing launchd listener marker")


def check_regressions(ui_source, helper_source):
    spawn_failure = "    if (auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)) {\n"
    restore_port = "        *endpointPort = placeholderPort;"
    listener_mutations = (
        "listener = nullptr;",
        "return true;",
        "auto escaped = WTFMove (listener);",
        "auto escaped = std::exchange(listener, nullptr);",
    )
    placeholder_mutations = (
        "placeholder = nullptr;",
        "auto escaped = WTFMove(placeholder);",
    )
    spawn_log = '        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));\n'
    cancel_line = "        xpc_connection_cancel(listener.get());\n"
    spawn_cancel = spawn_failure + spawn_log + cancel_line
    cases = (
        (check_ui, ui_source, spawn_cancel, spawn_failure + spawn_log + "        if (false)\n" + cancel_line,
         "if (false) before spawn failure cancel"),
        (check_ui, ui_source, spawn_cancel, spawn_failure + spawn_log + cancel_line[1:],
         "seven-space indentation on spawn failure cancel"),
        *((check_ui, ui_source, spawn_failure, f"{spawn_failure}        {statement}\n", statement)
          for statement in listener_mutations),
        *((check_helper, helper_source, restore_port, f"        {statement}\n{restore_port}", statement)
          for statement in placeholder_mutations),
    )
    for check, source, anchor, replacement, statement in cases:
        mutated = source.replace(anchor, replacement, 1)
        if mutated == source:
            raise ValueError(f"could not insert regression case: {statement}")
        try:
            check(mutated)
        except ValueError as error:
            print(f"OK: regression rejected {statement} ({error})")
            continue
        raise ValueError(f"regression accepted: {statement}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", type=Path, default=UI_PATCH)
    parser.add_argument("--helper-patch", type=Path, default=HELPER_PATCH)
    args = parser.parse_args()
    try:
        ui_source = patched_source(args.patch)
        helper_source = patched_source(args.helper_patch)
        check_ui(ui_source)
        check_helper(helper_source)
        check_regressions(ui_source, helper_source)
    except (OSError, ValueError) as error:
        print(f"FAIL: {args.patch}, {args.helper_patch}: {error}\n\nResult: FAIL", file=sys.stderr)
        return 1
    print("OK: registered-port spawn is the default, with named XPC on opt-out or synchronous fallback")
    print("OK: UI and helper temporary XPC connections are cancelled before release")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
