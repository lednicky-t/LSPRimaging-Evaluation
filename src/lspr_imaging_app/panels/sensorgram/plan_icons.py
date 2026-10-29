"""Pictograms of the Sensorgram ribbon's "Experimental plan" group (2026-10-26), painted like the other Sensorgram icons (``stat_icons.py``, whose helpers they share):
not vendored icon files (``lspr_ui/ICONS.md``), a plot frame with a bar of steps is not in the icon library.

``plan_bar_icon`` the toggle: a plot frame with a bar of three steps along its top and a trace under it; ``position_picture`` where the bar sits (top / bottom);
``text_content_picture`` the whole note or only the solution before the separator; ``align_picture`` what the text position is measured against (the bar, or the data of the step);
``evaluation_picture`` the Evaluation menu (a baseline bar and a signal bar over a stepped trace).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter

from ..spectra.pick_menu import PictureFor
from .stat_icons import _canvas, _pen, _picture, _polyline, _slash, _at, _STROKE


def plan_bar_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A plot frame, a bar of three steps (filled, with gaps) along its top and a trace below it; in ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap, painter = _canvas()
    color = QColor(on_color if on else off_color)
    frame = QColor(color)
    frame.setAlpha(150)
    painter.setPen(_pen(frame))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(_at(0.12, 0.12), _at(0.88, 0.88)), 3.0, 3.0)
    fill = QColor(color)
    fill.setAlpha(150)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    for x0, x1 in ((0.17, 0.39), (0.43, 0.66), (0.70, 0.83)):
        painter.drawRect(QRectF(_at(x0, 0.17), _at(x1, 0.38)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(color, _STROKE * 0.9))
    _polyline(painter, ((0.20, 0.74), (0.40, 0.70), (0.52, 0.56), (0.68, 0.58), (0.80, 0.50)))
    if not on:
        _slash(painter, color)
    painter.end()
    return QIcon(pixmap)


def _frame(painter: QPainter, ink: QColor) -> None:
    painter.setPen(_pen(ink, 1.3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(2.0, 2.0, 16.0, 16.0), 2.0, 2.0)


def position_picture(kind: str, steps: bool = False) -> PictureFor:
    """Where the bar (or the table) sits: a frame with a filled strip along its top or its bottom and a slanted trace in the room left. ``steps``: the trace is two steps instead, the
    second lower than the first (the step statistics table's placement menu)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _frame(painter, ink)
        strip = QColor(theme.accent_blue)
        strip.setAlpha(190)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(strip)
        painter.drawRect(QRectF(3.0, 3.0 if kind == "top" else 12.5, 14.0, 4.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(ink, 1.3))
        if steps:
            first, second = (11.0, 14.5) if kind == "top" else (5.5, 9.0)
            painter.drawLine(QPointF(4.5, first), QPointF(9.5, first))
            painter.drawLine(QPointF(9.5, first), QPointF(9.5, second))
            painter.drawLine(QPointF(9.5, second), QPointF(15.5, second))
        else:
            painter.drawLine(QPointF(4.5, 14.0 if kind == "top" else 9.5), QPointF(15.5, 11.0 if kind == "top" else 7.0))

    return _picture(draw_into)


def evaluation_picture() -> PictureFor:
    """The Evaluation menu: a frame with a step in the trace and, above its two levels, two bars: the baseline's neutral, the signal's in the accent colour (as the chips B1 / S1
    on the plan bar)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _frame(painter, ink)
        painter.setPen(Qt.PenStyle.NoPen)
        baseline = QColor(ink)
        baseline.setAlpha(120)
        painter.setBrush(baseline)
        painter.drawRect(QRectF(3.5, 3.5, 6.0, 3.5))
        signal = QColor(theme.accent_blue)
        signal.setAlpha(220)
        painter.setBrush(signal)
        painter.drawRect(QRectF(10.5, 3.5, 6.0, 3.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(ink, 1.3))
        painter.drawLine(QPointF(4.5, 14.5), QPointF(9.5, 14.5))
        painter.drawLine(QPointF(9.5, 14.5), QPointF(9.5, 10.5))
        painter.drawLine(QPointF(9.5, 10.5), QPointF(15.5, 10.5))

    return _picture(draw_into)


def text_content_picture(kind: str) -> PictureFor:
    """Which part of a note is shown: ``whole`` one long line of text, ``solution`` a short solid piece, a divider and the rest faded."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        painter.setPen(_pen(ink, 2.0))
        if kind == "solution":
            painter.drawLine(QPointF(2.0, 10.0), QPointF(8.0, 10.0))
            painter.setPen(_pen(QColor(theme.accent_gold), 1.4))
            painter.drawLine(QPointF(10.0, 5.5), QPointF(10.0, 14.5))
            faded = QColor(ink)
            faded.setAlpha(80)
            painter.setPen(_pen(faded, 2.0))
            painter.drawLine(QPointF(12.0, 10.0), QPointF(18.0, 10.0))
        else:
            painter.drawLine(QPointF(2.0, 10.0), QPointF(18.0, 10.0))

    return _picture(draw_into)


def align_picture(kind: str) -> PictureFor:
    """What the text position is measured against. ``bar``: a bar along the top with the text line inside it; ``data``: a trace with the text line above it, outside the bar."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _frame(painter, ink)
        strip = QColor(theme.accent_blue)
        strip.setAlpha(190)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(strip)
        painter.drawRect(QRectF(3.0, 3.0, 14.0, 5.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "data":
            painter.setPen(_pen(ink, 1.3))
            for (x0, y0), (x1, y1) in zip(((4.0, 15.5), (8.0, 14.0), (12.0, 16.0)), ((8.0, 14.0), (12.0, 16.0), (16.0, 14.5))):
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            painter.setPen(_pen(QColor(theme.accent_gold), 1.6))
            painter.drawLine(QPointF(6.0, 11.0), QPointF(14.0, 11.0))
        else:
            painter.setPen(_pen(QColor(theme.accent_gold), 1.6))
            painter.drawLine(QPointF(6.0, 5.5), QPointF(14.0, 5.5))

    return _picture(draw_into)


__all__ = ["align_picture", "evaluation_picture", "plan_bar_icon", "position_picture", "text_content_picture"]
