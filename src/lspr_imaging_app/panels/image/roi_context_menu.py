"""The right-click menu on the Image canvas's ROIs tab (2026-10-07).

Pure menu construction: it is handed what to offer and returns what was
chosen; `interaction.py` turns the choice into toolbox commands. "Lock ROI selection"
is always there, so the menu never opens with nothing in it (see `context_menu.py`).

Since 2026-11-18 (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``): **Add ROI** is a submenu (Circle, Rectangle, Current selection >
all together as one ROI / separate blobs), **Make this ROI > Sample / Reference** changes a role, and **Link references** / **Unlink** (Ctrl+L / Ctrl+K)
link the selected samples and references.

Since 2026-11-19 every entry wears an icon (`panels/menu_icons.py`: one icon per command in every menu), **Group** and **Ungroup** show their keys
(Ctrl+G / Ctrl+Shift+G), and the "Rectangle size..." entry is gone (maintainer: the ROI table's W / H / angle columns and the rectangle's handles
do that now).

**An entry whose prerequisite is not met is not in the menu** (maintainer, 2026-11-19; it used to be greyed): Unlink needs something linked, Delete / Deselect /
Group / Make this ROI need a selected ROI, Ungroup a grouped one, and so on. The commands about the ROI selection come first, together: Add ROI, Delete, Select
whole group, Deselect, Lock ROI selection; then the role and links, the groups, and last the cube edit.

**Deselect area** (2026-11-19, maintainer): with no tool armed this is the menu the ROIs tab opens, and it had no way to drop the pixel (area) selection; it is offered while there is one (`can_deselect_area`), next to the ROI deselect.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import QMenu, QWidget

from ...shortcuts import bind_action
from ..menu_icons import give_icon
from ..roi_selection_lock import MENU_TEXT as LOCK_MENU_TEXT
from ..roi_selection_lock import TIP as LOCK_TIP

SELECT_GROUP = "select_group"
ADD_ROI = "add_roi"  # Add ROI > Circle (the name is older than the submenu)
ADD_RECTANGLE = "add_rectangle"
ADD_SELECTION_ONE = "add_selection_one"  # Add ROI > Current selection > All together as one ROI
ADD_SELECTION_BLOBS = "add_selection_blobs"  # ... > Separate blobs
MAKE_SAMPLE = "make_sample"
MAKE_REFERENCE = "make_reference"
LINK = "link"
UNLINK = "unlink"
ADD_RIM = "add_rim"
TURN_RIM_OFF = "turn_rim_off"
RIM_TO_REFERENCE = "rim_to_reference"
GROUP = "group"
ADD_TO_GROUP = "add_to_group"
UNGROUP = "ungroup"
DELETE = "delete"
COPY = "copy"  # copy the selected ROIs (2026-11-24; Ctrl+C)
PASTE = "paste"  # paste the copied ROIs at the point clicked (Ctrl+V pastes at the pointer)
DESELECT ="deselect"
DESELECT_AREA = "deselect_area"  # drop the area selection (rectangle / lasso / wand / brush), not the ROI selection
REMOVE_CUBE_EDIT = "remove_cube_edit"
TOGGLE_LOCK = "toggle_lock"  # the "Lock ROI selection" entry (a check mark: on = the ROI selection is locked)


ADD_ROI_SUBMENU = "Add ROI"  # the submenu's text, here and in the selection tools' right-click menu (2026-11-19)


@dataclass(frozen=True)
class RoiMenuChoice:
    action: str
    group_id: str | None = None  # only for ADD_TO_GROUP


def add_current_selection_menu(add_root: QMenu, entry: Callable[[QMenu, str, str, str], QAction]) -> QMenu:
    """**Add ROI > Current selection > All together as one ROI / Separate blobs**, added to ``add_root``: the one definition behind the ROIs tab's menu
    and the selection tools' menu (2026-11-19, maintainer: the tools' menu should offer it too). ``entry(menu, text, icon_key, choice)`` adds one entry and
    remembers what it stands for; this function adds the tool tips."""
    from_selection = give_icon(add_root.addMenu("Current selection"), "from_selection")
    from_selection.setToolTipsVisible(True)
    entry(from_selection, "All together as one ROI", "one_roi", ADD_SELECTION_ONE).setToolTip(
        "One pixel-mask reference ROI holding every selected pixel, even where the blobs do not touch."
    )
    entry(from_selection, "Separate blobs", "separate_blobs", ADD_SELECTION_BLOBS).setToolTip(
        "One pixel-mask reference ROI for every separate blob of the selection."
    )
    return from_selection


def fill_add_roi_from_selection(add_root: QMenu) -> dict[QAction, str]:
    """The filler of the selection tools' "Add ROI" submenu (`context_menu.show_tool_context_menu(submenus=...)`): Current selection > the two entries.
    Returns ``{action: ADD_SELECTION_ONE / ADD_SELECTION_BLOBS}``."""
    picked: dict[QAction, str] = {}

    def entry(target: QMenu, text: str, icon: str, choice: str) -> QAction:
        action = give_icon(target.addAction(text), icon)
        picked[action] = choice
        return action

    add_current_selection_menu(add_root, entry)
    return picked


def add_roi_submenu_filler(*, can_add_shapes: bool, can_add_from_selection: bool) -> Callable[[QMenu], dict[QAction, str]]:
    """The filler of the selection tools' "Add ROI" submenu with the same entries as the ROIs tab's menu (2026-11-23, maintainer: the tools' menu
    should offer Circle and Rectangle too): Circle and Rectangle where a ROI may be placed (``can_add_shapes``), Current selection where there is an
    area selection (``can_add_from_selection``). Returns ``{action: ADD_ROI / ADD_RECTANGLE / ADD_SELECTION_ONE / ADD_SELECTION_BLOBS}``."""

    def fill(add_root: QMenu) -> dict[QAction, str]:
        picked: dict[QAction, str] = {}

        def entry(target: QMenu, text: str, icon: str, choice: str) -> QAction:
            action = give_icon(target.addAction(text), icon)
            picked[action] = choice
            return action

        if can_add_shapes:
            entry(add_root, "Circle", "roi_circle", ADD_ROI)
            entry(add_root, "Rectangle", "roi_rectangle", ADD_RECTANGLE)
        if can_add_from_selection:
            add_current_selection_menu(add_root, entry)
        return picked

    return fill


def show_roi_context_menu(
    parent: QWidget,
    *,
    selected_count: int,
    groups: Sequence[tuple[str, str]],
    any_selected_grouped: bool,
    can_add: bool,
    can_remove_cube_edit: bool = False,
    clicked_group: tuple[str, int] | None = None,
    clicked_group_fully_selected: bool = False,
    locked: bool = False,
    can_add_from_selection: bool = False,
    selected_roles: tuple[int, int] = (0, 0),
    can_link: bool = False,
    can_unlink: bool = False,
    can_add_rim: bool = False,
    can_turn_rim_off: bool = False,
    can_rim_to_reference: bool = False,
    can_deselect_area: bool = False,
    paste_count: int = 0,
) -> RoiMenuChoice | None:
    """Pop the menu at the cursor. ``groups`` is ``(group_id, name)`` pairs.
    ``clicked_group`` is ``(name, member_count)`` when the ROI under the cursor
    is in a group: it adds "Select whole group" (left out once every member is already selected, or while the selection is locked). ``locked``: the ROI
    selection is locked: the entries that would change it (Select whole group, Deselect) are left out and "Lock ROI selection" shows a check mark.
    ``can_add``: the click is somewhere a circle or rectangle may be added; ``can_add_from_selection``: there is an area selection to make ROIs from (the
    Add ROI submenu offers only what can be done, and is left out when neither). ``selected_roles``: how many of the selected ROIs are samples and
    how many references (the Make entry that already holds for all of them is checked). ``can_link`` / ``can_unlink``: the selection holds something to
    link or to take apart (`roi.link_actions`); the entry is left out otherwise. ``can_add_rim`` / ``can_turn_rim_off`` / ``can_rim_to_reference``: the selection holds a ROI that can get a
    rim (an unused reference, a sample whose rim is off), a sample whose rim is on (it can be turned off, linked or not), or a sample whose rim can become a linked reference (left out otherwise). ``any_selected_grouped``: Ungroup is offered. ``can_deselect_area``: there is an
    area selection (the marching ants), so "Deselect area" is offered. ``paste_count``: how many ROIs are on the ROI clipboard (0 = none: no Paste entry;
    the entry is also left out where a ROI may not be placed, ``can_add``). Copy is offered with a selection.
    Returns the choice, or `None` if dismissed."""
    plural = "" if selected_count == 1 else "s"
    have_selection = selected_count > 0
    menu = QMenu(parent)
    by_action: dict[QAction, str] = {}
    group_actions: dict[QAction, str] = {}

    def entry(target: QMenu, text: str, icon: str, choice: str) -> QAction:
        action = give_icon(target.addAction(text), icon)
        by_action[action] = choice
        return action

    # -- the ROI selection and what a click can add: Add ROI, Delete, Select whole group, Deselect, Lock ROI selection -------------
    if can_add or can_add_from_selection:
        add_menu_root = give_icon(menu.addMenu(ADD_ROI_SUBMENU), "add_roi")
        if can_add:
            entry(add_menu_root, "Circle", "roi_circle", ADD_ROI)
            entry(add_menu_root, "Rectangle", "roi_rectangle", ADD_RECTANGLE)
        if can_add_from_selection:
            add_current_selection_menu(add_menu_root, entry)
    if have_selection:
        copy = entry(menu, f"Copy {selected_count} ROI{plural}", "copy", COPY)
        bind_action(copy, "roi.copy")  # (shown for the key's sake: the working action is `RoiCreation.copy_action`)
        copy.setShortcutVisibleInContextMenu(True)
    if paste_count > 0 and can_add:
        paste = entry(menu, f"Paste {paste_count} ROI{'' if paste_count == 1 else 's'} here", "paste", PASTE)
        paste.setToolTip("The copied ROIs, with the middle of their box on the point clicked. The copies become the selection.")
        bind_action(paste, "roi.paste")
        paste.setShortcutVisibleInContextMenu(True)
    if have_selection:
        entry(menu, f"Delete {selected_count} ROI{plural}", "delete", DELETE)
    if clicked_group is not None and not clicked_group_fully_selected and not locked:
        group_name, member_count = clicked_group
        entry(menu, f"Select whole group \"{group_name}\" ({member_count} ROIs)", "select_rois", SELECT_GROUP)
    if have_selection and not locked:
        entry(menu, f"Deselect {selected_count} ROI{plural}", "deselect", DESELECT)
    if can_deselect_area:
        entry(menu, "Deselect area", "deselect", DESELECT_AREA).setToolTip(
            "Drop the area selection (the marching ants of the Rectangle, Lasso, Wand and Draw tools). Ctrl+Z brings it back."
        )
    lock = entry(menu, LOCK_MENU_TEXT, "lock", TOGGLE_LOCK)
    lock.setCheckable(True)
    lock.setChecked(locked)
    lock.setToolTip(LOCK_TIP)
    menu.addSeparator()

    # -- sample / reference, and the links between them ----------------------------------------------------------------------------
    n_samples, n_references = selected_roles
    if n_samples + n_references > 0:
        make_menu = give_icon(menu.addMenu("Make this ROI" if n_samples + n_references == 1 else "Make these ROIs"), "make_role")
        make_sample = entry(make_menu, "Sample", "sample", MAKE_SAMPLE)
        make_sample.setCheckable(True)
        make_sample.setChecked(n_samples > 0 and n_references == 0)
        make_reference = entry(make_menu, "Reference", "reference", MAKE_REFERENCE)
        make_reference.setCheckable(True)
        make_reference.setChecked(n_references > 0 and n_samples == 0)
    if can_add_rim:
        entry(menu, "Add rim", "add_rim", ADD_RIM).setToolTip(
            "Measure against a rim of the shape's own: a reference that no sample uses becomes a sample, a sample without a rim gets the default one "
            "(gap and thickness from its edge; a pixel map gets a rim round each of its blobs)."
        )
    if can_turn_rim_off:
        entry(menu, "Turn rim off", "turn_rim_off", TURN_RIM_OFF).setToolTip(
            "The sample becomes a rim-less ROI (on every cube). Its shape is untouched and the rim's offset and thickness are remembered: Add rim brings the same rim back."
        )
    if can_rim_to_reference:
        entry(menu, "Turn rim into linked reference", "rim_to_reference", RIM_TO_REFERENCE).setToolTip(
            "The rim's pixels, where the sample is now, become a pixel-map reference ROI of their own, linked to the sample. A frozen copy: it no longer follows the sample. "
            "The sample itself becomes a rim-less ROI (its rim's size is remembered: Add rim brings it back)."
        )
    if can_link:
        link = entry(menu, "Link references", "link", LINK)
        bind_action(link, "links.link")
        link.setShortcutVisibleInContextMenu(True)
    if can_unlink:
        unlink = entry(menu, "Unlink", "unlink", UNLINK)
        bind_action(unlink, "links.unlink")
        unlink.setShortcutVisibleInContextMenu(True)
    menu.addSeparator()

    # -- groups ----------------------------------------------------------------------------------------------------------------------
    if have_selection:
        group = entry(menu, f"Group {selected_count} selected ROI{plural}...", "group", GROUP)
        bind_action(group, "groups.group")  # (shown for the key's sake: the working action is `GroupActions.group_action`)
        group.setShortcutVisibleInContextMenu(True)
        if groups:
            add_menu = give_icon(menu.addMenu("Add to group"), "add_to")
            group_actions = {add_menu.addAction(name): group_id for group_id, name in groups}
    if any_selected_grouped:
        ungroup = entry(menu, "Ungroup", "ungroup", UNGROUP)
        bind_action(ungroup, "groups.ungroup")
        ungroup.setShortcutVisibleInContextMenu(True)
    menu.addSeparator()

    # -- the timeline ----------------------------------------------------------------------------------------------------------------
    if can_remove_cube_edit:
        entry(menu, "Remove this cube's edit", "restore_edit", REMOVE_CUBE_EDIT).setToolTip(
            "Forget the edit made on this cube: it follows the earlier edit (or the original geometry) again."
        )
    menu.setToolTipsVisible(True)

    chosen = menu.exec(QCursor.pos())
    choice = _choice_of(chosen, group_actions, by_action)
    menu.deleteLater()  # (the menu is a child of `parent`: without this every right-click would leave one behind, now with its icons)
    return choice


def _choice_of(chosen, group_actions: dict, by_action: dict) -> RoiMenuChoice | None:
    """What the picked entry means: an "Add to group" entry carries its group, the others are named by `by_action`."""
    if chosen is None:
        return None
    if chosen in group_actions:
        return RoiMenuChoice(ADD_TO_GROUP, group_actions[chosen])
    name = by_action.get(chosen)
    return RoiMenuChoice(name) if name is not None else None
