"""Image panel ribbon, "Export" tab: the chunk-style pick-up menu that replaced the Find best dialog (2026-10-21).

Three styles, each a picture of what one chunk holds: one ROI, a column of ROIs, a row of ROIs.
Picking one only emits `style_picked(key)`; `ExportTab` turns it into a chunk size and a grid aligned to the ROIs
(`dataset.chunk_plan.suggest_layouts`). Nothing here stores state.

The icons are drawn with `QPainter` (the Tabler set has no such pictures): pink filled circles are the ROIs, the dotted
squares / bars are the chunks.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme

from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_MARGIN = _RENDER_SIZE * 0.08
_ROI_PINK = "#ec4899"

# key (a `chunk_plan.Suggestion.key`), menu text
STYLES = (
    ("per_roi", "1 ROI per chunk"),
    ("columns", "Columns: one chunk per column of ROIs"),
    ("rows", "Rows: one chunk per row of ROIs"),
)


def style_icon(style: str, frame_color: str) -> QIcon:
    """The picture for ``style``: dotted chunk borders in ``frame_color`` around pink ROI circles."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    lo, hi = _MARGIN, _RENDER_SIZE - _MARGIN
    span = hi - lo

    def circles(count_x: int, count_y: int, cell: float, ox: float, oy: float, diameter: float) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_ROI_PINK))
        for iy in range(count_y):
            for ix in range(count_x):
                cx, cy = ox + (ix + 0.5) * cell, oy + (iy + 0.5) * cell
                painter.drawEllipse(QRectF(cx - diameter / 2, cy - diameter / 2, diameter, diameter))

    def chunk(rect: QRectF) -> None:
        pen = QPen(QColor(frame_color), 1.6)
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    if style == "per_roi":
        chunk(QRectF(lo, lo, span, span))
        circles(1, 1, span, lo, lo, span * 0.46)
    else:  # columns / rows: a 3 x 3 array, framed three times
        cell = span / 3
        for k in range(3):
            if style == "columns":
                chunk(QRectF(lo + k * cell + 0.5, lo, cell - 1.0, span))
            else:
                chunk(QRectF(lo, lo + k * cell + 0.5, span, cell - 1.0))
        circles(3, 3, cell, lo, lo, cell * 0.50)
    painter.end()
    return QIcon(pixmap)


class ChunkStylePicker(QToolButton):
    """A button that pops up the three styles; the button's own icon is set by the owner (`ExportTab._refresh_icons`)."""

    style_picked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self._actions: dict[str, QAction] = {}
        for key, text in STYLES:
            action = QAction(text, self._menu)
            action.triggered.connect(lambda _checked=False, picked=key: self.style_picked.emit(picked))
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)
        self.refresh_menu_icons()

    def refresh_menu_icons(self) -> None:
        color = get_active_theme().text_primary
        for key, action in self._actions.items():
            action.setIcon(style_icon(key, color))
