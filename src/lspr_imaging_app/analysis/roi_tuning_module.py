"""``RoiTuningModule``: the one owner of the state of ROI position tuning (2026-10-29, step C3). The ribbon tab, the Image overlay and (later) Accept talk to it,
never to each other.

What it holds, all in RAM (decided 2026-10-28: nothing is written beside the session; the ribbon's Export icon writes a table on request):

* the **trial settings**: the lattice (size, pitch) and the objective to rank by, plus two display switches (crosses, and Original / Best positions);
* the **result** of the last trial (:class:`~.roi_tuning_task.TuningResult`: scan-level numbers of every position, the metric traces, the objectives) and its
  **ranking** (:func:`~.roi_tuning_objectives.rank`) under the current objective. Switching the objective only re-ranks: it never scans or fits.

A trial takes its stretches of the experiment from the experimental plan (``plan.baseline_step()`` / ``plan.signal_step()``, the B1 / S1 steps; a range is a step,
decided 2026-10-29) and its ROIs from the current selection. States: ``idle`` (no result), ``running``, ``ready``, ``stale`` (a result exists but something it was made
from changed: its crosses are drawn dimmed and Accept is refused; the owner calls :meth:`mark_stale` from the signals that can alter a scan number, the same list the
scan results module watches). A change of the fit method, formula or smoothing settings re-fits the result in the background (:meth:`refit`); it does not make it
stale. Closing the dataset or switching session drops everything (:meth:`invalidate`).

Signals: ``state_changed`` (running / stale / message / availability: the tab's enabling and text), ``matrix_changed`` (a different result or ranking: the overlay and the
tab redraw), ``view_changed`` (the crosses / Original-Best switches). Messages are plain sentences for the status line.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..applog import log_event
from .roi_tuning import Lattice, moved_centre
from .roi_tuning_accept import SeedReport, accepted_moves
from .roi_tuning_export import table_rows, write_csv
from .roi_tuning_objectives import DEFAULT_OBJECTIVE_KEY, OBJECTIVES, Ranking, objective, rank
from .roi_tuning_run import TuningError
from .roi_tuning_task import RoiTuningTask, TuningRequest, TuningResult, cubes_text
from .scan_jobs import ScanSource, snapshot_source
from .settings import AnalysisSettingsChange

logger = logging.getLogger(__name__)

IDLE, RUNNING, READY, STALE = "idle", "running", "ready", "stale"
DEFAULT_LATTICE_SIZE, DEFAULT_PITCH_PX = 5, 1.0  # the maintainer's normal lattice (2026-10-28): 5 x 5 at 1 px, no big searches
LATTICE_SIZES = (3, 5, 7, 9)


@dataclass(frozen=True)
class TuningView:
    """What the Image overlay needs to draw the trial, as plain arrays (shared, never modified): the module's answer to ``view()``. ``revision`` changes whenever
    anything here does (a different result or ranking, the switches), so a drawing can tell cheaply whether it is up to date. Positions are processed-image
    pixels; ``scores`` are ``[position, roi]`` (0 worst .. 1 best in each ROI's lattice, NaN = no value); ``best_offsets`` are zero for a ROI that does not move."""

    revision: int
    roi_ids: tuple[int, ...]
    roi_xy: np.ndarray  # [roi, 2] the centres the trial started from
    offsets: np.ndarray  # [position, 2]
    scores: np.ndarray  # [position, roi]
    best: np.ndarray  # int [roi]: lattice row of the best position (the centre if none is better, -1 if the ROI could not be ranked)
    best_offsets: np.ndarray  # [roi, 2]
    centre: int
    stale: bool
    show_crosses: bool
    show_best: bool

    @property
    def moves_any(self) -> bool:
        return bool(np.any(self.best_offsets != 0.0))


@dataclass(frozen=True)
class _Problem:
    """Why a trial cannot start now (a plain sentence)."""

    message: str


class RoiTuningModule(QObject):
    state_changed = pyqtSignal()
    matrix_changed = pyqtSignal()
    view_changed = pyqtSignal()
    export_finished = pyqtSignal(str, str)  # path, error text ("" = written)
    seeded = pyqtSignal(object)  # a SeedReport: Accept's numbers were written into the scan file (or why not); the owner reloads the scan results
    _export_done = pyqtSignal(str, str)  # from the writer thread

    def __init__(
        self,
        source_factory: Callable[[], ScanSource],
        plan,
        selection,
        settings,
        dataset,
        roi_toolbox,
        parent: QObject | None = None,
        *,
        helper_process: bool = True,
    ) -> None:
        """``plan``: the ``ExperimentPlanModule``; ``selection``: ``selected_roi_ids()``; ``settings``: the ``AnalysisSettingsModule``; ``dataset``:
        ``cube_spans_unix_ms(cubes)``; ``roi_toolbox``: ``detection_settings()``. All are read on the GUI thread, through their public queries only."""
        super().__init__(parent)
        self._source_factory = source_factory
        self._plan = plan
        self._selection = selection
        self._settings = settings
        self._dataset = dataset
        self._roi_toolbox = roi_toolbox
        self._task = RoiTuningTask(self._install, parent=self, helper_process=helper_process, on_seeded=self._on_seeded)
        self._task.running_changed.connect(lambda _running: self.state_changed.emit())
        self._task.task_finished.connect(self._on_task_finished)
        self._export_done.connect(self.export_finished)
        settings.settings_changed.connect(self._on_settings_changed)
        self._lattice = Lattice(DEFAULT_LATTICE_SIZE, DEFAULT_PITCH_PX)
        self._objective_key = DEFAULT_OBJECTIVE_KEY
        self._interpolate = False
        self._show_crosses = True
        self._show_best = False
        self._result: TuningResult | None = None
        self._ranking: Ranking | None = None
        self._stale = False
        self._message = ""
        self._pending_refit = False
        self._revision = 0
        # Connected first, so every other listener of these two sees the new revision when it asks for the view.
        self.matrix_changed.connect(self._bump_revision)
        self.view_changed.connect(self._bump_revision)

    def _bump_revision(self) -> None:
        self._revision += 1

    # -- queries ---------------------------------------------------------------------------------------------------------------

    @property
    def task(self) -> RoiTuningTask:
        return self._task

    def lattice(self) -> Lattice:
        return self._lattice

    def objective_key(self) -> str:
        return self._objective_key

    def interpolate(self) -> bool:
        """True = the best position of a ROI is placed between the lattice points (``roi_tuning_objectives.refine_best``), not only on one."""
        return self._interpolate

    def show_crosses(self) -> bool:
        return self._show_crosses

    def show_best(self) -> bool:
        """True = the ROIs are drawn at their best positions, False = where they are (the crosses stay either way)."""
        return self._show_best

    def state(self) -> str:
        if self._task.is_running():
            return RUNNING
        if self._result is None:
            return IDLE
        return STALE if self._stale else READY

    def message(self) -> str:
        return self._message

    def result(self) -> TuningResult | None:
        return self._result

    def ranking(self) -> Ranking | None:
        return self._ranking

    def proposed_centres(self) -> dict[int, tuple[float, float]]:
        """ROI id -> (x, y) processed px of each tuned ROI at its best position (its own centre where it would not move); empty without a result."""
        if self._result is None or self._ranking is None:
            return {}
        matrix = self._result.matrix
        return {
            int(roi_id): moved_centre(float(x), float(y), float(dx), float(dy))
            for roi_id, (x, y), (dx, dy) in zip(matrix.roi_ids, matrix.roi_xy, self._ranking.best_offsets, strict=True)
        }

    def can_accept(self) -> bool:
        """The result is current, no trial is running, and at least one ROI would move."""
        return self.state() == READY and self.n_moving() > 0

    def view(self) -> TuningView | None:
        """The drawing data of the trial in hand (None without one). Cheap: the arrays are those of the result and the ranking."""
        if self._result is None or self._ranking is None:
            return None
        return TuningView(
            self._revision, self._result.matrix.roi_ids, self._result.matrix.roi_xy, self._ranking.offsets, self._ranking.scores, self._ranking.best,
            self._ranking.best_offsets, self._ranking.centre, self._stale, self._show_crosses, self._show_best,
        )

    def n_moving(self) -> int:
        return 0 if self._ranking is None else int(self._ranking.moves.sum())

    def can_run(self) -> bool:
        return not self._task.is_running() and not isinstance(self._prepare(), _Problem)

    def preview(self) -> str:
        """The sentence the tab shows before a run: what would be tried, or why nothing can be."""
        prepared = self._prepare()
        if isinstance(prepared, _Problem):
            return prepared.message
        request = prepared
        return (
            f"{request.lattice.n_points} positions x {len(request.roi_ids)} ROIs x {len(request.cubes)} cubes "
            f"(baseline {cubes_text(request.baseline_cubes)}, signal {cubes_text(request.signal_cubes)})."
        )

    def objectives(self) -> tuple[tuple[str, str, str], ...]:
        """``(key, label, tooltip)`` of every entry of the "Rank by" menu."""
        return tuple((o.key, o.label, o.tooltip) for o in OBJECTIVES)

    # -- commands: settings ----------------------------------------------------------------------------------------------------

    def set_lattice(self, size: int, pitch_px: float) -> bool:
        """Choose the lattice of the next trial. False (and a message) for a value that makes no lattice. The result in hand keeps the lattice it was made with."""
        try:
            lattice = Lattice(int(size), float(pitch_px))
        except (TypeError, ValueError) as exc:
            self._say(str(exc))
            return False
        if lattice == self._lattice:
            return True
        self._lattice = lattice
        self.state_changed.emit()
        return True

    def set_objective(self, key: str) -> bool:
        """Rank by ``key`` (see :meth:`objectives`). Re-ranks the result in hand; nothing is scanned or fitted."""
        try:
            objective(key)
        except KeyError as exc:
            self._say(str(exc.args[0]))
            return False
        if key == self._objective_key:
            return True
        self._objective_key = key
        self._rerank()
        return True

    def set_interpolate(self, on: bool) -> None:
        """Place the best position between the lattice points (on) or on a lattice point (off, the default: a measured position). Re-ranks the result in hand; nothing
        is scanned or fitted. An interpolated position was never scanned, so Accept moves the ROI there but cannot copy its numbers into the scan file."""
        if bool(on) == self._interpolate:
            return
        self._interpolate = bool(on)
        self._rerank()

    def _rerank(self) -> None:
        if self._result is not None:
            self._ranking = rank(self._result.objectives, self._objective_key, interpolate=self._interpolate)
            if not self._stale:  # an out-of-date result keeps saying so
                self._message = self._result_message(self._result, self._ranking)
            self.matrix_changed.emit()
        self.state_changed.emit()

    def set_show_crosses(self, on: bool) -> None:
        if bool(on) != self._show_crosses:
            self._show_crosses = bool(on)
            self.view_changed.emit()

    def set_show_best(self, on: bool) -> None:
        if bool(on) != self._show_best:
            self._show_best = bool(on)
            self.view_changed.emit()

    # -- commands: the trial ---------------------------------------------------------------------------------------------------

    def run(self, *, processes: int | None = None) -> bool:
        """Start a trial for the selected ROIs over the plan's baseline and signal steps. False (with a message) when it cannot start."""
        if self._task.is_running():
            self._say("A trial is already running.")
            return False
        prepared = self._prepare()
        if isinstance(prepared, _Problem):
            self._say(prepared.message)
            return False
        try:
            source = self._source_factory()
        except RuntimeError as exc:
            self._say(str(exc))
            return False
        try:
            self._task.start(source, prepared, processes=processes)
        except TuningError as exc:
            self._say(str(exc))
            return False
        self._message = "Trying positions..."
        self.state_changed.emit()
        return True

    def cancel(self) -> None:
        self._task.cancel()

    def clear(self) -> None:
        """Drop the result and the crosses (a running trial is stopped)."""
        if self._task.is_running():
            self._task.invalidate()
        self._drop("")

    def invalidate(self) -> None:
        """The dataset or the session changed: the result belongs to the old one. Stops a running trial and drops everything."""
        self._task.invalidate()
        self._drop("")

    def mark_stale(self) -> None:
        """Something the result was made from may have changed (geometry, mask, chromatic, background, ROIs, frame roles): its crosses no longer say where
        the best positions are. The result is kept so the user still sees what was tried, but it is not accepted."""
        if self._result is None or self._stale:
            return
        self._stale = True
        self._message = "The ROIs or the image settings changed after the trial; run it again."
        self.state_changed.emit()
        self.matrix_changed.emit()

    def refit(self) -> bool:
        """Redo the fits and objectives of the result in hand with the current fit, formula and smoothing settings (background; no scan). False when there is
        nothing to refit; a refit asked while the task is busy runs when it has finished."""
        if self._result is None or self._stale:
            return False
        if self._task.is_running():
            self._pending_refit = True
            return True
        prepared = self._prepare(for_refit=self._result.request)
        if isinstance(prepared, _Problem):
            return False
        self._task.refit(self._result.matrix, prepared)
        return True

    # -- commands: accept ------------------------------------------------------------------------------------------------------

    def accept(self, store_path: Path | None = None, dataset_label: str = "") -> bool:
        """Move the ROIs whose best centre is not their current one (the ROI toolbox, **one undo step**) and, when ``store_path`` is an existing scan file, copy their
        trial numbers into it so that the cubes of the two steps are not scanned again (``roi_tuning_accept``; the file is written in the background and
        ``seeded`` says how it went). The trial is spent either way: the result and the crosses are cleared. False (with a message) when nothing was accepted; in
        that case no ROI moved. The owner must not call this while an analysis scan runs (both write the scan file)."""
        refusal = self._accept_refusal()
        if refusal:
            self._say(refusal)
            return False
        matrix, ranking = self._result.matrix, self._ranking
        moves = accepted_moves(matrix, ranking)
        cubes = matrix.cube_indices
        try:
            before = snapshot_source(self._source_factory(), cubes)  # the app as the trial saw it: taken before anything moves
        except (RuntimeError, ValueError) as exc:
            self._say(str(exc))
            return False
        self._roi_toolbox.move_rois(moves, label="Accept tuned ROI positions")
        text = f"Moved {len(moves)} ROI{'s' if len(moves) != 1 else ''} (Edit > Undo puts them back)."
        seed_from: ScanSource | None = None
        if store_path is not None and Path(store_path).is_file():
            try:
                seed_from = snapshot_source(self._source_factory(), cubes)
            except (RuntimeError, ValueError):
                seed_from = None
        self._drop("")
        if seed_from is None:
            self._say(text + " The scan file was not changed: a Fast scan computes the moved ROIs.")
            return True
        self._say(text + " Saving their numbers to the scan file...")
        self._task.seed(matrix, ranking, before, seed_from, Path(store_path), dataset_label)
        return True

    def _accept_refusal(self) -> str:
        if self._task.is_running():
            return "A trial is running; accept when it has finished."
        if self._result is None or self._ranking is None:
            return "There is nothing to accept: run a trial first."
        if self._stale:
            return "The result is out of date (the ROIs or the image settings changed after the trial); run it again."
        if not self._ranking.moves.any():
            return "No ROI would move: the current centre is already the best."
        if self._roi_toolbox.has_timeline():
            return "The ROIs change over the experiment (a ROI geometry timeline); position tuning does not support that yet."
        matrix = self._result.matrix
        centres = {int(roi.area_roi_id): (float(roi.center_x), float(roi.center_y)) for roi in self._roi_toolbox.rois()}
        for roi_id, (x, y) in zip(matrix.roi_ids, matrix.roi_xy, strict=True):
            if centres.get(int(roi_id)) != (float(x), float(y)):
                self.mark_stale()
                return f"ROI {roi_id} is not where it was when the trial started; run it again."
        return ""

    def _on_seeded(self, report: SeedReport) -> None:
        """GUI thread, from the task: Accept's numbers are in the scan file, or the reason they are not."""
        if report.written:
            n = len(report.roi_ids)
            self._message = (
                f"Moved ROIs saved: the numbers of {n} ROI{'s' if n != 1 else ''} for {report.cubes} cubes are in the scan file. "
                "The other cubes are out of date until the next Fast scan."
            )
            if report.not_saved:
                self._message += (
                    f" ROI {', '.join(str(r) for r in report.not_saved)} also moved, but its numbers were not saved (it is next to another ROI, or its new centre "
                    "lies between lattice points): the next scan computes it."
                )
        else:
            self._message = report.reason
        log_event(logger, "tuning.accepted", logging.INFO, written=report.written, rois=len(report.roi_ids), cubes=report.cubes)
        self.state_changed.emit()
        self.seeded.emit(report)

    # -- commands: export ------------------------------------------------------------------------------------------------------

    def export_csv(self, path: Path) -> bool:
        """Write the numbers behind the crosses to ``path`` as a CSV (one row per ROI and position), on a thread of its own; ``export_finished(path, error)``
        says when. False (with a message) when there is no result."""
        if self._result is None or self._ranking is None:
            self._say("There is nothing to export: run a trial first.")
            return False
        rows = table_rows(self._result.objectives, self._ranking, self._result.matrix.roi_xy)  # GUI thread: reads shared arrays, ~4000 rows

        def write() -> None:
            try:
                write_csv(path, rows)
                self._export_done.emit(str(path), "")
            except OSError as exc:
                self._export_done.emit(str(path), str(exc))

        threading.Thread(target=write, name="roi-tuning-export", daemon=True).start()
        return True

    # -- the task's side -------------------------------------------------------------------------------------------------------

    def _install(self, result: TuningResult) -> None:
        """GUI thread, from the task: a finished trial (or refit) replaces the result."""
        self._result = result
        self._ranking = rank(result.objectives, self._objective_key, interpolate=self._interpolate)
        self._stale = False
        self._message = self._result_message(result, self._ranking)
        total = len(result.matrix.roi_ids)
        far = float(np.max(np.hypot(*self._ranking.best_offsets.T))) if total else 0.0
        log_event(
            logger, "tuning.result", logging.INFO, objective=self._objective_key, rois=total, moving=int(self._ranking.moves.sum()), max_shift_px=round(far, 2),
            interpolated=int(self._ranking.refined.sum()), at_edge=int(self._ranking.edge.sum()), interacting_pairs=len(result.matrix.interacting_pairs),
            matrix_mb=round(result.matrix.nbytes / 1e6, 1),
        )
        self.matrix_changed.emit()
        self.state_changed.emit()

    @staticmethod
    def _result_message(result: TuningResult, ranking: Ranking) -> str:
        """What the status line says about a ranked result: how many ROIs would move and by how far, what could not be ranked, what sits at the lattice edge."""
        moving, total = int(ranking.moves.sum()), len(result.matrix.roi_ids)
        unranked = int((ranking.best < 0).sum())
        far = float(np.max(np.hypot(*ranking.best_offsets.T))) if total else 0.0
        if moving:
            message = f"{moving} of {total} ROIs would move (by up to {far:.1f} px)."
            refined = int(ranking.refined.sum())
            if refined:
                message += f" {refined} placed between lattice points."
        elif unranked == total:
            message = "No ROI could be ranked: the baseline or the signal step gave no usable numbers (is the metric computed for these cubes?)."
        else:
            message = f"No ROI would move: the current centre is the best of the {result.matrix.lattice.n_points} tried."
        if unranked and unranked < total:
            message += f" {unranked} of {total} could not be ranked (no usable numbers)."
        edge = int(ranking.edge.sum())
        if edge:
            message += f" {edge} of {total} have their best centre at the edge of the lattice: the peak may lie further out."
        if result.matrix.interacting_pairs:
            message += f" {len(result.matrix.interacting_pairs)} ROI pairs are close enough to affect each other's reference ring: their numbers are approximate."
        return message

    def _on_task_finished(self, _task_id: str, outcome: str, message: str) -> None:
        if outcome == "failed":
            self._message = message
        elif outcome == "cancelled" and message:
            self._message = message
        elif outcome == "cancelled":
            self._message = "Stopped."
        self.state_changed.emit()
        if self._pending_refit and not self._task.is_running():
            self._pending_refit = False
            self.refit()

    def _on_settings_changed(self, change: AnalysisSettingsChange) -> None:
        if change.field in ("metric", "statistics"):
            self.refit()

    # -- internals -------------------------------------------------------------------------------------------------------------

    def _say(self, message: str) -> None:
        self._message = message
        self.state_changed.emit()

    def _drop(self, message: str) -> None:
        had = self._result is not None
        self._result, self._ranking, self._stale, self._pending_refit = None, None, False, False
        self._message = message
        if had:
            self.matrix_changed.emit()
        self.state_changed.emit()

    def _prepare(self, for_refit: TuningRequest | None = None) -> TuningRequest | _Problem:
        """The request for a trial now (or, for a refit, the old request with today's fit settings), or the reason there can be none."""
        if for_refit is not None:
            return TuningRequest(
                for_refit.roi_ids, for_refit.lattice, for_refit.baseline_cubes, for_refit.signal_cubes, self._roi_toolbox.detection_settings().formula_key,
                self._settings.metric_settings(), self._settings.statistics_settings(), for_refit.first_unix_ms, for_refit.last_unix_ms,
            )
        roi_ids = tuple(sorted(int(r) for r in self._selection.selected_roi_ids()))
        if not roi_ids:
            return _Problem("Select at least one ROI first.")
        baseline, signal = self._plan.baseline_step(), self._plan.signal_step()
        if baseline is None or signal is None:
            return _Problem("Choose a baseline step (B1) and a signal step (S1) on the Experimental plan tab of the Sensorgram first.")
        if baseline.ref == signal.ref:
            return _Problem("The baseline and the signal are the same step; choose two different steps.")
        baseline_cubes, signal_cubes = tuple(self._plan.cubes_of(baseline)), tuple(self._plan.cubes_of(signal))
        if not baseline_cubes:
            return _Problem(f"The baseline step {baseline.order} contains no whole cube.")
        if not signal_cubes:
            return _Problem(f"The signal step {signal.order} contains no whole cube.")
        if set(baseline_cubes) & set(signal_cubes):
            return _Problem("The baseline and the signal step share cubes.")
        if len(baseline_cubes) < 3:
            return _Problem(f"The baseline step has {len(baseline_cubes)} cubes; a noise needs at least 3.")
        cubes = tuple(sorted({*baseline_cubes, *signal_cubes}))
        first, last = self._dataset.cube_spans_unix_ms(cubes)
        return TuningRequest(
            roi_ids, self._lattice, baseline_cubes, signal_cubes, self._roi_toolbox.detection_settings().formula_key, self._settings.metric_settings(),
            self._settings.statistics_settings(), np.asarray(first, dtype=np.float64), np.asarray(last, dtype=np.float64),
        )
