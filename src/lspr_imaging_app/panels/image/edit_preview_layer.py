"""The Image panel's "edit preview" tint (2026-10-29): the pixels a Mask-tab tool would select or change, drawn over the image.

Display only. `MaskEditPreview` decides *which* pixels (raw-space booleans, as authored at the frame on screen); this turns them into
a coloured layer the same way the mask overlay does (`resolve_external_mask`: crop / rotate / flip, then no chromatic warp, since
they were made for the frame on screen). Drawn above the mask and histogram tints, below the ROI curves.

Cut to the **area selection** when there is one, because an edit never reaches outside it. That cut is made on the screen side
(cheap); the edit itself cuts the raw-space pixels, which can differ from this by a pixel along the selection's edge under a rotation
(the selection is mapped to raw pixels by nearest-pixel scatter).

Hidden while a preview tool (Rotate / Crop) shows the uncropped canvas, the same "other coordinate space" reason the mask overlay
hides then. Colour and opacity are fixed for now (`edit_preview_toggle.preview_color`, 0.55)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg

from ...image_tools import GeometryModule
from ...image_tools.preprocess import resolve_external_mask
from ...selection.area_selection_module import AreaSelectionModule
from ..mask_edit_preview import MaskEditPreview
from .edit_preview_toggle import preview_color
from .overlay_tint import OverlayTint

_ALPHA = 0.55


class EditPreviewLayer:
    def __init__(
        self,
        plot: pg.PlotItem,
        geometry: GeometryModule,
        area_selection: AreaSelectionModule,
        hidden: Callable[[], bool],
    ) -> None:
        self._preview: MaskEditPreview | None = None
        self._geometry = geometry
        self._area_selection = area_selection
        self._hidden = hidden
        # Attached now, so the layer sits under the ROI curves that are added after it; the preview that feeds it exists only
        # once the Mask tab is built (`set_preview`).
        self._tint = OverlayTint(preview_color(), _ALPHA)
        self._tint.attach(plot)
        self._painted: tuple[object, int, int] | None = None  # (resolved mask, selection version, colour) now on the item
        self._selection_version = 0
        area_selection.selection_changed.connect(self._on_selection_changed)

    def set_preview(self, preview: MaskEditPreview) -> None:
        self._preview = preview
        preview.changed.connect(self.refresh)

    @property
    def item(self) -> pg.ImageItem:
        return self._tint.item

    def _on_selection_changed(self) -> None:
        self._selection_version += 1
        self.refresh()

    def refresh(self) -> None:
        """Redraw from what the preview holds now. Called when it changes and from the panel's redraw (a geometry change moves
        where the raw pixels land); cheap when nothing changed (the resolved mask is cached, so the same object comes back)."""
        shown = None if self._preview is None or self._hidden() else self._preview.current()
        if shown is None:
            self._tint.hide()
            self._painted = None
            return
        mask = resolve_external_mask(shown.pixels, self._geometry.settings(), None)
        if mask is None or not np.any(mask):
            self._tint.hide()
            self._painted = None
            return
        self._tint.color = preview_color()
        state = (mask, self._selection_version, self._tint.color.rgba())
        painted = self._painted
        if painted is not None and painted[0] is mask and painted[1:] == state[1:]:
            self._tint.item.show()
            return
        region = self._area_selection.mask(mask.shape)
        if region is not None:
            mask = mask & region
            if not np.any(mask):
                self._tint.hide()
                self._painted = None
                return
        self._tint.paint(mask)
        self._painted = state
