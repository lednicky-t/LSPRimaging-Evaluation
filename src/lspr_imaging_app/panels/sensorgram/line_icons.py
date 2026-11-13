"""Pictures of the View tab's "Points & lines" menu (2026-11-13), in the style of the baseline shape pictures (constant, linear, ... `baseline_controls.shape_picture`): **three dots in the
text colour and the line through them in the gold accent**, drawn in 20 x 20 shown pixels (`stat_icons._picture`). Not vendored icon files (`lspr_ui/ICONS.md`).

``joint_picture`` the Straight / Spline choice: the same three dots joined by straight segments (a sharp point at the middle dot) or by a smooth curve through them (a round hill),
so that the picture shows what the choice does. The curve is the very one the plot draws, `curve_smoothing.smooth_run` (PCHIP: it turns round exactly at the dot and never overshoots), so
the picture does not promise more than the plot does. ``points_lines_icon`` the menu's face: the straight picture.

``points_toggle_icon`` and ``lines_toggle_icon`` (the Points and Lines show / hide toggles of the same menu): the same picture split in two, the three dots alone and the line alone, in the same
colours when on, both in the dim colour and **crossed out** when off, as the other toggles of the View tab. How the design was reached, with the measurements: `docs/sensorgram_view_tab_2026-11-13.md`.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath

from ..spectra.pick_menu import PictureFor
from .curve_smoothing import smooth_run
from .stat_icons import _pen, _picture

_DOTS = ((3.0, 15.0), (10.0, 5.0), (17.0, 13.0))  # three dots, in 20 x 20 shown pixels: the baseline shape pictures' three anchor dots, a little further apart in height
_DOT_RADIUS = 1.6  # as the baseline shape pictures' dots
_LINE_WIDTH = 1.4  # and their curve
_CURVE_PER_INTERVAL = 16  # drawn points between two dots: smooth at any size a picture is shown


def _straight_path() -> QPainterPath:
    path = QPainterPath(QPointF(*_DOTS[0]))
    for x, y in _DOTS[1:]:
        path.lineTo(QPointF(x, y))
    return path


def _smooth_path() -> QPainterPath:
    """The smooth curve through the dots: the plot's own PCHIP (`curve_smoothing.smooth_run`), drawn as a path."""
    xs, ys = smooth_run(np.array([x for x, _y in _DOTS]), np.array([y for _x, y in _DOTS]), _CURVE_PER_INTERVAL)
    path = QPainterPath(QPointF(float(xs[0]), float(ys[0])))
    for x, y in zip(xs[1:], ys[1:]):
        path.lineTo(QPointF(float(x), float(y)))
    return path


def joint_picture(kind: str) -> PictureFor:
    """The picture of one way to join the points: ``"straight"`` or ``"spline"``. Three dots in the colour it is drawn in (the text colour), the line through them in the gold accent."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        painter.setPen(_pen(QColor(theme.accent_gold), _LINE_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_smooth_path() if kind == "spline" else _straight_path())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in _DOTS:
            painter.drawEllipse(QPointF(x, y), _DOT_RADIUS, _DOT_RADIUS)

    return _picture(draw_into)


_TOGGLE_DOT_RADIUS = 2.0  # a little larger than the choice pictures' dots: these stand alone
_TOGGLE_LINE_WIDTH = 1.8
_SLASH = (QPointF(17.6, 2.4), QPointF(2.4, 17.6))  # the "off" stroke, from the top right to the bottom left corner, as the other toggles'


def _toggle_icon(draw_visible) -> Callable[[bool, str, str], QIcon]:
    """An `IconToggle` picture from ``draw_visible(painter, dots_color, line_color)``: on, the dots in the text colour and the line in the gold accent; off, both in `off_color`, crossed out."""

    def icon(on: bool, off_color: str, on_color: str) -> QIcon:
        def draw_into(painter: QPainter, ink: QColor, theme) -> None:
            dots, line = (QColor(theme.text_primary), QColor(theme.accent_gold)) if on else (ink, ink)
            draw_visible(painter, dots, line)
            if not on:
                painter.setPen(_pen(ink, _TOGGLE_LINE_WIDTH))
                painter.drawLine(*_SLASH)

        return QIcon(_picture(draw_into)(off_color))  # `ink` is only used off: the dim colour

    return icon


def _draw_three_dots(painter: QPainter, dots: QColor, _line: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(dots)
    for x, y in _DOTS:
        painter.drawEllipse(QPointF(x, y), _TOGGLE_DOT_RADIUS, _TOGGLE_DOT_RADIUS)


def _draw_line(painter: QPainter, _dots: QColor, line: QColor) -> None:
    painter.setPen(_pen(line, _TOGGLE_LINE_WIDTH))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_straight_path())


points_toggle_icon = _toggle_icon(_draw_three_dots)  # the Points toggle: the three dots alone
lines_toggle_icon = _toggle_icon(_draw_line)  # the Lines toggle: the line alone


def points_lines_icon(color: str) -> QIcon:
    """The Points & lines menu's face, in `color`: the dots joined by a line (the straight picture)."""
    return QIcon(joint_picture("straight")(color))
