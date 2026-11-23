#!/usr/bin/env python3
"""Fail a packaged WebKit deb whose XPC services are named something iOS never asks for.

The XPC service names the WebKit UI process dials are hardcoded literals with no suffix.
Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm at tag
Safari-18.3.1-iOS-18.3.2:190-212 returns "com.apple.WebKit.WebContent" (or
"com.apple.WebKit.WebContent.CaptivePortal" under Lockdown Mode), "com.apple.WebKit.Networking"
and "com.apple.WebKit.GPU". That file holds no ".Service" string anywhere.

The identifiers our XPC bundles register under come from
Source/WebKit/Configurations/{WebContentService,NetworkService,GPUService}.xcconfig, which gate
them on the SDK version:

  PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*] = $(PRODUCT_NAME).Service;   // :33 / :28 / :28
  PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*17.0*] = $(PRODUCT_NAME);
  ... 17.1, 17.2, 17.3 ...

`sdk=iphone*` matches every iOS SDK, so those four lines assume the SDK version tracks the
deployment target. Building the 18.5 SDK at a 16.1 deployment target breaks that assumption and
takes the first line: the deb ships com.apple.WebKit.WebContent.Service.xpc while every WebKit
main process asks for com.apple.WebKit.WebContent, so our WebContent is never selected and every
fix underneath it stays invisible.

patches/ios16-xpc-service-bundle-identifier.patch drops the suffix from that one line in
each of the three files. This script reads the CFBundleIdentifier of every .xpc the package
ships and fails when an identifier carries the suffix or disagrees with its own directory name.
It reads a .deb directly (ar + data.tar, without dpkg-deb) so CI can check the artifact it just
built, and an already-unpacked payload tree for local work.

Usage:
  scripts/check-xpc-service-bundle-identifiers.py --deb packages/webkit-rootless.deb
  scripts/check-xpc-service-bundle-identifiers.py --root /tmp/unpacked

Regression tests (stub packages, no build artifact and no device needed):
  python3 scripts/test-check-xpc-service-bundle-identifiers.py
"""
from __future__ import annotations

import argparse
import io
import plistlib
import sys
import tarfile
from pathlib import Path

# The four XPC services /System/Library/Frameworks/WebKit.framework/XPCServices holds on
# iOS 16.1, listed from the device over ssh. Only these four are expected to agree with the
# device; a package may also ship bundles the device does not have, and those are reported
# without failing. See check() for why .Development is one of them.
SYSTEM_IDENTIFIERS = (
    "com.apple.WebKit.GPU",
    "com.apple.WebKit.Networking",
    "com.apple.WebKit.WebContent",
    "com.apple.WebKit.WebContent.CaptivePortal",
)

SUFFIX = ".Service"
PLIST_NAME = "Info.plist"
XPC_SUFFIX = ".xpc"
FRAMEWORK = "WebKit.framework"
CONTAINER = "XPCServices"


def ar_members(archive: bytes) -> dict[str, bytes]:
    """Return the members of an ar archive by name.

    dpkg-deb writes a plain System V ar archive, so the fixed 60-byte headers are enough. Names
    carry a trailing "/" in that format; a BSD-style "#1/<len>" name is not something dpkg-deb
    emits, so such a member is skipped rather than guessed at.
    """
    magic = b"!<arch>\n"
    if not archive.startswith(magic):
        raise ValueError("not an ar archive: the file does not start with !<arch>")
    members: dict[str, bytes] = {}
    offset = len(magic)
    while offset + 60 <= len(archive):
        header = archive[offset:offset + 60]
        if header[58:60] != b"`\n":
            break
        name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii", "replace").strip())
        except ValueError:
            break
        body_start = offset + 60
        if name and not name.startswith("#1/"):
            members[name] = archive[body_start:body_start + size]
        # Every member is padded to an even offset, and the pad byte is not counted in size.
        offset = body_start + size + (size % 2)
    return members


def open_data_tar(archive: bytes) -> tarfile.TarFile:
    """Open the data.tar member of a .deb, whatever compression dpkg-deb used.

    `--build -Zgzip` is what scripts/package-webkit-device-deb.sh passes, but a deb built by hand
    may hold xz, bzip2 or an uncompressed data.tar, and tarfile's "r:*" detects all four.
    """
    members = ar_members(archive)
    for name in sorted(members):
        if name.startswith("data.tar"):
            return tarfile.open(fileobj=io.BytesIO(members[name]), mode="r:*")
    raise ValueError(f"no data.tar member in the archive; members: {', '.join(sorted(members))}")


def bundle_parts(path: str) -> str | None:
    """Return the XPC bundle stem of an Info.plist path, or None when the path is not one.

    Only <anywhere>/WebKit.framework/XPCServices/<stem>.xpc/Info.plist counts. That is the
    layout scripts/package-webkit-device-deb.sh installs and the layout the device has, so a
    plist copied anywhere else describes a bundle iOS never looks at and is not evidence.
    """
    parts = path.split("/")
    # <anything>/WebKit.framework/XPCServices/<stem>.xpc/Info.plist
    if len(parts) < 4 or parts[-1] != PLIST_NAME or parts[-3] != CONTAINER or parts[-4] != FRAMEWORK:
        return None
    bundle = parts[-2]
    return bundle[:-len(XPC_SUFFIX)] if bundle.endswith(XPC_SUFFIX) else None


