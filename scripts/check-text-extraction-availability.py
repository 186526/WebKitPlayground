#!/usr/bin/env python3
"""Require every @objc top-level declaration to be available wherever the types it references are.

At tag Safari-18.3.1-iOS-18.3.2 Source/WebKit/WebKitSwift/TextExtraction/WKTextExtractionItem.swift
declares nine top-level types, eight of them behind `@available(iOS 17.0, macOS 12.0, *)`. The
ninth, `@objc(WKTextExtractionRequest) class WKTextExtractionRequest`, has no annotation but
references WKTextExtractionItem in its stored completion handler, its init parameter and its
`fulfill(item:)` parameter. WebKitSwift compiles this file at every deployment target, so a 16.1
build reaches those three references and Swift rejects each one:

  WKTextExtractionItem.swift:126:38: error: 'WKTextExtractionItem' is only available in iOS 17.0
  or newer  (build.log of run 45078008945, also :128:78 and :133:47)

patches/ios18-public-sdk-text-extraction-availability.patch adds the missing annotation. This
script reads the patched file and fails when any @objc declaration references a type that is
gated at a version the declaration does not itself satisfy, which is the state that stopped that
build. Because it parses the file rather than matching one line, a future ref that adds a ninth
declaration cannot reintroduce the same error silently.

Usage:
  scripts/check-text-extraction-availability.py \
    --swift WebKit/Source/WebKit/WebKitSwift/TextExtraction/WKTextExtractionItem.swift
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

AVAILABLE = re.compile(r"@available\(([^)]*)\)")
VERSION = re.compile(r"\b(iOS|macOS|macCatalyst|tvOS|watchOS)\s+v?(\d+(?:\.\d+)?)")
# A declaration line. The attribute may or may not carry the ObjC name in parentheses, and the
# attributes this file writes sit either on that line or on the lines above it.
DECLARATION = re.compile(
    r"\b(?:public|internal|open|final|private|fileprivate)?\s*"
    r"(?:class|struct|enum|protocol|actor)\s+(\w+)"
)
OBJECTIVE_C_NAME = re.compile(r"@objc\((\w+)\)")
IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")


@dataclass
class Declaration:
    swift_name: str
    objc_name: str
    line: int
    availability: dict[str, tuple[int, ...]] = field(default_factory=dict)
    references: set[str] = field(default_factory=set)


def strip_line_comment(line: str) -> str:
    """Drop a trailing // comment.

    Neither WKTextExtractionItem.swift nor any ref of it holds a // inside a string literal, so
    splitting on the first // is enough and keeps the scan from reading commented-out code.
    """
    index = line.find("//")
    return line if index < 0 else line[:index]


def parse_versions(attribute: str) -> dict[str, tuple[int, ...]]:
    """Turn "iOS 17.0, macOS 12.0, *" into {"iOS": (17, 0), "macOS": (12, 0)}.

    A clause with no version number, such as the bare `iOS` in `@available(iOS, unavailable)`,
    contributes nothing: the `*` shorthand and `unavailable` both state no minimum, so a
    declaration carrying only those reads as unannotated and fails closed.
    """
    versions: dict[str, tuple[int, ...]] = {}
    for platform, number in VERSION.findall(attribute):
        versions[platform] = tuple(int(part) for part in number.split("."))
    return versions


def body_bounds(lines: list[str], start: int) -> tuple[int, int]:
    """Return the first and last line index of the braced body that opens at or after `start`."""
    depth = 0
    opener = None
    for index in range(start, len(lines)):
        for character in strip_line_comment(lines[index]):
            if character == "{":
                depth += 1
                if opener is None:
                    opener = index
            elif character == "}":
                depth -= 1
                if depth == 0 and opener is not None:
                    return opener, index
    return (start, len(lines) - 1)


def parse(path: Path) -> list[Declaration]:
    """Return each top-level type declaration with its annotation and the names its body uses.

    Only top-level declarations are collected: at depth 0 the scanner reads @available lines into
    `pending`, and a type declaration consumes them, so a nested type's annotation can never
    attach itself to the enclosing one. The body text is taken whole, so a reference in a stored
    property, an initializer parameter and a method parameter all count the same way.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    declarations: list[Declaration] = []
    pending: dict[str, tuple[int, ...]] = {}
    depth = 0
    index = 0
    while index < len(lines):
        code = strip_line_comment(lines[index])
        if depth == 0:
            attribute = AVAILABLE.search(code)
            declaration = DECLARATION.search(code)
            if declaration:
                versions = dict(pending)
                if attribute is not None:
                    versions.update(parse_versions(attribute.group(1)))
                objc = OBJECTIVE_C_NAME.search(code)
                entry = Declaration(
                    swift_name=declaration.group(1),
                    objc_name=objc.group(1) if objc else declaration.group(1),
                    line=index + 1,
                    availability=versions,
                )
                opener, closer = body_bounds(lines, index)
                text = "\n".join(strip_line_comment(line) for line in lines[index:closer + 1])
                # The declaration line itself carries the inheritance clause, so a subclass's
                # superclass is read from the same text as the body.
                entry.references = set(IDENTIFIER.findall(text)) - {entry.swift_name}
                declarations.append(entry)
                pending.clear()
                index = closer + 1
                depth = 0
                continue
            if attribute is not None:
                pending.update(parse_versions(attribute.group(1)))
            elif code.strip() and not code.lstrip().startswith("@"):
                pending.clear()
        depth += code.count("{") - code.count("}")
        index += 1
    return declarations


