"""A helper process for the Sensorgram's fits (2026-10-24). No Qt, no h5py: the process starts from this file (Windows "spawn" start).

**Why a process.** Python lets one thread run Python code at a time. A fit in a worker *thread* is a dozen tiny numpy calls, and every one needs
that lock; the GUI thread needs it back after every Qt call, and waited ~3.6 ms each time on average (measured 2026-10-24, 5 ms switch interval),
so a redraw of a thousand Qt calls froze for seconds while the Sensorgram was fitting. In another process the fits cannot take the lock from the
window at all. (The batch fits in ``trace_fit`` make the fits 10-100x shorter; the process is what keeps a fit that is still long - the "classic" Gaussian
solver, a very narrow fit window, any future heavy fit - from freezing the window.)

**One warm process, more when a run is long (2026-11-26).** The helper process starts once, on first use (``warm_up`` does it ahead of time, off the GUI thread), runs at low
priority when Preferences says so (the window keeps its turn) and exits with the app. A *long* run (the Gaussian fit took 8.5 s and the Voigt fit 28 s for 160 ROIs x 2655 cubes
on one process) fans out over several processes, the count coming from ``lspr_core.perf`` (``get_runtime().begin("sensorgram.fits", ...)``: Standard = every core but one,
capped by free memory at ``_WORKER_BYTES`` per worker; Fixed and Tune automatically as in Preferences). The pool is made at that size but a process starts only when a task finds
no idle one, so a short run (the polynomial fit: 1.5-2 s) stays on the one warm process: **extra workers are used only when the measured remaining work pays for their
start-up** (~1-2 s each; ``_EXTRA_WORKER_PAYBACK_S`` of work per extra worker, from the compute time of the tasks finished so far; workers already running are free). Measured on the
real 2655-cube scan (4 cores / 8 threads): Gaussian 8.5 s -> 5.4 s (2 workers) -> 3.8 s (4) -> 3.65 s (7); Voigt 28.2 -> 11.6 (4) -> 10.2 (7); the polynomial fit 2.06 -> 1.40 (2) -> 1.02 (4),
not worth the start-up. The extra processes (~145 MB each) are released after ``_IDLE_SHRINK_S`` without a run and one warm helper is kept. The tasks are the same at any worker count
(``_chunks`` does not look at it) and the results are collected by ROI id, so **the traces are identical at any count and in any completion order**
(``tests/unit/test_lspri_analysis_trace_pool.py``).

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

**Time outside the fits (2026-11-26).** ``prep_ms`` / ``fit_ms`` are summed over the workers' own clocks, so they do not add up to ``wall_ms``: the polynomial fit (1.7 s of fitting) took 3.0 s
of wall time. In the process the run therefore also logs ``setup_ms`` (until the first task is submitted), ``send_ms`` / ``send_max_ms`` (task submitted -> worker has its arguments: pickling, the
pipe, waiting for a free worker, a new worker's start-up; sum / slowest task), ``back_ms`` / ``back_max_ms`` (traces ready in the worker -> parent has them), ``payload_mb`` (arrays sent),
``tasks``, ``pickle_one_ms`` / ``pickle_one_mb`` (one task pickled in the parent: the parent's own cost per task) and ``gil_lag_max_ms`` / ``gil_stalls`` (the longest wake-up of a sleeping thread of the app during the run, and how many took over
``_STALL_MS``: ``_GilProbe``; only a stall means something, see there). First reading, 21 logged runs: the gap
between wall time and fit work was 0.85-1.3 s with nothing else running and 1.7-3.7 s when ``session.capture`` / ``session.write`` overlapped the run (correlation 0.74). The sums over tasks overlap when several workers run, so compare ``send_max_ms`` with ``wall_ms``.
"""

from __future__ import annotations

import dataclasses
import faulthandler
import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, BrokenExecutor, Future, ProcessPoolExecutor, wait
from multiprocessing.reduction import ForkingPickler  # what ProcessPoolExecutor pickles its tasks with

import numpy as np
from lspr_core.perf import get_runtime, lower_process_priority

