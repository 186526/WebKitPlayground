#!/usr/bin/env python3
"""Regression tests for scripts/check-network-session-proxy-configurations.py.

The fixtures are carved from the upstream function's own shape, so every case runs without Xcode, a
WebKit tree or network access. The four the mission names come first:

  the unpatched file must fail, because that is the build whose Network process aborted;
  the patched file in the respondsToSelector: spelling must pass;
  the patched file in the if (@available(iOS 17.0, *)) spelling must pass;
  a guard that wraps only the non-empty branch must fail, because the empty branch's write alone
  sends setProxyConfigurations: on iOS 16.

The respondsToSelector: fixture is built by splicing the patch's own hunk into the unpatched fixture,
so a patch that drifts from the file it edits fails here instead of splicing itself in at a guessed
anchor. The @available fixture wraps the upstream body in that guard, so both accepted spellings are
tested against the upstream function rather than against a rewritten one.

The remaining cases pin the ways the crash can come back and the ways the check could pass by
accident: an early return with the wrong polarity, a guard placed after an earlier statement, a
commented-out guard, a ref that has no such function (every tag before Safari-17.4), and both answers
of the --needs-guard mode the workflow's precheck loop reads.

Usage: python3 scripts/test-check-network-session-proxy-configurations.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-network-session-proxy-configurations.py"
PATCH = SCRIPTS.parent / "patches" / "ios16-network-session-proxy-configurations-guard.patch"

# The upstream function at tag Safari-18.3.1-iOS-18.3.2, with the #if that contains it so the
# fixture has the file's compile-time shape. Both branches keep upstream's text and upstream's
# brace-less else, which is what makes "which branch does this write sit in" a real question.
UNPATCHED = """\
// Excerpt of Source/WebKit/NetworkProcess/cocoa/NetworkSessionCocoa.mm at
// releases/Apple/Safari-18.3.1-iOS-18.3.2: the enclosing #if and the function the patch edits.

void NetworkSessionCocoa::setProxyConfigData(const Vector<std::pair<Vector<uint8_t>, WTF::UUID>>& proxyConfigurations)
{
    auto* clearProxies = nw_context_clear_proxiesPtr();
    if (!clearProxies)
        return;
}

#if HAVE(NW_PROXY_CONFIG)
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    if (!m_nwProxyConfigs.isEmpty()) {
        RetainPtr nwProxyConfigurations = adoptNS([[NSMutableArray alloc] initWithCapacity:m_nwProxyConfigs.size()]);
        for (auto& proxyConfig : m_nwProxyConfigs)
            [nwProxyConfigurations addObject:proxyConfig.get()];

        configuration.proxyConfigurations = nwProxyConfigurations.get();
    } else
        configuration.proxyConfigurations = @[ ];
}
#endif // HAVE(NW_PROXY_CONFIG)
"""

# The non-empty branch's opening line, which the narrower-guard variant rewrites.
NON_EMPTY_OPEN = "    if (!m_nwProxyConfigs.isEmpty()) {\n"

# The body the patch produces, spelled once so the wrong-polarity variant can derive from it.
SELECTOR_GUARD = """\
    if (![configuration respondsToSelector:NSSelectorFromString(@"setProxyConfigurations:")])
        return;

"""

# The @available spelling: the same whole-body guard, wrapping the upstream if/else.
AVAILABLE_WRAPPED_BODY = """\
    if (@available(iOS 17.0, *)) {
        if (!m_nwProxyConfigs.isEmpty()) {
            RetainPtr nwProxyConfigurations = adoptNS([[NSMutableArray alloc] initWithCapacity:m_nwProxyConfigs.size()]);
            for (auto& proxyConfig : m_nwProxyConfigs)
                [nwProxyConfigurations addObject:proxyConfig.get()];

            configuration.proxyConfigurations = nwProxyConfigurations.get();
        } else
            configuration.proxyConfigurations = @[ ];
    }
"""

UPSTREAM_BODY = """\
    if (!m_nwProxyConfigs.isEmpty()) {
        RetainPtr nwProxyConfigurations = adoptNS([[NSMutableArray alloc] initWithCapacity:m_nwProxyConfigs.size()]);
        for (auto& proxyConfig : m_nwProxyConfigs)
            [nwProxyConfigurations addObject:proxyConfig.get()];

        configuration.proxyConfigurations = nwProxyConfigurations.get();
    } else
        configuration.proxyConfigurations = @[ ];
