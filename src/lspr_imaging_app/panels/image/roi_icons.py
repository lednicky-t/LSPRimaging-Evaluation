"""The ROI "spot" and "ring" icons, drawn in one style so they read as a pair.

``spot_icon`` is the Background tab's sample-ROI icon (a filled circle), moved
here so the ROI tab's visibility toggle and the Background tab's exclusion
toggle are literally the same drawing. ``ring_icon`` is its counterpart for the
reference ring (two concentric circles, outlined). Both: coloured when "on",
grey with a slash (top right to bottom left) when "off".
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from .general_group import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2  # drawn at twice the size and scaled down, as the other ribbon icons are
_STROKE_WIDTH = 2.1


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _slash(painter: QPainter, color: QColor) -> None:
    pen = QPen(color, _STROKE_WIDTH * 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    edge = _RENDER_SIZE * 0.12
    painter.drawLine(QPointF(_RENDER_SIZE - edge, edge), QPointF(edge, _RENDER_SIZE - edge))


def spot_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A filled circle in ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    margin = _RENDER_SIZE * 0.17
    painter.drawEllipse(QRectF(margin, margin, _RENDER_SIZE - 2 * margin, _RENDER_SIZE - 2 * margin))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def ring_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """Two concentric circles (the reference ring's inner and outer edge) in
    ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    pen = QPen(color, _STROKE_WIDTH * 1.2)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    centre = _RENDER_SIZE / 2.0
    for radius in (_RENDER_SIZE * 0.34, _RENDER_SIZE * 0.17):
        painter.drawEllipse(QRectF(centre - radius, centre - radius, 2 * radius, 2 * radius))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)
