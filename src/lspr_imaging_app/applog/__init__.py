"""Application logging and diagnostics (2026-10-14). Design: `docs/logging_design_2026-10-14.md`.

Typical use in a module::

    import logging
    from ..applog import log_event, timed

    logger = logging.getLogger(__name__)

    with timed(logger, "dataset.load", folder=name) as t:
        ...
        t.set(planes=n)
    log_event(logger, "task.finished", task="find_reference", outcome="completed")

Everything here is Qt-free; the Log panel lives in `panels/log/`.
"""

from __future__ import annotations

from .aggregate import TimingAggregator, TimingRow
from .entry import LogEntry
from .events import emit_timing, log_event, timed, timed_call
from .filters import EntryFilter
from .handlers import RingBufferHandler
from .setup import LogSystem, configure_logging, current

__all__ = [
    "EntryFilter",
    "LogEntry",
    "LogSystem",
    "RingBufferHandler",
    "TimingAggregator",
    "TimingRow",
    "configure_logging",
    "current",
    "emit_timing",
    "log_event",
    "timed",
    "timed_call",
]
