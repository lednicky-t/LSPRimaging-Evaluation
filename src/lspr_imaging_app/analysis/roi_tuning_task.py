"""``RoiTuningTask``: the lattice scan and the metric fits of ROI position tuning, off the GUI thread (2026-10-29, step C3). Qt only for the task shell.

Two jobs, both reported through the app-wide task indicator (``threaded_task.ThreadedTask``): :meth:`start` scans every lattice position (``roi_tuning_run``,
worker processes from ``lspr_core.perf``), fits the Sensorgram's metric on every shifted copy and reduces them to the objectives; :meth:`refit` redoes only the fits
and the objectives of a matrix already in RAM (the user changed the fit, smoothing, ...). **The fits never run in this task's thread:** a fit loop next to the GUI
starves it of the interpreter lock (``docs/sensorgram_fit_freeze_2026-10-24.md``), so they go to a helper process (``trace_pool.TracePool``, kept for the next run,
closed in :meth:`shutdown`). Frames, masks and the matrix are shared with the GUI thread and never modified in place.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject

from ..applog import log_event
from ..threaded_task import TaskRefused, ThreadedTask
from .roi_tuning import Lattice, LatticeObjectives, TuningRegions
from .roi_tuning_accept import SeedReport, seed_scan_file
from .roi_tuning_objectives import Ranking
from .roi_tuning_run import (
    TuningError,
    TuningMatrix,
    compute_traces,
    objectives_from_traces,
    run_lattice_scan,
)
from .scan_jobs import ScanSource, build_scan_plan, snapshot_source
from .settings import MetricSettings, StatisticsSettings
from .trace_pool import TracePool

logger = logging.getLogger(__name__)

TASK_ID = "analysis.tuning"
TASK_LABEL = "ROI position tuning"
SCAN_SHARE = 0.9
"""The scan is most of the work (49 s of ~52 s measured for 20 ROIs at 25 positions, 2026-10-28): the first 90 % of the bar. The fits (5-8 % of the time) and the
objectives fill the rest."""


@dataclass(frozen=True)
class TuningRequest:
    """Everything one tuning run is made from, as plain values (read on the GUI thread; the thread touches no module). ``cubes`` is the union of the two steps'
    cubes, ascending; ``first_unix_ms`` / ``last_unix_ms`` are the acquisition span of each of them in that order."""

    roi_ids: tuple[int, ...]
    lattice: Lattice
    baseline_cubes: tuple[int, ...]
    signal_cubes: tuple[int, ...]
    formula_key: str
    metric: MetricSettings
    statistics: StatisticsSettings
    first_unix_ms: np.ndarray
    last_unix_ms: np.ndarray

    @property
    def cubes(self) -> tuple[int, ...]:
        return tuple(sorted({*self.baseline_cubes, *self.signal_cubes}))

    @property
    def regions(self) -> TuningRegions:
        return TuningRegions((min(self.baseline_cubes), max(self.baseline_cubes)), (min(self.signal_cubes), max(self.signal_cubes)))


@dataclass
class TuningResult:
    """What a run leaves: the matrix (scan-level numbers), the metric traces made from it, and the objectives of those traces, with the request they were made for."""

    request: TuningRequest
    matrix: TuningMatrix
    traces: np.ndarray  # float64 [position, roi, cube]
    objectives: LatticeObjectives
    seconds: float = 0.0


@dataclass(frozen=True)
class _Refused:
    message: str


class RoiTuningTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "roi-tuning"
    crash_log = "The ROI position tuning crashed"
    stopped_message = "stopped: the dataset or the session changed"

    def __init__(
        self,
        on_result: Callable[[TuningResult], None] | None = None,
        parent: QObject | None = None,
        *,
        helper_process: bool = True,
        on_seeded: Callable[[SeedReport], None] | None = None,
    ) -> None:
        """``helper_process=False`` fits in the task's own thread (tests and the command line only: never next to the GUI). ``on_seeded`` receives the report of
        a finished :meth:`seed` on the GUI thread."""
        super().__init__(parent)
        self._on_result_callback = on_result
        self._on_seeded_callback = on_seeded
        self._helper_process = helper_process
        self._pool: TracePool | None = None

    def set_helper_process(self, on: bool) -> None:
        """Fit in a helper process (True, the app) or in the task's own thread (False: tests and the command line only, never next to the GUI)."""
        self._helper_process = bool(on)
        if not on:
            pool, self._pool = self._pool, None
            if pool is not None:
                pool.shutdown()

    def shutdown(self) -> None:
        super().shutdown()
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown()

    # -- GUI thread -----------------------------------------------------------------------------------------------------------

    def start(self, source: ScanSource, request: TuningRequest, *, processes: int | None = None) -> None:
        """Begin a trial. ``processes=None`` (the app) lets ``lspr_core.perf`` choose; a number runs exactly that many (tests: 1 = in the task thread).
        Raises :class:`TuningError` for inputs that cannot be scanned (a cube that is not in the dataset)."""
        if self._running:
            raise RuntimeError("A ROI position trial is already running.")
        try:
            snapshot = snapshot_source(source, request.cubes)  # the only place the modules are read
        except ValueError as exc:
            raise TuningError(str(exc)) from exc
        self._begin_run()
        self._start_thread(lambda: self._scan_and_fit(snapshot, request, processes))

    def refit(self, matrix: TuningMatrix, request: TuningRequest) -> None:
        """Redo the fits and objectives for the matrix in hand under the request's metric, formula and statistics. No scan."""
        if self._running:
            raise RuntimeError("A ROI position trial is already running.")
        self._begin_run()
        self._start_thread(lambda: self._fit(matrix, request, 0.0, time.perf_counter()))

    def seed(
        self,
        matrix: TuningMatrix,
        ranking: Ranking,
        before: ScanSource,
        after: ScanSource,
        store_path: Path,
        dataset_label: str = "",
    ) -> None:
        """Accept's second half, off the GUI thread: copy the best positions' numbers of the moved ROIs into the scan file (``roi_tuning_accept``). ``before`` /
        ``after``: snapshots of the app (``snapshot_source`` over the matrix's cubes) taken before and after the ROIs were moved. Always ends with a
        :class:`~.roi_tuning_accept.SeedReport`, also when it writes nothing."""
        if self._running:
            raise RuntimeError("A ROI position trial is already running.")
        self._begin_run()
        self._start_thread(lambda: self._seed(matrix, ranking, before, after, Path(store_path), dataset_label))

    # -- the task thread ------------------------------------------------------------------------------------------------------

    def _seed(self, matrix: TuningMatrix, ranking: Ranking, before: ScanSource, after: ScanSource, store_path: Path, label: str) -> object:
        self._emit_progress(0.0, "Saving the numbers of the moved ROIs")
        try:
            cubes = matrix.cube_indices
            plan_before = build_scan_plan(before, cubes=cubes, resolve_masks=False)
            plan_after = build_scan_plan(after, cubes=cubes, resolve_masks=False)
        except ValueError as exc:
            return SeedReport(False, f"The numbers were not saved ({exc}); the next scan computes the moved ROIs.")
        return seed_scan_file(store_path, matrix, ranking, plan_before, plan_after, dataset_label=label)

    def _scan_and_fit(self, snapshot: ScanSource, request: TuningRequest, processes: int | None) -> object:
        started = time.perf_counter()
        self._emit_progress(0.0, "Preparing the frames")
        try:
            plan = build_scan_plan(snapshot, cubes=request.cubes)
            ids = list(plan.roi_ids)
            gone = [r for r in request.roi_ids if r not in ids]
            if gone:
                raise TuningError(f"ROI {gone[0]} is no longer in the scan; select the ROIs again.")
            matrix = run_lattice_scan(
                plan, [ids.index(r) for r in request.roi_ids], request.lattice, processes=processes,
                progress=lambda fraction, message: self._emit_progress(SCAN_SHARE * fraction, f"Trying positions: {message}"),
                cancelled=self._cancel_event.is_set,
            )
            return self._fit(matrix, request, SCAN_SHARE, started)
        except TuningError as exc:
            return _Refused(str(exc))

    def _fit(self, matrix: TuningMatrix, request: TuningRequest, done: float, started: float) -> object:
        self._emit_progress(done, "Fitting the metric")
        if self._helper_process and self._pool is None:
            self._pool = TracePool()
        traces = compute_traces(
            matrix, request.formula_key, request.metric, runner=None if self._pool is None else self._pool.run, cancelled=self._cancel_event.is_set,
            progress=lambda finished, total: self._emit_progress(done + (1.0 - done) * 0.9 * finished / max(total, 1), "Fitting the metric"),
        )
        objectives = objectives_from_traces(matrix, traces, request.regions, request.first_unix_ms, request.last_unix_ms, request.statistics)
        seconds = time.perf_counter() - started
        log_event(
            logger, "tuning.fitted", logging.INFO, positions=len(matrix.offsets), rois=len(matrix.roi_ids), cubes=len(matrix.cube_indices),
            seconds=round(seconds, 2), fit_method=request.metric.fit_method, from_scan=done > 0.0,
        )
        return TuningResult(request, matrix, traces, objectives, seconds)

    # -- back on the GUI thread -----------------------------------------------------------------------------------------------

    def _failure_message(self, error: BaseException) -> str:
        return f"The ROI position trial failed: {error}"

    def _on_result(self, result: object) -> tuple[str, object]:
        if isinstance(result, _Refused):
            raise TaskRefused(result.message)
        if isinstance(result, SeedReport):
            if self._on_seeded_callback is not None:
                self._on_seeded_callback(result)
            if result.written:
                return f"Saved the numbers of {len(result.roi_ids)} moved ROIs for {result.cubes} cubes in {result.seconds:.1f} s.", result
            return result.reason, result
        assert isinstance(result, TuningResult)
        if self._on_result_callback is not None:
            self._on_result_callback(result)
        matrix = result.matrix
        return (
            f"Tried {len(matrix.offsets)} positions of {len(matrix.roi_ids)} ROIs over {len(matrix.cube_indices)} cubes in {result.seconds:.0f} s.",
            result,
        )


def cubes_text(cubes: Sequence[int]) -> str:
    """'2-32' for a run of cubes, else '2, 5, 9': for sentences about a step."""
    cubes = sorted(cubes)
    if not cubes:
        return "no cubes"
    return f"{cubes[0]}-{cubes[-1]}" if cubes[-1] - cubes[0] + 1 == len(cubes) and len(cubes) > 1 else ", ".join(str(c) for c in cubes)
