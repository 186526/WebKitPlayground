#!/usr/bin/env python3
"""Require both proxyConfigurations writes in NetworkSessionCocoa.mm to sit behind one runtime guard.

Source/WebKit/NetworkProcess/cocoa/NetworkSessionCocoa.mm assigns
`configuration.proxyConfigurations` in two branches of
NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration: the non-empty branch builds an
NSMutableArray from m_nwProxyConfigs, the empty branch assigns @[ ]. Both sit under one compile-time
HAVE(NW_PROXY_CONFIG) block, and that macro is not a deployment-target test: Source/WTF/wtf/PlatformHave.h
keys it on __IPHONE_OS_VERSION_MAX_ALLOWED, the SDK version, so a 16.1 build against an 18.x SDK
compiles both writes and IPHONEOS_DEPLOYMENT_TARGET cannot switch them off.

-[NSURLSessionConfiguration setProxyConfigurations:] exists from iOS 17 / macOS 14, and no header in
this tree and none in a 16.x SDK declares it, so the compiler emits a warning-level send with no
link-time symbol requirement. On a 16.1 device the message reaches a class that does not implement it
and the process aborts:

  -[NSURLSessionConfiguration setProxyConfigurations:]: unrecognized selector sent to instance
  NSInvalidArgumentException

M55 reproduced that on the device; the process is com.apple.WebKit.Networking, and every NSURLSession
it creates reaches the function through SessionWrapper::initialize.

patches/ios16-network-session-proxy-configurations-guard.patch adds a runtime capability test at the
entry of the function, above both assignments. This script reads the patched file and requires
exactly that shape:

  one runtime guard, respondsToSelector: on the configuration for setProxyConfigurations: or
  @available(iOS 17.0, *), is the first statement of the function body;
  every proxyConfigurations assignment is behind that same guard, either inside its block or after
  its early return.

A guard at the entry is what the crash needs, because the empty branch writes the property too and
that write alone aborts the process, so a guard inside the non-empty branch leaves the crash in
place. The check also reads the early return's polarity: respondsToSelector: has to be negated, or
iOS 17 would skip writes it can perform, and an early return on @available(iOS 17.0, *) is rejected
for the same reason.

Both spellings pass, because either one tests the runtime capability. The patch uses
respondsToSelector: and its comment says why; @available is accepted so that a ref or a future patch
spelling the guard that way is judged on whether it guards, not on style.

Usage:
  scripts/check-network-session-proxy-configurations.py \
    --source WebKit/Source/WebKit/NetworkProcess/cocoa/NetworkSessionCocoa.mm

  # Decide whether an unpatched ref needs the patch, for the workflow's precheck loop:
  scripts/check-network-session-proxy-configurations.py --needs-guard --source <file>
  # exit 0: an unguarded write exists, so the patch is required
  # exit 3: no such function, no such write, or every write already guarded, so a skip is correct
  # exit 1: the file is missing or cannot be parsed, so neither answer is known

The three --needs-guard codes are distinct on purpose. Sharing one code between "no guard needed"
and "could not tell" would let the precheck loop read a parse failure as a legitimate skip and build
for five hours before the device aborted.

Regression tests write their own fixtures and need no checkout:
  python3 scripts/test-check-network-session-proxy-configurations.py
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

FUNCTION = "applyProxyConfigurationToSessionConfiguration"
WRITE = "proxyConfigurations"
# --needs-guard's answer for a file that needs no patch. Distinct from 1 so that a parse failure
# cannot be mistaken for a skip.
NOT_NEEDED = 3
ASSIGNMENT = re.compile(r"\bconfiguration\s*\.\s*proxyConfigurations\s*=")
RETURN = re.compile(r"\breturn\s*;")

# The runtime capability tests the guard may use. Both name the setter selector, the name the
# receiver either implements or does not.
GUARD_SPELLINGS = (
    (
        "respondsToSelector:",
        re.compile(
            r"respondsToSelector:\s*(?:"
            r"NSSelectorFromString\(\s*@\"setProxyConfigurations:\"\s*\)"
            r"|@selector\(\s*setProxyConfigurations:\s*\)"
            r")"
        ),
    ),
    ("@available(iOS 17.0, *)", re.compile(r"@available\s*\(\s*iOS\s+17(?:\.\d+)?\s*,")),
)


@dataclass(frozen=True)
class Guard:
    """One runtime capability test, and which offsets it actually protects.

    `start`/`end` bound the guard's own statement. `else_start`/`else_end` bound an else arm when the
    guard has one; a write there is on the side where the API is absent, so it is never protected,
    which is why the arm is recorded rather than folded into the extent.
    """

    kind: str
    condition: str
    start: int
    end: int
    leaves: bool
    else_start: int | None = None
    else_end: int | None = None

    @property
    def negated(self) -> bool:
        return self.condition.strip().startswith("!")

    @property
    def wrong_polarity(self) -> bool:
        """Whether the early return skips the writes on the systems that do have the API."""
        if not self.leaves:
            return False
        if self.kind == "respondsToSelector:":
            return not self.negated
        return self.negated

    def in_else_arm(self, offset: int) -> bool:
        return (
            self.else_start is not None
            and self.else_end is not None
            and self.else_start <= offset < self.else_end
        )

    def protects(self, offset: int) -> bool:
        """Whether a write at `offset` runs only where the setter exists.

        An early return protects what follows it. A wrapping block protects what is inside it. Its
        else arm is the other case either way, because a write there runs precisely where the
        capability test came out false.
        """
        if self.in_else_arm(offset):
            return False
        if self.leaves:
            return offset >= self.end
        return self.start <= offset < self.end


@dataclass
class Analysis:
    function_line: int | None
    writes: list[int]
    guard: Guard | None
    guard_line: int | None
    covered: list[int]
    failures: list[str]

    @property
    def needs_guard(self) -> bool:
        """Whether a 16.1 build would still reach an unguarded send from this file."""
        return len(self.covered) < len(self.writes)


def strip_comments(lines: list[str]) -> str:
    """Return the file with // and /* */ comments blanked, newlines kept so offsets stay usable.

    The patch explains the missing selector in prose above the guard and quotes the crash message
    that names it, so reading the raw text would report the explanation as a guard or a write.
    """
    output: list[str] = []
    in_block = False
    for line in lines:
        kept: list[str] = []
        index = 0
        while index < len(line):
            if in_block:
                end = line.find("*/", index)
                if end < 0:
                    index = len(line)
                    break
                in_block = False
                index = end + 2
                continue
            if line.startswith("/*", index):
                in_block = True
                index += 2
                continue
            # `//` inside a URL such as rdar:// is not a comment.
            if line.startswith("//", index) and not (index > 0 and line[index - 1] == ":"):
                break
            kept.append(line[index])
            index += 1
        output.append("".join(kept))
    return "\n".join(output)


def skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def at_boundary(text: str, index: int) -> bool:
    return index >= len(text) or not (text[index].isalnum() or text[index] == "_")


def skip_literal(text: str, index: int) -> int:
    """Return the offset just past an Objective-C string or character literal."""
    quote = text[index]
    cursor = index + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == quote:
            return cursor + 1
        cursor += 1
    return len(text)


def match_pair(text: str, index: int, opening: str, closing: str, end: int | None = None) -> int:
    """Return the offset of the bracket that closes the one at `index`."""
    limit = len(text) if end is None else end
    depth = 0
    cursor = index
    while cursor < limit:
        character = text[cursor]
        if character in "\"'":
            cursor = skip_literal(text, cursor)
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    raise ValueError(f"unbalanced {opening}{closing} starting at offset {index}")


def skip_statement(text: str, index: int, end: int) -> int:
    """Return the offset just past the statement that starts at `index`, stopping before any else.

    Braces and semicolons both end a statement here, which is all the guard needs to know: where the
    first statement of the body stops. An else arm is deliberately left outside that extent, because
    a write in it runs where the capability test came out false.
    """
    cursor = index
    depth = 0
    while cursor < end:
        character = text[cursor]
        if character in "\"'":
            cursor = skip_literal(text, cursor)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            if depth == 0:
                return cursor
            depth -= 1
            if depth == 0 and character == "}":
                return cursor + 1
        elif character == ";" and depth == 0:
            return cursor + 1
        cursor += 1
    return end


def guard_spelling(condition: str) -> str | None:
    """Name the runtime capability test in `condition`, or None when it is not one."""
    for name, pattern in GUARD_SPELLINGS:
        if pattern.search(condition):
            return name
    return None


def read_guard(text: str, index: int, end: int) -> Guard | None:
    """Parse the `if` statement at `index` as a runtime capability guard, or return None."""
    if not (text.startswith("if", index) and at_boundary(text, index + 2)):
        return None
    parenthesis = skip_space(text, index + 2)
    if parenthesis >= end or text[parenthesis] != "(":
        return None
    try:
        closing = match_pair(text, parenthesis, "(", ")", end)
    except ValueError:
        return None
    condition = text[parenthesis + 1:closing]
    kind = guard_spelling(condition)
    if kind is None:
        return None
    body = skip_space(text, closing + 1)
    finish = skip_statement(text, body, end)
    leaves = body < end and text[body] != "{" and RETURN.search(text[body:finish]) is not None
    else_start = else_end = None
    alternative = skip_space(text, finish)
    if text.startswith("else", alternative) and at_boundary(text, alternative + 4):
        else_start = skip_space(text, alternative + 4)
        else_end = skip_statement(text, else_start, end)
    return Guard(kind, condition, index, finish, leaves, else_start, else_end)


def find_body(text: str) -> tuple[int, int] | None:
    """Return the offsets of the method body's braces, or None when the method is absent.

    A call site reads the same up to the argument list, so the definition is the occurrence whose
    argument list is followed by a brace.
    """
    for match in re.finditer(rf"\b{FUNCTION}\s*\(", text):
        try:
            closing = match_pair(text, match.end() - 1, "(", ")")
        except ValueError:
            continue
        after = skip_space(text, closing + 1)
        if after < len(text) and text[after] == "{":
            return after, match_pair(text, after, "{", "}") + 1
    return None


def nested_guards(text: str, start: int, end: int) -> list[Guard]:
    """Return every runtime capability guard inside the body, to name a misplaced one."""
    found: list[Guard] = []
    for match in re.finditer(r"\bif\b", text[start:end]):
        guard = read_guard(text, start + match.start(), end)
        if guard is not None:
            found.append(guard)
    return found


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def validate_brackets(text: str, start: int, end: int) -> None:
    """Raise when a bracket inside the function body is unclosed or closed out of order.

    `skip_statement` walks to the end of its range when it never finds the statement's terminator,
    and then reports the guard as extending over everything that follows. Without this pass a body
    whose array literal or block is truncated would read as fully guarded and pass, which is the one
    wrong direction that matters: the device would still abort. The body's own braces are included
    in the range, so an unclosed function also fails here.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    cursor = start
    while cursor < end:
        character = text[cursor]
        if character in "\"'":
            cursor = skip_literal(text, cursor)
            continue
        if character in "([{":
            stack.append(character)
        elif character in ")]}":
            if not stack or stack[-1] != pairs[character]:
                raise ValueError(
                    f"unbalanced '{character}' at line {line_of(text, cursor)} inside the body of "
                    f"{FUNCTION}"
                )
            stack.pop()
        cursor += 1
    if stack:
        raise ValueError(
            f"unclosed '{stack[-1]}' inside the body of {FUNCTION}, so the function's structure "
            f"cannot be read"
        )


