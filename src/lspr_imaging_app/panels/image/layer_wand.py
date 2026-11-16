"""The magic wand on the active layer (2026-11-16, maintainer request; the layers: `layers.py`).

* **Image** active: the image wand as before (`wand_task.py`: contrast / brightness / edges, off the GUI thread).
* **Ignore mask** active: the mask blob under the click, exactly as drawn (the mask tint's own resolved mask, `TintOverlays.displayed_mask`),
  8-connected like the old Eraser's grain (`raster_tools.connected_grain`; here a flood fill that only visits the blob: ~1 ms instead of
  labelling the whole image).
* **ROI** active: the sample disk under the click, else the reference ring under it (`RoiQueries.region_at`, the scan's own rasteriser).

The result is combined like every selection (the mode button, Shift / Alt) and cut to the lock. A hidden active layer is never read: the
status line says so. The wand's numbers (threshold, Grow, method) belong to the image wand only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import cv2
import numpy as np

from ...applog import log_event
from ...selection import AreaSelectionModule
from .layers import IMAGE, LAYER_NAMES, MASK, ImageLayers

logger = logging.getLogger(__name__)

_VERBS = {"add": "added", "subtract": "took away", "intersect": "kept the overlap of"}


def mask_blob(mask: np.ndarray, row: int, col: int) -> np.ndarray | None:
    """The 8-connected blob of `mask` containing (`row`, `col`), or ``None`` when that pixel is not masked or is outside. Same result as
    `raster_tools.connected_grain(mask, row, col)` (pinned by a test), by a flood fill that touches only the blob."""
    mask = np.asarray(mask, dtype=bool)
    if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]) or not mask[row, col]:
        return None
    image = mask.astype(np.uint8)  # a new writable copy: floodFill wants one, and the shared mask array may be read-only
    filled = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=np.uint8)
    cv2.floodFill(image, filled, (int(col), int(row)), 0, 0, 0, 8 | cv2.FLOODFILL_MASK_ONLY | (1 << 8))
    return filled[1:-1, 1:-1].astype(bool)


class LayerWand:
    """Routes a wand click to the active layer (`click` is what `CanvasInteraction` calls)."""

    def __init__(
        self,
        layers: ImageLayers,
        image_wand_click: Callable[[float, float, str], object],
        selection: AreaSelectionModule,
        *,
        image_shape: Callable[[], tuple[int, ...] | None],
        displayed_mask: Callable[[], np.ndarray | None],
        roi_region: Callable[[float, float, tuple[int, int]], tuple[np.ndarray, str] | None],
        status: Callable[[str], None],
    ) -> None:
        self._layers = layers
        self._image_wand_click = image_wand_click
        self._selection = selection
        self._image_shape = image_shape
        self._displayed_mask = displayed_mask
        self._roi_region = roi_region
        self._status = status

    def click(self, x: float, y: float, how: str) -> None:
        layer = self._layers.active()
        if not self._layers.is_visible(layer):
            self._status(f"Wand: the active layer ({LAYER_NAMES[layer]}) is hidden. Show it in Layers, or pick another layer.")
            return
        if layer == IMAGE:
            self._image_wand_click(x, y, how)
            return
        shape = self._image_shape()
        if shape is None:
            self._status("Wand: no image on screen.")
            return
        shape = (int(shape[0]), int(shape[1]))
        col, row = int(np.floor(x)), int(np.floor(y))
        if not (0 <= row < shape[0] and 0 <= col < shape[1]):
            return  # off the image: not a click on anything
        fence = self._selection.lock_mask(shape)
        if fence is not None and not fence[row, col]:
            self._status("Wand: that point is outside the lock (Unlock in the selection toolbar to select there).")
            return
        if layer == MASK:
            mask = self._displayed_mask()
            if mask is None or tuple(mask.shape) != shape:
                self._status("Wand (Ignore mask): there is no mask at this frame.")
                return
            region = mask_blob(mask, row, col)
            what = "mask blob"
            if region is None:
                self._status("Wand (Ignore mask): no mask pixel under the click.")
                return
        else:
            found = self._roi_region(x, y, shape)
            if found is None:
                self._status("Wand (ROI): no shown ROI disk or reference ring under the click.")
                return
            region, what = found
        area = int(np.count_nonzero(region if fence is None else region & fence))
        if area == 0:
            self._status(f"Wand ({LAYER_NAMES[layer]}): the {what} lies outside the lock.")
            return
        self._selection.combine_region(region, how, f"Wand: {what}")
        log_event(logger, "wand.layer_click", logging.DEBUG, layer=layer, how=how, area_px=area)
        self._status(f"Wand ({LAYER_NAMES[layer]}): {_VERBS.get(how, 'selected')} the {what}, {area:,} px.")
