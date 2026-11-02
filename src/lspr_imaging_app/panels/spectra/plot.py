"""Spectra plot widget - the pyqtgraph-facing half of the Spectra panel (sketch §10 "spectra/panel.py + plot.py").

Draws what it is handed and nothing else: per ROI the measured points (optionally with error bars), the fitted curve and the metric marker,
an optional draggable fit window and a cursor at the current wavelength (a dashed line and, on the x axis, the Wavelength slider's circle in
its colour; dragging the circle or the line, or clicking anywhere else in the plot, emits ``wavelength_dragged``). It computes nothing (no fit, no formula: ``analysis/scan_view.py`` did that on a worker thread) and
reads no module. A spectrum that is out of date is drawn dimmed, not hidden.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsItem, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_view import RoiSpectrum
from ..axis_cursor import AxisCursor
from ..image.mark_colors import wavelength_color
from ..plot_item_pool import PlotItemPool

Y_LABELS = {
    "absorbance": "Absorbance (A)",
    "ratio": "Sample / reference",
    "relative_change": "(Reference - sample) / reference",
    "mod_absorbance": "Modified absorbance (mA)",
}
MAX_LEGEND_ROIS = 6
_DIM_ALPHA = 90
_WINDOW_BRUSH = (120, 160, 255, 40)
LINE_STYLES = ("solid", "dash", "dot", "dashdot")  # the styles of the line that joins the measured points (the View menu's picker)
_PEN_STYLES = {
    "solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine, "dashdot": Qt.PenStyle.DashDotLine,
}
_LINE_WIDTH = {"solid": 1.0, "dash": 1.5, "dot": 1.5, "dashdot": 1.5}  # a broken line at one pixel is hard to read
FIT_WIDTH = 2.0  # px, the fitted curve while few ROIs are drawn
FIT_WIDE_MAX_ROIS = 10  # with more ROIs the fitted curves are 1 px antialiased: Qt paints only 1 px lines fast (2 px was ~10x slower for 160 ROIs, 2026-11-02)
DEFAULT_POINT_SIZE = 6
POINT_SIZE_RANGE = (2, 20)


class _NoLeftPanViewBox(pg.ViewBox):
    """Left-drag on empty plot does nothing (a left click sets the wavelength); right-drag zoom, middle-drag pan and the wheel still work."""

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)


class SpectraPlot(QWidget):
    """One plot of several ROIs' spectra. ``fit_window_changed(low, high)`` is emitted when the user lets go of the dragged window."""

    fit_window_changed = pyqtSignal(float, float)
    wavelength_dragged = pyqtSignal(float)  # nm under the pointer while the cursor circle is dragged (the panel snaps it to a scanned wavelength)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self, viewBox=_NoLeftPanViewBox())
        self._widget.setMinimumHeight(100)
        self._item = self._widget.getPlotItem()
        self._item.showGrid(x=True, y=True, alpha=0.15)
        self._item.setLabel("bottom", "Wavelength (nm)")
        self._item.setLabel("left", Y_LABELS["absorbance"])
        self._legend: pg.LegendItem | None = None
        self._pool = PlotItemPool(self._item)
        self._show_points, self._point_size = True, DEFAULT_POINT_SIZE
        self._show_lines, self._line_style = True, "solid"
        self._window = pg.LinearRegionItem(values=(0.0, 1.0), brush=pg.mkBrush(*_WINDOW_BRUSH), pen=pg.mkPen(120, 160, 255, 160), movable=True)
        self._window.setZValue(-5)
        self._window.sigRegionChangeFinished.connect(self._on_window_finished)
        self._window_in_scene = False
        self._setting_window = False
        self._axis_cursor = AxisCursor(self._item, wavelength_color(), self)
        self._axis_cursor.dragged.connect(self.wavelength_dragged)
        self._item.scene().sigMouseClicked.connect(self._on_scene_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        for name in ("bottom", "left"):  # kept as pictures by Qt: the moving wavelength cursor no longer repaints the axes' text (2026-11-02)
            self._item.getAxis(name).setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.refresh_theme()

    # -- theme -----------------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        """pyqtgraph draws its own canvas and ignores Qt stylesheets: called at construction and on every live theme switch."""
        theme = get_active_theme()
        self._widget.setBackground(theme.toolbar_bg)
        self._axis_cursor.set_color(wavelength_color())
        for name in ("bottom", "left"):
            axis = self._item.getAxis(name)
            axis.setPen(pg.mkPen(theme.text_muted))
            axis.setTextPen(pg.mkPen(theme.text_muted))

    # -- drawing -----------------------------------------------------------------------------------------------------------------

    @property
    def _items(self) -> list[pg.GraphicsObject]:
        """The items drawn now: per ROI the points (and line), the error bars and the fitted curve (each when drawn), then one item with every metric marker."""
        return self._pool.drawn

    def clear(self) -> None:
        self._pool.clear()
        if self._legend is not None:
            self._legend.clear()

    def set_formula(self, formula_key: str) -> None:
        self._item.setLabel("left", Y_LABELS.get(formula_key, formula_key))

    def set_display(self, *, points: bool, point_size: int, lines: bool, line_style: str) -> None:
        """How the measured points are drawn at the next ``show_spectra``: the dots (and their size in px) and the line that joins them (and its style, one of ``LINE_STYLES``).
        Display only; the fitted curve, the metric marker and the error bars are not affected. Nothing is redrawn here."""
        self._show_points = bool(points)
        self._point_size = int(min(max(point_size, POINT_SIZE_RANGE[0]), POINT_SIZE_RANGE[1]))
        self._show_lines = bool(lines)
        self._line_style = line_style if line_style in _PEN_STYLES else "solid"

    def show_spectra(
        self, entries: Sequence[tuple[str, str, RoiSpectrum]], *, error_bars: bool, dimmed: bool
    ) -> None:
        """``entries``: (legend name, colour hex, spectrum) per ROI. ``dimmed``: the results are out of date (drawn faint, still readable).

        The items are reused between redraws (`PlotItemPool`): creating and adding ~4 items per ROI took ~2 s on the GUI thread for 160 ROIs
        (2026-11-02, ``docs/spectra_sensorgram_draw_cost_2026-11-02.md``)."""
        pool = self._pool
        pool.begin()
        if self._legend is not None:
            self._legend.clear()
        alpha = _DIM_ALPHA if dimmed else 255
        fit_width = FIT_WIDTH if len(entries) <= FIT_WIDE_MAX_ROIS else 1.0
        markers: list[tuple[float, float, object]] = []
        if len(entries) <= MAX_LEGEND_ROIS and self._legend is None:
            self._legend = self._item.addLegend(offset=(8, 8))
        for name, color_hex, spectrum in entries:
            if spectrum.values.size == 0:
                continue
            color = QColor(color_hex)
            color.setAlpha(alpha)
            line = QColor(color)
            line.setAlpha(min(alpha, 150))
            legend_name = name if len(entries) <= MAX_LEGEND_ROIS else None
            data_drawn = self._show_points or self._show_lines  # with both off the legend entry belongs to the fitted curve (below)
            pen = pg.mkPen(line, width=_LINE_WIDTH[self._line_style], style=_PEN_STYLES[self._line_style]) if self._show_lines else pg.mkPen(None)
            points = pool.take("points", pg.PlotDataItem)
            points.setData(
                spectrum.wavelengths_nm, spectrum.values, pen=pen,
                symbol="o" if self._show_points else None, symbolSize=self._point_size, symbolBrush=pg.mkBrush(color), symbolPen=None,
                name=legend_name if data_drawn else None,
            )
            if legend_name is not None and data_drawn:
                self._legend.addItem(points, legend_name)
            if error_bars:
                errors = np.where(np.isfinite(spectrum.errors), spectrum.errors, 0.0)
                bars = pool.take("bars", lambda: pg.ErrorBarItem(x=np.empty(0), y=np.empty(0), beam=0.0))
                bars.setData(x=spectrum.wavelengths_nm, y=spectrum.values, height=2.0 * errors, beam=0.0, pen=pg.mkPen(color, width=1))
            if spectrum.fit_values is not None:
                fit = pool.take("fit", pg.PlotCurveItem)
                fit.setData(
                    spectrum.fit_wavelengths_nm, spectrum.fit_values, pen=pg.mkPen(color, width=fit_width), antialias=fit_width <= 1.0,
                    name=None if data_drawn else legend_name,
                )
                if legend_name is not None and not data_drawn:
                    self._legend.addItem(fit, legend_name)
            if spectrum.metric_wavelength_nm is not None and spectrum.metric_value is not None:
                markers.append((spectrum.metric_wavelength_nm, spectrum.metric_value, pg.mkBrush(color)))
        if markers:  # one item for every ROI's marker, on top (one item per ROI cost ~0.4 ms each in pyqtgraph's bookkeeping)
            marker = pool.take("marker", pg.ScatterPlotItem, z=1.0)
            marker.setData(
                [m[0] for m in markers], [m[1] for m in markers], symbol="d", size=11, brush=[m[2] for m in markers], pen=pg.mkPen(255, 255, 255, alpha),
            )
        pool.end()
        self._item.getViewBox().autoRange()

    # -- the cursor and the fit window ---------------------------------------------------------------------------------------------------------

    def set_current_wavelength(self, wavelength_nm: float | None) -> None:
        """The dashed line and the slider-style circle at the wavelength shown on the Image panel (None hides them)."""
        self._axis_cursor.set_x(wavelength_nm)

    def set_fit_window(self, window: tuple[float, float] | None) -> None:
        """Show the window (low, high) in nm, or hide it (None). Setting it here never emits ``fit_window_changed``."""
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

    def fit_window(self) -> tuple[float, float] | None:
        if not self._window_in_scene:
            return None
        low, high = self._window.getRegion()
        return float(low), float(high)

    def x_range(self) -> tuple[float, float]:
        low, high = self._item.getViewBox().viewRange()[0]
        return float(low), float(high)

    def _on_scene_clicked(self, event) -> None:
        """A plain left click on the plot area puts the cursor there. Clicks another item handled (the fit window, the cursor) are not ours."""
        if event.button() != Qt.MouseButton.LeftButton or event.double() or event.isAccepted():
            return
        view_box = self._item.getViewBox()
        if view_box.sceneBoundingRect().contains(event.scenePos()):
            self.wavelength_dragged.emit(float(view_box.mapSceneToView(event.scenePos()).x()))

    def _on_window_finished(self) -> None:
        if self._setting_window:
            return
        low, high = self._window.getRegion()
        self.fit_window_changed.emit(float(low), float(high))
