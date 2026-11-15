"""The four selection-mode icons of the selection toolbar, drawn as Venn diagrams (2026-11-15, maintainer: "replace selection mode with 4 icons
for each mode representing boolean operation using a venn diagram, colored what is happening").

**What the picture says.** Two overlapping discs: the left one, **A**, is the selection as it is now; the right one, **B**, is the new region
(the rectangle, the wand's feature, the brush stroke ...). That makes three areas: *A only*, *both*, *B only*. Each mode does something
different to each of them (`MODE_AREAS`, one table, checked against `AreaSelectionModule.combine_region` by a test):

| mode | A only | both | B only | the picture |
|---|---|---|---|---|
| Replace   | dropped  | selected | selected | the right disc grey |
| Add       | selected | selected | selected | both discs grey (the union) |
| Subtract  | selected | **taken away** | - | the left disc grey, the overlap empty |
| Intersect | dropped  | selected | dropped | the overlap grey |

**Colours** (`selection_palette.py`, 2026-11-16, the maintainer's design). The circle outlines keep the two colours the image uses for them: the left
circle (the selection now) is pink, the right circle (the new region) green. The fill is the **result**, in **grey** (`ICON_SELECTED_FILL`): what ends
up selected. **Everything else is the plain background**: not selected, and also what is taken away (it had an orange fill until 2026-11-16, which had
almost no contrast with the pink and green lines: 1.1 and 1.6 : 1; the maintainer: "remove the red infill representing subtraction with normal
background representing nothing"), so the Subtract icon is the left disc grey with an empty lens. (The fill was blue first, 1.2 : 1 against the lines.)
The grey is a mid one, picked by measuring: 2.1 : 1 against the pink line, 3.2 : 1 against the green, 3.3 : 1 against the near-black toolbar, 5.5 : 1
against a white one (a step lighter than the first grey `#525b69`, at the maintainer's request: that one was 2.6 / 3.9 / 2.6 / 6.9); pink and green
themselves are 6.8 : 1 and 10.4 : 1 against the near-black toolbar, so a circle with no fill stays visible. The icon does not follow the theme (the
same rule as `mask_edit_tool_icons.py`: a colour that *means* something is not recoloured). `MODE_AREAS` still tells "taken away" from "not
selected" (the table is checked against the real combine rules); only the drawing no longer does.

**The circle colours are the user's** (2026-11-16): the toolbar's View pop-up changes the outline colours (`selection_style.py`), and the icons follow:
`combine_mode_icon(how, past, present)` draws the left circle in `past` and the right in `present` (the palette's pink and green by default). The
fill stays the fixed grey whatever is picked.

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
from .selection_palette import ICON_SELECTED_FILL, PAST_COLOR, PRESENT_COLOR

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

_FILL = {SELECTED: ICON_SELECTED_FILL}  # the other two states, removed and not selected, are drawn as the plain background

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


def _venn(how: str, framed: bool, past: str, present: str) -> QPixmap:
    pixmap = QPixmap(_RENDER, _RENDER)
    painter = _painter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    for path, state in zip(area_paths(), MODE_AREAS[how]):
        if state in _FILL:
            painter.setBrush(QColor(_FILL[state]))
            painter.drawPath(path)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(past), _OUTLINE_WIDTH))  # A, the selection as it stands
    painter.drawPath(_disc(_CENTRE_A))
    painter.setPen(QPen(QColor(present), _OUTLINE_WIDTH))  # B, the new region
    painter.drawPath(_disc(_CENTRE_B))
    if framed:
        _draw_frame(painter)
    painter.end()
    return pixmap


def combine_mode_icon(how: str, past: str | None = None, present: str | None = None) -> QIcon:
    """The Venn icon of mode `how` (`REPLACE` / `ADD` / `SUBTRACT` / `INTERSECT`). The "on" picture (a checked button) has the frame.
    `past` and `present` are the border colours of the left and the right circle: the palette's pink and green unless the user picked others
    in the toolbar's View pop-up (`SelectionStyle`; the toolbar passes them)."""
    if how not in MODE_AREAS:
        raise ValueError(f"how must be one of {tuple(MODE_AREAS)}, got {how!r}")
    return _icon(how, QColor(past or PAST_COLOR).name(), QColor(present or PRESENT_COLOR).name())


@functools.lru_cache(maxsize=128)  # four modes per pair of colours: a few dozen at most in a session
def _icon(how: str, past: str, present: str) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_venn(how, False, past, present), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_venn(how, True, past, present), QIcon.Mode.Normal, QIcon.State.On)
    return icon
