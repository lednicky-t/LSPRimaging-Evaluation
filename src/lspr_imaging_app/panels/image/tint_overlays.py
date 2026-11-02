"""The two display-only tints over the Image panel: the mask overlay and the histogram highlight overlay.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 1). The drawing code is the code that was in `ImagePanel`;
what changed is where the state lives: the cached mask (`mask_state`) and the last painted mask are owned here, and the three things
this needs from the panel (the displayed pixels, their finite range, "the background estimate is on show") come in as small functions.

`OverlayTint` (overlay_tint.py) holds one tint's colour, opacity and image item; this class decides *which pixels* get tinted and
answers the controls of the two tints (Mask tab, View tab). Cosmetic only - see mask_overlay_controls.py and
histogram_highlight_overlay_controls.py for why this state lives on the panel side and not on `MaskModule` / `HighlightRangeModule`.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtGui import QColor

from ...image_tools.geometry.model import GeometrySettings
from ...image_tools.preprocess import resolve_external_mask
from ...selection import HighlightRangeModule
from .overlay_style import OverlayStyle
from .overlay_tint import OverlayTint


class TintOverlays:
    def __init__(
        self,
        mask_tint: OverlayTint,
        highlight_tint: OverlayTint,
        highlight_range: HighlightRangeModule,
        *,
        display_image: Callable[[], np.ndarray | None],
        data_range: Callable[[], tuple[float, float] | None],
        show_background: Callable[[], bool],
        style_changed: Callable[[str, OverlayStyle], None],
    ) -> None:
        """``display_image()``: the pixel array now on screen (``None`` = nothing displayed). ``data_range()``: that frame's own
        finite min / max, or ``None`` when it is not known. ``show_background()``: the Background tab's "show the estimate instead of
        the image" switch. ``style_changed(kind, style)``: a tint's look changed (``kind`` is "mask" or "highlight"); the app
        settings remember it."""
        self._mask_tint = mask_tint
        self._highlight_tint = highlight_tint
        self._highlight_range = highlight_range
        self._display_image = display_image
        self._data_range = data_range
        self._show_background = show_background
        self._style_changed = style_changed
        # Mask-overlay display state (cosmetic only). `mask_state` caches the last resolved (authored_mask, geometry, warp_affine,
        # hidden) tuple so a pure visibility/color/alpha change can redraw the tint without re-resolving the mask or touching the
        # async pixel-render pipeline.
        self.mask_state: tuple[np.ndarray | None, GeometrySettings | None, np.ndarray | None, bool] | None = None
        # (resolved mask object, colour, alpha) last painted into the mask tint.
        self._mask_painted: tuple[np.ndarray, int, float] | None = None
        # The highlight tint has nothing to "resolve" - it is the already-displayed pixel array (`display_image()`) thresholded
        # against `HighlightRangeModule.current_range()` - so it caches nothing here.

    def clear(self) -> None:
        """The dataset went away: nothing to tint, nothing cached."""
        self._mask_painted = None
        self._mask_tint.item.hide()
        self.mask_state = None
        self._highlight_tint.item.hide()

    # -- mask overlay (display-only, see mask_overlay_controls.py) ----------

    def update_mask(
        self,
        authored_mask: np.ndarray | None,
        geometry: GeometrySettings | None,
        warp_affine: np.ndarray | None,
        *,
        hidden: bool,
    ) -> None:
        """Redraw the mask-overlay tint from an already-resolved mask -
        synchronous GUI-thread work, same as `_draw_overlays` (a few
        thousand boolean pixels, cheap next to a full image re-render).

        Caches its arguments as `self.mask_state` so a pure
        visibility/color/alpha change (`on_mask_*` below) can
        redraw without re-resolving the mask or touching the async
        pixel-render pipeline at all - none of those three controls change
        what is *computed*, only how the already-resolved mask is drawn."""
        self.mask_state = (authored_mask, geometry, warp_affine, hidden)
        if hidden or not self._mask_tint.visible or authored_mask is None or geometry is None:
            self._mask_tint.item.hide()
            self._mask_painted = None
            return
        mask = resolve_external_mask(authored_mask, geometry, warp_affine)
        if mask is None or not np.any(mask):
            self._mask_tint.item.hide()
            self._mask_painted = None
            return
        # `resolve_external_mask` hands back the same object for the same
        # inputs, so a wavelength change that resolves to the same mask does not
        # rebuild and re-upload the tint image.
        painted = self._mask_painted
        style = (self._mask_tint.color.rgba(), self._mask_tint.alpha)
        if painted is not None and painted[0] is mask and (painted[1], painted[2]) == style:
            self._mask_tint.item.show()
            return
        self._mask_tint.paint(mask)
        self._mask_painted = (mask, style[0], style[1])

    def redraw_mask_from_cache(self) -> None:
        if self.mask_state is not None:
            authored_mask, geometry, warp_affine, hidden = self.mask_state
            self.update_mask(authored_mask, geometry, warp_affine, hidden=hidden)

    def _emit_style(self, kind: str) -> None:
        tint = self._mask_tint if kind == "mask" else self._highlight_tint
        self._style_changed(kind, tint.style())

    def on_mask_visibility_changed(self, visible: bool) -> None:
        self._mask_tint.visible = bool(visible)
        self.redraw_mask_from_cache()
        self._emit_style("mask")

    def on_mask_color_changed(self, color: QColor) -> None:
        self._mask_tint.color = QColor(color)
        self.redraw_mask_from_cache()
        self._emit_style("mask")

    def on_mask_alpha_changed(self, alpha: float) -> None:
        self._mask_tint.alpha = float(alpha)
        self.redraw_mask_from_cache()
        self._emit_style("mask")

    # -- histogram highlight overlay (display-only, see histogram_highlight_overlay_controls.py) --

    def update_highlight(self) -> None:
        """Redraw the histogram highlight-overlay tint from the cached,
        already-displayed pixel array (`display_image()`, set in the panel's
        `_on_rendered`) - synchronous GUI-thread work, same cost reasoning
        as `update_mask`. A pure visibility/color/alpha change
        (`on_highlight_*` below) and a range change
        (`on_highlight_range_changed`) both land here; neither needs a new
        render, since the tint is a threshold over pixels already on
        screen - matches the stable app's own
        `_update_selected_intensity_overlay`."""
        image = self._display_image()
        if not self._highlight_tint.visible or image is None or self._show_background():
            self._highlight_tint.item.hide()
            return
        range_ = self._highlight_range.current_range()
        if range_ is None:
            self._highlight_tint.item.hide()
            return
        lower, upper = range_
        data_range = self._data_range()  # the displayed frame's own finite min / max, computed once in `_on_rendered`
        if data_range is not None and (upper < data_range[0] or lower > data_range[1] or (lower <= data_range[0] and upper >= data_range[1])):
            # Nothing in range, or every finite pixel in range: either way no tint is drawn (below). Decided from
            # the min / max alone: building the full-image mask for it cost ~6-10 ms of every Play frame.
            self._highlight_tint.item.hide()
            return
        selection_mask = (image >= lower) & (image <= upper)
        # Hide rather than tint when nothing is excluded (the whole image
        # falls in range) as well as when nothing is included - a full-image
        # tint carries no information, same reasoning the stable app applies
        # against its own fixed sensor-range bounds (here, against the
        # actually-displayed image's own range, since `HighlightRangeModule`
        # seeds to that, not a fixed 16-bit constant - see that module's
        # docstring).
        # Pixels without a value (NaN) are never selected and do not count as
        # "excluded" either: "everything in range" is judged on finite pixels.
        if not np.any(selection_mask):
            self._highlight_tint.item.hide()
            return
        # "Every finite pixel is in range" was already decided from the frame's min / max above whenever those are
        # known (all finite pixels lie between them), so the full-image `selection_mask[np.isfinite(image)]` test
        # (a ~1 MP copy, 2-3 ms per Play frame) only runs for a frame without a known range.
        if data_range is None and bool(np.all(selection_mask[np.isfinite(image)])):
            self._highlight_tint.item.hide()
            return
        self._highlight_tint.paint(selection_mask)

    def on_highlight_range_changed(self, _range: tuple[float, float] | None) -> None:
        self.update_highlight()

    def on_highlight_visibility_changed(self, visible: bool) -> None:
        self._highlight_tint.visible = bool(visible)
        self.update_highlight()
        self._emit_style("highlight")

    def on_highlight_color_changed(self, color: QColor) -> None:
        self._highlight_tint.color = QColor(color)
        self.update_highlight()
        self._emit_style("highlight")

    def on_highlight_alpha_changed(self, alpha: float) -> None:
        self._highlight_tint.alpha = float(alpha)
        self.update_highlight()
        self._emit_style("highlight")
