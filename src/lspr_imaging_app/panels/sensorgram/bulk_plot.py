"""The S_bulk plot (2026-11-12): a floating window with the step means against the refractive index, the fitted bulk line and the steps that do not follow it.

Screen purpose: see the fit behind the S_bulk numbers: is it a straight line, which step is off, and how far a step outside the set (e.g. a protein) sits from the line
(the ROI table's measured − predicted, drawn). A floating window, not a panel (maintainer, 2026-11-12: a rare check), opened and closed from the Sensorgram's S_bulk pop-up.

Layout::

    [Lines: Sensorgram lines | All groups v]  [Point size: 9 px]  [Calculate]  [save]       <- Calculate only when groups are asked for and not calculated yet
    +----------------------------------------------------------+
    |  y: step mean (nm), or shift with Subtract on              |      per line (its Sensorgram colour / the group colour):
    |      ●──────── fitted line, light band = its uncertainty   |      filled points = steps in the S_bulk set, vertical bars = SEM, horizontal = the RI's Std
    |   ○  (a step not in the set, with an RI)                   |      hollow points = other steps with an RI
    |  x: refractive index (RIU, at 589.3 nm)                    |
    +----------------------------------------------------------+
    legend: name  S_bulk = 254.3 ± 3.1 nm/RIU  (χ²red 1.2)
    status line: why something is missing (no S_bulk set, no RIs, groups not calculated, steps left out for no RI)

Display only: the numbers are the ones the Sensorgram's lines carry per step (computed with the curves while this window is open, `StepOverlays.stats_wanted`) or the
ROI table's group rows (`StepTableModule.group_numbers`); the fit is `step_table.bulk_values` / `bulk_sensitivity.excess`, the same as the table's S_bulk column. Nothing
is fitted on the GUI thread beyond those few-point lines (microseconds). The window remembers its size, place, the Lines choice and the point size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis import bulk_sensitivity as bs
from ...analysis.export_tables import Table
from ...analysis.step_table import BulkPoint, RowNumbers, bulk_values
from ..plot_export_button import ExportData, PlotExportButton
from ..ui_state import UiStateStore
from ...selection.plot_axes_module import PlotAxesModule
from ...selection.plot_line_order_module import PLOT_BULK, PlotLineOrderModule
from .axis_apply import AxisStyler
from .axis_controls import AxisControls
from .guide_controls import GuideControls
from .broken_axis import BrokenAxisItem
from .guide_items import GuideGestures, GuideLines, GuideViewBox
from .plot_window_draw import (
    LegendEntry,
    PlotAreaButton,
    DrawnState,
    StepCursor,
    add_band,
    fill_legend,
    legend_name,
    make_legend,
    median_abs_residual,
    mean_of,
    rows_of,
    stack_lines,
    style_legend,
)
from .view_settings import UNCERTAINTY_BAND, UNCERTAINTY_SD, SensorgramViewSettings, UncertaintyLook
from .plot_look import LINES_SENSORGRAM, WINDOW_LOOK_DEFAULTS, PlotLookControls, PlotLookKeys, UncertaintyControls, UncertaintyKeys, lines_pick_menu, restore_pick

GEOMETRY_KEY = "sensorgram/bulk_plot_geometry"
LINES_KEY = "sensorgram/bulk_plot_lines"
CURSOR_KEY = "sensorgram/bulk_plot_cursor"  # the refractive index of the step the cursor stands on
MAX_LINES = 12  # more lines than this make an unreadable plot; the rest are named in the status line
_BAND_POINTS = 60


@dataclass(frozen=True)
class BulkLine:
    """One line of the plot: its name, colour and step numbers (refs in the plan's step order)."""

    label: str
    color: str
    numbers: RowNumbers
    current: bool = True
    short_label: str = ""  # the label without the number of ROIs ("Group A" for "Group A (n=12)"), for the legend's ROI-count option


@dataclass(frozen=True)
class PlotStep:
    ref: object
    order: int
    name: str
    ri: float
    ri_u: float
    in_set: bool


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _band(fit: bs.BulkFit, x: np.ndarray) -> np.ndarray:
    """The line's own standard uncertainty at each x (from the fitted points' variances, raised as S_bulk's u is); NaN where unknown."""
    var = np.asarray(fit.var, dtype=np.float64)
    out = np.empty_like(x)
    for i, xi in enumerate(x):
        a = bs.line_weights(fit, float(xi))
        out[i] = math.sqrt(float((a * a * var).sum())) * fit.inflation
    return out


class BulkPlotWindow(QWidget):
    """The floating S_bulk plot. The owner hands it the lines (`set_lines`), the plan's steps with their RIs (`set_steps`) and the weighting; it asks for group numbers
    through `calculate_requested`. Its Points & lines, Legend and Uncertainty menus are the shared definitions of `plot_look.py` with this window's own values and remembered keys."""

    calculate_requested = pyqtSignal()
    lines_mode_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, line_order: PlotLineOrderModule | None = None, plot_axes: PlotAxesModule | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Bulk sensitivity")
        self.resize(600, 440)
        # This window's own look: the menus are the shared definitions (`plot_look.py`), the values and the remembered keys are this window's.
        self.look = PlotLookControls(self, PlotLookKeys.with_prefix("sensorgram/bulk_plot_look"), WINDOW_LOOK_DEFAULTS, object_prefix="bulk_plot", joined=False)
        self.uncertainty = UncertaintyControls(self, UncertaintyKeys.with_prefix("sensorgram/bulk_plot_look"), object_prefix="bulk_plot")
        self._store: UiStateStore | None = None
        self._lines: list[BulkLine] = []
        self._steps: list[PlotStep] = []
        self._weighting = bs.WEIGHT_INVERSE_VARIANCE
        self._y_label = "Step mean (nm)"
        self._labels = ("", "")  # the quantities the axes show, as the last redraw named them (the Plot menu keeps its options per quantity)
        self._drawing = False
        self._x_range: tuple[float, float] | None = None  # the RI range last set, so a zoom of the user's is kept until the RIs change
        self._wavelength_nm: float | None = None
        self._note = ""
        self._fits: list[tuple[BulkLine, object]] = []
        self._last_entries: list[LegendEntry] = []  # the lines drawn now: the Line order table's rows
        self._state: DrawnState | None = None  # what was drawn last, for a change that only re-orders (None: draw everything again)
        self.look.attach_line_order(line_order, PLOT_BULK, "S_bulk", lambda: rows_of(self._last_entries))  # the Manual draw order's list is the session's

        self._plot = pg.PlotWidget(parent=self, viewBox=GuideViewBox(), axisItems={"bottom": BrokenAxisItem("bottom")})  # the bottom axis counts from the Plot menu's zero
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._styler = AxisStyler(self._plot.getPlotItem())  # the Plot menu's tick spacing, grid and display range (2026-11-27)
        self.axes = AxisControls(self, plot_axes, PLOT_BULK, lambda: self._labels, lambda: self._visible_range(), object_prefix="bulk_axes", zero_steps=self._zero_step_choices)
        self.guides = GuideControls(self, self.axes.module, PLOT_BULK, lambda: self._labels, object_prefix="bulk_guides")
        self._guide_lines = GuideLines(self._plot.getPlotItem())  # drawn after every redraw: `PlotItem.clear()` takes them out
        self._guide_lines.moved.connect(self.guides.move_guide)  # dragged with the mouse: the table keeps it
        self._guide_gestures = GuideGestures(self._plot.getPlotItem(), self._guide_lines, self.guides, lambda text: self.status.setText(text))  # right-click: add a guide here, a guide's menu
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._lines_pick = lines_pick_menu(self)
        bar.addWidget(self._lines_pick)
        bar.addWidget(_separator(self))
        for menu in (self.look.lines_menu, self.uncertainty.menu, self.look.legend_menu, self.axes.menu, self.guides.menu):
            bar.addWidget(menu)
        bar.addStretch(1)
        self.export_button = PlotExportButton("s_bulk", lambda: self._plot.getPlotItem(), self.export_data, self)
        bar.addWidget(self.export_button)
        outer.addLayout(bar)
        self._legend = make_legend(self._plot.getPlotItem())
        self._cursor = StepCursor(self._plot.getPlotItem(), CURSOR_KEY, self)  # the step (its refractive index) the legend's "Value at step" order reads
        self._cursor.moved.connect(self._look_changed)
        outer.addWidget(self._plot, 1)
        self.calculate_button = PlotAreaButton("Calculate groups", self._plot)  # in the plot area, not the row: the plot is empty exactly when it is needed
        self.calculate_button.setToolTip("The groups' step numbers are not calculated yet (or out of date): calculate them, as the ROI table's refresh button does.")
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self._lines_pick.currentIndexChanged.connect(lambda _i: self.lines_mode_changed.emit(self.lines_mode()))
        self.calculate_button.clicked.connect(lambda _checked=False: self.calculate_requested.emit())
        self.look.settings_changed.connect(self._look_changed)
        self.uncertainty.settings_changed.connect(self._look_changed)
        self.axes.axes_changed.connect(self._axes_changed)
        self.refresh_theme()

    # -- what it shows ------------------------------------------------------------------------------------------------------------

    def lines_mode(self) -> str:
        return self._lines_pick.currentData() or LINES_SENSORGRAM

    def set_lines_mode(self, mode: str) -> None:
        """Press the Lines button of `mode` and report it, as a click does (a restore calls `restore_ui_state`, which does not)."""
        index = self._lines_pick.findData(mode)
        if index >= 0:
            self._lines_pick.setCurrentIndex(index)  # reports, as a pick does (a restore does not)

    def _visible_range(self) -> tuple[tuple[float, float], tuple[float, float]]:
        (x_low, x_high), (y_low, y_high) = self._plot.getPlotItem().getViewBox().viewRange()
        return (float(x_low), float(x_high)), (float(y_low), float(y_high))

    def _zero_step_choices(self) -> list[tuple[str, float]]:
        """The plan's steps that have a refractive index, as (label, RI) for the Plot menu's "Zero at" list: the x of a step is its RI."""
        return [(f"{s.name} · {s.ri:g}" if s.name else f"{s.ri:g}", float(s.ri)) for s in self._steps if math.isfinite(s.ri)]

    def _axes_changed(self) -> None:
        """The Plot menu's options changed (or an axis shows another quantity): ticks and grid at once; a range is shown by drawing the plot again (the RI span of the x axis is then ranged
        afresh, as after new steps)."""
        new = self.axes.axes()
        old = self._styler.axes()
        only_guides, redraw_needed = old.only_guides_differ(new), old.needs_redraw(new)
        self._styler.set_axes(new)
        if only_guides:  # a guide was added, edited or removed: the lines are put right without drawing the plot again
            self._guide_lines.set_guides(new.y.guides)
            return
        if not redraw_needed:  # a new step value, the minor step, the grid: the styler applied it at once; only a range or a step set / cleared needs the items drawn again
            return
        if not self._drawing:
            self._x_range = None
            if self.isVisible():
                self.redraw()

    def _look_changed(self, *_args: object) -> None:
        """A look option changed, or the cursor moved. When that only re-orders (`DrawnState.can_reorder`: the legend's order, the Draw order, the legend's corner, the cursor) the lines stay and
        the legend rows and the stacking are put in the new order (~30 ms instead of ~80-120); anything else, or nothing drawn yet, draws the plot again."""
        if not self.isVisible():
            return
        state, view, unc = self._state, self.look.settings(), self.uncertainty.settings()
        if state is None or not state.can_reorder(view, unc):
            self.redraw()
            return
        self._legend.clear()
        style_legend(self._legend, view)
        self._state = state.reordered(view, self._cursor.x())
        self._apply_order(self._state.entries, view, state.groups)

    def set_steps(self, steps: Sequence[PlotStep], wavelength_nm: float | None) -> None:
        self._steps, self._wavelength_nm = list(steps), wavelength_nm
        self._state = None

    def set_weighting(self, weighting: str) -> None:
        self._weighting = weighting
        self._state = None

    def set_lines(self, lines: Sequence[BulkLine], *, y_label: str, note: str = "", need_calculation: bool = False) -> None:
        """New lines (or none, with `note` saying why); redraws when shown."""
        self._lines, self._y_label, self._note = list(lines), y_label, note
        self._state = None
        self.calculate_button.set_centered(not self._lines)
        self.calculate_button.setVisible(need_calculation)
        if self.isVisible():
            self.redraw()

    def redraw(self) -> None:
        self._state = None
        item = self._plot.getPlotItem()
        self._styler.release_unpinned()
        item.clear()
        self._cursor.readd()  # `clear()` took its line out of the plot
        self._legend.clear()
        self._fits = []
        view, unc = self.look.settings(), self.uncertainty.settings()
        style_legend(self._legend, view)
        self._cursor.set_steps([s.ri for s in self._steps])  # a step is a place the cursor can stand on: its refractive index
        entries: list[LegendEntry] = []
        groups: dict[int, list] = {}  # entry index -> the plot items of that line (for the draw order)
        theme = get_active_theme()
        unit = self._y_label.split("(")[-1].rstrip(")") if "(" in self._y_label else ""
        wl = f", at {self._wavelength_nm:g} nm" if self._wavelength_nm else ""
        item.setLabel("bottom", f"Refractive index (RIU{wl})")
        item.setLabel("left", self._y_label)
        self._drawing, self._labels = True, ("Refractive index (RIU)", self._y_label)  # the wavelength is not part of the quantity: the options stay when it changes
        self.axes.labels_changed()
        self.guides.labels_changed()
        self._drawing = False
        in_set = [s for s in self._steps if s.in_set]
        notes = [self._note] if self._note else []
        if len(in_set) < 2:
            notes.append("Put at least two steps of different solutions into the S_bulk set (S_bulk button, or right-click a step > Use for S_bulk).")
        shown = self._lines[:MAX_LINES]
        if len(self._lines) > MAX_LINES:
            notes.append(f"Showing the first {MAX_LINES} of {len(self._lines)} lines.")
        points = [BulkPoint(s.ref, s.ri, s.ri_u) for s in in_set]
        x_all = [s.ri for s in self._steps]
        if x_all and max(x_all) > min(x_all):
            # The x axis runs from the lowest to the highest RI of the steps drawn (a few percent of room so the end points are not cut in half); the user's own zoom is
            # kept until those RIs change.
            span = (min(x_all), max(x_all))
            if span != self._x_range:
                self._plot.setXRange(*span, padding=0.04)
                self._x_range = span
        for line in shown:
            first, drawn_before = len(item.items), len(entries)
            values = bulk_values(line.numbers, points, self._weighting) if points else None
            fit = None if values is None else values.fit_sem
            self._fits.append((line, values))
            self._draw_points(item, line, view, unc, theme)
            if fit is not None and fit.ok and x_all:
                xs = np.linspace(min(x_all), max(x_all), _BAND_POINTS)
                ys = fit.slope * xs + fit.intercept
                if unc.show:  # the fit line's own uncertainty band follows the Uncertainty toggle
                    band = _band(fit, xs)
                    if np.all(np.isfinite(band)):
                        fill = pg.mkColor(line.color)
                        fill.setAlpha(45)
                        # PlotCurveItem, not PlotDataItem(pen=None): a PlotDataItem without a pen never hands its data to its curve, so the band had an empty path
                        # (nothing drawn, and an empty bounding rectangle at (0, 0) that pulled the autoscale's x axis down to 0).
                        upper, lower = pg.PlotCurveItem(xs, ys + band), pg.PlotCurveItem(xs, ys - band)
                        item.addItem(pg.FillBetweenItem(upper, lower, brush=fill))
                chi = f", χ²red {fit.chi2_red:.2f}" if math.isfinite(fit.chi2_red) else ""
                name = f"{legend_name(line.label, line.short_label, view)}: S_bulk = {fit.slope:.1f} ± {fit.u:.1f} {unit}/RIU{chi}" + ("" if line.current else " (out of date)")
                entries.append(
                    LegendEntry(
                        len(entries), line.short_label or line.label, name, line.color, view.line_width, mean=self._mean_of(line, None), at_cursor=self._mean_of(line, self._cursor.x()),
                        key=line.short_label or line.label, rough=self._scatter_of(line, fit),
                        value_at=lambda ri, line=line: self._mean_of(line, ri),
                    )
                )
                if view.show_lines:
                    item.addItem(pg.PlotCurveItem(xs, ys, pen=pg.mkPen(line.color, width=view.line_width), antialias=True))
            elif fit is not None and fit.reason:
                notes.append(f"{line.label}: {fit.reason}.")
            if len(entries) > drawn_before:
                groups[entries[-1].index] = list(item.items[first:])
        self._apply_order(entries, view, groups)
        self._state = DrawnState(view, unc, entries, groups)
        self.status.setText(" ".join(notes))
        self._guide_lines.set_guides(self.axes.axes().y.guides)
        self._styler.pin_ranges(autoscale_first=True)  # the Plot menu's display range replaces the autoscale

    def _mean_of(self, line: BulkLine, ri: float | None) -> float:
        """A line's number for the legend's Value order: the mean of its step means over every step drawn (`ri` None), or at the steps whose refractive index is `ri` (the cursor's:
        steps of the same solution share one; their means are averaged). NaN when there are none."""
        values = []
        for step in self._steps:
            if step.ref not in line.numbers.refs or (ri is not None and step.ri != ri):
                continue
            values.append(line.numbers.value_at(step.ref).mean)
        return mean_of(values)

    def _scatter_of(self, line: BulkLine, fit) -> float:
        """How far a line's step means sit from its own fitted line (the median absolute residual): what "rough" means for the Auto draw order of this plot."""
        pairs = [(step.ri, line.numbers.value_at(step.ref).mean) for step in self._steps if step.ref in line.numbers.refs]
        return median_abs_residual(pairs, fit.slope, fit.intercept)

    def _apply_order(self, entries: list[LegendEntry], view: SensorgramViewSettings, groups: dict[int, list]) -> None:
        """The legend rows in the order the Legend menu asks for, which line lies over which (the Draw order), and the step cursor (drawn only while the order reads the value at a step)."""
        order = view.legend_order()
        ri = self._cursor.x()
        fill_legend(self._legend, entries, view, step_known=ri is not None)
        stack_lines(groups, entries, view, self.look.manual_order(), step_known=ri is not None)
        self._last_entries = entries
        self.look.lines_changed()  # an open Line order table follows the lines
        self._cursor.show(bool(view.show_cursor and order.needs_step() and ri is not None))
        note = ""
        if order.needs_step():
            if ri is None:
                note = "No step has a refractive index yet: there is no step to read a value at."
            else:
                names = ", ".join(str(s.order) for s in self._steps if s.ri == ri)
                note = f"Order by the value at RI {ri:.5f} (step {names}; steps with the same RI are averaged). Drag the cursor to change it."
        self.look.set_order_note(note)

    def _draw_points(self, item, line: BulkLine, view: SensorgramViewSettings, unc: UncertaintyLook, theme) -> None:
        """Uncertainty and points: filled for the steps in the set, hollow for the others that have an RI. The vertical bar (or the band along the points) is the step mean's SEM or the
        cubes' SD, as chosen; the horizontal bar is the RI's Std from RIs.txt (none for a step whose RI has no Std; it is usually far smaller than the points)."""
        size = view.point_size
        kind = unc.kind
        band_x, band_y, band_e = [], [], []
        for in_set in (True, False):
            xs, ys, errs, x_errs, tips = [], [], [], [], []
            for step in self._steps:
                if step.in_set != in_set or step.ref not in line.numbers.refs:
                    continue
                value = line.numbers.value_at(step.ref)
                if not math.isfinite(value.mean):
                    continue
                spread = value.sd if kind == UNCERTAINTY_SD else value.sem
                xs.append(step.ri)
                ys.append(value.mean)
                errs.append(spread if math.isfinite(spread) else 0.0)
                has_std = math.isfinite(step.ri_u) and step.ri_u > 0
                x_errs.append(step.ri_u if has_std else 0.0)
                ri_text = f"{step.ri:.5f}" + (f" ± {step.ri_u:.5f}" if has_std else "")
                tips.append(f"step {step.order} ({step.name}): {value.mean:.3f} ± {spread:.3f} {kind.upper()} at RI {ri_text}")
            if not xs:
                continue
            x, y, e, xe = np.array(xs), np.array(ys), np.array(errs), np.array(x_errs)
            if unc.show:
                bars_style = unc.style != UNCERTAINTY_BAND
                if bars_style or xe.any():  # in the band style only the RI's horizontal bars stay bars
                    item.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * e if bars_style else 0.0 * e, width=2 * xe, beam=0.0, pen=pg.mkPen(line.color, width=1.0)))
                band_x += xs
                band_y += ys
                band_e += errs
            if view.show_points:
                brush = pg.mkBrush(line.color) if in_set else pg.mkBrush(theme.toolbar_bg)
                scatter = pg.ScatterPlotItem(
                    x=x, y=y, size=size if in_set else size + 1, pen=pg.mkPen(line.color, width=1.6), brush=brush, data=tips, hoverable=True, tip=lambda x, y, data: data,
                )
                item.addItem(scatter)
        if unc.show and unc.style == UNCERTAINTY_BAND and band_x:
            order = np.argsort(band_x, kind="stable")
            bx, by, be = np.array(band_x)[order], np.array(band_y)[order], np.array(band_e)[order]
            add_band(item, bx, by - be, by + be, line.color)

    # -- export ---------------------------------------------------------------------------------------------------------------------

    def export_data(self) -> ExportData | None:
        if not self._fits:
            raise ValueError("Nothing to export: no line has an S_bulk fit.")
        points_rows, fit_rows = [], []
        in_set_refs = [s.ref for s in self._steps if s.in_set]
        for line, values in self._fits:
            for step in self._steps:
                if step.ref not in line.numbers.refs:
                    continue
                value = line.numbers.value_at(step.ref)
                excess = u = None
                if values is not None and values.fit_sem.ok:
                    found = bs.excess(values.fit_sem, {step.ref: 1.0}, {
                        **{r: (float("nan"), float("nan"), line.numbers.value_at(r).mean, line.numbers.value_at(r).sem) for r in values.fit_keys()},
                        step.ref: (step.ri, step.ri_u, value.mean, value.sem),
                    }, values.fit_keys())
                    excess, u = found.value, found.u
                points_rows.append((line.label, step.order, step.name, step.ri, step.ri_u, "yes" if step.in_set else "no", value.mean, value.sem, value.n, excess, u))
            if values is not None:
                fit = values.fit_sem
                fit_rows.append((line.label, fit.slope, fit.u, fit.u_data, fit.chi2_red, fit.intercept, fit.n_points, self._weighting))
        tables = {
            "s_bulk_points": Table(
                ("line", "step", "solution", "ri", "ri_std", "in_s_bulk_set", "mean", "sem", "n", "measured_minus_predicted", "u"), tuple(points_rows),
            ),
            "s_bulk_fits": Table(("line", "s_bulk", "u", "u_data", "chi2_red", "intercept", "n_steps", "weighting"), tuple(fit_rows)),
        }
        return ExportData(tables, {"y": self._y_label, "lines": self.lines_mode(), "uncertainty": self.uncertainty.settings().kind, "s_bulk_steps": len(in_set_refs), "ri_wavelength_nm": self._wavelength_nm})

    # -- window -------------------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._plot.setBackground(theme.toolbar_bg)
        item = self._plot.getPlotItem()
        for name in ("bottom", "left"):
            axis = item.getAxis(name)
            axis.setPen(pg.mkPen(theme.text_muted))
            axis.setTextPen(pg.mkPen(theme.text_muted))
        self.export_button.refresh_theme()
        self._guide_lines.refresh_theme()
        self.guides.refresh_theme()
        self._cursor.refresh_theme()
        self.look.refresh_theme()
        self.uncertainty.refresh_theme()
        self._lines_pick.refresh_theme()
        if self.isVisible():
            self.redraw()

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._store = store
        self.look.restore_ui_state(store)
        self.uncertainty.restore_ui_state(store)
        self._cursor.restore_ui_state(store)
        restore_pick(store, LINES_KEY, self._lines_pick)
        geometry = store.get(GEOMETRY_KEY)
        if isinstance(geometry, str) and geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except (ValueError, UnicodeError):  # a damaged value: the default size and place
                pass

    def _remember_geometry(self) -> None:
        if self._store is not None:
            self._store.set(GEOMETRY_KEY, bytes(self.saveGeometry().toBase64()).decode("ascii"))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.redraw()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._remember_geometry()
        super().closeEvent(event)
        self.closed.emit()


def lines_from_series(series: Sequence[object], refs: Sequence[object]) -> list[BulkLine]:
    """The Sensorgram's drawn lines as plot lines: each carries its mean / SD / n per step (`Series.step_values`, columns = the plan's steps in order)."""
    out = []
    for s in series:
        values = getattr(s, "step_values", None)
        if values is None or len(values.mean) != len(refs):
            continue
        out.append(BulkLine(s.label, s.color, RowNumbers(refs=tuple(refs), n=np.asarray(values.n), mean=np.asarray(values.mean), sd=np.asarray(values.sd)), short_label=getattr(s, "short_label", "")))
    return out
