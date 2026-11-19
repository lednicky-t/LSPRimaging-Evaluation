"""The two-headed arrow cursor for resizing a selected ROI, drawn at any angle (2026-11-19, maintainer: "the arrow is not at an ideal angle when the rectangle is rotated").

Qt's standard resize cursors come in four directions only (horizontal, vertical and the two diagonals), so a rotated rectangle's edge or a circle's edge at 20 degrees
showed the arrow snapped to the nearest 45 degrees. A cursor can be any picture, so this module draws the arrow itself, pointing exactly along the direction a drag
moves the edge (a circle: the radius, a rectangle: its own axis), in the same white-over-dark look as the rotate cursor (`rotate_cursor.py`).

Only the *line* matters (the arrow has two heads), so an angle is taken modulo 180 degrees and rounded to ``STEP_DEG``: at most 1.5 degrees off, which is under a
pixel at the arrow tip. The cursors are made when first needed (a cursor needs the running application) and then reused, one per step: the hover code compares
cursors by identity to avoid setting the same one on every mouse move (`interaction.CanvasInteraction._update_roi_hover_cursor`).
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPixmap, QPolygonF

CURSOR_PX = 28
"""Side of the cursor picture in device-independent pixels (the rotate cursor is 26; this one is a little larger because a line reaches the corners)."""
STEP_DEG = 3.0
"""Angles are rounded to this. 3 divides 45 and 90, so an axis-aligned or diagonal direction is exactly horizontal, vertical or diagonal."""
_HALF_LENGTH = 11.5  # tip to centre, in cursor pixels
_HEAD_LENGTH = 6.5
_HEAD_HALF_WIDTH = 4.6
_SHAFT_HALF_WIDTH = 1.5
_OUTLINE_WIDTH = 1.4
_FILL_COLOR = "#ffffff"
_OUTLINE_COLOR = "#0f172a"

_cursors: dict[int, QCursor] = {}


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
    """The double-headed arrow pointing along +x, centred on the origin."""
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


def _picture(angle_deg: float) -> QPixmap:
    screen = QGuiApplication.primaryScreen()
    ratio = float(screen.devicePixelRatio()) if screen is not None else 1.0
    side = max(int(round(CURSOR_PX * ratio)), 1)
    picture = QPixmap(side, side)
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(CURSOR_PX / 2.0, CURSOR_PX / 2.0)
        painter.rotate(angle_deg)  # y points down on the screen, so a positive angle turns clockwise, as direction_angle_deg measures it
        pen = QPen(QColor(_OUTLINE_COLOR))
        pen.setWidthF(_OUTLINE_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(_FILL_COLOR))
        painter.drawPolygon(_arrow_polygon())
    finally:
        painter.end()
    return picture


def resize_cursor(angle_deg: float) -> QCursor:
    """The two-headed arrow along the line at ``angle_deg`` (see `direction_angle_deg`), rounded to `STEP_DEG`. The same object for the same step, every time.
    Its hot spot is the middle of the picture, so the arrow's centre is on the edge being held."""
    key = _bucket(angle_deg)
    cursor = _cursors.get(key)
    if cursor is None:
        cursor = QCursor(_picture(key * STEP_DEG), -1, -1)  # -1, -1: the centre
        _cursors[key] = cursor
    return cursor
