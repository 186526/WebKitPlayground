#!/usr/bin/env python3
# 黄金文本检查比较补丁原文，不能证明运行时控制流。
"""Check named-XPC fallback and XPC connection cleanup in the registered-port patches."""

import argparse
from itertools import zip_longest
from pathlib import Path
import re
import sys


PATCHES = Path(__file__).resolve().parents[1] / "patches"
UI_PATCH = PATCHES / "ios18-xpc-registered-port-ui.patch"
HELPER_PATCH = PATCHES / "ios18-xpc-registered-port-helper.patch"
LAUNCH_BLOCK = """
#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR)
// Registered-port spawn is the default; set WK_HELPER_SPAWN=launchd to use named XPC.
if (auto mode = getenv("WK_HELPER_SPAWN"); !mode || strcmp(mode, "launchd")) {
    if (registeredPortsFunction() && launchWithRegisteredPort(name))
        return;
    RELEASE_LOG_ERROR(IPC, "Registered-port spawn unavailable or failed; falling back to named XPC launch for %s", name.characters());
}
#endif
m_xpcConnection = adoptOSObject(xpc_connection_create(name, nullptr));
finishLaunchingProcess(name);
"""
LAUNCH_FUNCTION = '''bool ProcessLauncher::launchWithRegisteredPort(ASCIILiteral name)
{
    ASSERT(RunLoop::isMain());
    auto weakLauncher = ThreadSafeWeakPtr { *this };
    auto failBeforeConnection = [weakLauncher] {
        if (auto launcher = weakLauncher.get(); launcher && launcher->isLaunching() && launcher->m_client)
            launcher->didFinishLaunchingProcess(0, { });
    };

    if (auto key = missingHelperDyldVariable()) {
        RELEASE_LOG_ERROR(IPC, "Cannot spawn %s with a registered port: missing or empty %s", name.characters(), key);
        RunLoop::main().dispatch(WTFMove(failBeforeConnection));
        return true;
    }

    auto listener = adoptOSObject(xpc_connection_create(nullptr, dispatch_get_main_queue()));
    auto endpoint = listener ? adoptOSObject(xpc_endpoint_create(listener.get())) : OSObjectPtr<xpc_endpoint_t> { };
    mach_port_t listenerPort = MACH_PORT_NULL;
    if (endpoint) {
        // iPadOS 16.1 libxpc stores the endpoint's borrowed send right at this offset.
        constexpr size_t endpointPortOffset = 0x18;
        auto bytes = static_cast<const uint8_t*>((__bridge const void*)endpoint.get());
        memcpy(&listenerPort, bytes + endpointPortOffset, sizeof(listenerPort));
    }

    mach_port_type_t portType;
    auto result = mach_port_type(mach_task_self(), listenerPort, &portType);
    if (result != KERN_SUCCESS || !(portType & MACH_PORT_TYPE_SEND)) {
        RELEASE_LOG_ERROR(IPC, "WK_HELPER_SPAWN_FALLBACK: no registered send right for %s; using named XPC", name.characters());
        if (listener)
            xpc_connection_cancel(listener.get());
        return false;
    }

    pid_t helperPID = 0;
    if (auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)) {
        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));
        xpc_connection_cancel(listener.get());
        return false;
    }

    auto childExitSource = adoptOSObject(dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, helperPID, DISPATCH_PROC_EXIT, dispatch_get_main_queue()));
    if (!childExitSource) {
        kill(helperPID, SIGKILL);
        dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{ reapSpawnedHelper(helperPID); });
        xpc_connection_cancel(listener.get());
        return false;
    }

    m_spawnedHelperPID = helperPID;
    m_registeredPortListener = WTFMove(listener);
    dispatch_source_set_event_handler(childExitSource.get(), ^{
        reapSpawnedHelper(helperPID);
        dispatch_source_cancel(childExitSource.get());
        auto launcher = weakLauncher.get();
        if (!launcher)
            return;
        launcher->m_spawnedHelperPID = 0;
        if (launcher->m_registeredPortListener) {
            launcher->cancelRegisteredPortListener();
            failBeforeConnection();
        }
    });
    dispatch_resume(childExitSource.get());

    xpc_connection_set_event_handler(m_registeredPortListener.get(), ^(xpc_object_t event) {
        auto launcher = weakLauncher.get();
        if (!launcher || !launcher->m_registeredPortListener)
            return;
        launcher->cancelRegisteredPortListener();
        if (xpc_get_type(event) != XPC_TYPE_CONNECTION) {
            launcher->terminateXPCConnection();
            failBeforeConnection();
            return;
        }
        launcher->m_xpcConnection = static_cast<xpc_connection_t>(event);
        xpc_connection_set_target_queue(launcher->m_xpcConnection.get(), dispatch_get_main_queue());
        launcher->finishLaunchingProcess(name);
    });
    xpc_connection_resume(m_registeredPortListener.get());

    RunLoop::main().dispatchAfter(registeredPortLaunchTimeout, [weakLauncher, failBeforeConnection] {
        auto launcher = weakLauncher.get();
        if (!launcher || !launcher->m_registeredPortListener)
            return;
        launcher->terminateXPCConnection();
        failBeforeConnection();
    });
    return true;
}'''
CANCEL_FUNCTION = '''void ProcessLauncher::cancelRegisteredPortListener()
{
    if (auto listener = std::exchange(m_registeredPortListener, nullptr))
        xpc_connection_cancel(listener.get());
}'''
HELPER_FUNCTION = '''static OSObjectPtr<xpc_connection_t> createRegisteredPortConnection()
{
    const char* slotValue = getenv("WK_HELPER_REGISTERED_SLOT");
    if (!slotValue || !*slotValue || strspn(slotValue, "0123456789") != strlen(slotValue)) {
        RELEASE_LOG_FAULT(IPC, "Exiting: WK_HELPER_REGISTERED_SLOT must be a decimal index");
        return { };
    }
    errno = 0;
    auto slot = strtoul(slotValue, nullptr, 10);
    if (errno == ERANGE) {
        RELEASE_LOG_FAULT(IPC, "Exiting: WK_HELPER_REGISTERED_SLOT is out of range");
        return { };
    }

    mach_port_array_t registeredPorts = nullptr;
    mach_msg_type_number_t registeredPortCount = 0;
    auto task = mach_task_self();
    auto lookupResult = mach_ports_lookup(task, &registeredPorts, &registeredPortCount);
    if (lookupResult != KERN_SUCCESS) {
        RELEASE_LOG_FAULT(IPC, "Exiting: mach_ports_lookup failed (%d)", lookupResult);
        return { };
    }

    OSObjectPtr<xpc_connection_t> connection;
    auto port = slot < registeredPortCount ? registeredPorts[slot] : MACH_PORT_NULL;
    mach_port_type_t portType = 0;
    if (slot >= registeredPortCount)
        RELEASE_LOG_FAULT(IPC, "Exiting: registered slot %lu is beyond the port array", slot);
    else if (!MACH_PORT_VALID(port) || mach_port_type(task, port, &portType) != KERN_SUCCESS || !(portType & MACH_PORT_TYPE_SEND))
        RELEASE_LOG_FAULT(IPC, "Exiting: registered slot %lu does not contain a send right", slot);
    else {
        auto placeholder = adoptOSObject(xpc_connection_create(nullptr, nullptr));
        auto endpoint = adoptOSObject(xpc_endpoint_create(placeholder.get()));
        // The device's iOS 16.1 libxpc stores the endpoint's send right at this offset.
        constexpr size_t endpointPortOffset = 0x18;
        auto* endpointPort = reinterpret_cast<mach_port_t*>(static_cast<char*>((__bridge void*)endpoint.get()) + endpointPortOffset);
        auto placeholderPort = *endpointPort;
        *endpointPort = port;
        connection = adoptOSObject(xpc_connection_create_from_endpoint(endpoint.get()));
        // Restore the owned right so endpoint destruction releases it; the lookup owns port.
        *endpointPort = placeholderPort;
        endpoint = nullptr;
        // The placeholder is never activated. Cancel it before releasing its last reference.
        xpc_connection_cancel(placeholder.get());
    }

    // Release every right returned by lookup; only the designated slot is used to connect.
    for (mach_msg_type_number_t index = 0; index < registeredPortCount; ++index) {
        if (MACH_PORT_VALID(registeredPorts[index]))
            mach_port_deallocate(task, registeredPorts[index]);
    }
    if (registeredPortCount)
        vm_deallocate(task, reinterpret_cast<vm_address_t>(registeredPorts), registeredPortCount * sizeof(mach_port_t));
    return connection;
}'''
DESTROY_BLOCK = ''' void ProcessLauncher::platformDestroy()
 {
+#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR) && !USE(EXTENSIONKIT)
+    if (m_spawnedHelperPID)
+        terminateXPCConnection();
+#endif
 #if USE(EXTENSIONKIT)'''
