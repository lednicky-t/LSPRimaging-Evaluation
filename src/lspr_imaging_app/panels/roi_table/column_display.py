"""The ROI / Groups table's "Display" menu of a column (2026-11-26): how the numbers of one column are written.

**Screen purpose**: let the user show a column with more or fewer decimals, or in scientific notation, without changing a number that is stored, copied or typed.
**Main user actions**: right-click a numeric column's header (or a number cell in it) -> "Display" -> "More decimals" / "Fewer decimals" / "Automatic decimals", and
"Normal" / "Scientific" notation.
**Meaning**: a setting is per column, kept by the column's key (`column_keys.py`: a fixed column's number, a step's reference, a comparison column's pair and operation), so it stays
with its step when steps come and go. It is display only: the tool tip keeps its full numbers, a copied cell its plain text, the cell editor its plain number (`number_format.py` says
what decimals mean in each notation). A column with no setting looks as it always did.
**Remembered** like every control (``ROI_TABLE_COLUMN_DISPLAY``).
**Error messages**: none; an entry that cannot be used (no fewer decimals than none) is left out.

The model writes the numbers (`RoiTreeModel.set_column_display`); this file only asks for it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PyQt6.QtGui import QActionGroup
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMenu, QWidget

from ...storage.ui_state_keys import ROI_TABLE_COLUMN_DISPLAY
from ..menu_icons import give_icon, give_persistent_icon
from ..ui_state import UiStateStore
from .column_keys import key_from_payload, key_to_payload
from .model import RoiTreeModel
from .number_format import MAX_DECIMALS, MIN_DECIMALS, NOTATION_LABELS, NOTATIONS, SCIENTIFIC, ColumnDisplay, clean_display, format_number, stepped
from .view import RoiTreeView

MENU_TIP = (
    "How this column's numbers are written. Display only: the tool tip keeps the full numbers, a copied cell its plain text, and the cell editor its plain number."
)
NOTATION_TIPS = {
    "normal": "595.773",
    "scientific": "5.95773e+02 (one digit before the point)",
}
_EXAMPLE = 595.773
_NOTATION_ICONS = {"normal": "notation_normal", "scientific": "notation_scientific"}


class ColumnDisplayMenu(QObject):
    def __init__(
        self, panel: QWidget, tree: RoiTreeView, model: RoiTreeModel, header_menu: QMenu, store: Callable[[], UiStateStore | None], changed: Callable[[int], None]
    ) -> None:
        """`changed(column)`: told after a column's setting changed (the panel fits the step columns to their new text)."""
        super().__init__(panel)
        self._tree = tree
        self._model = model
        self._store = store
        self._changed = changed
        self._menu = give_persistent_icon(header_menu.addMenu("Display"), "column_display")
        self._menu.menuAction().setToolTip(MENU_TIP)
        self.prepare(-1)

    # -- the menu -------------------------------------------------------------------------------------------------------------

    def prepare(self, section: int) -> None:
        """Set the entries of the header menu for a menu opened on the header of `section` (-1: not on a column). Made again at every opening: the settings change."""
        self._menu.clear()
        self.fill(self._menu, section, persistent=True)
        self._menu.menuAction().setVisible(not self._menu.isEmpty())  # only on a column that holds numbers

    def add_to(self, menu: QMenu, column: int) -> None:
        """The same "Display" entries as a submenu of a pop-up menu (the right-click menu of a number cell); nothing is added for a column with no numbers."""
        sub = QMenu("Display", menu)
        self.fill(sub, column, persistent=False)
        if not sub.isEmpty():
            give_icon(menu.addMenu(sub), "column_display").setToolTip(MENU_TIP)
        else:
            sub.deleteLater()

    def fill(self, menu: QMenu, column: int, *, persistent: bool) -> None:
        key = self._tree.column_key(column) if column >= 0 else None
        plain = self._model.plain_decimals(column) if key is not None else None
        if key is None or plain is None:
            return
        icon = give_persistent_icon if persistent else give_icon
        display = self._model.column_display(column)
        shown = self._model.shown_decimals(column)
        shown = plain if shown is None else shown
        if shown < MAX_DECIMALS:
            icon(menu.addAction(f"More decimals ({shown} → {shown + 1})", partial(self._apply, key, column, stepped(display, shown, +1))), "decimals_more")
        if shown > MIN_DECIMALS:
            icon(menu.addAction(f"Fewer decimals ({shown} → {shown - 1})", partial(self._apply, key, column, stepped(display, shown, -1))), "decimals_fewer")
        if display.decimals is not None:
            automatic = icon(menu.addAction("Automatic decimals", partial(self._apply, key, column, ColumnDisplay(None, display.notation))), "decimals_auto")
            automatic.setToolTip("Back to the number of decimals the table chooses for this column.")
        menu.addSeparator()
        group = QActionGroup(menu)
        group.setExclusive(True)
        for notation in NOTATIONS:
            action = icon(menu.addAction(NOTATION_LABELS[notation], partial(self._apply, key, column, ColumnDisplay(display.decimals, notation))), _NOTATION_ICONS[notation])
            action.setCheckable(True)
            action.setChecked(display.notation == notation)
            action.setToolTip(f"Write the numbers like {format_number(_EXAMPLE, 5 if notation == SCIENTIFIC else 3, notation)}: {NOTATION_TIPS[notation]}.")
            group.addAction(action)

    # -- the actions ----------------------------------------------------------------------------------------------------------

    def _apply(self, key: object, column: int, display: ColumnDisplay, _checked: bool = False) -> None:
        # (no `prepare` here: it would delete the action whose click is being handled; the panel prepares the entries every time the menu opens)
        self._model.set_column_display(key, display)  # type: ignore[arg-type]
        self._save()
        self._changed(column)

    # -- remembered state -----------------------------------------------------------------------------------------------------

    def _save(self) -> None:
        store = self._store()
        if store is not None:
            saved = [
                {"key": key_to_payload(key), "decimals": display.decimals, "notation": display.notation}
                for key, display in sorted(self._model.column_displays().items(), key=lambda item: repr(item[0]))
            ]
            store.set(ROI_TABLE_COLUMN_DISPLAY, saved)

    def restore_ui_state(self, store: UiStateStore) -> None:
        saved = store.get(ROI_TABLE_COLUMN_DISPLAY)
        displays: dict = {}
        if isinstance(saved, list):
            for item in saved:
                if not isinstance(item, dict):
                    continue
                key = key_from_payload(item.get("key"))
                if key is not None:
                    displays[key] = clean_display(item.get("decimals"), item.get("notation"))
        self._model.set_column_displays(displays)
        self.prepare(-1)
