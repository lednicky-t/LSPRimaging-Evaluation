"""Making ROIs and linking them, from the Image panel (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

One small object between the Image panel's gestures (the right-click ROI menu, the ROIs tab's Link / Unlink buttons, Ctrl+L / Ctrl+K) and the toolbox
commands, so none of that goes into `panel.py` (which has a size budget) and a refusal always reaches the status bar:

* **Add ROI > Circle / Rectangle** at the point clicked (a 20 px circle, a 20 x 20 px square; their size and angle are edited afterwards).
* **Add ROI > Current selection > All together / Separate blobs** (maintainer's choice, 2026-11-18): the area selection becomes pixel-mask ROIs, by default
  **references, linked to nothing**. The selection is made on the frame on screen, so it is first carried back into the reference frame the ROIs
  are stored in (the inverse of the frame's chromatic affine; nothing to do for the identity).
* **Make this ROI > Sample / Reference**, **Link** and **Unlink** of the selected ROIs (`roi.link_actions` decides which ROIs a selection means).

Every command is one undo step (the toolbox's); a refusal says why in the status line and changes nothing. No state of its own.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QWidget

from ...domain.roi_shapes import Rectangle
from ...image_tools.chromatic.affine import invert_affine_matrix
from ...image_tools.chromatic.warp import warp_boolean_mask_affine
from ...roi import RoiToolbox
from ...roi.link_actions import can_link, can_unlink, link_selected, split_by_role, unlink_selected
from ...selection import AreaSelectionModule, SelectionModule
from ...shortcuts import bind_action
from .rectangle_size_dialog import ask_rectangle_size

DEFAULT_RECTANGLE_PX = 20.0
"""Width and height of a rectangle added by a click: the same 20 px as a new circle's diameter."""


