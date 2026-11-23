#!/usr/bin/env python3
"""Regression tests for scripts/check-text-extraction-availability.py.

The cases pin the file that stopped build 45078008945 at tag Safari-18.3.1-iOS-18.3.2: nine
top-level @objc declarations, eight annotated `@available(iOS 17.0, macOS 12.0, *)`, and
`WKTextExtractionRequest` unannotated while its stored handler, its init and `fulfill(item:)` all
name WKTextExtractionItem. Three errors, at :126:38, :128:78 and :133:47, and the WebKitSwift
target never emitted its module.

  the unpatched file must fail, because that is the build that ran;
  patches/ios18-public-sdk-text-extraction-availability.patch must make it pass;
  and the checker must keep failing for the other ways the same error can come back.

The unpatched fixture below is the full upstream file, so the "does it still fail" direction is
tested against the text the compiler actually read rather than a shortened sample. Each case
writes its own file, so nothing here depends on a WebKit checkout.

Usage: python3 scripts/test-check-text-extraction-availability.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-text-extraction-availability.py"
PATCH = SCRIPTS.parent / "patches" / "ios18-public-sdk-text-extraction-availability.patch"

# Source/WebKit/WebKitSwift/TextExtraction/WKTextExtractionItem.swift at tag
# Safari-18.3.1-iOS-18.3.2, byte for byte (md5 75a6a84c65b56f183d908d97fa6f45e9, also the blob at
# Safari-18-iOS-18.0).
UNPATCHED = """// Copyright (C) 2024 Apple Inc. All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions
// are met:
// 1. Redistributions of source code must retain the above copyright
//    notice, this list of conditions and the following disclaimer.
// 2. Redistributions in binary form must reproduce the above copyright
//    notice, this list of conditions and the following disclaimer in the
//    documentation and/or other materials provided with the distribution.
//
// THIS SOFTWARE IS PROVIDED BY APPLE INC. AND ITS CONTRIBUTORS ``AS IS''
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
// THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
// PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL APPLE INC. OR ITS CONTRIBUTORS
// BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
// CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
// SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
// CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
// THE POSSIBILITY OF SUCH DAMAGE.

