"""The Sensorgram ribbon's "Edit" group (2026-11-10): add, remove, merge, split and restore the steps of the experimental plan.

Screen purpose: change *which steps exist* from the ribbon. Most commands work on the steps picked on the plan bar; one does not need any step. Most were only in a step's right-click
menu ("Add step", "Remove step", "Unify selected steps", "Restore removed steps"); the menu keeps them. Display only: the group holds no choice and changes no step. It reports what
the user asked for, and `StepOverlays` hands that to `PlanBarActions`, which edits `ExperimentPlanModule` (one undo step each).

Layout (left to right, the first group of the Experimental plan tab, before "Evaluation"; the plan bar's "Steps" and "Stats" menus are on the View tab)::

    [add at cursor] [add in front] [add behind]   [remove]  [merge]  [split]  [restore removed]

- **add at cursor** (``add_requested("cursor")``): a new step that starts at the cursor (the cube shown now) and needs no step selected. In free time it runs to the next step or
  the end of the data; inside a step, that step is cut at the cursor and the new step takes the rest of it (steps never overlap). Needs the cursor on the plot.
- **add in front / add behind** (``add_requested("before" | "after")``): a new step next to the one selected step. Needs exactly one selected step, and traces on the plot (a new
  boundary snaps to the plotted cubes).
- **remove step**: the selected steps (``remove_requested``). A measured step is only hidden (restore brings it back, so does Edit > Undo); a step the user added is deleted.
- **merge steps**: the selected steps become one (``merge_requested``). Needs two or more selected steps with no other step between them.
- **split step** (``split_requested``): the step the cursor is inside is cut in two there, no step has to be selected. The first part stays as it is, the second part is a new step with
  the same note and colour. Needs the cursor inside a step (not on its first cube, not between steps).
- **restore removed steps**: every measured step that was removed comes back (``restore_requested``). Needs no selection, only that something was removed.

Each icon is two boxes standing for steps (add at cursor: a cursor line and a new step starting at it): the step the command is about is drawn in an accent colour (add: green, remove:
red, restore: blue) with its symbol, the other box in the ribbon's ordinary ink (merge has no single step it is about and stays in ink; split is two ink halves and a gold cut line).
The adds differ by where the coloured box is.

``set_state`` says what is selected and what is possible; a button that cannot act is greyed out and its tooltip says what is missing, so the next click is clear. The group is
greyed out when the dataset has no experiment steps, except that restore stays on while steps have been removed: after removing every step nothing is left to select, and this is
the way back.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme

from ..icon_button_style import style_general_icon_button
from .plan_icons import (
    add_step_after_icon, add_step_at_cursor_icon, add_step_before_icon, merge_steps_icon, remove_step_icon, restore_steps_icon, split_step_icon,
)

SELECT_HINT = "Click a step on the plan bar to select it (Ctrl-click or Shift-click selects several). The bar must be shown: turn it on in the Steps menu (View tab)."

NO_STEPS_TIP = "This dataset has no experiment steps. Import the measurement notes (and the pump plan) in Workflow > Experimental plan to get them."
NO_STEPS_LEFT_TIP = "There are no steps left to select: every step was removed. Restore removed steps brings them back."

CURSOR_TIP = (
    "Add a step at the cursor, with no step selected: it starts at the cursor (the dashed line, the cube shown now) and runs to the next step, or to the end of the data. Inside a "
    "step, that step is cut at the cursor and the new step takes the rest of it. Then drag its edges into place."
)
CURSOR_OFF_TIP = "Add a step at the cursor: the cursor is not on the plot. Run a scan, select a ROI and pick a cube that is drawn."
BEFORE_TIP = (
    "Add a step in front of the selected step, then drag its edges into place. It fills the free time before it, or takes the first half of the selected step when the steps are "
    "joined. The same as 'Add step > In front of this step' in the right-click menu."
)
AFTER_TIP = (
    "Add a step behind the selected step, then drag its edges into place. It fills the free time after it, or takes the second half of the selected step when the steps are "
    "joined. The same as 'Add step > Behind this step' in the right-click menu."
)
SIDE_NEEDS_ONE_TIP = "Add a step {where} the selected step: select one step first.\n" + SELECT_HINT
SIDE_MANY_TIP = "Add a step {where} the selected step: several steps are selected. Select just one."
SIDE_NO_TRACES_TIP = "Add a step {where} the selected step: needs traces on the plot, a new boundary snaps to the cubes that are drawn. Run a scan and select a ROI first."
REMOVE_TIP = (
    "Remove the selected step(s). A measured step is only hidden: Restore removed steps or Edit > Undo brings it back; a step you added is deleted. "
    "A role it had (baseline / signal) is cleared."
)
REMOVE_NEEDS_TIP = "Remove step: select one or more steps first.\n" + SELECT_HINT
MERGE_TIP = "Merge the selected steps into one step, from the start of the first to the end of the last. The same as 'Unify selected steps' in the right-click menu."
MERGE_NEEDS_TIP = (
    "Merge steps: select two or more steps that lie next to each other, with no other step between them.\n" + SELECT_HINT
)
SPLIT_TIP = (
    "Split the step the cursor is in (the dashed line, the cube shown now) into two, at the cursor. The first part stays as it is; the second part is a new step with the same note "
    "and colour. No step has to be selected. Then drag the edges into place."
)
SPLIT_OFF_TIP = "Split step: the cursor is not on the plot. Run a scan, select a ROI and pick a cube that is drawn."
SPLIT_NOT_INSIDE_TIP = (
    "Split step: the cursor has to be inside a step, not on its first cube and not between steps. Move it with the cube slider or a click on the plot."
)
RESTORE_TIP = (
    "Restore removed steps: every measured step you removed comes back as it was measured, with your edits to it. Steps you added are not affected. "
    "The same as 'Restore removed steps' in a step's right-click menu."
)
RESTORE_NOTHING_TIP = "Restore removed steps: nothing has been removed."


class PlanEditControls(QObject):
    add_requested = pyqtSignal(str)  # "cursor" (starts at the cursor) | "before" | "after" (in front of / behind the selected step)
    remove_requested = pyqtSignal()
    merge_requested = pyqtSignal()
    split_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._available = False
        self._selected = 0
        self._can_add = False
        self._can_add_at_cursor = False
        self._can_merge = False
        self._can_split = False
        self._can_restore = False

        self._add_cursor = QToolButton(parent)
        self._add_cursor.clicked.connect(lambda _checked=False: self.add_requested.emit("cursor"))
        self._add_before = QToolButton(parent)
        self._add_before.clicked.connect(lambda _checked=False: self.add_requested.emit("before"))
        self._add_after = QToolButton(parent)
        self._add_after.clicked.connect(lambda _checked=False: self.add_requested.emit("after"))
        self._remove = QToolButton(parent)
        self._remove.clicked.connect(lambda _checked=False: self.remove_requested.emit())
        self._merge = QToolButton(parent)
        self._merge.clicked.connect(lambda _checked=False: self.merge_requested.emit())
        self._split = QToolButton(parent)
        self._split.clicked.connect(lambda _checked=False: self.split_requested.emit())
        self._restore = QToolButton(parent)
        self._restore.clicked.connect(lambda _checked=False: self.restore_requested.emit())

        self.refresh_theme()
        self.set_state(False, 0, False, False)

    # -- the group ----------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._add_cursor, self._add_before, self._add_after, self._remove, self._merge, self._split, self._restore)

    def add_cursor_button(self) -> QToolButton:
        return self._add_cursor

    def add_before_button(self) -> QToolButton:
        return self._add_before

    def add_after_button(self) -> QToolButton:
        return self._add_after

    def remove_button(self) -> QToolButton:
        return self._remove

    def merge_button(self) -> QToolButton:
        return self._merge

    def split_button(self) -> QToolButton:
        return self._split

    def restore_button(self) -> QToolButton:
        return self._restore

    def set_state(
        self, available: bool, selected: int, can_add: bool, can_merge: bool, can_restore: bool = False, can_add_at_cursor: bool = False, can_split: bool = False
    ) -> None:
        """`available`: the dataset has steps. `selected`: how many steps are selected on the bar. `can_add`: there are traces to snap a new boundary to. `can_merge`: the
        selected steps lie next to each other (two or more). `can_restore`: some measured step was removed (it counts even when `available` is False: every step may have been
        removed). `can_add_at_cursor`: the cursor is on the plot, so a step can start there. `can_split`: the cursor is inside a
        step (so there is one to cut in two). Each button is enabled when its command could act, and its tooltip says what is
        missing otherwise."""
        self._available, self._selected, self._can_add, self._can_merge = bool(available), int(selected), bool(can_add), bool(can_merge)
        self._can_restore, self._can_add_at_cursor, self._can_split = bool(can_restore), bool(can_add_at_cursor), bool(can_split)
        cursor_ok = self._available and self._can_add_at_cursor
        side_ok = self._available and self._selected == 1 and self._can_add
        remove_ok = self._available and self._selected >= 1
        merge_ok = self._available and self._can_merge
        self._add_cursor.setEnabled(cursor_ok)
        self._add_before.setEnabled(side_ok)
        self._add_after.setEnabled(side_ok)
        self._remove.setEnabled(remove_ok)
        self._merge.setEnabled(merge_ok)
        split_ok = self._available and self._can_split
        self._split.setEnabled(split_ok)
        self._restore.setEnabled(self._can_restore)
        self._restore.setToolTip(RESTORE_TIP if self._can_restore else (NO_STEPS_TIP if not self._available else RESTORE_NOTHING_TIP))
        if not self._available:
            for button in (self._add_cursor, self._add_before, self._add_after, self._remove, self._merge, self._split):
                button.setToolTip(NO_STEPS_LEFT_TIP if self._can_restore else NO_STEPS_TIP)
            return
        self._add_cursor.setToolTip(CURSOR_TIP if cursor_ok else CURSOR_OFF_TIP)
        for button, where, tip in ((self._add_before, "in front of", BEFORE_TIP), (self._add_after, "behind", AFTER_TIP)):
            if side_ok:
                button.setToolTip(tip)
            elif self._selected == 0:
                button.setToolTip(SIDE_NEEDS_ONE_TIP.format(where=where))
            elif self._selected > 1:
                button.setToolTip(SIDE_MANY_TIP.format(where=where))
            else:
                button.setToolTip(SIDE_NO_TRACES_TIP.format(where=where))
        self._remove.setToolTip(REMOVE_TIP if remove_ok else REMOVE_NEEDS_TIP)
        self._merge.setToolTip(MERGE_TIP if merge_ok else MERGE_NEEDS_TIP)
        self._split.setToolTip(SPLIT_TIP if split_ok else (SPLIT_NOT_INSIDE_TIP if self._can_add_at_cursor else SPLIT_OFF_TIP))  # "on the plot" is the same fact as for a step at the cursor

    def is_available(self) -> bool:
        return self._available

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        ink = theme.text_primary
        green = theme.accent_green  # the step that a command changes is coloured: new = green, removed = red, restored = blue
        for button, icon in (
            (self._add_cursor, add_step_at_cursor_icon(ink, green)),
            (self._add_before, add_step_before_icon(ink, green)),
            (self._add_after, add_step_after_icon(ink, green)),
            (self._remove, remove_step_icon(ink, theme.accent_red)),
            (self._merge, merge_steps_icon(ink)),
            (self._split, split_step_icon(ink, theme.accent_gold)),
            (self._restore, restore_steps_icon(ink, theme.accent_blue)),
        ):
            style_general_icon_button(button)
            button.setIcon(icon)
