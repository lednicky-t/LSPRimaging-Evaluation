"""The right-click menu on the Image canvas's ROIs tab (2026-10-07).

Pure menu construction: it is handed what to offer and returns what was
chosen; `interaction.py` turns the choice into toolbox commands. "Add ROI here"
is always enabled so the menu never opens with nothing clickable in it (see
`context_menu.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget

ADD_ROI = "add_roi"
GROUP = "group"
ADD_TO_GROUP = "add_to_group"
UNGROUP = "ungroup"
DELETE = "delete"
DESELECT = "deselect"
APPLY_ALL_CUBES = "apply_all_cubes"
REMOVE_CUBE_EDIT = "remove_cube_edit"


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
    can_delete: bool,
    can_apply_all_cubes: bool = False,
    can_remove_cube_edit: bool = False,
) -> RoiMenuChoice | None:
    """Pop the menu at the cursor. ``groups`` is ``(group_id, name)`` pairs.
    Returns the choice, or `None` if dismissed."""
    plural = "" if selected_count == 1 else "s"
    menu = QMenu(parent)
    add = menu.addAction("Add ROI here")
    add.setEnabled(can_add)
    menu.addSeparator()
    group = menu.addAction(f"Group {selected_count} selected ROI{plural}..." if selected_count else "Group selected ROIs...")
    group.setEnabled(selected_count > 0)
    add_menu = menu.addMenu("Add to group")
    add_menu.setEnabled(selected_count > 0 and bool(groups))
    group_actions = {add_menu.addAction(name): group_id for group_id, name in groups}
    ungroup = menu.addAction("Ungroup")
    ungroup.setEnabled(any_selected_grouped)
    menu.addSeparator()
    apply_all = menu.addAction("Apply to all cubes")
    apply_all.setToolTip("Make the geometry shown on this cube the geometry of every cube (drops the per-cube edits).")
    apply_all.setEnabled(can_apply_all_cubes)
    remove_edit = menu.addAction("Remove this cube's edit")
    remove_edit.setToolTip("Forget the edit made on this cube: it follows the earlier edit (or the original geometry) again.")
    remove_edit.setEnabled(can_remove_cube_edit)
    menu.addSeparator()
    delete = menu.addAction(f"Delete {selected_count} ROI{plural}" if selected_count else "Delete selected ROIs")
    delete.setEnabled(selected_count > 0 and can_delete)
    deselect = menu.addAction("Deselect")
    deselect.setEnabled(selected_count > 0)

    chosen = menu.exec(QCursor.pos())
    if chosen is None:
        return None
    if chosen in group_actions:
        return RoiMenuChoice(ADD_TO_GROUP, group_actions[chosen])
    for action, name in ((add, ADD_ROI), (group, GROUP), (ungroup, UNGROUP), (apply_all, APPLY_ALL_CUBES), (remove_edit, REMOVE_CUBE_EDIT), (delete, DELETE), (deselect, DESELECT)):
        if chosen is action:
            return RoiMenuChoice(name)
    return None
