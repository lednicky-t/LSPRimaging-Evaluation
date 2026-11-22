"""The LbL plot (2026-11-22): a floating window with the shift of each line against the cycle number (bilayers deposited) of a layer-by-layer experiment.

Screen purpose: see how the resonance moves with every deposited bilayer, and whether the growth is regular. Two views of the same numbers: the **accumulated** shift (total
shift from the start step, a growing curve) and the shift **per cycle** (the change from the previous cycle alone, a falling curve when the layers saturate). A floating window,
not a panel (as the S_bulk plot: a rare check), opened and closed from the Sensorgram's LbL pop-up.

Layout::

    [Lines: Sensorgram lines | All groups v] [Shift: Accumulated | Per cycle v] [Point size: 9 px] [Calculate groups] [save]
    +----------------------------------------------------------+
    |  y: accumulated shift (nm)   or   shift per cycle (nm)     |   per line (its Sensorgram colour / the group colour):
    |      ●──●──●──●  points joined by a line, bars = the           |   one point per cycle, vertical bar = the two step SEMs
    |                  uncertainty of the shift                      |   added in quadrature
    |  x: cycle (0 = the start step, 1 = after the first bilayer ...) |
    +----------------------------------------------------------+
    status line: why something is missing (no series set, lines not calculated, ...)

Display only: the numbers are the ones the Sensorgram's lines carry per step (computed with the curves while this window is open, `StepOverlays.stats_wanted`) or the ROI table's
group rows (`StepTableModule.group_numbers`); the arithmetic is `analysis/lbl_series.py`. The window remembers its size, place, the Lines and Shift choices and the point size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.export_tables import Table
from ...analysis.lbl_series import LblPoint, lbl_points
from ..plot_export_button import ExportData, PlotExportButton
from ..ui_state import UiStateStore
from .bulk_plot import MAX_LINES, BulkLine

SHIFT_ACCUMULATED = "accumulated"
SHIFT_CYCLE = "cycle"
LINES_SENSORGRAM = "sensorgram"
LINES_GROUPS = "groups"
GEOMETRY_KEY = "sensorgram/lbl_plot_geometry"
LINES_KEY = "sensorgram/lbl_plot_lines"
SHIFT_KEY = "sensorgram/lbl_plot_shift"
POINT_SIZE_KEY = "sensorgram/lbl_plot_point_size"
DEFAULT_POINT_SIZE = 9
MAX_TICKED_CYCLES = 30  # up to this many cycles every one is a tick; more: the axis chooses
_SHIFT_NAME = {SHIFT_ACCUMULATED: "Accumulated shift", SHIFT_CYCLE: "Shift per cycle"}


@dataclass(frozen=True)
class LblStep:
    """One cycle of the series as the plan has it: the step's reference, its number on the bar (``order``) and its solution's name."""

    ref: object
    order: int
    name: str


class LblPlotWindow(QWidget):
    """The floating LbL plot. The owner hands it the lines (`set_lines`) and the series' steps (`set_steps`); it asks for group numbers through `calculate_requested`."""

    calculate_requested = pyqtSignal()
    lines_mode_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("LbL series")
        self.resize(560, 420)
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
        bar.addWidget(QLabel("Lines", self))
        self.lines_pick = QComboBox(self)
        self.lines_pick.addItem("Sensorgram lines", LINES_SENSORGRAM)
        self.lines_pick.addItem("All groups", LINES_GROUPS)
        self.lines_pick.setToolTip("Which lines are drawn: the ones on the Sensorgram now, or every ROI group (the ROI table's group rows: each group's averaged line).")
        bar.addWidget(self.lines_pick)
        bar.addWidget(QLabel("Shift", self))
        self.shift_pick = QComboBox(self)
        self.shift_pick.addItem("Accumulated", SHIFT_ACCUMULATED)
        self.shift_pick.addItem("Per cycle", SHIFT_CYCLE)
        self.shift_pick.setToolTip(
            "Accumulated: the total shift from the start step (cycle 0), growing with every bilayer. Per cycle: the shift of each bilayer alone, the change from the previous cycle."
        )
        bar.addWidget(self.shift_pick)
        bar.addWidget(QLabel("Point size", self))
        self.point_size = QSpinBox(self)
        self.point_size.setRange(3, 30)
        self.point_size.setValue(DEFAULT_POINT_SIZE)
        self.point_size.setSuffix(" px")
        self.point_size.setToolTip("Size of the points in the plot.")
        bar.addWidget(self.point_size)
        self.calculate_button = QPushButton("Calculate groups", self)
        self.calculate_button.setToolTip("The groups' step numbers are not calculated yet (or out of date): calculate them, as the ROI table's refresh button does.")
        self.calculate_button.setVisible(False)
        bar.addWidget(self.calculate_button)
        bar.addStretch(1)
        self.export_button = PlotExportButton("lbl_series", lambda: self._plot.getPlotItem(), self.export_data, self)
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
        self.shift_pick.currentIndexChanged.connect(lambda _i: self.redraw() if self.isVisible() else None)
        self.calculate_button.clicked.connect(lambda _checked=False: self.calculate_requested.emit())
        self.point_size.valueChanged.connect(lambda _value: self.redraw() if self.isVisible() else None)
        self.refresh_theme()

    # -- what it shows ------------------------------------------------------------------------------------------------------------

    def lines_mode(self) -> str:
        return self.lines_pick.currentData() or LINES_SENSORGRAM

    def shift_mode(self) -> str:
        return self.shift_pick.currentData() or SHIFT_ACCUMULATED

    def set_steps(self, steps: Sequence[LblStep]) -> None:
        self._steps = list(steps)

    def set_lines(self, lines: Sequence[BulkLine], *, y_label: str, note: str = "", need_calculation: bool = False) -> None:
        """New lines (or none, with `note` saying why); redraws when shown."""
        self._lines, self._y_label, self._note = list(lines), y_label, note
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
        theme = get_active_theme()
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
            points = lbl_points(line.numbers, refs) if refs else []
            self._drawn.append((line, points))
            suffix = "" if line.current else " (out of date)"
            self._draw_line(item, line, points, mode, theme, suffix)
        self.status.setText(" ".join(notes))

    def _draw_line(self, item, line: BulkLine, points: list[LblPoint], mode: str, theme, suffix: str) -> None:
        """One line: the points joined, with a bar of the shift's uncertainty. Cycle 0 has no per-cycle value (it has no cycle before it) and is left out of that view."""
        xs, ys, errs, tips = [], [], [], []
        for p in points:
            value, u = (p.accumulated, p.accumulated_u) if mode == SHIFT_ACCUMULATED else (p.cycle_shift, p.cycle_shift_u)
            if not math.isfinite(value):
                continue
            step = self._steps[p.cycle]
            xs.append(float(p.cycle))
            ys.append(value)
            errs.append(u if math.isfinite(u) else 0.0)
            tips.append(f"cycle {p.cycle} (step {step.order}, {step.name}): {value:.3f}" + (f" ± {u:.3f}" if math.isfinite(u) else ""))
        if not xs:
            return
        x, y, e = np.array(xs), np.array(ys), np.array(errs)
        item.addItem(pg.PlotDataItem(x, y, pen=pg.mkPen(line.color, width=1.6), name=line.label + suffix))
        item.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * e, beam=0.0, pen=pg.mkPen(line.color, width=1.0)))
        item.addItem(
            pg.ScatterPlotItem(
                x=x, y=y, size=self.point_size.value(), pen=pg.mkPen(line.color, width=1.6), brush=pg.mkBrush(line.color), data=tips, hoverable=True, tip=lambda x, y, data: data,
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
                rows.append((line.label, p.cycle, step.order, step.name, p.mean, p.sem, p.n, p.accumulated, p.accumulated_u, p.cycle_shift, p.cycle_shift_u))
        tables = {
            "lbl_points": Table(
                ("line", "cycle", "step", "solution", "mean", "sem", "n", "accumulated_shift", "accumulated_u", "cycle_shift", "cycle_shift_u"), tuple(rows),
            ),
        }
        return ExportData(tables, {"y": self._y_label, "lines": self.lines_mode(), "shift_shown": self.shift_mode(), "cycles": len(self._steps)})

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
        for key, combo in ((LINES_KEY, self.lines_pick), (SHIFT_KEY, self.shift_pick)):
            index = combo.findData(store.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)
        self.lines_pick.currentIndexChanged.connect(lambda _i: store.set(LINES_KEY, self.lines_mode()))
        self.shift_pick.currentIndexChanged.connect(lambda _i: store.set(SHIFT_KEY, self.shift_mode()))
        store.bind(POINT_SIZE_KEY, self.point_size)
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
