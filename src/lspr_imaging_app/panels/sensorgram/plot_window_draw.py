"""Drawing pieces the LbL and S_bulk plot windows share (2026-11-22): the legend, the shaded uncertainty band and the look settings they follow. Display only.

The windows draw from the shared look (`SharedPlotLook`: a `SensorgramViewSettings` and an `UncertaintyLook`); what is common to both is here so they cannot drift apart:

- ``make_legend`` / ``style_legend`` / ``add_legend_entry``: the legend is the Sensorgram's own class (`plot._Legend`, sized to exactly what its rows need), its text size and row gap are the
  View tab's, "Hide" hides it, and an entry is a sample line that is **not in the plot** (so a hidden line still has its colour in the legend, as in the Sensorgram).
- ``add_band``: a translucent band between two curves along the points, optionally on the smooth (PCHIP) curve.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsItem, QPushButton, QWidget

from ..axis_cursor import AxisCursor
from ..image.mark_colors import cube_color
from .curve_smoothing import smooth_run
from .draw_order import back_to_front
from .legend_order import SORT_VALUE, VALUE_MEAN, order_rows
from .line_order_dialog import LineRow
from .plot import _Legend
from .view_settings import LEGEND_HIDE, SensorgramViewSettings, UncertaintyLook, legend_text

BAND_ALPHA = 45
_INVISIBLE_PEN = pg.mkPen(QColor(0, 0, 0, 0))  # a curve that only gives the view its extent
LEGEND_OFFSET = (8, 8)
_CURSOR_Z = 1000.0
_STACK_STEP = 0.01  # the z between two lines of a window plot: the lines' items are at 0, 0.01, 0.02 ... (the step cursor is far above, `StepCursor.readd`)


def make_legend(item: pg.PlotItem) -> _Legend:
    """The plot item's legend (an `_Legend`, registered as the item's own so a named curve would join it too)."""
    legend = _Legend(offset=LEGEND_OFFSET)
    legend.setParentItem(item.vb)
    item.legend = legend
    return legend


def style_legend(legend: _Legend, view: SensorgramViewSettings) -> None:
    """The View tab's legend options: the text size (entries added afterwards take it), the row gap, the corner of the plot area it sits in and whether it is drawn. Call after `clear()`,
    before adding entries. The corner is applied only when it changed, so a legend the user dragged stays where it was dropped through every redraw."""
    legend.place_in_corner(view.legend_corner, *LEGEND_OFFSET)
    legend.setLabelTextSize(f"{view.legend_text_pt}pt")
    legend.layout.setVerticalSpacing(float(view.legend_spacing_px))
    legend.setVisible(view.legend_mode != LEGEND_HIDE)


def legend_name(label: str, short_label: str, view: SensorgramViewSettings) -> str:
    """A line's name in the legend: with or without its number of ROIs ("Group A (n=12)" / "Group A")."""
    return legend_text(label, short_label, view.legend_counts)


def add_legend_entry(legend: _Legend, color: str, width: float, text: str) -> None:
    """One entry: a short line in `color` and `text`. The sample is a curve of its own, not an item of the plot."""
    legend.addItem(pg.PlotCurveItem(pen=pg.mkPen(color, width=width)), text)


@dataclass(frozen=True)
class LegendEntry:
    """One line's legend row before it is placed: `index` is the line's place in the plot's own order (the Group order), `name` is what Name sorts by, `text` is what is written,
    `mean` / `at_cursor` are the two numbers Value can sort by (NaN: the line has none). `key` is the line's name in the Manual draw order's list, `rough` its roughness for Auto."""

    index: int
    name: str
    text: str
    color: str
    width: float
    mean: float = math.nan
    at_cursor: float = math.nan
    key: str = ""
    rough: float = 0.0
    # The line's number at a cursor position (`at_cursor` is its value at the position the cursor had when the line was drawn): lets a moved cursor re-order the rows without drawing again.
    value_at: Callable[[float | None], float] | None = field(default=None, compare=False, repr=False)


