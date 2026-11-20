"""The icons of the menu entries, in one table (2026-11-19, maintainer: "icons before the texts, reuse icons for the same options").

A command wears **one icon in every menu**: Delete is the bin whether it is in the Image panel's ROI menu, the ROI table's menu or a plan
step's menu; Group / Ungroup / Link / Unlink / Lock are the same icons as their ribbon buttons. So a menu names *what the entry does*
(``give_icon(action, "delete")``) and never an icon file: the choice of icon is made here, once, and a new menu that offers an existing
command picks the icon up by using the same key. All are the vendored Tabler icons (``packages/lspr_ui/ICONS.md``) except a few the app
already draws itself (the sample disc and the reference ring, the plan bar's step icons), reused unchanged.

Rules:

* An icon is added to the table here, not at the call site. A key that is not in the table is a bug found by
  ``tests/unit/test_lspri_menu_icons.py`` (it reads every key written in the source); at run time it logs a warning and the entry
  simply has no icon, because a menu that fails to open is worse than a menu without an icon.
* Context menus are built when they pop up, so they read the theme's colour then. A menu that lives as long as the window (the menu bar,
  the ROI table's header menu) registers its entries with ``give_persistent_icon`` and is recoloured by ``refresh_menu_icons`` when the theme
  switches (``app_rewrite._wire_theme_menu``).
* A greyed-out entry gets Qt's own greyed-out version of its icon; nothing to do here.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from typing import Any

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu

from lspr_ui import get_active_theme, load_tabler_icon

from .ribbon_icon_size import ICON_SIZE

logger = logging.getLogger(__name__)

_RENDER_SIZE = ICON_SIZE * 2  # drawn at twice the size and scaled down, as every ribbon icon is
_STROKE_WIDTH = 2.1


def tabler_menu_icon(name: str) -> QIcon:
    """The vendored Tabler icon ``name`` as a menu entry wears it: the theme's text colour, the ribbon's size and stroke."""
    return load_tabler_icon(name, color=get_active_theme().text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


# -- icons the app draws itself (imported late: this module is imported by the panels that own them) ---------------------------------


def _sample_disc() -> QIcon:
    from .image.roi_icons import spot_icon

    ink = get_active_theme().text_primary
    return spot_icon(True, ink, ink)  # the ROI tab's sample disc


def _reference_ring() -> QIcon:
    from .image.roi_icons import ring_icon

    ink = get_active_theme().text_primary
    return ring_icon(True, ink, ink)  # the ROI tab's reference ring


def _rim_off() -> QIcon:
    from .image.roi_icons import ring_icon

    ink = get_active_theme().text_primary
    return ring_icon(False, ink, ink)  # the ROI tab's ring, crossed out


def _sample_with_rim() -> QIcon:
    from .image.roi_icons import display_icon

    ink = get_active_theme().text_primary
    return display_icon(ink, ink)  # a filled disc inside a wider-gapped ring: a sample with its rim


def _plan_step(name: str, accent: str) -> Callable[[], QIcon]:
    """One of the plan bar's step icons (`sensorgram/plan_icons.py`), coloured as the ribbon's Edit group colours it
    (new = green, removed = red, restored = blue, split = gold; ``"none"`` for the one with no accent)."""

    def paint() -> QIcon:
        from .sensorgram import plan_icons

        theme = get_active_theme()
        draw = getattr(plan_icons, name)
        if accent == "none":
            return draw(theme.text_primary)
        return draw(theme.text_primary, getattr(theme, f"accent_{accent}"))

    return paint


# What each command looks like: a vendored Tabler icon's name, or a function that paints one.
ICONS: dict[str, str | Callable[[], QIcon]] = {
    # editing
    "undo": "arrow-back-up",
    "redo": "arrow-forward-up",
    "copy": "copy",
    "paste": "clipboard",
    "delete": "trash",  # Delete ROIs / a group / a change / a mask / a column
    "clear_all": "trash-x",  # "Clear all ..." (the anchor menu's, which already wore it)
    "cancel": "x",  # Cancel a tool, drop a cell selection, stop editing, Exit tool
    "apply": "checkbox",
    "rename": "pencil",  # Rename, Edit text
    "colour": "droplet",
    "colour_clear": "droplet-off",  # Clear colour, Default colour
    "reset": "reload",  # back to the measured / default value
    "restore_edit": "eraser",  # "Remove this cube's edit": forget one edit
    "settings": "settings",
    "mask": "mask",  # make masks
    "persistent": "pin",  # a mask / layout change that carries on to the cubes after it
    "individual": "focus-2",  # ... that applies to this cube only (the scope toggles' icon)
    # going somewhere, and order
    "go_to": "current-location",  # "Go to this cube", "Go to reference"
    "expand": "chevron-down",
    "collapse": "chevron-up",
    "move": "sort-ascending-numbers",  # the Move submenus: the order (and numbers) change
    "move_top": "arrow-bar-to-up",
    "move_up": "arrow-up",
    "move_down": "arrow-down",
    "move_bottom": "arrow-bar-to-down",
    "shift_position": "arrows-move",
    # ROIs and groups (the same icons as the ROIs tab and the ROI table's toolbar)
    "add_roi": "circle-plus",
    "roi_circle": "circle",
    "roi_rectangle": "square",
    "from_selection": "select-all-area",
    "one_roi": "map-pin",  # "All together as one ROI"
    "separate_blobs": "map-pins",
    "sample": _sample_disc,
    "reference": _reference_ring,
    "make_role": "replace",  # "Make this ROI" > Sample / Reference: the ROI changes sides
    "link": "link",
    "unlink": "link-off",
    "add_rim": _sample_with_rim,  # "Add rim": the ROI measures against a rim of its own
    "turn_rim_off": _rim_off,  # "Turn rim off": the ring crossed out, as the ROI tab's ring toggle shows "off"
    "rim_to_reference": "link",  # "Turn rim into linked reference": the rim becomes a reference ROI, linked
    "group": "group-rois",
    "ungroup": "ungroup-rois",
    "add_to": "square-rounded-plus",  # "Add to group" (also "Add selection to the mask")
    "select_rois": "select-all-area",  # "Select whole group", "Select ROIs in selection"
    "deselect": "square-off",
    "invert": "contrast-2",
    "lock": "lock",
    "unlock": "lock-open",
    "groups_only": "list",
    # the ROI table's columns and views
    "view_simple": "list",
    "view_geometry": "ruler-measure",
    "linked_view": "link",
    "hide_column": "eye-off",
    "show_column": "eye",
    "freeze": "snowflake",
    "unfreeze": "snowflake-off",
    "fit_columns": "arrow-autofit-width",
    # the experimental plan's steps
    "step_before": _plan_step("add_step_before_icon", "green"),
    "step_after": _plan_step("add_step_after_icon", "green"),
    "step_add": "plus",
    "step_remove": _plan_step("remove_step_icon", "red"),
    "step_restore": _plan_step("restore_steps_icon", "blue"),
    "step_merge": _plan_step("merge_steps_icon", "none"),
    "step_split": _plan_step("split_step_icon", "gold"),
    "use_as": "label-important",
    "use_bulk": "math-function",
    # the menu bar
    "open_dataset": "folder-open",
    "show_folder": "folder-search",
    "session": "device-floppy",
    "new": "plus",
    "import": "file-import",
    "export": "file-export",
    "dataset_manager": "database",
    "exit": "door-exit",
    "show_all": "eye",
    "hide_all": "eye-off",
    "panel_image": "photo",
    "panel_histogram": "chart-histogram",
    "panel_roi_groups": "table",
    "panel_spectra": "wave-sine",
    "panel_sensorgram": "chart-line",
    "layouts": "layout-grid",
    "save": "device-floppy",
    "save_data": "database-export",  # the numbers behind a plot or table, as CSV
    "theme": "palette",
    "theme_dark": "moon",
    "theme_bright": "sun-filled",
    "shortcuts": "keyboard",
    "log": "terminal-2",
    "folder": "folder",
    "about": "info-circle",
}

# The icon of each panel's View-menu toggle, by the panel's title (`shortcuts.PANEL_TOGGLE_IDS` has the same titles).
PANEL_ICON_KEYS: dict[str, str] = {
    "Image": "panel_image",
    "Histogram": "panel_histogram",
    "ROI / Groups": "panel_roi_groups",
    "Spectra": "panel_spectra",
    "Sensorgram": "panel_sensorgram",
}


def menu_icon(key: str) -> QIcon:
    """The icon of the command ``key``; an empty icon (and a warning in the log) for a key that is not in `ICONS`."""
    picked = ICONS.get(key)
    if picked is None:
        logger.warning("menu_icons: no icon for the command %r (add it to panels/menu_icons.py ICONS)", key)
        return QIcon()
    return tabler_menu_icon(picked) if isinstance(picked, str) else picked()


def _action_of(target: QAction | QMenu) -> QAction:
    return target.menuAction() if isinstance(target, QMenu) else target


def give_icon(target: Any, key: str) -> Any:
    """Put the icon of the command ``key`` on a menu entry (a `QAction`, or a `QMenu` for a submenu); returns ``target`` so a call can be chained."""
    _action_of(target).setIcon(menu_icon(key))
    return target


_persistent: list[tuple[weakref.ref, str]] = []


def give_persistent_icon(target: Any, key: str) -> Any:
    """`give_icon` for an entry that lives as long as the window: it is also remembered, and recoloured by `refresh_menu_icons` when the theme changes."""
    action = _action_of(target)
    action.setIcon(menu_icon(key))
    if len(_persistent) > 400:  # a long test run builds many windows: do not keep the ones that are gone
        _recolour(draw=False)
    _persistent.append((weakref.ref(action), key))
    return target


def refresh_menu_icons() -> None:
    """Redraw the icons of every `give_persistent_icon` entry in the current theme (after a theme switch); entries that are gone are forgotten."""
    _recolour(draw=True)


def _recolour(*, draw: bool) -> None:
    alive: list[tuple[weakref.ref, str]] = []
    for reference, key in _persistent:
        action = reference()
        if action is None:
            continue
        try:
            if draw:
                action.setIcon(menu_icon(key))
            else:
                action.objectName()  # raises when the Qt object is deleted although its Python wrapper is not yet
        except RuntimeError:
            continue
        alive.append((reference, key))
    _persistent[:] = alive
