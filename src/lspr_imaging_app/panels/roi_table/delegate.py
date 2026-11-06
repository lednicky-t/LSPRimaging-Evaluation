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

from PyQt6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QLineEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip, QTreeView, QWidget

from lspr_ui import get_active_theme

from ...plan.bar_model import contrast_text_color
from ...shortcuts import matches
from .model import (
    ALL_SELECTED_ROLE,
    COLOR_ROLE,
    COUNT_ROLE,
    DEPTH_ROLE,
    GROUP_ID_ROLE,
    INHERITED_ROLE,
    KIND_ROLE,
    MASK_ROLE,
    SHAPE_ROLE,
    STEP_STALE_ROLE,
    STEP_TINT_ROLE,
)
from .rows import (
    COLUMN_COUNT,
    COLUMN_ID,
    COLUMN_SHAPE,
    DIAMETER_COLUMNS,
    SHAPE_DISK,
    SHAPE_MASK,
    SHAPE_RING,
    step_text,
)

STRIP_WIDTH = 3
"""Left edge of every ROI row, left clear for a status strip (analysis state)
to be drawn in later."""
GROUP_INDENT = 14
CHIP_SIZE = 10
CHEVRON_ZONE = 28
"""Width, from the left edge, of the part of a group header that toggles it."""
_CELL_PAD = 6
CELL_ROOM = 2 * _CELL_PAD + 4
"""Pixels a number needs around its text to show whole: `_CELL_PAD` on both sides, the 2 px more the right side keeps (`_paint_roi`, `_paint_group_steps`), and 2 px of slack."""
_ROW_LINE_ALPHA = 90
_COLUMN_LINE_ALPHA = 55
_STALE_TINTED_TEXT_ALPHA = 135  # a number that is out of date, on a coloured step column: the same text colour, fainter (`text_dim` could vanish into the colour)


def step_tint_of(index: QModelIndex) -> QColor | None:
    """The background of a step column's cell or header (the plan bar's fill of that step), or None for any other column."""
    tint = index.data(STEP_TINT_ROLE)
    return tint if isinstance(tint, QColor) and tint.isValid() else None


def tinted_text_color(tint: QColor, base: QColor, *, stale: bool = False) -> QColor:
    """Dark or light text for a cell filled with `tint` over `base`: judged on the blended colour as the bar judges its labels (`contrast_text_color`), so a number stays readable on
    any step colour and at any opacity."""
    color = QColor(contrast_text_color(tint.name(), tint.alphaF(), base.name()))
    if stale:
        color.setAlpha(_STALE_TINTED_TEXT_ALPHA)
    return color


def row_height(font_metrics) -> int:
    return font_metrics.height() + 10


def group_row_height(font_metrics) -> int:
    return font_metrics.height() + 14


class _StepLineEdit(QLineEdit):
    """The cell editor. In a diameter cell, Up / Down step the value (Shift: the
    large step); Enter then applies it, to every selected ROI like any edit."""

    def __init__(self, parent: QWidget, unit) -> None:
        super().__init__(parent)
        self._unit = unit  # None: this cell does not step

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        large = matches(event, "roi_table.cell_step_large")
        if self._unit is not None and (large or matches(event, "roi_table.cell_step")):
            stepped = step_text(self.text(), 1 if event.key() == Qt.Key.Key_Up else -1, large, self._unit)
            if stepped is not None:
                self.setText(stepped)
            event.accept()
            return
        super().keyPressEvent(event)


class RoiTableDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editing: QPersistentModelIndex | None = None  # the group header whose name is being edited

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
        tint = step_tint_of(index)
        if tint is not None:
            painter.fillRect(rect, tint)  # under the selection / hover tint, which therefore still shows
        self._paint_row_background(painter, option, index, theme)
        self._paint_cell_borders(painter, rect, index.column(), theme, index.model().columnCount() - 1)
        column = index.column()
        if column == COLUMN_ID and rect.width() <= 0:
            return  # #/ROIs is hidden (collapsed to nothing): its dot would be drawn over the next column
        if column == COLUMN_SHAPE:
            self._paint_shape(painter, rect, index, theme)
            return
        font = QFont(option.font)
        color = QColor(theme.text_primary)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if index.data(INHERITED_ROLE) or (index.data(MASK_ROLE) and text == "mask") or index.data(STEP_STALE_ROLE):
            color = QColor(theme.text_dim)  # dim, not italic: italics read as "different kind of value" (a step number that is out of date is dimmed the same way)
        if tint is not None:
            color = tinted_text_color(tint, QColor(theme.toolbar_bg), stale=bool(index.data(STEP_STALE_ROLE)))
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
    def _paint_shape(painter: QPainter, rect: QRect, index: QModelIndex, theme) -> None:
        """A small pictogram of the ROI's shape, in the ROI's own colour (the name is the tooltip).

        TODO(shapes): rectangle with frame, polygon and freehand have no pictogram yet; they
        fall under "other" (drawn as a question mark) until they are implemented."""
        shape = index.data(SHAPE_ROLE)
        fill = QColor(index.data(COLOR_ROLE) or theme.text_dim)
        outline = QPen(QColor(theme.text_secondary), 1.3)
        center = QPointF(rect.left() + _CELL_PAD + 8.0, rect.center().y() + 0.5)
        painter.setPen(outline)
        if shape == SHAPE_RING:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 7.0, 7.0)
            painter.setBrush(fill)
            painter.drawEllipse(center, 3.2, 3.2)
        elif shape == SHAPE_DISK:
            painter.setBrush(fill)
            painter.drawEllipse(center, 5.0, 5.0)
        elif shape == SHAPE_MASK:
            outline.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(outline)
            painter.setBrush(fill)
            points = [(-6, -2), (-3, -6), (2, -5), (6, -1), (4, 5), (-1, 6), (-5, 3)]
            painter.drawPolygon(QPolygonF([QPointF(center.x() + dx, center.y() + dy) for dx, dy in points]))
        else:
            painter.setPen(QColor(theme.text_dim))
            painter.drawText(QRectF(center.x() - 8, rect.top(), 16, rect.height()), int(Qt.AlignmentFlag.AlignCenter), "?")

    @staticmethod
    def _paint_cell_borders(painter: QPainter, rect: QRect, column: int, theme, last_column: int = COLUMN_COUNT - 1) -> None:
        """Hairlines under the row and between the value columns: faint enough
        to guide the eye along a row and down a column, never a heavy grid."""
        line = QColor(theme.toolbar_border)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        line.setAlpha(_ROW_LINE_ALPHA)
        painter.setPen(QPen(line, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if column != last_column:
            line.setAlpha(_COLUMN_LINE_ALPHA)
            painter.setPen(QPen(line, 1))
            painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    @staticmethod
    def _paint_dot(painter: QPainter, rect: QRectF, color_hex: str | None, theme) -> None:
        painter.setBrush(QColor(color_hex or theme.text_dim))
        painter.setPen(QPen(QColor(theme.control_border), 1))
        painter.drawEllipse(rect)

    def _paint_group(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        theme = get_active_theme()
        rect = option.rect
        painter.fillRect(rect, QColor(theme.toolbar_section_bg))
        self._paint_group_step_tints(painter, option, index)
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

        count_text = str(index.data(COUNT_ROLE) or 0)  # sits where a ROI's id sits, under the "#/ROIs" title
        metrics = option.fontMetrics
        count_left = int(chip.right()) + 7
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            QRect(count_left, rect.top(), max(0, rect.width() - count_left), rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            count_text,
        )

        if self._editing is not None and self._editing == QPersistentModelIndex(index):
            return  # the editor sits exactly where the name is painted; painting it too would double it
        font = QFont(option.font)
        font.setBold(not ungrouped)
        font.setItalic(ungrouped)
        painter.setFont(font)
        painter.setPen(QColor(theme.text_muted if ungrouped else theme.text_primary))
        name_rect = self._group_name_rect(option, index, count_text)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, name_rect.width()),
        )
        self._paint_group_steps(painter, option, index, theme)

    @staticmethod
    def _step_columns_of(view, model) -> list[int]:
        return [c for c in range(COLUMN_COUNT, model.columnCount()) if not view.isColumnHidden(c)] if isinstance(view, QTreeView) else []

    def _paint_group_step_tints(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """A group header spans every column, so the background of its step columns is painted here too, before the selection / hover tint goes over it. Like the numbers
        (`_paint_group_steps`) it is placed relative to the spanned cell, whatever the scroll position."""
        view = option.widget
        for column in self._step_columns_of(view, index.model()):
            tint = step_tint_of(index.siblingAtColumn(column))
            if tint is not None:
                left = option.rect.left() + view.header().sectionPosition(column)
                painter.fillRect(QRect(left, option.rect.top(), view.columnWidth(column), option.rect.height()), tint)

    def _paint_group_steps(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, theme) -> None:
        """A group header spans every column, so its numbers in the step columns are painted here, over the spanned cell, at the places the columns have."""
        view = option.widget
        painter.setFont(QFont(option.font))
        for column in self._step_columns_of(view, index.model()):
            cell = index.siblingAtColumn(column)
            text = str(cell.data(Qt.ItemDataRole.DisplayRole) or "")
            if not text:
                continue
            tint = step_tint_of(cell)
            stale = bool(cell.data(STEP_STALE_ROLE))
            if tint is not None:
                painter.setPen(tinted_text_color(tint, QColor(theme.toolbar_section_bg), stale=stale))
            else:
                painter.setPen(QColor(theme.text_dim if stale else theme.text_primary))
            # Relative to the spanned cell, not to the scroll position: the same place wherever the cell is drawn (a frozen table draws it again unscrolled, `view.py`).
            area = QRect(option.rect.left() + view.header().sectionPosition(column) + _CELL_PAD, option.rect.top(), max(0, view.columnWidth(column) - 2 * _CELL_PAD - 2), option.rect.height())
            painter.drawText(
                area, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, area.width()),
            )

    def helpEvent(self, event, view, option, index) -> bool:  # type: ignore[override]
        """The tool tip of a step number in a group header: the header is one spanned cell, so ask which column the pointer is over."""
        if event.type() == QEvent.Type.ToolTip and index.isValid() and index.data(KIND_ROLE) == "group" and isinstance(view, QTreeView):
            column = view.column_at(event.pos().x()) if hasattr(view, "column_at") else view.columnAt(event.pos().x())  # (`column_at` knows about frozen columns)
            if column >= COLUMN_COUNT:
                tip = index.siblingAtColumn(column).data(Qt.ItemDataRole.ToolTipRole)
                if tip:
                    QToolTip.showText(event.globalPos(), str(tip), view)
                    return True
        return super().helpEvent(event, view, option, index)

    @staticmethod
    def _group_name_rect(option: QStyleOptionViewItem, index: QModelIndex, count_text: str) -> QRect:
        """Where a group header's name is painted (and edited): after the chevron, chip and
        count, but never left of the end of column 0 (the count sits under the "#/ROIs" title).

        Every x is relative to ``option.rect.left()`` (the left end of the spanned cell), never to the scroll position, so the name is in the same place of the cell wherever the
        cell is drawn. With frozen columns the name stops at their right edge: the rest of the header scrolls away, the name must not."""
        rect = option.rect
        view = option.widget
        first_column_right = rect.left() + (view.columnWidth(0) if isinstance(view, QTreeView) else 0)
        name_left = max(CHEVRON_ZONE + rect.left() + 12 + 9 + option.fontMetrics.horizontalAdvance(count_text) + 7, first_column_right + 2)
        right = rect.right()
        steps = RoiTableDelegate._step_columns_of(view, index.model())
        if steps:
            right = min(right, rect.left() + view.header().sectionPosition(steps[0]))  # the name stops where the step numbers begin
        pinned = view.pinned_width() if hasattr(view, "pinned_width") else 0
        if pinned:
            right = min(right, rect.left() + pinned - 4)
        return QRect(name_left, rect.top(), max(0, right - name_left - 8), rect.height())

    # -- editing --------------------------------------------------------------------

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> QWidget:  # type: ignore[override]
        if index.data(KIND_ROLE) == "group":
            self._editing = QPersistentModelIndex(index)
        steps = index.data(KIND_ROLE) != "group" and index.column() in DIAMETER_COLUMNS
        unit = getattr(index.model(), "unit", None)
        editor = _StepLineEdit(parent, unit() if steps and callable(unit) else None)
        theme = get_active_theme()
        editor.setStyleSheet(
            f"QLineEdit {{ background: {theme.control_bg}; color: {theme.text_primary}; "
            f"border: 1px solid {theme.control_border_hover}; border-radius: 3px; padding: 0 4px; }}"
        )
        if index.data(KIND_ROLE) == "group":
            font = editor.font()
            font.setBold(True)  # the name is painted bold; the edit keeps its look
            editor.setFont(font)
        if index.data(KIND_ROLE) != "group" and index.column() not in (COLUMN_ID, 1):
            editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # type: ignore[override]
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            editor.selectAll()

    def destroyEditor(self, editor: QWidget, index: QModelIndex) -> None:  # type: ignore[override]
        if self._editing is not None and self._editing == QPersistentModelIndex(index):
            self._editing = None
            view = self.parent()
            if isinstance(view, QTreeView):
                view.viewport().update()  # show the name again
        super().destroyEditor(editor, index)

    def updateEditorGeometry(self, editor: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        rect = option.rect
        if index.data(KIND_ROLE) == "group":
            # In place: over the painted name (which paint() skips meanwhile), its text starting
            # where the name's text did (1 px border + 4 px padding).
            name_rect = self._group_name_rect(option, index, str(index.data(COUNT_ROLE) or 0))
            left = name_rect.left() - 5
            right = rect.right()
            view = option.widget
            pinned = view.pinned_width() if hasattr(view, "pinned_width") else 0
            if pinned:  # the editor stays over the frozen columns, where the name is drawn
                right = min(right, rect.left() + pinned)
            editor.setGeometry(left, rect.top() + 3, max(60, right - 4 - left), rect.height() - 6)
        else:
            editor.setGeometry(rect.adjusted(2, 2, -2, -2))
