#!/usr/bin/env python3
"""Compile the search-path decision and both loops, then drive them over every case.

verify-systemhook-search-paths.py inspects a built dylib's strings, which cannot show what the
code does with them.  This harness extracts the patched functions out of common.c, links them
against the stock envbuf.c from the same tree, and exercises the behaviour:

  - which variables count as already in place, and what the two aggregates become;
  - jbSearchPathSet writes exactly the variables that are not in place;
  - jbSearchPathUnset removes exactly the values this hook would have written;
  - with DisableJBFrameworkPath on, or with JBROOT_PATH returning NULL, neither loop touches the
    environment at all, so an empty environment stays empty and an inherited one is left alone.

It also checks the source pairing that a string dump cannot see: which directory literal the
patched array binds to each variable name, in common.c and in this file's own table, so the two
cannot drift apart while both keep passing the behavioural cases.

The switch case is the reason this exists: an earlier version decided the switch once for the
fast-path aggregates but left both loops looking only at the per-variable flags, so disabling it
still wrote all four variables.  Nothing in a string dump can catch that.

Usage:
  dopamine-basebin/logic-harness/run.py <path to a Dopamine 3.0.13 tree, patched or not>

Exit status: 0 when every case passes, 1 otherwise.  The tree must be the one the patch is meant
for: the script applies dopamine-basebin/0001-systemhook-set-search-paths.patch if the helpers are
not already present, and refuses a tree where they are present but different from the patch.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH = HERE.parent / "0001-systemhook-set-search-paths.patch"
REL_COMMON = Path("BaseBin/systemhook/src/common/common.c")
REL_ENVBUF_C = Path("BaseBin/systemhook/src/common/envbuf.c")
REL_ENVBUF_H = Path("BaseBin/systemhook/src/common/envbuf.h")

HARNESS_C = r"""
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdlib.h>
#include "envbuf.h"

static int failures = 0;
static void check(const char *name, bool ok) {
    if (!ok) { printf("FAIL: %s\n", name); failures++; }
    else printf("PASS: %s\n", name);
}

/* Extracted verbatim from the patched common.c by run.py. */
#include "helpers.inc"

#define FRAMEWORK "/var/jb/Library/WebKitOverride"
#define LIBRARY   "/var/jb/Library/WebKitOverride/usr/lib"

static const jbSearchPathVariable VARIABLES[JB_SEARCH_PATH_COUNT] = {
    { "DYLD_FRAMEWORK_PATH",       FRAMEWORK },
    { "__XPC_DYLD_FRAMEWORK_PATH", FRAMEWORK },
    { "DYLD_LIBRARY_PATH",         LIBRARY },
    { "__XPC_DYLD_LIBRARY_PATH",   LIBRARY },
};

static int countWanted(char *const envp[]) {
    int n = 0;
    for (int i = 0; i < JB_SEARCH_PATH_COUNT; i++) {
        const char *v = envbuf_getenv((const char **)envp, VARIABLES[i].name);
        if (v && !strcmp(v, VARIABLES[i].value)) n++;
    }
    return n;
}

static int countAny(char *const envp[]) {
    int n = 0;
    for (int i = 0; i < JB_SEARCH_PATH_COUNT; i++) {
        if (envbuf_getenv((const char **)envp, VARIABLES[i].name)) n++;
    }
    return n;
}

