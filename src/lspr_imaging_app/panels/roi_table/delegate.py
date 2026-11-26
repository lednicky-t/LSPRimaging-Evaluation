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
    KIND_ROLE,
    PAINT_ROLE,
    STEP_STALE_ROLE,
    STEP_TINT_ROLE,
    TYPE_ROLE,
)
from .rows import (
    COLUMN_ANGLE,
    COLUMN_COUNT,
    COLUMN_ID,
    COLUMN_NAME,
    COLUMN_RIM_OFFSET,
    COLUMN_SHAPE,
    ICON_OTHER,
    KIND_REFERENCE_FREE,
    SIZE_COLUMNS,
    angle_step_text,
    step_text,
)
from .type_icons import paint_type_icon

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
_RANGE_TINT_ALPHA = 55  # the fill of the cells picked by dragging (of 255)
_RANGE_EDGE_WIDTH = 2.0  # the border round them, px
_ANTS_WIDTH = 1.5  # the marching ants round the copied cells, px
_ROW_LINE_ALPHA = 90
_COLUMN_LINE_ALPHA = 55
_STALE_TINTED_TEXT_ALPHA = 135  # a number that is out of date, on a coloured step column: the same text colour, fainter (`text_dim` could vanish into the colour)
LABEL_WASH_ALPHA = 0.5
"""While #/ROIs is hidden, the colour its dot / chip carried is painted behind the label instead, at this opacity (the same as the plan bar's default for a step colour)."""


_STATE_SELECTED = QStyle.StateFlag.State_Selected.value  # (`state & State_Selected` builds a new enum each time: 1.5 µs, for every cell painted)
_ELIDE_RIGHT = Qt.TextElideMode.ElideRight
_ANTIALIASING = QPainter.RenderHint.Antialiasing
_LINE_PENS: dict[str, tuple[QPen, QPen]] = {}


def _line_pens(theme) -> tuple[QPen, QPen]:
    """The two hairline pens (under a row, between columns) in the theme's border colour, made once per colour: a pen and two colours were made for every cell painted."""
    pens = _LINE_PENS.get(theme.toolbar_border)
    if pens is None:
        row, column = QColor(theme.toolbar_border), QColor(theme.toolbar_border)
        row.setAlpha(_ROW_LINE_ALPHA)
        column.setAlpha(_COLUMN_LINE_ALPHA)
        pens = _LINE_PENS[theme.toolbar_border] = (QPen(row, 1), QPen(column, 1))
    return pens


def first_column_collapsed(view) -> bool:
    """True while #/ROIs is hidden. It is collapsed to zero width, not hidden for Qt (`RoiTreeView.refresh_column_visibility` says why), so the group headers still get drawn:
    the chevron, colour chip and count that sit in that column are then left out, and the row's colour is a wash behind the label (`LABEL_WASH_ALPHA`)."""
    return isinstance(view, QTreeView) and view.columnWidth(COLUMN_ID) <= 0


def label_wash_of(color_hex) -> QColor | None:
    """The colour a label is washed with while #/ROIs is hidden: the row's own colour at `LABEL_WASH_ALPHA`; None for a row with no colour."""
    if not color_hex:
        return None
    wash = QColor(color_hex)
    if not wash.isValid():
        return None
    wash.setAlphaF(LABEL_WASH_ALPHA)
    return wash


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
    """The cell editor. In a size or angle cell, Up / Down step the value (Shift: the
    large step); Enter then applies it, to every selected ROI like any edit."""

    def __init__(self, parent: QWidget, unit, *, angle: bool = False, signed: bool = False) -> None:
        super().__init__(parent)
        self._unit = unit  # None: this cell does not step
        self._angle = angle  # degrees (stepped by `angle_step_text`) instead of a length in `unit`
        self._signed = signed  # a length that may go below zero (a rim's offset)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        large = matches(event, "roi_table.cell_step_large")
        if self._unit is not None and (large or matches(event, "roi_table.cell_step")):
            direction = 1 if event.key() == Qt.Key.Key_Up else -1
            stepped = angle_step_text(self.text(), direction, large) if self._angle else step_text(self.text(), direction, large, self._unit, signed=self._signed)
            if stepped is not None:
                self.setText(stepped)
            event.accept()
            return
        super().keyPressEvent(event)


