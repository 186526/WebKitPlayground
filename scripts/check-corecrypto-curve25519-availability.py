#!/usr/bin/env python3
"""Require HAVE(CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE) to be off at an iOS 16 deployment target.

Source/WTF/wtf/PlatformHave.h sets that macro from PLATFORM(IOS_FAMILY) alone, with no deployment
target test. The macro then selects CoreCryptoSPI.h's external-symbol arm, so WebCore imports
cccurve25519_make_priv, cccurve25519_make_pub and cccurve25519_make_key_pair strongly. iOS 16.1's
dyld shared cache exports none of the three, and WebCore fails to load on the device:

  Symbol not found: _cccurve25519_make_key_pair
  Expected in: /usr/lib/libSystem.B.dylib

patches/ios16-corecrypto-curve25519-inline.patch keys the two iOS-family arms on
__IPHONE_OS_VERSION_MIN_REQUIRED >= 170000, which selects CoreCryptoSPI.h's inline arm below iOS 17.

This script evaluates the `#if` block that defines the macro, with clang's preprocessor, once per
platform and deployment target. Evaluating the real header means a ref that rewrites the block, or
a patch that stops applying, fails here in seconds instead of at the dlopen on the device. clang
needs no Apple SDK for this: the block only reads the WTF_PLATFORM_* and __*_VERSION_MIN_REQUIRED
macros the caller supplies.

Then it compiles a caller of the three functions against the patched header and CoreCryptoSPI.h,
for the iOS 16.1 target, and requires llvm-nm -u to report none of the four forbidden names. That
is the actual acceptance condition, and it also proves the inline arm is what got selected.

Usage:
  scripts/check-corecrypto-curve25519-availability.py \
    --platform-have WebKit/Source/WTF/wtf/PlatformHave.h \
    --core-crypto-spi WebKit/Source/WebCore/PAL/pal/spi/cocoa/CoreCryptoSPI.h \
    --deployment-target 16.1

Regression tests write their own fixtures and need no checkout:
  python3 scripts/test-check-corecrypto-curve25519-availability.py
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MACRO = "HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE"

# (label, defines, expected value). Each entry carries exactly the WTF_PLATFORM_* macros
# PlatformLegacy.h defines for that platform, so no term is supplied that the real build would not
# supply: PLATFORM(IOS_FAMILY) is set only on the iOS-family platforms, and PLATFORM(IOS) only on
# iOS itself, because macCatalyst and visionOS sit under OS(IOS_FAMILY) without it. iOS 16.1 is the
# target this build ships, 17.0 is the version the three symbols arrived in, and the rest hold the
# values the unpatched header already gave each platform.
CASES: tuple[tuple[str, dict[str, str], str], ...] = (
    (
        "iOS 16.1",
        {
            "WTF_PLATFORM_IOS": "1",
            "WTF_PLATFORM_IOS_FAMILY": "1",
            "__IPHONE_OS_VERSION_MIN_REQUIRED": "160100",
        },
        "0",
    ),
    (
        "iOS 16.7",
        {
            "WTF_PLATFORM_IOS": "1",
            "WTF_PLATFORM_IOS_FAMILY": "1",
            "__IPHONE_OS_VERSION_MIN_REQUIRED": "160700",
        },
        "0",
    ),
    (
        "iOS 17.0",
        {
            "WTF_PLATFORM_IOS": "1",
            "WTF_PLATFORM_IOS_FAMILY": "1",
            "__IPHONE_OS_VERSION_MIN_REQUIRED": "170000",
        },
        "1",
    ),
    (
        "macCatalyst 16.1",
        {
            "WTF_PLATFORM_MACCATALYST": "1",
            "WTF_PLATFORM_IOS_FAMILY": "1",
            "__IPHONE_OS_VERSION_MIN_REQUIRED": "160100",
        },
        "0",
    ),
    (
        "macCatalyst 17.0",
        {
            "WTF_PLATFORM_MACCATALYST": "1",
            "WTF_PLATFORM_IOS_FAMILY": "1",
            "__IPHONE_OS_VERSION_MIN_REQUIRED": "170000",
        },
        "1",
    ),
    ("macOS 13", {"WTF_PLATFORM_MAC": "1", "__MAC_OS_X_VERSION_MIN_REQUIRED": "130000"}, "0"),
    ("macOS 14", {"WTF_PLATFORM_MAC": "1", "__MAC_OS_X_VERSION_MIN_REQUIRED": "140000"}, "1"),
    ("visionOS", {"WTF_PLATFORM_VISION": "1", "WTF_PLATFORM_IOS_FAMILY": "1"}, "1"),
    ("tvOS", {"WTF_PLATFORM_APPLETV": "1", "WTF_PLATFORM_IOS_FAMILY": "1"}, "1"),
    ("watchOS", {"WTF_PLATFORM_WATCHOS": "1", "WTF_PLATFORM_IOS_FAMILY": "1"}, "1"),
)

FORBIDDEN_SYMBOLS = (
    "cccurve25519_make_priv",
    "cccurve25519_make_pub",
    "cccurve25519_make_key_pair",
    "ccrng_generate",
)

PROLOGUE = """\
typedef unsigned char uint8_t;
typedef unsigned long size_t;
#define WTF_EXTERN_C_BEGIN extern "C" {
#define WTF_EXTERN_C_END }
#define USE(x) 0
#define HAVE(x) (defined HAVE_##x && HAVE_##x)
#define PLATFORM(x) (defined WTF_PLATFORM_##x && WTF_PLATFORM_##x)
"""

CALLER = """\
extern "C" void h1_client_imports(struct ccrng_state *rng)
{
    ccec25519pubkey pk;
    ccec25519secretkey sk;
    cccurve25519_make_key_pair(rng, pk, sk);
    cccurve25519_make_pub(pk, sk);
}
"""

# cccurve25519_make_priv is declared only in the inline arm, so the caller names the two functions
# both arms declare. That keeps the probe compiling against either arm, which is what lets the
# unpatched header fail on the symbols it imports rather than on a missing declaration. The import
# scan still rejects all four names.


def find_tool(name: str) -> str:
    """Resolve a clang or llvm tool, preferring Xcode's copy on macOS."""
    if shutil.which("xcrun"):
        found = subprocess.run(
            ["xcrun", "--find", name], capture_output=True, text=True, check=False
        )
        candidate = found.stdout.strip()
        if found.returncode == 0 and candidate:
            return candidate
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"Error: {name} not found in PATH")
    return resolved


