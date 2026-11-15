"""The Mask tab's "Selection" group: put the area selection into the mask, or take it out (2026-11-14).

The selection (rectangle, lasso, or the magic wand's pixel map, made with the selection toolbar beside the image) is in
displayed coordinates. It is mapped back to raw pixels the way every mask tool does it (`ImagePanel.selection_raw_mask`
-> `area_selection_to_raw`: exact without rotation, about one pixel at the edge under a rotation) and merged into the
mask in effect at the frame on screen with `MaskModule.apply_candidate`, at the Persistent / Individual scope of the
State group. One click = one undo step ("Add selection to mask" / "Subtract selection from mask").

An inverted selection is used as it edits (everything outside the shape). Widget-free, like `mask_grain_eraser.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, MaskModule, MaskScopeModule
from .mask_edit_common import resolve_mask_edit_base

if TYPE_CHECKING:
    # Deferred, as in mask_grain_eraser.py: a top-level import would close an import cycle.
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
    ) -> None:
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

    def apply(self, *, subtract: bool) -> int:
        """Merge the selection into the mask (``subtract=False``) or take it out. Returns the number of selected raw
        pixels used (0 when nothing was done; the status bar says why)."""
        frame = self._last_frame
        raw_shape = self._dataset.raw_plane_shape()
        if frame is None or raw_shape is None:
            self._status("Selection to mask: no image on screen.")
            return 0
        region = self._image_panel.selection_raw_mask(raw_shape)
        if region is None:
            self._status("Selection to mask: nothing is selected (use the selection toolbar beside the image).")
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
