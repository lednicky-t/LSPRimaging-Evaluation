"""``SignalMapModule``: what the Image panel's signal map shows, and the numbers behind it (2026-11-10; pure colour logic: ``analysis/signal_map.py``).

**No second calculation.** The map's numbers are the ROI / Groups table's step numbers (`StepTableModule`), which are the Sensorgram's lines as drawn. This module
asks that module to calculate the rows it needs (every ROI, or every group) and reads them back; the fitted traces are shared with the Sensorgram and the table
(`TraceStore`), so a ROI already fitted for either costs nothing, and a moved step, the Subtract switch or another scheme costs milliseconds.

**When it calculates** (the rule of the app: only on an explicit request). Switching the map on, choosing "By group", and the Refresh button are requests: each
calculates the rows that are missing or out of date. Anything else that changes an input (a fit setting, a new scan, a moved step boundary, a new ROI) does not
calculate: the map is drawn faded (out of date) or grey (no number yet), and `needs_refresh` lights the Refresh button.

**What it hands out.** `view()` is a `SignalMapView` (one colour per ROI, the legend's range and title, the numbers for the hover text and the export), or None while
the map is off; `view_changed` fires when it changes. `status()` is one sentence for the ribbon's tooltip and the status bar. The settings are plain values the
owner restores from the remembered controls; nothing here is saved in the session (like the formula and the fit, the numbers are recalculated, never stored).
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..analysis.signal_map import (
    DEFAULT_PER,
    DEFAULT_RANGE,
    DEFAULT_SCHEME,
    DEFAULT_SHOW,
    PER_GROUP,
    SHOW_CUBE,
    SHOW_SNR,
    SHOW_STEP,
    RANGE_AUTO,
    SignalMapView,
    colours_for,
    full_range,
    value_range,
)
from ..analysis.step_table import OP_DIFFERENCE, OP_SNR, UNGROUPED, GroupMembers, compare_values

if TYPE_CHECKING:  # only used in annotations
    from ..roi import RoiToolbox
    from .experiment_plan_module import ExperimentPlanModule
    from .step_table_module import StepTableModule


class SignalMapModule(QObject):
    view_changed = pyqtSignal(object)
    """The map to draw changed: a `SignalMapView`, or None (the map is off)."""
    status_changed = pyqtSignal()
    """`status()` or `needs_refresh()` may have changed."""

    def __init__(self, step_table: StepTableModule, roi_toolbox: RoiToolbox, experiment_plan: ExperimentPlanModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._steps = step_table
        self._toolbox = roi_toolbox
        self._plan = experiment_plan
        self._enabled = False
        self._show = DEFAULT_SHOW
        self._pair = 1
        self._step_ref: object | None = None
        self._per = DEFAULT_PER
        self._scheme = DEFAULT_SCHEME
        self._range = DEFAULT_RANGE
        self._fixed = (-1.0, 1.0)
        self._cube = 0  # the cube shown on the Image panel (dataset cube index), for "cube signal"
        self._range_cache: tuple[tuple, list[np.ndarray], tuple[float, float]] | None = None  # cube signal: the whole-dataset range of the lines it was taken from
        self._view: SignalMapView | None = None
        self._revision = 0
        self._status = "The signal map is off."
        self._needs_refresh = False
        self._calculating = False
        # Several signals arrive together (the step module emits `numbers_changed` and `state_changed` in a row): rebuild once, after them.
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(0)
        self._rebuild_timer.timeout.connect(self._rebuild)
        step_table.numbers_changed.connect(self._schedule)
        step_table.state_changed.connect(self._schedule)
        step_table.finished.connect(self._on_finished)
        experiment_plan.steps_changed.connect(self._schedule)
        roi_toolbox.geometry_changed.connect(self._schedule)
        roi_toolbox.cosmetic_changed.connect(self._schedule)  # a regroup changes which group a ROI takes its colour from
        roi_toolbox.roi_ids_renumbered.connect(self._schedule)

    # -- settings (the owner hands over the ribbon's values) ----------------------------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        """Switch the map on (a request: missing or out-of-date numbers are calculated) or off (the ROIs get their own colours back)."""
        self._enabled = bool(on)
        self._rebuild()
        if self._enabled and self._needs_refresh:
            self.calculate()

    def set_show(self, show: str) -> None:
        self._set("_show", show)

    def set_pair(self, pair: int) -> None:
        self._set("_pair", max(1, int(pair)))

    def set_step(self, ref: object | None) -> None:
        self._set("_step_ref", ref)

    def set_per(self, per: str) -> None:
        """Each ROI or by group. By group needs the group rows, which the table may not have: a request, so they are calculated."""
        self._set("_per", per)
        if self._enabled and self._needs_refresh:
            self.calculate()

    def set_scheme(self, key: str) -> None:
        self._set("_scheme", key)

    def set_range(self, mode: str, low: float, high: float) -> None:
        if (mode, (float(low), float(high))) != (self._range, self._fixed):
            self._range, self._fixed = mode, (float(low), float(high))
            self._rebuild()

    def set_cube(self, cube_index: int) -> None:
        """The cube shown on the Image panel changed (slider, Play). Only "cube signal" depends on it; a lookup, nothing is calculated."""
        if int(cube_index) == self._cube:
            return
        self._cube = int(cube_index)
        if self._show == SHOW_CUBE:
            self._schedule()

    def baseline_enabled(self) -> bool:
        """Subtract baseline: the Sensorgram's Subtract switch (one setting for the Sensorgram, the table and the map)."""
        return self._steps.baseline_enabled()

    def set_baseline_enabled(self, on: bool) -> None:
        """Switch Subtract baseline from the map (relative shift on, absolute position off). It changes the lines, so it is a request: with the map on, the rows are
        recalculated (the traces are kept, so it costs the statistics only)."""
        if bool(on) == self.baseline_enabled():
            return
        self._steps.set_baseline_enabled(bool(on))
        if self._enabled:
            self._rebuild()
            if self._needs_refresh:
                self.calculate()

    def _set(self, name: str, value: object) -> None:
        if getattr(self, name) != value:
            setattr(self, name, value)
            self._rebuild()

    # -- queries ------------------------------------------------------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._enabled

    def view(self) -> SignalMapView | None:
        return self._view

    def status(self) -> str:
        return self._status

    def needs_refresh(self) -> bool:
        """The map is on and some numbers are missing or out of date: a Refresh would change it."""
        return self._enabled and self._needs_refresh

    def is_calculating(self) -> bool:
        return self._calculating

    def roi_ids(self) -> tuple[int, ...]:
        return tuple(sorted(int(r.area_roi_id) for r in self._toolbox.rois()))

    def export_info(self) -> dict[str, object]:
        """What produced the map, for the record beside an exported table (the step module's record, plus the map's own choices)."""
        info = dict(self._steps.export_info())
        view = self._view
        info["signal_map"] = {
            "show": self._show, "pair": self._pair, "per": self._per, "scheme": self._scheme, "range": self._range, "subtract_baseline": self.baseline_enabled(),
            **({"cube": self._cube} if self._show == SHOW_CUBE else {}),
            "low": view.low if view else None, "high": view.high if view else None, "title": view.title if view else "",
        }
        return info

    # -- calculating ----------------------------------------------------------------------------------------------------------------

    def calculate(self) -> bool:
        """Calculate the rows the map needs (missing or out of date), off the GUI thread, in the step module. False with a reason in `status()` if nothing can be."""
        reason = self.blocked_reason()
        if reason:
            self._set_status(reason, self._needs_refresh)
            return False
        roi_ids, groups = self._needed_rows()
        if not roi_ids and not groups:
            self._set_status("Signal map: there are no ROIs.", False)
            return False
        started = self._steps.recalculate(roi_ids, groups)
        self._calculating = started
        if started:
            self._set_status("Signal map: calculating the step numbers (progress in the status bar).", self._needs_refresh)
        return started

    def _needed_rows(self) -> tuple[tuple[int, ...], tuple[GroupMembers, ...]]:
        if self._per == PER_GROUP:
            return (), self._group_rows()
        return self.roi_ids(), ()

    def _group_rows(self) -> tuple[GroupMembers, ...]:
        """Every group with members, then the ROIs in no group (the table's and the Sensorgram's "Ungrouped" line). A ROI in two groups takes the first."""
        claimed: set[int] = set()
        rows: list[GroupMembers] = []
        for group in self._toolbox.groups():
            members = tuple(sorted(int(r) for r in group.area_roi_ids))
            if members:
                rows.append(GroupMembers(group.group_id, members))
                claimed.update(members)
        rest = tuple(r for r in self.roi_ids() if r not in claimed)
        if rest:
            rows.append(GroupMembers(UNGROUPED, rest))
        return tuple(rows)

    def _on_finished(self, outcome: str, message: str) -> None:
        if not self._calculating:
            return
        self._calculating = False
        if outcome == "failed":
            self._set_status(f"Signal map: the calculation failed: {message}", True)
        self._schedule()

    # -- building the view -----------------------------------------------------------------------------------------------------------

    def blocked_reason(self) -> str:
        if not self._steps.can_calculate():
            return "Signal map: needs a scan and experiment steps (run the scan in Analysis > Scan)."
        if self._show == SHOW_CUBE:
            return ""
        if self._show == SHOW_STEP:
            if self._plan.step_by_ref(self._step_ref) is None:  # type: ignore[arg-type]
                return "Signal map: choose the step to show (Signal map settings)."
            return ""
        base, signal = self._plan.baseline_step(self._pair), self._plan.signal_step(self._pair)
        if base is None or signal is None:
            return (
                f"Signal map: choose the baseline B{self._pair} and the signal S{self._pair} steps in the Sensorgram's Experimental plan tab (Evaluation)."
            )
        if base.ref == signal.ref:
            return f"Signal map: B{self._pair} and S{self._pair} are the same step."
        return ""

    def _schedule(self, *_args: object) -> None:
        if self._enabled:
            self._rebuild_timer.start()

    def _rebuild(self) -> None:
        if not self._enabled:
            self._needs_refresh = False
            self._publish(None)
            self._set_status("The signal map is off.", False)
            return
        reason = self.blocked_reason()
        roi_ids = self.roi_ids()
        if reason:
            values = {r: math.nan for r in roi_ids}
            self._needs_refresh = False
            self._publish(self._make_view(values, {}, {}, "", "nm", stale=False, missing=len(roi_ids)))
            self._set_status(reason, False)
            return
        title, unit, read = self._reader()
        values: dict[int, float] = {}
        spreads: dict[int, float] = {}
        source: dict[int, str] = {}
        lines: list[np.ndarray] = []  # cube signal: every line used, for the whole-dataset colour range
        stale = False
        missing = 0
        if self._per == PER_GROUP:
            names = {g.group_id: g.name for g in self._toolbox.groups()}
            for row in self._group_rows():
                got = read(
                    lambda ref, key=row.key, members=row.roi_ids: self._steps.group_step_value(key, members, ref),
                    lambda key=row.key, members=row.roi_ids: self._steps.group_line(key, members), lines,
                )
                label = "Ungrouped" if row.key is UNGROUPED else f"group {names.get(row.key, row.key)}"
                for r in row.roi_ids:
                    if r in values:
                        continue
                    self._put(r, got, label, values, spreads, source)
                if got is None:
                    missing += len(row.roi_ids)
                elif not got[2]:
                    stale = True
        else:
            for r in roi_ids:
                got = read(lambda ref, r=r: self._steps.roi_step_value(r, ref), lambda r=r: self._steps.roi_line(r), lines)
                self._put(r, got, f"ROI {r}", values, spreads, source)
                if got is None:
                    missing += 1
                elif not got[2]:
                    stale = True
        self._needs_refresh = bool(missing or stale)
        notes: tuple[str, ...] = ()
        span = None
        if self._show == SHOW_CUBE:
            span = self._dataset_range(lines)
            if self._steps.scan_position(self._cube) is None and lines:
                notes = (f"Cube {self._cube} is not in the scan: no value.",)
        view = self._make_view(values, spreads, source, title, unit, stale=stale, missing=missing, span=span, notes=notes)
        self._publish(view)
        self._set_status(self._describe(view), self._needs_refresh)

    def _dataset_range(self, lines: list[np.ndarray]) -> tuple[float, float]:
        """Cube signal: the colour range over every value of every line used (all ROIs or groups, all cubes), so a colour means the same on every cube. Cached:
        the cube slider and Play only look values up; the range is taken again only when the lines or the range setting change."""
        key = (self._range, self._fixed)
        cached = self._range_cache
        if cached is not None and cached[0] == key and len(cached[1]) == len(lines) and all(a is b for a, b in zip(cached[1], lines)):
            return cached[2]
        everything = np.concatenate([np.ravel(a) for a in lines]) if lines else np.empty(0)
        # Auto = the lines' own min..max (maintainer 2026-11-11: a 0..7 nm response must read 0..7, not -7..7 because some noise dips below zero)
        span = full_range(everything) if self._range == RANGE_AUTO else value_range(everything, self._range, self._fixed)
        self._range_cache = (key, list(lines), span)
        return span

    @staticmethod
    def _put(roi_id: int, got: tuple[float, float, bool] | None, label: str, values: dict, spreads: dict, source: dict) -> None:
        values[roi_id] = got[0] if got is not None else math.nan
        spreads[roi_id] = got[1] if got is not None else math.nan
        source[roi_id] = label

    def _reader(self):
        """``(title, unit, read)``: ``read(lookup, line, lines)`` turns a row's numbers into ``(value, spread, current)``, or None when the row has no numbers for
        what is shown. ``lookup(ref)`` = the step module's ``(StepValue, current)`` in a step, or None; ``line()`` = the row's line as drawn and whether it is
        current, or None; ``lines`` collects the lines used (cube signal: the colour range is taken over all of them)."""
        pair = self._pair
        subtract = self._steps.baseline_enabled()
        if self._show == SHOW_CUBE:
            position = self._steps.scan_position(self._cube)
            title = f"Cube {self._cube}, {'shift from baseline' if subtract else 'position'}"

            def read_cube(_lookup, line, lines):
                got = line()
                if got is None:
                    return None
                lines.append(got[0])
                value = float(got[0][position]) if position is not None and position < len(got[0]) else math.nan
                return value, math.nan, got[1]

            return title, "nm", read_cube
        if self._show == SHOW_STEP:
            step = self._plan.step_by_ref(self._step_ref)  # type: ignore[arg-type]
            name = f"Step {step.order}" + (f" ({step.label})" if step and step.label else "") if step else "Step"
            title = f"{name} mean, {'shift from baseline' if subtract else 'position'}"

            def read_step(lookup, _line, _lines):
                got = lookup(self._step_ref)
                return None if got is None else (got[0].mean, got[0].sd, got[1])

            return title, "nm", read_step
        base, signal = self._plan.baseline_step(pair), self._plan.signal_step(pair)
        op = OP_SNR if self._show == SHOW_SNR else OP_DIFFERENCE
        title = f"(S{pair} − B{pair}) / σ_B{pair}" if op == OP_SNR else f"Shift S{pair} − B{pair}"
        unit = "" if op == OP_SNR else "nm"

        def read_pair(lookup, _line, _lines):
            b, s = lookup(base.ref), lookup(signal.ref)
            if b is None or s is None:
                return None
            _n, value, sd, _sem = compare_values(op, b[0], s[0])
            return value, sd, b[1] and s[1]

        return title, unit, read_pair

    def _make_view(
        self, values: dict, spreads: dict, source: dict, title: str, unit: str, *, stale: bool, missing: int,
        span: tuple[float, float] | None = None, notes: tuple[str, ...] = (),
    ) -> SignalMapView:
        """`span`: the colour range when it is not taken from these values (cube signal: over the whole dataset)."""
        ids = sorted(values)
        array = np.asarray([values[r] for r in ids], dtype=np.float64)
        low, high = span if span is not None else value_range(array, self._range, self._fixed)
        colours = colours_for(array, low, high, self._scheme, stale=stale)
        notes = (*notes, *((f"{missing} ROIs have no number yet.",) if missing else ()))
        return SignalMapView(
            self._revision, dict(zip(ids, colours)), values, spreads, source, title, unit, self._scheme, low, high, stale, missing, notes,
        )

    def _publish(self, view: SignalMapView | None) -> None:
        old = self._view
        if view is None and old is None:
            return
        if view is not None and old is not None and _same(view, old):
            return
        if view is not None:
            self._revision += 1
            view = replace(view, revision=self._revision)
        self._view = view
        self.view_changed.emit(view)

    def _describe(self, view: SignalMapView) -> str:
        shown = sum(1 for v in view.values.values() if math.isfinite(v))
        parts = [f"Signal map: {view.title}, {shown} of {len(view.values)} ROIs"]
        if math.isfinite(view.low):
            d = view.decimals()
            unit = f" {view.unit}" if view.unit else ""
            parts.append(f"colours from {view.low:.{d}f} to {view.high:.{d}f}{unit}")
        text = ", ".join(parts) + "."
        if self._calculating or self._steps.is_running():
            text += " Calculating..."
        elif view.missing:
            text += f" {view.missing} ROIs have no number yet (grey): press Refresh."
        elif view.stale:
            text += " The numbers are out of date (faded): press Refresh."
        return text

    def _set_status(self, text: str, needs_refresh: bool) -> None:
        self._status = text
        self._needs_refresh = needs_refresh
        self.status_changed.emit()


def _same(a: SignalMapView, b: SignalMapView) -> bool:
    """Equal in everything drawn or said (the revision aside). NaN != NaN, so the numbers are compared as text."""
    return (
        a.colors == b.colors and a.title == b.title and a.unit == b.unit and a.scheme == b.scheme and a.stale == b.stale and a.missing == b.missing
        and repr((a.low, a.high, sorted(a.values.items()), sorted(a.spreads.items()), sorted(a.source.items()))) ==
        repr((b.low, b.high, sorted(b.values.items()), sorted(b.spreads.items()), sorted(b.source.items())))
    )
