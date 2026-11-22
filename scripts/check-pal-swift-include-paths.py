#!/usr/bin/env python3
"""Expand PAL's OTHER_SWIFT_FLAGS the way xcodebuild does and require the PALSwift include paths.

WebKitTargetConditionals.xcconfig turns IPHONEOS_DEPLOYMENT_TARGET into a suffix on WK_IOS_17:
16.x yields _IOS_BEFORE_17 and 17.x and later yield _IOS_SINCE_17. PAL.xcconfig defines
OTHER_SWIFT_FLAGS_IOS_SINCE_17 only, so a 16.1 deployment target resolves OTHER_SWIFT_FLAGS to
the empty string, and the PAL swiftc invocation loses the two -I flags that make module PALSwift
in Source/WebCore/PAL/pal visible. CryptoKitShim.swift then fails with "no such module 'PALSwift'".

This script reads the two xcconfig files, evaluates the same key chain xcodebuild evaluates, and
prints the resolved settings. It exits non-zero when the include paths are missing, so the build
workflow fails in seconds instead of an hour into compilation.

Usage:
  scripts/check-pal-swift-include-paths.py \
    --pal-xcconfig WebKit/Source/WebCore/PAL/Configurations/PAL.xcconfig \
    --conditionals-xcconfig WebKit/Configurations/WebKitTargetConditionals.xcconfig \
    --deployment-target 16.1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ASSIGNMENT = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?);\s*$")
REFERENCE = re.compile(r"\$\(([^()]*)\)")
REQUIRED_FLAGS = ("-Xcc -std=c++20", "-I {srcroot}/pal", "-I {srcroot}/PAL/pal")


def parse_xcconfig(path: Path) -> dict[str, str]:
    """Return the unconditional assignments of one xcconfig file.

    Conditional assignments ([sdk=...], [config=...]) are dropped: every key this check reads is
    unconditional, and honouring a condition would need xcodebuild's own setting table.
    """
    settings: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if "[" in line.split("=", 1)[0]:
            continue
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        settings[match.group(1)] = match.group(2)
    return settings


def base_name(value: str) -> str:
    """Apply the :base operator: drop everything from the last dot on."""
    separator = value.rfind(".")
    return value if separator < 0 else value[:separator]


def lookup(name: str, settings: dict[str, str]) -> str:
    """Resolve one $() reference name, honouring the :base operators."""
    parts = name.split(":")
    value = settings.get(parts[0].strip(), "")
    for operator in parts[1:]:
        if operator.strip() == "base":
            value = base_name(value)
    return value


def expand(value: str, settings: dict[str, str]) -> str:
    """Expand innermost-first, the way xcodebuild resolves nested references."""
    for _ in range(32):
        expanded = REFERENCE.sub(lambda match: lookup(match.group(1), settings), value)
        if expanded == value:
            return expanded
        value = expanded
    raise SystemExit(f"reference expansion did not settle: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that PAL's OTHER_SWIFT_FLAGS carries the PALSwift include paths.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pal-xcconfig", required=True, type=Path)
    parser.add_argument("--conditionals-xcconfig", required=True, type=Path)
    parser.add_argument("--deployment-target", default="16.1")
    parser.add_argument("--platform", default="iphoneos")
    parser.add_argument("--srcroot", default="/WebKit/Source/WebCore/PAL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = parse_xcconfig(args.conditionals_xcconfig)
    settings.update(parse_xcconfig(args.pal_xcconfig))
    settings["SRCROOT"] = args.srcroot
    settings["IPHONEOS_DEPLOYMENT_TARGET"] = args.deployment_target
    settings["WK_PLATFORM_NAME"] = args.platform

    ios_before_17 = expand("$(WK_IOS_BEFORE_17)", settings)
    ios_17 = expand("$(WK_IOS_17)", settings)
    flags = expand("$(OTHER_SWIFT_FLAGS)", settings)

    print(f"IPHONEOS_DEPLOYMENT_TARGET = {settings['IPHONEOS_DEPLOYMENT_TARGET']}")
    print(f"WK_PLATFORM_NAME = {settings['WK_PLATFORM_NAME']}")
    print(f"WK_IOS_BEFORE_17 = {ios_before_17!r}")
    print(f"WK_IOS_17 = {ios_17!r}")
    print(f"OTHER_SWIFT_FLAGS = {flags!r}")

    # Tags before the Swift PAL landed have no OTHER_SWIFT_FLAGS key at all, so there is no
    # PALSwift module to find and nothing to check.
    if "OTHER_SWIFT_FLAGS" not in settings:
        print("SKIP: this tag's PAL.xcconfig has no OTHER_SWIFT_FLAGS, so PAL ships no Swift module")
        return 0

    missing = [
        flag.format(srcroot=args.srcroot)
        for flag in REQUIRED_FLAGS
        if flag.format(srcroot=args.srcroot) not in flags
    ]
    if missing:
        print("MISSING from OTHER_SWIFT_FLAGS:", file=sys.stderr)
        for flag in missing:
            print(f"  {flag}", file=sys.stderr)
        print(
            f"WK_IOS_17 resolved to {ios_17!r}, so PAL.xcconfig must define "
            f"OTHER_SWIFT_FLAGS{ios_17} or the PAL Swift build cannot find module PALSwift.",
            file=sys.stderr,
        )
        return 1
    print("OK: OTHER_SWIFT_FLAGS carries every PALSwift include path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
