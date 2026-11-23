#!/usr/bin/env python3
"""Regression tests for scripts/check-xpc-service-bundle-identifiers.py.

Both directions are pinned, because a checker that only ever passes proves nothing:

  a package whose bundles carry the .Service suffix must FAIL, which is what the 18.5 SDK
  produces at a 16.1 deployment target and what the deb on the device was built with;
  a package whose bundles carry the device's own names must PASS;
  and the checker must keep failing for the other ways a wrong identifier can ship.

Every package here is built in-process from python's own ar and tarfile writers, so the cases
need no WebKit checkout, no dpkg-deb, no build artifact and no device.

Usage: python3 scripts/test-check-xpc-service-bundle-identifiers.py
"""
from __future__ import annotations

import gzip
import io
import lzma
import bz2
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check-xpc-service-bundle-identifiers.py"
PATCH = SCRIPTS.parent / "patches" / "ios16-xpc-service-bundle-identifier.patch"

FRAMEWORK_DIR = "var/jb/Library/Frameworks/WebKit.framework"
XPCSERVICES = f"{FRAMEWORK_DIR}/XPCServices"

# The four bundles iOS 16.1 has, with the identifiers its own Info.plists declare.
DEVICE_NAMES = (
    "com.apple.WebKit.GPU",
    "com.apple.WebKit.Networking",
    "com.apple.WebKit.WebContent",
    "com.apple.WebKit.WebContent.CaptivePortal",
)

# What the 18.5 SDK at a 16.1 deployment target puts in the package, measured on the device.
SUFFIXED_NAMES = tuple(f"{name}.Service" for name in DEVICE_NAMES)

failures: list[str] = []


def plist_bytes(identifier: str | None) -> bytes:
    """Return an Info.plist as Xcode writes it, or one with no CFBundleIdentifier."""
    payload: dict[str, object] = {"CFBundleExecutable": identifier or "service"}
    if identifier is not None:
        payload["CFBundleIdentifier"] = identifier
    return plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)


