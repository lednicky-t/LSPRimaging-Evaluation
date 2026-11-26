"""The selected columns of the ROI / Groups table, and what the view needs to show them (2026-11-26). The click rules are pure and live in `column_selection.py`.

The selection is kept by column **key** (`column_keys.py`), so it survives the step columns being renumbered; the controller also holds it as the column numbers the keys stand for *now*
(only columns that are shown) and, for each of them, whether it is the left / right end of a run of selected columns, which is where the frame is drawn (`frame_of`). The painting
asks `is_selected` / `frame_of` for every cell, so both are plain dictionary lookups, made once when the selection or the columns change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from .column_keys import ColumnKey
from .column_selection import click_columns, is_step_key
from .rows import COLUMN_ID

if TYPE_CHECKING:
    from .view import RoiTreeView

Frame = tuple[bool, bool]
"""(left end, right end): whether the column is the first / last of a run of selected columns, so the frame has a left / right side there."""


class ColumnSelectionController(QObject):
    changed = pyqtSignal(object)
    """The user changed the selected columns by clicking headers: a tuple of their keys, left to right. Not sent for `set_keys` / `set_step_keys`."""

    def __init__(self, tree: RoiTreeView) -> None:
        super().__init__(tree)
        self._tree = tree
        self._keys: frozenset[ColumnKey] = frozenset()
        self._anchor: ColumnKey | None = None  # the column a Shift-click measures its range from
        self._frames: dict[int, Frame] = {}  # selected (and shown) column number -> its frame sides

    # -- what the painting asks ----------------------------------------------------------------------------------------------------

    def is_selected(self, column: int) -> bool:
        return column in self._frames

    def frame_of(self, column: int) -> Frame | None:
        """The frame sides of a selected column, or None for a column that is not selected."""
        return self._frames.get(column)

    def has_selection(self) -> bool:
        return bool(self._frames)

    def has_keys(self) -> bool:
        """Some key is selected (even if its column is hidden or not there now)."""
        return bool(self._keys)

    def selected_columns(self) -> tuple[int, ...]:
        """The selected columns that are shown, left to right."""
        return tuple(column for column, key in self._shown() if key in self._keys)

    def selected_keys(self) -> tuple[ColumnKey, ...]:
        return tuple(key for _column, key in self._shown() if key in self._keys)

    # -- changing it ------------------------------------------------------------------------------------------------------------

    def click(self, column: int, modifiers: object = Qt.KeyboardModifier.NoModifier) -> None:
        """A click on the header of `column` (`SubscriptHeader.column_clicked`): select it, or add it / extend to it with Ctrl / Shift."""
        key = self._tree.column_key(column)
        if key is None:
            return
        flags = modifiers if isinstance(modifiers, Qt.KeyboardModifier) else Qt.KeyboardModifier(modifiers)
        chosen, anchor = click_columns(
            self._keys, self._anchor, key, [k for _c, k in self._shown()],
            ctrl=bool(flags & Qt.KeyboardModifier.ControlModifier), shift=bool(flags & Qt.KeyboardModifier.ShiftModifier),
        )
        self._apply(chosen, anchor, announce=True)

    def set_keys(self, keys, anchor: ColumnKey | None = None) -> None:
        """Select exactly these columns (by key) without telling anyone: for the app keeping the table's selection in step with the Sensorgram's."""
        self._apply(frozenset(keys), anchor, announce=False)

    def set_step_keys(self, refs) -> None:
        """Select exactly these step columns (by the steps' references), keeping a selected fixed or comparison column; without telling anyone."""
        wanted = frozenset({key for key in self._keys if not is_step_key(key)} | set(refs))
        self._apply(wanted, self._anchor if self._anchor in wanted else None, announce=False)

    def refresh(self) -> None:
        """The columns changed (renumbered, hidden, shown, moved): work out again which column numbers the keys stand for, and repaint if that changed. A key whose column is not there
        or not shown selects nothing, and is kept: the column may come back (a step column after the next calculation, a hidden one when it is shown)."""
        shown = self._shown()
        frames: dict[int, Frame] = {}
        for position, (column, key) in enumerate(shown):
            if key not in self._keys:
                continue
            left_open = position == 0 or shown[position - 1][1] not in self._keys
            right_open = position + 1 == len(shown) or shown[position + 1][1] not in self._keys
            frames[column] = (left_open, right_open)
        if frames != self._frames:
            self._frames = frames
            self._tree.viewport().update()
            self._tree.header().viewport().update()

    # -- inside --------------------------------------------------------------------------------------------------------------------

    def _apply(self, keys: frozenset[ColumnKey], anchor: ColumnKey | None, *, announce: bool) -> None:
        anchor = anchor if keys else None
        if keys == self._keys:
            self._anchor = anchor
            return
        self._keys, self._anchor = keys, anchor
        self.refresh()
        if announce:
            self.changed.emit(self.selected_keys())

    def _is_shown(self, column: int) -> bool:
        """The column is on screen: not hidden, and not #/ROIs collapsed to nothing (`RoiTreeView.refresh_column_visibility`)."""
        tree = self._tree
        return not tree.header().isSectionHidden(column) and not (column == COLUMN_ID and tree.is_user_hidden(COLUMN_ID))

    def _shown(self) -> list[tuple[int, ColumnKey]]:
        """(column number, key) of the columns that are shown, left to right (the frozen ones first, as drawn)."""
        header = self._tree.header()
        shown: list[tuple[int, ColumnKey]] = []
        for visual in range(header.count()):
            column = header.logicalIndex(visual)
            key = self._tree.column_key(column) if self._is_shown(column) else None
            if key is not None:
                shown.append((column, key))
        return shown
