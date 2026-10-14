"""``EntryFilter`` - which entries the Log panel shows (2026-10-14). Pure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .entry import LogEntry


@dataclass(frozen=True)
class EntryFilter:
    min_level: int = logging.DEBUG
    text: str = ""
    """Space separated terms, all must appear (case-insensitive) in the message,
    logger name or event name. A term starting with ``-`` must NOT appear."""
    excluded_loggers: frozenset[str] = field(default_factory=frozenset)
    """Logger-name prefixes to hide (e.g. a chatty library)."""
    timed_only: bool = False  # only entries with a duration
    since_seq: int = 0  # only entries after this one (the "Mark" button / clear view)

    def matches(self, entry: LogEntry) -> bool:
        if entry.level < self.min_level or entry.seq <= self.since_seq:
            return False
        if self.timed_only and entry.dur_ms is None:
            return False
        if self.excluded_loggers and any(entry.logger.startswith(prefix) for prefix in self.excluded_loggers):
            return False
        if self.text:
            haystack = f"{entry.message} {entry.logger} {entry.event or ''}".lower()
            for term in self.text.lower().split():
                if term.startswith("-"):
                    if len(term) > 1 and term[1:] in haystack:
                        return False
                elif term not in haystack:
                    return False
        return True

    def apply(self, entries: list[LogEntry]) -> list[LogEntry]:
        return [entry for entry in entries if self.matches(entry)]
