#!/usr/bin/env python3
"""Regression tests for scripts/package-webkit-device-deb.sh.

The cases pin the layout run 45117186601 shipped and the reason it was broken:

  the build product keeps the real libWebKitSwift.dylib at top level and puts a symlink to it at
  WebKit.framework/Frameworks/libWebKitSwift.dylib.  The packaging script copied WebKit.framework
  only, and libWebKitSwift.dylib was in none of its artifact lists, so the symlink had no target in
  the payload and STEP 2 deleted it ("Warning: removing broken symlink").  The package therefore
  carried no overlay, while WebKit opens it through the bundle-relative path
  "Frameworks/libWebKitSwift.dylib".

  STEP 4's exclusion and the stray-copy case guard the shape where a top-level copy would reach the
  payload; in run 45117186601 no such copy existed, so STEP 4 never swept one to usr/lib.

Each case writes a fixture product tree and runs the real script against it.  ditto and ldid are
stand-ins on Linux; dpkg-deb and zsh are the host's.  The cases that need a broken payload put a
cp stand-in first on PATH, so the script itself stays free of test switches.

Usage: python3 scripts/test-package-webkit-device-deb.py
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "package-webkit-device-deb.sh"
CP = shutil.which("cp") or "/bin/cp"

OVERLAY = "libWebKitSwift.dylib"
OVERLAY_REL = f"WebKit.framework/Frameworks/{OVERLAY}"

# A Mach-O 64-bit arm64 header with no load commands is enough for both file(1) implementations to
# call the file Mach-O, which is what makes the script treat it as a binary to sign.
MACHO_HEADER = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0)

failures: list[str] = []


def write_macho(path: Path, payload: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MACHO_HEADER + payload + b"\0" * 4096)


def build_product(root: Path, with_top_level_overlay: bool = True) -> Path:
    """Write the product tree the packaging script reads.

    The overlay follows the real build shape: a top-level real file plus a relative symlink inside
    WebKit.framework pointing back at it.
    """
    product = root / "WebKit" / "WebKitBuild" / "Release-iphoneos"

    write_macho(product / "WebKit.framework" / "WebKit", b"WebKit")
    (product / "WebKit.framework" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    write_macho(product / "WebCore.framework" / "WebCore", b"WebCore")
    write_macho(product / "WebKitLegacy.framework" / "WebKitLegacy", b"WebKitLegacy")
    write_macho(product / "JavaScriptCore.framework" / "JavaScriptCore", b"JavaScriptCore")
    write_macho(product / "libwebrtc.dylib", b"libwebrtc")
    write_macho(product / "libANGLE-shared.dylib", b"libANGLE-shared")

    frameworks = product / "WebKit.framework" / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    (frameworks / OVERLAY).symlink_to(f"../../{OVERLAY}")

    if with_top_level_overlay:
        write_macho(product / OVERLAY, b"swift-overlay")

    return product


def write_stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def write_shims(bin_dir: Path, cp_body: str | None = None) -> None:
    """Write ditto and ldid stand-ins.

    ditto copies recursively without following symlinks, which is how the real one behaves and why
    the framework's relative link survives into the payload.  ldid ad-hoc signs the Mach-O files
    and has no observable effect on the layout.  The optional cp body replaces the overlay copy,
    which is how the negative cases reintroduce a broken payload.
    """
    write_stand_in(bin_dir, "ditto", f'exec {CP} -R "$@"')
    write_stand_in(bin_dir, "ldid", "exit 0")
    write_stand_in(bin_dir, "cp", cp_body or f'exec {CP} "$@"')


def run_script(product: Path, output: Path, mode: str, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    command = ["zsh", str(SCRIPT), "--product", str(product), "--output", str(output)]
    if mode:
        command.append(mode)
    return subprocess.run(
        command, capture_output=True, text=True, env=environment,
    )


def unpack(deb: Path, destination: Path) -> None:
    subprocess.run(["dpkg-deb", "-x", str(deb), str(destination)], check=True)


def expect_layout(label: str, unpacked: Path, prefix: str, product: Path) -> None:
    frameworks = unpacked / prefix / "Library" / "Frameworks"
    usr_lib = unpacked / prefix / "usr" / "lib"

    overlay = frameworks / OVERLAY_REL
    if overlay.is_symlink():
        failures.append(f"{label}: {OVERLAY_REL} is a symlink, not a regular file")
    elif not overlay.is_file():
        failures.append(f"{label}: {OVERLAY_REL} is missing from the package")
    elif overlay.read_bytes() != (product / OVERLAY).read_bytes():
        failures.append(f"{label}: {OVERLAY_REL} is not a copy of the product's {OVERLAY}")

    for name in ("libwebrtc.dylib", "libANGLE-shared.dylib"):
        if not (usr_lib / name).is_file():
            failures.append(f"{label}: usr/lib/{name} is missing")
        if (frameworks / name).exists():
            failures.append(f"{label}: {name} was left in Library/Frameworks")
    for stray in (frameworks / OVERLAY, usr_lib / OVERLAY):
        if stray.exists() or stray.is_symlink():
            failures.append(f"{label}: stray {stray.relative_to(unpacked)}")


def case_shipped_layout(label: str, mode: str, prefix: str) -> None:
    before = len(failures)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, mode, bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "removing broken symlink" in combined:
            failures.append(f"{label}: the overlay link was deleted instead of kept\n{combined}")
            return
        if f"Verified {OVERLAY_REL} is a regular file" not in combined:
            failures.append(f"{label}: the payload assertion did not run\n{combined}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        expect_layout(label, unpacked, prefix, product)
    if len(failures) == before:
        print(f"PASS {label}")


def case_broken_payload(label: str, cp_body: str, expected_text: str) -> None:
    """A payload without a real overlay must stop the script, not ship a package missing it."""
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir, cp_body)
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the packaging should have failed\n{combined}")
            return
        if expected_text not in combined:
            failures.append(f"{label}: output lacks {expected_text!r}\n{combined}")
            return
        if deb.exists():
            failures.append(f"{label}: a package was written despite the failed check")
            return
    print(f"PASS {label}")


def case_stray_top_level_copy() -> None:
    """A top-level copy that reaches the payload must be dropped, not moved to usr/lib."""
    label = "a stray top-level overlay copy is dropped instead of moved to usr/lib"
    before = len(failures)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        # The overlay copy also leaves a sibling under Library/Frameworks, which is the top-level
        # shape STEP 4 sweeps.  Without the exclusion it lands in usr/lib, where WebKit never
        # looks for it.
        frameworks_dir = "${argument%/WebKit.framework/Frameworks/" + OVERLAY + "}"
        write_shims(bin_dir, f'''for argument in "$@"; do
  case "$argument" in
    */{OVERLAY_REL})
      {CP} -fL "$2" "$argument" || exit 1
      {CP} -fL "$2" "{frameworks_dir}/{OVERLAY}" || exit 1
      exit 0;;
  esac
done
exec {CP} "$@"''')
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "dropping the top-level copy" not in combined:
            failures.append(f"{label}: STEP 4 did not drop the top-level copy\n{combined}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        expect_layout(label, unpacked, "var/jb", product)
    if len(failures) == before:
        print(f"PASS {label}")


def case_absent_overlay() -> None:
    """A product without the overlay legitimately has none, and building must still succeed."""
    label = "a product without the overlay is packaged without one"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root, with_top_level_overlay=False)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        overlay = unpacked / "var/jb/Library/Frameworks" / OVERLAY_REL
        if overlay.exists() or overlay.is_symlink():
            failures.append(f"{label}: {OVERLAY_REL} should not be in the package")
            return
    print(f"PASS {label}")


def main() -> int:
    missing = [tool for tool in ("zsh", "dpkg-deb") if shutil.which(tool) is None]
    if missing:
        print(f"missing required tool(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    case_shipped_layout("a rootless package ships the overlay inside WebKit.framework",
                        "--rootless", "var/jb")
    case_shipped_layout("a rootful package ships the overlay inside WebKit.framework",
                        "", "")
    case_broken_payload(
        "a payload that leaves the overlay as a symlink fails",
        f'''for argument in "$@"; do
  case "$argument" in
    */{OVERLAY_REL}) ln -sf ../../{OVERLAY} "$argument"; exit 0;;
  esac
done
exec {CP} "$@"''',
        f"Payload has {OVERLAY_REL} as a symlink",
    )
    case_broken_payload(
        "a payload that drops the overlay fails",
        f'''for argument in "$@"; do case "$argument" in */{OVERLAY_REL}) exit 0;; esac; done
exec {CP} "$@"''',
        f"Payload is missing {OVERLAY_REL}",
    )
    case_absent_overlay()
    case_stray_top_level_copy()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
