#!/usr/bin/env python3
"""Regression tests for scripts/check-translation-switch.py.

Each case writes its own patch fixture, so nothing here needs a checkout, Xcode or network access. The
cases are the ways the switch can come back and the ways the check could pass by accident:

  the patch as committed must pass, because that is the artefact the workflow ships;
  a re-added call must fail, because that is the regression the mission exists to prevent;
  the same call written on the setter's declaration must pass, so the check reads calls and not
  declarations;
  a call with no argument must fail, so `setShouldTranslateMessageNames()` is not mistaken for the
  declaration;
  a macro initialiser must fail, because Connection.h cannot see MessageNumberTranslation.h;
  deleting the wrong-16.1-tag fact must fail, because that is the defect a reader has to see;
  a missing patch must be reported distinctly, so a read error is not read as a pass;
  a count or tag drifted back to a wrong value must fail, so a needle cannot be a marker that also
  occurs somewhere else in the file;
  a table entry retargeted or renumbered must fail, because the prose check reads comments only and
  the tables are what a translated connection dispatches on;
  a read site that stops asking the member must fail, in either of the two shapes a bypass takes (a
  bare `{` and the deployment macro), because the setter call is only one of the switch's three
  sites;
  an assignment to the member outside the setter must fail, including the compound `|=` and the
  parenthesised `(member) =` forms, because both compile and both turn the flag on;
  a setter call folded across two lines must fail, because the check has to read statements and not
  lines;
  a translation call added to a third file, or to an exempt file outside a declaration, must fail,
  because the guard is a property of every call rather than of the two files that first had one,
  while a correctly guarded one-line call must pass;
  a guard whose condition mentions the member but does not imply it must fail — a negation, an
  equality with `false`, a disjunction with a fallback — because those run the translation exactly
  when the switch is off, and the boolean structure is evaluated rather than searched for the name;
  a guard wrapped in `UNLIKELY`, `LIKELY` or `static_cast<bool>` must pass, because those return
  their argument's value and the send path is the place a future edit would wrap it, while an
  unrelated call in the same position must still fail;
  a guard written with its body on the same line — `if (m) { return; }`, `if (m) { }`, `if (m) goto
  done;` — must fail for the statement after it, because such an `if` is complete and guards nothing;
  a guard whose own body does not open where the statement ends must fail — `if (m) { } else if (ok)
  {`, `if (m) { } else {`, `if (m) { x(); } do {` — because the brace belongs to a branch that can
  run with the member false, while an `else if` on the member itself must pass;
  a braceless guard followed by a comment must pass, because the comment is not part of the shape;
  a guard block closed anywhere other than at the start of a statement must end the guard — `return;
  }`, `pending(); }`, `} }` and `}}` all fail for the call that follows, because the block is closed
  whether the brace sits at the start, the middle or the end of the line;
  a branch headed by `} } else if (m) {` must still pass, because closing two blocks and heading the
  third is how a chain of nested guards is written, and the branch that follows is the guard;
  an identifier ending in `if` must not be read as a branch head — `m && notif(x)` and
  `m && find_if(p)` pass, because `notif`/`find_if` are the condition's operands, while
  `if (check_if(m))` and a `count_if(...)` conjunct still fail, because those conditions cannot be
  settled.

Open boundary: a call that follows a closed guard block inside the same statement — `if (m) { x();
} call;` — is refused in both spellings. The one-line form is refused: its chain is empty and no
closure sits before the call. The split form is refused: the check stops consulting the chain once a
closer in the call's own statement precedes the call. Before that fix such a call was let through,
because the chain was read at the statement's start and still held the guard the statement had just
closed; that was the defect, and the two sentences above are what keep it from coming back. Cutting
the statement at the closure would let either spelling through, which this check does not do. A block
comment inside the condition — `if (m /* c */)` — is refused because the condition is read as written
and the comment is not stripped there. This check does not read English: each direction sentence is
pinned with the direction it states and bound to a fixture of that shape, the rest of this paragraph
carries no direction word, and any direction word outside the pinned sentences fails it. The words it
treats as a direction are the closed list in `DIRECTION_WORD`, so a sentence using one of them cannot
go unaccounted for.

The two-closer boundary is accepted: a statement with two unmatched closers and a call between them —
`x(); } call; }` — is judged by the chain it started inside, because only the last closer decides
where the chain stops being consulted and that closer sits after the call. Measured on a 898065-line
WebKit tree: 96 lines carry two unmatched closers and none of them places a call between the two; the
mirror shape, an inner block closing mid-line while the guard is still open, is also zero. Both are
recorded here rather than fixed.

The "as committed" case reads the real patch, so a patch edited without updating this check fails
here rather than in the build. Two further passes read the real patch too: every rationale needle
must occur exactly once in its prose, and deleting any one needle must fail the checker. Together
those make "the needle is unique and necessary" an executable invariant instead of a reading.

The Open boundary text above is pinned, not parsed. `CLAIM` lists each direction sentence, the
direction it states, and whether the call is refused; the text has to contain each sentence verbatim,
the sentence's own direction word has to be the one its row declares, and that declaration has to be
what the checker returns on a fixture of that shape. After the pinned sentences are removed no
direction word may remain, so narration cannot smuggle in a state the tables do not record.

Two things are deliberately not claimed. The prose around the pinned sentences is not pinned: it
records history and mechanism, carries no direction word, and a rewritten sentence there is free.
And the vocabulary is closed — `DIRECTION_WORD` holds `refused` and `accepted`, and nothing else is
read as a direction, so a synonym is a sentence this check does not see rather than one it failed to
parse. Both limits are executable cases below rather than promises in prose; widening the vocabulary
would only move the same gap to the next synonym.

Four earlier versions tried to read the English instead — by the words present, then by negation, then
by tense, then by refusing to guess at shapes the reader could not match — and each time the next
unreadable shape walked through, because a reader that skips what it cannot match leaves a path where
a claim contributes nothing and the check stays green. There is no reader now. One case changed
meaning with this: a past-tense sentence stating the opposite used to pass as long as the current
claim was unchanged, and now fails, because a sentence saying the call was refused before a fix is
exactly what a reader can mistake for the present state.

The rewrite cases that spawn child runs are told to skip themselves by `--skip-rewrites` on the
command line, never by an environment variable. Every other assertion, including the comparison and
the vacuity check, still runs in the child.

Usage: python3 scripts/test-check-translation-switch.py
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-translation-switch.py"
REAL_PATCH = SCRIPTS.parent / "patches" / "ios16-ipc-message-number-translation.patch"

# Passed on a child run's command line to make it skip the rewrites that spawn children of their own.
# See `run_full_self_test` for why the recursion has to be cut here and what the child still checks. A
# command-line argument rather than an environment variable, so the switch cannot be set by accident:
# a stray environment variable would silently remove those assertions from an ordinary run.
REWRITE_FLAG = "--skip-rewrites"

# The checker's own matchers, imported rather than restated: the cross-check below has to use the
# same definition of "a setter call" as the checker, or it would agree with a different question.
_SPEC = importlib.util.spec_from_file_location("check_translation_switch", SCRIPT)
CHECK = importlib.util.module_from_spec(_SPEC)
sys.modules["check_translation_switch"] = CHECK
_SPEC.loader.exec_module(CHECK)

# A patch fixture with every anchor the checker requires, so a case that targets one rule is not
# failed by an unrelated one. The prose is quoted from the patch itself, line breaks and all, so a
# needle that only matches the real wording cannot pass here by coincidence. The table code lines are
# the ones check_table_anchors reads, in the shape the patch writes them. The last two sections carry
# the read sites check_read_sites pins; both rules key on the file path, so the `diff --git` headers
# are part of the fixture and not decoration.
BASE = """\
diff --git a/Source/WebKit/Platform/IPC/Connection.h b/Source/WebKit/Platform/IPC/Connection.h
+bool m_shouldTranslateMessageNames { false };
+void setShouldTranslateMessageNames(bool shouldTranslate) { m_shouldTranslateMessageNames = shouldTranslate; }
diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp
+// `MessageName` is a dense enumeration over every message in the tree: this one has 4086 entries
+// and the shipped 16.1 enumeration has 3185, so 4086 - 3185 = 901 entries were inserted, and any
+// one of them landing before a name reassigns that name and every number after it.
+// This tree has 4086 numbers and the shipped 16.1 enumeration
+// (releases/Apple/Safari-16.1-iOS-16.1) has 3185, so no 16.1 number falls off this enumeration's
+// end. Of the 2911 names the two enumerations share, 5 happen to carry the same number on both
+// sides and 2906 do not. 274 names exist only in 16.1 and 1175 only in this tree.
+// `InitializeConnection` is number 3076 in this tree. The shipped 16.1 enumeration
+// puts it at 2478, and 2478 is the entry the incoming table needs for it:
+// `case 2478: return MessageName::InitializeConnection;`. Against the shipped 16.1 enumeration
+// the number at this build's 3076 is
+// MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged, which is what an
+// incoming 16.1 number 3076 would become.
+// ConnectionCocoa.mm's `decoder->messageName() == MessageName::InitializeConnection`
+// then never fires and the connection never completes its handshake.
+// The tables this file carries are generated from that shipped 16.1 tag. Its anchors are
+// firstSynchronousOldMessageNumber 2946, InitializeConnection 2478 and Last 3184, all three read
+// out of the header that tag's own generate-message-receiver.py wrote. The boundary is
+// Read from the 16.1 header's own messageIsSync comparison rather than counted here.
+// The older side is generated from releases/Apple/Safari-16.1-iOS-16.1 (614.2.9.0.10), the same
+// build the system framework on the device is.
+// One message this build cannot map is worth naming. In the shipped 16.1 enumeration `Terminate`
+// is number 2483, and 2483 is in this file's isKnownUnmappedIncomingNumber list.
+// Outside the newer synchronous section 852 of the 1126 that are outside it carry a number at or
+// below `Last`, so the peer would not reject them at all.
+// `scripts/gen-ipc-message-number-tables.py` exits 0 (rc=0) and reports four counts: 2911 shared,
+// 274 only older, 1175 only newer, and 708 unmapped incoming, 1175 on the outgoing side.
+    case MessageName::InitializeConnection: return static_cast<MessageName>(2478);
+    case MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged: return static_cast<MessageName>(3076);
+    case MessageName::WebSharedWorkerServerConnection_SuspendForBackForwardCache: return static_cast<MessageName>(2444);
+    case MessageName::WrappedAsyncMessageForTesting: return static_cast<MessageName>(3184);
+    case 2478: return MessageName::InitializeConnection;
+    case 3076: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;
+    case 2444: return MessageName::WebSharedWorkerServerConnection_SuspendForBackForwardCache;
+    case 3184: return MessageName::WrappedAsyncMessageForTesting;
+    case 2946: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;
diff --git a/Source/WebKit/Platform/IPC/Connection.cpp b/Source/WebKit/Platform/IPC/Connection.cpp
+    if (m_shouldTranslateMessageNames) {
+        auto name = encoder->messageName();
+        encoder->setMessageName(translateOutgoingMessageName(name));
+    }
diff --git a/Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm b/Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm
+    if (m_shouldTranslateMessageNames) {
+        auto translated = translateIncomingMessageName(decoder->messageName());
+        decoder->setMessageName(translated);
+    }
+"""

def with_hunk_headers(text: str, delta: int = 0) -> str:
    """`text` with a `@@` header per `diff --git` section, declaring that section's body.

    `BASE` carries no header, so every other case can edit one rule without the hunk counts entering
    the answer. This puts the headers back for the cases that are about the counts: each declares the
    old and new line counts its own body carries, `delta` moves the declared new-side count off the
    true one, and a fixture that declares a count its body does not have is exactly the drift a
    comment edit makes when it adds lines and forgets the header.

    The counts are taken from the body with the rule the checker is supposed to use, so this helper
    is only as good as that rule. It is not the authority for the prefixed-line case: the header
    there is written by hand, because a helper that shared the checker's mistake would agree with it.
    """
    sections = []
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            sections.append((raw, []))
        elif sections:
            sections[-1][1].append(raw)
    out = []
    for header, body in sections:
        while body and body[-1] == "":
            body.pop()
        path = header.split(" b/", 1)[-1]
        old = sum(1 for line in body if line.startswith("-"))
        new = sum(1 for line in body if line.startswith("+"))
        out.append(header)
        out.append("--- a/%s" % path)
        out.append("+++ b/%s" % path)
        out.append("@@ -0,%d +1,%d @@" % (old, new + delta))
        out.extend(body)
    return "\n".join(out) + "\n"


# The header is written by the helper rather than read back, and the helper is an independent count:
# it splits on `diff --git` and counts `+`/`-` prefixes, never skipping a spelling. The two readings
# therefore disagree on this fixture — the helper calls the body 1 old and 37 new, a walk that skips
# those spellings calls it 0 and 36 — which is what makes the case below a test rather than a
# restatement of the same rule.
CALL = "+    m_connection->setShouldTranslateMessageNames(true);\n"
CALL_NO_ARGUMENT = "+    m_connection->setShouldTranslateMessageNames();\n"
DECLARATION = "+    void setShouldTranslateMessageNames(bool shouldTranslate);\n"
MACRO_INITIALISER = "+    bool m_shouldTranslateMessageNames { IOS_16_MESSAGE_NUMBER_TRANSLATION };\n"

# The two guards, quoted with the line beneath them: the guard line alone occurs twice (once per read
# site), and replacing the first match would silently test the send path every time.
SEND_GUARD = (
    "+    if (m_shouldTranslateMessageNames) {\n"
    "+        auto name = encoder->messageName();\n"
)
RECEIVE_GUARD = (
    "+    if (m_shouldTranslateMessageNames) {\n"
    "+        auto translated = translateIncomingMessageName(decoder->messageName());\n"
)

# Round 5's P2-1: the switch is a call and two reads, and only the call was guarded. Each entry
# replaces a guard with the shape a bypass took, and must land on exit 1. The folded call is the
# exception: it appends, because the committed patch carries no call for it to replace.
READ_SITE_CASES = (
    (
        "a patch whose send path runs the block with no condition fails",
        SEND_GUARD,
        "+    {\n+        auto name = encoder->messageName();\n",
        "guarded by something other than the member",
    ),
    (
        "a patch whose send path guards on the deployment macro fails",
        SEND_GUARD,
        "+    if (IOS_16_MESSAGE_NUMBER_TRANSLATION) {\n+        auto name = encoder->messageName();\n",
        "guarded by something other than the member",
    ),
    (
        "a patch that assigns the member directly on the send path fails",
        SEND_GUARD,
        "+    m_shouldTranslateMessageNames = true;\n"
        "+    if (m_shouldTranslateMessageNames) {\n"
        "+        auto name = encoder->messageName();\n",
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a patch that changes only the receive path's guard fails",
        RECEIVE_GUARD,
        "+    {\n+        auto translated = translateIncomingMessageName(decoder->messageName());\n",
        "the receive path that rewrites an incoming message number",
    ),
)

# Round 6's P2-1: two assignment shapes that compile, turn the flag on, and used to pass because the
# regex demanded whitespace then a single `=`. Each must land on exit 1.
ASSIGNMENT_CASES = (
    (
        "a patch that compound-assigns the member fails",
        SEND_GUARD,
        "+    m_shouldTranslateMessageNames |= true;\n" + SEND_GUARD,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a patch that assigns through parentheses around the member fails",
        SEND_GUARD,
        "+    (m_shouldTranslateMessageNames) = true;\n" + SEND_GUARD,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a patch that compares the member is not read as an assignment",
        SEND_GUARD,
        "+    if (m_shouldTranslateMessageNames == true) { return; }\n" + SEND_GUARD,
        None,
    ),
)

# Round 6's P2-2: the guard is a property of every translation call, not of the two files that held
# the read sites. A caller added to a third file, or to an exempt file but not in a declaration, must
# fail; a correctly guarded one-line caller must pass, so the rule is not merely strict.
THIRD_FILE_CALL = (
    "diff --git a/Source/WebKit/Platform/IPC/Connection.h b/Source/WebKit/Platform/IPC/Connection.h\n"
    "+void unrelated() { encoder->setMessageName(translateOutgoingMessageName(encoder->messageName())); }\n"
    "diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp\n"
    "+void bad() {\n+    translateIncomingMessageName(x);\n+}\n"
)
GUARDED_INLINE_CALL = (
    "diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp\n"
    "+void ok() { if (m_shouldTranslateMessageNames) { translateOutgoingMessageName(x); } }\n"
)
GUARD_CLOSED_INLINE_CALL = (
    "diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp\n"
    "+void bad() { if (m_shouldTranslateMessageNames) { } translateOutgoingMessageName(x); }\n"
)

CALL_IN_THIRD_FILE = (
    "diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp\n"
)

# Round 7's P2-1: the guard was recognised only as the innermost block compared to one literal line, so
# correct code failed whenever anything sat between the guard and the call, or whenever the guard was
# spelled differently. Each entry here is correct and must exit 0.
GUARD_SHAPES = (
    (
        "a translation call inside a for loop under the guard passes",
        CALL_IN_THIRD_FILE
        + "+void ok(unsigned count) {\n"
        "+    if (m_shouldTranslateMessageNames) {\n"
        "+        for (unsigned index = 0; index < count; ++index) {\n"
        "+            translateOutgoingMessageName(x);\n"
        "+        }\n"
        "+    }\n"
        "+}\n",
    ),
    (
        "a translation call inside a nested if under the guard passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        "+    if (m_shouldTranslateMessageNames) {\n"
        "+        if (someCondition) {\n"
        "+            translateOutgoingMessageName(x);\n"
        "+        }\n"
        "+    }\n"
        "+}\n",
    ),
    (
        "a call under a braceless member if passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        "+    if (m_shouldTranslateMessageNames)\n"
        "+        translateOutgoingMessageName(x);\n"
        "+}\n",
    ),
    (
        "a call under a member if with another conjunct passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        "+    if (m_shouldTranslateMessageNames && !m_inDispatchTesting) {\n"
        "+        translateOutgoingMessageName(x);\n"
        "+    }\n"
        "+}\n",
    ),
    (
        "a call under a member if spelled this-> passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        "+    if (this->m_shouldTranslateMessageNames) {\n"
        "+        translateOutgoingMessageName(x);\n"
        "+    }\n"
        "+}\n",
    ),
    (
        "a call under a twice-negated member if passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if (!!m_shouldTranslateMessageNames) {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
    ),
    (
        "a call under a parenthesised member if passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if ((m_shouldTranslateMessageNames)) {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
    ),
    (
        "a call under a member if with two negated conjuncts passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if (m_shouldTranslateMessageNames && !m_a && !m_b) {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
    ),
    (
        "a call under a member if with a conjunct the parser cannot read passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if (m_shouldTranslateMessageNames && someCall(x)) {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
    ),
    (
        "a call under an alternation that still implies the member passes",
        CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if ((m_shouldTranslateMessageNames || fallbackOn) && m_shouldTranslateMessageNames) {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
    ),
)

# Round 8's P2-1: the guard was accepted whenever the condition mentioned the member, so a negated or
# widened condition passed. Each entry below is a condition that can be true with the switch off, so
# the translation would run exactly when it must not. Every one has to land on exit 1.
GUARD_INVERSIONS = (
    ("a call under a negated member if fails", "!m_shouldTranslateMessageNames"),
    ("a call under a member if equal to false fails", "m_shouldTranslateMessageNames == false"),
    ("a call under a member if not equal to true fails", "m_shouldTranslateMessageNames != true"),
    ("a call under a negated this-> member if fails", "!this->m_shouldTranslateMessageNames"),
    ("a call under a member if widened with or fails", "m_shouldTranslateMessageNames || fallbackOn"),
    ("a call under a member if or its own negation fails",
     "m_shouldTranslateMessageNames || !m_shouldTranslateMessageNames"),
    ("a call under another object's member if fails", "connection->m_shouldTranslateMessageNames"),
    # `!m && x` is the form that a misread truth table lets through: it runs the translation only
    # when the switch is off, which is the exact inversion this check exists to refuse.
    ("a call under a negated member if with a conjunct fails", "!m_shouldTranslateMessageNames && m_ready"),
    # `&&` binds tighter than `||`, so this is `(m && x) || !m`, which is true with the switch off.
    # A reader that collapses the `&&` to a single factor loses that.
    ("a call under a conjunction or a negated member fails",
     "m_shouldTranslateMessageNames && m_ready || !m_shouldTranslateMessageNames"),
    # Round 9's P2-3: the member on the right of a disjunction. Every `||` case above put it on the
    # left, so a reader that discarded the accumulated left value still saw the member and passed.
    ("a call under a disjunction with the member on the right fails",
     "ready || m_shouldTranslateMessageNames"),
    # The same, wrapped: the wrapper is transparent, so it cannot launder a widened condition.
    ("a call under a wrapped disjunction with a fallback fails",
     "UNLIKELY(m_shouldTranslateMessageNames || fallbackOn)"),
    # The wrapper's own parentheses have to stay. Without them `UNLIKELY(m && ready) == other` reads
    # as `m && ready == other`, which is a conjunction and passes; the real condition is an equality
    # whose value with the member false is unknown, so it has to be refused.
    ("a call under a wrapped conjunction compared to another flag fails",
     "UNLIKELY(m_shouldTranslateMessageNames && m_ready) == m_disabled"),
)

# Round 9's P2-2: three wrappers that return their argument's boolean value. A send hot path that wraps
# the guard in `UNLIKELY` is the obvious future edit — Connection.h already has five of them — so each
# has to read as the bare condition, and a negated one has to read as a negated condition.
GUARD_WRAPPERS = (
    ("a call under a member if wrapped in UNLIKELY passes", "UNLIKELY(m_shouldTranslateMessageNames)"),
    ("a call under a member if wrapped in LIKELY passes", "LIKELY(m_shouldTranslateMessageNames)"),
    ("a call under a member if wrapped in static_cast<bool> passes",
     "static_cast<bool>(m_shouldTranslateMessageNames)"),
    ("a call under a wrapped member if with a conjunct passes",
     "UNLIKELY(m_shouldTranslateMessageNames) && !m_inDispatchTesting"),
)

# Round 9's P2-1: an `if` written with its body on the same line is a complete statement, so what
# follows it is not guarded by it. The condition alone would say it is, which is why the check reads
# what comes after the closing parenthesis too.
BRACELESS_BODY_SHAPES = (
    ("a call after a same-line return body fails", "+    if (m_shouldTranslateMessageNames) { return; }\n"),
    ("a call after a same-line empty body fails", "+    if (m_shouldTranslateMessageNames) { }\n"),
    ("a call after a same-line goto body fails", "+    if (m_shouldTranslateMessageNames) goto done;\n"),
    ("a call after a same-line wrapped return body fails",
     "+    if (UNLIKELY(m_shouldTranslateMessageNames)) { return; }\n"),
)

# Round 10's P2-1: only a statement whose own body opens where the statement ends guards what follows.
# `if (m) { } else if (ok) {` ends with a brace and looked guarded, but the brace belongs to the `else`
# branch, which runs when the member is false. Each entry here is a condition that implies the member
# and a tail that makes the following call unguarded anyway.
GUARD_TAIL_SHAPES = (
    ("a call after an else-if branch on another condition fails",
     "+    if (m_shouldTranslateMessageNames) { } else if (m_ready) {\n"),
    ("a call after an else branch fails",
     "+    if (m_shouldTranslateMessageNames) { } else {\n"),
    ("a call after an else branch of a returning guard fails",
     "+    if (m_shouldTranslateMessageNames) { return; } else {\n"),
    ("a call after a do loop that follows the guard fails",
     "+    if (m_shouldTranslateMessageNames) { m_ready = true; } do {\n"),
    ("a call after a while loop that follows the guard fails",
     "+    if (m_shouldTranslateMessageNames) { m_ready = true; } while (false) {\n"),
)

# Round 10's P2-1, the other direction: an `else if` on the member is a guard for its own body. Reading
# only the first branch, or refusing any statement that does not start with `if`, called this a bypass.
ELSE_IF_GUARDS = (
    ("a call under an else-if on the member passes",
     "+    if (m_ready) { } else if (m_shouldTranslateMessageNames) {\n"),
    ("a call under a multi-line else-if on the member passes",
     "+    if (m_ready) {\n"
     "+        m_started = true;\n"
     "+    } else if (m_shouldTranslateMessageNames) {\n"),
)

# Round 10's P2-2: a block comment between the condition and the body. The comment is not part of the
# shape, so it is stripped from the remainder before the `""`/`"{"` decision; leaving it in made this
# correct code fail. `//` was already cut when the patch was read.
COMMENTED_BRACELESS_GUARDS = (
    ("a braceless guard followed by a block comment passes",
     "+    if (m_shouldTranslateMessageNames) /* the switch */\n"
     "+        encoder->setMessageName(translateOutgoingMessageName(name));\n"),
    ("a braceless guard followed by a line comment passes",
     "+    if (m_shouldTranslateMessageNames) // the switch\n"
     "+        encoder->setMessageName(translateOutgoingMessageName(name));\n"),
)

# Round 11's P2-1: a `}` closes the block it ends, wherever it sits on the line. Counting only the
# leading run of `}` left the guard on the stack after `return; }` or `} }` had closed it, so the call
# that followed read as guarded. Each entry here closes the guard somewhere other than at the start of
# its own statement.
GUARD_CLOSED_INLINE = (
    ("a call after a guard whose body ends with a return on the closing line fails",
     ["if (m_shouldTranslateMessageNames) {",
      "    pending();",
      "    return; }"]),
    ("a call after a guard and an if both closed with a space between fails",
     ["if (m_shouldTranslateMessageNames) {",
      "    if (isSync) {",
      "        pending();",
      "    } }"]),
    ("a call after a guard closed at the end of its body line fails",
     ["if (m_shouldTranslateMessageNames) {",
      "    pending(); }"]),
    ("a call after a guard closed with two braces together fails",
     ["if (m_shouldTranslateMessageNames) {",
      "    pending(); }}"]),
)

# Round 11's P2-1, the other direction. `} } else if (m) {` closes two blocks and heads the branch it
# opens; the branch is the guard for the call inside it, so this is correct code. Tightening the closer
# count without this case would refuse it, because the head test saw `} else if` after stripping one
# brace.
GUARD_CLOSED_THEN_ELSE_IF_ON_MEMBER = (
    ("a call under an else-if on the member after two closing braces passes",
     ["if (m_ready) {",
      "    pending();",
      "} } else if (m_shouldTranslateMessageNames) {",
      "    translateOutgoingMessageName(x);",
      "}"]),
    ("a call under an else-if on the member after one closing brace passes",
     ["if (m_ready) {",
      "    pending();",
      "} else if (m_shouldTranslateMessageNames) {",
      "    translateOutgoingMessageName(x);",
      "}"]),
)

# Round 11's P2-2: an identifier ending in `if` is not a branch head. Without the word boundary the
# `if(` inside `notif(` wins the last-head search and the condition is cut inside the callee's name,
# which refused `m && notif(x)` — a conjunction that implies the member whatever `notif` returns.
#
# Each entry is (description, condition, expected exit code). The refusals are pinned too, and for two
# different reasons: a call whose own name ends in `if` leaves a condition the evaluator cannot settle,
# and a `count_if` conjunct is also outside the condition grammar because `::`, `.` and `,` are not
# tokens. Both are refused today, and pinning them keeps the second reason from being mistaken for a
# word-boundary defect if the grammar ever widens.
IDENTIFIER_ENDING_IN_IF = (
    ("a guard with a conjunct whose callee ends in if passes",
     "if (m_shouldTranslateMessageNames && notif(x)) {", 0),
    ("a guard with a conjunct whose callee ends in find_if passes",
     "if (m_shouldTranslateMessageNames && find_if(p)) {", 0),
    ("a guard whose whole condition is a call ending in if fails",
     "if (check_if(m_shouldTranslateMessageNames)) {", 1),
    ("a guard with a count_if conjunct compared to zero fails",
     "if (m_shouldTranslateMessageNames && std::count_if(v.begin(), v.end(), p) > 0) {", 1),
)

# Round 12: one lexical view for comments and literals. Each entry is a body line and the exit code the
# whole fixture has when the call is INSIDE the guard. A brace inside a string, a character constant or
# a comment is text, not structure, so it must not close the guard; the fixture is correct code and each
# of these exited 1 while the brace counters read the raw statement.
BRACES_IN_TEXT = (
    ("a brace in a string literal inside the guard passes",
     'RELEASE_LOG(IPC, "closing }");', 0),
    ("a brace in a character constant inside the guard passes",
     "char c = '}';", 0),
    ("a brace in a block comment inside the guard passes",
     "auto x = 1; /* } */", 0),
    ("an opening brace in a string does not hide a character constant",
     "log(\"{\"); char c = '}';", 0),
)

# The other direction: a brace in a string must not cancel the real closure, and a `//` inside a string
# must not cut the statement before that closure. Both fixtures end the guard inside the string's own
# line and then call outside it, so the answer is 1 — the call is not guarded.
BRACES_IN_TEXT_MASKING_CLOSURE = (
    ("an opening brace in a string does not hide the closure",
     'RELEASE_LOG(IPC, "closing {"); }', 1),
    ("a slash pair in a string does not cut the statement before the closure",
     'log("http://x"); }', 1),
    ("a closing brace in a string does not close the guard by itself",
     'log("}");', 0),
)

# `code_view` itself, on the shapes the tower named. The expected string is exact so a change in the
# sentinel shows up here rather than in a downstream judgement. A raw string is one literal including
# its `R"delim(` head; a brace in it is text.
#
# The last entries pin the ORDER: a comment is skipped before a literal is looked for. A pass that
# replaced literals first would find the apostrophe in `/* don't */` and treat it as a character
# constant's opener, so `/* don't */ log('}');` would leave `/* don` as code and lose the `)` and `;`.
CODE_VIEW_CASES = (
    ("Locker l { x };", "Locker l { x };"),
    ("bool m_x { false };", "bool m_x { false };"),
    ('R"x(})x"', "?"),
    ('"\\\\\\""', "?"),
    ("/* don't */", " "),
    ("// it's }", " "),
    ('log(R"x(})x"); }', "log(?); }"),
    ("x(); /* don't */ }", "x();   }"),
    ("x(); /* it's { */ }", "x();   }"),
    ("/* a \"q\" */ }", "  }"),
    ("/* don't */ log('}');", "  log(?);"),
    ("x(); /* don't */ log('}'); }", "x();   log(?); }"),
    # A digit separator is punctuation inside a pp-number, so the number stays verbatim in the view.
    # Read as a character constant it swallowed the rest of the statement: the closing brace above
    # disappeared, and a setter call written after it passed the whole check.
    ("1'000", "1'000"),
    ("0xFF'FF", "0xFF'FF"),
    ("1'000'000", "1'000'000"),
    ('RELEASE_LOG(IPC, "%d", 1\'000); }', "RELEASE_LOG(IPC, ?, 1'000); }"),
    # The other side of the same rule: these are character constants because their prefix is not a
    # digit-leading run, so they are still swallowed. `u8'1'` is the one that a rule keyed only on the
    # character before the apostrophe would read as a separator.
    ("u8'1'", "u8?"),
    ("'1'", "?"),
    # A digit-leading run is not enough on its own: the character after the apostrophe decides whether
    # a number continues there. `1';` is a character constant that a rule without the lookahead would
    # read as a separator, leaving `; }` as code and counting a brace that is really inside it.
    ("1';", "1?"),
    ("x = 1'; }", "x = 1?"),
)

# Round 13's P2-1: a `'` inside a pp-number is not a quote. Each entry is a body or guard line whose
# closing brace must still be counted, and the code around it must judge the way the compiler would.
DIGIT_SEPARATOR_CLOSERS = (
    ("a digit separator does not hide the closing brace of its statement",
     'RELEASE_LOG(IPC, "%d", 1\'000); }', 1),
    ("a hex digit separator does not hide the closing brace of its statement",
     "x = 0xFF'FF; }", 1),
    ("two digit separators do not hide the closing brace of their statement",
     "t = 1'000'000; }", 1),
)

# The same defect end to end. The first entry is the one that matters most: the separator used to
# swallow the setter call that followed it, so `setShouldTranslateMessageNames(true)` passed the check
# the whole mission exists for. The control differs only in the number's spelling.
DIGIT_SEPARATOR_PATCHES = (
    ("a setter call after a line with a digit separator fails",
     '+    RELEASE_LOG(IPC, "budget %d", 1\'000); setShouldTranslateMessageNames(true);\n', 1,
     "calls setShouldTranslateMessageNames"),
    ("a setter call after the same line without a separator fails",
     '+    RELEASE_LOG(IPC, "budget %d", 1000); setShouldTranslateMessageNames(true);\n', 1,
     "calls setShouldTranslateMessageNames"),
)

# A digit separator inside correct code. These must pass: the separator is digits, not structure, and
# reading it as a quote removed the loop's `{`, which popped the guard and refused the call inside it.
DIGIT_SEPARATOR_CORRECT_CODE = (
    ("a loop bound with a digit separator does not close the guard",
     "for (unsigned i = 0; i < 1'000; ++i) {", 0),
    ("the same loop bound without a separator passes", "for (unsigned i = 0; i < 1000; ++i) {", 0),
)

# Round 13's P2-2: a call after a closure in its own statement. The two spellings differ only in where
# the statement boundary falls, and before this round they disagreed: the one-line form was refused and
# the split form passed, because the chain is sampled at the statement's start.
GUARD_CLOSED_THEN_CALL_IN_ONE_STATEMENT = (
    ("a call after a closed guard in the same statement fails",
     ["if (m_shouldTranslateMessageNames) { pending(); } "
      "auto translated = translateIncomingMessageName(decoder->messageName());"]),
    ("the same call joined from two lines fails, because joining cannot make it guarded",
     ["if (m_shouldTranslateMessageNames) {",
      "pending(); } auto translated = translateIncomingMessageName(decoder->messageName());"]),
)


MULTILINE_SETTER = (
    "+void setShouldTranslateMessageNames(bool shouldTranslate)\n"
    "+{\n"
    "+    m_shouldTranslateMessageNames = shouldTranslate;\n"
    "+}\n"
)
INLINE_SETTER = (
    "+void setShouldTranslateMessageNames(bool shouldTranslate) "
    "{ m_shouldTranslateMessageNames = shouldTranslate; }\n"
)

# Round 7's P2-2: one statement can hold two calls, and checking only the first let the unguarded one
# through. The second call sits after the guard's block has closed.
DOUBLE_CALL_ONE_LINE = (
    CALL_IN_THIRD_FILE
    + "+void bad() { if (m_shouldTranslateMessageNames) { translateOutgoingMessageName(x); } "
    "translateOutgoingMessageName(y); }\n"
)

# Round 7's P2-3: two more spellings of the same assignment that compile and used to pass.
PARENTHESISED_ASSIGNMENT = "+    ((m_shouldTranslateMessageNames)) = true;\n"
WRAPPED_ASSIGNMENT = "+    m_shouldTranslateMessageNames\n+        = true;\n"

# The parameter name alone used to exempt an assignment, so any function taking `bool shouldTranslate`
# could turn the flag on. The value is the setter's, but the body is not.
SETTER_PARAMETER_ELSEWHERE = (
    CALL_IN_THIRD_FILE
    + "+void steer(bool shouldTranslate) {\n"
    "+    m_shouldTranslateMessageNames = shouldTranslate;\n"
    "+}\n"
)
# The other half of the same rule: inside the setter's body, the value still has to be the parameter.
# `= true` there turns the flag on for every connection the setter is called on, which is the state the
# whole patch exists to keep reachable only through a call this check can see.
SETTER_BODY_IGNORES_PARAMETER = (
    "+void setShouldTranslateMessageNames(bool shouldTranslate)\n"
    "+{\n"
    "+    (void)shouldTranslate;\n"
    "+    m_shouldTranslateMessageNames = true;\n"
    "+}\n"
)
# The same body shape, correct this time: a first line that begins with `(` must not swallow the `{`
# above it. If it did, the whole body would read as one statement and the legitimate assignment would
# be reported as sitting outside the setter.
MULTILINE_SETTER_WITH_CAST = (
    "+void setShouldTranslateMessageNames(bool shouldTranslate)\n"
    "+{\n"
    "+    (void)shouldTranslate;\n"
    "+    m_shouldTranslateMessageNames = shouldTranslate;\n"
    "+}\n"
)
# And on the read side: a guarded block whose first line begins with `(`. The setter case above is the
# one that fails if the `{` is joined to the next line; this one pins the read-side verdict, where the
# one-line guard path still recognises the guard.
GUARD_WITH_CAST_FIRST_LINE = (
    CALL_IN_THIRD_FILE
    + "+void ok(bool inDispatch) {\n"
    "+    if (m_shouldTranslateMessageNames) {\n"
    "+        (void)inDispatch;\n"
    "+        translateOutgoingMessageName(x);\n"
    "+    }\n"
    "+}\n"
)

# Appended rather than substituted: the committed patch carries no setter call, so there is nothing to
# replace. This is the wrap the line-at-a-time check used to miss.
FOLDED_CALL = "+    m_connection->setShouldTranslateMessageNames\n+        (true);\n"

# The needles that used to be bare numbers, and the wrong value each must now fail on. Every number
# here is also a real case label in the tables, which is why a needle that searched the whole patch
# matched whatever the prose said.
DRIFTED_COUNTS = (
    ("the entry-count difference, written as a subtraction",
     "4086 - 3185 = 901 entries", "4086 - 3186 = 900 entries"),
    ("the two one-sided counts", "1175 only in this tree", "1174 only in this tree"),
    ("the same-number count of the index comparison",
     "5 happen to carry the same number", "6 happen to carry the same number"),
)

VIDEO_FRAME = "MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"
ROOT_VIEW = "MessageName::WebPageProxy_RootViewToAccessibilityScreen"
SWAPPED_NAMES = (
    BASE.replace("is " + VIDEO_FRAME, "is " + ROOT_VIEW)
    .replace("`case 2478: return MessageName::InitializeConnection;`",
             "`case 2478: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;`")
)

# Each must fail on a sentence deletion or a value change, which is what "the needle is unique and
# necessary" means.
SENTENCE_NEEDLES = (
    (
        "a patch that deletes the InitializeConnection-is-3076 sentence fails",
        "// `InitializeConnection` is number 3076 in this tree. The shipped 16.1 enumeration\n",
        "// The connection handshake is the message discussed here.\n",
        "InitializeConnection is 3076 in this tree",
    ),
    (
        "a patch that drifts 2906 to 2905 fails",
        "+// sides and 2906 do not",
        "+// sides and 2905 do not",
        "the different-number count",
    ),
    (
        "a patch that retags the shipped 16.1 tag fails",
        "+// (releases/Apple/Safari-16.1-iOS-16.1) has 3185",
        "+// (releases/Apple/Safari-16.2-iOS-16.2) has 3185",
        "the shipped 16.1 enumeration's size and tag",
    ),
    (
        "a patch that drifts the incoming InitializeConnection case fails",
        "// `case 2478: return MessageName::InitializeConnection;`",
        "// `case 2479: return MessageName::InitializeConnection;`",
        "that incoming case written out",
    ),
    (
        "a patch that drifts the unmapped-incoming count to 707 fails",
        "1175 only newer, and 708 unmapped",
        "1175 only newer, and 707 unmapped",
        "the unmapped-incoming count, stated as a count",
    ),
    (
        "a patch that drops the generator's exit status fails",
        "exits 0 (rc=0) and reports four",
        "and reports four",
        "the generator's exit status and the four counts it prints",
    ),
)

# The table can be retargeted or renumbered while the prose still reads correctly, which is exactly
# the silent rename this check exists to prevent.
TABLE_CASES = (
    (
        "a patch that retargets incoming case 3076 at another real message fails",
        "+    case 3076: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;\n",
        "+    case 3076: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;\n",
        "3076 declares RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged",
    ),
    (
        "a patch that renumbers incoming case 3076 to 3075 fails",
        "+    case 3076: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;\n",
        "+    case 3075: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;\n",
        "no entry for 3076 declares RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged",
    ),
    (
        "a patch that retargets the handshake's outgoing number fails",
        "+    case MessageName::InitializeConnection: return static_cast<MessageName>(2478);\n",
        "+    case MessageName::InitializeConnection: return static_cast<MessageName>(2444);\n",
        "InitializeConnection translates to its shipped 16.1 number",
    ),
    (
        "a patch that gives 2444 back to the handshake fails",
        "+    case 2444: return MessageName::WebSharedWorkerServerConnection_SuspendForBackForwardCache;\n",
        "+    case 2444: return MessageName::InitializeConnection;\n",
        "the message that owns the number the handshake used to be translated to",
    ),
)


def run(patch_text: str | None) -> subprocess.CompletedProcess:
    if patch_text is None:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--patch", "/nonexistent/patch"],
            capture_output=True,
            text=True,
        )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.patch"
        path.write_text(patch_text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--patch", str(path)],
            capture_output=True,
            text=True,
        )


CASES = (
    ("the committed patch passes", None, 0, None),
    ("a re-added call fails", BASE + CALL, 1, "calls setShouldTranslateMessageNames"),
    ("a call with no argument fails", BASE + CALL_NO_ARGUMENT, 1, "calls setShouldTranslateMessageNames"),
    ("the setter's declaration passes", BASE + DECLARATION, 0, None),
    ("a macro initialiser fails", BASE + MACRO_INITIALISER, 1, "initialiser is not the literal false"),
    (
        "a patch that drops the tag fact fails",
        BASE.replace("(releases/Apple/Safari-16.1-iOS-16.1) has 3185", "(that release) has 3185"),
        1,
        "no longer records",
    ),
    (
        "a patch that drops the measured counts fails",
        BASE.replace("2906 do not", "many do not"),
        1,
        "no longer records",
    ),
    (
        "a patch that puts the snapshot back as the tables' basis fails",
        BASE + "+// These tables come from the Safari-614.1.9 snapshot.\n",
        1,
        "16.0-generation snapshot",
    ),
    (
        "the same mention allowed as superseded passes",
        BASE + "+// An earlier table came from the Safari-614.1.9 snapshot and was wrong.\n",
        0,
        None,
    ),
    ("a missing patch is reported distinctly", None, 2, None),
)

# The hunk-count rule, added after a comment edit in M141 added lines to two hunks without updating
# their `@@` headers: the patch applied to nothing, `git apply --check` reported `patch is corrupt`,
# and no other check in this file or in the build workflow noticed, because the counts are never
# derived by git and every other rule reads the patch's text rather than its shape.
#
# The first case is the control: a fixture with correct headers has to pass, or the two failures
# below would be refused for a reason other than the count under test and this block would prove
# nothing. The two off-by-N cases move the declared count in both directions, and they are not
# symmetric in what `git apply` does — measured on scratch trees, an overcount stops the apply with
# `patch is corrupt` while an undercount applies the truncated body and exits 0. Both are drift this
# check has to report, because the undercount is the one no exit status would ever show.
CASES += (
    (
        "a patch whose hunk headers match their bodies passes",
        with_hunk_headers(BASE),
        0,
        None,
    ),
    (
        "a hunk header that undercounts its body by one fails",
        with_hunk_headers(BASE, delta=-1),
        1,
        "a hunk header disagrees with the lines it introduces",
    ),
    (
        "a hunk header that overcounts its body by two fails",
        with_hunk_headers(BASE, delta=2),
        1,
        "a hunk header disagrees with the lines it introduces",
    ),
)

# A body line may itself begin with `---` or `+++`. Read as file headers, the two lines below are
# skipped and the body counts one short on each side; counted as body lines, they are a deletion and
# an addition and the header's `-10,2 +10,2` is right. The header is written to match the second
# reading, so this case passes only if the walk stops at `@@` and `diff --git` instead of at every
# line that looks like a header. Without it, a walk that skipped those spellings everywhere would
# still pass all three cases above, because none of them puts one inside a body.
CASES += (
    (
        "a body whose own lines begin with --- and +++ still counts correctly",
        with_hunk_headers(
            BASE.replace(
                "+    case 2946: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;\n",
                "+    case 2946: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;\n"
                "--- a deleted comment whose own text begins with two dashes\n"
                "+++ an added comment whose own text begins with two pluses\n",
            )
        ),
        0,
        None,
    ),
)

# Negative cases for the drift that P2-1 was about: each needle used to be a bare number that also
# occurs as a case label in the tables, so the check passed however the prose around it read. These
# cases fail if a needle ever goes back to matching code, and they name the drifted value in the
# expected message so a partial match cannot pass.
for description, right, wrong in DRIFTED_COUNTS:
    CASES += (
        (
            "a patch that drifts %s to %s fails" % (description, wrong),
            BASE.replace(right, wrong),
            1,
            "no longer records %s" % description,
        ),
    )

CASES += (
    (
        "a patch that swaps the two case-3076 message names fails",
        SWAPPED_NAMES,
        1,
        "no longer records",
    ),
)

for description, old_text, new_text, needle_description in SENTENCE_NEEDLES:
    CASES += ((description, BASE.replace(old_text, new_text), 1, "no longer records %s" % needle_description),)

for description, old_text, new_text, expected_message in TABLE_CASES:
    CASES += ((description, BASE.replace(old_text, new_text), 1, expected_message),)

for description, old_text, new_text, expected_message in READ_SITE_CASES:
    CASES += ((description, BASE.replace(old_text, new_text, 1), 1, expected_message),)

CASES += (
    (
        "a patch that folds the setter call across two lines still fails",
        BASE + FOLDED_CALL,
        1,
        "calls setShouldTranslateMessageNames",
    ),
)

for description, old_text, new_text, expected_message in ASSIGNMENT_CASES:
    if expected_message is None:
        # A comparison is not an assignment: exit 0 this rule, so the fixture must still pass overall.
        CASES += ((description, BASE.replace(old_text, new_text, 1), 0, None),)
    else:
        CASES += ((description, BASE.replace(old_text, new_text, 1), 1, expected_message),)

CASES += (
    (
        "a translation call added to a third file fails",
        BASE + THIRD_FILE_CALL,
        1,
        "calls the translation outside the member's guard",
    ),
    (
        "a translation call in an exempt file but outside a declaration fails",
        BASE + THIRD_FILE_CALL,
        1,
        "Source/WebKit/Platform/IPC/Encoder.cpp",
    ),
    (
        "a correctly guarded one-line translation call passes",
        BASE + GUARDED_INLINE_CALL,
        0,
        None,
    ),
    (
        "a one-line call after the guard block has closed fails",
        BASE + GUARD_CLOSED_INLINE_CALL,
        1,
        "calls the translation outside the member's guard",
    ),
    (
        "a setter written across four lines passes",
        BASE.replace(
            "+void setShouldTranslateMessageNames(bool shouldTranslate) { "
            "m_shouldTranslateMessageNames = shouldTranslate; }\n",
            MULTILINE_SETTER,
        ),
        0,
        None,
    ),
    (
        "a setter written on one line passes",
        BASE.replace(
            "+void setShouldTranslateMessageNames(bool shouldTranslate) { "
            "m_shouldTranslateMessageNames = shouldTranslate; }\n",
            INLINE_SETTER,
        ),
        0,
        None,
    ),
    (
        "two translation calls on one line, the second unguarded, fails",
        BASE + DOUBLE_CALL_ONE_LINE,
        1,
        "calls the translation outside the member's guard",
    ),
    (
        "a patch that assigns the member through two pairs of parentheses fails",
        BASE.replace(SEND_GUARD, PARENTHESISED_ASSIGNMENT + SEND_GUARD, 1),
        1,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a patch that wraps the assignment onto the next line fails",
        BASE.replace(SEND_GUARD, WRAPPED_ASSIGNMENT + SEND_GUARD, 1),
        1,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "the setter's parameter assigned from another function fails",
        BASE + SETTER_PARAMETER_ELSEWHERE,
        1,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a setter body that assigns true instead of its parameter fails",
        BASE.replace(
            "+void setShouldTranslateMessageNames(bool shouldTranslate) { "
            "m_shouldTranslateMessageNames = shouldTranslate; }\n",
            SETTER_BODY_IGNORES_PARAMETER,
        ),
        1,
        "assigns m_shouldTranslateMessageNames outside the setter",
    ),
    (
        "a setter whose body begins with a cast passes",
        BASE.replace(
            "+void setShouldTranslateMessageNames(bool shouldTranslate) { "
            "m_shouldTranslateMessageNames = shouldTranslate; }\n",
            MULTILINE_SETTER_WITH_CAST,
        ),
        0,
        None,
    ),
    (
        "a guarded block whose first line begins with a cast passes",
        BASE + GUARD_WITH_CAST_FIRST_LINE,
        0,
        None,
    ),
)

for description, shape in GUARD_SHAPES:
    CASES += ((description, BASE + shape, 0, None),)

for description, condition in GUARD_INVERSIONS:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void bad() {\n"
        + "+    if (" + condition + ") {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        1,
        "calls the translation outside the member's guard",
    ),)

for description, condition in GUARD_WRAPPERS:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "+    if (" + condition + ") {\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        0,
        None,
    ),)

for description, guard in BRACELESS_BODY_SHAPES:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void bad() {\n"
        + guard
        + "+    translateOutgoingMessageName(x);\n"
        + "+}\n",
        1,
        "calls the translation outside the member's guard",
    ),)

for description, guard in GUARD_TAIL_SHAPES:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void bad() {\n"
        + guard
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        1,
        "calls the translation outside the member's guard",
    ),)

for description, guard in ELSE_IF_GUARDS:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + guard
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        0,
        None,
    ),)

for description, guard in COMMENTED_BRACELESS_GUARDS:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + guard
        + "+}\n",
        0,
        None,
    ),)

for description, lines in GUARD_CLOSED_INLINE:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void bad() {\n"
        + "".join("+    " + line + "\n" for line in lines)
        + "+    translateOutgoingMessageName(x);\n"
        + "+}\n",
        1,
        "calls the translation outside the member's guard",
    ),)

for description, lines in GUARD_CLOSED_THEN_ELSE_IF_ON_MEMBER:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void ok() {\n"
        + "".join("+    " + line + "\n" for line in lines)
        + "+}\n",
        0,
        None,
    ),)

for description, guard, expected_code in IDENTIFIER_ENDING_IN_IF:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void f() {\n"
        + "+    " + guard + "\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        expected_code,
        None if expected_code == 0 else "calls the translation outside the member's guard",
    ),)

for description, body, expected_code in BRACES_IN_TEXT:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void f() {\n"
        + "+    if (m_shouldTranslateMessageNames) {\n"
        + "+        " + body + "\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+    }\n"
        + "+}\n",
        expected_code,
        None if expected_code == 0 else "calls the translation outside the member's guard",
    ),)

for description, body, expected_code in BRACES_IN_TEXT_MASKING_CLOSURE:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void f() {\n"
        + "+    if (m_shouldTranslateMessageNames) {\n"
        + "+        " + body + "\n"
        + "+    translateOutgoingMessageName(x);\n"
        + "+}\n",
        expected_code,
        None if expected_code == 0 else "calls the translation outside the member's guard",
    ),)

# Statement-level readings that are not patch fixtures: the counter and the last-closer offset.
COUNTER_CASES = ()

for description, statement, expected_count in DIGIT_SEPARATOR_CLOSERS:
    COUNTER_CASES += ((description, statement, expected_count),)

for description, statement, expected_code, expected_message in DIGIT_SEPARATOR_PATCHES:
    CASES += ((
        description,
        BASE.replace(SEND_GUARD, SEND_GUARD + statement, 1),
        expected_code,
        expected_message,
    ),)

for description, body, expected_code in DIGIT_SEPARATOR_CORRECT_CODE:
    CASES += ((
        description,
        BASE + CALL_IN_THIRD_FILE
        + "+void f() {\n"
        + "+    if (m_shouldTranslateMessageNames) {\n"
        + "+        " + body + "\n"
        + "+        translateOutgoingMessageName(x);\n"
        + "+        }\n"
        + "+}\n",
        expected_code,
        None if expected_code == 0 else "calls the translation outside the member's guard",
    ),)

for description, lines in GUARD_CLOSED_THEN_CALL_IN_ONE_STATEMENT:
    CASES += ((
        description,
        BASE.replace(RECEIVE_GUARD,
                     "".join("+    " + line + "\n" for line in lines), 1),
        1,
        "calls the translation outside the member's guard",
    ),)


def open_boundary_section(module_doc: str) -> str | None:
    """The Open boundary text of `module_doc`, or None when it is gone.

    Both paragraphs are included: the first names the spellings of a closed guard, the second records
    the two-closer boundary. A direction stated in either has to match the build, and a check that
    read only the first paragraph would leave the second one free to say anything.
    """
    start = module_doc.find("Open boundary:")
    if start < 0:
        return None
    end = module_doc.find('\n\nThe "as committed"', start)
    return module_doc[start:] if end < 0 else module_doc[start:end]


# The Open boundary text is pinned, not parsed.
#
# Four rounds tried to read the direction out of the paragraph's English — first by the words present,
# then by negation, then by tense, then by refusing to guess at forms the reader could not match. Each
# round the previous reader was closed and the next unreadable shape walked through, because a reader
# that skips what it cannot match has a path where a claim contributes nothing and the check stays
# green. The last shape was the cheapest: a sentence stating the opposite in a form the pattern did not
# cover was not a failure, it was invisible.
#
# So nothing here reads English. Each direction sentence is listed with the direction it states, and
# three separate things are then required of it: the paragraph contains it verbatim, the direction word
# inside the sentence is the one its row declares, and the row's declaration is what the checker
# returns on a fixture of that shape. Because the sentence is compared with the row as well as the row
# with the build, the two copies of the direction cannot be edited into agreement on a wrong answer.
#
# The prose around those sentences is not pinned. It records history and mechanism, and after the
# pinned sentences are removed any direction word left in it is a failure, so narration cannot state a
# state the tables do not record. Rewriting a sentence that carries no direction word is free.
#
# `CLAIM` is (name, sentence as the paragraph writes it, is the call refused). Whitespace is normalised
# before matching, so a rewrap of the docstring does not read as a rewrite.
CLAIM = (
    ("both spellings", "is refused in both spellings", True),
    ("one-line form", "The one-line form is refused", True),
    ("split form", "The split form is refused", True),
    ("block comment", "is refused because the condition is read as", True),
    ("two-closer boundary", "The two-closer boundary is accepted", False),
)

# How many claims the paragraph makes. It exists because `CLAIM` alone cannot tell a dropped row from a
# table that never had it: deleting an entry and the sentence it pins together leaves every other check
# consistent — the fixture count derives from the same table and drops with it, and no direction word is
# left over. The number is a second copy of one fact, which is the property that makes the edit visible.
# A coordinated edit of this number, the row and the paragraph is a rewrite of the table rather than a
# regression in it, and no test in this file can tell those apart; that one is left to review.
OPEN_BOUNDARY_CLAIM_COUNT = 5

# The words this file reads as a direction. Closed on purpose: a synonym such as `permitted` is not
# guessed at, it is invisible, and recording that limit is cheaper than a fifth reader that would fail
# at the next synonym the same way the previous four failed at the next shape.
DIRECTION_WORD = re.compile(r"\b(?:refused|accepted)\b")

# The message that means the paragraph and the build disagree on a direction. Kept as one string
# because it is the one problem this comparison emits that no broken comparison can produce by
# accident: it needs a claim the paragraph makes and a fixture result that contradicts it, so a
# comparison that stopped checking either half cannot report it.
DIRECTION_DISAGREEMENT = "the paragraph says the call is"

# The message that means a pinned sentence's own direction word contradicts the boolean beside it. It is
# a separate string from `DIRECTION_DISAGREEMENT` because the two comparisons fail independently: one
# binds the sentence to its row, the other binds the row to a fixture run. A check that required only
# "some problem" would pass with either comparison deleted, since the other still speaks up.
SENTENCE_ROW_DISAGREEMENT = "the pinned sentence for"


def normalise(text: str) -> str:
    """One line of text, so a docstring rewrap does not read as a rewrite."""
    return re.sub(r"\s+", " ", text).strip()


def open_boundary_comparison_count(claims=CLAIM) -> int:
    """How many fixture runs a full pass over `claims` has to perform.

    Derived from the table rather than written as a number, so a claim added to `CLAIM` raises the
    expectation in the same edit. `check_open_boundary_comparison_is_live` compares it with the runs
    a real pass actually made, which is what a deleted comparison loop cannot satisfy.
    """
    return sum(len(open_boundary_fixtures(phrase)) for phrase, _, _ in claims)


def open_boundary_direction_problems(module_doc: str, claims=CLAIM,
                                    measurements: list | None = None) -> list[str]:
    """Every claim the paragraph fails to make, and every direction word it was not told about.

    Four things are asserted. The direction word inside each pinned sentence has to be the one its row
    declares, so the sentence and the boolean cannot be edited into agreement on a wrong answer — they
    are two copies of one fact and this compares them. Each pinned sentence has to appear in the
    paragraph verbatim, so deleting or rewording a claim is a failure rather than a silently different
    reading. Each row's declared direction has to match what the checker does on a fixture of that
    shape, so editing a constant to disagree with the build fails here. And after the pinned sentences
    are removed, no direction word may remain, so narration cannot state a state the tables do not
    record.

    The first comparison is the one that closes the dangerous direction. Without it the sentence was
    only ever checked for presence, so a paragraph and a row both rewritten to say `accepted` passed
    with the boolean still saying refused — the paragraph said the shape was let through and every
    assertion stayed green.

    No English is read, and nothing is skipped. That is the whole point: the previous version parsed
    sentences and ignored the ones its patterns did not match, and a sentence stating the opposite in
    an unmatched shape was invisible rather than wrong. The vocabulary it does read is the closed list
    in `DIRECTION_WORD`; a synonym is outside that list, which is a documented limit rather than a
    parse that quietly contributed nothing.

    `claims` and `measurements` exist for the checks below. `claims` lets one be run against an edited
    table without rewriting this file's source, and `measurements` collects one tuple per fixture run
    `(phrase, description, declares_refused, returncode)`, which is how a pass that stopped running
    the fixtures is detected instead of looking like a pass with nothing to disagree with.

    Pure apart from running the checker: it returns the problems instead of printing them, so the
    vacuity check below can reuse it against paragraphs it has broken on purpose.
    """
    section = open_boundary_section(module_doc)
    if section is None:
        return ["the Open boundary text is gone from this file's docstring"]

    body = normalise(section)
    problems = []
    for phrase, sentence, declared_refused in claims:
        declared_word = "refused" if declared_refused else "accepted"
        stated = DIRECTION_WORD.findall(sentence)
        if stated != [declared_word]:
            problems.append(
                "%s %r states %s but its row says %s"
                % (SENTENCE_ROW_DISAGREEMENT, phrase,
                   " and ".join(stated) if stated else "no direction", declared_word))
        if normalise(sentence) not in body:
            problems.append("the pinned claim for %r is gone from the Open boundary text" % phrase)
            continue
        for description, patch_text in open_boundary_fixtures(phrase):
            result = run(patch_text)
            if measurements is not None:
                measurements.append((phrase, description, declared_refused, result.returncode))
            actually_refused = result.returncode != 0
            if actually_refused != declared_refused:
                problems.append(
                    "%s %s for %r, but %s exits %d"
                    % (DIRECTION_DISAGREEMENT, declared_word,
                       phrase, description, result.returncode))

    for _, sentence, _ in claims:
        body = body.replace(normalise(sentence), " ")

    leftover = sorted(set(DIRECTION_WORD.findall(body)))
    if leftover:
        problems.append(
            "the Open boundary text states a direction this check does not account for: %s"
            % ", ".join(leftover))
    return problems


# The claims the paragraph makes. A claim that covers both spellings is measured against both, because
# a sentence saying both are refused must match both. The block-comment claim is measured on its own
# shape: with the bare `m` the paragraph writes, the condition cannot be settled, so the call inside it
# is refused. The two-closer claim is measured on the boundary the second paragraph records.
#
# Fixtures for the claims that do not name a closure spelling. They are separate from the two closure
# fixtures because they exercise different rules: the condition, and where the chain stops.
OPEN_BOUNDARY_EXTRA_FIXTURES = {
    "block comment": (
        ("a call under a condition holding a block comment",
         BASE.replace(SEND_GUARD,
                      "+    if (m /* c */) {\n+        auto name = encoder->messageName();\n", 1)),
    ),
    "two-closer boundary": (
        ("a call between two unmatched closers",
         BASE.replace(RECEIVE_GUARD,
                      "+    if (m_shouldTranslateMessageNames) {\n"
                      "+        x(); } translateIncomingMessageName(decoder->messageName()); }\n", 1)),
    ),
}


def open_boundary_fixtures(phrase: str) -> list[tuple[str, str]]:
    """The (description, patch text) pairs the claim `phrase` has to agree with."""
    if phrase in OPEN_BOUNDARY_EXTRA_FIXTURES:
        return list(OPEN_BOUNDARY_EXTRA_FIXTURES[phrase])
    indices = {"both spellings": (0, 1), "one-line form": (0,), "split form": (1,)}[phrase]
    return [(GUARD_CLOSED_THEN_CALL_IN_ONE_STATEMENT[index][0],
             BASE.replace(RECEIVE_GUARD,
                          "".join("+    " + line + "\n"
                                  for line in GUARD_CLOSED_THEN_CALL_IN_ONE_STATEMENT[index][1]), 1))
            for index in indices]


def check_open_boundary_docstring_matches_behaviour() -> int:
    """Compare the direction the Open boundary paragraph claims with the direction the checker takes.

    Each claim is pinned to the sentence the paragraph writes and to a boolean — does the paragraph say
    this spelling is refused? — and each spelling is measured by running the checker on a fixture of
    that spelling, giving another boolean. The row and the measurement are asserted equal, and the
    sentence's own direction word is asserted equal to the row, so both copies of the direction and the
    build have to agree. Editing any one of the three alone fails here.

    This is deliberately not a search for the words themselves. An earlier version only required that
    `refused` appear, which stayed green when every `refused` in the paragraph was replaced by
    `accepted`: it bound the vocabulary, not the direction, and a green light on a paragraph saying
    the opposite is worse than no test at all.

    The scope is the closed vocabulary in `DIRECTION_WORD`. A direction word left over after the pinned
    sentences are removed fails here, so narration cannot state a state the tables do not record; a
    synonym outside that list is not read as a direction at all, which the module docstring states and
    `check_open_boundary_extra_claims_are_caught` pins as a case. That gap is recorded, not closed.

    The comparison itself lives in `open_boundary_direction_problems`, because the vacuity check below
    breaks the paragraph on purpose and needs the same comparison rather than a second copy of it.
    """
    module_doc = (SCRIPTS / "test-check-translation-switch.py").read_text(encoding="utf-8")
    problems = open_boundary_direction_problems(module_doc)
    for problem in problems:
        print("FAIL: %s" % problem)
    if not problems:
        print("ok: every direction the Open boundary paragraph claims matches the measured build")
    return len(problems)


def check_open_boundary_check_is_not_vacuous() -> int:
    """Break the Open boundary text every way a rewrite could, and assert the comparison notices.

    A comparison test is only worth what it catches. Every pinned claim is flipped to the other
    direction word one at a time, with the implementation left alone, so the claim the tables require
    is gone and the paragraph keeps a direction word nothing accounts for. The last case rewrites the
    whole paragraph to call the split form accepted, which is the defect the paragraph was written to
    record, reintroduced into the paragraph itself.

    Each mutation must be reported by both signals, and they are checked separately: the pinned claim
    that disappeared, and the direction word left over. A comparison that noticed only one of them
    would be blind to the other, and a single "problems is non-empty" test would accept either.

    The fixture comparison's own survival is a different property and has its own guard:
    `check_open_boundary_comparison_is_live` requires a real pass to run every fixture and requires a
    contradicted row to be reported through `DIRECTION_DISAGREEMENT`. It is separate because these
    mutations rewrite the paragraph and leave the table alone, so none of them exercises the comparison.

    The flips are enumerated from `CLAIM` rather than from the text, so adding a claim to the table
    adds its flip in the same edit instead of leaving it unguarded.
    """
    module_doc = (SCRIPTS / "test-check-translation-switch.py").read_text(encoding="utf-8")
    section = open_boundary_section(module_doc)
    if section is None:
        print("FAIL: the Open boundary text is gone from this file's docstring")
        return 1

    rewritten = (
        "Open boundary: a call that follows a closed guard block inside the same statement is refused\n"
        "in the one-line spelling. The one-line form is refused: its chain is empty and no closure\n"
        "sits before the call. The split form is accepted: the statement is cut at the closure. A\n"
        "block comment inside the condition — `if (m /* c */)` — is refused because the condition is\n"
        "read as written and the comment is not stripped there."
    )

    def rewrite(text: str) -> str | None:
        current = open_boundary_section(text)
        return text.replace(current, rewritten, 1) if current else None

    def flip_pinned_claim(phrase: str, sentence: str):
        """Flip the direction word inside the sentence `CLAIM` pins for `phrase`.

        The sentence is the table's, not one found by reading the paragraph, so the flip always lands
        on the text the comparison pins. That is what makes each flip a test of the comparison: the
        pinned sentence stops matching, and the paragraph keeps a direction word nothing accounts for.

        Both signals are required. Either alone would let a broken comparison pass this check — a
        comparison that reports only "the text changed" would satisfy the first, and one that reports
        only "a direction word is unaccounted for" would satisfy the second while no longer checking
        whether the claims it pins are present at all.
        """
        def apply(text: str) -> str | None:
            current = open_boundary_section(text)
            if current is None:
                return None
            for word, other in (("refused", "accepted"), ("accepted", "refused")):
                match = re.search(r"\b%s\b" % word, sentence)
                if match:
                    flipped = sentence[:match.start()] + other + sentence[match.end():]
                    return text.replace(current, current.replace(sentence, flipped, 1), 1)
            return None
        return apply, ("the pinned claim for %r is gone" % phrase,
                       "does not account for")

    mutations = [
        ("the claim for %r flipped" % phrase, flip_pinned_claim(phrase, sentence))
        for phrase, sentence, _ in CLAIM
    ]
    if not mutations:
        print("FAIL: no claim is pinned, so nothing here tests the comparison")
        return 1
    mutations.append(
        ("the paragraph rewritten to call the split form accepted",
         (rewrite, ("the pinned claim for 'both spellings' is gone", "does not account for"))))

    failures = 0
    for description, (mutate, required) in mutations:
        mutated = mutate(module_doc)
        if mutated is None:
            failures += 1
            print("FAIL: %s: the text to mutate is gone, so this check no longer tests anything"
                  % description)
            continue
        problems = open_boundary_direction_problems(mutated)
        missing = [fragment for fragment in required
                   if not any(fragment in problem for problem in problems)]
        if missing:
            failures += 1
            print("FAIL: %s was not caught as %r; the paragraph check reported %r"
                  % (description, missing, problems or None))
        else:
            print("ok: %s is caught (%d failure(s): %s)"
                  % (description, len(problems), problems[0]))
    return failures


def check_open_boundary_comparison_is_live() -> int:
    """Assert both direction comparisons run, and each is what reports its own kind of disagreement.

    A comparison test is only worth what it catches, and until now nothing caught either comparison:
    the whole `for description, patch_text in open_boundary_fixtures(...)` loop could be replaced by
    `pass` with the suite green, and the sentence-to-row check could be deleted outright with the suite
    green, because in both cases the other comparison still spoke up and "some problem" was all any
    assertion looked for.

    Four properties, each of which a different mutation breaks.

    First: a real pass runs one fixture per claim. The expectation is derived from
    `open_boundary_fixtures`, not written as a number, and the actual count comes from the pass itself,
    so a loop that stopped running leaves a short measurement and fails here. A deleted loop leaves it
    at zero.

    Second: a row declaring the opposite of what its fixture does has to be reported through
    `DIRECTION_DISAGREEMENT`, the message only that comparison emits. Requiring the message rather than
    "more than zero problems" is deliberate — the sentence-to-row comparison also speaks up when a row
    is flipped, so a non-empty test would pass with the fixture comparison gone.

    Third: the same requirement in the other direction. A flipped row also contradicts its pinned
    sentence, and the only thing that reports that is `SENTENCE_ROW_DISAGREEMENT`. Deleting the
    sentence-to-row comparison leaves the fixture comparison still reporting, so only a check that names
    this message can see the loss.

    Fourth: the table still holds `OPEN_BOUNDARY_CLAIM_COUNT` rows. That is the same defect one level
    up: a row and the sentence it pins can be deleted together, and nothing else notices — the fixture
    count here derives from the same table and drops with it, and the paragraph keeps no leftover
    direction word.

    The two message constants are what keep the two comparisons from standing in for each other. A
    single "any problem" requirement would let either one be deleted and still pass.
    """
    module_doc = (SCRIPTS / "test-check-translation-switch.py").read_text(encoding="utf-8")
    failures = 0

    if len(CLAIM) != OPEN_BOUNDARY_CLAIM_COUNT:
        failures += 1
        print("FAIL: the table holds %d claim(s) but this file declares %d; a row deleted together "
              "with the sentence it pins leaves every other check consistent"
              % (len(CLAIM), OPEN_BOUNDARY_CLAIM_COUNT))
    else:
        print("ok: the table holds the %d declared claim(s)" % OPEN_BOUNDARY_CLAIM_COUNT)

    expected = open_boundary_comparison_count()
    measurements: list[tuple[str, str, bool, int]] = []
    open_boundary_direction_problems(module_doc, measurements=measurements)
    if expected < 1:
        failures += 1
        print("FAIL: the pinned claims name no fixture, so nothing here tests the comparison")
    elif len(measurements) != expected:
        failures += 1
        print("FAIL: a pass ran %d fixture(s) but the pinned claims name %d; a comparison that "
              "stopped running the fixtures cannot report a disagreement"
              % (len(measurements), expected))
    else:
        print("ok: a pass runs every pinned fixture (%d of %d)" % (len(measurements), expected))

    for phrase, sentence, declared_refused in CLAIM:
        contradicted = tuple((name, text, not declared) if name == phrase else (name, text, declared)
                             for name, text, declared in CLAIM)
        problems = open_boundary_direction_problems(module_doc, claims=contradicted)
        if not any(DIRECTION_DISAGREEMENT in problem for problem in problems):
            failures += 1
            print("FAIL: the row for %r declaring the opposite direction was not reported as a "
                  "direction disagreement; the paragraph check reported %r" % (phrase, problems or None))
        else:
            print("ok: the row for %r declaring the opposite direction is reported as a disagreement"
                  % phrase)

    # The same two-sided requirement for the sentence-to-row comparison. Flipping a row's boolean leaves
    # the sentence saying the original direction, so the two copies disagree — and the only thing that
    # reports it is the `stated != [declared_word]` comparison. Without this case, deleting that
    # comparison is invisible: the fixture comparison above still reports the flipped row, and the
    # sentence stays present, so every other assertion still holds.
    for phrase, sentence, declared_refused in CLAIM:
        declared_word = "refused" if declared_refused else "accepted"
        stated = DIRECTION_WORD.findall(sentence)
        if stated != [declared_word]:
            continue
        contradicted = tuple((name, text, not declared) if name == phrase else (name, text, declared)
                             for name, text, declared in CLAIM)
        problems = open_boundary_direction_problems(module_doc, claims=contradicted)
        if not any(SENTENCE_ROW_DISAGREEMENT in problem for problem in problems):
            failures += 1
            print("FAIL: the row for %r contradicting its pinned sentence was not reported as a "
                  "sentence/row disagreement; the paragraph check reported %r"
                  % (phrase, problems or None))
        else:
            print("ok: the row for %r contradicting its pinned sentence is reported as a "
                  "sentence/row disagreement" % phrase)
    return failures


def check_open_boundary_extra_claims_are_caught() -> int:
    """Assert a paragraph stating more than the pinned tables is a failure.

    Each case edits the Open boundary text and runs this whole test file against the edit. The
    implementation is the one under test, not a copy: the edit goes into a temporary tree holding this
    file and the checker, and the exit code is what gets asserted.

    The cases are grouped by what the edit does to the accounting. A rewrite of a pinned claim removes
    text the tables require, so it fails twice — the claim is gone and its direction word is left over.
    An added sentence carrying a direction word is not accounted for at all, which is the shape that
    four earlier versions could not see: they parsed sentences and ignored the ones their patterns did
    not match, so a sentence stating the opposite was invisible rather than wrong. An added sentence
    with no direction word states nothing and has to stay green, which is what separates "fail on the
    unaccounted" from "fail on any change".

    One earlier case is deliberately inverted. It asserted that a past-tense decoy followed by an
    unchanged claim still passes, because the old reader skipped sentences it could not parse and the
    decoy was one of them. Under this representation the decoy is an unaccounted direction word, and a
    paragraph that says the call was refused before a fix is exactly the paragraph a reader could
    mistake for the current state. The case now asserts failure, and the reason is recorded in the
    module docstring rather than dropped.

    The last group asserts the opposite: the blind spots the module docstring states are blind on
    purpose, so a synonym for `accepted` appended to the paragraph has to stay green. Those cases fix
    the boundary of what this file claims. They are the reason it says the vocabulary is closed instead
    of saying an unseen sentence cannot pass.
    """
    module_doc = (SCRIPTS / "test-check-translation-switch.py").read_text(encoding="utf-8")

    anchor = "closer in the call's own statement precedes the call."

    rewrites = (
        ("the split form denied by `not`",
         "The split form is refused:", "The split form is not refused:"),
        ("the split form denied by `never`",
         "The split form is refused:", "The split form is never refused:"),
        ("the split form denied by `no longer`",
         "The split form is refused:", "The split form is no longer refused:"),
        ("the one-line form denied by `not`",
         "The one-line form is refused:", "The one-line form is not refused:"),
        ("the block comment denied by `not`",
         "is refused because the condition is read as",
         "is not refused because the condition is read as"),
        ("the two-closer boundary denied by `not`",
         "The two-closer boundary is accepted:", "The two-closer boundary is not accepted:"),
        ("both spellings denied by `not`",
         "is refused in both spellings", "is not refused in both spellings"),
        ("the split form rewritten to say accepted",
         "The split form is refused:", "The split form is accepted:"),
    )
    additions = (
        ("a contradicted claim with a contraction",
         "The split form isn't refused."),
        ("a contradicted claim with an adverb",
         "The split form is now accepted."),
        ("a direction word in a fragment",
         "In practice, accepted."),
        ("a past-tense sentence stating the opposite",
         "Before that fix the split form was refused."),
        ("a past-tense sentence the tables do not pin",
         "Earlier the two-closer boundary was accepted."),
        ("a contradiction of a pinned claim",
         "The two-closer boundary is refused."),
    )
    silent = (
        ("an added sentence with no direction word",
         "The check runs on every build."),
        ("an added sentence about a spelling with no direction word",
         "The two-closer boundary records a tree measurement."),
    )
    # The closed vocabulary, made executable instead of promised. Each of these says the split form is
    # let through in a word `DIRECTION_WORD` does not hold, so it is a sentence this check does not see.
    # The case asserts the documented behaviour — exit 0 — because that is the limit the module
    # docstring states. Widening the vocabulary would turn these green cases red and should: it would
    # mean the list changed, not that this case is wrong.
    documented_blind_spots = (
        ("a synonym for accepted: `permitted`", "The split form is permitted."),
        ("a synonym for accepted: `allowed`", "The split form is allowed."),
        ("a synonym for accepted: `passes`", "The split form passes."),
        ("a synonym for accepted: `goes through`", "The split form goes through."),
        ("a synonym for accepted: `valid`", "The split form is valid."),
    )
    # The other documented limit: prose carrying no direction word is free. These rewrite a sentence of
    # the pinned text that is not one of `CLAIM`'s, so they exercise the claim that only the pinned
    # sentences are fixed rather than reading the paragraph as frozen.
    free_prose = (
        ("a measurement in the second paragraph reworded",
         "Measured on a 898065-line", "Measured on a 900000-line"),
        ("the historical sentence reworded", "was let through", "slipped past"),
    )

    failures = 0

    def run_edit(description: str, edited: str, expected_non_zero: bool) -> None:
        nonlocal failures
        result = run_full_self_test(edited)
        if expected_non_zero and result.returncode == 0:
            failures += 1
            print("FAIL: %s exits 0; the paragraph now says something the pinned tables do not "
                  "account for and the binding stayed green" % description)
        elif not expected_non_zero and result.returncode != 0:
            failures += 1
            print("FAIL: %s exits %d; a sentence that states no direction was read as a violation"
                  % (description, result.returncode))
            print("  stdout tail: %s" % "\n  ".join(result.stdout.strip().splitlines()[-3:]))
        else:
            print("ok: %s exits %d" % (description, result.returncode))

    for description, old, new in rewrites:
        if old not in module_doc:
            failures += 1
            print("FAIL: %s: the sentence to rewrite is gone, so this case no longer tests anything"
                  % description)
            continue
        run_edit(description, module_doc.replace(old, new, 1), True)

    for description, sentence in additions:
        if anchor not in module_doc:
            failures += 1
            print("FAIL: %s: the anchor sentence is gone" % description)
            continue
        run_edit(description, module_doc.replace(anchor, anchor + " " + sentence, 1), True)

    for description, sentence in silent:
        if anchor not in module_doc:
            failures += 1
            print("FAIL: %s: the anchor sentence is gone" % description)
            continue
        run_edit(description, module_doc.replace(anchor, anchor + " " + sentence, 1), False)

    for description, sentence in documented_blind_spots:
        if anchor not in module_doc:
            failures += 1
            print("FAIL: %s: the anchor sentence is gone" % description)
            continue
        run_edit(description, module_doc.replace(anchor, anchor + " " + sentence, 1), False)

    for description, old, new in free_prose:
        if old not in module_doc:
            failures += 1
            print("FAIL: %s: the sentence to rewrite is gone, so this case no longer tests anything"
                  % description)
            continue
        run_edit(description, module_doc.replace(old, new, 1), False)

    return failures


def check_open_boundary_tables_account_for_every_direction_word() -> int:
    """Assert the pinned claims cover the paragraph's direction words exactly — no gaps, no padding.

    Two properties, both needed and neither implied by the other. Sufficiency: removing every pinned
    sentence leaves no direction word, so the table accounts for the whole paragraph as it stands.
    Necessity: each pinned sentence is the only thing accounting for its direction word, so deleting
    that entry exposes an unaccounted word rather than leaving the accounting intact.

    Without the second property an entry could be dead weight — pinning text that also happens to be
    covered elsewhere — and the paragraph could then drift from the entry without any test noticing.
    The necessity pass removes the other entries but keeps the declared direction word in the body, so
    it asks exactly "is this sentence the only copy", independently of which direction the sentence
    states. Entries with no direction word are pinned for a different reason: they fix wording, so they
    are excluded from the necessity pass rather than failing it.
    """
    module_doc = (SCRIPTS / "test-check-translation-switch.py").read_text(encoding="utf-8")
    section = open_boundary_section(module_doc)
    if section is None:
        print("FAIL: the Open boundary text is gone from this file's docstring")
        return 1

    def without(entries: list[str], keep: str = "") -> str:
        body = normalise(section)
        for entry in entries:
            body = body.replace(normalise(entry), " ")
        return body + " " + keep

    claims = [sentence for _, sentence, _ in CLAIM]
    failures = 0

    leftover = DIRECTION_WORD.findall(without(claims))
    if leftover:
        failures += 1
        print("FAIL: the pinned claims leave %d direction word(s) unaccounted: %s"
              % (len(leftover), ", ".join(leftover)))
    else:
        print("ok: the pinned claims account for every direction word in the Open boundary text")

    for phrase, entry, declared_refused in CLAIM:
        if not DIRECTION_WORD.search(entry):
            continue
        declared_word = "refused" if declared_refused else "accepted"
        others = [other for other in claims if other != entry]
        if not DIRECTION_WORD.search(without(others, declared_word)):
            failures += 1
            print("FAIL: the claim entry for %r is not load-bearing; the paragraph still accounts "
                  "for every direction word without it" % phrase)
        else:
            print("ok: the claim entry for %r is load-bearing" % phrase)
    return failures


def run_full_self_test(module_doc: str) -> subprocess.CompletedProcess:
    """Run this test file, with `module_doc` in its place, and return the completed process.

    A temporary tree holds the mutated copy and the real checker, so the run exercises the whole file
    — the direction comparison and the vacuity check that guards it — rather than one function.

    The child is told to skip `check_open_boundary_extra_claims_are_caught` by `REWRITE_FLAG` on its
    command line. Without that, every child would run these same rewrites against its own rewritten
    text, and the recursion would not terminate. A command-line argument rather than an environment
    variable is what keeps the switch deliberate: an environment variable can be set by accident, and
    when it is, the skipped assertions are simply gone from the run with nothing to show for it. An
    argument is only ever passed by this function.

    Skipping costs the child nothing the assertion depends on. Every other assertion in `main` still
    runs there, including the comparison and the vacuity check.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "scripts").mkdir()
        for name in (SCRIPT.name, "test-check-translation-switch.py"):
            source = SCRIPTS / name
            (root / "scripts" / name).write_text(
                module_doc if name == "test-check-translation-switch.py"
                else source.read_text(encoding="utf-8"),
                encoding="utf-8")
        (root / "patches").symlink_to(REAL_PATCH.parent)
        return subprocess.run(
            [sys.executable, str(root / "scripts" / "test-check-translation-switch.py"), REWRITE_FLAG],
            capture_output=True, text=True)


