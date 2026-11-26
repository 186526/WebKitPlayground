#!/usr/bin/env python3
"""Require the iOS 16 message number translation to stay switched off in a same-generation build.

patches/ios16-ipc-message-number-translation.patch adds two things: the translation tables, and a
switch that decides whether a connection uses them. IPC::Connection::m_shouldTranslateMessageNames
defaults to false in Connection.h, and the patch must leave it that way. The tables are compiled in
and inert. This script fails when that stops being true.

What it refuses, and why each combination is a defect rather than a preference:

  - Any call to setShouldTranslateMessageNames(true) in the patched tree, including a call split
    across lines. The check reads whole statements, not lines, so folding the call after the opening
    parenthesis does not hide it. The UI process on this deployment loads our own WebKit.framework
    through scripts/install-webkit-override.sh, so both sides of the connection are this build and
    already number messages identically. Switching translation on renumbers messages that were
    correct. `InitializeConnection` is number 3076 in
    this tree, and the incoming table switches over the shipped 16.1 numbering, so case 3076 answers
    MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged - a real message with no
    relation to the handshake. 3076 is not in isKnownUnmappedIncomingNumber,
    so the miss path that would flag the message is not taken and the rename is silent.
    ConnectionCocoa.mm's `decoder->messageName() == MessageName::InitializeConnection` then never
    fires, m_isConnected stays false, and the page renders blank instead of reporting anything.

  - A member initialiser other than `{ false }` in Connection.h. IPC::Connection::Connection does
    not include MessageNumberTranslation.h, so an initialiser written as a macro name would be an
    undeclared identifier or, worse, a same-named macro from elsewhere; either way the member's
    actual default stops being visible where it is read. The patch keeps the literal.

  - Removal of the rationale. The patch's own comment is the only place that names the measured
    numbers and the wrong-16.1-tag defect. A future edit that deletes the comment and re-adds the
    switch would look like a small change; the anchors checked below make that edit fail here. Each
    anchor is a phrase that occurs exactly once in the prose, and the tests assert that, because a
    bare number occurs many times and would keep matching after its sentence was rewritten.

  - A table entry that disagrees with the prose. The prose check reads comments only, so a renumbered
    `case` or a mapping retargeted at another real message would otherwise pass. The tables are what
    a translated connection dispatches on, so the anchor pairs the prose names are read back out of
    the added code and compared. Three of the nine pairs are not named in the prose at all: they are
    read from the tables themselves and pinned because they are the neighbours of the pairs the prose
    does name, and a silent rename would move them instead.

  - Prose that sends a reader back to the 16.0-generation snapshot as the tables' basis. The tables
    were once rendered from Safari-614.1.9, and that basis is what translated the handshake to an
    unrelated message. A line that names 614.1.9 is allowed only when the same line, or the line
    after, calls it superseded, earlier or wrongly based.

  - A translation call that does not sit under the member. The flag is a setter call and two reads;
    guarding only the call leaves the reads, and a read is the cheapest place to make translation
    unconditional. Every added statement that calls `translateOutgoingMessageName` or
    `translateIncomingMessageName` must sit under an `if` whose condition cannot hold while the switch
    is off. A bare `{` runs the block always, and `IOS_16_MESSAGE_NUMBER_TRANSLATION` is 1 in this
    build because the deployment target is 16.1, so substituting the macro is the same as a bare brace
    with a reassuring name. The condition is read by its boolean structure rather than searched for the
    member's name, because `!m_shouldTranslateMessageNames`, `m_shouldTranslateMessageNames == false`
    and `m_shouldTranslateMessageNames || fallback` all mention the member and all run the block
    exactly when the switch is off. `UNLIKELY(...)`, `LIKELY(...)` and `static_cast<bool>(...)` are
    transparent: they return their argument's boolean value, and the guard is a hot-path check that a
    future edit would plausibly wrap in one, so they are read as the condition they wrap rather than
    refused. A guard written with its body on the same line — `if (m) { return; }`, `if (m) { }`,
    `if (m) goto done;` — is a complete statement that guards nothing after it, and so is one whose
    body does not open where the statement ends: `if (m) { } else if (ok) {` and `if (m) { x(); } do {`
    end with a brace that belongs to a branch which runs with the member false. Only two shapes put
    the guarded body right after the condition — nothing at all (the next statement is the body) or an
    opening `{`. A comment between the two is not part of the shape. An `else if (m) {` guards its own
    branch, and the last branch is the one read, so `if (ready) { } else if (m) {` is correctly
    guarded; the closing braces in front of it are stripped whatever their spacing, so a branch headed
    by `} } else if (m) {` is read as the same branch. A guard block ends where its `}` sits, not where
    its line ends: `if (m) { ... return; }` and `if (m) { ... } }` both close the block for the
    statements after them. An identifier that ends in `if` is not a branch head, so `m && notif(x)` is
    read as the conjunction it is. Comments and literals are read through one lexical view rather than
    at each site, so a `}` inside `"..."`, `'...'` or a comment does not close a block and a `{` inside
    one does not hide the closure that follows it; a `//` inside a string does not cut the statement
    short. A caller added to a third file is checked too, because the rule is a property of the call and
    not a list of the two files that first had one; only the two functions' own definitions and their
    header declarations are exempt, and that is decided by the file plus the declaration, so a new
    caller inside an exempt file still has to be guarded.

  - An assignment to the member outside the setter. It may only be written from the setter's own
    parameter. `= true`, a compound `|=`, and a parenthesised `(member) =` all compile and all turn
    the flag on; each is refused, while `==` stays a comparison.

The script reads the patch file, not a checkout, so it runs before the build has a tree and cannot
be satisfied by a tree that happens to be patched differently from the deb.

Usage:
  scripts/check-translation-switch.py
  scripts/check-translation-switch.py --patch patches/ios16-ipc-message-number-translation.patch

Exit 0 when the switch is absent, the reads ask the member, the rationale is present and the table
anchors hold; 1 on any violation; 2 when the patch cannot be read.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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

# Text the patch must keep. Each entry is a claim a reader relies on to judge a future change.
#
# Every needle must occur exactly once in the patch's added comment prose. A needle that is a bare
# number or a bare tag name occurs several times in this file, so altering or deleting the sentence
# it came from would leave other occurrences matching and the check would pass: `2478` alone appears
# both as a prose anchor and as a table case label, and the releases tag appears three times. Each
# needle therefore carries the words that make it unique, and `test-check-translation-switch.py`
# asserts the uniqueness as an invariant rather than leaving it to be re-checked by eye.
#
# The needles are matched against the added comment prose only, never against code. Several of the
# numbers below are also real case labels in the tables (`case 2478:`, `return 3086`), so a search
# over the whole patch would match them whatever the prose said.
#
# Both sides of the comparison come from shipped tags: the older side is
# releases/Apple/Safari-16.1-iOS-16.1, which is the build on the device, and the newer side is
# releases/Apple/Safari-18.3.1-iOS-18.3.2. The entries below pin the identity of the older tag, its
# size, the six counts the comparison produces and the four anchors the tables were rendered with.
# The older tag is the whole point of the table: an earlier table was rendered from a 16.0-generation
# snapshot and translated the handshake to an unrelated message, so a needle naming the tag is what
# keeps that regression from coming back unnoticed.
REQUIRED_RATIONALE = (
    ("InitializeConnection is 3076 in this tree", "`InitializeConnection` is number 3076 in this tree"),
    ("the shipped 16.1 number for InitializeConnection and the incoming case that reads it",
     "puts it at 2478, and 2478 is the entry the incoming table needs"),
    ("that incoming case written out", "`case 2478: return MessageName::InitializeConnection;`"),
    ("the message 3076 is read as against the shipped 16.1 enumeration",
     "is MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("the handshake comparison that then never fires",
     "`decoder->messageName() == MessageName::InitializeConnection`"),
    ("the same-number count of the index comparison",
     "5 happen to carry the same number on both sides"),
    ("the different-number count", "and 2906 do not"),
    ("the count of 16.1 numbers past this enumeration's end", "no 16.1 number falls off this enumeration's end"),
    ("the entry-count difference, written as a subtraction", "4086 - 3185 = 901 entries were inserted"),
    ("the two one-sided counts", "274 names exist only in 16.1 and 1175 only in this tree"),
    ("the tree's own size", "This tree has 4086 numbers"),
    ("the shipped 16.1 enumeration's size and tag",
     "shipped 16.1 enumeration (releases/Apple/Safari-16.1-iOS-16.1) has 3185"),
    ("the tag the tables were generated from", "generated from that shipped 16.1 tag"),
    ("one unmapped incoming message, by number", "`Terminate` is number 2483"),
    ("the first anchor the older tag produces",
     "Read from the 16.1 header's own messageIsSync comparison rather than counted here"),
    ("the tag's own version, which identifies the build on the device",
     "releases/Apple/Safari-16.1-iOS-16.1 (614.2.9.0.10), the same build the system"),
    ("the three anchors read out of the tag's header", "2946, InitializeConnection 2478 and Last 3184"),
    ("the split of the 1175 one-sided names around the synchronous boundary",
     "852 of the 1126 that are outside it"),
    ("the generator's exit status and the four counts it prints",
     "`scripts/gen-ipc-message-number-tables.py` exits 0 (rc=0) and reports four"),
    ("the unmapped-incoming count, stated as a count",
     "1175 only newer, and 708 unmapped"),
)

# A mention of the setter inside a comment is documentation, not a call. The patch's added lines all
# start with '+', so anchoring the match on the code form keeps comments out of the count without
# trying to parse C++ comments.
ADDED_LINE = re.compile(r"^\+(.*)$")


@dataclass
class Violation:
    reason: str
    detail: str


# The one lexical view every structural decision reads. C++ comments and literals are file-wide rules,
# and the checks below used to re-derive them at each site: `strip_comment` cut at the first `//` even
# when it sat inside a string, and the brace counters read a `{` inside a literal as a block. That made
# every round of review find a new spelling on the same seam. The scan below is the only place that
# reads a character of the raw statement as lexical structure.
#
# The order is the rule, not a preference. A comment is skipped before a literal is looked for, because
# an apostrophe in ordinary prose — `/* don't */`, `// it's` — would otherwise open a character literal
# and swallow the rest of the line. A literal is skipped before a `//` or a `/*` inside it is looked for,
# because `log("http://x")` and `"a /* b"` carry those sequences as data.
#
# What a literal's content is replaced with. `?` is not a token the condition grammar knows, not an
# operator, and not a brace or parenthesis, so a condition that contains a literal stays unreadable to
# the evaluator and is refused — which is the direction that matters. The quotes go too: leaving `"`
# in place would let the next literal's opening quote be found inside the replacement.
LITERAL_SENTINEL = "?"
# A comment becomes a single space, which is what separates the code on either side of it without
# joining two tokens into one.
COMMENT_SENTINEL = " "
# `R"delimiter(`, with an empty delimiter allowed. Matched at the position the scan is on, so the anchor
# is the caller's loop rather than `^`.
RAW_STRING_HEAD = re.compile(r'R"(?P<delimiter>[^()\\\s]{0,16})\(')
# A pp-number as C++14 spells a digit separator: `1'000`, `0xFF'FF`, `1'000'000`. The apostrophe there
# is punctuation inside a number, not a quote, and reading it as one swallowed the rest of the
# statement: `RELEASE_LOG(IPC, "%d", 1'000); }` counted no closer, so a setter call written after it
# passed the whole check.
#
# The rule is the one the standard states for a pp-number, evaluated left of the apostrophe. Everything
# from the start of the identifier-like run must be `[0-9A-Za-z_.]` and the run must start with a
# digit; the character after the apostrophe must be alphanumeric. `u8'1'` is a character constant
# because its prefix starts with `u`, and `'}'` is one because the character before it is not part of a
# digit-leading run.
PP_NUMBER_RUN = re.compile(r"[0-9A-Za-z_.]+$")
DIGIT = re.compile(r"[0-9]")
ALPHANUMERIC = re.compile(r"[0-9A-Za-z]")


class LexicalRegion(NamedTuple):
    """One run of comment or literal text, by raw offset, and whether its delimiter pair closed."""

    kind: str
    start: int
    end: int
    closed: bool


def is_digit_separator(statement: str, position: int) -> bool:
    """True when the apostrophe at `position` sits inside a pp-number, as `1'000` does.

    C++14 lets a digit separator appear between digits of a number. The test is the pp-number rule read
    from the left: the run of identifier-like characters ending just before the apostrophe must be
    non-empty, must consist of `[0-9A-Za-z_.]`, and must start with a digit; the character after the
    apostrophe must be alphanumeric. Every other apostrophe opens a character constant.

    Both halves are load-bearing, on different shapes. The start-digit half keeps `u8'1'` a character
    constant, because `u8` is an encoding marker rather than a number, and it is also what refuses
    `'1'`, whose run before the quote is empty. The following-character half keeps `1';` a quote: a
    run that starts with a digit is not enough on its own, because the character after the apostrophe
    decides whether a number continues there.
    """
    run = PP_NUMBER_RUN.search(statement, 0, position)
    if not run or not DIGIT.match(run.group(0), 0):
        return False
    following = position + 1
    return following < len(statement) and bool(ALPHANUMERIC.match(statement, following))


def lexical_regions(statement: str) -> list[LexicalRegion]:
    """The spans of the statement that are comment or literal text.

    A statement is a fragment of a line, so a `/*` with no `*/` runs to the end of the fragment and a
    quote with no partner does the same; `closed` records which happened, because an unterminated
    block comment makes the fragment malformed in a way callers still refuse.

    A raw string is read by its own rule — `R"delim( ... )delim"` — and before the plain literal case,
    because `R"x(})x"` would otherwise be read as `R` followed by the literal `"x(}"`, leaving the `)x"`
    that follows as code. The delimiter is copied out of the opener, so a quote or a brace between the
    parentheses is text here exactly as it is text to the compiler.
    """
    regions: list[LexicalRegion] = []
    position = 0
    length = len(statement)
    while position < length:
        if statement.startswith("/*", position):
            end = statement.find("*/", position + 2)
            closed = end >= 0
            end = length if not closed else end + 2
            regions.append(LexicalRegion("comment", position, end, closed))
            position = end
            continue
        if statement.startswith("//", position):
            regions.append(LexicalRegion("comment", position, length, True))
            position = length
            continue
        raw = RAW_STRING_HEAD.match(statement, position)
        if raw:
            terminator = ")" + raw.group("delimiter") + '"'
            end = statement.find(terminator, raw.end())
            closed = end >= 0
            end = length if not closed else end + len(terminator)
            regions.append(LexicalRegion("literal", position, end, closed))
            position = end
            continue
        opener = statement[position]
        if opener in ('"', "'"):
            if opener == "'" and is_digit_separator(statement, position):
                position += 1
                continue
            cursor = position + 1
            while cursor < length:
                if statement[cursor] == "\\":
                    cursor += 2
                    continue
                if statement[cursor] == opener:
                    cursor += 1
                    break
                cursor += 1
            closed = cursor <= length and cursor > position + 1 and statement[cursor - 1] == opener
            end = min(cursor, length)
            regions.append(LexicalRegion("literal", position, end, closed))
            position = end
            continue
        position += 1
    return regions


def code_view(statement: str) -> str:
    """The statement with comments and literal contents replaced, so structure can be read from it.

    Length is not preserved: a region becomes one sentinel character, which is all the brace counters
    and the condition evaluator need. The result is what `count_unmatched_closers`, `if_parts`,
    `condition_implies_member`, `strip_comment` and the condition tokeniser read; none of them look at
    the raw statement again.
    """
    view: list[str] = []
    cursor = 0
    for region in lexical_regions(statement):
        view.append(statement[cursor:region.start])
        view.append(COMMENT_SENTINEL if region.kind == "comment" else LITERAL_SENTINEL)
        cursor = region.end
    view.append(statement[cursor:])
    return "".join(view)


def has_unterminated_block_comment(statement: str) -> bool:
    """True when a `/*` in the statement has no `*/` before the statement ends."""
    return any(region.kind == "comment" and not region.closed
               for region in lexical_regions(statement))


def strip_comment(line: str) -> str:
    """Drop a trailing comment so a mention in prose is not read as code.

    Reads the lexical regions rather than searching the raw text, so a `//` inside a string is left
    alone: `log("http://x")` used to be cut at the `//` and lose the rest of the line, including the
    brace that closed a guard.
    """
    for region in lexical_regions(line):
        if region.kind == "comment" and line.startswith("//", region.start):
            return line[:region.start]
    return line



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


# A statement is what a call has to be read as: `setShouldTranslateMessageNames` and its argument may
# sit on different lines, and a check that reads one line at a time misses the call the moment it is
# wrapped. The added code is joined back into statements with the usual continuation shapes: an
# unclosed parenthesis, a trailing comma or arrow, or a next line that opens with a call or member
# access. Measured against the committed patch: 7670 added lines become 7662 statements, so 8 joins,
# and all 8 fall in two places — the licence header (7) and a project.pbxproj list (1). None of the
# 5953 `case` statements is joined into its neighbour, which is what keeps a correct patch from being
# read as a violation.
CONTINUES_AFTER = (",", "\\", "&&", "||", "->", ".", "=")
# `=` is in this set too: `m_shouldTranslateMessageNames\n    = true;` puts the operator at the head of
# the second line, and splitting there leaves a statement with no member in it to check.
CONTINUES_BEFORE = ("(", ".", "->", ",", "=")
FILE_HEADER = re.compile(r"^diff --git a/(\S+) b/")


def added_code_statements(patch: str) -> list[tuple[str, str]]:
    """The patch's added code as (file, statement) pairs, with wrapped statements joined.

    Statements are joined within one file only: a call split across a file boundary is not a call.
    """
    per_file: list[tuple[str, str]] = []
    path = ""
    for raw in patch.splitlines():
        header = FILE_HEADER.match(raw)
        if header:
            path = header.group(1)
            continue
        match = ADDED_LINE.match(raw)
        if not match:
            continue
        code = strip_comment(match.group(1)).strip()
        if code:
            per_file.append((path, code))

    statements: list[tuple[str, str]] = []
    current_path, current, depth = "", "", 0
    for index, (line_path, code) in enumerate(per_file):
        if current and line_path != current_path:
            statements.append((current_path, current))
            current, depth = "", 0
        current_path = line_path
        # A preprocessor directive is one line by definition, and the line after it is a statement of
        # its own even when it opens with `(`. Joining across that boundary would report an assignment
        # as part of the enclosing `#if` and could hide which statement actually carried it.
        if code.startswith("#"):
            if current:
                statements.append((current_path, current))
                current, depth = "", 0
            statements.append((line_path, code))
            continue
        current = code if not current else current + " " + code
        depth = max(0, depth + code.count("(") - code.count(")"))
        following = per_file[index + 1][1] if index + 1 < len(per_file) and per_file[index + 1][0] == line_path else ""
        # A line that opens or closes a block ends the statement there. Without this a body written as
        # `{` on its own line is joined to the first line of that body whenever the body starts with a
        # `(` — the argument of a `(void)param;` or a `(Type)value` cast — and the `{` then never opens
        # the block, so everything inside it is read as unguarded and unsettered.
        if current.endswith("{"):
            statements.append((current_path, current))
            current, depth = "", 0
            continue
        if depth > 0 or current.endswith(CONTINUES_AFTER) or (following and following.startswith(CONTINUES_BEFORE)):
            continue
        statements.append((current_path, current))
        current, depth = "", 0
    if current:
        statements.append((current_path, current))
    return statements


def check_no_setter_call(patch: str) -> list[Violation]:
    violations = []
    for path, statement in added_code_statements(patch):
        view = code_view(statement)
        if not SETTER.search(view):
            continue
        if any(hint in view for hint in DECLARATION_HINTS):
            continue
        violations.append(
            Violation(
                "the patch calls setShouldTranslateMessageNames",
                "this deployment runs one WebKit build on both sides of the connection, so turning "
                "translation on renumbers messages that already agree and the InitializeConnection "
                "handshake never completes: %s: %s" % (path, statement),
            )
        )
    return violations


# The flag is read at exactly these two places, and each read guards one translation call. Pinning the
# guard by the call it wraps rather than by a line number is what makes "the condition is unchanged"
# a fact about the code: replacing the condition with a bare `{` removes the `if` statement, and
# replacing it with IOS_16_MESSAGE_NUMBER_TRANSLATION keeps an `if` but not this one.
READ_SITES = (
    ("Source/WebKit/Platform/IPC/Connection.cpp", "translateOutgoingMessageName(",
     "the send path that rewrites an outgoing message number"),
    ("Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm", "translateIncomingMessageName(",
     "the receive path that rewrites an incoming message number"),
)

# The member may be written from one place only: the setter's own body, from its parameter. Any other
# assignment reaches the same state without a call, which is the path check_no_setter_call cannot see.
#
# The left side allows any number of parentheses: `((m_shouldTranslateMessageNames)) = true` compiles
# and turns the flag on exactly as the single-parenthesis form does. The operator is a plain or
# compound assignment; `(?<![=!<>+\-*/%&|^])` keeps `==` a comparison and refuses to read `<=`/`>=`/
# `!=` as an assignment to a member named on the right of them. `\s*` spans a newline, so an operator
# on the next line is still the same assignment once the statements are joined.
ASSIGNMENT = re.compile(
    r"\(*\s*\bm_shouldTranslateMessageNames\s*\)*\s*"
    r"(?<![=!<>+\-*/%&|^])(?:[|&^+\-*/%]|<<|>>)?=(?!=)\s*([^=;]+);"
)
# The setter's definition is matched by shape, not by one literal line: written across three lines it
# still opens a block, and the assignment inside it is legitimate. The `{` is optional because the
# statement may be the declaration line with the body beginning on the next one.
SETTER_DEFINITION = re.compile(
    r"^void\s+setShouldTranslateMessageNames\s*\([^)]*\)\s*[;{]?$")
# The one-line form keeps the definition and the assignment in a single statement, so no block chain
# holds the definition. The head is what identifies it, and the body has to follow it.
SETTER_HEADER = re.compile(r"^void\s+setShouldTranslateMessageNames\s*\(")

# The two functions are declared in MessageNumberTranslation.h and defined in Encoder.cpp. Inside
# them the call is the translation rather than a use of it, and the header's two lines declare them,
# so those statements are the only ones whose enclosing block does not have to be the guard. The test
# is the file plus the declaration shape, with the return type required: a bare `translateX(name);`
# at the head of a statement has no `MessageName` in front of it and is therefore still a call. Adding
# a caller in either file still has to be guarded; a list of call sites would have the hole this check
# was rewritten to close.
TRANSLATION_DEFINITION = re.compile(
    r"^MessageName\s+translate(?:Outgoing|Incoming)MessageName\s*\([^)]*\)\s*[;{]?$")
DEFINITION_FILES = (
    "Source/WebKit/Platform/IPC/MessageNumberTranslation.h",
    "Source/WebKit/Platform/IPC/MessageNumberTranslation.cpp",
    "Source/WebKit/Platform/IPC/Encoder.cpp",
)
TRANSLATE_CALL = re.compile(r"\btranslate(?:Outgoing|Incoming)MessageName\s*\(")


def unmatched_closer_offsets(view: str) -> list[int]:
    """Offsets, in a lexical view, of the `}` that close a block opened before the statement started.

    A `}` is paired against a `{` from the same statement first, so a braced initialiser or a one-line
    function body contributes nothing. Each surviving `}` closes something that was already open, and
    `count_unmatched_closers` and `last_unmatched_closer` are two readings of this one list.
    """
    offsets = []
    opened = 0
    for offset, character in enumerate(view):
        if character == "{":
            opened += 1
        elif character == "}":
            if opened:
                opened -= 1
            else:
                offsets.append(offset)
    return offsets


def count_unmatched_closers(statement: str) -> int:
    """How many `}` in the statement close a block that was open before the statement started.

    A brace that the same statement opened is matched to it, so a braced initialiser or a one-line
    function body contributes nothing. Every `}` that survives that pairing closes something outside:
    `}`, `} else if (m) {`, `x(); }`, `} }` and `}}` all count, which is the point — the closure of a
    guard block ends a statement wherever the brace sits on the line, and counting only the leading run
    let a guard stay on the stack after `return; }` had closed it.

    The braces are read from the lexical view, so a `{` or `}` that is literal or comment text is not
    counted. `RELEASE_LOG(IPC, "closing {"); }` closes one block; without the view its string's `{`
    cancelled the real `}`, the guard stayed on the stack, and the call that followed was accepted.
    """
    return len(unmatched_closer_offsets(code_view(statement)))


def last_unmatched_closer(view: str) -> int:
    """Offset of the last `}` in the view that closes a block opened before the statement, or -1.

    A call after this offset has left every block the statement started inside, so the enclosing chain
    no longer describes where the call is. `enclosing_blocks` samples the stack before the statement's
    closers pop it, which is right for a call at the statement's head and wrong for one written after
    `pending(); }` on the same statement.
    """
    offsets = unmatched_closer_offsets(view)
    return offsets[-1] if offsets else -1


def enclosing_blocks(statements: list[tuple[str, str]]) -> list[list[str]]:
    """For each statement, the chain of blocks around it, innermost first.

    A chain rather than the innermost block alone: a call wrapped in a `for` inside the guard is still
    guarded, and comparing only the innermost block reported `for (...)` and failed correct code. The
    guard is satisfied when the chain contains it at any depth.

    A statement opens a block when it *ends* with `{`, and closes one for every `}` in it that no `{`
    of the same statement closes. That matters: `Locker locker { reportedUnmappedNamesLock };` and
    `bool m_x { false };` carry braces but end with `;`, so a naive brace count would treat the braced
    initialiser as a block and report the wrong guard. Counting the `}` anywhere rather than only the
    leading run is what keeps `if (m) { ... return; }` from leaving the guard on the stack: the closure
    ends the statement, not the line, so a `}` at the end or in the middle closes the block exactly as
    one at the start does, and `} }` closes two.

    A statement can do both. `} else if (m) {` closes the branch above it and opens the branch it heads,
    so the leading `}` is counted and the block is still opened. Treating "starts with `}`" as
    "closes and opens nothing" left the `else if` body outside any block, so a call inside it read as
    unguarded.

    A body opened on its own line is recorded as its definition, not as the bare `{`: the setter may
    be written as three lines, and the chain would otherwise hold a `{` that says nothing about what
    opens it.
    """
    blocks: list[list[str]] = []
    stack: list[str] = []
    current_path = None
    for index, (path, statement) in enumerate(statements):
        if path != current_path:
            stack = []
            current_path = path
        blocks.append(list(reversed(stack)))
        for _ in range(min(count_unmatched_closers(statement), len(stack))):
            stack.pop()
        if code_view(statement).endswith("{"):
            opener = statement
            if statement == "{" and index > 0 and statements[index - 1][0] == path:
                previous = statements[index - 1][1]
                if SETTER_DEFINITION.match(code_view(previous)):
                    opener = previous
            stack.append(opener)
    return blocks


# The member as the condition can spell it. `this->m_shouldTranslateMessageNames` is the same member.
MEMBER_NAME = "m_shouldTranslateMessageNames"
# `this->` is the same object as the implicit one, so the prefix is dropped before the condition is
# parsed. Any other object's member keeps its `->`, which makes the tokeniser fail and the condition
# be refused, which is the safe answer for a read on some other connection.
THIS_PREFIX = re.compile(r"\bthis\s*->\s*")
# `else if (m) {` guards its own block exactly as `if (m) {` does, so the head accepts both. The
# pattern matches the head and its opening parenthesis, so `if_parts` can take the last one in the
# statement: a chain whose last branch is an `else if` on the member is correct code, and it is that
# branch's condition, not the first one's, that decides whether the call is guarded.
#
# The `\b` before `if` is what keeps an identifier that ends in `if` from being read as a branch head.
# `notif(x)`, `find_if(...)` and `std::count_if(...)` all contain `if(`, and without the boundary the
# last of them wins `heads[-1]` and the condition is cut inside the callee's name.
IF_HEAD = re.compile(r"^(?:else\s+)?if\b")
IF_BRANCH = re.compile(r"(?:else\s+)?\bif\s*\(")

# The three wrappers that return their argument's boolean value, so a guard written with one asks the
# same question as the bare condition and has to be read the same way.
BOOLEAN_WRAPPER = re.compile(
    r"(?:\b(?:UN)?LIKELY\s*|\bstatic_cast\s*<\s*bool\s*>\s*)\(")

# A guard is not "a condition that mentions the member": `!m`, `m == false`, `m != true` and
# `m || fallback` all run the block exactly when the switch is off. The condition has to imply the
# member, so it is parsed and evaluated with the member false, and accepted only when the result is
# definitely false. The values are three-valued: `True`, `False`, and `UNKNOWN` for anything the
# grammar does not cover, so an unhandled shape is a refused patch rather than an unchecked one.
UNKNOWN = None
CONDITION_TOKEN = re.compile(
    r"\s*(?:(?P<operator>==|!=|&&|\|\||!|\(|\))|(?P<name>[A-Za-z_][A-Za-z_0-9]*))")


def if_parts(statement: str) -> tuple[str, str] | None:
    """The last `if`'s condition and the text after its closing parenthesis, or None.

    The remainder is what separates the shapes the guard check has to tell apart: a braceless guard is
    an `if` with nothing after the parenthesis (`""`), an ordinary block guard is one whose body opens
    where the statement ends (`"{"`), and anything else — `{ return; }`, `goto done;`, `{ } else if
    (ok) {`, `{ x(); } do {` — is a complete statement that guards nothing after it.

    The last branch is the one that guards what follows. In `if (ready) { } else if (m) {` the body is
    that of the `else if`, so it is `m` and not `ready` that has to imply the member. Reading the first
    branch instead refused this correct code, and reading only "does the statement end with `{`" let
    the `else` block through.

    Block comments are stripped from the remainder before it is returned, because `if (m) /* guard */`
    is a braceless guard that a comment happens to follow. `//` comments never reach here: whole-line
    comments are dropped when the patch is read, and a trailing one is cut before statements are
    joined. An unterminated block comment means the statement is malformed and the answer is None.

    Comments and literals are read through `code_view`, so a `}` inside `"..."`, `'...'` or a comment
    cannot close a branch and a `/*` inside a string cannot be mistaken for a comment. The condition is
    taken from the raw statement but cut at offsets the view agrees on, because the condition text has
    to keep its identifiers; a literal in the condition becomes the sentinel, which the evaluator
    refuses.
    """
    view = code_view(statement)
    stripped = view.strip()
    # A branch that follows a closing brace starts with it: `} else if (m) {` heads the branch it
    # opens, and the leading `}` belongs to the branch above. Dropping it here is what lets the chain
    # read that branch as a guard; `} else {` still fails the head test below.
    #
    # The braces are stripped as a pair, whitespace and all, because the statement joiner puts a space
    # between two lines: `} } else if (m) {` closes two blocks and heads the third, and `lstrip("}")`
    # left the space and the second brace in place, so the head test saw `} else if` and refused the
    # branch that guards the call.
    while stripped[:1] == "}":
        stripped = stripped[1:].lstrip()
    if not IF_HEAD.match(stripped):
        return None
    heads = list(IF_BRANCH.finditer(stripped))
    if not heads:
        return None
    open_paren = heads[-1].end() - 1
    close = matching_paren(stripped, open_paren)
    if close < 0:
        return None
    remainder = stripped[close + 1:].strip()
    # A comment region is one space in the view, so ``if (m) /* guard */`` arrives here with an empty
    # remainder. An unterminated one still has to be refused: the view cannot tell it from no comment
    # at all, so the raw statement is asked.
    if has_unterminated_block_comment(statement):
        return None
    return stripped[open_paren + 1:close], remainder


def matching_paren(text: str, open_index: int) -> int:
    """The index of the `)` that closes the `(` at `open_index`, or -1."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def strip_boolean_wrappers(condition: str) -> str | None:
    """Rewrite `UNLIKELY(x)`, `LIKELY(x)` and `static_cast<bool>(x)` as `(x)`.

    These three wrappers return their argument's boolean value, so a guard written with one asks the
    same question as the bare condition. They are not hypothetical here: Connection.h has five
    `if (UNLIKELY(...))`, Decoder.h two and Connection.cpp three, so a send hot path that wraps the
    guard in `UNLIKELY` is the obvious future edit. Only these three are transparent; any other call
    still reaches the evaluator as an unknown name and is refused.

    The wrapper is replaced in place and the argument kept in parentheses, so `m && UNLIKELY(x)`
    becomes `m && (x)` and precedence is unchanged. The text is rewritten recursively, so a nested
    wrapper is stripped too. A `(` with no closing `)` means the condition is malformed and the answer
    is None.
    """
    result = []
    position = 0
    while position < len(condition):
        match = BOOLEAN_WRAPPER.match(condition, position)
        if not match:
            result.append(condition[position])
            position += 1
            continue
        close = matching_paren(condition, match.end() - 1)
        if close < 0:
            return None
        inner = strip_boolean_wrappers(condition[match.end():close])
        if inner is None:
            return None
        result.append("(" + inner + ")")
        position = close + 1
    return "".join(result)


