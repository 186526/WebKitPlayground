#!/usr/bin/env python3
"""Regression tests for scripts/check-corecrypto-curve25519-imports.py.

The checker is the last line of the corecrypto fix: whatever the build produced, the shipped
Mach-O images must not import cccurve25519_make_priv, cccurve25519_make_pub,
cccurve25519_make_key_pair or ccrng_generate, because iOS 16.1's dyld shared cache exports none of
them and WebCore then fails to dlopen.

Every case writes its own Mach-O object by hand, so nothing here needs Xcode, clang or a WebKit
build; llvm-nm is the only tool required. make_object() emits an MH_OBJECT with an LC_SYMTAB whose
undefined external symbols are the names a test asks for, which is exactly what llvm-nm -u reads.

Cases:
  clean object                     passes and names all four rejected symbols in its PASS line
  one forbidden import             fails and names the symbol and its image
  all four forbidden imports       fails on every one of them
  already-prefixed names           the raw "_cccurve25519_..." spelling is matched too
  a disallowed lookalike           cccurve25519 and cced25519_make_pub stay allowed
  non-Mach-O input                 is an error, not a silent pass
  no input selected                is an error, not a silent pass
  a directory tree                 is walked, and one bad image in it fails the run
  a .deb payload                   is unpacked and checked, skipped without dpkg-deb

Usage: python3 scripts/test-check-corecrypto-curve25519-imports.py
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-corecrypto-curve25519-imports.py"

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_SYMTAB = 0x2
N_UNDF = 0x0
N_EXT = 0x1
NLIST_64_SIZE = 16
SYMTAB_CMD_SIZE = 24


def make_object(path: Path, symbol_names: list[str]) -> None:
    """Write a minimal arm64 MH_OBJECT whose undefined externals are `symbol_names`.

    Layout: mach_header_64 (32 bytes), LC_SYMTAB (24), nlist_64 entries, then the string table.
    llvm-nm -u reports every nlist with N_UNDF|N_EXT, which is the state that makes dyld fail.
    """
    names = [name.encode() for name in symbol_names]
    string_table = b"\x00"
    offsets = []
    for name in names:
        offsets.append(len(string_table))
        string_table += name + b"\x00"
    nlists = b"".join(
        struct.pack("<IBBHQ", offset, N_UNDF | N_EXT, 0, 0, 0) for offset in offsets
    )
    symoff = 32 + SYMTAB_CMD_SIZE
    stroff = symoff + len(nlists)
    header = struct.pack(
        "<IIIIIIII", MH_MAGIC_64, CPU_TYPE_ARM64, 0, 1, 1, SYMTAB_CMD_SIZE, 0, 0
    )
    # nsyms is the number of nlist_64 entries, not their byte size; llvm-nm rejects the file when
    # these are confused.
    command = struct.pack(
        "<IIIIII",
        LC_SYMTAB,
        SYMTAB_CMD_SIZE,
        symoff,
        len(symbol_names),
        stroff,
        len(string_table),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + command + nlists + string_table)


def make_deb(path: Path, image: Path, inner: str) -> None:
    root = path.parent / f"debroot-{path.stem}"
    target = root / inner
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image, target)
    control_dir = root / "DEBIAN"
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "control").write_text(
        "Package: test.corecrypto\n"
        "Version: 1\n"
        "Architecture: iphoneos-arm64\n"
        "Maintainer: test <test@example.com>\n"
        "Description: regression fixture for the corecrypto import checker\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", "-Zgzip", str(root), str(path)],
        capture_output=True,
        check=True,
    )


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def main() -> int:
    if not shutil.which("llvm-nm"):
        print("Error: llvm-nm not found in PATH", file=sys.stderr)
        return 2

    passed = 0
    failed = 0

    def check(
        name: str,
        want_exit: int,
        want_texts: list[str] | str,
        result: subprocess.CompletedProcess[str],
    ) -> None:
        nonlocal passed, failed
        if isinstance(want_texts, str):
            want_texts = [want_texts]
        combined = result.stdout + result.stderr
        reason = ""
        if result.returncode != want_exit:
            reason = f"exit {result.returncode}, wanted {want_exit}"
        else:
            missing = [text for text in want_texts if text not in combined]
            if missing:
                reason = f"output does not contain {missing}"
        if reason:
            print(f"FAIL {name}: {reason}\n{combined}")
            failed += 1
            return
        print(f"ok   {name}")
        passed += 1

    forbidden = [
        "cccurve25519_make_priv",
        "cccurve25519_make_pub",
        "cccurve25519_make_key_pair",
        "ccrng_generate",
    ]

    with tempfile.TemporaryDirectory(prefix="corecrypto-imports-test-") as tmp:
        root = Path(tmp)

        clean = root / "clean.o"
        make_object(clean, ["cccurve25519", "cced25519_make_pub", "ccsha512_di"])
        check(
            "clean object passes and names the rejected symbols",
            0,
            ["Result: PASS", "none imports cccurve25519_make_priv, cccurve25519_make_pub"],
            run("--image", str(clean)),
        )

        one = root / "one.o"
        make_object(one, ["cccurve25519", "cccurve25519_make_key_pair"])
        check(
            "one forbidden import fails and names it",
            1,
            [
                f"FORBIDDEN IMPORT: cccurve25519_make_key_pair in {one}",
                "Result: FAIL (1 forbidden import(s)",
            ],
            run("--image", str(one)),
        )

        every = root / "every.o"
        make_object(every, forbidden)
        check(
            "all four forbidden imports fail",
            1,
            [
                "FORBIDDEN IMPORT: cccurve25519_make_priv",
                "FORBIDDEN IMPORT: cccurve25519_make_pub",
                "FORBIDDEN IMPORT: cccurve25519_make_key_pair",
                "FORBIDDEN IMPORT: ccrng_generate",
            ],
            run("--image", str(every)),
        )

        prefixed = root / "prefixed.o"
        make_object(prefixed, ["_cccurve25519_make_pub"])
        check(
            "the raw underscore spelling is matched",
            1,
            "FORBIDDEN IMPORT: cccurve25519_make_pub",
            run("--image", str(prefixed)),
        )

        not_macho = root / "not-macho"
        not_macho.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        check(
            "a non-Mach-O file is an error",
            1,
            [f"Error: {not_macho}: not a Mach-O image", "Result: FAIL"],
            run("--image", str(not_macho)),
        )

        check("selecting nothing is an error", 2, ["at least one of"], run())

        tree = root / "tree"
        make_object(tree / "var/jb/Library/Frameworks/WebKit.framework/WebKit", ["cccurve25519"])
        make_object(
            tree / "var/jb/Library/Frameworks/WebCore.framework/WebCore",
            ["cccurve25519_make_pub"],
        )
        check(
            "a directory tree is walked and the bad image fails it",
            1,
            ["FORBIDDEN IMPORT: cccurve25519_make_pub in "],
            run("--directory", str(tree)),
        )

        if shutil.which("dpkg-deb"):
            deb = root / "webkit.deb"
            make_deb(
                deb,
                one,
                "var/jb/Library/Frameworks/WebCore.framework/WebCore",
            )
            check(
                "a .deb payload is unpacked and checked",
                1,
                [
                    "FORBIDDEN IMPORT: cccurve25519_make_key_pair in "
                    "var/jb/Library/Frameworks/WebCore.framework/WebCore"
                ],
                run("--deb", str(deb)),
            )
            deb_clean = root / "clean.deb"
            make_deb(
                deb_clean,
                clean,
                "var/jb/Library/Frameworks/WebKit.framework/WebKit",
            )
            check(
                "a clean .deb payload passes",
                0,
                "Result: PASS (1 checked",
                run("--deb", str(deb_clean)),
            )
        else:
            print("skip .deb cases: dpkg-deb not in PATH")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
