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

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ...analysis.worker import AnalysisWorker
from ...applog.events import emit_timing, timed
from ...image_tools import ChromaticModule, GeometryModule, MaskModule
from ...image_tools.preprocess import resolve_external_mask
from ...roi import RoiToolbox
from ...selection import HighlightRangeModule, SelectionModule
from ..image.panel import ImagePanel
from ..image.tool_ribbon import VIEW_TAB
from ...storage.ui_state_keys import HISTOGRAM_CURSOR_READOUT, HISTOGRAM_CURVE_STYLES, read
from ..ui_state import UiStateStore
from ..visibility_gate import HiddenPanelGate
from . import compute
from .plot import DEFAULT_LINE_WIDTH, HistogramPlot
from .roi_masks import RoiMaskProvider
from .settings_dialog import Y_MODES, HistogramPlotSettingsDialog

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8 - matches every other display panel
_REDRAW_CALM_MS = 1000  # while the Image panel's slideshow plays with "calm histogram" on
_JOIN_SECONDS = 5.0


@dataclass(frozen=True)
class _CurveContext:
    """What one redraw computed for the curves, so the ROI curves can be drawn
    later when their masks arrive from the background."""

    key: object
    edges: np.ndarray
    image: np.ndarray
    total_pixels: int
    reference_peak: float
    selected_key: object = None
    all_curve_peak: float = 0.0  # tallest bin of the drawn All-pixels curve, in the current Y units


@dataclass(frozen=True)
class _RedrawJob:
    """One redraw handed to the worker thread: everything it reads, captured on the GUI thread.

    The arrays are shared, not copied, because none of them is modified in place anywhere in the app: the frame
    (a new array per rendered frame), the area-selection region (read-only, replaced when it changes) and the
    ignore mask (the cached, read-only result of `resolve_external_mask`)."""

    generation: int  # `HistogramPanel._generation` when it was made; a job from before a clear is dropped
    frame: tuple[int, float]  # (cube index, wavelength) the image belongs to
    image: np.ndarray
    region: np.ndarray | None
    ignore_mask: np.ndarray | None
    edges: np.ndarray
    bin_width: float


