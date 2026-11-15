"""The four selection-mode icons of the selection toolbar, drawn as Venn diagrams (2026-11-15, maintainer: "replace selection mode with 4 icons
for each mode representing boolean operation using a venn diagram, colored what is happening").

**What the picture says.** Two overlapping discs: the left one, **A**, is the selection as it is now; the right one, **B**, is the new region
(the rectangle, the wand's feature, the brush stroke ...). That makes three areas: *A only*, *both*, *B only*. Each mode does something
different to each of them (`MODE_AREAS`, one table, checked against `AreaSelectionModule.combine_region` by a test):

| mode | A only | both | B only | the picture |
|---|---|---|---|---|
| Replace   | dropped  | selected | selected | the right disc blue |
| Add       | selected | selected | selected | both discs blue (the union) |
| Subtract  | selected | **taken away** | - | the left disc blue, the overlap orange |
| Intersect | dropped  | selected | dropped | the overlap blue |

**Colours.** Blue = ends up selected (the app's selection / Add colour, `ADD_COLOR`); orange = taken away from the old selection (the app's
Subtract colour, `SUBTRACT_COLOR`); nothing = not selected. The two outlines follow the theme's text colour, so the empty parts stay readable
on a dark and on a light theme. The fills do not change with the theme (the same rule as `mask_edit_tool_icons.py`: a colour that *means*
something is not recoloured).

**The picked mode** is drawn into the icon itself as a rounded frame (the "on" state of the icon): the toolbar's buttons are 18 px wide with no
room for a border or a background, and Qt picks the "on" picture for a checked button by itself.

Hand-painted with `QPainter` like `mask_edit_tool_icons.py`: a multi-colour glyph is what the single-colour SVG loader (`load_tabler_icon`)
cannot do. Drawn at 24 x 24 design units on a 2x pixmap, cached.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from ...selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR

SELECTED = "selected"  # ends up in the selection
REMOVED = "removed"  # was selected, is taken away
NOT_SELECTED = "none"

# (A only, both, B only): what each mode does to the three areas of the Venn picture. Read by the drawing below and pinned against the real
# combine rules in `tests/unit/test_lspri_combine_mode_icons.py`.
MODE_AREAS: dict[str, tuple[str, str, str]] = {
    REPLACE: (NOT_SELECTED, SELECTED, SELECTED),
    ADD: (SELECTED, SELECTED, SELECTED),
    SUBTRACT: (SELECTED, REMOVED, NOT_SELECTED),
    INTERSECT: (NOT_SELECTED, SELECTED, NOT_SELECTED),
}

_FILL = {SELECTED: ADD_COLOR, REMOVED: SUBTRACT_COLOR}

_DESIGN = 24.0
_RENDER = 48  # 2x
_RADIUS = 6.6
_CENTRE_A = QPointF(9.2, 12.0)
_CENTRE_B = QPointF(14.8, 12.0)
_OUTLINE_WIDTH = 1.5
_FRAME_COLOR = "#38bdf8"  # the toolbar's "armed" blue
_FRAME_WIDTH = 1.7


def _disc(centre: QPointF) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(centre, _RADIUS, _RADIUS)
    return path


def area_paths() -> tuple[QPainterPath, QPainterPath, QPainterPath]:
    """The three areas as paths: A only, both, B only."""
    a, b = _disc(_CENTRE_A), _disc(_CENTRE_B)
    return a.subtracted(b), a.intersected(b), b.subtracted(a)


def _painter(pixmap: QPixmap) -> QPainter:
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_RENDER / _DESIGN, _RENDER / _DESIGN)
    return painter


def _draw_frame(painter: QPainter) -> None:
    painter.setPen(QPen(QColor(_FRAME_COLOR), _FRAME_WIDTH))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = _FRAME_WIDTH / 2.0
    painter.drawRoundedRect(QRectF(inset, inset, _DESIGN - 2 * inset, _DESIGN - 2 * inset), 3.5, 3.5)


_FRAMED: dict[str, QIcon] = {}


def framed_icon(key: str, base: Callable[[], QIcon]) -> QIcon:
    """`base()` (a fixed multi-colour icon) with a second, "on" picture that has the armed frame of the toolbar's tool buttons (cached by `key`).
    A checkable `QToolButton` shows the "on" picture while it is checked; Tabler icons of the toolbar turn blue instead, these cannot (their colours
    mean something)."""
    cached = _FRAMED.get(key)
    if cached is not None:
        return cached
    off = base().pixmap(_RENDER, _RENDER)
    on = QPixmap(off)
    painter = QPainter(on)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_RENDER / _DESIGN, _RENDER / _DESIGN)
    _draw_frame(painter)
    painter.end()
    icon = QIcon()
    icon.addPixmap(off, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(on, QIcon.Mode.Normal, QIcon.State.On)
    _FRAMED[key] = icon
    return icon


def _venn(how: str, outline: str, framed: bool) -> QPixmap:
    pixmap = QPixmap(_RENDER, _RENDER)
    painter = _painter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    for path, state in zip(area_paths(), MODE_AREAS[how]):
        if state in _FILL:
            painter.setBrush(QColor(_FILL[state]))
            painter.drawPath(path)
    pen = QPen(QColor(outline), _OUTLINE_WIDTH)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_disc(_CENTRE_A))
    painter.drawPath(_disc(_CENTRE_B))
    if framed:
        _draw_frame(painter)
    painter.end()
    return pixmap


@functools.lru_cache(maxsize=None)
def combine_mode_icon(how: str, outline: str) -> QIcon:
    """The Venn icon of mode `how` (`REPLACE` / `ADD` / `SUBTRACT` / `INTERSECT`); `outline` is the colour of the two circle outlines (the
    theme's text colour). The "on" picture (a checked button) has the frame."""
    if how not in MODE_AREAS:
        raise ValueError(f"how must be one of {tuple(MODE_AREAS)}, got {how!r}")
    icon = QIcon()
    icon.addPixmap(_venn(how, outline, framed=False), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_venn(how, outline, framed=True), QIcon.Mode.Normal, QIcon.State.On)
    return icon
