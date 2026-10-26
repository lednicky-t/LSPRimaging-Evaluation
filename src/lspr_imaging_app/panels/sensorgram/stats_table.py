"""``StepStatsItem``: the mean +/- SD of every plotted line in every experiment step, written on the Sensorgram plot (2026-10-26).

Screen purpose: compare the steps with each other without leaving the plot. One row per line (in the line's colour), one column per step, each cell in the middle of
its step, along the top or the bottom edge of the plot, next to the plan bar. What is written, and how it is shortened to fit a narrow step, is decided by
``plan/stats_table.py`` (pure); the numbers come from ``analysis/step_stats.py``. This file paints them and answers the tooltip. Display only: no gesture.

Like the plan bar it is one item in the plot's ViewBox: x is in data coordinates (it follows pan and zoom), y is glued to the edge, so everything is drawn in device pixels.
**The rows are the legend's rows**: the plot hands this item a source of the legend's row positions (`set_row_source`), read from the laid-out legend, so a cell is on the same
line as the marker and name of its line, whatever the font; without a legend the rows are stacked from the edge. The legend itself is moved and the room made by the plot
(`SensorgramPlot.set_stats_table`). Drawn behind the traces (z -15, in front of the plan bar), clicks and drags pass through to what is below; only the hover is used.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QTransform
from PyQt6.QtWidgets import QApplication

from ...plan.bar_model import POSITION_TOP
from ...plan.stats_table import (
    MIN_CELL_WIDTH_PX,
    StatsColumn,
    StatsTable,
    StatsTableSettings,
    cell_tooltip,
    cell_variants,
    fit_variant,
)
from .device_item import DeviceSpaceItem

Z_VALUE = -15  # in front of the plan bar (-20), behind the bands of an average (-10), the baseline selector (-5) and every trace (0)
_SIDE_MARGIN_PX = 3.0
_FALLBACK_GAP_PX = 3.0  # between stacked rows when there is no legend to follow
_EDGE_MARGIN_PX = 4.0


@dataclass(frozen=True)
class LegendRows:
    """Where the legend's rows are, in scene coordinates: the vertical centre of each row, in the order of the lines, and the right edge of the legend."""

    centers_scene_y: tuple[float, ...]
    right_scene_x: float | None


@dataclass(frozen=True)
class DrawnCell:
    """One cell as it is placed, in device pixels. `text` is empty when nothing fits (the cell still answers the tooltip)."""

    line: int
    column: StatsColumn
    rect: QRectF
    text: str
    color: str
    pt: float  # the text size the whole table is drawn at: the size chosen in the size box


@dataclass(frozen=True)
class _Slot:
    """A number to write, before its text is chosen: where it may go and what it could say."""

    line: int
    column: StatsColumn
    left: float
    right: float
    available: float
    variants: tuple[str, ...]
    color: str


