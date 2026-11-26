#!/usr/bin/env python3
"""Regenerate the two `MessageNames.h` headers the translation tables are built from.

`scripts/gen-ipc-message-number-tables.py` reads one `MessageNames.h` per tree and joins them on
message name. Those headers are build products, so they are not in either checkout: each one is
written by the tag's own `Source/WebKit/Scripts/generate-message-receiver.py` from the
`.messages.in` files that the tag's `DerivedSources.make` lists in `MESSAGE_RECEIVERS`. This script
performs that generation from two git tags and writes both headers, so the tables can be rebuilt
from tags alone rather than from a scratch file left over from an earlier pass.

The inputs are two WebKit tags, and the only thing taken from a checkout is the tag's own tree:

* `Source/WebKit/DerivedSources.make`, read for its `MESSAGE_RECEIVERS` variable. That variable is
  the authority on which receivers are compiled. Globbing `Source/WebKit/**/*.messages.in` instead
  also picks up `Scripts/webkit/tests/` and several receivers that no iOS build compiles, which
  shifts every number after the first extra receiver; `docs/ios16-ipc-message-number-translation.md`
  records the 4-position shift that produced.
* `Source/WebKit/<receiver>.messages.in` for each entry of that variable, and
  `Source/WebKit/Scripts/`, which holds the generator plus the `webkit` package it imports.

The generator is run as the build runs it, with the tag's `Scripts` directory as `PYTHONPATH` and a
scratch working directory as the output location:

    PYTHONPATH=<tag>/Source/WebKit/Scripts python3 <tag>/Source/WebKit/Scripts/generate-message-receiver.py \\
        <srcdir> <receivers...>

It writes `MessageNames.h` into the current directory along with one header and one
`MessageReceiver.cpp` per receiver; only `MessageNames.h` is kept.

The 16.1 tag's generator emits no preprocessor conditionals, so a position in its file is the value
the compiler assigns. The 18.3 tag's generator wraps groups of enumerators in `#if` blocks, so a
position there is only the value under a configuration that keeps every entry. Both facts are
asserted below rather than assumed: the header's own `#if` count is checked against the expected
number for that tree, because a generator change that started emitting conditionals on the 16.1 side
would make every literal in the tables wrong without changing their shape.

Usage:
    scripts/regen-ipc-message-number-headers.py --repo PATH --names-old-out PATH --names-new-out PATH

`--repo` is a git checkout that has both tags, and it may be a partial clone: the `.messages.in`
blobs are read with `git cat-file --batch`, which a promisor remote fills on demand. Leaving any of
the three paths out is an error, because a default would let a run silently use a stale header from
a different tag. Every failure — a missing tag, a receiver whose `.messages.in` is absent, a
generator that writes no header — exits non-zero with the reason on stderr.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

OLD_TAG = 'releases/Apple/Safari-16.1-iOS-16.1'
NEW_TAG = 'releases/Apple/Safari-18.3.1-iOS-18.3.2'

# The `.messages.in` directories that belong to the tree itself. Read out of `DerivedSources.make`
# rather than from a list here, so a tag that adds a receiver needs no change to this script.
MESSAGE_RECEIVERS = re.compile(r'^MESSAGE_RECEIVERS\s*=\s*\\\n(?P<body>.*?)^#', re.MULTILINE | re.DOTALL)

ENUM_BODY = re.compile(
    r'enum\s+class\s+MessageName\s*:\s*uint16_t\s*\{(?P<body>.*?)\n\};',
    re.DOTALL,
)

NON_MESSAGE_NAMES = frozenset({
    'Count',
    'Invalid',
    'Last',
    'FirstSynchronous',
    'LastAsynchronous',
})


class RegenError(Exception):
    """Raised when a tag cannot produce a header, or produces one this script cannot vouch for."""


@dataclass(frozen=True)
class Tree:
    """One tag's facts, as measured from the header it generated."""

    tag: str
    messages: int
    guard_blocks: int
    anchors: dict[str, int]


