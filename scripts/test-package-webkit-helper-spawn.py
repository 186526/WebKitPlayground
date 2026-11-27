#!/usr/bin/env python3
"""Exercise the helper-spawn gate through the device deb packaging command."""

import runpy
import tempfile
from pathlib import Path


UI_MARKER = "posix_spawnattr_set_registered_ports_np"
HELPER_MARKER = "Exiting: no XPC client connection from registered ports"
MARKERS = (UI_MARKER, HELPER_MARKER)


def main() -> None:
    fixtures = runpy.run_path(str(Path(__file__).with_name("test-package-webkit-device-deb.py")))
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        fixtures["write_shims"](bin_dir)
        product = fixtures["build_product"](root)
        binary = product / "WebKit.framework" / "WebKit"
        original = binary.read_bytes()
        offset = fixtures["TEXT_OFFSET"]
        cases = {"neither": (), "ui-only": (UI_MARKER,), "helper-only": (HELPER_MARKER,), "both": MARKERS}
        for label, markers in cases.items():
            strings = (*markers, "WKWebsiteDataTypeCookies")
            if markers:
                strings += ("WK_HELPER_LAUNCH_MODE",)
            content = b"\0".join(text.encode("ascii") for text in strings)
            binary.write_bytes(original[:offset] + content + original[offset + len(content):])
            output = root / f"helper-spawn-{label}.deb"
            result = fixtures["run_script"](product, output, "--rootless", bin_dir)
            diagnostics = f"{label}:\n{result.stdout}{result.stderr}"
            if label == "both":
                assert result.returncode == 0 and output.is_file(), diagnostics
            else:
                assert result.returncode != 0 and not output.exists(), diagnostics
                assert "/Library/Frameworks/WebKit.framework/WebKit" in result.stderr, diagnostics
                assert all(marker in result.stderr for marker in MARKERS), diagnostics
    print("helper-spawn packaging cases pass")


if __name__ == "__main__":
    main()
