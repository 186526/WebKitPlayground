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

The cases that end in `_lc_str`, `_rejected`, `_atomicity` and `_image` drive the Mach-O tool the
script embeds directly, because the script deletes its work directory on exit and those cases need
to see the tree the tool was handed.  They read the tool out of the script rather than keeping a
copy, so they test the shipped code.

Usage: python3 scripts/test-package-webkit-device-deb.py
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
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
# WebCore reaches WebGPU through PrivateFrameworks with LC_LOAD_WEAK_DYLIB, while
# WebGPU's own install name is the Frameworks one.  The gap the WebGPU case guards
# is this second spelling.
WEBGPU_PRIVATE = "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU"
WEBGPU_PRIVATE_PREFIX = "/System/Library/PrivateFrameworks/WebGPU.framework/"
WEBRTC = WEBCORE_PREFIX + "Frameworks/libwebrtc.dylib"
ANGLE = WEBCORE_PREFIX + "Frameworks/libANGLE-shared.dylib"

SYSTEM_PREFIXES = (
    "/System/Library/Frameworks/WebKit.framework/",
    "/System/Library/Frameworks/WebCore.framework/",
    "/System/Library/Frameworks/JavaScriptCore.framework/",
    "/System/Library/Frameworks/WebKitLegacy.framework/",
    "/System/Library/Frameworks/WebGPU.framework/",
    WEBGPU_PRIVATE_PREFIX,
    WEBCORE_PREFIX,
    WEBKITLEGACY_PRIVATE_PREFIX,
)

LIBSYSTEM = "/usr/lib/libSystem.B.dylib"

MH_EXECUTE, MH_DYLIB = 2, 6
MH_MAGIC_64, CPU_TYPE_ARM64 = 0xFEEDFACF, 0x0100000C
LC_SEGMENT_64, LC_LOAD_DYLIB = 0x19, 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB = 0x80000018, 0x8000001F
LC_CODE_SIGNATURE = 0x1D

TEXT_OFFSET = 0x1000
IMAGE_SIZE = 0x8000

failures: list[str] = []


def _pad(blob: bytes) -> bytes:
    return blob + b"\0" * (-len(blob) % 8)


def _segment_command(text_offset: int = TEXT_OFFSET) -> bytes:
    segname = b"__TEXT".ljust(16, b"\0")
    section = _pad(
        b"__text".ljust(16, b"\0") + segname
        + struct.pack("<QQIIIIIIII", 0, 0x40, text_offset, 2, 0, 0, 0x80000400, 0, 0, 0)
    )
    return struct.pack("<II", LC_SEGMENT_64, 72 + len(section)) + segname + struct.pack(
        "<QQQQiiII", 0, IMAGE_SIZE, 0, IMAGE_SIZE, 7, 5, 1, 0
    ) + section


def _dylib_command(kind: int, name: str, lc_str: int = 24) -> bytes:
    """One dylib load command whose name sits at lc_str bytes into the command.

    24 is the layout Xcode 14 emits, but it is a field, not a constant: the shipped
    WebKit carries a 28 there for its CoreTelephony dependency, with four unused
    bytes between the fixed part and the name.  A fixture set that only ever builds
    24 cannot tell a reader that honours the field from a writer that does not.
    """
    encoded = name.encode() + b"\0"
    # cmdsize counts everything through the terminator and then the padding.
    size = -(-(lc_str + len(encoded)) // 8) * 8
    body = struct.pack("<IIIIII", kind, 0, lc_str, 0, 0x10000, 0x10000)
    body += b"\0" * (lc_str - 24) + encoded
    body += b"\0" * (size - len(body))
    return body[:4] + struct.pack("<I", size) + body[8:]


def build_macho(
    dependencies: tuple[tuple[int, str], ...] = (),
    *,
    filetype: int = MH_DYLIB,
    signature: bool = False,
    lc_str: int = 24,
    header_pad: bool = True,
) -> bytes:
    """Write one thin arm64 Mach-O carrying the given dylib load commands.

    Every image links libSystem the way any linked image does.  It is not decoration: rabin2 -l
    reports "ERROR: Missing bin header libs" and exits 0 for an image with no dylib command at all,
    and a fixture the rewrite would never see in a real product must not be what the check is tested
    against.

    lc_str is the offset each dylib command puts its name at.  header_pad=False starts __text
    immediately after the load commands, which is the shape whose header pad cannot take another
    LC_RPATH.
    """
    loaded = list(dependencies)
    if not loaded:
        loaded.append((LC_LOAD_DYLIB, LIBSYSTEM))
    commands = b"".join(_dylib_command(kind, name, lc_str) for kind, name in loaded)
    signature_offset = 0
    if signature:
        signature_offset = IMAGE_SIZE - 0x100
        commands += _pad(struct.pack("<IIII", LC_CODE_SIGNATURE, 16, signature_offset, 0x40))
    leading = _segment_command()
    text_offset = TEXT_OFFSET if header_pad else 32 + len(leading) + len(commands)
    if text_offset + 0x100 > IMAGE_SIZE:
        raise ValueError("the fixture's load commands leave no room for section data")
    commands = _segment_command(text_offset) + commands
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
    the framework's relative link survives into the payload.  ldid ad-hoc signs each Mach-O through
    this module's own signer, so the packaging script's post-condition reads a seal that covers the
    bytes rather than the `exit 0` that used to stand in for one.  Before signing it records, per
    image, whether the file it was handed carries an @rpath load command, in $LDID_LOG when that is
    set, which is how a case proves the rewrite ran before the signing pass.  The optional cp body
    replaces the overlay copy, which is how the negative cases reintroduce a broken payload.
    """
    write_stand_in(bin_dir, "ditto", f'exec {CP} -R "$@"')
    write_stand_in(
        bin_dir, "ldid",
        'for last; do :; done; '
        'if [ -n "${LDID_LOG:-}" ]; then '
        'printf "%s %s\\n" "$(grep -a -c "@rpath/" "$last" || true)" "$last" >> "$LDID_LOG"; '
        'fi; '
        f'exec {sys.executable} {Path(__file__).resolve()} --sign "$@"',
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


def case_archive_failure(package_type: str, failure: str, stale: bool = True) -> None:
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        product = build_product(root)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        if package_type == "tar":
            write_stand_in(bin_dir, "ldid", "exit 0")
        if failure == "copy":
            write_stand_in(bin_dir, "ditto", f'''case "$1" in
  */libANGLE-shared.dylib) echo 'stub copy failed' >&2; exit 42;;
esac
exec {CP} -R "$@"''')
        elif package_type == "deb":
            write_stand_in(bin_dir, "dpkg-deb", '''for last; do :; done
printf partial > "$last"
echo 'stub archive failed root-owner-group' >&2
exit 43''')
        else:
            write_stand_in(bin_dir, "gtar", '''printf partial > "$2"
echo 'stub archive failed' >&2
exit 43''')
        output = root / f"output.{package_type}"
        if stale:
            output.write_bytes(b"old archive")
        if package_type == "deb":
            result = run_script(product, output, "--rootless", bin_dir)
        else:
            result = subprocess.run(
                ["zsh", str(SCRIPT.with_name("package-webkit-device-artifacts.sh")),
                 "--product", str(product), "--output", str(output)],
                capture_output=True, text=True,
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, timeout=30,
            )
        combined = result.stdout + result.stderr
        label = f"{package_type}: {failure} failure with {'existing' if stale else 'absent'} output"
        expected = "stub copy failed" if failure == "copy" else "stub archive failed"
        preserved = output.read_bytes() == b"old archive" if stale and output.is_file() else not stale and not output.exists()
        if result.returncode == 0 or expected not in combined or "Package created" in combined or not preserved:
            failures.append(f"{label}: exit {result.returncode}, previous state preserved={preserved}\n{combined}")
            return
        print(f"PASS {label}")


def case_tar_replaces_old_output() -> None:
    label = "TAR succeeds and replaces an old output with the new payload"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        product = build_product(root)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        write_stand_in(bin_dir, "ldid", "exit 0")
        output = root / "webkit.tar.gz"
        output.write_bytes(b"old archive")
        result = subprocess.run(
            ["zsh", str(SCRIPT.with_name("package-webkit-device-artifacts.sh")),
             "--product", str(product), "--output", str(output)],
            capture_output=True, text=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, timeout=30,
        )
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{result.stdout}{result.stderr}")
            return
        with tarfile.open(output, "r:gz") as archive:
            relative = "WebKit.framework/WebKit"
            with archive.extractfile(f"payload/{relative}") as image:
                if image.read() != (product / relative).read_bytes():
                    failures.append(f"{label}: archive does not carry the prepared fixture image")
                    return
        print(f"PASS {label}")


def unpack(deb: Path, destination: Path) -> None:
    subprocess.run(["dpkg-deb", "-x", str(deb), str(destination)], check=True)


TOOL_HEREDOC = "MACHO_LOAD_PATHS_PYTHON"


def extract_tool(destination: Path) -> Path:
    """Write the Mach-O tool the script embeds, so a case can drive it directly.

    The script writes it into its own mktemp work directory and deletes that on exit, so a case that
    needs to see the payload tree it was given a partially-rewritten image of cannot go through the
    script.  Reading the tool out of the script, rather than keeping a second copy, is what makes
    these cases test the shipped code.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    opening = f"<<'{TOOL_HEREDOC}'\n"
    start = text.index(opening) + len(opening)
    end = text.index(f"\n{TOOL_HEREDOC}\n", start)
    destination.write_text(text[start:end] + "\n", encoding="utf-8")
    return destination


def run_tool(tool: Path, mode: str, root: Path, prefix: str = "/var/jb") -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(tool), mode, str(root), prefix],
                          capture_output=True, text=True)


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