def tokenise_condition(condition: str) -> list[str] | None:
    """The condition as tokens, or None when a character is outside the grammar."""
    tokens = []
    position = 0
    while position < len(condition):
        if condition[position].isspace():
            position += 1
            continue
        match = CONDITION_TOKEN.match(condition, position)
        if not match:
            return None
        tokens.append(match.group("operator") or match.group("name"))
        position = match.end()
    return tokens


def combine_not(value: bool | None) -> bool | None:
    return UNKNOWN if value is UNKNOWN else not value


def combine_binary(operator: str, left: bool | None, right: bool | None) -> bool | None:
    """The three-valued truth table: a side that settles the answer does so even if the other is
    unknown, which is what lets `m && anything` pass while `m || anything` does not."""
    if operator == "&&":
        if left is False or right is False:
            return False
        if left is True and right is True:
            return True
        return UNKNOWN
    if operator == "||":
        if left is True or right is True:
            return True
        if left is False and right is False:
            return False
        return UNKNOWN
    if left is UNKNOWN or right is UNKNOWN:
        return UNKNOWN
    return (left == right) if operator == "==" else (left != right)


def split_top_level(tokens: list[str], operator: str) -> list[list[str]]:
    """Split the tokens at `operator` wherever it is not inside parentheses.

    Splitting rather than parsing by position is what makes an unreadable conjunct tolerable: in
    `m && someCall(x)` the second half cannot be evaluated, but the split still produces it as its own
    factor, so the conjunction can be settled by the half that can.
    """
    parts: list[list[str]] = []
    current: list[str] = []
    depth = 0
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if token == operator and depth == 0:
            parts.append(current)
            current = []
            continue
        current.append(token)
    parts.append(current)
    return parts


