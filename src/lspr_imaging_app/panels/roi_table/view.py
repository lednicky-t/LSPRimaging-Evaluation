"""``RoiTreeView``: the tree widget, with the behaviour the stock one lacks.

The stock `QTreeView` would draw its own branch arrows in an indentation gutter
and expand a group on double-click. Here a group header paints its own
chevron (`delegate.py`), a click on it toggles the group, and a double-click
renames. Keyboard: Delete asks to delete, Alt+Up / Alt+Down ask to move.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QModelIndex, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyleOptionHeader, QTreeView, QWidget

from lspr_ui import get_active_theme

from .delegate import CHEVRON_ZONE, CHIP_SIZE, GROUP_INDENT, STRIP_WIDTH
from .model import DEPTH_ROLE, KIND_ROLE
from .rows import COLUMN_COUNT, COLUMN_ID, COLUMN_NAME, COLUMN_RING_IN, COLUMN_RING_OUT, COLUMN_SAMPLE, COLUMN_TITLES, COLUMN_X, COLUMN_Y

_SORT_INDICATOR_ROOM = 22
_CELL_PADDING = 16


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


class SubscriptHeader(QHeaderView):
    """Header that draws ``X_y`` titles with a real, readable subscript (Unicode
    subscript letters are far too small). Every other title is drawn as usual."""

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # type: ignore[override]
        title = self.model().headerData(logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole) if self.model() else None
        if not isinstance(title, str) or "_" not in title:
            super().paintSection(painter, rect, logicalIndex)
            return
        # Let the style draw the background and sort arrow, with the text left out.
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logicalIndex
        option.text = ""
        option.icon = QIcon()
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex:
            order = self.sortIndicatorOrder()
            option.sortIndicator = (
                QStyleOptionHeader.SortIndicator.SortDown if order == Qt.SortOrder.DescendingOrder
                else QStyleOptionHeader.SortIndicator.SortUp
            )
        else:
            option.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
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
    diameter = max(advance("999.9") + _CELL_PADDING, title_width(metrics, COLUMN_TITLES[COLUMN_RING_OUT]) + _SORT_INDICATOR_ROOM)
    widths = [0] * COLUMN_COUNT
    widths[COLUMN_ID] = STRIP_WIDTH + 5 + GROUP_INDENT + CHIP_SIZE + 7 + advance("9999") + 10
    widths[COLUMN_NAME] = advance("Name 99") + _CELL_PADDING
    widths[COLUMN_X] = widths[COLUMN_Y] = max(number, advance(COLUMN_TITLES[COLUMN_X]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_SAMPLE] = max(diameter, title_width(metrics, COLUMN_TITLES[COLUMN_SAMPLE]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_RING_IN] = max(diameter, title_width(metrics, COLUMN_TITLES[COLUMN_RING_IN]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_RING_OUT] = diameter
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
    """Ctrl+wheel over a cell: index, direction (+1 / -1), large step."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeader(SubscriptHeader(Qt.Orientation.Horizontal, self))
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
            self.setColumnWidth(column, width)

    # -- hover (the stock hover state is per cell; the row should light up) ---------

    def is_hovered_row(self, index: QModelIndex) -> bool:
        return self._hover is not None and self._hover == (index.parent(), index.row())

    def _set_hover(self, index: QModelIndex) -> None:
        key = (index.parent(), index.row()) if index.isValid() else None
        if key != self._hover:
            self._hover = key
            self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._set_hover(self.indexAt(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_hover(QModelIndex())
        super().leaveEvent(event)

    # -- group header: chevron click toggles ------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.data(KIND_ROLE) == "group" and event.position().x() < CHEVRON_ZONE - 4:
                self.setExpanded(index, not self.isExpanded(index))
                event.accept()
                return
        super().mousePressEvent(event)

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
            return left + CHEVRON_ZONE - 2 <= x <= left + CHEVRON_ZONE + 14
        if index.column() != COLUMN_ID:
            return False
        start = left + STRIP_WIDTH + 5 + (GROUP_INDENT if index.data(DEPTH_ROLE) else 0)
        return start - 3 <= x <= start + CHIP_SIZE + 3

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        """Ctrl + wheel over a cell steps its value (a plain wheel still scrolls)."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.angleDelta().y():
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                large = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self.step_requested.emit(index, 1 if event.angleDelta().y() > 0 else -1, large)
                event.accept()
                return
        super().wheelEvent(event)

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:  # type: ignore[override]
        """Nothing: the chevron is painted by the delegate."""

    # -- keyboard -------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if self.state() != QAbstractItemView.State.EditingState:
            if event.matches(QKeySequence.StandardKey.Copy):
                self.copy_requested.emit()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                self.paste_requested.emit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_D and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.fill_down_requested.emit()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_requested.emit()
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.move_requested.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
                event.accept()
                return
        super().keyPressEvent(event)
