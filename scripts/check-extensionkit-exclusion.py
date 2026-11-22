#!/usr/bin/env python3
"""Resolve BaseExtension.xcconfig's two exclusion settings the way xcodebuild does.

patches/ios18-public-sdk-extensionkit-extension-exclusion.patch replaces

    SWIFT_OBJC_BRIDGING_HEADER[sdk=iphone*] = Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h;
    EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = ;

with values read from the WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED switch, so the four ExtensionKit
targets compile no sources and generate no bridging-header PCH when the build asks for that. Those
targets declare conformance to a BrowserEngineKit protocol whose configuration property is
API_AVAILABLE(ios(17.4)); a Swift availability check is evaluated against IPHONEOS_DEPLOYMENT_TARGET
and cannot be turned off with a -D, so a 16.1 deployment target cannot compile
WebContentProcessExtension.swift or NetworkingProcessExtension.swift at all.

The two settings are paired on purpose. Every state where upstream sets
EXCLUDED_SOURCE_FILE_NAMES = * also blanks the bridging header: macos, 17.0 through 17.3, and xr.
A zero-source target that still names a bridging header is a combination upstream never ships, and it
is the one that emits "SwiftGeneratePch ... Compiling bridging header" — the command that failed for
GPUExtension in build.log:268012.

The switch defaults to empty, which leaves both settings exactly as upstream has them. That default
is what this script guards: a future ref that reshapes these keys must not silently change every
iOS build.

Usage:
  scripts/check-extensionkit-exclusion.py \
    --base-extension-xcconfig WebKit/Source/WebKit/Configurations/BaseExtension.xcconfig \
    --sdk iphoneos18.5 \
    --excluded-switch ''
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path

CONDITION = re.compile(r"\[sdk=([^\]]+)\]")
NAME = re.compile(r"^([A-Za-z0-9_]+)")
REFERENCE = re.compile(r"\$\(([^()]*)\)")

SWITCH = "WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED"
EXCLUSIONS = "EXCLUDED_SOURCE_FILE_NAMES"
BRIDGING_HEADER = "SWIFT_OBJC_BRIDGING_HEADER"
BRIDGING_HEADER_VALUE = "Shared/AuxiliaryProcessExtensions/AuxiliaryProcessExtensionBridge.h"

# The two settings the patch rewrites. Upstream pairs them in every platform and SDK state, so the
# checker asserts both in each direction.
PAIRED = (EXCLUSIONS, BRIDGING_HEADER)


def split_assignment(line: str) -> tuple[str, str] | None:
    """Split "NAME[sdk=...] = value" on the '=' that is not inside a condition.

    A condition carries its own '=', so the split tracks bracket depth; splitting on the first '='
    would read "EXCLUDED_SOURCE_FILE_NAMES[sdk" as the name.
    """
    depth = 0
    for index, character in enumerate(line):
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
        elif character == "=" and depth == 0:
            return line[:index].strip(), line[index + 1:].strip()
    return None


def parse_xcconfig(path: Path) -> list[tuple[str, str | None, str]]:
    """Return (name, sdk-condition or None, value) in file order.

    xcodebuild applies matching conditional assignments in the order it reads them, so the last
    matching assignment for a name wins. Keeping the order is what lets a narrower key such as
    [sdk=iphone*17.0*] override the wider [sdk=iphone*] one.
    """
    assignments: list[tuple[str, str | None, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if line.endswith(";"):
            line = line[:-1].strip()
        split = split_assignment(line)
        if split is None:
            continue
        name_part, value = split
        name_match = NAME.match(name_part)
        if not name_match:
            continue
        conditions = CONDITION.findall(name_part)
        assignments.append(
            (name_match.group(1), conditions[0] if conditions else None, value))
    return assignments


def sdk_matches(pattern: str, sdk: str) -> bool:
    """Match an [sdk=...] condition with the glob syntax xcodebuild accepts.

    Upstream writes the bare SDK prefix pattern (iphone*) and version-scoped variants
    (iphone*17.0*), so a plain glob against the SDK name reproduces both.
    """
    return fnmatch.fnmatchcase(sdk, pattern)


def resolve(name: str, assignments: list[tuple[str, str | None, str]], sdk: str,
            extra: dict[str, str]) -> str:
    if name in extra:
        return extra[name]
    value = ""
    for assigned_name, condition, assigned_value in assignments:
        if assigned_name != name:
            continue
        if condition is not None and not sdk_matches(condition, sdk):
            continue
        value = assigned_value
    return value


def expand(value: str, assignments: list[tuple[str, str | None, str]], sdk: str,
           extra: dict[str, str]) -> str:
    def reference(match: re.Match[str]) -> str:
        parts = match.group(1).split(":")
        resolved = resolve(parts[0].strip(), assignments, sdk, extra)
        for operator in parts[1:]:
            if operator.strip() == "base":
                separator = resolved.rfind(".")
                resolved = resolved if separator < 0 else resolved[:separator]
        return resolved

    for _ in range(32):
        expanded = REFERENCE.sub(reference, value)
        if expanded == value:
            return " ".join(expanded.split())
        value = expanded
    raise SystemExit(f"reference expansion did not settle: {value}")


def is_patched(assignments: list[tuple[str, str | None, str]]) -> bool:
    """Report whether both paired settings read the switch.

    The patch does not add a key named after the switch, it points the platform-wide values at
    $(..._$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED)). An unpatched file never mentions the switch; a
    half-patched file mentions it in one of the two, which is the state that leaves a zero-source
    target holding a bridging header.
    """
    return all(
        any(name == setting and SWITCH in value for name, _, value in assignments)
        for setting in PAIRED
    )


def version_scoped_conditions(assignments: list[tuple[str, str | None, str]]) -> list[str]:
    """Return the SDK patterns that scope an exclusion by SDK *version* rather than platform.

    Upstream writes [sdk=iphone*] for the whole platform and [sdk=iphone*17.0*] and friends for
    individual SDK releases. Only the first can reveal what the switch alone does. Both paired
    settings carry the same version-scoped keys, so either one can supply them.
    """
    return sorted({
        condition
        for name, condition, _ in assignments
        if name in PAIRED and condition is not None and condition.count("*") > 1
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that BaseExtension's source exclusion follows the deployment-target switch.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-extension-xcconfig", required=True, type=Path)
    parser.add_argument("--sdk", default="iphoneos18.5")
    parser.add_argument("--excluded-switch", default="",
                        help="value the build passes as WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_extension_xcconfig.is_file():
        print(f"SKIP: {args.base_extension_xcconfig} does not exist at this ref")
        return 0

    assignments = parse_xcconfig(args.base_extension_xcconfig)
    extra = {SWITCH: args.excluded_switch}
    excluded = expand(f"$({EXCLUSIONS})", assignments, args.sdk, extra)
    bridging_header = expand(f"$({BRIDGING_HEADER})", assignments, args.sdk, extra)

    print(f"sdk = {args.sdk}")
    print(f"{SWITCH} = {args.excluded_switch!r}")
    print(f"{EXCLUSIONS} = {excluded!r}")
    print(f"{BRIDGING_HEADER} = {bridging_header!r}")

    if not is_patched(assignments):
        print(f"SKIP: this tag's BaseExtension.xcconfig never reads {SWITCH} in both "
              f"{' and '.join(PAIRED)}, so the patch is not applied")
        return 0

    if args.excluded_switch:
        failures: list[str] = []
        if excluded != "*":
            failures.append(f"{EXCLUSIONS} must be '*', not {excluded!r}")
        if bridging_header:
            failures.append(f"{BRIDGING_HEADER} must be empty, not {bridging_header!r}")
        if failures:
            # A zero-source target that still names a bridging header is the combination that
            # emits the SwiftGeneratePch command that failed for GPUExtension.
            for failure in failures:
                print(
                    f"FAIL: with {SWITCH}={args.excluded_switch} the ExtensionKit targets must skip "
                    f"all Swift work, but {failure}",
                    file=sys.stderr,
                )
            return 1
        print("OK: the switch excludes every source and blanks the bridging header")
        return 0

    scoped = [
        pattern
        for pattern in version_scoped_conditions(assignments)
        if sdk_matches(pattern, args.sdk)
    ]
    if scoped:
        print(
            f"SKIP: {args.sdk} already matches {', '.join(scoped)}, so this SDK cannot show what the "
            f"switch does on its own; pass an SDK outside those ranges"
        )
        return 0

    failures = []
    if excluded:
        failures.append(f"{EXCLUSIONS} must stay empty, not {excluded!r}")
    if bridging_header != BRIDGING_HEADER_VALUE:
        failures.append(
            f"{BRIDGING_HEADER} must stay {BRIDGING_HEADER_VALUE}, not {bridging_header!r}")
    if failures:
        for failure in failures:
            print(
                f"FAIL: with {SWITCH} unset an ordinary iOS build must keep compiling these "
                f"targets, but {failure}",
                file=sys.stderr,
            )
        return 1
    print("OK: with the switch unset the file keeps the upstream values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