def write_destination_tree(root: Path, images: dict[str, bytes]) -> Path:
    """Write the files an install-table row checks for, plus the given images, under root/var/jb.

    Keys are payload-relative paths.  The six extra binaries are the destinations the install rows
    named by the fixture depend on, so the rewrite's destination check passes; they carry a single
    libSystem command and are never the image under test.
    """
    payload = root / "var" / "jb"
    for relative in ("Library/Frameworks/WebKit.framework/WebKit",
                     "Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
                     "Library/Frameworks/WebCore.framework/WebCore",
                     "Library/Frameworks/WebGPU.framework/WebGPU",
                     "usr/lib/libANGLE-shared.dylib",
                     "usr/lib/libwebrtc.dylib"):
        write_macho(payload / relative)
    for relative, data in images.items():
        write_macho(payload / relative, data)
    return payload


def case_nonstandard_lc_str() -> None:
    """A dylib command with lc_str=28 must be rewritten at 28, not at the usual 24.

    24 is the layout Xcode 14 emits for these commands and the only one the fixtures used to build,
    so a writer that hard-coded 24 passed everything while leaving lc_str=28 pointing at the tail of
    the name it wrote.  The shipped WebKit carries such a command for CoreTelephony, so the shape is
    reachable; here it names a framework the package ships, which is the reachable failure.
    """
    label = "a load command with lc_str=28 is rewritten at its own offset"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        image = "Library/Frameworks/WebKit.framework/WebKit"
        write_destination_tree(root, {image: build_macho(((LC_LOAD_DYLIB, WEBKIT),), lc_str=28)})
        before = (root / "var/jb" / image).read_bytes()

        result = run_tool(tool, "rewrite", root)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        data = (root / "var/jb" / image).read_bytes()
        count, size = struct.unpack_from("<II", data, 16)
        cursor, rewritten = 32, []
        for _ in range(count):
            kind, command_size = struct.unpack_from("<II", data, cursor)
            if kind in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
                rewritten.append((cursor, command_size,
                                  struct.unpack_from("<I", data, cursor + 8)[0]))
            cursor += command_size
        if len(rewritten) != 1:
            failures.append(f"{label}: expected one dylib command, found {len(rewritten)}")
            return
        offset, command_size, lc_str = rewritten[0]
        if lc_str != 28:
            failures.append(f"{label}: the rewrite changed lc_str from 28 to {lc_str}")
            return
        resolved = data[offset + lc_str:offset + command_size].split(b"\0", 1)[0]
        if resolved != b"@rpath/WebKit.framework/WebKit":
            failures.append(f"{label}: at lc_str=28 the command resolves {resolved!r}, not the "
                            f"@rpath name; the writer used a fixed offset")
            return
        # Nothing before lc_str may move.  A name written at 24 lands four bytes early and ends up
        # as the tail "th/WebKit.framework/WebKit" once the loader applies lc_str=28.
        untouched = before[offset + 24:offset + lc_str]
        if data[offset + 24:offset + lc_str] != untouched:
            failures.append(f"{label}: the rewrite changed the bytes between the fixed part and "
                            f"lc_str into {data[offset + 24:offset + lc_str]!r}")
            return
        if "no system load paths" not in run_tool(tool, "verify", root).stdout:
            failures.append(f"{label}: verify rejected an image that should be clean")
            return
    print(f"PASS {label}")


def case_corrupted_name_is_rejected() -> None:
    """verify must reject a load command whose name resolves to the tail of a name.

    This is the check whose absence let the fixed-offset write ship silently: a name like
    "th/WebKit.framework/WebKit" starts with neither a forbidden prefix nor "@rpath/", so the
    prefix test, the dependency list and the resolution loop all passed it.
    """
    label = "verify rejects a load command whose name resolves to a path tail"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        image = "Library/Frameworks/WebKit.framework/WebKit"
        write_destination_tree(root, {image: build_macho(((LC_LOAD_DYLIB, WEBKIT),), lc_str=28)})
        path = root / "var/jb" / image
        # Write the @rpath name at 24 and leave lc_str at 28: exactly what a fixed-offset writer
        # produced, byte for byte, without relying on the writer still being broken.
        data = bytearray(path.read_bytes())
        count = struct.unpack_from("<I", data, 16)[0]
        cursor = 32
        for _ in range(count):
            kind, command_size = struct.unpack_from("<II", data, cursor)
            if kind == LC_LOAD_DYLIB:
                lc_str = struct.unpack_from("<I", data, cursor + 8)[0]
                encoded = b"@rpath/WebKit.framework/WebKit\0"
                data[cursor + 24:cursor + command_size] = encoded + b"\0" * (command_size - 24 - len(encoded))
                struct.pack_into("<I", data, cursor + 8, lc_str)
            cursor += command_size
        path.write_bytes(bytes(data))

        result = run_tool(tool, "verify", root)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: verify passed a corrupted load command\n{combined}")
            return
        if "neither an absolute path nor @-prefixed" not in combined:
            failures.append(f"{label}: the failure does not name the corrupted command\n{combined}")
            return
    print(f"PASS {label}")


