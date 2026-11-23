#!/usr/bin/env python3
"""Regression tests for scripts/check-corecrypto-curve25519-availability.py.

Every case writes its own PlatformHave.h and CoreCryptoSPI.h fixture, so nothing here reads a
WebKit checkout. The cases pin the two states this build went through:

  unpatched  the macro keys on PLATFORM(IOS_FAMILY) alone, so a 16.1 build takes the external
             symbol arm and the caller imports cccurve25519_make_pub and cccurve25519_make_key_pair
             — the two names the device reported.
  patched    the macro keys the iOS-family arms on __IPHONE_OS_VERSION_MIN_REQUIRED >= 170000, so
             16.1 takes the inline arm and the caller imports neither.

The remaining cases hold the platform table: macOS, macCatalyst, visionOS, tvOS and watchOS must
keep the values the unpatched header gave them, so a future edit cannot widen the patch's effect.

clang and llvm-nm are required; they need no Apple SDK for this, because the fixture only reads the
WTF_PLATFORM_* and __*_VERSION_MIN_REQUIRED macros the checker supplies.

Usage: python3 scripts/test-check-corecrypto-curve25519-availability.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-corecrypto-curve25519-availability.py"

SPI_HEADER = """\
#pragma once

WTF_EXTERN_C_BEGIN

#define CC_SPTR(_sn_, _n_) _n_
#define CC_WIDE_NULL nullptr
#define CCRNG_STATE_COMMON \\
int(*CC_SPTR(ccrng_state, generate))(struct ccrng_state *rng, size_t outlen, void *out);

struct ccrng_state {
    CCRNG_STATE_COMMON
};
struct ccrng_state *ccrng(int *error);

#define ccrng_generate(rng, outlen, out) \\
((rng)->generate((struct ccrng_state *)(rng), (outlen), (out)))

typedef uint8_t ccec25519key[32];
typedef ccec25519key ccec25519secretkey;
typedef ccec25519key ccec25519pubkey;
typedef ccec25519key ccec25519base;

#if HAVE(CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE)

int cccurve25519(ccec25519key, const ccec25519secretkey, const ccec25519base);
int cccurve25519_make_pub(ccec25519pubkey, const ccec25519secretkey);
int cccurve25519_make_key_pair(struct ccrng_state *, ccec25519pubkey, ccec25519secretkey);

#else

void cccurve25519(ccec25519key out, const ccec25519secretkey sk, const ccec25519base);

inline void cccurve25519_make_priv(struct ccrng_state *rng, ccec25519secretkey sk)
{
    ccrng_generate(rng, 32, sk);
    sk[0] &= 248;
    sk[31] &= 127;
    sk[31] |= 64;
}

inline void cccurve25519_make_pub(ccec25519pubkey pk, const ccec25519secretkey sk)
{
    cccurve25519(pk, sk, CC_WIDE_NULL);
}

inline void cccurve25519_make_key_pair(struct ccrng_state *rng, ccec25519pubkey pk, ccec25519secretkey sk)
{
    cccurve25519_make_priv(rng, sk);
    cccurve25519_make_pub(pk, sk);
}

#endif

WTF_EXTERN_C_END
"""

UNPATCHED_BLOCK = """\
#if (PLATFORM(MAC) && __MAC_OS_X_VERSION_MIN_REQUIRED >= 140000) \\
    || PLATFORM(MACCATALYST) || PLATFORM(IOS_FAMILY)
#define HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE 1
#endif
"""

PATCHED_BLOCK = """\
#if (PLATFORM(MAC) && __MAC_OS_X_VERSION_MIN_REQUIRED >= 140000) \\
    || (PLATFORM(MACCATALYST) && __IPHONE_OS_VERSION_MIN_REQUIRED >= 170000) \\
    || (PLATFORM(IOS) && __IPHONE_OS_VERSION_MIN_REQUIRED >= 170000) \\
    || PLATFORM(VISION) || PLATFORM(APPLETV) || PLATFORM(WATCHOS)
