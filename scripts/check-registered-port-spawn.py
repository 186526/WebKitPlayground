#!/usr/bin/env python3
# 黄金文本检查比较补丁原文，不能证明运行时控制流。
# 补丁文件另以 SHA-256 整体校验。区间检查负责定位差异，哈希负责证明没有
# 未被区间覆盖的改动。改补丁必须同时更新脚本里的哈希与对应区间常量。
"""Check named-XPC fallback and XPC connection cleanup in the registered-port patches."""

import argparse
from hashlib import sha256
from itertools import zip_longest
from pathlib import Path
import re
import sys


PATCHES = Path(__file__).resolve().parents[1] / "patches"
UI_PATCH = PATCHES / "ios18-xpc-registered-port-ui.patch"
HELPER_PATCH = PATCHES / "ios18-xpc-registered-port-helper.patch"
XPC_MAIN_PATCH = PATCHES / "ios18-public-sdk-xpc-main.patch"
UI_PATCH_SHA256 = "6b96b89f6dbae024f0722e0bab3d2d49c354072a6cbfb83050589873819a8639"
HELPER_PATCH_SHA256 = "fc8c07cad82e9b0a90e6d2680e3098f1a7496362615c7207020ee185ce0e7907"
XPC_MAIN_PATCH_SHA256 = "3558e61c0e1bf5f81d82bb8c15424ce98d3f11eddd5e9fb8f71d0a95537f0946"
LAUNCH_BLOCK = '''     launchWithExtensionKit(*this, m_launchOptions.processType, m_client, WTFMove(handler));
 #else
     auto name = serviceName(m_launchOptions, m_client);
+#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR)
+    // Registered-port spawn is the default; set WK_HELPER_SPAWN=launchd to use named XPC.
+    if (auto mode = getenv("WK_HELPER_SPAWN"); !mode || strcmp(mode, "launchd")) {
+        if (registeredPortsFunction() && launchWithRegisteredPort(name))
+            return;
+        RELEASE_LOG_ERROR(IPC, "Registered-port spawn unavailable or failed; falling back to named XPC launch for %s", name.characters());
+    }
+#endif
     m_xpcConnection = adoptOSObject(xpc_connection_create(name, nullptr));
     finishLaunchingProcess(name);
 #endif'''
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

    m_registeredPortListener = adoptOSObject(xpc_connection_create(nullptr, dispatch_get_main_queue()));
    if (!m_registeredPortListener)
        return false;

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

    auto endpoint = adoptOSObject(xpc_endpoint_create(m_registeredPortListener.get()));
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
        cancelRegisteredPortListener();
        return false;
    }

    pid_t helperPID = 0;
    if (auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)) {
        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));
        cancelRegisteredPortListener();
        return false;
    }

    auto childExitSource = adoptOSObject(dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, helperPID, DISPATCH_PROC_EXIT, dispatch_get_main_queue()));
    if (!childExitSource) {
        kill(helperPID, SIGKILL);
        dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{ reapSpawnedHelper(helperPID); });
        cancelRegisteredPortListener();
        return false;
    }

    m_spawnedHelperPID = helperPID;
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
HELPER_LISTENER_BLOCK = '''+            if (!registeredPortBootstrapState.compare_exchange_strong(bootstrapState, RegisteredPortBootstrapState::TimedOut))
+                return;
+            RELEASE_LOG_FAULT(IPC, "Exiting: registered-port client timed out waiting for bootstrap");
+            exitProcess(EXIT_FAILURE);
+        });
+        XPCServiceEventHandler(connection.get());
+        // Keep main-queue work on the main thread; dispatch_main unbinds it (probe19).
+        runXPCServiceMainRunLoop();
+    }
+
+    RELEASE_LOG(IPC, "XPCServiceMain: launchd listener (WK_HELPER_LAUNCH_MODE did not select registered-port)");
     // xpc_main is marked API_UNAVAILABLE(ios), so reproduce what it does.
     // What it does is listen, not dial: on this device it reads XPC_SERVICE_NAME and calls
     // xpc_connection_create_listener, and the handler it wraps receives each connection launchd'''