TERMINATE_BLOCK = ''' void ProcessLauncher::terminateXPCConnection()
 {
+#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR) && !USE(EXTENSIONKIT)
+    cancelRegisteredPortListener();
+    if (m_spawnedHelperPID)
+        kill(m_spawnedHelperPID, SIGKILL);
+#endif
+
     if (!m_xpcConnection)'''
CANCEL_REFERENCES_OUTSIDE_LAUNCH = 3  # Declaration, definition, and terminateXPCConnection call.
CPP_TOKENS = re.compile(r'''//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{}]''', re.DOTALL)


def patched_hunks(patch):
    return ["\n".join(line[1:] for line in hunk.split("\n") if line.startswith(("+", " "))
                      and not line.startswith("+++"))
            for hunk in re.split(r"^@@ .*\n", patch, flags=re.MULTILINE)[1:]]


def compare_text(expected, actual):
    if expected == actual:
        return
    for number, (expected_line, actual_line) in enumerate(
            zip_longest(expected.split("\n"), actual.split("\n")), start=1):
        if expected_line != actual_line:
            raise ValueError(f"{expected.splitlines()[0]}, line {number}: expected {expected_line!r}, got {actual_line!r}")


def find_unique(texts, signature):
    pattern = re.compile(r"^" + re.escape(signature) + r"[ \t]*$", re.MULTILINE)
    matches = [(text, match.start()) for text in texts for match in pattern.finditer(text)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {signature}, got {len(matches)}")
    return matches[0]


def check_function(hunks, expected):
    signature = expected.split("\n", 1)[0]
    hunk, start = find_unique(hunks, signature)
    opening = start + len(signature) + 1
    if hunk[start:opening + 1] != signature + "\n{":
        compare_text(expected, hunk[start:])
    depth = 0
    for token in CPP_TOKENS.finditer(hunk, opening):
        if token[0] == "{":
            depth += 1
        if token[0] == "}":
            depth -= 1
            if depth == 0:
                end = hunk.find("\n", token.end())
                compare_text(expected, hunk[start:end] if end != -1 else hunk[start:])
                return
    raise ValueError(f"unbalanced braces in {signature}")


def check_anchored_block(patch, expected):
    signature = expected.split("\n", 1)[0]
    raw_patch, start = find_unique([patch], signature)
    actual = "\n".join(raw_patch[start:].split("\n")[:len(expected.splitlines())])
    compare_text(expected, actual)


def check_ui(patch):
    hunks = patched_hunks(patch)
    source = "\n".join(hunks)
    normalized = " ".join(source.split())
    if normalized.count('getenv("WK_HELPER_SPAWN")') != 1 or " ".join(LAUNCH_BLOCK.split()) not in normalized:
        raise ValueError("expected registered-port selection with launchd opt-out and named-XPC fallback")
    if "bool launchWithRegisteredPort(ASCIILiteral);" not in source:
        raise ValueError("expected a boolean launchWithRegisteredPort declaration")
    check_function(hunks, LAUNCH_FUNCTION)
    check_function(hunks, CANCEL_FUNCTION)
    check_anchored_block(patch, DESTROY_BLOCK)
    check_anchored_block(patch, TERMINATE_BLOCK)
    outside_launch = source.replace(LAUNCH_FUNCTION, "", 1)
    if outside_launch.count("cancelRegisteredPortListener()") != CANCEL_REFERENCES_OUTSIDE_LAUNCH:
        raise ValueError("unexpected cancelRegisteredPortListener() reference count outside launchWithRegisteredPort")


def check_helper(patch):
    hunks = patched_hunks(patch)
    check_function(hunks, HELPER_FUNCTION)
    if not any('XPCServiceMain: launchd listener (WK_HELPER_LAUNCH_MODE did not select registered-port)' in hunk
               for hunk in hunks):
        raise ValueError("expected the helper's existing launchd listener marker")


def check_regression(check, patch, description, replacements):
    for anchor, replacement in replacements:
        if anchor not in patch:
            raise ValueError(f"could not insert regression case: {description}")
        patch = patch.replace(anchor, replacement, 1)
    try:
        check(patch)
    except ValueError as error:
        print(f"OK: regression rejected {description} ({error})")
        return
    raise ValueError(f"regression accepted: {description}")


def check_regressions(ui_patch, helper_patch):
    spawn_failure = "+    if (auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)) {\n"
    restore_port = "+        *endpointPort = placeholderPort;"
    cleanup_opening = "+void ProcessLauncher::cancelRegisteredPortListener()\n+{\n"
    spawn_cancel = (spawn_failure
                    + '+        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));\n')
    cancel_line = "+        xpc_connection_cancel(listener.get());\n"
    for description, anchor, replacement in (
        ("if (false) at start of cancelRegisteredPortListener", cleanup_opening, cleanup_opening + "+    if (false)\n"),
        ("if (false) before spawn failure cancel", spawn_cancel, spawn_cancel + "+        if (false)\n"),
        ("seven-space indentation on spawn failure cancel", spawn_cancel + cancel_line, spawn_cancel + "+" + cancel_line[2:]),
        ("direct clearing of listener instead of cancelling", "+            launcher->cancelRegisteredPortListener();",
         "+            launcher->m_registeredPortListener = nullptr;"),
        *((statement, spawn_failure, f"{spawn_failure}+        {statement}\n") for statement in (
            "listener = nullptr;", "return true;", "auto escaped = WTFMove (listener);",
            "auto escaped = std::exchange(listener, nullptr);")),
    ):
        check_regression(check_ui, ui_patch, description, [(anchor, replacement)])
    for statement in ("placeholder = nullptr;", "auto escaped = WTFMove(placeholder);"):
        check_regression(check_helper, helper_patch, statement,
                         [(restore_port, f"+        {statement}\n{restore_port}")])
    connect = "+        connection = adoptOSObject(xpc_connection_create_from_endpoint(endpoint.get()));"
    release = "+    for (mach_msg_type_number_t index = 0;"
    check_regression(check_helper, helper_patch, "goto releaseRegisteredPorts; with target label", [
        (connect, "+        goto releaseRegisteredPorts;\n" + connect),
        (release, "+    releaseRegisteredPorts:\n" + release),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", type=Path, default=UI_PATCH)
    parser.add_argument("--helper-patch", type=Path, default=HELPER_PATCH)
    args = parser.parse_args()
    try:
        ui_patch = args.patch.read_text(encoding="utf-8")
        helper_patch = args.helper_patch.read_text(encoding="utf-8")
        check_ui(ui_patch)
        check_helper(helper_patch)
        check_regressions(ui_patch, helper_patch)
    except (OSError, ValueError) as error:
        print(f"FAIL: {args.patch}, {args.helper_patch}: {error}\n\nResult: FAIL", file=sys.stderr)
        return 1
    print("OK: registered-port spawn is the default, with named XPC on opt-out or synchronous fallback")
    print("OK: UI and helper temporary XPC connections are cancelled before release")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