class HistogramPanel(QWidget):
    """Plots a histogram of the currently displayed image and drives the
    shared Highlight-range selection."""

    # Carries the whole settings-dialog state (y_mode, log_y, bin_width_px,
    # line_width_px) after any one of them changes - one signal rather than
    # four, since `app_rewrite.py`'s only use for it is writing all four
    # back to `AppSettings` together (2026-09-30, same "app-level setting is
    # one field plus one wiring line" mechanism `AppSettings`'s own
    # docstring describes for `active_workflow_stage`/`theme`/etc). `y_mode`
    # is one of `settings_dialog.Y_MODES` ("percent"/"counts"/"normalized"),
    # not a bool - widened 2026-09-30 to add the "Normalized (peak = 1)"
    # mode (maintainer's spec: "normalization would be towards highest
    # value"), which needs a third state a percent/counts bool cannot hold.
    display_settings_changed = pyqtSignal(str, bool, int, float)

    _counts_ready = pyqtSignal(object, object)  # (job, RedrawCounts or None); emitted from the worker thread

    _Y_LABELS = {"percent": "Pixels (%)", "counts": "Counts", "normalized": "Normalized"}

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
        initial_y_mode: str = "percent",
        initial_log_y: bool = False,
        initial_bin_width: float = compute.DEFAULT_BIN_WIDTH,
        initial_line_width: float = DEFAULT_LINE_WIDTH,
        selection: SelectionModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._selection = selection
        self._image_panel = image_panel
        self._geometry = geometry
        self._mask = mask
        self._chromatic = chromatic
        self._roi_toolbox = roi_toolbox
        self._highlight_range = highlight_range

        self._image: np.ndarray | None = None
        self._frame: tuple[int, float] | None = None
        # The ROI curves need the union of every ROI's masks: cached, and built in the
        # background for many ROIs (roi_masks.py). `_curve_context` is what the redraw
        # computed for the curves, kept so the ROI curves can be drawn later, when the masks arrive.
        self._roi_masks = RoiMaskProvider(self)
        # Same, for just the ROIs selected in the ROI table (their own cache: a different ROI subset).
        self._selected_masks = RoiMaskProvider(self)
        self._curve_context: _CurveContext | None = None
        self._bin_width = float(initial_bin_width)
        self._y_mode = initial_y_mode if initial_y_mode in Y_MODES else "percent"
        self._log_y = bool(initial_log_y)
        self._line_width = float(initial_line_width)
        self._settings_dialog: HistogramPlotSettingsDialog | None = None
        # Set only by `_on_settings_bin_width_changed`, consumed and cleared
        # by `_redraw` once the (debounced) redraw it is waiting for
        # actually happens - see `_redraw`'s own comment for why the Y
        # refit can't just happen immediately in the settings handler.
        self._pending_y_refit = False

        self._build_ui()
        # Applied after `_build_ui` constructs `self._plot`, same as the
        # settings dialog's own live-apply handlers below - `_bin_width` and
        # `_y_mode` need no equivalent push, since `_redraw` (called
        # once real data arrives) already reads them fresh every time.
        self._plot.set_log_y(self._log_y)
        self._plot.set_line_width(self._line_width)

        self._calm = False
        self._gate = HiddenPanelGate(on_shown=self._schedule_redraw)  # no redraw while the dock is hidden
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)
        # The numbers of a redraw (~25 of its ~30 ms) are made on a plain thread (never a QThreadPool: CLAUDE.md),
        # one job at a time, newest request wins; `_counts_ready` brings the result back to the GUI thread.
        self._worker = AnalysisWorker()
        self._queued_job: _RedrawJob | None = None
        self._generation = 0
        self._worker_stopped = False  # set by `_stop_worker` (quit / close): no new job may start after it
        self._counts_ready.connect(self._on_counts_ready)

        self._connect_modules()
        self._update_y_label()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._roi_masks.stop)  # a worker must not outlive the Qt objects it emits into
            app.aboutToQuit.connect(self._selected_masks.stop)
            app.aboutToQuit.connect(self._stop_worker)

    def restore_ui_state(self, store: UiStateStore) -> None:
        """Put the plot's cursor readout toggle back as last left and keep
        saving it (see `panels/ui_state.py`). Called once by the app shell."""
        overlay = self._plot.cursor_overlay()
        overlay.set_enabled(read(store, HISTOGRAM_CURSOR_READOUT) is True)
        overlay.toggled.connect(lambda on: store.set(HISTOGRAM_CURSOR_READOUT.key, bool(on)))
        try:
            saved = json.loads(str(read(store, HISTOGRAM_CURVE_STYLES)))
        except ValueError:
            saved = {}  # a hand-edited settings file: fall back to default styles
        if isinstance(saved, dict):
            self._plot.set_curve_styles(saved)
        self._plot.curve_styles_changed.connect(lambda styles: store.set(HISTOGRAM_CURVE_STYLES.key, json.dumps(styles)))

    # -- construction ---------------------------------------------------------

    def _build_ui(self) -> None:
        self._plot = HistogramPlot(self)
        self._plot.highlight_dragged.connect(self._on_highlight_dragged)
        self._plot.min_edited.connect(self._on_highlight_min_edited)
        self._plot.max_edited.connect(self._on_highlight_max_edited)
        self._plot.settings_requested.connect(self._show_settings_dialog)
        self._plot.y_axis_double_clicked.connect(self._cycle_y_mode)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot, 1)

    def _connect_modules(self) -> None:
        self._image_panel.image_rendered.connect(self._on_image_rendered)
        self._image_panel.calm_histogram_changed.connect(self.set_calm)
        self._image_panel.image_cleared.connect(self._on_image_cleared)
        self._highlight_range.range_changed.connect(self._on_highlight_range_changed)
        # Area selection (2026-10-03): the plot narrows to it, but only while
        # the ribbon's "View" tab is the one open (maintainer's spec).
        self._image_panel.area_selection().selection_changed.connect(self._schedule_redraw)
        self._image_panel.ribbon_category_changed.connect(self._schedule_redraw)
        self._image_panel.cursor_value_changed.connect(self._plot.set_value_tick)
        self._roi_toolbox.geometry_changed.connect(self._on_roi_geometry_changed)
        self._roi_masks.ready.connect(self._on_roi_masks_ready)
        self._selected_masks.ready.connect(self._on_selected_masks_ready)
        if self._selection is not None:
            self._selection.roi_selection_changed.connect(lambda _ids: self._schedule_redraw())

    # -- image lifecycle --------------------------------------------------------

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._image = image
        self._frame = (cube_index, wavelength_nm)
        self._schedule_redraw()

    def _on_image_cleared(self) -> None:
        self._image = None
        self._frame = None
        self._curve_context = None
        self._generation += 1  # a result still on its way belongs to the image that is gone
        self._queued_job = None
        self._plot.clear()

    # -- redraw -----------------------------------------------------------------

    def set_panel_visible(self, visible: bool) -> None:
        """Connected to the dock's `visibilityChanged`: hidden = no computing or drawing (a redraw is ~30 ms of GUI
        time per frame); one catch-up redraw when it is shown again."""
        self._gate.set_visible(visible)

    def _schedule_redraw(self) -> None:
        if self._gate.defer():
            return
        # Calm: a *throttle* (a running timer is left alone), never a restarting debounce, which would
        # not fire at all while frames keep arriving faster than the interval.
        if self._calm and self._redraw_timer.isActive():
            return
        self._redraw_timer.start()

    def set_calm(self, calm: bool) -> None:
        """While the slideshow plays with "calm histogram" on the plot updates at most once a second
        (a redraw is ~30 ms of GUI time on a 1.2 MP image, every frame); when Play stops it catches up at once."""
        self._calm = bool(calm)
        self._redraw_timer.setInterval(_REDRAW_CALM_MS if self._calm else _REDRAW_COALESCE_MS)
        if not self._calm:
            self._redraw_timer.start()

    def _redraw(self) -> None:
        if self._gate.defer():  # the timer was already running when the dock was hidden
            return
        with timed(logger, "histogram.redraw"):  # a DEBUG timing: the Log panel's Timings tab lists it
            self._redraw_now()

    def _redraw_now(self) -> None:
        """Start a redraw. The GUI thread only captures what it needs (cheap) and hands the heavy numbers to the
        worker thread (`compute.redraw_counts`); `_on_counts_ready` draws them. Measured 2026-10-14 on a 1.2 MP
        frame: ~26 ms per redraw, of which ~24 ms was this numeric work and ~2-4 ms the plot drawing."""
        if self._image is None or self._frame is None:
            self._plot.clear()
            self._pending_y_refit = False
            self._queued_job = None
            return

        image = self._image
        # Area selection: pixels outside it become NaN, which every count,
        # percentage and normalisation below already drops (same as "no
        # data" pixels) - so the whole plot narrows to the selection with no
        # other change. The highlight range is still seeded from the *whole*
        # frame, so selecting an area never moves the user's range.
        region = self._selection_region(image.shape)
        self._ensure_highlight_range_seeded(lambda: image[np.isfinite(image)])
        ignore_mask = self._resolve_ignore_mask()
        if ignore_mask is not None and ignore_mask.shape != image.shape:
            ignore_mask = None  # a real, if narrow, mismatch case - see _resolve_ignore_mask's caller contract
        job = _RedrawJob(
            self._generation, self._frame, image, region, ignore_mask,
            compute.histogram_edges(self._bin_width), self._bin_width,
        )
        if self._worker.is_running():
            self._queued_job = job  # newest wins: started as soon as the running one has been delivered
        else:
            self._start_job(job)

    def _start_job(self, job: _RedrawJob) -> None:
        if self._worker_stopped:  # the panel is shutting down: a thread started now could emit into a dead widget
            return
        self._worker.join(_JOIN_SECONDS)  # the previous task has already emitted and is just exiting

        def task() -> None:
            started = time.perf_counter()
            try:
                counts = compute.redraw_counts(job.image, job.region, job.ignore_mask, job.edges)
            except Exception:  # noqa: BLE001 - a failed redraw leaves the plot as it was
                logger.exception("Histogram computation failed")
                counts = None
            emit_timing(
                logger, "histogram.compute", (time.perf_counter() - started) * 1000.0,
                h=int(job.image.shape[0]), w=int(job.image.shape[1]), selection=job.region is not None,
            )
            self._counts_ready.emit(job, counts)

        self._worker.submit(task)

    def _on_counts_ready(self, job: _RedrawJob, counts: "compute.RedrawCounts | None") -> None:
        """GUI thread: the worker finished `job`. Starts the newest job that arrived meanwhile (so the worker
        keeps going while this draws), then draws this result unless the image was cleared in the meantime."""
        queued, self._queued_job = self._queued_job, None
        if queued is not None and queued.generation == self._generation:
            self._start_job(queued)
        if counts is None or job.generation != self._generation or self._image is None:
            return
        if self._gate.defer():  # hidden meanwhile: nothing is drawn, one catch-up redraw when shown again
            return
        with timed(logger, "histogram.draw"):
            self._apply_counts(job, counts)

    def _stop_worker(self) -> None:
        """Cancel and wait for the worker (a thread must not emit into a Qt object that is being destroyed).
        After this the panel starts no more jobs (a redraw timer can still fire during shutdown)."""
        self._worker_stopped = True
        self._queued_job = None
        self._worker.cancel()
        self._worker.join(_JOIN_SECONDS)

    def _apply_counts(self, job: _RedrawJob, counts: "compute.RedrawCounts") -> None:
        image, total_pixels, edges = counts.image, counts.total_pixels, job.edges
        excluded_text = compute.excluded_pixel_text_from_counts(counts.in_domain, total_pixels)
        if job.region is not None:
            selection_text = f"Selection only: {counts.in_domain:,} px".replace(",", " ")
            excluded_text = selection_text if excluded_text is None else f"{selection_text} | {excluded_text}"
        self._plot.set_excluded_text(excluded_text)

        # Normalized mode divides every curve by this one peak (the main
        # plot's), so sub-populations stay comparable to "All pixels".
        reference_peak = compute.peak_count(counts.all_counts)
        all_curve = self._scaled(counts.all_counts, total_pixels, reference_peak)
        self._plot.set_all_pixels(edges, all_curve)

        # `None`: no authored mask for this frame - the curve draws as empty/zero, not "every pixel".
        if counts.ignore_counts is None:
            self._plot.set_ignore_mask(edges, np.zeros(edges.size - 1))
        else:
            self._plot.set_ignore_mask(edges, self._scaled(counts.ignore_counts, total_pixels, reference_peak))

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
        key, roi_masks = self._request_roi_masks(job.frame, image.shape)
        selected_key, selected_masks = self._request_selected_masks(job.frame, image.shape)
        self._curve_context = _CurveContext(
            key, edges, image, total_pixels, reference_peak, selected_key, compute.peak_count(all_curve)
        )
        if roi_masks is not None:  # `None`: still being built in the background; `_on_roi_masks_ready` draws them
            self._apply_roi_curves(self._curve_context, roi_masks)
        if selected_masks is not None:  # `None`: same, drawn by `_on_selected_masks_ready`
            self._apply_selected_curves(self._curve_context, selected_masks)

        if self._pending_y_refit and job.bin_width == self._bin_width:
            # A bin-size change just redrew the curves with a different
            # number/width of bins - pyqtgraph's own continuous Y auto-range
            # only keeps tracking automatically if nothing has ever disabled
            # it (e.g. the user previously zoomed/panned), so a manual bin
            # change after that point could otherwise leave Y sitting at
            # its old fit against the new curve shape (maintainer's report,
            # 2026-09-30: "when I do autoscale it will [not] fit the plot to
            # area, it is stale"). Doing this here, after the real redraw
            # above, rather than immediately in `_on_settings_bin_width_
            # changed`, is what makes it fit the *new* data - that handler
            # only schedules the redraw (debounced), it doesn't perform it.
            # (A result made with the old bin width does not count: the redraw for the new one is behind it.)
            self._pending_y_refit = False
            self._plot.refit_y()

    def _selection_region(self, image_shape: tuple[int, ...]) -> np.ndarray | None:
        """The area selection's editable region at `image_shape`, or `None`
        when there is none or the ribbon's Histogram tab is not open."""
        if self._image_panel.active_ribbon_category() != VIEW_TAB:
            return None
        return self._image_panel.area_selection().mask(image_shape)

    def _set_curve_from_mask(
        self,
        setter: Callable[[np.ndarray, np.ndarray], None],
        mask: np.ndarray | None,
        edges: np.ndarray,
        image: np.ndarray,
        total_pixels: int,
        reference_peak: float,
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
        setter(edges, self._scaled(counts, total_pixels, reference_peak))

    def _scaled(self, counts: np.ndarray, total_pixels: int, reference_peak: float) -> np.ndarray:
        if self._y_mode == "percent":
            return compute.as_percent(counts, total_pixels)
        if self._y_mode == "normalized":
            return compute.as_normalized(counts, reference_peak)
        return counts

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

    def _request_roi_masks(
        self, frame: tuple[int, float], image_shape: tuple[int, ...]
    ) -> tuple[object, tuple[np.ndarray | None, np.ndarray | None] | None]:
        """``(key, masks)``: `masks` is the (sample, reference) pair, ``(None, None)``
        when there are no ROIs, or `None` while a large union is still being built.
        `frame` is the one the drawn image belongs to (not necessarily the current one)."""
        rois = self._roi_toolbox.rois_at(frame[0])
        if not rois:
            return None, (None, None)
        affine = self._chromatic.affine_for(frame)
        detection = self._roi_toolbox.detection_settings()
        return self._roi_masks.request(
            rois,
            image_shape[:2],
            affine,
            detection.reference_inner_diameter_px,
            detection.reference_outer_diameter_px,
        )

    def _request_selected_masks(
        self, frame: tuple[int, float], image_shape: tuple[int, ...]
    ) -> tuple[object, tuple[np.ndarray | None, np.ndarray | None] | None]:
        """Like `_request_roi_masks`, but for only the ROIs selected in the ROI table."""
        if self._selection is None:
            return None, (None, None)
        selected = self._selection.selected_roi_ids()
        rois = [roi for roi in self._roi_toolbox.rois_at(frame[0]) if roi.area_roi_id in selected]
        if not rois:
            return None, (None, None)
        detection = self._roi_toolbox.detection_settings()
        return self._selected_masks.request(
            rois,
            image_shape[:2],
            self._chromatic.affine_for(frame),
            detection.reference_inner_diameter_px,
            detection.reference_outer_diameter_px,
            variant=frozenset(roi.area_roi_id for roi in rois),
        )

    def _apply_selected_curves(
        self, context: "_CurveContext", masks: tuple[np.ndarray | None, np.ndarray | None]
    ) -> None:
        """The selected ROIs' sample and reference pixels, each drawn at its own
        scale: its tallest bin is set to the All-pixels curve's tallest bin
        (whatever the Y mode), because their raw counts are far lower than the
        whole frame's and would otherwise sit flat on the axis."""
        sample_mask, reference_mask = masks
        for setter, mask in (
            (self._plot.set_selected_sample, sample_mask),
            (self._plot.set_selected_reference, reference_mask),
        ):
            if mask is None:
                setter(context.edges, np.zeros(context.edges.size - 1))
                continue
            counts = compute.population_counts(context.image[mask], context.edges)
            setter(context.edges, compute.scaled_to_peak(counts, context.all_curve_peak))

    def _on_selected_masks_ready(self, key: object) -> None:
        if self._gate.defer():
            return
        context = self._curve_context
        masks = self._selected_masks.cached(key)
        if context is None or context.selected_key != key or masks is None:
            return
        self._apply_selected_curves(context, masks)

    def _on_roi_geometry_changed(self, _change: object) -> None:
        self._roi_masks.invalidate()
        self._selected_masks.invalidate()
        # Redraw on our own (2026-10-21): the ROI curves used to come back because a ROI edit re-rendered the
        # image and `_on_image_rendered` redrew this panel. The Image panel now redraws only its overlay when the
        # pixels cannot have changed, so nothing else would ask for these curves again. The plane counts are
        # recomputed with them - wasted work in principle, but it happens on this panel's worker thread, not the
        # GUI thread, and it keeps one redraw path instead of two.
        self._schedule_redraw()

    def _apply_roi_curves(
        self, context: "_CurveContext", masks: tuple[np.ndarray | None, np.ndarray | None]
    ) -> None:
        sample_mask, reference_mask = masks
        args = (context.edges, context.image, context.total_pixels, context.reference_peak)
        self._set_curve_from_mask(self._plot.set_sample, sample_mask, *args)
        self._set_curve_from_mask(self._plot.set_reference, reference_mask, *args)

    def _on_roi_masks_ready(self, key: object) -> None:
        """The background union for `key` finished: draw the two ROI curves
        against what the last redraw computed, if that redraw is still current."""
        if self._gate.defer():
            return
        context = self._curve_context
        masks = self._roi_masks.cached(key)
        if context is None or context.key != key or masks is None:
            return
        self._apply_roi_curves(context, masks)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._roi_masks.stop()
        self._selected_masks.stop()
        self._stop_worker()
        super().closeEvent(event)

    # -- axis controls ------------------------------------------------------

    def _update_y_label(self) -> None:
        self._plot.set_y_label(self._Y_LABELS[self._y_mode])

    # -- highlight range ----------------------------------------------------

    def _ensure_highlight_range_seeded(self, finite_values: "Callable[[], np.ndarray]") -> None:
        """Seeds the Highlight range to the current frame's own [min, max]
        the first time real data arrives, since the region is hidden (and
        therefore undraggable) until a range exists. Never overwrites an
        existing range; `HighlightRangeModule.clear_range` (wired to
        `dataset_cleared` in `app_rewrite.py`) resets it so the next dataset
        gets its own fresh seed. `finite_values` is a function returning the
        whole frame's finite pixels: called only when a seed is actually
        needed (once per dataset), not on every redraw."""
        if self._highlight_range.current_range() is not None:
            return
        values = finite_values()
        if values.size == 0:
            return
        self._highlight_range.set_range(float(values.min()), float(values.max()))

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
                y_mode=self._y_mode,
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

    def _cycle_y_mode(self) -> None:
        """Double-clicking the Y axis (`plot.py`'s `y_axis_double_clicked`)
        steps to the next entry in `Y_MODES`, wrapping around - maintainer's
        spec, 2026-09-30: "can y axis be switchable also by double clicking
        on it." Goes through the same `_apply_y_mode` the settings dialog's
        combo uses, and keeps that combo's own selection in sync if it
        happens to be open at the time, so the two controls can never show
        a different mode from each other."""
        next_index = (Y_MODES.index(self._y_mode) + 1) % len(Y_MODES)
        if self._settings_dialog is not None:
            self._settings_dialog.axis_mode_combo.setCurrentIndex(next_index)  # triggers _on_settings_axis_mode_changed
        else:
            self._apply_y_mode(Y_MODES[next_index])
            self._emit_display_settings_changed()

    def _on_settings_axis_mode_changed(self, index: int) -> None:
        self._apply_y_mode(Y_MODES[index])
        self._emit_display_settings_changed()

    def _apply_y_mode(self, mode: str) -> None:
        self._y_mode = mode
        self._update_y_label()
        # The refit has to see the redrawn curves (the Y scale changes with the mode), and the numbers of a
        # redraw come back from the worker a moment after `_redraw` returns: `_apply_counts` refits then.
        self._pending_y_refit = True
        self._redraw()
        if self._image is None:  # nothing will be drawn, so fit the empty axes now
            self._pending_y_refit = False
            self._plot.refit_y()

    def _on_settings_scale_changed(self, index: int) -> None:
        self._log_y = index == 1
        self._plot.set_log_y(self._log_y)
        self._emit_display_settings_changed()

    def _on_settings_bin_width_changed(self, value: int) -> None:
        # Coalesced, not immediate: a `QSpinBox` fires `valueChanged` on
        # every step of holding its arrow button or scrolling the mouse
        # wheel over it, same as any other redraw trigger this panel
        # already debounces through `_schedule_redraw`. `_pending_y_refit`
        # rides along with that same debounce - see `_redraw`'s comment for
        # why the refit has to wait for the redraw it's attached to, rather
        # than happening here immediately (which would just fit Y to the
        # *old* bins, one redraw too early).
        self._bin_width = float(value)
        self._pending_y_refit = True
        self._schedule_redraw()
        self._emit_display_settings_changed()

    def _on_settings_line_width_changed(self, value: float) -> None:
        self._line_width = value
        self._plot.set_line_width(value)
        self._emit_display_settings_changed()

    def _emit_display_settings_changed(self) -> None:
        self.display_settings_changed.emit(
            self._y_mode, self._log_y, int(self._bin_width), self._line_width
        )
