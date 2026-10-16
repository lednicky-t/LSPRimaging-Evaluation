"""The right-click menu on a mark of a Mask row (2026-10-16).

Pure menu construction, like `roi_layout_menu.py`: it is handed the entries to offer and returns the key of the one chosen.
The entries differ per row and per mark (a cube can have a persistent mask, individual masks, or both), so unlike the ROIs
menu they are data here, built by `mask_rows.py`. A header line names the mark; an entry greyed out says why in its tooltip.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget


@dataclass(frozen=True)
class MenuEntry:
    key: str
    text: str
    enabled: bool = True
    tooltip: str = ""
    separator_before: bool = False


def show_entries_menu(parent: QWidget, *, header: str, entries: Sequence[MenuEntry], pos: QPoint | None = None) -> str | None:
    """Pop the menu at `pos` (default: the cursor): a disabled header, then `entries`. Returns the chosen entry's key, or
    `None` if dismissed."""
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    menu.addAction(header).setEnabled(False)
    actions = {}
    for entry in entries:
        if entry.separator_before:
            menu.addSeparator()
        action = menu.addAction(entry.text)
        action.setEnabled(entry.enabled)
        if entry.tooltip:
            action.setToolTip(entry.tooltip)
        actions[action] = entry.key
    chosen = menu.exec(pos if pos is not None else QCursor.pos())
    for action, key in actions.items():
        if chosen is action:
            return key
    return None
