#!/usr/bin/env python3
"""Require the iOS 17 trait API to be unreachable from an iOS 16.1 build.

Source/WebCore/platform/ios/LocalCurrentTraitCollection.mm builds its trait collection with
`-[UITraitCollection traitCollectionByModifyingTraits:]` and the UIMutableTraits protocol, both iOS
17 API. A 16.1 build compiles that send and the device aborts inside WebProcessPool::prewarmProcess:

  EXC_CRASH (SIGABRT)
  WebProcessPool::prewarmProcess → platformInitializeWebProcess →
  RenderThemeIOS::cssValueToSystemColorMap → LocalCurrentTraitCollection::LocalCurrentTraitCollection(bool, bool)
  → -[NSObject doesNotRecognizeSelector:] → abort()

Disassembly of the shipped WebCore puts the call at offset 0xc62250 on the UITraitCollection that
-currentTraitCollection returns, so the selector is missing on the device rather than misused.

patches/ios16-local-current-trait-collection-guard.patch splits the constructor on
__IPHONE_OS_VERSION_MIN_REQUIRED < 170000 and builds the same traits from
+traitCollectionWithTraitsFromCollections: (iOS 8), +traitCollectionWithUserInterfaceStyle:
(iOS 12) and +traitCollectionWithUserInterfaceLevel: (iOS 13) below that version.

This script reads the patched file, replays its `#if` structure through clang's preprocessor once
per platform and deployment target, and checks which lines each configuration still reaches:

  iOS 16.1        must reach the three class methods and no send of the iOS 17 selector;
  iOS 17.0        must still reach the constructor's upstream block, so the patch guards that arm
                  instead of dropping it;
  macCatalyst     is iOS-family and takes the same below-17.0 arm;
  tvOS and
  watchOS         must not take the fallback either, because UIKit marks
                  +traitCollectionWithUserInterfaceLevel: unavailable on both and
                  AvailabilityInternal.h defines __IPHONE_OS_VERSION_MIN_REQUIRED as 90000 there;
  visionOS        keeps its own PLATFORM(VISION) block, which the patch does not touch. The iOS 17
                  selector and the UIMutableTraits protocol it uses are allowed inside that block
                  only, and a file whose constructor never sends the selector needs no guard at all.

Reading the directives rather than one fixed line means a ref that rewrites the guard, or a patch
that stops applying, fails here in seconds instead of at an abort on the device. Two failures live
outside this check's reach and are not claimed anywhere in its output: a 16.x SDK that does not
declare UIMutableTraits also fails to compile (clang reports that itself), and whether the three
class methods actually respond at runtime on iOS 16 rests on their API_AVAILABLE(ios(12.0)) /
API_AVAILABLE(ios(13.0)) header annotations, since the device would not load a probe binary.

Usage:
  scripts/check-local-current-trait-collection.py \
    --source WebKit/Source/WebCore/platform/ios/LocalCurrentTraitCollection.mm

Regression tests write their own fixtures and need no checkout:
  python3 scripts/test-check-local-current-trait-collection.py
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The selector and the protocol that only exist from iOS 17, and the three class methods the
# below-17.0 arm has to use instead.
IOS_17_SELECTOR = "traitCollectionByModifyingTraits"
IOS_17_PROTOCOL = "UIMutableTraits"
CLASS_METHODS = (
    "traitCollectionWithTraitsFromCollections:",
    "traitCollectionWithUserInterfaceStyle:",
    "traitCollectionWithUserInterfaceLevel:",
)

DIRECTIVE = re.compile(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b")
MARKER = "H1_REACHES_"

# One configuration per platform WebKit builds this file for. iOS 16.1 is the target this build
# ships and 17.0 is the version the upstream arm needs. macCatalyst takes the same below-17.0 arm
# as iOS. tvOS and watchOS read 90000 out of __IPHONE_OS_VERSION_MIN_REQUIRED while marking the
# level trait unavailable, which is why the guard has to name its platforms instead of testing that
# macro alone. visionOS is the platform the untouched PLATFORM(VISION) block is written for.
IOS_16_1 = {
    "WTF_PLATFORM_IOS": "1",
    "WTF_PLATFORM_IOS_FAMILY": "1",
    "__IPHONE_OS_VERSION_MIN_REQUIRED": "160100",
}
IOS_17_0 = {
    "WTF_PLATFORM_IOS": "1",
    "WTF_PLATFORM_IOS_FAMILY": "1",
    "__IPHONE_OS_VERSION_MIN_REQUIRED": "170000",
}
MACCATALYST_16_1 = {
    "WTF_PLATFORM_MACCATALYST": "1",
    "WTF_PLATFORM_IOS_FAMILY": "1",
    "__IPHONE_OS_VERSION_MIN_REQUIRED": "160100",
}
TVOS_16_1 = {
    "WTF_PLATFORM_APPLETV": "1",
    "WTF_PLATFORM_IOS_FAMILY": "1",
    "__IPHONE_OS_VERSION_MIN_REQUIRED": "90000",
}
WATCHOS_9_0 = {
    "WTF_PLATFORM_WATCHOS": "1",
    "WTF_PLATFORM_IOS_FAMILY": "1",
    "__IPHONE_OS_VERSION_MIN_REQUIRED": "90000",
}
VISION = {"WTF_PLATFORM_VISION": "1", "WTF_PLATFORM_IOS_FAMILY": "1"}

CONFIGURATIONS = (
    ("iOS 16.1", IOS_16_1),
    ("iOS 17.0", IOS_17_0),
    ("macCatalyst 16.1", MACCATALYST_16_1),
    ("tvOS 16.1", TVOS_16_1),
    ("watchOS 9.0", WATCHOS_9_0),
    ("visionOS", VISION),
)

# A [[fallthrough]] on visionOS and an #else are ordinary C, but a probe preprocesses the file
# without parsing it, so only the directives and markers matter. The probe therefore supplies
# PLATFORM(x) the way PlatformLegacy.h defines it — a platform is on when its WTF_PLATFORM_ macro is
# defined and non-zero — and nothing else.
PROLOGUE = "#define PLATFORM(x) (defined WTF_PLATFORM_##x && WTF_PLATFORM_##x)\n"


@dataclass(frozen=True)
class Frame:
    """One open conditional level: the guard that opened it and the branch this line sits in."""

    guard: str
    branch: str

    def describe(self) -> str:
        return self.guard if self.branch == "if" else f"{self.guard} → {self.branch}"


@dataclass
class Source:
    """The file's physical lines, comment-free, plus the conditional frames that hold each one."""

    lines: list[str]
    frames: list[list[Frame]]

    def occurrences(self, needle: str) -> list[int]:
        """Return the 1-based line numbers whose code contains `needle`."""
        return [number for number, text in enumerate(self.lines, 1) if needle in text]

    def definitions(self, needle: str) -> list[int]:
        """Return the line numbers that define `needle` rather than use it."""
        return [
            number
            for number, text in enumerate(self.lines, 1)
            if re.search(rf"#\s*define\s+\w*{re.escape(needle)}\b", text)
        ]

    def uses(self, needle: str) -> list[int]:
        return [number for number in self.occurrences(needle) if number not in self.definitions(needle)]

    def describe(self, number: int) -> str:
        """Name the conditional that holds a line, so a failure points at a branch."""
        frames = self.frames[number - 1]
        if not frames:
            return f"line {number} is outside every conditional"
        return f"line {number} sits under {' / '.join(frame.describe() for frame in frames)}"


