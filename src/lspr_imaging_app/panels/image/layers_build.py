"""Builds the Image panel's layers (`layers.py`) from the panel's existing toggles and items (2026-11-16). Construction only, in the pattern
of `canvas_build.py`: it reads the panel's parts by their names, so the panel itself grows by one call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .layers import IMAGE, MASK, ROI, ImageCover, ImageLayers, apply_layer_order

if TYPE_CHECKING:
    from .panel import ImagePanel


def build_layers(panel: ImagePanel) -> tuple[ImageLayers, ImageCover]:
    """The layers object (its mask and ROI eyes are the Mask tab's and the ROIs tab's toggles), the cover that hides the frame, and the
    stacking (the mask tint above the ROI circles). Needs the ribbon tabs and the canvas items to exist."""
    mask_controls = panel._mask_tab.overlay_controls
    roi_tab = panel._roi_tab
    display = panel._roi_display

    def set_roi_visible(shown: bool) -> None:  # the ROI eye switches the sample circles and the reference rings together
        roi_tab.sample_controls.set_visible(shown)
        roi_tab.reference_controls.set_visible(shown)

    layers = ImageLayers(
        mask_visible=lambda: bool(panel._mask_tint.visible),
        set_mask_visible=mask_controls.set_visible,
        roi_visible=lambda: bool(display.sample.visible or display.reference.visible),
        set_roi_visible=set_roi_visible,
        parent=panel,
    )
    # A toggle switched on a ribbon tab: the Layers button and pop-up follow (connected after the tint's own handlers, so they read the new state).
    panel._mask_tab.overlay_visibility_changed.connect(lambda _shown: layers.note_visibility(MASK))
    panel._view_tab.mask_visibility_changed.connect(lambda _shown: layers.note_visibility(MASK))
    display.changed.connect(lambda _kind: layers.note_visibility(ROI))

    cover = ImageCover(panel._plot, panel._image_item, panel._no_data_item)
    layers.visibility_changed.connect(lambda layer, shown: cover.set_image_visible(shown) if layer == IMAGE else None)
    panel.image_rendered.connect(lambda image, _cube, _wavelength: cover.set_shape(image.shape))
    panel.image_cleared.connect(lambda: cover.set_shape(None))
    apply_layer_order(panel._mask_tint.item, panel._edit_preview_layer.item, panel._lock_layer.item)
    return layers, cover
