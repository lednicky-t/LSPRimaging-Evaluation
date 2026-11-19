"""The ROI table's Geometry / Simple toggle icon (2026-11-19): a small part with its **dimension lines**, as in a technical drawing, a horizontal one under it and a vertical one beside it, each with
an arrow at both ends and extension lines from the part's corners. Painted, not a vendored file (`lspr_ui/ICONS.md`: the library has no dimensioning icon), in the style of the Sensorgram's
pictures (`sensorgram/plan_icons.py`): drawn in 20 x 20 shown pixels in the colour it is asked for (the accent colour while the Geometry view is on)."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPolygonF

from ..sensorgram.stat_icons import _pen, _picture  # (the shared painter helpers of the pictograms)
from ..spectra.pick_menu import PictureFor

_ARROW_LENGTH = 2.6
_ARROW_HALF_WIDTH = 1.15


def _arrow(painter: QPainter, tip: QPointF, along_x: float, along_y: float) -> None:
    """A small filled arrowhead with its point at `tip`, the head lying along (`along_x`, `along_y`) (a unit step from the tip into the line)."""
    base = QPointF(tip.x() + along_x * _ARROW_LENGTH, tip.y() + along_y * _ARROW_LENGTH)
    across = QPointF(-along_y * _ARROW_HALF_WIDTH, along_x * _ARROW_HALF_WIDTH)
    painter.drawPolygon(QPolygonF([tip, base + across, base - across]))


def dimensions_picture() -> PictureFor:
    """A rectangle (the part) with a width dimension under it and a height dimension beside it."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(ink, 1.4))
        painter.drawRect(QRectF(3.0, 3.0, 10.0, 8.0))  # the part
        painter.setPen(_pen(ink, 0.9))  # dimension and extension lines are thin
        painter.drawLine(QPointF(3.0, 12.2), QPointF(3.0, 17.6))  # extension lines from the corners ...
        painter.drawLine(QPointF(13.0, 12.2), QPointF(13.0, 17.6))
        painter.drawLine(QPointF(14.2, 3.0), QPointF(18.6, 3.0))
        painter.drawLine(QPointF(14.2, 11.0), QPointF(18.6, 11.0))
        painter.drawLine(QPointF(3.0, 15.6), QPointF(13.0, 15.6))  # ... and the dimension lines between them
        painter.drawLine(QPointF(16.6, 3.0), QPointF(16.6, 11.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        _arrow(painter, QPointF(3.0, 15.6), 1.0, 0.0)
        _arrow(painter, QPointF(13.0, 15.6), -1.0, 0.0)
        _arrow(painter, QPointF(16.6, 3.0), 0.0, 1.0)
        _arrow(painter, QPointF(16.6, 11.0), 0.0, -1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    return _picture(draw_into)
