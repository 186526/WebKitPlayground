#!/usr/bin/env python3
"""Regression tests for scripts/check-extensionkit-exclusion.py.

The cases pin the file patches/ios18-public-sdk-extensionkit-extension-exclusion.patch produces
against tag Safari-18.3.1-iOS-18.3.2, and the reason the switch has to be opt-in:

  the 18.5 SDK reaches WebContentExtension, WebContentCaptivePortalExtension and NetworkingExtension
  with -target arm64e-apple-ios16.1, and Swift rejects the BrowserEngineKit conformance because the
  protocol's configuration property is API_AVAILABLE(ios(17.4)) (build.log:267936, :267969, :268002).
  Upstream excludes these targets by SDK version only up to 17.3, so the 18.5 SDK is not covered.

Each case writes the xcconfig it needs, so nothing here depends on a WebKit checkout.

Usage: python3 scripts/test-check-extensionkit-exclusion.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-extensionkit-exclusion.py"

# The shape upstream has at tag Safari-18.3.1-iOS-18.3.2: the platform key is empty and each SDK
# release that must not carry these targets is spelled out.
UNPATCHED = """
SWIFT_OBJC_BRIDGING_HEADER = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*] = Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*17.0*] = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=xr*] = ;
EXCLUDED_SOURCE_FILE_NAMES = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = ;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*17.0*] = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*17.3*] = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=xr*] = *;
"""

PATCHED = """
SWIFT_OBJC_BRIDGING_HEADER = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*] = $(SWIFT_OBJC_BRIDGING_HEADER_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED));
SWIFT_OBJC_BRIDGING_HEADER_ = Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h;
SWIFT_OBJC_BRIDGING_HEADER_YES = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*17.0*] = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=xr*] = ;
EXCLUDED_SOURCE_FILE_NAMES = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = $(EXCLUDED_SOURCE_FILE_NAMES_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED));
EXCLUDED_SOURCE_FILE_NAMES_YES = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*17.0*] = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*17.3*] = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=xr*] = *;
"""

# The half-patched state the round-1 review blocked: sources excluded, bridging header left in
# place, which is what emits the SwiftGeneratePch command that failed for GPUExtension.
HALF_PATCHED = """
SWIFT_OBJC_BRIDGING_HEADER = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*] = Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h;
EXCLUDED_SOURCE_FILE_NAMES = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = $(EXCLUDED_SOURCE_FILE_NAMES_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED));
EXCLUDED_SOURCE_FILE_NAMES_YES = *;
"""

# The switch wired backwards: the empty value on the unset arm and the path on the YES arm. Both
# directions must fail, which is what pins the assertion the review asked for.
REVERSED = """
SWIFT_OBJC_BRIDGING_HEADER = ;
SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*] = $(SWIFT_OBJC_BRIDGING_HEADER_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED));
SWIFT_OBJC_BRIDGING_HEADER_ = ;
SWIFT_OBJC_BRIDGING_HEADER_YES = Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h;
EXCLUDED_SOURCE_FILE_NAMES = *;
EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = $(EXCLUDED_SOURCE_FILE_NAMES_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED));
EXCLUDED_SOURCE_FILE_NAMES_YES = *;
"""

# Tag Safari-17.4 keys the whole file on USE_INTERNAL_SDK, so the public SDK already excludes
# everything and the patch cannot apply there.
SEVENTEEN_FOUR = """
SWIFT_OBJC_BRIDGING_HEADER = $(SWIFT_OBJC_BRIDGING_HEADER_$(USE_INTERNAL_SDK));
SWIFT_OBJC_BRIDGING_HEADER_ = ;
EXCLUDED_SOURCE_FILE_NAMES = $(EXCLUDED_SOURCE_FILE_NAMES_$(USE_INTERNAL_SDK));
EXCLUDED_SOURCE_FILE_NAMES_ = *;
EXCLUDED_SOURCE_FILE_NAMES_YES = *;
EXCLUDED_SOURCE_FILE_NAMES_YES[sdk=iphone*] = ;
"""

failures: list[str] = []


def run(xcconfig: str, sdk: str, switch: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "BaseExtension.xcconfig"
        path.write_text(xcconfig, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT),
             "--base-extension-xcconfig", str(path),
             "--sdk", sdk,
             "--excluded-switch", switch],
            capture_output=True, text=True,
        )


def case(label: str, xcconfig: str, sdk: str, switch: str, expected_status: int,
         expected_text: str | tuple[str, ...]) -> None:
    result = run(xcconfig, sdk, switch)
    combined = result.stdout + result.stderr
    if result.returncode != expected_status:
        failures.append(
            f"{label}: exit {result.returncode}, expected {expected_status}\n{combined}")
        return
    expected = (expected_text,) if isinstance(expected_text, str) else expected_text
    for text in expected:
        if text not in combined:
            failures.append(f"{label}: {text!r} missing from output\n{combined}")
            return
    print(f"PASS {label}")


def main() -> int:
    # The resolved values are asserted alongside the verdict: a check that only prints OK while
    # resolving the wrong settings would let a broken patch through.
    case("the switch excludes every source and blanks the bridging header",
         PATCHED, "iphoneos18.5", "YES", 0,
         ("EXCLUDED_SOURCE_FILE_NAMES = '*'",
          "SWIFT_OBJC_BRIDGING_HEADER = ''",
          "OK: the switch excludes every source and blanks the bridging header"))
    case("with the switch unset both settings keep the upstream values",
         PATCHED, "iphoneos18.5", "", 0,
         ("EXCLUDED_SOURCE_FILE_NAMES = ''",
          "SWIFT_OBJC_BRIDGING_HEADER = 'Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h'",
          "OK: with the switch unset"))
    case("an SDK upstream already scopes is skipped rather than judged",
         PATCHED, "iphoneos17.0", "", 0, "already matches iphone*17.0*")
    case("an unpatched tag is skipped",
         UNPATCHED, "iphoneos18.5", "", 0, "never reads WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED")
    case("the 18.3.1 shape leaves the 18.5 SDK uncovered, which is the bug",
         UNPATCHED, "iphoneos18.5", "YES", 0,
         "never reads WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED")
    case("a USE_INTERNAL_SDK-keyed tag is not mistaken for a patched one",
         SEVENTEEN_FOUR, "iphoneos18.5", "YES", 0,
         "never reads WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED")
    # The round-1 blocking case: the switch is wired for sources only. It must not read as applied,
    # because that state still generates the bridging-header PCH.
    case("a patch that wires only the source exclusion is not accepted",
         HALF_PATCHED, "iphoneos18.5", "YES", 0,
         "never reads WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED in both")
    # Both arms of the bridging-header switch, so removing either assertion fails the suite.
    case("switching the bridging header the wrong way fails with the switch set",
         REVERSED, "iphoneos18.5", "YES", 1,
         "SWIFT_OBJC_BRIDGING_HEADER must be empty")
    case("switching the bridging header the wrong way fails with the switch unset",
         REVERSED, "iphoneos18.5", "", 1,
         "SWIFT_OBJC_BRIDGING_HEADER must stay")

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
