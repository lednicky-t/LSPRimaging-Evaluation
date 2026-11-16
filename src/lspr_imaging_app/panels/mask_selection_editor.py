"""The Mask tab's "Selection" group: put the area selection into the mask, or take it out (2026-11-14); replace the mask by it, or load
the mask as the selection (2026-11-15).

**Replace** (the mask in effect at the frame := the selection, at the Persistent / Individual scope) and **Selection from mask** (the
mask in effect at the frame becomes the selection, combined as the mode button / Shift / Alt say) are what lets the selection tools
work *on a mask*: load it, Modify selection (Grow / Shrink / Open / Close), Replace. Both directions go through the displayed image
(`area_selection_to_raw` one way, `resolve_external_mask` the other): exact without a rotation, about one pixel at the edge under one.
Replace with nothing selected does nothing (a replace by nothing would wipe the mask). **Under a lock Replace replaces only what is inside
the lock** (the mask outside it is kept as it is): a lock is the canvas, so a tool working inside it must not change what is outside.

The selection (rectangle, lasso, or the magic wand's pixel map, made with the selection toolbar beside the image) is in
displayed coordinates. It is mapped back to raw pixels the way every mask tool does it (`ImagePanel.selection_regions.selection_raw_mask`
-> `area_selection_to_raw`: exact without rotation, about one pixel at the edge under a rotation) and merged into the
mask in effect at the frame on screen with `MaskModule.apply_candidate`, at the Persistent / Individual scope of the
State group. One click = one undo step ("Add selection to mask" / "Subtract selection from mask").

An inverted selection is used as it edits (everything outside the shape). Widget-free, like `mask_candidate_editor.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ..image_tools.preprocess import resolve_external_mask
from ..selection.area_selection_module import how_for_keys
from .mask_edit_common import resolve_mask_edit_base

if TYPE_CHECKING:
    # Deferred, as in mask_candidate_editor.py: a top-level import would close an import cycle.
    from .image.panel import ImagePanel


class MaskSelectionEditor:
    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        status: Callable[[str], None],
        geometry: GeometryModule | None = None,
    ) -> None:
        self._geometry = geometry  # needed by `from_mask` only (the mask goes raw -> displayed)
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._image_panel = image_panel
        self._mask_scope = mask_scope
        self._status = status
        self._last_frame: tuple[int, float] | None = None
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    def _on_image_rendered(self, _image: object, cube_index: int, wavelength_nm: float) -> None:
        self._last_frame = (cube_index, wavelength_nm)

    def _on_image_cleared(self) -> None:
        self._last_frame = None

    def replace(self) -> int:
        """Make the mask in effect at the frame exactly the selection (one undo step). Returns the selected raw pixels used
        (0 = nothing done; the status bar says why)."""
        frame = self._last_frame
        raw_shape = self._dataset.raw_plane_shape()
        if frame is None or raw_shape is None:
            self._status("Replace mask: no image on screen.")
            return 0
        region = self._image_panel.selection_regions.selected_raw_mask(raw_shape)
        if region is None or not region.any():
            self._status("Replace mask: nothing is selected, so the mask is left alone (a replace by nothing would wipe it).")
            return 0
        count = int(np.count_nonzero(region))
        lock = self._image_panel.selection_regions.lock_raw_mask(raw_shape)
        if lock is None:
            base = np.zeros(raw_shape, dtype=bool)
        else:  # keep the mask outside the lock; inside it the selection takes over
            base = resolve_mask_edit_base(self._mask, self._chromatic, frame, raw_shape) & ~lock
        self._mask.apply_candidate(
            base, region, target_frame=frame, scope=self._mask_scope.scope().value,
            subtract=False, label="Replace mask with selection",
        )
        where = " inside the lock (the mask outside it is kept)" if lock is not None else ""
        self._status(f"Replaced the mask at this frame{where} by the {count:,} selected px.")
        return count

    def from_mask(self, how: str | None = None) -> int:
        """Make the mask in effect at the frame the selection (or combine it into the selection: `how`, default the mode button's,
        overridden by Shift / Alt / Shift+Alt held now). Returns the displayed pixels of the mask used (0 = nothing done)."""
        frame = self._last_frame
        raw_shape = self._dataset.raw_plane_shape()
        shape = self._image_panel.selection_regions.displayed_shape()
        if frame is None or raw_shape is None or shape is None or self._geometry is None:
            self._status("Selection from mask: no image on screen.")
            return 0
        base = resolve_mask_edit_base(self._mask, self._chromatic, frame, raw_shape)
        if not base.any():
            self._status("Selection from mask: there is no mask at this frame.")
            return 0
        region = resolve_external_mask(base, self._geometry.settings(), None)
        if region is None or region.shape != tuple(shape):
            self._status("Selection from mask: the image size changed, try again.")
            return 0
        selection = self._image_panel.area_selection()
        if how is None:
            modifiers = QGuiApplication.keyboardModifiers()
            how = how_for_keys(
                bool(modifiers & Qt.KeyboardModifier.ShiftModifier), bool(modifiers & Qt.KeyboardModifier.AltModifier),
                selection.combine_mode(),
            )
        fence = selection.lock_mask(region.shape)
        count = int(np.count_nonzero(region if fence is None else region & fence))
        if count == 0:
            self._status(f"Selection from mask: no mask pixel{' inside the lock' if fence is not None else ''}; the selection is unchanged.")
            return 0
        selection.combine_region(region, how, "Selection from mask")
        verb = {"add": "added", "subtract": "took away", "intersect": "kept the overlap of"}.get(how, "selected")
        self._status(f"Selection from mask: {verb} {count:,} mask px.")
        return count

    def apply(self, *, subtract: bool) -> int:
        """Merge the selection into the mask (``subtract=False``) or take it out. Returns the number of selected raw
        pixels used (0 when nothing was done; the status bar says why)."""
        frame = self._last_frame
        raw_shape = self._dataset.raw_plane_shape()
        if frame is None or raw_shape is None:
            self._status("Selection to mask: no image on screen.")
            return 0
        # The selection itself, not the region editing may reach: with only a lock nothing is selected, so nothing is added
        # (maintainer, 2026-11-15: "a locked selection is not a selection any more").
        region = self._image_panel.selection_regions.selected_raw_mask(raw_shape)
        if region is None:
            locked = " (a lock alone selects nothing: select inside it)" if self._image_panel.area_selection().has_lock() else ""
            self._status(f"Selection to mask: nothing is selected{locked}. Use the selection toolbar beside the image.")
            return 0
        count = int(np.count_nonzero(region))
        if count == 0:
            self._status("Selection to mask: the selection covers no pixel of the image.")
            return 0
        base_mask = resolve_mask_edit_base(self._mask, self._chromatic, frame, raw_shape)
        label = "Subtract selection from mask" if subtract else "Add selection to mask"
        self._mask.apply_candidate(
            base_mask, region, target_frame=frame, scope=self._mask_scope.scope().value, subtract=subtract, label=label
        )
        self._status(f"{'Took' if subtract else 'Added'} {count:,} selected px {'out of' if subtract else 'to'} the mask.")
        return count
