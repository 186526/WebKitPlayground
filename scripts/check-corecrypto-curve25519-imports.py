#!/usr/bin/env python3
"""Fail when a shipped Mach-O image still imports an iOS 17 corecrypto symbol.

The device run that produced docs/device-install-plan.md stopped at

  dlopen(/var/jb/Library/Frameworks/WebCore.framework/WebCore, 0x000A):
  Symbol not found: _cccurve25519_make_key_pair

and, once that name was resolved, at _cccurve25519_make_pub. iOS 16.1's dyld shared cache
exports cccurve25519, ccrng, cced25519_* and ccsha512_di, but none of cccurve25519_make_priv,
cccurve25519_make_pub, cccurve25519_make_key_pair or ccrng_generate (checked there by dlsym
against RTLD_DEFAULT), so an image that imports one of them cannot load. WebCore imports all
four strongly, which is why -Wl,-U cannot weaken them back into a working binary.

patches/ios16-corecrypto-curve25519-inline.patch turns HAVE(CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE)
off at a 16.1 deployment target, which selects the header's inline cccurve25519_make_* definitions;
they call cccurve25519 and the ccrng_state function pointer, both of which iOS 16.1 has. This
script is the check that the patch actually produced that: it runs llvm-nm -u over every Mach-O
image it is given and fails on a match, printing the symbol and the image it came from.

Modes:
  --deb <path>        unpack with dpkg-deb -x and check every Mach-O image in the payload.
  --image <path>      check one file; repeats. A file that is not Mach-O is an error, so a
                      mistyped path cannot pass silently.
  --directory <dir>   walk a directory and check every Mach-O file in it.

Every architecture slice is checked: a fat binary is read with --arch all, and a slice llvm-nm
cannot read makes the run fail instead of being skipped, because an unread file is precisely the
one that hides the import.

Forbidden names are the four above, matched both with and without the Mach-O leading underscore.

Usage:
  scripts/check-corecrypto-curve25519-imports.py --deb packages/webkit-rootless.deb
  scripts/check-corecrypto-curve25519-imports.py --directory WebKitBuild/Release-iphoneos

The script reads .deb payloads through dpkg-deb, so a Linux host can check a package built on
macOS without a device. Regression tests build synthetic Mach-O objects and need no Xcode:
  python3 scripts/test-check-corecrypto-curve25519-imports.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_SYMBOLS = (
    "cccurve25519_make_priv",
    "cccurve25519_make_pub",
    "cccurve25519_make_key_pair",
    "ccrng_generate",
)

# Thin and fat Mach-O magic numbers, as the raw file bytes. A universal binary is read with
# --arch all, so the magic only decides whether the file is worth handing to llvm-nm.
MACH_O_MAGIC = {
    "feedface",
    "cefaedfe",
    "feedfacf",
    "cffaedfe",
    "cafebabe",
    "cafebabf",
    "bebafeca",
    "bfbafeca",
}


@dataclass(frozen=True)
class Import:
    image: str
    symbol: str


def find_tool(name: str) -> str:
    """Resolve a llvm tool, preferring the Xcode-provided one on macOS."""
    if shutil.which("xcrun"):
        found = subprocess.run(
            ["xcrun", "--find", name], capture_output=True, text=True, check=False
        )
        candidate = found.stdout.strip()
        if found.returncode == 0 and candidate:
            return candidate
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"Error: {name} not found in PATH; it is part of the LLVM tools")
    return resolved


def is_macho(path: Path) -> bool:
    try:
        magic = path.open("rb").read(4).hex()
    except OSError:
        return False
    return magic in MACH_O_MAGIC


def undo_symbol_prefix(name: str) -> str:
    """Strip Mach-O's leading underscore so one name table covers both spellings."""
    return name[1:] if name.startswith("_") else name


