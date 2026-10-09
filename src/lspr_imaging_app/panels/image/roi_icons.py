"""The ROI "spot" and "ring" icons, drawn in one style so they read as a pair.

``spot_icon`` is the Background tab's sample-ROI icon (a filled circle), moved
here so the ROI tab's visibility toggle and the Background tab's exclusion
toggle are literally the same drawing. ``ring_icon`` is its counterpart for the
reference ring (a filled ring). Both: coloured when "on",
grey with a slash (top right to bottom left) when "off".
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

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
    margin = _RENDER_SIZE * 0.25
    painter.drawEllipse(QRectF(margin, margin, _RENDER_SIZE - 2 * margin, _RENDER_SIZE - 2 * margin))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def display_icon(sample_color: str, reference_color: str) -> QIcon:
    """A filled circle (the size of ``spot_icon``'s) inside a wider-gapped filled
    ring: the face of the ROIs tab's "ROI display" menu. Always drawn "on"."""
    pixmap, painter = _canvas()
    centre = _RENDER_SIZE / 2.0
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(sample_color))
    radius = _RENDER_SIZE * 0.25  # the same circle as spot_icon
    painter.drawEllipse(QRectF(centre - radius, centre - radius, 2 * radius, 2 * radius))
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for radius in (_RENDER_SIZE * 0.48, _RENDER_SIZE * 0.37):  # outer, inner: a gap around the circle
        path.addEllipse(QRectF(centre - radius, centre - radius, 2 * radius, 2 * radius))
    painter.setBrush(QColor(reference_color))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def ring_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A filled ring (the reference region between the inner and outer edge) in
    ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    centre = _RENDER_SIZE / 2.0
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for radius in (_RENDER_SIZE * 0.33, _RENDER_SIZE * 0.16):
        path.addEllipse(QRectF(centre - radius, centre - radius, 2 * radius, 2 * radius))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)
