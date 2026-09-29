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
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget


def show_tool_context_menu(parent: QWidget, actions: Sequence[tuple[str, bool]]) -> str | None:
    """Pop a menu at the current cursor position with one action per
    *(label, enabled)* pair. Returns the chosen label, or `None` if the
    menu was dismissed without a choice (Esc, click elsewhere, or every
    action disabled). A disabled action stays visible rather than being
    left out - "there is nothing to cancel" is itself useful information,
    the same reasoning a standard Undo menu item follows."""
    menu = QMenu(parent)
    action_by_label = {label: menu.addAction(label) for label, _enabled in actions}
    for label, enabled in actions:
        action_by_label[label].setEnabled(enabled)
    chosen = menu.exec(QCursor.pos())
    for label, action in action_by_label.items():
        if chosen is action:
            return label
    return None
