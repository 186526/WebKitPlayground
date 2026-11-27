#!/usr/bin/env python3
"""Emit the canonical CI build-info record for a DEB and its verified inputs."""

import os
import sys
from pathlib import Path


def main() -> None:
    archive, archive_digest, proxy_source_digest = sys.argv[1:]
    fields = {
        "archive": Path(archive).name,
        "sha256": archive_digest,
        "proxy_source_sha256": proxy_source_digest,
        "webkit_ref": os.environ["WEBKIT_REF"],
        "webkit_commit": os.environ["WEBKIT_COMMIT"],
        "workflow_commit": os.environ["GITHUB_SHA"],
        "apply_patch": os.environ["APPLY_PATCH"],
        "xcode": os.environ["XCODE"],
        "arch": os.environ["WK_BUILD_ARCH"],
        "max_minos": os.environ["WK_BUILD_DEPLOYMENT_TARGET"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    }
    for name, value in fields.items():
        if not value or "\r" in value or "\n" in value:
            sys.exit(f"Invalid build-info value for {name}")
    for name, value in fields.items():
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
