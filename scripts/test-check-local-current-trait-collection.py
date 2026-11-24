#!/usr/bin/env python3
"""Regression tests for scripts/check-local-current-trait-collection.py.

The fixtures are written here rather than read from a WebKit checkout, so the cases run without
Xcode, a WebKit tree or network access. Three of them are the outcomes the mission names:

  the unpatched file must fail, because that is the build that crashed the device;
  patches/ios16-local-current-trait-collection-guard.patch must make it pass;
  a file whose only iOS 17 send is inside PLATFORM(VISION) must be skipped, because a visionOS
  build has the API and that block is upstream's.

The rest pin the ways the crash can come back: a guard that also fires on tvOS and watchOS, a
fallback arm that disappears, a guard that never switches at 17.0, and an arm that hides the
upstream block so nothing builds the collection at 17.0.

UNPATCHED is the upstream file at tag Safari-18.3.1-iOS-18.3.2, byte for byte (78 lines, sha256
6f45ce98b5ae8e1fb9a02f8993e08854b41aa5e9620bbf53e78a6b6c1dc3ccb3), so the failing direction is tested
against the text the compiler actually built. The patched fixture is rebuilt by applying the patch's
own hunk to that text, so it cannot drift from the patch the build applies.

Usage: python3 scripts/test-check-local-current-trait-collection.py
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-local-current-trait-collection.py"
PATCH = SCRIPTS.parent / "patches" / "ios16-local-current-trait-collection-guard.patch"
UPSTREAM_SHA256 = "6f45ce98b5ae8e1fb9a02f8993e08854b41aa5e9620bbf53e78a6b6c1dc3ccb3"

UNPATCHED = """/*
 * Copyright (C) 2019 Apple Inc. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1.  Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 * 2.  Redistributions in binary form must reproduce the above copyright
 *     notice, this list of conditions and the following disclaimer in the
 *     documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY APPLE INC. AND ITS CONTRIBUTORS ``AS IS'' AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL APPLE INC. OR ITS CONTRIBUTORS BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
 * ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#import "config.h"
#import "LocalCurrentTraitCollection.h"

#if PLATFORM(IOS_FAMILY)

#import <pal/ios/UIKitSoftLink.h>

namespace WebCore {

UITraitCollection *traitCollectionWithAdjustedIdiomForSystemColors(UITraitCollection *traitCollection)
{
#if PLATFORM(VISION)
    // Use the iPad idiom instead of the Vision idiom, since some system colors are transparent
    // in the Vision idiom, and are not web-compatible.
    if (traitCollection.userInterfaceIdiom == UIUserInterfaceIdiomVision) {
        return [traitCollection traitCollectionByModifyingTraits:^(id<UIMutableTraits> traits) {
            traits.userInterfaceIdiom = UIUserInterfaceIdiomPad;
        }];
    }
#endif
    return traitCollection;
}

LocalCurrentTraitCollection::LocalCurrentTraitCollection(bool useDarkAppearance, bool useElevatedUserInterfaceLevel)
{
    m_savedTraitCollection = [PAL::getUITraitCollectionClass() currentTraitCollection];

    // FIXME: <rdar://problem/96607991> `-[UITraitCollection currentTraitCollection]` is not guaranteed
    // to return a useful set of traits in cases where it has not been explicitly set. Ideally, this
    // method should also take in a base, full-specified trait collection from the view hierarchy, to be
    // used when building the new trait collection.
    RetainPtr combinedTraits = [m_savedTraitCollection traitCollectionByModifyingTraits:^(id<UIMutableTraits> traits) {
        traits.userInterfaceStyle = useDarkAppearance ? UIUserInterfaceStyleDark : UIUserInterfaceStyleLight;
        traits.userInterfaceLevel = useElevatedUserInterfaceLevel ? UIUserInterfaceLevelElevated : UIUserInterfaceLevelBase;
    }];

    [PAL::getUITraitCollectionClass() setCurrentTraitCollection:traitCollectionWithAdjustedIdiomForSystemColors(combinedTraits.get())];
}

LocalCurrentTraitCollection::LocalCurrentTraitCollection(UITraitCollection *traitCollection)
{
    m_savedTraitCollection = [PAL::getUITraitCollectionClass() currentTraitCollection];
    auto newTraitCollection = traitCollectionWithAdjustedIdiomForSystemColors(traitCollection);
    [PAL::getUITraitCollectionClass() setCurrentTraitCollection:newTraitCollection];
}

LocalCurrentTraitCollection::~LocalCurrentTraitCollection()
{
    [PAL::getUITraitCollectionClass() setCurrentTraitCollection:m_savedTraitCollection.get()];
}

}

#endif // PLATFORM(IOS_FAMILY)
"""

# The constructor's four lines that send the iOS 17 selector, and the guard the patch puts above
# them. Both are written once and used to derive every failing variant, so a variant cannot drift
# from the patch either.
IOS_17_SEND = """\
    RetainPtr combinedTraits = [m_savedTraitCollection traitCollectionByModifyingTraits:^(id<UIMutableTraits> traits) {
        traits.userInterfaceStyle = useDarkAppearance ? UIUserInterfaceStyleDark : UIUserInterfaceStyleLight;
        traits.userInterfaceLevel = useElevatedUserInterfaceLevel ? UIUserInterfaceLevelElevated : UIUserInterfaceLevelBase;
    }];
"""

PATCHED_GUARD = (
    "#if defined(__IPHONE_OS_VERSION_MIN_REQUIRED) && __IPHONE_OS_VERSION_MIN_REQUIRED < 170000 "
    "&& (PLATFORM(IOS) || PLATFORM(MACCATALYST))"
)

FALLBACK = """\
    auto userInterfaceStyleTrait = [PAL::getUITraitCollectionClass() traitCollectionWithUserInterfaceStyle:useDarkAppearance ? UIUserInterfaceStyleDark : UIUserInterfaceStyleLight];
    auto userInterfaceLevelTrait = [PAL::getUITraitCollectionClass() traitCollectionWithUserInterfaceLevel:useElevatedUserInterfaceLevel ? UIUserInterfaceLevelElevated : UIUserInterfaceLevelBase];
    RetainPtr<UITraitCollection> combinedTraits = [PAL::getUITraitCollectionClass() traitCollectionWithTraitsFromCollections:@[ m_savedTraitCollection.get(), userInterfaceStyleTrait, userInterfaceLevelTrait ]];
"""

# The historical spelling from 7305395509b2bb: the constructor merges the traits with the class
# methods unconditionally, and the only iOS 17 send left in the file is the vision idiom block.
HISTORICAL_SEND = """\
    auto userInterfaceStyleTrait = [PAL::getUITraitCollectionClass() traitCollectionWithUserInterfaceStyle:useDarkAppearance ? UIUserInterfaceStyleDark : UIUserInterfaceStyleLight];
    auto backgroundLevelTrait = [PAL::getUITraitCollectionClass() traitCollectionWithUserInterfaceLevel:useElevatedUserInterfaceLevel ? UIUserInterfaceLevelElevated : UIUserInterfaceLevelBase];
    RetainPtr combinedTraits = [PAL::getUITraitCollectionClass() traitCollectionWithTraitsFromCollections:@[ m_savedTraitCollection.get(), userInterfaceStyleTrait, backgroundLevelTrait ]];
"""

# An empty header with no constructor at all: the file still sends the selector, but only where a
# visionOS build compiles it.
HEADER_ONLY = UNPATCHED.replace(IOS_17_SEND, "")

failures: list[str] = []


def apply_patch(text: str) -> str:
    """Apply the patch's single hunk to `text`, walking its lines in order.

    The hunk interleaves context and additions (`+#if`, fallback lines, `+#else`, then the upstream
    block as context, then `+#endif`), so the result is built by consuming context from the fixture
    and emitting additions where they sit. Consuming means a context line or a removal has to match
    the fixture at that position, which is what makes a drifted patch fail here instead of splicing
    itself in at a guessed anchor.
    """
    hunk: list[str] = []
    in_hunk = False
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@"):
            in_hunk = True
            hunk.append(line)
            continue
        if line.startswith(("diff ", "--- ", "+++ ")):
            in_hunk = False
            continue
        if in_hunk and line[:1] in (" ", "+", "-"):
            hunk.append(line)
    headers = [line for line in hunk if line.startswith("@@")]
    if len(headers) != 1:
        raise ValueError(f"expected one hunk in {PATCH.name}, found {len(headers)}")
    old_start = int(headers[0].split()[1].split(",")[0].lstrip("-"))
    lines = text.splitlines()
    cursor = old_start - 1
    output = list(lines[:cursor])
    for line in hunk[1:]:
        marker, body = line[0], line[1:]
        if marker == " ":
            _expect(lines, cursor, body)
            output.append(body)
            cursor += 1
        elif marker == "-":
            _expect(lines, cursor, body)
            cursor += 1
        else:
            output.append(body)
    output.extend(lines[cursor:])
    return "\n".join(output) + "\n"


def _expect(lines: list[str], cursor: int, body: str) -> None:
    """Fail loudly when the hunk and the fixture disagree at `cursor`."""
    if cursor >= len(lines) or lines[cursor] != body:
        actual = lines[cursor] if cursor < len(lines) else "<end of file>"
        raise ValueError(
            f"the patch does not match the fixture at line {cursor + 1}:\n"
            f"  patch:   {body!r}\n"
            f"  fixture: {actual!r}"
        )


def guard_with(text: str, condition: str) -> str:
    """Swap the patch's guard for another condition, keeping the rest of the hunk unchanged."""
    if PATCHED_GUARD not in text:
        raise ValueError("the patched fixture no longer carries the guard the variants derive from")
    return text.replace(PATCHED_GUARD, condition)


def run(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "LocalCurrentTraitCollection.mm"
        path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(path)], capture_output=True, text=True
        )


def case(label: str, text: str, expected_status: int,
         expected: str | tuple[str, ...]) -> None:
    result = run(text)
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


def case_upstream_fixture() -> None:
    """The unpatched fixture must still be the upstream file the device crashed on."""
    digest = hashlib.sha256(UNPATCHED.encode("utf-8")).hexdigest()
    if digest != UPSTREAM_SHA256:
        failures.append(
            f"the unpatched fixture is not the upstream file: sha256 {digest}, expected "
            f"{UPSTREAM_SHA256} (a fixture that drifts stops proving the failure case)"
        )
        return
    if PATCHED_GUARD not in PATCH.read_text(encoding="utf-8"):
        failures.append("the patch no longer carries the guard the variants derive from")
        return
    print("PASS the unpatched fixture is the upstream 18.3.1 file byte for byte")


def main() -> int:
    try:
        patched = apply_patch(UNPATCHED)
    except ValueError as error:
        # A patch that no longer applies is the failure mode this suite exists to catch, so it joins
        # the failure list instead of escaping as a traceback and hiding the later cases.
        failures.append(f"the patch does not apply to the upstream fixture: {error}")
        patched = ""

    case("the unpatched 18.3.1 file fails and names the aborted selector",
         UNPATCHED, 1,
         ("FAIL: traitCollectionByModifyingTraits is compiled into the 16.1 build: line 56 "
          "sits under #if PLATFORM(IOS_FAMILY)",
          "doesNotRecognizeSelector: inside prewarmProcess"))
    if patched:
        case("the patched 18.3.1 file passes",
             patched, 0,
             ("iOS 16.1 compiles class methods",
              "iOS 17.0 compiles traitCollectionByModifyingTraits",
              "Result: PASS"))
        # Each failing variant below is the patched file with one edit, so a green run proves the
        # check discriminates rather than merely accepting the patched bytes.
        case("a guard that tests only the version macro fails for tvOS and watchOS",
             guard_with(patched, "#if defined(__IPHONE_OS_VERSION_MIN_REQUIRED) && "
                                 "__IPHONE_OS_VERSION_MIN_REQUIRED < 170000"), 1,
             ("is compiled for tvOS 16.1", "is compiled for watchOS 9.0",
              "defines as 90000 on both"))
        case("a guard whose fallback arm is empty fails at 16.1",
             guard_with(patched, PATCHED_GUARD).replace(FALLBACK, ""), 1,
             "is never called, so the below-17.0 arm cannot build the trait collection")
        case("a guard that never switches at 17.0 fails",
             guard_with(patched, "#if defined(__IPHONE_OS_VERSION_MIN_REQUIRED) && "
                                 "(PLATFORM(IOS) || PLATFORM(MACCATALYST))"), 1,
             "is also compiled at iOS 17.0")
        case("an arm that hides the upstream block fails at 17.0",
             guard_with(patched, "#if 1"), 1,
             "no traitCollectionByModifyingTraits call is compiled at iOS 17.0")
        # The historical 7305395509b2bb spelling: the constructor merges unconditionally with the
        # class methods, so the only iOS 17 send left in the file is the vision idiom block, which a
        # visionOS build has.
        case("a file whose only iOS 17 send is the vision block is skipped",
             patched.replace(IOS_17_SEND, HISTORICAL_SEND), 0,
             ("SKIP:", "Result: PASS (skipped)"))
    case("a file with no constructor is skipped rather than passed",
         HEADER_ONLY, 0,
         ("SKIP:", "Result: PASS (skipped)"))
    case_missing_file()
    case_upstream_fixture()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