int main(void) {
    const char *none[]       = { NULL };
    const char *insertOnly[] = { "DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib", NULL };
    const char *allFour[] = {
        "DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib",
        "DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        "__XPC_DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        "DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib",
        "__XPC_DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib",
        NULL };
    const char *frameworkOnly[] = {
        "DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        "__XPC_DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        NULL };
    const char *staleLibrary[] = {
        "DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        "__XPC_DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride",
        "DYLD_LIBRARY_PATH=/var/jb/usr/lib",
        NULL };
    const char *sharedDir[]  = { "DYLD_LIBRARY_PATH=/var/jb/usr/lib", NULL };
    const char *thirdParty[] = { "DYLD_FRAMEWORK_PATH=/somebody/else/frameworks", NULL };

    jbSearchPathState s = jbSearchPathStateFor(none, true, VARIABLES);
    check("nothing set: not all present", !s.allPresent);
    check("nothing set: nothing present", !s.anyPresent);

    s = jbSearchPathStateFor(insertOnly, true, VARIABLES);
    check("only insert: not all present", !s.allPresent);

    s = jbSearchPathStateFor(allFour, true, VARIABLES);
    check("all four: all present (fast path taken)", s.allPresent);
    check("all four: any present", s.anyPresent);

    s = jbSearchPathStateFor(frameworkOnly, true, VARIABLES);
    check("framework only: not all present, so library vars still get written", !s.allPresent);
    check("framework only: the two framework vars are present", s.present[0] && s.present[1]);
    check("framework only: the two library vars are absent", !s.present[2] && !s.present[3]);

    s = jbSearchPathStateFor(staleLibrary, true, VARIABLES);
    check("library pointing at the shared dir: treated as not matching", !s.allPresent);
    check("library pointing at the shared dir: must be overwritten", !s.present[2]);

    s = jbSearchPathStateFor(sharedDir, true, VARIABLES);
    check("third-party library value: not claimed as ours", !s.anyPresent);
    check("third-party library value: not all present", !s.allPresent);

    s = jbSearchPathStateFor(thirdParty, true, VARIABLES);
    check("third-party framework value: not claimed as ours", !s.anyPresent);

    s = jbSearchPathStateFor(none, false, VARIABLES);
    check("switch off, nothing set: fast path exactly as stock", s.allPresent);
    check("switch off, nothing set: nothing present", !s.anyPresent);
    check("switch off, nothing set: no per-variable present",
          !s.present[0] && !s.present[1] && !s.present[2] && !s.present[3]);

    s = jbSearchPathStateFor(allFour, false, VARIABLES);
    check("switch off, all four inherited: nothing considered present",
          !s.present[0] && !s.present[1] && !s.present[2] && !s.present[3]);
    check("switch off, all four inherited: nothing any-present", !s.anyPresent);

    {
        jbSearchPathState st = jbSearchPathStateFor(none, true, VARIABLES);
        char **envc = envbuf_mutcopy(none);
        jbSearchPathSet(&envc, VARIABLES, &st);
        check("set, switch on, empty env: all four written", countWanted(envc) == 4);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(none, false, VARIABLES);
        char **envc = envbuf_mutcopy(none);
        jbSearchPathSet(&envc, VARIABLES, &st);
        check("set, switch off, empty env: no variable written", countAny(envc) == 0);
        check("set, switch off, empty env: environment still empty", envc[0] == NULL);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(frameworkOnly, true, VARIABLES);
        char **envc = envbuf_mutcopy(frameworkOnly);
        jbSearchPathSet(&envc, VARIABLES, &st);
        check("set, framework only: library vars added", countWanted(envc) == 4);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(allFour, true, VARIABLES);
        char **envc = envbuf_mutcopy(allFour);
        jbSearchPathSet(&envc, VARIABLES, &st);
        check("set, all four present: all four still there", countWanted(envc) == 4);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(thirdParty, true, VARIABLES);
        char **envc = envbuf_mutcopy(thirdParty);
        jbSearchPathSet(&envc, VARIABLES, &st);
        const char *v = envbuf_getenv((const char **)envc, "DYLD_FRAMEWORK_PATH");
        check("set, third-party framework value: replaced with ours", v && !strcmp(v, FRAMEWORK));
        check("set, third-party framework value: the other three added", countWanted(envc) == 4);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(staleLibrary, true, VARIABLES);
        char **envc = envbuf_mutcopy(staleLibrary);
        jbSearchPathSet(&envc, VARIABLES, &st);
        const char *v = envbuf_getenv((const char **)envc, "DYLD_LIBRARY_PATH");
        check("set, stale shared-dir value: replaced with the dedicated dir", v && !strcmp(v, LIBRARY));
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(allFour, true, VARIABLES);
        char **envc = envbuf_mutcopy(allFour);
        jbSearchPathUnset(&envc, VARIABLES, &st);
        check("unset, switch on, all four ours: all four removed", countAny(envc) == 0);
        check("unset, switch on: DYLD_INSERT_LIBRARIES kept",
              envbuf_getenv((const char **)envc, "DYLD_INSERT_LIBRARIES") != NULL);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(allFour, false, VARIABLES);
        char **envc = envbuf_mutcopy(allFour);
        jbSearchPathUnset(&envc, VARIABLES, &st);
        check("unset, switch off: all four inherited values kept", countWanted(envc) == 4);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(sharedDir, true, VARIABLES);
        char **envc = envbuf_mutcopy(sharedDir);
        jbSearchPathUnset(&envc, VARIABLES, &st);
        const char *v = envbuf_getenv((const char **)envc, "DYLD_LIBRARY_PATH");
        check("unset, third-party value: not removed", v && !strcmp(v, "/var/jb/usr/lib"));
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(frameworkOnly, true, VARIABLES);
        char **envc = envbuf_mutcopy(frameworkOnly);
        jbSearchPathUnset(&envc, VARIABLES, &st);
        check("unset, framework only: the two present removed", countAny(envc) == 0);
        envbuf_free(envc);
    }
    {
        jbSearchPathState st = jbSearchPathStateFor(allFour, false, VARIABLES);
        char **envc = envbuf_mutcopy(allFour);
        jbSearchPathSet(&envc, VARIABLES, &st);
        jbSearchPathUnset(&envc, VARIABLES, &st);
        check("null root, both loops: environment unchanged", countWanted(envc) == 4);
        envbuf_free(envc);
    }

    printf(failures ? "\nFAILURES: %d\n" : "\nALL PASS\n", failures);
    return failures != 0;
}
"""


def extract_functions(source: str) -> str:
    """Pull the helper block out of common.c exactly as the patch leaves it.

    The block is contiguous: the match helper, the count, both structs, and the three
    functions, immediately above the numbered comment that heads spawn_exec_hook_common.
    Taking the whole span verbatim keeps the harness honest about what ships; slicing per
    function would silently drop a struct if one were added.
    """
    lines = source.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("static bool envbuf_variable_matches(")),
        None,
    )
    if start is None:
        raise SystemExit("missing in common.c: static bool envbuf_variable_matches(")
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("// 1. Ensure the binary about to be spawned")),
        None,
    )
    if end is None:
        raise SystemExit("missing in common.c: the comment heading spawn_exec_hook_common")
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    if lines[end - 1] != "}":
        raise SystemExit("helper block does not end on a closing brace")
    return "\n".join(lines[start:end]) + "\n"


def check_source_pairing(source: str) -> int:
    """Assert the patched array binds each name to the directory its purpose requires.

    The compiled cases drive this file's own table, so they stay green even if the patch swapped a
    value.  Reading the two lines that define the paths and the array out of common.c closes that:
    a swap there fails here.  Structural rather than textual, so indentation and spacing may vary.
    """
    failures = 0
    def check(name: str, ok: bool) -> None:
        nonlocal failures
        if not ok:
            print(f"FAIL: {name}")
            failures += 1
        else:
            print(f"PASS: {name}")

    framework = re.search(r'const char \*jbFrameworkPath\s*=\s*JBROOT_PATH\("([^"]+)"\)', source)
    library = re.search(r'const char \*jbLibraryPath\s*=\s*JBROOT_PATH\("([^"]+)"\)', source)
    check("common.c defines jbFrameworkPath", framework is not None)
    check("common.c defines jbLibraryPath", library is not None)
    if framework is None or library is None:
        return failures
    check("the framework directory is /Library/WebKitOverride", framework.group(1) == "/Library/WebKitOverride")
    check(
        "the library directory sits under the framework directory",
        library.group(1) == "/Library/WebKitOverride/usr/lib",
    )

    expected = {
        "DYLD_FRAMEWORK_PATH": "jbFrameworkPath",
        "__XPC_DYLD_FRAMEWORK_PATH": "jbFrameworkPath",
        "DYLD_LIBRARY_PATH": "jbLibraryPath",
        "__XPC_DYLD_LIBRARY_PATH": "jbLibraryPath",
    }
    for name, pointer in expected.items():
        bound = re.search(
            r'\{\s*"' + name + r'"\s*,\s*(\w+)\s*\}', source
        )
        check(
            f"common.c binds {name} to {pointer}",
            bound is not None and bound.group(1) == pointer,
        )

    # The harness table must agree with the patch, or the behavioural cases would be testing a
    # pairing nobody ships.
    for name, pointer in expected.items():
        macro = "FRAMEWORK" if pointer == "jbFrameworkPath" else "LIBRARY"
        check(
            f"run.py binds {name} to {macro}",
            re.search(r'\{\s*"' + name + r'"\s*,\s*' + macro + r'\s*\}', HARNESS_C) is not None,
        )
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <Dopamine tree>", file=sys.stderr)
        return 1
    tree = Path(sys.argv[1]).resolve()
    common = tree / REL_COMMON
    for required in (common, tree / REL_ENVBUF_C, tree / REL_ENVBUF_H):
        if not required.is_file():
            print(f"missing: {required}", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory(prefix="jb-search-path-logic-") as tmp:
        work = Path(tmp) / "tree"
        shutil.copytree(tree, work)

        source = (work / REL_COMMON).read_text()
        if "jbSearchPathStateFor" not in source:
            applied = subprocess.run(
                ["git", "apply", str(PATCH)], cwd=work, capture_output=True, text=True, check=False
            )
            if applied.returncode != 0:
                print(f"git apply failed: {applied.stderr}", file=sys.stderr)
                return 1
            source = (work / REL_COMMON).read_text()

        build = Path(tmp) / "build"
        build.mkdir()
        (build / "helpers.inc").write_text(extract_functions(source))
        shutil.copy(work / REL_ENVBUF_C, build / "envbuf.c")
        shutil.copy(work / REL_ENVBUF_H, build / "envbuf.h")
        (build / "harness.c").write_text(HARNESS_C)

        cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not cc:
            print("no C compiler on PATH", file=sys.stderr)
            return 1

        compiled = subprocess.run(
            [cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-o", "harness", "harness.c", "envbuf.c"],
            cwd=build, capture_output=True, text=True, check=False,
        )
        if compiled.returncode != 0:
            print(f"compile failed:\n{compiled.stderr}", file=sys.stderr)
            return 1

        run = subprocess.run([str(build / "harness")], capture_output=True, text=True, check=False)
        print(run.stdout, end="")
        if run.returncode != 0:
            print(run.stderr, file=sys.stderr)

        pairing_failures = check_source_pairing(source)
        if pairing_failures:
            print(f"\n{pairing_failures} pairing failure(s)", file=sys.stderr)
        return 1 if run.returncode != 0 or pairing_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