def encloses_whole(tokens: list[str]) -> bool:
    """True when the first `(` matches the last token, so the tokens are one parenthesised group."""
    if len(tokens) < 2 or tokens[0] != "(" or tokens[-1] != ")":
        return False
    depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0:
                return index == len(tokens) - 1
    return False


def evaluate_disjunction(tokens: list[str]) -> bool | None:
    value = False
    for part in split_top_level(tokens, "||"):
        value = combine_binary("||", value, evaluate_conjunction(part))
    return value


def evaluate_conjunction(tokens: list[str]) -> bool | None:
    value = True
    for factor in split_top_level(tokens, "&&"):
        value = combine_binary("&&", value, evaluate_factor(factor))
    return value


def evaluate_factor(tokens: list[str]) -> bool | None:
    if not tokens:
        return UNKNOWN
    if tokens[0] == "!":
        return combine_not(evaluate_factor(tokens[1:]))
    if encloses_whole(tokens):
        return evaluate_disjunction(tokens[1:-1])
    for operator in ("==", "!="):
        parts = split_top_level(tokens, operator)
        if len(parts) == 2:
            return combine_binary(operator, evaluate_factor(parts[0]), evaluate_factor(parts[1]))
        if len(parts) > 2:
            return UNKNOWN
    # The whole evaluation is under the assumption that the switch is off, which is the state the
    # member has by default and the state this check exists to keep it in.
    if tokens == [MEMBER_NAME]:
        return False
    if tokens == ["true"]:
        return True
    if tokens == ["false"]:
        return False
    return UNKNOWN


