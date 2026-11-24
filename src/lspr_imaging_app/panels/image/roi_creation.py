"""Making ROIs and linking them, from the Image panel (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

One small object between the Image panel's gestures (the right-click ROI menu, the ROIs tab's Link / Unlink buttons, Ctrl+L / Ctrl+K) and the toolbox
commands, so none of that goes into `panel.py` (which has a size budget) and a refusal always reaches the status bar:

* **Add ROI > Circle / Rectangle** at the point clicked (a 20 px circle, a 20 x 20 px square; their size and angle are edited afterwards).
* **Add ROI > Circle / Rectangle fitted to selection** (2026-11-24, replaces the two plain entries while the area selection is exactly one blob): a circle of the
  blob's area at its centre of mass, or the smallest rectangle that contains it (`roi/shape_fit.py`), a sample ROI whose rim has the shared default gap and the
  thickness that gives it the area of the ROI itself (`rim_area.equal_area_thickness`, as "Rim as large as ROI"; maintainer, 2026-11-24).
* **Add ROI > Current selection > All together / Separate blobs** (maintainer's choice, 2026-11-18): the area selection becomes pixel-mask ROIs, by default
  **references, linked to nothing**. The selection is made on the frame on screen, so it is first carried back into the reference frame the ROIs
  are stored in (the inverse of the frame's chromatic affine; nothing to do for the identity).
* **Make this ROI > Sample / Reference**, **Link** and **Unlink** of the selected ROIs (`roi.link_actions` decides which ROIs a selection means).

* **Ctrl+A** selects all ROIs while the ROIs tab is open.
* **Delete / Ctrl+D** (2026-11-24): Delete removes the selected ROIs; Ctrl+D drops the ROI selection and the area selection.
* **Copy / Paste** (2026-11-24, Ctrl+C / Ctrl+V on the Image panel and the right-click menu): the selected ROIs are copied (`RoiToolbox.copy_rois`) and pasted
  with the middle of their box on the pointer (`paste_rois`). The copy is held here, in this app only: not saved, and not on the system clipboard.

Every command is one undo step (the toolbox's); a refusal says why in the status line and changes nothing. No domain state of its own (only the copy).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QWidget

from ...domain.roi_shapes import Border, Rectangle
from ...image_tools.chromatic.affine import invert_affine_matrix
from ...image_tools.chromatic.warp import warp_boolean_mask_affine
from ...roi import RoiToolbox
from ...roi.clipboard import RoiClip
from ...roi.link_actions import can_link, can_unlink, link_selected, split_by_role, unlink_selected
from ...roi.rim_area import equal_area_thickness
from ...roi.scope import RoiEditTarget
from ...roi.shape_fit import fit_circle, fit_rectangle, has_single_blob
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
        pointer_xy: Callable[[], tuple[float, float] | None] | None = None,
        roi_tab_active: Callable[[], bool] | None = None,
        edit_target: RoiEditTarget | None = None,
        parent: QObject | None = None,
    ) -> None:
        """``image_shape()``: the processed frame on screen, or ``None`` without one. ``current_affine()``: that frame's chromatic affine (2 x 3).
        ``to_reference(x, y)``: a display point in the reference frame. ``action_parent``: the widget the Link / Unlink shortcuts live on.
        ``pointer_xy()``: where the mouse pointer is on the image (display point), or ``None`` when it is not over it (Ctrl+V pastes there)."""
        super().__init__(parent)
        self._edit_target = edit_target  # where a rim-size edit lands (scope + cube); None = the base geometry
        self._pointer_xy = pointer_xy
        self._roi_tab_active = roi_tab_active
        self._clip: RoiClip | None = None  # the copied ROIs: this app's own clipboard, not saved, not the system one
        # Ctrl+C / Ctrl+V on the Image panel (the ROI table keeps its own: they copy cells there). A text field inside the panel keeps its own copy / paste.
        self.copy_action = QAction("Copy ROIs", action_parent)
        self.paste_action = QAction("Paste ROIs", action_parent)
        for action, binding_id, command in ((self.copy_action, "roi.copy", self.copy_selected), (self.paste_action, "roi.paste", self.paste_at_pointer)):
            bind_action(action, binding_id, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda _checked=False, run=command: run())
            action_parent.addAction(action)
        self._toolbox = toolbox
        self._selection = selection
        self._area_selection = area_selection
        self._image_shape = image_shape
        self._current_affine = current_affine
        self._to_reference = to_reference
        self._dialog_parent = action_parent
        # Ctrl+L / Ctrl+K work from anywhere in the application (the ROI table is another dock); they always run, and say why when they cannot.
        self.link_action = QAction("Link references", action_parent)
        self.unlink_action = QAction("Unlink", action_parent)
        for action, binding_id, command in ((self.link_action, "links.link", self.link), (self.unlink_action, "links.unlink", self.unlink)):
            bind_action(action, binding_id, context=Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(lambda _checked=False, run=command: run())
            action_parent.addAction(action)
        # Delete / Ctrl+D on the Image panel (2026-11-24): delete the selected ROIs; drop the ROI selection and the area selection. Text fields keep their own keys.
        self.delete_action = QAction("Delete selected ROIs", action_parent)
        self.deselect_action = QAction("Deselect", action_parent)
        self.select_all_action = QAction("Select all ROIs", action_parent)
        for action, binding_id, command in (
            (self.delete_action, "roi.delete", self.delete_selected), (self.deselect_action, "selection.deselect", self.deselect_all),
            (self.select_all_action, "roi.select_all", self.select_all),
        ):
            bind_action(action, binding_id, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
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

    # -- copy and paste ----------------------------------------------------------------------------

    def can_paste(self) -> bool:
        """Something was copied."""
        return self._clip is not None

    def pasted_count(self) -> int:
        return 0 if self._clip is None else len(self._clip)

    def copy_selected(self) -> None:
        """Copy the selected ROIs (as they are at the cube on screen) into the app's ROI clipboard. Says why when none is selected."""

        def command() -> str:
            ids = self._selection.selected_roi_ids()
            if not ids:
                raise ValueError("select one or more ROIs first.")
            clip = self._toolbox.copy_rois(ids, cube=int(self._selection.current_cube()))
            self._clip = clip
            return f"Copied {len(clip)} ROI{'' if len(clip) == 1 else 's'}: Ctrl+V pastes them at the pointer."

        self._run("Copy ROIs", command)

    def paste_at_pointer(self) -> None:
        """Ctrl+V: paste where the mouse pointer is on the image; says so when it is not over the image."""
        spot = None if self._pointer_xy is None else self._pointer_xy()
        if spot is None:
            self.status.emit("Paste ROIs: move the pointer over the image, then paste.")
            return
        self.paste_at(*spot)

    def paste_at(self, x: float, y: float) -> None:
        """Paste the copied ROIs with the middle of their box on the display point ``(x, y)``; the new ROIs become the selection (so the next paste
        can follow at once). One undo step."""

        def command() -> str:
            if self._clip is None:
                raise ValueError("copy one or more ROIs first (select them, then Ctrl+C).")
            ids = self._toolbox.paste_rois(self._clip, *self._to_reference(x, y))
            self._select(ids)
            return f"Pasted {len(ids)} ROI{'' if len(ids) == 1 else 's'}."

        self._run("Paste ROIs", command)

    # -- delete and deselect (Delete / Space) --------------------------------------------------------

    def delete_selected(self) -> None:
        """Delete the selected ROIs (one undo step); says so when none is selected."""

        def command() -> str:
            ids = tuple(sorted(self._selection.selected_roi_ids()))
            if not ids:
                raise ValueError("select one or more ROIs first.")
            self._toolbox.delete_rois(ids)
            return f"Deleted {len(ids)} ROI{'' if len(ids) == 1 else 's'} (Ctrl+Z brings them back)."

        self._run("Delete ROIs", command)

    def deselect_all(self) -> None:
        """Drop the ROI selection (a locked one refuses and says so) and the area selection (one undo step)."""
        had_area = bool(self._area_selection.has_selection())
        had_rois = bool(self._selection.selected_roi_ids())
        if not (had_area or had_rois):
            self.status.emit("Deselect: nothing is selected.")
            return
        if had_area:
            self._area_selection.clear()
        if had_rois:
            self._selection.set_roi_selection(set())  # a locked ROI selection refuses and says so itself

    def select_all(self) -> None:
        """Ctrl+A: select every ROI (samples and references), while the ROIs ribbon tab is open; says so otherwise. A locked ROI selection refuses and says so."""
        if self._roi_tab_active is not None and not self._roi_tab_active():
            self.status.emit("Select all ROIs: open the ROIs tab first.")
            return
        ids = {roi.area_roi_id for roi in self._toolbox.rois()}
        if not ids:
            self.status.emit("Select all ROIs: there are no ROIs.")
            return
        self._selection.set_roi_selection(ids)

    # -- adding ----------------------------------------------------------------------------------

    def add_circle(self, x: float, y: float) -> None:
        """A circle at the display point ``(x, y)``, selected."""
        self._run("Add ROI", lambda: self._select({self._toolbox.add_roi(*self._to_reference(x, y))}) or None)

    def add_rectangle(self, x: float, y: float) -> None:
        """A `DEFAULT_RECTANGLE_PX` square, not turned, centred on the display point ``(x, y)``, selected."""
        shape = Rectangle(DEFAULT_RECTANGLE_PX, DEFAULT_RECTANGLE_PX, 0.0)
        self._run("Add rectangle ROI", lambda: self._select({self._toolbox.add_shaped_roi(*self._to_reference(x, y), shape)}) or None)

    def has_pixel_selection(self) -> bool:
        """There is an area selection (rectangle, lasso, wand, brush) to make ROIs from."""
        return bool(self._area_selection.has_selection())

    def add_from_selection(self, *, together: bool) -> None:
        """The area selection as pixel-mask reference ROIs, linked to nothing: one ROI for all of it (``together``) or one per separate blob."""
        self._run("Add ROI from selection", lambda: self._add_from_selection(together))

    def has_single_blob(self) -> bool:
        """The area selection is exactly one separate blob (8-connected): the right-click menu then offers a circle / rectangle **fitted** to it.
        Asked when a menu opens (one labelling of the selection, a few ms on a full frame), never while the pointer moves."""
        shape = self._image_shape()
        plane = None if shape is None else self._area_selection.selected_mask(shape)
        return plane is not None and has_single_blob(plane)

    def add_fitted(self, kind: str) -> None:
        """Add a circle (``"circle"``) or the smallest containing rectangle (``"rectangle"``) fitted to the one blob of the area selection, as a **sample ROI whose rim
        has the default gap and the area of the ROI**; it becomes the ROI selection. One undo step. Says why in the status line when the selection is not one blob."""
        label = "Add circle ROI fitted to the selection" if kind == "circle" else "Add rectangle ROI fitted to the selection"
        self._run(label, lambda: self._add_fitted(kind))

    def _add_fitted(self, kind: str) -> str:
        if kind not in ("circle", "rectangle"):
            raise ValueError(f"unknown shape {kind!r}")
        fitted = (fit_circle if kind == "circle" else fit_rectangle)(self._selection_in_reference_frame())
        # the rim keeps the shared default gap and gets the thickness at which it has the area of the ROI itself (the same rule as "Rim as large as ROI")
        gap = self._toolbox.detection_settings().default_border().gap_px
        rim = Border(gap, equal_area_thickness(fitted.shape, gap))
        roi_id = self._toolbox.add_shaped_roi(fitted.center_x, fitted.center_y, fitted.shape, border=rim)
        self._select({roi_id})
        if kind == "circle":
            size = f"{fitted.shape.diameter_px:.1f} px across"
        else:
            size = f"{fitted.shape.width_px:.1f} x {fitted.shape.height_px:.1f} px, turned {fitted.shape.angle_deg:.1f} deg"
        return f"Added {kind} ROI {roi_id} fitted to the selected blob ({fitted.blob_pixels} px; {size}), with a rim of its own area ({rim.gap_px:g} px gap, {rim.thickness_px:.1f} px thick)."

    def _selection_in_reference_frame(self) -> np.ndarray:
        """The area selection as a bool plane in the reference frame ROIs are stored in; ``ValueError`` (for the status line) when there is none."""
        shape = self._image_shape()
        plane = None if shape is None else self._area_selection.selected_mask(shape)
        if plane is None or not plane.any():
            raise ValueError("there is no area selection to make a ROI from (draw one with the Rectangle, Lasso, Wand or brush first).")
        affine = np.asarray(self._current_affine(), dtype=np.float64)
        if not np.array_equal(affine[:2, :3], np.eye(2, 3)):  # the selection is of the frame on screen; ROIs are stored in the reference frame
            plane = warp_boolean_mask_affine(plane, invert_affine_matrix(affine), output_shape=plane.shape)
        return plane

    def _add_from_selection(self, together: bool) -> str:
        plane = self._selection_in_reference_frame()
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

    def can_turn_rim_off(self, roi_ids) -> bool:
        """Some selected ROI is a sample whose rim is on (linked or not)."""
        return any(self._toolbox.can_turn_rim_off(i) for i in self._known(roi_ids))

    def turn_rim_off(self, roi_ids) -> None:
        """Switch the rim of the selected samples off (`RoiToolbox.turn_rim_off`): they become rim-less ROIs, their rim's numbers kept for Add rim."""
        ids = [i for i in self._known(roi_ids) if self._toolbox.can_turn_rim_off(i)]

        def command() -> str:
            self._toolbox.turn_rim_off(ids)
            return f"Turned the rim off on {len(ids)} ROI{'' if len(ids) == 1 else 's'} (Add rim brings it back with the same size)."

        self._run("Turn rim off", command)

    def can_match_rim_area(self, roi_ids) -> bool:
        """Some selected ROI is a sample whose rim is on (the rim's thickness can be set to the sample's area)."""
        return any(self._toolbox.can_match_rim_area(i) for i in self._known(roi_ids))

    def match_rim_area(self, roi_ids) -> None:
        """Set the thickness of the selected samples' rims so that each rim has the area of its sample ROI (`RoiToolbox.match_rim_area`; the offset stays; scope and cube as for any rim size)."""
        ids = [i for i in self._known(roi_ids) if self._toolbox.can_match_rim_area(i)]

        def command() -> str:
            if not ids:
                raise ValueError("no selected ROI has a rim (a reference or a rim-less ROI has none to set).")
            self._toolbox.match_rim_area(ids, **({} if self._edit_target is None else self._edit_target.kwargs()))
            return f"Set the rim of {len(ids)} ROI{'' if len(ids) == 1 else 's'} to the area of the ROI itself (its offset is unchanged)."

        self._run("Rim as large as the ROI", command)

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
            # the rim is taken where the sample is on the cube on screen: a move made on a later cube is not in the sample's base position
            new_ids = self._toolbox.rims_to_references(ids, (int(shape[0]), int(shape[1])), cube=int(self._selection.current_cube()))
            left_out = len(known) - len(ids)
            text = (
                f"Turned {len(ids)} rim{'' if len(ids) == 1 else 's'} into linked reference ROI{'' if len(ids) == 1 else 's'} {', '.join(str(i) for i in new_ids)}; "
                f"the sample{'' if len(ids) == 1 else 's'} ha{'s' if len(ids) == 1 else 've'} no rim of {'its' if len(ids) == 1 else 'their'} own now."
            )
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