def satisfies(
    declaration: Declaration, required: dict[str, tuple[int, ...]]
) -> tuple[bool, str]:
    """Report whether `declaration` is available wherever `required` says it is.

    macOS is compared as a second platform rather than through the `*` shorthand: every
    declaration in this file carries both, and a class gated only on iOS would compile on macOS
    while referencing a macOS 12 type.

    The returned phrase is a complete predicate for the declaration, not a bare version list, so
    the caller reads as one sentence. A declaration with no annotation at all does not "require"
    any version; it states nothing, which is the mistake that stopped the 16.1 build.
    """
    if not declaration.availability:
        return (False, "carries no @available annotation")
    missing: list[str] = []
    for platform, version in required.items():
        own = declaration.availability.get(platform)
        if own is None:
            missing.append(f"requires {platform} {'.'.join(map(str, version))}")
        elif own < version:
            missing.append(
                f"declares {platform} {'.'.join(map(str, own))}, below {'.'.join(map(str, version))}")
    return (not missing, " and ".join(missing))


def describe(versions: dict[str, tuple[int, ...]]) -> str:
    if not versions:
        return "no @available annotation"
    return ", ".join(f"{platform} {'.'.join(map(str, version))}"
                     for platform, version in versions.items())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that every @objc declaration can reach the types it references.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--swift", required=True, type=Path,
                        help="WKTextExtractionItem.swift in the patched WebKit tree")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.swift.is_file():
        # Every tag before Safari-18.0 has no WebKitSwift/TextExtraction directory at all, so
        # there is no file to annotate and nothing to check on those refs.
        print(f"SKIP: {args.swift} does not exist at this ref, so this tag ships no text-extraction Swift")
        return 0

    declarations = parse(args.swift)
    if not declarations:
        print(f"FAIL: {args.swift} declares no @objc type, so this checker read nothing it can vouch for",
              file=sys.stderr)
        return 1

    by_swift_name = {declaration.swift_name: declaration for declaration in declarations}
    by_objc_name = {declaration.objc_name: declaration for declaration in declarations}
    gated_count = sum(1 for declaration in declarations if declaration.availability)

    print(f"{args.swift}: {len(declarations)} top-level @objc declaration(s)")
    failures: list[str] = []
    for declaration in sorted(declarations, key=lambda entry: entry.line):
        referenced = sorted(
            {
                name
                for name in declaration.references
                if (name in by_swift_name or name in by_objc_name)
                and (by_swift_name.get(name) or by_objc_name.get(name)) is not declaration
            }
        )
        print(f"  {declaration.swift_name} (line {declaration.line}): {describe(declaration.availability)}"
              f"  references: {', '.join(referenced) if referenced else '(none)'}")
        for name in referenced:
            other = by_swift_name.get(name) or by_objc_name.get(name)
            if other is None or not other.availability:
                continue
            ok, missing = satisfies(declaration, other.availability)
            if not ok:
                failures.append(
                    f"{declaration.swift_name} (line {declaration.line}) references {name}, available in "
                    f"{describe(other.availability)}, but {declaration.swift_name} {missing}; "
                    f"a deployment target below {describe(other.availability)} compiles it and Swift "
                    f"rejects the reference"
                )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: all {gated_count} available type(s) are reachable only from declarations gated at least as high")
    return 0


if __name__ == "__main__":
    sys.exit(main())
