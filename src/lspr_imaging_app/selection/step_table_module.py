"""``StepTableModule``: the step columns of the ROI / Groups table, as a module of their own (2026-11-05).

**What it is.** The owner of "the mean and SD of every ROI and group in every experiment step": which steps are the columns and what they are called, the numbers
of each row, whether those numbers are still current, and the calculation that makes them. The table panel shows them and asks for a calculation; the Sensorgram and
this module share the fitted traces (`TraceStore`), the x axis and the Subtract switch (`SensorgramAxis`) and the statistics settings (`AnalysisSettingsModule`), so
the numbers here are the numbers of the Sensorgram's lines (`step_table.py`). Design record: ``docs/step_table_2026-11-05.md``.

**Calculation is the user's decision** (maintainer, 2026-11-05): nothing is calculated until the user asks for the rows on screen (`recalculate`), so a table with hundreds of
collapsed ROIs costs nothing. What is asked for is: every ROI row on screen, and every group row on screen. A group row needs the traces of **all** its member ROIs, expanded
or not (its number is the average of all of them), so a collapsed group still fits its members; only the ROIs of a collapsed group get no row numbers of their own. The fits
are the cost (a few ms per ROI for the polynomial fit, more for the Gaussian / Voigt ones, `scan_trace.py`) and are cached in the shared store, so everything after the
first calculation (a moved step, the Subtract switch, another smoothing) costs only milliseconds.

**Comparison columns** (step 2, 2026-11-05). After the step columns the table can show columns that compare two steps of an evaluation pair of the Sensorgram's Experimental plan
(S1 - B1: the signal step against the baseline step; later any pair of steps). They are *derived*: pure arithmetic on the step numbers already calculated (`step_table.compare_values`),
so they need no calculation of their own, change the moment the user picks other steps for the pair, and are current exactly when the two step cells they use are. What a column
computes is its operation (`OP_DIFFERENCE`, `OP_SNR`), chosen in the column's header. The list of comparison columns is a view setting (remembered by the table like its other
controls, not part of the session); `add_compare` / `set_compare_op` / `remove_compare` / `restore_compare` change it and tell the table through ``columns_changed``.

**Out of date.** Every row remembers what it was computed from (the scan, the fit, the steps' boundaries, the x axis, the statistics that matter, the members of a group)
and `cell` says whether that still holds. A changed input does not recalculate by itself: the row keeps its numbers, marked not current, and `state_changed` tells the
panel to dim them and light the recalculate button. A different dataset drops the numbers (they would be another experiment's). A new scan file keeps them, dimmed. After a
ROI renumbering every row is out of date (the ids no longer name the same ROIs; the scan file still holds the old ids until it is run again, as the Sensorgram shows).
The scan's own out-of-date cubes (grey diamonds on the Sensorgram) are a different matter that a recalculation cannot fix; `scan_note` says so.

**Threads.** The fits and the statistics run on a worker thread, the fits themselves in the helper process (`TraceStore.pool`), like the Sensorgram's. The newest request wins;
the traces a cancelled or superseded run finished are kept. Nothing here shows a widget: progress goes out as signals (the panel's `PanelTask` shows it in the status bar).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.scan_trace import fit_missing_traces, trace_key
from ..analysis.step_stats import assign_cubes, membership_segments
from ..analysis.step_table import (
    OP_DIFFERENCE,
    OP_SNR,
    OPERATIONS,
    GroupMembers,
    RowNumbers,
    StepTableNumbers,
    StepValue,
    compare_values,
    compute_step_numbers,
    numbers_key,
    sem_of,
)
from ..analysis.trace_store import TraceStore
from ..applog import log_event
from ..plan.bar_model import describe_devices, effective_colors
from ..plan.label_rule import DEFAULT_SEPARATORS, MODE_WHOLE, shown_label
from .experiment_plan_module import REASON_DATASET, ExperimentPlanModule
from .sensorgram_axis import SensorgramAxis

if TYPE_CHECKING:  # only used in annotations
    from ..analysis.scan_results import ScanResultsModule
    from ..analysis.scan_store import StoredScan
    from ..analysis.settings_module import AnalysisSettingsModule
    from ..dataset.module import DatasetModule
    from ..roi import RoiToolbox

logger = logging.getLogger(__name__)

_UNSET = object()
_TIME_CHANGES = ("boundary", "reset", "restored", "structure")  # `ExperimentPlanModule.steps_changed` reasons that move or add/remove steps (the dataset reason clears everything)
_ROI_REASONS = {"detection_settings", "session_restored"}  # `RoiToolbox.geometry_changed` reasons that change the formula the traces are made with
_JOIN_SECONDS = 5.0
_PROGRESS_MIN_ROIS = 8  # a status line "Fitting ROI 3 of 160" only for runs long enough to need it


@dataclass(frozen=True)
class StepColumn:
    """One experiment step as a column. `ref` names it (it stays the same when steps are added or removed; the number does not)."""

    ref: object  # plan.state.StepRef
    order: int  # 1, 2, 3 ... by start time: the number the user sees
    title: str  # as the Sensorgram's plan bar writes it (the label rule applied)
    tooltip: str
    roles: tuple[str, ...]  # "B1", "S1" ...
    color: str | None  # '#rrggbb' the Sensorgram's plan bar paints this step with (the user's own colour, else the automatic one), so the table can paint the column the same; None for a comparison column
    kind: str = "step"  # "step" or "compare"
    unit: str = "nm"  # of the numbers in the column's cells ("" for a ratio)
    decimals: int | None = None  # None: the table's number of decimals
    value_word: str = "mean"  # what the first number is called in the tool tip and the export: "mean", "difference", "signal-to-noise"
    has_sd: bool = True
    export_label: str = ""  # an ASCII label for the CSV header; empty: "step N (title)"
    op: str = ""  # a comparison column: its operation
    pair: int = 0  # a comparison column: the evaluation pair it compares


@dataclass(frozen=True)
class CompareSpec:
    """One comparison column as the user made it."""

    uid: int  # stays the same while the column lives (its header is renumbered when others are removed)
    pair: int  # the evaluation pair (1 = B1 / S1)
    op: str


COMPARE = "cmp"  # the first item of a comparison column's `ref`: ("cmp", uid)


def compare_title(pair: int, op: str) -> str:
    """The title of a comparison column, written as the maths it calculates: ``μ_S1 − μ_B1`` (the mean of the step with the signal role minus the mean of the baseline step) and
    ``(μ_S1 − μ_B1) / σ_B1`` (that, divided by the noise of the baseline step). The header and the arrow menu draw ``_S1`` as a subscript (`panels/roi_table/step_columns.py`,
    `subscript_runs`, `title_html`); a place that cannot has `plain_title` or words. This is the one place the title is written."""
    shift = f"μ_S{pair} − μ_B{pair}"
    return f"({shift}) / σ_B{pair}" if op == OP_SNR else shift


def is_compare_ref(ref: object) -> bool:
    return isinstance(ref, tuple) and len(ref) == 2 and ref[0] == COMPARE


@dataclass(frozen=True)
class CellValue:
    n: int
    mean: float
    sd: float
    current: bool  # False: computed from inputs that have changed since
    sem: float = float("nan")  # standard error of the mean, SD / sqrt(n) (`step_table.sem_of`); for a difference the two steps' SEMs added in quadrature


@dataclass(frozen=True)
class _Row:
    numbers: RowNumbers
    stamp: tuple


@dataclass(frozen=True)
class _Job:
    generation: int
    stored: "StoredScan"
    key: tuple
    rois: tuple[int, ...]
    groups: tuple[GroupMembers, ...]
    needed: tuple[int, ...]
    known: dict
    formula_key: str
    metric: object
    x: object
    statistics: object
    segments: object
    membership: object
    roi_stamp: tuple
    cancel: threading.Event
    response_groups: tuple = ()  # the session's ROI groups (member ids), for the response shape


class StepTableModule(QObject):
    numbers_changed = pyqtSignal()
    """Numbers arrived, were dropped, or a row stopped being current: repaint."""
    columns_changed = pyqtSignal()
    """The steps (or the way they are titled) changed: the table's step columns must be rebuilt."""
    state_changed = pyqtSignal()
    """Something that decides whether the numbers are current changed: the panel re-reads `count_not_current` for the recalculate button."""
    started = pyqtSignal(str)
    """A calculation began (message for the status bar)."""
    progress = pyqtSignal(float, str)
    """(fraction 0..1, message) while ROIs are being fitted."""
    finished = pyqtSignal(str, str)
    """("completed" | "failed" | "cancelled", message)."""
    notice = pyqtSignal(str)
    """A plain sentence for the user: nothing can be calculated, and why."""
    _result_ready = pyqtSignal(object, object, object, object)  # (job, traces fitted, numbers | None, error | None) - from the worker thread
    _progress_from_worker = pyqtSignal(int, int)

    def __init__(
        self,
        scan_results: ScanResultsModule,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        analysis_settings: AnalysisSettingsModule,
        experiment_plan: ExperimentPlanModule,
        traces: TraceStore,
        axis: SensorgramAxis,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan_results
        self._toolbox = roi_toolbox
        self._dataset = dataset
        self._settings = analysis_settings
        self._plan = experiment_plan
        self._traces = traces
        self._axis = axis
        self._rois: dict[int, _Row] = {}
        self._groups: dict[str | None, _Row] = {}
        self._base: tuple | None | object = _UNSET
        self._columns: tuple[StepColumn, ...] | None = None
        self._compare: tuple[CompareSpec, ...] = ()
        self._compare_uid = 0
        self._title_mode = MODE_WHOLE
        self._separators: tuple[str, ...] = DEFAULT_SEPARATORS
        self._scan_generation = 0  # a new scan file: whatever was computed from the old one is out of date
        self._renumbering = 0  # a ROI renumbering: the ids name other ROIs than the numbers were computed for
        self._job: _Job | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0  # a calculation from before a dataset change is dropped
        self._result_ready.connect(self._on_result)
        self._progress_from_worker.connect(self._on_progress)
        scan_results.results_changed.connect(self._on_results_changed)
        analysis_settings.settings_changed.connect(lambda _change: self._invalidate())
        experiment_plan.steps_changed.connect(self._on_plan_changed)
        roi_toolbox.geometry_changed.connect(self._on_roi_changed)
        roi_toolbox.cosmetic_changed.connect(self._on_roi_cosmetic)
        roi_toolbox.roi_ids_renumbered.connect(self._on_renumbered)
        axis.axis_changed.connect(self._invalidate)

    # -- the columns ----------------------------------------------------------------------------------------------------------

    def set_title_style(self, mode: str, separators: Sequence[str]) -> None:
        """How a step's note is written in its title: the Sensorgram plan bar's text mode and separators (`plan/label_rule.py`), so the table says what the bar says."""
        separators = tuple(separators)
        if (mode, separators) != (self._title_mode, self._separators):
            self._title_mode, self._separators = mode, separators
            self._columns = None
            self.columns_changed.emit()

    def columns(self) -> tuple[StepColumn, ...]:
        """The steps as columns in time order, then the comparison columns in the order they were made."""
        if self._columns is None:
            steps = self._plan.steps()
            colors = effective_colors(steps, self._separators)  # the bar's colours: same steps, same separators
            self._columns = (*(self._column(step, color) for step, color in zip(steps, colors)), *(self._compare_column(spec) for spec in self._compare))
        return self._columns

    # -- comparison columns -------------------------------------------------------------------------------------------------

    def compare_specs(self) -> tuple[CompareSpec, ...]:
        return self._compare

    def defined_pairs(self) -> list[int]:
        """The evaluation pairs that have both a baseline and a signal step."""
        return [p.number for p in self._plan.state().pairs if p.baseline is not None and p.signal is not None]

    def add_compare(self) -> CompareSpec:
        """A new comparison column: the first combination of (evaluation pair that is set, operation) that is not a column yet; failing that, pair 1 with the difference. The pair
        need not be set (the column then says so and stays empty until it is)."""
        have = {(c.pair, c.op) for c in self._compare}
        pairs = self.defined_pairs() or [1]
        pick = next(((pair, op) for pair in pairs for op in OPERATIONS if (pair, op) not in have), (pairs[0], OP_DIFFERENCE))
        spec = CompareSpec(self._next_uid(), pick[0], pick[1])
        self._set_compare((*self._compare, spec))
        return spec

    def set_compare_op(self, uid: int, op: str) -> None:
        if op in OPERATIONS:
            self._set_compare(tuple(CompareSpec(c.uid, c.pair, op) if c.uid == uid else c for c in self._compare))

    def remove_compare(self, uid: int) -> None:
        self._set_compare(tuple(c for c in self._compare if c.uid != uid))

    def restore_compare(self, entries: Sequence[tuple[int, str]]) -> None:
        """The columns as remembered: `(pair, operation)` each. Anything unknown is left out."""
        wanted = [(int(pair), op) for pair, op in entries if op in OPERATIONS and int(pair) >= 1]
        if wanted != [(c.pair, c.op) for c in self._compare]:  # the same list again (the table restores it every time it is shown) changes nothing
            self._set_compare(tuple(CompareSpec(self._next_uid(), pair, op) for pair, op in wanted))

    def _next_uid(self) -> int:
        self._compare_uid += 1
        return self._compare_uid

    def _set_compare(self, specs: tuple[CompareSpec, ...]) -> None:
        if specs != self._compare:
            self._compare = specs
            self._columns = None
            self.columns_changed.emit()

    def _compare_column(self, spec: CompareSpec) -> StepColumn:
        base, signal = self._plan.baseline_step(spec.pair), self._plan.signal_step(spec.pair)
        s_name, b_name = f"S{spec.pair}", f"B{spec.pair}"

        def named(step) -> str:
            return "not set" if step is None else f"step {step.order}" + (f", {shown_label(step.label, self._title_mode, self._separators)}" if step.label else "")

        lines = [f"Signal step {s_name} ({named(signal)}) compared with baseline step {b_name} ({named(base)})."]
        if base is None or signal is None:
            lines.append(f"Choose the steps of evaluation pair {spec.pair} in the Sensorgram's Experimental plan tab (Evaluation); the column stays empty until both are set.")
        # The titles are written as the maths they are (μ = the mean of the step with that role, σ = the noise of the baseline step); the header draws "_S1" as a subscript
        # (`panels/roi_table/view.py`, `step_columns.subscript_runs`; a title never reaches a plain-text place as it is: `step_columns.plain_title`). The tool tip says it in words,
        # the export labels stay ASCII, "S1 - B1".
        title = compare_title(spec.pair, spec.op)
        if spec.op == OP_SNR:
            word, unit, decimals, has_sd = "signal-to-noise", "", 1, False
            label = f"({s_name} - {b_name}) / noise of {b_name}"
            lines.append("Value: (mean of the signal step − mean of the baseline step) / σ of the baseline step, its noise. σ is the SD of the baseline step about a straight line through it, so a steady drift is not counted.")
        else:
            word, unit, decimals, has_sd = "difference", "nm", None, True
            label = f"{s_name} - {b_name}"
            lines.append("Value: mean of the signal step − mean of the baseline step. SD: the two steps' SDs added in quadrature (assumes independent cubes; it does not shrink with more cubes).")
        lines.append("Click the arrow in the header to change the calculation or remove the column.")
        return StepColumn((COMPARE, spec.uid), 0, title, "\n".join(lines), (), None, "compare", unit, decimals, word, has_sd, label, spec.op, spec.pair)

    def _column(self, step, color: str) -> StepColumn:
        title = shown_label(step.label, self._title_mode, self._separators) or f"Step {step.order}"
        lines = [f"Step {step.order}: {step.label}" if step.label else f"Step {step.order}", f"{step.duration_s:.1f} s"]
        lines.extend(describe_devices(step.devices))
        for role in step.roles:
            lines.append(f"{'Baseline' if role.startswith('B') else 'Signal'} step of evaluation pair {role[1:]} ({role}).")
        if step.is_added:
            lines.append("Added by you (not in the dataset).")
        elif step.edited:
            lines.append("Edited (the measured step is kept in the dataset).")
        return StepColumn(step.ref, step.order, title, "\n".join(lines), tuple(step.roles), color)

    # -- the numbers ------------------------------------------------------------------------------------------------------------

    def roi_cell(self, roi_id: int, ref: object) -> CellValue | None:
        """The ROI's numbers in the step named by `ref`; None if the ROI has none (never calculated, or its row was calculated without that step)."""
        row = self._rois.get(int(roi_id))
        if row is None:
            return None
        stamp = self._stamp(group=False, members=None)
        return self._compare_cell(row, ref, stamp) if is_compare_ref(ref) else self._cell(row, ref, stamp)

    def group_cell(self, key: str | None, members: Sequence[int], ref: object) -> CellValue | None:
        row = self._groups.get(key)
        if row is None:
            return None
        stamp = self._stamp(group=True, members=tuple(sorted(int(r) for r in members)))
        return self._compare_cell(row, ref, stamp) if is_compare_ref(ref) else self._cell(row, ref, stamp)

    def _compare_cell(self, row: _Row, ref: object, stamp: tuple | None) -> CellValue | None:
        """A comparison column's cell, worked out from the row's two step cells. None where the column's pair is not set or the row was calculated without one of its steps."""
        spec = next((c for c in self._compare if c.uid == ref[1]), None)  # type: ignore[index]
        if spec is None:
            return None
        base, signal = self._plan.baseline_step(spec.pair), self._plan.signal_step(spec.pair)
        if base is None or signal is None or base.ref not in row.numbers.refs or signal.ref not in row.numbers.refs:
            return None
        n, value, sd, sem = compare_values(spec.op, row.numbers.value_at(base.ref), row.numbers.value_at(signal.ref))
        return CellValue(n, value, sd, current=stamp is not None and row.stamp == stamp, sem=sem)  # current exactly when the row's numbers are (they are all the cell is made of)

    def _cell(self, row: _Row, ref: object, stamp: tuple | None) -> CellValue | None:
        if ref not in row.numbers.refs:
            return None
        n, mean, sd = row.numbers.at(ref)
        return CellValue(n, mean, sd, current=stamp is not None and row.stamp == stamp, sem=sem_of(n, sd))

    def roi_step_value(self, roi_id: int, ref: object) -> tuple[StepValue, bool] | None:
        """Everything a ROI row has in one step (n, mean, SD, noise) and whether it is current; None if the row has no numbers for that step. For a reader
        that combines steps itself (the signal map, `selection/signal_map_module.py`)."""
        row = self._rois.get(int(roi_id))
        if row is None or ref not in row.numbers.refs:
            return None
        stamp = self._stamp(group=False, members=None)
        return row.numbers.value_at(ref), stamp is not None and row.stamp == stamp

    def group_step_value(self, key: str | None, members: Sequence[int], ref: object) -> tuple[StepValue, bool] | None:
        """As `roi_step_value`, for a group row (`members`: the group's ROI ids, which are part of what makes it current)."""
        row = self._groups.get(key)
        if row is None or ref not in row.numbers.refs:
            return None
        stamp = self._stamp(group=True, members=tuple(sorted(int(r) for r in members)))
        return row.numbers.value_at(ref), stamp is not None and row.stamp == stamp

    def has_numbers(self) -> bool:
        return bool(self._rois or self._groups)

    def count_not_current(self, roi_ids: Sequence[int], groups: Sequence[GroupMembers]) -> int:
        """How many of these rows have no numbers or numbers that are out of date: what a recalculation of them would change."""
        roi_stamp = self._stamp(group=False, members=None)
        count = 0
        for roi_id in roi_ids:
            row = self._rois.get(int(roi_id))
            count += row is None or roi_stamp is None or row.stamp != roi_stamp
        for g in groups:
            row = self._groups.get(g.key)
            stamp = self._stamp(group=True, members=tuple(sorted(int(r) for r in g.roi_ids)))
            count += row is None or stamp is None or row.stamp != stamp
        return count

    def scan_note(self) -> str:
        """A sentence about the scan's own out-of-date cubes (empty when there are none): a recalculation cannot fix them, the scan has to be run again."""
        stale, stored = self._scan.stale_cubes(), self._scan.stored()
        if not stale or stored is None:
            return ""
        return f"{len(stale)} of {len(stored.cube_indices)} cubes of the scan are out of date (grey diamonds on the Sensorgram); run the scan again."

    def is_running(self) -> bool:
        return self._job is not None

    def can_calculate(self) -> bool:
        """There is a scan and there are experiment steps: a calculation would produce numbers. (`recalculate` says why not, in a notice; this asks without a word, for a
        calculation the user did not ask for, such as the one after launch.)"""
        return self._scan.stored() is not None and bool(self._plan.steps())

    # -- the Subtract switch and the record of an export -------------------------------------------------------------------------

    def baseline_enabled(self) -> bool:
        """The Sensorgram's Subtract switch (one setting for both): the means then read as shifts from the baseline, otherwise as absolute positions."""
        return self._settings.statistics_settings().baseline_enabled

    def set_baseline_enabled(self, on: bool) -> None:
        self._axis.set_baseline_enabled(on)

    def export_info(self) -> dict[str, object]:
        """What produced the numbers, for the record beside an exported table."""
        from dataclasses import asdict

        detection = self._toolbox.detection_settings()
        return {
            "x_axis": self._axis.label_and_x()[0],
            "settings": {"formula": detection.formula_key, "metric": asdict(self._settings.metric_settings()), "statistics": asdict(self._settings.statistics_settings())},
            "steps": [
                {"order": s.order, "title": c.title, "note": s.label, "start_unix_ms": s.start_unix_ms, "end_unix_ms": s.end_unix_ms, "roles": list(s.roles)}
                for s, c in zip(self._plan.steps(), self.columns())
            ],
            "comparisons": [
                {"title": c.export_label, "operation": c.op, "pair": c.pair, "unit": c.unit}
                for c in self.columns() if c.kind == "compare"
            ],
            "what": (
                "mean and sd over the cubes of each experiment step, of the line as the Sensorgram draws it (spike removal, smoothing, baseline subtraction as set). "
                "A ROI row is the ROI's own line; a group row is the line made by averaging all the group's ROIs (the Sensorgram's Centre setting), so its sd is the "
                "scatter of that averaged line in the step, not the spread between the ROIs. sem = sd / sqrt(n), n = the cubes in the step (a difference column: the two "
                "steps' sems added in quadrature); it counts every cube as independent, so smoothing makes it a little too small."
            ),
        }

    # -- what the numbers were made from ----------------------------------------------------------------------------------------

    def _base_stamp(self) -> tuple | None:
        """What the numbers of every row depend on, except a group's members; None when nothing can be calculated (no scan)."""
        if self._base is _UNSET:
            stored = self._scan.stored()
            if stored is None:
                self._base = None
            else:
                statistics = self._settings.statistics_settings()
                steps = tuple((s.ref, s.start_unix_ms, s.end_unix_ms) for s in self._plan.steps())
                scan = (
                    self._scan_generation, self._renumbering,
                    trace_key(stored, self._toolbox.detection_settings().formula_key, self._settings.metric_settings(), self._group_members()),
                )
                self._base = (scan, steps, self._axis.uses_time(), numbers_key(statistics, group=False), numbers_key(statistics, group=True))
        return self._base  # type: ignore[return-value]

    def _stamp(self, *, group: bool, members: tuple[int, ...] | None) -> tuple | None:
        base = self._base_stamp()
        if base is None:
            return None
        return (*base[:3], base[4] if group else base[3], members)

    def _invalidate(self, *_args: object) -> None:
        self._base = _UNSET
        self.state_changed.emit()
        self.numbers_changed.emit()

    def _on_results_changed(self) -> None:
        self._scan_generation += 1
        self._invalidate()

    def _on_renumbered(self, _mapping: object) -> None:
        self._renumbering += 1
        self._invalidate()

    def _on_roi_changed(self, change: object) -> None:
        if getattr(change, "reason", "") in _ROI_REASONS:
            self._invalidate()

    def _on_roi_cosmetic(self, change: object) -> None:
        """A regroup is cosmetic for every metric but the response shape, whose weights are learned per group (`analysis/response_shape.py`)."""
        if getattr(change, "reason", "") in ("regroup", "session_restored") and self._settings.metric_settings().uses_response:
            self._invalidate()

    def _group_members(self) -> tuple[tuple[int, ...], ...]:
        """The session's ROI groups as member ids (what the response shape learns its weights from)."""
        return tuple(tuple(g.area_roi_ids) for g in self._toolbox.groups())

    def _on_plan_changed(self, reason: str) -> None:
        self._columns = None
        if reason == REASON_DATASET:
            self.clear()
            self.columns_changed.emit()
            return
        self.columns_changed.emit()  # a note, a colour, a role (which steps a comparison column uses), or the steps themselves
        if reason in _TIME_CHANGES:
            self._invalidate()

    def clear(self) -> None:
        """Another dataset: drop the numbers and stop a calculation of the old one."""
        self._generation += 1
        if self._job is not None:
            self._job.cancel.set()
        self._rois, self._groups, self._base = {}, {}, _UNSET
        self.numbers_changed.emit()
        self.state_changed.emit()

    # -- calculating ------------------------------------------------------------------------------------------------------------

    def recalculate(self, roi_ids: Sequence[int], groups: Sequence[GroupMembers]) -> bool:
        """Calculate these rows (the ones on screen) now, off the GUI thread. True if a calculation was started; False, with a `notice`, if there is nothing to calculate."""
        stored = self._scan.stored()
        if stored is None:
            self.notice.emit("No scan yet. Run it in Workflow > Analysis.")
            return False
        steps = self._plan.steps()
        if not steps:
            self.notice.emit("This dataset has no experiment steps, so there are no step columns to fill.")
            return False
        rois = tuple(dict.fromkeys(int(r) for r in roi_ids))
        group_rows = tuple(GroupMembers(g.key, tuple(sorted(int(r) for r in g.roi_ids))) for g in groups if g.roi_ids)  # members in id order: the same set always gives the same numbers
        if not rois and not group_rows:
            self.notice.emit("No ROI or group is shown.")
            return False
        detection = self._toolbox.detection_settings()
        metric = self._settings.metric_settings()
        statistics = self._settings.statistics_settings()
        membership = assign_cubes(steps, *self._dataset.cube_spans_unix_ms(stored.cube_indices))
        within = statistics.smooth_within_steps and statistics.smoothing_method != "none"
        needed = tuple(dict.fromkeys([*rois, *(r for g in group_rows for r in g.roi_ids)]))
        group_members = self._group_members()
        key = trace_key(stored, detection.formula_key, metric, group_members)
        known = self._traces.known(key)
        self._traces.warm_up()
        if self._job is not None:
            self._job.cancel.set()  # superseded: it stops between ROIs and what it fitted is kept
        self._generation += 1
        job = _Job(
            self._generation, stored, key, rois, group_rows, needed, known, detection.formula_key, metric, self._axis.x(), statistics,
            membership_segments(membership) if within else None, membership,
            self._stamp(group=False, members=None) or (), threading.Event(),  # a group's stamp is made when its numbers arrive (its members are part of it)
            group_members,
        )
        self._job = job
        missing = sum(1 for r in needed if r not in known)
        self.started.emit(f"fitting {missing} ROI{'s' if missing != 1 else ''}" if missing else "applying the statistics")
        self._thread = threading.Thread(target=self._work, args=(job,), name="step-table-compute", daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel.set()

    def shutdown(self) -> None:
        """The application is quitting: stop a calculation and wait briefly, so it never emits into an object Qt is tearing down."""
        self._generation += 1
        if self._job is not None:
            self._job.cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=_JOIN_SECONDS)

    def _work(self, job: _Job) -> None:
        t0 = time.perf_counter()
        missing = sum(1 for r in job.needed if r not in job.known)
        report = (lambda done, total: self._progress_from_worker.emit(done, total)) if missing >= _PROGRESS_MIN_ROIS else None
        try:
            new, was_cancelled = fit_missing_traces(
                job.stored, job.needed, job.formula_key, job.metric, job.known,  # type: ignore[arg-type]
                cancelled=job.cancel.is_set, progress=report, runner=self._traces.runner(), groups=job.response_groups,
            )
            fitted_ms = (time.perf_counter() - t0) * 1e3
            numbers = None
            if not was_cancelled:
                numbers = compute_step_numbers(
                    job.rois, job.groups, {**job.known, **new}, job.x, job.statistics, job.segments, job.membership,  # type: ignore[arg-type]
                    tuple(job.stored.cube_indices),
                )
            log_event(
                logger, "step_table.compute", logging.DEBUG, rois=len(job.rois), groups=len(job.groups), fitted=len(new), fit_ms=round(fitted_ms, 1),
                total_ms=round((time.perf_counter() - t0) * 1e3, 1), cancelled=was_cancelled,
            )
            self._result_ready.emit(job, new, numbers, None)
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Calculating the step columns failed")
            self._result_ready.emit(job, {}, None, exc)

    def _on_progress(self, done: int, total: int) -> None:
        if self._job is not None and not self._job.cancel.is_set():
            self.progress.emit(done / total if total else -1.0, f"fitting ROI {done} of {total}")

    def _on_result(self, job: _Job, new: object, numbers: object, error: object) -> None:
        if isinstance(new, dict) and new:
            self._traces.add(job.key, new)  # a cancelled or superseded run's finished traces are kept (ignored if the fit settings have moved on)
        if job is not self._job:
            return  # superseded: the newer calculation reports
        self._job = None
        if isinstance(error, BaseException):
            self.finished.emit("failed", str(error))
        elif not isinstance(numbers, StepTableNumbers) or job.generation != self._generation or job.cancel.is_set():
            self.finished.emit("cancelled", "")
        else:
            for roi_id, values in numbers.rois.items():
                self._rois[roi_id] = _Row(values, job.roi_stamp)
            members_of = {g.key: g.roi_ids for g in job.groups}
            for key, values in numbers.groups.items():
                self._groups[key] = _Row(values, self._group_stamp_of(job, members_of[key]))
            self.finished.emit("completed", "")
        self.numbers_changed.emit()
        self.state_changed.emit()

    @staticmethod
    def _group_stamp_of(job: _Job, members: tuple[int, ...]) -> tuple:
        """The stamp of a group row calculated by `job`: what the job was made from (taken when it started), with the members it used."""
        return (*job.roi_stamp[:3], numbers_key(job.statistics, group=True), tuple(members))  # type: ignore[arg-type]
