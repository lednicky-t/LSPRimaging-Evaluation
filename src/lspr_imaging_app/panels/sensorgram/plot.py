"""Sensorgram plot widget - the pyqtgraph-facing half of the Sensorgram panel (sketch §10 "sensorgram/panel.py + plot.py").

Draws what it is handed: a line per series (the metric against time or cube number), a band for averaged series, hollow markers where the
scan is out of date, mask marks (a triangle: a persistent mask touched the point's ROIs, a circle: an individual one did), a vertical line at the current cube and an optional draggable baseline window. It computes nothing and reads no module.
A left click inside the plot emits ``x_clicked`` with the x value (the panel turns it into "go to that cube"); dragging the circle on the x axis
(the Cube slider's handle, in the same colour) emits it too, for every pointer position.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_trace import Series
from ..axis_cursor import AxisCursor
from ..image.mark_colors import MASK_COLOR, cube_color

MAX_LEGEND_SERIES = 6
_BAND_ALPHA = 55
_WINDOW_BRUSH = (120, 160, 255, 40)


def _finite_runs(*arrays: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) of the stretches where every array is finite: a band is drawn per stretch (a gap stays a gap)."""
    ok = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        ok &= np.isfinite(a)
    runs, start = [], None
    for i, flag in enumerate(ok):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(ok)))
    return [(a, b) for a, b in runs if b - a >= 2]


class _MiddlePanViewBox(pg.ViewBox):
    """pyqtgraph pans with the left button by default; here only the middle button pans (the left one is left free for clicking a cube)."""

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 - pyqtgraph naming
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)


