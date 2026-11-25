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

The load-path cases pin the second defect.  A built WebKit links to the system frameworks by
absolute path, and production dyld answers an absolute path out of the shared cache without ever
looking on disk, so a package that keeps those paths never loads its own copies.  STEP 5 rewrites
each such command to "@rpath/<Name>.framework/<Name>" and adds the two LC_RPATH entries that
resolve it; STEP 7 refuses to ship an image that still names a system path.

Fixtures are real thin arm64 Mach-O images: a header, one LC_SEGMENT_64 carrying __text, and the
dylib load commands the case needs.  A 32-byte header with ncmds=0 is not a legal image and would
give the rewrite a payload no linker can produce, so the fixtures do not use one.

Each case writes a fixture product tree and runs the real script against it.  ditto and ldid are
stand-ins on Linux; dpkg-deb, zsh, file and python3 are the host's.  The cases that need a broken
payload put a cp stand-in first on PATH, so the script itself stays free of test switches.

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

WEBKIT = "/System/Library/Frameworks/WebKit.framework/WebKit"
WEBCORE = "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"
WEBCORE_PREFIX = "/System/Library/PrivateFrameworks/WebCore.framework/"
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"
WEBKITLEGACY = "/System/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy"
# The shipped WebKit re-exports WebKitLegacy from PrivateFrameworks, not from
# Frameworks, while WebCore links the Frameworks one.
WEBKITLEGACY_PRIVATE = "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy"
WEBKITLEGACY_PRIVATE_PREFIX = "/System/Library/PrivateFrameworks/WebKitLegacy.framework/"
WEBGPU = "/System/Library/Frameworks/WebGPU.framework/WebGPU"
WEBRTC = WEBCORE_PREFIX + "Frameworks/libwebrtc.dylib"
ANGLE = WEBCORE_PREFIX + "Frameworks/libANGLE-shared.dylib"

SYSTEM_PREFIXES = (
    "/System/Library/Frameworks/WebKit.framework/",
    "/System/Library/Frameworks/WebCore.framework/",
    "/System/Library/Frameworks/JavaScriptCore.framework/",
    "/System/Library/Frameworks/WebKitLegacy.framework/",
    "/System/Library/Frameworks/WebGPU.framework/",
    WEBCORE_PREFIX,
    WEBKITLEGACY_PRIVATE_PREFIX,
)

LIBSYSTEM = "/usr/lib/libSystem.B.dylib"

MH_EXECUTE, MH_DYLIB = 2, 6
MH_MAGIC_64, CPU_TYPE_ARM64 = 0xFEEDFACF, 0x0100000C
LC_SEGMENT_64, LC_LOAD_DYLIB = 0x19, 0xC
LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB = 0x80000018, 0x8000001F
LC_CODE_SIGNATURE = 0x1D

TEXT_OFFSET = 0x1000
IMAGE_SIZE = 0x8000

failures: list[str] = []


def _pad(blob: bytes) -> bytes:
    return blob + b"\0" * (-len(blob) % 8)


def _segment_command() -> bytes:
    segname = b"__TEXT".ljust(16, b"\0")
    section = _pad(
        b"__text".ljust(16, b"\0") + segname
        + struct.pack("<QQIIIIIIII", 0, 0x40, TEXT_OFFSET, 2, 0, 0, 0x80000400, 0, 0, 0)
    )
    return struct.pack("<II", LC_SEGMENT_64, 72 + len(section)) + segname + struct.pack(
        "<QQQQiiII", 0, IMAGE_SIZE, 0, IMAGE_SIZE, 7, 5, 1, 0
    ) + section


