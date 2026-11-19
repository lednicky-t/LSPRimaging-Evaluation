"""The right-click menu on a mark of a Mask row (2026-10-16).

Pure menu construction, like `roi_layout_menu.py`: it is handed the entries to offer and returns the key of the one chosen.
The entries differ per row and per mark (a cube can have a persistent mask, individual masks, or both), so unlike the ROIs
menu they are data here, built by `mask_rows.py`. A header line names the mark. **An entry that is not available (`enabled` False) is left out**
(maintainer, 2026-11-19), it used to be greyed with its reason as a tool tip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget

from ..menu_icons import give_icon


@dataclass(frozen=True)
class MenuEntry:
    key: str
    text: str
    enabled: bool = True
    tooltip: str = ""
    separator_before: bool = False


def show_entries_menu(
    parent: QWidget, *, header: str, entries: Sequence[MenuEntry], pos: QPoint | None = None, icons: Mapping[str, str] | None = None,
) -> str | None:
    """Pop the menu at `pos` (default: the cursor): a disabled header, then the `entries` that are enabled. Returns the chosen entry's key, or
    `None` if dismissed. `icons` maps an entry's key to the command whose icon it wears (a key of `panels/menu_icons.py`)."""
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    menu.addAction(header).setEnabled(False)
    actions = {}
    for entry in entries:
        if not entry.enabled:
            continue
        if entry.separator_before:
            menu.addSeparator()
        action = menu.addAction(entry.text)
        if icons is not None and entry.key in icons:
            give_icon(action, icons[entry.key])
        action.setEnabled(entry.enabled)
        if entry.tooltip:
            action.setToolTip(entry.tooltip)
        actions[action] = entry.key
    chosen = menu.exec(pos if pos is not None else QCursor.pos())
    result = next((key for action, key in actions.items() if chosen is action), None)
    menu.deleteLater()  # (a child of `parent`: one would be left behind per right-click)
    return result