def extract_block(header: Path) -> str | None:
    """Return the #if/#endif block that defines MACRO, or None when the header has no such macro.

    Anchored on the #define rather than on a fixed condition, so a ref that rewrites the
    condition, or a patch that rewords it, still yields the block the compiler would read.

    None is a legitimate answer: Safari-16.6 and Safari-17.2 have no such block at all, which is
    exactly why patches/ios16-corecrypto-curve25519-inline.patch cannot apply there and the
    workflow skips it. The caller prints SKIP in that case instead of failing, so an older ref
    does not turn the check red for a macro it never had.
    """
    lines = header.read_text(encoding="utf-8").splitlines()
    defines = [n for n, line in enumerate(lines) if line.strip().startswith(f"#define {MACRO} ")]
    if not defines:
        return None
    start = defines[0]
    while start >= 0 and not lines[start].lstrip().startswith("#if"):
        start -= 1
    end = defines[0]
    while end < len(lines) and not lines[end].lstrip().startswith("#endif"):
        end += 1
    if start < 0 or end >= len(lines):
        raise SystemExit(f"Error: could not find the #if/#endif block around {MACRO} in {header}")
    return "\n".join(lines[start : end + 1]) + "\n"


def evaluate_block(block: str, defines: dict[str, str], work: Path, clang: str) -> tuple[str, str]:
    """Preprocess the block under `defines` and return its value and clang's diagnostics."""
    source = work / "probe.cpp"
    source.write_text(
        "#define WTF_PLATFORM_GUARD_AGAINST_INDIRECT_INCLUSION 1\n"
        + PROLOGUE
        + block.replace(f"#define {MACRO} 1", f"#define {MACRO} 1\nH1_VALUE 1", 1)
        + f"#ifndef {MACRO}\nH1_VALUE 0\n#endif\n",
        encoding="utf-8",
    )
    args = [clang, "-E", "-P", "-x", "c++", str(source)]
    args += [f"-D{name}={value}" for name, value in defines.items()]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return "", result.stderr.strip() or f"clang exited {result.returncode}"
    for line in result.stdout.splitlines():
        if line.startswith("H1_VALUE "):
            return line.split()[1], result.stderr.strip()
    return "", "clang produced no H1_VALUE line"


def version_to_min_required(version: str) -> str:
    """Turn 16.1 into __IPHONE_OS_VERSION_MIN_REQUIRED's 160100 spelling."""
    parts = (version.split(".") + ["0", "0"])[:3]
    major, minor, patch = (int(part) if part else 0 for part in parts)
    return f"{major:02d}{minor:02d}{patch:02d}"


