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
each of the three files. This script checks identifiers and the CFBundleExecutable of each
packaged .xpc bundle. It requires the four service names and rejects a suffix, directory/identifier
mismatch or missing executable Mach-O file. It reads a .deb
directly (ar + data.tar, without dpkg-deb) so CI can check the artifact it just built, and an
already-unpacked payload tree for local work; both modes apply the same rules.

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
import struct
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

THIN_MACHO = {bytes.fromhex(magic): (endian, size) for magic, endian, size in (
    ("feedface", ">", 28), ("cefaedfe", "<", 28), ("feedfacf", ">", 32), ("cffaedfe", "<", 32),
)}
FAT_MACHO = {bytes.fromhex(magic): (endian, entry) for magic, endian, entry in (
    ("cafebabe", ">", "5I"), ("bebafeca", "<", "5I"),
    ("cafebabf", ">", "2I2Q2I"), ("bfbafeca", "<", "2I2Q2I"),
)}
MH_EXECUTE = 2


def executable_header(header: bytes) -> bool:
    format_info = THIN_MACHO.get(header[:4])
    if format_info is None:
        return False
    endian, size = format_info
    return len(header) >= size and struct.unpack_from(endian + "I", header, 12)[0] == MH_EXECUTE


def executable_image(reader: BinaryIO) -> bool:
    magic = reader.read(4)
    if magic not in FAT_MACHO:
        return executable_header(magic + reader.read(28))
    endian, layout = FAT_MACHO[magic]
    count_bytes = reader.read(4)
    if len(count_bytes) != 4:
        return False
    count, = struct.unpack(endian + "I", count_bytes)
    entry = struct.Struct(endian + layout)
    image_size = reader.seek(0, io.SEEK_END)
    table_end = 8 + count * entry.size
    if count == 0 or table_end > image_size:
        return False
    for index in range(count):
        reader.seek(8 + index * entry.size)
        _, _, offset, size, *_ = entry.unpack(reader.read(entry.size))
        if offset < table_end or size < 28 or offset + size > image_size:
            return False
        reader.seek(offset)
        if not executable_header(reader.read(min(size, 32))):
            return False
    return True