def group_label_edge(view, model, left: int, end: int, pin_inset: int = 0) -> int:
    """The x (one past the last pixel) where the stretch of a group header that holds its name ends: `end` (one past the end of the row), the first step column (the name stops where
    the step numbers begin) or the frozen columns' right edge (less `pin_inset`), whichever comes first. `left`: where the header's spanned cell begins."""
    edge = end
    first_step = RoiTableDelegate._first_step_column(view, model)
    if first_step is not None:
        edge = min(edge, left + view.header().sectionPosition(first_step))
    pinned = view.pinned_width() if hasattr(view, "pinned_width") else 0
    if pinned:
        edge = min(edge, left + pinned - pin_inset)
    return edge


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
        #
        # Exception (2026-11-06): while #/ROIs is hidden it has zero width and Qt skips it - it asks the columns from the first one *with width* on - so column 0 alone would
        # leave every row at height 0 (a table that looked fine until its next rebuild, then showed nothing). The columns Qt does ask answer then.
        if index.column() != 0 and not first_column_collapsed(option.widget or self.parent()):
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

    @staticmethod
    def _label_only(view) -> bool:
        """The right-click choice "highlight selected rows in the Label column only" (`RoiTreeView.label_only_highlight`)."""
        asker = getattr(view, "label_only_highlight", None)
        return bool(asker()) if callable(asker) else False

    @staticmethod
    def _locked(view) -> bool:
        """The ROI selection is locked: a selected row is framed by the view (`RoiTreeView._draw_locked_selection_frame`), not tinted."""
        asker = getattr(view, "selection_locked", None)
        return bool(asker()) if callable(asker) else False

    def _paint_row_background(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, theme) -> None:
        if option.state.value & _STATE_SELECTED:
            if self._locked(option.widget):
                return
            # With the choice on, only the row's label cells are tinted: #/ROIs and Label, whichever the view mode shows (Geometry shows #/ROIs, Simple both). If neither has any
            # width (the user hid them) the row is tinted whole, or a selection would show nowhere.
            view = option.widget
            if index.column() in (COLUMN_ID, COLUMN_NAME) or not self._label_only(view) or (view.columnWidth(COLUMN_ID) <= 0 and view.columnWidth(COLUMN_NAME) <= 0):
                painter.fillRect(option.rect, self._selected_tint(theme))
        elif self._hovered(option, index):
            hover = QColor(theme.control_bg_hover)
            hover.setAlpha(150)
            painter.fillRect(option.rect, hover)

    def _paint_roi(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        theme = get_active_theme()
        rect = option.rect
        text, dim, stale, alignment, tint = index.data(PAINT_ROLE)  # one question for everything the cell shows (it was seven)
        if tint is not None and not (isinstance(tint, QColor) and tint.isValid()):
            tint = None
        column = index.column()
        if tint is not None:
            painter.fillRect(rect, tint)  # under the selection / hover tint, which therefore still shows
        wash = label_wash_of(index.data(COLOR_ROLE)) if column == COLUMN_NAME and first_column_collapsed(option.widget) else None
        if wash is not None:
            painter.fillRect(rect, wash)  # the ROI's colour, which its dot would have shown in the hidden #/ROIs column
        self._paint_row_background(painter, option, index, theme)
        picked = option.widget.range_edges(index) if hasattr(option.widget, "range_edges") else None  # the cells picked by clicking a selected row again and dragging
        copied = option.widget.copied_edges(index) if hasattr(option.widget, "copied_edges") else None  # the cells just copied: the marching ants
        if picked is not None:
            fill = QColor(theme.accent_blue)
            fill.setAlpha(_RANGE_TINT_ALPHA)
            painter.fillRect(rect, fill)
        self._paint_cell_borders(painter, rect, column, theme, self._last_column_on_screen(option.widget, index.model()))
        if column == COLUMN_ID and rect.width() <= 0:
            return  # #/ROIs is hidden (collapsed to nothing): its dot would be drawn over the next column
        if column == COLUMN_SHAPE:
            self._paint_shape(painter, rect, index, theme)
            return
        color = QColor(theme.text_primary)
        if dim or stale:
            color = QColor(theme.text_dim)  # dim, not italic: italics read as "different kind of value" (a step number that is out of date is dimmed the same way)
        if tint is not None:
            color = tinted_text_color(tint, QColor(theme.toolbar_bg), stale=stale)
        elif wash is not None:
            color = tinted_text_color(wash, QColor(theme.toolbar_bg))  # readable on any colour, as on a step colour
        if column == COLUMN_ID:
            left = rect.left() + STRIP_WIDTH + 5 + (GROUP_INDENT if index.data(DEPTH_ROLE) else 0)
            self._paint_dot(painter, QRectF(left, rect.center().y() - CHIP_SIZE / 2 + 0.5, CHIP_SIZE, CHIP_SIZE), index.data(COLOR_ROLE), theme)
            text_rect = QRect(left + CHIP_SIZE + 7, rect.top(), max(0, rect.right() - left - CHIP_SIZE - 7), rect.height())
        else:
            text_rect = rect.adjusted(_CELL_PAD, 0, -_CELL_PAD - 2, 0)
        painter.setFont(option.font)
        painter.setPen(color)
        painter.drawText(text_rect, alignment, option.fontMetrics.elidedText(text, _ELIDE_RIGHT, text_rect.width()))
        if picked is not None:
            self._draw_block_edges(painter, rect, picked, QPen(QColor(theme.accent_blue), _RANGE_EDGE_WIDTH))
        if copied is not None:
            ants = QPen(QColor(theme.text_primary), _ANTS_WIDTH)
            ants.setDashPattern([3.0, 3.0])
            ants.setDashOffset(float(option.widget.ants_phase()))
            self._draw_block_edges(painter, rect, copied, ants)

    @staticmethod
    def _draw_block_edges(painter: QPainter, rect: QRect, edges: tuple[bool, bool, bool, bool], pen: QPen) -> None:
        """The sides of a cell that lie on the border of a block of cells (`cell_marks.edges`), drawn just inside the cell, clockwise (top left to right, right downwards, bottom right to
        left, left upwards), so a dashed line marches round the block."""
        top, right, bottom, left = edges
        inset = pen.widthF() / 2.0
        x0, x1 = rect.left() + inset, rect.right() + 1 - inset
        y0, y1 = rect.top() + inset, rect.bottom() + 1 - inset
        painter.save()
        painter.setPen(pen)
        if top:
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y0))
        if right:
            painter.drawLine(QPointF(x1, y0), QPointF(x1, y1))
        if bottom:
            painter.drawLine(QPointF(x1, y1), QPointF(x0, y1))
        if left:
            painter.drawLine(QPointF(x0, y1), QPointF(x0, y0))
        painter.restore()

    @staticmethod
    def _paint_shape(painter: QPainter, rect: QRect, index: QModelIndex, theme) -> None:
        """The Type cell's pictogram: the ROI's shape, a ring round it while it measures against its own rim, filled in the colour of its kind of pair (green, amber,
        blue, gray: `type_icons`). The name and the kind in words are the tooltip."""
        icon, kind = index.data(TYPE_ROLE) or (ICON_OTHER, KIND_REFERENCE_FREE)
        paint_type_icon(painter, QPointF(rect.left() + _CELL_PAD + 8.0, rect.center().y() + 0.5), icon, kind, theme)

    @staticmethod
    def _last_column_on_screen(view, model) -> int:
        """The column drawn rightmost that is not hidden: it gets no hairline on its right. Not simply the model's last column: the shape and the area sit in the
        middle on screen (2026-11-19), so with no step column the model's last one is no longer the rightmost."""
        header = view.header() if hasattr(view, "header") else None
        if header is not None:
            for visual in range(header.count() - 1, -1, -1):
                logical = header.logicalIndex(visual)
                if not header.isSectionHidden(logical):
                    return logical
        return model.columnCount() - 1

    @staticmethod
    def _paint_cell_borders(painter: QPainter, rect: QRect, column: int, theme, last_column: int = COLUMN_COUNT - 1) -> None:
        """Hairlines under the row and between the value columns: faint enough
        to guide the eye along a row and down a column, never a heavy grid."""
        row_pen, column_pen = _line_pens(theme)
        painter.setRenderHint(_ANTIALIASING, False)
        painter.setPen(row_pen)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if column != last_column:
            painter.setPen(column_pen)
            painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setRenderHint(_ANTIALIASING, True)

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
        collapsed = first_column_collapsed(option.widget)
        wash = label_wash_of(index.data(COLOR_ROLE)) if collapsed else None
        if wash is not None:  # the group's colour, which its chip would have shown in the hidden #/ROIs column, behind the name (and the stretch the name may use)
            painter.fillRect(QRect(rect.left(), rect.top(), max(0, self._group_label_edge(option, index) - rect.left()), rect.height()), wash)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if (selected or index.data(ALL_SELECTED_ROLE)) and self._locked(option.widget):
            pass  # locked: framed by the view, not tinted
        elif selected or index.data(ALL_SELECTED_ROLE):
            if self._label_only(option.widget):  # the header's own stretch (chevron, count, name), not the step numbers on the right
                painter.fillRect(QRect(rect.left(), rect.top(), max(0, self._group_label_edge(option, index) - rect.left()), rect.height()), self._selected_tint(theme))
            else:
                painter.fillRect(rect, self._selected_tint(theme))
        elif self._hovered(option, index):
            hover = QColor(theme.control_bg_hover)
            hover.setAlpha(110)
            painter.fillRect(rect, hover)
        painter.setPen(QPen(QColor(theme.toolbar_border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        ungrouped = index.data(GROUP_ID_ROLE) is None
        count_text = str(index.data(COUNT_ROLE) or 0)  # sits where a ROI's id sits, under the "#/ROIs" title
        if not collapsed:
            self._paint_group_marks(painter, option, index, theme, count_text)

        if self._editing is not None and self._editing == QPersistentModelIndex(index):
            return  # the editor sits exactly where the name is painted; painting it too would double it
        metrics = option.fontMetrics
        font = QFont(option.font)
        font.setBold(not ungrouped)
        font.setItalic(ungrouped)
        painter.setFont(font)
        if wash is not None:
            painter.setPen(tinted_text_color(wash, QColor(theme.toolbar_section_bg)))
        else:
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
    def _paint_group_marks(painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, theme, count_text: str) -> None:
        """The chevron, the colour chip and the ROI count at the left end of a group header (left out while #/ROIs is hidden: `first_column_collapsed`)."""
        rect = option.rect
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

        chip = QRectF(rect.left() + CHEVRON_ZONE, cy - 6, 12, 12)
        painter.setPen(QPen(QColor(theme.control_border), 1))
        painter.setBrush(QColor(index.data(COLOR_ROLE) or theme.text_dim))
        painter.drawRoundedRect(chip, 3, 3)

        count_left = int(chip.right()) + 7
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            QRect(count_left, rect.top(), max(0, rect.width() - count_left), rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            count_text,
        )

    @staticmethod
    def _step_columns_of(view, model) -> list[int]:
        """Every visible step column. Only for what needs all of them; painting uses `_step_columns_on_screen`."""
        return [c for c in range(COLUMN_COUNT, model.columnCount()) if not view.isColumnHidden(c)] if isinstance(view, QTreeView) else []

    @staticmethod
    def _first_step_column(view, model) -> int | None:
        """The first step column that is not hidden (where a group header's name stops), without listing them all."""
        if isinstance(view, QTreeView):
            for column in range(COLUMN_COUNT, model.columnCount()):
                if not view.isColumnHidden(column):
                    return column
        return None

    @staticmethod
    def _step_columns_on_screen(view) -> list[int]:
        """The visible step columns that are within the viewport (2026-11-26). A group header is one cell spanning every column and Qt does not clip what the delegate draws
        in it, so drawing all step columns of every header on every paint cost 0.6-2 s with 241 step columns and 16 groups (log of 2026-11-26: `roi_table.slow_paint`); the
        ones outside the viewport are never seen. (A frozen table draws a header a second time, translated; the step columns are clipped away there, so the viewport's
        range being a little off for that copy changes nothing.)"""
        if not isinstance(view, QTreeView):
            return []
        header = view.header()
        first = header.visualIndexAt(0)  # (viewport coordinates: the header adds its scroll offset itself)
        last = header.visualIndexAt(view.viewport().width() - 1)
        first = 0 if first < 0 else first
        last = header.count() - 1 if last < 0 else last
        columns = []
        for visual in range(first, last + 1):
            logical = header.logicalIndex(visual)
            if logical >= COLUMN_COUNT and not header.isSectionHidden(logical):
                columns.append(logical)
        return columns

    def _paint_group_step_tints(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """A group header spans every column, so the background of its step columns is painted here too, before the selection / hover tint goes over it. Like the numbers
        (`_paint_group_steps`) it is placed relative to the spanned cell, whatever the scroll position."""
        view = option.widget
        for column in self._step_columns_on_screen(view):
            tint = step_tint_of(index.siblingAtColumn(column))
            if tint is not None:
                left = option.rect.left() + view.header().sectionPosition(column)
                painter.fillRect(QRect(left, option.rect.top(), view.columnWidth(column), option.rect.height()), tint)

    def _paint_group_steps(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, theme) -> None:
        """A group header spans every column, so its numbers in the step columns are painted here, over the spanned cell, at the places the columns have."""
        view = option.widget
        painter.setFont(QFont(option.font))
        for column in self._step_columns_on_screen(view):
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
        count, but never left of the end of column 0 (the count sits under the "#/ROIs" title). While #/ROIs is hidden none of those is drawn: the name starts where a
        ROI's label does.

        Every x is relative to ``option.rect.left()`` (the left end of the spanned cell), never to the scroll position, so the name is in the same place of the cell wherever the
        cell is drawn. With frozen columns the name stops at their right edge: the rest of the header scrolls away, the name must not."""
        rect = option.rect
        view = option.widget
        if first_column_collapsed(view):
            name_left = rect.left() + _CELL_PAD
        else:
            first_column_right = rect.left() + (view.columnWidth(0) if isinstance(view, QTreeView) else 0)
            name_left = max(CHEVRON_ZONE + rect.left() + 12 + 9 + option.fontMetrics.horizontalAdvance(count_text) + 7, first_column_right + 2)
        right = RoiTableDelegate._group_label_edge(option, index, pin_inset=4) - 1
        return QRect(name_left, rect.top(), max(0, right - name_left - 8), rect.height())

    @staticmethod
    def _group_label_edge(option: QStyleOptionViewItem, index: QModelIndex, *, pin_inset: int = 0) -> int:
        """`group_label_edge` for the cell being painted."""
        return group_label_edge(option.widget, index.model(), option.rect.left(), option.rect.right() + 1, pin_inset)

    # -- editing --------------------------------------------------------------------

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex) -> QWidget:  # type: ignore[override]
        if index.data(KIND_ROLE) == "group":
            self._editing = QPersistentModelIndex(index)
        steps = index.data(KIND_ROLE) != "group" and index.column() in SIZE_COLUMNS
        unit = getattr(index.model(), "unit", None)
        editor = _StepLineEdit(parent, unit() if steps and callable(unit) else None, angle=index.column() == COLUMN_ANGLE, signed=index.column() == COLUMN_RIM_OFFSET)
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