def condition_implies_member(statement: str) -> bool:
    """True when the `if` condition cannot hold while the switch is off.

    `if (m_shouldTranslateMessageNames)` passes, and so does `m && !m_inDispatchTesting`: a false
    member settles a conjunction whatever else is in it, including a conjunct the parser cannot read.
    `!m`, `m == false`, `m != true`, `!this->m` and `m || fallbackOn` are refused, because each can be
    true with the member false, which runs the translation exactly when the switch is supposed to be
    off. So are `UNLIKELY(m)`, `LIKELY(m)` and `static_cast<bool>(m)`, which are read as the condition
    they wrap. Anything outside the grammar — a comma operator, another object's member, a call where
    the member's value is still needed — is unknown, and an unknown top-level result is refused.

    The statement's shape is checked before its condition, and the condition alone is not enough:
    `if (m) { } else { call }` has a condition that implies the member, and the call runs when the
    switch is off all the same. Only two shapes put the statement's own body right after the
    condition — nothing at all (the following statement is the body) and an opening `{` (the body
    starts here). `if (m) { } else if (ok) {`, `if (m) { return; } else {` and `if (m) { x(); } do {`
    all end in a brace and are refused by their remainder.
    """
    parts = if_parts(statement)
    if parts is None:
        return False
    condition, remainder = parts
    if remainder not in ("", "{"):
        return False
    unwrapped = strip_boolean_wrappers(condition)
    if unwrapped is None:
        return False
    tokens = tokenise_condition(THIS_PREFIX.sub("", unwrapped))
    if tokens is None:
        return False
    return evaluate_disjunction(tokens) is False


