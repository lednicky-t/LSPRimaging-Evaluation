"""Sensorgram worker orchestration, absorbance-spectrum (single-cube,
interactive) result handling, and cache-signature building/plumbing.
Mixed into AnalysisController (see analysis_controller.py's class
declaration) - `self` here is the AnalysisController instance, so these
methods use the same `self.window` state as the rest of the class.

Bundled into one mixin (not three) deliberately: these clusters cross-call
each other roughly two dozen times (the sensorgram worker reaches directly
into the cache/signature builders and the absorbance-result handlers reach
into both), so splitting them into separate files would mean a reader
tracing the main "run a calculation" path bounces between 3 files on nearly
every other line - worse than the original single file for that specific
path. Bundling makes nearly all of those cross-calls intra-file again; the
only remaining cross-file calls are into AnalysisChromaticGeometryMixin
(`_build_shared_wavelength_geometry`/`_build_shared_wavelength_mask`, called
from `_start_sensorgram_worker`), which is one-directional and fine.
"""

from __future__ import annotations

import logging
import os
import time
import numpy as np
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_imaging_app.domain.exclusions import is_excluded
from lspr_imaging_app.domain.models import AreaRoi, FormulaSpectrumResult
from lspr_imaging_app.gui.analysis_tasks import _roi_formula_spectrum_signature, _scoped_formula_spectrum_task
from lspr_imaging_app.processing.analysis import (
    formula_values_from_reduced_values,
    metric_value_from_fit,
    metric_value_from_spectrum,
    project_formula_spectrum,
    project_reduction_result,
)
from lspr_imaging_app.processing.chromatic import warp_boolean_mask_affine
from lspr_imaging_app.processing.roi_math import DEFAULT_TRIMMED_MEAN_FRACTION, REDUCTION_METHODS
from lspr_imaging_app.storage.measurement_export import (
    FormulaSpectrumBackupRow,
    FormulaSpectrumTraceIndex,
    SensorgramPointBackupRow,
)
from lspr_imaging_app.gui.analysis_types import SpectrumSettingsSnapshot, SharedWavelengthGeometry


@dataclass(slots=True)
class FormulaSpectrumRenderBundle:
    """Everything `_render_formula_spectrum_result` needs to update the
    spectrum plot/labels, computed once by `_compute_formula_spectrum_result`
    with no Qt widget touched. Same-thread, single-consumer, write-once-
    read-once handoff between those two methods - unlike
    SpectrumSettingsSnapshot/SharedWavelengthGeometry (analysis_types.py),
    this never crosses a thread or a file boundary, so it lives here rather
    than there."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    metric_value: float | None
    metric_signal: float | None
    current_x: float | None
    current_y: float | None
    basic_text: str
    detail_tooltip: str
    fit_seconds: float


# A dark-correction shift worth more than 5% of the real spectrum's own
# natural variation (max-min of its own formula values) is treated as
# "worth acting on" - self-relative, not an absolute cutoff, since what
# counts as a "big" absorbance shift depends entirely on how much the real
# signal itself varies across the spectrum. Same reasoning/threshold as
# this module's earlier wavelength-shift version of this test, just applied
# to formula-value range instead of nm range now.
DARK_FRAME_IMPACT_RECOMMEND_EXCLUDE_THRESHOLD = 0.05

# If the dark frame's plain mean is more than this fraction above its own
# trimmed mean (DEFAULT_TRIMMED_MEAN_FRACTION discards the extreme 10% from
# each tail), a handful of unusually hot/noisy pixels are likely inflating
# the mean rather than the whole ROI being uniformly offset - worth flagging
# separately from the overall correction-impact number, since it points to
# a sensor/ROI-placement issue rather than a "should I dark-correct" one.
DARK_FRAME_HOT_PIXEL_RATIO_THRESHOLD = 0.15


@dataclass(slots=True)
class DarkFramePixelImpact:
    """Pure result of `dark_frame_pixel_impact`: what a dataset's 0 nm
    (dark/background) frame's own pixel counts look like, and how much
    subtracting them from every real wavelength's sample/reference values
    would actually change the computed formula value - the real question a
    user needs answered to decide whether dark-current subtraction matters
    for their dataset, not (as an earlier version of this test asked) how
    the dark frame distorts a spectral *fit* it was never meant to be part
    of in the first place."""

    dark_sample_mean: float
    dark_reference_mean: float
    dark_sample_hot_pixel_ratio: float | None
    """(mean - trimmed_mean) / mean for the dark frame's own sample-ROI
    pixels - None if the dark sample mean is ~0 (division would be
    meaningless) or trimmed_mean data wasn't available."""
    dark_as_percent_of_dimmest_signal: float | None
    """dark_sample_mean as a percentage of the real spectrum's own dimmest
    (minimum) sample value - None if that minimum is ~0."""
    worst_wavelength_nm: float
    worst_shift: float
    """Signed: (formula value with dark subtracted) - (without), at
    whichever real wavelength the subtraction changes the most."""
    mean_abs_shift: float
    formula_value_range: float
    """max - min of the real (uncorrected) formula values across the
    spectrum - the denominator for expressing a shift as "how much of your
    own signal's variation" instead of an arbitrary absolute cutoff."""


def dark_frame_pixel_impact(
    wavelengths_nm: np.ndarray,
    sample_reduced_value: np.ndarray,
    reference_reduced_value: np.ndarray,
    reduced_values_by_method: dict[str, tuple[np.ndarray, np.ndarray]],
    formula_key: str,
) -> DarkFramePixelImpact | None:
    """Pure core of run_dark_frame_impact_test: given one already-computed
    spectrum (real wavelengths plus the dataset's 0 nm dark/background
    frame mixed in, all reduced the same way real ROI pixels are), measures
    the dark frame's own pixel-count level and simulates subtracting it
    from every real wavelength's sample/reference values, comparing the
    resulting formula value (e.g. absorbance) against the uncorrected one.
    Returns None if there's no 0 nm entry or no real wavelength to compare
    against. Split out from orchestration/dispatch so it's directly
    unit-testable with synthetic arrays - no Qt app, dataset, or background
    thread needed (mirrors this repo's other pure-compute-vs-orchestration
    splits, e.g. main_window.py's _format_busy_detail_text).

    `reduced_values_by_method` is the same dict `_scoped_formula_spectrum_task`
    already returns when `compute_all_reduction_methods=True` (every
    Reduction method computed from the same pixels, "for free") - used here
    only for its `"trimmed_mean"` entry, as a robust-to-outliers comparison
    against the plain mean to flag likely hot/noisy pixels in the dark
    frame specifically, not to change which Reduction method drives the
    actual correction (that's always the plain mean, matching the physical
    question "what's the average dark offset every pixel in this ROI
    carries").
    """
    wavelengths = np.asarray(wavelengths_nm, dtype=np.float64)
    dark_mask = wavelengths == 0.0
    if not np.any(dark_mask):
        return None
    real_mask = ~dark_mask
    if not np.any(real_mask):
        return None
    dark_index = int(np.flatnonzero(dark_mask)[0])

    sample = np.asarray(sample_reduced_value, dtype=np.float64)
    reference = np.asarray(reference_reduced_value, dtype=np.float64)
    dark_sample_mean = float(sample[dark_index])
    dark_reference_mean = float(reference[dark_index])

    hot_pixel_ratio: float | None = None
    trimmed = reduced_values_by_method.get("trimmed_mean")
    if trimmed is not None and abs(dark_sample_mean) > 1.0e-9:
        trimmed_sample = np.asarray(trimmed[0], dtype=np.float64)
        if dark_index < trimmed_sample.size:
            dark_sample_trimmed = float(trimmed_sample[dark_index])
            hot_pixel_ratio = (dark_sample_mean - dark_sample_trimmed) / dark_sample_mean

    real_wavelengths = wavelengths[real_mask]
    real_sample = sample[real_mask]
    real_reference = reference[real_mask]

    uncorrected = formula_values_from_reduced_values(real_sample, real_reference, formula_key)
    corrected = formula_values_from_reduced_values(real_sample - dark_sample_mean, real_reference - dark_reference_mean, formula_key)
    shift = corrected - uncorrected
    worst_index = int(np.argmax(np.abs(shift)))

    min_real_sample = float(np.min(real_sample)) if real_sample.size else 0.0
    dark_percent_of_dimmest = (dark_sample_mean / min_real_sample * 100.0) if min_real_sample > 1.0e-9 else None
    formula_value_range = float(np.max(uncorrected) - np.min(uncorrected)) if uncorrected.size >= 2 else 0.0

    return DarkFramePixelImpact(
        dark_sample_mean=dark_sample_mean,
        dark_reference_mean=dark_reference_mean,
        dark_sample_hot_pixel_ratio=hot_pixel_ratio,
        dark_as_percent_of_dimmest_signal=dark_percent_of_dimmest,
        worst_wavelength_nm=float(real_wavelengths[worst_index]),
        worst_shift=float(shift[worst_index]),
        mean_abs_shift=float(np.mean(np.abs(shift))),
        formula_value_range=formula_value_range,
    )


def format_dark_frame_impact_result(
    impact: DarkFramePixelImpact | None,
    spectral_cube_index: int,
    formula_label: str,
) -> str:
    """Pure formatter for AnalysisWorkerMixin.run_dark_frame_impact_test's
    result text - split out so it's directly unit-testable without a
    running Qt app or a real dataset (mirrors main_window.py's
    _format_busy_detail_text pattern)."""
    if impact is None:
        return (
            f"Cube {spectral_cube_index}: could not evaluate dark-frame impact - "
            "this dataset has no 0 nm frame, or no real wavelength to compare it against."
        )
    lines = [
        f"Cube {spectral_cube_index}: dark frame counts - sample={impact.dark_sample_mean:.1f}, "
        f"reference={impact.dark_reference_mean:.1f}."
    ]
    if impact.dark_as_percent_of_dimmest_signal is not None:
        lines.append(f"That's {impact.dark_as_percent_of_dimmest_signal:.2f}% of your dimmest real wavelength's own signal.")
    if impact.dark_sample_hot_pixel_ratio is not None and impact.dark_sample_hot_pixel_ratio > DARK_FRAME_HOT_PIXEL_RATIO_THRESHOLD:
        lines.append(
            f"Note: the dark mean is {impact.dark_sample_hot_pixel_ratio * 100.0:.0f}% above its trimmed mean - "
            "a few unusually hot/noisy pixels may be inflating it, worth checking this ROI/mask rather than "
            "just the overall dark level."
        )
    if impact.formula_value_range > 1.0e-9:
        percent = abs(impact.worst_shift) / impact.formula_value_range * 100.0
        percent_text = f"{percent:.1f}% of your spectrum's own {impact.formula_value_range:.4g}-unit range"
        significant = percent > DARK_FRAME_IMPACT_RECOMMEND_EXCLUDE_THRESHOLD * 100.0
    else:
        percent_text = "your spectrum is too flat here to express as a percentage"
        significant = abs(impact.worst_shift) > 1.0e-9
    verdict = (
        "This could visibly affect your results - consider dark-correcting or excluding your lowest-signal wavelengths."
        if significant
        else "Negligible for this dataset - dark subtraction wouldn't meaningfully change your results."
    )
    lines.append(
        f"Dark-correcting would shift {formula_label} by up to {impact.worst_shift:+.4g} "
        f"(at {impact.worst_wavelength_nm:.0f}nm, worst case; {percent_text}). {verdict}"
    )
    return " ".join(lines)


def _write_measurement_backup_buffers(writer, formula_buffer, sensorgram_buffer) -> None:
    """The actual HDF5 write behind a measurement-backup flush - writes
    every row in `formula_buffer`/`sensorgram_buffer` (the same
    {roi_id_str: [(cube_index, signature_hash, value, timestamp_utc_ms), ...]}
    shape `_backup_formula_spectrum_series`/`_backup_sensorgram_point` build)
    via one batch call per ROI.

    Deliberately a plain module-level function, not a method on
    AnalysisWorkerMixin: `_flush_measurement_backup_buffers_async` runs this
    on a background thread (see its docstring), and a bound method would
    tempt a future edit into reaching back into `self.window` from that
    thread - every value this function touches must already be plain data
    handed in by the caller, nothing resolved by calling back into GUI-owned
    state. Shared by both the synchronous flush (main thread) and the
    background one, so there is exactly one place this logic can drift from.
    """
    if writer is not None and formula_buffer:
        for roi_id_str, entries in formula_buffer.items():
            if not entries:
                continue
            rows = [
                FormulaSpectrumBackupRow(
                    wavelengths_nm=roi_result.wavelengths_nm,
                    formula_values=roi_result.formula_values,
                    sample_mean=roi_result.sample_reduced_value,
                    reference_mean=roi_result.reference_reduced_value,
                    cube_index=cube_index,
                    timestamp_utc_ms=timestamp_utc_ms,
                    formula_key=roi_result.formula_key,
                    reduction_method=roi_result.reduction_method,
                    signature_hash=signature_hash,
                    reduced_values_by_method=roi_result.reduced_values_by_method or None,
                )
                for cube_index, signature_hash, roi_result, timestamp_utc_ms in entries
            ]
            try:
                writer.append_formula_spectrum_batch(roi_id_str, rows)
            except Exception:
                logging.getLogger("lspr_imaging_app.workflow").warning(
                    "Failed to append absorbance spectrum batch to measurement export backup", exc_info=True
                )
    if writer is not None and sensorgram_buffer:
        for roi_id_str, entries in sensorgram_buffer.items():
            if not entries:
                continue
            rows = [
                SensorgramPointBackupRow(
                    cube_index=cube_index,
                    timestamp_utc_ms=timestamp_utc_ms,
                    metric_value=metric_value,
                    signature_hash=signature_hash,
                )
                for cube_index, signature_hash, metric_value, timestamp_utc_ms in entries
            ]
            try:
                writer.append_sensorgram_point_batch(roi_id_str, rows)
            except Exception:
                logging.getLogger("lspr_imaging_app.workflow").warning(
                    "Failed to append sensorgram point batch to measurement export backup", exc_info=True
                )


