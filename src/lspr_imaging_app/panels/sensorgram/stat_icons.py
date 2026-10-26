"""Pictograms of the Sensorgram ribbon's statistics controls (2026-10-26), drawn with a painter in the style of the Image panel's ROI icons (`image/roi_icons.py`):
coloured when "on", grey and crossed out when "off". Not vendored icon files (`lspr_ui/ICONS.md`): a spike on a trace and a trace lowered onto its baseline are not in the icon library,
and each is a few lines of painter code.

``step_stats_icon`` a bar of two steps with strokes of text under each (the toggle of the table of mean +/- SD per step); ``within_steps_icon`` a dashed step boundary with a separately smoothed curve on each side (the toggle that smooths each experiment step on its own);
``spikes_icon`` a trace with one sharp spike (the toggle that removes such outliers); ``subtract_icon`` a dashed baseline, a trace that has risen from it and an arrow down to it
(the toggle that shows every trace as a shift from its baseline); ``smoothing_picture`` the picture of one smoothing method for the pick-up menu: the letters N/A for no smoothing,
SG for Savitzky-Golay and MA for the moving average.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from lspr_ui import get_active_theme

from ..ribbon_icon_size import ICON_SIZE
from ..spectra.pick_menu import PictureFor, equation_picture

_RENDER_SIZE = ICON_SIZE * 2  # drawn at twice the size and scaled down, as the other ribbon icons are
_STROKE = 2.1


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _at(x: float, y: float) -> QPointF:
    return QPointF(x * _RENDER_SIZE, y * _RENDER_SIZE)


def _pen(color: QColor, width: float = _STROKE, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _slash(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, _STROKE * 1.4))
    painter.drawLine(_at(0.88, 0.12), _at(0.12, 0.88))


def _polyline(painter: QPainter, points: tuple[tuple[float, float], ...]) -> None:
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        painter.drawLine(_at(x0, y0), _at(x1, y1))


def spikes_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A flat trace with one sharp spike, with a dot on the spike's top; in ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    painter.setPen(_pen(color))
    _polyline(painter, ((0.10, 0.70), (0.38, 0.70), (0.50, 0.12), (0.62, 0.70), (0.90, 0.70)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(_at(0.50, 0.12), _RENDER_SIZE * 0.07, _RENDER_SIZE * 0.07)
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def subtract_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A dashed baseline along the bottom, a trace that rises from it to a plateau and a thin arrow from the plateau down to the baseline; in ``on_color``; grey and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    painter.setPen(_pen(color, _STROKE * 0.8, Qt.PenStyle.DashLine))
    painter.drawLine(_at(0.08, 0.80), _at(0.92, 0.80))
    painter.setPen(_pen(color))
    _polyline(painter, ((0.10, 0.68), (0.34, 0.66), (0.50, 0.26), (0.74, 0.24)))
    painter.setPen(_pen(color, _STROKE * 0.8))
    painter.drawLine(_at(0.84, 0.30), _at(0.84, 0.74))  # the arrow: from the plateau down onto the baseline
    painter.drawLine(_at(0.84, 0.74), _at(0.76, 0.62))
    painter.drawLine(_at(0.84, 0.74), _at(0.92, 0.62))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def within_steps_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The "smooth inside each step" switch: a dashed vertical step boundary with a soft curve on each side that stops at it, the right one at another level (two
    smoothed pieces, not one curve across the boundary); in ``on_color``; grey and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    painter.setPen(_pen(color, _STROKE * 0.8, Qt.PenStyle.DashLine))
    painter.drawLine(_at(0.50, 0.08), _at(0.50, 0.92))
    painter.setPen(_pen(color))
    for start, first, second, end in (
        ((0.08, 0.72), (0.18, 0.58), (0.30, 0.82), (0.40, 0.66)),  # left of the boundary: lower
        ((0.60, 0.40), (0.70, 0.26), (0.82, 0.50), (0.92, 0.34)),  # right of it: higher
    ):
        curve = QPainterPath(_at(*start))
        curve.cubicTo(_at(*first), _at(*second), _at(*end))
        painter.drawPath(curve)
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def step_stats_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The step statistics table: a bar of two steps along the top and, under each step, two short strokes standing for the numbers written there (one row per line);
    in ``on_color``; grey and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    painter.setPen(_pen(color, _STROKE * 0.8))
    for left, right in ((0.08, 0.48), (0.52, 0.92)):
        painter.drawRect(QRectF(_at(left, 0.10), _at(right, 0.30)))
    painter.setPen(_pen(color))
    for y in (0.55, 0.80):
        painter.drawLine(_at(0.16, y), _at(0.40, y))
        painter.drawLine(_at(0.60, y), _at(0.84, y))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def smoothing_picture(kind: str) -> PictureFor:
    """The picture of one smoothing method for the pick-up menu, written as letters: N/A for no smoothing, SG for Savitzky-Golay, MA for the moving average."""
    return equation_picture({"savgol": "SG", "moving_average": "MA"}.get(kind, "N/A"))


_PICTURE_SIZE = 20  # the pick-up menus' pictures: 20 x 20 shown pixels, drawn at twice that
_PICTURE_SCALE = 2


def _picture(draw_into) -> PictureFor:
    """A pick-up menu picture from a function ``draw_into(painter, ink, theme)`` that draws in 20 x 20 shown pixels."""

    def draw(color: str) -> QPixmap:
        pixmap = QPixmap(_PICTURE_SIZE * _PICTURE_SCALE, _PICTURE_SIZE * _PICTURE_SCALE)
        pixmap.setDevicePixelRatio(_PICTURE_SCALE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_into(painter, QColor(color), get_active_theme())
        painter.end()
        return pixmap

    return draw


def _wavy(painter: QPainter, y: float, amplitude: float = 1.6) -> None:
    """A short wavy trace centred on height ``y``."""
    points = ((1.5, y), (5.5, y - amplitude), (9.5, y + 0.6 * amplitude), (13.5, y - 0.6 * amplitude), (18.5, y))
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))


def display_picture(kind: str) -> PictureFor:
    """How the selected ROIs are drawn. ``individual``: three separate traces; ``average_by_group``: two bundles of traces, one in each of two colours; ``average_all``:
    one trace inside its shaded band."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        if kind == "individual":
            for index, y in enumerate((4.5, 10.0, 15.5)):
                shade = QColor(ink)
                shade.setAlphaF(1.0 if index == 1 else 0.7)
                painter.setPen(_pen(shade, 1.4))
                _wavy(painter, y)
        elif kind == "average_by_group":
            for colour, ys in ((theme.accent_blue, (3.5, 6.5)), (theme.accent_gold, (13.0, 16.0))):
                painter.setPen(_pen(QColor(colour), 1.4))
                for y in ys:
                    _wavy(painter, y, 1.2)
        else:
            band = QColor(ink)
            band.setAlpha(70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(band)
            painter.drawRect(QRectF(1.5, 5.0, 17.0, 10.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(ink, 1.6))
            _wavy(painter, 10.0, 1.4)

    return _picture(draw_into)


_SPREAD = ((3.5, 13.0), (7.0, 5.0), (10.5, 15.0), (14.0, 8.0), (17.5, 3.0))  # five ROIs at one cube: their values, in 20 x 20 shown pixels (up = higher)


def center_picture(kind: str) -> PictureFor:
    """How the ROIs are combined at each cube. ``mean``: five points and a dashed line at their average; ``median``: the same points with the middle one (by height) ringed and a line through it."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        marker = QColor(theme.accent_gold)
        if kind == "mean":
            painter.setPen(_pen(marker, 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(1.0, 8.8), QPointF(19.0, 8.8))
        else:
            painter.setPen(_pen(marker, 1.4))
            painter.drawLine(QPointF(1.0, 8.0), QPointF(19.0, 8.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in _SPREAD:
            painter.drawEllipse(QPointF(x, y), 1.5, 1.5)
        if kind == "median":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(marker, 1.4))
            painter.drawEllipse(QPointF(14.0, 8.0), 3.2, 3.2)

    return _picture(draw_into)


def band_picture(kind: str) -> PictureFor:
    """The shaded band around an average, written as letters: SD (standard deviation of the ROIs) or SEM (standard error of the mean)."""
    return equation_picture("SEM" if kind == "sem" else "SD")


__all__ = ["band_picture", "center_picture", "display_picture", "smoothing_picture", "spikes_icon", "step_stats_icon", "subtract_icon", "within_steps_icon"]
