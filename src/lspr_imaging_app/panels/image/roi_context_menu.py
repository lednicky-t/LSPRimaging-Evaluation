"""The right-click menu on the Image canvas's ROIs tab (2026-10-07).

Pure menu construction: it is handed what to offer and returns what was
chosen; `interaction.py` turns the choice into toolbox commands. "Lock selection"
is always enabled so the menu never opens with nothing clickable in it (see
`context_menu.py`).

Since 2026-11-18 (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``): **Add ROI** is a submenu (Circle, Rectangle, Current selection >
all together as one ROI / separate blobs), **Make this ROI > Sample / Reference** changes a role, and **Link references** / **Unlink** (Ctrl+L / Ctrl+K)
link the selected samples and references.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget

from ...shortcuts import bind_action
from ..roi_selection_lock import MENU_TEXT as LOCK_MENU_TEXT
from ..roi_selection_lock import TIP as LOCK_TIP

SELECT_GROUP = "select_group"
ADD_ROI = "add_roi"  # Add ROI > Circle (the name is older than the submenu)
ADD_RECTANGLE = "add_rectangle"
ADD_SELECTION_ONE = "add_selection_one"  # Add ROI > Current selection > All together as one ROI
ADD_SELECTION_BLOBS = "add_selection_blobs"  # ... > Separate blobs
MAKE_SAMPLE = "make_sample"
MAKE_REFERENCE = "make_reference"
RESIZE_RECTANGLE = "resize_rectangle"  # "Rectangle size..." (opens `rectangle_size_dialog.py`)
LINK = "link"
UNLINK = "unlink"
GROUP = "group"
ADD_TO_GROUP = "add_to_group"
UNGROUP = "ungroup"
DELETE = "delete"
DESELECT = "deselect"
REMOVE_CUBE_EDIT = "remove_cube_edit"
TOGGLE_LOCK = "toggle_lock"  # the "Lock selection" entry (a check mark: on = the ROI selection is locked)


@dataclass(frozen=True)
class RoiMenuChoice:
    action: str
    group_id: str | None = None  # only for ADD_TO_GROUP


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
    selected_rectangles: int = 0,
) -> RoiMenuChoice | None:
    """Pop the menu at the cursor. ``groups`` is ``(group_id, name)`` pairs.
    ``clicked_group`` is ``(name, member_count)`` when the ROI under the cursor
    is in a group: it adds "Select whole group" as the first entry (greyed once
    every member is already selected, or while the selection is locked). ``locked``: the ROI selection is
    locked: the entry that would change it (Select whole group, Deselect) is greyed and "Lock selection" shows a
    check mark. ``can_add_from_selection``: there is an area selection to make ROIs from. ``selected_roles``: how many of the selected ROIs are samples and
    how many references (the Make entry that already holds for all of them is checked). ``can_link`` / ``can_unlink``: the selection holds something to
    link or to take apart (`roi.link_actions`). ``selected_rectangles``: how many selected ROIs are rectangles ("Rectangle size..." needs one).
    Returns the choice, or `None` if dismissed."""
    plural = "" if selected_count == 1 else "s"
    menu = QMenu(parent)
    select_group = None
    if clicked_group is not None:
        group_name, member_count = clicked_group
        select_group = menu.addAction(f"Select whole group \"{group_name}\" ({member_count} ROIs)")
        select_group.setEnabled(not clicked_group_fully_selected and not locked)
        menu.addSeparator()
    add_menu_root = menu.addMenu("Add ROI")
    add_menu_root.setEnabled(can_add or can_add_from_selection)
    add = add_menu_root.addAction("Circle")
    add.setEnabled(can_add)
    add_rectangle = add_menu_root.addAction("Rectangle")
    add_rectangle.setEnabled(can_add)
    from_selection = add_menu_root.addMenu("Current selection")
    from_selection.setEnabled(can_add_from_selection)
    from_selection.setToolTipsVisible(True)
    add_one = from_selection.addAction("All together as one ROI")
    add_one.setToolTip("One pixel-mask reference ROI holding every selected pixel, even where the blobs do not touch.")
    add_blobs = from_selection.addAction("Separate blobs")
    add_blobs.setToolTip("One pixel-mask reference ROI for every separate blob of the selection.")
    menu.addSeparator()
    n_samples, n_references = selected_roles
    make_menu = menu.addMenu("Make this ROI" if n_samples + n_references == 1 else "Make these ROIs")
    make_menu.setEnabled(n_samples + n_references > 0)
    make_sample = make_menu.addAction("Sample")
    make_sample.setCheckable(True)
    make_sample.setChecked(n_samples > 0 and n_references == 0)
    make_reference = make_menu.addAction("Reference")
    make_reference.setCheckable(True)
    make_reference.setChecked(n_references > 0 and n_samples == 0)
    resize_rectangle = menu.addAction("Rectangle size...")
    resize_rectangle.setEnabled(selected_rectangles > 0)
    resize_rectangle.setToolTip("Set the width, height and angle of the selected rectangle ROIs.")
    link = menu.addAction("Link references")
    link.setEnabled(can_link)
    bind_action(link, "links.link")
    link.setShortcutVisibleInContextMenu(True)
    unlink = menu.addAction("Unlink")
    unlink.setEnabled(can_unlink)
    bind_action(unlink, "links.unlink")
    unlink.setShortcutVisibleInContextMenu(True)
    menu.addSeparator()
    group = menu.addAction(f"Group {selected_count} selected ROI{plural}..." if selected_count else "Group selected ROIs...")
    group.setEnabled(selected_count > 0)
    add_menu = menu.addMenu("Add to group")
    add_menu.setEnabled(selected_count > 0 and bool(groups))
    group_actions = {add_menu.addAction(name): group_id for group_id, name in groups}
    ungroup = menu.addAction("Ungroup")
    ungroup.setEnabled(any_selected_grouped)
    menu.addSeparator()
    remove_edit = menu.addAction("Remove this cube's edit")
    remove_edit.setToolTip("Forget the edit made on this cube: it follows the earlier edit (or the original geometry) again.")
    remove_edit.setEnabled(can_remove_cube_edit)
    menu.addSeparator()
    delete = menu.addAction(f"Delete {selected_count} ROI{plural}" if selected_count else "Delete selected ROIs")
    delete.setEnabled(selected_count > 0)
    deselect = menu.addAction("Deselect")
    deselect.setEnabled(selected_count > 0 and not locked)
    menu.addSeparator()
    lock = menu.addAction(LOCK_MENU_TEXT)
    lock.setCheckable(True)
    lock.setChecked(locked)
    lock.setToolTip(LOCK_TIP)
    menu.setToolTipsVisible(True)

    chosen = menu.exec(QCursor.pos())
    if chosen is None:
        return None
    if chosen in group_actions:
        return RoiMenuChoice(ADD_TO_GROUP, group_actions[chosen])
    for action, name in (
        (add_rectangle, ADD_RECTANGLE), (add_one, ADD_SELECTION_ONE), (add_blobs, ADD_SELECTION_BLOBS), (make_sample, MAKE_SAMPLE),
        (make_reference, MAKE_REFERENCE), (resize_rectangle, RESIZE_RECTANGLE), (link, LINK), (unlink, UNLINK),
    ):
        if chosen is action:
            return RoiMenuChoice(name)
    for action, name in ((select_group, SELECT_GROUP), (add, ADD_ROI), (group, GROUP), (ungroup, UNGROUP), (remove_edit, REMOVE_CUBE_EDIT), (delete, DELETE), (deselect, DESELECT), (lock, TOGGLE_LOCK)):
        if chosen is action:
            return RoiMenuChoice(name)
    return None
