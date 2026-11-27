#!/usr/bin/env python3
"""Check the registered-port launch gate and named-XPC fallback in the UI patch."""

import argparse
from pathlib import Path
import re
import sys


PATCH = Path(__file__).resolve().parents[1] / "patches/ios18-xpc-registered-port-ui.patch"
LAUNCH_BLOCK = """
#if PLATFORM(IOS) && !PLATFORM(IOS_SIMULATOR)
if (auto mode = getenv("WK_HELPER_SPAWN"); !mode || strcmp(mode, "launchd")) {
    if (registeredPortsFunction()) {
        launchWithRegisteredPort(name);
        return;
    }
    LOG_ERROR("Registered-port spawn API unavailable; using named XPC launch for %s", name.characters());
}
#endif
m_xpcConnection = adoptOSObject(xpc_connection_create(name, nullptr));
finishLaunchingProcess(name);
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", type=Path, default=PATCH)
    args = parser.parse_args()
    try:
        lines = args.patch.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        print(f"FAIL: {error}\n\nResult: FAIL", file=sys.stderr)
        return 1

    patched_text = "\n".join(line[1:] if line.startswith(("+", " ")) else ""
                             for line in lines if not line.startswith("-"))
    source = re.sub(r"(?m)^\s*//[^\n]*", "", patched_text)
    normalized = " ".join(source.split())
    if normalized.count('getenv("WK_HELPER_SPAWN")') != 1 or " ".join(LAUNCH_BLOCK.split()) not in normalized:
        print(f"FAIL: {args.patch} does not default to registered-port spawn with an explicit launchd opt-out and API fallback",
              file=sys.stderr)
        print("\nResult: FAIL", file=sys.stderr)
        return 1
    print("OK: unset and registered-port select registered-port spawn; launchd selects named XPC")
    print("OK: an unavailable registered-port API logs an error and falls back to named XPC")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