def strip_comments(lines: list[str]) -> list[str]:
    """Blank out // and /* */ comments while keeping one output line per input line.

    The patch explains the iOS 17 call in prose above the guard, so the selector name appears in
    comments as well as in code; matching it there would report the explanation as a call.
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
            # `//` in `rdar://problem/96607991` is part of a URL, not a comment.
            if line.startswith("//", index) and not (index > 0 and line[index - 1] == ":"):
                break
            kept.append(line[index])
            index += 1
        output.append("".join(kept))
    return output


def conditional_frames(lines: list[str]) -> list[list[Frame]]:
    """Return the open conditionals at each line, in file order.

    `#elif` and `#else` replace the branch at their own level rather than opening a new one, so the
    frames record which arm of a guard a line belongs to and `#if` over an `#else` cannot be read as
    two independent conditions.
    """
    frames: list[list[Frame]] = []
    stack: list[Frame] = []
    continuation = False
    for text in lines:
        stripped = text.strip()
        if continuation:
            frames.append(list(stack))
            continuation = stripped.endswith("\\")
            continue
        directive = DIRECTIVE.match(text)
        if directive is not None:
            keyword = directive.group(1)
            if keyword in ("if", "ifdef", "ifndef"):
                stack.append(Frame(stripped, "if"))
            elif keyword in ("elif", "else"):
                if not stack:
                    raise ValueError(f"#{keyword} with no open #if: {stripped}")
                parent = stack.pop()
                stack.append(Frame(parent.guard, "else" if keyword == "else" else stripped))
            elif keyword == "endif":
                if not stack:
                    raise ValueError(f"#endif with no open #if: {stripped}")
                stack.pop()
            frames.append(list(stack))
            continuation = stripped.endswith("\\")
            continue
        frames.append(list(stack))
    if stack:
        raise ValueError(f"{len(stack)} unterminated #if block(s), last is: {stack[-1].guard}")
    return frames


def read_source(path: Path) -> Source:
    raw = path.read_text(encoding="utf-8").splitlines()
    lines = strip_comments(raw)
    return Source(lines=lines, frames=conditional_frames(lines))


def find_clang() -> str:
    """Resolve clang, preferring Xcode's copy on macOS and falling back to PATH."""
    if shutil.which("xcrun"):
        found = subprocess.run(
            ["xcrun", "--find", "clang"], capture_output=True, text=True, check=False
        )
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    resolved = shutil.which("clang")
    if not resolved:
        raise SystemExit("Error: clang not found in PATH and xcrun could not locate it")
    return resolved