def check_needles_are_unique_and_necessary() -> int:
    """Assert each rationale needle occurs exactly once in the real patch's prose, and is necessary.

    Uniqueness is what makes a needle a guard. A bare number or tag name occurs several times in this
    patch, so deleting the sentence the needle was taken from leaves another occurrence matching and
    the check passes: that was the defect in `"3076"`, which occurs six times. Necessity is the same
    property from the other side and is tested by deleting the needle from the prose and running the
    checker, which has to exit 1. Both halves run against the committed patch's own text, so a rewrite
    that reintroduces a shared marker fails here rather than at review time.
    """
    if not REAL_PATCH.exists():
        print("FAIL: the real patch is missing: %s" % REAL_PATCH)
        return 1
    real_text = REAL_PATCH.read_text(encoding="utf-8")
    prose = CHECK.added_comment_prose(real_text)
    failures = 0
    for description, needle in CHECK.REQUIRED_RATIONALE:
        count = prose.count(needle)
        if count != 1:
            failures += 1
            print("FAIL: %r occurs %d times in the prose, not once" % (needle, count))
            print("  needle for %s; a second occurrence means deleting its sentence still passes"
                  % description)
    if failures:
        return failures
    print("ok: every rationale needle occurs exactly once (%d needles)"
          % len(CHECK.REQUIRED_RATIONALE))

    # The fixture is the real patch's prose on one line plus the code the other rules need, so an
    # edit here cannot be confounded by where a comment happens to wrap.
    scaffold = "\n".join([
        "diff --git a/Source/WebKit/Platform/IPC/Connection.h b/Source/WebKit/Platform/IPC/Connection.h",
        "+bool m_shouldTranslateMessageNames { false };",
        "+void setShouldTranslateMessageNames(bool shouldTranslate) { m_shouldTranslateMessageNames = shouldTranslate; }",
        "diff --git a/Source/WebKit/Platform/IPC/Encoder.cpp b/Source/WebKit/Platform/IPC/Encoder.cpp",
        "+// " + prose,
        "+    case MessageName::InitializeConnection: return static_cast<MessageName>(2478);",
        "+    case MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged: return static_cast<MessageName>(3076);",
        "+    case MessageName::WebSharedWorkerServerConnection_SuspendForBackForwardCache: return static_cast<MessageName>(2444);",
        "+    case MessageName::WrappedAsyncMessageForTesting: return static_cast<MessageName>(3184);",
        "+    case 2478: return MessageName::InitializeConnection;",
        "+    case 3076: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;",
        "+    case 2444: return MessageName::WebSharedWorkerServerConnection_SuspendForBackForwardCache;",
        "+    case 3184: return MessageName::WrappedAsyncMessageForTesting;",
        "+    case 2946: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;",
        "diff --git a/Source/WebKit/Platform/IPC/Connection.cpp b/Source/WebKit/Platform/IPC/Connection.cpp",
        "+    if (m_shouldTranslateMessageNames) {",
        "+        auto name = encoder->messageName();",
        "+        encoder->setMessageName(translateOutgoingMessageName(name));",
        "+    }",
        "diff --git a/Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm b/Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm",
        "+    if (m_shouldTranslateMessageNames) {",
        "+        auto translated = translateIncomingMessageName(decoder->messageName());",
        "+        decoder->setMessageName(translated);",
        "+    }",
        "",
    ])
    if run(scaffold).returncode != 0:
        print("FAIL: the scaffold built from the real prose does not pass on its own")
        result = run(scaffold)
        print("  stdout: %s" % result.stdout.strip())
        print("  stderr: %s" % result.stderr.strip())
        return 1

    for description, needle in CHECK.REQUIRED_RATIONALE:
        result = run(scaffold.replace(needle, "", 1))
        if result.returncode != 1:
            failures += 1
            print("FAIL: deleting %r for %s left the checker at exit %d, not 1"
                  % (needle, description, result.returncode))
            print("  stdout: %s" % result.stdout.strip())
            print("  stderr: %s" % result.stderr.strip())
    if failures:
        return failures
    print("ok: deleting any one needle fails the check (all %d tried)"
          % len(CHECK.REQUIRED_RATIONALE))
    return 0