def ordered_entries(entries: Sequence[LegendEntry], view: SensorgramViewSettings, *, step_known: bool) -> list[LegendEntry]:
    """The entries in the order the Legend menu asks for (`legend_order.py`), whatever order they come in (the plot's own order is `index`). `step_known`: the plot has a step to read
    `at_cursor` at; without one, a Value order by step is the default order (the plot says why)."""
    own = sorted(entries, key=lambda e: e.index)
    order = view.legend_order()
    values = None
    if order.key == SORT_VALUE and (order.value == VALUE_MEAN or step_known):
        values = [e.mean if order.value == VALUE_MEAN else e.at_cursor for e in own]
    return [own[position] for position in order_rows([e.name for e in own], values, order)]


def fill_legend(legend: _Legend, entries: Sequence[LegendEntry], view: SensorgramViewSettings, *, step_known: bool) -> None:
    """Write the entries into the (cleared, styled) legend in the order the view asks for, with one layout pass (`_Legend.add_entries`)."""
    legend.add_entries([(pg.PlotCurveItem(pen=pg.mkPen(e.color, width=e.width)), e.text) for e in ordered_entries(entries, view, step_known=step_known)])


def stack_lines(
    groups: Mapping[int, Sequence[QGraphicsItem]], entries: Sequence[LegendEntry], view: SensorgramViewSettings, manual: Sequence[str], *, step_known: bool
) -> list[int]:
    """Put the lines in the draw order of the view (`draw_order.py`: Auto / Legend / Manual): every item of a line (`groups[entry.index]`: its curve, bars, band, points) gets the z of the
    line's place, the line at the back the lowest. Items of one z stay in the order they were added, so a line's own items keep their stacking. Returns the entries' indices, back to front."""
    own = sorted(entries, key=lambda e: e.index)
    legend = ordered_entries(own, view, step_known=step_known)
    place = {e.index: i for i, e in enumerate(own)}
    back = back_to_front(
        [e.key for e in own], view.draw_mode,
        legend_order=[place[e.index] for e in legend], roughness_of=[e.rough for e in own], manual=manual,
    )
    for rank, position in enumerate(back):
        z = _STACK_STEP * rank
        for item in groups.get(own[position].index, ()):
            if item.zValue() != z:
                item.setZValue(z)
    return [own[position].index for position in back]


def rows_of(entries: Sequence[LegendEntry]) -> list[LineRow]:
    """The Line order table's rows for these entries, in the plot's own order."""
    return [LineRow(e.key, e.name, e.color) for e in sorted(entries, key=lambda e: e.index)]


def median_abs_residual(pairs: Sequence[tuple[float, float]], slope: float, intercept: float) -> float:
    """The median absolute distance of the points (x, y) from the line y = slope * x + intercept; 0 when there is nothing to judge. For a plot whose lines are fits (S_bulk), a line
    whose points sit far from its fit is the rough one."""
    return median_abs([y - (slope * x + intercept) for x, y in pairs if math.isfinite(x) and math.isfinite(y)])


def median_abs(values: Sequence[float]) -> float:
    """The median of the absolute finite values; 0 when there are none (a line with nothing to judge is smooth)."""
    finite = [abs(float(v)) for v in values if math.isfinite(float(v))]
    return float(np.median(finite)) if finite else 0.0


def cursor_value_at(xs: Sequence[float], ys: Sequence[float], x: float | None) -> float:
    """The y of the point at exactly `x` (the cursor snaps to a point's x, so no interpolation); NaN when there is none."""
    if x is None:
        return math.nan
    for px, py in zip(xs, ys):
        if px == x:
            return float(py)
    return math.nan


def mean_of(values: Sequence[float]) -> float:
    """The mean of the finite values; NaN when there are none."""
    finite = [float(v) for v in values if math.isfinite(float(v))]
    return sum(finite) / len(finite) if finite else math.nan


