"""The x axis with breaks, its move handles and the edit-mode spans (2026-11-27). Display only.

Screen purpose: draw the Sensorgram's x axis the way a figure with a broken axis is drawn: the labels count the data (not the drawn position), each break is a small gap on the
axis line with the usual `//` marks, and a small handle with the same mark stands on the axis under each gap and slides the break along the data.

Main user actions:
- **Look**: `BrokenAxisItem` is a `pg.AxisItem` that draws its ticks per drawn stretch (`BreakMap.segments`), writes the data value on each tick, and cuts its line with two slanted strokes
  at each gap. With no break it is a plain axis (pyqtgraph's own ticks, nothing extra drawn).
- **Move a break**: drag the break mark itself, the gap with its `//` on the axis. There is no handle drawn: `BreakHandle` is an invisible grab area over the gap (pointer: horizontal
  arrows), which shows the marks in the accent colour while the pointer is over it, and while it is dragged also a dashed line up through the plot, so the new place is seen before it is
  laid out. A drag reports the distance dragged, in drawn units, which for a gap is the same as in data units (moving a break neither changes the length kept nor the gap):
  `break_dragged(index, delta)` while it goes and `break_drag_finished(index)` on release. The plot moves only this ghost while the mouse is down and lays the data out again on release.
- **Edit mode** (`BreakRegions`): the plot shows the axis continuous again and each break as a shaded span (`pg.LinearRegionItem`) whose two edges and body can be dragged; a right-click
  on a span removes it (the plot hands such right-clicks to `claims_scene_pos` first: pyqtgraph gives them to the view otherwise). `edited(breaks)` fires when a drag ends.

Neither the handles nor the spans are part of a figure: they do not draw while a figure is exported (pyqtgraph tells every item, `_exportOpts`), and the `//` marks are part of the axis, so
they are exported with it.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen

from lspr_ui import get_active_theme

from ...domain.axis_breaks import AxisBreak, BreakMap

_MARK_HALF_HEIGHT = 5.0  # the `//` strokes: half their height, in pixels
_MARK_SLANT = 3.0  # and half their horizontal reach
_GRAB = 3.0  # the grab area reaches this far (pixels) beyond the marks
_MIN_GRAB_HALF_WIDTH = 8.0  # a gap narrower than this is still this easy to catch
REGION_ALPHA = 55


class BrokenAxisItem(pg.AxisItem):
    """A bottom axis that can carry breaks (`set_map`); with none it behaves as pyqtgraph's own."""

    break_dragged = pyqtSignal(int, float)  # (break index, distance dragged in drawn units)
    break_drag_finished = pyqtSignal(int)

    def __init__(self, orientation: str = "bottom", **kwargs) -> None:
        super().__init__(orientation, **kwargs)
        self._map = BreakMap()
        self._background = QColor(get_active_theme().toolbar_bg)
        self._handles: list[BreakHandle] = []
        self._handles_shown = True
        self._zero = 0.0  # the data value printed as 0 (the x offset / time zero of the Plot menu)

    # -- the zero ---------------------------------------------------------------------------------------------------------------------

    def zero(self) -> float:
        return self._zero

    def set_zero(self, zero: float | None) -> None:
        """Count the labels from this data value (None: the data's own numbers). Ticks stand on multiples of the step *from the zero*; nothing else moves."""
        zero = 0.0 if zero is None else float(zero)
        if zero != self._zero:
            self._zero = zero
            self.picture = None
            self.update()

    # -- the breaks -------------------------------------------------------------------------------------------------------------------

    def break_map(self) -> BreakMap:
        return self._map

    def set_map(self, break_map: BreakMap) -> None:
        """Draw the axis with these breaks (`BreakMap()` for none); the handles follow."""
        self._map = break_map
        self.picture = None
        self.update()
        self._sync_handles()

    def set_background(self, color: QColor) -> None:
        """The plot's background colour: the axis line is cut across a gap with a stroke of it."""
        self._background = QColor(color)
        self.picture = None
        self.update()

    def set_handles_shown(self, shown: bool) -> None:
        self._handles_shown = bool(shown)
        self._sync_handles()

    def handles(self) -> list[BreakHandle]:
        return list(self._handles)

    def _sync_handles(self) -> None:
        wanted = len(self._map.breaks) if self._handles_shown and self._map.active else 0
        while len(self._handles) > wanted:
            handle = self._handles.pop()
            handle.setParentItem(None)
            if handle.scene() is not None:
                handle.scene().removeItem(handle)
        while len(self._handles) < wanted:
            handle = BreakHandle(len(self._handles))
            handle.dragged.connect(self.break_dragged)
            handle.finished.connect(self.break_drag_finished)
            handle.setParentItem(self)
            self._handles.append(handle)
        view = self.linkedView()
        for index, handle in enumerate(self._handles):
            handle.attach(view)
            handle.set_span(*self._map.gaps()[index])

    # -- ticks: per drawn stretch, with the data's own numbers -----------------------------------------------------------------------

    def tickValues(self, minVal, maxVal, size):
        """pyqtgraph's ticks, computed in the numbers the axis prints (counted from the zero), at their place in the data; with breaks, per drawn stretch."""
        bm, zero = self._map, self._zero
        if not bm.active:
            if zero == 0.0:
                return super().tickValues(minVal, maxVal, size)
            return [(spacing, [v + zero for v in ticks]) for spacing, ticks in super().tickValues(float(minVal) - zero, float(maxVal) - zero, size)]
        low, high = sorted((float(minVal), float(maxVal)))
        data_low, data_high = float(bm.inverse(low)), float(bm.inverse(high))
        span = high - low
        pieces: list[tuple[float, float, float, float]] = []  # (stretch low, stretch high, visible part low, visible part high), data units
        kept_length, kept_drawn = 0.0, 0.0
        for seg_low, seg_high in bm.segments():
            a, b = max(seg_low, data_low), min(seg_high, data_high)
            if b <= a:
                continue
            pieces.append((seg_low, seg_high, a, b))
            kept_length += b - a
            kept_drawn += float(bm.forward(b)) - float(bm.forward(a))
        if not pieces or kept_length <= 0.0:
            return []
        # One spacing for the whole axis, chosen as for a continuous axis of the data that are kept (their length, the pixels they have): a break must not change the tick density, and a
        # narrow stretch must not get ticks of its own spacing (2026-11-27: the labels of a narrow stretch ran into each other while a wide one had one).
        kept_size = max(size * kept_drawn / span if span > 0 else size, 1.0)
        levels = super().tickValues(0.0, kept_length, kept_size)
        values: list[list[float]] = []
        emitted: list[float] = []  # data values of the finer levels must not repeat a coarser level's tick
        for spacing, _ticks in levels:
            level_values: list[float] = []
            slack = 1e-9 * max(spacing, 1e-12)
            for seg_low, seg_high, a, b in pieces:
                first = int(np.ceil((a - zero) / spacing - 1e-9))
                last = int(np.floor((b - zero) / spacing + 1e-9))
                for k in range(first, last + 1):
                    v = zero + k * spacing
                    if v < seg_low - slack or v > seg_high + slack:
                        continue
                    if any(abs(v - e) < 0.01 * spacing for e in emitted):
                        continue
                    level_values.append(float(bm.forward(min(max(v, seg_low), seg_high))))
                    emitted.append(v)
            values.append(level_values)
        return [(spacing, ticks) for (spacing, _old), ticks in zip(levels, values)]

    def tickStrings(self, values, scale, spacing):
        """The label of a tick is the data value at its drawn place, counted from the zero, never the drawn number."""
        data = np.asarray(values, dtype=np.float64)
        if self._map.active:
            data = self._map.inverse(data)
        if self._zero != 0.0:
            data = np.round(data - self._zero, 9) + 0.0  # rounded so that 59.99999999 prints as 60, and a -0.0 as 0
        elif not self._map.active:
            return super().tickStrings(values, scale, spacing)
        return super().tickStrings(list(data), scale, spacing)

    # -- the gaps ----------------------------------------------------------------------------------------------------------------------

    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs) -> None:
        super().drawPicture(p, axisSpec, tickSpecs, textSpecs)
        view = self.linkedView()
        if view is None or not self._map.active or self.orientation != "bottom":
            return
        axis_pen, start, end = axisSpec
        y = float(start.y())
        for gap_start, gap_end in self._map.gaps():
            x0 = self.mapFromScene(view.mapViewToScene(QPointF(gap_start, 0.0))).x()
            x1 = self.mapFromScene(view.mapViewToScene(QPointF(gap_end, 0.0))).x()
            if x1 < start.x() or x0 > end.x():
                continue
            cut = QPen(self._background)
            cut.setWidthF(float(axis_pen.widthF()) + 2.0)
            cut.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(cut)
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            slash = QPen(axis_pen)
            slash.setCosmetic(True)
            p.setPen(slash)
            for x in (x0, x1):
                p.drawLine(QPointF(x - _MARK_SLANT, y + _MARK_HALF_HEIGHT), QPointF(x + _MARK_SLANT, y - _MARK_HALF_HEIGHT))


