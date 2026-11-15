"""The toolbar's "Modify selection" menu (2026-11-15, maintainer: the Mask tab's Morphology becomes a modifier of the *selection*).

Screen purpose: grow, shrink or clean the current selection without redrawing it.
Main user actions: click the button, pick Grow / Shrink / Open / Close / Fill holes / Invert; the radius field in the same menu is
the number of pixels.
Layout: a pop-up menu beside the button (the radius field is its last row).
Important states: enabled only while something is selected (a lock alone selects nothing).
Validation: the radius is 1..100 px. Error messages (status bar): nothing selected; the operation changed nothing.

The operations are `raster_tools.apply_morphology_to_mask` (the very function the old mask tool used: 4-connected, one pixel per
step, so the numbers did not change when the tool moved) and `fill_mask_holes`. They run on the selection **as it edits now**
(`AreaSelectionModule.selected_mask`: the lock cut already applied, an inversion resolved) and the result replaces the selection,
cut to the lock again (a Grow never leaves the lock; a Shrink moves every border of the selection, also where it meets the lock's edge).
Each is one undo step. 10-35 ms on a 1.2 MP plane at any radius (scipy walks only the boundary), so it runs on the GUI thread.
Selection space is the displayed image: exact, unlike the old mask operation, which ran in raw space (the two differ by about one pixel
at the edge under a rotation).

The radius is `MaskSettings.morphology_radius_px` (saved with the session, the same field the old tool used).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSpinBox, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...image_tools import MaskModule
from ...image_tools.mask import raster_tools
from ...selection import AreaSelectionModule
from ...selection.area_selection_module import REPLACE
from ..mask_tool_settings import push_tool_settings
from ..ribbon_icon_size import ICON_SIZE
from .selection_menu_icons import FILL_HOLES_ICON, INVERT_ICON

# (operation, menu text, icon, what it does). The first four icons are the old Morphology tool's: the maintainer knows them from the stable app;
# Fill holes and Invert (2026-11-16) have a new one (`selection_menu_icons.py`).
OPERATIONS: tuple[tuple[str, str, str | None, str], ...] = (
    ("dilate", "Grow", "arrows-maximize", "Grow the selection by the radius."),
    ("erode", "Shrink", "arrows-minimize", "Shrink the selection by the radius."),
    ("open", "Open", "book", "Shrink then grow: removes small isolated specks."),
    ("close", "Close", "book-2", "Grow then shrink: fills small gaps and notches."),
    ("fill_holes", "Fill holes", FILL_HOLES_ICON, "Fill every hole inside the selection (a ring becomes a disk)."),
)
_LABEL = {operation: text for operation, text, _icon, _tip in OPERATIONS}


class ModifySelectionMenu(QMenu):
    def __init__(
        self,
        mask: MaskModule,
        area_selection: AreaSelectionModule,
        image_shape: Callable[[], tuple[int, ...] | None],
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._selection = area_selection
        self._image_shape = image_shape
        self._status = status
        self._syncing = False
        self.actions_by_operation = {}
        for operation, text, _icon, tip in OPERATIONS:
            action = self.addAction(text)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, op=operation: self.apply(op))
            self.actions_by_operation[operation] = action
        self.invert_action = self.addAction("Invert selection")
        self.invert_action.setToolTip("Select what is not selected (inside the lock, if there is one).")
        self.invert_action.triggered.connect(lambda _checked=False: self._selection.invert())
        self.addSeparator()

        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(QLabel("Radius", row))
        self.radius_spin = QSpinBox(row)
        self.radius_spin.setRange(1, 100)
        self.radius_spin.setSuffix(" px")
        self.radius_spin.setKeyboardTracking(False)
        self.radius_spin.setToolTip("How many pixels Grow, Shrink, Open and Close move the border.")
        layout.addWidget(self.radius_spin)
        radius_action = QWidgetAction(self)
        radius_action.setDefaultWidget(row)
        self.addAction(radius_action)

        self._sync(mask.settings().morphology_radius_px)
        self.radius_spin.valueChanged.connect(self._on_radius_changed)
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)
        area_selection.selection_changed.connect(self._refresh_enabled)
        self._refresh_enabled()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        color = get_active_theme().text_primary
        for operation, _text, icon, _tip in OPERATIONS:
            if icon is not None:
                self.actions_by_operation[operation].setIcon(load_tabler_icon(icon, color=color, size=ICON_SIZE * 2, stroke_width=2.1))
        self.invert_action.setIcon(load_tabler_icon(INVERT_ICON, color=color, size=ICON_SIZE * 2, stroke_width=2.1))

    def _refresh_enabled(self) -> None:
        on = self._selection.has_selection()
        for action in (*self.actions_by_operation.values(), self.invert_action):
            action.setEnabled(on)

    def _sync(self, radius: int) -> None:
        self._syncing = True
        try:
            self.radius_spin.setValue(int(radius))
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: object) -> None:
        self._sync(self._mask.settings().morphology_radius_px)

    def _on_radius_changed(self, value: int) -> None:
        if not self._syncing:
            push_tool_settings(self._mask, morphology_radius_px=int(value))

    def apply(self, operation: str) -> bool:
        """Modify the selection with `operation` (one of `OPERATIONS`); returns whether the selection changed."""
        shape = self._image_shape()
        selected = None if shape is None else self._selection.selected_mask(shape)
        if selected is None or not selected.any():
            self._status("Modify selection: nothing is selected.")
            return False
        radius = int(self.radius_spin.value())
        if operation == "fill_holes":
            result = raster_tools.fill_mask_holes(selected)
        else:
            result = raster_tools.apply_morphology_to_mask(selected, operation, radius)
        before = int(np.count_nonzero(selected))
        label = f"{_LABEL[operation]} selection" + ("" if operation == "fill_holes" else f" by {radius} px")
        if not result.any():
            self._status(f"{label}: nothing would be left; the selection is unchanged.")
            return False
        changed = self._selection.combine_region(result, REPLACE, label)  # cut to the lock inside
        if not changed:
            self._status(f"{label}: no change.")
            return False
        self._status(f"{label}: {before:,} to {int(np.count_nonzero(self._selection.selected_mask(shape))):,} px.")
        return True
