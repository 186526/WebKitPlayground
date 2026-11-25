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
    this tree, and the incoming table switches over the older numbering the tables were generated
    from, so case 3076 answers MessageName::WebPageProxy_RootViewToAccessibilityScreen - the message
    that occupies 3076 in the Safari-614.1.9 snapshot, and a real message with no relation to the
    handshake. Against the shipped 16.1 enumeration the same number instead names
    MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged, which is what a peer
    running that build would be misread as. Either way 3076 is not in isKnownUnmappedIncomingNumber,
    so the miss path that would flag the message is not taken and the rename is silent.
    ConnectionCocoa.mm's `decoder->messageName() == MessageName::InitializeConnection` then never
    fires, m_isConnected stays false, and the page renders blank instead of reporting anything.

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
# needles are short so that rewrapping a comment does not fail the check, and specific enough that
# deleting or altering the fact does.
#
# They are matched against the patch's added comment prose only, never against its code. Several of
# the numbers below are also real case labels in the tables (`case 3135:`, `return 946`), so a
# search over the whole patch would match them whatever the prose said, and a count that drifted
# back to a wrong value would still find a match in the code. For the same reason the counts carry
# the words around them rather than standing alone: `946` on its own still occurs inside the
# device anchor `2946`, which is a different claim.
#
# Every 16.1 mention in the patch is qualified, because the tables are NOT built on shipped 16.1:
# they come from the 614.1.9 snapshot, which is the iOS 16.0 generation. The entries below pin both
# bases and the counts for both, so an edit cannot quietly drop one and leave the patch claiming a
# basis it does not have.
REQUIRED_RATIONALE = (
    ("InitializeConnection is 3076 in this tree", "3076"),
    ("the 614.1.9 number for InitializeConnection", "2444 and Last 3139"),
    ("the message 3076 silently becomes, per the 614.1.9 basis",
     "case 3076 with MessageName::WebPageProxy_RootViewToAccessibilityScreen"),
    ("the message 3076 would become against shipped 16.1",
     "names MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("the handshake comparison that then never fires",
     "decoder->messageName() == MessageName::InitializeConnection"),
    ("the same-name count of the index comparison", "5 land on a message"),
    ("the different-message count against shipped 16.1", "3180"),
    ("the beyond-range count against shipped 16.1", "and 901 fall off"),
    ("the net growth over the 614.1.9 snapshot", "946 more entries"),
    ("the different-message and beyond-range counts against the 614.1.9 basis",
     "5, 3135 and 946. Both readings"),
    ("the tree's own size", "4086 numbers"),
    ("the shipped 16.1 enumeration's size", "3185"),
    ("the tag the tables were generated from", "Safari-614.1.9"),
    ("the statement that the tables are a 16.0-generation snapshot", "16.0-generation snapshot"),
    ("the first anchor that tag produces", "firstSynchronousOldMessageNumber 2899"),
    ("the tag the device actually runs", "releases/Apple/Safari-16.1-iOS-16.1"),
    ("the anchors the device's build produces", "2946, 2478 and 3184"),
    ("the gap at Last between the two enumerations", "45 at"),
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


def added_comment_prose(patch: str) -> str:
    """The comment text the patch adds, as one whitespace-normalised string for the needles.

    Code is left out on purpose: `case 3135:` and `return 946` are real table entries, so allowing
    them into the haystack would let every count needle match even after the prose around it had
    been changed to a wrong value. Line breaks are collapsed too, so a needle that a comment
    rewrap splits across two lines still matches while its wording stays the same.
    """
    prose = []
    for raw in patch.splitlines():
        match = ADDED_LINE.match(raw)
        if not match:
            continue
        line = match.group(1)
        index = line.find("//")
        if index < 0:
            continue
        prose.append(line[index + 2:])
    return " ".join(" ".join(prose).split())


def check_rationale(patch: str) -> list[Violation]:
    violations = []
    prose = added_comment_prose(patch)
    for description, needle in REQUIRED_RATIONALE:
        if needle not in prose:
            violations.append(
                Violation(
                    "the patch no longer records %s" % description,
                    "expected the text %r in the patch; a reader needs it to judge the next change "
                    "to the switch" % needle,
                )
            )
    return violations


# A bare "16.1" next to the word "table" is the claim this check exists to prevent: the tables come
# from the 614.1.9 snapshot, which is the iOS 16.0 generation, so calling them 16.1 would tell a
# reader they cover the build on the device when they do not. A mention is qualified when the same
# line, or the line after, names the tag, the snapshot, or the fact that it is not 16.1.
UNQUALIFIED = re.compile(r"\btables?\b", re.IGNORECASE)
QUALIFIERS = ("Safari-614.1.9", "614.1.9", "16.0-generation", "snapshot", "not 16.1", "NOT built on")


def check_no_unqualified_table_claim(patch: str) -> list[Violation]:
    lines = patch.splitlines()
    violations = []
    for index, raw in enumerate(lines):
        match = ADDED_LINE.match(raw)
        if not match:
            continue
        text = match.group(1)
        if "16.1" not in text or not UNQUALIFIED.search(text):
            continue
        window = " ".join(lines[index:index + 2])
        if any(qualifier in window for qualifier in QUALIFIERS):
            continue
        violations.append(
            Violation(
                "the patch calls the translation tables 16.1 without saying which 16.1",
                "the tables are built on the 614.1.9 snapshot, which is the iOS 16.0 generation, "
                "and the device runs releases/Apple/Safari-16.1-iOS-16.1; a bare 16.1 reads as the "
                "latter, which these tables do not cover: %s" % text.strip(),
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
    violations += check_no_unqualified_table_claim(patch)

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
