"""Runs analysis step 1 (the scan) off the GUI thread, saves it to ``scan.h5`` and reports it to the status bar (2026-10-23).

The pure parts are ``scan_jobs`` (inputs -> plan), ``scan`` (plan -> numbers, worker processes), ``scan_fingerprint`` (which stored cells are
still current), ``scan_store`` (the file) and ``scan_arrays`` (the arrays of one run); this is only the thread / signal / Cancel wrapper the rule
in CLAUDE.md asks for. The thread, signals, Cancel and the stale-result rule are in :class:`~..threaded_task.ThreadedTask`.

**Split between the two threads** (design record: ``docs/analysis_redesign_plan_2026-10-19.md`` sections 15-18):

* GUI thread, in ``start()``: ``snapshot_source`` reads everything the scan needs from the app into plain tables (a few lookups per
  frame, ROIs copied, ignore masks still compressed). Afterwards the task touches no module, so an edit made during the run cannot change it.
* Task thread: decode the ignore masks and build the plan (``build_scan_plan``), hash every cell, open the file, then ``scan_cubes`` in worker
  *processes* (the thread is a plain ``threading.Thread``, never ``QThreadPool``: the workers read the dataset, see CLAUDE.md). Each finished cube
  is handed to ``scan_store.ScanWriter`` (a thread of its own, batches of up to 16 cubes, crash-safe order of ``scan_store``), so the collector never
  waits for the disk and the pool is not left idle; a cancelled run keeps every cube it finished (the writer drains first).

**What is scanned.** With a ``store_path`` and ``only_stale`` (the Fast scan button) a cube is scanned only if the file holds no current result
for one of its cells (hash differs or never computed); a run on an unchanged session finishes in a few seconds with "up to date". The unit
of work is the cube, but since 2026-10-27 a cube with only a few stale ROIs is computed for those ROIs alone (``CubeJob.roi_subset``; the
pixels read are still the whole frame, so the saving is the per-ROI work, until chunk reads exist): moving one ROI makes its cells stale
(``scan_fingerprint``) and nothing else, unless background removal depends on the ROI positions. More than ``SUBSET_MAX_SHARE`` of a cube's ROIs
stale: the whole cube is computed (the cells that were current come out identical and are rewritten), which keeps the layouts a worker holds small.

**Progress** is split into stages weighted by measured time: preparing the plan (mask decoding, hashing) is ~2-3 s of a ~3 min run, so it gets the
first 2 % of the bar. **Logging**: ``analysis.scan_prepared`` (plan build time), ``analysis.scan_start``, one DEBUG ``analysis.scan_cube`` per
cube (busy seconds per stage), and ``analysis.scan_done`` / ``analysis.scan_current`` / ``analysis.scan_cancelled`` with wall time, frames per
second, the worker count and mode from ``lspr_core.perf``, the busy time per stage and the time spent writing. The worker processes have no
logging of their own: their timings come back in ``CubeResult.seconds``.

The arrays of the run are also handed to ``on_result`` on the GUI thread (tests; later panels). A cancelled or stale run installs nothing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from lspr_core.perf import RunReport, get_runtime
from PyQt6.QtCore import QObject, pyqtSignal

from ..applog import log_event, timed
from ..progress import Cancelled, throttled
from ..threaded_task import TaskRefused, ThreadedTask
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from .scan import _SCAN_WORKER_BYTES, scan_cubes
from .scan_arrays import ScanArrays, new_arrays, place
from .scan_fingerprint import cell_hashes, stamped_by_previous_formula
from .scan_pool import MODE_NORMAL, MODES, WarmPool, uses_warm_pool
from .scan_jobs import ScanPlan, ScanSource, build_scan_plan, snapshot_source, subset_plan
from .scan_store import ScanStore, ScanWriter, stale_cell_matrix

TASK_ID = "analysis_scan"
TASK_LABEL = "Analysis scan"

PREPARE_SHARE = 0.02  # first part of the bar: decoding the masks, building and hashing the plan (~2-3 s of ~3 min measured)
SUBSET_MAX_SHARE = 0.5
"""A cube is computed for its stale ROIs only when they are at most this share of its ROIs. A speed and memory knob (a worker keeps one
restricted layout per wavelength and subset); it never changes a result."""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanSummary:
    """What a finished scan reports (status bar text, log, tests)."""

    cubes: int  # cubes scanned in this run
    frames: int
    rois: int  # ROIs in the file
    wavelengths: int
    seconds: float
    frames_per_second: float
    workers: int  # 0 = run in this process (an explicit process count was given)
    mode: str  # "standard" | "saved" | "auto" | "manual" | "fixed" | "explicit" | "none" (nothing to scan)
    busy_seconds: dict[str, float] = field(default_factory=dict)  # read / prepare / reduce, summed over the workers
    warnings: tuple[str, ...] = ()
    cubes_current: int = 0  # cubes left alone because every cell in them was already current
    store_path: Path | None = None
    file_mb: float = 0.0
    rois_scanned: int = 0  # different ROIs computed in this run (``rois`` when a whole cube was); 0 = nothing was scanned
    partial_frames: int = 0  # frames read as chunks around the ROIs (OME-Zarr, background off, no rotation) instead of as whole planes
    index_seconds: float = 0.0  # busy time, summed over the workers, reading and checking OME-Zarr shard indexes (part of the read time)
    crc_seconds: float = 0.0  # of which the CRC32C: KNOWN BOTTLENECK of scanning an OME-Zarr, see dataset/io.py
    warm_pool: bool = False  # the run used worker processes that were already running (performance mode)
    first_cube_seconds: float = 0.0  # from the start of the workers' job to the first finished cube: the cost of starting the workers


@dataclass
class _Outcome:
    plan: ScanPlan
    arrays: ScanArrays
    summary: ScanSummary


@dataclass(frozen=True)
class _Refused:
    message: str


class ScanTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "analysis-scan"
    crash_log = "The analysis scan crashed"
    stopped_message = "stopped: the dataset or the session changed"

    pool_state_changed = pyqtSignal(str, int)  # "off" | "starting" | "ready" | "failed", workers (performance mode's pool; any thread may emit)

    def __init__(self, on_result: Callable[[ScanPlan, ScanArrays, ScanSummary], None] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_result_callback = on_result
        self._mode = MODE_NORMAL
        self._pending_mode: str | None = None  # a mode chosen while a scan was running: applied when it has ended
        self._warm: WarmPool | None = None
        self._pool_starting = False
        self._pool_generation = 0  # bumped when the pool is switched off, so a start that is still going ends itself

    # -- performance mode (scan_pool.py) -----------------------------------------------------------------------------------------

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, *, start_pool: bool = True) -> None:
        """Choose ``normal`` or ``performance`` (see ``scan_pool``). Performance starts the worker processes on a thread of its own (the
        GUI does not wait); ``start_pool=False`` only records the mode, for a remembered one at launch, and ``start_pool()`` is called when the
        application has settled. Chosen while a scan runs: applied when it has ended (the scan keeps the workers it started with)."""
        mode = mode if mode in MODES else MODE_NORMAL
        if self._running:
            self._pending_mode = mode if mode != self._mode else None
            return
        self._pending_mode = None
        if mode == self._mode:
            return
        self._mode = mode
        if uses_warm_pool(mode):
            self._warm = WarmPool()
            if start_pool:
                self.start_pool()
        else:
            self._stop_pool()

    def start_pool(self) -> None:
        """Start the warm pool's workers now if the mode wants them and they are not running (idempotent; GUI thread)."""
        warm = self._warm
        if warm is None or not uses_warm_pool(self._mode) or self._pool_starting or warm.is_ready():
            return
        self._pool_starting = True
        generation = self._pool_generation

        def work() -> None:
            try:
                run = get_runtime().begin("analysis.scan", total_units=None, per_worker_bytes=_SCAN_WORKER_BYTES)
                size, low_priority = run.maximum, run.low_priority
                run.finish()
                self.pool_state_changed.emit("starting", size)
                started = time.perf_counter()
                pids = warm.start(size, low_priority)
                if generation != self._pool_generation:  # switched off while the workers were starting
                    warm.shutdown()
                    return
                log_event(logger, "analysis.pool_ready", logging.INFO, workers=len(pids), seconds=round(time.perf_counter() - started, 1))
                self.pool_state_changed.emit("ready", len(pids))
            except Exception:  # noqa: BLE001 - performance mode is an extra: a pool that cannot start leaves the scans as they were
                logger.warning("the warm worker pool could not be started; scans start their own workers", exc_info=True)
                self.pool_state_changed.emit("failed", 0)
            finally:
                self._pool_starting = False

        threading.Thread(target=work, name="analysis-warm-pool", daemon=True).start()

    def _stop_pool(self) -> None:
        warm, self._warm = self._warm, None
        self._pool_generation += 1
        self.pool_state_changed.emit("off", 0)
        if warm is not None:  # closing waits for the idle workers to end: not on the GUI thread
            threading.Thread(target=warm.shutdown, name="analysis-warm-pool-close", daemon=True).start()

    def shutdown(self) -> None:
        super().shutdown()
        warm, self._warm = self._warm, None
        if warm is not None:
            warm.shutdown()

    def _after_run(self) -> None:
        if self._pending_mode is not None:
            pending, self._pending_mode = self._pending_mode, None
            self.set_mode(pending)

    def start(
        self,
        source: ScanSource,
        *,
        cubes: Sequence[int] | None = None,
        store_path: Path | None = None,
        only_stale: bool = True,
        dataset_label: str = "",
        methods: tuple[str, ...] = ("mean",),
        exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE,
        trimmed_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
        processes: int | None = None,
    ) -> None:
        """Begin a scan of ``cubes`` (default: all). Call on the GUI thread. ``store_path``: where ``scan.h5`` lives (None: keep the numbers in
        memory only); ``only_stale``: skip cubes whose stored cells are current. ``processes=None`` (the app) lets ``lspr_core.perf`` choose;
        a number runs exactly that many worker processes (tests: 1 = in the task thread)."""
        if self._running:
            raise RuntimeError("An analysis scan is already running.")
        try:
            snapshot = snapshot_source(source, cubes)  # GUI thread: the only place the modules are read
        except ValueError as exc:
            self.failed.emit(str(exc))
            return
        self._begin_run()
        warm = self._warm if uses_warm_pool(self._mode) else None
        self._start_thread(
            lambda: self._compute(snapshot, methods, exclusion_mode, trimmed_fraction, processes, store_path, only_stale, dataset_label, warm)
        )

    # -- the task thread -----------------------------------------------------------------------------------------------------

    def _compute(self, snapshot: ScanSource, methods, exclusion_mode, trimmed_fraction, processes, store_path, only_stale, dataset_label, warm=None) -> object:
        started = time.perf_counter()
        self._emit_progress(0.0, "Preparing the frames")
        try:
            with timed(logger, "analysis.scan_prepared", level=logging.INFO) as t:
                # Two steps: a plan without the ignore masks says (by hash) which cubes are stale; only those cubes get their masks built
                # (decode, rotate, chromatic warp: ~3 s and 400 MB for all 411 masks of the test dataset, none of it needed for a current cube).
                plan = build_scan_plan(
                    snapshot, methods=methods, exclusion_mode=exclusion_mode, trimmed_fraction=trimmed_fraction, resolve_masks=False,
                )
                hashes = cell_hashes(plan)
                store = ScanStore(store_path) if store_path is not None else None
                positions = list(range(len(plan.jobs)))
                stale = None  # bool [cube, roi] of the stale cells; None = every cell is to be computed
                restamped = 0  # cells given a new stamp without being scanned (their inputs are unchanged, see below)
                if store is not None:
                    stored = store.prepare(plan, dataset_label=dataset_label)
                    if only_stale:
                        stale = stale_cell_matrix(stored, hashes)
                        if stale.any():
                            # Cells stamped with the previous hash formula (the whole ignore mask in every cell) whose inputs have not changed are
                            # current: give them the new stamp and leave their numbers alone (a cube with masks would otherwise be scanned again).
                            old_stamp = stamped_by_previous_formula(stored, hashes, plan)
                            if old_stamp.any():
                                restamped = store.restamp(plan, old_stamp, hashes)
                                stale &= ~old_stamp
                        positions = [int(p) for p in np.flatnonzero(stale.any(axis=1))]
                run_plan = None
                if positions:
                    run_plan = build_scan_plan(
                        snapshot, cubes=[plan.cube_indices[p] for p in positions], methods=methods, exclusion_mode=exclusion_mode,
                        trimmed_fraction=trimmed_fraction,
                    )
                    if stale is not None:
                        run_plan = _with_roi_subsets(run_plan, [stale[p] for p in positions])
                t.set(
                    cubes=len(plan.jobs), stale_cubes=len(positions), frames=plan.n_frames,
                    ignore_mask_mb=round((run_plan.ignore_mask_bytes if run_plan is not None else 0) / 1e6, 1),
                    stale_cells=int(stale.sum()) if stale is not None else len(plan.jobs) * len(plan.roi_ids), restamped_cells=restamped,
                )
        except ValueError as exc:  # nothing to scan / inconsistent ROI ids: the user's setup, not a crash
            return _Refused(str(exc))
        settings = plan.settings
        current = len(plan.jobs) - len(positions)
        if not positions:
            log_event(logger, "analysis.scan_current", logging.INFO, cubes=len(plan.jobs), seconds=round(time.perf_counter() - started, 1))
            run_plan = subset_plan(plan, [])
            summary = ScanSummary(
                cubes=0, frames=0, rois=len(plan.roi_ids), wavelengths=len(plan.wavelength_axis_nm), seconds=time.perf_counter() - started,
                frames_per_second=0.0, workers=0, mode="none", warnings=plan.warnings, cubes_current=current, store_path=store_path,
                file_mb=_file_mb(store_path),
            )
            return _Outcome(run_plan, new_arrays(run_plan), summary)

        run_hashes = hashes[positions]  # the same hashes the resolved plan would give: its frames carry the same ignore keys
        rois_scanned = _rois_scanned(run_plan)
        log_event(
            logger, "analysis.scan_start", logging.INFO, cubes=len(run_plan.jobs), cubes_current=current, frames=run_plan.n_frames,
            rois=len(plan.roi_ids), rois_scanned=rois_scanned, wavelengths=len(plan.wavelength_axis_nm), methods=",".join(settings.methods),
            background=settings.background.flatten_background_enabled, exclusion=settings.exclusion_mode,
            processes=processes if processes is not None else "auto", warnings=len(run_plan.warnings), store=store is not None,
        )

        arrays = new_arrays(run_plan)
        busy = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
        partial_frames = 0
        index_seconds = crc_seconds = 0.0
        reports: list[RunReport | None] = []
        report_progress = throttled(self._emit_progress)

        def scan_progress(fraction: float, message: str) -> None:
            report_progress(PREPARE_SHARE + (1.0 - PREPARE_SHARE) * fraction, message)

        self._emit_progress(PREPARE_SHARE, "Starting the worker processes")  # a few seconds before the first cube is back
        scan_started = time.perf_counter()
        # Finished cubes are written by a thread of their own: this thread only hands them over, so the pool is topped up without waiting for the disk.
        writer = ScanWriter(store, run_plan, arrays) if store is not None else None
        first_cube_seconds = 0.0
        try:
            for result in scan_cubes(
                run_plan.jobs, settings, processes=processes, progress=scan_progress, cancelled=self._cancel_event.is_set,
                on_finished=reports.append, warm=warm,
            ):
                if not first_cube_seconds:
                    first_cube_seconds = time.perf_counter() - scan_started
                place(arrays, run_plan, result)
                partial_frames += result.partial_frames
                index_seconds += result.index_seconds
                crc_seconds += result.crc_seconds
                for stage, seconds in result.seconds.items():
                    busy[stage] = busy.get(stage, 0.0) + seconds
                if writer is not None:
                    writer.submit(result.cube_pos, result.roi_subset, run_hashes[result.cube_pos])
                log_event(
                    logger, "analysis.scan_cube", logging.DEBUG, cube=run_plan.cube_indices[result.cube_pos], frames=len(run_plan.jobs[result.cube_pos].frames),
                    read_s=round(result.seconds.get("read", 0.0), 3), prepare_s=round(result.seconds.get("prepare", 0.0), 3),
                    reduce_s=round(result.seconds.get("reduce", 0.0), 3), partial_frames=result.partial_frames,
                    index_s=round(result.index_seconds, 3), crc_s=round(result.crc_seconds, 3),
                )
            wait_started = time.perf_counter()
            if writer is not None:
                writer.finish()  # everything handed over is on disk before the run counts as done
            write_wait_seconds = time.perf_counter() - wait_started
            if warm is not None:
                warm.trim()  # the workers keep their imports, not the caches of this scan
        except BaseException as exc:
            if warm is not None:
                warm.trim()
            if writer is not None and exc is not writer.error:
                try:
                    writer.finish()  # a Cancel (or any other stop) keeps every cube that was finished
                except Exception:  # noqa: BLE001 - the original exception is the one to report
                    logger.warning("could not write every finished cube to the scan file", exc_info=True)
            if isinstance(exc, Cancelled):
                log_event(
                    logger, "analysis.scan_cancelled", logging.INFO, cubes_done=arrays.n_done, cubes=len(run_plan.jobs),
                    seconds=round(time.perf_counter() - started, 1), saved=store is not None,
                )
            raise
        wall = time.perf_counter() - scan_started
        write_seconds = writer.busy_seconds if writer is not None else 0.0
        report = reports[-1] if reports else None
        frames = run_plan.n_frames
        summary = ScanSummary(
            cubes=len(run_plan.jobs), frames=frames, rois=len(plan.roi_ids), wavelengths=len(plan.wavelength_axis_nm),
            seconds=time.perf_counter() - started, frames_per_second=frames / wall if wall > 0 else 0.0,
            workers=report.workers if report is not None else (processes if processes and processes > 1 else 0),
            mode=report.mode if report is not None else "explicit", busy_seconds=busy, warnings=run_plan.warnings, cubes_current=current,
            store_path=store_path, file_mb=_file_mb(store_path), rois_scanned=rois_scanned, partial_frames=partial_frames,
            index_seconds=index_seconds, crc_seconds=crc_seconds, warm_pool=warm is not None, first_cube_seconds=first_cube_seconds,
        )
        fields = report.as_fields() if report is not None else {}
        log_event(
            logger, "analysis.scan_done", logging.INFO, cubes=summary.cubes, cubes_current=current, frames=frames, rois=summary.rois,
            rois_scanned=rois_scanned, partial_frames=partial_frames, busy_index_s=round(index_seconds, 2), busy_crc_s=round(crc_seconds, 2),
            warm_pool=warm is not None, first_cube_s=round(first_cube_seconds, 2),
            seconds=round(summary.seconds, 1), frames_per_s=round(summary.frames_per_second, 1), busy_read_s=round(busy["read"], 1),
            busy_prepare_s=round(busy["prepare"], 1), busy_reduce_s=round(busy["reduce"], 1), write_s=round(write_seconds, 1),
            write_wait_s=round(write_wait_seconds, 2), write_batches=writer.batches if writer is not None else 0,
            file_mb=round(summary.file_mb, 1), warnings=len(run_plan.warnings), **{f"perf_{k}": v for k, v in fields.items()},
        )
        return _Outcome(run_plan, arrays, summary)

    def _failure_message(self, error: BaseException) -> str:
        if isinstance(error, OSError):
            return f"Could not save the scan file: {error}"
        return super()._failure_message(error)

    # -- back on the GUI thread ----------------------------------------------------------------------------------------------

    def _on_result(self, result: object) -> tuple[str, object]:
        if isinstance(result, _Refused):
            raise TaskRefused(result.message)
        assert isinstance(result, _Outcome)
        if self._on_result_callback is not None:
            self._on_result_callback(result.plan, result.arrays, result.summary)
        s = result.summary
        if s.cubes == 0:
            return f"Scan is up to date: all {s.cubes_current} cubes are current in {s.store_path.name if s.store_path else 'the file'}", s
        how = f"{s.workers} workers ({s.mode})" if s.workers else "1 process"
        if s.warm_pool:
            how += f", performance mode: first cube after {s.first_cube_seconds:.1f} s"
        rois = f"{s.rois}" if s.rois_scanned in (0, s.rois) else f"{s.rois_scanned} of {s.rois}"  # "1 of 160 ROIs": a Fast scan after one ROI moved
        text = f"{rois} ROIs x {s.cubes} cubes x {s.wavelengths} wavelengths scanned in {s.seconds:.0f} s ({s.frames_per_second:.0f} frames/s, {how})"
        if s.cubes_current:
            text += f"; {s.cubes_current} cubes were already current"
        if s.store_path is not None:
            text += f"; saved to {s.store_path.name} ({s.file_mb:.0f} MB)"
        if s.warnings:
            text += f"; warning: {s.warnings[0]}"
        return text, s


def _with_roi_subsets(plan: ScanPlan, stale_rows: Sequence[np.ndarray]) -> ScanPlan:
    """``plan`` with, for every cube whose stale ROIs are at most ``SUBSET_MAX_SHARE`` of its ROIs, the job limited to those ROIs
    (``stale_rows[p]`` is the bool row of cube ``p`` of the plan); other cubes are computed whole."""
    jobs = []
    for job, row in zip(plan.jobs, stale_rows):
        stale_rois = np.flatnonzero(row)
        small = 0 < stale_rois.size <= SUBSET_MAX_SHARE * len(row)
        jobs.append(replace(job, roi_subset=tuple(int(i) for i in stale_rois)) if small else job)
    return replace(plan, jobs=tuple(jobs))


def _rois_scanned(plan: ScanPlan) -> int:
    """How many different ROIs a run of ``plan`` computes (all of them when any cube is computed whole)."""
    if any(job.roi_subset is None for job in plan.jobs):
        return len(plan.roi_ids)
    return len({i for job in plan.jobs for i in job.roi_subset})


def _file_mb(path: Path | None) -> float:
    try:
        return path.stat().st_size / 1e6 if path is not None else 0.0
    except OSError:
        return 0.0
