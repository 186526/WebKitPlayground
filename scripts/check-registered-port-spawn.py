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
    branches = re.findall(r"^    if \(([^\n]*)\) \{\n(.*?)^    \}", body, re.MULTILINE | re.DOTALL)
    failure_conditions = (
        "result != KERN_SUCCESS || !(portType & MACH_PORT_TYPE_SEND)",
        "auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)",
        "!childExitSource",
    )
    cancel = "xpc_connection_cancel(listener.get());"
    if body.count("return false;") != len(failure_conditions) or body.count(cancel) != len(failure_conditions):
        raise ValueError("expected one listener cancel per synchronous fallback")
    branches_by_condition = dict(branches)
    for condition in failure_conditions:
        branch = branches_by_condition.get(condition, "")
        cleanup = f"        {cancel}\n        return false;"
        if condition == failure_conditions[0]:
            cleanup = f"        if (listener)\n    {cleanup}"
            if 'RELEASE_LOG_ERROR(IPC, "WK_HELPER_SPAWN_FALLBACK:' not in branch:
                raise ValueError("expected the send-right failure to log WK_HELPER_SPAWN_FALLBACK")
        if not branch.rstrip().endswith(cleanup) or "failBeforeConnection" in branch:
            raise ValueError(f"expected cancel before named-XPC fallback in branch: {condition}")
    if not re.search(r"std::exchange\(m_registeredPortListener, nullptr\)\)\s+" + re.escape(cancel), source):
        raise ValueError("expected the transferred listener to be cleared and cancelled once")


def check_helper(source):
    if not re.search(r"\*endpointPort = placeholderPort;\s+endpoint = nullptr;\s+"
                     r"xpc_connection_cancel\(placeholder.get\(\)\);\s+\}", source):
        raise ValueError("expected restoration of the endpoint right and cancel before placeholder scope exit")
    if 'XPCServiceMain: launchd listener (WK_HELPER_LAUNCH_MODE did not select registered-port)' not in source:
        raise ValueError("expected the helper's existing launchd listener marker")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", type=Path, default=UI_PATCH)
    parser.add_argument("--helper-patch", type=Path, default=HELPER_PATCH)
    args = parser.parse_args()
    for path, check in ((args.patch, check_ui), (args.helper_patch, check_helper)):
        try:
            check(patched_source(path))
        except (OSError, ValueError) as error:
            print(f"FAIL: {path}: {error}\n\nResult: FAIL", file=sys.stderr)
            return 1
    print("OK: registered-port spawn is the default, with named XPC on opt-out or synchronous fallback")
    print("OK: UI and helper temporary XPC connections are cancelled before release")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
