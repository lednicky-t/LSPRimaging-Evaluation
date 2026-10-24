"""Runs analysis step 1 (the scan) off the GUI thread, saves it to ``scan.h5`` and reports it to the status bar (2026-10-23).

The pure parts are ``scan_jobs`` (inputs -> plan), ``scan`` (plan -> numbers, worker processes), ``scan_fingerprint`` (which stored cells are
still current), ``scan_store`` (the file) and ``scan_arrays`` (the arrays of one run); this is only the thread / signal / Cancel wrapper the rule
in CLAUDE.md asks for. The thread, signals, Cancel and the stale-result rule are in :class:`~..threaded_task.ThreadedTask`.

**Split between the two threads** (design record: ``docs/analysis_redesign_plan_2026-10-19.md`` sections 15-18):

* GUI thread, in ``start()``: ``snapshot_source`` reads everything the scan needs from the app into plain tables (a few lookups per
  frame, ROIs copied, ignore masks still compressed). Afterwards the task touches no module, so an edit made during the run cannot change it.
* Task thread: decode the ignore masks and build the plan (``build_scan_plan``), hash every cell, open the file, then ``scan_cubes`` in worker
  *processes* (the thread is a plain ``threading.Thread``, never ``QThreadPool``: the workers read the dataset, see CLAUDE.md). Each finished cube
  is written to the file at once, in the crash-safe order of ``scan_store``, so a cancelled run keeps every cube it finished.

**What is scanned.** With a ``store_path`` and ``only_stale`` (the default) a cube is scanned only if the file holds no current result for
one of its cells (hash differs or never computed); a run on an unchanged session finishes in a few seconds with "up to date". The unit of work
is the cube: the frame stage dominates the cost, so rescanning a cube for one stale ROI costs almost the same as for all of them.

**Progress** is split into stages weighted by measured time: preparing the plan (mask decoding, hashing) is ~2-3 s of a ~3 min run, so it gets the
first 2 % of the bar. **Logging**: ``analysis.scan_prepared`` (plan build time), ``analysis.scan_start``, one DEBUG ``analysis.scan_cube`` per
cube (busy seconds per stage), and ``analysis.scan_done`` / ``analysis.scan_current`` / ``analysis.scan_cancelled`` with wall time, frames per
second, the worker count and mode from ``lspr_core.perf``, the busy time per stage and the time spent writing. The worker processes have no
logging of their own: their timings come back in ``CubeResult.seconds``.

The arrays of the run are also handed to ``on_result`` on the GUI thread (tests; later panels). A cancelled or stale run installs nothing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lspr_core.perf import RunReport
from PyQt6.QtCore import QObject

from ..applog import log_event, timed
from ..progress import Cancelled, throttled
from ..threaded_task import TaskRefused, ThreadedTask
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from .scan import scan_cubes
from .scan_arrays import ScanArrays, new_arrays, place
from .scan_fingerprint import cell_hashes
from .scan_jobs import ScanPlan, ScanSource, build_scan_plan, snapshot_source, subset_plan
from .scan_store import ScanStore, stale_cube_positions

TASK_ID = "analysis_scan"
TASK_LABEL = "Analysis scan"

PREPARE_SHARE = 0.02  # first part of the bar: decoding the masks, building and hashing the plan (~2-3 s of ~3 min measured)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanSummary:
    """What a finished scan reports (status bar text, log, tests)."""

    cubes: int  # cubes scanned in this run
    frames: int
    rois: int
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

    def __init__(self, on_result: Callable[[ScanPlan, ScanArrays, ScanSummary], None] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_result_callback = on_result

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
        self._start_thread(
            lambda: self._compute(snapshot, methods, exclusion_mode, trimmed_fraction, processes, store_path, only_stale, dataset_label)
        )

    # -- the task thread -----------------------------------------------------------------------------------------------------

    def _compute(self, snapshot: ScanSource, methods, exclusion_mode, trimmed_fraction, processes, store_path, only_stale, dataset_label) -> object:
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
                if store is not None:
                    stored = store.prepare(plan, dataset_label=dataset_label)
                    if only_stale:
                        positions = stale_cube_positions(stored, hashes)
                run_plan = None
                if positions:
                    run_plan = build_scan_plan(
                        snapshot, cubes=[plan.cube_indices[p] for p in positions], methods=methods, exclusion_mode=exclusion_mode,
                        trimmed_fraction=trimmed_fraction,
                    )
                t.set(
                    cubes=len(plan.jobs), stale_cubes=len(positions), frames=plan.n_frames,
                    ignore_mask_mb=round((run_plan.ignore_mask_bytes if run_plan is not None else 0) / 1e6, 1),
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
        log_event(
            logger, "analysis.scan_start", logging.INFO, cubes=len(run_plan.jobs), cubes_current=current, frames=run_plan.n_frames,
            rois=len(plan.roi_ids), wavelengths=len(plan.wavelength_axis_nm), methods=",".join(settings.methods),
            background=settings.background.flatten_background_enabled, exclusion=settings.exclusion_mode,
            processes=processes if processes is not None else "auto", warnings=len(run_plan.warnings), store=store is not None,
        )

        arrays = new_arrays(run_plan)
        busy = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
        write_seconds = 0.0
        reports: list[RunReport | None] = []
        report_progress = throttled(self._emit_progress)

        def scan_progress(fraction: float, message: str) -> None:
            report_progress(PREPARE_SHARE + (1.0 - PREPARE_SHARE) * fraction, message)

        self._emit_progress(PREPARE_SHARE, "Starting the worker processes")  # a few seconds before the first cube is back
        scan_started = time.perf_counter()
        try:
            for result in scan_cubes(
                run_plan.jobs, settings, processes=processes, progress=scan_progress, cancelled=self._cancel_event.is_set,
                on_finished=reports.append,
            ):
                place(arrays, run_plan, result)
                for stage, seconds in result.seconds.items():
                    busy[stage] = busy.get(stage, 0.0) + seconds
                if store is not None:
                    t_write = time.perf_counter()
                    store.write_cube(run_plan, arrays, result.cube_pos, run_hashes[result.cube_pos])
                    write_seconds += time.perf_counter() - t_write
                log_event(
                    logger, "analysis.scan_cube", logging.DEBUG, cube=run_plan.cube_indices[result.cube_pos], frames=len(run_plan.jobs[result.cube_pos].frames),
                    read_s=round(result.seconds.get("read", 0.0), 3), prepare_s=round(result.seconds.get("prepare", 0.0), 3),
                    reduce_s=round(result.seconds.get("reduce", 0.0), 3),
                )
        except Cancelled:
            log_event(
                logger, "analysis.scan_cancelled", logging.INFO, cubes_done=arrays.n_done, cubes=len(run_plan.jobs),
                seconds=round(time.perf_counter() - started, 1), saved=store is not None,
            )
            raise
        wall = time.perf_counter() - scan_started
        report = reports[-1] if reports else None
        frames = run_plan.n_frames
        summary = ScanSummary(
            cubes=len(run_plan.jobs), frames=frames, rois=len(plan.roi_ids), wavelengths=len(plan.wavelength_axis_nm),
            seconds=time.perf_counter() - started, frames_per_second=frames / wall if wall > 0 else 0.0,
            workers=report.workers if report is not None else (processes if processes and processes > 1 else 0),
            mode=report.mode if report is not None else "explicit", busy_seconds=busy, warnings=run_plan.warnings, cubes_current=current,
            store_path=store_path, file_mb=_file_mb(store_path),
        )
        fields = report.as_fields() if report is not None else {}
        log_event(
            logger, "analysis.scan_done", logging.INFO, cubes=summary.cubes, cubes_current=current, frames=frames, rois=summary.rois,
            seconds=round(summary.seconds, 1), frames_per_s=round(summary.frames_per_second, 1), busy_read_s=round(busy["read"], 1),
            busy_prepare_s=round(busy["prepare"], 1), busy_reduce_s=round(busy["reduce"], 1), write_s=round(write_seconds, 1),
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
        text = f"{s.rois} ROIs x {s.cubes} cubes x {s.wavelengths} wavelengths scanned in {s.seconds:.0f} s ({s.frames_per_second:.0f} frames/s, {how})"
        if s.cubes_current:
            text += f"; {s.cubes_current} cubes were already current"
        if s.store_path is not None:
            text += f"; saved to {s.store_path.name} ({s.file_mb:.0f} MB)"
        if s.warnings:
            text += f"; warning: {s.warnings[0]}"
        return text, s


def _file_mb(path: Path | None) -> float:
    try:
        return path.stat().st_size / 1e6 if path is not None else 0.0
    except OSError:
        return 0.0
