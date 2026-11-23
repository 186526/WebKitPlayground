#!/usr/bin/env python3
"""Judge in one log line whether ccache's working set fits inside its max_size.

ccache enforces max_size lazily while compiling: once the cache grows past the limit it trims
whole level-2 directories, so a working set that does not fit loses entries the same build is
about to ask for again. Two read-only reports expose that. `ccache --show-stats --verbose` prints Cache
size against max_size plus a cumulative Cleanups counter, and `ccache --show-compression` prints
the Compression ratio. Cleanups accumulates over the life of the cache directory, so only the
difference between the pre-build and post-build snapshots describes one build; pass the pre-build
snapshot as --baseline.

The verdict is RESIDENT when this build trimmed nothing and the cache ended below 98% of max_size,
and NOT-RESIDENT otherwise. The in-run hit rate is printed beside it because that is the number to
compare across builds, but it does not decide the verdict: it also falls when the sources change.
Without --baseline the counter cannot be attributed to one build, so only saturation decides.

The script reads only. Its arguments are files; when one is omitted the matching ccache reporting
command is run, which writes nothing either.

Usage, as the workflow calls it after the build:
  scripts/report-ccache-capacity.py \
    --baseline "$GITHUB_WORKSPACE/diagnostics/ccache-before.txt" \
    --compression "$GITHUB_WORKSPACE/diagnostics/ccache-compression.txt"

With neither --stats nor --compression, the matching ccache reporting command is run instead.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CACHEABLE = re.compile(r"^\s*Cacheable calls:\s*(\d+)\s*/\s*(\d+)\s*\(\s*([\d.]+)%\)", re.MULTILINE)
HITS = re.compile(r"^\s*Hits:\s*(\d+)(?:\s*/\s*(\d+))?", re.MULTILINE)
MISSES = re.compile(r"^\s*Misses:\s*(\d+)(?:\s*/\s*(\d+))?", re.MULTILINE)
# ccache 4.x prints (GB) unqualified and (GiB) under --verbose; both name the unit of the two
# numbers, and the percentage in brackets is the ratio this script judges.
CACHE_SIZE = re.compile(
    r"^\s*Cache size \((GB|GiB)\):\s*([\d.]+)\s*/\s*([\d.]+)\s*\(\s*([\d.]+)%\)", re.MULTILINE
)
CLEANUPS = re.compile(r"^\s*Cleanups:\s*(\d+)\s*$", re.MULTILINE)
COMPRESSION_RATIO = re.compile(r"Compression ratio:\s*([\d.]+)\s*x")
SPACE_SAVINGS = re.compile(r"\(\s*([\d.]+)% space savings\)")

# At or above this share of max_size the cache is saturated, and ccache trims to stay there. Below
# it the working set had room to spare. The Cleanups delta reports the trims themselves exactly;
# this threshold covers the runs where no baseline snapshot exists.
RESIDENT_PERCENT = 98.0


@dataclass(frozen=True)
class StatsSnapshot:
    cacheable_calls: int
    total_calls: int
    cacheable_percent: float
    hits: int
    misses: int
    size_unit: str
    cache_size: float
    max_size: float
    percent_full: float
    cleanups: int

    @property
    def hit_percent_of_cacheable(self) -> float:
        return 100.0 * self.hits / self.cacheable_calls if self.cacheable_calls else 0.0

    @property
    def miss_percent_of_cacheable(self) -> float:
        return 100.0 * self.misses / self.cacheable_calls if self.cacheable_calls else 0.0


@dataclass(frozen=True)
class CompressionSnapshot:
    ratio: float
    savings_percent: float | None


def _first_fraction(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    return int(match.group(1))


def parse_stats(text: str, source: str) -> StatsSnapshot:
    cacheable = CACHEABLE.search(text)
    size = CACHE_SIZE.search(text)
    if cacheable is None:
        raise SystemExit(f"{source}: no 'Cacheable calls' line; is this ccache --show-stats --verbose output?")
    if size is None:
        raise SystemExit(f"{source}: no 'Cache size (GB)' line; is this ccache --show-stats --verbose output?")

    hits = _first_fraction(HITS, text)
    misses = _first_fraction(MISSES, text)
    if hits is None or misses is None:
        raise SystemExit(f"{source}: no 'Hits'/'Misses' line; is this ccache --show-stats --verbose output?")
    cleanups = _first_fraction(CLEANUPS, text)
    # ccache omits the Cleanups line entirely while the counter is zero, so a missing line
    # means no trims rather than an unknown count.
    if cleanups is None:
        cleanups = 0

    return StatsSnapshot(
        cacheable_calls=int(cacheable.group(1)),
        total_calls=int(cacheable.group(2)),
        cacheable_percent=float(cacheable.group(3)),
        hits=hits,
        misses=misses,
        size_unit=size.group(1),
        cache_size=float(size.group(2)),
        max_size=float(size.group(3)),
        percent_full=float(size.group(4)),
        cleanups=cleanups,
    )


def parse_compression(text: str, source: str) -> CompressionSnapshot:
    ratio = COMPRESSION_RATIO.search(text)
    if ratio is None:
        raise SystemExit(f"{source}: no 'Compression ratio' line; is this ccache --show-compression output?")
    savings = SPACE_SAVINGS.search(text)
    return CompressionSnapshot(
        ratio=float(ratio.group(1)),
        savings_percent=float(savings.group(1)) if savings else None,
    )


def read_input(path: Path | None, command: list[str]) -> tuple[str, str]:
    if path is not None:
        if not path.is_file():
            raise SystemExit(f"{path}: no such file")
        return path.read_text(encoding="utf-8", errors="replace"), str(path)
    source = " ".join(command)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"{command[0]}: not found; pass the matching file argument instead")
    return completed.stdout, source


def format_number(value: float) -> str:
    return f"{value:.1f}"


def capacity_line(
    after: StatsSnapshot,
    before: StatsSnapshot | None,
    compression: CompressionSnapshot,
) -> str:
    cleanups_delta = None
    if before is not None:
        cleanups_delta = after.cleanups - before.cleanups

    in_run_hit_percent = None
    if before is not None:
        cacheable_delta = after.cacheable_calls - before.cacheable_calls
        hit_delta = after.hits - before.hits
        if cacheable_delta > 0:
            in_run_hit_percent = 100.0 * hit_delta / cacheable_delta

    saturated = after.percent_full >= RESIDENT_PERCENT
    resident = not saturated and (cleanups_delta is None or cleanups_delta == 0)
    verdict = "RESIDENT" if resident else "NOT-RESIDENT"

    fields = [
        f"ccache-capacity: {verdict}",
        f"cacheable={after.cacheable_calls}/{after.total_calls} ({format_number(after.cacheable_percent)}%)",
        f"hits={after.hits} ({format_number(after.hit_percent_of_cacheable)}%)",
        f"misses={after.misses} ({format_number(after.miss_percent_of_cacheable)}%)",
        f"cache={format_number(after.cache_size)}/{format_number(after.max_size)}"
        f"{after.size_unit} ({format_number(after.percent_full)}%)",
        "cleanups=" + (f"{cleanups_delta:+d}" if cleanups_delta is not None else "n/a, no baseline"),
        f"compression={compression.ratio:.3f}x",
    ]
    if compression.savings_percent is not None:
        fields.append(f"savings={format_number(compression.savings_percent)}%")
    fields.append(
        "in-run-hit="
        + (f"{format_number(in_run_hit_percent)}%" if in_run_hit_percent is not None else "n/a, no baseline")
    )
    return " | ".join(fields)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Judge whether ccache's working set fits inside max_size, in one line.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--stats",
        type=Path,
        default=None,
        help="Post-build `ccache --show-stats --verbose` output. Defaults to running that command.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Pre-build `ccache --show-stats --verbose` output, used for the Cleanups and hit deltas.",
    )
    parser.add_argument(
        "--compression",
        type=Path,
        default=None,
        help="`ccache --show-compression` output. Defaults to running that command.",
    )
    parser.add_argument("--ccache", default="ccache", help="ccache binary used when a file is omitted.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    after_text, after_source = read_input(args.stats, [args.ccache, "--show-stats", "--verbose"])
    after = parse_stats(after_text, after_source)

    before = None
    if args.baseline is not None:
        before_text, before_source = read_input(args.baseline, [])
        before = parse_stats(before_text, before_source)

    compression_text, compression_source = read_input(args.compression, [args.ccache, "--show-compression"])
    compression = parse_compression(compression_text, compression_source)

    print(capacity_line(after, before, compression))
    return 0


if __name__ == "__main__":
    sys.exit(main())