#define HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE 1
#endif
"""

HEADER_PREAMBLE = """\
#pragma once
#define PLATFORM(WTF_FEATURE) (defined WTF_PLATFORM_##WTF_FEATURE && WTF_PLATFORM_##WTF_FEATURE)
"""


def write_fixture(root: Path, block: str) -> tuple[Path, Path]:
    platform_have = root / "Source/WTF/wtf/PlatformHave.h"
    platform_have.parent.mkdir(parents=True, exist_ok=True)
    platform_have.write_text(HEADER_PREAMBLE + "\n" + block, encoding="utf-8")
    spi = root / "Source/WebCore/PAL/pal/spi/cocoa/CoreCryptoSPI.h"
    spi.parent.mkdir(parents=True, exist_ok=True)
    spi.write_text(SPI_HEADER, encoding="utf-8")
    return platform_have, spi


def run(platform_have: Path, spi: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--platform-have",
            str(platform_have),
            "--core-crypto-spi",
            str(spi),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    for tool in ("clang", "llvm-nm"):
        if not shutil.which(tool):
            print(f"Error: {tool} not found in PATH", file=sys.stderr)
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

    with tempfile.TemporaryDirectory(prefix="corecrypto-availability-test-") as tmp:
        root = Path(tmp)

        patched = root / "patched"
        platform_have, spi = write_fixture(patched, PATCHED_BLOCK)
        check(
            "patched header passes the whole check",
            0,
            "iOS 16.1 caller imports none of",
            run(platform_have, spi),
        )
        check(
            "patched header turns the macro off at iOS 16.1",
            0,
            "ok   iOS 16.1: HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE is 0",
            run(platform_have, spi),
        )
        check(
            "patched header keeps macOS 14, visionOS, tvOS and watchOS on",
            0,
            "ok   watchOS: HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE is 1",
            run(platform_have, spi),
        )
        check(
            "macro-only mode still evaluates the whole platform table",
            0,
            "ok   macCatalyst 17.0",
            run(platform_have, spi, "--skip-compile"),
        )

        unpatched = root / "unpatched"
        platform_have_bad, spi_bad = write_fixture(unpatched, UNPATCHED_BLOCK)
        check(
            "unpatched header fails on the two imported names",
            1,
            "caller imports cccurve25519_make_key_pair, cccurve25519_make_pub",
            run(platform_have_bad, spi_bad),
        )
        check(
            "unpatched header names the 16.1 macro regression",
            1,
            "FAIL iOS 16.1: HAVE_CORE_CRYPTO_SIGNATURES_INT_RETURN_VALUE is 1, expected 0",
            run(platform_have_bad, spi_bad),
        )
        # The macro table alone catches it, which is what lets the workflow fail in seconds.
        check(
            "macro-only mode catches the unpatched header without compiling",
            1,
            "expected 0",
            run(platform_have_bad, spi_bad, "--skip-compile"),
        )
        # The tower's own concern: a header that keys the iOS arm on PLATFORM(IOS) but forgets the
        # version. The platform table has the right shape, yet 16.1 still takes the external arm, so
        # both the macro evaluation and the compile step must fail.
        half = root / "half"
        platform_have_half, spi_half = write_fixture(
            half,
            PATCHED_BLOCK.replace(
                "    || (PLATFORM(IOS) && __IPHONE_OS_VERSION_MIN_REQUIRED >= 170000) \\\n",
                "    || PLATFORM(IOS) \\\n",
            ),
        )
        check(
            "an iOS arm without the version test fails the 16.1 macro evaluation",
            1,
            "FAIL iOS 16.1",
            run(platform_have_half, spi_half),
        )
        check(
            "an iOS arm without the version test fails on the caller's imports",
            1,
            "caller imports cccurve25519_make_key_pair, cccurve25519_make_pub",
            run(platform_have_half, spi_half),
        )

        # Safari-16.6 and Safari-17.2 carry no such block, which is why the workflow skips the
        # patch on those refs. The check must agree and skip rather than fail.
        missing = root / "missing"
        missing.mkdir()
        bare = missing / "PlatformHave.h"
        bare.write_text("#pragma once\n", encoding="utf-8")
        check(
            "a header with no such macro skips instead of failing",
            0,
            ["SKIP:", "Result: PASS (skipped)"],
            run(bare, spi_bad),
        )

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
