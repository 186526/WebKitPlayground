#!/usr/bin/env python3
# Reads a process's real environment list, the only way to answer "does this
# process carry a DYLD name dyld will read". `launchctl print` answers a
# different question: it prints the pid's *domain* environment, and a
# runningboardd app carries only the __XPC_ spelling while its domain shows the
# bare one. The two disagree, so neither substitutes for the other.
#
# Usage:
#
#   python3 inspect-process-env.py <pid> [<pid> ...]
#   python3 inspect-process-env.py --check <pid> [<pid> ...]
#
# Without --check it prints every entry beginning with DYLD or __XPC_, whole,
# plus a count. With --check it prints the bare and prefixed spellings as
# separate counts, so "bare 0 prefixed 0" is distinguishable from "bare 0
# prefixed 1" — the distinction a substring grep cannot make.
#
# Two ways this measurement goes wrong, both of which this script refuses to
# paper over:
#
# 1. Substring counting. __XPC_DYLD_FRAMEWORK_PATH contains DYLD_FRAMEWORK_PATH,
#    so `grep -c DYLD_FRAMEWORK_PATH` over an envp reports the prefixed spelling
#    as a bare-name hit. Measured on the device 2026-11-26:
#      printf '%s\n' '__XPC_DYLD_FRAMEWORK_PATH=/x' | grep -c   DYLD_FRAMEWORK_PATH   -> 1
#      printf '%s\n' '__XPC_DYLD_FRAMEWORK_PATH=/x' | grep -cE '^DYLD_FRAMEWORK_PATH=' -> 0
#    This script compares each entry against "NAME=" from its first byte, so the
#    prefixed spelling can only ever match the __XPC_ counter.
#
# 2. Silent truncation. On this device KERN_PROCARGS2 answers a complete buffer
#    that ends after the last argument string, with no error and no envp at all,
#    for a process that was started from a shell. Measured on the device
#    2026-11-26: a child started with
#    DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride in its environment reads
#    back as 53 bytes containing no such bytes. On the same run every process
#    launchd spawned (258 of 258) answered with an envp region, and 9 of the 10
#    others answered without one — the exception was the scan reading itself. A
#    zero-length envp is therefore a property of how the process was spawned,
#    not of what it carries, so this script exits 2 instead of reporting zero
#    names. "Not tested" must never be printed as "absent".
#
# Needs root for another user's process: run it under sudo -S -p "" on the
# device. Exit codes, which differ for a reason rather than as detail:
#
#   0  read, and a bare spelling dyld reads is present
#   1  read, and no spelling of any of the four is present
#   3  read, and only the __XPC_ prefixed spelling is present. dyld does not
#      read that spelling, so this is not success; but it is not 1 either,
#      because that shape is the untested case rather than a refutation
#   2  the environment could not be read, so no conclusion is available
#
# "Not tested" must never be reported as "absent", and a spelling dyld cannot
# read must never be reported as one it can.
import ctypes
import struct
import sys

CTL_KERN = 1
KERN_PROCARGS2 = 49
# A larger answer than this is not a plausible argument vector; the cap keeps a
# bad size from turning into a multi-gigabyte allocation.
MAX_PROBE_SIZE = 4 * 1024 * 1024

BARE_NAMES = ("DYLD_FRAMEWORK_PATH", "DYLD_LIBRARY_PATH")
PREFIXED_NAMES = (
    "__XPC_DYLD_FRAMEWORK_PATH",
    "__XPC_DYLD_LIBRARY_PATH",
)

libc = ctypes.CDLL(None, use_errno=True)


def resolve(symbol):
    # hasattr on the CDLL, not dlsym with handle -2. Handle -2 is RTLD_DEFAULT
    # on Darwin but RTLD_NEXT on glibc, where the call segfaults — so a dlsym
    # probe would crash on the build host instead of refusing cleanly.
    #
    # AGENTS.md §六 warns that getattr on a CDLL answers absent for any symbol
    # on this device. Measured 2026-11-26 that it does not apply to CDLL(None):
    # hasattr answers True for sysctl, csops and malloc there, so it separates
    # the two hosts correctly. Attribute lookup is the right question here
    # because the script either finds the symbol or must not run at all.
    return hasattr(libc, symbol)


def require_darwin():
    # Name the cause when the Darwin-only call is missing, so a lookup failure
    # does not read like a defect in this script.
    if not resolve("sysctl") or not resolve("malloc"):
        raise SystemExit(
            "inspect-process-env: sysctl and malloc are not both in this libc, "
            "so this is not the device. The script reads Darwin-only "
            "interfaces and runs on the device, not on the Linux build host.")


