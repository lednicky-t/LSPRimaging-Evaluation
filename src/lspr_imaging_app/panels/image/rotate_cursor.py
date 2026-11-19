"""The mouse cursor for turning a selected rectangle ROI (2026-11-18, maintainer: "when rotating, the cursor should change to rotate, not the hold handle").

Qt has no standard rotate cursor, so it is drawn from the glyph the Rotate tool already uses (the vendored Tabler ``rotate-clockwise-2``): a white stroke over a
dark one, so it reads on a bright and on a dark image. It is shown while the pointer is over the knob above a selected rectangle and for the whole turning drag
(`interaction.CanvasInteraction`), and nowhere else. One cursor, made when first needed (a cursor needs the running application) and then reused: the hover code
compares cursors by identity to avoid setting the same one on every mouse move.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QGuiApplication, QPainter, QPixmap

from lspr_ui import load_tabler_icon

CURSOR_PX = 26
"""Side of the cursor picture in device-independent pixels (a standard arrow cursor is about 16 to 32)."""
_GLYPH = "rotate-clockwise-2"
_OUTLINE_STROKE = 4.4  # the dark under-stroke, in the icon's own 24-unit grid
_INNER_STROKE = 2.3
_INNER_COLOR = "#ffffff"
_OUTLINE_COLOR = "#0f172a"

_cursor: QCursor | None = None


def _picture() -> QPixmap:
    screen = QGuiApplication.primaryScreen()
    ratio = float(screen.devicePixelRatio()) if screen is not None else 1.0
    side = max(int(round(CURSOR_PX * ratio)), 1)
    picture = QPixmap(side, side)
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    try:
        for color, stroke in ((_OUTLINE_COLOR, _OUTLINE_STROKE), (_INNER_COLOR, _INNER_STROKE)):
            glyph = load_tabler_icon(_GLYPH, color=color, size=side, stroke_width=stroke).pixmap(side, side)
            painter.drawPixmap(0, 0, CURSOR_PX, CURSOR_PX, glyph)
    finally:
        painter.end()
    return picture


def rotate_cursor() -> QCursor:
    """The rotate cursor (the same object every time). Its hot spot is the middle of the picture, where the knob is being held."""
    global _cursor
    if _cursor is None:
        _cursor = QCursor(_picture(), -1, -1)  # -1, -1: the centre
    return _cursor