def chain_has_guard(chain: list[str]) -> bool:
    """True when any block in the chain is an `if` whose condition implies the member."""
    return any(condition_implies_member(block) for block in chain)


def chain_has_setter(chain: list[str]) -> bool:
    """True when the statement sits inside the setter's body, judged by the block chain.

    The definition line may be written on one line or split across three, so it is matched by shape
    (`SETTER_DEFINITION`) rather than compared to one literal statement. `enclosing_blocks` attributes a
    body opened on its own line to the definition above it, so both spellings arrive here as the same
    chain element.
    """
    return any(SETTER_DEFINITION.match(code_view(block)) for block in chain)


def assignment_in_setter(chain: list[str], statement: str, value: str) -> bool:
    """True when this assignment is the setter doing its job.

    Two facts have to hold together: the statement is inside the setter's body (from the chain), and the
    value assigned is the setter's own parameter. The parameter alone is not enough — any function that
    happens to take a `bool shouldTranslate` could assign the member and turn the flag on from a place
    this check cannot see through. The one-line spelling keeps the definition and the assignment in one
    statement, so there the chain is empty and the statement's own head is what says which body it is.
    """
    if value != "shouldTranslate":
        return False
    if chain_has_setter(chain):
        return True
    return bool(SETTER_HEADER.match(code_view(statement)))


