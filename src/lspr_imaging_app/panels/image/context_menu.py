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

**Callers must not open a menu with nothing enabled in it** - a menu with
every action grayed out looks exactly like a broken/frozen one, which is
what the maintainer actually hit, 2026-09-29 (not a Qt bug, as first
suspected and wrongly "fixed" by deferring the popup via `QTimer.
singleShot`, since undone). Rotate's and Crop's menus are one- and two-
item *single-purpose* menus, not an Edit menu with other, always-enabled
items sitting next to a conditionally-grayed Undo - so a "gray it out
when there's nothing to do" item can't be the *only* item, or a whole
right-click can open a menu with nothing clickable in it. Both menus'
"Cancel" action is unconditionally enabled for exactly this reason (see
`panel.py`'s `_show_rotate_context_menu`/`_show_crop_context_menu`) - it
always means "exit the tool", which is always a valid thing to do,
dropping whatever was pending along the way; only "Apply crop" (Crop's
second action) is ever conditionally disabled, and never on its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget


def show_tool_context_menu(parent: QWidget, actions: Sequence[tuple[str, bool]]) -> str | None:
    """Pop a menu at the current cursor position with one action per
    *(label, enabled)* pair. Returns the chosen label, or `None` if the
    menu was dismissed without a choice (Esc or a click elsewhere) - never
    call this with every action disabled, see the module docstring."""
    menu = QMenu(parent)
    action_by_label = {label: menu.addAction(label) for label, _enabled in actions}
    for label, enabled in actions:
        action_by_label[label].setEnabled(enabled)
    chosen = menu.exec(QCursor.pos())
    for label, action in action_by_label.items():
        if chosen is action:
            return label
    return None
