"""Spectra plot widget - the pyqtgraph-facing half of the Spectra panel (sketch §10 "spectra/panel.py + plot.py").

Draws what it is handed and nothing else: per ROI the measured points (optionally with error bars), the fitted curve and the metric marker,
and an optional draggable fit window. It computes nothing (no fit, no formula: ``analysis/scan_view.py`` did that on a worker thread) and
reads no module. A spectrum that is out of date is drawn dimmed, not hidden.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_view import RoiSpectrum

Y_LABELS = {
    "absorbance": "Absorbance (A)",
    "ratio": "Sample / reference",
    "relative_change": "(Reference - sample) / reference",
    "mod_absorbance": "Modified absorbance (mA)",
}
MAX_LEGEND_ROIS = 6
_DIM_ALPHA = 90
_WINDOW_BRUSH = (120, 160, 255, 40)


class SpectraPlot(QWidget):
    """One plot of several ROIs' spectra. ``fit_window_changed(low, high)`` is emitted when the user lets go of the dragged window."""

    fit_window_changed = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self)
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.refresh_theme()

    # -- theme -----------------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        """pyqtgraph draws its own canvas and ignores Qt stylesheets: called at construction and on every live theme switch."""
        theme = get_active_theme()
        self._widget.setBackground(theme.toolbar_bg)
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

    # -- the fit window ---------------------------------------------------------------------------------------------------------

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

    def _on_window_finished(self) -> None:
        if self._setting_window:
            return
        low, high = self._window.getRegion()
        self.fit_window_changed.emit(float(low), float(high))