def read_procargs(pid):
    """Return the raw KERN_PROCARGS2 buffer for pid, or None when unreadable."""
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_int(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return None
    if size.value <= 0 or size.value > MAX_PROBE_SIZE:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    filled = ctypes.c_int(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(filled), None, 0) != 0:
        return None
    return buffer.raw[: filled.value]


def split_procargs(raw):
    """Return the envp entries, or None when the buffer is shorter than argc.

    The layout is argc, the executable path, argc argument strings, then the
    envp strings. An empty result is not reported as "carries no names": the
    device returns the same empty shape for a process launchd did not spawn, so
    an empty list means "this read cannot see the environment", and `describe`
    turns it into an error rather than a negative result.
    """
    if len(raw) < 4:
        return None
    argc = struct.unpack("<i", raw[:4])[0]
    position = 4
    # The executable path is followed by padding NULs, so skip runs of them
    # rather than assuming one separator byte.
    while position < len(raw) and raw[position] != 0:
        position += 1
    while position < len(raw) and raw[position] == 0:
        position += 1
    # argc of 0 is not a hole in the layout: there were no arguments to skip,
    # so the walk falls through to the environment directly.
    for _ in range(max(argc, 0)):
        while position < len(raw) and raw[position] != 0:
            position += 1
        position += 1
    while position < len(raw) and raw[position] == 0:
        position += 1
    entries = []
    while position < len(raw):
        end = raw.find(b"\0", position)
        if end < 0:
            break
        entry = raw[position:end]
        if not entry:
            break
        entries.append(entry.decode(errors="replace"))
        position = end + 1
    return entries


def names_in(entries):
    """Count each spelling by anchored prefix, never by substring."""
    counts = {}
    for name in BARE_NAMES + PREFIXED_NAMES:
        needle = name + "="
        counts[name] = sum(1 for e in entries if e.startswith(needle))
    return counts


def describe(pid):
    """Return (entries, error). entries is None whenever no conclusion is safe."""
    raw = read_procargs(pid)
    if raw is None:
        return None, "KERN_PROCARGS2 returned nothing (no such process, or not permitted)"
    entries = split_procargs(raw)
    if entries is None:
        return None, "argument vector shorter than its own argc field"
    if not entries:
        return None, (
            "envp region empty: %d bytes, arguments end at the last byte. On "
            "this device that shape is what a process started from a shell "
            "returns, not what a process carrying no names returns, so it "
            "describes how the process was spawned rather than what it "
            "carries" % len(raw))
    return entries, None


def carries_name_dyld_reads(counts):
    """True only when a bare spelling is present.

    Deliberately excludes PREFIXED_NAMES. dyld does not read
    __XPC_DYLD_FRAMEWORK_PATH — it is a separate handoff mechanism — so a
    process carrying only that spelling is in exactly the position the caller
    needs to hear about, not the one it needs to pass. Counting all four here
    would report success for the one shape whose effect is undetermined, and
    that is the error this script exists to remove rather than to reproduce.
    """
    return any(counts[name] > 0 for name in BARE_NAMES)


def report_pid(pid, check):
    """Return "bare", "prefixed" or None (unreadable), in exit-code terms."""
    entries, error = describe(pid)
    print("pid %d" % pid)
    if error is not None:
        print("  envp: unreadable: %s" % error)
        return None
    counts = names_in(entries)
    print("  envp entries: %d" % len(entries))
    if check:
        for name in BARE_NAMES + PREFIXED_NAMES:
            print("    %-28s %d" % (name, counts[name]))
        print("  bare %d  prefixed %d" % (
            sum(counts[n] for n in BARE_NAMES),
            sum(counts[n] for n in PREFIXED_NAMES),
        ))
    else:
        relevant = [e for e in entries if e.startswith(("DYLD", "__XPC"))]
        for entry in relevant:
            print("    %s" % entry)
        if not relevant:
            print("    (no entry begins with DYLD or __XPC)")
    if carries_name_dyld_reads(counts):
        return "bare"
    if any(counts[name] > 0 for name in PREFIXED_NAMES):
        # Carries a spelling, but not one dyld reads. Distinct from "none",
        # because it is the shape whose effect is unverified, not a refutation.
        print("  note: only the __XPC_ spelling is present; dyld does not read "
              "it, and whether xpcproxy hands it over is unverified")
        return "prefixed"
    return "none"


def main(argv):
    check = False
    pids = []
    for argument in argv[1:]:
        if argument == "--check":
            check = True
            continue
        try:
            pids.append(int(argument))
        except ValueError:
            print("inspect-process-env: %s is not a pid" % argument,
                  file=sys.stderr)
            return 2
    if not pids:
        print(__doc__ or "", file=sys.stderr)
        return 2
    require_darwin()
    results = [report_pid(pid, check) for pid in pids]
    if any(result is None for result in results):
        # An unreadable environment is not a negative result. Say so with the
        # exit code, because that is what a script will branch on.
        return 2
    if any(result == "bare" for result in results):
        return 0
    if any(result == "prefixed" for result in results):
        # Distinct from 1 on purpose. Exit 1 would say "this process carries no
        # spelling, so env delivery was not the cause"; exit 3 says "it carries
        # the one dyld cannot read, which is the untested case". Collapsing the
        # two is how a gap becomes a negative result.
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