def imports_of(image: Path, nm: str) -> tuple[list[str], str]:
    """Return the undefined symbols of every slice, and llvm-nm's stderr on failure.

    --arch all is passed unconditionally: llvm-nm reads a thin image the same either way, and a
    later slice of a universal image must be checked, not just the first.
    """
    result = subprocess.run(
        [nm, "-u", "--arch", "all", str(image)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [], result.stderr.strip() or f"llvm-nm exited {result.returncode}"
    symbols = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return symbols, ""


def offending(symbols: list[str]) -> list[str]:
    forbidden = set(FORBIDDEN_SYMBOLS)
    return [name for name in (undo_symbol_prefix(s) for s in symbols) if name in forbidden]


def check_image(image: Path, label: str, nm: str) -> tuple[list[Import], str]:
    symbols, error = imports_of(image, nm)
    if error:
        return [], f"{label}: llvm-nm could not read the image ({error})"
    return [Import(label, name) for name in offending(symbols)], ""


def collect_from_directory(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and is_macho(path))


def collect_from_deb(deb: Path) -> tuple[Path, list[Path]]:
    """Unpack the payload and return the temporary root plus its Mach-O images."""
    dpkg_deb = shutil.which("dpkg-deb")
    if not dpkg_deb:
        raise SystemExit("Error: dpkg-deb not found in PATH; --deb needs it to unpack the package")
    unpack_dir = Path(tempfile.mkdtemp(prefix="corecrypto-imports-"))
    result = subprocess.run(
        [dpkg_deb, "-x", str(deb), str(unpack_dir)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        shutil.rmtree(unpack_dir, ignore_errors=True)
        raise SystemExit(f"Error: dpkg-deb could not unpack {deb}: {result.stderr.strip()}")
    return unpack_dir, collect_from_directory(unpack_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a Mach-O image imports an iOS 17 corecrypto curve25519 symbol."
    )
    parser.add_argument("--deb", type=Path, help="a .deb; its whole payload is checked")
    parser.add_argument(
        "--image", type=Path, action="append", default=[], help="a Mach-O file to check; repeats"
    )
    parser.add_argument("--directory", type=Path, help="a directory tree to check")
    args = parser.parse_args(argv)

    if not (args.deb or args.image or args.directory):
        parser.error("pass at least one of --deb, --image or --directory")

    nm = find_tool("llvm-nm")
    unpack_dir: Path | None = None
    images: list[tuple[Path, str]] = []
    errors: list[str] = []

    try:
        for image in args.image:
            if not image.is_file():
                errors.append(f"{image}: not a file")
            elif not is_macho(image):
                errors.append(f"{image}: not a Mach-O image")
            else:
                images.append((image, str(image)))
        if args.directory:
            if not args.directory.is_dir():
                errors.append(f"{args.directory}: not a directory")
            else:
                images.extend((p, str(p)) for p in collect_from_directory(args.directory))
        if args.deb:
            if not args.deb.is_file():
                errors.append(f"{args.deb}: not a file")
            else:
                unpack_dir, payload = collect_from_deb(args.deb)
                images.extend((p, str(p.relative_to(unpack_dir))) for p in payload)

        found: list[Import] = []
        for path, label in images:
            matches, error = check_image(path, label, nm)
            found.extend(matches)
            if error:
                errors.append(error)

        if not images and not errors:
            errors.append("no Mach-O image was found to check")

        for image in found:
            print(f"FORBIDDEN IMPORT: {image.symbol} in {image.image}", file=sys.stderr)
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)

        if found or errors:
            print(
                f"Result: FAIL ({len(found)} forbidden import(s), {len(errors)} unread image(s) "
                f"across {len(images)} checked)",
                file=sys.stderr,
            )
            return 1
    finally:
        if unpack_dir is not None:
            shutil.rmtree(unpack_dir, ignore_errors=True)

    print(
        f"Result: PASS ({len(images)} checked, none imports "
        + ", ".join(FORBIDDEN_SYMBOLS)
        + ")"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
