"""Click a mask pixel, the whole blob it belongs to leaves the mask (the Mask tab's Eraser, 2026-10-29).

Widget-free like `mask_highlight_editor.py` and `mask_candidate_editor.py`, whose merge it shares: the Image panel passes the click it
gets while the Eraser is armed (`CanvasInteraction`) to `erase_at`.

**What a "grain" is.** The masked pixels that touch each other (8-connected: a corner is enough) and contain the pixel clicked; see
`raster_tools.connected_grain`. It is found in the mask *as the image shows it* (`resolve_external_mask`, i.e. after crop / rotation /
flip), because that is what the user clicks; then it is mapped back to raw pixels the way an area selection is
(`area_selection_to_raw`: a 3x3 closing fills the one-pixel holes a rotation leaves, exact with no rotation) and taken out of the mask
in effect at the frame on screen with the same call Subtract uses (`MaskModule.apply_candidate`). So it follows the **State** group:
a Persistent erase is authored at this frame and carries on to the later cubes, an Individual one changes this frame only.

**What it does not do.** A click on a pixel that is not masked does nothing (and says so). An **area selection** limits it like every
other mask tool: a click outside the selection does nothing and only the part of the grain inside the selection is taken away. It is not
on the undo stack, like the other tools' Add / Subtract (`MaskModule`'s docstring).

Cost of a click (measured 2026-10-29 on a 1275 x 799 mask with 300 blobs): labelling 6-7 ms, mapping back to raw pixels 23 ms (37 ms
with a 1.42 deg rotation), the mask as drawn 0.5 ms from the cache: 30-45 ms in all, so no worker thread.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ..image_tools.mask import raster_tools
from ..image_tools.preprocess import area_selection_to_raw, resolve_external_mask
from .mask_edit_common import resolve_mask_edit_base

if TYPE_CHECKING:
    # Deferred, as in mask_highlight_editor.py: a top-level import would close an import cycle.
    from .image.panel import ImagePanel


class MaskGrainEraser:
    def __init__(
        self,
        mask: MaskModule,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        status: Callable[[str], None],
    ) -> None:
        self._mask = mask
        self._geometry = geometry
        self._chromatic = chromatic
        self._dataset = dataset
        self._image_panel = image_panel
        self._mask_scope = mask_scope
        self._status = status
        self._last_frame: tuple[int, float] | None = None
        self._last_shape: tuple[int, int] | None = None  # of the image on screen (the space a click is in)
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._last_frame = (cube_index, wavelength_nm)
        self._last_shape = (int(image.shape[0]), int(image.shape[1]))

    def _on_image_cleared(self) -> None:
        self._last_frame = None
        self._last_shape = None

    def erase_at(self, x: float, y: float) -> int:
        """Remove the grain under view point (`x`, `y`) (image pixel coordinates; pixel (row, col) spans [col, col+1) x [row, row+1)).
        Returns the number of mask pixels removed (0 when nothing was)."""
        frame, shape = self._last_frame, self._last_shape
        raw_shape = self._dataset.raw_plane_shape()
        if frame is None or shape is None or raw_shape is None:
            return 0
        col, row = int(np.floor(x)), int(np.floor(y))
        if not (0 <= row < shape[0] and 0 <= col < shape[1]):
            return 0  # off the image: not a click on the mask
        if not self._image_panel.area_selection().contains(x, y, shape):
            self._status("Eraser: the click is outside the area selection, nothing was erased.")
            return 0
        base_mask = resolve_mask_edit_base(self._mask, self._chromatic, frame, raw_shape)
        settings = self._geometry.settings()
        shown = resolve_external_mask(base_mask, settings, None)  # the mask as drawn on the image
        if shown is None or shown.shape != shape:
            self._status("Eraser: the image changed, click again.")
            return 0
        grain = raster_tools.connected_grain(shown, row, col)
        if grain is None:
            self._status("Eraser: there is no mask pixel under the click, nothing was erased.")
            return 0
        candidate = area_selection_to_raw(grain, raw_shape, settings)
        region = self._image_panel.selection_raw_mask(raw_shape)
        if region is not None:
            candidate = candidate & region
        removed = int(np.count_nonzero(candidate & base_mask))
        if removed == 0:
            self._status("Eraser: there is no mask pixel under the click, nothing was erased.")
            return 0
        self._mask.apply_candidate(
            base_mask, candidate, target_frame=frame, scope=self._mask_scope.scope().value, subtract=True
        )
        self._status(f"Eraser: removed a grain of {removed:,} px from the mask.")
        return removed
