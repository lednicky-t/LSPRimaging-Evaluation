"""Pictograms of the Spectra ribbon's "View" menu, drawn in the style of the Image panel's ROI icons (`image/roi_icons.py`): coloured when "on", grey and crossed out
when "off". Drawn here with a painter, not vendored icon files (`lspr_ui/ICONS.md`): an error-bar glyph and a row of joined points do not exist in the icon library, and each is a
few lines of painter code.

``points_icon`` three measured points; ``lines_icon`` the line that joins them; ``error_bars_icon`` two points with whiskers (the pictogram of an error bar); ``view_icon`` all
three together, the face of the menu button; ``line_style_icon`` a horizontal stroke in one of the four line styles (the picker's buttons).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..ribbon_icon_size import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2  # drawn at twice the size and scaled down, as the other ribbon icons are
_STROKE = 2.1
_DOT_RADIUS = 0.095
_POINTS = ((0.17, 0.70), (0.40, 0.42), (0.62, 0.60), (0.84, 0.30))  # a peak-shaped scatter, in 0..1 of the icon


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _at(x: float, y: float) -> QPointF:
    return QPointF(x * _RENDER_SIZE, y * _RENDER_SIZE)


def _pen(color: QColor, width: float = _STROKE) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _dots(painter: QPainter, color: QColor, points=_POINTS, radius: float = _DOT_RADIUS) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for x, y in points:
        painter.drawEllipse(_at(x, y), radius * _RENDER_SIZE, radius * _RENDER_SIZE)


def _polyline(painter: QPainter, color: QColor, points=_POINTS) -> None:
    painter.setPen(_pen(color))
    for start, end in zip(points, points[1:]):
        painter.drawLine(_at(*start), _at(*end))


def _whiskers(painter: QPainter, color: QColor, specs=((0.40, 0.14, 0.72), (0.84, 0.06, 0.58))) -> None:
    """Error bars: for each ``(x, top, bottom)`` a vertical stroke with a short cap at each end."""
    painter.setPen(_pen(color, _STROKE * 0.85))
    cap = 0.09
    for x, top, bottom in specs:
        painter.drawLine(_at(x, top), _at(x, bottom))
        painter.drawLine(_at(x - cap, top), _at(x + cap, top))
        painter.drawLine(_at(x - cap, bottom), _at(x + cap, bottom))


def _slash(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, _STROKE * 1.4))
    edge = _RENDER_SIZE * 0.12
    painter.drawLine(QPointF(_RENDER_SIZE - edge, edge), QPointF(edge, _RENDER_SIZE - edge))


def _finish(painter: QPainter, pixmap: QPixmap, on: bool, color: QColor) -> QIcon:
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def points_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """Three measured points; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    _dots(painter, color)
    return _finish(painter, pixmap, on, color)


def lines_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The line that joins the points (no dots); in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    _polyline(painter, color)
    return _finish(painter, pixmap, on, color)


def error_bars_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """Two points with a whisker each (a vertical stroke capped at both ends): the error-bar pictogram; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    _whiskers(painter, color)
    _dots(painter, color, ((0.40, 0.43), (0.84, 0.32)), _DOT_RADIUS * 0.9)
    return _finish(painter, pixmap, on, color)


def view_icon(line_color: str, point_color: str, whisker_color: str) -> QIcon:
    """The face of the View menu: the joined points with whiskers on two of them. Always drawn "on"."""
    pixmap, painter = _canvas()
    _whiskers(painter, QColor(whisker_color))
    _polyline(painter, QColor(line_color))
    _dots(painter, QColor(point_color))
    painter.end()
    return QIcon(pixmap)


LINE_STYLE_PATTERNS: dict[str, tuple[float, ...] | None] = {
    # key: dash pattern in units of the stroke width (None = solid), as Qt's custom dash pattern reads it
    "solid": None,
    "dash": (4.0, 2.5),
    "dot": (0.6, 2.2),
    "dashdot": (4.0, 2.0, 0.6, 2.0),
}


def line_style_icon(style: str, color: str) -> QIcon:
    """A horizontal stroke in the given line style (``solid``, ``dash``, ``dot`` or ``dashdot``)."""
    pixmap, painter = _canvas()
    pen = QPen(QColor(color), _STROKE * 1.1)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap if style != "dot" else Qt.PenCapStyle.RoundCap)
    pattern = LINE_STYLE_PATTERNS[style]
    if pattern is not None:
        pen.setDashPattern(list(pattern))
    painter.setPen(pen)
    y = _RENDER_SIZE / 2.0
    margin = _RENDER_SIZE * 0.06
    painter.drawLine(QPointF(margin, y), QPointF(_RENDER_SIZE - margin, y))
    painter.end()
    return QIcon(pixmap)


__all__ = ["LINE_STYLE_PATTERNS", "error_bars_icon", "line_style_icon", "lines_icon", "points_icon", "view_icon"]