import Foundation

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionItem) public class WKTextExtractionItem: NSObject {
    @objc public let rectInWebView: CGRect
    @objc public let children: [WKTextExtractionItem]

    public init(with rectInWebView: CGRect, children: [WKTextExtractionItem]) {
        self.rectInWebView = rectInWebView
        self.children = children
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc public enum WKTextExtractionContainer: Int {
    case root
    case viewportConstrained
    case list
    case listItem
    case blockQuote
    case article
    case section
    case nav
    case button
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionContainerItem) public class WKTextExtractionContainerItem: WKTextExtractionItem {
    @objc public let container: WKTextExtractionContainer

    @objc public init(container: WKTextExtractionContainer, rectInWebView: CGRect, children: [WKTextExtractionItem]) {
        self.container = container
        super.init(with: rectInWebView, children: children)
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionEditable) public class WKTextExtractionEditable: NSObject {
    @objc public let label: String
    @objc public let placeholder: String
    @objc public let isSecure: Bool
    @objc public let isFocused: Bool

    @objc public init(label: String, placeholder: String, isSecure: Bool, isFocused: Bool) {
        self.label = label
        self.placeholder = placeholder
        self.isSecure = isSecure
        self.isFocused = isFocused
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionLink) public class WKTextExtractionLink: NSObject {
    @objc public let url: NSURL
    @objc public let range: NSRange

    @objc(initWithURL:range:) public init(url: NSURL, range: NSRange) {
        self.url = url
        self.range = range
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionTextItem) public class WKTextExtractionTextItem: WKTextExtractionItem {
    @objc public let content: String
    @objc public let selectedRange: NSRange
    @objc public let links: [WKTextExtractionLink]
    @objc public let editable: WKTextExtractionEditable?

    @objc public init(content: String, selectedRange: NSRange, links: [WKTextExtractionLink], editable: WKTextExtractionEditable?, rectInWebView: CGRect, children: [WKTextExtractionItem]) {
        self.content = content
        self.selectedRange = selectedRange
        self.links = links
        self.editable = editable
        super.init(with: rectInWebView, children: children)
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionScrollableItem) public class WKTextExtractionScrollableItem: WKTextExtractionItem {
    @objc public let contentSize: CGSize

    @objc public init(contentSize: CGSize, rectInWebView: CGRect, children: [WKTextExtractionItem]) {
        self.contentSize = contentSize
        super.init(with: rectInWebView, children: children)
    }
}

@available(iOS 17.0, macOS 12.0, *)
@objc(WKTextExtractionImageItem) public class WKTextExtractionImageItem: WKTextExtractionItem {
    @objc public let name: String
    @objc public let altText: String

    @objc public init(name: String, altText: String, rectInWebView: CGRect, children: [WKTextExtractionItem]) {
        self.name = name
        self.altText = altText
        super.init(with: rectInWebView, children: children)
    }
}

@objc(WKTextExtractionRequest) class WKTextExtractionRequest: NSObject {
    @objc public let rectInWebView: CGRect
    private var completionHandler: ((WKTextExtractionItem?) -> Void)?

    @objc public init(rectInWebView: CGRect, _ completionHandler: @escaping (WKTextExtractionItem?) -> Void) {
        self.rectInWebView = rectInWebView
        self.completionHandler = completionHandler
    }

    @objc(fulfill:) public func fulfill(item: WKTextExtractionItem) {
        guard let completionHandler = self.completionHandler else { return }
        completionHandler(item)
        self.completionHandler = nil
    }
}
"""

# The file the patch produces: one annotation line added above the WKTextExtractionRequest
# declaration. The patch's added lines are spliced in rather than retyped, so the fixture cannot
# drift from the patch the build applies.
REQUEST_DECLARATION = "@objc(WKTextExtractionRequest) class WKTextExtractionRequest: NSObject {"

# The same error one line later: the annotation is present but names a lower version than the
# type it references, which is what a hand-edited "17.0" -> "16.0" would produce.
LOWERED = UNPATCHED.replace(
    REQUEST_DECLARATION,
    "@available(iOS 16.0, macOS 12.0, *)\n" + REQUEST_DECLARATION,
)

# The error coming back through a second declaration: a ninth type that references
# WKTextExtractionItem and carries no annotation at all.
NEW_UNANNOTATED = UNPATCHED + """
@objc(WKTextExtractionBatch) class WKTextExtractionBatch: NSObject {
    @objc(collect:) public func collect(item: WKTextExtractionItem) {
        _ = item
    }
}
"""

# A file with no @objc declaration at all, so "pass" cannot mean "read nothing".
EMPTY = """// Copyright (C) 2024 Apple Inc. All rights reserved.

import Foundation
"""

failures: list[str] = []


def apply_hunk(text: str) -> str:
    """Apply the patch's single hunk to `text`, the way git apply would.

    The context lines decide where the insertion lands, so a patch whose context no longer
    matches the fixture must fail here rather than splice itself in at a guessed anchor. Only
    one hunk is supported, which is all this patch has; a second hunk is reported as a mismatch
    instead of being silently ignored.
    """
    hunk_lines: list[str] = []
    in_hunk = False
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@") or line.startswith("diff ") or line.startswith("--- ") or line.startswith("+++ "):
            if line.startswith("@@"):
                in_hunk = True
                hunk_lines.append(line)
            else:
                in_hunk = False
            continue
        if in_hunk and line[:1] in (" ", "+", "-"):
            hunk_lines.append(line)
    starts = [line for line in hunk_lines if line.startswith("@@")]
    if len(starts) != 1:
        raise ValueError(f"expected one hunk in {PATCH.name}, found {len(starts)}")

    old_start = int(re.match(r"@@ -(\d+)", starts[0]).group(1))
    # Split the hunk into the context before the addition, the added lines, and the context
    # after. The context is only verified against the fixture; re-inserting it would duplicate
    # lines the fixture already has.
    before: list[str] = []
    added: list[str] = []
    after: list[str] = []
    for line in hunk_lines:
        if line.startswith("@@"):
            continue
        marker, body = line[0], line[1:]
        if marker == "-":
            raise ValueError("this patch removes no line, so a removal means it changed shape")
        if marker == "+":
            added.append(body)
        elif added:
            after.append(body)
        else:
            before.append(body)
    if not added:
        raise ValueError("this patch adds no line, so it would not fix anything")

    # git apply counts both sides from the hunk header, so both context runs must match exactly.
    first = old_start - 1
    text_lines = text.splitlines()
    if text_lines[first:first + len(before)] != before:
        raise ValueError(
            "the patch context before the addition does not match the fixture:\n"
            f"  patch:   {before}\n"
            f"  fixture: {text_lines[first:first + len(before)]}"
        )
    after_start = first + len(before)
    if text_lines[after_start:after_start + len(after)] != after:
        raise ValueError(
            "the patch context after the addition does not match the fixture:\n"
            f"  patch:   {after}\n"
            f"  fixture: {text_lines[after_start:after_start + len(after)]}"
        )
    return "\n".join(text_lines[:after_start] + added + text_lines[after_start:]) + "\n"


def patched_text() -> str:
    """The fixture with the patch applied, rebuilt from the patch rather than from an anchor."""
    try:
        return apply_hunk(UNPATCHED)
    except ValueError as error:
        raise AssertionError(f"the patch does not apply to the fixture: {error}") from error


def run(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "WKTextExtractionItem.swift"
        path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--swift", str(path)],
            capture_output=True, text=True,
        )


def case(label: str, text: str, expected_status: int,
         expected_text: str | tuple[str, ...]) -> None:
    result = run(text)
    combined = result.stdout + result.stderr
    if result.returncode != expected_status:
        failures.append(
            f"{label}: exit {result.returncode}, expected {expected_status}\n{combined}")
        return
    expected = (expected_text,) if isinstance(expected_text, str) else expected_text
    for text_fragment in expected:
        if text_fragment not in combined:
            failures.append(f"{label}: {text_fragment!r} missing from output\n{combined}")
            return
    print(f"PASS {label}")


def case_missing_file() -> None:
    """A tag before Safari-18 has no such file, and a skip is the correct verdict there."""
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--swift", str(Path(directory) / "absent.swift")],
            capture_output=True, text=True,
        )
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        failures.append(f"a missing file must skip, exit {result.returncode}\n{combined}")
        return
    if "SKIP:" not in combined:
        failures.append(f"a missing file must report SKIP, got\n{combined}")
        return
    print("PASS a tag without the file is skipped")


def _first_difference(left: str, right: str) -> int:
    """Return the 1-based line number where two texts first differ, for a readable failure."""
    for index, (left_line, right_line) in enumerate(zip(left.splitlines(), right.splitlines()), 1):
        if left_line != right_line:
            return index
    return min(len(left.splitlines()), len(right.splitlines())) + 1


def case_patch_matches_fixture() -> None:
    """The patch must be the only change to the fixture, and it must be one added line."""
    added = [
        line[1:]
        for line in PATCH.read_text(encoding="utf-8").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    removed = [
        line[1:]
        for line in PATCH.read_text(encoding="utf-8").splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    if added != ["@available(iOS 17.0, macOS 12.0, *)"]:
        failures.append(f"the patch must add exactly the annotation line, adds {added}")
        return
    if removed:
        failures.append(f"the patch must not change any existing line, removes {removed}")
        return
    # patched_text() rebuilds the file from the patch's own hunk, so this compares the patch's
    # result against the real upstream file instead of against a second copy of the same anchor.
    try:
        spliced = patched_text()
    except AssertionError as error:
        failures.append(str(error))
        return
    expected = UNPATCHED.replace(
        REQUEST_DECLARATION, added[0] + "\n" + REQUEST_DECLARATION
    )
    if spliced != expected:
        failures.append(
            "the patch's hunk does not reproduce the annotated fixture; "
            f"first difference at line {_first_difference(spliced, expected)}"
        )
        return
    print("PASS the patch adds one annotation line and touches nothing else")


def main() -> int:
    # The wording of the failure is asserted, not only its exit code: the declaration the 16.1
    # build tripped on has no annotation, so a message claiming it "requires iOS 17.0" would
    # misdescribe the very case this patch fixes.
    case("the unpatched 18.3.1 file fails and names the three sites",
         UNPATCHED, 1,
         ("FAIL: WKTextExtractionRequest (line 124) references WKTextExtractionItem",
          "but WKTextExtractionRequest carries no @available annotation;",
          "9 top-level @objc declaration(s)"))
    try:
        applied = patched_text()
    except AssertionError as error:
        # A patch that no longer applies is the failure mode this case exists to catch, so it
        # joins the failure list instead of escaping as a traceback and hiding later cases.
        failures.append(str(error))
        applied = None
    if applied is not None:
        case("the patched 18.3.1 file passes",
             applied, 0,
             ("WKTextExtractionRequest (line 125): iOS 17.0, macOS 12.0  references: WKTextExtractionItem",
              "OK: all 9 available type(s)"))
    # The reverse direction has to fail for each way the missing annotation can come back, or a
    # green run says nothing about the patch.
    case("an annotation naming a lower version than the type it references fails",
         LOWERED, 1,
         ("WKTextExtractionRequest declares iOS 16.0, below 17.0",
          "WKTextExtractionRequest (line 125): iOS 16.0"))
    case("a second unannotated declaration referencing the type fails",
         NEW_UNANNOTATED, 1,
         ("FAIL: WKTextExtractionBatch",
          "but WKTextExtractionBatch carries no @available annotation;"))
    case("a file with no @objc declaration is not accepted as a pass",
         EMPTY, 1, "declares no @objc type")
    case_missing_file()
    case_patch_matches_fixture()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