class SensorgramPlot(QWidget):
    x_clicked = pyqtSignal(float)
    baseline_window_changed = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self, viewBox=_MiddlePanViewBox())
        self._widget.setMinimumHeight(100)
        self._item = self._widget.getPlotItem()
        self._item.showGrid(x=True, y=True, alpha=0.15)
        self._legend: pg.LegendItem | None = None
        self._items: list[pg.GraphicsObject] = []
        self._axis_cursor = AxisCursor(self._item, cube_color(), self)
        self._axis_cursor.dragged.connect(self.x_clicked)
        self._cursor = self._axis_cursor.line
        self._window = pg.LinearRegionItem(values=(0.0, 1.0), brush=pg.mkBrush(*_WINDOW_BRUSH), pen=pg.mkPen(120, 160, 255, 160), movable=True)
        self._window.setZValue(-5)
        self._window.sigRegionChangeFinished.connect(self._on_window_finished)
        self._window_in_scene = False
        self._setting_window = False
        self._item.scene().sigMouseClicked.connect(self._on_scene_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.set_axis_labels("Cube (#)", "Peak position (nm)")
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """pyqtgraph draws its own canvas and ignores Qt stylesheets: called at construction and on every live theme switch."""
        theme = get_active_theme()
        self._widget.setBackground(theme.toolbar_bg)
        self._axis_cursor.set_color(cube_color())
        for name in ("bottom", "left"):
            axis = self._item.getAxis(name)
            axis.setPen(pg.mkPen(theme.text_muted))
            axis.setTextPen(pg.mkPen(theme.text_muted))

    def set_axis_labels(self, x_label: str, y_label: str) -> None:
        self._item.setLabel("bottom", x_label)
        self._item.setLabel("left", y_label)

    # -- drawing -----------------------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        for item in self._items:
            self._item.removeItem(item)
        self._items.clear()
        if self._legend is not None:
            self._legend.clear()

    def show_series(
        self, series: Sequence[Series], stale: np.ndarray | None = None, mask_marks: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None
    ) -> None:
        """``stale``: bool per point (same length as the series' x); those points get a hollow marker on top: out-of-date results are still drawn.
        ``mask_marks``: series key -> (persistent, individual), bool per point; a persistent mask gets a hollow triangle (like a play button),
        an individual one a hollow circle, both in the mask colour (None draws none)."""
        self.clear()
        if len(series) <= MAX_LEGEND_SERIES:
            if self._legend is None:
                self._legend = self._item.addLegend(offset=(8, 8))
        elif self._legend is not None:
            self._legend.clear()
        for s in series:
            color = QColor(s.color)
            line = pg.PlotDataItem(
                s.x, s.y, pen=pg.mkPen(color, width=1.5), symbol="o", symbolSize=4, symbolBrush=pg.mkBrush(color), symbolPen=None,
                connect="finite", name=s.label if len(series) <= MAX_LEGEND_SERIES else None,
            )
            self._add(line)
            if s.band_low is not None and s.band_high is not None:
                brush = QColor(color)
                brush.setAlpha(_BAND_ALPHA)
                for a, b in _finite_runs(s.x, s.band_low, s.band_high):
                    low = pg.PlotDataItem(s.x[a:b], s.band_low[a:b], pen=None)
                    high = pg.PlotDataItem(s.x[a:b], s.band_high[a:b], pen=None)
                    fill = pg.FillBetweenItem(low, high, brush=pg.mkBrush(brush))
                    fill.setZValue(-10)
                    self._add(fill)
            if stale is not None and stale.any():
                mask = np.asarray(stale, dtype=bool) & np.isfinite(s.y) & np.isfinite(s.x)
                if mask.any():
                    hollow = pg.ScatterPlotItem(s.x[mask], s.y[mask], symbol="o", size=9, brush=None, pen=pg.mkPen(color, width=1.5))
                    self._add(hollow)
            marks = None if mask_marks is None else mask_marks.get(s.key)
            if marks is not None:
                finite = np.isfinite(s.y) & np.isfinite(s.x)
                for flags, symbol, size in ((marks[0], "t2", 11), (marks[1], "o", 13)):
                    on = np.asarray(flags, dtype=bool) & finite
                    if on.any():
                        self._add(pg.ScatterPlotItem(s.x[on], s.y[on], symbol=symbol, size=size, brush=None, pen=pg.mkPen(MASK_COLOR, width=1.8)))
        self._item.getViewBox().autoRange()

    def _add(self, item: pg.GraphicsObject) -> None:
        self._item.addItem(item)
        self._items.append(item)

    # -- the cursor and the baseline window -------------------------------------------------------------------------------------------

    def set_current_x(self, x: float | None) -> None:
        """The dashed line and the slider-style circle at the current cube (None hides them)."""
        self._axis_cursor.set_x(x)

    def set_baseline_window(self, window: tuple[float, float] | None) -> None:
        """Show the baseline window (low, high) in x units, or hide it (None). Setting it here never emits ``baseline_window_changed``."""
        if window is None:
            if self._window_in_scene:
                self._item.removeItem(self._window)
                self._window_in_scene = False
            return
        if not self._window_in_scene:
            self._item.addItem(self._window, ignoreBounds=True)
            self._window_in_scene = True
        self._setting_window = True
        try:
            self._window.setRegion(window)
        finally:
            self._setting_window = False

    def baseline_window(self) -> tuple[float, float] | None:
        if not self._window_in_scene:
            return None
        low, high = self._window.getRegion()
        return float(low), float(high)

    def x_range(self) -> tuple[float, float]:
        low, high = self._item.getViewBox().viewRange()[0]
        return float(low), float(high)

    def _on_window_finished(self) -> None:
        if self._setting_window:
            return
        low, high = self._window.getRegion()
        self.baseline_window_changed.emit(float(low), float(high))

    def _on_scene_clicked(self, event) -> None:
        """A plain left click on the plot area: report the x it landed on. Clicks another item handled (the baseline band) are not ours."""
        if event.button() != Qt.MouseButton.LeftButton or event.double() or event.isAccepted():
            return
        view_box = self._item.getViewBox()
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        self.x_clicked.emit(float(view_box.mapSceneToView(event.scenePos()).x()))
