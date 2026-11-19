"""One shared right-click menu builder for every Image panel canvas tool
(2026-09-29, maintainer's spec: "this context menu can be some general
function/worker/tool, we will use it in more tools but maybe content will
differ a bit, but design will stay").

Rotate's menu is one action ("Cancel rotation"); Crop's is two ("Apply
crop", "Cancel crop"); a future tool's will differ again - but all of them
are "a QMenu at the cursor, one action per (label, enabled) pair, return
whichever label was chosen". Keeping that one construction/positioning
path in one place means a future tool gets the same menu behavior for
free, and a design change (e.g. icons on the actions) is one edit, not one
per tool.

**An entry whose prerequisite is not met is left out, not greyed** (maintainer, 2026-11-19: "options are not available when they do not have
their prerequisites satisfied"). Before that a menu with every action greyed out looked exactly like a broken/frozen one, which is what the
maintainer hit on 2026-09-29 (not a Qt bug, as first suspected). Now the rule is the same in a better shape: **a menu with nothing in it does not
open** (`show_tool_context_menu` returns `None`), and the tools' own "Cancel" / "Exit tool" entries are always offered, so a tool's menu is
never empty anyway - they always mean "exit the tool", which is always a valid thing to do, dropping whatever was pending along the way; "Apply crop"
(Crop's second entry) appears only with something pending (`CropTool.has_pending_changes`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence

from PyQt6.QtGui import QAction, QCursor, QIcon
from PyQt6.QtWidgets import QMenu, QWidget

SubmenuFiller = Callable[[QMenu], Mapping[QAction, str]]
"""Fills a submenu and says what each of its actions returns: ``{action: the string `show_tool_context_menu` returns when it is picked}``."""


def show_tool_context_menu(
    parent: QWidget,
    actions: Sequence[tuple[str, bool]],
    *,
    icons: Mapping[str, QIcon] | None = None,
    separate_before: Collection[str] = (),
    submenus: Mapping[str, SubmenuFiller] | None = None,
) -> str | None:
    """Pop a menu at the current cursor position with one action per
    *(label, available)* pair; a pair that is not available is **left out**
    (see the module docstring). Returns the chosen label, or `None` if the
    menu was dismissed without a choice (Esc or a click elsewhere) or there
    was nothing to offer (no menu is opened then).

    `icons` gives a label its icon; `separate_before` puts a separator line
    above those labels (never above the first entry), so a caller can group
    its entries (2026-11-16: the selection menu's Invert / Deselect pair and
    its last entry, Exit tool). `submenus` turns a label into a submenu
    (2026-11-19: the selection tools' "Add ROI"): the filler adds the submenu's
    entries and returns the string each one stands for."""
    offered = [label for label, available in actions if available]
    if not offered:
        return None
    menu = QMenu(parent)
    action_by_label = {}
    picked_in_submenu: dict[QAction, str] = {}
    for index, label in enumerate(offered):
        if index > 0 and label in separate_before:
            menu.addSeparator()
        if submenus is not None and label in submenus:
            submenu = menu.addMenu(label)
            if icons is not None and label in icons:
                submenu.setIcon(icons[label])
            picked_in_submenu.update(submenus[label](submenu))
            continue
        action_by_label[label] = menu.addAction(label)
        if icons is not None and label in icons:
            action_by_label[label].setIcon(icons[label])
    chosen = menu.exec(QCursor.pos())
    result = picked_in_submenu.get(chosen) if chosen is not None else None
    if result is None:
        result = next((label for label, action in action_by_label.items() if chosen is action), None)
    menu.deleteLater()  # (a child of `parent`: one would be left behind per right-click)
    return result
