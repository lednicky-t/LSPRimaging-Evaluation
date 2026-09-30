"""``HistogramPanel`` (sketch §7 "Display panels" > Histogram, §10).

Self-sufficient per the maintainer's own description: pulls "current
displayed image" from :class:`~lspr_imaging_app.panels.image.panel.ImagePanel`
(one narrow read - `image_rendered`/`image_cleared`), computes and plots its
own histogram, and drives its own Highlight-range selection through
:class:`~lspr_imaging_app.selection.HighlightRangeModule`.

**Why this only ever subscribes to `ImagePanel`'s two signals, and nothing
from Mask/ROI Toolbox/Chromatic/Geometry directly**: those four are still
read here (`resolve_mask_source`, `affine_for`, `.settings()`, `.rois()`),
but only as *queries* at redraw time, never as signal subscriptions - because
`ImagePanel` already re-renders (and therefore re-emits `image_rendered`) on
every change any of them can make (`ImagePanel._connect_modules`: geometry,
mask, chromatic, and ROI changes all schedule a redraw there). Subscribing
to them a second time here would just be redundant bookkeeping for the same
event `ImagePanel` already turns into a signal this panel already listens to.

**Highlight range is shared state, not Histogram-owned** (maintainer's
explicit direction, 2026-09-29 - see `HighlightRangeModule`'s own
docstring): dragging the region calls `HighlightRangeModule.set_range`, and
this panel reacts to `range_changed` the same way a fresh drag would,
exactly like `ImagePanel`'s ROI-drag -> `RoiToolbox.request_move` ->
`geometry_changed` -> redraw one-way flow. Mask and ROI Toolbox read the
same module directly and need no reference to this panel at all - see
`HighlightRangeModule`'s docstring for why that shape was chosen over
Histogram-specific pub/sub.

**Percent-vs-counts and linear-vs-log are independent controls**, each set
from `HistogramPlotSettingsDialog` (opened via the plot's corner gear icon)
- a deliberate improvement over the stable app, where log mode silently
forced counts too. The Highlight-range min/max fields live as a floating
readout on the plot itself (`HighlightRangeReadout`), not in this panel;
this panel only supplies the dialog's initial values and reacts to its
changes, the same query/command shape it uses for everything else.

Deferred work (Mask/ROI-detection wiring, the "wand" auto-range button,
Image<->Histogram cursor sync, the maybe-Residual curve) is tracked in
`docs/rewrite_build_log_2026-09.md`'s "Still open" note, not duplicated
here.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ...image_tools import ChromaticModule, GeometryModule, MaskModule
from ...image_tools.preprocess import resolve_external_mask
from ...roi import RoiToolbox
from ...roi.rasterize import rasterize_reference, rasterize_sample
from ...selection import HighlightRangeModule
from ..image.panel import ImagePanel
from . import compute
from .plot import DEFAULT_LINE_WIDTH, HistogramPlot
from .settings_dialog import HistogramPlotSettingsDialog

_REDRAW_COALESCE_MS = 100  # sketch §8 - matches every other display panel


class HistogramPanel(QWidget):
    """Plots a histogram of the currently displayed image and drives the
    shared Highlight-range selection."""

    # Carries the whole settings-dialog state (percent_mode, log_y,
    # bin_width_px, line_width_px) after any one of them changes - one signal
    # rather than four, since `app_rewrite.py`'s only use for it is writing
    # all four back to `AppSettings` together (2026-09-30, same "app-level
    # setting is one field plus one wiring line" mechanism `AppSettings`'s
    # own docstring describes for `active_workflow_stage`/`theme`/etc).
    display_settings_changed = pyqtSignal(bool, bool, int, float)

    def __init__(
        self,
        image_panel: ImagePanel,
        geometry: GeometryModule,
        mask: MaskModule,
        chromatic: ChromaticModule,
        roi_toolbox: RoiToolbox,
        highlight_range: HighlightRangeModule,
        parent: QWidget | None = None,
        *,
        initial_percent_mode: bool = True,
        initial_log_y: bool = False,
        initial_bin_width: float = compute.DEFAULT_BIN_WIDTH,
        initial_line_width: float = DEFAULT_LINE_WIDTH,
    ) -> None:
        super().__init__(parent)
        self._image_panel = image_panel
        self._geometry = geometry
        self._mask = mask
        self._chromatic = chromatic
        self._roi_toolbox = roi_toolbox
        self._highlight_range = highlight_range

        self._image: np.ndarray | None = None
        self._frame: tuple[int, float] | None = None
        self._bin_width = float(initial_bin_width)
        self._percent_mode = bool(initial_percent_mode)
        self._log_y = bool(initial_log_y)
        self._line_width = float(initial_line_width)
        self._settings_dialog: HistogramPlotSettingsDialog | None = None

        self._build_ui()
        # Applied after `_build_ui` constructs `self._plot`, same as the
        # settings dialog's own live-apply handlers below - `_bin_width` and
        # `_percent_mode` need no equivalent push, since `_redraw` (called
        # once real data arrives) already reads them fresh every time.
        self._plot.set_log_y(self._log_y)
        self._plot.set_line_width(self._line_width)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        self._connect_modules()
        self._update_y_label()

    # -- construction ---------------------------------------------------------

    def _build_ui(self) -> None:
        self._plot = HistogramPlot(self)
        self._plot.highlight_dragged.connect(self._on_highlight_dragged)
        self._plot.min_edited.connect(self._on_highlight_min_edited)
        self._plot.max_edited.connect(self._on_highlight_max_edited)
        self._plot.settings_requested.connect(self._show_settings_dialog)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot, 1)

    def _connect_modules(self) -> None:
        self._image_panel.image_rendered.connect(self._on_image_rendered)
        self._image_panel.image_cleared.connect(self._on_image_cleared)
        self._highlight_range.range_changed.connect(self._on_highlight_range_changed)

    # -- image lifecycle --------------------------------------------------------

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._image = image
        self._frame = (cube_index, wavelength_nm)
        self._schedule_redraw()

    def _on_image_cleared(self) -> None:
        self._image = None
        self._frame = None
        self._plot.clear()

    # -- redraw -----------------------------------------------------------------

    def _schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _redraw(self) -> None:
        if self._image is None or self._frame is None:
            self._plot.clear()
            return

        image = self._image
        finite = np.isfinite(image)
        total_pixels = int(np.count_nonzero(finite))
        edges = compute.histogram_edges(self._bin_width)
        self._ensure_highlight_range_seeded(image[finite])

        self._plot.set_all_pixels(edges, self._scaled(compute.population_counts(image, edges), total_pixels))

        ignore_mask = self._resolve_ignore_mask()
        if ignore_mask is not None and ignore_mask.shape != image.shape:
            ignore_mask = None  # a real, if narrow, mismatch case - see _resolve_ignore_mask's caller contract
        self._set_curve_from_mask(self._plot.set_ignore_mask, ignore_mask, edges, image, total_pixels)

        # ROI positions are authored in processed/cropped space and rasterize
        # to exactly `image.shape` by construction (each mask is built at
        # that shape - see `_resolve_roi_masks`), so this can never
        # index-mismatch the way `ignore_mask` could. It can still be
        # transiently *misaligned* with what's on screen while a preview
        # tool (Crop/Rotate) shows the uncropped frame - the same "processed-
        # space coordinates over an uncropped image" case `ImagePanel._draw_
        # overlays` hides its own ROI overlay for. Left as a known, narrow,
        # transient cosmetic gap for now rather than adding an
        # `ActiveToolModule` dependency to suppress it exactly - revisit if
        # it turns out to matter in practice.
        sample_mask, reference_mask = self._resolve_roi_masks(image.shape)
        self._set_curve_from_mask(self._plot.set_sample, sample_mask, edges, image, total_pixels)
        self._set_curve_from_mask(self._plot.set_reference, reference_mask, edges, image, total_pixels)

    def _set_curve_from_mask(
        self,
        setter: Callable[[np.ndarray, np.ndarray], None],
        mask: np.ndarray | None,
        edges: np.ndarray,
        image: np.ndarray,
        total_pixels: int,
    ) -> None:
        """Shared shape for the three *optional*-population curves (Ignore
        mask, Sample ROI, Reference ROI - the All-pixels curve has no
        "absent" case and is set directly in `_redraw`). `mask=None` means
        the population doesn't apply this frame (no authored mask, no ROIs)
        and draws as empty/zero, not "every pixel". Non-finite values are
        dropped by `compute.population_counts` itself, not pre-filtered
        here - no need to intersect with an `isfinite` mask before indexing
        when that function already does it internally."""
        if mask is None:
            setter(edges, np.zeros(edges.size - 1))
            return
        counts = compute.population_counts(image[mask], edges)
        setter(edges, self._scaled(counts, total_pixels))

    def _scaled(self, counts: np.ndarray, total_pixels: int) -> np.ndarray:
        return compute.as_percent(counts, total_pixels) if self._percent_mode else counts

    def _resolve_ignore_mask(self) -> np.ndarray | None:
        assert self._frame is not None
        resolution = self._mask.resolve_mask_source(self._frame)
        if resolution is None:
            return None
        authored_frame, authored_mask, _scope = resolution
        warp_affine = None
        if authored_frame != self._frame:
            warp_affine = self._chromatic.affine_between(authored_frame, self._frame)
        return resolve_external_mask(authored_mask, self._geometry.settings(), warp_affine)

    def _resolve_roi_masks(self, image_shape: tuple[int, ...]) -> tuple[np.ndarray | None, np.ndarray | None]:
        assert self._frame is not None
        rois = self._roi_toolbox.rois()
        if not rois:
            return None, None
        shape_2d = image_shape[:2]
        affine = self._chromatic.affine_for(self._frame)
        detection = self._roi_toolbox.detection_settings()
        sample = np.zeros(shape_2d, dtype=bool)
        reference = np.zeros(shape_2d, dtype=bool)
        for roi in rois:
            sample |= rasterize_sample(roi, shape_2d, affine)
            reference |= rasterize_reference(
                roi,
                shape_2d,
                affine,
                default_inner_radius_px=detection.reference_inner_radius_px,
                default_outer_radius_px=detection.reference_outer_radius_px,
            )
        return sample, reference

    # -- axis controls ------------------------------------------------------

    def _update_y_label(self) -> None:
        self._plot.set_y_label("Pixels (%)" if self._percent_mode else "Counts")

    # -- highlight range ----------------------------------------------------

    def _ensure_highlight_range_seeded(self, finite_values: np.ndarray) -> None:
        """Seeds the Highlight range to the current frame's own [min, max]
        the first time real data arrives, since the region is hidden (and
        therefore undraggable) until a range exists. Never overwrites an
        existing range; `HighlightRangeModule.clear_range` (wired to
        `dataset_cleared` in `app_rewrite.py`) resets it so the next dataset
        gets its own fresh seed. `finite_values` is the caller's own
        already-`isfinite`-filtered array (`_redraw` needs it for
        `total_pixels` anyway) rather than recomputed here."""
        if self._highlight_range.current_range() is not None:
            return
        if finite_values.size == 0:
            return
        self._highlight_range.set_range(float(finite_values.min()), float(finite_values.max()))

    def _on_highlight_dragged(self, lo: float, hi: float) -> None:
        self._highlight_range.set_range(lo, hi)

    def _on_highlight_min_edited(self, value: float) -> None:
        self._push_highlight_bound(is_min=True, value=value)

    def _on_highlight_max_edited(self, value: float) -> None:
        self._push_highlight_bound(is_min=False, value=value)

    def _push_highlight_bound(self, *, is_min: bool, value: float) -> None:
        """Push the *other* bound forward if the edited one would cross it,
        rather than calling `HighlightRangeModule.set_range` with a crossed
        pair and relying on its swap-safety - that swap is meant for a
        caller that always passes a coherent (min, max) together (a region
        drag, the wand), not for two independently-edited fields: silently
        swapping here would snap the field the user did *not* touch to a
        value they never entered. Reads the module's own current state for
        "the other bound" rather than a local widget copy - this panel
        keeps none."""
        current = self._highlight_range.current_range()
        lo, hi = current if current is not None else (compute.DEFAULT_INTENSITY_MIN, compute.DEFAULT_INTENSITY_MAX)
        if is_min:
            self._highlight_range.set_range(value, max(hi, value))
        else:
            self._highlight_range.set_range(min(lo, value), value)

    def _on_highlight_range_changed(self, range_: tuple[float, float] | None) -> None:
        self._plot.set_highlight_range(range_)

    # -- settings dialog ------------------------------------------------------

    def _show_settings_dialog(self) -> None:
        if self._settings_dialog is None:
            dialog = HistogramPlotSettingsDialog(
                percent_mode=self._percent_mode,
                log_y=self._log_y,
                bin_width=int(self._bin_width),
                line_width=self._line_width,
                parent=self,
            )
            dialog.axis_mode_combo.currentIndexChanged.connect(self._on_settings_axis_mode_changed)
            dialog.scale_combo.currentIndexChanged.connect(self._on_settings_scale_changed)
            dialog.bin_spin.valueChanged.connect(self._on_settings_bin_width_changed)
            dialog.line_width_spin.valueChanged.connect(self._on_settings_line_width_changed)
            self._settings_dialog = dialog
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_axis_mode_changed(self, index: int) -> None:
        self._percent_mode = index == 0
        self._update_y_label()
        self._redraw()
        self._emit_display_settings_changed()

    def _on_settings_scale_changed(self, index: int) -> None:
        self._log_y = index == 1
        self._plot.set_log_y(self._log_y)
        self._emit_display_settings_changed()

    def _on_settings_bin_width_changed(self, value: int) -> None:
        # Coalesced, not immediate: a `QSpinBox` fires `valueChanged` on
        # every step of holding its arrow button or scrolling the mouse
        # wheel over it, same as any other redraw trigger this panel
        # already debounces through `_schedule_redraw`.
        self._bin_width = float(value)
        self._schedule_redraw()
        self._emit_display_settings_changed()

    def _on_settings_line_width_changed(self, value: float) -> None:
        self._line_width = value
        self._plot.set_line_width(value)
        self._emit_display_settings_changed()

    def _emit_display_settings_changed(self) -> None:
        self.display_settings_changed.emit(
            self._percent_mode, self._log_y, int(self._bin_width), self._line_width
        )