def case_rewrite_already_rpath_linked() -> None:
    label = "rewrite repairs missing LC_RPATH and is byte-identical on repeat"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        helper = "Library/Frameworks/WebKit.framework/XPCServices/helper"
        dependency = "@rpath/WebKit.framework/WebKit"
        payload = write_destination_tree(root, {
            helper: build_macho(((LC_LOAD_DYLIB, dependency),), filetype=MH_EXECUTE),
        })
        result = run_tool(tool, "verify", root)
        if result.returncode != 1 or "resolves to no file inside the payload" not in result.stderr:
            failures.append(f"{label}: verify accepted absent LC_RPATH or failed unexpectedly: {result}")
            return
        result = run_tool(tool, "rewrite", root)
        if result.returncode != 0:
            failures.append(f"{label}: initial rewrite failed: {result}")
            return
        if rpaths(payload / helper) != ["/var/jb/Library/Frameworks", "/var/jb/usr/lib"]:
            failures.append(f"{label}: rewrite did not add both LC_RPATH entries")
            return
        before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
        for mode in ("verify", "rewrite"):
            result = run_tool(tool, mode, root)
            if result.returncode != 0:
                failures.append(f"{label}: {mode} failed on the repaired payload: {result}")
                return
            after = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
            if after != before:
                failures.append(f"{label}: {mode} modified the repaired payload")
                return
    print(f"PASS {label}")


def case_rewrite_rejects_malformed_name() -> None:
    label = "rewrite rejects a malformed load name without writing any image"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        dependency = "@rpath/WebKit.framework/WebKit"
        payload = write_destination_tree(root, {
            "Library/Frameworks/adattributiond": build_macho(((LC_LOAD_DYLIB, WEBKIT),)),
            "Library/Frameworks/webpushd": build_macho(
                ((LC_LOAD_DYLIB, dependency), (LC_LOAD_DYLIB, "bad-tail"))),
        })
        before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
        result = run_tool(tool, "rewrite", root)
        after = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
        if result.returncode != 1 or "absolute path nor @-prefixed: 'bad-tail'" not in result.stderr:
            failures.append(f"{label}: unexpected rewrite result: {result}")
            return
        if after != before:
            failures.append(f"{label}: rewrite changed the payload")
            return
    print(f"PASS {label}")


def case_header_pad_atomicity() -> None:
    """A payload rejected for header-pad exhaustion must be left byte-identical.

    adattributiond has room for the two LC_RPATH entries and sorts before webpushd, whose __text
    starts immediately after its load commands.  Before the capacity check moved into the planning
    phase, the first image was written and then the second failed the run, leaving a half-rewritten
    tree behind a non-zero exit.
    """
    label = "a payload whose header pad is too small is left untouched"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        roomy = "Library/Frameworks/adattributiond"
        tight = "Library/Frameworks/webpushd"
        write_destination_tree(root, {
            roomy: build_macho(((LC_LOAD_DYLIB, WEBKIT),), filetype=MH_EXECUTE),
            tight: build_macho(((LC_LOAD_DYLIB, "@rpath/WebKit.framework/WebKit"),),
                               filetype=MH_EXECUTE, header_pad=False),
        })
        before = {relative: (root / "var/jb" / relative).read_bytes() for relative in (roomy, tight)}

        result = run_tool(tool, "rewrite", root)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the exhausted header pad should have failed\n{combined}")
            return
        if "no room for" not in combined:
            failures.append(f"{label}: the failure is not the header-pad one\n{combined}")
            return
        after = {relative: (root / "var/jb" / relative).read_bytes() for relative in (roomy, tight)}
        for relative in (roomy, tight):
            if before[relative] != after[relative]:
                failures.append(f"{label}: {relative} was modified even though the run failed")
                return
    print(f"PASS {label}")


def case_truncated_image() -> None:
    """A Mach-O whose command area runs past the end of the file must fail with a payload error.

    The shape matters.  A file whose command list happens to end before the truncation point still
    parses, and the old tool reported the size mismatch cleanly.  The defect appears when the
    command walk itself reaches the end of the buffer: struct.unpack_from then raises struct.error,
    which main() never caught, so the packager printed a traceback instead of a payload error.
    """
    label = "a truncated Mach-O fails with a payload error, not a traceback"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        image = "Library/Frameworks/WebKit.framework/WebKit"
        write_destination_tree(root, {})
        whole = bytearray(build_macho(((LC_LOAD_DYLIB, WEBKIT),)))
        # Fill the command area with 8-byte commands and claim far more of them than the truncated
        # file holds, so the parse walks off the end of the buffer.
        for offset in range(32, 1000, 8):
            struct.pack_into("<II", whole, offset, LC_CODE_SIGNATURE, 8)
        struct.pack_into("<II", whole, 16, 1000, 0x2000)
        (root / "var/jb" / image).write_bytes(bytes(whole[:1000]))

        result = run_tool(tool, "rewrite", root)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, a truncated image should fail\n{combined}")
            return
        if "Traceback" in combined:
            failures.append(f"{label}: the failure is an uncaught traceback\n{combined}")
            return
        if "is truncated" not in combined:
            failures.append(f"{label}: the failure is not the truncation error\n{combined}")
            return
    print(f"PASS {label}")


def sign_fixture(path: Path) -> None:
    """Sign one fixture image the way `ldid -S` does, so the check has something real to read.

    The packaging script's post-condition rejects an image that ships unsigned, so a stand-in that
    only logs like the old one would make every full-script case fail.  This writes a CodeDirectory
    that covers the image, which is what the check verifies.

    The signature has to start exactly where the covered range ends and reach the end of the file,
    so the covered length and the signature size are solved together: a longer signature shortens
    the covered range, which can change the page count, which changes the signature size.  Two
    passes settle it for images this size.
    """
    data = bytearray(path.read_bytes())
    count, _size = struct.unpack_from("<II", data, 16)
    existing = None
    cursor = 32
    for index in range(count):
        kind, command_size = struct.unpack_from("<II", data, cursor)
        if kind == LC_CODE_SIGNATURE:
            existing = cursor
            break
        cursor += command_size

    identifier = path.name.encode() + b"\0"
    page = 4096
    code_limit = len(data)
    if existing is None:
        # Appending a command grows the header, so the covered range starts after it and the
        # command is rewritten once the final size is known.
        count += 1
    for _ in range(4):
        pages = (code_limit + page - 1) // page
        directory_length = 44 + pages * 20 + len(identifier)
        signature_size = 12 + 8 + directory_length
        candidate = len(data) - signature_size
        if candidate == code_limit:
            break
        code_limit = candidate
    pages = (code_limit + page - 1) // page
    directory_length = 44 + pages * 20 + len(identifier)
    signature_size = 12 + 8 + directory_length
    code_limit = len(data) - signature_size

    command = struct.pack("<IIII", LC_CODE_SIGNATURE, 16, code_limit, signature_size)
    if existing is None:
        insert_at = 32 + struct.unpack_from("<I", data, 20)[0]
        data[insert_at:insert_at] = command
        struct.pack_into("<I", data, 16, count)
        struct.pack_into("<I", data, 20, struct.unpack_from("<I", data, 20)[0] + 16)
    else:
        data[existing:existing + 16] = command

    body = bytes(data[:code_limit])
    digests = b"".join(
        hashlib.sha1(body[slot * page:min((slot + 1) * page, code_limit)]).digest()
        for slot in range(pages)
    )
    header_size = 44
    ident_offset = header_size + len(digests)
    directory = struct.pack(
        ">IIIIIIIIIBBBBBBBB",
        0xFADE0C02, directory_length, 0x20400, 0x2, header_size, ident_offset, 0,
        pages, code_limit, 20, 1, 0, 12, 0, 0, 0, 0,
    )
    code_directory = directory + digests + identifier
    superblob = struct.pack(">III", 0xFADE0CC0, 12 + 8 + directory_length, 1)
    superblob += struct.pack(">II", 0, 12 + 8) + code_directory
    if len(superblob) != signature_size:
        raise AssertionError(f"fixture signature is {len(superblob)}, planned {signature_size}")
    path.write_bytes(body + superblob)


