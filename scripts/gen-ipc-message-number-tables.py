#!/usr/bin/env python3
"""Generate the IPC message-number translation tables between two WebKit trees.

`MessageName` is one enumeration covering every message in a tree, and the wire
format carries its ordinal. Two trees with different message sets therefore
disagree on the number of every message past the first divergence, even where
the message names match. This script emits the two lookup functions that let a
build of the newer tree talk to a process running the older enumeration, plus
the predicates that tell a caller whether an unmapped number is a known gap
between the two trees or a defect in these tables.

The hard part is that the two generated headers are not equally readable.

The iOS 16.1 generator (`generate_message_names_header` in that tree's
`Source/WebKit/Scripts/webkit/messages.py`) joins its enumerators with
`'\\n    , '.join(str(e) for e in message_enumerators)` and emits no
preprocessor conditionals at all: its `get_message_enumerators` never consults
`message.condition()`. Every message exists in every configuration, so counting
positions in the file gives the value the compiler assigns, and that value is
the same whether the file is compiled for iOS 16.1, iOS 18, or macOS - measured
by compiling the enumeration body under each of those macro sets, all three
reporting `Last == 3139`. Those positions are safe to write as integer
literals.

The 18.3 generator groups enumerators with
`itertools.groupby(..., lambda e: (e.condition(), e.synchronous()))` and wraps
each group in `#if <condition>`; there are 703 such blocks in the generated
header. A position counted in the file is therefore *not* the value the
compiler assigns: a configuration removes the entries it excludes and every
later entry shifts down by that many. `InitializeConnection` is the clearest
case, since it sits under `#if PLATFORM(COCOA)` at flat position 3076 and every
build that does not enable Cocoa numbers it lower.

So the 18.3 side never appears as a number. Each case names the enumerator -
`MessageName::Some_Message` - and the compiler supplies the value under whatever
conditionals are in force. The 16.1 side is an integer literal, which is
correct precisely because that generator emits no conditionals.

For scale, and stated in the only caliber this script can read off the two
files: the 16.1 enumeration has 3140 messages, largest 3139, with the
synchronous section starting at 2899. The 18.3 enumeration has 4086 messages
at file-flat positions, `Count` at 4087, and `FirstSynchronous` at flat
position 3838. Those four numbers describe the files with every guard counted,
not any single build: a build removes the entries its configuration excludes
and every later position shifts down. 2807 messages are shared, 333 exist only
in 16.1, and 1279 only in 18.3.

A build-caliber count is deliberately absent, because reading one requires
evaluating 703 `#if` conditions against a real toolchain, and a number measured
by hand here would describe the configuration that happened to be measured
rather than the ones this header ships in. That is also why the emitted cases
name enumerators instead of numbers: whatever a build computes is what the case
compiles to.

Three consequences follow, and the script enforces all three:

* Every case whose 18.3 enumerator sits under an `#if` is emitted under that
  same `#if`. Reusing the guard verbatim makes the case exist exactly when the
  enumerator it names exists, so a configuration that drops a message drops its
  case too instead of failing to compile.
* Both directions switch over the same 2807 shared names, so the two functions
  are mutual inverses on that set. The script checks the round trip rather than
  relying on the construction.
* Every emitted 16.1 literal is asserted to be no larger than the 18.3
  enumeration's `Last`. A 16.1 number above that would be rejected by
  `isValidEnum`, so the check guards an invariant that the data satisfies today
  but that a future enumeration could break silently.

The two functions never answer unmapped input with the input itself, and never
with `MessageName::Invalid` on the outgoing side. Returning the input looks safe
and is not: older numbering is dense and total, so an unchanged number is a
*valid* older message and would run whichever handler owns that number.
Returning `Invalid` is wrong for a different reason: `Invalid` is `Count`, which
compares greater than `FirstSynchronous`, and `receiverName(Invalid)` clamps to
`Last` and so names a real receiver, so the number would be dispatched as a
synchronous message under a name the peer never sent. Both directions therefore
answer `MessageName::Invalid`, and the caller *must* consult the predicates first:

* `isKnownUnmappedOutgoingName` / `isKnownUnmappedIncomingNumber` answer true for
  names and numbers that exist in one tree and cannot exist in the other: the 333
  messages only 16.1 has, the 1279 only 18.3 has, and the shared messages whose
  18.3 enumerator a build may compile out. On the outgoing side the caller skips
  the send; on the incoming side it drops the message before any dispatch
  decision. Neither is an error: a peer from the other generation provably cannot
  handle a message it does not have, and the older process has no handler for it.
* Anything else reaching `default` means these tables disagree with the
  enumeration they were generated from. That is a defect, and the caller should
  turn it into an invalid-message report so it surfaces during development rather
  than being silently absorbed.

`firstSynchronousOldMessageNumber` is the older tree's own synchronous boundary,
read from its `messageIsSync` comparison rather than counted here. The number is
needed because a dropped message still carries the older tree's number, which is
below the newer tree's own `FirstSynchronous`; testing against the newer boundary
would classify every dropped message as asynchronous and skip the reply the older
sender is waiting for. A caller uses it to send that sender an empty reply, so
the split between dropped-sync and dropped-async is the residual risk this layer
cannot remove: the sender decodes a default value instead of no reply at all.

The output is complete function bodies rather than arrays: a `switch` lets each
`case` carry its own `#if`, and the compiler folds the whole thing into a jump
table, so there is no runtime search and no ordering constraint to maintain.

The script exits non-zero, with the reason on stderr, when the inputs cannot be
parsed, when either side repeats a name, when a 16.1 number is reused, when a
guarded case would be emitted without its `#if`, when a 16.1 literal exceeds
the 18.3 `Last`, or when a shared message does not survive the round trip.

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
class Entry:
    """One enumerator of a generated `MessageName`, in declaration order.

    `value` is the slot the compiler assigns under a configuration that keeps
    every entry: for an implicit enumerator that is its position among all
    implicit enumerators, sentinels included. `explicit` holds the right-hand
    side of an enumerator the generator wrote with `=`, if it had one.
    """

    name: str
    value: int
    guards: tuple[str, ...]
    explicit: str | None

    @property
    def is_message(self) -> bool:
        return self.explicit is None and self.name not in NON_MESSAGE_NAMES


@dataclass(frozen=True)
class SharedMessage:
    """A name both trees enumerate, carrying each side's own facts.

    The two are separate entries, not one merged record, because the number
    comes from the older tree and the guard comes from the newer one. Reading a
    shared message's guard off the older entry yields nothing - that generator
    emits no conditionals - and emitting a guarded case without it fails to
    compile wherever the newer enumerator is absent.
    """

    old: Entry
    new: Entry

    @property
    def name(self) -> str:
        return self.old.name


@dataclass(frozen=True)
class Table:
    """Both enumerations, joined on message name."""

    shared: tuple[SharedMessage, ...]
    old_only: tuple[Entry, ...]
    new_only: tuple[Entry, ...]
    first_synchronous_old: int
    new_last: int
    old_count: int
    new_count: int
    guard_blocks: int


def read_enum_body(path: Path) -> str:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as error:
        raise TableError('cannot read %s: %s' % (path, error.strerror)) from error

    match = ENUM_BODY_PATTERN.search(text)
    if not match:
        raise TableError('%s does not contain an `enum class MessageName : uint16_t`' % path)
    return match.group('body')


def parse_enum(path: Path, *, require_unguarded: bool) -> list[Entry]:
    """Return every enumerator of a generated header, in declaration order.

    Explicitly valued entries keep their right-hand side uninterpreted; the
    script only needs the implicit ones to know what the compiler assigns.
    """
    body = read_enum_body(path)
    entries: list[Entry] = []
    guards: list[str] = []
    next_value = 0
    guard_blocks = 0

    for raw_line in body.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('#if'):
            guard_blocks += 1
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
        if not line:
            continue
        if require_unguarded and guards:
            raise TableError(
                '%s guards %s with `#if %s`, but the older tree is expected to '
                'emit no conditionals, so its numbers cannot be read by position'
                % (path, line, guards[0])
            )

        name, separator, explicit = line.partition('=')
        name = name.strip()
        explicit = explicit.strip() if separator else None
        if explicit is None:
            entries.append(Entry(name, next_value, tuple(guards), None))
            next_value += 1
        else:
            entries.append(Entry(name, -1, tuple(guards), explicit))

    if guards:
        raise TableError('%s leaves %d conditional(s) unclosed' % (path, len(guards)))
    if not entries:
        raise TableError('%s enumerates no messages' % path)

    resolved = resolve_explicit_values(entries, path)
    return resolved


def resolve_explicit_values(entries: list[Entry], path: Path) -> list[Entry]:
    """Replace every explicit right-hand side with the value it names.

    The generated headers only use explicit values for sentinels, and only in
    the forms `Count - 1`, `FirstSynchronous - 1` and a bare enumerator name.
    Anything else is a change this script has not been taught to read.
    """
    known = {entry.name: entry.value for entry in entries if entry.explicit is None}
    resolved: list[Entry] = []

    for entry in entries:
        if entry.explicit is None:
            resolved.append(entry)
            continue

        expression = entry.explicit
        match = re.fullmatch(r'(\w+)\s*-\s*1', expression)
        if match:
            base = match.group(1)
            if base not in known:
                raise TableError(
                    '%s: %s is defined as `%s`, whose base %s is not yet known'
                    % (path, entry.name, expression, base)
                )
            value = known[base] - 1
        elif expression in known:
            value = known[expression]
        else:
            raise TableError(
                '%s: %s has the explicit value `%s`, which this script cannot '
                'resolve' % (path, entry.name, expression)
            )
        resolved.append(Entry(entry.name, value, entry.guards, expression))

    return resolved


def build_table(
    old_entries: list[Entry],
    new_entries: list[Entry],
    old_path: Path,
    new_path: Path,
) -> Table:
    old_messages = {entry.name: entry for entry in old_entries if entry.is_message}
    new_messages = {entry.name: entry for entry in new_entries if entry.is_message}
    if len(old_messages) != len([e for e in old_entries if e.is_message]):
        raise TableError('%s repeats a message name' % old_path)
    if len(new_messages) != len([e for e in new_entries if e.is_message]):
        raise TableError('%s repeats a message name' % new_path)

    shared_names = sorted(set(old_messages) & set(new_messages))
    if not shared_names:
        raise TableError('the two enumerations share no message name')

    first_synchronous = next(
        (entry for entry in old_entries if entry.name == 'FirstSynchronous'), None
    )
    if first_synchronous is not None:
        first_synchronous_old = first_synchronous.value
    else:
        # The 16.1 header has no FirstSynchronous enumerator; it spells the
        # boundary as a comparison against the first synchronous message, so
        # that message's value is the boundary.
        first_synchronous_old = first_synchronous_boundary(old_path)

    count_entry = next((entry for entry in new_entries if entry.name == 'Count'), None)
    if count_entry is not None and count_entry.value >= 0:
        new_last = count_entry.value - 1
    else:
        last_entry = next((entry for entry in new_entries if entry.name == 'Last'), None)
        if last_entry is None or last_entry.value < 0:
            raise TableError('%s has no resolvable Count or Last' % new_path)
        new_last = last_entry.value

    guard_blocks = count_guard_blocks(new_path)

    return Table(
        shared=tuple(
            SharedMessage(old=old_messages[name], new=new_messages[name])
            for name in shared_names
        ),
        old_only=tuple(
            old_messages[name] for name in sorted(set(old_messages) - set(new_messages),
                                                 key=lambda name: old_messages[name].value)
        ),
        new_only=tuple(
            new_messages[name] for name in sorted(set(new_messages) - set(old_messages),
                                                  key=lambda name: new_messages[name].value)
        ),
        first_synchronous_old=first_synchronous_old,
        new_last=new_last,
        old_count=len(old_messages),
        new_count=len(new_messages),
        guard_blocks=guard_blocks,
    )


def first_synchronous_boundary(old_path: Path) -> int:
    """Read the message a 16.1 header compares against to test for synchrony.

    The older generator emits `return name >= MessageName::<first synchronous
    message>;`, so that message's value is the boundary. The script needs it to
    tell a dropped synchronous message from a dropped asynchronous one: the
    former has a sender waiting for a reply, the latter does not.
    """
    text = old_path.read_text(encoding='utf-8')
    match = re.search(
        r'constexpr\s+bool\s+messageIsSync\s*\(\s*MessageName\s+name\s*\)\s*'
        r'\{\s*return\s+name\s*>=\s*MessageName::(\w+)\s*;\s*\}',
        text,
    )
    if not match:
        raise TableError(
            '%s does not spell messageIsSync as a comparison against a message, '
            'so the synchronous boundary cannot be read' % old_path
        )
    entries = parse_enum(old_path, require_unguarded=True)
    for entry in entries:
        if entry.name == match.group(1):
            return entry.value
    raise TableError(
        '%s compares against MessageName::%s, which it does not enumerate'
        % (old_path, match.group(1))
    )


def count_guard_blocks(path: Path) -> int:
    return sum(
        1 for line in read_enum_body(path).split('\n') if line.strip().startswith('#if')
    )


def effective_guards(entry: Entry) -> tuple[str, ...]:
    """Drop conditions that hold for every iOS build."""
    return tuple(guard for guard in entry.guards if guard not in GUARDS_ALWAYS_TRUE_ON_IOS)


def assert_numbers_unique(table: Table) -> None:
    seen: dict[int, str] = {}
    for message in table.shared:
        if message.old.value in seen:
            raise TableError(
                'older enumeration number %d is claimed by both %s and %s'
                % (message.old.value, seen[message.old.value], message.name)
            )
        seen[message.old.value] = message.name


def assert_names_unique(table: Table) -> None:
    seen: set[str] = set()
    for message in table.shared:
        if message.name in seen:
            raise TableError('message %s appears twice' % message.name)
        seen.add(message.name)


def assert_guards_are_one_level(table: Table) -> None:
    """Nested guards would need nested `#if`, which the generator never emits."""
    entries = [message.new for message in table.shared] + list(table.new_only)
    for entry in entries:
        if len(entry.guards) > 1:
            raise TableError(
                'message %s sits under %d stacked conditions, which the '
                'generator does not emit' % (entry.name, len(entry.guards))
            )


def section_lines(document: str, function_signature: str) -> list[str]:
    """The body lines of one emitted function, excluding the next one."""
    start = document.index(function_signature)
    end = document.find('\n\n', document.index('{', start))
    return document[start:end].split('\n')


def rendered_outgoing_pairs(document: str) -> dict[str, int]:
    """The name -> older number pairs the emitted outgoing switch actually has."""
    pattern = re.compile(
        r'case MessageName::(\w+): return static_cast<MessageName>\((\d+)\);'
    )
    pairs: dict[str, int] = {}
    for line in section_lines(document, 'MessageName translateOutgoingMessageName(MessageName name)'):
        for name, number in pattern.findall(line):
            if name in pairs:
                raise TableError('the outgoing switch emits %s twice' % name)
            pairs[name] = int(number)
    return pairs


def rendered_incoming_pairs(document: str) -> dict[int, str]:
    """The older number -> name pairs the emitted incoming switch actually has."""
    pattern = re.compile(r'case (\d+): return MessageName::(\w+);')
    pairs: dict[int, str] = {}
    for line in section_lines(document, 'MessageName translateIncomingMessageName(MessageName name)'):
        for number, name in pattern.findall(line):
            if int(number) in pairs:
                raise TableError('the incoming switch emits %s twice' % number)
            pairs[int(number)] = name
    return pairs


def assert_round_trip(table: Table, document: str) -> None:
    """The two emitted switches must be inverses of each other and of the table.

    Both sides are read back from the rendered `case` labels, not from the table
    they came from: comparing the table with itself cannot fail, and would pass a
    renderer that emitted the wrong number for every message.
    """
    outgoing = rendered_outgoing_pairs(document)
    incoming = rendered_incoming_pairs(document)
    expected = {message.name: message.old.value for message in table.shared}

    if outgoing != expected:
        missing = sorted(set(expected) - set(outgoing))[:3]
        extra = sorted(set(outgoing) - set(expected))[:3]
        wrong = sorted(
            (name, expected[name], outgoing[name])
            for name in set(expected) & set(outgoing)
            if expected[name] != outgoing[name]
        )[:3]
        raise TableError(
            'the outgoing switch names %d messages, the table %d; missing %s, '
            'unexpected %s, wrong number %s'
            % (len(outgoing), len(expected), missing, extra, wrong)
        )

    inverted = {number: name for name, number in expected.items()}
    if incoming != inverted:
        missing = sorted(set(inverted) - set(incoming))[:3]
        extra = sorted(set(incoming) - set(inverted))[:3]
        wrong = sorted(
            (number, inverted[number], incoming[number])
            for number in set(inverted) & set(incoming)
            if inverted[number] != incoming[number]
        )[:3]
        raise TableError(
            'the incoming switch maps %d numbers, the outgoing switch has %d; '
            'missing %s, unexpected %s, wrong name %s'
            % (len(incoming), len(inverted), missing, extra, wrong)
        )


def assert_old_numbers_fit_new_enum(table: Table) -> None:
    """A 16.1 literal above 18.3's Last would fail the receiver's range check."""
    highest = max(entry.value for entry in table.old_only)
    highest = max(highest, max(message.old.value for message in table.shared))
    if highest > table.new_last:
        raise TableError(
            'older enumeration number %d exceeds the newer enumeration Last %d, '
            'so the receiver would reject it as out of range' % (highest, table.new_last)
        )


def assert_guarded_cases_are_wrapped(table: Table, document: str) -> None:
    """Every case naming a conditionally compiled enumerator must sit in its `#if`.

    A case without its guard names an enumerator the configuration does not
    declare, and the document does not compile. A case under the wrong `#if`
    compiles only in configurations its enumerator is absent from, which is the
    same failure with a different trigger. The check reads the emitted text so it
    fails on the artefact a reader gets, and compares the condition itself rather
    than the nesting depth: any non-zero depth would satisfy the depth alone.
    """
    guards_by_name = {message.name: effective_guards(message.new) for message in table.shared}
    guards_by_name.update(
        {entry.name: effective_guards(entry) for entry in table.new_only}
    )

    open_guards: list[str] = []
    for line in document.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#if '):
            open_guards.append(stripped[len('#if '):].strip())
            continue
        if stripped == '#endif':
            if not open_guards:
                raise TableError('the emitted document closes an #if it never opened')
            open_guards.pop()
            continue
        match = re.match(r'case MessageName::(\w+):', stripped)
        if not match:
            continue
        name = match.group(1)
        expected_guard = guards_by_name.get(name)
        if expected_guard is None:
            continue
        emitted = open_guards[-1] if open_guards else ''
        wanted = ' && '.join(expected_guard)
        if emitted != wanted:
            raise TableError(
                'case %s is emitted under %r, but its enumerator needs %r'
                % (name, emitted, wanted)
            )
        if len(open_guards) > 1:
            raise TableError(
                'case %s sits under %d stacked conditions; the generator emits '
                'each guard as a single `#if`' % (name, len(open_guards))
            )
    if open_guards:
        raise TableError('the emitted document leaves the #if %r open' % open_guards[-1])


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


def unmapped_incoming_numbers(table: Table) -> list[int]:
    """16.1 numbers that no configuration of this build maps to a message.

    That is every message only 16.1 has, plus every shared message whose 18.3
    enumerator a build may compile out - the latter is unmapped exactly in the
    configurations that drop it, and listing it unconditionally is harmless
    because the lookup function answers before `default` is reached.
    """
    numbers = [entry.value for entry in table.old_only]
    numbers += [message.old.value for message in table.shared
                if effective_guards(message.new)]
    return sorted(numbers)


def unmapped_outgoing_names(table: Table) -> list[Entry]:
    """18.3 messages with no 16.1 counterpart, keeping their own guards.

    Only `new_only` qualifies: a shared name exists in both enumerations, so the
    older process has a handler for it and the translator has a case for it.
    Listing a shared name here would refuse a send the translator can perform.
    Each entry is emitted only under the condition that keeps its enumerator
    present, so the name compiles in exactly the configurations that have it.
    """
    return sorted(table.new_only, key=lambda entry: entry.name)


def render_unmapped_incoming(table: Table) -> str:
    """The incoming predicate names no symbol, so it needs no `#if`."""
    rows = [('    case %d:' % number, ()) for number in unmapped_incoming_numbers(table)]
    body: list[str] = []
    for index in range(0, len(rows), 8):
        body.append(' '.join(label.strip() for label, _ in rows[index:index + 8]))
    return '\n'.join([
        '// True for a 16.1 number that this build cannot map in any configuration:',
        '// a message only 16.1 has, or a shared message whose 18.3 enumerator a',
        '// build may compile out. A caller that reaches `default` can use this to',
        '// tell an expected gap between the two trees from a defect in these tables.',
        'bool isKnownUnmappedIncomingNumber(uint16_t number)',
        '{',
        '    switch (number) {',
        *('    ' + line for line in body),
        '        return true;',
        '    default:',
        '        return false;',
        '    }',
        '}',
    ])


def render_unmapped_outgoing(table: Table) -> str:
    rows = [
        ('    case MessageName::%s:' % entry.name, effective_guards(entry))
        for entry in unmapped_outgoing_names(table)
    ]
    grouped: list[tuple[str, tuple[str, ...]]] = []
    for label, guards in rows:
        if grouped and grouped[-1][1] == guards:
            grouped[-1] = (grouped[-1][0] + ' ' + label.strip(), guards)
        else:
            grouped.append((label, guards))
    return '\n'.join([
        '// True for an 18.3 message with no 16.1 counterpart. Every case carries the',
        '// guard that keeps its enumerator present, so this compiles in exactly the',
        '// configurations that have the message.',
        'bool isKnownUnmappedOutgoingName(MessageName name)',
        '{',
        '    switch (name) {',
        *render_cases(grouped),
        '    default:',
        '        return false;',
        '    }',
        '}',
    ])


def render_outgoing(table: Table) -> str:
    """Newer build sending to an older process: message name -> old number."""
    # The switch is over `MessageName` itself, not its underlying integer: a
    # scoped enumeration's case labels must be enumerators of the switched type,
    # so `case MessageName::X` needs `switch (name)`. Switching on
    # `static_cast<uint16_t>(name)` would reject every label below.
    rows = [
        ('    case MessageName::%s: return static_cast<MessageName>(%d);'
         % (message.name, message.old.value), effective_guards(message.new))
        for message in table.shared
    ]
    return '\n'.join([
        'MessageName translateOutgoingMessageName(MessageName name)',
        '{',
        '    switch (name) {',
        *render_cases(rows),
        '    default:',
        '        // Not an unchanged pass-through: the 16.1 number space starts over at',
        '        // zero, so an 18.3 number sent unchanged would read as a different 16.1',
        '        // message. The caller checks isKnownUnmappedOutgoingName first, which',
        '        // separates the expected gaps from a table that has fallen out of sync.',
        '        return MessageName::Invalid;',
        '    }',
        '}',
    ])


def render_incoming(table: Table) -> str:
    """Receiving from an older process: old number -> message name."""
    # The incoming switch is over the integer, because its labels are the older
    # tree's numbers. Those are unwrapped from the parameter once, here.
    rows = [
        ('    case %d: return MessageName::%s;' % (message.old.value, message.name),
         effective_guards(message.new))
        for message in sorted(table.shared, key=lambda message: message.old.value)
    ]
    return '\n'.join([
        'MessageName translateIncomingMessageName(MessageName name)',
        '{',
        '    switch (static_cast<uint16_t>(name)) {',
        *render_cases(rows),
        '    default:',
        '        // Same reasoning as the outgoing direction. A 16.1 number this build',
        '        // does not map is either a message only 16.1 has or a shared message',
        '        // this configuration compiles out, and 3139 - the largest 16.1 number -',
        '        // is below 18.3 Last, so returning it unchanged would deliver it to',
        '        // whichever 18.3 receiver owns that number instead of failing.',
        '        return MessageName::Invalid;',
        '    }',
        '}',
    ])


def render_constant(table: Table) -> str:
    # `const` rather than `constexpr`: the header declares this `extern`, and a
    # constexpr definition would be internal to this file and leave the header's
    # declaration unresolved.
    return '\n'.join([
        '// The first synchronous message number in the 16.1 enumeration. A caller',
        '// uses it to tell, among the messages it drops, the ones whose sender is',
        '// waiting for a reply from the ones whose sender is not. Read from the 16.1',
        '// header\'s own messageIsSync comparison rather than counted here.',
        'const uint16_t firstSynchronousOldMessageNumber = %d;'
        % table.first_synchronous_old,
    ])


def render(table: Table) -> str:
    return '\n\n'.join([
        render_constant(table),
        render_unmapped_incoming(table),
        render_unmapped_outgoing(table),
        render_outgoing(table),
        render_incoming(table),
    ])


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
        table = build_table(old_entries, new_entries, arguments.names_old, arguments.names_new)

        assert_names_unique(table)
        assert_numbers_unique(table)
        assert_guards_are_one_level(table)
        assert_old_numbers_fit_new_enum(table)
        document = render(table)
        assert_round_trip(table, document)
        assert_guarded_cases_are_wrapped(table, document)
    except TableError as error:
        print('gen-ipc-message-number-tables: %s' % error, file=sys.stderr)
        return 1

    print(
        'gen-ipc-message-number-tables: %d shared, %d only older, %d only newer, '
        '%d unmapped incoming, %d unmapped outgoing'
        % (len(table.shared), len(table.old_only), len(table.new_only),
           len(unmapped_incoming_numbers(table)), len(unmapped_outgoing_names(table))),
        file=sys.stderr,
    )

    if arguments.output:
        arguments.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