class RoiCreation(QObject):
    status = pyqtSignal(str)  # one line for the status bar: what was done, or why nothing was

    def __init__(
        self,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        area_selection: AreaSelectionModule,
        *,
        image_shape: Callable[[], tuple[int, ...] | None],
        current_affine: Callable[[], np.ndarray],
        to_reference: Callable[[float, float], tuple[float, float]],
        action_parent: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """``image_shape()``: the processed frame on screen, or ``None`` without one. ``current_affine()``: that frame's chromatic affine (2 x 3).
        ``to_reference(x, y)``: a display point in the reference frame. ``action_parent``: the widget the Link / Unlink shortcuts live on."""
        super().__init__(parent)
        self._toolbox = toolbox
        self._selection = selection
        self._area_selection = area_selection
        self._image_shape = image_shape
        self._current_affine = current_affine
        self._to_reference = to_reference
        self._dialog_parent = action_parent
        self._picked_shape = "circle"  # the Shape picker's choice: what the Add ROI tool places
        # Ctrl+L / Ctrl+K work from anywhere in the application (the ROI table is another dock); they always run, and say why when they cannot.
        self.link_action = QAction("Link references", action_parent)
        self.unlink_action = QAction("Unlink", action_parent)
        for action, binding_id, command in ((self.link_action, "links.link", self.link), (self.unlink_action, "links.unlink", self.unlink)):
            bind_action(action, binding_id, context=Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(lambda _checked=False, run=command: run())
            action_parent.addAction(action)

    # -- one command, one status line -------------------------------------------------------------

    def _run(self, label: str, command: Callable[[], str | None]) -> None:
        try:
            message = command()
        except (ValueError, KeyError) as exc:
            self.status.emit(f"{label}: {exc}")
            return
        if message:
            self.status.emit(message)

    def _select(self, ids) -> None:
        self._selection.set_roi_selection({int(i) for i in ids})  # a locked selection refuses and says so itself

    # -- adding ----------------------------------------------------------------------------------

    def add_circle(self, x: float, y: float) -> None:
        """A circle at the display point ``(x, y)``, selected."""
        self._run("Add ROI", lambda: self._select({self._toolbox.add_roi(*self._to_reference(x, y))}) or None)

    def add_rectangle(self, x: float, y: float) -> None:
        """A `DEFAULT_RECTANGLE_PX` square, not turned, centred on the display point ``(x, y)``, selected."""
        shape = Rectangle(DEFAULT_RECTANGLE_PX, DEFAULT_RECTANGLE_PX, 0.0)
        self._run("Add rectangle ROI", lambda: self._select({self._toolbox.add_shaped_roi(*self._to_reference(x, y), shape)}) or None)

    def set_picked_shape(self, shape: str) -> None:
        """The Shape picker's choice (``"circle"`` or ``"rectangle"``): what a click of the Add ROI tool places."""
        self._picked_shape = shape

    def add_picked(self, x: float, y: float) -> None:
        """What the Add ROI tool does on a click at the view point ``(x, y)``: the picked shape, centred there. The point goes through ``to_reference`` like the
        right-click menu's (cell shift off, then the frame's affine inverted), so the ROI is drawn under the pointer; until 2026-11-18 the tool stored the raw
        pointer position, which is half a pixel off the pixels the ROI measures (and the affine's shift off, away from the reference wavelength)."""
        if self._picked_shape == "rectangle":
            shape = Rectangle(DEFAULT_RECTANGLE_PX, DEFAULT_RECTANGLE_PX, 0.0)
            self._run("Add rectangle ROI", lambda: self._toolbox.add_shaped_roi(*self._to_reference(x, y), shape) and None)
        else:
            self._run("Add ROI", lambda: self._toolbox.add_roi(*self._to_reference(x, y)) and None)

    def has_pixel_selection(self) -> bool:
        """There is an area selection (rectangle, lasso, wand, brush) to make ROIs from."""
        return bool(self._area_selection.has_selection())

    def add_from_selection(self, *, together: bool) -> None:
        """The area selection as pixel-mask reference ROIs, linked to nothing: one ROI for all of it (``together``) or one per separate blob."""
        self._run("Add ROI from selection", lambda: self._add_from_selection(together))

    def _add_from_selection(self, together: bool) -> str:
        shape = self._image_shape()
        plane = None if shape is None else self._area_selection.selected_mask(shape)
        if plane is None or not plane.any():
            raise ValueError("there is no area selection to make a ROI from (draw one with the Rectangle, Lasso, Wand or brush first).")
        affine = np.asarray(self._current_affine(), dtype=np.float64)
        if not np.array_equal(affine[:2, :3], np.eye(2, 3)):  # the selection is of the frame on screen; ROIs are stored in the reference frame
            plane = warp_boolean_mask_affine(plane, invert_affine_matrix(affine), output_shape=plane.shape)
        ids = self._toolbox.add_mask_rois(plane, together=together)
        self._select(ids)
        count = f"{len(ids)} reference ROI{'' if len(ids) == 1 else 's'}"
        return f"Added {count} from the selection, linked to no sample (right-click > Make these ROIs > Sample to measure with them)."

    # -- roles and links -------------------------------------------------------------------------

    def role_counts(self, roi_ids) -> tuple[int, int]:
        """``(samples, references)`` among ``roi_ids``."""
        samples, references = split_by_role(self._toolbox, roi_ids)
        return len(samples), len(references)

    def set_role(self, roi_ids, role: str) -> None:
        """Make ``roi_ids`` samples or references."""
        ids = tuple(sorted({int(i) for i in roi_ids}))

        def command() -> str:
            self._toolbox.set_roi_role(ids, role)
            return f"Made {len(ids)} ROI{'' if len(ids) == 1 else 's'} {role}{'' if len(ids) == 1 else 's'}."

        self._run("Make ROI " + role, command)

    def rectangles_among(self, roi_ids) -> list[Rectangle]:
        """The shapes of the rectangle ROIs among ``roi_ids``, in id order."""
        wanted = {int(i) for i in roi_ids}
        return [roi.shape for roi in self._toolbox.rois() if roi.area_roi_id in wanted and isinstance(roi.shape, Rectangle)]

    def resize_rectangles(self, roi_ids) -> None:
        """Ask for a width, height and / or angle and apply what was changed to every selected rectangle (one undo step)."""
        shapes = self.rectangles_among(roi_ids)
        changes = ask_rectangle_size(self._dialog_parent, shapes)
        if changes is None:
            return
        ids = [roi.area_roi_id for roi in self._toolbox.rois() if int(roi.area_roi_id) in {int(i) for i in roi_ids} and isinstance(roi.shape, Rectangle)]

        def command() -> str:
            self._toolbox.resize_rectangles(ids, **changes)
            return f"Resized {len(ids)} rectangle ROI{'' if len(ids) == 1 else 's'}."

        self._run("Rectangle size", command)

    # -- rim pairs and linked pairs -----------------------------------------------------------------

    def can_add_rim(self, roi_ids) -> bool:
        """Some selected ROI would get a rim (an unused reference, a sample whose rim is off)."""
        return any(self._toolbox.can_add_rim(i) for i in self._known(roi_ids))

    def can_rim_to_reference(self, roi_ids) -> bool:
        """Some selected ROI is a sample with a rim and no links: its rim can become a linked reference."""
        return any(self._toolbox.rim_refusal(i) is None for i in self._known(roi_ids))

    def _known(self, roi_ids) -> list[int]:
        have = {roi.area_roi_id for roi in self._toolbox.rois()}
        return [int(i) for i in roi_ids if int(i) in have]

    def add_rim(self, roi_ids) -> None:
        """Give the selected ROIs a rim (`RoiToolbox.add_rim`; the ones that cannot take one are left alone)."""
        ids = [i for i in self._known(roi_ids) if self._toolbox.can_add_rim(i)]

        def command() -> str:
            self._toolbox.add_rim(ids)
            return f"Added a rim to {len(ids)} ROI{'' if len(ids) == 1 else 's'}."

        self._run("Add rim", command)

    def rim_to_reference(self, roi_ids) -> None:
        """Turn the rim of every selected sample that has one (and no links) into a linked pixel-map reference; say how many were left out and why."""
        known = self._known(roi_ids)
        ids = [i for i in known if self._toolbox.rim_refusal(i) is None]

        def command() -> str:
            shape = self._image_shape()
            if shape is None:
                raise ValueError("there is no image on screen to take the rim's pixels from.")
            if not ids:
                raise ValueError(self._toolbox.rim_refusal(known[0]) if known else "no ROI is selected.")
            new_ids = self._toolbox.rims_to_references(ids, (int(shape[0]), int(shape[1])))
            left_out = len(known) - len(ids)
            text = f"Turned {len(ids)} rim{'' if len(ids) == 1 else 's'} into linked reference ROI{'' if len(ids) == 1 else 's'} {', '.join(str(i) for i in new_ids)}."
            return text + (f" {left_out} selected ROI{'' if left_out == 1 else 's'} left out (a reference, already linked, or without a rim)." if left_out else "")

        self._run("Turn rim into linked reference", command)

    def can_link(self, roi_ids) -> bool:
        return can_link(self._toolbox, roi_ids)

    def can_unlink(self, roi_ids) -> bool:
        return can_unlink(self._toolbox, roi_ids)

    def link(self) -> None:
        """Link the selected samples to the selected references (Ctrl+L, the Link button, the ROI menu)."""
        self._run("Link", lambda: link_selected(self._toolbox, self._selection.selected_roi_ids()))

    def unlink(self) -> None:
        """Take apart the links the selection means (Ctrl+K, the Unlink button, the ROI menu)."""
        self._run("Unlink", lambda: unlink_selected(self._toolbox, self._selection.selected_roi_ids()))
