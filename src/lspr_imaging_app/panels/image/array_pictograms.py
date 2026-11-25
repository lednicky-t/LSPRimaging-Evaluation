"""The small drawings of the Array pop-out: one pictogram per setting, drawn here (not Tabler icons).

Why drawn and not vendored: a setting like "pitch" or "start corner" is a picture of *lattice dots with the one measure highlighted*, and no icon set has
those. Each drawing shows dim dots (the lattice) and the measure in the accent colour (an arrow, a bracket, an arc), so the field next to it reads without
its label. They are redrawn on a theme change (`refresh_theme`); sizes are in a 24 x 24 logical box, rendered at the screen's device pixel ratio.

Pure painting: no state, no signals. `pictogram_icon(kind)` is also what the type buttons use.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QToolButton, QWidget

from lspr_ui import get_active_theme

BOX = 24
"""Logical side of every drawing."""
DOT = 1.9
KINDS = (
    "type_rectangular", "type_triangular", "cell_square", "cell_rectangle", "cell_equilateral", "cell_triangle",
    "apply", "cancel", "rows", "cols", "pitch_x", "pitch_y", "step_x", "step_y", "angle", "row_offset",
)


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _dots(p: QPainter, points: list[tuple[float, float]], color: str) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    for x, y in points:
        p.drawEllipse(QPointF(x, y), DOT, DOT)


def _arrow(p: QPainter, a: tuple[float, float], b: tuple[float, float], color: str, *, both: bool = True) -> None:
    """A line from ``a`` to ``b`` with a head at ``b`` (and at ``a`` when ``both``)."""
    p.setPen(_pen(color, 1.5))
    p.drawLine(QPointF(*a), QPointF(*b))
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    ends = [(b, angle + math.pi)] + ([(a, angle)] if both else [])
    for (x, y), direction in ends:
        for turn in (0.5, -0.5):
            p.drawLine(QPointF(x, y), QPointF(x + 3.6 * math.cos(direction + turn), y + 3.6 * math.sin(direction + turn)))


def _square_lattice(rows: int, cols: int, x0: float, y0: float, step: float) -> list[tuple[float, float]]:
    return [(x0 + c * step, y0 + r * step) for r in range(rows) for c in range(cols)]


def _draw_type_rectangular(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, _square_lattice(3, 3, 5.0, 5.0, 7.0), dim)
    p.setPen(_pen(accent, 1.0))
    p.drawLine(QPointF(5, 5), QPointF(19, 5))
    p.drawLine(QPointF(5, 5), QPointF(5, 19))


def _draw_type_triangular(p: QPainter, dim: str, accent: str) -> None:
    rows = [(5.0, [4.0, 12.0, 20.0]), (12.0, [8.0, 16.0]), (19.0, [4.0, 12.0, 20.0])]
    _dots(p, [(x, y) for y, xs in rows for x in xs], dim)
    p.setPen(_pen(accent, 1.0))
    p.drawLine(QPointF(4, 5), QPointF(8, 12))
    p.drawLine(QPointF(8, 12), QPointF(12, 5))
    p.drawLine(QPointF(4, 5), QPointF(12, 5))


def _cell(p: QPainter, corners: list[tuple[float, float]], dim: str, accent: str) -> None:
    """One lattice cell: its outline in the accent colour, a dot on every corner."""
    p.setPen(_pen(accent, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for a, b in zip(corners, corners[1:] + corners[:1], strict=True):
        p.drawLine(QPointF(*a), QPointF(*b))
    _dots(p, corners, accent)


def _draw_cell_square(p: QPainter, dim: str, accent: str) -> None:
    _cell(p, [(6.0, 6.0), (18.0, 6.0), (18.0, 18.0), (6.0, 18.0)], dim, accent)


def _draw_cell_rectangle(p: QPainter, dim: str, accent: str) -> None:
    _cell(p, [(3.0, 7.5), (21.0, 7.5), (21.0, 16.5), (3.0, 16.5)], dim, accent)


def _draw_cell_equilateral(p: QPainter, dim: str, accent: str) -> None:
    _cell(p, [(12.0, 4.5), (19.5, 17.5), (4.5, 17.5)], dim, accent)


def _draw_cell_triangle(p: QPainter, dim: str, accent: str) -> None:
    _cell(p, [(15.5, 5.5), (21.0, 18.0), (3.5, 18.0)], dim, accent)  # lopsided: not all sides, not all angles equal


def _draw_apply(p: QPainter, dim: str, accent: str) -> None:
    """A tick: make the array."""
    p.setPen(_pen(accent, 2.6))
    p.drawPolyline([QPointF(4.5, 12.5), QPointF(9.5, 17.5), QPointF(19.5, 6.5)])


def _draw_cancel(p: QPainter, dim: str, accent: str) -> None:
    """A cross: close without making anything."""
    p.setPen(_pen(accent, 2.6))
    p.drawLine(QPointF(6.0, 6.0), QPointF(18.0, 18.0))
    p.drawLine(QPointF(18.0, 6.0), QPointF(6.0, 18.0))


def _draw_cols(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, _square_lattice(3, 3, 5.0, 4.0, 7.0), dim)
    _arrow(p, (5.0, 20.5), (19.0, 20.5), accent)


def _draw_rows(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, _square_lattice(3, 3, 8.0, 5.0, 7.0), dim)
    _arrow(p, (3.5, 5.0), (3.5, 19.0), accent)


def _draw_pitch_x(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, [(4.5, 12.0), (19.5, 12.0)], dim)
    _arrow(p, (7.5, 12.0), (16.5, 12.0), accent)


def _draw_pitch_y(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, [(12.0, 4.5), (12.0, 19.5)], dim)
    _arrow(p, (12.0, 7.5), (12.0, 16.5), accent)


def _draw_step_x(p: QPainter, dim: str, accent: str) -> None:
    xs = [3.0, 7.5, 14.0, 22.0]  # gaps 4.5, 6.5, 8: each wider than the last
    _dots(p, [(x, 8.0) for x in xs], dim)
    p.setPen(_pen(accent, 1.5))
    for a, b in zip(xs, xs[1:], strict=False):
        p.drawLine(QPointF(a + 1.5, 15.0), QPointF(b - 1.5, 15.0))
        p.drawLine(QPointF(a + 1.5, 13.0), QPointF(a + 1.5, 17.0))
        p.drawLine(QPointF(b - 1.5, 13.0), QPointF(b - 1.5, 17.0))


def _draw_step_y(p: QPainter, dim: str, accent: str) -> None:
    ys = [3.0, 7.5, 14.0, 22.0]
    _dots(p, [(8.0, y) for y in ys], dim)
    p.setPen(_pen(accent, 1.5))
    for a, b in zip(ys, ys[1:], strict=False):
        p.drawLine(QPointF(15.0, a + 1.5), QPointF(15.0, b - 1.5))
        p.drawLine(QPointF(13.0, a + 1.5), QPointF(17.0, a + 1.5))
        p.drawLine(QPointF(13.0, b - 1.5), QPointF(17.0, b - 1.5))


def _draw_angle(p: QPainter, dim: str, accent: str) -> None:
    origin = (4.5, 5.0)
    _dots(p, [(origin[0] + k * 7.0, origin[1]) for k in range(3)], dim)  # the lattice row as it was
    tilt = math.radians(32.0)  # positive = clockwise on screen (y points down)
    turned = [(origin[0] + k * 7.0 * math.cos(tilt), origin[1] + k * 7.0 * math.sin(tilt)) for k in range(1, 3)]
    _dots(p, turned, accent)
    p.setPen(_pen(accent, 1.3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(origin[0] - 9.0, origin[1] - 9.0, 18.0, 18.0), 0, -32 * 16)  # a negative span runs clockwise on screen


def _draw_row_offset(p: QPainter, dim: str, accent: str) -> None:
    _dots(p, [(5.0, 7.0), (12.0, 7.0), (19.0, 7.0), (8.5, 17.0), (15.5, 17.0)], dim)
    _arrow(p, (3.5, 20.5), (8.5, 20.5), accent, both=False)
    p.setPen(_pen(accent, 1.0))
    p.drawLine(QPointF(5.0, 17.5), QPointF(5.0, 23.0))  # the row start it is measured from


_DRAWINGS: dict[str, Callable[[QPainter, str, str], None]] = {
    "type_rectangular": _draw_type_rectangular,
    "type_triangular": _draw_type_triangular,
    "cell_square": _draw_cell_square,
    "cell_rectangle": _draw_cell_rectangle,
    "cell_equilateral": _draw_cell_equilateral,
    "cell_triangle": _draw_cell_triangle,
    "apply": _draw_apply,
    "cancel": _draw_cancel,
    "rows": _draw_rows,
    "cols": _draw_cols,
    "pitch_x": _draw_pitch_x,
    "pitch_y": _draw_pitch_y,
    "step_x": _draw_step_x,
    "step_y": _draw_step_y,
    "angle": _draw_angle,
    "row_offset": _draw_row_offset,
}


def pictogram_pixmap(kind: str, *, dim: str | None = None, accent: str | None = None, ratio: float = 2.0) -> QPixmap:
    """The drawing of ``kind`` (one of `KINDS`) at ``ratio`` times its logical size, in the theme's colours unless given."""
    if kind not in _DRAWINGS:
        raise ValueError(f"unknown pictogram {kind!r}; known: {KINDS}")
    theme = get_active_theme()
    pixmap = QPixmap(round(BOX * ratio), round(BOX * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _DRAWINGS[kind](painter, dim or theme.text_dim, accent or theme.accent_blue)
    painter.end()
    return pixmap


def pictogram_icon(kind: str, *, dim: str | None = None, accent: str | None = None) -> QIcon:
    return QIcon(pictogram_pixmap(kind, dim=dim, accent=accent))


class FlatIconButton(QToolButton):
    """A drawing you can click, with no button around it: no frame, no background, not even on hover (the icon itself says what is on)."""

    def __init__(self, tip: str, parent: QWidget | None = None, *, size: int = 28) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size + 4, size + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0px; }")
        self.setToolTip(tip)


class Pictogram(QLabel):
    """A label that shows one drawing; `refresh_theme` redraws it."""

    def __init__(self, kind: str, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(BOX, BOX)
        self.setToolTip(tip)
        self.refresh_theme()

    def kind(self) -> str:
        return self._kind

    def refresh_theme(self) -> None:
        self.setPixmap(pictogram_pixmap(self._kind))
