"""Painting for the ROI/Group table: the modern look lives here.

No grid lines. A ROI row is a colour dot, the number, then right-aligned
figures; a selected row gets a soft tint of the highlight colour. A group
header is a slightly raised bar with a chevron, a colour chip, the name in
bold and a count badge. Every colour is read from the active theme at paint
time, so a theme switch needs only a repaint.

Each cell paints its own background, so a row looks continuous across
columns without the view drawing anything behind it (the view's indentation
is 0 on purpose; child rows are indented here instead).
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QLineEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget

from lspr_ui import get_active_theme

from .model import (
    ALL_SELECTED_ROLE,
    COLOR_ROLE,
    COUNT_ROLE,
    DEPTH_ROLE,
    GROUP_ID_ROLE,
    INHERITED_ROLE,
    KIND_ROLE,
    MASK_ROLE,
)
from .rows import COLUMN_ID

STRIP_WIDTH = 3
"""Left edge of every ROI row, left clear for a status strip (analysis state)
to be drawn in later."""
GROUP_INDENT = 14
CHIP_SIZE = 10
CHEVRON_ZONE = 28
"""Width, from the left edge, of the part of a group header that toggles it."""
_CELL_PAD = 6


def row_height(font_metrics) -> int:
    return font_metrics.height() + 10


def group_row_height(font_metrics) -> int:
    return font_metrics.height() + 14


class RoiTableDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # type: ignore[override]
        # Only the height matters: the header sizes every column itself (stretch /
        # interactive, never "resize to contents"), and Qt takes a row's height as
        # the largest over its columns - so column 0 alone decides it. Not asking
        # the base class (which formats every cell's text again) nor the other
        # columns made `expandAll()` on 1500 ROIs 1.3 s -> 0.2 s (2026-10-07).
        if index.column() != 0:
            return QSize(0, 0)
        metrics = option.fontMetrics
        height = group_row_height(metrics) if index.data(KIND_ROLE) == "group" else row_height(metrics)
        return QSize(0, height)

    # -- painting -----------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            if index.data(KIND_ROLE) == "group":
                self._paint_group(painter, option, index)
            else:
                self._paint_roi(painter, option, index)
        finally:
            painter.restore()

    @staticmethod
    def _hovered(option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        view = option.widget
        checker = getattr(view, "is_hovered_row", None)
        return bool(checker(index)) if callable(checker) else bool(option.state & QStyle.StateFlag.State_MouseOver)

    @staticmethod
    def _selected_tint(theme) -> QColor:
        color = QColor(theme.highlight_color)
        color.setAlpha(70)
        return color

    def _paint_row_background(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, theme) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, self._selected_tint(theme))
        elif self._hovered(option, index):
            hover = QColor(theme.control_bg_hover)
            hover.setAlpha(150)
            painter.fillRect(option.rect, hover)

    def _paint_roi(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        theme = get_active_theme()
        rect = option.rect
        self._paint_row_background(painter, option, index, theme)
        column = index.column()
        font = QFont(option.font)
        color = QColor(theme.text_primary)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if index.data(INHERITED_ROLE) or (index.data(MASK_ROLE) and text == "mask"):
            font.setItalic(True)
            color = QColor(theme.text_dim)
        alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
        alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if alignment is None else Qt.AlignmentFlag(alignment)
        if column == COLUMN_ID:
            left = rect.left() + STRIP_WIDTH + 5 + (GROUP_INDENT if index.data(DEPTH_ROLE) else 0)
            self._paint_dot(painter, QRectF(left, rect.center().y() - CHIP_SIZE / 2 + 0.5, CHIP_SIZE, CHIP_SIZE), index.data(COLOR_ROLE), theme)
            text_rect = QRect(left + CHIP_SIZE + 7, rect.top(), max(0, rect.right() - left - CHIP_SIZE - 7), rect.height())
        else:
            text_rect = rect.adjusted(_CELL_PAD, 0, -_CELL_PAD - 2, 0)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(text_rect, int(alignment), option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()))

    @staticmethod
    def _paint_dot(painter: QPainter, rect: QRectF, color_hex: str | None, theme) -> None:
        painter.setBrush(QColor(color_hex or theme.text_dim))
        painter.setPen(QPen(QColor(theme.control_border), 1))
        painter.drawEllipse(rect)

    def _paint_group(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        theme = get_active_theme()
        rect = option.rect
        painter.fillRect(rect, QColor(theme.toolbar_section_bg))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected or index.data(ALL_SELECTED_ROLE):
            painter.fillRect(rect, self._selected_tint(theme))
        elif self._hovered(option, index):
            hover = QColor(theme.control_bg_hover)
            hover.setAlpha(110)
            painter.fillRect(rect, hover)
        painter.setPen(QPen(QColor(theme.toolbar_border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        view = option.widget
        expanded = bool(isinstance(view, QTreeView) and view.isExpanded(index))
        cx, cy = rect.left() + 12.0, rect.center().y() + 0.5
        chevron = (
            QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])
            if expanded
            else QPolygonF([QPointF(cx - 2, cy - 4), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 4)])
        )
        painter.setPen(QPen(QColor(theme.text_muted), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(chevron)

        ungrouped = index.data(GROUP_ID_ROLE) is None
        chip = QRectF(rect.left() + CHEVRON_ZONE, cy - 6, 12, 12)
        painter.setPen(QPen(QColor(theme.control_border), 1))
        painter.setBrush(QColor(index.data(COLOR_ROLE) or theme.text_dim))
        painter.drawRoundedRect(chip, 3, 3)

        count_text = str(index.data(COUNT_ROLE) or 0)
        metrics = option.fontMetrics
        pill_width = metrics.horizontalAdvance(count_text) + 14
        pill = QRectF(rect.right() - pill_width - 10, cy - 9, pill_width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.control_bg))
        painter.drawRoundedRect(pill, 9, 9)
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(pill.toRect(), int(Qt.AlignmentFlag.AlignCenter), count_text)

        font = QFont(option.font)
        font.setBold(not ungrouped)
        font.setItalic(ungrouped)
        painter.setFont(font)
        painter.setPen(QColor(theme.text_muted if ungrouped else theme.text_primary))
        name_left = int(chip.right()) + 9
        name_rect = QRect(name_left, rect.top(), max(0, int(pill.left()) - name_left - 8), rect.height())
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, name_rect.width()),
        )

    # -- editing --------------------------------------------------------------------

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> QWidget:  # type: ignore[override]
        editor = QLineEdit(parent)
        theme = get_active_theme()
        editor.setStyleSheet(
            f"QLineEdit {{ background: {theme.control_bg}; color: {theme.text_primary}; "
            f"border: 1px solid {theme.control_border_hover}; border-radius: 3px; padding: 0 4px; }}"
        )
        if index.data(KIND_ROLE) != "group" and index.column() not in (COLUMN_ID, 1):
            editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # type: ignore[override]
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            editor.selectAll()

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        rect = option.rect
        if index.data(KIND_ROLE) == "group":
            left = rect.left() + CHEVRON_ZONE + 21
            editor.setGeometry(left, rect.top() + 3, max(60, rect.width() - (left - rect.left()) - 52), rect.height() - 6)
        else:
            editor.setGeometry(rect.adjusted(2, 2, -2, -2))