def _dylib_command(kind: int, name: str) -> bytes:
    encoded = name.encode() + b"\0"
    # cmdsize counts the padding, so it is computed after it.
    size = -(-(24 + len(encoded)) // 8) * 8
    body = struct.pack("<IIIIII", kind, size, 24, 0, 0x10000, 0x10000) + encoded
    return body + b"\0" * (size - len(body))


def build_macho(
    dependencies: tuple[tuple[int, str], ...] = (),
    *,
    filetype: int = MH_DYLIB,
    signature: bool = False,
) -> bytes:
    """Write one thin arm64 Mach-O carrying the given dylib load commands.

    Every image links libSystem the way any linked image does.  It is not decoration: rabin2 -l
    reports "ERROR: Missing bin header libs" and exits 0 for an image with no dylib command at all,
    and a fixture the rewrite would never see in a real product must not be what the check is tested
    against.
    """
    commands = _segment_command()
    loaded = list(dependencies)
    if not loaded:
        loaded.append((LC_LOAD_DYLIB, LIBSYSTEM))
    for kind, name in loaded:
        commands += _dylib_command(kind, name)
    signature_offset = 0
    if signature:
        signature_offset = IMAGE_SIZE - 0x100
        commands += _pad(struct.pack("<IIII", LC_CODE_SIGNATURE, 16, signature_offset, 0x40))
    header = struct.pack(
        "<IiiIIIII", MH_MAGIC_64, CPU_TYPE_ARM64, 0, filetype,
        1 + len(loaded) + (1 if signature else 0), len(commands), 0x00200085, 0,
    )
    image = bytearray(header + commands + b"\0" * (IMAGE_SIZE - 32 - len(commands)))
    if signature:
        image[signature_offset:signature_offset + 4] = b"\xfa\xde\x0c\xc0"
    return bytes(image)


def write_macho(path: Path, data: bytes | None = None, payload: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else build_macho() + payload)


def build_product(root: Path, with_top_level_overlay: bool = True, with_dependencies: bool = True) -> Path:
    """Write the product tree the packaging script reads.

    The overlay follows the real build shape: a top-level real file plus a relative symlink inside
    WebKit.framework pointing back at it.  The dependencies the cases rewrite are the same ones the
    shipped package carries, including the asymmetric ones: WebCore lives under PrivateFrameworks on
    the system and under Library/Frameworks here, and libwebrtc/libANGLE sit under the system WebCore
    bundle while the package lays them flat in usr/lib.
    """
    product = root / "WebKit" / "WebKitBuild" / "Release-iphoneos"

    deps = with_dependencies
    write_macho(product / "WebKit.framework" / "WebKit",
                build_macho(
                    ((LC_LOAD_DYLIB, JSC), (LC_LOAD_DYLIB, WEBCORE),
                     (LC_LOAD_WEAK_DYLIB, WEBRTC), (LC_REEXPORT_DYLIB, WEBKITLEGACY_PRIVATE))
                    if deps else (),
                    filetype=MH_EXECUTE))
    (product / "WebKit.framework" / "Info.plist").write_text("<plist/>", encoding="utf-8")
    write_macho(product / "WebCore.framework" / "WebCore",
                build_macho(
                    ((LC_LOAD_DYLIB, JSC), (LC_LOAD_WEAK_DYLIB, ANGLE),
                     (LC_LOAD_WEAK_DYLIB, WEBRTC), (LC_LOAD_DYLIB, WEBKITLEGACY)) if deps else ()))
    write_macho(product / "WebKitLegacy.framework" / "WebKitLegacy",
                build_macho(((LC_LOAD_DYLIB, JSC), (LC_LOAD_DYLIB, WEBCORE)) if deps else ()))
    write_macho(product / "JavaScriptCore.framework" / "JavaScriptCore", build_macho())
    write_macho(product / "libwebrtc.dylib", build_macho())
    write_macho(product / "libANGLE-shared.dylib", build_macho())
    if deps:
        # A re-export and an executable outside the frameworks exercise the other
        # two command kinds and the shapes that ship as a plain executable.
        write_macho(product / "WebGPU.framework" / "WebGPU",
                    build_macho(((LC_LOAD_DYLIB, JSC), (LC_REEXPORT_DYLIB, WEBCORE))))
        write_macho(product / "webpushd",
                    build_macho(((LC_LOAD_DYLIB, WEBKIT), (LC_LOAD_DYLIB, WEBCORE)),
                                filetype=MH_EXECUTE, signature=True))
        write_macho(product / "JavaScriptCore.framework" / "Helpers" / "jsc",
                    build_macho(((LC_LOAD_DYLIB, JSC),), filetype=MH_EXECUTE))
    else:
        # With every dependency removed the payload names no system path at all;
        # the rewrite must refuse it rather than ship an unrewritten package.
        write_macho(product / "WebGPU.framework" / "WebGPU", build_macho())
        write_macho(product / "webpushd", build_macho(filetype=MH_EXECUTE))
        write_macho(product / "JavaScriptCore.framework" / "Helpers" / "jsc",
                    build_macho(filetype=MH_EXECUTE))

    frameworks = product / "WebKit.framework" / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    (frameworks / OVERLAY).symlink_to(f"../../{OVERLAY}")

    if with_top_level_overlay:
        write_macho(product / OVERLAY, build_macho(((LC_LOAD_DYLIB, WEBKIT),) if deps else ()),
                    payload=b"swift-overlay")

    return product


def write_stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def write_shims(bin_dir: Path, cp_body: str | None = None) -> None:
    """Write ditto and ldid stand-ins.

    ditto copies recursively without following symlinks, which is how the real one behaves and why
    the framework's relative link survives into the payload.  ldid ad-hoc signs each Mach-O; the
    stand-in records, per image, whether the file it was handed carries an @rpath load command, in
    $LDID_LOG when that is set.  A case can then prove the signing pass ran on the rewritten bytes,
    and the file itself is left alone so the layout cases still compare real content.  The optional
    cp body replaces the overlay copy, which is how the negative cases reintroduce a broken payload.
    """
    write_stand_in(bin_dir, "ditto", f'exec {CP} -R "$@"')
    write_stand_in(
        bin_dir, "ldid",
        'for last; do :; done; '
        'if [ -n "${LDID_LOG:-}" ]; then '
        'printf "%s %s\\n" "$(grep -a -c "@rpath/" "$last" || true)" "$last" >> "$LDID_LOG"; '
        'fi',
    )
    write_stand_in(bin_dir, "cp", cp_body or f'exec {CP} "$@"')


def run_script(product: Path, output: Path, mode: str, bin_dir: Path,
               ldid_log: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    if ldid_log is not None:
        environment["LDID_LOG"] = str(ldid_log)
    command = ["zsh", str(SCRIPT), "--product", str(product), "--output", str(output)]
    if mode:
        command.append(mode)
    return subprocess.run(
        command, capture_output=True, text=True, env=environment,
    )


def unpack(deb: Path, destination: Path) -> None:
    subprocess.run(["dpkg-deb", "-x", str(deb), str(destination)], check=True)


def is_macho(path: Path) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(4)
    return head in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")


def load_commands(path: Path) -> tuple[list[int], list[str]]:
    """Return every dylib command kind and name in one thin 64-bit image."""
    data = path.read_bytes()
    count, size = struct.unpack_from("<II", data, 16)
    cursor, kinds, names = 32, [], []
    for _ in range(count):
        kind, command_size = struct.unpack_from("<II", data, cursor)
        if kind in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            offset = struct.unpack_from("<I", data, cursor + 8)[0]
            names.append(data[cursor + offset:cursor + command_size].split(b"\0", 1)[0].decode())
            kinds.append(kind)
        cursor += command_size
    return kinds, names


def rpaths(path: Path) -> list[str]:
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 16)[0]
    cursor, values = 32, []
    for _ in range(count):
        kind, command_size = struct.unpack_from("<II", data, cursor)
        if kind == 0x8000001C:
            offset = struct.unpack_from("<I", data, cursor + 8)[0]
            values.append(data[cursor + offset:cursor + command_size].split(b"\0", 1)[0].decode())
        cursor += command_size
    return values


def expect_layout(label: str, unpacked: Path, prefix: str, product: Path) -> None:
    frameworks = unpacked / prefix / "Library" / "Frameworks"
    usr_lib = unpacked / prefix / "usr" / "lib"

    overlay = frameworks / OVERLAY_REL
    if overlay.is_symlink():
        failures.append(f"{label}: {OVERLAY_REL} is a symlink, not a regular file")
    elif not overlay.is_file():
        failures.append(f"{label}: {OVERLAY_REL} is missing from the package")
    elif overlay.read_bytes() != (product / OVERLAY).read_bytes():
        # The overlay links WebKit, so STEP 5 rewrites its command; everything
        # else about the file must be the product's bytes.
        _, names = load_commands(overlay)
        if names != ["@rpath/WebKit.framework/WebKit"]:
            failures.append(f"{label}: {OVERLAY_REL} is not the product's {OVERLAY} rewritten "
                            f"to load its own WebKit (it loads {names})")

    for name in ("libwebrtc.dylib", "libANGLE-shared.dylib"):
        if not (usr_lib / name).is_file():
            failures.append(f"{label}: usr/lib/{name} is missing")
        if (frameworks / name).exists():
            failures.append(f"{label}: {name} was left in Library/Frameworks")
    for stray in (frameworks / OVERLAY, usr_lib / OVERLAY):
        if stray.exists() or stray.is_symlink():
            failures.append(f"{label}: stray {stray.relative_to(unpacked)}")


def expect_load_paths(label: str, unpacked: Path, prefix: str) -> None:
    """Every shipped image names its own copy of each framework, and nothing system-wide.

    A dependency the rewrite did not touch must keep its original form: /usr/lib/libSystem.B.dylib
    and the other system paths outside this package's frameworks stay absolute on purpose, and only
    the frameworks this package ships are reached through @rpath.
    """
    frameworks_dir = f"/{prefix}/Library/Frameworks".replace("//", "/")
    usr_lib_dir = f"/{prefix}/usr/lib".replace("//", "/")
    wanted_rpaths = (frameworks_dir, usr_lib_dir)
    expected = {
        f"{frameworks_dir}/WebKit.framework/WebKit": {
            "@rpath/JavaScriptCore.framework/JavaScriptCore",
            "@rpath/WebCore.framework/WebCore",
            "@rpath/libwebrtc.dylib",
            "@rpath/WebKitLegacy.framework/WebKitLegacy",
        },
        f"{usr_lib_dir}/libwebrtc.dylib": set(),
        f"{usr_lib_dir}/libANGLE-shared.dylib": set(),
    }
    images = 0
    for path in sorted(unpacked.rglob("*")):
        if not path.is_file() or not is_macho(path):
            continue
        images += 1
        relative = "/" + str(path.relative_to(unpacked))
        kinds, names = load_commands(path)
        for kind, name in zip(kinds, names):
            if name.startswith(SYSTEM_PREFIXES):
                failures.append(f"{label}: {relative} still loads the system path {name}")
        rewritten = [name for name in names if name.startswith("@rpath/")]
        if not rewritten:
            continue
        for want in wanted_rpaths:
            if want not in rpaths(path):
                failures.append(f"{label}: {relative} has no LC_RPATH {want}")
        for name in rewritten:
            target = name[len("@rpath/"):]
            if not any((unpacked / value.lstrip("/") / target).exists() for value in wanted_rpaths):
                failures.append(f"{label}: {relative} @rpath name {name} resolves to no shipped file")
        if relative.lstrip("/") in expected:
            missing = expected[relative.lstrip("/")] - set(rewritten)
            if missing:
                failures.append(f"{label}: {relative} lost the rewritten command(s) {sorted(missing)}")
    if images < 8:
        failures.append(f"{label}: only {images} Mach-O images reached the package")


def case_shipped_layout(label: str, mode: str, prefix: str) -> None:
    before = len(failures)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root)
        deb = root / "webkit.deb"
        ldid_log = root / "ldid.log"
        result = run_script(product, deb, mode, bin_dir, ldid_log)
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
        if "Rewrote 16 load command(s) across 7 image(s)." not in combined:
            failures.append(f"{label}: the load-path rewrite did not run as expected\n{combined}")
            return
        if "Verified 10 Mach-O image(s) carry no system framework load path." not in combined:
            failures.append(f"{label}: the load-path verification did not run as expected\n{combined}")
            return
        # The ldid stand-in records, per image, how many times the install prefix
        # appeared in the bytes it was handed.  A non-zero count proves the rewrite
        # ran before signing, not after.
        signed = ldid_log.read_text(encoding="utf-8").splitlines() if ldid_log.exists() else []
        rewritten_before_signing = [line for line in signed if line.split()[0] != "0"]
        if len(signed) != 10 or len(rewritten_before_signing) != 7:
            failures.append(f"{label}: signing saw {len(signed)} image(s), of which "
                            f"{len(rewritten_before_signing)} carried an @rpath command: {signed}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        expect_layout(label, unpacked, prefix, product)
        expect_load_paths(label, unpacked, prefix)
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


def case_no_system_dependencies() -> None:
    """A payload that names no system framework path must fail, not ship unrewritten."""
    label = "a payload whose images name no system framework path fails"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root, with_dependencies=False)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the packaging should have failed\n{combined}")
            return
        if "the install table matched nothing" not in combined:
            failures.append(f"{label}: output lacks the zero-hit error\n{combined}")
            return
        if deb.exists():
            failures.append(f"{label}: a package was written despite the failed check")
            return
    print(f"PASS {label}")


