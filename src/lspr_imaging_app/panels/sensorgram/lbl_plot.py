"""The LbL plot (2026-11-22): a floating window with the shift of each line against the cycle number (bilayers deposited) of a layer-by-layer experiment.

Screen purpose: see how the resonance moves with every deposited bilayer, and whether the growth is regular. Two views of the same numbers: the **accumulated** shift (total
shift from the start step, a growing curve) and the shift **per cycle** (the change from the previous cycle alone, a falling curve when the layers saturate). A floating window,
not a panel (as the S_bulk plot: a rare check), opened and closed from the Sensorgram's LbL pop-up.

Layout::

    [Lines: (individual) (groups)] | [Shift: (accumulated) (per cycle)] | [Points & lines] [Uncertainty] [Legend] [Calculate groups] [save]
    +----------------------------------------------------------+
    |  y: accumulated shift (nm)   or   shift per cycle (nm)     |   per line (its Sensorgram colour / the group colour):
    |      ●──●──●──●  points joined by a line, bars = the           |   one point per cycle, vertical bar = the two step SEMs
    |                  uncertainty of the shift                      |   added in quadrature
    |  x: cycle (0 = the start step, 1 = after the first bilayer ...) |
    +----------------------------------------------------------+
    status line: why something is missing (no series set, lines not calculated, ...)

Display only: the numbers are the ones the Sensorgram's lines carry per step (computed with the curves while this window is open, `StepOverlays.stats_wanted`) or the ROI table's
group rows (`StepTableModule.group_numbers`); the arithmetic is `analysis/lbl_series.py`. The window remembers its size, place and the Lines and Shift choices.

**Ribbon (2026-11-22).** Lines and Shift are pairs of picture buttons (the Sensorgram's own "individual" and "average by group" pictures; a dashed baseline with strokes up from it for
Accumulated, a staircase with a stroke between neighbouring dots for Per cycle). Points & lines, Uncertainty and Legend are the same menus as the Sensorgram's View tab and the S_bulk
window (`plot_look.py`, one definition for every plot; **each plot keeps its own values**): points and lines on / off, size, width, Straight / Spline, the legend, and the bars or band of the points (SEM or SD of
the steps).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.export_tables import Table
from ...analysis.lbl_series import LblPoint, lbl_points
from ..plot_export_button import ExportData, PlotExportButton
from ..ui_state import UiStateStore
from .bulk_plot import MAX_LINES, BulkLine, _separator
from .curve_smoothing import smooth_run
from ..spectra.pick_menu import PickMenu
from .plot_window_draw import PlotAreaButton, add_band, add_legend_entry, legend_name, make_legend, style_legend
from .view_settings import LINE_SPLINE, UNCERTAINTY_BAND, SensorgramViewSettings, UncertaintyLook
from .plot_look import LINES_SENSORGRAM, WINDOW_LOOK_DEFAULTS, PlotLookControls, PlotLookKeys, UncertaintyControls, UncertaintyKeys, lines_pick_menu, restore_pick
from .window_look_icons import accumulated_picture, cycle_picture

SHIFT_ACCUMULATED = "accumulated"
SHIFT_CYCLE = "cycle"
GEOMETRY_KEY = "sensorgram/lbl_plot_geometry"
LINES_KEY = "sensorgram/lbl_plot_lines"
SHIFT_KEY = "sensorgram/lbl_plot_shift"
SHIFT_LABELS = {SHIFT_ACCUMULATED: "Accumulated", SHIFT_CYCLE: "Per cycle"}
SHIFT_PICTURES = {SHIFT_ACCUMULATED: accumulated_picture(), SHIFT_CYCLE: cycle_picture()}
SHIFT_TIPS = {
    SHIFT_ACCUMULATED: "Accumulated: the total shift from the start step (cycle 0), growing with every bilayer. Every point is measured from the start level.",
    SHIFT_CYCLE: "Per cycle: the shift of each bilayer alone, the change from the previous cycle. Every point is measured from the cycle before it.",
}
MAX_TICKED_CYCLES = 30  # up to this many cycles every one is a tick; more: the axis chooses
_SHIFT_NAME = {SHIFT_ACCUMULATED: "Accumulated shift", SHIFT_CYCLE: "Shift per cycle"}


@dataclass(frozen=True)
class LblStep:
    """One cycle of the series as the plan has it: the step's reference, its number on the bar (``order``) and its solution's name."""

    ref: object
    order: int
    name: str


class LblPlotWindow(QWidget):
    """The floating LbL plot. The owner hands it the lines (`set_lines`) and the series' steps (`set_steps`); it asks for group numbers through `calculate_requested`.
    Its Points & lines, Legend and Uncertainty menus are the shared definitions of `plot_look.py` with this window's own values and remembered keys."""

    calculate_requested = pyqtSignal()
    lines_mode_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("LbL series")
        self.resize(640, 440)
        # This window's own look: the menus are the shared definitions (`plot_look.py`), the values and the remembered keys are this window's.
        self.look = PlotLookControls(self, PlotLookKeys.with_prefix("sensorgram/lbl_plot_look"), WINDOW_LOOK_DEFAULTS, object_prefix="lbl_plot", joined=True)
        self.uncertainty = UncertaintyControls(self, UncertaintyKeys.with_prefix("sensorgram/lbl_plot_look"), object_prefix="lbl_plot")
        self._store: UiStateStore | None = None
        self._lines: list[BulkLine] = []
        self._steps: list[LblStep] = []
        self._y_label = "Step mean (nm)"
        self._note = ""
        self._drawn: list[tuple[BulkLine, list[LblPoint]]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._lines_pick = lines_pick_menu(self)
        bar.addWidget(self._lines_pick)
        bar.addWidget(_separator(self))
        self._shift_pick = PickMenu(self, name_on_face=False)  # as the Sensorgram's Plot tab picks: the chosen picture is the button
        for key in (SHIFT_ACCUMULATED, SHIFT_CYCLE):
            self._shift_pick.add_choice(key, SHIFT_LABELS[key], SHIFT_TIPS[key], SHIFT_PICTURES[key])
        self._shift_pick.finish()
        self._shift_pick.setToolTip("Which shift is plotted.")
        bar.addWidget(self._shift_pick)
        bar.addWidget(_separator(self))
        for menu in (self.look.lines_menu, self.uncertainty.menu, self.look.legend_menu):
            bar.addWidget(menu)
        bar.addStretch(1)
        self.export_button = PlotExportButton("lbl_series", lambda: self._plot.getPlotItem(), self.export_data, self)
        bar.addWidget(self.export_button)
        outer.addLayout(bar)
        self._plot = pg.PlotWidget(parent=self)
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._legend = make_legend(self._plot.getPlotItem())
        outer.addWidget(self._plot, 1)
        self.calculate_button = PlotAreaButton("Calculate groups", self._plot)  # in the plot area, not the row: the plot is empty exactly when it is needed
        self.calculate_button.setToolTip("The groups' step numbers are not calculated yet (or out of date): calculate them, as the ROI table's refresh button does.")
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self._lines_pick.currentIndexChanged.connect(lambda _i: self.lines_mode_changed.emit(self.lines_mode()))
        self._shift_pick.currentIndexChanged.connect(lambda _i: self.redraw() if self.isVisible() else None)
        self.calculate_button.clicked.connect(lambda _checked=False: self.calculate_requested.emit())
        self.look.settings_changed.connect(self._look_changed)
        self.uncertainty.settings_changed.connect(self._look_changed)
        self.refresh_theme()

    # -- what it shows ------------------------------------------------------------------------------------------------------------

    def lines_mode(self) -> str:
        return self._lines_pick.currentData() or LINES_SENSORGRAM

    def set_lines_mode(self, mode: str) -> None:
        """Press the Lines button of `mode` and report it, as a click does (a restore calls `restore_ui_state`, which does not)."""
        index = self._lines_pick.findData(mode)
        if index >= 0:
            self._lines_pick.setCurrentIndex(index)  # reports, as a pick does (a restore does not)

    def shift_mode(self) -> str:
        return self._shift_pick.currentData() or SHIFT_ACCUMULATED

    def set_shift_mode(self, mode: str) -> None:
        """Press the Shift button of `mode` (the plot is redrawn when shown)."""
        index = self._shift_pick.findData(mode)
        if index >= 0:
            self._shift_pick.setCurrentIndex(index)

    def _look_changed(self, *_args: object) -> None:
        if self.isVisible():
            self.redraw()

    def set_steps(self, steps: Sequence[LblStep]) -> None:
        self._steps = list(steps)

    def set_lines(self, lines: Sequence[BulkLine], *, y_label: str, note: str = "", need_calculation: bool = False) -> None:
        """New lines (or none, with `note` saying why); redraws when shown."""
        self._lines, self._y_label, self._note = list(lines), y_label, note
        self.calculate_button.set_centered(not self._lines)
        self.calculate_button.setVisible(need_calculation)
        if self.isVisible():
            self.redraw()

    def _unit(self) -> str:
        return self._y_label.split("(")[-1].rstrip(")") if "(" in self._y_label else ""

    def redraw(self) -> None:
        item = self._plot.getPlotItem()
        item.clear()
        self._legend.clear()
        self._drawn = []
        view, unc = self.look.settings(), self.uncertainty.settings()
        style_legend(self._legend, view)
        mode = self.shift_mode()
        unit = self._unit()
        item.setLabel("bottom", "Cycle (bilayers; 0 = the start step)")
        item.setLabel("left", _SHIFT_NAME[mode] + (f" ({unit})" if unit else ""))
        axis = item.getAxis("bottom")
        axis.setTicks([[(k, str(k)) for k in range(len(self._steps))]] if 0 < len(self._steps) <= MAX_TICKED_CYCLES else None)
        notes = [self._note] if self._note else []
        if len(self._steps) < 2:
            notes.append("Set the start step and the steps per cycle in the LbL pop-up: the series needs at least two cycles inside the plan.")
        shown = self._lines[:MAX_LINES]
        if len(self._lines) > MAX_LINES:
            notes.append(f"Showing the first {MAX_LINES} of {len(self._lines)} lines.")
        refs = [s.ref for s in self._steps]
        for line in shown:
            points = lbl_points(line.numbers, refs, unc.kind) if refs else []
            self._drawn.append((line, points))
            suffix = "" if line.current else " (out of date)"
            self._draw_line(item, line, points, mode, view, unc, suffix)
        self.status.setText(" ".join(notes))

    def _draw_line(self, item, line: BulkLine, points: list[LblPoint], mode: str, view: SensorgramViewSettings, unc: UncertaintyLook, suffix: str) -> None:
        """One line: the points joined, with the uncertainty of the shift as bars or a band. Cycle 0 has no per-cycle value (it has no cycle before it) and is left out of that view."""
        xs, ys, errs, tips = [], [], [], []
        kind = unc.kind.upper()
        for p in points:
            value, u = (p.accumulated, p.accumulated_u) if mode == SHIFT_ACCUMULATED else (p.cycle_shift, p.cycle_shift_u)
            if not math.isfinite(value):
                continue
            step = self._steps[p.cycle]
            xs.append(float(p.cycle))
            ys.append(value)
            errs.append(u if math.isfinite(u) else 0.0)
            tips.append(f"cycle {p.cycle} (step {step.order}, {step.name}): {value:.3f}" + (f" ± {u:.3f} ({kind})" if math.isfinite(u) else ""))
        if not xs:
            return
        x, y, e = np.array(xs), np.array(ys), np.array(errs)
        spline = view.line_style == LINE_SPLINE
        add_legend_entry(self._legend, line.color, view.line_width, legend_name(line.label, line.short_label, view) + suffix)
        if view.show_lines:
            line_x, line_y = smooth_run(x, y) if spline else (x, y)
            item.addItem(pg.PlotCurveItem(line_x, line_y, pen=pg.mkPen(line.color, width=view.line_width), antialias=True))
        if unc.show:
            if unc.style == UNCERTAINTY_BAND:
                add_band(item, x, y - e, y + e, line.color, spline=spline)
            else:
                item.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * e, beam=0.0, pen=pg.mkPen(line.color, width=1.0)))
        if view.show_points:
            item.addItem(
                pg.ScatterPlotItem(
                    x=x, y=y, size=view.point_size, pen=pg.mkPen(line.color, width=1.6), brush=pg.mkBrush(line.color), data=tips, hoverable=True, tip=lambda x, y, data: data,
                )
            )

    # -- export ---------------------------------------------------------------------------------------------------------------------

    def export_data(self) -> ExportData | None:
        if not self._drawn or not any(points for _line, points in self._drawn):
            raise ValueError("Nothing to export: no line has LbL steps.")
        rows = []
        for line, points in self._drawn:
            for p in points:
                step = self._steps[p.cycle]
                rows.append((line.label, p.cycle, step.order, step.name, p.mean, p.sem, p.sd, p.n, p.accumulated, p.accumulated_u, p.cycle_shift, p.cycle_shift_u))
        tables = {
            "lbl_points": Table(
                ("line", "cycle", "step", "solution", "mean", "sem", "sd", "n", "accumulated_shift", "accumulated_u", "cycle_shift", "cycle_shift_u"), tuple(rows),
            ),
        }
        return ExportData(tables, {"y": self._y_label, "lines": self.lines_mode(), "shift_shown": self.shift_mode(), "uncertainty": self.uncertainty.settings().kind, "cycles": len(self._steps)})

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
        self.look.refresh_theme()
        self.uncertainty.refresh_theme()
        self._lines_pick.refresh_theme()
        self._shift_pick.refresh_theme()
        if self.isVisible():
            self.redraw()

    def restore_ui_state(self, store: UiStateStore) -> None:
        self._store = store
        self.look.restore_ui_state(store)
        self.uncertainty.restore_ui_state(store)
        restore_pick(store, LINES_KEY, self._lines_pick)
        restore_pick(store, SHIFT_KEY, self._shift_pick)
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
