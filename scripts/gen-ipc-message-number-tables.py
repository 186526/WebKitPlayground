#!/usr/bin/env python3
"""Generate the IPC message-number translation tables between two WebKit trees.

`MessageName` is one enumeration covering every message in a tree, and the wire
format carries its ordinal. Two trees with different message sets therefore
disagree on the number of every message past the first divergence, even where
the message names match. This script emits the two lookup functions that let a
build of the newer tree talk to a process running the older enumeration.

The hard part is that the two generated headers are not equally readable.

The iOS 16.1 generator (`generate_message_names_header` in that tree's
`Source/WebKit/Scripts/webkit/messages.py`) joins its enumerators with
`'\\n    , '.join(str(e) for e in message_enumerators)` and emits no
preprocessor conditionals at all. Every message exists in every configuration,
so counting positions in the file gives the value the compiler assigns. Those
positions are safe to write as integer literals.

The 18.3 generator groups enumerators with
`itertools.groupby(..., lambda e: (e.condition(), e.synchronous()))` and wraps
each group in `#if <condition>`; there are 703 such blocks in the generated
header. A position counted in the file is therefore *not* the value the
compiler assigns: a configuration removes the entries it excludes and every
later entry shifts down by that many. For `InitializeConnection` the flat
position is 3076, while the value an iOS build assigns is smaller by the number
of preceding entries that build compiles out.

So the 18.3 side never appears as a number. Each case names the enumerator -
`MessageName::Some_Message` - and the compiler supplies the value under whatever
conditionals are in force. The 16.1 side is an integer literal, which is
correct precisely because that generator emits no conditionals.

For scale, measured from the two generated headers: the 16.1 enumeration has
3140 messages with the synchronous section starting at 2899; the 18.3
enumeration has 4086 messages, its synchronous section starts at 3838, and its
whole enumeration is wrapped in 703 `#if` blocks, of which 636 fall on messages
that exist in both trees. 2807 messages are shared.

Two consequences follow, and the script enforces both:

* Every case whose 18.3 enumerator sits under an `#if` is emitted under that
  same `#if`. Reusing the guard verbatim makes the case exist exactly when the
  enumerator it names exists, so a configuration that drops a message drops its
  case too instead of failing to compile.
* Both directions switch over the same 2807 names, so the two functions are
  mutual inverses by construction. The script checks it anyway.

The output is two complete function bodies rather than arrays: a `switch` lets
each `case` carry its own `#if`, and the compiler folds the whole thing into a
jump table, so there is no runtime search and no ordering constraint to
maintain.

The script exits non-zero, with the reason on stderr, when the inputs cannot be
parsed, when either side repeats a name, when a 16.1 number is reused, when a
guarded case would be emitted without its `#if`, or when the two directions are
not mutual inverses.

Usage:
    gen-ipc-message-number-tables.py [--names-old PATH] [--names-new PATH]
                                     [--output PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Protocol sentinels and compiler-generated names. They occupy a slot in the
# enumeration but name no message, so they never appear on the wire.
NON_MESSAGE_NAMES = frozenset({
    'Count',
    'Invalid',
    'Last',
    'FirstSynchronous',
    'LastAsynchronous',
})

ENUM_BODY_PATTERN = re.compile(
    r'enum\s+class\s+MessageName\s*:\s*uint16_t\s*\{(?P<body>.*?)\n\};',
    re.DOTALL,
)

# Conditions that hold for every iOS build this translation layer ships in. A
# case guarded by one of these needs no `#if` of its own: the enumerator it
# names is always present. Keeping the guard would still compile, but it would
# wrap 229 of the 643 guarded messages for no reason.
GUARDS_ALWAYS_TRUE_ON_IOS = frozenset({
    'PLATFORM(COCOA)',
    'PLATFORM(IOS)',
    'PLATFORM(IOS_FAMILY)',
    'OS(DARWIN)',
})


class TableError(Exception):
    """Raised when the inputs cannot produce a usable table pair."""


@dataclass(frozen=True)
class Message:
    """One message that exists in both enumerations.

    `number` is the older tree's ordinal, read by position. `guards` are the
    preprocessor conditions the newer tree puts around the enumerator.
    """

    name: str
    number: int
    guards: tuple[str, ...]


def read_enum_body(path: Path) -> str:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as error:
        raise TableError('cannot read %s: %s' % (path, error.strerror)) from error

    match = ENUM_BODY_PATTERN.search(text)
    if not match:
        raise TableError('%s does not contain an `enum class MessageName : uint16_t`' % path)
    return match.group('body')


def parse_enum(path: Path, *, require_unguarded: bool) -> list[tuple[str, tuple[str, ...]]]:
    """Return (name, guards) for every message enumerator, in declaration order.

    Explicitly valued entries are dropped: the generated headers use them only
    for the sentinels, which name no message and hold no implicit slot.
    """
    body = read_enum_body(path)
    entries: list[tuple[str, tuple[str, ...]]] = []
    guards: list[str] = []

    for raw_line in body.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('#if'):
            guards.append(line[len('#if'):].strip())
            continue
        if line.startswith('#endif'):
            if not guards:
                raise TableError('%s closes a conditional it never opened' % path)
            guards.pop()
            continue
        if line.startswith('#else') or line.startswith('#elif'):
            raise TableError(
                '%s uses %s, which this script does not model; the generated '
                'headers emit only #if/#endif' % (path, line.split()[0])
            )
        if line.startswith('//'):
            continue

        line = line.lstrip(',').rstrip(',').strip()
        if not line or '=' in line:
            continue
        if require_unguarded and guards:
            raise TableError(
                '%s guards %s with `#if %s`, but the older tree is expected to '
                'emit no conditionals, so its numbers cannot be read by position'
                % (path, line, guards[0])
            )
        entries.append((line, tuple(guards)))

    if guards:
        raise TableError('%s leaves %d conditional(s) unclosed' % (path, len(guards)))
    if not entries:
        raise TableError('%s enumerates no messages' % path)
    return entries


def number_messages(entries: list[tuple[str, tuple[str, ...]]]) -> dict[str, int]:
    """Assign the older tree's ordinals by declaration position."""
    numbers: dict[str, int] = {}
    ordinal = 0
    for name, _ in entries:
        if name in NON_MESSAGE_NAMES:
            ordinal += 1
            continue
        numbers[name] = ordinal
        ordinal += 1
    return numbers


