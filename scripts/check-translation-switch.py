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
    switch would look like a small change; the anchors checked below make that edit fail here. Each
    anchor is a phrase that occurs exactly once in the prose, and the tests assert that, because a
    bare number occurs many times and would keep matching after its sentence was rewritten.

  - A table entry that disagrees with the prose. The prose check reads comments only, so a renumbered
    `case` or a mapping retargeted at another real message would otherwise pass. The tables are what
    a translated connection dispatches on, so the anchor pairs the prose names are read back out of
    the added code and compared. Two of the seven pairs are not named in the prose at all: they are
    read from the tables themselves and pinned because they are the neighbours of the pairs the prose
    does name, and a silent rename would move them instead.

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
    `if (m) goto done;` — is a complete statement that guards nothing after it; only an `if` with
    nothing after its closing parenthesis guards the next statement. A caller added to a third file is
    checked too, because the rule is a property of the call and not a list of the two files that first
    had one; only the two functions' own definitions and their header declarations are exempt, and that
    is decided by the file plus the declaration, so a new caller inside an exempt file still has to be
    guarded.

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
# it came from would leave other occurrences matching and the check would pass: `3076` alone appears
# six times, `Safari-614.1.9` twice, and the releases tag three times. Each needle therefore carries
# the words that make it unique, and `test-check-translation-switch.py` asserts the uniqueness as an
# invariant rather than leaving it to be re-checked by eye.
#
# The needles are matched against the added comment prose only, never against code. Several of the
# numbers below are also real case labels in the tables (`case 3135:`, `return 946`), so a search
# over the whole patch would match them whatever the prose said.
#
# Every 16.1 mention in the patch is qualified, because the tables are NOT built on shipped 16.1:
# they come from the 614.1.9 snapshot, which is the iOS 16.0 generation. The entries below pin both
# bases and the counts for both, so an edit cannot quietly drop one and leave the patch claiming a
# basis it does not have.
REQUIRED_RATIONALE = (
    ("InitializeConnection is 3076 in this tree", "`InitializeConnection` is number 3076 in this tree"),
    ("the 614.1.9 number for InitializeConnection", "2444 and Last 3139"),
    ("the message 3076 silently becomes, per the 614.1.9 basis",
     "case 3076 with MessageName::WebPageProxy_RootViewToAccessibilityScreen"),
    ("the message 3076 would become against shipped 16.1",
     "names MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("the handshake comparison that then never fires",
     "decoder->messageName() == MessageName::InitializeConnection"),
    ("the same-name count of the index comparison", "5 land on a message"),
    ("the different-message count against shipped 16.1", "3180 land on a different message"),
    ("the beyond-range count against shipped 16.1", "and 901 fall off"),
    ("the net growth over the 614.1.9 snapshot", "946 more entries"),
    ("the different-message and beyond-range counts against the 614.1.9 basis",
     "5, 3135 and 946. Both readings"),
    ("the tree's own size", "4086 numbers"),
    ("the shipped 16.1 enumeration's size and tag",
     "shipped 16.1 enumeration (releases/Apple/Safari-16.1-iOS-16.1, 3185 numbers)"),
    ("the tag the tables were generated from", "built on tag Safari-614.1.9"),
    ("the statement that the tables are a 16.0-generation snapshot", "16.0-generation snapshot"),
    ("the first anchor that tag produces", "firstSynchronousOldMessageNumber 2899"),
    ("the tag the device actually runs", "device runs releases/Apple/Safari-16.1-iOS-16.1 (614.2.9.0.10)"),
    ("the anchors the device's build produces", "2946, 2478 and 3184"),
    ("the gap at Last between the two enumerations", "45 at Last"),
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
        if not SETTER.search(statement):
            continue
        if any(hint in statement for hint in DECLARATION_HINTS):
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


def enclosing_blocks(statements: list[tuple[str, str]]) -> list[list[str]]:
    """For each statement, the chain of blocks around it, innermost first.

    A chain rather than the innermost block alone: a call wrapped in a `for` inside the guard is still
    guarded, and comparing only the innermost block reported `for (...)` and failed correct code. The
    guard is satisfied when the chain contains it at any depth.

    A statement opens a block when it *ends* with `{`, and closes one when it starts with `}`. That
    matters: `Locker locker { reportedUnmappedNamesLock };` and `bool m_x { false };` carry braces but
    end with `;`, so a naive brace count would treat the braced initialiser as a block and report the
    wrong guard.

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
        if statement.startswith("}"):
            if stack:
                stack.pop()
            continue
        if statement.endswith("{"):
            opener = statement
            if statement == "{" and index > 0 and statements[index - 1][0] == path:
                previous = statements[index - 1][1]
                if SETTER_DEFINITION.match(previous):
                    opener = previous
            stack.append(opener)
    return blocks


# The member as the condition can spell it. `this->m_shouldTranslateMessageNames` is the same member.
MEMBER_NAME = "m_shouldTranslateMessageNames"
# `this->` is the same object as the implicit one, so the prefix is dropped before the condition is
# parsed. Any other object's member keeps its `->`, which makes the tokeniser fail and the condition
# be refused, which is the safe answer for a read on some other connection.
THIS_PREFIX = re.compile(r"\bthis\s*->\s*")
IF_HEAD = re.compile(r"^if\b")

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
    """The `if`'s condition and the text after its closing parenthesis, or None when it is not an `if`.

    The remainder is what separates the two shapes the guard check has to tell apart: a braceless guard
    is an `if` with nothing after the parenthesis, while `if (m) { return; }` and `if (m) goto done;`
    carry a body, so they guard nothing that follows them.
    """
    stripped = statement.strip()
    if not IF_HEAD.match(stripped):
        return None
    open_paren = stripped.find("(")
    if open_paren < 0:
        return None
    depth = 0
    for index in range(open_paren, len(stripped)):
        if stripped[index] == "(":
            depth += 1
        elif stripped[index] == ")":
            depth -= 1
            if depth == 0:
                return stripped[open_paren + 1:index], stripped[index + 1:].strip()
    return None


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
    """
    parts = if_parts(statement)
    if parts is None:
        return False
    condition, _remainder = parts
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
    return any(SETTER_DEFINITION.match(block) for block in chain)


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
    return bool(SETTER_HEADER.match(statement))


def inline_guard_covers(statement: str, call_start: int) -> bool:
    """True when a member `if` opens a block that is still open at `call_start`.

    A one-line form such as `void f() { if (m_shouldTranslateMessageNames) { call(x); } }` is a
    correctly guarded call, but it is a single statement, so the brace stack has no enclosing block to
    report. Counting depth from the guard's `{` to the call keeps that case passing without accepting
    `if (m_shouldTranslateMessageNames) { } call(x);`, where the block has already closed.
    """
    for offset, block in brace_opens(statement):
        if offset >= call_start or not condition_implies_member(block):
            continue
        depth = 0
        for character in statement[offset:call_start]:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    break
        if depth > 0:
            return True
    return False


def brace_opens(statement: str) -> list[tuple[int, str]]:
    """Every `{` in the statement, paired with the clause that opens it.

    Nested braces are included: a function written on one line still has its guard's `{` inside the
    function's `{`, and the inner one is the brace that decides whether the call is guarded. The clause
    is the text since the previous `;` or `}` at the same nesting, so a guard that follows another
    statement is read as the `if` rather than as everything before it.
    """
    opens = []
    clause_starts = [0]
    paren_depth = 0
    for index, character in enumerate(statement):
        if character == "(":
            paren_depth += 1
        elif character == ")":
            paren_depth = max(0, paren_depth - 1)
        elif character == "{":
            opens.append((index, statement[clause_starts[-1]:index].strip()))
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

    def is_exempt(path: str, statement: str) -> bool:
        """True for the translation's own definition and for the header's declarations.

        These are the statements where the call is the translation rather than a use of it. The test
        is the file plus the declaration shape, so adding a caller in either file still has to be
        guarded; a list of call sites would have the hole this check was rewritten to close.
        """
        return path in DEFINITION_FILES and bool(TRANSLATION_DEFINITION.match(statement))

    def guarded(index: int, call_start: int) -> bool:
        if chain_has_guard(blocks[index]):
            return True
        if braceless_guard_before(statements, index):
            return True
        return inline_guard_covers(statements[index][1], call_start)

    # Every added statement that calls the translation has to sit under the member, not just the two
    # files that were the read sites when this check was written. Every call in a statement is checked,
    # not only the first: two calls on one line would otherwise pass on the strength of one guard.
    for index, (path, statement) in enumerate(statements):
        if is_exempt(path, statement):
            continue
        for call in TRANSLATE_CALL.finditer(statement):
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
                   if line_path == path for found in [TRANSLATE_CALL.search(statement)] if found]
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
        for found in ASSIGNMENT.finditer(statement):
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
# Five of these seven pairs are named in the patch's prose, so a change to either side is a change to
# a claim a reader relies on:
#
#   `InitializeConnection` is number 3076 in this tree, and the older numbering it is translated to
#   is 614.1.9, where it is 2444. The incoming table reads case 3076 as
#   WebPageProxy_RootViewToAccessibilityScreen, the message that occupies 3076 in 614.1.9, which is
#   why the handshake comparison stops matching.
#
# The other two pairs are not in the prose at all: `3024` and `WrappedAsyncMessageForTesting` and
# `GPUConnectionToWebProcess_EnsureAudioSession` each occur zero times there (`3139` and `2899` do
# occur, but as "Last 3139" and "firstSynchronousOldMessageNumber 2899", not as these pairs). Those
# pairs are pinned because they are what the tables actually hold next to the named ones, and a
# silent rename shifts them even when the prose still reads correctly. They are the stronger half of
# the check, so they are kept and the reason states where they come from.
TABLE_ANCHORS = (
    ("InitializeConnection translates to its 614.1.9 number", "outgoing", "InitializeConnection", 2444),
    ("3076 declares WebPageProxy_RootViewToAccessibilityScreen, not the handshake", "incoming", 3076,
     "WebPageProxy_RootViewToAccessibilityScreen"),
    ("that message's own outgoing number is 3076", "outgoing",
     "WebPageProxy_RootViewToAccessibilityScreen", 3076),
    ("RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged translates to 3024", "outgoing",
     "RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged", 3024),
    ("case 3024 reads back as RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged", "incoming",
     3024, "RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"),
    ("the outgoing table's largest number is 614.1.9's Last of 3139", "outgoing",
     "WrappedAsyncMessageForTesting", 3139),
    ("the incoming table's first synchronous number is the anchor node", "incoming", 2899,
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
    violations += check_read_sites(patch)
    violations += check_member_initialiser(patch)
    violations += check_rationale(patch)
    violations += check_table_anchors(patch)
    violations += check_no_unqualified_table_claim(patch)

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