def check_compiled_symbols(
    block: str, spi: Path, work: Path, clang: str, nm: str, deployment_target: str
) -> tuple[list[str], str]:
    """Compile a caller at `deployment_target` and return the forbidden imports it left behind."""
    source = work / "caller.cpp"
    # Resolved, because the probe is written to a temporary directory and a relative --core-crypto-spi
    # would then resolve against that directory rather than the caller's.
    source.write_text(
        PROLOGUE + block + f'#include "{spi.resolve()}"\n' + CALLER, encoding="utf-8"
    )
    obj = work / "caller.o"
    # The deployment target reaches both halves: the -target triple, which selects the SDK's own
    # availability defaults, and the macro the patched block reads.
    defines = {
        "WTF_PLATFORM_IOS": "1",
        "WTF_PLATFORM_IOS_FAMILY": "1",
        "__IPHONE_OS_VERSION_MIN_REQUIRED": version_to_min_required(deployment_target),
    }
    result = subprocess.run(
        [
            clang,
            "-target",
            f"arm64-apple-ios{deployment_target}",
            "-x",
            "c++",
            "-c",
            str(source),
            "-o",
            str(obj),
        ]
        + [f"-D{name}={value}" for name, value in defines.items()],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [], f"compiling the caller failed: {result.stderr.strip()}"
    listed = subprocess.run([nm, "-u", str(obj)], capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        return [], f"llvm-nm could not read the caller object: {listed.stderr.strip()}"
    imported = [line.strip().lstrip("_") for line in listed.stdout.splitlines() if line.strip()]
    return [name for name in imported if name in set(FORBIDDEN_SYMBOLS)], ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Require {MACRO} to be off at an iOS 16.1 deployment target and prove the "
            "compiled caller imports none of the iOS 17 corecrypto symbols."
        )
    )
    parser.add_argument("--platform-have", type=Path, required=True, help="PlatformHave.h")
    parser.add_argument(
        "--core-crypto-spi",
        type=Path,
        required=True,
        help="CoreCryptoSPI.h, read for the inline arm",
    )
    parser.add_argument(
        "--deployment-target",
        default="16.1",
        help="the shipped target the macro must be off at (default 16.1)",
    )
    parser.add_argument("--skip-compile", action="store_true", help="evaluate the macro only")
    args = parser.parse_args(argv)

    for path in (args.platform_have, args.core_crypto_spi):
        if not path.is_file():
            raise SystemExit(f"Error: {path} not found")

    clang = find_tool("clang")
    nm = find_tool("llvm-nm")
    block = extract_block(args.platform_have)
    if block is None:
        # Safari-16.6 and Safari-17.2 have no such block, which is why the patch cannot apply
        # there and the workflow skips it. Nothing to evaluate, so print SKIP and succeed.
        print(f"SKIP: {args.platform_have} never defines {MACRO}, so there is no arm to select")
        print("\nResult: PASS (skipped)")
        return 0
    print(f"== {MACRO} block from {args.platform_have}")
    print(block.rstrip())

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="corecrypto-availability-") as tmp:
        work = Path(tmp)
        for label, defines, expected in CASES:
            value, diagnostics = evaluate_block(block, defines, work, clang)
            if not value:
                failures.append(f"{label}: could not evaluate the block ({diagnostics})")
                print(f"FAIL {label}: {diagnostics}")
                continue
            if value != expected:
                failures.append(f"{label}: {MACRO} is {value}, expected {expected}")
                print(f"FAIL {label}: {MACRO} is {value}, expected {expected}")
            else:
                print(f"ok   {label}: {MACRO} is {value}")

        target = f"iOS {args.deployment_target}"
        if not args.skip_compile:
            matches, error = check_compiled_symbols(
                block, args.core_crypto_spi, work, clang, nm, args.deployment_target
            )
            if error:
                failures.append(f"{target} caller: {error}")
                print(f"FAIL {target} caller: {error}")
            elif matches:
                failures.append(f"{target} caller imports {', '.join(matches)}")
                print(f"FAIL {target} caller imports {', '.join(matches)}")
            else:
                print(f"ok   {target} caller imports none of {', '.join(FORBIDDEN_SYMBOLS)}")

    if failures:
        print(f"\nResult: FAIL ({len(failures)} finding(s))", file=sys.stderr)
        return 1
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