def preprocess(clang: str, text: str, defines: dict[str, str], work: Path) -> str:
    path = work / "probe.c"
    path.write_text(PROLOGUE + text, encoding="utf-8")
    arguments = [clang, "-E", "-P", "-x", "c", str(path)]
    arguments += [f"-D{name}={value}" for name, value in defines.items()]
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Error: clang could not preprocess a probe ({result.stderr.strip()})")
    return result.stdout


def reaches(clang: str, source: Source, defines: dict[str, str], work: Path) -> set[int]:
    """Return the line numbers clang's preprocessor keeps under `defines`.

    Every code line becomes a numbered marker and every directive is passed through verbatim, so
    the probe keeps the file's real `#if` nesting and only the conditions are decided by `defines`.
    A continuation line of a wrapped directive is passed through too, or clang would see half a
    condition. Preprocessing does not parse C, so markers inside a function body or a message send
    are fine.
    """
    probe: list[str] = []
    continuing = False
    for number, text in enumerate(source.lines, 1):
        if continuing or DIRECTIVE.match(text):
            probe.append(text)
            continuing = text.rstrip().endswith("\\")
            continue
        probe.append(f"{MARKER}{number}")
    kept = set()
    for token in preprocess(clang, "\n".join(probe) + "\n", defines, work).split():
        if token.startswith(MARKER):
            kept.add(int(token[len(MARKER):]))
    return kept


def vision_guards(clang: str, source: Source, work: Path) -> set[str]:
    """Return the `#if` guards that only hold on visionOS.

    Tested per guard rather than per line, so a guard is classified once no matter how many lines it
    encloses: it holds under the visionOS defines and not under the iOS 16.1 ones, which is exactly
    the `#if PLATFORM(VISION)` this file uses around its idiom adjustment.
    """
    guards = {
        frame.guard
        for frames in source.frames
        for frame in frames
        if frame.branch == "if" and frame.guard.startswith("#if")
    }
    only_vision = set()
    for guard in guards:
        on_vision = preprocess(clang, f"{guard}\nH1_YES\n#endif\n", VISION, work)
        on_ios = preprocess(clang, f"{guard}\nH1_YES\n#endif\n", IOS_16_1, work)
        if "H1_YES" in on_vision.split() and "H1_YES" not in on_ios.split():
            only_vision.add(guard)
    return only_vision


def in_vision_branch(source: Source, number: int, guards: set[str]) -> bool:
    """Whether a line is compiled only for visionOS: an `#if` arm whose guard is vision-only."""
    return any(
        frame.branch == "if" and frame.guard in guards for frame in source.frames[number - 1]
    )


def arm_summary(source: Source, reachable: set[int]) -> str:
    """Name the arm a configuration compiles, so the report reads as one sentence per platform."""
    upstream = [number for number in source.uses(IOS_17_SELECTOR) if number in reachable]
    fallback = sorted(
        {
            number
            for method in CLASS_METHODS
            for number in source.uses(method)
            if number in reachable
        }
    )
    arms = []
    if fallback:
        arms.append(f"class methods (lines {', '.join(map(str, fallback))})")
    if upstream:
        arms.append(f"{IOS_17_SELECTOR} (lines {', '.join(map(str, upstream))})")
    return " and ".join(arms) or "no trait-collection construction"


