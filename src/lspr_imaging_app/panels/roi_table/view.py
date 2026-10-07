"""``RoiTreeView``: the tree widget, with the behaviour the stock one lacks.

The stock `QTreeView` would draw its own branch arrows in an indentation gutter
and expand a group on double-click. Here a group header paints its own
chevron (`delegate.py`), a click on it toggles the group, and a double-click
renames. Keyboard: Delete asks to delete, Alt+Up / Alt+Down ask to move.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QKeyEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTreeView, QWidget

from .delegate import CHEVRON_ZONE, CHIP_SIZE, GROUP_INDENT, STRIP_WIDTH
from .model import DEPTH_ROLE, KIND_ROLE
from .rows import COLUMN_COUNT, COLUMN_ID, COLUMN_NAME, COLUMN_RING_IN, COLUMN_RING_OUT, COLUMN_SAMPLE, COLUMN_TITLES, COLUMN_X, COLUMN_Y

_SORT_INDICATOR_ROOM = 22
_CELL_PADDING = 16


def suggested_column_widths(metrics: QFontMetrics) -> list[int]:
    """Default column widths: the worst-case text each column must hold, plus
    padding (and room for the sort arrow on the header). Measured, not
    guessed, so figures are not clipped in the user's font."""
    advance = metrics.horizontalAdvance
    number = advance("9999.9") + _CELL_PADDING
    diameter = max(advance("999.9") + _CELL_PADDING, advance("Ring out") + _SORT_INDICATOR_ROOM)
    widths = [0] * COLUMN_COUNT
    widths[COLUMN_ID] = STRIP_WIDTH + 5 + GROUP_INDENT + CHIP_SIZE + 7 + advance("9999") + 10
    widths[COLUMN_NAME] = advance("Name 99") + _CELL_PADDING
    widths[COLUMN_X] = widths[COLUMN_Y] = max(number, advance(COLUMN_TITLES[COLUMN_X]) + _SORT_INDICATOR_ROOM)
    widths[COLUMN_SAMPLE] = max(diameter, advance("Sample") + _SORT_INDICATOR_ROOM)
    widths[COLUMN_RING_IN] = max(diameter, advance("Ring in") + _SORT_INDICATOR_ROOM)
    widths[COLUMN_RING_OUT] = diameter
    return widths


class RoiTreeView(QTreeView):
    delete_requested = pyqtSignal()
    move_requested = pyqtSignal(int)
    """-1 = up, +1 = down."""
    chip_double_clicked = pyqtSignal(QModelIndex)
    """A double-click on a colour chip (a ROI's dot or a group's chip): change
    the colour instead of editing the row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:  # type: ignore[override]
        """Nothing: the chevron is painted by the delegate."""

    # -- keyboard -------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if self.state() != QAbstractItemView.State.EditingState:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_requested.emit()
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.move_requested.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
                event.accept()
                return
        super().keyPressEvent(event)
