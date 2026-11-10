"""``StepOverlays``: everything the Sensorgram panel does with the experiment steps (split out of ``panel.py`` 2026-10-26, behaviour unchanged).

The steps come from ``ExperimentPlanModule`` (the dataset's measured steps with the user's edits). This object owns what hangs on them:

- the **bar of steps** on the plot and its ribbon group "View steps" on the Experimental plan tab (`PlanBarControls`), and the bar's gestures (`PlanBarActions`, which edits the module);
  the bar's structure tools in the right-click menu (remove, add, unify, the roles) are offered only while that tab is the active one (`set_structure_tools`);
- the group "Edit" (`PlanEditControls`, 2026-11-10): add (at the cursor, in front of / behind the selected step) / remove / merge / split / restore buttons, the same commands as that
  menu plus add at the cursor and split at the cursor (through `PlanBarActions`); their
  enabled state follows the selection and the plan (`_update_edit_tools`);
- the group "Evaluation" (`EvaluationControls`): one icon whose pop-up holds a picker per baseline / signal step of each evaluation pair (B1, S1, B2, S2 ...), which write the plan
  module's roles; taking the last pair out clears its roles in the module;
- the **table of mean +/- SD per step** and its ribbon group "View stats" (`StatsTableControls`);
- the two inputs the steps add to a Sensorgram request: the **pieces to smooth on their own** ("smooth inside steps") and **which cubes belong to which step** (`step_inputs`).

It converts the steps to the plot's x axis (``plan/axis_map.py``) and hands them to the plot, and it asks the panel for a redraw when a change of the steps changes what the curves are
made of (a moved, reset or restored boundary while either input is wanted; never for a note or a colour). It reads what is drawn, computes nothing, and knows nothing of the job, the
cache or the baseline: the panel gives it four callbacks (`drawn`, `x_axis`, `schedule_redraw`, `defer`) and one to tell it when steps exist (`on_steps_available`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget

from ...analysis.baseline_drift import uses_anchor_steps, window_step_regions
from ...analysis.scan_store import StoredScan
from ...analysis.scan_trace import Series
from ...analysis.settings_module import AnalysisSettingsModule
from ...analysis.step_stats import StepMembership, assign_cubes, membership_segments
from ...dataset import DatasetModule
from ...plan.bar_model import PlanBarStep, build_bar_steps
from ...selection.experiment_plan_module import (
    REASON_BOUNDARY,
    REASON_DATASET,
    REASON_RESET,
    REASON_RESTORED,
    REASON_STRUCTURE,
    ExperimentPlanModule,
)
from ...plan.stats_table import StatsLine, build_stats_table
from ...selection import CubeDisplayModule
from ...selection.cube_display_module import MODE_TIME
from ..ui_state import UiStateStore
from .edit_group import PlanEditControls
from .plan_actions import PlanAxisContext, PlanBarActions
from .evaluation_group import EvaluationControls
from .plan_group import PlanBarControls
from .plot import LEGEND_MAX_HEIGHT_FRACTION, MAX_LEGEND_ENTRIES, MAX_LEGEND_SERIES, SensorgramPlot
from .stats_group import StatsTableControls

# a step edit that moves where a step starts or ends, or changes which steps exist (not its note, colour or role)
_STEP_TIME_CHANGES = (REASON_BOUNDARY, REASON_RESET, REASON_RESTORED, REASON_DATASET, REASON_STRUCTURE)

Drawn = tuple[StoredScan, list[Series]]  # the scan and the lines last drawn


class StepOverlays(QObject):
    def __init__(
        self,
        parent: QWidget,
        *,
        experiment_plan: ExperimentPlanModule | None,
        plot: SensorgramPlot,
        dataset: DatasetModule,
        settings: AnalysisSettingsModule,
        cube_display: CubeDisplayModule,
        drawn: Callable[[], Drawn | None],
        x_axis: Callable[[], tuple[str, np.ndarray]],
        schedule_redraw: Callable[[], None],
        defer: Callable[[], bool],
        on_steps_available: Callable[[bool], None],
        current_cube: Callable[[], int] | None = None,
    ) -> None:
        """`drawn`: the scan and lines last drawn, None before the first draw or after a clear. `x_axis`: (label, x per cube of the file). `schedule_redraw`: ask the panel for a
        new request. `defer`: True while the panel is hidden (the overlays then skip drawing; the panel catches up when shown). `on_steps_available`: told, at every refresh, whether the
        dataset has steps. `current_cube`: the cube the cursor stands on (without it no step can be added at the cursor). Without `experiment_plan` the two ribbon groups stay greyed
        out and nothing is drawn."""
        super().__init__(parent)
        self._plan = experiment_plan
        self._plot = plot
        self._dataset = dataset
        self._settings = settings
        self._cube_display = cube_display
        self._drawn = drawn
        self._x_axis = x_axis
        self._schedule_redraw = schedule_redraw
        self._defer = defer
        self._on_steps_available = on_steps_available
        self._current_cube = current_cube
        self._edit_has_axis = False  # what the last full update of the Edit group found: traces drawn, so the plan has an axis
        self._edit_cursor_flags = (False, False)  # (the cursor is on the plot, it is inside a step) as the Edit group last showed them
        self.plan_controls = PlanBarControls(parent)
        self.plan_controls.set_available(False)  # until a dataset with steps is open
        self.stats_controls = StatsTableControls(parent)
        self.stats_controls.set_available(False)
        self.evaluation = EvaluationControls(parent)
        self.evaluation.role_chosen.connect(self._on_role_chosen)
        self.evaluation.pair_removed.connect(self._on_pair_removed)
        self.actions = None if experiment_plan is None else PlanBarActions(experiment_plan, plot, self.plan_context, self, cursor=self.cursor_unix_ms)
        self.edit_controls = PlanEditControls(parent)  # add / remove / merge steps (2026-11-10): the right-click menu's commands on the steps selected on the bar
        if self.actions is not None:
            self.edit_controls.add_requested.connect(self._on_add_requested)
            self.edit_controls.remove_requested.connect(self.actions.remove_selected)
            self.edit_controls.merge_requested.connect(self.actions.merge_selected)
            self.edit_controls.split_requested.connect(self.actions.split_at_cursor)
            self.edit_controls.restore_requested.connect(self.actions.restore_removed)
            plot.plan_bar().selection_changed.connect(lambda _indices: self._update_edit_tools())
        self.set_structure_tools(False)  # until the Experimental plan tab is shown
        self.plan_controls.settings_changed.connect(self.refresh)
        self.stats_controls.settings_changed.connect(self._on_stats_settings_changed)
        if experiment_plan is not None:
            experiment_plan.steps_changed.connect(self._on_plan_changed)

    # -- the panel's wiring -------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        self.plan_controls.restore_ui_state(store)
        self.stats_controls.restore_ui_state(store)
        self.evaluation.restore_ui_state(store)

    def refresh_theme(self) -> None:
        self.edit_controls.refresh_theme()
        self.plan_controls.refresh_theme()
        self.stats_controls.refresh_theme()
        self.evaluation.refresh_theme()

    def set_structure_tools(self, active: bool) -> None:
        """The right-click menu offers remove / add / unify steps and the baseline / signal roles only while the Experimental plan tab is the active one."""
        if self.actions is not None:
            self.actions.set_structure_tools(active)

    def _on_add_requested(self, where: str) -> None:
        """An Add button of the Edit group: "cursor" starts a new step at the cursor, "before" / "after" put one next to the selected step."""
        if self.actions is None:
            return
        if where == "cursor":
            self.actions.add_at_cursor()
        else:
            self.actions.add_at_selection(where)

    def cursor_unix_ms(self) -> float | None:
        """The time of the cube cursor (the dashed line the cube slider moves) on the plot, or None when the cursor is not on it: nothing drawn, the cube is not one of the plotted
        cubes, or it has no timing."""
        drawn = self._drawn()
        if drawn is None or self._current_cube is None:
            return None
        stored, _series = drawn
        cube = int(self._current_cube())
        if cube not in stored.cube_indices:
            return None
        start = self._dataset.cube_start_unix_ms(cube)
        return None if start is None else float(start)

    def _on_role_chosen(self, role: str, ref: object, pair: int) -> None:
        """The user picked a step for a pair's baseline or signal in the Evaluation pop-up (or none): the plan module keeps it."""
        if self._plan is None:
            return
        step = self._plan.step_by_ref(ref) if ref is not None else None
        self._plan.set_role(role, None if step is None else step.order - 1, pair)

    def _on_pair_removed(self, pair: int) -> None:
        """The user took the last pair out of the Evaluation pop-up: its roles are cleared in the plan (one undo step)."""
        if self._plan is not None:
            self._plan.clear_pair(pair)

    # -- what the steps add to a request ------------------------------------------------------------------------------------

    def smoothing_follows_steps(self) -> bool:
        """Smoothing is done step by step: the switch is on, something is smoothed, and the dataset has experiment steps. Otherwise the whole trace is smoothed."""
        stats = self._settings.statistics_settings()
        return self._plan is not None and stats.smooth_within_steps and stats.smoothing_method != "none" and self._plan.has_steps()

    def stats_wanted(self) -> bool:
        """The step statistics table is on and the dataset has experiment steps: the lines then carry their mean +/- SD per step."""
        return self.stats_controls.settings().visible and self._plan is not None and self._plan.has_steps()

    def step_inputs(self, stored: StoredScan) -> tuple[np.ndarray | None, StepMembership | None]:
        """What the request needs from the experiment steps: (the pieces to smooth on their own, one label per cube; which cubes belong to which step, for the numbers of the
        table). Each is None when not wanted (switch off, nothing smoothed, table off, no steps). One look at the steps and at the cube times serves both. Cheap (a few steps x the
        cubes), so it is made on every redraw and a dragged boundary is picked up."""
        pieces, numbers = self.smoothing_follows_steps(), self.stats_wanted()
        if not (pieces or numbers):
            return None, None
        membership = assign_cubes(self._plan.steps(), *self._dataset.cube_spans_unix_ms(stored.cube_indices))
        return (membership_segments(membership) if pieces else None), (membership if numbers else None)

    def anchor_steps_marked(self) -> bool:
        """The Range baseline has plan steps marked as its anchors (2026-11-09)."""
        return uses_anchor_steps(self._settings.statistics_settings())

    def baseline_step_regions(self, stored: StoredScan) -> tuple[np.ndarray, ...] | None:
        """The Range baseline's marked anchor steps as cube regions for the request (`baseline_drift.window_step_regions`): None when none is marked
        (the window's mean), () when the dataset has no steps to place them on. Cheap, made on every redraw, so a moved boundary is picked up."""
        if not self.anchor_steps_marked():
            return None
        if self._plan is None or not self._plan.has_steps():
            return ()
        membership = assign_cubes(self._plan.steps(), *self._dataset.cube_spans_unix_ms(stored.cube_indices))
        return window_step_regions(membership, self._settings.statistics_settings())

    def anchor_step_indices(self) -> frozenset[int]:
        """The positions (in `ExperimentPlanModule.steps()`, the bar's `index`) of the steps marked as Range baseline anchors."""
        if self._plan is None or not self.anchor_steps_marked():
            return frozenset()
        refs = set(self._settings.statistics_settings().baseline_anchor_steps)
        return frozenset(i for i, step in enumerate(self._plan.steps()) if tuple(step.ref) in refs)

    # -- reacting to changes ------------------------------------------------------------------------------------------------

    def _on_plan_changed(self, reason: str = "") -> None:
        """The experiment steps changed (a dataset opened, a session restored, an edit). Another dataset or session means other steps, and a step added, removed or unified
        renumbers the rest (the selection is by index): nothing stays selected."""
        if reason in ("dataset", "restored", REASON_STRUCTURE):
            self._plot.plan_bar().clear_selection()
        if not self._defer():
            self.refresh()
        if reason in _STEP_TIME_CHANGES and (self.smoothing_follows_steps() or self.stats_wanted() or self.anchor_steps_marked()):
            # the pieces smoothed on their own, the cubes of each step's numbers and the Range baseline's anchor steps are the steps: another boundary,
            # other values (the fits are cached)
            self._schedule_redraw()

    def _on_stats_settings_changed(self) -> None:
        """The table was switched on or off, moved to the other edge or resized. The numbers are computed with the curves, so switching it on asks for a redraw when the
        lines drawn do not carry them yet; the rest is only drawing."""
        drawn = self._drawn()
        lines = drawn[1] if drawn is not None else []
        if self.stats_wanted() and any(s.step_values is None for s in lines):
            self._schedule_redraw()
        else:
            self.refresh()

    # -- the steps on the plot ----------------------------------------------------------------------------------------------

    def _plan_axis(self):
        """(time <-> x map, plotted cubes) for the traces being drawn, or None when nothing is drawn or the cubes have no timing."""
        drawn = self._drawn()
        if self._plan is None or drawn is None:
            return None
        stored, _series = drawn
        _label, x = self._x_axis()
        cubes = [int(c) for c in stored.cube_indices]
        axis = self._plan.axis_map(cubes, [float(v) for v in x])
        return None if axis is None else (axis, cubes)

    def plan_context(self) -> PlanAxisContext | None:
        """What a gesture on the plan bar needs: the axis, where a boundary may land, and the unit. None while nothing is drawn."""
        found = self._plan_axis()
        if found is None:
            return None
        axis, cubes = found
        return PlanAxisContext(axis, self._plan.snap_grid_unix_ms(cubes), self._cube_display.effective_mode() == MODE_TIME)

    def refresh(self) -> None:
        """Convert the experiment steps to the plot's x axis and hand them to the plot, or hand it none (no steps, no traces, no timing); then the table. Cheap: a few steps and
        the series already drawn; it does not compute anything."""
        settings = self.plan_controls.settings()
        steps = () if self._plan is None else self._plan.steps()
        self.plan_controls.set_available(bool(steps))
        self._on_steps_available(bool(steps))
        found = self._plan_axis() if steps else None
        if found is None:
            self._show_evaluation(steps, None)
            self._plot.set_plan((), settings)
            self.show_stats_table((), [], bool(steps))
            self._update_edit_tools(has_axis=False)
            return
        axis, _cubes = found
        _stored, series = self._drawn()
        lines: list[tuple[np.ndarray, np.ndarray]] = []
        for s in series:  # everything drawn counts for the Data text mode: each line and the edges of its band
            lines.append((s.x, s.y))
            if s.band_low is not None and s.band_high is not None:
                lines.extend(((s.x, s.band_low), (s.x, s.band_high)))
        bars = build_bar_steps(
            steps, axis, lines, text_mode=settings.text_mode, separators=settings.separators,
            time_mode=self._cube_display.effective_mode() == MODE_TIME,
        )
        self._plot.set_plan(bars, settings)
        self._plot.plan_bar().set_baseline_anchors(self.anchor_step_indices())  # a small anchor in the lower left corner of each
        self._show_evaluation(steps, [bar.color for bar in bars])
        self.show_stats_table(bars, series, True)
        self._update_edit_tools(has_axis=True)

    def _update_edit_tools(self, has_axis: bool | None = None) -> None:
        """The Edit group: which of add / remove / merge / split / restore can act on the steps selected on the bar and the cursor now. `has_axis`: the traces drawn give the plan an
        axis (a new boundary snaps to their cubes); None asks. Cheap (a few steps), so it runs at every refresh and every change of the selection."""
        if self._plan is None or self.actions is None:
            self.edit_controls.set_state(False, 0, False, False, False)
            return
        if has_axis is None:
            has_axis = self._plan_axis() is not None
        self._edit_has_axis = has_axis
        self._edit_cursor_flags = on_plot, inside = self._cursor_flags(has_axis)
        self.edit_controls.set_state(
            self._plan.has_steps(), len(self.actions.selected_steps()), has_axis, self.actions.can_merge_selected(), self._plan.has_removed(),
            can_add_at_cursor=on_plot, can_split=inside,
        )

    def _cursor_flags(self, has_axis: bool) -> tuple[bool, bool]:
        """(the cube cursor is on the plot, the cursor is strictly inside a step): what the Add at cursor and Split buttons need. A look-up of one cube and a pass over a few steps."""
        at = self.cursor_unix_ms() if has_axis else None
        if at is None or self._plan is None:
            return False, False
        return True, any(step.start_unix_ms < at < step.end_unix_ms for step in self._plan.steps())

    def cursor_moved(self) -> None:
        """The cube cursor moved (slider, a click on the plot, Play). Only whether it is on the plot and whether it is inside a step can change which Edit buttons are on, so the
        group is updated only when one of those changes: this runs at every slideshow step and must stay O(1) in the size of the dataset (a cube look-up and a few steps; the axis is
        not rebuilt)."""
        if self.actions is None:
            return
        if self._cursor_flags(self._edit_has_axis) != self._edit_cursor_flags:
            self._update_edit_tools(has_axis=self._edit_has_axis)

    def _show_evaluation(self, steps, colours: list[str] | None) -> None:
        """The Evaluation pop-up: the plan's steps (in the bar's colours when the bar is drawn) and the roles of every pair."""
        self.evaluation.set_steps(steps, colours, () if self._plan is None else self._plan.state().pairs)

    def show_stats_table(self, bars: Sequence[PlanBarStep], series: list[Series], has_steps: bool) -> None:
        """The table of mean +/- SD per step and line, from the numbers the lines carry (computed with them, in the worker), or none: no steps, nothing drawn, the numbers
        not computed yet (a redraw is on its way), or too many lines for a legend (the rows are the legend's rows). Cheap: it only reshapes numbers already there."""
        settings = self.stats_controls.settings()
        table, note = None, ""
        if series and bars and settings.visible and has_steps:
            if self._plot.legend_entry_count() != len(series):
                note = (
                    f"Not drawn now: the legend does not hold {len(series)} lines (it keeps at most {MAX_LEGEND_ENTRIES}, and more than {MAX_LEGEND_SERIES} only while it takes "
                    f"at most {round(100 * LEGEND_MAX_HEIGHT_FRACTION)} % of the plot height), and the rows are the legend's rows. Select fewer ROIs or groups, or make the panel taller."
                )
            elif all(s.step_values is not None and len(s.step_values.mean) >= len(bars) for s in series):
                lines = [
                    StatsLine(s.label, s.color, tuple(float(v) for v in s.step_values.mean), tuple(float(v) for v in s.step_values.sd),
                              tuple(int(v) for v in s.step_values.n), s.step_values.between_rois)
                    for s in series
                ]
                table = build_stats_table(bars, lines)
        self.stats_controls.set_available(has_steps, note)
        self._plot.set_stats_table(table, settings)
