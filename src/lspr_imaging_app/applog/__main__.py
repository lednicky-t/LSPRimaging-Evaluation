"""Read a saved log file without the app (2026-10-14)::

    python -m lspr_imaging_app.applog summary logs/lspri-20261014-101500-4242.jsonl
    python -m lspr_imaging_app.applog grep logs/lspri-....jsonl --level WARNING --text "reference"
    python -m lspr_imaging_app.applog grep logs/lspri-....jsonl --event render.frame --csv

``summary``: record counts per level, the first errors / warnings, and a timing
table (count, mean, p50, p95, max, total per event). ``grep``: filtered records as
text (or ``--jsonl`` / ``--csv``). The file is plain JSON Lines, so ``jq`` and
pandas read it directly too.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from .aggregate import TimingAggregator
from .entry import LogEntry
from .export import entries_to_csv, entries_to_jsonl, entries_to_text
from .filters import EntryFilter


def read_entries(path: Path) -> tuple[dict, list[LogEntry]]:
    """(header, entries) of a JSONL log; a line that is not valid JSON is skipped."""
    header: dict = {}
    entries: list[LogEntry] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "schema" in payload and "lvl" not in payload:
                header = payload
                continue
            entries.append(LogEntry.from_dict(payload, seq=number))
    return header, entries


def summary_text(header: dict, entries: list[LogEntry], top: int = 25) -> str:
    lines = [
        f"session {header.get('session', '?')}  app {header.get('version', '?')}  python {header.get('python', '?')}",
        f"{len(entries)} records",
    ]
    counts = Counter(entry.level_name for entry in entries)
    lines.append("  " + "  ".join(f"{name} {counts[name]}" for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") if counts[name]))
    problems = [entry for entry in entries if entry.level >= logging.WARNING]
    if problems:
        lines.append("")
        lines.append(f"first {min(len(problems), 10)} of {len(problems)} warnings / errors:")
        lines.extend("  " + line for line in entries_to_text(problems[:10]).splitlines())
    aggregator = TimingAggregator()
    for entry in entries:
        if entry.dur_ms is not None and entry.event:
            aggregator.add(entry.event, entry.dur_ms)
    rows = aggregator.rows()[:top]
    if rows:
        lines.append("")
        lines.append(f"{'event':<34}{'count':>7}{'mean ms':>10}{'p50':>9}{'p95':>9}{'max':>10}{'total ms':>12}")
        for row in rows:
            lines.append(
                f"{row.event:<34}{row.count:>7}{row.mean_ms:>10.2f}{row.p50_ms:>9.2f}{row.p95_ms:>9.2f}{row.max_ms:>10.2f}{row.total_ms:>12.1f}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lspr_imaging_app.applog", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summary", help="counts, first problems and a timing table")
    summary.add_argument("file", type=Path)
    summary.add_argument("--top", type=int, default=25)
    grep = commands.add_parser("grep", help="print the records that match")
    grep.add_argument("file", type=Path)
    grep.add_argument("--level", default="DEBUG")
    grep.add_argument("--text", default="")
    grep.add_argument("--event", default="")
    grep.add_argument("--logger", default="", help="logger name prefix to keep")
    grep.add_argument("--timed", action="store_true", help="only records with a duration")
    format_group = grep.add_mutually_exclusive_group()
    format_group.add_argument("--jsonl", action="store_true")
    format_group.add_argument("--csv", action="store_true")
    args = parser.parse_args(argv)

    header, entries = read_entries(args.file)
    if args.command == "summary":
        print(summary_text(header, entries, args.top))
        return 0
    level = logging.getLevelName(args.level.upper())
    selected = EntryFilter(
        min_level=level if isinstance(level, int) else logging.DEBUG, text=args.text, timed_only=args.timed
    ).apply(entries)
    if args.event:
        selected = [entry for entry in selected if entry.event == args.event]
    if args.logger:
        selected = [entry for entry in selected if entry.logger.startswith(args.logger)]
    render = entries_to_jsonl if args.jsonl else entries_to_csv if args.csv else entries_to_text
    sys.stdout.write(render(selected) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
