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
  while a correctly guarded one-line call must pass.

The "as committed" case reads the real patch, so a patch edited without updating this check fails
here rather than in the build. Two further passes read the real patch too: every rationale needle
must occur exactly once in its prose, and deleting any one needle must fail the checker. Together
those make "the needle is unique and necessary" an executable invariant instead of a reading.

Usage: python3 scripts/test-check-translation-switch.py
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-translation-switch.py"
REAL_PATCH = SCRIPTS.parent / "patches" / "ios16-ipc-message-number-translation.patch"

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
+// dense enumeration over every message in the tree, so 946 more entries inserted
+// anywhere before a name reassigns the numbers after them.
+// This tree has 4086 numbers. Read against the shipped 16.1 enumeration
+// (releases/Apple/Safari-16.1-iOS-16.1, 3185 numbers) only 5 land on a message
+// with the same name, 3180 land on a different message, and 901 fall off its end.
+// built on tag Safari-614.1.9, which holds 3140 numbers; see the paragraph on
+// that tag below. Read against 614.1.9 the same measurement gives 5, 3135 and
+// 946. Both readings are here because both matter.
+// basis, so both are named. `InitializeConnection` is number 3076 in this tree.
+// The incoming table switches over the older numbering and answers case 3076
+// with MessageName::WebPageProxy_RootViewToAccessibilityScreen, the message that
+// occupies 3076 in 614.1.9. Against the shipped 16.1 enumeration the same number
+// instead names MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged,
+// which is what a peer running that build would be misread as.
+// ConnectionCocoa.mm's `decoder->messageName() == MessageName::InitializeConnection`
+// then never fires and the connection never completes its handshake.
+// whose anchors are firstSynchronousOldMessageNumber 2899, InitializeConnection
+// 2444 and Last 3139. That is not the 16.1 the device runs. The device runs
+// releases/Apple/Safari-16.1-iOS-16.1 (614.2.9.0.10), which has 3185 numbers and
+// anchors 2946, 2478 and 3184. Every one of those three numbers differs: 45 at
+// Last, 34 at InitializeConnection and 47 at the first synchronous message.
+// So these tables are a 16.0-generation snapshot, not 16.1.
+    case MessageName::InitializeConnection: return static_cast<MessageName>(2444);
+    case MessageName::WebPageProxy_RootViewToAccessibilityScreen: return static_cast<MessageName>(3076);
+    case MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged: return static_cast<MessageName>(3024);
+    case MessageName::WrappedAsyncMessageForTesting: return static_cast<MessageName>(3139);
+    case 3076: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;
+    case 3024: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;
+    case 2899: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;
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

# Round 7's P2-4: the setter's body was matched as one literal line, so a body written on its own lines
# was read as an assignment outside the setter. The definition line and its `{` are on separate lines.
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

# The five needles that used to be bare numbers, and the wrong value each must now fail on. Every
# number here is also a real case label in the tables, which is why a needle that searched the whole
# patch matched whatever the prose said.
DRIFTED_COUNTS = (
    ("the beyond-range count against shipped 16.1", "and 901 fall off", "and 903 fall off"),
    ("the net growth over the 614.1.9 snapshot", "946 more entries", "948 more entries"),
    ("the different-message and beyond-range counts against the 614.1.9 basis",
     "gives 5, 3135 and", "gives 5, 3134 and"),
)

ROOT_VIEW = "MessageName::WebPageProxy_RootViewToAccessibilityScreen"
VIDEO_FRAME = "MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged"
SWAPPED_NAMES = (
    BASE.replace("with " + ROOT_VIEW, "with " + VIDEO_FRAME)
    .replace("names " + VIDEO_FRAME, "names " + ROOT_VIEW)
)

# The bare markers that stayed behind when the first five needles were fixed. Each must now fail on a
# sentence deletion or a value change, which is what "the needle is unique and necessary" means.
SENTENCE_NEEDLES = (
    (
        "a patch that deletes the InitializeConnection-is-3076 sentence fails",
        "// basis, so both are named. `InitializeConnection` is number 3076 in this tree.\n",
        "// basis, so both are named. The connection handshake is the message discussed here.\n",
        "InitializeConnection is 3076 in this tree",
    ),
    (
        "a patch that drifts 3180 to 3181 fails",
        "+// with the same name, 3180 land on a different message",
        "+// with the same name, 3181 land on a different message",
        "the different-message count against shipped 16.1",
    ),
    (
        "a patch that retags Safari-614.1.9 fails",
        "+// built on tag Safari-614.1.9, which holds 3140 numbers",
        "+// built on tag Safari-614.2.0, which holds 3140 numbers",
        "the tag the tables were generated from",
    ),
)

# P2-2's two cases: the table can be retargeted or renumbered while the prose still reads correctly,
# which is exactly the silent rename this check exists to prevent.
TABLE_CASES = (
    (
        "a patch that retargets incoming case 3076 at another real message fails",
        "+    case 3076: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;\n",
        "+    case 3076: return MessageName::WebPageProxy_RootViewToScreen;\n",
        "3076 declares WebPageProxy_RootViewToAccessibilityScreen",
    ),
    (
        "a patch that renumbers incoming case 3076 to 3075 fails",
        "+    case 3076: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;\n",
        "+    case 3075: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;\n",
        "no entry for 3076 declares WebPageProxy_RootViewToAccessibilityScreen",
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
        "a patch that drops the wrong-tag fact fails",
        BASE.replace("Safari-614.1.9", "the current 16.1 tag"),
        1,
        "no longer records",
    ),
    (
        "a patch that drops the measured counts fails",
        BASE.replace("3180", "many"),
        1,
        "no longer records",
    ),
    (
        "a patch that calls the tables 16.1 without qualifying it fails",
        BASE + "+// This table is the 16.1 numbering.\n",
        1,
        "without saying which 16.1",
    ),
    (
        "the same claim qualified by the tag passes",
        BASE + "+// This table is the 614.1.9 numbering.\n",
        0,
        None,
    ),
    ("a missing patch is reported distinctly", None, 2, None),
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
        "+    case MessageName::InitializeConnection: return static_cast<MessageName>(2444);",
        "+    case MessageName::WebPageProxy_RootViewToAccessibilityScreen: return static_cast<MessageName>(3076);",
        "+    case MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged: return static_cast<MessageName>(3024);",
        "+    case MessageName::WrappedAsyncMessageForTesting: return static_cast<MessageName>(3139);",
        "+    case 3076: return MessageName::WebPageProxy_RootViewToAccessibilityScreen;",
        "+    case 3024: return MessageName::RemoteMediaPlayerProxy_VideoFrameForCurrentTimeIfChanged;",
        "+    case 2899: return MessageName::GPUConnectionToWebProcess_EnsureAudioSession;",
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

    if failures:
        print("%d case(s) failed" % failures)
        return 1
    print("all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
