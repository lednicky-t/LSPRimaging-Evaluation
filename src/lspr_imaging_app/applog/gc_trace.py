"""Which thread runs Python's cyclic garbage collector, and how long does it stop everything? (2026-10-14)

**Why.** Python frees ordinary objects the moment nothing refers to them, but objects that refer to each
other in a loop (a Qt item and the slot connected to it, a widget and its helper) wait for the *cyclic
garbage collector*. It runs on **whichever thread happens to allocate memory when its counter trips**, not
on the GUI thread, and while it runs every other thread is stopped. Two consequences matter here:

- If it frees a Qt object on a worker thread (render, histogram, log writer) while the GUI thread is
  painting, Qt can crash. The 2026-10-14 crash (`QRasterPaintEngine::penChanged`, GUI thread, inside a
  paint) has exactly that shape, but nothing proved the collector ran on a worker thread. This does.
- A full collection of a big heap can freeze the GUI for tens of milliseconds, which shows up as a frame
  that is late for no visible reason.

**What it records.** One DEBUG record per 5 s (``gc.summary``) while there was any collection: how many
ran, how many were full, how many ran **off the main (GUI) thread** and on which threads, the longest pause
and where, the total pause time and the number of objects freed. Nothing is logged *from inside* the
collector's callback: a log call there can deadlock (the collection may have started while this very thread
held the logging queue's lock), so the callback only adds up numbers and `flush()` (called from a GUI timer)
writes the record.

Installed by `configure_logging` (the app only; tests never get it).
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .events import log_event

logger = logging.getLogger(__name__)


@dataclass
class _Totals:
    count: int = 0
    full: int = 0
    off_main: int = 0
    off_threads: dict[str, int] = field(default_factory=dict)
    total_ms: float = 0.0
    max_ms: float = 0.0
    max_thread: str = ""
    collected: int = 0


class GcTrace:
    def __init__(self, main_ident: int | None = None, clock: Callable[[], float] = time.perf_counter) -> None:
        self._main_ident = main_ident if main_ident is not None else threading.main_thread().ident
        self._clock = clock
        self._started = 0.0  # when the running collection began (collections never overlap)
        self._totals = _Totals()
        self._since = clock()
        self._installed = False

    # -- the collector's callback: numbers only, no logging, no locks ---------------------

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        if phase == "start":
            self._started = self._clock()
            return
        duration_ms = (self._clock() - self._started) * 1000.0
        totals = self._totals  # `flush` swaps the object; a count landing in the old one is harmless
        totals.count += 1
        totals.total_ms += duration_ms
        totals.collected += int(info.get("collected", 0))
        if int(info.get("generation", 0)) >= 2:
            totals.full += 1
        if threading.get_ident() != self._main_ident:
            name = threading.current_thread().name
            totals.off_main += 1
            totals.off_threads[name] = totals.off_threads.get(name, 0) + 1
        if duration_ms > totals.max_ms:
            totals.max_ms = duration_ms
            totals.max_thread = threading.current_thread().name

    # -- install / report ---------------------------------------------------------------------

    def install(self) -> None:
        if not self._installed:
            gc.callbacks.append(self._on_gc)
            self._installed = True

    def uninstall(self) -> None:
        if self._installed:
            try:
                gc.callbacks.remove(self._on_gc)
            except ValueError:
                pass
            self._installed = False

    def flush(self) -> bool:
        """Log the summary of everything since the last call (DEBUG, event ``gc.summary``). False when there was nothing."""
        totals, self._totals = self._totals, _Totals()
        now = self._clock()
        seconds, self._since = now - self._since, now
        if totals.count == 0:
            return False
        log_event(
            logger, "gc.summary", logging.DEBUG,
            seconds=round(seconds, 1), collections=totals.count, full=totals.full,
            off_main=totals.off_main,
            off_main_threads=",".join(f"{name}={n}" for name, n in sorted(totals.off_threads.items())),
            total_ms=round(totals.total_ms, 1), max_ms=round(totals.max_ms, 1), max_thread=totals.max_thread,
            collected=totals.collected,
        )
        return True