def run_git(repo: Path, arguments: list[str], *, stdin: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ['git', '-C', str(repo), *arguments],
        input=stdin,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RegenError(
            'git %s failed in %s: %s'
            % (' '.join(arguments), repo, completed.stderr.decode('utf-8', 'replace').strip())
        )
    return completed.stdout


def resolve_tag(repo: Path, tag: str) -> str:
    """The commit a tag points at, peeled, so a caller sees which commit produced the header.

    Passing the tag name to `git cat-file` works, but an annotated tag and its commit share one name
    in the output, and the reported commit is what a later reader checks the numbers against.
    """
    return run_git(repo, ['rev-parse', '--verify', '%s^{commit}' % tag]).decode().strip()


def read_blob(repo: Path, revision: str, path: str) -> bytes:
    return run_git(repo, ['cat-file', 'blob', '%s:%s' % (revision, path)])


def read_message_receivers(make: str, tag: str) -> list[str]:
    match = MESSAGE_RECEIVERS.search(make)
    if not match:
        raise RegenError('%s: Source/WebKit/DerivedSources.make has no MESSAGE_RECEIVERS block' % tag)
    receivers = []
    for line in match.group('body').split('\n'):
        entry = line.strip().rstrip('\\').strip()
        if entry:
            receivers.append(entry)
    if not receivers:
        raise RegenError('%s: MESSAGE_RECEIVERS is empty' % tag)
    return receivers


def extract_messages_in(repo: Path, revision: str, receivers: list[str], source_root: Path) -> None:
    """Write every receiver's `.messages.in` under `source_root`.

    Batched through one `git cat-file --batch`, then any entry that came back `missing` is retried
    one path at a time. Both halves are needed. The batch call is what keeps a full checkout fast;
    `git cat-file --batch` does not ask the promisor remote for an object it does not have locally,
    so on a partial clone most entries answer `missing` and the per-path retry is what actually
    fetches them. `git cat-file blob <rev>:<path>` does go to the remote, which is why the retry uses
    it. A path absent from both attempts is reported with the receiver's name.
    """
    queries = ''.join('%s:Source/WebKit/%s.messages.in\n' % (revision, receiver) for receiver in receivers)
    output = run_git(repo, ['cat-file', '--batch'], stdin=queries.encode())

    position = 0
    bodies: dict[str, bytes] = {}
    for receiver in receivers:
        newline = output.index(b'\n', position)
        header = output[position:newline].decode()
        position = newline + 1
        fields = header.split()
        if len(fields) != 3 or fields[1] != 'blob':
            continue
        size = int(fields[2])
        bodies[receiver] = output[position:position + size]
        position += size + 1

    unreachable = []
    for receiver in receivers:
        if receiver in bodies:
            continue
        path = 'Source/WebKit/%s.messages.in' % receiver
        completed = subprocess.run(
            ['git', '-C', str(repo), 'cat-file', 'blob', '%s:%s' % (revision, path)],
            capture_output=True,
        )
        if completed.returncode != 0:
            unreachable.append(receiver)
            continue
        bodies[receiver] = completed.stdout

    if unreachable:
        raise RegenError(
            '%s: no .messages.in blob for %d receiver(s): %s'
            % (revision, len(unreachable), ', '.join(unreachable[:5]))
        )

    for receiver, body in bodies.items():
        path = source_root / 'Source' / 'WebKit' / (receiver + '.messages.in')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def extract_scripts(repo: Path, revision: str, scripts_root: Path) -> None:
    """Write the tag's `Source/WebKit/Scripts/` tree, which holds the generator and its package.

    Every file is copied rather than a subset: the generator imports `webkit.messages`,
    `webkit.parser` and `webkit.model`, and which of their siblings those pull in is the tag's
    business, not this script's.
    """
    listing = run_git(
        repo, ['ls-tree', '-r', '--name-only', revision, 'Source/WebKit/Scripts/']
    ).decode().split('\n')
    paths = [path for path in listing if path.strip()]
    if not paths:
        raise RegenError('%s: Source/WebKit/Scripts/ is empty' % revision)
    for path in paths:
        relative = path[len('Source/WebKit/Scripts/'):]
        target = scripts_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_blob(repo, revision, path))


def run_generator(scripts_root: Path, source_root: Path, receivers: list[str], output_dir: Path) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            str(scripts_root / 'generate-message-receiver.py'),
            str(source_root / 'Source' / 'WebKit'),
            *receivers,
        ],
        cwd=output_dir,
        capture_output=True,
        env={'PYTHONPATH': str(scripts_root), 'PATH': '/usr/bin:/bin'},
    )
    if completed.returncode != 0:
        raise RegenError(
            'generate-message-receiver.py exited %d: %s'
            % (completed.returncode, completed.stderr.decode('utf-8', 'replace').strip()[:2000])
        )
    header = output_dir / 'MessageNames.h'
    if not header.exists():
        raise RegenError('generate-message-receiver.py exited 0 but wrote no MessageNames.h')
    return header


