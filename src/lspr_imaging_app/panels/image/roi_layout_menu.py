"""The right-click menu on a mark of the ROI row (2026-10-16).

Pure menu construction, like `roi_context_menu.py`: it is handed what to offer and returns what was chosen;
`panel.py` turns the choice into `RoiToolbox` commands. Every action works on the **whole point** (every ROI's
change at that cube), because a cube is one kind of point for the whole layout; the text says how many ROIs that is
(and how many of them the "only selected ROIs" filter hides). The first cube holds the start layout, which is always
persistent and no change that could be deleted: its actions are greyed with a reason, and "Go to this cube" keeps the menu
from opening with nothing clickable (see `context_menu.py`).
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu, QWidget

from ...roi.model import SCOPE_PERSISTENT

GO_TO = "go_to"
MAKE_INDIVIDUAL = "make_individual"
MAKE_PERSISTENT = "make_persistent"
DELETE = "delete"


def who(roi_count: int, hidden_count: int = 0) -> str:
    """"3 ROIs", or "3 ROIs, 2 not selected" when the filter hides some of them."""
    text = f"{roi_count} ROI{'' if roi_count == 1 else 's'}"
    return f"{text}, {hidden_count} not selected" if hidden_count else text


def show_layout_point_menu(
    parent: QWidget,
    *,
    cube_text: str,
    kind: str,
    is_start: bool,
    roi_count: int,
    hidden_count: int = 0,
    pos: QPoint | None = None,
) -> str | None:
    """Pop the menu at `pos` (default: the cursor). `kind` is the point's kind ("persistent" / "individual"). Returns one of
    `GO_TO`, `MAKE_INDIVIDUAL`, `MAKE_PERSISTENT`, `DELETE`, or `None` if dismissed."""
    persistent = kind == SCOPE_PERSISTENT
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    header = menu.addAction(f"{cube_text} - " + ("start layout" if is_start else ("persistent" if persistent else "individual")))
    header.setEnabled(False)
    menu.addSeparator()
    go_to = menu.addAction("Go to this cube")
    menu.addSeparator()
    names = who(roi_count, hidden_count)
    if persistent:
        convert = menu.addAction(f"Make individual ({names})")
        convert.setToolTip("This cube only. The cubes after it go back to the layout before this change.")
    else:
        convert = menu.addAction(f"Make persistent ({names})")
        convert.setToolTip("The layout of this cube carries on to the cubes after it, until the next persistent change.")
    delete = menu.addAction(f"Delete this change ({names})")
    delete.setToolTip("Forget the change: this cube follows the earlier layout again. Undo brings it back.")
    if is_start:
        reason = "The first cube holds the start layout: it is always persistent and cannot be deleted."
        for action in (convert, delete):
            action.setEnabled(False)
            action.setToolTip(reason)

    chosen = menu.exec(pos if pos is not None else QCursor.pos())
    for action, name in ((go_to, GO_TO), (convert, MAKE_PERSISTENT if not persistent else MAKE_INDIVIDUAL), (delete, DELETE)):
        if chosen is action:
            return name
    return None
