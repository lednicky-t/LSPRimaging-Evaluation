"""Entries -> JSON Lines / CSV / plain text (2026-10-14). Pure."""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Iterable

from .entry import LogEntry


def clock_text(entry: LogEntry) -> str:
    """Local wall-clock time with milliseconds."""
    seconds = time.localtime(entry.t)
    return f"{time.strftime('%H:%M:%S', seconds)}.{int((entry.t % 1) * 1000):03d}"


def entry_to_line(entry: LogEntry) -> str:
    duration = f" ({entry.dur_ms:.1f} ms)" if entry.dur_ms is not None and "dur_ms=" not in entry.message else ""
    return f"{clock_text(entry)} {entry.level_name:<8} {entry.logger} | {entry.message}{duration}"


def entries_to_text(entries: Iterable[LogEntry]) -> str:
    lines = []
    for entry in entries:
        lines.append(entry_to_line(entry))
        if entry.exc:
            lines.append(entry.exc)
    return "\n".join(lines)


def entries_to_jsonl(entries: Iterable[LogEntry]) -> str:
    return "\n".join(json.dumps(entry.to_dict(), default=repr, separators=(",", ":")) for entry in entries)


def entries_to_csv(entries: Iterable[LogEntry]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["time", "level", "logger", "event", "dur_ms", "message", "thread", "fields"])
    for entry in entries:
        writer.writerow(
            [
                clock_text(entry), entry.level_name, entry.logger, entry.event or "",
                "" if entry.dur_ms is None else f"{entry.dur_ms:.3f}", entry.message, entry.thread,
                json.dumps(entry.fields, default=repr) if entry.fields else "",
            ]
        )
    return buffer.getvalue()