class AnalysisWorkerMixin:
    def _apply_cached_sensorgram_result(self, signature, result, *, preview: bool = False) -> None:
        self.window._sensorgram_running = False
        self.window._sensorgram_running_signature = None
        self.window._sensorgram_cancel_event = None
        self.window._end_busy()
        self.window._sync_busy_cursor_state()
        self.window._sensorgram_spectral_cube_indices = np.asarray(result.spectral_cube_indices, dtype=np.int32)
        self.window._sensorgram_metric_values = np.asarray(result.metric_values, dtype=np.float64)
        self.window._sensorgram_metric_signal = np.asarray(result.metric_signal, dtype=np.float64)
        if signature:
            self._store_in_lru_cache(self.window._sensorgram_cache, signature, result, self.window.SENSORGRAM_CACHE_SIZE)
        self.set_sensorgram_series(self.window._sensorgram_spectral_cube_indices, self.window._sensorgram_metric_values)
        summary = (
            f"{self.window._analysis_metric_label()} | Calculated {result.completed_count}/{result.total_count} spectral cubes"
            f"{self._poly_order_summary_suffix()}"
        )
        if result.cancelled:
            summary = (
                f"{self.window._analysis_metric_label()} | Stopped after {result.completed_count}/{result.total_count} spectral cubes"
                f"{self._poly_order_summary_suffix()}"
            )
        self.window._set_sensorgram_summary_text(summary)
        if result.cancelled:
            self.window._set_status_text("SG | stopped")
        elif preview:
            self.window._set_status_text("SG | cache 00:00")
        else:
            timing = self.window._compact_timing_text(("prep", result.prep_seconds), ("fit", result.fit_seconds))
            self.window._set_status_text(f"SG | {timing}" if timing else "SG | done")
        self.window._update_analysis_control_state()

    def calculate_sensorgram(self) -> None:
        self._calculate_sensorgram_for_range()

    def stop_sensorgram(self) -> None:
        self.window._stop_sensorgram_calculation()

    def run_or_stop_sensorgram(self) -> None:
        """Merged Start analysis / Stop button (Analysis section title row):
        dispatches to whichever action the icon is currently showing -
        Start analysis while idle, Stop while a run is already in progress."""
        if self.window._sensorgram_running:
            self.stop_sensorgram()
        else:
            self.calculate_sensorgram()

    def clear_sensorgram(self, summary_text: str) -> None:
        self.window._sensorgram_spectral_cube_indices = np.asarray([], dtype=np.int32)
        self.window._sensorgram_metric_values = np.asarray([], dtype=np.float64)
        self.window._sensorgram_metric_signal = np.asarray([], dtype=np.float64)
        self.window._pending_sensorgram_payload = None
        self.window.sensorgram_curve.setData([], [])
        self.window.sensorgram_current_point.setData([], [])
        self.window.sensorgram_processed_curve.hide()
        self.window.sensorgram_group_curve.hide()
        self.window.sensorgram_group_band_fill_item.hide()
        self.update_plot_labels()
        self.update_selection_highlight(force=True)
        self.window.sensorgram_summary_label.setText(summary_text)

    def set_sensorgram_series(self, spectral_cube_indices, metric_values, *, summary_text: str | None = None) -> None:
        spectral_cubes = np.asarray(spectral_cube_indices, dtype=np.int32)
        metrics = np.asarray(metric_values, dtype=np.float64)
        x_values = self._sensorgram_x_values(spectral_cubes)
        valid_mask = np.isfinite(x_values) & np.isfinite(metrics)
        self.window._sensorgram_spectral_cube_indices = spectral_cubes.copy()
        self.window._sensorgram_metric_values = metrics.copy()
        self.window.sensorgram_curve.setData(x_values[valid_mask], metrics[valid_mask])
        self.update_plot_labels()
        self.update_selection_highlight(force=True)
        if np.any(valid_mask):
            x_plotted = x_values[valid_mask]
            y_values = metrics[valid_mask].astype(np.float64, copy=False)
            self.window.sensorgram_plot.setXRange(float(np.min(x_plotted)), float(np.max(x_plotted)), padding=0.03)
            y_min = float(np.min(y_values))
            y_max = float(np.max(y_values))
            y_span = max(y_max - y_min, 0.05)
            self.window.sensorgram_plot.setYRange(y_min - y_span * 0.08, y_max + y_span * 0.12, padding=0.0)
        self.update_current_point()
        if summary_text is not None:
            self.window.sensorgram_summary_label.setText(summary_text)
        self._update_statistics_overlays()

    def calculate_sensorgram_for_range(self) -> None:
        """Public alias for `_calculate_sensorgram_for_range` - kept as a
        separate name because `_finish_group_calculation` and the
        live-preview selection-change prompt (main_window.py) already call
        it under this name. There used to be two full, diverging
        implementations here (missing/misordered running-state guard, one
        locked, one not) - consolidated into the one canonical
        implementation, see `_calculate_sensorgram_for_range`."""
        self._calculate_sensorgram_for_range()

    def preview_sensorgram_from_cache(self) -> bool:
        if self._sensorgram_prerequisite_blocked() is not None:
            return False
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if not selected_roi_ids:
            return False
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if not selected_source_rois:
            return False
        spectral_cubes = self.available_analysis_spectral_cubes()
        if not spectral_cubes:
            return False
        signature = self.window._sensorgram_signature_for_selection(spectral_cubes, selected_roi_ids, selected_source_rois)
        if signature is None:
            return False
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is None:
            return False
        self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
        return True

    def _spectrum_settings_snapshot(self) -> SpectrumSettingsSnapshot:
        """Build a SpectrumSettingsSnapshot once, up front - see its docstring.
        Callers pass the result into `_prepare_scoped_spectrum_payload_for_spectral_cube`
        instead of letting it deep-copy the live state fresh on every call.
        """
        return SpectrumSettingsSnapshot(
            preprocessing=deepcopy(self.window._state.preprocessing),
            area_roi_settings=deepcopy(self.window._state.area_roi_settings),
            mask_state=deepcopy(self.window._state.mask) if self.window._mask_section_applied() else None,
        )

    def _prepare_scoped_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple,
        selected_source_rois: list,
        settings_snapshot: SpectrumSettingsSnapshot | None = None,
        shared_geometry: SharedWavelengthGeometry | None = None,
        shared_mask_by_wavelength: dict[float, object] | None = None,
        wavelength_values: list[float] | None = None,
    ) -> tuple | None:
        """Build the payload for `_scoped_formula_spectrum_task` - the one
        spectrum-compute path, used for every dataset regardless of format.
        Returns None only when there's genuinely nothing to compute (no data
        for this spectral_cube_index, or no ROI geometry to build a read
        region from at all).

        `spectrum_read_region` (analysis_tasks.py) is the only place format
        matters: a TIFF dataset gets the whole plane as its region (its only
        real read unit), an OME-Zarr dataset gets the ROI union bounding box
        (cheap at any size - see the `zarrs` codec pipeline fix). Everything
        below this point is format-agnostic.

        `shared_geometry`: [λ] mode - when given, the per-wavelength
        chromatic affine and the read region are taken from it instead of
        being recomputed for this cube (see SharedWavelengthGeometry's
        docstring for why that's valid). `shared_mask_by_wavelength`: [λ]
        mode's equivalent for the marked-pixels mask (see
        _build_shared_wavelength_mask) - when given, the already-warped,
        already-diffed mask for each wavelength is taken from it instead of
        being fetched/warped fresh for this cube. Which record/file to read
        per wavelength, and exclusions, are still resolved per cube either
        way - those are genuinely cube-specific (different files).

        `wavelength_values`: which wavelengths to build the payload over -
        defaults to `self.window._wavelength_values` (the normal, possibly
        `_filtered_wavelength_values`-filtered list every other caller
        implicitly uses). Only `run_dark_frame_impact_test` passes this
        explicitly, with the dataset's raw, unfiltered wavelength list -
        that test needs to see a 0 nm frame regardless of whether the
        "Treat 0 nm as a dark reference frame" preference already filters
        it out of the normal list.
        """
        from lspr_imaging_app.gui.analysis_tasks import spectrum_read_region
        from lspr_imaging_app.io.dataset import load_image_shape
        from lspr_imaging_app.processing.preprocess import spatial_output_shape

        if self.window._state.dataset is None or not selected_source_rois:
            return None
        if settings_snapshot is None:
            settings_snapshot = self._spectrum_settings_snapshot()
        preprocessing = settings_snapshot.preprocessing
        if wavelength_values is None:
            wavelength_values = self.window._wavelength_values

        # Mirror ignored_pixel_mask's own gating: an external mask only excludes
        # pixels from the absorbance calculation when ignore_marked_pixels is
        # on. Fetching it unconditionally and applying it in the fast task
        # regardless of this flag would silently diverge from the slow path.
        exclude_marked_pixels = bool(getattr(settings_snapshot.area_roi_settings, "ignore_marked_pixels", False))

        measurement_payload: list[tuple[float, np.ndarray | None, np.ndarray | None, object]] = []
        affine_matrices: list[np.ndarray | None] = []
        first_record = None
        for wavelength in wavelength_values:
            record = self.window._record_map.get((spectral_cube_index, wavelength))
            if record is None or is_excluded(self.window._state.image_exclusions, spectral_cube_index, wavelength):
                continue
            if first_record is None:
                first_record = record
            image_key = (spectral_cube_index, float(wavelength))
            if shared_geometry is not None:
                affine_matrix = shared_geometry.affine_matrix_by_wavelength.get(float(wavelength))
            else:
                affine_matrix = self.window._chromatic_affine_for_image_key(image_key)
                if affine_matrix is not None:
                    affine_matrix = np.asarray(affine_matrix, dtype=np.float64)
            external_mask = None
            if exclude_marked_pixels:
                if shared_mask_by_wavelength is not None:
                    external_mask = shared_mask_by_wavelength.get(float(wavelength))
                else:
                    external_mask, _ = self.window._effective_external_mask_for_record(record.path, processed_space=True)
                    if external_mask is not None:
                        external_mask = np.asarray(external_mask, dtype=bool)
                        # Same per-wavelength chromatic warp as the slow/absorbance
                        # payload builder above - must happen here, before the mask is
                        # sliced into a wavelength-specific patch box downstream (the
                        # box itself is computed in per-wavelength-transformed space
                        # since the ROIs move; warping after slicing would read the
                        # wrong region of the unwarped mask).
                        if affine_matrix is not None:
                            external_mask = warp_boolean_mask_affine(external_mask, affine_matrix)
                        external_mask = self.window._apply_mask_wavelength_diff(external_mask, image_key)
            measurement_payload.append(
                (
                    float(wavelength),
                    affine_matrix,
                    external_mask,
                    record,
                )
            )
            affine_matrices.append(affine_matrix)
        if not measurement_payload or first_record is None:
            return None

        if shared_geometry is not None:
            raw_shape = shared_geometry.raw_shape
            image_height, image_width = shared_geometry.image_height, shared_geometry.image_width
            box = shared_geometry.box
        else:
            try:
                raw_shape = load_image_shape(str(first_record.path))
            except Exception:
                return None
            image_height, image_width = spatial_output_shape(raw_shape, preprocessing)

            box = spectrum_read_region(
                self.window._state.dataset,
                image_height,
                image_width,
                selected_source_rois,
                float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
                affine_matrices,
            )
            if box is None:
                return None

        # mask_state only when the mask panel is applied/linked, and
        # background's own exclusion mask_settings only when background
        # flattening is configured to exclude the mask.
        mask_state = settings_snapshot.mask_state
        background_mask_settings = (
            settings_snapshot.area_roi_settings
            if bool(getattr(preprocessing, "flatten_background_exclude_mask", False))
            else None
        )

        return (
            self.window._state.dataset,
            int(spectral_cube_index),
            measurement_payload,
            # Already a deepcopy by the time it reaches this function - see
            # _prepare_formula_spectrum_payload's own comment on this.
            selected_source_rois,
            selected_roi_ids,
            float(settings_snapshot.area_roi_settings.reference_inner_radius_px),
            float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
            box,
            preprocessing,
            raw_shape,
            # The cache key folds in patch shape/origin (see
            # _formula_spectrum_roi_mask_cache_key) so masks for different
            # regions can never collide in this shared, size-capped cache.
            self.window._formula_spectrum_roi_mask_cache,
            self.window._analysis_cache_lock,
            int(self.window.FORMULA_SPECTRUM_ROI_MASK_CACHE_SIZE),
            mask_state,
            background_mask_settings,
        )

    def run_dark_frame_impact_test(self, on_result, on_error) -> None:
        """"Test dark-frame impact" (Preferences > Wavelength handling,
        next to "Treat 0 nm as a dark reference frame"): for the first
        spectral cube only, measures the dataset's 0 nm (dark/background)
        frame's own pixel-count level in the current ROI selection, and
        simulates subtracting it from every real wavelength's sample/
        reference values to see how much that actually changes the
        computed formula value (e.g. Absorbance) - the physically real
        question ("does this dark count matter enough to correct for"),
        not how a dark frame distorts a spectral fit it was never meant to
        be part of (an earlier version of this test asked that instead;
        see `dark_frame_pixel_impact`'s own docstring for why the pixel-
        level question is the right one). Built from a real incident where
        a dark frame's near-zero signal, left in an order-11 polynomial
        fit, pulled a sensorgram trace to a nonsense wavelength - see
        docs/qthreadpool_zarr_crash_investigation.md's sibling
        sensorgram-wavelength-range bug writeup.

        Always builds against the dataset's raw, unfiltered wavelength list
        (`dataset.wavelengths_nm`, not `window._wavelength_values`) so the
        test is meaningful regardless of whether the preference is
        currently on - `_wavelength_values` may already have 0 nm filtered
        out by the time this runs.

        Format-agnostic: reuses `_prepare_scoped_spectrum_payload_for_spectral_cube`
        / `_scoped_formula_spectrum_task`, the same pipeline "Start
        analysis" and the live preview already use for both TIFF and
        OME-Zarr - `spectrum_read_region` (analysis_tasks.py) is the only
        place either of those cares about format, and this test doesn't
        touch that decision at all.

        `on_result(text)`/`on_error(text)` are plain callbacks, called back
        on the GUI thread by FunctionWorker's signals - not dispatched
        through QThreadPool (see gui/worker.py's FunctionWorker docstring
        for why that matters for anything touching the dataset).
        """
        from lspr_imaging_app.gui.worker import FunctionWorker

        window = self.window
        dataset = window._state.dataset
        if dataset is None:
            on_error("No dataset is loaded.")
            return
        raw_wavelengths = sorted({float(w) for w in dataset.wavelengths_nm})
        if 0.0 not in raw_wavelengths:
            on_error("This dataset has no 0 nm frame - there's nothing to test.")
            return
        if len(raw_wavelengths) < 2:
            on_error("Not enough wavelengths in this dataset to compare against.")
            return
        selected_roi_ids = window._selected_spectrum_roi_ids()
        selected_source_rois = window._selected_source_rois_snapshot()
        if not selected_roi_ids or not selected_source_rois:
            on_error("Select at least one ROI (in the ROI table) before running this test.")
            return
        spectral_cubes = self.available_analysis_spectral_cubes()
        if not spectral_cubes:
            on_error("No spectral cubes available to test.")
            return
        first_cube = int(min(spectral_cubes))

        settings_snapshot = self._spectrum_settings_snapshot()
        payload = self._prepare_scoped_spectrum_payload_for_spectral_cube(
            first_cube,
            selected_roi_ids,
            selected_source_rois,
            settings_snapshot,
            wavelength_values=raw_wavelengths,
        )
        if payload is None:
            on_error(f"Could not read spectral cube {first_cube} for the current ROI selection.")
            return

        (reduction_method,) = self._roi_reduction_signature_elements()
        formula_key = self._active_formula_key()
        formula_label = self._analysis_formula_axis_label()

        def _on_spectrum_ready(spectrum) -> None:
            impact = dark_frame_pixel_impact(
                spectrum.wavelengths_nm,
                spectrum.sample_reduced_value,
                spectrum.reference_reduced_value,
                spectrum.reduced_values_by_method,
                formula_key,
            )
            on_result(format_dark_frame_impact_result(impact, first_cube, formula_label))

        worker = FunctionWorker(
            _scoped_formula_spectrum_task,
            *payload,
            reduction_method=reduction_method,
            trimmed_mean_fraction=DEFAULT_TRIMMED_MEAN_FRACTION,
            formula_key=formula_key,
            compute_all_reduction_methods=True,
        )
        worker.signals.result.connect(_on_spectrum_ready)
        worker.signals.error.connect(on_error)
        worker.start()

    def _ensure_analysis_worker_count_calibration(self) -> None:
        """Kick off the background per-machine analysis worker-count
        calibration (see io/dataset.py's calibrate_analysis_worker_count)
        the first time "Start analysis" runs this session. A no-op if
        already done or already in flight - safe to call at the top of
        every _start_sensorgram_worker call. Non-blocking and never
        load-bearing for the run that triggers it: that run (and any other
        started before this finishes) simply uses _scoped_formula_spectrum_
        task's own os.cpu_count()-based fallback, exactly as before this
        existed - only a run started *after* this completes benefits. See
        bulk_analysis_performance_investigation.md Follow-up #11/#12.
        """
        window = self.window
        if window._analysis_worker_count_calibration_attempted or window._analysis_worker_count_calibration_pending:
            return
        window._analysis_worker_count_calibration_pending = True
        from lspr_imaging_app.gui.worker import FunctionWorker
        from lspr_imaging_app.io.dataset import calibrate_analysis_worker_count

        worker = FunctionWorker(calibrate_analysis_worker_count)
        worker.signals.result.connect(self._on_analysis_worker_count_calibration_done)
        worker.signals.error.connect(lambda _message: self._on_analysis_worker_count_calibration_done(None))
        worker.start()

    def _on_analysis_worker_count_calibration_done(self, worker_count: int | None) -> None:
        window = self.window
        window._analysis_worker_count_calibration_pending = False
        window._analysis_worker_count_calibration_attempted = True
        window._analysis_worker_count_calibration = worker_count
        if worker_count is not None:
            logging.getLogger("lspr_imaging_app.workflow").debug(
                "Analysis worker-count calibration | worker_count=%s", worker_count
            )

    def _start_sensorgram_worker(
        self,
        signature: tuple[object, ...],
        spectral_cubes: list[int],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> None:
        import time

        self._ensure_analysis_worker_count_calibration()
        self.window._sensorgram_request_id += 1
        request_id = self.window._sensorgram_request_id
        self.window._sensorgram_running = True
        self.window._sensorgram_running_signature = signature
        self.window._sensorgram_running_roi_ids = selected_roi_ids
        import threading

        from lspr_imaging_app.gui.worker import FunctionWorker
        from lspr_imaging_app.gui.analysis_tasks import (
            _sensorgram_metric_task,
            _scoped_formula_spectrum_task,
        )

        settings_snapshot = self._spectrum_settings_snapshot()
        # Read once, up front, on the main thread - the worker thread below
        # only ever sees the plain resulting dict, never touches HDF5 itself
        # (see _combined_absorbance_results_from_ram_or_disk's docstring).
        disk_formula_spectrum_trace_index = self._build_disk_formula_spectrum_trace_index(selected_source_rois)
        # Also captured once, up front: every cube in this run projects onto
        # the SAME active formula/reduction, even if the user changes either
        # control while the run is still in flight - see
        # _combined_formula_spectrum_results_from_ram_or_disk's docstring.
        active_formula_key = self._active_formula_key()
        active_reduction_method = self._active_reduction_method()
        # [λ] mode: None when the toggle is off, or when there was genuinely
        # nothing to build from (no data for the reference cube), in which
        # case the closure below transparently falls back to per-cube
        # computation.
        shared_wavelength_geometry = (
            self._build_shared_wavelength_geometry(spectral_cubes, selected_source_rois, settings_snapshot)
            if self.window._analysis_time_independent
            else None
        )
        # [λ] mode's mask counterpart - only worth building when there's a
        # geometry to warp it with AND marked-pixel exclusion is even on
        # (otherwise the fast-path payload builder never looks at a mask at
        # all, per its own exclude_marked_pixels gate).
        shared_mask_by_wavelength = (
            self._build_shared_wavelength_mask(shared_wavelength_geometry, int(spectral_cubes[0]))
            if shared_wavelength_geometry is not None
            and bool(getattr(settings_snapshot.area_roi_settings, "ignore_marked_pixels", False))
            else None
        )

        self.window._sensorgram_cancel_event = threading.Event()
        self.window._sensorgram_started_at = time.perf_counter()
        self.window._pending_sensorgram_payload = None
        self.clear_sensorgram("")
        self.window._update_analysis_control_state()
        ram_only_label = " | [RAM]" if bool(getattr(self.window, "_analysis_ram_only_backup", False)) else ""
        self.window._set_sensorgram_summary_text(
            f"{self.window._analysis_metric_label()} | Preparing {len(spectral_cubes)} spectral cubes"
            f" | Range {spectral_cubes[0]}-{spectral_cubes[-1]}{ram_only_label}"
        )
        self.window._set_status_text("Preparing spectral cube reads...")
        # show_wait_cursor=False: analysis runs entirely in the background
        # (a FunctionWorker thread) - the app itself stays fully interactive
        # while it runs, so it shouldn't look/feel frozen behind an app-wide
        # wait cursor. Progress is still visible in the status bar as usual.
        self.window._begin_busy(
            "Preparing spectral cube reads...", determinate=True, show_wait_cursor=False, total_items=len(spectral_cubes)
        )

        def spectral_cube_payload_builder(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois, settings_snapshot=settings_snapshot, shared_wavelength_geometry=shared_wavelength_geometry, shared_mask_by_wavelength=shared_mask_by_wavelength):
            return self._prepare_scoped_spectrum_payload_for_spectral_cube(
                spectral_cube_index,
                selected_roi_ids,
                selected_source_rois,
                settings_snapshot,
                shared_wavelength_geometry,
                shared_mask_by_wavelength,
            )
        task_fn = _scoped_formula_spectrum_task

        def spectral_cube_result_cache_get(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois, active_formula_key=active_formula_key):
            return self._cached_sensorgram_spectral_cube_result(
                spectral_cube_index,
                selected_roi_ids,
                selected_source_rois,
                formula_key=active_formula_key,
            )

        def spectral_cube_result_cache_store(spectral_cube_index, result, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
            self._store_sensorgram_spectral_cube_result(
                spectral_cube_index,
                selected_roi_ids,
                selected_source_rois,
                result,
            )

        # Disk-backed shortcut that skips the fit step entirely, not just the
        # spectrum read/build above - see analysis_pipeline_redesign.md \S4c
        # item 3. Distinct from spectral_cube_result_cache_get: that RAM
        # cache returns a full pre-fit AbsorbanceSpectrumResult, reusable
        # across fit-method/metric/poly-order changes; a backed-up HDF5 row
        # only ever stores the already-reduced metric_value for one exact
        # (fit method, metric, poly order) combination
        # (_sensorgram_point_signature_hash folds those into the hash), so a
        # disk hit can supply the finished answer for a fully matching
        # signature but can never repopulate that RAM spectrum cache.
        disk_metric_index: dict[int, tuple[str, float]] = {}
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is not None:
            backup_roi_id, _ = self._sensorgram_backup_roi_key(selected_roi_ids)
            if backup_roi_id:
                try:
                    disk_metric_index = writer.sensorgram_metric_index(backup_roi_id)
                except Exception:
                    logging.getLogger("lspr_imaging_app.workflow").warning(
                        "Failed to read sensorgram metric index from measurement export backup", exc_info=True
                    )
                    disk_metric_index = {}

        def metric_value_cache_get(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
            if not disk_metric_index:
                return None
            entry = disk_metric_index.get(int(spectral_cube_index))
            if entry is None:
                return None
            stored_hash, metric_value = entry
            if not stored_hash:
                return None
            live_hash = self._sensorgram_point_signature_hash(
                int(spectral_cube_index), tuple(selected_roi_ids), selected_source_rois
            )
            if live_hash and live_hash == stored_hash:
                return metric_value
            return None

        def spectral_cube_formula_spectrum_cache_get(
            spectral_cube_index,
            selected_source_rois=selected_source_rois,
            disk_formula_spectrum_trace_index=disk_formula_spectrum_trace_index,
            active_formula_key=active_formula_key,
            active_reduction_method=active_reduction_method,
        ):
            roi_results = self._combined_formula_spectrum_results_from_ram_or_disk(
                spectral_cube_index,
                selected_source_rois,
                disk_formula_spectrum_trace_index,
                formula_key=active_formula_key,
                reduction_method=active_reduction_method,
            )
            if roi_results is None:
                return None
            return self._combine_roi_formula_spectrum_results(roi_results)

        def spectral_cube_formula_spectrum_cache_store(spectral_cube_index, roi_formula_spectrum_results, selected_source_rois=selected_source_rois):
            self._store_roi_formula_spectrum_cache_for_cube(roi_formula_spectrum_results, spectral_cube_index, selected_source_rois)

        wavelength_range = self.window._analysis_wavelength_range()
        reduction_method = active_reduction_method
        formula_key = active_formula_key
        # A single-cube call (live preview, or a just-edited group member) is
        # worth computing every Reduction method for, so switching the
        # dropdown afterward stays instant - see reduce_sample_and_reference_
        # all_methods's docstring. A multi-cube "Start analysis" sweep is not:
        # paying that 4x cost (np.where's mask scan plus plane_fit's lstsq,
        # both otherwise skippable) on every ROI of every wavelength of
        # hundreds of cubes is real, measured multi-second-per-cube overhead
        # for a switch most bulk runs never make - switching Reduction after
        # the fact for an already-computed cube still works, it just re-reads
        # that one cube's pixels instead of hitting a cache.
        compute_all_reduction_methods = len(spectral_cubes) <= 1
        worker = FunctionWorker(
            _sensorgram_metric_task,
            spectral_cubes,
            self.window._analysis_poly_order(),
            self.window._analysis_metric_key(),
            cancel_event=self.window._sensorgram_cancel_event,
            supports_progress=True,
            supports_partial=True,
            spectral_cube_payload_builder=spectral_cube_payload_builder,
            task_fn=task_fn,
            spectral_cube_result_cache_get=spectral_cube_result_cache_get,
            spectral_cube_result_cache_store=spectral_cube_result_cache_store,
            metric_value_cache_get=metric_value_cache_get,
            spectral_cube_formula_spectrum_cache_get=spectral_cube_formula_spectrum_cache_get,
            spectral_cube_formula_spectrum_cache_store=spectral_cube_formula_spectrum_cache_store,
            wl_min=None if wavelength_range is None else wavelength_range[0],
            wl_max=None if wavelength_range is None else wavelength_range[1],
            fit_method_key=self._analysis_fit_method_key(),
            reduction_method=reduction_method,
            trimmed_mean_fraction=DEFAULT_TRIMMED_MEAN_FRACTION,
            formula_key=formula_key,
            compute_all_reduction_methods=compute_all_reduction_methods,
            worker_count_override=self.window._analysis_worker_count_calibration,
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.partial.connect(
            lambda point, request_id=request_id, total=len(spectral_cubes): self.on_sensorgram_partial_result(request_id, total, point)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self.on_sensorgram_ready(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self.on_sensorgram_failed(request_id, message))
        worker.start()

    @staticmethod
    def _measurement_backup_periodic_flush_due(buffered_cube_count: int, batch_size: int, ram_only: bool) -> bool:
        """Whether on_sensorgram_partial_result's periodic backup-buffer
        flush should fire now. Pure logic, split out so it's testable
        without a real window/QTimer - same reasoning as MainWindow.
        _format_busy_detail_text.

        Always False in [RAM] mode (`ram_only` - the Analysis title row's
        [disk]/[RAM] toggle, `_analysis_ram_only_backup`): buffered results
        still all reach the backup file, just not from here - on_sensorgram_
        ready/on_sensorgram_failed's own unconditional flush (which runs
        regardless of this toggle) is what writes them, once, when the run
        finishes or is stopped. This function only decides whether to flush
        *early*, mid-run.
        """
        return not ram_only and buffered_cube_count >= max(int(batch_size), 1)

    def on_sensorgram_partial_result(self, request_id: int, total_count: int, point) -> None:
        if request_id != self.window._sensorgram_request_id or not self.window._analysis_enabled:
            return
        metric_value = float("nan") if point.metric_value is None else float(point.metric_value)
        metric_signal = float("nan") if point.metric_signal is None else float(point.metric_signal)
        self.window._sensorgram_spectral_cube_indices = np.append(self.window._sensorgram_spectral_cube_indices, int(point.spectral_cube_index)).astype(np.int32, copy=False)
        self.window._sensorgram_metric_values = np.append(self.window._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self.window._sensorgram_metric_signal = np.append(self.window._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        # Coalesced, not called directly here: set_sensorgram_series redoes
        # its O(n log n) statistics-overlay recompute over the WHOLE trace
        # (see AnalysisController._update_processed_trace_overlay) on every
        # call, so calling it once per finished cube would make total
        # GUI-thread cost across a run grow roughly quadratically with cube
        # count - see _sensorgram_curve_update_timer's setup in
        # MainWindow.__init__ and _apply_pending_sensorgram_curve_update
        # below. The final, fully up-to-date redraw always still happens
        # unconditionally in _apply_cached_sensorgram_result once the run
        # completes, so nothing about the end result depends on this timer
        # actually firing for every intermediate point.
        self.window._pending_sensorgram_curve_summary_text = (
            f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_spectral_cube_indices.size}/{total_count} spectral cubes"
        )
        self.window._sensorgram_curve_update_timer.start()
        # Stage-timed unconditionally, same reasoning as _process_image_
        # task's "Image raw load" log: this backup path runs synchronously
        # on the GUI thread once per finished cube, so its cost is worth
        # being able to see directly rather than re-instrumenting each time
        # a slowdown gets reported - see docs/measurement_backup_
        # performance_and_crash_recovery.md for the investigation that
        # established what actually drives this cost (resize-operation
        # count over the backup file's lifetime, not its current size).
        backup_point_started = time.perf_counter()
        self._backup_sensorgram_point(point)
        backup_point_ms = (time.perf_counter() - backup_point_started) * 1000.0
        roi_formula_spectrum_results = getattr(point, "roi_formula_spectrum_results", None)
        backup_series_ms = 0.0
        if roi_formula_spectrum_results:
            # Unconditional - not gated by live preview: "save them in HDF5"
            # is a completeness guarantee for every cube a run touches, not
            # just the ones the user happens to watch live. The writer's own
            # dedup-by-signature-hash means an already-backed-up cube (a RAM/
            # disk cache hit here) is a cheap no-op, not a duplicate row.
            series_payloads = [
                (str(roi_id), int(roi_id), roi_result) for roi_id, roi_result in roi_formula_spectrum_results.items()
            ]
            backup_series_started = time.perf_counter()
            self._backup_formula_spectrum_series(series_payloads, cube_index=int(point.spectral_cube_index))
            backup_series_ms = (time.perf_counter() - backup_series_started) * 1000.0
        logging.getLogger("lspr_imaging_app.workflow").debug(
            "SG backup timing | cube %s | sensorgram_point=%.1fms formula_series=%.1fms (rois=%s)",
            int(point.spectral_cube_index),
            backup_point_ms,
            backup_series_ms,
            len(roi_formula_spectrum_results) if roi_formula_spectrum_results else 0,
        )
        # Flush the buffered-backup-rows batch (see _backup_formula_spectrum_
        # series/_backup_sensorgram_point) every measurement_backup_batch_size
        # cubes, so buffered data doesn't grow without bound over a long run.
        # Async (_flush_measurement_backup_buffers_async): the actual HDF5
        # write runs on a background thread, not here on the GUI thread, so
        # a periodic backup save never stalls the next cube's analysis (see
        # that method's docstring - this used to cost ~1-1.75s inline, every
        # 5th cube by default). The final partial batch at the end of a run
        # is flushed unconditionally and *synchronously* by
        # on_sensorgram_ready/on_sensorgram_failed instead of here, since a
        # run can end between multiples of the batch size - see
        # _measurement_backup_periodic_flush_due's docstring for [RAM]
        # mode's effect on this decision.
        self.window._measurement_backup_buffered_cube_count += 1
        if self._measurement_backup_periodic_flush_due(
            self.window._measurement_backup_buffered_cube_count,
            self.window._measurement_backup_batch_size(),
            bool(getattr(self.window, "_analysis_ram_only_backup", False)),
        ):
            self._flush_measurement_backup_buffers_async()
        if roi_formula_spectrum_results:
            # This cube's formula-spectrum results just landed in
            # _roi_formula_spectrum_cache (via spectral_cube_formula_spectrum_cache_store,
            # a background-thread write that - unlike _store_roi_formula_spectrum_cache -
            # doesn't itself trigger a slider refresh). Schedule one here so
            # the tick coloring updates as soon as a run finishes - it's a
            # no-op while one is still active (schedule_cube_slider_cache_
            # refresh suppresses itself then; see that method's docstring
            # for why a per-cube live refresh isn't worth its cost).
            self.schedule_cube_slider_cache_refresh()
            if self.window._analysis_live_preview_enabled:
                self.window._pending_sensorgram_live_point = point
                self.window._sensorgram_live_preview_timer.start()

    def _apply_pending_sensorgram_curve_update(self) -> None:
        """Fires on the coalescing 100ms timer started by
        `on_sensorgram_partial_result` (see its setup in
        `MainWindow.__init__`). Applies the sensorgram trace curve redraw +
        statistics-overlay recompute (`set_sensorgram_series`) at most 10x/s
        during a run instead of once per finished cube - cheap for a
        handful of cubes, but `set_sensorgram_series` redoes O(n log n) of
        work over the WHOLE trace so far on every call (see
        `_update_processed_trace_overlay`'s argsort/spike-rejection/
        smoothing), so paying it per-cube made total GUI-thread cost across
        a long run grow roughly quadratically with cube count. Safe to skip
        entirely if the run already finished by the time this fires -
        `_apply_cached_sensorgram_result` always does one more
        unconditional, fully up-to-date redraw on completion regardless."""
        if not self.window._sensorgram_running:
            return
        self.set_sensorgram_series(
            self.window._sensorgram_spectral_cube_indices,
            self.window._sensorgram_metric_values,
            summary_text=self.window._pending_sensorgram_curve_summary_text,
        )

    def _apply_pending_sensorgram_live_preview(self) -> None:
        """Fires on the coalescing 80ms timer started by
        on_sensorgram_partial_result - draws only the most recently finished
        cube's spectrum, skipping any cubes that finished and were
        overwritten as "pending" before this timer got a chance to fire.
        Distinct from `_apply_absorbance_spectrum_result`: this is a trimmed
        redraw-only path (no re-backup - the calling code already backed this
        cube up unconditionally; no fit/metric recompute - the sensorgram
        loop already has the metric) for a cube that isn't necessarily the
        one the user was looking at, labeled "Live: cube N" so it reads as a
        running preview, not the user's own selection.
        """
        point = self.window._pending_sensorgram_live_point
        self.window._pending_sensorgram_live_point = None
        if point is None or not self.window._sensorgram_running:
            return
        roi_formula_spectrum_results = getattr(point, "roi_formula_spectrum_results", None)
        if not roi_formula_spectrum_results:
            return
        spectral_cube_index = int(point.spectral_cube_index)
        window = self.window
        if spectral_cube_index in window._spectral_cube_values:
            slider_position = window._spectral_cube_values.index(spectral_cube_index)
            # blockSignals, and deliberately no _schedule_image_refresh() -
            # this only needs to move the slider's own visual position so a
            # run's progress is visible; reloading the raw image display on
            # top of that is real, avoidable I/O + processing work competing
            # with the background computation for the same CPU/GIL, on top
            # of the spectrum-panel redraw below. The image view catches up
            # to wherever the slider ended up once the run finishes and the
            # user actually interacts with it again.
            window.spectral_cube_slider.blockSignals(True)
            window.spectral_cube_slider.setValue(slider_position)
            window.spectral_cube_slider.blockSignals(False)

        series_payloads = [
            (f"ROI {int(roi_id)}", int(roi_id), roi_result) for roi_id, roi_result in sorted(roi_formula_spectrum_results.items())
        ]
        window._clear_spectrum_series_items()
        window.spectrum_current_point.setData([], [])
        window.spectrum_metric_point.setData([], [])
        x_values_all: list[np.ndarray] = []
        y_values_all: list[np.ndarray] = []
        for label, roi_id, roi_result in series_payloads:
            computed = window._compute_spectrum_series_data(roi_result)
            if computed is None:
                continue
            window._render_spectrum_series(computed, roi_id=roi_id, label=label, highlighted=False, dimmed=False)
            x_values_all.append(np.asarray(computed.x_values, dtype=np.float64))
            y_values_all.append(np.asarray(computed.y_values, dtype=np.float64))
        if not x_values_all:
            return
        x_min = min(float(np.min(values)) for values in x_values_all)
        x_max = max(float(np.max(values)) for values in x_values_all)
        y_min = min(float(np.min(values)) for values in y_values_all)
        y_max = max(float(np.max(values)) for values in y_values_all)
        y_span = max(y_max - y_min, 0.05)
        window.spectrum_plot.setXRange(x_min, x_max, padding=0.02)
        window.spectrum_plot.setYRange(y_min - y_span * 0.08, y_max + y_span * 0.12, padding=0.0)
        window._set_spectrum_summary_text(f"Live: cube {spectral_cube_index}")

    @staticmethod
    def _sensorgram_backup_roi_key(selected_roi_ids: tuple[int, ...]) -> tuple[str, str]:
        """(roi_id, combined_roi_ids) backup key for a selection. A single-ROI
        selection backs up under its real `roi_id`; a combined/grouped
        multi-ROI selection (several ROI rows selected together, averaged
        into one trace) has no single ROI to attribute the value to, so it
        backs up under a synthetic `"combined_<id>_<id>..."` key instead of
        being dropped - `combined_roi_ids` records which real ROIs it's a
        combination of (see `ImagingMeasurementExportWriter.
        set_sensorgram_metric`). Shared by the write path
        (`_backup_sensorgram_point`) and the disk-hit lookup
        (`_start_sensorgram_worker`) so both land on the identical key."""
        sorted_ids = sorted(int(roi_id) for roi_id in selected_roi_ids)
        if not sorted_ids:
            return "", ""
        if len(sorted_ids) == 1:
            return str(sorted_ids[0]), ""
        return "combined_" + "_".join(str(i) for i in sorted_ids), ",".join(str(i) for i in sorted_ids)

    def _backup_sensorgram_point(self, point) -> None:
        """Append this sensorgram point to the measurement-export/backup
        file, if one is open for the current dataset. Deduplicates by (roi_id,
        spectral_cube_index, signature_hash) so redisplaying an already-
        backed-up, still-current cube (e.g. a cache hit) doesn't append a
        second row - while a value recomputed under different settings (an
        ROI moved, a transform changed) still gets a fresh row, since its
        hash differs from whatever's already on disk for that cube.

        While a bulk "Start analysis" run is in flight (`_sensorgram_
        running`), this buffers the row in RAM instead of writing it
        immediately - see `_flush_measurement_backup_buffers` and the
        `measurement_backup_batch_size` preference. The interactive
        single-cube path (this method also runs then, with `_sensorgram_
        running` False) still writes immediately, same as always - there's
        no stream of rows to batch there.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is None:
            return
        selected_roi_ids = getattr(self.window, "_sensorgram_running_roi_ids", None) or ()
        if not selected_roi_ids:
            return
        sorted_ids = sorted(int(roi_id) for roi_id in selected_roi_ids)
        roi_id, combined_roi_ids = self._sensorgram_backup_roi_key(selected_roi_ids)
        cube_index = int(point.spectral_cube_index)
        selected_ids_set = set(sorted_ids)
        selected_source_rois = [roi for roi in self.window._state.area_rois if int(roi.area_roi_id) in selected_ids_set]
        signature_hash = self._sensorgram_point_signature_hash(cube_index, tuple(sorted_ids), selected_source_rois)
        backed_up = self.window._measurement_export_backed_up_sensorgram
        key = (roi_id, cube_index, signature_hash)
        if key in backed_up:
            return
        if point.metric_value is None:
            return
        try:
            writer.set_sensorgram_metric(
                roi_id,
                metric_name=self.window._analysis_metric_key(),
                formula_key=self._active_formula_key(),
                combined_roi_ids=combined_roi_ids,
            )
        except Exception:
            logging.getLogger("lspr_imaging_app.workflow").warning(
                "Failed to set sensorgram metric on measurement export backup", exc_info=True
            )
            return
        if bool(getattr(self.window, "_sensorgram_running", False)):
            # Timestamp resolved here (cheap - a dict lookup against
            # dataset.compact_image_timings, see AnalysisController.
            # _compact_image_timings), not at flush
            # time: the periodic flush hands buffered rows to a background
            # thread (_flush_measurement_backup_buffers_async), which must
            # not call back into any self.window method - baking the
            # already-resolved value into the tuple keeps that background
            # function pure (writer + plain data only).
            self.window._sensorgram_backup_buffer.setdefault(str(roi_id), []).append(
                (cube_index, signature_hash, float(point.metric_value), self._acquisition_timestamp_ms_for_cube(cube_index))
            )
            backed_up.add(key)
            return
        try:
            writer.append_sensorgram_point(
                roi_id,
                cube_index=cube_index,
                signature_hash=signature_hash,
                timestamp_utc_ms=self._acquisition_timestamp_ms_for_cube(cube_index),
                metric_value=float(point.metric_value),
            )
        except Exception:
            logging.getLogger("lspr_imaging_app.workflow").warning(
                "Failed to append sensorgram point to measurement export backup", exc_info=True
            )
            return
        backed_up.add(key)

    def _acquisition_timestamp_ms_for_cube(self, spectral_cube_index: int) -> int:
        """Real acquisition time for `spectral_cube_index` if the dataset
        has per-image timing metadata loaded, otherwise the current wall
        clock time as a best-effort fallback (still monotonically
        increasing across a single analysis run, just not tied to the
        original acquisition)."""
        metadata = self._sensorgram_time_mode_metadata()
        if metadata is not None:
            timing_ms = self._earliest_timing_by_cube_index(metadata).get(int(spectral_cube_index))
            if timing_ms is not None:
                return int(timing_ms)
        return int(datetime.now().timestamp() * 1000)

    def on_sensorgram_ready(self, request_id: int, result) -> None:
        # Pairs with the gc.disable() at the top of _sensorgram_metric_task
        # (analysis_tasks.py). Unconditional, before the stale-request early
        # return below: the worker task itself already finished by the time
        # this slot fires either way, so GC must come back on regardless of
        # which run's result this is. gc.enable() is a no-op if it was never
        # disabled, so this is safe even for a result that didn't come from
        # that task (e.g. a cache-hit path).
        import gc as _gc

        _gc.enable()
        _gc.collect()  # reclaim anything that piled up while GC was off, rather than leaving it for the next automatic trigger
        # Unconditional, same reasoning as gc.enable() above and before the
        # stale-request early return below: whatever's buffered (see
        # _backup_formula_spectrum_series/_backup_sensorgram_point) is real,
        # already-computed data that should never be left sitting unwritten
        # just because this particular result turned out to be superseded.
        self._flush_measurement_backup_buffers()
        if request_id != self.window._sensorgram_request_id:
            if self.window._pending_sensorgram_payload is not None:
                self.start_pending_sensorgram_refresh()
            return
        signature = self.window._sensorgram_running_signature
        self.window._sensorgram_running = False
        self.window._sensorgram_cancel_event = None
        self.window._sensorgram_running_signature = None
        self.window._sensorgram_started_at = None
        self.window._end_busy(show_wait_cursor=False)
        self.window._sync_busy_cursor_state()
        if not self.window._analysis_enabled:
            self.window._update_analysis_control_state()
            return
        # Live preview leaves the spectrum panel showing a stripped-down
        # "Live: cube N" redraw (see _apply_pending_sensorgram_live_preview -
        # no fit overlay/metric marker/proper status text). Now that the run
        # has actually finished (_sensorgram_running is already False above,
        # so _refresh_absorbance_spectrum's own guard against racing a live
        # run no longer applies), replace it with a full, normal redraw for
        # whichever cube the slider ended up on - a cheap RAM-cache hit
        # thanks to _store_roi_absorbance_cache_for_cube having already
        # populated it during the run, not a recompute.
        self.window._formula_spectrum_dirty = True
        self._refresh_formula_spectrum()
        if signature:
            self._apply_cached_sensorgram_result(signature, result, preview=False)
        else:
            self._apply_cached_sensorgram_result((), result, preview=False)
        self.window._append_workflow_log(
            f"SG {'stopped' if result.cancelled else 'done'} | {result.completed_count}/{result.total_count} spectral cubes"
            f" | prep {self.window._format_elapsed_seconds(result.prep_seconds)}"
            f" | fit {self.window._format_elapsed_seconds(result.fit_seconds)}",
            level="info",
        )
        if result.cancelled:
            self.window._set_status_text("SG | stopped")
        else:
            timing = self.window._compact_timing_text(("prep", result.prep_seconds), ("fit", result.fit_seconds))
            self.window._set_status_text(f"SG | {timing}" if timing else "SG | done")
        # A full or stopped run just populated _roi_absorbance_cache for
        # every cube it reached (see _store_roi_absorbance_cache_for_cube in
        # _sensorgram_metric_task's loop) - refresh the slider's cached-tick
        # indicator so it reflects what's now actually in RAM, same as a
        # stop does below.
        self.schedule_cube_slider_cache_refresh()
        if getattr(self, "_group_calculation_active", False):
            # A "Calculate group" run is mid-flight: this result was one
            # member's own trace, now cached under its own signature above.
            # Advance to the next member (or finish and restore the actual
            # current-selection display) instead of the normal pending-
            # refresh check below, which is for a real user-driven change.
            self._on_group_member_sensorgram_ready()
            return
        if self.window._pending_sensorgram_payload is not None:
            self.start_pending_sensorgram_refresh()

    def on_sensorgram_failed(self, request_id: int, message: str) -> None:
        # See on_sensorgram_ready's matching comment; same reasoning
        # applies to the failure path.
        import gc as _gc

        _gc.enable()
        _gc.collect()  # reclaim anything that piled up while GC was off, rather than leaving it for the next automatic trigger
        self._flush_measurement_backup_buffers()  # see on_sensorgram_ready's matching comment
        if request_id != self.window._sensorgram_request_id:
            return
        self.window._sensorgram_running = False
        self.window._sensorgram_cancel_event = None
        self.window._sensorgram_running_signature = None
        self.window._sensorgram_started_at = None
        self.window._end_busy(show_wait_cursor=False)
        self.window._sync_busy_cursor_state()
        self.window._update_analysis_control_state()
        self.window._set_sensorgram_summary_text(f"Sensorgram failed: {message}")
        self.window._background_error("Sensorgram", message)
        self.schedule_cube_slider_cache_refresh()
        if getattr(self, "_group_calculation_active", False):
            # Skip the failed member rather than stalling the queue forever;
            # it just won't be part of the aggregated band.
            self._on_group_member_sensorgram_ready()
            return
        if self.window._pending_sensorgram_payload is not None:
            self.start_pending_sensorgram_refresh()

    def start_pending_sensorgram_refresh(self) -> None:
        if self.window._pending_sensorgram_payload is None:
            return
        signature, spectral_cubes, selected_roi_ids, selected_source_rois = self.window._pending_sensorgram_payload
        self.window._pending_sensorgram_payload = None
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is not None:
            self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
            return
        self._start_sensorgram_worker(signature, list(spectral_cubes), tuple(selected_roi_ids), list(selected_source_rois))

    def update_current_point(self) -> None:
        current_spectral_cube = self.window._current_spectral_cube()
        if current_spectral_cube is None or self.window._sensorgram_spectral_cube_indices.size == 0:
            self.window.sensorgram_current_point.setData([], [])
            return
        matches = np.where(self.window._sensorgram_spectral_cube_indices == int(current_spectral_cube))[0]
        if matches.size == 0:
            self.window.sensorgram_current_point.setData([], [])
            return
        index = int(matches[-1])
        value = float(self.window._sensorgram_metric_values[index])
        if not np.isfinite(value):
            self.window.sensorgram_current_point.setData([], [])
            return
        x_value = float(self._sensorgram_x_values([int(self.window._sensorgram_spectral_cube_indices[index])])[0])
        if not np.isfinite(x_value):
            self.window.sensorgram_current_point.setData([], [])
            return
        self.window.sensorgram_current_point.setData([x_value], [value])

    def mark_stale(self, reason: str | None = None) -> None:
        if self.window._sensorgram_running:
            return
        metric_label = self.window._analysis_metric_label()
        range_text = ""
        spectral_cube_range = self.window._current_analysis_spectral_cube_range()
        if spectral_cube_range is not None:
            range_text = f" | Spectral cubes {spectral_cube_range[0]}-{spectral_cube_range[1]}"
        message = reason or f"{metric_label} sensorgram is out of date | Press Start analysis{range_text}"
        self.clear_sensorgram(message)

    def _sensorgram_prerequisite_blocked(self) -> str | None:
        """'disabled' | 'no_dataset' | 'chromatic_active' if a basic
        sensorgram/spectrum prerequisite isn't met, else None. Callers own
        their own exact message wording and failure action (clear_sensorgram
        vs. a plain False return vs. also clearing the spectrum summary) -
        this only unifies the repeated condition-checking itself, which was
        duplicated near-verbatim across three methods."""
        if not self.window._analysis_enabled:
            return "disabled"
        if self.window._state.dataset is None:
            return "no_dataset"
        if self.window._chromatic_setup_active:
            return "chromatic_active"
        return None

    def _calculate_sensorgram_for_range(self) -> None:
        blocked = self._sensorgram_prerequisite_blocked()
        if blocked == "disabled":
            self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if blocked == "no_dataset":
            self.window._clear_sensorgram("Load a dataset before calculating the sensorgram.")
            return
        if blocked == "chromatic_active":
            self.window._clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if not selected_roi_ids:
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if not selected_source_rois:
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return

        spectral_cubes = self.window._available_analysis_spectral_cubes()
        if not spectral_cubes:
            self.window._clear_sensorgram("No spectral cubes are available in the selected range.")
            return

        cached_signature = self.window._sensorgram_signature_for_selection(spectral_cubes, selected_roi_ids, selected_source_rois)
        if self.window._sensorgram_running:
            # A run is already in flight. This check must come BEFORE the
            # cache-hit lookup below, not after: a cache hit for a
            # *different* signature than the one currently running still
            # needs to be queued, not applied immediately - applying it
            # immediately would overwrite the display with a result that
            # the in-flight run's own completion (on_sensorgram_ready) is
            # about to overwrite again once it finishes, and would do so
            # while _sensorgram_running/_sensorgram_running_signature still
            # describe the OTHER run, silently desyncing what the UI shows
            # from what those flags say is actually in progress. Queueing
            # via _pending_sensorgram_payload is the same mechanism already
            # used when a setting changes mid-run; on_sensorgram_ready/
            # on_sensorgram_failed drain it once the current run settles.
            if self.window._sensorgram_running_signature == cached_signature:
                self.window._append_workflow_log("SG calc start | already running with identical settings", level="debug")
                return
            self.window._pending_sensorgram_payload = (cached_signature, spectral_cubes, selected_roi_ids, selected_source_rois)
            self.window._append_workflow_log("SG calc start | queued - a run is already in progress", level="debug")
            self.window._set_sensorgram_summary_text(
                f"{self.window._analysis_metric_label()} | Updating {len(spectral_cubes)} spectral cubes"
            )
            return
        if cached_signature is not None:
            with self.window._analysis_cache_lock:
                cached_sensorgram = self.window._sensorgram_cache.get(cached_signature)
                if cached_sensorgram is not None:
                    self.window._sensorgram_cache.move_to_end(cached_signature)
            if cached_sensorgram is not None:
                self.window._append_workflow_log(
                    f"SG cache hit | spectral_cubes {len(spectral_cubes)} | metric {self.window._analysis_metric_label()}",
                    level="debug",
                )
                self.window._append_workflow_log(
                    f"SG cache summary | payload hit {len(spectral_cubes)} build 0 | result hit 1 build 0",
                    level="debug",
                )
                self._apply_cached_sensorgram_result(cached_signature, cached_sensorgram, preview=True)
                return
        self.window._sensorgram_running_signature = cached_signature

        self.window._append_workflow_log(
            f"SG calc start | rois {len(selected_roi_ids)} | spectral_cubes {len(spectral_cubes)} | metric {self.window._analysis_metric_label()}",
            level="info",
        )
        self._start_sensorgram_worker(cached_signature, spectral_cubes, selected_roi_ids, selected_source_rois)

    def _stop_sensorgram_calculation(self) -> None:
        if not self.window._sensorgram_running or self.window._sensorgram_cancel_event is None:
            return
        completed = int(self.window._sensorgram_spectral_cube_indices.size)
        self.window._append_workflow_log(f"SG stop requested | {completed} spectral cubes completed so far", level="info")
        self.window._sensorgram_cancel_event.set()
        self.window._pending_sensorgram_payload = None
        self._set_sensorgram_summary_text("Stopping sensorgram calculation...")
        self.window._set_status_text("Stopping sensorgram calculation...")

    def _prepare_formula_spectrum_payload(
        self,
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> tuple[tuple[object, ...], tuple[object, ...], object] | None:
        """Build the (signature, payload, task_fn) for a single-spectral-cube
        spectrum calculation. Uses the exact same payload builder as the
        sensorgram loop (_prepare_scoped_spectrum_payload_for_spectral_cube)
        - a single-spectral-cube spectrum is just a one-spectral-cube
        sensorgram, so there is one payload builder and one task function,
        not a separate implementation for this case.
        """
        if self.window._state.dataset is None:
            return None
        # Both branches must hand the payload builders an already-isolated copy -
        # _selected_source_rois_snapshot() deep-copies internally, but an explicit
        # caller-supplied list might be live references into window._state.area_rois,
        # so it gets the same treatment here rather than relying on each builder to
        # deepcopy it again (removed as a redundant deepcopy-of-a-deepcopy, see
        # _prepare_scoped_spectrum_payload_for_spectral_cube).
        selected_source_rois = (
            self.window._selected_source_rois_snapshot()
            if selected_source_rois is None
            else [deepcopy(roi) for roi in selected_source_rois]
        )
        if not selected_source_rois:
            return None
        signature = self._formula_spectrum_signature_for_source_rois(selected_source_rois)
        if signature is None:
            return None
        spectral_cube_index = int(signature[0])
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        settings_snapshot = self._spectrum_settings_snapshot()

        from lspr_imaging_app.gui.analysis_tasks import _scoped_formula_spectrum_task

        payload = self._prepare_scoped_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois, settings_snapshot)
        if payload is None:
            return None
        return signature, payload, _scoped_formula_spectrum_task

    def _on_formula_spectrum_payload_ready(
        self,
        request_id: int,
        expected_signature: tuple[object, ...],
        prepared: tuple[tuple[object, ...], tuple[object, ...], object] | None,
    ) -> None:
        if request_id != self.window._formula_spectrum_prep_request_id:
            return
        self.window._formula_spectrum_prep_running = False
        if self.window._formula_spectrum_prep_started_at is not None:
            self.window._append_workflow_log(
                f"Spec prep done | {self.window._format_elapsed_seconds(time.perf_counter() - self.window._formula_spectrum_prep_started_at)}",
                level="success",
            )
        self.window._formula_spectrum_prep_started_at = None
        self.window._formula_spectrum_prep_request_signature = None
        if prepared is None:
            self.window._end_busy("Select ROIs to show absorbance spectrum.")
            return
        signature, payload, task_fn = prepared
        if signature != expected_signature:
            self.window._formula_spectrum_dirty = True
            self.window._end_busy("Select ROIs to show absorbance spectrum.")
            return
        self.window._pending_formula_spectrum_payload = (signature, payload, task_fn)
        self.window._start_pending_formula_spectrum_refresh(reuse_busy=True)

    def _on_formula_spectrum_payload_failed(self, request_id: int, message: str) -> None:
        if request_id != self.window._formula_spectrum_prep_request_id:
            return
        self.window._formula_spectrum_prep_running = False
        self.window._formula_spectrum_prep_started_at = None
        self.window._formula_spectrum_prep_request_signature = None
        self.window._end_busy()
        self.window._background_error("Spectral absorbance prep", message)

    def _refresh_formula_spectrum(self) -> None:
        start_time = time.perf_counter()
        if not self.window._analysis_enabled:
            self.window._clear_formula_spectrum()
            return
        if self.window._sensorgram_running:
            # A "Start analysis" run already owns the spectrum panel while
            # it's in progress (see on_sensorgram_partial_result/
            # _apply_pending_sensorgram_live_preview) - moving its live cube
            # slider position would otherwise re-trigger this same method via
            # the normal cube-changed path and race the run that's driving
            # it. window._absorbance_spectrum_dirty is left as-is, so a
            # refresh is picked up normally once the run finishes.
            return
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if not selected_source_rois:
            self.window._clear_formula_spectrum()
            return
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        roi_signatures = [self.window._roi_formula_spectrum_signature(roi) for roi in selected_source_rois]
        if any(signature is None for signature in roi_signatures):
            self.window._clear_formula_spectrum()
            return
        if len(selected_source_rois) == 1:
            roi_signature = roi_signatures[0]
            assert roi_signature is not None
            cached_roi_result = self.window._roi_formula_spectrum_cache.get(roi_signature)
            if cached_roi_result is not None:
                self.window._formula_spectrum_dirty = False
                self._apply_formula_spectrum_result(cached_roi_result)
                self.window._roi_formula_spectrum_cache.move_to_end(roi_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._formula_spectrum_signature_for_source_rois(selected_source_rois)
        if signature is not None:
            cached_result = self.window._cached_formula_spectrum_result_for_selection(signature, selected_roi_ids, selected_source_rois)
            if cached_result is not None:
                self.window._formula_spectrum_dirty = False
                self._apply_formula_spectrum_result(cached_result)
                spectral_cube_signature = self.window._formula_spectral_cube_signature(signature)
                if spectral_cube_signature is not None and spectral_cube_signature in self.window._formula_spectral_cube_cache:
                    self.window._formula_spectral_cube_cache.move_to_end(spectral_cube_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        missing_source_rois = [
            roi
            for roi, signature_value in zip(selected_source_rois, roi_signatures, strict=False)
            if signature_value is None or self.window._roi_formula_spectrum_cache.get(signature_value) is None
        ]
        if missing_source_rois:
            spectral_cube_index = self.window._current_spectral_cube()
            if spectral_cube_index is not None:
                # Cross-restart resume: a cube already backed up to HDF5 in a
                # previous session can skip recomputation here too, not just
                # in the "Start analysis" loop - same all-or-nothing check
                # (see _combined_absorbance_results_from_ram_or_disk), so a
                # partial hit still falls through to the background worker
                # below for the whole missing set rather than being
                # special-cased. A full hit resolves every previously-missing
                # ROI straight into the RAM cache, so re-running the combined
                # cache lookup just below picks it up as a normal cache hit -
                # no separate "apply immediately" branch needed here.
                disk_trace_index = self._build_disk_formula_spectrum_trace_index(missing_source_rois)
                if disk_trace_index and self._combined_formula_spectrum_results_from_ram_or_disk(
                    int(spectral_cube_index), missing_source_rois, disk_trace_index
                ) is not None:
                    missing_source_rois = []
                    cached_result = self._cached_formula_spectrum_result_from_roi_cache(selected_source_rois)
                    if cached_result is not None:
                        self.window._formula_spectrum_dirty = False
                        self._apply_formula_spectrum_result(cached_result)
                        elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                        self.window._set_status_text(f"Spec | cache {elapsed}")
                        return
        target_source_rois = missing_source_rois if missing_source_rois else selected_source_rois
        signature = self._formula_spectrum_signature_for_source_rois(target_source_rois)
        if signature is None:
            self.window._clear_formula_spectrum()
            return
        if self.window._formula_spectrum_running and self.window._formula_spectrum_running_signature == signature:
            return
        if (
            self.window._pending_formula_spectrum_payload is not None
            and self.window._pending_formula_spectrum_payload[0] == signature
        ):
            return
        if (
            signature in self.window._formula_spectrum_cache
        ):
            self.window._formula_spectrum_dirty = False
            self._apply_formula_spectrum_result(self.window._formula_spectrum_cache[signature])
            self.window._formula_spectrum_cache.move_to_end(signature)
            elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
            self.window._set_status_text(f"Spec | cache {elapsed}")
            return
        self.window._start_formula_spectrum_preparation(signature, target_source_rois)

    def _on_formula_spectrum_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        result: FormulaSpectrumResult,
    ) -> None:
        started_at = self.window._formula_spectrum_started_at
        self.window._formula_spectrum_started_at = None
        self.window._formula_spectrum_running = False
        self.window._formula_spectrum_running_signature = None
        self.window._end_busy()
        if request_id != self.window._formula_spectrum_request_id:
            if self.window._pending_formula_spectrum_payload is not None:
                self.window._start_pending_formula_spectrum_refresh()
            return
        self._store_in_lru_cache(self.window._formula_spectrum_cache, signature, result, self.window.FORMULA_SPECTRUM_CACHE_SIZE)
        self.window._append_workflow_log(
            f"Spec cache store | rois {len(signature[2]) if len(signature) > 2 and isinstance(signature[2], tuple) else 0}",
            level="debug",
        )
        spectral_cube_signature = self.window._formula_spectral_cube_signature(signature)
        if spectral_cube_signature is not None:
            self._store_in_lru_cache(
                self.window._formula_spectral_cube_cache, spectral_cube_signature, result,
                self.window.FORMULA_SPECTRAL_CUBE_CACHE_SIZE,
            )
            self.window._append_workflow_log("Spec spectral_cube_index cache store", level="debug")
        self._store_roi_formula_spectrum_cache(result)
        self.window._formula_spectrum_dirty = False
        fit_seconds = self._apply_formula_spectrum_result(result) or 0.0
        result.fit_seconds = float(fit_seconds)
        self.window._append_workflow_log(
            f"Spec done | load {self.window._format_elapsed_seconds(result.load_seconds)} | roi {self.window._format_elapsed_seconds(result.roi_seconds)} | fit {self.window._format_elapsed_seconds(fit_seconds)}",
            level="success",
        )
        load_timing = self.window._compact_timing_text(("load", result.load_seconds), ("roi", result.roi_seconds))
        fit_timing = self.window._format_elapsed_seconds(fit_seconds)
        status_parts = ["Spec"]
        if load_timing:
            status_parts.append(load_timing)
        if fit_timing:
            status_parts.append(f"fit {fit_timing}")
        if not load_timing and not fit_timing:
            elapsed = self.window._format_elapsed_seconds(time.perf_counter() - started_at) if started_at is not None else ""
            if elapsed:
                status_parts.append(f"t {elapsed}")
        self.window._set_status_text(" | ".join(status_parts))
        if self.window._pending_formula_spectrum_payload is not None:
            self.window._start_pending_formula_spectrum_refresh()

    def _on_formula_spectrum_failed(self, request_id: int, message: str) -> None:
        self.window._formula_spectrum_started_at = None
        self.window._formula_spectrum_running = False
        self.window._formula_spectrum_running_signature = None
        self.window._end_busy()
        if request_id == self.window._formula_spectrum_request_id:
            self.window._background_error("Spectral absorbance", message)
        if self.window._pending_formula_spectrum_payload is not None:
            self.window._start_pending_formula_spectrum_refresh()

    def _backup_formula_spectrum_series(
        self,
        series_payloads: list[tuple[str, int, FormulaSpectrumResult]],
        *,
        cube_index: int | None = None,
    ) -> None:
        """Append each per-ROI absorbance spectrum to the measurement-export/
        backup file, if one is open for the current dataset. Skips the
        "Selection" fallback entry (a combined/whole-selection result, not a
        real per-ROI trace) and deduplicates by (roi_id, spectral_cube_index,
        signature_hash) so redisplaying an already-backed-up, still-current
        cube (e.g. a cache hit) doesn't append a second row, while a value
        recomputed under different pixel-extraction settings still gets a
        fresh one - see docs/imaging_measurement_export_format.md.

        `signature_hash` (and the dedup key) use the reduction-independent
        disk signature (`_roi_disk_signature_for_cube`), not the reduction-
        inclusive one RAM keys on: a row saved once already carries every
        reduction method's values via `reduced_values_by_method` (schema
        6.7+, see `ImagingMeasurementExportWriter.append_formula_spectrum`),
        so a later run under a *different* Reduction for the same cube has
        nothing new to add - the existing row already covers it - and
        correctly dedupes as a no-op rather than appending a near-duplicate
        row that only differs in which method happened to be active.

        `cube_index`: explicit for the multi-cube "Start analysis" loop, which
        backs up a cube that isn't necessarily the one on screen. Defaults to
        the currently-displayed cube (window._current_spectral_cube()) for the
        interactive single-cube refresh path, preserving its existing behavior.

        While a bulk "Start analysis" run is in flight (`_sensorgram_
        running`), rows are buffered in RAM instead of written immediately -
        see `_flush_measurement_backup_buffers` and the `measurement_backup_
        batch_size` preference (root-caused 2026-09-02: per-write HDF5 cost
        climbs with how many times this file's ~14-datasets-per-ROI have
        EVER been resized over its lifetime, not with current file size -
        confirmed by watching write-only time climb while file size stayed
        flat, most calls being dedup no-ops). The interactive single-cube
        path (`_sensorgram_running` False here) still writes immediately.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is None:
            return
        if cube_index is None:
            cube_index = self.window._current_spectral_cube()
        if cube_index is None:
            return
        cube_index = int(cube_index)
        backed_up = self.window._measurement_export_backed_up_formula_spectrum
        buffering = bool(getattr(self.window, "_sensorgram_running", False))
        for label, roi_id, roi_result in series_payloads:
            if label == "Selection":
                continue
            roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
            signature_hash = self._signature_hash(self._roi_disk_signature_for_cube(roi, cube_index)) if roi is not None else ""
            key = (int(roi_id), cube_index, signature_hash)
            if key in backed_up:
                continue
            if buffering:
                # Timestamp resolved here, not at flush time - see the
                # matching comment in _backup_sensorgram_point; the periodic
                # flush runs this data through a background thread that must
                # not call back into self.window.
                self.window._formula_spectrum_backup_buffer.setdefault(str(roi_id), []).append(
                    (cube_index, signature_hash, roi_result, self._acquisition_timestamp_ms_for_cube(cube_index))
                )
                backed_up.add(key)
                continue
            try:
                writer.append_formula_spectrum(
                    roi_id,
                    wavelengths_nm=roi_result.wavelengths_nm,
                    formula_values=roi_result.formula_values,
                    sample_mean=roi_result.sample_reduced_value,
                    reference_mean=roi_result.reference_reduced_value,
                    cube_index=cube_index,
                    signature_hash=signature_hash,
                    timestamp_utc_ms=self._acquisition_timestamp_ms_for_cube(cube_index),
                    formula_key=roi_result.formula_key,
                    reduction_method=roi_result.reduction_method,
                    reduced_values_by_method=roi_result.reduced_values_by_method or None,
                )
            except Exception:
                logging.getLogger("lspr_imaging_app.workflow").warning(
                    "Failed to append absorbance spectrum to measurement export backup", exc_info=True
                )
                continue
            backed_up.add(key)

    def _flush_measurement_backup_buffers(self) -> None:
        """Writes out every spectra/sensorgram-point row currently buffered
        in RAM by `_backup_formula_spectrum_series`/`_backup_sensorgram_
        point` while a bulk run is in flight - via `append_formula_spectrum_
        batch`/`append_sensorgram_point_batch`, one bulk HDF5 write per ROI
        per dataset instead of one per row. Blocking: use this whenever the
        write must have actually finished before the caller proceeds -
          - unconditionally when a run ends (on_sensorgram_ready/failed),
            so the tail of a run (fewer than a full batch) isn't left
            sitting unwritten;
          - before the writer itself is closed (dataset switch/close) and
            on app close, so a graceful shutdown never loses buffered rows -
            only an actual crash mid-batch can (the deliberate, user-
            configurable trade-off this batching makes; see the
            Preferences dialog control);
          - before compacting the backup file (nothing pending should be
            left out of the copy).
        The *periodic* mid-run trigger (every `measurement_backup_batch_
        size` cubes, see on_sensorgram_partial_result) uses
        `_flush_measurement_backup_buffers_async` instead - this method
        would otherwise stall the very next cube's analysis on a
        synchronous HDF5 write (~1-1.75s measured - see docs/tiff_vs_ome_
        zarr_read_benchmark.md's Finding 3).

        Waits for any already-in-flight background flush to finish first
        (`_measurement_backup_flush_pool.waitForDone()`) - both so this
        call's own write can't race a background one on the same writer,
        and so cube ordering within each ROI's on-disk trace stays
        chronological (this call's rows are always the *newest*, so it must
        write after anything already queued, not concurrently with it).
        Safe and cheap to call when nothing is buffered.
        """
        window = self.window
        window._measurement_backup_flush_pool.waitForDone()
        writer = getattr(window, "_measurement_export_writer", None)
        formula_buffer = getattr(window, "_formula_spectrum_backup_buffer", None) or {}
        sensorgram_buffer = getattr(window, "_sensorgram_backup_buffer", None) or {}
        _write_measurement_backup_buffers(writer, formula_buffer, sensorgram_buffer)
        formula_buffer.clear()
        sensorgram_buffer.clear()
        window._measurement_backup_buffered_cube_count = 0

    def _flush_measurement_backup_buffers_async(self) -> None:
        """Non-blocking counterpart to `_flush_measurement_backup_buffers`,
        for the periodic mid-run trigger only (on_sensorgram_partial_result)
        - every other caller needs the write to have actually finished
        before it proceeds and must keep calling the synchronous version.

        Swaps the current buffer dicts for fresh empty ones - a plain
        attribute reassignment, not a lock; the GUI thread never blocks on
        anything here, and `_backup_sensorgram_point`/`_backup_formula_
        spectrum_series` (which only ever *append* to whatever dict is
        currently installed) can keep buffering the next cube into the new
        one immediately - then hands the swapped-out data to
        `_write_measurement_backup_buffers` running on a dedicated
        single-worker QThreadPool (`_measurement_backup_flush_pool`), so a
        periodic backup save can never again stall the next cube's analysis
        the way it did running inline on the GUI thread. `waitForDone()` on
        that same pool (called by the synchronous flush before run end,
        dataset switch, or app close) is what guarantees this background
        write has actually landed before anything relies on it being there.
        """
        window = self.window
        writer = getattr(window, "_measurement_export_writer", None)
        formula_buffer = getattr(window, "_formula_spectrum_backup_buffer", None)
        sensorgram_buffer = getattr(window, "_sensorgram_backup_buffer", None)
        if writer is None or not (formula_buffer or sensorgram_buffer):
            window._measurement_backup_buffered_cube_count = 0
            return
        window._formula_spectrum_backup_buffer = {}
        window._sensorgram_backup_buffer = {}
        window._measurement_backup_buffered_cube_count = 0

        from lspr_imaging_app.gui.worker import FunctionWorker

        worker = FunctionWorker(_write_measurement_backup_buffers, writer, formula_buffer, sensorgram_buffer)
        worker.signals.error.connect(
            lambda message: logging.getLogger("lspr_imaging_app.workflow").warning(
                "Background measurement backup flush failed: %s", message
            )
        )
        window._measurement_backup_flush_pool.start(worker)

    def export_results(self) -> None:
        """"Export Results..." button (Results / Export panel): saves a
        point-in-time snapshot of everything backed up so far this session -
        ROI definitions, per-ROI absorbance spectra, and sensorgram traces -
        to a file the user chooses. This exports what `_backup_absorbance_
        series`/`_backup_sensorgram_point` have already recorded into the
        live `analysis/measurement_backup.h5`, not a fresh recomputation
        across every ROI/cube - so spectra only cover cubes actually viewed,
        and sensorgram only covers metrics actually calculated, this
        session.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is None:
            self.window._set_status_text("No dataset loaded - nothing to export yet.")
            return
        dataset = self.window._state.dataset
        dataset_name = dataset.home.name if dataset is not None else "results"
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_path = writer.path.parent / f"{dataset_name}_results_{stamp}.h5"
        path_str, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export analyzed results (spectra + sensorgram)",
            str(default_path),
            "HDF5 Files (*.h5 *.hdf5)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() not in (".h5", ".hdf5"):
            path = path.with_suffix(".h5")
        try:
            writer.export_snapshot(path)
        except Exception as exc:
            logging.getLogger("lspr_imaging_app.workflow").warning(
                "Failed to export analyzed results", exc_info=True
            )
            QMessageBox.warning(self.window, "Export failed", f"Could not write export file:\n{exc}")
            return
        self.window._set_status_text(f"Exported analyzed results to {path.name}.")

    def open_results_export_folder(self) -> None:
        """Icon button next to "Export Results...": opens the folder that
        button's save dialog defaults to (the dataset's `analysis` sidecar
        folder, see MainWindow._analysis_root) in File Explorer, mirroring
        DatasetController.open_dataset_folder_in_explorer /
        WorkflowLogController.open_logs_folder. Exports can be redirected
        elsewhere via that dialog, so this is only the default location,
        not necessarily every export ever made.
        """
        folder = self.window._analysis_root()
        if folder is None:
            self.window._set_status_text("Cannot open exports folder - no dataset loaded yet.")
            return
        if not folder.is_dir():
            self.window._set_status_text(f"Cannot open exports folder - path does not exist: {folder}")
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self.window._set_status_text(f"Could not open exports folder in File Explorer: {exc}")

    def compact_measurement_backup(self) -> None:
        """"Compact backup file" button (Results/Export panel): rewrites
        measurement_backup.h5 in place to reset the HDF5 per-write cost a
        long analysis session accumulates - see ImagingMeasurementExport
        Writer.compact's docstring for the mechanism (root-caused
        2026-09-02: write time climbs with how many times a dataset has
        EVER been resized over the file's lifetime, not with current file
        size). A maintenance action triggered deliberately by the user,
        not run automatically - runs on the GUI thread and blocks briefly
        (the file is fully rewritten), same as any other synchronous
        Qt-slot action in this app; safe to run mid-analysis (any live
        partial-result updates just queue up for the moment it takes).

        Flushes any RAM-buffered-but-not-yet-written rows first (see
        _flush_measurement_backup_buffers) so nothing pending is left out
        of the copy.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is None:
            self.window._set_status_text("Cannot compact backup file - no dataset loaded yet.")
            return
        self._flush_measurement_backup_buffers()
        self.window._set_status_text("Compacting measurement backup file...")
        started = time.perf_counter()
        try:
            size_before, size_after = writer.compact()
        except Exception as exc:
            logging.getLogger("lspr_imaging_app.workflow").warning(
                "Failed to compact measurement export backup", exc_info=True
            )
            self.window._set_status_text(f"Failed to compact backup file: {exc}")
            return
        elapsed = time.perf_counter() - started
        before_mb = size_before / (1024.0 * 1024.0)
        after_mb = size_after / (1024.0 * 1024.0)
        self.window._append_workflow_log(
            f"Measurement backup compacted | {before_mb:.1f}MB -> {after_mb:.1f}MB | {elapsed:.1f}s",
            level="info",
        )
        self.window._set_status_text(f"Compacted backup file: {before_mb:.1f}MB -> {after_mb:.1f}MB ({elapsed:.1f}s)")

    def _compute_formula_spectrum_result(self, result: FormulaSpectrumResult) -> FormulaSpectrumRenderBundle | None:
        """Everything about applying one formula-spectrum result except the
        final widget updates - see `_render_formula_spectrum_result` for
        those, and `_apply_formula_spectrum_result` for the pair wired
        together. Draws each ROI's series via `window._add_spectrum_series`
        (still fused compute+render itself, see plot_manager.py) as a side
        effect, same as before this split - only the axis-range/metric/
        current-point/text finalization at the end is deferred to a
        FormulaSpectrumRenderBundle for the render step to apply. Returns
        None when there's no valid data (mirrors the original method's single
        early-return branch - no other error/deferred case reaches this far,
        those are all handled by the caller in `_refresh_formula_spectrum`
        before this is ever called)."""
        fit_started = time.perf_counter()
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        series_payloads: list[tuple[str, int, FormulaSpectrumResult]] = []
        if result.area_roi_results:
            if selected_roi_ids:
                for roi_id in selected_roi_ids:
                    roi_result = result.area_roi_results.get(int(roi_id))
                    if roi_result is not None:
                        series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), roi_result))
            else:
                for roi_id in sorted(result.area_roi_results):
                    series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), result.area_roi_results[int(roi_id)]))
        if selected_roi_ids and len(series_payloads) < len(selected_roi_ids):
            existing_ids = {int(roi_id) for _, roi_id, _ in series_payloads}
            for roi_id in selected_roi_ids:
                if int(roi_id) in existing_ids:
                    continue
                roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
                if roi is None:
                    continue
                roi_signature = self.window._roi_formula_spectrum_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._roi_formula_spectrum_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), cached_result))
        if not series_payloads and len(selected_roi_ids) > 1:
            for roi_id in selected_roi_ids:
                roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
                if roi is None:
                    continue
                roi_signature = self.window._roi_formula_spectrum_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._roi_formula_spectrum_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), cached_result))
        if not series_payloads:
            fallback_id = int(selected_roi_ids[0]) if selected_roi_ids else 0
            series_payloads = [("Selection", fallback_id, result)]
        highlighted_ids = set(selected_roi_ids)
        self._backup_formula_spectrum_series(series_payloads)

        self.window._clear_spectrum_series_items()
        self.window.spectrum_current_point.setData([], [])
        self.window.spectrum_metric_point.setData([], [])

        x_values_all: list[np.ndarray] = []
        y_values_all: list[np.ndarray] = []
        primary_result = series_payloads[0][2]
        # The primary series' computed fit is kept so the metric/current-point
        # block below can reuse it instead of calling
        # _analysis_fit_result_from_spectrum(primary_result) a second,
        # independent time - see compute_spectrum_series_data's docstring
        # (plot_manager.py) for why that used to be computed twice.
        primary_computed = None
        for label, roi_id, roi_result in series_payloads:
            computed = self.window._compute_spectrum_series_data(roi_result)
            if computed is None:
                continue
            if roi_result is primary_result:
                primary_computed = computed
            self.window._render_spectrum_series(
                computed,
                roi_id=roi_id,
                label=label,
                highlighted=bool(highlighted_ids) and int(roi_id) in highlighted_ids,
                dimmed=len(series_payloads) > 1 and bool(highlighted_ids),
            )
            x_values_all.append(np.asarray(computed.x_values, dtype=np.float64))
            y_values_all.append(np.asarray(computed.y_values, dtype=np.float64))

        if not x_values_all:
            return None

        # Axis range is driven by the real spectrum points only - never by
        # the fitted curve. A fit (esp. a higher-order polynomial through
        # sparse/noisy points) can swing far from the data between sample
        # points (Runge's phenomenon); letting it into the axis range let a
        # single bad fit collapse the real data to an invisible sliver. The
        # fit line is still drawn and may simply run off-screen if it does
        # this, which is itself a fair cue that the fit is poorly conditioned.
        x_min = min(float(np.min(values)) for values in x_values_all)
        x_max = max(float(np.max(values)) for values in x_values_all)
        y_min = min(float(np.min(values)) for values in y_values_all)
        y_max = max(float(np.max(values)) for values in y_values_all)

        metric_value = None
        metric_signal = None
        current_text = ""
        fit_text = ""
        current_x: float | None = None
        current_y: float | None = None
        if len(series_payloads) == 1:
            # Reuses the fit already computed for the plotted curve above
            # (primary_computed.fit) instead of calling
            # _analysis_fit_result_from_spectrum(primary_result) again -
            # primary_computed is guaranteed set here, since len==1 and
            # x_values_all being non-empty (checked above) means the loop's
            # one iteration succeeded and was primary_result's own.
            fit = primary_computed.fit if primary_computed is not None else None
            if fit is not None:
                metric_value, metric_signal = metric_value_from_fit(fit, self.window._analysis_metric_key())
            elif self._analysis_fit_method_key() == "none":
                wavelength_range = self._analysis_wavelength_range()
                metric_value, metric_signal = metric_value_from_spectrum(
                    primary_result.wavelengths_nm,
                    primary_result.formula_values,
                    self.window._analysis_metric_key(),
                    wl_min=None if wavelength_range is None else wavelength_range[0],
                    wl_max=None if wavelength_range is None else wavelength_range[1],
                )
            current_wavelength = self.window._current_wavelength()
            current_point_index = None
            if current_wavelength is not None:
                current_point_index = next(
                    (
                        index
                        for index, wavelength_nm in enumerate(primary_result.wavelengths_nm)
                        if abs(float(wavelength_nm) - float(current_wavelength)) < 1e-6
                        and np.isfinite(primary_result.formula_values[index])
                    ),
                    None,
                )
            if current_point_index is not None:
                current_x = float(primary_result.wavelengths_nm[current_point_index])
                current_y = float(primary_result.formula_values[current_point_index])
                current_sample_reduced_value = float(primary_result.sample_reduced_value[current_point_index])
                current_reference_reduced_value = float(primary_result.reference_reduced_value[current_point_index])
                current_text = (
                    f" | A({current_x:g} nm) = {current_y:.4f}"
                    f" | sample {current_sample_reduced_value:.1f}, reference {current_reference_reduced_value:.1f}"
                )
            if metric_value is not None and np.isfinite(metric_value):
                fit_text = f" | {self.window._analysis_metric_label()} {float(metric_value):.3f} nm"
                if fit is not None:
                    fit_text += f" | Poly {self.window._analysis_poly_order()}"
        else:
            fit_text = f" | {len(series_payloads)} ROI series"
        fit_seconds = time.perf_counter() - fit_started
        self.window._last_formula_spectrum_fit_seconds = fit_seconds

        spectral_cube_index = self.window._current_spectral_cube()
        cube_display = spectral_cube_index if spectral_cube_index is not None else "-"
        sample_pixels = int(np.nanmax(primary_result.sample_pixel_count)) if primary_result.sample_pixel_count.size else 0
        reference_pixels = int(np.nanmax(primary_result.reference_pixel_count)) if primary_result.reference_pixel_count.size else 0
        roi_count = len(self.window._state.area_rois)
        group_count = len(self.window._state.area_roi_groups)
        cube_axis_label = self.window._spectral_cube_axis_label()
        basic_text = f"ROI: {roi_count}, Groups: {group_count}, {cube_axis_label}: {cube_display}"
        detail_tooltip = (
            f"{self.window._spectrum_selection_label()} | Spectral cube {cube_display}"
            f" | ROI px: sample {sample_pixels}, reference {reference_pixels}{current_text}{fit_text}"
        )

        return FormulaSpectrumRenderBundle(
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            metric_value=metric_value,
            metric_signal=metric_signal,
            current_x=current_x,
            current_y=current_y,
            basic_text=basic_text,
            detail_tooltip=detail_tooltip,
            fit_seconds=fit_seconds,
        )

    def _render_formula_spectrum_result(self, bundle: FormulaSpectrumRenderBundle) -> float | None:
        """Applies a `FormulaSpectrumRenderBundle` (see
        `_compute_formula_spectrum_result`) to the spectrum plot/labels -
        the only Qt-widget-touching half of the former single
        `_apply_formula_spectrum_result` method."""
        y_span = max(bundle.y_max - bundle.y_min, 0.05)
        self.window.spectrum_plot.setXRange(bundle.x_min, bundle.x_max, padding=0.02)
        self.window.spectrum_plot.setYRange(bundle.y_min - y_span * 0.08, bundle.y_max + y_span * 0.12, padding=0.0)
        if (
            bundle.metric_value is not None
            and bundle.metric_signal is not None
            and np.isfinite(bundle.metric_value)
            and np.isfinite(bundle.metric_signal)
        ):
            self.window.spectrum_metric_point.setData([float(bundle.metric_value)], [float(bundle.metric_signal)])
        else:
            self.window.spectrum_metric_point.setData([], [])
        if bundle.current_x is None or bundle.current_y is None:
            self.window.spectrum_current_point.setData([], [])
        else:
            self.window.spectrum_current_point.setData([bundle.current_x], [bundle.current_y])
        self.window._set_spectrum_summary_text(bundle.basic_text, bundle.detail_tooltip)
        self.window._update_single_spectral_cube_sensorgram(bundle.metric_value, bundle.metric_signal)
        return bundle.fit_seconds

    def _apply_formula_spectrum_result(self, result: FormulaSpectrumResult) -> float | None:
        # Single choke point for every interactive display/metric-readout path
        # (cache hit, disk resume, or fresh compute all funnel through here) -
        # re-express onto the currently-active formula. A no-op (returns
        # `result` unchanged) when it's already the active formula, so this is
        # always safe/cheap even when the caller already projected. See
        # processing/analysis.py's project_formula_spectrum.
        result = project_formula_spectrum(result, self._active_formula_key())
        bundle = self._compute_formula_spectrum_result(result)
        if bundle is None:
            self.window._set_spectrum_summary_text(f"{self.window._spectrum_selection_label()} | No valid spectrum values")
            return None
        return self._render_formula_spectrum_result(bundle)

    def _start_formula_spectrum_preparation(
        self,
        signature: tuple[object, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> None:
        from PyQt6.QtWidgets import QApplication
        from lspr_imaging_app.gui.worker import FunctionWorker

        if self.window._formula_spectrum_prep_running:
            return
        self.window._formula_spectrum_prep_request_id += 1
        request_id = self.window._formula_spectrum_prep_request_id
        self.window._formula_spectrum_prep_running = True
        self.window._formula_spectrum_prep_request_signature = signature
        self.window._formula_spectrum_prep_started_at = time.perf_counter()
        self.window._append_workflow_log("Spec prep start", level="info")
        self.window._begin_busy("Preparing absorbance spectrum...", determinate=False)
        QApplication.processEvents()
        worker = FunctionWorker(self._prepare_formula_spectrum_payload, selected_source_rois)
        worker.signals.result.connect(
            lambda prepared, request_id=request_id, signature=signature: self._on_formula_spectrum_payload_ready(
                request_id,
                signature,
                prepared,
            )
        )
        worker.signals.error.connect(
            lambda message, request_id=request_id: self._on_formula_spectrum_payload_failed(request_id, message)
        )
        worker.start()

    def _start_pending_formula_spectrum_refresh(self, *, reuse_busy: bool = False) -> None:
        from lspr_imaging_app.gui.worker import FunctionWorker

        if self.window._pending_formula_spectrum_payload is None:
            return
        signature, payload, task_fn = self.window._pending_formula_spectrum_payload
        self.window._pending_formula_spectrum_payload = None
        request_id = self.window._formula_spectrum_request_id + 1
        self.window._formula_spectrum_request_id = request_id
        self.window._formula_spectrum_running = True
        self.window._formula_spectrum_running_signature = signature
        self.window._formula_spectrum_started_at = time.perf_counter()
        if reuse_busy:
            self.window._busy_started_at = time.perf_counter()
            self.window._busy_is_determinate = True
            self.window._busy_last_percent = 0
            self.window._status_bar_busy.setRange(0, 100)
            self.window._status_bar_busy.setValue(0)
            self.window._status_bar_busy.setTextVisible(True)
            self.window._status_bar_busy.show()
            self.window._status_bar_busy_detail.setText("0:00 | ETA --:-- | 0%")
            self.window._status_bar_busy_detail.show()
            self.window._set_status_text("Updating absorbance spectrum...")
        else:
            self.window._begin_busy("Updating absorbance spectrum...", determinate=True)
        (reduction_method,) = self._roi_reduction_signature_elements()
        worker = FunctionWorker(
            task_fn,
            *payload,
            supports_progress=True,
            reduction_method=reduction_method,
            trimmed_mean_fraction=DEFAULT_TRIMMED_MEAN_FRACTION,
            formula_key=self._active_formula_key(),
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.result.connect(
            lambda result,
            request_id=request_id,
            signature=signature: self._on_formula_spectrum_ready(request_id, signature, result)
        )
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_formula_spectrum_failed(request_id, message))
        worker.start()

    def _refresh_visible_spectrum_from_cache(self) -> bool:
        if not self.window._analysis_enabled:
            return False
        selected_source_rois = self._selected_source_rois_snapshot()
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        roi_signature_single = None
        if len(selected_source_rois) == 1:
            roi_signature_single = self._roi_formula_spectrum_signature(selected_source_rois[0])
            if roi_signature_single is not None:
                cached_roi_result = self.window._roi_formula_spectrum_cache.get(roi_signature_single)
                if cached_roi_result is not None:
                    self._apply_formula_spectrum_result(cached_roi_result)
                    self.window._roi_formula_spectrum_cache.move_to_end(roi_signature_single)
                    self.window._append_workflow_log("Spec repaint | roi cache", level="debug")
                    return True
        signature = self._formula_spectrum_signature()
        if signature is None:
            return False
        if not selected_source_rois:
            cached_result = self.window._formula_spectrum_cache.get(signature)
            if cached_result is not None:
                self._apply_formula_spectrum_result(cached_result)
                spectral_cube_signature = self._formula_spectral_cube_signature(signature)
                if spectral_cube_signature is not None and spectral_cube_signature in self.window._formula_spectral_cube_cache:
                    self.window._formula_spectral_cube_cache.move_to_end(spectral_cube_signature)
                self.window._append_workflow_log("Spec repaint | spectrum cache", level="debug")
                return True
            return False
        cached_result = self._cached_formula_spectrum_result_for_selection(signature, selected_roi_ids)
        if cached_result is not None:
            self._apply_formula_spectrum_result(cached_result)
            spectral_cube_signature = self._formula_spectral_cube_signature(signature)
            if spectral_cube_signature is not None and spectral_cube_signature in self.window._formula_spectral_cube_cache:
                self.window._formula_spectral_cube_cache.move_to_end(spectral_cube_signature)
            self.window._append_workflow_log("Spec repaint | spectrum cache", level="debug")
            return True
        return False

    @staticmethod
    def _store_in_lru_cache(cache, key, value, max_size: int, *, lock=None) -> None:
        """Insert/refresh `key` in `cache` (an OrderedDict), then evict the
        oldest entries over `max_size`. This exact 3-step shape (assign,
        move_to_end, evict-while-over-capacity) was duplicated across most
        of this file's cache-store call sites - factored out once here.

        `lock`, if given, wraps the whole insert+evict sequence (for caches
        also touched from a background worker thread); omit it for caches
        that are only ever touched from the GUI thread. This must be
        decided per call site to match what that specific cache's other
        readers/writers already assume - never default one or the other.
        """
        def _do() -> None:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > max_size:
                cache.popitem(last=False)

        if lock is not None:
            with lock:
                _do()
        else:
            _do()

    def _store_roi_formula_spectrum_cache(self, result) -> None:
        area_roi_results = getattr(result, "area_roi_results", None)
        if not area_roi_results:
            return
        area_roi_by_id = {int(area_roi.area_roi_id): area_roi for area_roi in self.window._state.area_rois}
        spectral_cube_index = self.window._current_spectral_cube()
        for area_roi_id, roi_result in area_roi_results.items():
            area_roi = area_roi_by_id.get(int(area_roi_id))
            if area_roi is None:
                continue
            signature = self.window._roi_formula_spectrum_signature(area_roi)
            if signature is None:
                continue
            self._store_in_lru_cache(self.window._roi_formula_spectrum_cache, signature, roi_result, self.window.ROI_FORMULA_SPECTRUM_CACHE_SIZE)
            if spectral_cube_index is not None:
                self._write_through_reduced_values_by_method(area_roi, int(spectral_cube_index), roi_result)
        self._refresh_cached_roi_ids_snapshot()

    def _write_through_reduced_values_by_method(
        self,
        roi: AreaRoi,
        spectral_cube_index: int,
        result: FormulaSpectrumResult,
        *,
        lock=None,
    ) -> None:
        """Right after a fresh per-ROI reduction result is cached under its
        own (pixel-extraction + active reduction_method) signature, also
        derive and cache the other three Reduction methods' results for the
        SAME cube/ROI via `project_reduction_result` - purely from `result.
        reduced_values_by_method`, no pixel access - each stored under ITS
        OWN signature (see `_roi_formula_spectrum_signature_for_cube`'s
        `reduction_method_override`).

        This is what makes switching Reduction (mean/median/trimmed_mean/
        plane_fit) an ordinary, unmodified cache hit through the existing
        exact-signature lookups everywhere else in this file, instead of
        needing any new read-time miss-handling: the other three slots are
        simply already there by the time the user asks for them. A cube
        that's never been visited under ANY reduction method still misses
        everywhere and falls through to a real recompute, exactly as before
        this existed - same for a disk-resumed result, whose `reduced_
        values_by_method` is empty (only one method's means are ever
        persisted), so `project_reduction_result` correctly yields nothing
        to write through here."""
        active_method = str(result.reduction_method).strip().lower()
        for method in REDUCTION_METHODS:
            if method == active_method:
                continue
            projected = project_reduction_result(result, method, str(result.formula_key))
            if projected is None:
                continue
            signature = self._roi_formula_spectrum_signature_for_cube(roi, spectral_cube_index, reduction_method_override=method)
            if signature is None:
                continue
            self._store_in_lru_cache(
                self.window._roi_formula_spectrum_cache, signature, projected, self.window.ROI_FORMULA_SPECTRUM_CACHE_SIZE,
                lock=lock,
            )

    def _ensure_disk_formula_spectrum_trace_cached(self, rois: list[AreaRoi]) -> dict[int, FormulaSpectrumTraceIndex]:
        """Lazily fills `window._formula_spectrum_disk_trace_cache` (per ROI
        id, kept for the life of the loaded dataset - cleared alongside the
        RAM cache in `_invalidate_formula_spectrum_cache`) so
        `_refresh_cube_slider_cache_indicators` can answer "was this cube
        ever calculated and saved", not just "is it still warm in the RAM
        cache" - the RAM cache is LRU-capped (`ROI_FORMULA_SPECTRUM_CACHE_SIZE`)
        and reset every app restart, but the HDF5 export backup is the
        permanent record.

        Only a real disk hit gets cached - a `None` result (nothing saved
        yet) is deliberately NOT cached, so a ROI that's mid-run in this
        session naturally picks up its first real disk entry on a later call
        instead of being stuck answering "nothing on disk" from an earlier,
        now-stale check (a same-session gap is harmless regardless, since the
        RAM cache already covers anything computed this session - see
        `_refresh_cube_slider_cache_indicators`). Once a real hit is cached,
        later calls for that ROI are a free dict lookup - `formula_spectrum_
        index` reads the ROI's full per-cube trace, which is the expensive
        part this avoids repeating; a ROI with nothing on disk is a cheap
        early-return every time, so calling this on every debounced slider
        refresh (including the frequent per-cube ones during a live run) is
        fine.

        Must only run on the main thread (see `_build_disk_formula_spectrum_
        trace_index` below); returns a shallow snapshot safe to hand to a
        background worker.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is not None:
            for roi in rois:
                roi_id = int(roi.area_roi_id)
                if roi_id in self.window._formula_spectrum_disk_trace_cache:
                    continue
                try:
                    trace = writer.formula_spectrum_index(roi_id)
                except Exception:
                    logging.getLogger("lspr_imaging_app.workflow").warning(
                        "Failed to read absorbance spectrum index from measurement export backup", exc_info=True
                    )
                    continue
                if trace is not None:
                    self.window._formula_spectrum_disk_trace_cache[roi_id] = trace
        return dict(self.window._formula_spectrum_disk_trace_cache)

    def _formula_spectrum_signature_saved_on_disk(
        self,
        roi: AreaRoi,
        spectral_cube_index: int,
        signature: tuple[object, ...],
        disk_trace_cache: dict[int, FormulaSpectrumTraceIndex],
    ) -> bool:
        """Boolean-only counterpart of `_formula_spectrum_result_from_disk_row`
        for the slider's cached-tick check: same hash-validity rule (including
        the pre-6.7 fallback below), but skips materializing the full
        `FormulaSpectrumResult` (wavelength/formula/mean arrays) since the
        tick indicator only needs a yes/no per cube."""
        trace = disk_trace_cache.get(int(roi.area_roi_id))
        if trace is None:
            return False
        entry = trace.by_cube.get(int(spectral_cube_index))
        if entry is None:
            return False
        stored_hash = entry[0]
        if not stored_hash:
            return False
        if stored_hash == self._signature_hash(signature):
            return True
        return self._formula_spectrum_signature_matches_legacy_hash(roi, spectral_cube_index, stored_hash, trace)

    def _formula_spectrum_signature_matches_legacy_hash(
        self, roi: AreaRoi, spectral_cube_index: int, stored_hash: str, trace: FormulaSpectrumTraceIndex
    ) -> bool:
        """Pre-schema-6.7 rows had their `signature_hash` computed with the
        ACTUAL reduction method baked in (e.g. "mean"), not today's
        reduction-independent placeholder (see `_roi_disk_signature_for_cube`)
        - a row written before that migration will never match a hash built
        the new way, and would otherwise be permanently misread as "never
        calculated" even though it's sitting right there on disk. Reconstruct
        the older-style signature using the reduction method this row was
        actually recorded under (`trace.reduction_method`, the group-level
        attr `append_formula_spectrum` stamps on every write) - this is
        exactly the same signature `_persist_formula_spectrum`'s dedup check
        computed before the migration, just evaluated against today's live
        settings (ROI geometry, wavelengths, chromatic state, exclusions),
        so it still correctly rejects a row that's genuinely stale under any
        of those, not only ones from a reduction-method change."""
        legacy_signature = self._roi_formula_spectrum_signature_for_cube(
            roi, spectral_cube_index, reduction_method_override=trace.reduction_method
        )
        return legacy_signature is not None and stored_hash == self._signature_hash(legacy_signature)

    def _build_disk_formula_spectrum_trace_index(self, rois: list[AreaRoi]) -> dict[int, FormulaSpectrumTraceIndex]:
        """One HDF5 read per selected ROI, off the writer's already-open
        handle (safe to call while it's still appending elsewhere - see
        ImagingMeasurementExportWriter.formula_spectrum_index). Must only
        ever be called from the main thread; callers hand the resulting plain
        dict into background workers rather than letting them touch the
        writer directly.
        """
        writer = getattr(self.window, "_measurement_export_writer", None)
        if writer is None:
            return {}
        index: dict[int, FormulaSpectrumTraceIndex] = {}
        for roi in rois:
            roi_id = int(roi.area_roi_id)
            try:
                trace = writer.formula_spectrum_index(roi_id)
            except Exception:
                logging.getLogger("lspr_imaging_app.workflow").warning(
                    "Failed to read absorbance spectrum index from measurement export backup", exc_info=True
                )
                continue
            if trace is not None:
                index[roi_id] = trace
        return index

    def _formula_spectrum_result_from_disk_row(
        self,
        roi: AreaRoi,
        spectral_cube_index: int,
        signature: tuple[object, ...],
        disk_trace_index: dict[int, FormulaSpectrumTraceIndex] | None,
    ) -> FormulaSpectrumResult | None:
        """`signature` must be the reduction-independent disk signature (see
        `_roi_disk_signature_for_cube`) - the row's validity no longer
        depends on which reduction method happened to be active when it was
        written, since (schema 6.7+) it can carry more than one. Rows
        written BEFORE that migration are also accepted via a fallback to
        the older, reduction-inclusive hash - see
        `_formula_spectrum_signature_matches_legacy_hash`.

        Builds the result under a baseline reduction method (whichever the
        trace says was last active, or any available one as a fallback) and
        carries the row's *entire* `reduced_values_by_method` dict onto the
        returned result - the caller projects onto whichever reduction/
        formula is actually wanted afterward (see `project_reduction_result`
        in `_combined_formula_spectrum_results_from_ram_or_disk`), exactly
        the same "build a baseline, project after" pattern already used for
        formula."""
        if not disk_trace_index:
            return None
        trace = disk_trace_index.get(int(roi.area_roi_id))
        if trace is None:
            return None
        entry = trace.by_cube.get(int(spectral_cube_index))
        if entry is None:
            return None
        stored_hash, reduced_values_by_method = entry
        if not stored_hash or not reduced_values_by_method:
            return None
        if stored_hash != self._signature_hash(signature) and not self._formula_spectrum_signature_matches_legacy_hash(
            roi, spectral_cube_index, stored_hash, trace
        ):
            return None
        baseline_method = trace.reduction_method if trace.reduction_method in reduced_values_by_method else next(
            iter(reduced_values_by_method)
        )
        sample_reduced_value, reference_reduced_value = reduced_values_by_method[baseline_method]
        # sample_pixel_count/reference_pixel_count aren't persisted to the
        # HDF5 backup today (only the reduced values are) - a disk-resumed
        # result shows 0px in the spectrum tooltip until this cube is
        # recomputed fresh. Accepted gap, not a bug: these counts reflect
        # exclusion masks that can change over time, so they aren't
        # derivable from ROI geometry alone, and nothing besides that
        # tooltip reads them.
        return FormulaSpectrumResult(
            wavelengths_nm=trace.wavelengths_nm,
            formula_values=formula_values_from_reduced_values(sample_reduced_value, reference_reduced_value, trace.formula_key),
            sample_reduced_value=sample_reduced_value,
            reference_reduced_value=reference_reduced_value,
            sample_pixel_count=np.asarray([], dtype=np.int32),
            reference_pixel_count=np.asarray([], dtype=np.int32),
            reduction_method=baseline_method,
            formula_key=trace.formula_key,
            reduced_values_by_method=reduced_values_by_method,
        )

    def _combined_formula_spectrum_results_from_ram_or_disk(
        self,
        spectral_cube_index: int,
        selected_source_rois: list[AreaRoi],
        disk_trace_index: dict[int, FormulaSpectrumTraceIndex] | None = None,
        formula_key: str | None = None,
        reduction_method: str | None = None,
    ) -> dict[int, FormulaSpectrumResult] | None:
        """All-or-nothing: returns the full per-ROI results dict only if
        EVERY selected ROI already has a valid result for this cube in RAM or
        on disk (populating the RAM cache from any disk hits along the way);
        None if even one ROI is still missing, meaning the caller must fall
        back to a full compute for this cube. Deliberately all-or-nothing
        rather than computing just the missing subset: the underlying task
        functions already compute every selected ROI together in one call, so
        a partial hit still needs that same one full call for the ROIs that
        are missing - special-casing a smaller batch would add real
        complexity for a rare case (selection changed between runs) without
        meaningfully cutting cost.

        Each returned per-ROI result is projected onto `reduction_method`
        and `formula_key` (each defaults to the live active setting if not
        given explicitly) via `project_reduction_result` - a RAM/disk hit is
        valid for ANY reduction method and formula (the RAM signature drops
        formula entirely, see `_roi_formula_spectrum_signature_for_cube`,
        and a schema-6.7+ disk row can carry every reduction method's values
        via `reduced_values_by_method`), so this is what lets a Reduction or
        Formula switch reuse an already-computed cube instantly instead of
        re-reading pixels. Callers driving a multi-cube background run
        should pass both captured once at run start explicitly, rather than
        relying on the live defaults, so every cube in that run is
        consistent even if either setting changes while the run is still in
        flight - see `_roi_formula_spectrum_signature_for_cube`'s
        `reduction_method_override`, used here for the same reason on the
        RAM side.

        Thread-safe: safe to call from a background worker thread (used by
        the multi-cube "Start analysis" loop) as well as the main thread
        (used by the interactive single-cube refresh) - RAM cache access is
        lock-protected, and `disk_trace_index` is a plain, pre-loaded dict
        (see _build_disk_absorbance_trace_index), never live HDF5 I/O here.
        """
        if not selected_source_rois:
            return None
        active_formula_key = formula_key if formula_key is not None else self._active_formula_key()
        active_reduction_method = reduction_method if reduction_method is not None else self._active_reduction_method()
        results: dict[int, FormulaSpectrumResult] = {}
        for roi in selected_source_rois:
            roi_id = int(roi.area_roi_id)
            signature = self._roi_formula_spectrum_signature_for_cube(
                roi, spectral_cube_index, reduction_method_override=active_reduction_method
            )
            if signature is None:
                return None
            with self.window._analysis_cache_lock:
                cached = self.window._roi_formula_spectrum_cache.get(signature)
                if cached is not None:
                    self.window._roi_formula_spectrum_cache.move_to_end(signature)
            if cached is None:
                disk_signature = self._roi_disk_signature_for_cube(roi, spectral_cube_index)
                cached = self._formula_spectrum_result_from_disk_row(roi, spectral_cube_index, disk_signature, disk_trace_index)
                if cached is not None:
                    self._store_in_lru_cache(
                        self.window._roi_formula_spectrum_cache, signature, cached, self.window.ROI_FORMULA_SPECTRUM_CACHE_SIZE,
                        lock=self.window._analysis_cache_lock,
                    )
                    # A disk-resumed result can carry every reduction method
                    # at once (see _formula_spectrum_result_from_disk_row) -
                    # write-through the other three RAM slots too, same as a
                    # fresh compute, so a later switch to a different method
                    # for this same cube hits RAM directly instead of
                    # re-reading the (already-loaded) disk trace index again.
                    self._write_through_reduced_values_by_method(
                        roi, spectral_cube_index, cached, lock=self.window._analysis_cache_lock
                    )
            if cached is None:
                return None
            projected = project_reduction_result(cached, active_reduction_method, active_formula_key)
            if projected is None:
                return None
            results[roi_id] = projected
        return results

    def _store_roi_formula_spectrum_cache_for_cube(
        self,
        roi_results: dict[int, FormulaSpectrumResult],
        spectral_cube_index: int,
        rois: list[AreaRoi],
    ) -> None:
        """Same as `_store_roi_absorbance_cache`, but for an arbitrary cube -
        see `_roi_absorbance_signature_for_cube`. Lock-protected: called from
        the sensorgram worker thread as well as the main thread.
        """
        roi_by_id = {int(roi.area_roi_id): roi for roi in rois}
        for roi_id, roi_result in roi_results.items():
            roi = roi_by_id.get(int(roi_id))
            if roi is None:
                continue
            signature = self._roi_formula_spectrum_signature_for_cube(roi, spectral_cube_index)
            if signature is None:
                continue
            self._store_in_lru_cache(
                self.window._roi_formula_spectrum_cache, signature, roi_result, self.window.ROI_FORMULA_SPECTRUM_CACHE_SIZE,
                lock=self.window._analysis_cache_lock,
            )
            self._write_through_reduced_values_by_method(
                roi, spectral_cube_index, roi_result, lock=self.window._analysis_cache_lock
            )

    def _roi_reduction_signature_elements(self) -> tuple[str]:
        """(reduction_method,) - the ROI's-math setting that changes what a
        ROI pair's masked pixels reduce to (sample_reduced_value/reference_
        reduced_value). Appended to every cache/disk signature whose cached
        value would otherwise go stale when the user changes Reduction
        without anything else changing.

        Trim % is deliberately NOT a live parameter here (see processing/
        roi_math.py's DEFAULT_TRIMMED_MEAN_FRACTION) - reduction_method
        itself is kept in the signature (so each method gets its own cache
        slot), but every slot is populated together via write-through
        caching (see `_write_through_reduced_values_by_method`) whenever any
        one of them is freshly computed, so switching Reduction among
        mean/median/trimmed_mean/plane_fit is a normal cache hit through
        this same signature, not a special-cased read-time projection.

        Deliberately does NOT include formula_key: the formula combines two
        already-reduced numbers via a cheap, pure `formula_value()` call (see
        processing/analysis.py) with no pixel access, so any formula is
        exactly derivable from a signature-valid result via
        `project_formula_spectrum` - see `_active_formula_key` below. A
        signature that fed straight off a *finished, fitted* value (the
        sensorgram trace/metric, not the raw per-wavelength reduction) still
        needs the active formula explicitly - see
        `_sensorgram_signature_for_selection` and
        `_sensorgram_point_signature_hash`, which is the one place getting
        this wrong would be a real correctness bug: it would let a formula
        switch silently reuse a metric value computed under the old formula."""
        settings = self.window._state.area_roi_settings
        return (str(settings.reduction_method),)

    def _active_formula_key(self) -> str:
        """The ROI's-formula selection currently active for display/Metric
        trace extraction - see AreaRoiDetectionSettings.formula_key and the
        "ROI's formula" control in the Metric trace section."""
        return str(self.window._state.area_roi_settings.formula_key or "absorbance")

    def _active_reduction_method(self) -> str:
        """The live Reduction setting - `_roi_reduction_signature_elements()`
        unpacked to a plain string, for callers that just want the value
        (not a signature-shaped tuple)."""
        (reduction_method,) = self._roi_reduction_signature_elements()
        return reduction_method

    # Sentinel `reduction_method_override` value used ONLY to build a
    # reduction-independent signature for on-disk validity checks (see
    # `_roi_disk_signature_for_cube`, `_backup_formula_spectrum_series`'s
    # `signature_hash`, and `_formula_spectrum_result_from_disk_row`).
    # Reusing the existing reduction-INCLUSIVE signature builder with a
    # fixed, never-varying value in that slot is equivalent to omitting
    # reduction_method from the signature entirely, without a second
    # near-duplicate signature function to keep in sync. Never used for a
    # RAM cache key - RAM keeps one slot per reduction method by design (see
    # `_write_through_reduced_values_by_method`); this is disk-only, because
    # a saved row's `reduced_values/<method>/` subgroup (see
    # storage/measurement_export.py) makes ALL four methods available from
    # one row, so the row's own validity must not depend on which one
    # happened to be active when it was written.
    _DISK_SIGNATURE_REDUCTION_PLACEHOLDER = "__disk_pixel_signature__"

    def _roi_disk_signature_for_cube(self, roi: AreaRoi, spectral_cube_index: int) -> tuple[object, ...] | None:
        return self._roi_formula_spectrum_signature_for_cube(
            roi, spectral_cube_index, reduction_method_override=self._DISK_SIGNATURE_REDUCTION_PLACEHOLDER
        )

    def _sensorgram_signature_for_selection(
        self,
        spectral_cubes: list[int],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_roi_ids or not selected_source_rois or not spectral_cubes:
            return None
        spectral_cube_signatures: list[tuple[object, ...]] = []
        for spectral_cube_index in spectral_cubes:
            spectral_cube_signatures.append(
                (
                    int(spectral_cube_index),
                    tuple(
                        self.window._preprocessing_signature((int(spectral_cube_index), float(wavelength)))
                        for wavelength in self.window._wavelength_values
                    ),
                    self._exclusion_signature_for_cube(spectral_cube_index),
                )
            )
        dataset_key = str(self.window._state.dataset.folder)
        wavelength_range = self.window._analysis_wavelength_range()
        return (
            dataset_key,
            tuple(selected_roi_ids),
            self.window._roi_signature(selected_source_rois),
            self.window._analysis_fit_method_key(),
            self.window._analysis_metric_key(),
            int(self.window._analysis_poly_order()),
            None if wavelength_range is None else (round(wavelength_range[0], 6), round(wavelength_range[1], 6)),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            tuple(spectral_cube_signatures),
            round(float(self.window._state.area_roi_settings.reference_inner_radius_px), 3),
            round(float(self.window._state.area_roi_settings.reference_outer_radius_px), 3),
            *self._roi_reduction_signature_elements(),
            self._active_formula_key(),
        )

    def _sensorgram_spectral_cube_payload_signature(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_roi_ids or not selected_source_rois:
            return None
        return (
            str(self.window._state.dataset.folder),
            int(spectral_cube_index),
            tuple(selected_roi_ids),
            self.window._roi_signature(selected_source_rois),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            tuple(
                self.window._preprocessing_signature((int(spectral_cube_index), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
            round(float(self.window._state.area_roi_settings.reference_inner_radius_px), 3),
            round(float(self.window._state.area_roi_settings.reference_outer_radius_px), 3),
            *self._roi_reduction_signature_elements(),
            self._exclusion_signature_for_cube(spectral_cube_index),
        )

    def _sensorgram_point_signature_hash(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> str:
        """Signature hash for one backed-up sensorgram point (see
        `storage/measurement_export.py`'s `signature_hash` column). Unlike
        `_sensorgram_spectral_cube_payload_signature` - deliberately
        fit-method/metric-independent, since it keys a cache of the full
        pre-fit `AbsorbanceSpectrumResult` that stays reusable across fit
        changes - the HDF5 backup only stores the already-reduced final
        `metric_value` for one row, so a fit-method/metric/poly-order
        change must count as a different value here. Falls back to an
        empty string (never a hit) when the payload signature itself can't
        be built (no dataset/selection yet).

        Explicitly includes the active formula (the payload signature itself
        does not - see `_roi_reduction_signature_elements`): a finished
        metric_value bakes in whichever formula produced it, unlike the raw
        pre-fit spectrum, which is exactly re-derivable under any formula.
        Omitting it here would let a formula switch silently reuse a disk
        metric value computed under the previous formula.

        Also explicitly includes the wavelength-range filter (Analysis
        section's min/max nm spinners, `_analysis_wavelength_range()`) - for
        the same reason as the formula above, and previously missing here:
        Maximum/Centroid are computed from whatever wavelength window the
        fit/metric search is restricted to, so a `metric_value` backed up
        under one range is a different, generally wrong answer once the
        range changes (e.g. a peak search narrowed to some sub-window
        earlier in a session, or in a previous session against this same
        persistent measurement_backup.h5, produces a peak_wavelength_nm that
        can land nowhere near the peak the *current*, wider range would
        find) - without this, `metric_value_cache_get` would keep serving
        that stale value forever after the range is widened back out,
        because every other element of the signature (fit method, metric,
        poly order, ROI/preprocessing signature) can still match exactly."""
        payload_signature = self._sensorgram_spectral_cube_payload_signature(
            spectral_cube_index, selected_roi_ids, selected_source_rois
        )
        if payload_signature is None:
            return ""
        wavelength_range = self.window._analysis_wavelength_range()
        full_signature = (
            payload_signature,
            self._active_formula_key(),
            self._analysis_fit_method_key(),
            self.window._analysis_metric_key(),
            int(self.window._analysis_poly_order()),
            None if wavelength_range is None else round(float(wavelength_range[0]), 6),
            None if wavelength_range is None else round(float(wavelength_range[1]), 6),
        )
        return self._signature_hash(full_signature)

    def _cached_sensorgram_spectral_cube_result(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
        formula_key: str | None = None,
    ) -> FormulaSpectrumResult | None:
        """Per-frame math-layer cache (sample/reference means -> AbsorbanceSpectrumResult),
        keyed by the same fit-parameter-AND-formula-independent signature as the payload cache
        above, so changing only poly_order/metric_key/formula never forces re-reading pixels
        for a frame whose sample/reference means are already known. Projects onto `formula_key`
        (defaults to the live `_active_formula_key()`) before returning - see
        `_combined_formula_spectrum_results_from_ram_or_disk` for the same pattern and why a
        multi-cube run should pass its own captured formula explicitly rather than rely on the
        live default."""
        signature = self._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if signature is None:
            return None
        with self.window._analysis_cache_lock:
            cached = self.window._sensorgram_spectral_cube_result_cache.get(signature)
            if cached is not None:
                self.window._sensorgram_spectral_cube_result_cache.move_to_end(signature)
        if cached is None:
            return None
        return project_formula_spectrum(cached, formula_key if formula_key is not None else self._active_formula_key())

    def _store_sensorgram_spectral_cube_result(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
        result: FormulaSpectrumResult,
    ) -> None:
        signature = self._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if signature is None:
            return
        self._store_in_lru_cache(
            self.window._sensorgram_spectral_cube_result_cache, signature, result,
            self.window.SENSORGRAM_SPECTRAL_CUBE_RESULT_CACHE_SIZE, lock=self.window._analysis_cache_lock,
        )

    def _formula_spectrum_signature_for_source_rois(
        self,
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        spectral_cube_index = self.window._current_spectral_cube()
        if spectral_cube_index is None or not selected_source_rois:
            return None
        selected_roi_ids = tuple(int(roi.area_roi_id) for roi in selected_source_rois)
        return (
            int(spectral_cube_index),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            selected_roi_ids,
            tuple(
                self.window._chromatic_signature_for_image_key((int(spectral_cube_index), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
            *self._roi_reduction_signature_elements(),
            self._exclusion_signature_for_cube(spectral_cube_index),
        )

    def _formula_spectrum_signature(self) -> tuple[object, ...] | None:
        return self._formula_spectrum_signature_for_source_rois(self.window._selected_source_rois_snapshot())

    def _cached_formula_spectrum_result_from_roi_cache(
        self,
        selected_source_rois: list[AreaRoi],
    ) -> FormulaSpectrumResult | None:
        if not selected_source_rois:
            return None
        roi_results: dict[int, FormulaSpectrumResult] = {}
        for roi in selected_source_rois:
            roi_signature = self.window._roi_formula_spectrum_signature(roi)
            if roi_signature is None:
                return None
            cached_result = self.window._roi_formula_spectrum_cache.get(roi_signature)
            if cached_result is None:
                return None
            roi_results[int(roi.area_roi_id)] = cached_result
        return self._combine_roi_formula_spectrum_results(roi_results)

    @staticmethod
    def _combine_roi_formula_spectrum_results(
        roi_results: dict[int, FormulaSpectrumResult],
    ) -> FormulaSpectrumResult | None:
        """Combine already-resolved per-ROI results (RAM cache, disk resume,
        or a fresh compute - the caller decides where each came from) into one
        displayable/fittable `AbsorbanceSpectrumResult`. All selected ROIs
        share the same wavelengths/reduction/formula, so the "combined"
        result's own curve is just the first ROI's - only `area_roi_results`
        (read separately by anything that needs a specific ROI's own curve)
        actually varies per ROI.
        """
        first_result = next(iter(roi_results.values()), None)
        if first_result is None:
            return None
        return FormulaSpectrumResult(
            wavelengths_nm=np.asarray(first_result.wavelengths_nm, dtype=np.float64),
            formula_values=np.asarray(first_result.formula_values, dtype=np.float64),
            sample_reduced_value=np.asarray(first_result.sample_reduced_value, dtype=np.float64),
            reference_reduced_value=np.asarray(first_result.reference_reduced_value, dtype=np.float64),
            sample_pixel_count=np.asarray(first_result.sample_pixel_count, dtype=np.int32),
            reference_pixel_count=np.asarray(first_result.reference_pixel_count, dtype=np.int32),
            load_seconds=float(first_result.load_seconds),
            roi_seconds=float(first_result.roi_seconds),
            fit_seconds=float(first_result.fit_seconds),
            total_seconds=float(first_result.total_seconds),
            reduction_method=str(first_result.reduction_method),
            formula_key=str(first_result.formula_key),
            area_roi_results=roi_results,
        )

    def _cached_formula_spectrum_result_for_selection(
        self,
        signature: tuple[object, ...],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> FormulaSpectrumResult | None:
        if not selected_roi_ids:
            return None
        if len(selected_roi_ids) == 1:
            for cache_signature, cached_result in reversed(list(self.window._roi_formula_spectrum_cache.items())):
                if self._formula_spectral_cube_signature(cache_signature) != self._formula_spectral_cube_signature(signature):
                    continue
                if self._formula_spectrum_result_covers_roi_ids(cached_result, selected_roi_ids):
                    return cached_result
        spectral_cube_signature = self._formula_spectral_cube_signature(signature)
        if spectral_cube_signature is not None:
            cached_result = self.window._formula_spectral_cube_cache.get(spectral_cube_signature)
            if cached_result is not None and self._formula_spectrum_result_covers_roi_ids(cached_result, selected_roi_ids):
                return cached_result
        for cache_signature, cached_result in reversed(list(self.window._formula_spectrum_cache.items())):
            if self._formula_spectral_cube_signature(cache_signature) != spectral_cube_signature:
                continue
            if self._formula_spectrum_result_covers_roi_ids(cached_result, selected_roi_ids):
                return cached_result
        if selected_source_rois:
            cached_from_rois = self._cached_formula_spectrum_result_from_roi_cache(selected_source_rois)
            if cached_from_rois is not None:
                return cached_from_rois
        # "Start analysis" already computed and cached a full
        # per-wavelength spectrum for every cube it visited (see
        # _store_sensorgram_spectral_cube_result) - that cache was never
        # consulted here, so browsing to a cube the batch run already covered
        # silently recomputed it from scratch (or showed nothing at all, with
        # Live Preview off) instead of reusing work already paid for. Reuses
        # the sensorgram cache's own signature function, so this only ever
        # hits when the cube/ROI/wavelength/preprocessing settings are
        # unchanged since that computation - anything different (moved ROI,
        # changed reference radius, etc.) naturally misses and falls through
        # to a fresh computation below, same as before this fallback existed.
        if selected_source_rois:
            spectral_cube_index = self.window._current_spectral_cube()
            if spectral_cube_index is not None:
                sensorgram_result = self._cached_sensorgram_spectral_cube_result(
                    int(spectral_cube_index), selected_roi_ids, selected_source_rois,
                )
                if sensorgram_result is not None and self._formula_spectrum_result_covers_roi_ids(sensorgram_result, selected_roi_ids):
                    return sensorgram_result
        return None

    def _exclusion_signature_for_cube(self, spectral_cube_index: int) -> tuple[object, ...]:
        """Per-wavelength "is this frame currently excluded" booleans for one
        cube, folded into every cache signature that reduces across a cube's
        wavelengths - `is_excluded` already resolves whole-cube and
        whole-wavelength wildcard rules down to a plain per-frame bool, so
        this is enough to make a signature miss whenever an exclusion rule
        is added, removed, or changed, without needing to hash the rule list
        itself. See `_invalidate_caches_for_exclusion_change`'s removal for
        why this matters (docs/analysis_pipeline_redesign.md §2c)."""
        return tuple(
            is_excluded(self.window._state.image_exclusions, int(spectral_cube_index), float(wavelength))
            for wavelength in self.window._wavelength_values
        )

    def _roi_formula_spectrum_signature(self, roi: AreaRoi) -> tuple[object, ...] | None:
        spectral_cube_index = self.window._current_spectral_cube()
        if spectral_cube_index is None:
            return None
        return self._roi_formula_spectrum_signature_for_cube(roi, int(spectral_cube_index))

    def _roi_formula_spectrum_signature_for_cube(
        self, roi: AreaRoi, spectral_cube_index: int, *, reduction_method_override: str | None = None
    ) -> tuple[object, ...] | None:
        """Same as `_roi_absorbance_signature`, but for an arbitrary cube
        rather than hard-coding `window._current_spectral_cube()` - needed by
        the unified per-cube absorbance getter (§2 of the sensorgram/spectrum
        unification), which computes/caches results for whichever cube a
        multi-cube run is currently processing, not necessarily the one on
        screen.

        `reduction_method_override`: build the signature for a SPECIFIC
        reduction method instead of the live setting - used by
        `_write_through_reduced_values_by_method` to compute the OTHER
        reduction methods' own cache signatures for the same cube/ROI, so a
        freshly-computed result can be stashed under all of them at once."""
        if not self.window._wavelength_values:
            return None
        (reduction_method,) = (
            self._roi_reduction_signature_elements() if reduction_method_override is None else (reduction_method_override,)
        )
        return _roi_formula_spectrum_signature(
            int(spectral_cube_index),
            tuple(float(value) for value in self.window._wavelength_values),
            roi,
            tuple(
                self.window._chromatic_signature_for_image_key((int(spectral_cube_index), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
            reduction_method,
            DEFAULT_TRIMMED_MEAN_FRACTION,
            exclusion_signatures=self._exclusion_signature_for_cube(spectral_cube_index),
        )

    def _roi_has_cached_formula_spectrum(self, roi: AreaRoi) -> bool:
        signature = self._roi_formula_spectrum_signature(roi)
        return signature is not None and self.window._roi_formula_spectrum_cache.get(signature) is not None
