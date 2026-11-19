"""The two-headed arrow cursor for resizing a selected ROI, drawn at any angle (2026-11-19, maintainer: "the arrow is not at an ideal angle when the rectangle is rotated").

Qt's standard resize cursors come in four directions only (horizontal, vertical and the two diagonals), so a rotated rectangle's edge or a circle's edge at 20 degrees
showed the arrow snapped to the nearest 45 degrees. A cursor can be any picture, so this module draws the arrow itself, pointing exactly along the direction a drag
moves the edge (a circle: the radius, a rectangle: its own axis).

**It is drawn to the measurements of Windows' own arrows** (read from the system cursor bitmaps at 100 % scale, 2026-11-19, after a first version with a thick border):
23 pixels long, a 1 pixel white shaft with a 1 pixel dark border each side (3 pixels across), heads 9 pixels across at the base, the border about 74 % black. Those
are "design pixels": **Windows makes its cursors bigger with the screen scale** (the cursor bitmap is 32 pixels at 100 % and 48 at 150 %, `GetSystemMetricsForDpi`
(SM_CXCURSOR)), so the whole drawing, border included, is multiplied by the scale factor of the screen the pointer is on. (A second version drew a fixed 23 device
pixels because the measurement was taken at 100 %; on a 150 % screen it came out a third smaller than the move cursor, maintainer 2026-11-19.)

Only the *line* matters (the arrow has two heads), so an angle is taken modulo 180 degrees and rounded to ``STEP_DEG``: at most 1.5 degrees off, which is under a
pixel at the arrow tip. The cursors are made when first needed (a cursor needs the running application) and then reused, one per step and screen scale: the hover
code compares cursors by identity to avoid setting the same one on every mouse move (`interaction.CanvasInteraction._update_roi_hover_cursor`).
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPixmap, QPolygonF

CURSOR_PX = 27
"""Side of the cursor picture in design pixels (that is, at 100 % screen scale; it is made odd in device pixels, so the arrow's centre is a pixel's middle). The arrow
itself is 23 long, like Windows' own."""
STEP_DEG = 3.0
"""Angles are rounded to this. 3 divides 45 and 90, so an axis-aligned or diagonal direction is exactly horizontal, vertical or diagonal."""
# The white shape, in design pixels about the centre; the dark border is drawn 1 pixel outside it, so the whole arrow reaches 10.5 + 1 = 11.5 = 23 / 2.
_HALF_LENGTH = 10.5
_HEAD_LENGTH = 4.5
_HEAD_HALF_WIDTH = 3.5  # 7 white across the base, 9 with the border
_SHAFT_HALF_WIDTH = 0.5  # 1 white across, 3 with the border
_BORDER_WIDTH = 1.0
_FILL_COLOR = QColor(255, 255, 255)
_BORDER_COLOR = QColor(0, 0, 0, 200)  # Windows' border is 74 % black along the shaft and the slopes

_cursors: dict[tuple[int, float], QCursor] = {}


def direction_angle_deg(dx: float, dy: float) -> float:
    """Angle of the line along ``(dx, dy)`` on the screen (x to the right, y downwards: clockwise-positive), in ``[0, 180)`` degrees; ``0`` for no direction."""
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _bucket(angle_deg: float) -> int:
    """The step number of a line at ``angle_deg``: 0 .. (180 / STEP_DEG - 1), the line at 180 degrees being the line at 0."""
    steps = int(round(180.0 / STEP_DEG))
    return int(round((angle_deg % 180.0) / STEP_DEG)) % steps


def _arrow_polygon() -> QPolygonF:
    """The white double-headed arrow pointing along +x, centred on the origin."""
    length, head, wide, shaft = _HALF_LENGTH, _HEAD_LENGTH, _HEAD_HALF_WIDTH, _SHAFT_HALF_WIDTH
    points = (
        (-length, 0.0),
        (-length + head, -wide),
        (-length + head, -shaft),
        (length - head, -shaft),
        (length - head, -wide),
        (length, 0.0),
        (length - head, wide),
        (length - head, shaft),
        (-length + head, shaft),
        (-length + head, wide),
    )
    return QPolygonF([QPointF(x, y) for x, y in points])


def _screen_ratio() -> float:
    """The scale factor of the screen the pointer is on (the app's window is under it while this runs), else the primary screen's."""
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _picture(angle_deg: float, ratio: float = 1.0) -> QPixmap:
    """The arrow at ``angle_deg`` for a screen of scale ``ratio``: ``CURSOR_PX * ratio`` device pixels square (made odd), the drawing enlarged by ``ratio``."""
    side = int(math.ceil(CURSOR_PX * ratio))
    side += 1 - side % 2  # odd: the centre is the middle of a pixel, which is where the hot spot lands
    picture = QPixmap(side, side)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(side / 2.0, side / 2.0)
        painter.scale(ratio, ratio)  # from here on the coordinates are design pixels, the border's width included
        painter.rotate(angle_deg)  # y points down on the screen, so a positive angle turns clockwise, as direction_angle_deg measures it
        polygon = _arrow_polygon()
        # Two passes, so the border is exactly _BORDER_WIDTH thick and wholly outside: a dark stroke twice as wide (half of it falls inside the shape), then the white
        # shape on top, with no pen. (One pass with a pen would eat half the border's width out of the white, a 1 pixel shaft would vanish.)
        pen = QPen(_BORDER_COLOR)
        pen.setWidthF(2.0 * _BORDER_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(polygon)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_FILL_COLOR)
        painter.drawPolygon(polygon)
    finally:
        painter.end()
    picture.setDevicePixelRatio(ratio)  # after drawing: it says how many device pixels make one of Qt's units, so Qt takes the picture 1:1 on that screen (not stretched)
    return picture


def resize_cursor(angle_deg: float) -> QCursor:
    """The two-headed arrow along the line at ``angle_deg`` (see `direction_angle_deg`), rounded to `STEP_DEG`. The same object for the same step and screen, every
    time. Its hot spot is the middle of the picture, so the arrow's centre is on the edge being held."""
    ratio = _screen_ratio()
    key = (_bucket(angle_deg), round(ratio, 3))
    cursor = _cursors.get(key)
    if cursor is None:
        cursor = QCursor(_picture(key[0] * STEP_DEG, ratio), -1, -1)  # -1, -1: the centre
        _cursors[key] = cursor
    return cursor
