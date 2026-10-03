"""Widget-free "add/remove the Histogram panel's highlighted pixels to/from
the mask" logic - extracted 2026-10-02 from `workflow/mask_highlight_actions.
MaskHighlightActions` (maintainer request: give the Image panel's new "Edit"
picker the same action as a second front door, mirroring the precedent
`MaskScopeModule`/`MaskScopeToggle` already set for the Persistent/
Individual scope toggle - "one backend, several front doors", not two
independently-maintained copies of the same coordinate-space math).

`MaskHighlightActions` (the Workflow panel's own widget, which also owns the
Persistent/Individual `MaskScopeToggle`) now holds one of these and delegates
to it; `panels/image/mask_edit_panels.py`'s `HistogramSelectionEditPanel`
(just an Add/Subtract button pair - the Image panel's "Mask" tab already
shows its own copy of the scope toggle in a separate "State" group, so that
panel doesn't need a second one) holds another. Both read the exact same
live state (`MaskModule`/`HighlightRangeModule`/`MaskScopeModule`/
`ImagePanel`), so they can never disagree about what Add/Subtract would do.

No Qt base class needed - this isn't itself a signal source, just a plain
object whose bound methods are valid PyQt slots for whichever widget
connects them to `ImagePanel.image_rendered`/`image_cleared` and
`HighlightRangeModule.range_changed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ..image_tools.preprocess import histogram_highlight_mask_to_raw
from ..selection import HighlightRangeModule
from .mask_edit_common import resolve_mask_edit_base

if TYPE_CHECKING:
    # Deferred - see mask_highlight_actions.py's own identical note for why
    # a plain top-level import here would close a real import cycle.
    from .image.panel import ImagePanel


class HistogramHighlightMaskEditor:
    """Reads the currently-displayed image/frame (from `ImagePanel`) and the
    Histogram panel's highlighted range (`HighlightRangeModule`), and commits
    an add/subtract edit to `MaskModule` at whichever scope `MaskScopeModule`
    currently selects."""

    def __init__(
        self,
        mask: MaskModule,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        highlight_range: HighlightRangeModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
    ) -> None:
        self._mask = mask
        self._geometry = geometry
        self._chromatic = chromatic
        self._dataset = dataset
        self._highlight_range = highlight_range
        self._mask_scope = mask_scope
        self._image_panel = image_panel
        # Same "read from the one panel that already has it" convention as
        # the Histogram panel - see mask_highlight_actions.py's original
        # docstring for why `ImagePanel._current_wavelength()`'s snapped
        # value, not a fresh `SelectionModule` read, is the correct frame.
        self._last_image: np.ndarray | None = None
        self._last_frame: tuple[int, float] | None = None

        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    def is_ready(self) -> bool:
        return self._last_image is not None and self._highlight_range.current_range() is not None

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._last_image = image
        self._last_frame = (cube_index, wavelength_nm)

    def _on_image_cleared(self) -> None:
        self._last_image = None
        self._last_frame = None

    def _current_scope(self) -> str:
        return self._mask_scope.scope().value

    def apply(self, *, subtract: bool) -> None:
        if self._last_image is None or self._last_frame is None:
            return
        highlight = self._highlight_range.current_range()
        if highlight is None:
            return
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            return
        min_value, max_value = highlight
        image = self._last_image
        # Area selection: pixels outside it become NaN, which the mapping
        # below never selects - so the edit only reaches inside the selection.
        region = self._image_panel.area_selection().mask(image.shape)
        if region is not None:
            image = np.where(region, image, np.float32(np.nan))
        candidate = histogram_highlight_mask_to_raw(
            image, min_value, max_value, raw_shape, self._geometry.settings()
        )
        # The exact frame the candidate was built for - `self._last_frame`,
        # not `SelectionModule` re-read fresh (see `_last_frame`'s own
        # comment above for why those can disagree).
        target_frame = self._last_frame
        base_mask = resolve_mask_edit_base(self._mask, self._chromatic, target_frame, raw_shape)
        self._mask.apply_candidate(
            base_mask, candidate, target_frame=target_frame, scope=self._current_scope(), subtract=subtract
        )
