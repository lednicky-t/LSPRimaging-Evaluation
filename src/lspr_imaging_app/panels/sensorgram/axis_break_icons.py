"""Pictures of the axis breaks (2026-11-27), in the style of `line_icons.py`: drawn in 20 x 20 shown pixels, not vendored icon files (`lspr_ui/ICONS.md`).

- ``breaks_icon`` (the Show group's toggle, and with ``breaks_menu_icon`` the face of the break rows): an axis line with a gap in it and the two slanted strokes of a break at the gap,
  the strokes in the gold accent; both in the dim colour and crossed out when off.
- ``edit_breaks_icon``: the Tabler pencil, the edit mode's toggle (accent colour while on).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QIcon, QPainter

from lspr_ui import load_tabler_icon

from .line_icons import _SLASH, _TOGGLE_LINE_WIDTH
from .stat_icons import _pen, _picture


def _draw_axis_break(painter: QPainter, axis: QColor, marks: QColor, width: float) -> None:
    painter.setPen(_pen(axis, width))
    painter.drawLine(QPointF(1.5, 13.0), QPointF(7.0, 13.0))
    painter.drawLine(QPointF(13.0, 13.0), QPointF(18.5, 13.0))
    painter.drawLine(QPointF(4.0, 13.0), QPointF(4.0, 5.0))  # a trace on each side: the axis carries something
    painter.drawLine(QPointF(16.0, 13.0), QPointF(16.0, 8.0))
    painter.setPen(_pen(marks, width))
    for x in (7.0, 13.0):
        painter.drawLine(QPointF(x - 2.2, 16.6), QPointF(x + 2.2, 9.4))


def breaks_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The Show group's toggle: the axis with its break (marks in the gold accent) when the breaks are shown; dim and crossed out when the axis is drawn continuous."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        axis, marks = (QColor(theme.text_primary), QColor(theme.accent_gold)) if on else (ink, ink)
        _draw_axis_break(painter, axis, marks, _TOGGLE_LINE_WIDTH * 0.8)
        if not on:
            painter.setPen(_pen(ink, _TOGGLE_LINE_WIDTH))
            painter.drawLine(*_SLASH)

    return QIcon(_picture(draw_into)(off_color))


def breaks_menu_icon(color: str) -> QIcon:
    """The face of the Plot menu's break rows: the axis with its break, the marks in the gold accent."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _draw_axis_break(painter, ink, QColor(theme.accent_gold), _TOGGLE_LINE_WIDTH * 0.8)

    return QIcon(_picture(draw_into)(color))


def edit_breaks_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The edit mode's toggle: the pencil, in the accent colour while the mode is on."""
    return load_tabler_icon("pencil", color=on_color if on else off_color, size=24)


def guides_menu_icon(color: str) -> QIcon:
    """The Plot group's Guides button: the two axes with a dashed horizontal line across (gold accent) and the short stroke of its label above the right end."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        painter.setPen(_pen(ink, _TOGGLE_LINE_WIDTH * 0.8))
        painter.drawLine(QPointF(3.0, 3.0), QPointF(3.0, 17.0))
        painter.drawLine(QPointF(3.0, 17.0), QPointF(18.0, 17.0))
        painter.setPen(_pen(QColor(theme.accent_gold), _TOGGLE_LINE_WIDTH * 0.8))
        for x0, x1 in ((4.5, 7.5), (9.0, 12.0), (13.5, 16.5)):
            painter.drawLine(QPointF(x0, 10.0), QPointF(x1, 10.0))
        painter.drawLine(QPointF(12.0, 6.5), QPointF(16.5, 6.5))

    return QIcon(_picture(draw_into)(color))