class BreakHandle(pg.GraphicsObject):
    """The grab area of a break on the axis, over the gap and its `//` marks. Nothing is drawn at rest (the axis draws the marks); under the pointer the marks show in the accent colour,
    and while dragged the item follows the pointer and also draws a dashed line up through the plot. Keeps its pixel size (a child of the axis item)."""

    dragged = pyqtSignal(int, float)
    finished = pyqtSignal(int)

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setZValue(60)
        self.setToolTip("Axis break: drag to move it along the data. Edit mode (Plot menu) changes where it starts and ends, adds and removes breaks.")
        self._view: pg.ViewBox | None = None
        self._span = (0.0, 0.0)  # the gap's drawn start and end
        self._gap_half = 0.0  # half the gap's width on screen (pixels)
        self._half = _MIN_GRAB_HALF_WIDTH  # half the grab area's width (pixels)
        self._rise = 0.0  # while dragged: how far above the axis the dashed line reaches (pixels)
        self._hover = False
        self._start_v: float | None = None
        self._dragging = False

    def attach(self, view: pg.ViewBox | None) -> None:
        if view is self._view or view is None:
            return
        self._view = view
        view.sigRangeChanged.connect(self._reposition)
        view.sigResized.connect(self._reposition)
        axis = self.parentItem()
        if axis is not None:
            axis.geometryChanged.connect(self._reposition)

    def set_span(self, start: float, end: float) -> None:
        """The gap this handle covers, in drawn x."""
        self._span = (float(start), float(end))
        self._reposition()

    def _reposition(self, *_args: object) -> None:
        parent, view = self.parentItem(), self._view
        if parent is None or view is None or self._dragging:
            return
        start, end = self._span
        low, high = view.viewRange()[0]
        self.setVisible(end >= low and start <= high)
        x0 = view.mapViewToScene(QPointF(start, 0.0)).x()
        x1 = view.mapViewToScene(QPointF(end, 0.0)).x()
        gap_half = abs(x1 - x0) / 2.0
        half = max(gap_half + _MARK_SLANT, _MIN_GRAB_HALF_WIDTH)
        if half != self._half or gap_half != self._gap_half:
            self.prepareGeometryChange()
            self._half, self._gap_half = half, gap_half
        self.setPos(parent.mapFromScene(QPointF((x0 + x1) / 2.0, 0.0)).x(), 0.0)

    def boundingRect(self) -> QRectF:
        reach = _MARK_HALF_HEIGHT + _GRAB
        return QRectF(-self._half - _GRAB, -reach - self._rise, 2.0 * (self._half + _GRAB), 2.0 * reach + self._rise)

    def paint(self, painter, _option, _widget=None) -> None:  # Qt signature
        if self._exportOpts is not False or not (self._hover or self._dragging):  # a UI control, not part of a figure; nothing at rest
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(get_active_theme().accent_blue)
        painter.setPen(QPen(accent, 1.8))
        for x in (-self._gap_half, self._gap_half):
            painter.drawLine(QPointF(x - _MARK_SLANT, _MARK_HALF_HEIGHT), QPointF(x + _MARK_SLANT, -_MARK_HALF_HEIGHT))
        if self._dragging and self._rise > 0.0:
            dashed = QPen(accent, 1.0, Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawLine(QPointF(0.0, -_MARK_HALF_HEIGHT), QPointF(0.0, -self._rise))

    def hoverEnterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def mouseDragEvent(self, ev) -> None:
        view = self._view
        if ev.button() != Qt.MouseButton.LeftButton or view is None:
            ev.ignore()
            return
        ev.accept()
        if ev.isStart():
            self._start_v = float(view.mapSceneToView(ev.buttonDownScenePos()).x())
            self._dragging = True
            self.prepareGeometryChange()
            self._rise = float(view.height())  # the dashed line reaches the top of the plot
        if self._start_v is None:
            return
        now = float(view.mapSceneToView(ev.scenePos()).x())
        parent = self.parentItem()
        if parent is not None:  # the ghost follows the pointer by itself; the data are laid out again when the mouse goes up
            start, end = self._span
            centre_scene = (view.mapViewToScene(QPointF(start, 0.0)).x() + view.mapViewToScene(QPointF(end, 0.0)).x()) / 2.0
            shift = view.mapViewToScene(QPointF(now, 0.0)).x() - view.mapViewToScene(QPointF(self._start_v, 0.0)).x()
            self.setPos(parent.mapFromScene(QPointF(centre_scene + shift, 0.0)).x(), 0.0)
        self.dragged.emit(self.index, now - self._start_v)
        if ev.isFinish():
            self._dragging = False
            self._start_v = None
            self.prepareGeometryChange()
            self._rise = 0.0
            self.update()
            self.finished.emit(self.index)

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()  # a click on the break is not a click on the plot behind it


class _BreakRegion(pg.LinearRegionItem):
    """One break in edit mode. A right-click asks for its removal."""

    remove_requested = pyqtSignal(object)

    def __init__(self, item: AxisBreak, color: QColor) -> None:
        super().__init__(values=(item.start, item.end), movable=True, span=(0.0, 1.0))
        self.setZValue(-4)
        self.set_color(color)

    def set_color(self, color: QColor) -> None:
        fill = QColor(color)
        fill.setAlpha(REGION_ALPHA)
        edge = QColor(color)
        self.setBrush(pg.mkBrush(fill))
        self.setHoverBrush(pg.mkBrush(QColor(color.red(), color.green(), color.blue(), REGION_ALPHA + 40)))
        for line in self.lines:
            line.setPen(pg.mkPen(edge, width=1.5))
            line.setHoverPen(pg.mkPen(edge, width=2.5))

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            self.remove_requested.emit(self)
            return
        super().mouseClickEvent(ev)

    def paint(self, painter, *args) -> None:
        if self._exportOpts is not False:  # an editing aid, not part of a figure
            return
        super().paint(painter, *args)


class BreakRegions(QObject):
    """The shaded spans of the breaks while the plot is in edit mode: drag an edge to change where a break starts or ends, the body to slide it; right-click removes it."""

    edited = pyqtSignal(object)  # the breaks now, a tuple of AxisBreak (sorted, merged), when a drag ends or a span is removed

    def __init__(self, view_box: pg.ViewBox, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view_box
        self._regions: list[_BreakRegion] = []
        self._color = QColor(get_active_theme().accent_blue)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        for region in self._regions:
            region.set_color(self._color)

    def set_breaks(self, breaks: tuple[AxisBreak, ...]) -> None:
        """Show these spans (an empty tuple removes them all)."""
        self.clear()
        for item in breaks:
            region = _BreakRegion(item, self._color)
            region.sigRegionChangeFinished.connect(lambda *_args: self._emit())
            region.remove_requested.connect(self._remove)
            self._view.addItem(region, ignoreBounds=True)
            self._regions.append(region)

    def clear(self) -> None:
        for region in self._regions:
            self._view.removeItem(region)
        self._regions = []

    def breaks(self) -> tuple[AxisBreak, ...]:
        out = []
        for region in self._regions:
            low, high = region.getRegion()
            out.append(AxisBreak(float(low), float(high)))
        return tuple(out)

    def regions(self) -> list[_BreakRegion]:
        return list(self._regions)

    def claims_scene_pos(self, scene_pos) -> bool:
        """True where a span is under `scene_pos` (the plot's view gives such a right-click to the span instead of opening its own menu)."""
        return any(region.sceneBoundingRect().contains(scene_pos) for region in self._regions)

    def _remove(self, region: _BreakRegion) -> None:
        if region in self._regions:
            self._view.removeItem(region)
            self._regions.remove(region)
            self._emit()

    def _emit(self) -> None:
        self.edited.emit(self.breaks())