def measure(tag: str, header: Path, anchors: tuple[str, ...]) -> Tree:
    """Count the header's messages and `#if` blocks, and read each anchor's position.

    Positions are counted from the declaration order, which is the value the compiler assigns on a
    tree whose generator emits no conditionals. `measure` records the `#if` count beside them so the
    caller can refuse a tree where that assumption stops holding.
    """
    text = header.read_text(encoding='utf-8')
    match = ENUM_BODY.search(text)
    if not match:
        raise RegenError('%s: %s has no `enum class MessageName : uint16_t`' % (tag, header.name))
    body = match.group('body')

    positions: dict[str, int] = {}
    guard_blocks = 0
    for raw in body.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#if'):
            guard_blocks += 1
            continue
        if line.startswith('#'):
            continue
        line = line.lstrip(',').rstrip(',').strip()
        name, separator, _value = line.partition('=')
        name = name.strip()
        if name in NON_MESSAGE_NAMES:
            continue
        positions[name] = len(positions)

    absent = [anchor for anchor in anchors if anchor not in positions]
    if absent:
        raise RegenError('%s: %s does not enumerate %s' % (tag, header.name, ', '.join(absent)))

    return Tree(
        tag=tag,
        messages=len(positions),
        guard_blocks=guard_blocks,
        anchors={anchor: positions[anchor] for anchor in anchors},
    )


def describe(tree: Tree, commit: str, header: Path) -> str:
    import hashlib

    digest = hashlib.sha256(header.read_bytes()).hexdigest()
    anchors = ' '.join('%s=%d' % (name, value) for name, value in tree.anchors.items())
    return (
        '%s (%s): messages=%d #if=%d %s sha256=%s'
        % (tree.tag, commit[:12], tree.messages, tree.guard_blocks, anchors, digest)
    )


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Regenerate the 16.1 and 18.3 MessageNames.h headers from their tags, which is what '
            'scripts/gen-ipc-message-number-tables.py reads.'
        ),
    )
    parser.add_argument('--repo', required=True, type=Path,
                        help='a git checkout carrying both tags (may be a partial clone)')
    parser.add_argument('--names-old-out', required=True, type=Path,
                        help='write the 16.1 MessageNames.h here')
    parser.add_argument('--names-new-out', required=True, type=Path,
                        help='write the 18.3 MessageNames.h here')
    parser.add_argument('--old-tag', default=OLD_TAG, help='the older tag (default: %(default)s)')
    parser.add_argument('--new-tag', default=NEW_TAG, help='the newer tag (default: %(default)s)')
    return parser.parse_args(argv)


def generate(repo: Path, tag: str, anchors: tuple[str, ...], expected_guards: int, scratch: Path) -> tuple[Tree, Path]:
    """Run one tag end to end and return its measurements plus the header it produced."""
    commit = resolve_tag(repo, tag)
    receivers = read_message_receivers(
        read_blob(repo, commit, 'Source/WebKit/DerivedSources.make').decode('utf-8'), tag
    )

    tree_root = scratch / commit
    source_root = tree_root / 'src'
    scripts_root = tree_root / 'scripts'
    output_dir = tree_root / 'generated'
    for directory in (source_root, scripts_root, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    extract_messages_in(repo, commit, receivers, source_root)
    extract_scripts(repo, commit, scripts_root)
    header = run_generator(scripts_root, source_root, receivers, output_dir)

    measured = measure(tag, header, anchors)
    if measured.guard_blocks != expected_guards:
        raise RegenError(
            '%s: the generated header has %d #if blocks, expected %d; the tables read one side by '
            'declaration order, which only holds while this count is 0'
            % (tag, measured.guard_blocks, expected_guards)
        )
    return measured, header


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    # The two anchors the tables pin as literals, in the order the tables name them.
    anchors = ('InitializeConnection', 'GPUConnectionToWebProcess_EnsureAudioSession',
               'WrappedAsyncMessageForTesting')

    try:
        with tempfile.TemporaryDirectory(prefix='regen-ipc-headers-') as directory:
            scratch = Path(directory)
            old, old_header = generate(arguments.repo, arguments.old_tag, anchors, 0, scratch)
            new, new_header = generate(arguments.repo, arguments.new_tag, anchors, 703, scratch)

            for tree, header, destination in (
                (old, old_header, arguments.names_old_out),
                (new, new_header, arguments.names_new_out),
            ):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(header.read_bytes())
                print(describe(tree, resolve_tag(arguments.repo, tree.tag), header))
    except RegenError as error:
        print('regen-ipc-message-number-headers: %s' % error, file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
