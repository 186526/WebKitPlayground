#!/usr/bin/env python3
"""Regression tests for scripts/inspect-process-env.py.

Two defects are pinned, both of which produced wrong readings in this repo before:

  counting by substring, where __XPC_DYLD_FRAMEWORK_PATH is reported as a bare
  DYLD_FRAMEWORK_PATH hit — so "the process carries the bare name" was asserted
  from a count that only ever saw the prefixed spelling;

  reading an empty envp region as "carries no names", where the device answers
  exactly that shape for a process it was not asked to spawn with — so "no
  names" and "not visible" came out the same.

The parser is exercised directly, because the buffer shapes that produce these
readings are hard to reproduce on a device on demand and trivial to write down.
The script's Darwin-only path is not touched here; it refuses to run on the
build host by design, which the last test pins.

Usage: python3 scripts/test-inspect-process-env.py
"""
from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "inspect-process-env.py"


def load_module():
    spec = importlib.util.spec_from_file_location("inspect_process_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def procargs(*argv, env=(), path=b"/var/jb/usr/bin/probe"):
    """Build a KERN_PROCARGS2 buffer the way the kernel lays it out."""
    raw = struct.pack("<i", len(argv))
    raw += path + b"\0"
    raw += b"\0\0\0"
    for item in argv:
        raw += item + b"\0"
    raw += b"\0\0\0"
    for item in env:
        raw += item + b"\0"
    return raw


def main():
    module = load_module()
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print("ok    %s" % label)
        else:
            print("FAIL  %s%s" % (label, (": " + detail) if detail else ""))
            failures.append(label)

    # A prefixed name must not be counted as a bare name. This is the whole
    # point of the script: the substring form answers 1 for this buffer.
    raw = procargs(env=(b"__XPC_DYLD_FRAMEWORK_PATH=/overlay",))
    entries = module.split_procargs(raw)
    counts = module.names_in(entries)
    check("prefixed entry yields bare count 0", counts["DYLD_FRAMEWORK_PATH"] == 0,
          str(counts))
    check("prefixed entry yields prefixed count 1",
          counts["__XPC_DYLD_FRAMEWORK_PATH"] == 1, str(counts))
    check("the substring the old reading used does match this buffer",
          b"DYLD_FRAMEWORK_PATH" in raw)

    # The bare name still has to be found, or the anchor would pass by
    # refusing everything.
    raw = procargs(env=(b"DYLD_FRAMEWORK_PATH=/overlay",))
    counts = module.names_in(module.split_procargs(raw))
    check("bare entry yields bare count 1", counts["DYLD_FRAMEWORK_PATH"] == 1,
          str(counts))
    check("bare entry yields prefixed count 0",
          counts["__XPC_DYLD_FRAMEWORK_PATH"] == 0, str(counts))

    # Both spellings present: each counted once, neither folded into the other.
    raw = procargs(env=(b"DYLD_FRAMEWORK_PATH=/a",
                        b"__XPC_DYLD_FRAMEWORK_PATH=/b"))
    counts = module.names_in(module.split_procargs(raw))
    check("both spellings counted separately",
          counts["DYLD_FRAMEWORK_PATH"] == 1
          and counts["__XPC_DYLD_FRAMEWORK_PATH"] == 1, str(counts))

    # A name that merely starts with the same bytes but differs after "=" must
    # not count; anchoring on "NAME=" rather than "NAME" is what decides this.
    raw = procargs(env=(b"DYLD_FRAMEWORK_PATH_EXTRA=/x",))
    counts = module.names_in(module.split_procargs(raw))
    check("name sharing a prefix but not the '=' does not count",
          counts["DYLD_FRAMEWORK_PATH"] == 0, str(counts))

    # argc of 0: no arguments to skip, the environment follows the path.
    raw = procargs(env=(b"DYLD_LIBRARY_PATH=/lib",))
    counts = module.names_in(module.split_procargs(raw))
    check("argc 0 still reaches the environment",
          counts["DYLD_LIBRARY_PATH"] == 1, str(counts))

    # An empty environment is a shape the device really produces, and the
    # parser must not invent entries for it.
    raw = procargs(b"15")
    check("empty environment parses to no entries",
          module.split_procargs(raw) == [])

    # A buffer shorter than its own argc field is a parse failure, not empty.
    check("truncated header returns None",
          module.split_procargs(b"\x02\x00") is None)

    # The device answers an empty envp region for a process it was not the
    # spawner of. describe() must turn that into an error, because reporting 0
    # names would read as "carries nothing" and be false: a child started with
    # DYLD_FRAMEWORK_PATH in its environment reads back exactly this shape.
    real_read = module.read_procargs
    module.read_procargs = lambda pid: procargs(b"20")
    entries, error = module.describe(1234)
    check("empty envp region is an error, not a zero count",
          entries is None and error is not None and "empty" in error,
          repr((entries, error)))
    module.read_procargs = real_read

    # An unreadable pid is also an error, and must name itself as one.
    module.read_procargs = lambda pid: None
    entries, error = module.describe(999999)
    check("unreadable pid is an error", entries is None and error is not None)
    module.read_procargs = real_read

    # On the build host the guard must refuse rather than proceed into a
    # Darwin-only call and crash. dlsym with handle -2 was tried here once and
    # segfaulted, because -2 is RTLD_NEXT on glibc; hasattr is what is pinned.
    has_sysctl = hasattr(module.libc, "sysctl")
    if not has_sysctl:
        try:
            module.require_darwin()
        except SystemExit:
            print("ok    build host refused by the Darwin guard")
        else:
            print("FAIL  build host was not refused by the Darwin guard")
            failures.append("build host refusal")
    else:
        print("skip  Darwin guard (this host has sysctl, so it is the device)")

    print()
    if failures:
        print("%d failure(s)" % len(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
