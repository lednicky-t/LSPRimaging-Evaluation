"""The Image panel's three layers (2026-11-16, maintainer request): Image (the frame), ROI and Ignore mask, Photoshop-style.

**Order is fixed**, bottom to top: Image, ROI, Ignore mask (`LAYERS` lists them top first, as the Layers pop-up shows them). The mask tint
is drawn above the ROI circles for that reason (`apply_layer_order`).

**Visibility.** The ROI and Ignore mask layers have no switch of their own: their eyes are the existing show / hide toggles (the Mask tab's,
mirrored on the View tab; the ROIs tab's sample circles and reference rings, both switched together by the ROI eye). The Image layer's eye
is new and lives here (`ImageCover` hides the frame and shows a flat grey in its place). Display only: nothing is computed differently.

**Active layer.** One layer is active (the Layers button shows its icon; the wheel over the button changes it). The selection tools that
*read* something work on the active layer only: the magic wand picks the image feature (Image), the mask blob (Ignore mask) or the ROI's disk
or ring (ROI) under the click (`layer_wand.py`); the select-by-value tools read image brightness, so they work only with the Image layer
active. Rectangle, Lasso, Draw and Modify are shapes and work the same on every layer. A tool never reads a hidden active layer: the status
line says why. Hiding a layer never changes which one is active (a control does exactly what it says).

Cosmetic state of the panel (the panel owns it, as it owns the tints' looks); remembered across restarts (`bind_ui_state`).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRectF, pyqtSignal

from ...storage.ui_state_keys import IMAGE_ACTIVE_LAYER, IMAGE_LAYER_IMAGE_VISIBLE
from ..ui_state import UiStateStore

IMAGE = "image"
ROI = "roi"
MASK = "mask"
LAYERS = (MASK, ROI, IMAGE)  # top to bottom, as drawn and as listed
LAYER_NAMES = {MASK: "Ignore mask", ROI: "ROI", IMAGE: "Image (frame)"}

# Stacking (pyqtgraph z values inside the view; the image is 0, the "no data" checker -1, the ROI circles' layer 0 but added after the image).
_COVER_Z = -0.5  # the flat grey shown instead of the frame: under everything, the frame item itself is made transparent
MASK_Z = 0.5  # the mask tint: above the ROI circles (the Ignore mask layer is the top layer)
EDIT_PREVIEW_Z = 0.6  # the tools' preview tint and the lock's dimming stay above the mask, as they were
LOCK_DIM_Z = 0.7
_COVER_GREY = 72  # a flat, neutral dark grey: not the "no data" checker (that means "no value here"), so the two never read alike


class ImageLayers(QObject):
    """Which layer is active and which are shown. ``active_changed(layer)``; ``visibility_changed(layer, shown)`` (also when the mask or ROI
    toggle was switched elsewhere: the owner calls `note_visibility`)."""

    active_changed = pyqtSignal(str)
    visibility_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        *,
        mask_visible: Callable[[], bool],
        set_mask_visible: Callable[[bool], None],
        roi_visible: Callable[[], bool],
        set_roi_visible: Callable[[bool], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._active = IMAGE
        self._image_visible = True
        self._getters: dict[str, Callable[[], bool]] = {MASK: mask_visible, ROI: roi_visible, IMAGE: lambda: self._image_visible}
        self._setters: dict[str, Callable[[bool], None]] = {MASK: set_mask_visible, ROI: set_roi_visible, IMAGE: self._set_image_visible}

    def active(self) -> str:
        return self._active

    def set_active(self, layer: str) -> None:
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}, got {layer!r}")
        if layer != self._active:
            self._active = layer
            self.active_changed.emit(layer)

    def cycle(self, steps: int) -> str:
        """The wheel over the Layers button: the layer above (``steps`` < 0) or below, wrapping round. Returns the new active layer."""
        index = (LAYERS.index(self._active) + int(steps)) % len(LAYERS)
        self.set_active(LAYERS[index])
        return self._active

    def is_visible(self, layer: str) -> bool:
        return bool(self._getters[layer]())

    def set_visible(self, layer: str, shown: bool) -> None:
        """Show or hide a layer (the mask and ROI layers through their own toggles, so every copy of them follows)."""
        self._setters[layer](bool(shown))

    def active_visible(self) -> bool:
        return self.is_visible(self._active)

    def note_visibility(self, layer: str) -> None:
        """A toggle outside this object changed a layer's visibility: tell the listeners (the Layers button and its pop-up)."""
        self.visibility_changed.emit(layer, self.is_visible(layer))

    def _set_image_visible(self, shown: bool) -> None:
        if shown != self._image_visible:
            self._image_visible = shown
            self.visibility_changed.emit(IMAGE, shown)

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the Image layer's eye and the active layer across restarts."""
        saved_layer = store.get(IMAGE_ACTIVE_LAYER.key)
        if saved_layer in LAYERS:
            self.set_active(str(saved_layer))
        saved_visible = store.get(IMAGE_LAYER_IMAGE_VISIBLE.key)
        if isinstance(saved_visible, bool):
            self._set_image_visible(saved_visible)
        self.active_changed.connect(lambda layer: store.set(IMAGE_ACTIVE_LAYER.key, layer))
        self.visibility_changed.connect(
            lambda layer, shown: store.set(IMAGE_LAYER_IMAGE_VISIBLE.key, bool(shown)) if layer == IMAGE else None
        )


class ImageCover:
    """Hides the frame (the Image layer's eye): the frame item and the "no data" checker are made fully transparent (they keep their size,
    so the view's range is untouched, and Qt does not paint an item of opacity 0), and one flat grey item the size of the image is shown
    under everything. The frame keeps being rendered, so the wand and the value tools still have it when the layer is shown again."""

    def __init__(self, plot: pg.PlotItem, image_item: pg.ImageItem, no_data_item: pg.ImageItem) -> None:
        self._image_item = image_item
        self._no_data_item = no_data_item
        self._item = pg.ImageItem(np.full((1, 1, 4), (_COVER_GREY, _COVER_GREY, _COVER_GREY, 255), dtype=np.uint8), axisOrder="row-major")
        self._item.setZValue(_COVER_Z)
        self._item.hide()
        plot.addItem(self._item, ignoreBounds=True)
        self._shape: tuple[int, int] | None = None
        self._visible = True

    @property
    def item(self) -> pg.ImageItem:
        return self._item

    def set_shape(self, shape: tuple[int, ...] | None) -> None:
        """The frame's size changed (or the frame went away: ``None``)."""
        new = None if shape is None else (int(shape[0]), int(shape[1]))
        if new != self._shape:
            self._shape = new
            if new is not None:
                self._item.setRect(QRectF(0.0, 0.0, float(new[1]), float(new[0])))
            self._apply()

    def set_image_visible(self, shown: bool) -> None:
        self._visible = bool(shown)
        self._apply()

    def _apply(self) -> None:
        opacity = 1.0 if self._visible else 0.0
        self._image_item.setOpacity(opacity)
        self._no_data_item.setOpacity(opacity)
        self._item.setVisible(not self._visible and self._shape is not None)


def apply_layer_order(mask_item: pg.GraphicsObject, edit_preview_item: pg.GraphicsObject | None, lock_dim_item: pg.GraphicsObject | None) -> None:
    """Put the mask tint above the ROI circles (the Ignore mask is the top layer), keeping the tool preview and the lock's dimming above it."""
    mask_item.setZValue(MASK_Z)
    if edit_preview_item is not None:
        edit_preview_item.setZValue(EDIT_PREVIEW_Z)
    if lock_dim_item is not None:
        lock_dim_item.setZValue(LOCK_DIM_Z)
