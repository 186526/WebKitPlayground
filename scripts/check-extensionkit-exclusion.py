#!/usr/bin/env python3
"""Resolve BaseExtension.xcconfig's EXCLUDED_SOURCE_FILE_NAMES the way xcodebuild does.

patches/ios18-public-sdk-extensionkit-extension-exclusion.patch replaces

    EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] = ;

with a value read from the WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED switch, so the four ExtensionKit
targets compile no sources when the build asks for that. Those targets declare conformance to a
BrowserEngineKit protocol whose configuration property is API_AVAILABLE(ios(17.4)); a Swift
availability check is evaluated against IPHONEOS_DEPLOYMENT_TARGET and cannot be turned off with a
-D, so a 16.1 deployment target cannot compile WebContentProcessExtension.swift or
NetworkingProcessExtension.swift at all.

The switch defaults to empty, which leaves EXCLUDED_SOURCE_FILE_NAMES[sdk=iphone*] empty exactly as
upstream has it. That default is what this script guards: a future ref that reshapes these keys must
not silently start excluding sources for every iOS build.

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
    """Report whether the exclusion value reads the switch.

    The patch does not add a key named after the switch, it points the platform-wide
    EXCLUDED_SOURCE_FILE_NAMES at $(EXCLUDED_SOURCE_FILE_NAMES_$(WK_EXTENSIONKIT_EXTENSIONS_EXCLUDED)).
    An unpatched file leaves that key empty and never mentions the switch.
    """
    return any(
        name == EXCLUSIONS and SWITCH in value
        for name, _, value in assignments
    )


def version_scoped_conditions(assignments: list[tuple[str, str | None, str]]) -> list[str]:
    """Return the SDK patterns that scope an exclusion by SDK *version* rather than platform.

    Upstream writes [sdk=iphone*] for the whole platform and [sdk=iphone*17.0*] and friends for
    individual SDK releases. Only the first can reveal what the switch alone does.
    """
    return sorted({
        condition
        for name, condition, _ in assignments
        if name == EXCLUSIONS and condition is not None and condition.count("*") > 1
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
    assignments = parse_xcconfig(args.base_extension_xcconfig)
    extra = {SWITCH: args.excluded_switch}
    excluded = expand("$(EXCLUDED_SOURCE_FILE_NAMES)", assignments, args.sdk, extra)

    print(f"sdk = {args.sdk}")
    print(f"{SWITCH} = {args.excluded_switch!r}")
    print(f"EXCLUDED_SOURCE_FILE_NAMES = {excluded!r}")

    if not is_patched(assignments):
        print(f"SKIP: this tag's BaseExtension.xcconfig never reads {SWITCH}, so the patch is not applied")
        return 0

    if args.excluded_switch:
        if excluded != "*":
            print(
                f"FAIL: with {SWITCH}={args.excluded_switch} the ExtensionKit targets must compile "
                f"no sources, so EXCLUDED_SOURCE_FILE_NAMES must be '*', not {excluded!r}",
                file=sys.stderr,
            )
            return 1
        print("OK: the switch excludes every source")
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

    if excluded:
        print(
            f"FAIL: with {SWITCH} unset EXCLUDED_SOURCE_FILE_NAMES must stay empty so an ordinary "
            f"iOS build keeps compiling these targets, but it resolved to {excluded!r}",
            file=sys.stderr,
        )
        return 1
    print("OK: with the switch unset the file excludes nothing, as upstream has it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
