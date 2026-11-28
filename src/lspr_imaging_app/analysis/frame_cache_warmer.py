"""Fills the snapshot's :class:`FrameTableCache` in short steps on the GUI thread (2026-11-28), so the staleness check never freezes the window.

**Why.** The check needs a snapshot of the app's inputs, and reading them is 1.2-3.3 s of work that only the GUI thread may do (the modules are not
thread-safe). With the cache a *second* snapshot costs 63 ms, but the first one after the launch, or after a change that invalidates the cache, is a
single 1.2-3.3 s freeze (log of 2026-11-27: ``snapshot_ms`` 1.9-8.2 s; `docs/startup_and_close_2026-11-28.md`). This reads the same tables a few
milliseconds at a time (``warm_frame_tables``), with a pause between steps in which the event loop draws and answers input, and then lets the check go on;
its snapshot reads nothing.

**Safe against a change meanwhile.** Anything that can change the tables invalidates the cache (``FrameTableCache.generation`` grows); a step that finds the
generation changed throws its source away (its chromatic memo is only valid until the next change) and starts again with a new one, so nothing read before a
change is ever kept after it. If reading fails, or there is no dataset any more, the waiting caller is told and does the ordinary synchronous snapshot, which
reports the problem the way it always did.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from ..applog import log_event
from .scan_jobs import FrameTableCache, ScanSource, warm_frame_tables

logger = logging.getLogger(__name__)

SLICE_S = 0.015  # the GUI thread is held for at most about this long per step
PAUSE_MS = 5  # between two steps: the event loop runs (painting, input, other timers)


class FrameCacheWarmer(QObject):
    def __init__(self, cache: FrameTableCache, source_factory: Callable[[], ScanSource], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._source_factory = source_factory
        self._source: ScanSource | None = None
        self._source_generation = -1
        self._then: Callable[[], None] | None = None
        self._failed_generation = -1  # a generation whose tables could not be read: the caller's own snapshot reports why
        self._started = 0.0
        self._steps = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PAUSE_MS)
        self._timer.timeout.connect(self._step)

    def is_filled_for(self, source: ScanSource) -> bool:
        """True when a snapshot of ``source`` would read nothing from the modules now (or this cannot be made so: reading failed for the current
        generation, then the caller's synchronous snapshot goes ahead and reports it)."""
        return self._cache.generation == self._failed_generation or self._cache.is_filled_for(source)

    def warm(self, then: Callable[[], None]) -> None:
        """Fill the cache in steps and call ``then()`` (GUI thread) when it is filled. A call while a fill is running only replaces ``then``."""
        self._then = then
        if not self._timer.isActive() and self._source is None:
            self._started, self._steps = time.perf_counter(), 0
            self._timer.start(0)

    def _finish(self, outcome: str) -> None:
        then, self._then, self._source = self._then, None, None
        log_event(logger, "frame_cache.warmed", logging.DEBUG, outcome=outcome, steps=self._steps, total_ms=round((time.perf_counter() - self._started) * 1000))
        if then is not None:
            then()

    def _step(self) -> None:
        self._steps += 1
        try:
            if self._source is None or self._source_generation != self._cache.generation:  # (re)start: a change threw the old tables away
                self._source_generation = self._cache.generation
                self._source = self._source_factory()
            done = warm_frame_tables(self._source, SLICE_S)
        except (ValueError, RuntimeError) as exc:  # no dataset, no ROIs, ...: the ordinary snapshot says so
            self._failed_generation = self._cache.generation
            logger.debug("frame cache warm-up stopped: %s", exc)
            self._finish("unavailable")
            return
        except Exception:  # noqa: BLE001 - never leave the check waiting; the synchronous snapshot meets the same error and reports it
            self._failed_generation = self._cache.generation
            logger.warning("frame cache warm-up failed", exc_info=True)
            self._finish("failed")
            return
        if done and self._source_generation == self._cache.generation:
            self._finish("filled")
        else:
            self._timer.start()
