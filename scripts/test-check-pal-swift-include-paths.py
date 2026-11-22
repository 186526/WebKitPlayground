#!/usr/bin/env python3
"""Regression tests for scripts/check-pal-swift-include-paths.py.

The cases pin the two real builds this check was written against:

  run 44773942038, IPHONEOS_DEPLOYMENT_TARGET=18.5, no patch
    WK_IOS_17 resolves to _IOS_SINCE_17, OTHER_SWIFT_FLAGS_IOS_SINCE_17 applies, PAL builds.
  run 44959967331, IPHONEOS_DEPLOYMENT_TARGET=16.1, no patch
    WK_IOS_17 resolves to _IOS_BEFORE_17, no OTHER_SWIFT_FLAGS_IOS_BEFORE_17 exists,
    OTHER_SWIFT_FLAGS is empty and PAL fails with "no such module 'PALSwift'".

Each case writes the two xcconfig files it needs, so nothing here depends on a WebKit checkout.

Usage: python3 scripts/test-check-pal-swift-include-paths.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-pal-swift-include-paths.py"

CONDITIONALS = """
WK_IOS_BEFORE_17 = $(WK_IOS_BEFORE_17_$(IPHONEOS_DEPLOYMENT_TARGET:base));
WK_IOS_BEFORE_17_13 = YES;
WK_IOS_BEFORE_17_14 = YES;
WK_IOS_BEFORE_17_15 = YES;
WK_IOS_BEFORE_17_16 = YES;

WK_IOS_17 = $(WK_IOS_17_$(WK_IOS_BEFORE_17));
WK_IOS_17_ = _IOS_SINCE_17;
WK_IOS_17_YES = _IOS_BEFORE_17;
"""

PATCHED_PAL = """
SWIFT_VERSION = 5.0;
OTHER_SWIFT_FLAGS = $(OTHER_SWIFT_FLAGS_$(WK_PLATFORM_NAME));
OTHER_SWIFT_FLAGS_iphoneos = $(OTHER_SWIFT_FLAGS$(WK_IOS_17));
OTHER_SWIFT_FLAGS_IOS_BEFORE_17 = -Xcc -std=c++20 -I $(SRCROOT)/pal -I $(SRCROOT)/PAL/pal;
OTHER_SWIFT_FLAGS_IOS_SINCE_17 = -Xcc -std=c++20 -I $(SRCROOT)/pal -I $(SRCROOT)/PAL/pal;
"""

UNPATCHED_PAL = """
SWIFT_VERSION = 5.0;
OTHER_SWIFT_FLAGS = $(OTHER_SWIFT_FLAGS_$(WK_PLATFORM_NAME));
OTHER_SWIFT_FLAGS_iphoneos = $(OTHER_SWIFT_FLAGS$(WK_IOS_17));
OTHER_SWIFT_FLAGS_IOS_SINCE_17 = -Xcc -std=c++20 -I $(SRCROOT)/pal -I $(SRCROOT)/PAL/pal;
"""

NO_SWIFT_PAL = """
SWIFT_VERSION = 5.0;
"""

failures: list[str] = []


def apply_patch_hunk(patch: Path, target: Path) -> bool:
    """Apply the patch's last hunk to target by hand.

    git apply needs a repository and a matching blob; these tests only carry the one region the
    patch touches, so the added lines are inserted after the IOS_SINCE_17 line the hunk anchors on.
    """
    added = [
        line[1:]
        for line in patch.read_text(encoding="utf-8").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    text = target.read_text(encoding="utf-8")
    anchor = "OTHER_SWIFT_FLAGS_IOS_SINCE_17"
    for index, line in enumerate(text.splitlines()):
        if line.startswith(anchor):
            lines = text.splitlines()
            lines[index + 1 : index + 1] = added
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def run_check(root: Path, deployment_target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pal-xcconfig", str(root / "PAL.xcconfig"),
            "--conditionals-xcconfig", str(root / "WebKitTargetConditionals.xcconfig"),
            "--deployment-target", deployment_target,
        ],
        capture_output=True,
        text=True,
    )


def case(name: str, pal_text: str, deployment_target: str, want_code: int, want_text: str) -> None:
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        (root / "WebKitTargetConditionals.xcconfig").write_text(CONDITIONALS, encoding="utf-8")
        (root / "PAL.xcconfig").write_text(pal_text, encoding="utf-8")
        result = run_check(root, deployment_target)
    combined = result.stdout + result.stderr
    if result.returncode != want_code:
        failures.append(f"{name}: exit {result.returncode}, wanted {want_code}\n{combined}")
        return
    if want_text not in combined:
        failures.append(f"{name}: output lacks {want_text!r}\n{combined}")
        return
    print(f"PASS {name}")


def case_patch_applies() -> None:
    """The patch itself must add a key that makes the 16.1 case pass."""
    patch = SCRIPT.resolve().parent.parent / "patches" / "ios18-public-sdk-pal-swift-include-paths.patch"
    if not patch.is_file():
        failures.append(f"patch missing: {patch}")
        return
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        (root / "WebKitTargetConditionals.xcconfig").write_text(CONDITIONALS, encoding="utf-8")
        (root / "PAL.xcconfig").write_text(UNPATCHED_PAL, encoding="utf-8")
        if not apply_patch_hunk(patch, root / "PAL.xcconfig"):
            failures.append("patch hunk did not anchor on OTHER_SWIFT_FLAGS_IOS_SINCE_17")
            return
        result = run_check(root, "16.1")
        patched_text = (root / "PAL.xcconfig").read_text(encoding="utf-8")
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        failures.append(f"patch did not restore the include paths at 16.1\n{combined}")
        return
    if "OTHER_SWIFT_FLAGS_IOS_BEFORE_17" not in patched_text:
        failures.append("patch did not add OTHER_SWIFT_FLAGS_IOS_BEFORE_17")
        return
    print("PASS patch restores the include paths at 16.1")


def main() -> int:
    case(
        "16.1 without the key fails and names _IOS_BEFORE_17",
        UNPATCHED_PAL, "16.1", 1, "WK_IOS_17 resolved to '_IOS_BEFORE_17'",
    )
    case("16.1 with the key passes", PATCHED_PAL, "16.1", 0, "OK: OTHER_SWIFT_FLAGS")
    case("17.0 with the key passes", PATCHED_PAL, "17.0", 0, "OK: OTHER_SWIFT_FLAGS")
    case("18.5 without the key still passes", UNPATCHED_PAL, "18.5", 0, "OK: OTHER_SWIFT_FLAGS")
    case("a tag with no Swift PAL is skipped", NO_SWIFT_PAL, "16.1", 0, "SKIP:")
    case_patch_applies()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