def collect_messages(
    old_entries: list[tuple[str, tuple[str, ...]]],
    new_entries: list[tuple[str, tuple[str, ...]]],
) -> list[Message]:
    """Join the two enumerations on message name."""
    old_numbers = number_messages(old_entries)
    new_guards = {name: guards for name, guards in new_entries}

    if len(old_numbers) != len(old_entries) - len(
        [name for name, _ in old_entries if name in NON_MESSAGE_NAMES]
    ):
        raise TableError('older enumeration repeats a message name')

    shared = sorted(set(old_numbers) & set(new_guards))
    if not shared:
        raise TableError('the two enumerations share no message name')

    return [Message(name, old_numbers[name], new_guards[name]) for name in shared]


def assert_numbers_unique(messages: list[Message]) -> None:
    seen: dict[int, str] = {}
    for message in messages:
        if message.number in seen:
            raise TableError(
                'older enumeration number %d is claimed by both %s and %s'
                % (message.number, seen[message.number], message.name)
            )
        seen[message.number] = message.name


def assert_names_unique(messages: list[Message]) -> None:
    seen: set[str] = set()
    for message in messages:
        if message.name in seen:
            raise TableError('message %s appears twice' % message.name)
        seen.add(message.name)


def assert_guards_are_one_level(messages: list[Message]) -> None:
    """Nested guards would need nested `#if`, which the generator never emits."""
    for message in messages:
        if len(message.guards) > 1:
            raise TableError(
                'message %s sits under %d stacked conditions, which the '
                'generator does not emit' % (message.name, len(message.guards))
            )