def inline_guard_covers(view: str, call_start: int) -> bool:
    """True when a member `if` opens a block that is still open at `call_start`.

    A one-line form such as `void f() { if (m_shouldTranslateMessageNames) { call(x); } }` is a
    correctly guarded call, but it is a single statement, so the brace stack has no enclosing block to
    report. Counting depth from the guard's `{` to the call keeps that case passing without accepting
    `if (m_shouldTranslateMessageNames) { } call(x);`, where the block has already closed.

    Both arguments are in `code_view` coordinates: the caller finds the call in the view, so a brace in
    a literal or a comment is not a depth change here.
    """
    for offset, block in brace_opens(view):
        if offset >= call_start or not condition_implies_member(block):
            continue
        depth = 0
        for character in view[offset:call_start]:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    break
        if depth > 0:
            return True
    return False


def brace_opens(view: str) -> list[tuple[int, str]]:
    """Every `{` in the view, paired with the clause that opens it.

    Nested braces are included: a function written on one line still has its guard's `{` inside the
    function's `{`, and the inner one is the brace that decides whether the call is guarded. The clause
    is the text since the previous `;` or `}` at the same nesting, so a guard that follows another
    statement is read as the `if` rather than as everything before it.

    Takes a `code_view` string, not a raw statement: an offset returned here is compared against call
    offsets found in the same view, and a brace that is literal text must not open a block.
    """
    opens = []
    clause_starts = [0]
    paren_depth = 0
    for index, character in enumerate(view):
        if character == "(":
            paren_depth += 1
        elif character == ")":
            paren_depth = max(0, paren_depth - 1)
        elif character == "{":
            opens.append((index, view[clause_starts[-1]:index].strip()))
            clause_starts.append(index + 1)
        elif character == "}":
            if len(clause_starts) > 1:
                clause_starts.pop()
            clause_starts[-1] = index + 1
        elif character == ";" and paren_depth == 0:
            clause_starts[-1] = index + 1
    return opens


def braceless_guard_before(statements: list[tuple[str, str]], index: int) -> bool:
    """True when the statement just before `index` is a member `if` with no body of its own.

    `if (m_shouldTranslateMessageNames)` on its own line guards the next statement, and that guard is
    not part of any block the chain can see.

    Nothing may follow the closing parenthesis. `if (m_shouldTranslateMessageNames) { return; }` and
    `if (m_shouldTranslateMessageNames) goto done;` are complete statements with their own body: what
    comes after them is not guarded by them, however short the body is. Telling them apart by "does the
    line end with `{`" accepted all of these, because a body written on the same line ends with `}` or
    `;` and the condition was then read as the guard of the following statement.
    """
    if index == 0:
        return False
    previous_path, previous = statements[index - 1][0], statements[index - 1][1]
    if previous_path != statements[index][0]:
        return False
    parts = if_parts(previous)
    if parts is None:
        return False
    _condition, remainder = parts
    return remainder == "" and condition_implies_member(previous)


