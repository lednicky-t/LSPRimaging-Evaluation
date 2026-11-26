"""``RoiTreeView``: the tree widget, with the behaviour the stock one lacks.

The stock `QTreeView` would draw its own branch arrows in an indentation gutter
and expand a group on double-click. Here a group header paints its own
chevron (`delegate.py`), a click on it toggles the group, and a double-click
renames. Keyboard: Delete asks to delete, Alt+Up / Alt+Down ask to move. While #/ROIs is hidden a group header has no chevron, chip or count and no click zone for them
(`delegate.first_column_collapsed`); the colour shows as a wash behind the label.

**Frozen columns** (2026-11-06). `QTreeView` cannot hold columns at the left edge while the rest scrolls sideways, so this view does it by painting: after Qt has drawn a row
(scrolled as usual), the frozen cells are painted again at their unscrolled places over the left of the row, with the same delegate; the header does the same with its
sections. The columns that are frozen are always the first ones (in screen order) up to a chosen one: ``set_frozen_upto``. Because the frozen cells are *drawn* somewhere
other than where the model's layout puts them, what answers "which cell is here" and "where is this cell" is shifted the same way: `indexAt` and `visualRect` (so clicks,
tool tips, drag and drop and cell editors all land on what is drawn), and the header's mouse events (`SubscriptHeader`). With nothing frozen every override falls straight through
to Qt. A freeze that would fill more than `_FREEZE_MAX_SHARE` of the view is not applied (there would be no room left to scroll); it applies again when the view is wide enough.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable

from dataclasses import dataclass

from PyQt6.QtCore import QModelIndex, QPoint, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen, QPolygonF, QWheelEvent
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QStyle, QStyleOptionFocusRect, QStyleOptionHeader, QStyleOptionViewItem, QTreeView, QWidget

from lspr_ui import get_active_theme

from ...applog import StageTimer, log_event
from ...shortcuts import matches
from .cell_marks import CellMarks, Edges
from .cell_marks import edges as block_edges
from .column_keys import ColumnKey
from .column_keys import column_key as key_of_step_column
from .delegate import CELL_ROOM, CHEVRON_ZONE, CHIP_SIZE, GROUP_INDENT, STRIP_WIDTH, first_column_collapsed, group_label_edge, tinted_text_color
from .model import ALL_SELECTED_ROLE, COLUMN_KIND_ROLE, DEPTH_ROLE, KIND_ROLE, STEP_TINT_ROLE, VALUES_ONLY_ROLE
from .rows import (
    COLUMN_ANGLE,
    COLUMN_AREA,
    COLUMN_COUNT,
    COLUMN_HEIGHT,
    COLUMN_ID,
    COLUMN_NAME,
    COLUMN_RIM_OFFSET,
    COLUMN_RIM_THICKNESS,
    COLUMN_SAMPLE,
    COLUMN_SHAPE,
    COLUMN_TITLES,
    COLUMN_WIDTH,
    COLUMN_X,
    COLUMN_Y,
    NUMERIC_COLUMNS,
    RECTANGLE_COLUMNS,
    VIEW_MODE_COLUMNS,
)
from .step_columns import MAX_STEP_WIDTH, MIN_STEP_WIDTH, STEP_WIDTH_DEFAULT, elide_runs, subscript_runs, widest_text

WHEEL_NOTCH = 120  # `QWheelEvent.angleDelta` units of one notch of a mouse wheel
AUTOSCROLL_MS = 40  # while a cell range is dragged the table scrolls this often when the pointer is at (or past) its edge
AUTOSCROLL_EDGE = 10  # px from the viewport's edge that count as "at the edge"
ANTS_MS = 120  # how often the marching ants of a copied block advance


@dataclass
class _RangeDrag:
    """A cell range being dragged: where it started and what to measure against (taken once at the press: nothing changes the rows while the button is down)."""

    anchor_id: int
    anchor_column: int
    order: list[int]  # the ROIs shown, top to bottom (rows of a collapsed group are not shown)
    position: dict[int, int]  # ROI id -> its place in `order`
    selected: frozenset[int]  # the selected ROIs: only these can be in a range
    last_id: int  # the ROI the range reached last (kept while the pointer is over a group header)
_ARROW_ZONE = 20  # px at the left end of a comparison column's header: the arrow that opens its operation menu
_SORT_INDICATOR_ROOM = 22
_CELL_PADDING = 16
_FREEZE_MAX_SHARE = 0.6  # the frozen columns may fill at most this share of the view's width; more leaves too little to scroll
_FREEZE_EDGE_ALPHA = 130
_SECTION_PADDING = 6  # px left and right of a header title: what the header's style sheet (`panel.py`, `_apply_theme`) gives a section, copied by `_paint_tinted_section`
_SORT_ARROW_WIDTH = 7  # px: the sort triangle a coloured step header draws at its left end
_SORT_ARROW_ROOM = 12  # px the title keeps clear of it (the title is right-aligned: it only matters for a long one)


_SUBSCRIPT_SCALE = 0.8  # subscript size relative to the header font (not a true ratio: smaller was unreadable)
_SUBSCRIPT_DROP = 0.25  # how far the subscript sits below the baseline, as a fraction of the font height


def _split_title(title: str) -> tuple[str, str]:
    """``"D_s"`` -> ``("D", "s")``; a title without an underscore -> ``(title, "")``."""
    base, _, sub = title.partition("_")
    return base, sub


def _subscript_font(font: QFont) -> QFont:
    small = QFont(font)
    small.setPointSizeF(font.pointSizeF() * _SUBSCRIPT_SCALE) if font.pointSizeF() > 0 else small.setPixelSize(
        max(1, round(font.pixelSize() * _SUBSCRIPT_SCALE))
    )
    return small


def title_width(metrics: QFontMetrics, title: str) -> int:
    """Width of a header title as `SubscriptHeader` paints it (the subscript
    width is scaled from the normal font's, so a pixel or so of slack)."""
    base, sub = _split_title(title)
    return metrics.horizontalAdvance(base) + math.ceil(metrics.horizontalAdvance(sub) * _SUBSCRIPT_SCALE)


def runs_width(font: QFont, runs: list[tuple[str, bool]]) -> int:
    """Width in px of a title written as `step_columns.subscript_runs` gives it, in `font` (subscripts in the smaller font `SubscriptHeader` draws them in)."""
    base, small = QFontMetrics(font), QFontMetrics(_subscript_font(font))
    return sum((small if sub else base).horizontalAdvance(text) for text, sub in runs)


logger = logging.getLogger(__name__)
_SLOW_PAINT_MS = 20.0
"""A paint of the table longer than this is logged (`roi_table.slow_paint`, DEBUG). A full viewport of 26 rows is 27-31 ms on the development laptop (2026-11-19), so this notes the big ones;
the one-row repaints of a hover are a few ms."""


class SubscriptHeader(QHeaderView):
    """Header that draws ``X_y`` titles with a real, readable subscript (Unicode
    subscript letters are far too small). Every other title is drawn as usual.

    A comparison column (μ_S1 − μ_B1 ...) has an arrow at the left end of its header: a click on it asks for the operation menu (`compare_menu_requested`) and is not a sort click;
    a click anywhere else on the header sorts as for every column. Its title is drawn with subscripts (`_paint_math_section`), as many as it has; in a column too narrow for the whole
    formula it is cut at the end with "..." like any title, still with its subscripts (never written with underscores)."""

    compare_menu_requested = pyqtSignal(int, QPoint)
    """(the section, where to open the menu in global coordinates)."""
    fit_requested = pyqtSignal(int)
    """A double-click on the edge of a step column's section (the section): the user wants the step columns fitted to their content."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._pinned: Callable[[], list[int]] = list  # the frozen sections, left to right (`RoiTreeView.pinned_columns`); none until the view says
        self._drag_shift = 0  # how far the mouse events of the press in progress are moved (see `_pin_shift`)
        self._in_double_click = False
        self._edge_double_clicked = -1  # the section whose edge the double-click in progress hit
        self.sectionHandleDoubleClicked.connect(self._on_edge_double_clicked)

    def set_pin_source(self, pinned: Callable[[], list[int]]) -> None:
        self._pinned = pinned

    # -- frozen sections: painted again at their unscrolled places, mouse events moved to match ---------------------------------

    def _pinned_width(self, columns: list[int]) -> int:
        return sum(self.sectionSize(c) for c in columns)

    def _pin_shift(self, x: float) -> int:
        """How far the section drawn at header position `x` is from where `QHeaderView` lays it out: the scroll offset over the frozen sections (they are drawn unscrolled, the
        layout has scrolled them to the left), else 0. A mouse position over a frozen section therefore has to be moved *back* by it before `QHeaderView` sees it."""
        offset = self.offset()
        if offset <= 0:
            return 0
        columns = self._pinned()
        return offset if columns and x < self._pinned_width(columns) else 0

    def section_at(self, x: float) -> int:
        """The section drawn at header position `x` (-1 for none); `logicalIndexAt` alone is wrong over the frozen sections while the table is scrolled."""
        return self.logicalIndexAt(int(x - self._pin_shift(x)))

    @staticmethod
    def _moved(event: QMouseEvent, shift: int) -> QMouseEvent:
        """The same mouse event, at the position where the layout has the section the pointer is over (`_pin_shift`)."""
        if not shift:
            return event
        position = event.position()
        return QMouseEvent(event.type(), QPointF(position.x() - shift, position.y()), event.globalPosition(), event.button(), event.buttons(), event.modifiers())

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        columns = self._pinned()
        if not columns:
            return
        width, height = self._pinned_width(columns), self.viewport().height()
        painter = QPainter(self.viewport())
        theme = get_active_theme()
        if self.offset() > 0:  # the scrolled sections were drawn under them: cover those and draw the frozen ones where they belong
            area = QRect(0, 0, width, height)
            painter.setClipRect(area)
            painter.fillRect(area, QColor(theme.toolbar_bg))
            for column in columns:
                self.paintSection(painter, QRect(self.sectionPosition(column), 0, self.sectionSize(column), height), column)
            painter.setClipping(False)
        edge = QColor(theme.text_muted)
        edge.setAlpha(_FREEZE_EDGE_ALPHA)
        painter.setPen(QPen(edge, 1))
        painter.drawLine(width - 1, 0, width - 1, height)
        painter.end()

    def _is_compare(self, logical_index: int) -> bool:
        model = self.model()
        return logical_index >= COLUMN_COUNT and model is not None and model.headerData(logical_index, self.orientation(), COLUMN_KIND_ROLE) == "compare"

    def _paint_arrow(self, painter: QPainter, rect: QRect) -> None:
        cx, cy = rect.left() + _ARROW_ZONE / 2 + 2.0, rect.center().y() + 1.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(get_active_theme().text_muted))
        painter.drawPolygon(QPolygonF([QPointF(cx - 3.5, cy - 2.0), QPointF(cx + 3.5, cy - 2.0), QPointF(cx, cy + 2.5)]))
        painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_shift = self._pin_shift(event.position().x())  # a press, and the drag that follows it, belong to the layer the press started in
        event = self._moved(event, self._drag_shift)
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            section = self.logicalIndexAt(point)
            if section >= 0 and self._is_compare(section) and point.x() - self.sectionViewportPosition(section) <= _ARROW_ZONE:
                self._swallow_release = True  # `sectionClicked` is sent on release: the sort must not happen
                self.compare_menu_requested.emit(section, self.mapToGlobal(QPoint(self.sectionViewportPosition(section) + self._drag_shift, self.height())))  # where the section is drawn
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        shift = self._drag_shift if event.buttons() else self._pin_shift(event.position().x())
        super().mouseMoveEvent(self._moved(event, shift))

    def _on_edge_double_clicked(self, section: int) -> None:
        self._edge_double_clicked = section

    def in_double_click(self) -> bool:
        """True while a double-click is being handled: Qt then resizes the one section to its content, which is not the user dragging an edge (the panel ignores it)."""
        return self._in_double_click

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Qt resizes a section to its content on a double-click on its edge. For a step column that is asked of the view (`RoiTreeView.sizeHintForColumn`), and the double-click
        also says the user wants every step column fitted again (`fit_requested`). Moved like the other mouse events when the table is scrolled under frozen columns."""
        self._edge_double_clicked = -1
        self._in_double_click = True
        try:
            super().mouseDoubleClickEvent(self._moved(event, self._pin_shift(event.position().x())))
        finally:
            self._in_double_click = False
        section, self._edge_double_clicked = self._edge_double_clicked, -1
        if section >= COLUMN_COUNT:
            self.fit_requested.emit(section)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        shift, self._drag_shift = self._drag_shift, 0
        if getattr(self, "_swallow_release", False):
            self._swallow_release = False
            event.accept()
            return
        super().mouseReleaseEvent(self._moved(event, shift))

    def _tint_of(self, logical_index: int) -> QColor | None:
        """The colour a step column's header is filled with (the Sensorgram plan bar's fill of that step), or None."""
        tint = self.model().headerData(logical_index, self.orientation(), STEP_TINT_ROLE) if self.model() and logical_index >= COLUMN_COUNT else None
        return tint if isinstance(tint, QColor) and tint.isValid() else None

    def _paint_tinted_section(self, painter: QPainter, rect: QRect, logical_index: int, tint: QColor) -> None:
        """A step's header on the step's colour. Drawn by hand: the header's style sheet gives every section an opaque background, and a colour cannot be put under that. What the
        sheet gives a section is copied here: the base colour, the line below, `_SECTION_PADDING` at both ends, the semi-bold font; the title is as the model aligns it (right)."""
        theme = get_active_theme()
        base = QColor(theme.toolbar_bg)
        text_color = tinted_text_color(tint, base)
        painter.save()
        painter.fillRect(rect, base)
        painter.fillRect(rect, tint)
        painter.setPen(QColor(theme.toolbar_border))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        sorted_here = self.isSortIndicatorShown() and self.sortIndicatorSection() == logical_index
        if sorted_here:  # a small triangle of our own at the left end (what a style draws depends on the platform's style): up = ascending. The right-aligned title stays where it is
            ascending = self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            cx, cy = rect.left() + _SECTION_PADDING + _SORT_ARROW_WIDTH / 2, rect.center().y() + 0.5
            tip, flat = (cy - 2.5, cy + 2.0) if ascending else (cy + 2.5, cy - 2.0)
            arrow_color = QColor(text_color)
            arrow_color.setAlpha(210)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(arrow_color)
            half = _SORT_ARROW_WIDTH / 2
            painter.drawPolygon(QPolygonF([QPointF(cx - half, flat), QPointF(cx + half, flat), QPointF(cx, tip)]))
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)  # the sheet's font-weight: 600
        painter.setFont(font)
        painter.setPen(text_color)
        text_rect = rect.adjusted(_SECTION_PADDING + (_SORT_ARROW_ROOM if sorted_here else 0), 0, -_SECTION_PADDING, -1)
        alignment = self.model().headerData(logical_index, self.orientation(), Qt.ItemDataRole.TextAlignmentRole)
        alignment = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) if alignment is None else int(alignment)
        title = str(self.model().headerData(logical_index, self.orientation(), Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(text_rect, alignment, QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()

    def _paint_section_background(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        """Let the style draw the background and sort arrow of a section, with the text left out (the caller draws it)."""
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logical_index
        option.text = ""
        option.icon = QIcon()
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical_index:
            order = self.sortIndicatorOrder()
            option.sortIndicator = (
                QStyleOptionHeader.SortIndicator.SortDown if order == Qt.SortOrder.DescendingOrder
                else QStyleOptionHeader.SortIndicator.SortUp
            )
        else:
            option.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)

    def _paint_math_section(self, painter: QPainter, rect: QRect, logical_index: int, title: str) -> bool:
        """A comparison column's title with real subscripts (``μ_S1 − μ_B1``: μ, then S1 small and low, ...). Placed as the style sheet places the other step titles: right-aligned,
        `_SECTION_PADDING` from the edge, semi-bold, in the sheet's colour, centred above the line under the header; the arrow stays at the left end. What does not fit between
        the arrow and the right edge is cut with "..." (`elide_runs`: a column the user made narrow, as all step columns, keeps the start of the formula). Returns False, painting
        nothing, only when the title has no subscript: the caller then draws it as any title."""
        runs = subscript_runs(title)
        if not any(sub for _text, sub in runs):
            return False
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)  # the sheet's font-weight: 600
        metrics, small = QFontMetrics(font), _subscript_font(font)
        small_metrics = QFontMetrics(small)

        def advance(text: str, sub: bool) -> int:
            return (small_metrics if sub else metrics).horizontalAdvance(text)

        runs = elide_runs(runs, advance, rect.width() - _SECTION_PADDING - _ARROW_ZONE - 2)
        right = rect.right() + 1 - _SECTION_PADDING
        left = right - sum(advance(text, sub) for text, sub in runs)
        self._paint_section_background(painter, rect, logical_index)
        baseline = rect.top() + (rect.height() - 1 + metrics.ascent() - metrics.descent()) // 2  # (centred above the 1 px line under the header)
        drop = round(metrics.height() * _SUBSCRIPT_DROP)
        painter.save()
        painter.setPen(QColor(get_active_theme().text_muted))  # the colour panel.py's header stylesheet gives the titles
        x = left
        for text, sub in runs:
            painter.setFont(small if sub else font)
            painter.drawText(x, baseline + (drop if sub else 0), text)
            x += advance(text, sub)
        painter.restore()
        self._paint_arrow(painter, rect)
        return True

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # type: ignore[override]
        tint = self._tint_of(logicalIndex)
        if tint is not None:
            self._paint_tinted_section(painter, rect, logicalIndex, tint)
            return
        title = self.model().headerData(logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole) if self.model() else None
        if self._is_compare(logicalIndex) and isinstance(title, str) and self._paint_math_section(painter, rect, logicalIndex, title):
            return
        if not isinstance(title, str) or "_" not in title or logicalIndex >= COLUMN_COUNT:  # a step's title is the user's note (30perEG_-_ch2 ...): no subscripts
            super().paintSection(painter, rect, logicalIndex)
            if self._is_compare(logicalIndex):
                self._paint_arrow(painter, rect)
            return
        self._paint_section_background(painter, rect, logicalIndex)
        base, sub = _split_title(title)
        font = self.font()
        small = _subscript_font(font)
        metrics = QFontMetrics(font)
        total = metrics.horizontalAdvance(base) + QFontMetrics(small).horizontalAdvance(sub)
        text_area = rect.adjusted(0, 0, -_SORT_INDICATOR_ROOM // 2, 0)  # keep clear of the sort arrow
        x = text_area.left() + max(0, (text_area.width() - total) // 2)
        baseline = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) // 2
        painter.save()
        painter.setPen(QColor(get_active_theme().text_muted))  # the colour panel.py's header stylesheet gives the other titles
        painter.setFont(font)
        painter.drawText(x, baseline, base)
        painter.setFont(small)
        painter.drawText(x + metrics.horizontalAdvance(base), baseline + round(metrics.height() * _SUBSCRIPT_DROP), sub)
        painter.restore()


def suggested_column_widths(metrics: QFontMetrics) -> list[int]:
    """Default column widths: the worst-case text each column must hold, plus
    padding (and room for the sort arrow on the header). Measured, not
    guessed, so figures are not clipped in the user's font."""
    advance = metrics.horizontalAdvance
    number = advance("9999.9") + _CELL_PADDING
    diameter = max(advance("999.9") + _CELL_PADDING, title_width(metrics, COLUMN_TITLES[COLUMN_SAMPLE]) + _SORT_INDICATOR_ROOM)
    widths = [0] * COLUMN_COUNT
    widths[COLUMN_ID] = STRIP_WIDTH + 5 + GROUP_INDENT + CHIP_SIZE + 7 + advance("9999") + 10
    widths[COLUMN_NAME] = advance("Group label 99") + _CELL_PADDING
    widths[COLUMN_X] = widths[COLUMN_Y] = max(number, advance(COLUMN_TITLES[COLUMN_X]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_SAMPLE] = max(diameter, title_width(metrics, COLUMN_TITLES[COLUMN_SAMPLE]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_RIM_OFFSET] = max(advance("-999.9") + _CELL_PADDING, title_width(metrics, COLUMN_TITLES[COLUMN_RIM_OFFSET]) + _SORT_INDICATOR_ROOM)  # an offset can be negative
    widths[COLUMN_RIM_THICKNESS] = max(diameter, title_width(metrics, COLUMN_TITLES[COLUMN_RIM_THICKNESS]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_SHAPE] = max(32, advance(COLUMN_TITLES[COLUMN_SHAPE]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_WIDTH] = widths[COLUMN_HEIGHT] = max(diameter, advance(COLUMN_TITLES[COLUMN_WIDTH]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_ANGLE] = max(advance("-999.9") + _CELL_PADDING, advance(COLUMN_TITLES[COLUMN_ANGLE]) + _SORT_INDICATOR_ROOM)  # a turn can be negative
    widths[COLUMN_AREA] = max(advance("99999.9") + _CELL_PADDING, title_width(metrics, COLUMN_TITLES[COLUMN_AREA]) + _SORT_INDICATOR_ROOM)  # 99999 px, or 9999.9 µm^2
    return widths


class RoiTreeView(QTreeView):
    delete_requested = pyqtSignal()
    move_requested = pyqtSignal(int)
    """-1 = up, +1 = down."""
    chip_double_clicked = pyqtSignal(QModelIndex)
    """A double-click on a colour chip (a ROI's dot or a group's chip): change
    the colour instead of editing the row."""
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    fill_down_requested = pyqtSignal()
    """Ctrl+C / Ctrl+V / Ctrl+D on the current cell (the panel decides what they do)."""
    step_requested = pyqtSignal(QModelIndex, int, bool)
    """A turn of the wheel over a cell that steps (see `wheelEvent`): index, direction (+1 / -1), large step."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frozen_upto = -1  # the last frozen column (a logical column number); -1 = nothing frozen
        self._selection_locked = False  # True: the ROI selection is locked; a selected row is framed, not tinted (`set_selection_locked`)
        self._label_only_highlight = False  # True: a selected row is tinted in its Label column only, not across every column (right-click menu)
        self._mode_columns: frozenset[int] = frozenset(range(COLUMN_COUNT))  # the fixed columns the view mode shows (`set_view_mode`)
        self._hidden_keys: frozenset = frozenset()  # the columns the user hid, by key (`column_keys.py`)
        self._first_width = 0  # the width #/ROIs had before it was hidden (collapsed to nothing, see `refresh_column_visibility`); 0 = not known yet
        self._wheel_turn = 0  # wheel units turned so far that are not a whole notch yet (`wheelEvent`)
        self._marks = CellMarks()  # the picked cell range and the copied cells (`cell_marks.py`)
        self._range_drag: _RangeDrag | None = None
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self._ants = QTimer(self)
        self._ants.setInterval(ANTS_MS)
        self._ants.timeout.connect(self._ants_tick)
        header = SubscriptHeader(Qt.Orientation.Horizontal, self)
        header.set_pin_source(self.pinned_columns)
        self.setHeader(header)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setExpandsOnDoubleClick(False)
        self.setItemsExpandable(True)
        self.setAnimated(False)
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setMouseTracking(True)
        # Drag ROIs to reorder them or onto a group header (see model.drop_target).
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAutoExpandDelay(500)  # hovering over a collapsed group while dragging opens it
        self.setFrameShape(QTreeView.Shape.NoFrame)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._hover: tuple[object, int] | None = None
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setHighlightSections(False)
        header.setMinimumSectionSize(28)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def apply_default_column_widths(self) -> None:
        for column, width in enumerate(suggested_column_widths(self.fontMetrics())):
            self.set_fixed_column_width(column, width)

    def apply_step_column_widths(self, width: int) -> None:
        """Every step column gets the same width (the one remembered for them); the fixed columns are not touched."""
        for column in range(COLUMN_COUNT, self.model().columnCount() if self.model() is not None else 0):
            self.setColumnWidth(column, width)
            self.setColumnHidden(column, self.is_user_hidden(column))

    def step_column_fit(self, column: int, stages: StageTimer | None = None) -> int:
        """The width a step column needs: wide enough for its title (bold, as the header draws it, with the room for the sort triangle or the comparison menu's arrow) and for
        the widest thing any row shows in it. A column with no numbers yet gets the old default, so it does not jump narrow and then wide when the first calculation ends."""
        model = self.model()
        title = str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        compare = model.headerData(column, Qt.Orientation.Horizontal, COLUMN_KIND_ROLE) == "compare"
        bold = QFont(self.header().font())
        bold.setWeight(QFont.Weight.DemiBold)  # the header's style sheet: font-weight 600
        runs = subscript_runs(title) if compare else []  # (a comparison column's title is drawn with subscripts, which are narrower than the underscore text)
        text_width = runs_width(bold, runs) if any(sub for _text, sub in runs) else QFontMetrics(bold).horizontalAdvance(title)
        title_room = text_width + 2 * _SECTION_PADDING + (_ARROW_ZONE if compare else _SORT_ARROW_ROOM)
        if stages is not None:
            stages.mark("title")
        texts = model.step_texts(column - COLUMN_COUNT)
        if stages is not None:
            stages.mark("format")  # (every row's and group's number written as the cell shows it)
        if not texts:
            return min(max(title_room, STEP_WIDTH_DEFAULT), MAX_STEP_WIDTH)
        cells = widest_text(texts, self.fontMetrics().horizontalAdvance) + CELL_ROOM
        if stages is not None:
            stages.mark("measure")
        return min(max(title_room, cells, MIN_STEP_WIDTH), MAX_STEP_WIDTH)

    def fit_step_columns(self) -> None:
        """Every step column gets the width its own content needs (`step_column_fit`); the fixed columns are not touched. One ``roi_table.fit_step_columns`` record (DEBUG) says how
        long it took and in which stage, summed over the columns: ``title`` (measuring the header text), ``format`` (writing every row's and group's number as its cell shows it),
        ``measure`` (the widest of those texts), ``set`` (setting the widths: Qt's resize signals run in there)."""
        model = self.model()
        stages = StageTimer()
        for column in range(COLUMN_COUNT, model.columnCount() if model is not None else 0):
            self.setColumnWidth(column, self.step_column_fit(column, stages))  # (a column the user hid keeps its fitted width for when it is shown again)
            self.setColumnHidden(column, self.is_user_hidden(column))
            stages.mark("set")
        if model is not None:
            stages.emit(logger, "roi_table.fit_step_columns", columns=model.columnCount() - COLUMN_COUNT, rows=len(model.rows()))

    def sizeHintForColumn(self, column: int) -> int:  # type: ignore[override]
        """What Qt's "resize to contents" (a double-click on a section's edge) asks for. The delegate gives no width of its own, so for a step column the answer is the fit."""
        model = self.model()
        if model is not None and COLUMN_COUNT <= column < model.columnCount():
            return self.step_column_fit(column)
        return super().sizeHintForColumn(column)

    def set_view_mode(self, mode: str) -> None:
        """Show the columns of a view mode (`rows.VIEW_MODE_COLUMNS`) and hide the others (and the ones the user hid)."""
        self._mode_columns = frozenset(VIEW_MODE_COLUMNS[mode])
        self.refresh_column_visibility()
        header = self.header()  # the shape sits between the position and the diameters on screen, wherever its column number is; the area right after it (2026-11-19)
        header.moveSection(header.visualIndex(COLUMN_SHAPE), header.visualIndex(COLUMN_Y) + 1)
        header.moveSection(header.visualIndex(COLUMN_AREA), header.visualIndex(COLUMN_SHAPE) + 1)

    # -- columns the user hid (`hide_columns.py`) ---------------------------------------------------------------------------------

    def column_key(self, column: int) -> ColumnKey | None:
        """The key (`column_keys.py`) of a column: it stays the same when the step columns are renumbered. None for a column that is not there."""
        if column < COLUMN_COUNT:
            return column
        model = self.model()
        steps = model.step_columns() if model is not None else ()
        position = column - COLUMN_COUNT
        return key_of_step_column(steps[position]) if 0 <= position < len(steps) else None

    def hidden_keys(self) -> frozenset:
        """The keys of the columns the user hid (some may name columns this view does not have now: another view mode, a step that is gone)."""
        return self._hidden_keys

    def set_hidden_keys(self, keys) -> None:
        self._hidden_keys = frozenset(keys)
        self.refresh_column_visibility()

    def is_in_view(self, column: int) -> bool:
        """True if the current view mode has this column (every step column is in every mode)."""
        return column >= COLUMN_COUNT or column in self._mode_columns

    def is_user_hidden(self, column: int) -> bool:
        key = self.column_key(column)
        return key is not None and key in self._hidden_keys

    def refresh_column_visibility(self) -> None:
        """The one place that decides which columns show: a fixed column when the view mode has it, every step column, and none that the user hid; the width, height and angle
        columns (`rows.RECTANGLE_COLUMNS`) also only while the table holds a rectangle (2026-11-19: with none they would be three empty columns). Cheap; called whenever the
        mode, the hidden set, the step columns or the ROIs change (a model reset gives new step columns the default, visible).

        **#/ROIs is hidden differently** (2026-11-06): it is collapsed to zero width and stays a column for Qt. A group header is one cell spanning the row that Qt attaches to
        the first column; with that column really hidden Qt paints no group header at all (no chevron, name or step numbers, nothing to click, rename or drop on). With the
        column at zero width the header is drawn and answers as before, a ROI row only loses its dot and number (the delegate skips them), and the section cannot be dragged
        open (fixed size) while it is hidden."""
        model = self.model()
        no_rectangles = model is not None and not model.has_rectangles()
        for column in range(model.columnCount() if model is not None else 0):
            if column == COLUMN_ID:
                self._collapse_first_column(self.is_user_hidden(COLUMN_ID))
                continue
            hidden = (column < COLUMN_COUNT and column not in self._mode_columns) or self.is_user_hidden(column) or (no_rectangles and column in RECTANGLE_COLUMNS)
            if hidden != self.isColumnHidden(column):
                self.setColumnHidden(column, hidden)
        if self._marks.has_range():
            self.restrict_cell_range(self._marks.range_rows)  # a column that is not shown any more leaves the picked cells

    def _collapse_first_column(self, collapsed: bool) -> None:
        header = self.header()
        if collapsed:
            if self.columnWidth(COLUMN_ID) > 0:
                self._first_width = self.columnWidth(COLUMN_ID)  # for when it is shown again
            header.setSectionResizeMode(COLUMN_ID, QHeaderView.ResizeMode.Fixed)  # the left edge of the next column is also this one's handle: it must not be draggable open
            if self.columnWidth(COLUMN_ID) != 0:
                self.setColumnWidth(COLUMN_ID, 0)
        else:
            header.setSectionResizeMode(COLUMN_ID, QHeaderView.ResizeMode.Interactive)
            if self.columnWidth(COLUMN_ID) == 0:
                self.setColumnWidth(COLUMN_ID, self._first_width or suggested_column_widths(self.fontMetrics())[COLUMN_ID])

    def set_fixed_column_width(self, column: int, width: int) -> None:
        """Give a fixed column a width (the saved or the suggested one). While #/ROIs is hidden it stays at zero and the width is kept for when it is shown again."""
        if column == COLUMN_ID and self.is_user_hidden(COLUMN_ID):
            self._first_width = width
            return
        self.setColumnWidth(column, width)

    def label_only_highlight(self) -> bool:
        """Whether a selected row is tinted in its Label column only (the delegate asks, per cell, so it is a plain attribute read)."""
        return self._label_only_highlight

    def selection_locked(self) -> bool:
        """Whether the ROI selection is locked: selected rows then show a frame instead of a tint (the delegate asks, per cell)."""
        return self._selection_locked

    def set_selection_locked(self, on: bool) -> None:
        if on != self._selection_locked:
            self._selection_locked = on
            self.setAllColumnsShowFocus(not (on or self._label_only_highlight))  # Qt's faint tint of the current row, across every column, would undo the frame-only look
            self.viewport().update()

    def set_label_only_highlight(self, on: bool) -> None:
        if on != self._label_only_highlight:
            self._label_only_highlight = on
            self.setAllColumnsShowFocus(not (on or self._selection_locked))  # the current row's faint focus tint is Qt's, across every column: it would undo this
            self.viewport().update()

    # -- frozen columns (see the module docstring) ------------------------------------------------------------------------------

    def set_frozen_upto(self, column: int) -> None:
        """Freeze every column from the first one (on screen) up to and including `column`, a logical column number; -1 unfreezes."""
        if column != self._frozen_upto:
            self._frozen_upto = column
            self._repaint_all()

    def frozen_upto(self) -> int:
        return self._frozen_upto

    def pinned_columns(self) -> list[int]:
        """The columns held at the left edge, left to right: the visible ones from the first up to the frozen one. Empty when nothing is frozen or the frozen columns would fill
        too much of the view to leave room to scroll (the freeze comes back when the view is wide enough)."""
        if self._frozen_upto < 0 or self.model() is None or self._frozen_upto >= self.model().columnCount():
            return []
        header = self.header()
        last = header.visualIndex(self._frozen_upto)  # a hidden column keeps its place: freezing up to it freezes the visible columns before it
        if last < 0:
            return []
        columns = [c for c in (header.logicalIndex(v) for v in range(last + 1)) if not header.isSectionHidden(c)]
        width = sum(header.sectionSize(c) for c in columns)
        return columns if columns and width <= _FREEZE_MAX_SHARE * self.viewport().width() else []

    def pinned_width(self) -> int:
        """Width in px of the frozen columns (0 when none are held); the delegate keeps a group's name inside it."""
        return sum(self.header().sectionSize(c) for c in self.pinned_columns())

    def column_at(self, x: float) -> int:
        """The column drawn at viewport position `x` (-1 for none); `columnAt` alone is wrong over the frozen columns while the table is scrolled."""
        return self.header().section_at(x)

    def _repaint_all(self) -> None:
        self.viewport().update()
        self.header().viewport().update()

    def _pin_shift(self, x: float) -> int:
        """How far the cell drawn at viewport position `x` is from where Qt's layout has it: the scroll offset over the frozen columns (drawn unscrolled, laid out scrolled to
        the left), else 0. A point over a frozen cell is moved *back* by it before Qt is asked which cell it is."""
        if self._frozen_upto < 0:
            return 0
        offset = self.header().offset()
        return offset if offset > 0 and x < self.pinned_width() else 0

    def indexAt(self, point: QPoint) -> QModelIndex:  # type: ignore[override]
        shift = self._pin_shift(point.x())
        return super().indexAt(QPoint(point.x() - shift, point.y()) if shift else point)

    def visualRect(self, index: QModelIndex) -> QRect:  # type: ignore[override]
        rect = super().visualRect(index)
        if self._frozen_upto >= 0 and index.isValid() and self.header().offset() > 0 and index.column() in self.pinned_columns():
            rect.translate(self.header().offset(), 0)  # drawn unscrolled
        return rect

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        super().drawRow(painter, option, index)
        if self._frozen_upto >= 0 and self.header().offset() > 0:
            columns = self.pinned_columns()
            if columns:
                self._draw_pinned_cells(painter, option, index, columns)
        if self._label_only_highlight:
            self._draw_label_focus(painter, option, index)
        if self._selection_locked:
            self._draw_locked_selection_frame(painter, option, index)

    def _draw_locked_selection_frame(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """While the selection is locked a selected row (a group header too when all of its ROIs are selected) has a frame in the highlight colour and no tint: round the label cells
        when only the label is highlighted, else round the row."""
        selection = self.selectionModel()
        if not ((selection is not None and selection.isSelected(index)) or index.data(ALL_SELECTED_ROLE)):
            return
        if self._label_only_highlight:
            rect = self.label_focus_rect(index, option.rect.top(), option.rect.height())
        else:
            rect = QRect(self.columnViewportPosition(self.header().logicalIndex(0)), option.rect.top(), self.header().length(), option.rect.height())
        painter.save()
        painter.setPen(QPen(QColor(get_active_theme().highlight_color), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.restore()

    def label_focus_rect(self, index: QModelIndex, top: int, height: int) -> QRect:
        """Where the current row's focus frame goes while only the label is highlighted: round the row's label cells (#/ROIs and Label, whichever have width), or a group header's name
        stretch (up to its step numbers or the frozen columns' edge); the whole row if no label cell has any width. Where the cells are *drawn*: a frozen column at its unscrolled place."""
        if self.isFirstColumnSpanned(index.row(), index.parent()):
            left = self.columnViewportPosition(self.header().logicalIndex(0))
            if self.pinned_columns() and self.header().offset() > 0:
                left += self.header().offset()  # the part of the header that stays is drawn unscrolled
            end = group_label_edge(self, self.model(), left, left + self.header().length())
            return QRect(left, top, max(0, end - left), height)
        rect = QRect()
        for column in (COLUMN_ID, COLUMN_NAME):
            cell = self.visualRect(index.siblingAtColumn(column))
            if cell.width() > 0:
                rect = rect.united(QRect(cell.left(), top, cell.width(), height))
        if rect.isNull():
            rect = QRect(self.columnViewportPosition(self.header().logicalIndex(0)), top, self.header().length(), height)
        return rect

    def _draw_label_focus(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """The faint frame Qt draws round the current row of a view with focus, over the label cells only (Qt's own spans every column: `setAllColumnsShowFocus` is off while only the
        label is highlighted, so what it would draw across the row is not drawn). The same frame as `_draw_pinned_cells` draws for the frozen part."""
        current = self.currentIndex()
        if not (self.hasFocus() and current.isValid() and current.row() == index.row() and current.parent() == index.parent()):
            return
        focus = QStyleOptionFocusRect()
        focus.initFrom(self)
        focus.state |= QStyle.StateFlag.State_KeyboardFocusChange  # (Fusion draws the frame only for a focus that came from the keyboard)
        focus.rect = self.label_focus_rect(index, option.rect.top(), option.rect.height())
        selection = self.selectionModel()
        focus.backgroundColor = self.palette().color(QPalette.ColorRole.Highlight if selection is not None and selection.isSelected(current) else QPalette.ColorRole.Window)
        QApplication.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus, painter, self)

    def _draw_pinned_cells(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex, columns: list[int]) -> None:
        """Paint the frozen cells of a row again over the scrolled ones, at the places they have when the table is not scrolled. The delegate is handed the rectangles Qt
        would give it (scrolled) and the painter is moved by the scroll offset, so the delegate needs to know nothing of this."""
        offset, top, height = self.header().offset(), option.rect.top(), option.rect.height()
        area = QRect(0, top, sum(self.columnWidth(c) for c in columns), height)
        painter.save()
        painter.setClipRect(area, Qt.ClipOperation.IntersectClip)
        painter.fillRect(area, QColor(get_active_theme().toolbar_bg))  # what the scrolled cells drawn there showed is covered
        painter.translate(offset, 0)
        if self.isFirstColumnSpanned(index.row(), index.parent()):  # a group header is one cell across every column
            cells = [(index, QRect(self.columnViewportPosition(self.header().logicalIndex(0)), top, self.header().length(), height))]
        else:
            cells = [(index.siblingAtColumn(c), QRect(self.columnViewportPosition(c), top, self.columnWidth(c), height)) for c in columns]
        selection = self.selectionModel()
        for cell, rect in cells:
            cell_option = QStyleOptionViewItem(option)
            cell_option.rect = rect
            selected = selection is not None and selection.isSelected(cell)
            cell_option.state = (cell_option.state & ~QStyle.StateFlag.State_Selected) | (QStyle.StateFlag.State_Selected if selected else QStyle.StateFlag(0))
            self.itemDelegateForIndex(cell).paint(painter, cell_option, cell)
        current = self.currentIndex()
        if self.allColumnsShowFocus() and self.hasFocus() and current.isValid() and current.row() == index.row() and current.parent() == index.parent():
            # What `QTreeView.drawRow` adds after the cells of the current row of a view with focus (the faint frame across the whole row): without it the frozen part of
            # that row would lack the tint the rest of the row has.
            focus = QStyleOptionFocusRect()
            focus.initFrom(self)
            focus.state |= QStyle.StateFlag.State_KeyboardFocusChange  # (Fusion draws the frame only for a focus that came from the keyboard)
            focus.rect = QRect(self.columnViewportPosition(self.header().logicalIndex(0)), top, self.header().length(), height)
            focus.backgroundColor = self.palette().color(QPalette.ColorRole.Highlight if selection is not None and selection.isSelected(current) else QPalette.ColorRole.Window)
            QApplication.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus, painter, self)  # (the view's own style is wrapped by its stylesheet, whose "outline: none" draws nothing here)
        painter.restore()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        super().scrollContentsBy(dx, dy)
        if dx and self._frozen_upto >= 0:
            self._repaint_all()  # Qt moved the pixels of the frozen cells with the rest: draw them again in place

    def dataChanged(self, top_left: QModelIndex, bottom_right: QModelIndex, roles=()) -> None:  # type: ignore[override]
        if VALUES_ONLY_ROLE in roles:
            self.viewport().update()  # only what the cells say changed: nothing to lay out again (Qt's handler asks the delegate for the size of every cell of the range)
            return
        super().dataChanged(top_left, bottom_right, roles)
        if self._frozen_upto >= 0 and self.header().offset() > 0:
            self.viewport().update()  # `visualRect` of a frozen cell is not where Qt looks for the scrolled copy

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().paintEvent(event)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > _SLOW_PAINT_MS:  # only a slow paint is logged (a table repaints on every hover and scroll step: one record each would be a flood)
            log_event(logger, "roi_table.slow_paint", logging.DEBUG, ms=round(elapsed_ms, 1), width=event.rect().width(), height=event.rect().height())
        width = self.pinned_width() if self._frozen_upto >= 0 else 0
        if width:  # a line at the frozen columns' right edge, so a frozen table looks frozen before it is scrolled
            painter = QPainter(self.viewport())
            edge = QColor(get_active_theme().text_muted)
            edge.setAlpha(_FREEZE_EDGE_ALPHA)
            painter.setPen(QPen(edge, 1))
            painter.drawLine(width - 1, 0, width - 1, self.viewport().height())
            painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._frozen_upto >= 0:
            self._repaint_all()  # the freeze may start or stop applying with the width

    # -- hover (the stock hover state is per cell; the row should light up) ---------

    def is_hovered_row(self, index: QModelIndex) -> bool:
        return self._hover is not None and self._hover == (index.parent(), index.row())

    def _set_hover(self, index: QModelIndex) -> None:
        key = (index.parent(), index.row()) if index.isValid() else None
        if key != self._hover:
            self._hover = key
            self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._range_drag is not None:  # dragging a cell range: no hover, no drag and drop
            self._extend_range(event.position().toPoint())
            event.accept()
            return
        self._set_hover(self.indexAt(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_hover(QModelIndex())
        super().leaveEvent(event)

    # -- group header: chevron click toggles ------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            # (while #/ROIs is hidden there is no chevron: a group is opened and closed from its right-click menu or with the Right / Left arrow key)
            if index.isValid() and index.data(KIND_ROLE) == "group" and event.position().x() < CHEVRON_ZONE - 4 and not first_column_collapsed(self):
                self.setExpanded(index, not self.isExpanded(index))
                event.accept()
                return
            if self._begin_cell_range(event, index):
                return
            self._set_range(None)  # a click anywhere else lets go of the picked cells (a right-click does not: its menu acts on them)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._range_drag is not None and event.button() == Qt.MouseButton.LeftButton:
            self._end_range_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- picking cells: click a selected row's number again and drag --------------------------------------------------------------

    def _begin_cell_range(self, event: QMouseEvent, index: QModelIndex) -> bool:
        """A plain left press on a number (`rows.NUMERIC_COLUMNS`) of a row that is **already selected** starts picking cells (the row selection stays as it is, also with several
        rows selected); a press anywhere else is the usual row click. Qt's own press runs too, so a double-click still opens the editor; its drag and drop is stopped here."""
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            return False
        if not (index.isValid() and index.data(KIND_ROLE) == "roi" and index.column() in NUMERIC_COLUMNS and self.selectionModel().isSelected(index)):
            return False
        model = self.model()
        roi_id = model.roi_id(index)
        order = self._shown_roi_ids()
        selected = frozenset(i for i in (model.roi_id(row) for row in self.selectionModel().selectedRows()) if i is not None)
        super().mousePressEvent(event)  # (it sets what a double-click needs; the selection does not change: the row is selected already)
        self.setState(QAbstractItemView.State.NoState)  # (and no drag and drop of the row: we own the drag until the button goes up)
        self._range_drag = _RangeDrag(roi_id, index.column(), order, {r: i for i, r in reversed(list(enumerate(order)))}, selected, roi_id)
        self._set_range(((roi_id,), (index.column(),)))
        self._autoscroll.start()
        event.accept()
        return True

    def _end_range_drag(self) -> None:
        self._range_drag = None
        self._autoscroll.stop()

    def _shown_roi_ids(self) -> list[int]:
        """The ROIs on screen from top to bottom (the rows of a collapsed group are not)."""
        model = self.model()
        ids: list[int] = []
        for row in range(model.rowCount()):
            top = model.index(row, 0)
            if model.is_group(top):
                if self.isExpanded(top):
                    ids.extend(i for i in (model.roi_id(model.index(r, 0, top)) for r in range(model.rowCount(top))) if i is not None)
            elif model.roi_id(top) is not None:
                ids.append(model.roi_id(top))
        return ids

    def _shown_numeric_columns(self) -> list[int]:
        """The number columns that are on screen, left to right."""
        header = self.header()
        shown = [c for c in NUMERIC_COLUMNS if c < self.model().columnCount() and not self.isColumnHidden(c) and self.columnWidth(c) > 0]
        return sorted(shown, key=header.visualIndex)

    def _extend_range(self, pos: QPoint) -> None:
        """The pointer moved to `pos` (viewport coordinates) while a range is dragged: the range is the rows between the starting row and the one under the pointer **that are
        selected**, and the number columns between the starting column and the one under the pointer. Past the table's edge the nearest row / column counts."""
        drag = self._range_drag
        if drag is None:
            return
        area = self.viewport().rect()
        point = QPoint(max(0, min(pos.x(), area.width() - 1)), max(0, min(pos.y(), area.height() - 1)))
        index = self.indexAt(point)
        model = self.model()
        if not index.isValid():  # below the last row (or between rows): the last row, the column under the pointer
            row_id = drag.order[-1] if pos.y() >= point.y() and drag.order else drag.last_id
            column = self._column_at(point.x())
        elif model.is_group(index):  # a group header is no row to pick: the range stays where it was
            row_id, column = drag.last_id, self._column_at(point.x())
        else:
            row_id, column = model.roi_id(index), index.column()
        if row_id not in drag.position or column < 0:
            return
        drag.last_id = row_id
        low, high = sorted((drag.position[drag.anchor_id], drag.position[row_id]))
        rows = tuple(r for r in drag.order[low:high + 1] if r in drag.selected)
        header = self.header()
        first, last = sorted((header.visualIndex(drag.anchor_column), header.visualIndex(column)))
        columns = tuple(c for c in self._shown_numeric_columns() if first <= header.visualIndex(c) <= last)
        self._set_range((rows, columns))

    def _column_at(self, x: int) -> int:
        """The column under viewport x, or the last column on screen when x is past the last one."""
        column = self.header().logicalIndexAt(x)
        if column >= 0:
            return column
        shown = [c for c in range(self.model().columnCount()) if not self.isColumnHidden(c)]
        return max(shown, key=self.header().visualIndex) if shown else -1

    def _autoscroll_tick(self) -> None:
        """While the button is down and the pointer is at or past the edge of the table, scroll that way (and let the range follow)."""
        if self._range_drag is None:
            self._autoscroll.stop()
            return
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        area = self.viewport().rect()
        bar = self.verticalScrollBar()
        if pos.y() < AUTOSCROLL_EDGE:
            bar.setValue(bar.value() - max(4, min(40, (AUTOSCROLL_EDGE - pos.y()) // 2)))
        elif pos.y() > area.height() - AUTOSCROLL_EDGE:
            bar.setValue(bar.value() + max(4, min(40, (pos.y() - area.height() + AUTOSCROLL_EDGE) // 2)))
        side = self.horizontalScrollBar()
        if pos.x() < AUTOSCROLL_EDGE:
            side.setValue(side.value() - 10)
        elif pos.x() > area.width() - AUTOSCROLL_EDGE:
            side.setValue(side.value() + 10)
        self._extend_range(pos)

    # -- the marks: the picked range and the copied block (`cell_marks.py`) -------------------------------------------------------

    def _set_range(self, picked: tuple[tuple[int, ...], tuple[int, ...]] | None) -> None:
        """Set the picked range to (rows, columns), or drop it (None). Repaints only if it changed."""
        before = (self._marks.range_rows, self._marks.range_columns)
        if picked is None:
            self._marks.clear_range()
        else:
            self._marks.set_range(*picked)
        if before != (self._marks.range_rows, self._marks.range_columns):
            self.viewport().update()

    def cell_range(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """The picked range: (ROI ids top to bottom as the table lists them, column numbers left to right); both empty when none is picked."""
        header = self.header()
        order = {r: i for i, r in enumerate(self.model().ordered_roi_ids())}
        rows = tuple(sorted(self._marks.range_rows, key=lambda r: order.get(r, len(order))))
        return rows, tuple(sorted(self._marks.range_columns, key=header.visualIndex))

    def set_cell_range(self, rows, columns) -> None:
        self._set_range((tuple(rows), tuple(columns)))

    def restrict_cell_range(self, selected_ids) -> None:
        """The ROI selection changed: the range keeps only the rows still selected (it goes when none is)."""
        before = (self._marks.range_rows, self._marks.range_columns)
        self._marks.restrict_range(selected_ids, self._shown_numeric_columns())
        if before != (self._marks.range_rows, self._marks.range_columns):
            self.viewport().update()

    def set_copied_cells(self, rows, columns, token: str) -> None:
        """Mark the copied block with the marching ants (until `clear_copied_cells`, Esc or the next copy)."""
        self._marks.set_copied(rows, columns, token)
        if self._marks.has_copied() and self.isVisible():
            self._ants.start()
        self.viewport().update()

    def copied_token(self) -> str:
        return self._marks.copied_token

    def clear_copied_cells(self) -> None:
        had = self._marks.has_copied()
        self._marks.clear_copied()
        self._ants.stop()
        if had:
            self.viewport().update()

    def clear_cell_marks(self) -> None:
        """Esc / "Cancel cell selection": let go of the picked range and the copied block's ants."""
        self._end_range_drag()
        self._set_range(None)
        self.clear_copied_cells()

    def has_cell_marks(self) -> bool:
        return self._marks.has_any()

    def _ants_tick(self) -> None:
        if not self.isVisible() or not self._marks.has_copied():
            self._ants.stop()  # (a hidden panel does no work: `showEvent` starts it again)
            return
        self._marks.step_ants()
        self.viewport().update()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._marks.has_copied():
            self._ants.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._ants.stop()

    def ants_phase(self) -> int:
        return self._marks.ants_phase

    def range_edges(self, index: QModelIndex) -> Edges | None:
        """The sides of this cell on the border of the picked range, or None if it is not in it."""
        return self._edges_of(index, self._marks.range_rows, self._marks.range_columns)

    def copied_edges(self, index: QModelIndex) -> Edges | None:
        """The sides of this cell on the border of the copied block, or None if it is not in it."""
        return self._edges_of(index, self._marks.copied_rows, self._marks.copied_columns)

    def _edges_of(self, index: QModelIndex, rows, columns) -> Edges | None:
        if not rows or not columns:
            return None
        model = self.model()
        roi_id = model.roi_id(index)
        if roi_id is None or roi_id not in rows or index.column() not in columns:
            return None
        above, below = self.indexAbove(index), self.indexBelow(index)
        return block_edges(
            roi_id, index.column(), rows, columns,
            model.roi_id(above) if above.isValid() else None, model.roi_id(below) if below.isValid() else None,
            self._next_column(index.column(), -1), self._next_column(index.column(), 1),
        )

    def _next_column(self, column: int, step: int) -> int | None:
        """The column shown left (step -1) or right (+1) of `column`, or None."""
        header = self.header()
        visual = header.visualIndex(column) + step
        while 0 <= visual < header.count():
            logical = header.logicalIndex(visual)
            if not self.isColumnHidden(logical) and self.columnWidth(logical) > 0:
                return logical
            visual += step
        return None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and self._on_chip(index, event.position().x()):
                self.chip_double_clicked.emit(index.siblingAtColumn(0))
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _on_chip(self, index: QModelIndex, x: float) -> bool:
        """Is ``x`` (viewport coordinates) over the colour chip of the row?"""
        left = self.visualRect(index.siblingAtColumn(0)).left()
        if index.data(KIND_ROLE) == "group":
            return not first_column_collapsed(self) and left + CHEVRON_ZONE - 2 <= x <= left + CHEVRON_ZONE + 14  # (no chip while #/ROIs is hidden)
        if index.column() != COLUMN_ID:
            return False
        start = left + STRIP_WIDTH + 5 + (GROUP_INDENT if index.data(DEPTH_ROLE) else 0)
        return start - 3 <= x <= start + CHIP_SIZE + 3

    def wheel_steps(self, index: QModelIndex) -> bool:
        """Does a plain wheel turn into a step over this cell? Yes for a number the user can edit (`rows.NUMERIC_COLUMNS`) on a ROI row that is **selected**; anywhere else the
        wheel scrolls the table as usual (a group header, a blank cell, a row that is not selected, the label)."""
        return (
            index.isValid() and index.data(KIND_ROLE) == "roi" and index.column() in NUMERIC_COLUMNS
            and bool(index.flags() & Qt.ItemFlag.ItemIsEditable) and self.selectionModel().isSelected(index)
        )

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        """Over a number of a selected ROI row the wheel steps its value (Shift: large steps); Ctrl + wheel does it over any cell. Elsewhere a plain wheel scrolls.

        One step per full notch (120 units): a smooth wheel or a touchpad sends many small turns, which add up instead of each being a step."""
        delta = event.angleDelta().y()
        if delta and self._range_drag is None:  # (while cells are being picked the wheel only scrolls)
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and (event.modifiers() & Qt.KeyboardModifier.ControlModifier or self.wheel_steps(index)):
                if self._wheel_turn * delta < 0:
                    self._wheel_turn = 0  # the other way: start counting again
                self._wheel_turn += delta
                large = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                while abs(self._wheel_turn) >= WHEEL_NOTCH:
                    direction = 1 if self._wheel_turn > 0 else -1
                    self._wheel_turn -= direction * WHEEL_NOTCH
                    self.step_requested.emit(index, direction, large)
                event.accept()
                return
        super().wheelEvent(event)

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:  # type: ignore[override]
        """Nothing: the chevron is painted by the delegate."""

    # -- keyboard -------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if self.state() != QAbstractItemView.State.EditingState:
            if matches(event, "roi_table.copy"):
                self.copy_requested.emit()
                event.accept()
                return
            if matches(event, "roi_table.paste"):
                self.paste_requested.emit()
                event.accept()
                return
            if matches(event, "roi_table.fill_down"):
                self.fill_down_requested.emit()
                event.accept()
                return
            if matches(event, "roi_table.cancel_selection") and self.has_cell_marks():
                self.clear_cell_marks()
                event.accept()
                return
            if matches(event, "roi_table.delete"):
                self.delete_requested.emit()
                event.accept()
                return
            if matches(event, "roi_table.move"):
                self.move_requested.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
                event.accept()
                return
        super().keyPressEvent(event)
