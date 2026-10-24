"""A helper process for the Sensorgram's fits (2026-10-24). No Qt, no h5py: the process starts from this file (Windows "spawn" start).

**Why a process.** Python lets one thread run Python code at a time. A fit in a worker *thread* is a dozen tiny numpy calls, and every one needs
that lock; the GUI thread needs it back after every Qt call, and waited ~3.6 ms each time on average (measured 2026-10-24, 5 ms switch interval),
so a redraw of a thousand Qt calls froze for seconds while the Sensorgram was fitting. In another process the fits cannot take the lock from the
window at all. (The batch fits in ``trace_fit`` make the fits 10-100x shorter; the process is what keeps a fit that is still long - the "classic" Gaussian
solver, a very narrow fit window, any future heavy fit - from freezing the window.)

**One process, not a pool.** This is isolation, not fan-out: the work is one ROI after another and the batch fit is fast, so ``lspr_core.perf``
tuning (which tunes the *number* of workers on measured throughput) has nothing to tune; a fan-out of the Gaussian fit over several processes is a
possible later step and would take its count from ``lspr_core.perf`` like the scan does. The process starts once, on first use (``warm_up`` does it
ahead of time, off the GUI thread), runs at low priority when Preferences says so (the window keeps its turn) and exits with the app.

**Results never depend on where they are computed:** the process runs exactly ``trace_fit.traces_from_inputs`` on the same arrays, so its traces are
equal to the in-thread ones (pinned by ``tests/unit/test_lspri_analysis_trace_pool.py``).

**Failure is not silent.** If the process cannot start or dies, the run falls back to computing in the calling thread (same numbers, slower and
the window may stutter), logs a WARNING (``sensorgram.pool_fallback``) and, after a second failure, stops trying for this session. An error
*inside* a fit is not swallowed: it is raised to the caller, as it was in the thread.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, BrokenExecutor, Future, ProcessPoolExecutor, wait

import numpy as np
from lspr_core.perf import get_runtime, lower_process_priority

from ..applog import log_event
from .settings import MetricSettings
from .trace_fit import BATCH_ROWS, TraceInput, traces_from_inputs

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.05  # how often a waiting run looks at ``cancelled``
_GIVE_UP_AFTER = 2  # failed starts / crashes of the helper process before this session stops using it


def _init_worker(low_priority: bool) -> None:
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)


def _ping() -> int:
    return 1


def _trace_task(chunk: list[tuple[int, TraceInput]], formula_key: str, metric: MetricSettings) -> dict[int, np.ndarray]:
    traces = traces_from_inputs([data for _roi_id, data in chunk], formula_key, metric)
    return {roi_id: trace for (roi_id, _data), trace in zip(chunk, traces)}


def _chunks(jobs: list[tuple[int, TraceInput]]) -> list[list[tuple[int, TraceInput]]]:
    """The ROIs in tasks of about ``BATCH_ROWS`` cubes (several ROIs per task: a fit batch is cheapest per cube at that size, and one message
    per ROI would be mostly overhead)."""
    per_task = max(1, BATCH_ROWS // max(1, jobs[0][1].sample.shape[0]))
    return [jobs[i : i + per_task] for i in range(0, len(jobs), per_task)]


class TracePool:
    """One helper process computing metric traces. ``run`` is called by one thread at a time (the Sensorgram's compute thread)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None
        self._failures = 0
        self._closed = False

    # -- the process --------------------------------------------------------------------------------------------------------------

    def _get(self) -> ProcessPoolExecutor | None:
        with self._lock:
            if self._closed or self._failures >= _GIVE_UP_AFTER:
                return None
            if self._executor is None:
                low_priority = bool(get_runtime().preferences.low_priority)
                self._executor = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker, initargs=(low_priority,),
                )
            return self._executor

    def _discard(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def warm_up(self) -> None:
        """Start the process and let it import numpy / scipy now, so the first fit does not wait for that. Blocks until it is ready (call it from
        a thread that may wait) and never raises: a failure here shows up, and is handled, at the first ``run``."""
        try:
            executor = self._get()
            if executor is not None:
                executor.submit(_ping).result(timeout=60.0)
        except Exception:  # noqa: BLE001 - run() deals with a process that does not work
            logger.debug("trace pool warm-up failed", exc_info=True)

    def shutdown(self) -> None:
        """Application is quitting: the process is told to exit; nothing waits for it."""
        with self._lock:
            self._closed = True
        self._discard()

    # -- running ---------------------------------------------------------------------------------------------------------------------

    def run(
        self,
        jobs: list[tuple[int, TraceInput]],
        formula_key: str,
        metric: MetricSettings,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[dict[int, np.ndarray], bool]:
        """The traces of ``jobs`` (ROI id, inputs): ``(finished traces, cancelled before the end)``. Fits run in the helper process; this thread only
        waits (and so does not hold the interpreter lock). ROIs go in tasks of ~``BATCH_ROWS`` cubes; ``progress(done ROIs, total)`` after each task;
        ``cancelled()`` is looked at every 50 ms and the tasks not yet started are dropped (one already running finishes in the process and is
        ignored)."""
        done: dict[int, np.ndarray] = {}
        if not jobs:
            return done, False
        try:
            return self._run_in_process(jobs, formula_key, metric, cancelled, progress, done)
        except (BrokenExecutor, OSError, EOFError) as exc:  # could not start, or died: the numbers are the same in this thread
            self._failures += 1
            self._discard()
            log_event(
                logger, "sensorgram.pool_fallback", logging.WARNING,
                reason=f"{type(exc).__name__}: {exc}", failures=self._failures, finished=len(done), total=len(jobs),
            )
        rest = [job for job in jobs if job[0] not in done]
        was_cancelled = self._run_in_thread(rest, formula_key, metric, cancelled, progress, done, len(jobs))
        return done, was_cancelled

    def _run_in_process(self, jobs, formula_key, metric, cancelled, progress, done) -> tuple[dict[int, np.ndarray], bool]:
        executor = self._get()
        if executor is None:
            raise BrokenExecutor("the helper process is not available in this session")
        futures: list[Future] = [executor.submit(_trace_task, chunk, formula_key, metric) for chunk in _chunks(jobs)]
        pending = set(futures)
        try:
            while pending:
                if cancelled is not None and cancelled():
                    return done, True
                finished, pending = wait(pending, timeout=_POLL_SECONDS, return_when=FIRST_COMPLETED)
                for future in finished:
                    done.update(future.result())  # an error inside a fit is raised here, to the caller
                    if progress is not None:
                        progress(len(done), len(jobs))
        finally:
            for future in pending:
                future.cancel()
        return done, False

    @staticmethod
    def _run_in_thread(jobs, formula_key, metric, cancelled, progress, done, total) -> bool:
        for chunk in _chunks(jobs) if jobs else []:
            if cancelled is not None and cancelled():
                return True
            done.update(_trace_task(chunk, formula_key, metric))
            if progress is not None:
                progress(len(done), total)
        return False
