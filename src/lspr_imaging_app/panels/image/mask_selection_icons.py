"""Two-pictogram "source -> target" icons of the Mask tab's Selection group (2026-11-16): the source is always top left, the target
bottom right, and a bent arrow in the free top-right quadrant leads from one to the other. Selection -> mask is Replace, mask ->
selection is Selection from mask.

The pictograms are the vendored Tabler ones (`select-rectangle` = a selection, `mask` = the mask, the same `mask` the tint toggle
uses); only the composition is painted here, like `combine_mode_icons.py`, because the single-colour SVG loader draws one glyph.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from lspr_ui import load_tabler_icon

from ..ribbon_icon_size import ICON_SIZE

SELECTION_GLYPH = "select-rectangle"
MASK_GLYPH = "mask"

_CANVAS = ICON_SIZE * 2  # drawn at 2x and scaled down, like every ribbon icon
_GLYPH_BOX = 0.68  # each pictogram's square, as a fraction of the canvas (its drawing has a margin, so the boxes may overlap)
_GLYPH_CENTRES = ((0.25, 0.25), (0.75, 0.75))  # source top left, target bottom right
_GLYPH_STROKE = 2.5  # in the glyph's own 24-unit space: ends up as thick as the 2.1 of a full-size icon, relative to the canvas
_LINE_WIDTH = 0.07  # the arrow's pen, as a fraction of the canvas
# The bent arrow, in canvas fractions: out of the source to the right, a rounded corner, down towards the target.
_ARROW_START = (0.54, 0.25)
_ARROW_CORNER = (0.75, 0.25)
_ARROW_TIP = (0.75, 0.47)
_ARROW_BEND = 0.06  # how far before the corner the turn begins
_ARROW_HEAD = 0.08


def transfer_icon(source: str, target: str, color: str) -> QIcon:
    """`source` pictogram top left, `target` pictogram bottom right, bent arrow top right. Full colour in the Disabled mode too,
    like `mask_edit_common.action_button`."""
    size = _CANVAS
    box = _GLYPH_BOX * size
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for name, (cx, cy) in zip((source, target), _GLYPH_CENTRES):
        glyph = load_tabler_icon(name, color=color, size=size, stroke_width=_GLYPH_STROKE).pixmap(size, size)
        painter.drawPixmap(QRectF(cx * size - box / 2, cy * size - box / 2, box, box), glyph, QRectF(0, 0, size, size))
    pen = QPen(QColor(color), _LINE_WIDTH * size)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    (sx, sy), (kx, ky), (tx, ty) = ((v * size for v in point) for point in (_ARROW_START, _ARROW_CORNER, _ARROW_TIP))
    bend = _ARROW_BEND * size
    path = QPainterPath(QPointF(sx, sy))
    path.lineTo(kx - bend, ky)
    path.quadTo(QPointF(kx, ky), QPointF(kx, ky + bend))
    path.lineTo(tx, ty)
    painter.drawPath(path)
    head = _ARROW_HEAD * size
    painter.drawLine(QPointF(tx, ty), QPointF(tx - head, ty - head))
    painter.drawLine(QPointF(tx, ty), QPointF(tx + head, ty - head))
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    return icon


def selection_to_mask_icon(color: str) -> QIcon:
    return transfer_icon(SELECTION_GLYPH, MASK_GLYPH, color)


def mask_to_selection_icon(color: str) -> QIcon:
    return transfer_icon(MASK_GLYPH, SELECTION_GLYPH, color)