def bundles_from_tar(tar: tarfile.TarFile) -> dict[str, bytes]:
    """Map each XPC bundle's directory stem to its Info.plist bytes."""
    bundles: dict[str, bytes] = {}
    for member in tar.getmembers():
        if not member.isfile():
            continue
        stem = bundle_parts(member.name)
        if stem is None:
            continue
        if stem in bundles:
            raise ValueError(f"two {stem}{XPC_SUFFIX} bundles in the package; one identifier "
                             f"cannot describe both")
        extracted = tar.extractfile(member)
        if extracted is not None:
            bundles[stem] = extracted.read()
    return bundles


def bundles_from_directory(root: Path) -> dict[str, bytes]:
    """Read the same bundle layout from an unpacked payload tree."""
    bundles: dict[str, bytes] = {}
    for plist in sorted(root.rglob(PLIST_NAME)):
        stem = bundle_parts(plist.as_posix())
        if stem is None:
            continue
        if stem in bundles:
            raise ValueError(f"two {stem}{XPC_SUFFIX} bundles under {root}")
        bundles[stem] = plist.read_bytes()
    return bundles


def identifier(bundle: str, plist: bytes) -> str:
    """Return the CFBundleIdentifier a bundle claims, or raise ValueError explaining why not."""
    try:
        parsed = plistlib.loads(plist)
    except Exception as error:  # plistlib raises several unrelated parser errors
        raise ValueError(f"{bundle}{XPC_SUFFIX}: {PLIST_NAME} is not a readable plist ({error})") from error
    value = parsed.get("CFBundleIdentifier") if isinstance(parsed, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{bundle}{XPC_SUFFIX}: {PLIST_NAME} has no CFBundleIdentifier")
    return value.strip()


def check(bundles: dict[str, bytes]) -> tuple[list[str], list[str], list[str]]:
    """Return the per-bundle table rows, the failures and the notes, in that order.

    Two things make an identifier unusable on the device. The .Service suffix is what the 18.5
    SDK appends for a pre-17.0 deployment target, and no caller asks for it. A directory name
    that disagrees with its own CFBundleIdentifier is also fatal on its own: the system
    enumerates the directory while the caller asks for the identifier.

    Absence is not a failure here. scripts/package-webkit-device-deb.sh owns which bundles are
    required, and its lists already vary by tag, so a missing bundle is reported as a note.
    """
    rows: list[str] = []
    failures: list[str] = []
    notes: list[str] = []
    for bundle in sorted(bundles):
        try:
            value = identifier(bundle, bundles[bundle])
        except ValueError as error:
            failures.append(str(error))
            continue
        verdict = "ok"
        if value.endswith(SUFFIX):
            failures.append(
                f"{bundle}{XPC_SUFFIX}: CFBundleIdentifier is {value}; the {SUFFIX} suffix is what "
                f"the 18.5 SDK appends at a pre-17.0 deployment target and no WebKit process asks "
                f"for that name (ProcessLauncherCocoa.mm:190-212 has no {SUFFIX} literal)")
            verdict = "suffix"
        if value != bundle:
            failures.append(
                f"{bundle}{XPC_SUFFIX}: directory says {bundle} but CFBundleIdentifier says {value}")
            verdict = "mismatch"
        if bundle not in SYSTEM_IDENTIFIERS and verdict == "ok":
            # Only reached by a bundle that is correctly named for a bundle the device does not
            # have. .Development is the case: iOS asks for it only when the UI process sets
            # nonValidInjectedCodeAllowed, which a shipped Safari does not, so it is reported
            # rather than failed. A wrongly named bundle is already in `failures`.
            notes.append(f"{bundle}{XPC_SUFFIX}: no bundle of this name in the device's "
                         f"{CONTAINER} directory; only the suffix was checked")
        rows.append(f"  {bundle}{XPC_SUFFIX}: {value}  [{verdict}]")

    missing = [name for name in SYSTEM_IDENTIFIERS if name not in bundles]
    if missing:
        # One line rather than one per name: which bundles a package must ship is decided by
        # scripts/package-webkit-device-deb.sh, whose lists differ per tag.
        notes.append(f"absent from this package: {', '.join(missing)}; the required-artifact "
                     f"lists live in scripts/package-webkit-device-deb.sh")
    return rows, failures, notes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that every shipped XPC bundle carries an identifier iOS asks for.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--deb", type=Path, help="packaged .deb to read")
    source.add_argument("--root", type=Path, help="already-unpacked payload tree to read")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.deb is not None:
        described = str(args.deb)
        if not args.deb.is_file():
            print(f"FAIL: {described} does not exist", file=sys.stderr)
            return 1
        try:
            with open_data_tar(args.deb.read_bytes()) as tar:
                bundles = bundles_from_tar(tar)
        except (ValueError, tarfile.TarError) as error:
            print(f"FAIL: cannot read {described}: {error}", file=sys.stderr)
            return 1
    else:
        described = str(args.root)
        if not args.root.is_dir():
            print(f"FAIL: {described} is not a directory", file=sys.stderr)
            return 1
        try:
            bundles = bundles_from_directory(args.root)
        except ValueError as error:
            print(f"FAIL: cannot read {described}: {error}", file=sys.stderr)
            return 1

    if not bundles:
        # A package with no XPC service replaces no WebContent, Networking or GPU process, so
        # accepting it would report success for a build that cannot work.
        print(f"FAIL: {described} ships no {FRAMEWORK}/{CONTAINER}/<name>{XPC_SUFFIX}/{PLIST_NAME}, "
              f"so there is nothing to check and no process this package could replace",
              file=sys.stderr)
        return 1

    rows, failures, notes = check(bundles)
    print(f"{described}: {len(bundles)} XPC bundle(s) under {FRAMEWORK}/{CONTAINER}")
    for row in rows:
        print(row)
    for note in notes:
        print(f"NOTE: {note}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: all {len(bundles)} bundle(s) carry an identifier iOS asks for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