# These are the four names ProcessLauncherCocoa.mm:190-212 asks for on iOS 16.1.
REQUIRED_IDENTIFIERS = (
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


@dataclass(frozen=True)
class Bundle:
    plist: bytes
    members: dict[str, bool]


def bundles_from_tar(tar: tarfile.TarFile, rootless: bool) -> dict[str, Bundle]:
    framework = PurePosixPath("var/jb/Library/Frameworks") / FRAMEWORK / CONTAINER
    groups: dict[PurePosixPath, dict[str, tarfile.TarInfo]] = {}
    for member in tar:
        path = PurePosixPath(member.name)
        if not path.name or bundle_parts((path.parent / PLIST_NAME).as_posix()) is None:
            continue
        if rootless and path.parent.parent != framework:
            raise ValueError(f"unexpected XPC bundle path: {path.parent} (expected under {framework})")
        group = groups.setdefault(path.parent, {})
        if path.name in group:
            raise ValueError(f"duplicate archive member: {path}")
        group[path.name] = member

    bundles: dict[str, Bundle] = {}
    for directory, group in groups.items():
        stem = directory.stem
        if stem in bundles:
            raise ValueError(f"two {stem}{XPC_SUFFIX} bundles in the package")
        plist = group.get(PLIST_NAME)
        if plist is None or not plist.isfile():
            raise ValueError(f"{directory}: missing regular {PLIST_NAME}")
        with tar.extractfile(plist) as reader:
            contents = reader.read()
        members = {}
        for name, member in group.items():
            if member.isfile() and member.mode & 0o111:
                with tar.extractfile(member) as reader:
                    members[name] = executable_image(reader)
        bundles[stem] = Bundle(contents, members)
    return bundles


def bundles_from_directory(root: Path) -> dict[str, Bundle]:
    bundles: dict[str, Bundle] = {}
    for directory in sorted(root.rglob(f"*{XPC_SUFFIX}")):
        stem = bundle_parts((directory / PLIST_NAME).as_posix())
        if stem is None:
            continue
        if stem in bundles:
            raise ValueError(f"two {stem}{XPC_SUFFIX} bundles under {root}")
        plist = directory / PLIST_NAME
        if directory.is_symlink() or not plist.is_file() or plist.is_symlink():
            raise ValueError(f"{directory}: missing regular {PLIST_NAME}")
        members = {}
        for candidate in directory.iterdir():
            if not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_mode & 0o111:
                with candidate.open("rb") as reader:
                    members[candidate.name] = executable_image(reader)
        bundles[stem] = Bundle(plist.read_bytes(), members)
    return bundles


def bundle_metadata(bundle: str, plist: bytes) -> tuple[str, str]:
    try:
        parsed = plistlib.loads(plist)
    except Exception as error:  # plistlib raises several unrelated parser errors
        raise ValueError(f"{bundle}{XPC_SUFFIX}: {PLIST_NAME} is not a readable plist ({error})") from error
    value = parsed.get("CFBundleIdentifier") if isinstance(parsed, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{bundle}{XPC_SUFFIX}: {PLIST_NAME} has no CFBundleIdentifier")
    executable = parsed.get("CFBundleExecutable")
    if (not isinstance(executable, str) or not executable.strip()
            or executable in {".", ".."} or "/" in executable or "\0" in executable):
        raise ValueError(f"{bundle}{XPC_SUFFIX}: {PLIST_NAME} has no valid CFBundleExecutable basename")
    return value.strip(), executable


def check(bundles: dict[str, Bundle]) -> tuple[list[str], list[str], list[str]]:
    """Return the per-bundle table rows, the failures and the notes, in that order.

    Three things make a package unusable on the device. The .Service suffix is what the 18.5 SDK
    appends for a pre-17.0 deployment target, and no caller asks for it. A directory name that
    disagrees with its own CFBundleIdentifier is fatal on its own: the system enumerates the
    directory while the caller asks for the identifier. A required bundle that is absent is also
    fatal: ProcessLauncherCocoa.mm:190-212 asks for each of the four by name, so a package that
    dropped one ships nothing the corresponding call can launch.

    An optional bundle may be absent, but a shipped bundle must also carry its executable.
    """
    rows: list[str] = []
    failures: list[str] = []
    notes: list[str] = []
    for bundle in sorted(bundles):
        contents = bundles[bundle]
        try:
            value, executable = bundle_metadata(bundle, contents.plist)
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
        if executable not in contents.members:
            failures.append(f"{bundle}{XPC_SUFFIX}: missing executable {executable} (expected an executable regular file)")
            verdict = "missing executable"
        elif not contents.members[executable]:
            failures.append(f"{bundle}{XPC_SUFFIX}: {executable} is not a Mach-O executable")
            verdict = "not Mach-O"
        if bundle not in REQUIRED_IDENTIFIERS and verdict == "ok":
            notes.append(f"{bundle}{XPC_SUFFIX}: not one of the four required names")
        rows.append(f"  {bundle}{XPC_SUFFIX}: {value}  [{verdict}]")

    for name in REQUIRED_IDENTIFIERS:
        if name not in bundles:
            # One failure per name so the log names every one that is gone, not just a count.
            # This is the case a SKIP_INSTALL change would produce: the bundle is simply not in
            # the package, and the caller that asks for it has nothing to launch.
            failures.append(
                f"{name}{XPC_SUFFIX} is missing from this package; ProcessLauncherCocoa.mm:190-212 "
                f"asks for {name} by name, so nothing would answer that call")
    return rows, failures, notes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that every shipped XPC bundle carries an identifier iOS asks for.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--deb", type=Path, help="packaged .deb to read")
    source.add_argument("--root", type=Path, help="already-unpacked payload tree to read")
    parser.add_argument("--rootless", action="store_true", help="require the rootless archive layout with --deb")
    args = parser.parse_args()
    if args.rootless and args.deb is None:
        parser.error("--rootless requires --deb")
    return args


def main() -> int:
    args = parse_args()
    if args.deb is not None:
        described = str(args.deb)
        if not args.deb.is_file():
            print(f"FAIL: {described} does not exist", file=sys.stderr)
            return 1
        try:
            with open_data_tar(args.deb.read_bytes()) as tar:
                bundles = bundles_from_tar(tar, args.rootless)
        except (ValueError, OSError, EOFError, tarfile.TarError) as error:
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
        # accepting it would report success for a build that cannot work. The names go in the
        # message because "no bundle at all" is the extreme of the missing-required case.
        print(f"FAIL: {described} ships no {FRAMEWORK}/{CONTAINER}/<name>{XPC_SUFFIX}/{PLIST_NAME}, "
              f"so there is nothing to check and no process this package could replace; all four "
              f"of {', '.join(REQUIRED_IDENTIFIERS)} are missing", file=sys.stderr)
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
    print(f"OK: all {len(REQUIRED_IDENTIFIERS)} required bundle(s) are present and carry an "
          f"identifier iOS asks for ({len(bundles)} shipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
