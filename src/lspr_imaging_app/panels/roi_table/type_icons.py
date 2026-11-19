"""The Type column's pictograms (plan 2026-11-19, "Rim pairs, linked pairs, the Type column"): a ROI's shape, with a ring round it while it measures against its own
rim, filled in the colour of its **kind of pair**.

Kind colours (maintainer, 2026-11-19), for the Type icons only (not the dot of the first column, which is the ROI's own colour, and not the image or the plots):
**green** a sample with a rim or with links, **amber** a sample with neither (it has no reference, its results are missing), **blue** a reference some sample uses (blue
is the colour of a reference ring everywhere else), **gray** a reference nobody uses. The rim that is drawn round a shape is always the reference blue: it is a reference
region. Every colour comes from the active theme, read at paint time.

Painting only; the icon and the kind of each row are decided in `rows.icon_of` / `rows.kind_of`.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF

from .rows import (
    ICON_CIRCLE,
    ICON_CIRCLE_RIM,
    ICON_MAP,
    ICON_MAP_RIM,
    ICON_RECTANGLE,
    ICON_RECTANGLE_RIM,
    KIND_BARE,
    KIND_LINKED,
    KIND_REFERENCE_FREE,
    KIND_REFERENCE_USED,
    KIND_RIM,
)

_BLOB = ((-6, -2), (-3, -6), (2, -5), (6, -1), (4, 5), (-1, 6), (-5, 3))
"""The plain pixel map: the pictogram the table has always had for a mask."""
_JAGGED = ((-7, -1), (-4, -6), (0, -4), (3, -7), (7, -2), (5, 2), (7, 5), (1, 6), (-2, 4), (-6, 5))
"""A pixel map with a rim: a less uniform blob, so it does not read as the plain one; its rim is this outline, the map itself the same shape at half the size."""
_INNER = 0.5
_RIM_WIDTH = 1.6


def kind_color(kind: str, theme) -> str:
    """The colour (hex) of a kind of pair in the active theme."""
    return {
        KIND_RIM: theme.accent_green,
        KIND_LINKED: theme.accent_green,
        KIND_BARE: theme.accent_gold,  # amber
        KIND_REFERENCE_USED: theme.accent_blue,
        KIND_REFERENCE_FREE: theme.text_dim,
    }.get(kind, theme.text_dim)


def _polygon(centre: QPointF, points, scale: float = 1.0) -> QPolygonF:
    return QPolygonF([QPointF(centre.x() + dx * scale, centre.y() + dy * scale) for dx, dy in points])


def paint_type_icon(painter: QPainter, centre: QPointF, icon: str, kind: str, theme) -> None:
    """One pictogram, about 16 px across, centred on ``centre``. ``icon`` is a `rows.ICON_*` key, ``kind`` a `rows.KIND_*`; an unknown icon is a question mark."""
    fill = QColor(kind_color(kind, theme))
    outline = QPen(QColor(theme.text_secondary), 1.3)
    rim = QPen(QColor(theme.accent_blue), _RIM_WIDTH)
    painter.save()
    try:
        if icon == ICON_CIRCLE:
            painter.setPen(outline)
            painter.setBrush(fill)
            painter.drawEllipse(centre, 5.0, 5.0)
        elif icon == ICON_CIRCLE_RIM:
            painter.setPen(rim)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, 7.2, 7.2)
            painter.setPen(outline)
            painter.setBrush(fill)
            painter.drawEllipse(centre, 3.4, 3.4)
        elif icon == ICON_RECTANGLE:
            painter.setPen(outline)
            painter.setBrush(fill)
            painter.drawRect(QRectF(centre.x() - 7.0, centre.y() - 4.5, 14.0, 9.0))
        elif icon == ICON_RECTANGLE_RIM:
            painter.setPen(rim)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(centre.x() - 8.0, centre.y() - 6.0, 16.0, 12.0), 2.5, 2.5)
            painter.setPen(outline)
            painter.setBrush(fill)
            painter.drawRect(QRectF(centre.x() - 4.0, centre.y() - 2.5, 8.0, 5.0))
        elif icon == ICON_MAP:
            dashed = QPen(outline)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.setBrush(fill)
            painter.drawPolygon(_polygon(centre, _BLOB))
        elif icon == ICON_MAP_RIM:
            painter.setPen(rim)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(_polygon(centre, _JAGGED))
            dashed = QPen(outline)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.setBrush(fill)
            painter.drawPolygon(_polygon(centre, _JAGGED, _INNER))
        else:
            painter.setPen(QColor(theme.text_dim))
            painter.drawText(QRectF(centre.x() - 8.0, centre.y() - 8.0, 16.0, 16.0), int(Qt.AlignmentFlag.AlignCenter), "?")
    finally:
        painter.restore()