def check_read_sites(patch: str) -> list[Violation]:
    statements = added_code_statements(patch)
    blocks = enclosing_blocks(statements)
    violations = []

    def is_exempt(path: str, view: str) -> bool:
        """True for the translation's own definition and for the header's declarations.

        These are the statements where the call is the translation rather than a use of it. The test
        is the file plus the declaration shape, so adding a caller in either file still has to be
        guarded; a list of call sites would have the hole this check was rewritten to close.
        """
        return path in DEFINITION_FILES and bool(TRANSLATION_DEFINITION.match(view))

    def guarded(index: int, call_start: int) -> bool:
        """True when the member's guard covers the call at `call_start`.

        A call written after a closer in the same statement has left the block the chain describes.
        `enclosing_blocks` samples the stack before the statement's own closers pop it, which is right
        for a call at the statement's head and wrong for one written after `pending(); }` on the same
        statement: the chain still names the guard the statement just closed, so the call was accepted
        as guarded. Past the last closer only the call's own statement can decide, and
        `inline_guard_covers` asks exactly that.
        """
        view = code_view(statements[index][1])
        closer = last_unmatched_closer(view)
        if closer == -1 or call_start < closer:
            if chain_has_guard(blocks[index]):
                return True
            if braceless_guard_before(statements, index):
                return True
        return inline_guard_covers(view, call_start)

    # Every added statement that calls the translation has to sit under the member, not just the two
    # files that were the read sites when this check was written. Every call in a statement is checked,
    # not only the first: two calls on one line would otherwise pass on the strength of one guard.
    #
    # The call is found in the lexical view, so a mention of the function inside a string or a comment
    # is not a call, and the offset it carries is in the same coordinates as `brace_opens`.
    for index, (path, statement) in enumerate(statements):
        view = code_view(statement)
        if is_exempt(path, view):
            continue
        for call in TRANSLATE_CALL.finditer(view):
            if guarded(index, call.start()):
                continue
            chain = blocks[index]
            found = "no enclosing block" if not chain else "the blocks `%s`" % "` < `".join(chain)
            violations.append(
                Violation(
                    "the patch calls the translation outside the member's guard",
                    "expected an `if` on `m_shouldTranslateMessageNames` around `%s` in %s, found %s; "
                    "a translation call anywhere else runs on every message, which is the switch "
                    "turned on without anyone flipping it" % (statement.strip(), path, found),
                )
            )

    for path, call, description in READ_SITES:
        matches = [(index, found.start(), statement)
                   for index, (line_path, statement) in enumerate(statements)
                   if line_path == path
                   for found in [TRANSLATE_CALL.search(code_view(statement))] if found]
        if not matches:
            violations.append(
                Violation(
                    "%s no longer calls %s" % (description, call),
                    "expected %s in %s; the translation has to happen somewhere, and this check pins "
                    "that it happens under the member" % (call, path),
                )
            )
            continue
        for index, call_start, statement in matches:
            if guarded(index, call_start):
                continue
            chain = blocks[index]
            found = "no enclosing block" if not chain else "the blocks `%s`" % "` < `".join(chain)
            violations.append(
                Violation(
                    "%s is guarded by something other than the member" % description,
                    "expected an `if` on `m_shouldTranslateMessageNames` around %s in %s, found %s; "
                    "IOS_16_MESSAGE_NUMBER_TRANSLATION is 1 in this build because the deployment "
                    "target is 16.1, so a macro condition here is a bare brace with a reassuring name"
                    % (call, path, found),
                )
            )

    for index, (path, statement) in enumerate(statements):
        for found in ASSIGNMENT.finditer(code_view(statement)):
            if assignment_in_setter(blocks[index], statement, found.group(1).strip()):
                continue
            violations.append(
                Violation(
                    "the patch assigns m_shouldTranslateMessageNames outside the setter",
                    "the flag may only be set from the setter's parameter, so that every route to "
                    "translation is a call this check can see; found `%s` in %s"
                    % (statement.strip(), path),
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


# The two tables, read from the patch's added code rather than its prose. The prose check above
# deliberately excludes code, so without this one a table entry can be renumbered and the run stays
# green. That is the same silent rename this whole check exists to prevent, reachable from the table
# side: the tables are what a translated connection actually dispatches on.
OUTGOING = re.compile(r"case\s+MessageName::(\w+)\s*:\s*return\s+static_cast<MessageName>\((\d+)\);")
INCOMING = re.compile(r"case\s+(\d+)\s*:\s*return\s+MessageName::(\w+);")

# (description, direction, key, expected). `key` is a message name for the outgoing table and a
# number for the incoming one.
#
# Three of these nine pairs are named in the patch's prose, so a change to either side is a change to
# a claim a reader relies on:
#
#   `InitializeConnection` is number 3076 in this tree, and the shipped 16.1 numbering translates it
#   to 2478. The incoming table reads case 3076 as
#   RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged, the shipped 16.1 message that occupies
#   3076, which is why the handshake comparison stops matching.
#
# Three pairs are not in the prose at all: `2444`, `3184`'s incoming case and `2946`. They are pinned
# because they are what the tables hold next to the named ones, and a silent rename shifts them even
# when the prose still reads correctly. `2444` is the number that made the earlier, wrongly based
# table translate `InitializeConnection` to `WebSharedWorkerServerConnection_SuspendForBackForwardCache`;
# pinning it records that 2444 now belongs to that message and to nothing else.
TABLE_ANCHORS = (
    ("InitializeConnection translates to its shipped 16.1 number", "outgoing", "InitializeConnection", 2478),
    ("the incoming table reads 2478 back as InitializeConnection", "incoming", 2478,
     "InitializeConnection"),
    ("3076 declares RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged, not the handshake",
     "incoming", 3076, "RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("that message's own outgoing number is 3076", "outgoing",
     "RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged", 3076),
    ("the message that owns the number the handshake used to be translated to", "incoming", 2444,
     "WebSharedWorkerServerConnection_SuspendForBackForwardCache"),
    ("that message's own outgoing number is 2444", "outgoing",
     "WebSharedWorkerServerConnection_SuspendForBackForwardCache", 2444),
    ("the outgoing table's largest number is the tag's Last of 3184", "outgoing",
     "WrappedAsyncMessageForTesting", 3184),
    ("the incoming table's largest case is that same message", "incoming", 3184,
     "WrappedAsyncMessageForTesting"),
    ("the incoming table's first synchronous number is the anchor node", "incoming", 2946,
     "GPUConnectionToWebProcess_EnsureAudioSession"),
)


def table_entries(patch: str) -> tuple[dict[str, int], dict[int, str]]:
    """The outgoing and incoming mappings the patch adds, read from its non-comment code lines.

    A patch that carries two different numbers for one name keeps the last one read, which is what
    the later `case` would do at run time only if the first were unreachable; the anchor check
    reports the mismatch rather than modelling that, because a duplicate key is itself the defect.
    """
    outgoing: dict[str, int] = {}
    incoming: dict[int, str] = {}
    for code in added_code_lines(patch):
        if code.startswith("case") is False:
            continue
        found = OUTGOING.match(code)
        if found:
            outgoing[found.group(1)] = int(found.group(2))
            continue
        found = INCOMING.match(code)
        if found:
            incoming[int(found.group(1))] = found.group(2)
    return outgoing, incoming


def check_table_anchors(patch: str) -> list[Violation]:
    outgoing, incoming = table_entries(patch)
    violations = []
    for description, direction, key, expected in TABLE_ANCHORS:
        if direction == "outgoing":
            table, label = outgoing, "outgoing"
        else:
            table, label = incoming, "incoming"
        if key not in table:
            violations.append(
                Violation(
                    "the %s table has no entry for %s" % (label, description),
                    "expected case %s -> %s in the %s table the patch adds; without it the rename "
                    "this check exists to prevent is unguarded from the table side"
                    % (key, expected, label),
                )
            )
            continue
        if table[key] != expected:
            violations.append(
                Violation(
                    "the %s table disagrees with the prose about %s" % (label, description),
                    "expected %s -> %s, found %s -> %s; the table is what a translated connection "
                    "dispatches on, so a mismatch here is the rename, not a comment error"
                    % (key, expected, key, table[key]),
                )
            )
    return violations


# The opposite claim from the one this check used to make. The tables were once rendered from the
# Safari-614.1.9 snapshot, which is the iOS 16.0 generation, and that basis is what translated
# `InitializeConnection` to an unrelated message. They now come from the shipped 16.1 tag, so prose
# that sends a reader back to the snapshot describes a table this file no longer carries. A mention
# is allowed when the same line, or the line after, calls it superseded, earlier or wrongly based.
STALE_BASIS = re.compile(r"614\.1\.9|16\.0-generation", re.IGNORECASE)
SUPERSEDED = ("earlier", "superseded", "wrongly", "used to", "no longer", "regression")


def check_no_stale_table_basis(patch: str) -> list[Violation]:
    lines = patch.splitlines()
    violations = []
    for index, raw in enumerate(lines):
        match = ADDED_LINE.match(raw)
        if not match:
            continue
        text = match.group(1)
        if not STALE_BASIS.search(text):
            continue
        window = " ".join(lines[index:index + 2]).lower()
        if any(marker in window for marker in SUPERSEDED):
            continue
        violations.append(
            Violation(
                "the patch describes the tables as coming from the 16.0-generation snapshot",
                "the tables carry the shipped 16.1 numbering from "
                "releases/Apple/Safari-16.1-iOS-16.1; naming 614.1.9 as their basis is the defect "
                "this whole check exists to keep out: %s" % text.strip(),
            )
        )
    return violations


# A `@@` header states how many body lines follow it, one count per side, and `git apply` streams the
# body against those counts instead of deriving them. `parse_fragment` in git's `apply.c` holds the
# two counts as unsigned counters and decrements them line by line, so a wrong count is never
# reported as a wrong count.
#
# The outcome of a wrong count is not decided by which side runs long. What the counts themselves do
# was measured in two shapes: a counter decremented after it reached zero wraps, and a counter still
# positive when the body ends stays positive. Both stop the parse with
# `corrupt patch at <patch>:<line>` and rc 128. A third shape leaves the parse cleanly: the read
# stops at the body line that takes the second counter to zero, and every line after it is never
# parsed — not read and rejected, never looked at. Two counters reaching zero on the same line is not
# required for that: `@@ -1,1 +1,2 @@` over `-a` / `+A` / `+B` parses because the old counter
# reaches zero on the first of those lines and no later line decrements it again.
#
# Those three are what the rows below measure. They are not the whole of `parse_fragment`, and this
# comment does not claim to be: besides the two counter failures it returns from an unparsable `@@`
# header, from a last body line with no newline, from a body line that begins with anything other
# than a space, `+`, `-` or nothing at all — an empty line is a context line, which `case '\n'`
# accepts — and from `if (!patch->recount && !deleted && !added) return -1;` — a hunk whose counts
# the read satisfied but whose parsed region held no `+` or `-` line at all. Measured, on a file of
# `a` / `b` / `c`: `@@ -1,2 +1,2 @@` over ` a` / ` b` / `-c` / `+C` exits 128, because the first
# two lines take both counters to zero, the loop exits, `-c` and `+C` are never parsed, and `deleted`
# and `added` never leave 0. The same body under `@@ -1,3 +1,3 @@` exits 0, and so does the first
# header under `--recount`; that failure never consults a counter. One more lives a stage up, in
# `parse_single_patch`, and there the hunk's kind does decide: `new file %s depends on old contents`
# fires when a new-file hunk carries any old-side line, so the `@@ -1,1 +1,2 @@` shape above is fatal
# as a new file even though the same counts and body parse and apply as a modification.
#
# Everything below was measured on a scratch repo with git 2.55.0. Each row names the file it was
# applied to, the header and the body, so it can be re-run. The body lines are written in backticks,
# where the leading space of a context line shows as the first character:

#   file `a` `b` `c` `d` `e`
#   @@ -1,3 +1,3 @@  ` a` `-b` `+B` ` c`                     rc 0    f.txt -> a B c d e
#   @@ -1,3 +1,3 @@  ` a` `-b` `+B` ` c` ` d`                rc 0    the ` d` is never parsed
#   @@ -1,3 +1,3 @@  ... ` c` ` ZZZZ-not-in-the-file`         rc 0    that line is not in the file
#   @@ -1,3 +1,2 @@  ` a` `-b` `+B` ` c`                     rc 128  ` c` decrements a zero count
#   @@ -1,3 +1,4 @@  ` a` `-b` `+B` ` c`                     rc 128  new count still 1 at the end
#   @@ -1,4 +1,3 @@  ` a` `-b` `+B` ` c`                     rc 128  old count still 1 at the end
#
# A parse that succeeds still has to land. With no trailing context line the fragment must end at the
# end of the file, because `apply.c` sets `match_end = !state->unidiff_zero && !trailing`. Measured
# with the body ` a` / `-b` / `+B` under `@@ -1,2 +1,2 @@`: rc 0 against a file of `a` and `b`, and
# rc 1 `patch does not apply` against `a b c`, the same fragment that parsed cleanly a file earlier.
# The same body under `@@ -1,3 +1,3 @@` with ` c` / ` d` after it parses as a 3-line fragment and
# then exits 0 against `a b c d e` but 1 against `z z z`, so rc 128 tracks the counts and rc 1 tracks
# the tree.
#
# So a wrong count does not have one outcome, and the rows above are the ones measured: it applies
# correctly, applies with a later edit never parsed, reports `patch does not apply`, or stops with
# `corrupt patch`. Which of those four the counts reach follows from the counters where the body's
# lines allow it, and from the lines the target file happens to have where they do not. Hunk kind
# decides none of the four, but it does decide whether the patch gets that far: the new-file check in
# `parse_single_patch` above turns a shape that applies as a modification into a fatal new-file hunk.
#
# Both hunks that prompted this check had a header their body outgrew, and the two did not share an
# outcome. The new-file hunk at b70ce36 declared `@@ -0,0 +1,184 @@` over a 194-line body: its old
# count starts at 0 and every body line is `+`, so the read stops at line 184 and the last 10 lines
# are never parsed; applied on its own it exits 0 and writes 184 of the 194 lines. The
# AuxiliaryProcess hunk declared `@@ -106,6 +106,27 @@` over 28 lines, and its 28th line ` }` is
# context: it decrements the new counter that line 27 had already taken to zero, wraps it, and exits
# 128. Applied as one patch, the file stops at line 8297, the second hunk, so the first hunk's
# truncated output was never reached.
#
# That is what earns this check its place. `git apply` reports a consequence — a corrupt patch, a
# refused context, or nothing at all — and two of the four outcomes leave a short file and a success
# status. `check_hunk_counts` is the only gate that reports the header itself, with its line number
# and both counts, so the diagnosis names the line to edit.
#
# That parse stage does not consult the tree. A header whose counters cannot be satisfied reports
# `corrupt` whatever file it is pointed at: both the overcount row above and a zero-decrementing one
# exit 128 against a file of `z` lines as well. A header whose fragment merely fails to match is the
# case that reports `patch does not apply` instead, so this check runs before any hunk is checked
# against the tree.
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def check_hunk_counts(patch: str) -> list[Violation]:
    """Compare every `@@` header's declared counts against the body it introduces.

    The walk mirrors what `git apply` does: a line starting with `+` is a new-side line, `-` an
    old-side line, and a line starting with a space or empty a context line counted on both sides.

    Where a body ends is the part worth stating, because the obvious rule is wrong. A body ends only
    at the next `@@` header or at the next `diff --git` line, never at a `---` or `+++` line: the
    lines a patch deletes and adds can themselves begin with those characters, so `--- x` inside a
    body is a deleted line whose text starts with `--`, not a file header. Outside a body every line
    is a file header or patch preamble and belongs to no count, which is where `index`, `new file
    mode`, `---` and `+++` do live. A `\\ No newline at end of file` marker is not a body line and
    is counted on neither side.

    Both directions are violations, and the check reports the numbers rather than predicting what
    `git apply` will do with them. What it does is not settled by the counts alone — a hunk can also
    fail for carrying no `+` or `-` line at all, or for a new-file hunk that carries an old-side line
    — so the diagnosis a reader needs is which header is wrong and by how much, and that is the same
    whatever the apply then does.

    A header this function cannot parse is a violation, not an exception. `git apply` accepts combined
    diffs and a `@@` line is only recognised as a header by its prefix, so a header the regex does not
    match is reachable from any patch this file is pointed at. Every other unmatched shape here is
    reported rather than guessed at, and a traceback would replace the diagnosis this function exists
    to produce, so an unmatched header is reported with its line and text and its counts are not
    compared.
    """
    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    hunks = []
    current = None
    old_count = new_count = 0
    header_line = 0
    in_body = False

    def close_hunk() -> None:
        if current is not None:
            hunks.append((header_line, current, old_count, new_count))

    for index, raw in enumerate(lines, start=1):
        if raw.startswith("diff --git "):
            close_hunk()
            current, in_body = None, False
            continue
        if raw.startswith("@@"):
            close_hunk()
            current, header_line, in_body = raw, index, True
            old_count = new_count = 0
            continue
        if not in_body or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            new_count += 1
        elif raw.startswith("-"):
            old_count += 1
        elif raw.startswith(" ") or raw == "":
            old_count += 1
            new_count += 1
    close_hunk()

    violations = []
    for header_line, header, old_count, new_count in hunks:
        match = HUNK_HEADER.match(header)
        if match is None:
            violations.append(
                Violation(
                    "a hunk header is not one this check can read, at patch line %d" % header_line,
                    "the header is %r, which does not carry the `@@ -old,count +new,count @@` "
                    "counts every other hunk in this file does. A combined diff writes `@@@` and "
                    "this patch format is a plain unified diff, so the counts cannot be compared "
                    "and the hunk is reported rather than skipped" % (header,),
                )
            )
            continue
        declared_old = int(match.group(2)) if match.group(2) is not None else 1
        declared_new = int(match.group(4)) if match.group(4) is not None else 1
        if (old_count, new_count) != (declared_old, declared_new):
            violations.append(
                Violation(
                    "a hunk header disagrees with the lines it introduces, at patch line %d"
                    % header_line,
                    "the header declares %d old and %d new lines but its body has %d and %d. "
                    "git apply never derives these counts, so it does not report the header: too "
                    "large stops the apply with `patch is corrupt`, and too small can apply with "
                    "exit 0 and leave the file missing whatever fell past the count: %s"
                    % (declared_old, declared_new, old_count, new_count, header),
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
    violations += check_read_sites(patch)
    violations += check_member_initialiser(patch)
    violations += check_rationale(patch)
    violations += check_table_anchors(patch)
    violations += check_no_stale_table_basis(patch)
    violations += check_hunk_counts(patch)

    if violations:
        for violation in violations:
            print("check-translation-switch: %s" % violation.reason, file=sys.stderr)
            print("  %s" % violation.detail, file=sys.stderr)
        return 1

    print("check-translation-switch: no call to setShouldTranslateMessageNames, both reads ask the "
          "member, the flag keeps its false initialiser, the rationale is intact and the table "
          "anchors hold")
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
