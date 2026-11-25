#!/usr/bin/env python3
"""Require the iOS 16 message number translation to stay switched off in a same-generation build.

patches/ios16-ipc-message-number-translation.patch adds two things: the translation tables, and a
switch that decides whether a connection uses them. IPC::Connection::m_shouldTranslateMessageNames
defaults to false in Connection.h, and the patch must leave it that way. The tables are compiled in
and inert. This script fails when that stops being true.

What it refuses, and why each combination is a defect rather than a preference:

  - Any call to setShouldTranslateMessageNames(true) in the patched tree. The UI process on this
    deployment loads our own WebKit.framework through scripts/install-webkit-override.sh, so both
    sides of the connection are this build and already number messages identically. Switching
    translation on renumbers messages that were correct. `InitializeConnection` is number 3076 in
    this tree and 2478 in the shipped 16.1 enumeration, and the incoming table is a switch over 16.1
    numbers, so case 3076 answers
    MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged - a real message with no
    relation to the handshake. 3076 is not in isKnownUnmappedIncomingNumber, so the miss path that
    would flag the message is not taken and the rename is silent. ConnectionCocoa.mm's
    `decoder->messageName() == MessageName::InitializeConnection` then never fires, m_isConnected
    stays false, and the page renders blank instead of reporting anything.

  - A member initialiser other than `{ false }` in Connection.h. IPC::Connection::Connection does
    not include MessageNumberTranslation.h, so an initialiser written as a macro name would be an
    undeclared identifier or, worse, a same-named macro from elsewhere; either way the member's
    actual default stops being visible where it is read. The patch keeps the literal.

  - Removal of the rationale. The patch's own comment is the only place that names the measured
    numbers and the wrong-16.1-tag defect. A future edit that deletes the comment and re-adds the
    switch would look like a small change; the anchors checked below make that edit fail here.

The script reads the patch file, not a checkout, so it runs before the build has a tree and cannot
be satisfied by a tree that happens to be patched differently from the deb.

Usage:
  scripts/check-translation-switch.py
  scripts/check-translation-switch.py --patch patches/ios16-ipc-message-number-translation.patch

Exit 0 when the switch is absent and the rationale is present, 1 on any violation, 2 when the patch
cannot be read.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATCH = Path(__file__).resolve().parent.parent / "patches" / "ios16-ipc-message-number-translation.patch"

# The setter the patch adds to Connection.h. Any uncommented mention of a call is a violation; the
# declaration and the member stay, because the flag has to exist to be read.
SETTER = re.compile(r"\bsetShouldTranslateMessageNames\s*\(")
# A call, as opposed to the declaration whose parameter list follows the same name. The declaration
# has a parameter; a call has an argument or no argument at all and is not preceded by a return type.
DECLARATION_HINTS = ("void setShouldTranslateMessageNames", "bool setShouldTranslateMessageNames")

# The member initialiser, which must stay a literal false. Written as a macro it depends on a header
# Connection.h does not include.
MEMBER = re.compile(r"\bbool\s+m_shouldTranslateMessageNames\s*\{([^}]*)\}")

# Text the patch must keep. Each entry is a claim a reader relies on to judge a future change; the
# substrings are short so that rewrapping a comment does not fail the check, and specific enough that
# deleting the fact does.
REQUIRED_RATIONALE = (
    ("InitializeConnection is 3076 in this tree", "3076"),
    ("the 16.1 number for InitializeConnection", "2478"),
    ("the message 3076 is silently renamed to", "RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("the handshake comparison that then never fires", "decoder->messageName() == MessageName::InitializeConnection"),
    ("the same-name count of the index comparison", "5 land on a message with the same name"),
    ("the different-message count", "3180"),
    ("the beyond-16.1-range count", "903"),
    ("the per-index totals", "4088"),
    ("the 16.1 enumeration's size", "3185"),
    ("the tag the 16.1 side of the tables was generated from", "Safari-614.1.9"),
    ("the anchors that tag produces", "firstSynchronousOldMessageNumber 2899"),
    ("the second anchor", "InitializeConnection 2444"),
    ("the third anchor", "Last 3139"),
    ("the tag the device actually runs", "releases/Apple/Safari-16.1-iOS-16.1"),
    ("the anchors the device's build produces", "2946, 2478 and 3184"),
    ("the size of the gap between the two 16.1 enumerations", "45"),
)

# A mention of the setter inside a comment is documentation, not a call. The patch's added lines all
# start with '+', so anchoring the match on the code form keeps comments out of the count without
# trying to parse C++ comments.
ADDED_LINE = re.compile(r"^\+(.*)$")


@dataclass
class Violation:
    reason: str
    detail: str


def strip_comment(line: str) -> str:
    """Drop a trailing // comment so a mention in prose is not read as code."""
    index = line.find("//")
    return line if index < 0 else line[:index]