def main() -> int:
    failures = 0

    # The real patch, in the state it is committed in. This is a cross-check rather than a fixed
    # verdict: an independent, naive read of the patch decides what the answer must be, and the
    # checker has to agree. A fixed expectation would be wrong in one of two legitimate states -
    # before M85's rework deletes the setter call, and after - and a tautology would test nothing.
    if not REAL_PATCH.exists():
        print("FAIL: the real patch is missing: %s" % REAL_PATCH)
        return 1
    real_text = REAL_PATCH.read_text(encoding="utf-8")
    has_call = any(
        CHECK.SETTER.search(CHECK.strip_comment(CHECK.ADDED_LINE.match(raw).group(1)))
        and not any(hint in raw for hint in CHECK.DECLARATION_HINTS)
        for raw in real_text.splitlines()
        if CHECK.ADDED_LINE.match(raw)
    )
    expected_code = 1 if has_call else 0
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    if result.returncode != expected_code:
        failures += 1
        print("FAIL: the checker disagrees with a plain read of the committed patch")
        print("  the patch %s a setter call, so exit %d was expected, got %d"
              % ("contains" if has_call else "does not contain", expected_code, result.returncode))
        print(result.stdout, result.stderr)
    else:
        print("ok: the checker agrees with a plain read of the committed patch (exit %d)" % expected_code)
        if has_call:
            print("  note: the committed patch still carries M85's setter call; the check turns to a")
            print("  pass once M85's rework removes it and this branch rebases onto that tip")

    for description, patch_text, expected_code, expected_message in CASES:
        if patch_text is None and expected_code == 0:
            continue
        result = run(patch_text)
        problems = []
        if result.returncode != expected_code:
            problems.append("expected exit %d, got %d" % (expected_code, result.returncode))
        if expected_message is not None and expected_message not in (result.stderr + result.stdout):
            problems.append("expected %r in the output" % expected_message)
        if problems:
            failures += 1
            print("FAIL: %s" % description)
            for problem in problems:
                print("  %s" % problem)
            print("  stdout: %s" % result.stdout.strip())
            print("  stderr: %s" % result.stderr.strip())
        else:
            print("ok: %s" % description)

    # The real patch must exist, or every check above was reading a fixture.
    if not REAL_PATCH.exists():
        print("FAIL: the real patch is missing: %s" % REAL_PATCH)
        failures += 1
        return 1

    failures += check_needles_are_unique_and_necessary()

    # The lexical view itself, so a change in the sentinel or in where a region ends is reported as
    # such rather than through a judgement it happens to shift.
    for statement, expected_view in CODE_VIEW_CASES:
        actual = CHECK.code_view(statement)
        if actual != expected_view:
            failures += 1
            print("FAIL: code_view(%r) is %r, expected %r" % (statement, actual, expected_view))
        else:
            print("ok: code_view(%r) is %r" % (statement, actual))

    for description, statement, expected_count in COUNTER_CASES:
        actual = len(CHECK.unmatched_closer_offsets(CHECK.code_view(statement)))
        if actual != expected_count:
            failures += 1
            print("FAIL: %s: closers(%r) is %d, expected %d"
                  % (description, statement, actual, expected_count))
        else:
            print("ok: %s: closers(%r) is %d" % (description, statement, actual))

    failures += check_open_boundary_docstring_matches_behaviour()
    failures += check_open_boundary_check_is_not_vacuous()
    failures += check_open_boundary_comparison_is_live()
    failures += check_open_boundary_tables_account_for_every_direction_word()
    if REWRITE_FLAG not in sys.argv:
        failures += check_open_boundary_extra_claims_are_caught()
    else:
        print("note: the rewrite cases were skipped, as asked on the command line by the parent run")

    if failures:
        print("%d case(s) failed" % failures)
        return 1
    print("all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
