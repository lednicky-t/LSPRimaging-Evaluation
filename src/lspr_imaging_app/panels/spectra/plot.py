"""Spectra plot widget - the pyqtgraph-facing half of the Spectra panel (sketch §10 "spectra/panel.py + plot.py").

Draws what it is handed and nothing else: per ROI the measured points (optionally with error bars), the fitted curve and the metric marker,
an optional draggable fit window and a cursor at the current wavelength (a dashed line and, on the x axis, the Wavelength slider's circle in
its colour; dragging the circle or the line, or clicking anywhere else in the plot, emits ``wavelength_dragged``). It computes nothing (no fit, no formula: ``analysis/scan_view.py`` did that on a worker thread) and
reads no module. A spectrum that is out of date is drawn dimmed, not hidden.

**x axis in nm or eV** (2026-11-02): a double-click on the x axis label switches between wavelength (nm) and photon energy (eV = 1239.84198 / nm),
display only. Everything the panel hands over and gets back stays in nm (spectra, fit window, cursor, clicks); only what is drawn is converted.
``x_unit_changed`` tells the panel, which remembers the choice.
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
from ...analysis.voigt_fit import HC_EV_NM
from ..axis_cursor import AxisCursor
from ..image.mark_colors import wavelength_color
from ..plot_item_pool import PlotItemPool
from ..svg_layers import SvgPart

Y_LABELS = {
    "absorbance": "Absorbance (A)",
    "ratio": "Sample / reference",
    "relative_change": "(Reference - sample) / reference",
    "mod_absorbance": "Modified absorbance (mA)",
}
X_UNITS = ("nm", "ev")
X_LABELS = {"nm": "Wavelength (nm)", "ev": "Photon energy (eV)"}
_LABEL_HIT_MARGIN = 4.0  # pixels around the x axis label that still count as a double-click on it
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
    x_unit_changed = pyqtSignal(str)  # "nm" | "ev", after a double-click on the x axis label

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self, viewBox=_NoLeftPanViewBox())
        self._widget.setMinimumHeight(100)
        self._item = self._widget.getPlotItem()
        self._item.showGrid(x=True, y=True, alpha=0.15)
        self._unit = "nm"
        self._item.setLabel("bottom", X_LABELS["nm"])
        self._item.getAxis("bottom").setToolTip("Double-click the label to switch between wavelength (nm) and photon energy (eV).")
        self._last_draw: tuple | None = None  # (entries, error_bars, dimmed) of the last show_spectra: drawn again on a unit switch
        self._window_nm: tuple[float, float] | None = None
        self._cursor_nm: float | None = None
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
        self._axis_cursor.dragged.connect(lambda x: self.wavelength_dragged.emit(self._to_nm(x)))
        self._item.scene().sigMouseClicked.connect(self._on_scene_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        for name in ("bottom", "left"):  # kept as pictures by Qt: the moving wavelength cursor no longer repaints the axes' text (2026-11-02)
            self._item.getAxis(name).setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.refresh_theme()

    # -- theme -----------------------------------------------------------------------------------------------------------------

    def export_parts(self) -> list[SvgPart]:
        """What each drawn item is, for a layered SVG (`svg_layers.py`): per ROI its spectrum, error bars and fitted curve; the metric markers, the
        fit window and the wavelength cursor. Axes and legend are named there."""
        parts = list(getattr(self, "_svg_parts", []))
        parts += [SvgPart("Cursor", "Wavelength cursor line", self._axis_cursor.line), SvgPart("Cursor", "Wavelength cursor handle", self._axis_cursor.handle)]
        if self._window_in_scene:
            parts.append(SvgPart("Fit window", "Fit window", self._window))
        return parts

    def plot_item(self) -> pg.PlotItem:
        """The pyqtgraph plot (for the export button: figures are drawn from it)."""
        return self._item

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

    # -- the x unit ------------------------------------------------------------------------------------------------------------------

    def x_unit(self) -> str:
        return self._unit

    def set_x_unit(self, unit: str) -> None:
        """Draw the x axis in ``unit`` ("nm" or "ev"; anything else is nm). Redraws what is shown; emits nothing."""
        unit = unit if unit in X_UNITS else "nm"
        if unit == self._unit:
            return
        self._unit = unit
        self._item.setLabel("bottom", X_LABELS[unit])
        if self._last_draw is not None:
            self.show_spectra(self._last_draw[0], error_bars=self._last_draw[1], dimmed=self._last_draw[2])
        self.set_fit_window(self._window_nm)
        self.set_current_wavelength(self._cursor_nm)

    def _axis(self, wavelength_nm):
        """nm -> what the x axis shows (array in, array out)."""
        values = np.asarray(wavelength_nm, dtype=np.float64)
        if self._unit == "ev":
            with np.errstate(divide="ignore"):
                return HC_EV_NM / values
        return values

    def _to_nm(self, x: float) -> float:
        """What the x axis shows -> nm (the same formula: the conversion is its own inverse)."""
        return float(HC_EV_NM / x) if self._unit == "ev" and x != 0.0 else float(x)

    def _on_x_label(self, scene_pos) -> bool:
        label = self._item.getAxis("bottom").label
        rect = label.sceneBoundingRect().adjusted(-_LABEL_HIT_MARGIN, -_LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN)
        return rect.contains(scene_pos)

    def toggle_x_unit(self) -> None:
        """nm <-> eV, as the double-click on the axis label does: redraw, show the whole range, tell the panel."""
        self.set_x_unit("ev" if self._unit == "nm" else "nm")
        self._item.getViewBox().autoRange()
        self.x_unit_changed.emit(self._unit)

    # -- drawing (continued) -------------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        self._last_draw = None
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
        self._last_draw = (entries, error_bars, dimmed)
        self._svg_parts: list[SvgPart] = []  # names for a layered SVG export (svg_layers.py)
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
                self._axis(spectrum.wavelengths_nm), spectrum.values, pen=pen,
                symbol="o" if self._show_points else None, symbolSize=self._point_size, symbolBrush=pg.mkBrush(color), symbolPen=None,
                name=legend_name if data_drawn else None,
            )
            self._svg_parts.append(SvgPart("Spectra", name, points))
            if legend_name is not None and data_drawn:
                self._legend.addItem(points, legend_name)
            if error_bars:
                errors = np.where(np.isfinite(spectrum.errors), spectrum.errors, 0.0)
                bars = pool.take("bars", lambda: pg.ErrorBarItem(x=np.empty(0), y=np.empty(0), beam=0.0))
                self._svg_parts.append(SvgPart("Error bars", name, bars))
                bars.setData(x=self._axis(spectrum.wavelengths_nm), y=spectrum.values, height=2.0 * errors, beam=0.0, pen=pg.mkPen(color, width=1))
            if spectrum.fit_values is not None:
                fit = pool.take("fit", pg.PlotCurveItem)
                self._svg_parts.append(SvgPart("Fits", name, fit))
                fit.setData(
                    self._axis(spectrum.fit_wavelengths_nm), spectrum.fit_values, pen=pg.mkPen(color, width=fit_width), antialias=fit_width <= 1.0,
                    name=None if data_drawn else legend_name,
                )
                if legend_name is not None and not data_drawn:
                    self._legend.addItem(fit, legend_name)
            if spectrum.metric_wavelength_nm is not None and spectrum.metric_value is not None:
                markers.append((float(self._axis(spectrum.metric_wavelength_nm)), spectrum.metric_value, pg.mkBrush(color)))
        if markers:  # one item for every ROI's marker, on top (one item per ROI cost ~0.4 ms each in pyqtgraph's bookkeeping)
            marker = pool.take("marker", pg.ScatterPlotItem, z=1.0)
            self._svg_parts.append(SvgPart("Metric markers", "Markers", marker))
            marker.setData(
                [m[0] for m in markers], [m[1] for m in markers], symbol="d", size=11, brush=[m[2] for m in markers], pen=pg.mkPen(255, 255, 255, alpha),
            )
        pool.end()
        self._item.getViewBox().autoRange()

    # -- the cursor and the fit window ---------------------------------------------------------------------------------------------------------

    def set_current_wavelength(self, wavelength_nm: float | None) -> None:
        """The dashed line and the slider-style circle at the wavelength shown on the Image panel (None hides them)."""
        self._cursor_nm = wavelength_nm
        self._axis_cursor.set_x(None if wavelength_nm is None else float(self._axis(wavelength_nm)))

    def set_fit_window(self, window: tuple[float, float] | None) -> None:
        """Show the window (low, high) in nm, or hide it (None). Setting it here never emits ``fit_window_changed``."""
        self._window_nm = None if window is None else (float(window[0]), float(window[1]))
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
            low, high = sorted(float(v) for v in self._axis(np.asarray(window, dtype=np.float64)))  # in eV the order turns round
            self._window.setRegion((low, high))
        finally:
            self._setting_window = False

    def fit_window(self) -> tuple[float, float] | None:
        """The window as shown, in nm (low, high)."""
        if not self._window_in_scene:
            return None
        low, high = sorted(self._to_nm(float(v)) for v in self._window.getRegion())
        return low, high

    def x_range(self) -> tuple[float, float]:
        """The visible x range, in nm (low, high)."""
        low, high = sorted(self._to_nm(float(v)) for v in self._item.getViewBox().viewRange()[0])
        return low, high

    def _on_scene_clicked(self, event) -> None:
        """A plain left click on the plot area puts the cursor there; a double-click on the x axis label switches nm / eV. Clicks another item
        handled (the fit window, the cursor) are not ours."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.double():
            if self._on_x_label(event.scenePos()):
                self.toggle_x_unit()
            return
        if event.isAccepted():
            return
        view_box = self._item.getViewBox()
        if view_box.sceneBoundingRect().contains(event.scenePos()):
            self.wavelength_dragged.emit(self._to_nm(float(view_box.mapSceneToView(event.scenePos()).x())))

    def _on_window_finished(self) -> None:
        if self._setting_window:
            return
        low, high = self.fit_window()
        self._window_nm = (low, high)
        self.fit_window_changed.emit(low, high)
