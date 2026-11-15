"""A path item that draws itself from pictures when that is cheaper than stroking it (2026-11-16, maintainer: "can the ants animation be optimized?
Cos it is basically repeating pattern").

**The idea.** The selection's outline is a hairline (white, or coloured dashes that crawl). Its dash pattern repeats every 8 screen pixels and the crawl
moves it 2 pixels a tick, so there are only **four pictures** of it. Once the view holds still, each picture is drawn once into a transparent
``QPixmap`` and every later paint (a crawl tick, or any repaint of the view: every frame of Play) just copies the picture, instead of stroking the path
again. The result is **pixel-identical** to drawing the path (pinned by tests, also on a scaled screen, device pixel ratio 1.5).

**When it pays** (measured 2026-11-16, 1275 x 800 image, offscreen raster, scale 1; `docs/selection_lock_and_tool_move_2026-11-15.md` section 20):
stroking costs about 0.15 - 0.2 us per path element, copying a picture 0.2 - 0.5 ms per megapixel. So a picture wins only when its area is small
compared with the path's element count: **at most `PIXELS_PER_ELEMENT` (150) pixels per element** (break-even is about 380; 150 keeps a safety
margin). A selection of 300 particles (8,600 elements) in a 0.8 MP view: yes; 1,500 particles: yes (a tick went from 11.1 to 4.0 ms); a rectangle or
a lasso (a few hundred elements at most): no, it is cheaper to stroke (1.1 ms against 1.4 ms cached), so they are drawn directly as before.
The pictures are also capped (`MAX_PICTURE_BYTES`): a view so big that the pictures would not fit is drawn directly, never slower than before.

**When the pictures are made.** Not on the first paint of a view, only on the next one that sees the *same* view, colour and path: while the view
is being panned or zoomed, or the path changes at every mouse move (the dragged shape, the Subtract / Intersect preview), every paint is direct and
nothing is built. A new path, colour, zoom, pan or window size throws the pictures away (the key below). One picture per dash offset is made
when that offset is first painted, so a crawl fills its four pictures over its first four ticks.

**Outside a window** (`widget is None`: exports, `scene.render`) the path is always drawn directly.

Costs: a picture is the item's bounding box (clipped to the view) in device pixels, 4 bytes a pixel, `phases` pictures per item.
"""

from __future__ import annotations

import logging
import math
import time

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QStyleOptionGraphicsItem, QWidget

from ...applog.events import log_event

logger = logging.getLogger(__name__)

PIXELS_PER_ELEMENT = 150  # a picture is used only while its area is at most this many device pixels per path element
MAX_PICTURE_BYTES = 160 * 1024 * 1024  # all the pictures of one item
_MARGIN = 2  # device pixels added round the box: the hairline's antialiasing / caps


class CachedLineItem(QGraphicsPathItem):
    """`QGraphicsPathItem` whose paint is a copy of a cached picture while that is worth it. `phases` = how many different dash offsets the pen
    cycles through (1 for a solid line, 4 for the ants): the number of pictures kept."""

    def __init__(self, phases: int = 1, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._phases = max(1, int(phases))
        self._pictures: dict[float, tuple[QPixmap, QPoint]] = {}  # dash offset -> (picture, its top-left corner in device pixels)
        self._view_key: tuple | None = None  # the view, colour and path the pictures belong to
        self._worth_it: bool | None = None  # decided when the first picture of a view would be made
        self._elements = 0
        self.caching = True  # tests and benchmarks switch it off
        self.direct_paints = 0
        self.picture_paints = 0
        self.pictures_built = 0

    def setPath(self, path) -> None:  # Qt's own method name
        super().setPath(path)
        self._elements = path.elementCount()
        self._forget()

    def pictures(self) -> int:
        """How many pictures are kept now (tests)."""
        return len(self._pictures)

    def _forget(self) -> None:
        self._pictures.clear()
        self._view_key = None
        self._worth_it = None

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if not self.caching or widget is None:
            self._paint_direct(painter, option, widget)
            return
        ratio = widget.devicePixelRatioF()
        world = painter.worldTransform()
        pen = self.pen()
        key = (
            world.m11(), world.m12(), world.m21(), world.m22(), world.dx(), world.dy(),
            pen.color().rgba(), pen.widthF(), widget.width(), widget.height(), ratio,
        )
        if key != self._view_key:  # another view, colour or window size: the pictures are stale; draw directly, build from the next paint on
            self._pictures.clear()
            self._view_key = key
            self._worth_it = None
            self._paint_direct(painter, option, widget)
            return
        if self._worth_it is False:
            self._paint_direct(painter, option, widget)
            return
        offset = pen.dashOffset()
        picture = self._pictures.get(offset)
        if picture is None:
            if len(self._pictures) >= self._phases:  # more offsets than expected: do not grow without limit
                self._paint_direct(painter, option, widget)
                return
            picture = self._make_picture(painter, option, world, ratio, widget)
            if picture is None:
                self._paint_direct(painter, option, widget)
                return
            self._pictures[offset] = picture
        self.picture_paints += 1
        pixmap, corner = picture
        painter.save()
        painter.setWorldTransform(QTransform.fromScale(1.0 / ratio, 1.0 / ratio))  # the picture is in device pixels: copy it 1 : 1
        painter.drawPixmap(corner, pixmap)
        painter.restore()

    def _paint_direct(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None) -> None:
        self.direct_paints += 1
        super().paint(painter, option, widget)

    def _make_picture(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, world: QTransform, ratio: float, widget: QWidget
    ) -> tuple[QPixmap, QPoint] | None:
        """Draw the path into a transparent picture of its box in device pixels (what the view would show), or ``None`` when that is not worth it."""
        started = time.perf_counter()
        to_device = world * QTransform.fromScale(ratio, ratio)
        box = to_device.mapRect(self.boundingRect()).toAlignedRect().adjusted(-_MARGIN, -_MARGIN, _MARGIN, _MARGIN)
        box = box.intersected(QRect(0, 0, math.ceil(widget.width() * ratio), math.ceil(widget.height() * ratio)))
        if box.isEmpty():
            self._worth_it = False  # nothing of it is in view: nothing to draw, nothing to keep
            return None
        area = box.width() * box.height()
        if self._worth_it is None:
            self._worth_it = area <= PIXELS_PER_ELEMENT * self._elements and area * 4 * self._phases <= MAX_PICTURE_BYTES
        if not self._worth_it:
            return None
        pixmap = QPixmap(box.size())
        pixmap.fill(Qt.GlobalColor.transparent)  # (not 0: that is Qt's `color0`, an opaque black)
        picture_painter = QPainter(pixmap)
        picture_painter.setRenderHints(painter.renderHints())
        picture_painter.setWorldTransform(to_device * QTransform.fromTranslate(-box.x(), -box.y()))
        QGraphicsPathItem.paint(self, picture_painter, option, None)
        picture_painter.end()
        self.pictures_built += 1
        log_event(
            logger, "selection.line_picture", logging.DEBUG,
            elements=self._elements, mp=round(area / 1e6, 2), build_ms=round((time.perf_counter() - started) * 1000, 1),
            kept=len(self._pictures) + 1,
        )
        return pixmap, box.topLeft()
