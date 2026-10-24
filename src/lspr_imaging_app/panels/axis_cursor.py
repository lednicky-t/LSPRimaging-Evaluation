"""The cursor of a plot whose x axis is one of the Image panel's sliders (2026-10-24, maintainer request): a dashed vertical line at the
current value and, on the x axis, the same circle the slider's handle is (ring in the slider's colour, filled with the window colour).

Used by the Sensorgram (x = cube or time; the Cube slider's gold) and the Spectra (x = wavelength; the Wavelength slider's blue). The circle sits on the x axis (a child of the
axis item, not of the view box, which would clip it) and is a handle, and so is the line (hover it: the pointer changes): dragging either emits
``dragged(x)`` with the x value under the pointer. This class moves nothing by itself on a drag: the owner
turns the value into "the nearest cube / wavelength", sets it on the ``SelectionModule`` and calls ``set_x`` when the selection changes, so the
circle jumps from tick to tick like the slider handle and the plot never shows a value the Image panel does not have (a dragged line
that the owner did not move is put back where it was when the mouse button goes up).

Display only: it draws what it is told and reads no module.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen

from lspr_ui import get_active_theme

_RADIUS = 5.0  # the slider handle's (DataAxisSlider.paintEvent)
_PEN_WIDTH = 2.0
_GRAB = 4.0  # extra pixels around the ring that still grab it


class _Handle(pg.GraphicsObject):
    """The circle. It keeps its pixel size whatever the zoom (``ItemIgnoresTransformations``), is a child of the bottom axis item and is
    centred on the axis line, under the data x it stands for. Hidden while that x is outside the visible range."""

    dragged = pyqtSignal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setZValue(50)
        self._color = QColor("#38bdf8")
        self._view_box: pg.ViewBox | None = None
        self._x = 0.0
        self._wanted = False  # the owner wants it shown (it may still be out of the visible range)

    def attach(self, view_box: pg.ViewBox, axis: pg.AxisItem) -> None:
        """Stand on `axis` under the data x of `view_box` (called by the owner once the plot is built)."""
        self._view_box = view_box
        self.setParentItem(axis)
        view_box.sigRangeChanged.connect(self._reposition)
        view_box.sigResized.connect(self._reposition)
        axis.geometryChanged.connect(self._reposition)
        self._reposition()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_x(self, x: float | None) -> None:
        self._wanted = x is not None
        if x is not None:
            self._x = float(x)
        self._reposition()

    def _reposition(self, *_args: object) -> None:
        parent, view_box = self.parentItem(), self._view_box
        if parent is None or view_box is None:
            return
        low, high = view_box.viewRange()[0]
        self.setVisible(self._wanted and low <= self._x <= high)
        scene_x = view_box.mapViewToScene(QPointF(self._x, 0.0)).x()
        self.setPos(parent.mapFromScene(QPointF(scene_x, 0.0)).x(), 0.0)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        reach = _RADIUS + _PEN_WIDTH + _GRAB
        return QRectF(-reach, -reach, 2.0 * reach, 2.0 * reach)

    def paint(self, painter, _option, _widget=None) -> None:  # noqa: ANN001 - Qt signature
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._color, _PEN_WIDTH))
        painter.setBrush(QColor(get_active_theme().window_bg))
        painter.drawEllipse(QPointF(0.0, 0.0), _RADIUS, _RADIUS)

    def mouseDragEvent(self, ev) -> None:  # noqa: N802 - pyqtgraph naming
        if ev.button() != Qt.MouseButton.LeftButton or self._view_box is None:
            ev.ignore()
            return
        ev.accept()  # the view box must not pan while the circle is dragged
        self.dragged.emit(float(self._view_box.mapSceneToView(ev.scenePos()).x()))

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 - pyqtgraph naming
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()  # a click on the circle is not a click on the plot behind it


class AxisCursor(QObject):
    """Dashed vertical line plus the slider-style circle on the x axis of `plot_item`."""

    dragged = pyqtSignal(float)

    def __init__(self, plot_item: pg.PlotItem, color: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._x: float | None = None  # where the owner last put the cursor
        self._line = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen(150, 150, 150, 160, style=Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen(220, 220, 220, 230, width=2, style=Qt.PenStyle.DashLine),
        )
        self._line.setCursor(Qt.CursorShape.SizeHorCursor)
        self._line.sigDragged.connect(lambda line: self.dragged.emit(float(line.value())))
        self._line.sigPositionChangeFinished.connect(self._put_back)
        self._handle = _Handle()
        self._handle.set_color(color)
        self._handle.dragged.connect(self.dragged)
        plot_item.addItem(self._line, ignoreBounds=True)
        self._handle.attach(plot_item.getViewBox(), plot_item.getAxis("bottom"))
        self.set_x(None)

    @property
    def line(self) -> pg.InfiniteLine:
        return self._line

    @property
    def handle(self) -> pg.GraphicsObject:
        return self._handle

    def set_color(self, color: str) -> None:
        self._handle.set_color(color)

    def set_x(self, x: float | None) -> None:
        """Put the cursor at `x` (data units), or hide it (None or not finite)."""
        shown = x is not None and x == x and abs(x) != float("inf")
        self._x = float(x) if shown else None
        self._line.setVisible(shown)
        self._handle.set_x(self._x)
        if shown:
            self._line.setValue(float(x))

    def x(self) -> float | None:
        return self._x

    def _put_back(self, *_args: object) -> None:
        """The mouse button went up after a drag of the line: it stands where the owner put the cursor, not where the pointer let go."""
        if self._x is not None and self._line.value() != self._x:
            self._line.setValue(self._x)

    def is_shown(self) -> bool:
        return self._line.isVisible()