NAMED_LISTENER_RUN_LOOP = '''+    xpc_connection_resume(listener);
+    runXPCServiceMainRunLoop();
+#endif'''
XPC_MAIN_RUN_LOOP = '''[[noreturn]] static void runXPCServiceMainRunLoop()
{
    // On iPadOS 16.1, dispatch_main calls pthread_exit and libdispatch unbinds
    // the main queue from the main thread (probe19: main_np=0). CFRunLoopRun
    // keeps WorkQueue::main().dispatchSync on the main thread (probe20: main_np=1).
    // This satisfies InitializeWebKit2's main-thread assertion when the
    // InitializeWebKit2MainThreadAssertion SDK-aligned behavior is disabled.
    CFRunLoopRun();
    RELEASE_LOG_FAULT(IPC, "Exiting: XPC service main run loop returned");
    exitProcess(EXIT_FAILURE);
}'''
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


def check_anchored_block(patch, expected, *, whole_hunk=False):
    signature = expected.split("\n", 1)[0]
    if whole_hunk:
        hunks = re.split(r"^@@ .*\n", patch, flags=re.MULTILINE)[1:]
        hunk, _ = find_unique(hunks, signature)
        actual = hunk.removesuffix("\n")
    else:
        raw_patch, start = find_unique([patch], signature)
        actual = "\n".join(raw_patch[start:].split("\n")[:len(expected.splitlines())])
    compare_text(expected, actual)


def check_launch_block(patch, source):
    find_unique([patch], LAUNCH_BLOCK.split("\n", 1)[0])
    check_anchored_block(patch, LAUNCH_BLOCK, whole_hunk=True)
    if source.count('getenv("WK_HELPER_SPAWN")') != 1:
        raise ValueError("expected one registered-port launch mode selector")


def check_ui(patch):
    hunks = patched_hunks(patch)
    source = "\n".join(hunks)
    check_launch_block(patch, source)
    if patch.splitlines().count("+    bool launchWithRegisteredPort(ASCIILiteral);") != 1:
        raise ValueError("expected exactly one added boolean launchWithRegisteredPort declaration")
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
    check_anchored_block(patch, HELPER_LISTENER_BLOCK)


def check_xpc_main(patch):
    check_function(patched_hunks(patch), XPC_MAIN_RUN_LOOP)
    check_anchored_block(patch, NAMED_LISTENER_RUN_LOOP)


def check_regression(check, patch, description, replacements):
    for anchor, replacement in replacements:
        if patch.count(anchor) != 1:
            raise ValueError(f"expected one insertion anchor for regression case: {description}")
        patch = patch.replace(anchor, replacement, 1)
    try:
        check(patch)
    except ValueError as error:
        print(f"OK: regression rejected {description} ({error})")
        return
    raise ValueError(f"regression accepted: {description}")


def check_regressions(ui_patch, helper_patch):
    service_name = "     auto name = serviceName(m_launchOptions, m_client);\n"
    finish_launch = "     finishLaunchingProcess(name);\n #endif"
    check_regression(check_ui, ui_patch, "if (false) wrapping registered-port selection and named-XPC fallback", [
        (service_name, service_name + "+    if (false) {\n"),
        (finish_launch, "     finishLaunchingProcess(name);\n+    }\n #endif"),
    ])
    spawn_failure = "+    if (auto error = spawnHelperWithRegisteredPort(helperPID, name, listenerPort)) {\n"
    restore_port = "+        *endpointPort = placeholderPort;"
    cleanup_opening = "+void ProcessLauncher::cancelRegisteredPortListener()\n+{\n"
    spawn_cancel = (spawn_failure
                    + '+        RELEASE_LOG_ERROR(IPC, "Could not spawn %s with a registered port: %s", name.characters(), strerror(error));\n')
    cancel_line = "+        cancelRegisteredPortListener();\n"
    resume = "+    xpc_connection_resume(m_registeredPortListener.get());\n"
    child_exit_resume = "+    dispatch_resume(childExitSource.get());\n"
    check_regression(check_ui, ui_patch, "cancel and release on failure before resume", [
        (resume, ""),
        (child_exit_resume, child_exit_resume + resume),
    ])
    check_regression(check_ui, ui_patch, "resume again on success", [
        (child_exit_resume, child_exit_resume + resume),
    ])
    handler = "+    xpc_connection_set_event_handler(m_registeredPortListener.get(), ^(xpc_object_t event) {\n"
    check_regression(check_ui, ui_patch, "resume before setting the event handler", [
        (resume, ""),
        (handler, resume + handler),
    ])
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
    listener_log = next(line for line in HELPER_LISTENER_BLOCK.splitlines() if "launchd listener" in line) + "\n"
    check_regression(check_helper, helper_patch, "dispatch_main in registered-port entry", [
        ("+        runXPCServiceMainRunLoop();", "+        dispatch_main();"),
    ])
    check_regression(check_helper, helper_patch, "return 0 before the helper's named listener log", [
        (listener_log, "+    return 0;\n" + listener_log),
    ])
    for statement in ("placeholder = nullptr;", "auto escaped = WTFMove(placeholder);"):
        check_regression(check_helper, helper_patch, statement,
                         [(restore_port, f"+        {statement}\n{restore_port}")])
    connect = "+        connection = adoptOSObject(xpc_connection_create_from_endpoint(endpoint.get()));"
    release = "+    for (mach_msg_type_number_t index = 0;"
    check_regression(check_helper, helper_patch, "goto releaseRegisteredPorts; with target label", [
        (connect, "+        goto releaseRegisteredPorts;\n" + connect),
        (release, "+    releaseRegisteredPorts:\n" + release),
    ])


