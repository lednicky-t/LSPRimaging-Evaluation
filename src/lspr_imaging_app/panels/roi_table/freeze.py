"""The ROI / Groups table's "Freeze columns" entries (2026-11-06): in the menu a right-click on a column header opens, next to the view choice.

**Screen purpose**: keep the first columns (the ROI number, a group's header) in view while the table is scrolled sideways to its step columns.
**Main user actions**: right-click a column header, "Freeze columns up to <that column>"; or "Unfreeze columns".
**Meaning**: frozen = the columns from the first one (on screen) up to the one chosen, like the freeze panes of a spreadsheet; not one column alone, because a column that stays
while the ones before it scroll away would be cut loose from the row it belongs to. Only the fixed columns (#/ROIs to Shape) can be the last one: the step columns are
the ones that scroll. A group's name is drawn across the columns after #/ROIs, so a freeze up to #/ROIs alone keeps the chevron, colour and count of a group but little room
for its name; the tool tip says so.
**Remembered** like every control: the last frozen column is saved with the other table settings (``ROI_TABLE_FROZEN_UPTO``).
**Error messages**: a freeze that would fill too much of the panel cannot be applied (`view.py`); the title bar says so and the freeze starts working when the panel is wider.

The painting and mouse mapping that make a column stay are in ``view.py``; this file only asks for them.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMenu, QWidget

from ...storage.ui_state_keys import ROI_TABLE_FROZEN_UPTO
from ..ui_state import UiStateStore
from .rows import COLUMN_COUNT, COLUMN_TITLES
from .view import RoiTreeView

NOT_FROZEN = -1

FREEZE_TIP = (
    "Keep this column and the ones to its left in view when the table is scrolled sideways to the step columns. "
    "A group's name is drawn across the columns after #/ROIs, so freeze up to the second column to keep the names in view too."
)
STEP_COLUMN_TIP = "Only the ROI columns (#/ROIs to Shape) can be frozen; the step columns are the ones that scroll."


class FreezeColumns(QObject):
    def __init__(self, panel: QWidget, tree: RoiTreeView, menu: QMenu, notice: Callable[..., None], store: Callable[[], UiStateStore | None]) -> None:
        super().__init__(panel)
        self._tree = tree
        self._notice = notice
        self._store = store
        self._section = -1  # the header the menu was opened on
        menu.addSeparator()
        self._freeze = menu.addAction("Freeze columns up to here")
        self._unfreeze = menu.addAction("Unfreeze columns")
        self._unfreeze.setToolTip("Let every column scroll sideways again.")
        self._freeze.triggered.connect(lambda _checked=False: self._choose(self._section))
        self._unfreeze.triggered.connect(lambda _checked=False: self._choose(NOT_FROZEN))
        self.prepare(-1)

    def prepare(self, section: int) -> None:
        """Set the entries for a menu opened on the header of `section` (-1: not on a column)."""
        self._section = section
        frozen = self._tree.frozen_upto()
        is_fixed = 0 <= section < COLUMN_COUNT
        self._freeze.setText(f"Freeze columns up to “{COLUMN_TITLES[section]}”" if is_fixed else "Freeze columns up to here")
        self._freeze.setEnabled(is_fixed)
        self._freeze.setToolTip(FREEZE_TIP if is_fixed else STEP_COLUMN_TIP)
        self._freeze.setVisible(section != frozen)  # the column the freeze already ends at has nothing to offer but "Unfreeze"
        self._unfreeze.setVisible(frozen != NOT_FROZEN)

    def _choose(self, column: int) -> None:
        self._tree.set_frozen_upto(column)
        store = self._store()
        if store is not None:
            store.set(ROI_TABLE_FROZEN_UPTO, column)
        if column != NOT_FROZEN and self._tree.isVisible() and not self._tree.pinned_columns():
            self._notice("These columns fill too much of the panel to freeze. Narrow them or widen the panel; the freeze then starts working.")

    def restore_ui_state(self, store: UiStateStore) -> None:
        saved = store.get(ROI_TABLE_FROZEN_UPTO)
        if isinstance(saved, int) and not isinstance(saved, bool) and NOT_FROZEN <= saved < COLUMN_COUNT:
            self._tree.set_frozen_upto(saved)
        self.prepare(-1)