def case_unparseable_image() -> None:
    """An image whose load commands overrun the file must fail, not be skipped."""
    label = "an unparseable Mach-O fails the packaging instead of being skipped"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root)
        broken = product / "webpushd"
        data = bytearray(broken.read_bytes())
        struct.pack_into("<I", data, 20, 0xFFFF)
        broken.write_bytes(bytes(data))
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the packaging should have failed\n{combined}")
            return
        if "sizeofcmds says" not in combined:
            failures.append(f"{label}: output lacks the unparseable-image error\n{combined}")
            return
        if deb.exists():
            failures.append(f"{label}: a package was written despite the failed check")
            return
    print(f"PASS {label}")


def case_missing_table_destination() -> None:
    """A dependency whose install row points at a file the payload lacks must fail."""
    label = "a dependency whose install row has no shipped file fails"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        # libANGLE-shared.dylib is a recommended artifact, so dropping it from the
        # product only warns; WebCore's weak load command then names a path the
        # install table resolves to a file the payload does not carry.  libwebrtc
        # cannot be used here because it is a required artifact and the run would
        # stop at STEP 1 for a different reason.
        write_shims(bin_dir)
        product = build_product(root)
        (product / "libANGLE-shared.dylib").unlink()
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the packaging should have failed\n{combined}")
            return
        if "is not in the payload" not in combined:
            failures.append(f"{label}: output lacks the missing-destination error\n{combined}")
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
    missing = [tool for tool in ("zsh", "dpkg-deb", "file") if shutil.which(tool) is None]
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
    case_no_system_dependencies()
    case_unparseable_image()
    case_missing_table_destination()

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
