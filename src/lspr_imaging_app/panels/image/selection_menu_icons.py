"""The icons of the selection's menus, in one place (2026-11-16, maintainer: icons for Deselect, Invert and Fill holes).

Two menus offer the same commands: the right-click menu on the image (`context_menu.py` via `interaction.py`) and the toolbar's Modify selection
menu (`selection_modify.py`). Invert looked different in each if each picked its own, so both read the names below. All are the vendored Tabler
icons (`packages/lspr_ui/ICONS.md`), drawn in the theme's text colour at the size the Modify menu's icons use.
"""

from __future__ import annotations

from PyQt6.QtGui import QIcon

from lspr_ui import get_active_theme, load_tabler_icon

from ..ribbon_icon_size import ICON_SIZE

INVERT_ICON = "contrast-2"  # a square split into its two halves: what is selected and what is not swap places
DESELECT_ICON = "square-off"  # the square with a slash through it: nothing is selected
FILL_HOLES_ICON = "bucket-droplet"  # a paint bucket: fill
LOCK_ICON = "lock"  # the same two as the toolbar's Lock / Unlock buttons (`selection_toolbar.py`)
UNLOCK_ICON = "lock-open"


def menu_icon(name: str) -> QIcon:
    """The vendored icon `name` as a menu entry wears it (theme text colour, the Modify menu's size and stroke)."""
    return load_tabler_icon(name, color=get_active_theme().text_primary, size=ICON_SIZE * 2, stroke_width=2.1)
