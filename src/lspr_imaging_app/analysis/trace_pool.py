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

**Why it stopped, and where the time went (2026-10-24).** A helper that died left only "terminated abruptly" in the log (a launch on 2026-10-24 fell
back to the thread and the Sensorgram took 39 s; the cause was never seen). Now: the process writes a fatal-error traceback (``faulthandler``) to
``logs/lspri-<session>.jsonl.pool<n>.log`` beside the session log, and when it dies the WARNING carries its *exit code* (Windows NTSTATUS, e.g.
``0xC0000005`` = access violation; ``1`` = it raised while starting, and its traceback went to the console that launched the app). Each ``run`` logs
one ``sensorgram.trace_run`` event: where it ran, the settings, and the time of each stage (``prep_ms`` / ``fit_ms`` / ``per_cube_ms``), plus, in the
thread, how much of the wall time the thread was actually computing (``cpu_ms``; far below ``dur_ms`` = it waited for the interpreter lock).
"""

from __future__ import annotations

import faulthandler
import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, BrokenExecutor, Future, ProcessPoolExecutor, wait

import numpy as np
from lspr_core.perf import get_runtime, lower_process_priority

from ..applog import current as current_log_system
from ..applog import log_event
from .settings import MetricSettings
from .trace_fit import BATCH_ROWS, TraceInput, traces_from_inputs

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.05  # how often a waiting run looks at ``cancelled``
_GIVE_UP_AFTER = 2  # failed starts / crashes of the helper process before this session stops using it


_fault_file = None  # in the helper process: kept open for as long as the process lives (faulthandler writes to it when the process crashes)


def _init_worker(low_priority: bool, fault_path: str | None = None) -> None:
    global _fault_file
    if fault_path:
        try:
            _fault_file = open(fault_path, "w", encoding="utf-8")  # noqa: SIM115 - must outlive this function
            faulthandler.enable(_fault_file, all_threads=True)
        except OSError:
            pass  # a diagnostic aid: never a reason for the helper not to start
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)


def _ping() -> int:
    return 1


def _trace_task(
    chunk: list[tuple[int, TraceInput]], formula_key: str, metric: MetricSettings
) -> tuple[dict[int, np.ndarray], dict[str, float]]:
    """The traces of one task and the stage times (``trace_fit.traces_from_inputs`` ``timings``)."""
    timings: dict[str, float] = {}
    traces = traces_from_inputs([data for _roi_id, data in chunk], formula_key, metric, timings)
    return {roi_id: trace for (roi_id, _data), trace in zip(chunk, traces)}, timings


def _add_timings(total: dict[str, float], part: dict[str, float]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0.0) + value


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
        self._started = 0  # helper processes started this session (numbers the crash-dump file)
        self._fault_path: Path | None = None

    # -- the process --------------------------------------------------------------------------------------------------------------

    def _get(self) -> ProcessPoolExecutor | None:
        with self._lock:
            if self._closed or self._failures >= _GIVE_UP_AFTER:
                return None
            if self._executor is None:
                low_priority = bool(get_runtime().preferences.low_priority)
                self._started += 1
                system = current_log_system()
                self._fault_path = None if system is None else system.log_path.with_name(f"{system.log_path.name}.pool{self._started}.log")
                self._executor = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker,
                    initargs=(low_priority, None if self._fault_path is None else str(self._fault_path)),
                )
            return self._executor

    def _exit_report(self, executor: ProcessPoolExecutor | None) -> dict[str, object]:
        """Why the helper is gone: the exit code of its process (0xC0000005 = access violation, 0xC0000409 = stack buffer overrun / abort, 1 =
        raised while starting) and its crash-dump file when that has something in it. Never raises (it reads a private attribute of the executor)."""
        report: dict[str, object] = {}
        try:
            processes = dict(getattr(executor, "_processes", None) or {})
            report["exit_codes"] = ",".join(
                "running" if p.exitcode is None else hex(p.exitcode & 0xFFFFFFFF) if p.exitcode < 0 or p.exitcode > 255 else str(p.exitcode)
                for p in processes.values()
            ) or "unknown"
            fault = self._fault_path
            if fault is not None and fault.exists() and fault.stat().st_size > 0:
                report["crash_dump"] = str(fault)
        except Exception:  # noqa: BLE001 - a diagnostic must not break the fallback
            report["exit_codes"] = "unreadable"
        return report

    def _discard(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def warm_up(self) -> None:
        """Start the process and let it import numpy / scipy now, so the first fit does not wait for that. Blocks until it is ready (call it from
        a thread that may wait) and never raises: a failure here shows up, and is handled, at the first ``run``."""
        executor = None
        t0 = time.perf_counter()
        try:
            executor = self._get()
            if executor is not None:
                executor.submit(_ping).result(timeout=60.0)
                log_event(logger, "sensorgram.pool_ready", logging.DEBUG, start_ms=round((time.perf_counter() - t0) * 1e3, 1))
        except Exception as exc:  # noqa: BLE001 - run() deals with a process that does not work
            log_event(
                logger, "sensorgram.pool_warmup_failed", logging.WARNING, reason=f"{type(exc).__name__}: {exc}",
                after_ms=round((time.perf_counter() - t0) * 1e3, 1), **self._exit_report(executor),
            )
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
        timings: dict[str, float] = {}
        t0, cpu0 = time.perf_counter(), time.thread_time()
        try:
            result = self._run_in_process(jobs, formula_key, metric, cancelled, progress, done, timings)
            self._log_run("process", jobs, metric, formula_key, t0, cpu0, timings, result[1])
            return result
        except (BrokenExecutor, OSError, EOFError) as exc:  # could not start, or died: the numbers are the same in this thread
            self._failures += 1
            exit_report = self._exit_report(self._executor)  # before _discard: shutting the executor down forgets its processes
            self._discard()
            log_event(
                logger, "sensorgram.pool_fallback", logging.WARNING,
                reason=f"{type(exc).__name__}: {exc}", failures=self._failures, finished=len(done), total=len(jobs),
                after_ms=round((time.perf_counter() - t0) * 1e3, 1), **exit_report,
            )
        rest = [job for job in jobs if job[0] not in done]
        t0, cpu0 = time.perf_counter(), time.thread_time()
        timings = {}
        was_cancelled = self._run_in_thread(rest, formula_key, metric, cancelled, progress, done, len(jobs), timings)
        self._log_run("thread", rest, metric, formula_key, t0, cpu0, timings, was_cancelled)
        return done, was_cancelled

    @staticmethod
    def _log_run(where, jobs, metric, formula_key, t0, cpu0, timings, was_cancelled) -> None:
        """One DEBUG event per run: where it ran, what it fitted, and the time of each stage (``timings`` add up the tasks, so with the helper
        process they are its busy time, which may overlap in the wall time ``dur_ms``)."""
        first = jobs[0][1] if jobs else None
        log_event(
            logger, "sensorgram.trace_run", logging.DEBUG,
            where=where, rois=len(jobs), cubes=0 if first is None else int(first.sample.shape[0]),
            wavelengths=0 if first is None else int(first.sample.shape[1]),
            fit=str(metric.fit_method), poly_order=int(metric.poly_order), metric=str(metric.metric_key), formula=str(formula_key),
            wall_ms=round((time.perf_counter() - t0) * 1e3, 1),
            cpu_ms=round((time.thread_time() - cpu0) * 1e3, 1) if where == "thread" else None,
            cancelled=bool(was_cancelled), **{k: round(v, 1) for k, v in timings.items()},
        )

    def _run_in_process(self, jobs, formula_key, metric, cancelled, progress, done, timings) -> tuple[dict[int, np.ndarray], bool]:
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
                    traces, task_timings = future.result()  # an error inside a fit is raised here, to the caller
                    done.update(traces)
                    _add_timings(timings, task_timings)
                    if progress is not None:
                        progress(len(done), len(jobs))
        finally:
            for future in pending:
                future.cancel()
        return done, False

    @staticmethod
    def _run_in_thread(jobs, formula_key, metric, cancelled, progress, done, total, timings) -> bool:
        for chunk in _chunks(jobs) if jobs else []:
            if cancelled is not None and cancelled():
                return True
            traces, task_timings = _trace_task(chunk, formula_key, metric)
            done.update(traces)
            _add_timings(timings, task_timings)
            if progress is not None:
                progress(len(done), total)
        return False