def added_code_lines(patch: str) -> list[str]:
    """The lines the patch adds, with comments removed and the leading '+' stripped."""
    lines = []
    for raw in patch.splitlines():
        match = ADDED_LINE.match(raw)
        if not match:
            continue
        code = strip_comment(match.group(1)).strip()
        if code:
            lines.append(code)
    return lines


def check_no_setter_call(patch: str) -> list[Violation]:
    violations = []
    for code in added_code_lines(patch):
        if not SETTER.search(code):
            continue
        if any(hint in code for hint in DECLARATION_HINTS):
            continue
        violations.append(
            Violation(
                "the patch calls setShouldTranslateMessageNames",
                "this deployment runs one WebKit build on both sides of the connection, so turning "
                "translation on renumbers messages that already agree and the InitializeConnection "
                "handshake never completes: %s" % code,
            )
        )
    return violations


def check_member_initialiser(patch: str) -> list[Violation]:
    # Every declaration of the member, not just the first: a patch that keeps the literal one and
    # adds a macro-initialised duplicate would otherwise pass on the first match alone.
    declarations = list(MEMBER.finditer(patch))
    if not declarations:
        return [
            Violation(
                "the flag member is missing from the patch",
                "Connection.h must carry `bool m_shouldTranslateMessageNames { false };`; without it "
                "the patch's reads do not compile",
            )
        ]
    violations = []
    for found in declarations:
        initialiser = found.group(1).strip()
        if initialiser != "false":
            violations.append(
                Violation(
                    "the flag's initialiser is not the literal false",
                    "Connection.h does not include MessageNumberTranslation.h, so an initialiser "
                    "written as a macro never sees that macro and does not mean what it says; "
                    "found {%s}" % initialiser,
                )
            )
    return violations


def check_rationale(patch: str) -> list[Violation]:
    violations = []
    for description, needle in REQUIRED_RATIONALE:
        if needle not in patch:
            violations.append(
                Violation(
                    "the patch no longer records %s" % description,
                    "expected the text %r in the patch; a reader needs it to judge the next change "
                    "to the switch" % needle,
                )
            )
    return violations


def run(patch_path: Path) -> int:
    try:
        patch = patch_path.read_text(encoding="utf-8")
    except OSError as error:
        print("check-translation-switch: cannot read %s: %s" % (patch_path, error), file=sys.stderr)
        return 2

    violations = []
    violations += check_no_setter_call(patch)
    violations += check_member_initialiser(patch)
    violations += check_rationale(patch)

    if violations:
        for violation in violations:
            print("check-translation-switch: %s" % violation.reason, file=sys.stderr)
            print("  %s" % violation.detail, file=sys.stderr)
        return 1

    print("check-translation-switch: no call to setShouldTranslateMessageNames, the flag keeps its "
          "false initialiser, and the rationale is intact")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Require the iOS 16 message number translation to stay switched off."
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default=DEFAULT_PATCH,
        help="the translation patch to read (default: %(default)s)",
    )
    return run(parser.parse_args(argv).patch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