def check_patch_sha256(content, expected, path):
    actual = sha256(content).hexdigest()
    if actual != expected:
        raise ValueError(f"{path}: SHA-256 mismatch: expected {expected}, got {actual}")


def check_hash_regression(helper_bytes):
    anchor = b" #else\n+    // The deb defaults"
    if helper_bytes.count(anchor) != 1:
        raise ValueError("expected one helper #else insertion anchor")
    mutated = helper_bytes.replace(anchor, b" #else\n+    return 0;\n+    // The deb defaults", 1)
    try:
        check_patch_sha256(mutated, HELPER_PATCH_SHA256, HELPER_PATCH.name)
    except ValueError as error:
        print(f"OK: regression rejected return 0 after helper #else ({error})")
        return
    raise ValueError("regression accepted: return 0 after helper #else")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", type=Path, default=UI_PATCH)
    parser.add_argument("--helper-patch", type=Path, default=HELPER_PATCH)
    parser.add_argument("--xpc-main-patch", type=Path, default=XPC_MAIN_PATCH)
    args = parser.parse_args()
    try:
        ui_bytes = args.patch.read_bytes()
        helper_bytes = args.helper_patch.read_bytes()
        xpc_main_bytes = args.xpc_main_patch.read_bytes()
        check_patch_sha256(ui_bytes, UI_PATCH_SHA256, args.patch)
        check_patch_sha256(helper_bytes, HELPER_PATCH_SHA256, args.helper_patch)
        check_patch_sha256(xpc_main_bytes, XPC_MAIN_PATCH_SHA256, args.xpc_main_patch)
        ui_patch = ui_bytes.decode("utf-8")
        helper_patch = helper_bytes.decode("utf-8")
        xpc_main_patch = xpc_main_bytes.decode("utf-8")
        check_ui(ui_patch)
        check_helper(helper_patch)
        check_xpc_main(xpc_main_patch)
        check_regressions(ui_patch, helper_patch)
        check_regression(check_xpc_main, xpc_main_patch, "dispatch_main in named listener entry", [
            ("+    runXPCServiceMainRunLoop();", "+    dispatch_main();"),
        ])
        check_regression(check_xpc_main, xpc_main_patch, "dispatch_main in shared main-loop driver", [
            ("+    CFRunLoopRun();", "+    dispatch_main();"),
        ])
        check_hash_regression(helper_bytes)
    except (OSError, ValueError) as error:
        print(f"FAIL: {args.patch}, {args.helper_patch}, {args.xpc_main_patch}: {error}\n\nResult: FAIL", file=sys.stderr)
        return 1
    print("OK: registered-port spawn is the default, with named XPC on opt-out or synchronous fallback")
    print("OK: UI and helper temporary XPC connections are cancelled before release")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