"""

# P2-2: a string literal containing /* used to open a block comment and swallow the rest of the
# file, which read as "no such function" and turned both modes into a pass. The reviewer's repro.
LITERAL_BLOCK_COMMENT = """\
static NSString *kSeparator = @"a/*b";
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    if (!m_nwProxyConfigs.isEmpty()) {
        configuration.proxyConfigurations = x;
    } else
        configuration.proxyConfigurations = @[ ];
}
"""

# P3-1: a // inside a literal, and a single-armed #if whose arms each open the brace.
LITERAL_LINE_COMMENT = """\
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    static NSString *kSeparator = @"a // b";
    if (!m_nwProxyConfigs.isEmpty()) {
        configuration.proxyConfigurations = x;
    } else
        configuration.proxyConfigurations = @[ ];
}
"""

ONE_ARMED_IF = """\
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    if (![configuration respondsToSelector:NSSelectorFromString(@"setProxyConfigurations:")])
        return;
#if WTF_PLATFORM_MAC
    if (macPath) {
#else
    if (iosPath) {
#endif
        configuration.proxyConfigurations = x;
    }
}
"""

# P3-3: the negated spelling puts the safe arm in the else, so a write there is protected.
NEGATED_ELSE = """\
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    if (![configuration respondsToSelector:NSSelectorFromString(@"setProxyConfigurations:")]) {
        m_nwProxyConfigs.clear();
    } else {
        if (!m_nwProxyConfigs.isEmpty()) {
            RetainPtr nwProxyConfigurations = adoptNS([[NSMutableArray alloc] initWithCapacity:m_nwProxyConfigs.size()]);
            configuration.proxyConfigurations = nwProxyConfigurations.get();
        } else
            configuration.proxyConfigurations = @[ ];
    }
}
"""

NO_FUNCTION = """\
void NetworkSessionCocoa::clearProxyConfigData()
{
    auto* clearProxies = nw_context_clear_proxiesPtr();
    if (!clearProxies)
        return;
}
"""

failures: list[str] = []


def hunk_lines() -> list[tuple[str, str]]:
    """Return (marker, body) for each line of the patch's one hunk."""
    lines: list[tuple[str, str]] = []
    in_hunk = False
    headers = 0
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@"):
            in_hunk = True
            headers += 1
            continue
        if line.startswith(("diff ", "--- ", "+++ ")):
            in_hunk = False
            continue
        if in_hunk and line[:1] in (" ", "+", "-"):
            lines.append((line[0], line[1:]))
    if headers != 1:
        raise ValueError(f"expected one hunk in {PATCH.name}, found {headers}")
    return lines


def apply_patch(text: str) -> str:
    """Splice the patch's hunk into `text` by matching its old side, not by trusting a line number.

    The old side is the run of context and removed lines, and it has to appear exactly once in the
    fixture, so a patch that no longer matches the upstream text fails here instead of landing at a
    nearby anchor.
    """
    old: list[str] = []
    new: list[str] = []
    for marker, body in hunk_lines():
        if marker in (" ", "-"):
            old.append(body)
        if marker in (" ", "+"):
            new.append(body)
    old_text = "\n".join(old) + "\n"
    occurrences = text.count(old_text)
    if occurrences != 1:
        raise ValueError(
            f"the patch's old side appears {occurrences} times in the fixture, expected once; the "
            f"patch and the fixture have drifted apart"
        )
    return text.replace(old_text, "\n".join(new) + "\n")


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"{old!r} appears {text.count(old)} times, expected once")
    return text.replace(old, new, 1)


def run(text: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "NetworkSessionCocoa.mm"
        path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(path), *arguments],
            capture_output=True, text=True,
        )


def case(label: str, text: str, expected_status: int,
         expected: str | tuple[str, ...], *arguments: str) -> None:
    result = run(text, *arguments)
    combined = result.stdout + result.stderr
    if result.returncode != expected_status:
        failures.append(f"{label}: exit {result.returncode}, expected {expected_status}\n{combined}")
        return
    for fragment in (expected,) if isinstance(expected, str) else expected:
        if fragment not in combined:
            failures.append(f"{label}: {fragment!r} missing from output\n{combined}")
            return
    print(f"PASS {label}")


def case_missing_file() -> None:
    """A wrong path must not read as a pass, because the workflow always passes a real one."""
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(Path(directory) / "absent.mm")],
            capture_output=True, text=True,
        )
    combined = result.stdout + result.stderr
    if result.returncode != 1 or "FAIL:" not in combined:
        failures.append(f"a missing file must fail, exit {result.returncode}\n{combined}")
        return
    print("PASS a missing file fails instead of skipping")


