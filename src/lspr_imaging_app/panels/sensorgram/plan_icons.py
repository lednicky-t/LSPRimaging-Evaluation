"""Pictograms of the Sensorgram ribbon's "Experimental plan" group (2026-10-26), painted like the other Sensorgram icons (``stat_icons.py``, whose helpers they share):
not vendored icon files (``lspr_ui/ICONS.md``), a plot frame with a bar of steps is not in the icon library.

``plan_bar_icon`` the toggle: a plot frame with a bar of three steps along its top and a trace under it; ``position_picture`` where the bar sits (top / bottom);
``text_content_picture`` the whole note or only the solution before the separator; ``align_picture`` what the text position is measured against (the bar, or the data of the step);
``evaluation_picture`` the Evaluation menu (a baseline bar and a signal bar over a stepped trace); ``add_step_at_cursor_icon`` / ``add_step_before_icon`` / ``add_step_after_icon`` /
``remove_step_icon`` / ``merge_steps_icon`` / ``split_step_icon`` / ``restore_steps_icon`` the buttons of the Edit group (2026-11-10), each a few outlined boxes standing for steps;
the step that a command changes is in an accent colour (add green, remove red, restore blue; split has two ink halves and a gold cut line, merge is all ink).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath

from ..spectra.pick_menu import PictureFor
from .stat_icons import _RENDER_SIZE, _canvas, _pen, _picture, _polyline, _slash, _at, _STROKE


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


def _step_rect(painter: QPainter, color: QColor, left: float, top: float, right: float, bottom: float, *, ghost: bool = False) -> None:
    """One step of the plan as an outlined box (0..1 of the icon). ``ghost``: a dashed outline, the step that is being made or taken away."""
    pen = _pen(color, _STROKE * 0.85)
    if ghost:
        pen.setDashPattern([1.5, 1.3])  # in pen widths: short dashes, so even a box this small shows several
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(_at(left, top), _at(right, bottom)))


def _step_and_ghost(color: str, accent: str, symbol, *, ghost_left: bool = False) -> QIcon:
    """A step in the normal ink beside a dashed one, the step the command is about, drawn with its symbol in ``accent``: the icon has two colours, and the coloured one is the step
    that changes. ``ghost_left``: the dashed step is on the left (in front of the other one in time) instead of the right. ``symbol(painter)`` draws inside the dashed box with the
    painter's pen already set to ``accent`` (written for the right-hand box; moved with the box). Greyed by Qt when the button is disabled."""
    pixmap, painter = _canvas()
    ink_box, ghost_box = ((0.55, 0.20, 0.95, 0.80), (0.05, 0.20, 0.45, 0.80)) if ghost_left else ((0.05, 0.20, 0.45, 0.80), (0.55, 0.20, 0.95, 0.80))
    _step_rect(painter, QColor(color), *ink_box)
    mark = QColor(accent)
    _step_rect(painter, mark, *ghost_box, ghost=True)
    painter.setPen(_pen(mark))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if ghost_left:
        painter.translate(-0.5 * _RENDER_SIZE, 0.0)  # the symbols are drawn in the right-hand box: the left one is half an icon away
    symbol(painter)
    painter.end()
    return QIcon(pixmap)


def _plus(painter: QPainter) -> None:
    painter.drawLine(_at(0.64, 0.50), _at(0.86, 0.50))
    painter.drawLine(_at(0.75, 0.39), _at(0.75, 0.61))


def _cross(painter: QPainter) -> None:
    painter.drawLine(_at(0.66, 0.39), _at(0.84, 0.61))
    painter.drawLine(_at(0.84, 0.39), _at(0.66, 0.61))


def _return_arrow(painter: QPainter) -> None:
    """The usual "back" arrow: its head on the left, the shaft running right and hooking back below."""
    painter.drawLine(_at(0.72, 0.38), _at(0.66, 0.45))  # the head
    painter.drawLine(_at(0.66, 0.45), _at(0.72, 0.52))
    hook = QPainterPath(_at(0.66, 0.45))  # the shaft, then a half turn back below it
    hook.lineTo(_at(0.80, 0.45))
    hook.cubicTo(_at(0.88, 0.45), _at(0.88, 0.60), _at(0.80, 0.60))
    hook.lineTo(_at(0.75, 0.60))
    painter.drawPath(hook)


