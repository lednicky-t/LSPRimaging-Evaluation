"""Pictures of the LbL and S_bulk plot windows' ribbon (2026-11-22), in the style of `line_icons.py`: **dots in the text colour, the line / bar / band in the gold accent**, drawn in
20 x 20 shown pixels. Not vendored icon files (`lspr_ui/ICONS.md`).

- ``error_bars_icon`` (the Uncertainty menu's show / hide toggle, and with ``uncertainty_menu_icon`` its face): three dots, each with a vertical bar; crossed out when off.
- ``bars_picture`` / ``band_picture``: the Bars / Band choice, the same three dots with a bar at each, or with a shaded band along them.
- ``accumulated_picture``: the Accumulated shift: a dashed baseline at the start level and, from it, a gold stroke up to every dot (each longer than the one before: the total from the start).
- ``cycle_picture``: the Shift per cycle: three dots on a staircase, a gold stroke between each dot and the level of the one before (the step from the previous cycle alone).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath

from ..spectra.pick_menu import PictureFor
from .line_icons import _DOT_RADIUS, _DOTS, _LINE_WIDTH, _SLASH, _TOGGLE_DOT_RADIUS, _TOGGLE_LINE_WIDTH
from .stat_icons import _pen, _picture

_BAR_HALF = 3.2  # how far a bar reaches above and below its dot


def _draw_bars(painter: QPainter, dots: QColor, bars: QColor, radius: float, width: float) -> None:
    painter.setPen(_pen(bars, width))
    for x, y in _DOTS:
        painter.drawLine(QPointF(x, y - _BAR_HALF - 1.0), QPointF(x, y + _BAR_HALF + 1.0))
        painter.drawLine(QPointF(x - 1.8, y - _BAR_HALF - 1.0), QPointF(x + 1.8, y - _BAR_HALF - 1.0))  # the caps
        painter.drawLine(QPointF(x - 1.8, y + _BAR_HALF + 1.0), QPointF(x + 1.8, y + _BAR_HALF + 1.0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(dots)
    for x, y in _DOTS:
        painter.drawEllipse(QPointF(x, y), radius, radius)


def error_bars_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The Uncertainty show / hide toggle: dots with bars (dots in the text colour, bars in the gold accent); both in the dim colour and crossed out when off."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        dots, bars = (QColor(theme.text_primary), QColor(theme.accent_gold)) if on else (ink, ink)
        _draw_bars(painter, dots, bars, _TOGGLE_DOT_RADIUS, _TOGGLE_LINE_WIDTH * 0.8)
        if not on:
            painter.setPen(_pen(ink, _TOGGLE_LINE_WIDTH))
            painter.drawLine(*_SLASH)

    return QIcon(_picture(draw_into)(off_color))


def bars_picture() -> PictureFor:
    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _draw_bars(painter, ink, QColor(theme.accent_gold), _DOT_RADIUS, _LINE_WIDTH * 0.8)

    return _picture(draw_into)


def band_picture() -> PictureFor:
    """The Band choice: a clearly wide shaded band (about half the picture's height) with an outline, the line through its middle and the dots on it."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        gold = QColor(theme.accent_gold)
        flat = ((2.0, 11.0), (10.0, 8.0), (18.0, 10.0))  # the dots: flatter than the others', so the band has room above and below
        half = 4.6
        outline = QPainterPath(QPointF(flat[0][0], flat[0][1] - half))
        for x, y in flat[1:]:
            outline.lineTo(QPointF(x, y - half))
        for x, y in reversed(flat):
            outline.lineTo(QPointF(x, y + half))
        outline.closeSubpath()
        fill = QColor(gold)
        fill.setAlpha(120)
        edge = QColor(gold)
        edge.setAlpha(200)
        painter.setPen(_pen(edge, 0.9))
        painter.setBrush(fill)
        painter.drawPath(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(gold, _LINE_WIDTH))
        line = QPainterPath(QPointF(*flat[0]))
        for x, y in flat[1:]:
            line.lineTo(QPointF(x, y))
        painter.drawPath(line)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in flat:
            painter.drawEllipse(QPointF(x, y), _DOT_RADIUS, _DOT_RADIUS)

    return _picture(draw_into)


def uncertainty_menu_icon(color: str) -> QIcon:
    """The Uncertainty menu's face, in `color`: the bars picture."""
    return QIcon(bars_picture()(color))


_STAIRS = ((3.0, 14.5), (10.0, 9.5), (17.0, 4.5))  # three dots, each one step higher than the one before


def accumulated_picture() -> PictureFor:
    """Accumulated: every dot measured from the start (a dashed baseline at the first dot's level; a gold stroke up from it to each dot)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        baseline_y = _STAIRS[0][1]
        painter.setPen(_pen(ink, 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(1.0, baseline_y), QPointF(19.0, baseline_y))
        painter.setPen(_pen(QColor(theme.accent_gold), _LINE_WIDTH))
        for x, y in _STAIRS[1:]:
            painter.drawLine(QPointF(x, baseline_y), QPointF(x, y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in _STAIRS:
            painter.drawEllipse(QPointF(x, y), _DOT_RADIUS, _DOT_RADIUS)

    return _picture(draw_into)


def cycle_picture() -> PictureFor:
    """Per cycle: every dot measured from the one before (a dashed level from each dot to the next one's place; a gold stroke up to that next dot)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        for (x0, y0), (x1, y1) in zip(_STAIRS, _STAIRS[1:]):
            painter.setPen(_pen(ink, 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y0))
            painter.setPen(_pen(QColor(theme.accent_gold), _LINE_WIDTH))
            painter.drawLine(QPointF(x1, y0), QPointF(x1, y1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in _STAIRS:
            painter.drawEllipse(QPointF(x, y), _DOT_RADIUS, _DOT_RADIUS)

    return _picture(draw_into)


def show_plot_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The "show the plot window" toggle of the S_bulk and LbL pop-ups: a chart, grey when the window is closed, in the accent colour (blue) while it is open."""
    from lspr_ui import load_tabler_icon

    return load_tabler_icon("chart-line", color=on_color if on else off_color, size=24)