class StepCursor(QObject):
    """The cursor of the LbL and S_bulk plots (2026-11-22): the Image panel's own cursor (`AxisCursor`: dashed line + circle on the x axis, the Cube slider's gold) that **jumps from one
    step to the next** (a cycle in the LbL plot, a step's refractive index in the S_bulk plot), because "the value at a step" has to be a measured one. It picks the step the Legend's
    order reads when it sorts by the value At step. Drawn only while that order is on and the plot's Cursor toggle is on; the place is remembered (`key`)."""

    moved = pyqtSignal()

    def __init__(self, plot_item: pg.PlotItem, key: str, parent: QObject) -> None:
        super().__init__(parent)
        self._item = plot_item
        self._key = key
        self._store = None
        self._steps: list[float] = []
        self._x: float | None = None
        self._cursor = AxisCursor(plot_item, cube_color(), self)
        self._cursor.dragged.connect(self._dragged)

    def x(self) -> float | None:
        """The x of the step the cursor stands on (None: the plot has no steps)."""
        return self._x

    def restore_ui_state(self, store) -> None:
        self._store = store
        saved = store.get(self._key)
        if isinstance(saved, (int, float)) and not isinstance(saved, bool):
            self._x = self._nearest(float(saved)) if self._steps else float(saved)

    def readd(self) -> None:
        """Put the cursor's line back into the plot after `PlotItem.clear()` took every item out."""
        self._cursor.line.setZValue(_CURSOR_Z)
        self._item.addItem(self._cursor.line, ignoreBounds=True)

    def set_steps(self, xs: Sequence[float]) -> None:
        """The x of every step the cursor may stand on. It stays where it is if that is still one; else it goes to the nearest, or to the last step when it had no place yet."""
        self._steps = sorted({float(x) for x in xs if math.isfinite(float(x))})
        if not self._steps:
            self._x = None
        elif self._x is None:
            self._x = self._steps[-1]
        elif self._x not in self._steps:
            self._x = self._nearest(self._x)

    def show(self, shown: bool) -> None:
        self._cursor.set_hidden(not shown)
        self._cursor.set_x(self._x)

    def refresh_theme(self) -> None:
        self._cursor.set_color(cube_color())

    def _nearest(self, x: float) -> float:
        return min(self._steps, key=lambda s: abs(s - x))

    def _dragged(self, x: float) -> None:
        if not self._steps:
            return
        snapped = self._nearest(x)
        if snapped == self._x:
            return
        self._x = snapped
        self._cursor.set_x(snapped)
        if self._store is not None:
            self._store.set(self._key, snapped)
        self.moved.emit()


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


@dataclass(frozen=True)
class DrawnState:
    """What a plot window drew last (2026-11-22): the look it drew with, its legend entries and, per entry, the plot items of that line. With it a change that only re-orders (the legend's order,
    the Draw order, the legend's corner, the cursor: `SensorgramViewSettings.differs_only_in_order`) puts the rows and the stacking in the new order **without drawing the lines again**
    (~80-120 ms -> ~30 ms). A window sets it at the end of `redraw` and drops it (None) whenever its lines, steps or weighting change."""

    view: SensorgramViewSettings
    unc: UncertaintyLook  # drawn with (any change of it redraws)
    entries: list[LegendEntry]
    groups: dict[int, list]

    def can_reorder(self, view: SensorgramViewSettings, unc: UncertaintyLook) -> bool:
        return unc == self.unc and view.differs_only_in_order(self.view)

    def reordered(self, view: SensorgramViewSettings, cursor_x: float | None) -> DrawnState:
        """The same drawing under `view`, the entries' number at the cursor read at `cursor_x` (the cursor may have moved since the draw)."""
        entries = [replace(e, at_cursor=e.value_at(cursor_x)) if e.value_at is not None else e for e in self.entries]
        return replace(self, view=view, entries=entries)