def add_step_after_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "add a step behind the selected one" (``accent``: green): the selected step, then a dashed one, the new step, with a plus inside it."""
    return _step_and_ghost(color, accent, _plus)


def add_step_before_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "add a step in front of the selected one" (``accent``: green): the dashed new step with a plus inside it, then the selected step."""
    return _step_and_ghost(color, accent, _plus, ghost_left=True)


def add_step_at_cursor_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "add a step at the cursor" (``accent``: green): a vertical cursor line in ink and, starting at it and running right, a dashed new step with a plus inside it.
    No other step is drawn: none has to be chosen."""
    pixmap, painter = _canvas()
    mark = QColor(accent)
    _step_rect(painter, mark, 0.20, 0.30, 0.95, 0.70, ghost=True)
    painter.setPen(_pen(mark))
    painter.drawLine(_at(0.47, 0.50), _at(0.69, 0.50))
    painter.drawLine(_at(0.58, 0.39), _at(0.58, 0.61))
    painter.setPen(_pen(QColor(color)))
    painter.drawLine(_at(0.20, 0.08), _at(0.20, 0.92))  # the cursor, drawn over the dashed box's left edge
    painter.end()
    return QIcon(pixmap)


def remove_step_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "remove a step" (``accent``: red): a step next to a dashed one, the step that goes, with a cross inside it."""
    return _step_and_ghost(color, accent, _cross)


def restore_steps_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "restore removed steps" (``accent``: blue): a step next to a dashed one, the step that was taken away, with a return arrow inside it."""
    return _step_and_ghost(color, accent, _return_arrow)


def split_step_icon(color: str, accent: str) -> QIcon:
    """The Edit group's "split the step at the cursor" (``accent``: gold): a step cut in two, the halves a little apart, with the cut, a line taller than the step, in ``accent``."""
    pixmap, painter = _canvas()
    ink = QColor(color)
    _step_rect(painter, ink, 0.05, 0.28, 0.44, 0.72)
    _step_rect(painter, ink, 0.56, 0.28, 0.95, 0.72)
    painter.setPen(_pen(QColor(accent), _STROKE * 1.1))
    painter.drawLine(_at(0.50, 0.08), _at(0.50, 0.92))
    painter.end()
    return QIcon(pixmap)


def merge_steps_icon(color: str) -> QIcon:
    """The Edit group's "merge steps": two steps side by side, an arrow down and one wide step below (two steps become one)."""
    pixmap, painter = _canvas()
    ink = QColor(color)
    _step_rect(painter, ink, 0.05, 0.08, 0.47, 0.34)
    _step_rect(painter, ink, 0.53, 0.08, 0.95, 0.34)
    painter.setPen(_pen(ink))
    painter.drawLine(_at(0.50, 0.42), _at(0.50, 0.62))
    painter.drawLine(_at(0.50, 0.62), _at(0.39, 0.51))
    painter.drawLine(_at(0.50, 0.62), _at(0.61, 0.51))
    _step_rect(painter, ink, 0.05, 0.70, 0.95, 0.94)
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


def bulk_picture() -> PictureFor:
    """The S_bulk menu (2026-11-12): a frame with a rising fit line in the accent colour and three points with error bars on it (shift against refractive index)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        _frame(painter, ink)
        painter.setPen(_pen(QColor(theme.accent_blue), 1.4))
        painter.drawLine(QPointF(3.5, 15.5), QPointF(16.5, 4.5))
        painter.setPen(_pen(ink, 1.0))
        for x, y, half in ((5.5, 13.2, 2.0), (10.0, 10.0, 2.6), (14.5, 6.0, 1.8)):
            painter.drawLine(QPointF(x, y - half), QPointF(x, y + half))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in ((5.5, 13.2), (10.0, 10.0), (14.5, 6.0)):
            painter.drawEllipse(QPointF(x, y), 1.5, 1.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)

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


__all__ = [
    "bulk_picture",
    "add_step_after_icon", "add_step_at_cursor_icon", "add_step_before_icon", "align_picture", "evaluation_picture", "merge_steps_icon", "plan_bar_icon", "position_picture",
    "remove_step_icon", "restore_steps_icon", "split_step_icon", "text_content_picture",
]