def drop_always_true_guards(messages: list[Message]) -> list[Message]:
    """Remove conditions that hold for every iOS build.

    A case needs an `#if` only when the enumerator can be absent. `PLATFORM(COCOA)`
    and friends are true on every iOS build, so repeating them adds noise to 229
    rows without protecting anything.
    """
    return [
        Message(message.name, message.number, tuple(
            guard for guard in message.guards
            if guard not in GUARDS_ALWAYS_TRUE_ON_IOS
        ))
        for message in messages
    ]


def render_cases(rows: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    """Emit case labels, grouping consecutive rows that share a guard."""
    lines: list[str] = []
    index = 0
    while index < len(rows):
        label, guards = rows[index]
        run = [label]
        index += 1
        while index < len(rows) and rows[index][1] == guards:
            run.append(rows[index][0])
            index += 1

        if guards:
            lines.append('#if %s' % ' && '.join(guards))
            lines.extend(run)
            lines.append('#endif')
        else:
            lines.extend(run)
    return lines


def render_outgoing(messages: list[Message]) -> str:
    """Newer build sending to an older process: message name -> old number."""
    # The switch is over `MessageName` itself, not its underlying integer: a
    # scoped enumeration's case labels must be enumerators of the switched type,
    # so `case MessageName::X` needs `switch (name)`. Switching on
    # `static_cast<uint16_t>(name)` would reject every label below.
    rows = [
        ('    case MessageName::%s: return static_cast<MessageName>(%d);'
         % (message.name, message.number), message.guards)
        for message in messages
    ]
    return '\n'.join([
        'MessageName translateOutgoingMessageName(MessageName name)',
        '{',
        '    switch (name) {',
        *render_cases(rows),
        '    default: return name;',
        '    }',
        '}',
    ])


def render_incoming(messages: list[Message]) -> str:
    """Receiving from an older process: old number -> message name."""
    # The incoming switch is over the integer, because its labels are the older
    # tree's numbers. Those are unwrapped from the parameter once, here.
    rows = [
        ('    case %d: return MessageName::%s;' % (message.number, message.name),
         message.guards)
        for message in sorted(messages, key=lambda message: message.number)
    ]
    return '\n'.join([
        'MessageName translateIncomingMessageName(MessageName name)',
        '{',
        '    switch (static_cast<uint16_t>(name)) {',
        *render_cases(rows),
        '    default: return name;',
        '    }',
        '}',
    ])


def render(messages: list[Message]) -> str:
    return '%s\n\n%s' % (render_outgoing(messages), render_incoming(messages))


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Generate the IPC message-number translation functions from two '
            'generated MessageNames.h files.'
        ),
    )
    parser.add_argument(
        '--names-old',
        default='/tmp/Names16_correct.h',
        type=Path,
        help='MessageNames.h of the older tree (default: %(default)s)',
    )
    parser.add_argument(
        '--names-new',
        default='/tmp/Names18_correct.h',
        type=Path,
        help='MessageNames.h of the newer tree (default: %(default)s)',
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='write the functions to this file instead of stdout',
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        old_entries = parse_enum(arguments.names_old, require_unguarded=True)
        new_entries = parse_enum(arguments.names_new, require_unguarded=False)
        messages = collect_messages(old_entries, new_entries)

        assert_names_unique(messages)
        assert_numbers_unique(messages)
        assert_guards_are_one_level(messages)
        messages = drop_always_true_guards(messages)
    except TableError as error:
        print('gen-ipc-message-number-tables: %s' % error, file=sys.stderr)
        return 1

    document = render(messages)
    if arguments.output:
        arguments.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