def check(source: Source, vision: set[str], work: Path, clang: str) -> tuple[list[str], list[str]]:
    """Return (failures, notes) for the patched file."""
    failures: list[str] = []
    reachable = {label: reaches(clang, source, defines, work) for label, defines in CONFIGURATIONS}
    constructor_uses = [
        number for number in source.uses(IOS_17_SELECTOR) if not in_vision_branch(source, number, vision)
    ]

    for number in constructor_uses:
        if number in reachable["iOS 16.1"]:
            failures.append(
                f"{IOS_17_SELECTOR} is compiled into the 16.1 build: {source.describe(number)}; "
                f"UIKit on iOS 16 does not implement that selector, so the web process aborts in "
                f"doesNotRecognizeSelector: inside prewarmProcess"
            )
    for number in source.uses(IOS_17_PROTOCOL):
        if number in reachable["iOS 16.1"]:
            failures.append(
                f"{IOS_17_PROTOCOL} is referenced in the 16.1 build: {source.describe(number)}; a "
                f"16.x SDK does not declare that protocol, so WebCore would not compile"
            )
    if constructor_uses and not any(number in reachable["iOS 17.0"] for number in constructor_uses):
        failures.append(
            f"no {IOS_17_SELECTOR} call is compiled at iOS 17.0, so the patch dropped the "
            f"constructor's upstream block instead of guarding it "
            f"({source.describe(constructor_uses[0])})"
        )

    for method in CLASS_METHODS:
        uses = source.uses(method)
        if not uses:
            failures.append(
                f"+{method} is never called, so the below-17.0 arm cannot build the trait "
                f"collection it replaces"
            )
            continue
        if not any(number in reachable["iOS 16.1"] for number in uses):
            failures.append(
                f"+{method} is not compiled at iOS 16.1: {source.describe(uses[0])}; the 16.1 "
                f"build would then have no arm to build the trait collection with"
            )
        for number in uses:
            if number in reachable["iOS 17.0"]:
                failures.append(
                    f"+{method} is also compiled at iOS 17.0: {source.describe(number)}; the guard "
                    f"does not switch the arm at 17.0"
                )
        for configuration in ("tvOS 16.1", "watchOS 9.0"):
            for number in uses:
                if number in reachable[configuration]:
                    failures.append(
                        f"+{method} is compiled for {configuration}: {source.describe(number)}; "
                        f"UIKit marks that method unavailable there, so the arm has to name "
                        f"PLATFORM(IOS) or PLATFORM(MACCATALYST) rather than test "
                        f"__IPHONE_OS_VERSION_MIN_REQUIRED alone, which AvailabilityInternal.h "
                        f"defines as 90000 on both"
                    )

    notes = [f"{label} compiles {arm_summary(source, reachable[label])}"
             for label, _ in CONFIGURATIONS]
    return failures, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require the iOS 17 trait API in LocalCurrentTraitCollection.mm to be unreachable from "
            "an iOS 16.1 build."
        )
    )
    parser.add_argument("--source", required=True, type=Path,
                        help="the patched LocalCurrentTraitCollection.mm")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        # This file has shipped since 2019, so a missing one is a wrong path rather than an older
        # ref; the workflow applies the patch first and checks the path the patch edits.
        print(f"FAIL: {args.source} does not exist, so the guard cannot be checked", file=sys.stderr)
        return 1

    try:
        source = read_source(args.source)
    except ValueError as error:
        print(f"FAIL: {args.source} does not preprocess as a self-contained file: {error}",
              file=sys.stderr)
        return 1

    clang = find_clang()
    with tempfile.TemporaryDirectory(prefix="local-current-trait-collection-") as directory:
        work = Path(directory)
        vision = vision_guards(clang, source, work)
        constructor_uses = [
            number
            for number in source.uses(IOS_17_SELECTOR)
            if not in_vision_branch(source, number, vision)
        ]
        if not constructor_uses:
            # Upstream before 278011@main built the collection with the class methods, so a ref that
            # already reads that way has no iOS 17 send to guard; the patch does not apply there and
            # the workflow skips it. A file that only uses the selector under PLATFORM(VISION) is
            # this same case, because a visionOS build has the API.
            print(f"SKIP: {args.source} never sends {IOS_17_SELECTOR} outside PLATFORM(VISION), "
                  f"so its constructor needs no guard")
            print("\nResult: PASS (skipped)")
            return 0
        failures, notes = check(source, vision, work, clang)

    print(f"== {args.source}")
    for note in notes:
        print(f"   {note}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"\nResult: FAIL ({len(failures)} finding(s))", file=sys.stderr)
        return 1
    print(
        f"OK: iOS 16.1 compiles the {len(CLASS_METHODS)} iOS 12/13 class methods and no "
        f"{IOS_17_SELECTOR} call, and iOS 17.0 still compiles the upstream block"
    )
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
