"""The Line order table (2026-11-22): the Manual draw order of a plot's lines, edited as a list.

Screen purpose: choose by hand which line lies over which where the lines of a plot overlap (Points & lines menu -> Draw order -> Manual -> "Order..."). Display only: no number changes.
Opened from that menu, one per plot (Sensorgram, LbL, S_bulk), a small window that stays open beside the plot while the order is tried; the plot follows every change at once.

Main user actions:
- drag a row (or several selected rows) to another place;
- the buttons beside the list, and the right-click menu of the rows, **To top / Up / Down / To bottom** (the same Move entries, icons and wording as the ROI table's; an entry that would
  change nothing is left out of the menu, greyed on the button);
- "Own order": forget the manual list, the lines go back to the plot's own order.

Layout:
- Left (the whole window): the list. One row per line the plot draws now: its colour (a small swatch, the line's own colour) and its name, **the first row in front** (a caption says so).
- Right: To top / Up / Down / To bottom, a gap, Own order.
- Bottom: a hint line (what is in front, that the order is used only in Manual).

Important states: a line the plot draws that is not in the saved list (a new one) is shown first (in front); a saved name that is no longer a line is not shown (and is dropped at the next change).
The rows follow the plot while the window is open (`refresh`, called when the plot redraws). The saved list is the session's (`selection/plot_line_order_module.py`, schema 1.16) and is written at
every change (autosave). The mode is the menu's: with Auto or Legend picked the table can still be edited, and says that it is not used now.

Validation / errors: nothing can be entered wrongly (rows only move). No undo step: the table itself undoes (drag it back); "Own order" clears.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...selection.plot_line_order_module import PlotLineOrderModule
from ..menu_icons import give_icon, menu_icon
from .draw_order import DRAW_MANUAL, MOVE_BOTTOM, MOVE_DOWN, MOVE_TOP, MOVE_UP, can_move, manual_rows, moved

_SWATCH_PX = 14
HINT = "The first row is in front. Drag rows, or use the buttons or the right-click menu."
NOT_USED = "Draw order is {mode} now: this list is used when you pick Manual."
MOVES = (
    (MOVE_TOP, "To top", "move_top", "Bring the selected lines to the front."),
    (MOVE_UP, "Up", "move_up", "Move the selected lines one place towards the front."),
    (MOVE_DOWN, "Down", "move_down", "Move the selected lines one place towards the back."),
    (MOVE_BOTTOM, "To bottom", "move_bottom", "Send the selected lines to the back."),
)


@dataclass(frozen=True)
class LineRow:
    """One line of a plot as the table shows it: the name the manual list keeps, what is written, and the line's colour."""

    key: str
    label: str
    color: str


def swatch(color: str) -> QIcon:
    """A small rounded square in the line's colour with a thin outline (so a white line shows on a light window too)."""
    pixmap = QPixmap(_SWATCH_PX, _SWATCH_PX)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(128, 128, 128, 160))
    painter.drawRoundedRect(1, 1, _SWATCH_PX - 2, _SWATCH_PX - 2, 3, 3)
    painter.end()
    return QIcon(pixmap)


class LineOrderDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        source: Callable[[], Sequence[LineRow]],
        module: PlotLineOrderModule,
        plot_id: str,
        mode_now: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Line order: {title}")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setObjectName("line_order_dialog")
        self.resize(300, 380)
        self._source, self._module, self._plot_id, self._mode_now = source, module, plot_id, mode_now
        self._shown: list[LineRow] = []

        self._list = QListWidget(self)
        self._list.setObjectName("line_order_list")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setIconSize(QSize(_SWATCH_PX, _SWATCH_PX))
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        self._list.model().rowsMoved.connect(lambda *_args: QTimer.singleShot(0, self._dropped))  # after the drop has finished: the list is rebuilt from the saved order

        self._buttons: dict[str, QToolButton] = {}
        side = QVBoxLayout()
        side.setSpacing(4)
        for how, text, icon_key, tip in MOVES:
            button = QToolButton(self)
            button.setObjectName(f"line_order_{how}")
            button.setText(text)
            button.setIcon(menu_icon(icon_key))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, h=how: self.move_selected(h))
            self._buttons[how] = button
            side.addWidget(button)
        side.addSpacing(12)
        self._reset = QToolButton(self)
        self._reset.setObjectName("line_order_reset")
        self._reset.setText("Own order")
        self._reset.setIcon(menu_icon("reset"))
        self._reset.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._reset.setToolTip("Forget the manual list: the lines go back to the plot's own order (first line in front).")
        self._reset.clicked.connect(lambda _checked=False: self.reset_order())
        side.addWidget(self._reset)
        side.addStretch(1)

        caption = QLabel("Front", self)
        self._hint = QLabel(HINT, self)
        self._hint.setWordWrap(True)
        body = QHBoxLayout()
        body.addWidget(self._list, 1)
        body.addLayout(side)
        outer = QVBoxLayout(self)
        outer.addWidget(caption)
        outer.addLayout(body, 1)
        outer.addWidget(QLabel("Back", self))
        outer.addWidget(self._hint)
        self.refresh()

    # -- what it shows ------------------------------------------------------------------------------------------------------------

    def keys(self) -> list[str]:
        """The rows' names, front first."""
        return [str(self._list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self._list.count())]

    def selected_keys(self) -> set[str]:
        return {str(item.data(Qt.ItemDataRole.UserRole)) for item in self._list.selectedItems()}

    def refresh(self) -> None:
        """Show the plot's lines now, in the saved order (the new ones first). Called when the plot redraws; a table that already shows exactly this is left alone."""
        lines = list(self._source())
        by_key = {line.key: line for line in lines}
        rows = [by_key[k] for k in manual_rows([line.key for line in lines], self._module.order(self._plot_id))]
        if rows != self._shown or self.keys() != [row.key for row in rows]:
            keep = self.selected_keys()
            self._list.blockSignals(True)
            self._list.clear()
            for row in rows:
                item = QListWidgetItem(swatch(row.color), row.label)
                item.setData(Qt.ItemDataRole.UserRole, row.key)
                item.setToolTip(row.label)
                self._list.addItem(item)
                item.setSelected(row.key in keep)
            self._list.blockSignals(False)
            self._shown = rows
        self._update_buttons()

    def _update_buttons(self) -> None:
        rows, chosen = self.keys(), self.selected_keys()
        for how, button in self._buttons.items():
            button.setEnabled(can_move(rows, chosen, how))
        self._reset.setEnabled(bool(self._module.order(self._plot_id)))
        mode = self._mode_now() if self._mode_now is not None else None
        self._hint.setText(HINT if mode in (None, DRAW_MANUAL) else f"{HINT}\n{NOT_USED.format(mode=mode)}")

    # -- changing it --------------------------------------------------------------------------------------------------------------

    def move_selected(self, how: str) -> None:
        """Move the selected rows (To top / Up / Down / To bottom); nothing selected, or nothing that would change: nothing happens."""
        rows, chosen = self.keys(), self.selected_keys()
        if can_move(rows, chosen, how):
            self._commit(moved(rows, chosen, how))

    def reset_order(self) -> None:
        self._module.set_order(self._plot_id, [])
        self.refresh()

    def _dropped(self) -> None:
        """A row was dragged to another place: the list's order is the new order."""
        self._commit(self.keys())

    def _commit(self, keys: list[str]) -> None:
        """Save `keys` as the plot's manual list (the plot redraws), and show it."""
        self._module.set_order(self._plot_id, keys)
        self._shown = []  # force the rebuild: a drop leaves the widget in the new order but with Qt's moved items
        self.refresh()

    # -- the right-click menu (the ROI table's Move entries) ----------------------------------------------------------------------

    def _context_menu(self, position: QPoint) -> None:
        item = self._list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self._list.clearSelection()
            item.setSelected(True)
        rows, chosen = self.keys(), self.selected_keys()
        menu = QMenu(self._list)
        for how, text, icon_key, _tip in MOVES:
            if can_move(rows, chosen, how):  # a menu offers only what can be done now
                give_icon(menu.addAction(text, lambda h=how: self.move_selected(h)), icon_key)
        if menu.isEmpty():
            menu.deleteLater()
            return
        menu.exec(self._list.viewport().mapToGlobal(position))
        menu.deleteLater()