def write_rcodesign(bin_dir: Path) -> None:
    """Swap the ldid stand-in for an rcodesign one.

    The script prefers ldid and only looks for rcodesign when no ldid is on PATH, so the fallback
    is unreachable while the ldid stand-in exists.  rcodesign takes an output path instead of
    editing in place, and it is the signer a Linux host uses, which is the branch whose
    "reported success but wrote no output" guard would otherwise never run.
    """
    (bin_dir / "ldid").unlink()
    write_stand_in(bin_dir, "rcodesign", f'''input=""
output=""
while [ $# -gt 0 ]; do
  case "$1" in
    sign) shift;;
    --entitlements-xml-file) shift 2;;
    *) input="$output"; output="$1"; shift;;
  esac
done
{CP} "$input" "$output" && exec {sys.executable} {Path(__file__).resolve()} --sign "$output"''')


def signature_state(path: Path) -> str:
    """Ask the packaging script's own checker what seal one image ships."""
    checker = Path(__file__).resolve().parent / "macho-code-signature.py"
    with tempfile.TemporaryDirectory() as work:
        target = Path(work) / path.name
        shutil.copyfile(path, target)
        result = subprocess.run(
            [sys.executable, str(checker), "survey", work], capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                return parts[3]
    return "absent"


def case_signer_that_does_nothing() -> None:
    """A signer that reports success without signing must fail the run, not ship.

    This is the defect the check exists for: `ldid -S` with no post-condition made a signing pass
    that did nothing indistinguishable from one that worked.  The fixture product ships unsigned
    images, so a signer that leaves them alone has to stop the run.
    """
    label = "a signer that leaves images unsigned fails the run"
def run_verify_deb(deb: Path, mode: str, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the script's --verify-deb mode over an already built package.

    It takes neither a build product nor a signer, so the PATH it gets holds none of the packaging
    stand-ins: the mode has to work on a machine that only has dpkg-deb and python3, which is what
    the workflow step relies on.
    """
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    command = ["zsh", str(SCRIPT), "--verify-deb", str(deb)]
    if mode:
        command.append(mode)
    return subprocess.run(command, capture_output=True, text=True, env=environment)


def skip_without_dpkg_deb(label: str) -> bool:
    """Report a skip instead of a failure where dpkg-deb is not installed.

    The mode cannot be exercised without it, and an absence on the host says nothing about the
    code.  main() also refuses to run any case without dpkg-deb, so this guard only matters when
    one of these cases is driven on its own.
    """
    if shutil.which("dpkg-deb") is not None:
        return False
    print(f"SKIP {label} (dpkg-deb not installed)")
    return True


def make_deb(root: Path, images: dict[str, bytes], prefix: str = "var/jb") -> Path:
    """Write a package holding the given images under the install prefix.

    The control files are written directly rather than copied out of a real package: dpkg-deb only
    needs a DEBIAN/control to build, and the cases care about the payload, not the metadata.
    """
    staging = root / "staging"
    for relative, data in images.items():
        destination = staging / prefix / relative if prefix else staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (staging / "DEBIAN").mkdir(parents=True, exist_ok=True)
    (staging / "DEBIAN" / "control").write_text(
        "Package: com.example.verify\nVersion: 1.0\nArchitecture: iphoneos-arm64\n"
        "Maintainer: t <t@example>\nDescription: fixture\n",
        encoding="utf-8",
    )
    deb = root / "fixture.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(staging), str(deb)],
                   check=True, capture_output=True)
    return deb


def case_verify_deb_rejects_unrewritten() -> None:
    """--verify-deb must reject a package whose images still name the system frameworks.

    This is the state a skipped STEP 5 leaves, and the check exists so that package cannot be
    uploaded.  A mode that passed here would be a log line, not a gate.
    """
    label = "--verify-deb rejects a package whose images still name system frameworks"
    if skip_without_dpkg_deb(label):
        return
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        # The old `exit 0` stand-in, which is the shape of the real defect.
        write_stand_in(bin_dir, "ldid", "exit 0")
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, an unsigned payload was shipped\n{combined}")
            return
        if "no code signature" not in combined:
            failures.append(f"{label}: the check did not name the unsigned images\n{combined}")
            return
        if deb.exists():
            failures.append(f"{label}: a package was written despite the failed check")
        deb = make_deb(root, {
            "Library/Frameworks/WebKit.framework/WebKit":
                build_macho(((LC_LOAD_DYLIB, JSC), (LC_LOAD_DYLIB, WEBCORE)), filetype=MH_EXECUTE),
            "Library/Frameworks/WebCore.framework/WebCore": build_macho(((LC_LOAD_DYLIB, JSC),)),
        })
        result = run_verify_deb(deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, an unrewritten package must be rejected\n{combined}")
            return
        if JSC not in combined:
            failures.append(f"{label}: the error does not name the offending system path\n{combined}")
            return
    print(f"PASS {label}")


def case_resign_is_recorded() -> None:
    """The run must list the cdhashes it changed, because the device admits by cdhash."""
    label = "a re-signing pass lists the cdhashes it changed"
def case_verify_deb_accepts_rewritten() -> None:
    """--verify-deb must accept a package STEP 5 left consistent, or the gate blocks every build."""
    label = "--verify-deb accepts a package whose images resolve inside it"
    if skip_without_dpkg_deb(label):
        return
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "carry a new signature after STEP 5's rewrite" not in combined:
            failures.append(f"{label}: the run did not report the new signatures\n{combined}")
            return
        if "->" not in combined:
            failures.append(f"{label}: the run did not list any cdhash\n{combined}")
            return
    print(f"PASS {label}")


def case_rcodesign_path() -> None:
    """The rcodesign dispatch has to sign, and its output has to reach the payload.

    ldid is preferred and rcodesign is only consulted when no ldid is on PATH, so without a case
    that removes the ldid stand-in the branch a Linux host takes never runs.  rcodesign also writes
    an output path rather than editing in place, which is the file the script moves back.
    """
    label = "a package signed through rcodesign passes the seal check"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        write_rcodesign(bin_dir)
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "Signing Mach-O binaries with rcodesign" not in combined:
            failures.append(f"{label}: the rcodesign branch did not run\n{combined}")
            return
        if "carry a new signature after STEP 5's rewrite" not in combined:
            failures.append(f"{label}: the rcodesign pass did not re-sign the payload\n{combined}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        for path in unpacked.rglob("*"):
            if path.is_file() and is_macho(path) and signature_state(path) != "valid":
                failures.append(f"{label}: {path.relative_to(unpacked)} ships no valid seal")
                return
    print(f"PASS {label}")


def case_rcodesign_writes_no_output() -> None:
    """An rcodesign that exits 0 without writing its file must fail, not pass a stale image.

    Without the guard the script moves nothing, the image keeps its pre-rewrite seal, and the
    comparison reads bytes that were never re-signed.
    """
    label = "an rcodesign that writes no output fails the run"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        write_rcodesign(bin_dir)
        write_stand_in(bin_dir, "rcodesign", "exit 0")
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, the run passed without signing\n{combined}")
            return
        if "wrote no output" not in combined:
            failures.append(f"{label}: the failure is not the missing-output one\n{combined}")
            return
        if deb.exists():
            failures.append(f"{label}: a package was written despite the failed signing pass")
            return
    print(f"PASS {label}")


def case_byte_identical_note() -> None:
    """A signer that leaves every image unchanged has to say so out loud.

    This is the other half of the silent-pass diagnosis: a pass that changed nothing is not an
    error, because a payload can already be correctly sealed, but the operator has to be told the
    run cannot distinguish that from a signer that did nothing at all.

    Driven against `macho-code-signature.py compare` directly.  A full-script run cannot produce
    this shape: STEP 5's rewrite edits the command area of every image it touches, so the signing
    pass always changes those cdhashes, and the case would be asserting a state the pipeline never
    reaches.
    """
    label = "a run that changes no cdhash says it cannot tell a signer ran"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        payload = root / "payload"
        image = payload / "Image.dylib"
        write_macho(image, payload=b"code")
        sign_fixture(image)
        checker = Path(__file__).resolve().parent / "macho-code-signature.py"
        survey = subprocess.run(
            [sys.executable, str(checker), "survey", str(payload)],
            capture_output=True, text=True,
        )
        before = root / "before.txt"
        before.write_text(survey.stdout, encoding="utf-8")
        if "\tvalid\t" not in survey.stdout:
            failures.append(f"{label}: the signed fixture did not survey as valid\n{survey.stdout}")
            return
        compare = subprocess.run(
            [sys.executable, str(checker), "compare", "--before", str(before), str(payload)],
            capture_output=True, text=True,
        )
        combined = compare.stdout + compare.stderr
        if compare.returncode != 0:
            failures.append(f"{label}: exit {compare.returncode} on an unchanged payload\n{combined}")
            return
        if "byte-identical to the pre-signing survey" not in combined:
            failures.append(f"{label}: the note did not name the byte-identical state\n{combined}")
            return
        if "cannot tell that a signer ran" not in combined:
            failures.append(f"{label}: the note did not say what it cannot tell\n{combined}")
            return
    print(f"PASS {label}")


def case_stale_seal_is_rejected() -> None:
    """The checker itself must reject a seal that no longer covers the file.

    Driven directly against `macho-code-signature.py compare`, so the rule is pinned without
    depending on which signer the host has.
    """
    label = "a stale seal is reported as a violation"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        image = root / "payload" / "Image.dylib"
        write_macho(image, payload=b"code")
        sign_fixture(image)
        checker = Path(__file__).resolve().parent / "macho-code-signature.py"
        survey = subprocess.run(
            [sys.executable, str(checker), "survey", str(root / "payload")],
            capture_output=True, text=True,
        )
        if survey.returncode != 0:
            failures.append(f"{label}: survey failed\n{survey.stderr}")
            return
        before = root / "before.txt"
        before.write_text(survey.stdout, encoding="utf-8")
        if "\tvalid\t" not in survey.stdout:
            failures.append(f"{label}: the signed fixture did not survey as valid\n{survey.stdout}")
            return
        # Change a byte the seal covers.
        data = bytearray(image.read_bytes())
        data[40] ^= 0xFF
        image.write_bytes(bytes(data))
        compare = subprocess.run(
            [sys.executable, str(checker), "compare", "--before", str(before), str(root / "payload")],
            capture_output=True, text=True,
        )
        if compare.returncode == 0:
            failures.append(f"{label}: a stale seal was accepted\n{compare.stdout}{compare.stderr}")
            return
        if "does not cover" not in compare.stderr:
            failures.append(f"{label}: the violation did not explain the seal\n{compare.stderr}")
            return
    print(f"PASS {label}")


def case_survey_follows_the_rewrite() -> None:
    """The seal survey has to bracket the signing pass, which means it runs after the rewrite.

    This is the merged order, and the reason it is asserted rather than assumed: the rewrite edits
    the command area, which sits inside the pages a seal hashes, so a survey taken before it
    reports a seal that is valid at the time and broken once the rewrite lands.  A check in that
    order goes green while the shipped image is broken.  The script's own output names the step it
    credits for the rewrite, so a comment that drifts back to the wrong step fails here too.
    """
    label = "the seal survey runs after STEP 5's rewrite"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_shims(bin_dir)
        product = build_product(root)
        deb = root / "webkit.deb"
        result = run_script(product, deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        # Every image the rewrite touched has to be re-signed, and the run says so.  If the survey
        # had run before the rewrite this count would be zero and the seals would be stale.
        if "carry a new signature after STEP 5's rewrite" not in combined:
            failures.append(f"{label}: no image carries a signature from after the rewrite\n{combined}")
            return
        unpacked = root / "unpacked"
        unpack(deb, unpacked)
        # The shipped bytes are what the seal has to cover: survey them again and require valid.
        checker = Path(__file__).resolve().parent / "macho-code-signature.py"
        shipped = subprocess.run(
            [sys.executable, str(checker), "survey", str(unpacked)],
            capture_output=True, text=True,
        )
        if shipped.returncode != 0:
            failures.append(f"{label}: surveying the shipped package failed\n{shipped.stderr}")
            return
        rows = [line.split("\t") for line in shipped.stdout.splitlines() if "\t" in line]
        broken = [row[0] for row in rows if len(row) >= 4 and row[3] != "valid"]
        if broken:
            failures.append(f"{label}: the shipped package carries seal(s) that do not cover it: "
                            f"{broken}")
            return
    print(f"PASS {label}")


def case_step_banners_are_ordered() -> None:
    """The script's step banners have to be numbered without gaps or reuse.

    The merged script is the one the pipeline runs.  A banner that numbers a step differently from
    the step that performs it is what makes a comment credit the wrong step: the rewrite is STEP 5,
    so a duplicated number anywhere leaves two steps claiming one ordinal.
    """
    label = "the step banners are numbered once each, in order"
    text = SCRIPT.read_text(encoding="utf-8")
    numbers = [int(match) for match in re.findall(r"^# STEP (\d+) —", text, re.MULTILINE)]
    if numbers != list(range(1, len(numbers) + 1)):
        failures.append(f"{label}: the banners are {numbers}")
        return
    # TOTAL_STEPS is declared as 0 next to STEP_INDEX and assigned its real value after the
    # argument parsing, so the effective number is the last assignment in the file.
    assignments = [int(value) for value in re.findall(r"^TOTAL_STEPS=(\d+)$", text, re.MULTILINE)]
    if not assignments or assignments[-1] != len(numbers):
        failures.append(f"{label}: TOTAL_STEPS assignments are {assignments} but there are "
                        f"{len(numbers)} banner(s)")
        return
    print(f"PASS {label}")


PAYLOAD_IMAGES = (
    ("Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
     "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore", ()),
    ("Library/Frameworks/WebCore.framework/WebCore",
     "/System/Library/PrivateFrameworks/WebCore.framework/WebCore",
     ((LC_LOAD_WEAK_DYLIB, "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU"),
      (LC_LOAD_WEAK_DYLIB,
       "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib"),
      (LC_LOAD_WEAK_DYLIB,
       "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
      (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"))),
    ("Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
     "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy",
     ((LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
      (LC_LOAD_DYLIB, "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"))),
    ("Library/Frameworks/WebGPU.framework/WebGPU",
     "/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU",
     ((LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),)),
    ("usr/lib/libwebrtc.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib", ()),
    ("usr/lib/libANGLE-shared.dylib",
     "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib", ()),
    ("Library/Frameworks/WebKit.framework/WebKit",
     "/System/Library/Frameworks/WebKit.framework/WebKit",
     ((LC_REEXPORT_DYLIB,
       "/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy"),
      (LC_LOAD_WEAK_DYLIB,
       "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"),
      (LC_LOAD_DYLIB, "/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
      (LC_LOAD_DYLIB, "/System/Library/PrivateFrameworks/WebCore.framework/WebCore"))),
)


def build_signed_macho(identity: str, dependencies: tuple[tuple[int, str], ...]) -> bytes:
    """One of the seven as the installed package carries it, with a CodeDirectory over its pages.

    The page hashes are real: the rewrite changes the load commands, which lie in the first hashed
    page, so a package built from an image whose hashes were already wrong would make the rewrite's
    own repair invisible.
    """
    signature_offset = IMAGE_SIZE - 0x200
    commands = [
        _segment_command(TEXT_OFFSET),
        _dylib_command(LC_ID_DYLIB, identity),
        *(_dylib_command(kind, name) for kind, name in dependencies),
        _dylib_command(LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib"),
        _pad(struct.pack("<IIII", LC_CODE_SIGNATURE, 16, signature_offset, 32)),
    ]
    body = b"".join(commands)
    header = struct.pack("<IiiIIIII", MH_MAGIC_64, CPU_TYPE_ARM64, 0, MH_DYLIB,
                         len(commands), len(body), 0x00200085, 0)
    image = bytearray(header + body + b"\0" * (IMAGE_SIZE - 32 - len(body)))
    blob = build_code_directory(bytes(image), signature_offset)
    image[signature_offset:signature_offset + len(blob)] = blob
    return bytes(image)


def build_code_directory(code: bytes, code_limit: int, page: int = 0x1000) -> bytes:
    """A SuperBlob with one SHA-256 CodeDirectory whose page hashes describe `code`.

    Only the SHA-256 slot is written: the rewrite recomputes whichever algorithms the file carries,
    and one is enough to prove it reads the file it is about to ship.
    """
    hash_size = 32
    pages = (code_limit + page - 1) // page
    hash_offset = 44 + 2 * hash_size
    body = bytearray(44)
    struct.pack_into(">IIIIIIIII", body, 0, 0xFADE0C02, 0, 0x20001, 0, hash_offset,
                     0, 2, pages, code_limit)
    struct.pack_into(">BBBB", body, 36, hash_size, 2, 0, page.bit_length() - 1)
    body += b"\0" * (2 * hash_size)
    for index in range(pages):
        body += hashlib.sha256(code[index * page:min((index + 1) * page, code_limit)]).digest()
    struct.pack_into(">II", body, 4, len(body), 4096)
    index = struct.pack(">II", 4096, 12 + 8)
    return struct.pack(">III", 0xFADE0CC0, 12 + len(index) + len(body), 1) + index + bytes(body)


def write_manifest(root: Path, paths: list[str]) -> Path:
    """The file --expect-manifest reads: one device absolute path per line."""
    manifest = root / "installed-manifest.txt"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    return manifest


def case_payload_mode() -> None:
    """--payload rewrites the installed tree in place and the .deb carries those bytes.

    This is the mode the mission adds: the rewrite target is the directory the ellekit filter loads,
    not a second tree.  The case checks the package the way a reader would: unpack it and read the
    seven load-command tables back.
    """
    label = "--payload rewrites the installed tree and ships those bytes"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        source = root / "installed"
        for relative, identity, dependencies in PAYLOAD_IMAGES:
            path = source / "var/jb" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_signed_macho(identity, dependencies))
        before = {relative: hashlib.sha256((source / "var/jb" / relative).read_bytes()).hexdigest()
                  for relative, _, _ in PAYLOAD_IMAGES}
        # The integrity check needs a list to compare against. This machine has no
        # dpkg database for the package, so the case supplies the list explicitly,
        # which is also the path a build host without a device takes.
        manifest = write_manifest(root, [f"/var/jb/{relative}" for relative, _, _ in PAYLOAD_IMAGES])

        output = root / "payload.deb"
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
             "--version", "0.0.0-test", "--expect-manifest", str(manifest),
             "--output", str(output)],
            capture_output=True, text=True, env={**os.environ, "PATH": os.environ["PATH"]},
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if not output.exists():
            failures.append(f"{label}: no package was written\n{combined}")
            return
        # The source tree is an input; a rewrite that edited it would make a second
        # run start from a moved tree.
        after = {relative: hashlib.sha256((source / "var/jb" / relative).read_bytes()).hexdigest()
                 for relative, _, _ in PAYLOAD_IMAGES}
        if before != after:
            failures.append(f"{label}: the --payload tree was modified in place")
            return
        if "all seven images in the .deb are the bytes the rewrite produced" not in combined:
            failures.append(f"{label}: the package self-check did not run\n{combined}")
            return

        unpacked = root / "unpacked"
        subprocess.run(["dpkg-deb", "-x", str(output), str(unpacked)], check=True)
        edges = 0
        for relative, identity, _ in PAYLOAD_IMAGES:
            data = (unpacked / "var/jb" / relative).read_bytes()
            commands = load_commands_bytes(data)
            identities = [name for kind, name in commands if kind == LC_ID_DYLIB]
            if identities != [identity]:
                failures.append(f"{label}: {relative} identity is {identities}, not {identity}")
                return
            edges += sum(1 for kind, name in commands
                         if kind != LC_ID_DYLIB and name and name.startswith("/var/jb/"))
            stale = [name for kind, name in commands
                     if kind != LC_ID_DYLIB and name in {row[1] for row in PAYLOAD_IMAGES}
                     and not name.startswith("/var/jb/")]
            if stale:
                failures.append(f"{label}: {relative} still names {stale}")
                return
        if edges != 11:
            failures.append(f"{label}: {edges} /var/jb edge(s), expected 11")
            return
        if "11 /var/jb edge(s) among the seven, 11 expected" not in combined:
            failures.append(f"{label}: the self-check did not report eleven edges\n{combined}")
            return
    print(f"PASS {label}")


def case_payload_mode_wrong_root() -> None:
    """A --payload root with none of the seven fails instead of shipping an empty package."""
    label = "--payload fails when the tree lacks the seven"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        (root / "installed" / "var/jb").mkdir(parents=True)
        manifest = write_manifest(root, ["/var/jb/usr/lib/libwebrtc.dylib"])
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(root / "installed"), "--rootless",
             "--version", "0.0.0-test", "--expect-manifest", str(manifest),
             "--output", str(root / "out.deb")],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 on a tree with none of the seven\n{combined}")
            return
        if "is not in the payload" not in combined:
            failures.append(f"{label}: the failure does not name the missing image\n{combined}")
            return
        if (root / "out.deb").exists():
            failures.append(f"{label}: a package was written anyway")
            return
    print(f"PASS {label}")


def case_payload_drops_a_listed_file() -> None:
    """A payload that lacks a file the installed package carries must not be packaged.

    dpkg installs an upgrade by writing the new file list and removing every file the
    old list names and the new one does not, so a payload built without a listed file
    deletes that file from the device.  The shipped package lost webpushd and
    adattributiond exactly this way, and the packager accepted it.
    """
    label = "--payload fails when it drops a file the installed package carries"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        source = root / "installed"
        for relative, identity, dependencies in PAYLOAD_IMAGES:
            path = source / "var/jb" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_signed_macho(identity, dependencies))
        daemon = "Library/Frameworks/webpushd"
        manifest = write_manifest(
            root, [f"/var/jb/{relative}" for relative, _, _ in PAYLOAD_IMAGES]
            + [f"/var/jb/{daemon}"])
        output = root / "out.deb"
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
             "--version", "0.0.0-test", "--expect-manifest", str(manifest),
             "--output", str(output)],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 on a payload missing {daemon}\n{combined}")
            return
        if f"/var/jb/{daemon}" not in combined:
            failures.append(f"{label}: the failure does not name {daemon}\n{combined}")
            return
        if "the payload is missing 1 of the" not in combined:
            failures.append(f"{label}: the failure does not count the missing file\n{combined}")
            return
        if output.exists():
            failures.append(f"{label}: a package was written anyway")
            return

        # The same tree with the file back must pass, so the case proves the check
        # reads the payload rather than refusing everything the manifest names.
        (source / "var/jb" / daemon).write_bytes(b"a stand-in, this case never loads it")
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
             "--version", "0.0.0-test", "--expect-manifest", str(manifest),
             "--output", str(output)],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode} with the file back\n{combined}")
            return
        if "the payload carries all" not in combined:
            failures.append(f"{label}: the check did not report a complete payload\n{combined}")
            return
    print(f"PASS {label}")


def case_payload_needs_version() -> None:
    """--payload without --version must stop: this mode cannot derive one.

    The mode has no Version.xcconfig to read, so a default here once produced
    0.0.1-<timestamp>, below the installed 620.2.4, and the install was a downgrade.
    """
    label = "--payload without --version fails instead of guessing one"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        source = root / "installed"
        for relative, identity, dependencies in PAYLOAD_IMAGES:
            path = source / "var/jb" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_signed_macho(identity, dependencies))
        manifest = write_manifest(root, [f"/var/jb/{relative}" for relative, _, _ in PAYLOAD_IMAGES])
        output = root / "out.deb"
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
             "--expect-manifest", str(manifest), "--output", str(output)],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 without --version\n{combined}")
            return
        if "--payload needs --version" not in combined:
            failures.append(f"{label}: the failure does not ask for --version\n{combined}")
            return
        if output.exists():
            failures.append(f"{label}: a package was written anyway")
            return
    print(f"PASS {label}")


def case_payload_needs_a_manifest() -> None:
    """--payload with no list to compare against must stop, not skip the check.

    Silently skipping would ship whatever the caller passed, which is the state that
    lost the two daemons. This machine has no dpkg entry for the package, so the
    fallback cannot answer, and the case pins that as a refusal.
    """
    label = "--payload with no file list fails instead of skipping the check"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        source = root / "installed"
        for relative, identity, dependencies in PAYLOAD_IMAGES:
            path = source / "var/jb" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_signed_macho(identity, dependencies))
        output = root / "out.deb"
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
             "--id", "com.example.no-such-package", "--version", "0.0.0-test",
             "--output", str(output)],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 with no list to check against\n{combined}")
            return
        if "No file list to check the payload against" not in combined:
            failures.append(f"{label}: the failure does not explain the missing list\n{combined}")
            return
        if output.exists():
            failures.append(f"{label}: a package was written anyway")
            return
    print(f"PASS {label}")


def case_payload_rootful_prefix() -> None:
    """A rootful --payload tree has no prefix, and the shell must pass it as empty.

    "${PREFIX:-/var/jb}" substitutes when the value is empty, so the rootful run used to
    hand /var/jb to the rewriter while the tree sat at the root, and every image was
    reported absent.  Rootful is the default mode, so this is the plain invocation.
    """
    label = "--payload in rootful mode passes an empty prefix, not /var/jb"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        source = root / "rootful"
        for relative, identity, dependencies in PAYLOAD_IMAGES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_signed_macho(identity, dependencies))
        manifest = write_manifest(root, [f"/{relative}" for relative, _, _ in PAYLOAD_IMAGES])
        output = root / "out.deb"
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--payload", str(source),
             "--version", "0.0.0-test", "--expect-manifest", str(manifest),
             "--output", str(output)],
            capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "is not in the payload" in combined:
            failures.append(f"{label}: the rewriter was given a prefix the tree lacks\n{combined}")
            return
        if "Rootful mode: install prefix = /" not in combined:
            failures.append(f"{label}: the run did not take the rootful branch\n{combined}")
            return
        if not output.exists():
            failures.append(f"{label}: no package was written\n{combined}")
            return
        unpacked = root / "unpacked"
        subprocess.run(["dpkg-deb", "-x", str(output), str(unpacked)], check=True)
        if not (unpacked / PAYLOAD_IMAGES[0][0]).exists():
            failures.append(f"{label}: the package did not install at the root")
            return
    print(f"PASS {label}")


def package_with_manifest(root: Path, manifest_lines: list[str]):
    """Run --payload against a complete tree with a manifest the caller controls.

    The tree is complete, so nothing about the payload can be the reason to stop:
    whatever the run does, it did because of the manifest.
    """
    source = root / "installed"
    for relative, identity, dependencies in PAYLOAD_IMAGES:
        path = source / "var/jb" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_signed_macho(identity, dependencies))
    manifest = write_manifest(root, manifest_lines)
    output = root / "out.deb"
    result = subprocess.run(
        ["zsh", str(SCRIPT), "--payload", str(source), "--rootless",
         "--version", "0.0.0-test", "--expect-manifest", str(manifest),
         "--output", str(output)],
        capture_output=True, text=True,
    )
    return result, output


def case_manifest_of_directories_only() -> None:
    """A manifest that lists only directories compares no file and must stop.

    The guard used to count listed paths, and a path is either a file or a
    directory, so a list of nothing but directories passed it and the run printed
    "carries all 0 file(s)" — a success line for a check that read no file. The
    real dpkg -L does list directories alongside files, so this input is one
    --expect-manifest hands the script as written.
    """
    label = "--payload fails on a manifest that lists only directories"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        directories = ["/var/jb/Library/Frameworks",
                       "/var/jb/Library/Frameworks/WebKit.framework",
                       "/var/jb/usr/lib",
                       "/var"]
        result, output = package_with_manifest(root, directories)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 on a manifest with no file in it\n{combined}")
            return
        if "not one file" not in combined:
            failures.append(f"{label}: the failure does not say no file was listed\n{combined}")
            return
        if "carries all" in combined:
            failures.append(f"{label}: it still printed the success line\n{combined}")
            return
        if output.exists():
            failures.append(f"{label}: a package was written anyway")
def case_verify_deb_prefix_guard() -> None:
    """A wrong --rootless must fail closed rather than scan an empty tree and pass.

    The mode reads the prefix out of the archive.  Without that, --rootless against a rootful
    package would look under var/jb, find no images, and either report a vacuous success or a
    confusing zero-image error instead of saying the flag is wrong.
    """
    label = "--verify-deb refuses a flag that disagrees with the package's prefix"
    if skip_without_dpkg_deb(label):
        return
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        # A rootful package: images at the archive root, no var/jb.
        deb = make_deb(root, {
            "Library/Frameworks/WebKit.framework/WebKit":
                build_macho(((LC_LOAD_DYLIB, LIBSYSTEM),), filetype=MH_EXECUTE),
        }, prefix="")
        result = run_verify_deb(deb, "--rootless", bin_dir)
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0, a wrong prefix must fail\n{combined}")
            return
        if "prefix" not in combined:
            failures.append(f"{label}: the error does not explain the prefix mismatch\n{combined}")
            return
    print(f"PASS {label}")


def case_manifest_of_root_only() -> None:
    """A manifest holding only the install root is the same emptiness, one level up.

    dpkg -L prints the root as "/.", which is neither a file to look for nor a
    directory worth counting. Before, the filter meant to drop it tested
    path.rstrip("/") against ".", which "/." does not equal, so it was kept,
    counted as a directory, and the run printed "carries all 0 file(s)".
    """
    label = "--payload fails on a manifest that lists only the install root"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        result, output = package_with_manifest(root, ["/."])
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            failures.append(f"{label}: exit 0 on a manifest holding only /.\n{combined}")
            return
        if "not one file" not in combined:
            failures.append(f"{label}: the failure does not say no file was listed\n{combined}")
            return
        if "carries all" in combined:
            failures.append(f"{label}: it still printed the success line\n{combined}")
            return
        if output.exists():
            failures.append(f"{label}: a package was written anyway")
            return
    print(f"PASS {label}")


def case_manifest_root_line_is_dropped() -> None:
    """The root line must not be counted as a directory entry.

    dpkg -L always prints "/." first, so counting it made every real run report one
    directory more than the package has. Dropping it is what makes that count mean
    "the directories the payload lacks", which is the number a reader acts on.
    """
    label = "a real dpkg -L list counts its directories, not its root line"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        entries = [f"/var/jb/{relative}" for relative, _, _ in PAYLOAD_IMAGES]
        entries.append("/var/jb/usr/lib")
        result, output = package_with_manifest(root, ["/."] + entries)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        # One listed directory exists in the payload (/var/jb/usr/lib), so the
        # count reads 1. Reading 2 means "/." was counted as a second one.
        if "1 directory entry(s)" not in combined:
            failures.append(f"{label}: the directory count is not 1\n{combined}")
            return
    print(f"PASS {label}")


def case_webgpu_private_frameworks_spelling() -> None:
    """WebCore loads WebGPU from PrivateFrameworks, and that spelling must be rewritten too.

    WebGPU's own install name is /System/Library/Frameworks/WebGPU.framework/WebGPU, while
    WebCore reaches it through the PrivateFrameworks path with LC_LOAD_WEAK_DYLIB.  The prefix
    list held only the Frameworks spelling, so this load command was filtered out before the
    install table was consulted and the shipped WebCore kept a system path to the very image the
    package ships.  `verify` did not catch it because the prefix list it tests against was the
    same incomplete one.  The fixture is WebCore with the four edges the real image has inside
    this package; before the fix the run reported three, because the fourth never got past the
    prefix filter.
    """
    label = "WebCore's PrivateFrameworks WebGPU load command is rewritten to @rpath"
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        tool = extract_tool(root / "macho-load-paths.py")
        image = "Library/Frameworks/WebCore.framework/WebCore"
        write_destination_tree(root, {image: build_macho(
            ((LC_LOAD_WEAK_DYLIB, WEBGPU_PRIVATE),
             (LC_LOAD_WEAK_DYLIB, ANGLE),
             (LC_LOAD_WEAK_DYLIB, WEBRTC),
             (LC_LOAD_DYLIB, JSC)))})

        result = run_tool(tool, "rewrite", root)
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            failures.append(f"{label}: exit {result.returncode}\n{combined}")
            return
        if "Rewrote 4 load command(s) across 1 image(s)." not in combined:
            failures.append(f"{label}: the private spelling was not rewritten\n{combined}")
            return
        names = [name for _, name in load_commands_bytes((root / "var/jb" / image).read_bytes())]
        if WEBGPU_PRIVATE in names:
            failures.append(f"{label}: WebCore still names {WEBGPU_PRIVATE}")
            return
        if "@rpath/WebGPU.framework/WebGPU" not in names:
            failures.append(f"{label}: WebCore does not name @rpath/WebGPU.framework/WebGPU "
                            f"(it names {names})")
            return
        verified = run_tool(tool, "verify", root)
        if verified.returncode != 0 or "Error" in verified.stdout + verified.stderr:
            failures.append(f"{label}: verify rejected the rewritten tree\n"
                            f"{verified.stdout}{verified.stderr}")
            return
    print(f"PASS {label}")


def load_commands_bytes(data: bytes) -> list[tuple[int, str | None]]:
    """Every load command kind paired with its name, or None for a command with no lc_str."""
    count, _ = struct.unpack_from("<II", data, 16)
    commands: list[tuple[int, str | None]] = []
    offset = 32
    for _ in range(count):
        kind, size = struct.unpack_from("<II", data, offset)
        name = None
        if kind in (LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            name_offset, = struct.unpack_from("<I", data, offset + 8)
            start = offset + name_offset
            name = data[start:data.index(b"\0", start)].decode()
        commands.append((kind, name))
        offset += size
    return commands


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--sign":
        # The signer stand-ins call back into this module so the fixtures carry seals the packaging
        # script's post-condition can actually read.  ldid's shape is `ldid -S[-e entitlements]
        # file`; rcodesign's is `rcodesign sign [--entitlements-xml-file e] in out`, so the last
        # argument that is not a flag is the file to sign in both.
        arguments = [argument for argument in sys.argv[2:] if not argument.startswith("-")]
        if not arguments:
            return 2
        sign_fixture(Path(arguments[-1]))
        return 0

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
    case_nonstandard_lc_str()
    case_corrupted_name_is_rejected()
    case_rewrite_already_rpath_linked()
    case_rewrite_rejects_malformed_name()
    case_header_pad_atomicity()
    case_truncated_image()
    case_payload_mode()
    case_payload_mode_wrong_root()
    case_payload_drops_a_listed_file()
    case_payload_needs_version()
    case_payload_needs_a_manifest()
    case_payload_rootful_prefix()
    case_manifest_of_directories_only()
    case_manifest_of_root_only()
    case_manifest_root_line_is_dropped()
    case_webgpu_private_frameworks_spelling()
    case_signer_that_does_nothing()
    case_resign_is_recorded()
    case_rcodesign_path()
    case_rcodesign_writes_no_output()
    case_byte_identical_note()
    case_stale_seal_is_rejected()
    case_survey_follows_the_rewrite()
    case_step_banners_are_ordered()
    case_verify_deb_rejects_unrewritten()
    case_verify_deb_accepts_rewritten()
    case_verify_deb_prefix_guard()
    case_tar_replaces_old_output()
    for package_type in ("deb", "tar"):
        case_archive_failure(package_type, "copy")
        case_archive_failure(package_type, "archive")
        case_archive_failure(package_type, "archive", stale=False)

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