class StepStatsItem(DeviceSpaceItem):
    def __init__(self) -> None:
        super().__init__()
        self._table: StatsTable | None = None
        self._settings = StatsTableSettings()
        self._row_source: Callable[[], LegendRows | None] | None = None
        self.setZValue(Z_VALUE)

    # -- what to draw ---------------------------------------------------------------------------------------------------

    def set_table(self, table: StatsTable | None, settings: StatsTableSettings) -> None:
        self.prepareGeometryChange()
        self._table = table
        self._settings = settings
        self.update()

    def set_row_source(self, source: Callable[[], LegendRows | None] | None) -> None:
        """Where the rows go: a function returning the legend's current row positions (None: it has none). Asked at every paint."""
        self._row_source = source
        self.update()

    def table(self) -> StatsTable | None:
        return self._table

    def settings(self) -> StatsTableSettings:
        return self._settings

    def is_shown(self) -> bool:
        return self._table is not None and self._settings.visible

    def font(self, pt: float | None = None) -> QFont:
        font = QFont(QApplication.font())
        font.setPointSizeF(float(self._settings.text_pt if pt is None else pt))
        return font

    # -- geometry ---------------------------------------------------------------------------------------------------------

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        return self.view_rect() if self.is_shown() else QRectF()

    def _row_centers(self, to_device: QTransform, area: QRectF, text_height: float, count: int) -> tuple[list[float], float]:
        """(device y of the centre of each row, device x of the legend's right edge or -inf). From the legend when it has one row per line, else stacked from the edge."""
        rows = None if self._row_source is None else self._row_source()
        if rows is not None and len(rows.centers_scene_y) == count:
            centers = [to_device.map(self.mapFromScene(QPointF(0.0, y))).y() for y in rows.centers_scene_y]
            right = -math.inf if rows.right_scene_x is None else to_device.map(self.mapFromScene(QPointF(rows.right_scene_x, 0.0))).x()
            return centers, right
        pitch = text_height + _FALLBACK_GAP_PX
        if self._settings.position == POSITION_TOP:
            return [area.top() + _EDGE_MARGIN_PX + (i + 0.5) * pitch for i in range(count)], -math.inf
        return [area.bottom() - _EDGE_MARGIN_PX - (count - i - 0.5) * pitch for i in range(count)], -math.inf

    def layout(self, to_device: QTransform, view: QRectF) -> list[DrawnCell]:
        """Every cell that has a number, with the text that fits its step (data -> device transform, visible data rectangle). No painting."""
        table = self._table
        area = to_device.mapRect(view)
        if table is None or not self._settings.visible or area.isEmpty():
            return []
        centers, legend_right = self._row_centers(to_device, area, QFontMetricsF(self.font()).height(), len(table.lines))
        slots: list[_Slot] = []
        for column in table.columns:
            x0 = to_device.map(QPointF(column.x_start, 0.0)).x()
            x1 = to_device.map(QPointF(column.x_end, 0.0)).x()
            left, right = max(x0, area.left(), legend_right + _SIDE_MARGIN_PX), min(x1, area.right())  # never under the legend
            if right <= left:
                continue
            for index, line in enumerate(table.lines):
                mean, sd, _n = table.value(index, column)
                if math.isfinite(mean):
                    slots.append(_Slot(index, column, left, right, right - left - 2.0 * _SIDE_MARGIN_PX, cell_variants(mean, sd, table.decimals), line.color))
        # The size is the one in the size box, whatever the numbers: a step too narrow for it gets shorter text (`fit_variant`), never a smaller font. (Until 2026-10-26 the whole
        # table shrank to fit its narrowest step, so the same box value drew at 6 pt for one selection and 10 pt for another, depending on the digits and signs of the numbers.)
        pt = float(self._settings.text_pt)
        metrics = QFontMetricsF(self.font(pt))
        height = metrics.height()
        return [
            DrawnCell(
                s.line, s.column, QRectF(s.left, centers[s.line] - height / 2.0, s.right - s.left, height),
                fit_variant(s.variants, s.available, metrics.horizontalAdvance) if s.available >= MIN_CELL_WIDTH_PX else "", s.color, pt,
            )
            for s in slots
        ]

    # -- hover ---------------------------------------------------------------------------------------------------------------

    def _cells_data(self) -> list[tuple[DrawnCell, QRectF]]:
        """The cells in data coordinates, so that they answer the pointer."""
        transform = self._device_transform()
        if transform is None or not self.is_shown():
            return []
        inverse, ok = transform.inverted()
        return [] if not ok else [(c, inverse.mapRect(c.rect).normalized()) for c in self.layout(transform, self.view_rect())]

    def shape(self) -> QPainterPath:
        """Only the cells answer the pointer, not the whole plot the bounding rectangle covers."""
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for _cell, rect in self._cells_data():
            path.addRect(rect)
        return path

    def tooltip_at(self, local: QPointF) -> str:
        table = self._table
        if table is None:
            return ""
        for cell, rect in self._cells_data():
            if rect.contains(local):
                return cell_tooltip(table, cell.line, cell.column)
        return ""

    def hover_at(self, local: QPointF) -> None:
        """The pointer is over the plot at this point (item coordinates): the cell's full numbers as the tooltip."""
        self.setToolTip(self.tooltip_at(local))

    # -- painting (device pixels) ---------------------------------------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802 - Qt naming
        if not self.is_shown():
            return
        view = self.view_rect()
        if view.isEmpty():
            return
        to_device = painter.worldTransform()
        cells = [c for c in self.layout(to_device, view) if c.text]
        if not cells:
            return
        area = to_device.mapRect(view)
        painter.save()
        painter.resetTransform()  # from here on x and y are device pixels
        painter.setClipRect(area)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font(cells[0].pt))  # one size for the whole table
        for cell in cells:
            painter.setPen(QColor(cell.color))
            painter.drawText(cell.rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), cell.text)
        painter.restore()