def ar_member(name: str, body: bytes) -> bytes:
    """Encode one System V ar member the way dpkg-deb writes it.

    The name is left-padded to 16 bytes and terminated with a "/"; size and the other fields are
    space-padded. A body of odd length is followed by a newline that the size field excludes.
    """
    header = (
        f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(body):<10}`\n".encode("ascii")
    )
    if len(header) != 60:
        raise AssertionError(f"ar header for {name} is {len(header)} bytes, not 60")
    return header + body + (b"\n" if len(body) % 2 else b"")


def compress(body: bytes, compression: str) -> bytes:
    if compression == "gz":
        return gzip.compress(body, mtime=0)
    if compression == "xz":
        return lzma.compress(body)
    if compression == "bz2":
        return bz2.compress(body)
    if compression == "none":
        return body
    raise AssertionError(f"unsupported compression {compression}")


def write_deb(path: Path, files: dict[str, bytes], compression: str = "gz") -> None:
    """Write a deb holding `files` in its data.tar, plus the control member dpkg-deb requires."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
    control = b"Package: com.example.stub\nVersion: 1.0\nArchitecture: iphoneos-arm64\n"
    path.write_bytes(
        b"!<arch>\n"
        + ar_member("debian-binary", b"2.0\n")
        + ar_member("control.tar.gz", gzip.compress(control, mtime=0))
        + ar_member(f"data.tar.{compression}", compress(data.getvalue(), compression))
    )


def bundle_files(names: tuple[str, ...],
                 identifiers: dict[str, str] | None = None,
                 renamed: dict[str, str] | None = None) -> dict[str, bytes]:
    """Lay out one .xpc per name, with its Info.plist and an executable, in the installed shape.

    `identifiers` overrides what a bundle's plist declares; `renamed` renames the directory, so a
    case can make the two disagree.
    """
    files: dict[str, bytes] = {}
    for name in names:
        directory = (renamed or {}).get(name, name)
        files[f"{XPCSERVICES}/{directory}.xpc/{directory}"] = b"\xcf\xfa\xed\xfe"  # a Mach-O magic
        files[f"{XPCSERVICES}/{directory}.xpc/Info.plist"] = plist_bytes(
            (identifiers or {}).get(name, name))
    return files


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def case_result(label: str, result: subprocess.CompletedProcess[str], expected_status: int,
                expected: str | tuple[str, ...]) -> None:
    combined = result.stdout + result.stderr
    if result.returncode != expected_status:
        failures.append(f"{label}: exit {result.returncode}, expected {expected_status}\n{combined}")
        return
    wanted = (expected,) if isinstance(expected, str) else expected
    for fragment in wanted:
        if fragment not in combined:
            failures.append(f"{label}: {fragment!r} missing from output\n{combined}")
            return
    print(f"PASS {label}")


def case_deb(label: str, files: dict[str, bytes], expected_status: int,
             expected: str | tuple[str, ...], compression: str = "gz") -> None:
    with tempfile.TemporaryDirectory() as work:
        deb = Path(work) / "webkit.deb"
        try:
            write_deb(deb, files, compression)
        except AssertionError as error:
            failures.append(f"{label}: {error}")
            return
        case_result(label, run([sys.executable, str(SCRIPT), "--deb", str(deb)]),
                    expected_status, expected)


def case_deb_missing(label: str) -> None:
    missing = Path(tempfile.gettempdir()) / "webkit-nonexistent-8a3c.deb"
    case_result(label, run([sys.executable, str(SCRIPT), "--deb", str(missing)]), 1, "does not exist")


def case_not_an_archive(label: str) -> None:
    """A truncated or non-ar file must report the read failure, not silently pass."""
    with tempfile.TemporaryDirectory() as work:
        bogus = Path(work) / "webkit.deb"
        bogus.write_bytes(b"not an ar archive at all")
        case_result(label, run([sys.executable, str(SCRIPT), "--deb", str(bogus)]), 1,
                    "not an ar archive")


def case_root_directory() -> None:
    """The same layout read from an unpacked tree, which is how a local repro is checked."""
    with tempfile.TemporaryDirectory() as work:
        root = Path(work) / "unpacked"
        for name, body in bundle_files(DEVICE_NAMES).items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
        case_result("the unpacked device layout passes via --root",
                    run([sys.executable, str(SCRIPT), "--root", str(root)]), 0,
                    ("4 XPC bundle(s)", "OK: all 4 bundle(s)"))


def case_compression() -> None:
    """dpkg-deb is told -Zgzip here, but a hand-built deb may use any of these."""
    for compression in ("xz", "bz2", "none"):
        case_deb(f"a {compression} data.tar is read",
                 bundle_files(SUFFIXED_NAMES), 1, "com.apple.WebKit.WebContent.Service",
                 compression=compression)


def case_patch_produces_the_device_names() -> None:
    """The patch must remove the suffix from all three files and add nothing else.

    This ties the two deliverables together: the names the checker expects are the names the
    patch makes the build produce, and a patch that stopped removing the suffix would leave the
    checker passing while the device still never selected our service.
    """
    try:
        text = PATCH.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {PATCH}: {error}")
        return
    removed = [line[1:] for line in text.splitlines()
               if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in text.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    expected_removed = "PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*] = $(PRODUCT_NAME).Service;"
    expected_added = "PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*] = $(PRODUCT_NAME);"
    if removed != [expected_removed] * 3:
        failures.append(f"the patch must drop the suffix from three files, removes {removed}")
        return
    if added != [expected_added] * 3:
        failures.append(f"the patch must add the suffix-free line three times, adds {added}")
        return
    files = re.findall(r"^\+\+\+ b/(\S+)", text, re.MULTILINE)
    wanted = [
        "Source/WebKit/Configurations/GPUService.xcconfig",
        "Source/WebKit/Configurations/NetworkService.xcconfig",
        "Source/WebKit/Configurations/WebContentService.xcconfig",
    ]
    if files != wanted:
        failures.append(f"the patch must touch the three iPhone service configs, touches {files}")
        return
    print("PASS the patch drops the suffix from exactly the three service configs")


def main() -> int:
    # The shipped state: the 18.5 SDK suffixes all four bundles. This is the package that was
    # measured on the device, so it is the direction that must fail.
    case_deb("a package named with the .Service suffix fails",
             bundle_files(SUFFIXED_NAMES), 1,
             ("com.apple.WebKit.WebContent.Service.xpc: CFBundleIdentifier is "
              "com.apple.WebKit.WebContent.Service",
              "no WebKit process asks for that name",
              "com.apple.WebKit.GPU.Service",
              "com.apple.WebKit.Networking.Service",
              "com.apple.WebKit.WebContent.CaptivePortal.Service"))

    # The state the patch produces: the four names the device's own Info.plists declare.
    case_deb("a package named the way the device is passes",
             bundle_files(DEVICE_NAMES), 0,
             ("4 XPC bundle(s)", "com.apple.WebKit.WebContent.xpc: com.apple.WebKit.WebContent  [ok]",
              "OK: all 4 bundle(s)"))

    # A package that also ships the Development bundle. iOS 16.1 has no such bundle and asks
    # for it only when nonValidInjectedCodeAllowed is set, so it is reported, not failed.
    case_deb("an extra Development bundle is reported but does not fail",
             bundle_files(DEVICE_NAMES + ("com.apple.WebKit.WebContent.Development",)), 0,
             ("NOTE: com.apple.WebKit.WebContent.Development.xpc", "OK: all 5 bundle(s)"))

    # The reverse direction for the other ways a wrong identifier can ship.
    case_deb("a directory whose identifier disagrees with it fails",
             bundle_files(("com.apple.WebKit.WebContent", "com.apple.WebKit.GPU"),
                          identifiers={"com.apple.WebKit.GPU": "com.apple.WebKit.Graphics"}),
             1, "directory says com.apple.WebKit.GPU but CFBundleIdentifier says "
                "com.apple.WebKit.Graphics")
    case_deb("a bundle with no CFBundleIdentifier fails",
             {f"{XPCSERVICES}/com.apple.WebKit.WebContent.xpc/Info.plist": plist_bytes(None)},
             1, "has no CFBundleIdentifier")
    case_deb("an unreadable Info.plist fails",
             {f"{XPCSERVICES}/com.apple.WebKit.WebContent.xpc/Info.plist": b"<not-a-plist"},
             1, "is not a readable plist")
    case_deb("a package with no XPC bundle fails",
             {"var/jb/Library/Frameworks/WebKit.framework/WebKit": b"\xcf\xfa\xed\xfe"},
             1, "ships no WebKit.framework/XPCServices/")
    case_deb("a plist outside XPCServices is not evidence",
             {"var/jb/Library/Frameworks/WebKit.framework/Info.plist": plist_bytes(
                 "com.apple.WebKit.WebContent.Service")},
             1, "ships no WebKit.framework/XPCServices/")

    case_compression()
    case_root_directory()
    case_deb_missing("a missing deb fails")
    case_not_an_archive("a file that is not an ar archive fails")
    case_patch_produces_the_device_names()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