from ..applog import current as current_log_system
from ..applog.events import log_event
from .scan_view import RoiSpectrum, SpectrumFitJob, fit_spectra
from .settings import MetricSettings
from .trace_fit import BATCH_ROWS, TraceInput, traces_from_inputs

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.05  # how often a waiting run looks at ``cancelled``
_GIVE_UP_AFTER = 2  # failed starts / crashes of the helper process before this session stops using it
TASK_KEY = "sensorgram.fits"  # the name `lspr_core.perf` keeps learned worker counts under
_WORKER_BYTES = 250 * 1024 * 1024  # memory limit of one worker: measured 145 MB idle, 175 MB at the peak of a Voigt task (2026-11-26)
_EXTRA_WORKER_PAYBACK_S = 3.0  # a process started now costs ~1-2 s of start-up (spawn + numpy / scipy imports): each extra one must get at least this much work
TASK_ROWS = 6 * BATCH_ROWS
"""Cubes handed to a worker in one task (the ROIs are grouped until they reach this: 4 ROIs of 2655 cubes). Every task costs several interpreter-lock hand-offs in the parent
(submit, pickle, result), and each can wait a full switch interval (5 ms) while another thread of the app runs Python; with one ROI per task (160 tasks) a real run spent ~1.3 s of
a 1.4 s wall time outside the fits (``fit="none"``, 2026-11-26 log). Standalone benchmark, 188 MB in 160 ROIs to 7 workers, parent thread hogging the lock: 16.4 s with 1 ROI per task,
4.7 s with 4, 1.4 s with 23; idle parent: 0.28 s / 0.37 s / 0.44 s. Bigger tasks cost coarser progress and cancel and a worse tail (the last task runs alone), so not bigger than this."""
_IDLE_SHRINK_S = 120.0  # seconds without a run after which the extra processes are released (the one warm helper stays)


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
    """The traces of one task and the stage times (``trace_fit.traces_from_inputs`` ``timings``). Two clock stamps ride along under ``_recv`` / ``_done``
    (``time.time()`` seconds: the parent reads the same system clock, ``perf_counter``'s zero is not promised to be shared between processes): the moment
    the arguments had been unpickled here, and the moment the traces were ready to be sent back. The parent turns them into ``send_ms`` / ``back_ms``."""
    received = time.time()
    timings: dict[str, float] = {}
    traces = traces_from_inputs([data for _roi_id, data in chunk], formula_key, metric, timings)
    out = {roi_id: trace for (roi_id, _data), trace in zip(chunk, traces)}
    timings["_recv"], timings["_done"] = received, time.time()
    return out, timings


def _add_timings(total: dict[str, float], part: dict[str, float]) -> None:
    for key, value in part.items():
        if not key.startswith("_"):  # clock stamps (`_trace_task`), not durations: they are not summed or logged
            total[key] = total.get(key, 0.0) + value


def _payload_bytes(chunk: list[tuple[int, TraceInput]]) -> int:
    """Bytes of the arrays one task sends to the helper process (what has to be pickled and pushed through the pipe)."""
    return sum(
        int(value.nbytes)
        for _roi_id, data in chunk
        for field in dataclasses.fields(data)
        if isinstance(value := getattr(data, field.name), np.ndarray)
    )


