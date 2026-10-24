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
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_view import RoiSpectrum
from ..axis_cursor import AxisCursor
from ..image.mark_colors import wavelength_color

Y_LABELS = {
    "absorbance": "Absorbance (A)",
    "ratio": "Sample / reference",
    "relative_change": "(Reference - sample) / reference",
    "mod_absorbance": "Modified absorbance (mA)",
}
MAX_LEGEND_ROIS = 6
_DIM_ALPHA = 90
_WINDOW_BRUSH = (120, 160, 255, 40)


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
        self._items: list[pg.GraphicsObject] = []
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

    def clear(self) -> None:
        for item in self._items:
            self._item.removeItem(item)
        self._items.clear()
        if self._legend is not None:
            self._legend.clear()

    def set_formula(self, formula_key: str) -> None:
        self._item.setLabel("left", Y_LABELS.get(formula_key, formula_key))

    def show_spectra(
        self, entries: Sequence[tuple[str, str, RoiSpectrum]], *, error_bars: bool, dimmed: bool
    ) -> None:
        """``entries``: (legend name, colour hex, spectrum) per ROI. ``dimmed``: the results are out of date (drawn faint, still readable)."""
        self.clear()
        alpha = _DIM_ALPHA if dimmed else 255
        if len(entries) <= MAX_LEGEND_ROIS:
            if self._legend is None:
                self._legend = self._item.addLegend(offset=(8, 8))
        elif self._legend is not None:
            self._legend.clear()
        for name, color_hex, spectrum in entries:
            if spectrum.values.size == 0:
                continue
            color = QColor(color_hex)
            color.setAlpha(alpha)
            line = QColor(color)
            line.setAlpha(min(alpha, 150))
            legend_name = name if len(entries) <= MAX_LEGEND_ROIS else None
            points = pg.PlotDataItem(
                spectrum.wavelengths_nm, spectrum.values, pen=pg.mkPen(line, width=1), symbol="o", symbolSize=6,
                symbolBrush=pg.mkBrush(color), symbolPen=None, name=legend_name,
            )
            self._item.addItem(points)
            self._items.append(points)
            if error_bars:
                errors = np.where(np.isfinite(spectrum.errors), spectrum.errors, 0.0)
                bars = pg.ErrorBarItem(x=spectrum.wavelengths_nm, y=spectrum.values, height=2.0 * errors, beam=0.0, pen=pg.mkPen(color, width=1))
                self._item.addItem(bars)
                self._items.append(bars)
            if spectrum.fit_values is not None:
                fit = pg.PlotDataItem(spectrum.fit_wavelengths_nm, spectrum.fit_values, pen=pg.mkPen(color, width=2))
                self._item.addItem(fit)
                self._items.append(fit)
            if spectrum.metric_wavelength_nm is not None and spectrum.metric_value is not None:
                marker = pg.ScatterPlotItem(
                    [spectrum.metric_wavelength_nm], [spectrum.metric_value], symbol="d", size=11, brush=pg.mkBrush(color),
                    pen=pg.mkPen(255, 255, 255, alpha),
                )
                self._item.addItem(marker)
                self._items.append(marker)
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
