"""``LogEntry`` - one log record in the form every consumer shares (2026-10-14).

The Log panel's table, the JSON Lines file, the CSV/text export and the timing
aggregator all read this one immutable type, so nothing is ever parsed back out
of text. See `docs/logging_design_2026-10-14.md`.

A record becomes an entry in `entry_from_record`. Structured data travels on the
stdlib `LogRecord` as attributes with an ``lspr_`` prefix (set through
``extra=`` by `events.log_event` / `events.timed`), so any plain
``logger.info(...)`` call is captured too, just without ``event`` / ``fields``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

SCHEMA = "lspri-log/1"

# Set once by `setup.configure_logging`; stamped on every entry so records from
# several sessions can be told apart after concatenation.
_session_id = ""


def set_session_id(session_id: str) -> None:
    global _session_id
    _session_id = session_id


def session_id() -> str:
    return _session_id


@dataclass(frozen=True, slots=True)
class LogEntry:
    seq: int  # 1, 2, 3 ... in arrival order within this process (0 for entries not from the ring)
    t: float  # wall clock, seconds since the epoch
    mono: float  # time.perf_counter() at creation: use it for intervals, it never jumps
    level: int
    logger: str
    message: str
    thread: str
    event: str | None = None  # "domain.action" for structured events and timings
    fields: dict[str, Any] | None = None
    dur_ms: float | None = None  # present on timings
    span: int | None = None  # id of this timed span
    parent: int | None = None  # id of the enclosing timed span (same thread / context)
    exc: str | None = None  # formatted traceback
    session: str = ""

    @property
    def level_name(self) -> str:
        return logging.getLevelName(self.level)

    def to_dict(self) -> dict[str, Any]:
        """The JSON Lines record (keys documented in the design doc)."""
        out: dict[str, Any] = {
            "t": round(self.t, 6),
            "mono": round(self.mono, 6),
            "lvl": self.level_name,
            "log": self.logger,
            "msg": self.message,
            "thr": self.thread,
        }
        if self.event is not None:
            out["ev"] = self.event
        if self.fields:
            out["f"] = self.fields
        if self.dur_ms is not None:
            out["dur_ms"] = round(self.dur_ms, 3)
        if self.span is not None:
            out["span"] = self.span
        if self.parent is not None:
            out["parent"] = self.parent
        if self.exc:
            out["exc"] = self.exc
        if self.session:
            out["sess"] = self.session
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any], seq: int = 0) -> "LogEntry":
        """Inverse of `to_dict` (for reading a saved file)."""
        level = logging.getLevelName(str(payload.get("lvl", "INFO")))
        return cls(
            seq=seq,
            t=float(payload.get("t", 0.0)),
            mono=float(payload.get("mono", 0.0)),
            level=level if isinstance(level, int) else logging.INFO,
            logger=str(payload.get("log", "")),
            message=str(payload.get("msg", "")),
            thread=str(payload.get("thr", "")),
            event=payload.get("ev"),
            fields=payload.get("f"),
            dur_ms=payload.get("dur_ms"),
            span=payload.get("span"),
            parent=payload.get("parent"),
            exc=payload.get("exc"),
            session=str(payload.get("sess", "")),
        )


_FORMATTER = logging.Formatter()


def entry_from_record(record: logging.LogRecord, seq: int = 0) -> LogEntry:
    """Build the entry for *record*. The message is formatted here (once), the
    traceback only when there is one."""
    exc = record.exc_text
    if not exc and record.exc_info and record.exc_info[0] is not None:
        exc = _FORMATTER.formatException(record.exc_info)
    return LogEntry(
        seq=seq,
        t=record.created,
        mono=getattr(record, "lspr_mono", record.created),
        level=record.levelno,
        logger=record.name,
        message=record.getMessage(),
        thread=record.threadName or "",
        event=getattr(record, "lspr_event", None),
        fields=getattr(record, "lspr_fields", None) or None,
        dur_ms=getattr(record, "lspr_dur_ms", None),
        span=getattr(record, "lspr_span", None),
        parent=getattr(record, "lspr_parent", None),
        exc=exc or None,
        session=_session_id,
    )