def main() -> int:
    try:
        patched = apply_patch(UNPATCHED)
    except ValueError as error:
        # A patch that no longer applies is the failure mode this suite exists to catch, so it joins
        # the failure list instead of escaping as a traceback and hiding the later cases.
        failures.append(f"the patch does not apply to the upstream fixture: {error}")
        patched = ""

    case("the unpatched 18.3.1 function fails and names both writes",
         UNPATCHED, 1,
         ("writes proxyConfigurations at lines 19, 21 with no runtime guard",
          "unrecognized selector sent to instance"))

    if patched:
        case("the patched file in the respondsToSelector: spelling passes",
             patched, 0,
             ("runtime guard respondsToSelector:",
              "behind it:",
              "Result: PASS"))
        case("the patched file in the @available spelling passes",
             replace_once(UNPATCHED, UPSTREAM_BODY, AVAILABLE_WRAPPED_BODY), 0,
             ("runtime guard @available(iOS 17.0, *)",
              "Result: PASS"))
        case("a guard around the non-empty branch only fails on the empty branch's write",
             replace_once(
                 replace_once(patched, SELECTOR_GUARD, ""),
                 NON_EMPTY_OPEN,
                 "    if (!m_nwProxyConfigs.isEmpty()) {\n        if (![configuration "
                 "respondsToSelector:NSSelectorFromString(@\"setProxyConfigurations:\")])\n"
                 "            return;\n"), 1,
             ("is not at the entry of applyProxyConfigurationToSessionConfiguration",
              "the write outside that branch still sends setProxyConfigurations: on iOS 16"))
        case("an @available block that holds only the non-empty branch fails",
             replace_once(UNPATCHED, UPSTREAM_BODY,
                          "    if (@available(iOS 17.0, *)) {\n"
                          "        if (!m_nwProxyConfigs.isEmpty()) {\n"
                          "            RetainPtr nwProxyConfigurations = adoptNS([[NSMutableArray "
                          "alloc] initWithCapacity:m_nwProxyConfigs.size()]);\n"
                          "            for (auto& proxyConfig : m_nwProxyConfigs)\n"
                          "                [nwProxyConfigurations addObject:proxyConfig.get()];\n"
                          "\n"
                          "            configuration.proxyConfigurations = "
                          "nwProxyConfigurations.get();\n"
                          "        }\n"
                          "    } else\n"
                          "        configuration.proxyConfigurations = @[ ];\n"), 1,
             "is not behind the runtime guard")
        case("an early return with the wrong polarity fails",
             replace_once(patched, SELECTOR_GUARD,
                          "    if ([configuration respondsToSelector:"
                          "NSSelectorFromString(@\"setProxyConfigurations:\")])\n        return;\n\n"),
             1,
             "has the wrong polarity")
        case("a guard placed after an earlier statement is not the entry guard",
             replace_once(patched, SELECTOR_GUARD,
                          "    m_nwProxyConfigs.isEmpty();\n" + SELECTOR_GUARD), 1,
             "is not at the entry of applyProxyConfigurationToSessionConfiguration")
        case("a commented-out guard does not protect the writes",
             replace_once(UNPATCHED, NON_EMPTY_OPEN,
                          "    // if (![configuration respondsToSelector:NSSelectorFromString("
                          "@\"setProxyConfigurations:\")])\n    //     return;\n" + NON_EMPTY_OPEN), 1,
             "with no runtime guard")

    # The --needs-guard mode is what the workflow's precheck loop reads, so both of its answers are
    # pinned here rather than only the check mode's exit codes above.
    case("--needs-guard says the unpatched ref requires the patch",
         UNPATCHED, 0, "NEEDS GUARD:", "--needs-guard")
    if patched:
        case("--needs-guard says the patched ref needs no patch",
             patched, 3, "NO GUARD NEEDED:", "--needs-guard")
    # Safari-16.6 and Safari-17.0 ship no such function, so the loop has to be free to skip there.
    case("--needs-guard says a ref without the function needs no patch",
         NO_FUNCTION, 3, "has no applyProxyConfigurationToSessionConfiguration", "--needs-guard")
    case("--needs-guard says a ref whose body never writes the property needs no patch",
         UNPATCHED.replace("configuration.proxyConfigurations", "configuration.otherProperty"), 3,
         "never writes proxyConfigurations", "--needs-guard")

    # A ref with no such function prints SKIP in the check mode too, rather than failing a build
    # that has nothing to guard.
    case("a ref without the function is skipped",
         NO_FUNCTION, 0, ("SKIP:", "Result: PASS (skipped)"))

    # A file the parser cannot read must not answer "no guard needed", or the precheck loop would
    # skip the patch and the build would abort on the device five hours later.
    case("--needs-guard reports an unparseable file as a failure, not as a skip",
         UNPATCHED.replace("        configuration.proxyConfigurations = @[ ];",
                           "        configuration.proxyConfigurations = @[ ;"), 1,
         ("cannot be parsed", "neither answer is known"), "--needs-guard")

    # An unclosed function brace has to fail the same way: find_body cannot bound the body at all, and
    # a silent pass there would be a skip for a file nobody could read.
    case("--needs-guard reports an unclosed function brace as a failure",
         UNPATCHED.replace("""        configuration.proxyConfigurations = @[ ];
}
#endif // HAVE(NW_PROXY_CONFIG)
""", """        configuration.proxyConfigurations = @[ ];
"""), 1,
         ("cannot be parsed", "neither answer is known"), "--needs-guard")

    # P2-2: both modes have to read this file rather than skip it. The literal's /* must not open a
    # comment that swallows the function, because skipping is a pass and the device would still abort.
    case("a /* inside a string literal does not hide the function (check mode)",
         LITERAL_BLOCK_COMMENT, 1,
         ("writes proxyConfigurations at lines 5, 7 with no runtime guard",
          "unrecognized selector sent to instance"))
    case("a /* inside a string literal still needs the patch (needs-guard mode)",
         LITERAL_BLOCK_COMMENT, 0, "NEEDS GUARD:", "--needs-guard")

    # P3-1: both are legal C++ and must be read as code, not as a parse failure.
    case("a // inside a string literal does not hide the writes",
         LITERAL_LINE_COMMENT, 1,
         "writes proxyConfigurations at lines 5, 7 with no runtime guard")
    case("a single-armed #if whose arms each open the brace is well formed",
         ONE_ARMED_IF, 0, ("runtime guard respondsToSelector:", "Result: PASS"))

    # P3-3: with the negated spelling the else arm is the side where the setter exists.
    case("a write in the else arm of a negated guard is protected",
         NEGATED_ELSE, 0, ("runtime guard respondsToSelector:", "Result: PASS"))
    # The same shape un-negated keeps the else arm on the unsafe side: there the test came out false,
    # which is exactly the device that lacks the setter, so both writes must still be reported.
    case("a write in the else arm of an un-negated guard is not protected",
         NEGATED_ELSE.replace(
             "if (![configuration respondsToSelector:NSSelectorFromString(@\"setProxyConfigurations:\")]) {",
             "if ([configuration respondsToSelector:NSSelectorFromString(@\"setProxyConfigurations:\")]) {"),
         1, ("behind it: nothing", "is not behind the runtime guard"))
    # And the mirror: the block of an un-negated guard is the side where the setter exists, so the
    # same writes inside the block are protected. Without this case the polarity rule could be
    # satisfied by rejecting every else arm and every block, which would pass the case above.
    case("a write in the block of an un-negated guard is protected",
         NEGATED_ELSE.replace(
             "if (![configuration respondsToSelector:NSSelectorFromString(@\"setProxyConfigurations:\")]) {",
             "if ([configuration respondsToSelector:NSSelectorFromString(@\"setProxyConfigurations:\")]) {")
                    .replace("""    } else {
        if (!m_nwProxyConfigs.isEmpty()) {""", """        if (!m_nwProxyConfigs.isEmpty()) {""")
                    .replace("""        } else
            configuration.proxyConfigurations = @[ ];
    }
}""", """        } else
            configuration.proxyConfigurations = @[ ];
    }
}"""), 0, ("runtime guard respondsToSelector:", "Result: PASS"))

    # The help text has to describe the codes the tool actually returns (P3-2).
    with tempfile.TemporaryDirectory() as directory:
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True,
        )
    if "3 when a skip is correct" not in help_result.stdout:
        failures.append(
            "--help does not name the skip code 3, so it describes codes the tool no longer returns"
            f"\n{help_result.stdout}"
        )
    else:
        print("PASS --help names the skip code 3")

    case_missing_file()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
