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
  a missing patch must be reported distinctly, so a read error is not read as a pass.

The "as committed" case reads the real patch, so a patch edited without updating this check fails
here rather than in the build.

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
# needle that only matches the real wording cannot pass here by coincidence.
BASE = """\
+bool m_shouldTranslateMessageNames { false };
+void setShouldTranslateMessageNames(bool shouldTranslate) { m_shouldTranslateMessageNames = shouldTranslate; }
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
"""

CALL = "+    m_connection->setShouldTranslateMessageNames(true);\n"
CALL_NO_ARGUMENT = "+    m_connection->setShouldTranslateMessageNames();\n"
DECLARATION = "+    void setShouldTranslateMessageNames(bool shouldTranslate);\n"
MACRO_INITIALISER = "+    bool m_shouldTranslateMessageNames { IOS_16_MESSAGE_NUMBER_TRANSLATION };\n"

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

    if failures:
        print("%d case(s) failed" % failures)
        return 1
    print("all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
