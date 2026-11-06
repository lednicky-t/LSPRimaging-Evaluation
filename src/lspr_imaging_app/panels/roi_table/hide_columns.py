"""The ROI / Groups table's "Hide column" and "Show hidden column" entries (2026-11-06): in the menu a right-click on a column header opens, after the view choice.

**Screen purpose**: take a column out of the way (a diameter, a step, a comparison column) and get it back.
**Main user actions**: right-click a column header -> "Hide column “<that column>”"; right-click any header -> "Show hidden column" (a list of the hidden columns: pick one) or
"Show all hidden columns".
**Meaning**: hiding is the user's choice on top of the view mode. A column shows when the view mode has it and the user has not hidden it, so switching between Simple and
Geometry never brings a hidden column back, and a hidden column the other mode does not show is not offered in the list (showing it would change nothing on screen). The
#/ROIs column can be hidden too, but it stays a column of zero width (`RoiTreeView.refresh_column_visibility` says why): the ROI rows then show no dot or number, the group
headers stay as they are. A step or comparison column is remembered by what it is (`column_keys.py`), not by its
number, so it stays hidden when steps are added or removed, and a hidden column of another experiment hides nothing here.
**Remembered** like every control (``ROI_TABLE_HIDDEN_COLUMNS``). A table sorted by a column that is then hidden goes back to the ``#`` order, as when a view mode hides it.
**Error messages**: none; hiding a column says in the title bar how to bring it back.

The view does the hiding (`view.py`: `set_hidden_keys`, `refresh_column_visibility`); this file only asks for it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QMenu, QWidget

from ...storage.ui_state_keys import ROI_TABLE_HIDDEN_COLUMNS
from ..ui_state import UiStateStore
from .column_keys import key_from_payload, key_to_payload
from .model import COLUMN_KIND_ROLE
from .step_columns import plain_title
from .view import RoiTreeView

HIDE_TIP = "Take this column out of the way. Right-click any column header and choose Show hidden column to bring it back."
NOTHING_HIDDEN_TIP = "No column is hidden."


class HideColumns(QObject):
    def __init__(
        self, panel: QWidget, tree: RoiTreeView, menu: QMenu, notice: Callable[..., None], store: Callable[[], UiStateStore | None], column_hidden: Callable[[int], None]
    ) -> None:
        """`column_hidden(column)`: told when a column was hidden (the panel puts the sort back to the ``#`` order if the table was sorted by it)."""
        super().__init__(panel)
        self._tree = tree
        self._notice = notice
        self._store = store
        self._column_hidden = column_hidden
        self._section = -1  # the header the menu was opened on
        menu.addSeparator()
        self._hide = menu.addAction("Hide column")
        self._show_menu = menu.addMenu("Show hidden column")
        self._show_all = menu.addAction("Show all hidden columns")
        self._hide.triggered.connect(lambda _checked=False: self._hide_section(self._section))
        self._show_all.triggered.connect(lambda _checked=False: self._set_hidden(frozenset()))
        self.prepare(-1)

    # -- the menu -------------------------------------------------------------------------------------------------------------

    def hidden_columns(self) -> list[tuple[int, str]]:
        """The hidden columns this view could show, left to right on screen: (column number, title). A column the view mode does not have is not in it."""
        model, header = self._tree.model(), self._tree.header()
        columns = [c for c in range(model.columnCount()) if self._tree.is_user_hidden(c) and self._tree.is_in_view(c)]
        columns.sort(key=header.visualIndex)
        return [(c, self._title(c)) for c in columns]

    def _title(self, column: int) -> str:
        model = self._tree.model()
        title = str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        return plain_title(title) if model.headerData(column, Qt.Orientation.Horizontal, COLUMN_KIND_ROLE) == "compare" else title  # (a menu entry cannot draw subscripts)

    def prepare(self, section: int) -> None:
        """Set the entries for a menu opened on the header of `section` (-1: not on a column)."""
        self._section = section
        key = self._tree.column_key(section) if section >= 0 else None
        self._hide.setText(f"Hide column “{self._title(section)}”" if key is not None else "Hide column")
        self._hide.setEnabled(key is not None)
        self._hide.setToolTip(HIDE_TIP if key is not None else "Right-click the header of the column to hide.")
        hidden = self.hidden_columns()
        self._show_menu.clear()
        for column, title in hidden:
            action = self._show_menu.addAction(title)
            action.triggered.connect(partial(self._show, self._tree.column_key(column)))  # (the key is taken now, when the entry is made: the columns may be renumbered by the time it is clicked)
        self._show_menu.setEnabled(bool(hidden))
        self._show_menu.menuAction().setToolTip("Bring one of the hidden columns back." if hidden else NOTHING_HIDDEN_TIP)
        self._show_all.setEnabled(bool(hidden))
        self._show_all.setToolTip("Bring every hidden column back." if hidden else NOTHING_HIDDEN_TIP)

    # -- the actions ----------------------------------------------------------------------------------------------------------

    def _hide_section(self, section: int) -> None:
        key = self._tree.column_key(section)
        if key is None:
            return
        title = self._title(section)
        self._set_hidden(self._tree.hidden_keys() | {key})
        self._column_hidden(section)
        self._notice(f"Hid column “{title}”. Right-click any column header and choose Show hidden column to bring it back.")

    def _show(self, key: object, _checked: bool = False) -> None:
        self._set_hidden(self._tree.hidden_keys() - {key})

    def _set_hidden(self, keys: frozenset) -> None:
        # (no `prepare` here: it rebuilds the list of hidden columns, which would delete the very action whose click is being handled. The panel prepares the entries
        # every time the menu opens.)
        self._tree.set_hidden_keys(keys)
        self._save()

    # -- remembered state -----------------------------------------------------------------------------------------------------

    def _save(self) -> None:
        store = self._store()
        if store is not None:
            store.set(ROI_TABLE_HIDDEN_COLUMNS, [key_to_payload(key) for key in sorted(self._tree.hidden_keys(), key=repr)])

    def restore_ui_state(self, store: UiStateStore) -> None:
        saved = store.get(ROI_TABLE_HIDDEN_COLUMNS)
        if isinstance(saved, list):
            self._tree.set_hidden_keys(key for key in (key_from_payload(item) for item in saved) if key is not None)
        self.prepare(-1)
