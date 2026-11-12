"""The S_bulk plot (2026-11-12): a floating window with the step means against the refractive index, the fitted bulk line and the steps that do not follow it.

Screen purpose: see the fit behind the S_bulk numbers: is it a straight line, which step is off, and how far a step outside the set (e.g. a protein) sits from the line
(the ROI table's measured − predicted, drawn). A floating window, not a panel (maintainer, 2026-11-12: a rare check), opened and closed from the Sensorgram's S_bulk pop-up.

Layout::

    [Lines: Sensorgram lines | All groups v]  [Calculate]  [save]       <- Calculate only when groups are asked for and not calculated yet
    +----------------------------------------------------------+
    |  y: step mean (nm), or shift with Subtract on              |      per line (its Sensorgram colour / the group colour):
    |      ●──────── fitted line, light band = its uncertainty   |      filled points = steps in the S_bulk set, error bars = SEM
    |   ○  (a step not in the set, with an RI)                   |      hollow points = other steps with an RI
    |  x: refractive index (RIU, at 589.3 nm)                    |
    +----------------------------------------------------------+
    legend: name  S_bulk = 254.3 ± 3.1 nm/RIU  (χ²red 1.2)
    status line: why something is missing (no S_bulk set, no RIs, groups not calculated, steps left out for no RI)

Display only: the numbers are the ones the Sensorgram's lines carry per step (computed with the curves while this window is open, `StepOverlays.stats_wanted`) or the
ROI table's group rows (`StepTableModule.group_numbers`); the fit is `step_table.bulk_values` / `bulk_sensitivity.excess`, the same as the table's S_bulk column. Nothing
is fitted on the GUI thread beyond those few-point lines (microseconds). The window remembers its size, place and the Lines choice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis import bulk_sensitivity as bs
from ...analysis.export_tables import Table
from ...analysis.step_table import BulkPoint, RowNumbers, bulk_values
from ..plot_export_button import ExportData, PlotExportButton
from ..ui_state import UiStateStore

LINES_SENSORGRAM = "sensorgram"
LINES_GROUPS = "groups"
GEOMETRY_KEY = "sensorgram/bulk_plot_geometry"
LINES_KEY = "sensorgram/bulk_plot_lines"
MAX_LINES = 12  # more lines than this make an unreadable plot; the rest are named in the status line
_BAND_POINTS = 60


@dataclass(frozen=True)
class BulkLine:
    """One line of the plot: its name, colour and step numbers (refs in the plan's step order)."""

    label: str
    color: str
    numbers: RowNumbers
    current: bool = True


@dataclass(frozen=True)
class PlotStep:
    ref: object
    order: int
    name: str
    ri: float
    ri_u: float
    in_set: bool


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
    through `calculate_requested`."""

    calculate_requested = pyqtSignal()
    lines_mode_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Bulk sensitivity")
        self.resize(560, 420)
        self._store: UiStateStore | None = None
        self._lines: list[BulkLine] = []
        self._steps: list[PlotStep] = []
        self._weighting = bs.WEIGHT_INVERSE_VARIANCE
        self._y_label = "Step mean (nm)"
        self._x_range: tuple[float, float] | None = None  # the RI range last set, so a zoom of the user's is kept until the RIs change
        self._wavelength_nm: float | None = None
        self._note = ""
        self._fits: list[tuple[BulkLine, object]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Lines", self))
        self.lines_pick = QComboBox(self)
        self.lines_pick.addItem("Sensorgram lines", LINES_SENSORGRAM)
        self.lines_pick.addItem("All groups", LINES_GROUPS)
        self.lines_pick.setToolTip("Which lines are fitted: the ones drawn on the Sensorgram now, or every ROI group (the ROI table's group rows: each group's averaged line).")
        bar.addWidget(self.lines_pick)
        self.calculate_button = QPushButton("Calculate groups", self)
        self.calculate_button.setToolTip("The groups' step numbers are not calculated yet (or out of date): calculate them, as the ROI table's refresh button does.")
        self.calculate_button.setVisible(False)
        bar.addWidget(self.calculate_button)
        bar.addStretch(1)
        self.export_button = PlotExportButton("s_bulk", lambda: self._plot.getPlotItem(), self.export_data, self)
        bar.addWidget(self.export_button)
        outer.addLayout(bar)
        self._plot = pg.PlotWidget(parent=self)
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._legend = self._plot.getPlotItem().addLegend(offset=(8, 8))
        outer.addWidget(self._plot, 1)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.lines_pick.currentIndexChanged.connect(lambda _i: self.lines_mode_changed.emit(self.lines_mode()))
        self.calculate_button.clicked.connect(lambda _checked=False: self.calculate_requested.emit())
        self.refresh_theme()

    # -- what it shows ------------------------------------------------------------------------------------------------------------

    def lines_mode(self) -> str:
        return self.lines_pick.currentData() or LINES_SENSORGRAM

    def set_steps(self, steps: Sequence[PlotStep], wavelength_nm: float | None) -> None:
        self._steps, self._wavelength_nm = list(steps), wavelength_nm

    def set_weighting(self, weighting: str) -> None:
        self._weighting = weighting

    def set_lines(self, lines: Sequence[BulkLine], *, y_label: str, note: str = "", need_calculation: bool = False) -> None:
        """New lines (or none, with `note` saying why); redraws when shown."""
        self._lines, self._y_label, self._note = list(lines), y_label, note
        self.calculate_button.setVisible(need_calculation)
        if self.isVisible():
            self.redraw()

    def redraw(self) -> None:
        item = self._plot.getPlotItem()
        item.clear()
        self._legend.clear()
        self._fits = []
        theme = get_active_theme()
        unit = self._y_label.split("(")[-1].rstrip(")") if "(" in self._y_label else ""
        wl = f", at {self._wavelength_nm:g} nm" if self._wavelength_nm else ""
        item.setLabel("bottom", f"Refractive index (RIU{wl})")
        item.setLabel("left", self._y_label)
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
            values = bulk_values(line.numbers, points, self._weighting) if points else None
            fit = None if values is None else values.fit_sem
            self._fits.append((line, values))
            self._draw_points(item, line, theme)
            if fit is not None and fit.ok and x_all:
                xs = np.linspace(min(x_all), max(x_all), _BAND_POINTS)
                ys = fit.slope * xs + fit.intercept
                band = _band(fit, xs)
                if np.all(np.isfinite(band)):
                    fill = pg.mkColor(line.color)
                    fill.setAlpha(45)
                    upper, lower = pg.PlotDataItem(xs, ys + band, pen=None), pg.PlotDataItem(xs, ys - band, pen=None)
                    item.addItem(pg.FillBetweenItem(upper, lower, brush=fill))
                chi = f", χ²red {fit.chi2_red:.2f}" if math.isfinite(fit.chi2_red) else ""
                name = f"{line.label}: S_bulk = {fit.slope:.1f} ± {fit.u:.1f} {unit}/RIU{chi}" + ("" if line.current else " (out of date)")
                item.addItem(pg.PlotDataItem(xs, ys, pen=pg.mkPen(line.color, width=1.6), name=name))
            elif fit is not None and fit.reason:
                notes.append(f"{line.label}: {fit.reason}.")
        self.status.setText(" ".join(notes))

    def _draw_points(self, item, line: BulkLine, theme) -> None:
        """Error bars (SEM) and points: filled for the steps in the set, hollow for the others that have an RI."""
        for in_set in (True, False):
            xs, ys, errs, tips = [], [], [], []
            for step in self._steps:
                if step.in_set != in_set or step.ref not in line.numbers.refs:
                    continue
                value = line.numbers.value_at(step.ref)
                if not math.isfinite(value.mean):
                    continue
                xs.append(step.ri)
                ys.append(value.mean)
                errs.append(value.sem if math.isfinite(value.sem) else 0.0)
                tips.append(f"step {step.order} ({step.name}): {value.mean:.3f} ± {value.sem:.3f} at RI {step.ri:.5f}")
            if not xs:
                continue
            x, y, e = np.array(xs), np.array(ys), np.array(errs)
            item.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * e, beam=0.0, pen=pg.mkPen(line.color, width=1.0)))
            brush = pg.mkBrush(line.color) if in_set else pg.mkBrush(theme.toolbar_bg)
            scatter = pg.ScatterPlotItem(x=x, y=y, size=9 if in_set else 10, pen=pg.mkPen(line.color, width=1.6), brush=brush, data=tips, hoverable=True, tip=lambda x, y, data: data)
            item.addItem(scatter)

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
        return ExportData(tables, {"y": self._y_label, "lines": self.lines_mode(), "s_bulk_steps": len(in_set_refs), "ri_wavelength_nm": self._wavelength_nm})

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
        if self.isVisible():
            self.redraw()

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._store = store
        mode = store.get(LINES_KEY)
        index = self.lines_pick.findData(mode)
        if index >= 0:
            self.lines_pick.setCurrentIndex(index)
        self.lines_pick.currentIndexChanged.connect(lambda _i: store.set(LINES_KEY, self.lines_mode()))
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
        out.append(BulkLine(s.label, s.color, RowNumbers(refs=tuple(refs), n=np.asarray(values.n), mean=np.asarray(values.mean), sd=np.asarray(values.sd))))
    return out