class _GilProbe:
    """Measures how late a sleeping thread of this process gets the interpreter lock back (2026-11-26). A thread that sleeps ``period_s`` and then needs the lock to
    carry on is what the pool's own threads (submit, pickle, collect) do for every task; if another thread of the app is running Python (the window, the session autosave,
    a plot) it has to wait for that thread to let go, up to a whole switch interval (5 ms), and a long call that never lets go (a big ``json.dumps``) blocks it completely.
    The lateness of each wake-up is that wait.

    **Only a stall means anything, not the mean (measured 2026-11-26).** On Windows a 5 ms wait comes back ~10 ms late *whatever else runs*: an idle machine with no workers read
    mean 11.2 ms, p95 13.7 ms, and four busy worker processes with an idle parent read the same (10.3-10.6 ms); it follows the system timer's granularity, which other programs
    change (the first run of a session read 1.2 ms). A first version reported the mean and p95 and they were read as "a thread keeps running Python": they were this. What does stand
    out is a stall: the session autosave's write blocked the lock for 560-720 ms (the log of that run), against <= 43 ms when it did not run. Hence ``gil_lag_max_ms`` and the number
    of wake-ups later than ``_STALL_MS``. The probe wakes ~65 times a second (cheap, but not free)."""

    def __init__(self, period_s: float = 0.005) -> None:
        self._period = period_s
        self._stop = threading.Event()
        self._lags_ms: list[float] = []
        self._thread = threading.Thread(target=self._loop, name="gil-probe", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            self._stop.wait(self._period)  # releases the lock; coming back needs it
            if not self._stop.is_set():
                self._lags_ms.append(max(0.0, (time.perf_counter() - t0 - self._period) * 1e3))

    def stop(self) -> dict[str, float]:
        """Stop and summarize: ``gil_lag_max_ms``, ``gil_stalls`` (wake-ups later than ``_STALL_MS``), ``gil_samples`` (empty dict when too short to have a sample)."""
        self._stop.set()
        self._thread.join(timeout=0.1)
        return _summarize_lags(list(self._lags_ms))


_STALL_MS = 50.0  # well above the ~10-16 ms a wake-up is late on Windows anyway (`_GilProbe`)


def _summarize_lags(lags_ms: list[float]) -> dict[str, float]:
    if not lags_ms:
        return {}
    return {
        "gil_lag_max_ms": max(lags_ms),
        "gil_stalls": float(sum(lag > _STALL_MS for lag in lags_ms)),
        "gil_samples": float(len(lags_ms)),
    }


def _note_transfer(stage: dict[str, float], sent: float, task_timings: dict[str, float], received: float) -> None:
    """Add one finished task to the transfer times. ``send`` = submit until the worker had the arguments (pickling, the pipe, waiting for a free worker,
    unpickling; for the first task of a new process also its start-up); ``back`` = the worker had the traces until the parent had them (pickling, pipe, unpickling)."""
    send = max(0.0, (task_timings.get("_recv", sent) - sent) * 1e3)
    back = max(0.0, (received - task_timings.get("_done", received)) * 1e3)
    stage["send_ms"] += send
    stage["back_ms"] += back
    stage["send_max_ms"] = max(stage["send_max_ms"], send)
    stage["back_max_ms"] = max(stage["back_max_ms"], back)
    stage["tasks"] += 1


def _chunks(jobs: list[tuple[int, TraceInput]]) -> list[list[tuple[int, TraceInput]]]:
    """The ROIs in tasks of about ``TASK_ROWS`` cubes (>= 1 ROI). The fits inside a task are still cut into batches of ``BATCH_ROWS`` cubes
    (``trace_fit``), so the task size changes neither the numbers nor the fit speed: it only sets how many messages go back and forth."""
    per_task = max(1, TASK_ROWS // max(1, jobs[0][1].sample.shape[0]))
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
        self._trace_runs = 0  # Sensorgram runs in progress (the Spectra fits stay in their thread meanwhile)
        self._pool_size = 1  # most processes the executor may start (`lspr_core.perf`'s count when it was made)
        self._shrink_timer: threading.Timer | None = None

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
                self._pool_size = self._wanted_pool_size()
                self._executor = ProcessPoolExecutor(  # processes start only as tasks need them: warm_up and short runs use one
                    max_workers=self._pool_size, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker,
                    initargs=(low_priority, None if self._fault_path is None else str(self._fault_path)),
                )
            return self._executor

    @staticmethod
    def _wanted_pool_size() -> int:
        """How many processes ``lspr_core.perf`` allows for the fits (Preferences, cores, free memory); 1 when it cannot say."""
        try:
            return max(1, int(get_runtime().begin(TASK_KEY, per_worker_bytes=_WORKER_BYTES).maximum))
        except Exception:  # noqa: BLE001 - a missing / broken runtime must never stop the fits: one process is what it was before
            logger.debug("could not ask lspr_core.perf for the fit worker count", exc_info=True)
            return 1

    @staticmethod
    def _alive(executor: ProcessPoolExecutor | None) -> int:
        """Processes the executor has started so far (at least 1). Reads a private attribute, like `_exit_report`; never raises."""
        try:
            return max(1, len(getattr(executor, "_processes", None) or {}))
        except Exception:  # noqa: BLE001
            return 1

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
            if self._shrink_timer is not None:
                self._shrink_timer.cancel()
                self._shrink_timer = None
        self._discard()

    def _schedule_shrink(self) -> None:
        """After a run: when more than one process was started, release the extra ones if nothing runs for ``_IDLE_SHRINK_S`` (each holds ~145 MB)."""
        with self._lock:
            if self._closed or self._executor is None or self._alive(self._executor) <= 1:
                return
            if self._shrink_timer is not None:
                self._shrink_timer.cancel()
            timer = threading.Timer(_IDLE_SHRINK_S, self._shrink_if_idle)
            timer.daemon = True
            self._shrink_timer = timer
            timer.start()

    def _shrink_if_idle(self) -> None:
        """Timer thread: replace a pool of several processes by one warm helper, unless a run has started again."""
        with self._lock:
            self._shrink_timer = None
            if self._closed or self._trace_runs > 0 or self._executor is None or self._alive(self._executor) <= 1:
                return
            executor, self._executor = self._executor, None
            executor.shutdown(wait=False, cancel_futures=True)  # inside the lock: `run_spectra` submits under it, so it never meets a shut-down executor
        log_event(logger, "sensorgram.pool_shrunk", logging.DEBUG, idle_s=_IDLE_SHRINK_S)
        self.warm_up()  # one process back, ready for the next short run (this is the timer's own thread)

    # -- the Spectra panel's fits (one cube, several ROIs) -------------------------------------------------------------------------------

    def run_spectra(self, jobs: list[SpectrumFitJob], metric: MetricSettings) -> list[RoiSpectrum]:
        """``scan_view.fit_spectra`` in the helper process (2026-11-02): the Spectra panel refits every selected ROI at each cube change, and
        in a thread of the app that took the interpreter lock from the window as the Sensorgram's fits did. Same function, same numbers; if the
        process is not available or dies, the fits run in the calling thread (logged, as for the traces). Shares the one process with the
        Sensorgram. Not used (the fits run in the calling thread, as before) while the process is not started yet (starting takes ~2 s; it is
        started in the background for the next time) or busy with a Sensorgram run (a Gaussian fit of 160 ROIs can take long)."""
        if not jobs:
            return []
        with self._lock:
            ready = self._executor is not None and self._trace_runs == 0 and not self._closed
            start = self._executor is None and not self._closed and self._failures < _GIVE_UP_AFTER
        if not ready:
            if start:
                threading.Thread(target=self.warm_up, name="trace-pool-warmup", daemon=True).start()
            return fit_spectra(jobs, metric)
        t0 = time.perf_counter()
        try:
            executor = self._get()
            if executor is None:
                raise BrokenExecutor("the helper process is not available in this session")
            with self._lock:  # the idle shrink replaces the executor under this lock, so a submit never meets a shut-down one
                future = executor.submit(fit_spectra, jobs, metric) if self._executor is executor else None
            if future is None:
                return fit_spectra(jobs, metric)  # the pool was just replaced: this once, in this thread
            out = future.result()  # an error inside a fit is raised here, to the caller
            log_event(logger, "spectra.fit_run", logging.DEBUG, where="process", rois=len(jobs), wall_ms=round((time.perf_counter() - t0) * 1e3, 1))
            return out
        except (BrokenExecutor, OSError, EOFError) as exc:
            self._failures += 1
            exit_report = self._exit_report(self._executor)
            self._discard()
            log_event(
                logger, "spectra.pool_fallback", logging.WARNING, reason=f"{type(exc).__name__}: {exc}", failures=self._failures,
                after_ms=round((time.perf_counter() - t0) * 1e3, 1), **exit_report,
            )
        return fit_spectra(jobs, metric)

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
        waits (and so does not hold the interpreter lock). ROIs go in tasks of ~``TASK_ROWS`` cubes; ``progress(done ROIs, total)`` after each task;
        ``cancelled()`` is looked at every 50 ms and the tasks not yet started are dropped (one already running finishes in the process and is
        ignored)."""
        done: dict[int, np.ndarray] = {}
        if not jobs:
            return done, False
        with self._lock:
            self._trace_runs += 1
            if self._shrink_timer is not None:
                self._shrink_timer.cancel()
                self._shrink_timer = None
        try:
            return self._run(jobs, formula_key, metric, cancelled, progress, done)
        finally:
            with self._lock:
                self._trace_runs -= 1
            self._schedule_shrink()

    def _run(self, jobs, formula_key, metric, cancelled, progress, done) -> tuple[dict[int, np.ndarray], bool]:
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

    def _in_flight_limit(self, run, executor, tasks_left: int, task_seconds: list[float]) -> int:
        """How many tasks to keep submitted now. One until a task has been timed (and always for a run of one task); then as many as ``lspr_core.perf`` allows, but extra processes
        only when the work left pays for starting them: ``tasks_left x mean compute time of a task`` must give each extra worker ``_EXTRA_WORKER_PAYBACK_S`` of work (processes
        already running cost nothing, so they are always used). The estimate comes from the tasks finished so far, so it needs no per-machine constants."""
        if run is None or not task_seconds:
            return 1
        worth = int(tasks_left * (sum(task_seconds) / len(task_seconds)) / max(_EXTRA_WORKER_PAYBACK_S, 1e-6))  # (0 = every worker is worth it: the tests set that)
        return max(1, min(run.workers, self._pool_size, max(self._alive(executor), worth), tasks_left))

    def _run_in_process(self, jobs, formula_key, metric, cancelled, progress, done, timings) -> tuple[dict[int, np.ndarray], bool]:
        executor = self._get()
        if executor is None:
            raise BrokenExecutor("the helper process is not available in this session")
        chunks = _chunks(jobs)
        run = None
        if len(chunks) > 1:  # one task is one process: nothing to fan out or tune
            run = get_runtime().begin(TASK_KEY, total_units=len(chunks), per_worker_bytes=_WORKER_BYTES, max_useful=len(chunks))
            if run.maximum > self._pool_size:  # Preferences allow more than the pool was made for: a bigger one (this costs the warm process)
                self._discard()
                executor = self._get()
                if executor is None:
                    raise BrokenExecutor("the helper process is not available in this session")
        pending: set[Future] = set()
        task_seconds: list[float] = []  # compute time of each finished task (the worker's own clock: no queueing, no pickling)
        next_task, peak = 0, 1
        # Where the wall time goes outside the fits (2026-11-26: the polynomial fit took 1.7 s of fitting in 3.0 s of wall time): `setup_ms` = this call until the first
        # submit (pool, worker count), `send_*` / `back_*` = the trip of each task to the worker and back (`_note_transfer`), `payload_mb` = what was pickled to the worker.
        stage = {"send_ms": 0.0, "send_max_ms": 0.0, "back_ms": 0.0, "back_max_ms": 0.0, "payload_mb": 0.0, "tasks": 0.0}
        sent_at: dict[Future, float] = {}
        started, setup_ms = time.perf_counter(), None
        probe = _GilProbe()
        probe.start()
        try:
            t_pickle = time.perf_counter()  # what the executor does to one task before it can send it (its feeder thread, here measured in this one): CPU of this process, or the lock
            blob = ForkingPickler.dumps((_trace_task, chunks[0], formula_key, metric))
            stage["pickle_one_ms"], stage["pickle_one_mb"] = (time.perf_counter() - t_pickle) * 1e3, memoryview(blob).nbytes / 1e6
            while next_task < len(chunks) or pending:
                if cancelled is not None and cancelled():
                    return done, True
                limit = self._in_flight_limit(run, executor, len(chunks) - len(task_seconds), task_seconds)
                while next_task < len(chunks) and len(pending) < limit:
                    if setup_ms is None:
                        setup_ms = (time.perf_counter() - started) * 1e3
                    stamp = time.time()
                    future = executor.submit(_trace_task, chunks[next_task], formula_key, metric)
                    sent_at[future] = stamp
                    stage["payload_mb"] += _payload_bytes(chunks[next_task]) / 1e6
                    pending.add(future)
                    next_task += 1
                peak = max(peak, len(pending))
                finished, pending = wait(pending, timeout=_POLL_SECONDS, return_when=FIRST_COMPLETED)
                received_at = time.time()
                for future in finished:
                    traces, task_timings = future.result()  # an error inside a fit is raised here, to the caller
                    done.update(traces)
                    _note_transfer(stage, sent_at.pop(future), task_timings, received_at)
                    _add_timings(timings, task_timings)
                    task_seconds.append(sum(task_timings.get(key, 0.0) for key in ("prep_ms", "fit_ms", "per_cube_ms")) / 1000.0)
                    if run is not None:
                        run.unit_done(1)
                    if progress is not None:
                        progress(len(done), len(jobs))
        finally:
            for future in pending:
                future.cancel()
            timings["workers"] = peak  # the most tasks in flight at once = processes used (also for a cancelled run)
            timings.update(stage)
            timings.update(probe.stop())
            timings["setup_ms"] = (time.perf_counter() - started) * 1e3 if setup_ms is None else setup_ms
        if run is not None:
            log_event(logger, "perf.run", logging.DEBUG, **run.finish().as_fields())
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
