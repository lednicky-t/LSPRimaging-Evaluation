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
import time
import numpy as np
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_imaging_app.domain.exclusions import is_excluded
from lspr_imaging_app.domain.models import AreaRoi, FormulaSpectrumResult
from lspr_imaging_app.gui.analysis_tasks import _roi_formula_spectrum_signature
from lspr_imaging_app.processing.analysis import metric_value_from_fit, metric_value_from_spectrum, project_formula_spectrum, project_reduction_result
from lspr_imaging_app.processing.chromatic import warp_boolean_mask_affine
from lspr_imaging_app.processing.roi_math import DEFAULT_TRIMMED_MEAN_FRACTION, REDUCTION_METHODS
from lspr_imaging_app.storage.measurement_export import FormulaSpectrumTraceIndex
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

    def _fast_spectrum_path_eligible(self, selected_source_rois: list[AreaRoi]) -> bool:
        """Whether the ROI-scoped, zarr-chunk-aware fast path can be used for
        the whole sensorgram/spectrum run. Decided once, upfront — NOT
        re-checked per spectral_cube_index — because a per-spectral_cube_index "not worth it" bail-out
        would silently drop that spectral_cube_index's data point instead of falling back
        to the full-plane path (spectral_cube_payload_builder results that come back
        None are just skipped, not retried another way).

        The "is the ROI selection compact enough" part uses a chromatic-shift
        agnostic box (chromatic shifts are small perturbations that wouldn't
        change whether ROIs are scattered enough to make scoping pointless),
        so this stays a cheap, synchronous, no-pixel-data-loaded check.
        """
        from lspr_imaging_app.gui.analysis_tasks import compute_roi_union_bounding_box, roi_union_box_is_worth_scoping
        from lspr_imaging_app.io.dataset import load_image_shape
        from lspr_imaging_app.processing.preprocess import spatial_output_shape

        preprocessing = self.window._state.preprocessing
        dataset = self.window._state.dataset
        basic_eligible = (
            bool(selected_source_rois)
            and dataset is not None
            and dataset.is_ome_zarr
        )
        if not basic_eligible:
            return False
        first_record = next(iter(self.window._record_map.values()), None)
        if first_record is None:
            return False
        try:
            raw_shape = load_image_shape(str(first_record.path))
        except Exception:
            return False
        image_height, image_width = spatial_output_shape(raw_shape, preprocessing)
        box = compute_roi_union_bounding_box(
            selected_source_rois,
            float(self.window._state.area_roi_settings.reference_outer_radius_px),
            [None],
            image_height,
            image_width,
        )
        return box is not None and roi_union_box_is_worth_scoping(box, image_height, image_width)

    def _spectrum_settings_snapshot(self) -> SpectrumSettingsSnapshot:
        """Build a SpectrumSettingsSnapshot once, up front - see its docstring.
        Callers pass the result into `_prepare_fast_spectrum_payload_for_spectral_cube`/
        `_prepare_absorbance_spectrum_payload_for_spectral_cube` instead of letting
        those methods deep-copy the live state fresh on every call.
        """
        return SpectrumSettingsSnapshot(
            preprocessing=deepcopy(self.window._state.preprocessing),
            area_roi_settings=deepcopy(self.window._state.area_roi_settings),
            mask_state=deepcopy(self.window._state.mask) if self.window._mask_section_applied() else None,
        )

    def _prepare_formula_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
        settings_snapshot: SpectrumSettingsSnapshot | None = None,
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_source_rois:
            return None
        if settings_snapshot is None:
            settings_snapshot = self._spectrum_settings_snapshot()
        preprocessing = settings_snapshot.preprocessing
        flatten_mask_settings = settings_snapshot.area_roi_settings if preprocessing.flatten_background_exclude_mask else None
        measurement_settings = settings_snapshot.area_roi_settings
        measurement_payload: list[tuple[float, str, list[AreaRoi], np.ndarray | None, bool, np.ndarray | None]] = []
        for wavelength in self.window._wavelength_values:
            record = self.window._record_map.get((spectral_cube_index, wavelength))
            if record is None or is_excluded(self.window._state.image_exclusions, spectral_cube_index, wavelength):
                continue
            image_key = (spectral_cube_index, float(wavelength))
            preprocessing_rois = deepcopy(self.window._rois_for_preprocessing(image_key))
            affine_matrix = self.window._chromatic_affine_for_image_key(image_key)
            if affine_matrix is not None:
                affine_matrix = np.asarray(affine_matrix, dtype=np.float64)
            external_mask, external_mask_processed = self.window._effective_external_mask_for_record(record.path, processed_space=True)
            if external_mask is not None:
                external_mask = np.asarray(external_mask, dtype=bool)
                # Ignore-mask geometry is authored/stored independent of wavelength; when
                # chromatic correction is on, carry it into this wavelength's corrected
                # geometry the same way circle/annulus ROIs already are (affine_matrix
                # above), so the excluded pixels stay aligned with the real feature they
                # were drawn over instead of a fixed pixel location.
                if affine_matrix is not None:
                    external_mask = warp_boolean_mask_affine(external_mask, affine_matrix)
                external_mask = self.window._apply_mask_wavelength_diff(external_mask, image_key)
            measurement_payload.append(
                (
                    float(wavelength),
                    str(record.path),
                    preprocessing_rois,
                    affine_matrix,
                    bool(external_mask_processed),
                    external_mask,
                )
            )
        if not measurement_payload:
            return None
        return (
            measurement_payload,
            preprocessing,
            flatten_mask_settings,
            measurement_settings,
            self.window._formula_spectrum_roi_mask_cache,
            self.window._analysis_cache_lock,
            int(self.window.FORMULA_SPECTRUM_ROI_MASK_CACHE_SIZE),
            # Already a deepcopy by the time it reaches this function (every
            # caller builds selected_source_rois via deepcopy(roi) up front),
            # so copying it again here was a deepcopy-of-a-deepcopy.
            selected_source_rois,
            selected_roi_ids,
            float(settings_snapshot.area_roi_settings.reference_inner_radius_px),
            float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
            settings_snapshot.mask_state,
        )

    def _prepare_fast_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple,
        selected_source_rois: list,
        settings_snapshot: SpectrumSettingsSnapshot | None = None,
        shared_geometry: SharedWavelengthGeometry | None = None,
        shared_mask_by_wavelength: dict[float, object] | None = None,
    ) -> tuple | None:
        """Build a lightweight payload for _absorbance_spectrum_fast_task.

        Used when the dataset is OME-Zarr, background flattening is off, and
        no rotation/flip transform is active (see the eligibility check in
        _start_sensorgram_worker, which also confirms the ROI selection is
        compact enough for a scoped read to be worthwhile — that decision is
        made once for the whole run, not per spectral_cube_index, so this always proceeds
        with a scoped read once called; it must never bail out for a "not
        worth it" reason here, or a spectral_cube_index would silently vanish from the
        sensorgram instead of falling back to the full-plane path). Returns
        None only when there's genuinely nothing to compute (no data for this
        spectral_cube_index, or no ROI geometry to build a box from at all).

        `shared_geometry`: [λ] mode - when given, the per-wavelength
        chromatic affine and the scoped-read box are taken from it instead of
        being recomputed for this cube (see SharedWavelengthGeometry's
        docstring for why that's valid). `shared_mask_by_wavelength`: [λ]
        mode's equivalent for the marked-pixels mask (see
        _build_shared_wavelength_mask) - when given, the already-warped,
        already-diffed mask for each wavelength is taken from it instead of
        being fetched/warped fresh for this cube. Which record/file to read
        per wavelength, and exclusions, are still resolved per cube either
        way - those are genuinely cube-specific (different files).
        """
        from lspr_imaging_app.gui.analysis_tasks import compute_roi_union_bounding_box
        from lspr_imaging_app.io.dataset import load_image_shape
        from lspr_imaging_app.processing.preprocess import spatial_output_shape

        if self.window._state.dataset is None or not selected_source_rois:
            return None
        if settings_snapshot is None:
            settings_snapshot = self._spectrum_settings_snapshot()
        preprocessing = settings_snapshot.preprocessing

        # Mirror ignored_pixel_mask's own gating: an external mask only excludes
        # pixels from the absorbance calculation when ignore_marked_pixels is
        # on. Fetching it unconditionally and applying it in the fast task
        # regardless of this flag would silently diverge from the slow path.
        exclude_marked_pixels = bool(getattr(settings_snapshot.area_roi_settings, "ignore_marked_pixels", False))

        measurement_payload: list[tuple[float, np.ndarray | None, np.ndarray | None, object]] = []
        affine_matrices: list[np.ndarray | None] = []
        first_record = None
        for wavelength in self.window._wavelength_values:
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

            box = compute_roi_union_bounding_box(
                selected_source_rois,
                float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
                affine_matrices,
                image_height,
                image_width,
            )
            if box is None:
                return None

        # Matches _prepare_absorbance_spectrum_payload_for_spectral_cube's own convention
        # for these two: mask_state only when the mask panel is applied/linked,
        # and background's own exclusion mask_settings only when background
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
            # Already a deepcopy by the time it reaches this function - see the
            # matching comment in _prepare_absorbance_spectrum_payload_for_spectral_cube.
            selected_source_rois,
            selected_roi_ids,
            float(settings_snapshot.area_roi_settings.reference_inner_radius_px),
            float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
            box,
            preprocessing,
            raw_shape,
            # Shared with the slow-path ROI mask cache below - the cache key
            # folds in patch shape/origin (see _absorbance_roi_mask_cache_key)
            # so scoped fast-path masks can never collide with full-image
            # slow-path ones; sharing the dict just means both paths draw
            # from the same size-capped budget instead of needing a second one.
            self.window._formula_spectrum_roi_mask_cache,
            self.window._analysis_cache_lock,
            int(self.window.FORMULA_SPECTRUM_ROI_MASK_CACHE_SIZE),
            mask_state,
            background_mask_settings,
        )

    def _start_sensorgram_worker(
        self,
        signature: tuple[object, ...],
        spectral_cubes: list[int],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> None:
        import time

        self.window._sensorgram_request_id += 1
        request_id = self.window._sensorgram_request_id
        self.window._sensorgram_running = True
        self.window._sensorgram_running_signature = signature
        self.window._sensorgram_running_roi_ids = selected_roi_ids
        import threading

        from lspr_imaging_app.gui.worker import FunctionWorker
        from lspr_imaging_app.gui.analysis_tasks import (
            _sensorgram_metric_task,
            _formula_spectrum_fast_task,
        )

        use_fast_path = self._fast_spectrum_path_eligible(selected_source_rois)
        settings_snapshot = self._spectrum_settings_snapshot()
        # Read once, up front, on the main thread - the worker thread below
        # only ever sees the plain resulting dict, never touches HDF5 itself
        # (see _combined_absorbance_results_from_ram_or_disk's docstring).
        disk_formula_spectrum_trace_index = self._build_disk_formula_spectrum_trace_index(selected_source_rois)
        # Also captured once, up front: every cube in this run projects onto
        # the SAME active formula, even if the user flips the combo while the
        # run is still in flight - see _combined_formula_spectrum_results_
        # from_ram_or_disk's docstring.
        active_formula_key = self._active_formula_key()
        # [λ] mode: only meaningful for the fast/scoped-read path (the slow
        # path never computes a shared box at all - it reads the full plane
        # every time regardless). None when the toggle is off, or when there
        # was genuinely nothing to build from (no data for the reference
        # cube), in which case the closure below transparently falls back to
        # per-cube computation.
        shared_wavelength_geometry = (
            self._build_shared_wavelength_geometry(spectral_cubes, selected_source_rois, settings_snapshot)
            if use_fast_path and self.window._analysis_time_independent
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
        fast_label = " [fast]" if use_fast_path else ""
        self.window._set_sensorgram_summary_text(
            f"{self.window._analysis_metric_label()}{fast_label} | Preparing {len(spectral_cubes)} spectral cubes"
            f" | Range {spectral_cubes[0]}-{spectral_cubes[-1]}"
        )
        self.window._set_status_text("Preparing spectral cube reads...")
        # show_wait_cursor=False: analysis runs entirely in the background
        # (a QThreadPool worker) - the app itself stays fully interactive
        # while it runs, so it shouldn't look/feel frozen behind an app-wide
        # wait cursor. Progress is still visible in the status bar as usual.
        self.window._begin_busy(
            "Preparing spectral cube reads...", determinate=True, show_wait_cursor=False, total_items=len(spectral_cubes)
        )

        if use_fast_path:
            def spectral_cube_payload_builder(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois, settings_snapshot=settings_snapshot, shared_wavelength_geometry=shared_wavelength_geometry, shared_mask_by_wavelength=shared_mask_by_wavelength):
                return self._prepare_fast_spectrum_payload_for_spectral_cube(
                    spectral_cube_index,
                    selected_roi_ids,
                    selected_source_rois,
                    settings_snapshot,
                    shared_wavelength_geometry,
                    shared_mask_by_wavelength,
                )
            task_fn = _formula_spectrum_fast_task
        else:
            def spectral_cube_payload_builder(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois, settings_snapshot=settings_snapshot):
                return self._cached_sensorgram_spectral_cube_payload(
                    spectral_cube_index,
                    selected_roi_ids,
                    selected_source_rois,
                    settings_snapshot,
                )
            task_fn = None

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

        def spectral_cube_formula_spectrum_cache_get(spectral_cube_index, selected_source_rois=selected_source_rois, disk_formula_spectrum_trace_index=disk_formula_spectrum_trace_index, active_formula_key=active_formula_key):
            roi_results = self._combined_formula_spectrum_results_from_ram_or_disk(
                spectral_cube_index, selected_source_rois, disk_formula_spectrum_trace_index, formula_key=active_formula_key
            )
            if roi_results is None:
                return None
            return self._combine_roi_formula_spectrum_results(roi_results)

        def spectral_cube_formula_spectrum_cache_store(spectral_cube_index, roi_formula_spectrum_results, selected_source_rois=selected_source_rois):
            self._store_roi_formula_spectrum_cache_for_cube(roi_formula_spectrum_results, spectral_cube_index, selected_source_rois)

        wavelength_range = self.window._analysis_wavelength_range()
        (reduction_method,) = self._roi_reduction_signature_elements()
        formula_key = active_formula_key
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
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.partial.connect(
            lambda point, request_id=request_id, total=len(spectral_cubes): self.on_sensorgram_partial_result(request_id, total, point)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self.on_sensorgram_ready(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self.on_sensorgram_failed(request_id, message))
        self.window._thread_pool.start(worker)

    def on_sensorgram_partial_result(self, request_id: int, total_count: int, point) -> None:
        if request_id != self.window._sensorgram_request_id or not self.window._analysis_enabled:
            return
        metric_value = float("nan") if point.metric_value is None else float(point.metric_value)
        metric_signal = float("nan") if point.metric_signal is None else float(point.metric_signal)
        self.window._sensorgram_spectral_cube_indices = np.append(self.window._sensorgram_spectral_cube_indices, int(point.spectral_cube_index)).astype(np.int32, copy=False)
        self.window._sensorgram_metric_values = np.append(self.window._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self.window._sensorgram_metric_signal = np.append(self.window._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        self.set_sensorgram_series(
            self.window._sensorgram_spectral_cube_indices,
            self.window._sensorgram_metric_values,
            summary_text=f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_spectral_cube_indices.size}/{total_count} spectral cubes",
        )
        self._backup_sensorgram_point(point)
        roi_formula_spectrum_results = getattr(point, "roi_formula_spectrum_results", None)
        if roi_formula_spectrum_results:
            # Unconditional - not gated by live preview: "save them in HDF5"
            # is a completeness guarantee for every cube a run touches, not
            # just the ones the user happens to watch live. The writer's own
            # dedup-by-signature-hash means an already-backed-up cube (a RAM/
            # disk cache hit here) is a cheap no-op, not a duplicate row.
            series_payloads = [
                (str(roi_id), int(roi_id), roi_result) for roi_id, roi_result in roi_formula_spectrum_results.items()
            ]
            self._backup_formula_spectrum_series(series_payloads, cube_index=int(point.spectral_cube_index))
            # This cube's formula-spectrum results just landed in
            # _roi_formula_spectrum_cache (via spectral_cube_formula_spectrum_cache_store,
            # a background-thread write that - unlike _store_roi_formula_spectrum_cache -
            # doesn't itself trigger a slider refresh). Schedule one here so the
            # Cube/Time slider's cached-tick coloring updates incrementally
            # during a live run instead of only once at on_sensorgram_ready/
            # on_sensorgram_failed.
            self.schedule_cube_slider_cache_refresh()
            if self.window._analysis_live_preview_enabled:
                self.window._pending_sensorgram_live_point = point
                self.window._sensorgram_live_preview_timer.start()

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
            timing = self._earliest_timing_by_cube_index(metadata).get(int(spectral_cube_index))
            if timing is not None:
                return int(timing.acquired_at_unix_ms)
        return int(datetime.now().timestamp() * 1000)

    def on_sensorgram_ready(self, request_id: int, result) -> None:
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
        """Build the (signature, payload, task_fn) for a single-spectral-cube spectrum
        calculation. Uses the exact same eligibility decision and payload
        builders as the sensorgram loop (_fast_spectrum_path_eligible,
        _prepare_fast_spectrum_payload_for_spectral_cube /
        _prepare_absorbance_spectrum_payload_for_spectral_cube) — a single-spectral-cube
        spectrum is just a one-spectral-cube sensorgram, so there is one decision
        point and one pair of task functions, not a separate parallel
        implementation for this case.
        """
        if self.window._state.dataset is None:
            return None
        # Both branches must hand the payload builders an already-isolated copy -
        # _selected_source_rois_snapshot() deep-copies internally, but an explicit
        # caller-supplied list might be live references into window._state.area_rois,
        # so it gets the same treatment here rather than relying on each builder to
        # deepcopy it again (removed as a redundant deepcopy-of-a-deepcopy, see
        # _prepare_fast_spectrum_payload_for_spectral_cube).
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

        from lspr_imaging_app.gui.analysis_tasks import _formula_spectrum_fast_task, _formula_spectrum_task

        if self._fast_spectrum_path_eligible(selected_source_rois):
            payload = self._prepare_fast_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois, settings_snapshot)
            if payload is not None:
                return signature, payload, _formula_spectrum_fast_task
            # Fast payload builder found genuinely nothing to compute for this
            # spectral cube (e.g. no records) — fall through to the full-plane path
            # rather than reporting "no spectrum" when the slow path might
            # still have an answer.
        payload = self._prepare_formula_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois, settings_snapshot)
        if payload is None:
            return None
        return signature, payload, _formula_spectrum_task

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
        recomputed under different settings still gets a fresh one - see
        docs/imaging_measurement_export_format.md.

        `cube_index`: explicit for the multi-cube "Start analysis" loop, which
        backs up a cube that isn't necessarily the one on screen. Defaults to
        the currently-displayed cube (window._current_spectral_cube()) for the
        interactive single-cube refresh path, preserving its existing behavior.
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
        for label, roi_id, roi_result in series_payloads:
            if label == "Selection":
                continue
            roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
            signature_hash = self._signature_hash(self._roi_formula_spectrum_signature_for_cube(roi, cube_index)) if roi is not None else ""
            key = (int(roi_id), cube_index, signature_hash)
            if key in backed_up:
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
                )
            except Exception:
                logging.getLogger("lspr_imaging_app.workflow").warning(
                    "Failed to append absorbance spectrum to measurement export backup", exc_info=True
                )
                continue
            backed_up.add(key)

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
        self.window._thread_pool.start(worker)

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
        self.window._thread_pool.start(worker)

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
        roi_id: int,
        spectral_cube_index: int,
        signature: tuple[object, ...],
        disk_trace_cache: dict[int, FormulaSpectrumTraceIndex],
    ) -> bool:
        """Boolean-only counterpart of `_formula_spectrum_result_from_disk_row`
        for the slider's cached-tick check: same hash-validity rule, but skips
        materializing the full `FormulaSpectrumResult` (wavelength/formula/
        mean arrays) since the tick indicator only needs a yes/no per cube."""
        trace = disk_trace_cache.get(int(roi_id))
        if trace is None:
            return False
        entry = trace.by_cube.get(int(spectral_cube_index))
        if entry is None:
            return False
        stored_hash = entry[0]
        return bool(stored_hash) and stored_hash == self._signature_hash(signature)

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
        roi_id: int,
        spectral_cube_index: int,
        signature: tuple[object, ...],
        disk_trace_index: dict[int, FormulaSpectrumTraceIndex] | None,
    ) -> FormulaSpectrumResult | None:
        if not disk_trace_index:
            return None
        trace = disk_trace_index.get(int(roi_id))
        if trace is None:
            return None
        entry = trace.by_cube.get(int(spectral_cube_index))
        if entry is None:
            return None
        stored_hash, formula_values, sample_reduced_value, reference_reduced_value = entry
        if not stored_hash or stored_hash != self._signature_hash(signature):
            return None
        # sample_pixel_count/reference_pixel_count aren't persisted to the
        # HDF5 backup today (only the spectrum curve itself is) - a
        # disk-resumed result shows 0px in the spectrum tooltip until this
        # cube is recomputed fresh. Accepted gap, not a bug: these counts
        # reflect exclusion masks that can change over time, so they aren't
        # derivable from ROI geometry alone, and nothing besides that tooltip
        # reads them.
        return FormulaSpectrumResult(
            wavelengths_nm=trace.wavelengths_nm,
            formula_values=formula_values,
            sample_reduced_value=sample_reduced_value,
            reference_reduced_value=reference_reduced_value,
            sample_pixel_count=np.asarray([], dtype=np.int32),
            reference_pixel_count=np.asarray([], dtype=np.int32),
            reduction_method=trace.reduction_method,
            formula_key=trace.formula_key,
        )

    def _combined_formula_spectrum_results_from_ram_or_disk(
        self,
        spectral_cube_index: int,
        selected_source_rois: list[AreaRoi],
        disk_trace_index: dict[int, FormulaSpectrumTraceIndex] | None = None,
        formula_key: str | None = None,
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

        Each returned per-ROI result is projected onto `formula_key`
        (defaults to the live `_active_formula_key()` if not given
        explicitly) via `project_formula_spectrum` - a RAM/disk hit is valid
        for ANY formula (the signature is formula-independent, see
        `_roi_formula_spectrum_signature_for_cube`), so this is what lets a
        formula switch reuse an already-reduced cube instantly. Callers
        driving a multi-cube background run should pass the formula
        captured once at run start explicitly, rather than relying on the
        live default, so every cube in that run is consistent even if the
        active formula changes while the run is still in flight.

        Thread-safe: safe to call from a background worker thread (used by
        the multi-cube "Start analysis" loop) as well as the main thread
        (used by the interactive single-cube refresh) - RAM cache access is
        lock-protected, and `disk_trace_index` is a plain, pre-loaded dict
        (see _build_disk_absorbance_trace_index), never live HDF5 I/O here.
        """
        if not selected_source_rois:
            return None
        active_formula_key = formula_key if formula_key is not None else self._active_formula_key()
        results: dict[int, FormulaSpectrumResult] = {}
        for roi in selected_source_rois:
            roi_id = int(roi.area_roi_id)
            signature = self._roi_formula_spectrum_signature_for_cube(roi, spectral_cube_index)
            if signature is None:
                return None
            with self.window._analysis_cache_lock:
                cached = self.window._roi_formula_spectrum_cache.get(signature)
                if cached is not None:
                    self.window._roi_formula_spectrum_cache.move_to_end(signature)
            if cached is None:
                cached = self._formula_spectrum_result_from_disk_row(roi_id, spectral_cube_index, signature, disk_trace_index)
                if cached is not None:
                    self._store_in_lru_cache(
                        self.window._roi_formula_spectrum_cache, signature, cached, self.window.ROI_FORMULA_SPECTRUM_CACHE_SIZE,
                        lock=self.window._analysis_cache_lock,
                    )
            if cached is None:
                return None
            results[roi_id] = project_formula_spectrum(cached, active_formula_key)
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
        metric value computed under the previous formula."""
        payload_signature = self._sensorgram_spectral_cube_payload_signature(
            spectral_cube_index, selected_roi_ids, selected_source_rois
        )
        if payload_signature is None:
            return ""
        full_signature = (
            payload_signature,
            self._active_formula_key(),
            self._analysis_fit_method_key(),
            self.window._analysis_metric_key(),
            int(self.window._analysis_poly_order()),
        )
        return self._signature_hash(full_signature)

    def _cached_sensorgram_spectral_cube_payload(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
        settings_snapshot: SpectrumSettingsSnapshot | None = None,
    ) -> tuple[object, ...] | None:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        signature = self._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if signature is None:
            return None
        with self.window._analysis_cache_lock:
            cached = self.window._sensorgram_spectral_cube_payload_cache.get(signature)
            if cached is not None:
                self.window._sensorgram_spectral_cube_payload_cache.move_to_end(signature)
                logger.debug(
                    "SG payload cache hit | spectral_cube_index=%s rois=%s",
                    int(spectral_cube_index),
                    len(selected_roi_ids),
                )
                return cached
        payload = self._prepare_formula_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois, settings_snapshot)
        if payload is None:
            return None
        self._store_in_lru_cache(
            self.window._sensorgram_spectral_cube_payload_cache, signature, payload,
            self.window.SENSORGRAM_SPECTRAL_CUBE_PAYLOAD_CACHE_SIZE, lock=self.window._analysis_cache_lock,
        )
        logger.debug(
            "SG payload cache built | spectral_cube_index=%s rois=%s",
            int(spectral_cube_index),
            len(selected_roi_ids),
        )
        return payload

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
