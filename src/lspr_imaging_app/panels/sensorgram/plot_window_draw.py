"""Drawing pieces the LbL and S_bulk plot windows share (2026-11-22): the legend, the shaded uncertainty band and the look settings they follow. Display only.

The windows draw from the shared look (`SharedPlotLook`: a `SensorgramViewSettings` and an `UncertaintyLook`); what is common to both is here so they cannot drift apart:

- ``make_legend`` / ``style_legend`` / ``add_legend_entry``: the legend is the Sensorgram's own class (`plot._Legend`, sized to exactly what its rows need), its text size and row gap are the
  View tab's, "Hide" hides it, and an entry is a sample line that is **not in the plot** (so a hidden line still has its colour in the legend, as in the Sensorgram).
- ``add_band``: a translucent band between two curves along the points, optionally on the smooth (PCHIP) curve.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QPushButton, QWidget

from .curve_smoothing import smooth_run
from .plot import _Legend
from .view_settings import LEGEND_HIDE, SensorgramViewSettings, legend_text

BAND_ALPHA = 45
_INVISIBLE_PEN = pg.mkPen(QColor(0, 0, 0, 0))  # a curve that only gives the view its extent
LEGEND_OFFSET = (8, 8)


def make_legend(item: pg.PlotItem) -> _Legend:
    """The plot item's legend (an `_Legend`, registered as the item's own so a named curve would join it too)."""
    legend = _Legend(offset=LEGEND_OFFSET)
    legend.setParentItem(item.vb)
    item.legend = legend
    return legend


def style_legend(legend: _Legend, view: SensorgramViewSettings) -> None:
    """The View tab's legend options: the text size (entries added afterwards take it), the row gap and whether it is drawn. Call after `clear()`, before adding entries."""
    legend.setLabelTextSize(f"{view.legend_text_pt}pt")
    legend.layout.setVerticalSpacing(float(view.legend_spacing_px))
    legend.setVisible(view.legend_mode != LEGEND_HIDE)


def legend_name(label: str, short_label: str, view: SensorgramViewSettings) -> str:
    """A line's name in the legend: with or without its number of ROIs ("Group A (n=12)" / "Group A")."""
    return legend_text(label, short_label, view.legend_counts)


def add_legend_entry(legend: _Legend, color: str, width: float, text: str) -> None:
    """One entry: a short line in `color` and `text`. The sample is a curve of its own, not an item of the plot."""
    legend.addItem(pg.PlotCurveItem(pen=pg.mkPen(color, width=width)), text)


def add_band(item: pg.PlotItem, x: np.ndarray, low: np.ndarray, high: np.ndarray, color: str, *, spline: bool = False) -> None:
    """A translucent band between `low` and `high` along `x` (strictly increasing). The two edges are invisible curves that frame the view; the fill itself stays out of the
    auto-range (its own bounding rectangle can reach far outside the band: the Sensorgram's band has the same rule)."""
    if x.size < 2:
        return
    band_x, lo = (smooth_run(x, low) if spline else (x, low))
    hi = smooth_run(x, high)[1] if spline else high
    low_curve, high_curve = pg.PlotDataItem(band_x, lo, pen=_INVISIBLE_PEN), pg.PlotDataItem(band_x, hi, pen=_INVISIBLE_PEN)
    fill_color = QColor(color)
    fill_color.setAlpha(BAND_ALPHA)
    item.addItem(low_curve)
    item.addItem(high_curve)
    item.addItem(pg.FillBetweenItem(low_curve, high_curve, brush=pg.mkBrush(fill_color)), ignoreBounds=True)


class PlotAreaButton(QPushButton):
    """A button that sits **in the plot area** (a child of the plot widget), e.g. "Calculate groups" when the numbers the plot needs are not there. In the middle of an empty plot, in its
    top right corner when there is something drawn (so it does not cover the data). It follows the plot's size."""

    def __init__(self, text: str, plot: QWidget) -> None:
        super().__init__(text, plot)
        self._plot = plot
        self._centered = True
        self.setVisible(False)
        plot.installEventFilter(self)

    def set_centered(self, centered: bool) -> None:
        """True: the middle of the plot (nothing is drawn); False: the top right corner."""
        self._centered = bool(centered)
        self.place()

    def place(self) -> None:
        self.adjustSize()
        area = self._plot.rect()
        if self._centered:
            self.move((area.width() - self.width()) // 2, (area.height() - self.height()) // 2)
        else:
            self.move(area.width() - self.width() - 10, 10)
        self.raise_()

    def setVisible(self, visible: bool) -> None:
        super().setVisible(visible)
        if visible:
            self.place()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._plot and event.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        return False