def analyse(path: Path) -> Analysis:
    text = strip_comments(path.read_text(encoding="utf-8").splitlines())
    found = find_body(text)
    if found is None:
        return Analysis(None, [], None, None, [], [])

    body_start, body_end = found
    validate_brackets(text, body_start, body_end)
    writes = [
        body_start + match.start() for match in ASSIGNMENT.finditer(text[body_start:body_end])
    ]
    if not writes:
        return Analysis(line_of(text, body_start), [], None, None, [], [])

    entry = read_guard(text, skip_space(text, body_start + 1), body_end)
    covered: list[int] = []
    failures: list[str] = []
    guard_line = line_of(text, entry.start) if entry is not None else None

    if entry is None:
        misplaced = nested_guards(text, body_start + 1, body_end)
        if misplaced:
            failures.append(
                f"the runtime guard on {WRITE} is not at the entry of {FUNCTION}: it sits inside "
                f"the body at line {line_of(text, misplaced[0].start)}, so the write outside that "
                f"branch still sends setProxyConfigurations: on iOS 16"
            )
        else:
            failures.append(
                f"{FUNCTION} writes {WRITE} at lines "
                f"{', '.join(map(str, (line_of(text, offset) for offset in writes)))} with no "
                f"runtime guard; HAVE(NW_PROXY_CONFIG) keys on the SDK version rather than the "
                f"deployment target, so a 16.1 device aborts with "
                f"-[NSURLSessionConfiguration setProxyConfigurations:]: unrecognized selector "
                f"sent to instance"
            )
    else:
        if entry.wrong_polarity:
            failures.append(
                f"the runtime guard at line {guard_line} has the wrong polarity: "
                f"'{entry.condition.strip()}' leaves the function where the API exists, so iOS 17 "
                f"and later would stop applying the proxy configuration"
            )
        for offset in writes:
            if entry.protects(offset):
                covered.append(offset)
            else:
                failures.append(
                    f"the {WRITE} write on line {line_of(text, offset)} is not behind the runtime "
                    f"guard at line {guard_line}; a guard around one branch leaves this send "
                    f"reachable on iOS 16, and the empty branch's write alone aborts the process"
                )

    return Analysis(
        function_line=line_of(text, body_start),
        writes=[line_of(text, offset) for offset in writes],
        guard=entry,
        guard_line=guard_line,
        covered=[line_of(text, offset) for offset in covered],
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require both proxyConfigurations writes in NetworkSessionCocoa.mm to sit behind one "
            f"runtime guard at the entry of {FUNCTION}."
        )
    )
    parser.add_argument("--source", required=True, type=Path,
                        help="NetworkSessionCocoa.mm, patched, in the build tree")
    parser.add_argument("--needs-guard", action="store_true",
                        help="exit 0 when the file needs the patch, 1 when a skip is correct")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"FAIL: {args.source} does not exist, so the guard cannot be checked", file=sys.stderr)
        return 1

    try:
        analysis = analyse(args.source)
    except ValueError as error:
        print(f"FAIL: {args.source} cannot be parsed: {error}", file=sys.stderr)
        if args.needs_guard:
            print(f"FAIL: neither answer is known, so the precheck loop must not read this as a "
                  f"skip (exit {NOT_NEEDED} would mean that)", file=sys.stderr)
        return 1

    if args.needs_guard:
        if analysis.needs_guard:
            print(f"NEEDS GUARD: {FUNCTION} writes {WRITE} at lines "
                  f"{', '.join(map(str, analysis.writes))} with only "
                  f"{len(analysis.covered)} behind a runtime guard, so the patch is required for a "
                  f"16.1 build")
            return 0
        if analysis.function_line is None:
            print(f"NO GUARD NEEDED: {args.source} has no {FUNCTION}, so there is nothing to guard")
        elif not analysis.writes:
            print(f"NO GUARD NEEDED: {FUNCTION} never writes {WRITE}")
        else:
            print(f"NO GUARD NEEDED: every {WRITE} write is already behind a runtime guard")
        return NOT_NEEDED

    if analysis.function_line is None:
        print(f"SKIP: {args.source} has no {FUNCTION}, so there is no proxy configuration write to "
              f"guard")
        print("\nResult: PASS (skipped)")
        return 0
    if not analysis.writes:
        print(f"SKIP: {FUNCTION} in {args.source} never writes {WRITE}, so there is nothing to guard")
        print("\nResult: PASS (skipped)")
        return 0

    print(f"== {args.source}")
    print(f"   {FUNCTION} at line {analysis.function_line}")
    print(f"   {WRITE} writes at lines {', '.join(map(str, analysis.writes))}")
    if analysis.guard is not None:
        print(f"   runtime guard {analysis.guard.kind} at line {analysis.guard_line}")
        print(f"   behind it: {', '.join(map(str, analysis.covered)) or 'nothing'}")
    if analysis.failures:
        # Flush first, so the FAIL lines land after the readings that explain them instead of ahead
        # of them when the two streams are interleaved into one log.
        sys.stdout.flush()
        for failure in analysis.failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"\nResult: FAIL ({len(analysis.failures)} finding(s))", file=sys.stderr)
        return 1
    print(
        f"OK: every {WRITE} write is behind the runtime guard {analysis.guard.kind} at the entry of "
        f"{FUNCTION}, so iOS 16 never sends setProxyConfigurations:"
    )
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
