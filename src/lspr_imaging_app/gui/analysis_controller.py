from __future__ import annotations

import logging
import time
import numpy as np
from copy import deepcopy
from datetime import datetime
from PyQt6.QtGui import QColor
import pyqtgraph as pg

from lspr_imaging_app.domain.exclusions import is_cube_fully_excluded, is_excluded
from lspr_imaging_app.domain.models import AreaRoi, AbsorbanceSpectrumResult
from lspr_imaging_app.gui.worker import SensorgramComputationResult
from lspr_imaging_app.gui.analysis_tasks import _roi_absorbance_signature
from lspr_imaging_app.processing.analysis import metric_value_from_fit, metric_value_from_spectrum
from lspr_imaging_app.processing.trace_statistics import (
    aggregate_group_traces,
    normalize_to_baseline_window,
    reject_spikes_hampel,
    reject_spikes_running_median,
    smooth_moving_average,
    smooth_savgol,
)


class AnalysisController:
    def __init__(self, window) -> None:
        self.window = window

    def _sensorgram_selection_color(self) -> QColor:
        selected_roi_ids = tuple(self.window._selected_spectrum_roi_ids())
        if len(selected_roi_ids) == 1:
            return QColor(self.window._roi_spectrum_color(int(selected_roi_ids[0])))
        if selected_roi_ids:
            return QColor("#38bdf8")
        return QColor("#22c55e")

    def update_selection_highlight(self, *, force: bool = False) -> None:
        selected_signature = tuple(self.window._selected_spectrum_roi_ids())
        if not force and selected_signature == getattr(self.window, "_sensorgram_selection_highlight_signature", None):
            return
        self.window._sensorgram_selection_highlight_signature = selected_signature
        has_data = self.window._sensorgram_spectral_cube_indices.size > 0
        selected = bool(selected_signature) and has_data
        color = self._sensorgram_selection_color()
        curve_color = color if selected else QColor("#22c55e")
        curve_width = 3.2 if selected else 2.2
        curve_symbol_size = 7.5 if selected else 6
        point_size = 11 if selected else 9
        point_brush = pg.mkBrush(color.lighter(138) if selected else "#f8fafc")
        point_pen = pg.mkPen(color.darker(125) if selected else "#22c55e", width=2.0)
        self.window.sensorgram_curve.setPen(pg.mkPen(curve_color, width=curve_width))
        self.window.sensorgram_curve.setSymbolSize(curve_symbol_size)
        self.window.sensorgram_curve.setSymbolBrush(pg.mkBrush(color.lighter(130) if selected else "#22c55e"))
        self.window.sensorgram_curve.setSymbolPen(pg.mkPen(curve_color.darker(120) if selected else "#bbf7d0", width=1.4))
        self.window.sensorgram_current_point.setSymbolSize(point_size)
        self.window.sensorgram_current_point.setSymbolBrush(point_brush)
        self.window.sensorgram_current_point.setSymbolPen(point_pen)

    def _store_roi_absorbance_cache(self, result) -> None:
        area_roi_results = getattr(result, "area_roi_results", None)
        if not area_roi_results:
            return
        area_roi_by_id = {int(area_roi.area_roi_id): area_roi for area_roi in self.window._state.area_rois}
        for area_roi_id, roi_result in area_roi_results.items():
            area_roi = area_roi_by_id.get(int(area_roi_id))
            if area_roi is None:
                continue
            signature = self.window._roi_absorbance_signature(area_roi)
            if signature is None:
                continue
            self.window._roi_absorbance_cache[signature] = roi_result
            self.window._roi_absorbance_cache.move_to_end(signature)
            while len(self.window._roi_absorbance_cache) > self.window.ROI_ABSORBANCE_CACHE_SIZE:
                self.window._roi_absorbance_cache.popitem(last=False)
        self._refresh_cached_roi_ids_snapshot()

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
            self.window._sensorgram_cache[signature] = result
            self.window._sensorgram_cache.move_to_end(signature)
            while len(self.window._sensorgram_cache) > self.window.SENSORGRAM_CACHE_SIZE:
                self.window._sensorgram_cache.popitem(last=False)
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

    def refresh_absorbance_spectrum(self) -> None:
        self.window._refresh_absorbance_spectrum()

    def calculate_sensorgram(self) -> None:
        self.window._calculate_sensorgram_for_range()

    def stop_sensorgram(self) -> None:
        self.window._stop_sensorgram_calculation()

    def on_fit_settings_changed(self, *_args) -> None:
        self.window._on_analysis_fit_settings_changed(*_args)

    def on_spectral_cube_range_changed(self, *_args) -> None:
        self.window._on_analysis_spectral_cube_range_changed(*_args)

    def on_wavelength_range_changed(self, *_args) -> None:
        self._on_analysis_wavelength_range_changed(*_args)

    def update_control_state(self) -> None:
        self.window._update_analysis_control_state()

    def update_plot_labels(self) -> None:
        self.window._update_sensorgram_plot_labels()

    def _sensorgram_time_mode_metadata(self):
        """The loaded `ImagingAcquisitionMetadata` if it has per-image timing
        data (enough to plot the sensorgram against real elapsed time),
        otherwise None - falls back to plotting by raw spectral_cube_index,
        the behavior for every dataset without acquisition metadata loaded."""
        dataset = self.window._state.dataset
        metadata = getattr(dataset, "acquisition_metadata", None) if dataset is not None else None
        if metadata is None or not metadata.image_timings:
            return None
        return metadata

    @staticmethod
    def _sensorgram_time_anchor_ms(metadata) -> int:
        """t=0 for the elapsed-time axis: the dataset's recorded start time
        if parseable (consistent with how comment-event/image timestamps are
        already anchored), otherwise the earliest recorded image timing."""
        if metadata.started_at_utc:
            try:
                return int(datetime.fromisoformat(metadata.started_at_utc.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                pass
        return min(timing.acquired_at_unix_ms for timing in metadata.image_timings)

    def _sensorgram_x_values(self, spectral_cube_indices) -> np.ndarray:
        """Map spectral cube indices to sensorgram x-axis display values:
        elapsed seconds since the dataset's start when acquisition metadata
        with per-image timing is loaded (NaN for any cube with no timing
        entry, so it's dropped rather than plotted at a misleading raw-index
        position), otherwise the raw cube indices themselves, unchanged from
        today's behavior."""
        indices = np.asarray(list(spectral_cube_indices), dtype=np.int64)
        metadata = self._sensorgram_time_mode_metadata()
        if metadata is None:
            return indices.astype(np.float64)
        anchor_ms = self._sensorgram_time_anchor_ms(metadata)
        values = np.full(indices.shape, np.nan, dtype=np.float64)
        for position, cube_index in enumerate(indices):
            timing = metadata.earliest_timing_for_cube(int(cube_index))
            if timing is not None:
                values[position] = (timing.acquired_at_unix_ms - anchor_ms) / 1000.0
        return values

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

    def prepare_absorbance_spectrum_payload(self):
        return self._prepare_absorbance_spectrum_payload()

    def available_analysis_spectral_cubes(self) -> list[int]:
        return self.window._available_analysis_spectral_cubes()

    def calculate_sensorgram_for_range(self) -> None:
        if not self.window._analysis_enabled:
            self.clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if self.window._state.dataset is None:
            self.clear_sensorgram("Load a dataset before calculating the sensorgram.")
            return
        if self.window._chromatic_setup_active:
            self.clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if not selected_roi_ids:
            self.clear_sensorgram("Detect or select ROIs before calculating the sensorgram.")
            return
        selected_roi_id_set = set(selected_roi_ids)
        selected_source_rois = [deepcopy(roi) for roi in self.window._state.area_rois if roi.area_roi_id in selected_roi_id_set]
        if not selected_source_rois:
            self.clear_sensorgram("Detect or select ROIs before calculating the sensorgram.")
            return
        spectral_cubes = self.available_analysis_spectral_cubes()
        if not spectral_cubes:
            self.clear_sensorgram("No spectral cubes are available in the selected range.")
            return
        signature = self.window._sensorgram_signature_for_selection(spectral_cubes, selected_roi_ids, selected_source_rois)
        if signature is None:
            self.clear_sensorgram("No spectra are available in the selected spectral cube range.")
            return
        if self.window._sensorgram_running and self.window._sensorgram_running_signature == signature:
            return
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is not None and not self.window._sensorgram_running:
            self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
            return
        if self.window._sensorgram_running:
            self.window._pending_sensorgram_payload = (signature, spectral_cubes, selected_roi_ids, selected_source_rois)
            self.window._set_sensorgram_summary_text(
                f"{self.window._analysis_metric_label()} | Updating {len(spectral_cubes)} spectral cubes"
            )
            return
        self._start_sensorgram_worker(signature, spectral_cubes, selected_roi_ids, selected_source_rois)

    def preview_sensorgram_from_cache(self) -> bool:
        if not self.window._analysis_enabled or self.window._state.dataset is None or self.window._chromatic_setup_active:
            return False
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if not selected_roi_ids:
            return False
        selected_roi_id_set = set(selected_roi_ids)
        selected_source_rois = [deepcopy(roi) for roi in self.window._state.area_rois if roi.area_roi_id in selected_roi_id_set]
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
        import threading

        from lspr_imaging_app.gui.worker import FunctionWorker
        from lspr_imaging_app.gui.analysis_tasks import (
            _sensorgram_metric_task,
            _absorbance_spectrum_fast_task,
        )

        use_fast_path = self._fast_spectrum_path_eligible(selected_source_rois)

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
        self.window._set_status_text("Preparing fitted sensorgram...")
        self.window._begin_busy("Preparing fitted sensorgram...", determinate=True)

        if use_fast_path:
            def spectral_cube_payload_builder(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
                return self._prepare_fast_spectrum_payload_for_spectral_cube(
                    spectral_cube_index,
                    selected_roi_ids,
                    selected_source_rois,
                )
            task_fn = _absorbance_spectrum_fast_task
        else:
            def spectral_cube_payload_builder(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
                return self._cached_sensorgram_spectral_cube_payload(
                    spectral_cube_index,
                    selected_roi_ids,
                    selected_source_rois,
                )
            task_fn = None

        def spectral_cube_result_cache_get(spectral_cube_index, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
            return self._cached_sensorgram_spectral_cube_result(
                spectral_cube_index,
                selected_roi_ids,
                selected_source_rois,
            )

        def spectral_cube_result_cache_store(spectral_cube_index, result, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois):
            self._store_sensorgram_spectral_cube_result(
                spectral_cube_index,
                selected_roi_ids,
                selected_source_rois,
                result,
            )

        wavelength_range = self.window._analysis_wavelength_range()
        reduction_method, trimmed_mean_fraction, formula_key = self._roi_math_signature_elements()
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
            wl_min=None if wavelength_range is None else wavelength_range[0],
            wl_max=None if wavelength_range is None else wavelength_range[1],
            fit_method_key=self._analysis_fit_method_key(),
            reduction_method=reduction_method,
            trimmed_mean_fraction=trimmed_mean_fraction,
            formula_key=formula_key,
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.partial.connect(
            lambda point, request_id=request_id, total=len(spectral_cubes): self.on_sensorgram_partial_result(request_id, total, point)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self.on_sensorgram_ready(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self.on_sensorgram_failed(request_id, message))
        self.window._thread_pool.start(worker)

    def stop_sensorgram_calculation(self) -> None:
        if not self.window._sensorgram_running or self.window._sensorgram_cancel_event is None:
            return
        self.window._sensorgram_cancel_event.set()
        self.window._pending_sensorgram_payload = None
        self.window._set_sensorgram_summary_text("Stopping sensorgram calculation...")
        self.window._set_status_text("Stopping sensorgram calculation...")

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
        self.window._end_busy()
        self.window._sync_busy_cursor_state()
        if not self.window._analysis_enabled:
            self.window._update_analysis_control_state()
            return
        if signature:
            self._apply_cached_sensorgram_result(signature, result, preview=False)
        else:
            self._apply_cached_sensorgram_result((), result, preview=False)
        if result.cancelled:
            self.window._set_status_text("SG | stopped")
        else:
            timing = self.window._compact_timing_text(("prep", result.prep_seconds), ("fit", result.fit_seconds))
            self.window._set_status_text(f"SG | {timing}" if timing else "SG | done")
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
        self.window._end_busy()
        self.window._sync_busy_cursor_state()
        self.window._update_analysis_control_state()
        self.window._set_sensorgram_summary_text(f"Sensorgram failed: {message}")
        self.window._background_error("Sensorgram", message)
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
        message = reason or f"{metric_label} sensorgram is out of date | Press Calculate all spectral cubes{range_text}"
        self.clear_sensorgram(message)

    # ------------------------------------------------------------------
    # Cache / signature / serialization / payload-builder methods
    # (moved from MainWindow)
    # ------------------------------------------------------------------

    def _roi_math_signature_elements(self) -> tuple[str, float, str]:
        """(reduction_method, trimmed_mean_fraction, formula_key) - the three
        ROI's-math settings that change what a ROI pair's masked pixels
        reduce to. Appended to every cache signature whose cached value would
        otherwise go stale when the user changes Reduction/Formula without
        anything else changing."""
        settings = self.window._state.area_roi_settings
        return (
            str(settings.reduction_method),
            round(float(settings.trimmed_mean_fraction), 4),
            str(settings.formula_key),
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
            *self._roi_math_signature_elements(),
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
            *self._roi_math_signature_elements(),
        )

    def _cached_sensorgram_spectral_cube_payload(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
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
        payload = self._prepare_absorbance_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if payload is None:
            return None
        with self.window._analysis_cache_lock:
            self.window._sensorgram_spectral_cube_payload_cache[signature] = payload
            self.window._sensorgram_spectral_cube_payload_cache.move_to_end(signature)
            while len(self.window._sensorgram_spectral_cube_payload_cache) > self.window.SENSORGRAM_SPECTRAL_CUBE_PAYLOAD_CACHE_SIZE:
                self.window._sensorgram_spectral_cube_payload_cache.popitem(last=False)
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
    ) -> AbsorbanceSpectrumResult | None:
        """Per-frame math-layer cache (sample/reference means -> AbsorbanceSpectrumResult),
        keyed by the same fit-parameter-independent signature as the payload cache above, so
        changing only poly_order/metric_key never forces re-reading pixels for a frame whose
        sample/reference means are already known."""
        signature = self._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if signature is None:
            return None
        with self.window._analysis_cache_lock:
            cached = self.window._sensorgram_spectral_cube_result_cache.get(signature)
            if cached is not None:
                self.window._sensorgram_spectral_cube_result_cache.move_to_end(signature)
            return cached

    def _store_sensorgram_spectral_cube_result(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
        result: AbsorbanceSpectrumResult,
    ) -> None:
        signature = self._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if signature is None:
            return
        with self.window._analysis_cache_lock:
            self.window._sensorgram_spectral_cube_result_cache[signature] = result
            self.window._sensorgram_spectral_cube_result_cache.move_to_end(signature)
            while len(self.window._sensorgram_spectral_cube_result_cache) > self.window.SENSORGRAM_SPECTRAL_CUBE_RESULT_CACHE_SIZE:
                self.window._sensorgram_spectral_cube_result_cache.popitem(last=False)

    def _absorbance_spectrum_signature_for_source_rois(
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
            *self._roi_math_signature_elements(),
        )

    def _absorbance_spectrum_signature(self) -> tuple[object, ...] | None:
        return self._absorbance_spectrum_signature_for_source_rois(self.window._selected_source_rois_snapshot())

    def _cached_absorbance_result_from_roi_cache(
        self,
        selected_source_rois: list[AreaRoi],
    ) -> AbsorbanceSpectrumResult | None:
        if not selected_source_rois:
            return None
        roi_results: dict[int, AbsorbanceSpectrumResult] = {}
        for roi in selected_source_rois:
            roi_signature = self.window._roi_absorbance_signature(roi)
            if roi_signature is None:
                return None
            cached_result = self.window._roi_absorbance_cache.get(roi_signature)
            if cached_result is None:
                return None
            roi_results[int(roi.area_roi_id)] = cached_result
        first_result = next(iter(roi_results.values()), None)
        if first_result is None:
            return None
        return AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(first_result.wavelengths_nm, dtype=np.float64),
            absorbance=np.asarray(first_result.absorbance, dtype=np.float64),
            sample_mean=np.asarray(first_result.sample_mean, dtype=np.float64),
            reference_mean=np.asarray(first_result.reference_mean, dtype=np.float64),
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

    @staticmethod
    def _serialize_absorbance_result(result: AbsorbanceSpectrumResult) -> dict:
        return {
            "wavelengths_nm": [float(value) for value in np.asarray(result.wavelengths_nm, dtype=np.float64)],
            "absorbance": [float(value) for value in np.asarray(result.absorbance, dtype=np.float64)],
            "sample_mean": [float(value) for value in np.asarray(result.sample_mean, dtype=np.float64)],
            "reference_mean": [float(value) for value in np.asarray(result.reference_mean, dtype=np.float64)],
            "sample_pixel_count": [int(value) for value in np.asarray(result.sample_pixel_count, dtype=np.int32)],
            "reference_pixel_count": [int(value) for value in np.asarray(result.reference_pixel_count, dtype=np.int32)],
            "load_seconds": float(result.load_seconds),
            "roi_seconds": float(result.roi_seconds),
            "fit_seconds": float(result.fit_seconds),
            "total_seconds": float(result.total_seconds),
            "reduction_method": str(result.reduction_method),
            "formula_key": str(result.formula_key),
            "area_roi_results": {
                str(int(roi_id)): AnalysisController._serialize_absorbance_result(roi_result)
                for roi_id, roi_result in (result.area_roi_results or {}).items()
            },
        }

    @staticmethod
    def _deserialize_absorbance_result(payload) -> AbsorbanceSpectrumResult:
        if not isinstance(payload, dict):
            return AbsorbanceSpectrumResult(
                wavelengths_nm=np.asarray([], dtype=np.float64),
                absorbance=np.asarray([], dtype=np.float64),
                sample_mean=np.asarray([], dtype=np.float64),
                reference_mean=np.asarray([], dtype=np.float64),
                sample_pixel_count=np.asarray([], dtype=np.int32),
                reference_pixel_count=np.asarray([], dtype=np.int32),
            )
        raw_roi_results = payload.get("area_roi_results") or payload.get("roi_results", {})
        roi_results: dict[int, AbsorbanceSpectrumResult] = {}
        if isinstance(raw_roi_results, dict):
            for key, value in raw_roi_results.items():
                try:
                    roi_id = int(key)
                except Exception:
                    continue
                roi_results[roi_id] = AnalysisController._deserialize_absorbance_result(value)
        return AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(payload.get("wavelengths_nm", []), dtype=np.float64),
            absorbance=np.asarray(payload.get("absorbance", []), dtype=np.float64),
            sample_mean=np.asarray(payload.get("sample_mean") or payload.get("spot_mean", []), dtype=np.float64),
            reference_mean=np.asarray(payload.get("reference_mean") or payload.get("ring_mean", []), dtype=np.float64),
            sample_pixel_count=np.asarray(payload.get("sample_pixel_count") or payload.get("spot_pixel_count", []), dtype=np.int32),
            reference_pixel_count=np.asarray(payload.get("reference_pixel_count") or payload.get("ring_pixel_count", []), dtype=np.int32),
            load_seconds=float(payload.get("load_seconds", 0.0)),
            roi_seconds=float(payload.get("roi_seconds", 0.0)),
            fit_seconds=float(payload.get("fit_seconds", 0.0)),
            total_seconds=float(payload.get("total_seconds", 0.0)),
            reduction_method=str(payload.get("reduction_method", "mean")),
            formula_key=str(payload.get("formula_key", "absorbance")),
            area_roi_results=roi_results,
        )

    @staticmethod
    def _serialize_sensorgram_result(result: SensorgramComputationResult) -> dict:
        return {
            "spectral_cube_indices": [int(value) for value in np.asarray(result.spectral_cube_indices, dtype=np.int32)],
            "metric_values": [float(value) for value in np.asarray(result.metric_values, dtype=np.float64)],
            "metric_signal": [float(value) for value in np.asarray(result.metric_signal, dtype=np.float64)],
            "completed_count": int(result.completed_count),
            "total_count": int(result.total_count),
            "prep_seconds": float(result.prep_seconds),
            "fit_seconds": float(result.fit_seconds),
            "total_seconds": float(result.total_seconds),
            "cancelled": bool(result.cancelled),
        }

    @staticmethod
    def _deserialize_sensorgram_result(payload) -> SensorgramComputationResult:
        if not isinstance(payload, dict):
            return SensorgramComputationResult(
                spectral_cube_indices=np.asarray([], dtype=np.int32),
                metric_values=np.asarray([], dtype=np.float64),
                metric_signal=np.asarray([], dtype=np.float64),
                completed_count=0,
                total_count=0,
                cancelled=False,
            )
        return SensorgramComputationResult(
            spectral_cube_indices=np.asarray(payload.get("spectral_cube_indices", []), dtype=np.int32),
            metric_values=np.asarray(payload.get("metric_values", []), dtype=np.float64),
            metric_signal=np.asarray(payload.get("metric_signal", []), dtype=np.float64),
            completed_count=int(payload.get("completed_count", 0)),
            total_count=int(payload.get("total_count", 0)),
            prep_seconds=float(payload.get("prep_seconds", 0.0)),
            fit_seconds=float(payload.get("fit_seconds", 0.0)),
            total_seconds=float(payload.get("total_seconds", 0.0)),
            cancelled=bool(payload.get("cancelled", False)),
        )

    def _prepare_absorbance_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_source_rois:
            return None
        preprocessing = deepcopy(self.window._state.preprocessing)
        flatten_mask_settings = deepcopy(self.window._state.area_roi_settings) if preprocessing.flatten_background_exclude_mask else None
        measurement_settings = deepcopy(self.window._state.area_roi_settings)
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
            measurement_payload.append(
                (
                    float(wavelength),
                    str(record.path),
                    preprocessing_rois,
                    affine_matrix,
                    bool(external_mask_processed),
                    None if external_mask is None else np.asarray(external_mask, dtype=bool),
                )
            )
        if not measurement_payload:
            return None
        return (
            measurement_payload,
            preprocessing,
            flatten_mask_settings,
            measurement_settings,
            self.window._absorbance_roi_mask_cache,
            self.window._analysis_cache_lock,
            int(self.window.ABSORBANCE_ROI_MASK_CACHE_SIZE),
            deepcopy(selected_source_rois),
            selected_roi_ids,
            float(self.window._state.area_roi_settings.reference_inner_radius_px),
            float(self.window._state.area_roi_settings.reference_outer_radius_px),
            deepcopy(self.window._state.mask) if self.window._mask_section_applied() else None,
        )

    def _prepare_fast_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple,
        selected_source_rois: list,
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
        """
        from lspr_imaging_app.gui.analysis_tasks import compute_roi_union_bounding_box
        from lspr_imaging_app.io.dataset import load_image_shape
        from lspr_imaging_app.processing.preprocess import spatial_output_shape

        if self.window._state.dataset is None or not selected_source_rois:
            return None
        preprocessing = self.window._state.preprocessing

        # Mirror ignored_pixel_mask's own gating: an external mask only excludes
        # pixels from the absorbance calculation when ignore_marked_pixels is
        # on. Fetching it unconditionally and applying it in the fast task
        # regardless of this flag would silently diverge from the slow path.
        exclude_marked_pixels = bool(getattr(self.window._state.area_roi_settings, "ignore_marked_pixels", False))

        measurement_payload: list[tuple[float, np.ndarray | None, np.ndarray | None]] = []
        affine_matrices: list[np.ndarray | None] = []
        first_record = None
        for wavelength in self.window._wavelength_values:
            record = self.window._record_map.get((spectral_cube_index, wavelength))
            if record is None or is_excluded(self.window._state.image_exclusions, spectral_cube_index, wavelength):
                continue
            if first_record is None:
                first_record = record
            image_key = (spectral_cube_index, float(wavelength))
            affine_matrix = self.window._chromatic_affine_for_image_key(image_key)
            if affine_matrix is not None:
                affine_matrix = np.asarray(affine_matrix, dtype=np.float64)
            external_mask = None
            if exclude_marked_pixels:
                external_mask, _ = self.window._effective_external_mask_for_record(record.path, processed_space=True)
            measurement_payload.append(
                (
                    float(wavelength),
                    affine_matrix,
                    None if external_mask is None else np.asarray(external_mask, dtype=bool),
                )
            )
            affine_matrices.append(affine_matrix)
        if not measurement_payload or first_record is None:
            return None

        try:
            raw_shape = load_image_shape(str(first_record.path))
        except Exception:
            return None
        image_height, image_width = spatial_output_shape(raw_shape, preprocessing)

        box = compute_roi_union_bounding_box(
            selected_source_rois,
            float(self.window._state.area_roi_settings.reference_outer_radius_px),
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
        mask_state = deepcopy(self.window._state.mask) if self.window._mask_section_applied() else None
        background_mask_settings = (
            deepcopy(self.window._state.area_roi_settings)
            if bool(getattr(preprocessing, "flatten_background_exclude_mask", False))
            else None
        )

        return (
            self.window._state.dataset,
            int(spectral_cube_index),
            measurement_payload,
            dict(self.window._record_map),
            deepcopy(selected_source_rois),
            selected_roi_ids,
            float(self.window._state.area_roi_settings.reference_inner_radius_px),
            float(self.window._state.area_roi_settings.reference_outer_radius_px),
            box,
            deepcopy(preprocessing),
            raw_shape,
            mask_state,
            background_mask_settings,
        )

    def _prepare_absorbance_spectrum_payload(
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
        selected_source_rois = self.window._selected_source_rois_snapshot() if selected_source_rois is None else list(selected_source_rois)
        if not selected_source_rois:
            return None
        signature = self._absorbance_spectrum_signature_for_source_rois(selected_source_rois)
        if signature is None:
            return None
        spectral_cube_index = int(signature[0])
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)

        from lspr_imaging_app.gui.analysis_tasks import _absorbance_spectrum_fast_task, _absorbance_spectrum_task

        if self._fast_spectrum_path_eligible(selected_source_rois):
            payload = self._prepare_fast_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois)
            if payload is not None:
                return signature, payload, _absorbance_spectrum_fast_task
            # Fast payload builder found genuinely nothing to compute for this
            # spectral cube (e.g. no records) — fall through to the full-plane path
            # rather than reporting "no spectrum" when the slow path might
            # still have an answer.
        payload = self._prepare_absorbance_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois)
        if payload is None:
            return None
        return signature, payload, _absorbance_spectrum_task

    # ------------------------------------------------------------------
    # Result / event handler methods (moved from MainWindow)
    # ------------------------------------------------------------------

    def _on_absorbance_spectrum_payload_ready(
        self,
        request_id: int,
        expected_signature: tuple[object, ...],
        prepared: tuple[tuple[object, ...], tuple[object, ...], object] | None,
    ) -> None:
        if request_id != self.window._absorbance_prep_request_id:
            return
        self.window._absorbance_prep_running = False
        if self.window._absorbance_prep_started_at is not None:
            self.window._append_workflow_log(
                f"Spec prep done | {self.window._format_elapsed_seconds(time.perf_counter() - self.window._absorbance_prep_started_at)}",
                level="success",
            )
        self.window._absorbance_prep_started_at = None
        self.window._absorbance_prep_request_signature = None
        if prepared is None:
            self.window._end_busy("Select ROIs to show absorbance spectrum.")
            return
        signature, payload, task_fn = prepared
        if signature != expected_signature:
            self.window._absorbance_spectrum_dirty = True
            self.window._end_busy("Select ROIs to show absorbance spectrum.")
            return
        self.window._pending_absorbance_spectrum_payload = (signature, payload, task_fn)
        self.window._start_pending_absorbance_spectrum_refresh(reuse_busy=True)

    def _on_absorbance_spectrum_payload_failed(self, request_id: int, message: str) -> None:
        if request_id != self.window._absorbance_prep_request_id:
            return
        self.window._absorbance_prep_running = False
        self.window._absorbance_prep_started_at = None
        self.window._absorbance_prep_request_signature = None
        self.window._end_busy()
        self.window._background_error("Spectral absorbance prep", message)

    def _refresh_absorbance_spectrum(self) -> None:
        start_time = time.perf_counter()
        if not self.window._analysis_enabled:
            self.window._clear_absorbance_spectrum("Analysis calculations are disabled for this panel.")
            return
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if not selected_source_rois:
            self.window._clear_absorbance_spectrum("Select ROIs to show absorbance spectrum.")
            return
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        roi_signatures = [self.window._roi_absorbance_signature(roi) for roi in selected_source_rois]
        if any(signature is None for signature in roi_signatures):
            self.window._clear_absorbance_spectrum("Select ROIs to show absorbance spectrum.")
            return
        if len(selected_source_rois) == 1:
            roi_signature = roi_signatures[0]
            assert roi_signature is not None
            cached_roi_result = self.window._roi_absorbance_cache.get(roi_signature)
            if cached_roi_result is not None:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_roi_result)
                self.window._roi_absorbance_cache.move_to_end(roi_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._absorbance_spectrum_signature_for_source_rois(selected_source_rois)
        if signature is not None:
            cached_result = self.window._cached_absorbance_result_for_selection(signature, selected_roi_ids, selected_source_rois)
            if cached_result is not None:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_result)
                spectral_cube_signature = self.window._absorbance_spectral_cube_signature(signature)
                if spectral_cube_signature is not None and spectral_cube_signature in self.window._absorbance_spectral_cube_cache:
                    self.window._absorbance_spectral_cube_cache.move_to_end(spectral_cube_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        missing_source_rois = [
            roi
            for roi, signature_value in zip(selected_source_rois, roi_signatures, strict=False)
            if signature_value is None or self.window._roi_absorbance_cache.get(signature_value) is None
        ]
        target_source_rois = missing_source_rois if missing_source_rois else selected_source_rois
        signature = self._absorbance_spectrum_signature_for_source_rois(target_source_rois)
        if signature is None:
            self.window._clear_absorbance_spectrum("Select ROIs to show absorbance spectrum.")
            return
        if self.window._absorbance_spectrum_running and self.window._absorbance_spectrum_running_signature == signature:
            return
        if (
            self.window._pending_absorbance_spectrum_payload is not None
            and self.window._pending_absorbance_spectrum_payload[0] == signature
        ):
            return
        if (
            signature in self.window._absorbance_spectrum_cache
        ):
            self.window._absorbance_spectrum_dirty = False
            self._apply_absorbance_spectrum_result(self.window._absorbance_spectrum_cache[signature])
            self.window._absorbance_spectrum_cache.move_to_end(signature)
            elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
            self.window._set_status_text(f"Spec | cache {elapsed}")
            return
        self.window._start_absorbance_spectrum_preparation(signature, target_source_rois)

    def _on_analysis_fit_settings_changed(self, *_args) -> None:
        start_time = time.perf_counter()
        self.window._save_control_preferences()
        if self.window._analysis_live_preview_enabled:
            self.window._schedule_sensorgram_refresh()
        else:
            self.window._mark_sensorgram_stale(
                f"{self.window._analysis_metric_label()} sensorgram is out of date | Press Calculate all spectral cubes"
            )
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if len(selected_source_rois) == 1:
            roi_signature = self.window._roi_absorbance_signature(selected_source_rois[0])
            if roi_signature is not None and roi_signature in self.window._roi_absorbance_cache and not self.window._absorbance_spectrum_dirty:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(self.window._roi_absorbance_cache[roi_signature])
                self.window._roi_absorbance_cache.move_to_end(roi_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._absorbance_spectrum_signature()
        if signature is not None and signature in self.window._absorbance_spectrum_cache and not self.window._absorbance_spectrum_dirty:
            self._apply_absorbance_spectrum_result(self.window._absorbance_spectrum_cache[signature])
            self.window._absorbance_spectrum_cache.move_to_end(signature)
            elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
            self.window._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
            self.window._set_status_text(f"Spec | cache {elapsed}")
        elif self.window._analysis_live_preview_enabled:
            self.window._schedule_absorbance_spectrum_refresh()

    def _on_analysis_spectral_cube_range_changed(self, *_args) -> None:
        self.window._save_control_preferences()
        if self.window.analysis_start_spectral_cube_spin.value() > self.window.analysis_end_spectral_cube_spin.value():
            self.window.analysis_start_spectral_cube_spin.blockSignals(True)
            self.window.analysis_end_spectral_cube_spin.blockSignals(True)
            start = min(self.window.analysis_start_spectral_cube_spin.value(), self.window.analysis_end_spectral_cube_spin.value())
            end = max(self.window.analysis_start_spectral_cube_spin.value(), self.window.analysis_end_spectral_cube_spin.value())
            self.window.analysis_start_spectral_cube_spin.setValue(start)
            self.window.analysis_end_spectral_cube_spin.setValue(end)
            self.window.analysis_start_spectral_cube_spin.blockSignals(False)
            self.window.analysis_end_spectral_cube_spin.blockSignals(False)
        low = int(self.window.analysis_start_spectral_cube_spin.value())
        high = int(self.window.analysis_end_spectral_cube_spin.value())
        if self.window.analysis_spectral_cube_range_slider.values() != (low, high):
            self.window.analysis_spectral_cube_range_slider.setValues(low, high)
        if self.window._analysis_live_preview_enabled and not self.preview_sensorgram_from_cache():
            self.mark_stale(
                f"{self.window._analysis_metric_label()} sensorgram is out of date | Press Calculate all spectral cubes"
            )
        elif not self.window._analysis_live_preview_enabled:
            self.window._mark_sensorgram_stale()

    def on_spectral_cube_range_slider_changed(self, low: int, high: int) -> None:
        start_spin = self.window.analysis_start_spectral_cube_spin
        end_spin = self.window.analysis_end_spectral_cube_spin
        if start_spin.value() != low:
            start_spin.setValue(low)
        if end_spin.value() != high:
            end_spin.setValue(high)

    def select_all_spectral_cube_range(self) -> None:
        if not self.window._spectral_cube_values:
            return
        self.window.analysis_start_spectral_cube_spin.setValue(int(min(self.window._spectral_cube_values)))
        self.window.analysis_end_spectral_cube_spin.setValue(int(max(self.window._spectral_cube_values)))

    def _on_analysis_wavelength_range_changed(self, *_args) -> None:
        min_spin = self.window.analysis_wavelength_min_spin
        max_spin = self.window.analysis_wavelength_max_spin
        if min_spin.value() > max_spin.value():
            min_spin.blockSignals(True)
            max_spin.blockSignals(True)
            low = min(min_spin.value(), max_spin.value())
            high = max(min_spin.value(), max_spin.value())
            min_spin.setValue(low)
            max_spin.setValue(high)
            min_spin.blockSignals(False)
            max_spin.blockSignals(False)
        if self.window._wavelength_values:
            low_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - min_spin.value()))
            high_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - max_spin.value()))
            if self.window.analysis_wavelength_range_slider.values() != (low_index, high_index):
                self.window.analysis_wavelength_range_slider.setValues(low_index, high_index)
        self.on_fit_settings_changed()

    def on_wavelength_range_slider_changed(self, low_index: int, high_index: int) -> None:
        if not self.window._wavelength_values:
            return
        low_value = float(self.window._wavelength_values[low_index])
        high_value = float(self.window._wavelength_values[high_index])
        min_spin = self.window.analysis_wavelength_min_spin
        max_spin = self.window.analysis_wavelength_max_spin
        if min_spin.value() != low_value:
            min_spin.setValue(low_value)
        if max_spin.value() != high_value:
            max_spin.setValue(high_value)

    def select_all_wavelength_range(self) -> None:
        if not self.window._wavelength_values:
            return
        self.window.analysis_wavelength_min_spin.setValue(float(min(self.window._wavelength_values)))
        self.window.analysis_wavelength_max_spin.setValue(float(max(self.window._wavelength_values)))

    def _calculate_sensorgram_for_range(self) -> None:
        if not self.window._analysis_enabled:
            self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if self.window._state.dataset is None:
            self.window._clear_sensorgram("Load a dataset before calculating the sensorgram.")
            return
        if self.window._chromatic_setup_active:
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
        if cached_signature is not None:
            with self.window._analysis_cache_lock:
                cached_sensorgram = self.window._sensorgram_cache.get(cached_signature)
                if cached_sensorgram is not None:
                    self.window._sensorgram_cache.move_to_end(cached_signature)
                    self.window._append_workflow_log(
                        f"SG cache hit | spectral_cubes {len(spectral_cubes)} | metric {self.window._analysis_metric_label()}",
                        level="debug",
                    )
                    self.window._append_workflow_log(
                        f"SG cache summary | payload hit {len(spectral_cubes)} build 0 | result hit 1 build 0",
                        level="debug",
                    )
                    self.window._sensorgram_spectral_cube_indices = np.asarray(cached_sensorgram.spectral_cube_indices, dtype=np.int32)
                    self.window._sensorgram_metric_values = np.asarray(cached_sensorgram.metric_values, dtype=np.float64)
                    self.window._sensorgram_metric_signal = np.asarray(cached_sensorgram.metric_signal, dtype=np.float64)
                    self.window._set_sensorgram_series(self.window._sensorgram_spectral_cube_indices, self.window._sensorgram_metric_values)
                    summary = (
                        f"{self.window._analysis_metric_label()} | Cached {cached_sensorgram.completed_count}/"
                        f"{cached_sensorgram.total_count} spectral cubes{self._poly_order_summary_suffix()}"
                    )
                    self.window._set_sensorgram_summary_text(summary)
                    self.window._set_status_text("Sensorgram cache used.")
                    return
        self.window._sensorgram_running_signature = cached_signature

        self.window._append_workflow_log(
            f"SG calc start | rois {len(selected_roi_ids)} | spectral_cubes {len(spectral_cubes)} | metric {self.window._analysis_metric_label()}",
            level="info",
        )
        self._start_sensorgram_worker(cached_signature, spectral_cubes, selected_roi_ids, selected_source_rois)

    def _on_sensorgram_partial_result(
        self,
        request_id: int,
        total_count: int,
        point,
    ) -> None:
        if request_id != self.window._sensorgram_request_id or not self.window._analysis_enabled:
            return
        metric_value = float("nan") if point.metric_value is None else float(point.metric_value)
        metric_signal = float("nan") if point.metric_signal is None else float(point.metric_signal)
        self.window._sensorgram_spectral_cube_indices = np.append(self.window._sensorgram_spectral_cube_indices, int(point.spectral_cube_index)).astype(np.int32, copy=False)
        self.window._sensorgram_metric_values = np.append(self.window._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self.window._sensorgram_metric_signal = np.append(self.window._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        self.window._set_sensorgram_series(
            self.window._sensorgram_spectral_cube_indices,
            self.window._sensorgram_metric_values,
            summary_text=(
                f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_spectral_cube_indices.size}/{total_count} spectral cubes"
            ),
        )

    def _on_sensorgram_ready(self, request_id: int, result: SensorgramComputationResult) -> None:
        if request_id != self.window._sensorgram_request_id:
            return
        self.window._sensorgram_running = False
        self.window._sensorgram_cancel_event = None
        self.window._end_busy()
        if not self.window._analysis_enabled:
            self.window._sensorgram_running_signature = None
            self.window._update_analysis_control_state()
            return
        self.window._sensorgram_spectral_cube_indices = np.asarray(result.spectral_cube_indices, dtype=np.int32)
        self.window._sensorgram_metric_values = np.asarray(result.metric_values, dtype=np.float64)
        self.window._sensorgram_metric_signal = np.asarray(result.metric_signal, dtype=np.float64)
        sensorgram_signature = self.window._sensorgram_running_signature
        self.window._sensorgram_running_signature = None
        if sensorgram_signature is not None:
            with self.window._analysis_cache_lock:
                self.window._sensorgram_cache[sensorgram_signature] = result
                self.window._sensorgram_cache.move_to_end(sensorgram_signature)
                while len(self.window._sensorgram_cache) > self.window.SENSORGRAM_CACHE_SIZE:
                    self.window._sensorgram_cache.popitem(last=False)
            self.window._append_workflow_log(
                f"SG cache store | spectral_cubes {int(result.completed_count)}/{int(result.total_count)}",
                level="debug",
            )
            self.window._append_workflow_log(
                f"SG cache summary | payload result cached | prep {self.window._format_elapsed_seconds(result.prep_seconds)}",
                level="debug",
            )
        self.window._set_sensorgram_series(self.window._sensorgram_spectral_cube_indices, self.window._sensorgram_metric_values)
        self.window._append_workflow_log(
            f"SG done | prep {self.window._format_elapsed_seconds(result.prep_seconds)} | fit {self.window._format_elapsed_seconds(result.fit_seconds)}",
            level="success",
        )
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
        self.window._set_status_text("Sensorgram calculation stopped." if result.cancelled else "Sensorgram calculation finished.")
        self.window._update_analysis_control_state()

    def _on_sensorgram_failed(self, request_id: int, message: str) -> None:
        if request_id != self.window._sensorgram_request_id:
            return
        self.window._sensorgram_running = False
        self.window._sensorgram_cancel_event = None
        self.window._end_busy()
        self.window._update_analysis_control_state()
        self.window._set_sensorgram_summary_text(f"Sensorgram failed: {message}")
        self.window._background_error("Sensorgram", message)

    def _on_absorbance_spectrum_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        result: AbsorbanceSpectrumResult,
    ) -> None:
        started_at = self.window._absorbance_spectrum_started_at
        self.window._absorbance_spectrum_started_at = None
        self.window._absorbance_spectrum_running = False
        self.window._absorbance_spectrum_running_signature = None
        self.window._end_busy()
        if request_id != self.window._absorbance_spectrum_request_id:
            if self.window._pending_absorbance_spectrum_payload is not None:
                self.window._start_pending_absorbance_spectrum_refresh()
            return
        self.window._absorbance_spectrum_cache[signature] = result
        self.window._absorbance_spectrum_cache.move_to_end(signature)
        while len(self.window._absorbance_spectrum_cache) > self.window.ABSORBANCE_SPECTRUM_CACHE_SIZE:
            self.window._absorbance_spectrum_cache.popitem(last=False)
        self.window._append_workflow_log(
            f"Spec cache store | rois {len(signature[2]) if len(signature) > 2 and isinstance(signature[2], tuple) else 0}",
            level="debug",
        )
        spectral_cube_signature = self.window._absorbance_spectral_cube_signature(signature)
        if spectral_cube_signature is not None:
            self.window._absorbance_spectral_cube_cache[spectral_cube_signature] = result
            self.window._absorbance_spectral_cube_cache.move_to_end(spectral_cube_signature)
            while len(self.window._absorbance_spectral_cube_cache) > self.window.ABSORBANCE_SPECTRAL_CUBE_CACHE_SIZE:
                self.window._absorbance_spectral_cube_cache.popitem(last=False)
            self.window._append_workflow_log("Spec spectral_cube_index cache store", level="debug")
        self._store_roi_absorbance_cache(result)
        self.window._absorbance_spectrum_dirty = False
        fit_seconds = self._apply_absorbance_spectrum_result(result) or 0.0
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
        if self.window._pending_absorbance_spectrum_payload is not None:
            self.window._start_pending_absorbance_spectrum_refresh()

    def _on_absorbance_spectrum_failed(self, request_id: int, message: str) -> None:
        self.window._absorbance_spectrum_started_at = None
        self.window._absorbance_spectrum_running = False
        self.window._absorbance_spectrum_running_signature = None
        self.window._end_busy()
        if request_id == self.window._absorbance_spectrum_request_id:
            self.window._background_error("Spectral absorbance", message)
        if self.window._pending_absorbance_spectrum_payload is not None:
            self.window._start_pending_absorbance_spectrum_refresh()

    def _apply_absorbance_spectrum_result(self, result: AbsorbanceSpectrumResult) -> float | None:
        fit_started = time.perf_counter()
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        series_payloads: list[tuple[str, int, AbsorbanceSpectrumResult]] = []
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
                roi_signature = self.window._roi_absorbance_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._roi_absorbance_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), cached_result))
        if not series_payloads and len(selected_roi_ids) > 1:
            for roi_id in selected_roi_ids:
                roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
                if roi is None:
                    continue
                roi_signature = self.window._roi_absorbance_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._roi_absorbance_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(roi_id)}", int(roi_id), cached_result))
        if not series_payloads:
            fallback_id = int(selected_roi_ids[0]) if selected_roi_ids else 0
            series_payloads = [("Selection", fallback_id, result)]
        highlighted_ids = set(selected_roi_ids)

        self.window._clear_spectrum_series_items()
        self.window.spectrum_current_point.setData([], [])
        self.window.spectrum_metric_point.setData([], [])

        x_values_all: list[np.ndarray] = []
        y_values_all: list[np.ndarray] = []
        fit_y_values_all: list[np.ndarray] = []
        primary_result = series_payloads[0][2]
        for label, roi_id, roi_result in series_payloads:
            rendered = self.window._add_spectrum_series(
                roi_id=roi_id,
                result=roi_result,
                label=label,
                highlighted=bool(highlighted_ids) and int(roi_id) in highlighted_ids,
                dimmed=len(series_payloads) > 1 and bool(highlighted_ids),
            )
            if rendered is None:
                continue
            x_values, y_values, fit_x_values, fit_y_values = rendered
            x_values_all.append(np.asarray(x_values, dtype=np.float64))
            y_values_all.append(np.asarray(y_values, dtype=np.float64))
            if fit_x_values is not None and fit_y_values is not None and fit_x_values.size and fit_y_values.size:
                fit_y_values_all.append(np.asarray(fit_y_values, dtype=np.float64))

        if not x_values_all:
            self.window._set_spectrum_summary_text(f"{self.window._spectrum_selection_label()} | No valid absorbance values")
            return

        x_min = min(float(np.min(values)) for values in x_values_all)
        x_max = max(float(np.max(values)) for values in x_values_all)
        y_min = min(float(np.min(values)) for values in y_values_all)
        y_max = max(float(np.max(values)) for values in y_values_all)
        for fit_values in fit_y_values_all:
            if fit_values.size:
                y_min = min(y_min, float(np.nanmin(fit_values)))
                y_max = max(y_max, float(np.nanmax(fit_values)))
        y_span = max(y_max - y_min, 0.05)
        self.window.spectrum_plot.setXRange(x_min, x_max, padding=0.02)
        self.window.spectrum_plot.setYRange(y_min - y_span * 0.08, y_max + y_span * 0.12, padding=0.0)

        metric_value = None
        metric_signal = None
        current_text = ""
        fit_text = ""
        fit_seconds = 0.0
        if len(series_payloads) == 1:
            fit = self.window._analysis_fit_result_from_spectrum(primary_result)
            if fit is not None:
                metric_value, metric_signal = metric_value_from_fit(fit, self.window._analysis_metric_key())
            elif self._analysis_fit_method_key() == "none":
                wavelength_range = self._analysis_wavelength_range()
                metric_value, metric_signal = metric_value_from_spectrum(
                    primary_result.wavelengths_nm,
                    primary_result.absorbance,
                    self.window._analysis_metric_key(),
                    wl_min=None if wavelength_range is None else wavelength_range[0],
                    wl_max=None if wavelength_range is None else wavelength_range[1],
                )
            if metric_value is not None and metric_signal is not None and np.isfinite(metric_value) and np.isfinite(metric_signal):
                self.window.spectrum_metric_point.setData([float(metric_value)], [float(metric_signal)])
            else:
                self.window.spectrum_metric_point.setData([], [])
            current_wavelength = self.window._current_wavelength()
            current_point_index = None
            if current_wavelength is not None:
                current_point_index = next(
                    (
                        index
                        for index, wavelength_nm in enumerate(primary_result.wavelengths_nm)
                        if abs(float(wavelength_nm) - float(current_wavelength)) < 1e-6
                        and np.isfinite(primary_result.absorbance[index])
                    ),
                    None,
                )
            if current_point_index is None:
                self.window.spectrum_current_point.setData([], [])
            else:
                current_x = float(primary_result.wavelengths_nm[current_point_index])
                current_y = float(primary_result.absorbance[current_point_index])
                self.window.spectrum_current_point.setData([current_x], [current_y])
                current_sample_mean = float(primary_result.sample_mean[current_point_index])
                current_reference_mean = float(primary_result.reference_mean[current_point_index])
                current_text = (
                    f" | A({current_x:g} nm) = {current_y:.4f}"
                    f" | sample {current_sample_mean:.1f}, reference {current_reference_mean:.1f}"
                )
            if metric_value is not None and np.isfinite(metric_value):
                fit_text = f" | {self.window._analysis_metric_label()} {float(metric_value):.3f} nm"
                if fit is not None:
                    fit_text += f" | Poly {self.window._analysis_poly_order()}"
        else:
            self.window.spectrum_current_point.setData([], [])
            self.window.spectrum_metric_point.setData([], [])
            fit_text = f" | {len(series_payloads)} ROI series"
        fit_seconds = time.perf_counter() - fit_started
        self.window._last_absorbance_fit_seconds = fit_seconds

        spectral_cube_index = self.window._current_spectral_cube()
        sample_pixels = int(np.nanmax(primary_result.sample_pixel_count)) if primary_result.sample_pixel_count.size else 0
        reference_pixels = int(np.nanmax(primary_result.reference_pixel_count)) if primary_result.reference_pixel_count.size else 0
        self.window._set_spectrum_summary_text(
            f"{self.window._spectrum_selection_label()} | Spectral cube {spectral_cube_index if spectral_cube_index is not None else '-'}"
            f" | ROI px: sample {sample_pixels}, reference {reference_pixels}{current_text}{fit_text}"
        )
        self.window._update_single_spectral_cube_sensorgram(metric_value, metric_signal)
        return fit_seconds

    def _on_analysis_section_applied_changed(self, applied: bool) -> None:
        applied = bool(applied)
        self.window._append_workflow_log(f"Analysis linked state changed: {applied}", level="debug")
        if self.window._analysis_enabled == applied:
            self.window._update_analysis_control_state()
            return
        self.window._analysis_enabled = applied
        self.window._settings.setValue("analysis_section_applied", self.window._analysis_enabled)
        if not self.window._analysis_enabled and self.window._analysis_live_preview_enabled:
            self.window._analysis_live_preview_enabled = False
            self.window._settings.setValue("analysis/live_preview", False)
        self.window._update_analysis_control_state()
        if self.window._analysis_enabled:
            self.window._mark_absorbance_spectrum_dirty()
            self.window._set_status_text("Analysis calculations enabled.")
            return
        self.window._stop_sensorgram_calculation()
        self.window._pending_sensorgram_payload = None
        self.window._clear_absorbance_spectrum("Analysis calculations are disabled for this panel.")
        self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
        self.window._set_status_text("Analysis calculations disabled.")

    def _analysis_fit_method_key(self) -> str:
        return str(self.window.analysis_fit_method_combo.currentData() or "none")

    def _analysis_metric_key(self) -> str:
        return str(self.window.analysis_metric_combo.currentData() or "centroid")

    def _analysis_metric_label(self) -> str:
        return str(self.window.analysis_metric_combo.currentText() or "Metric")

    def _analysis_metric_axis_label(self) -> str:
        metric_key = self._analysis_metric_key()
        if metric_key in {"maximum", "centroid"}:
            return "Wavelength (nm)"
        return "Metric"

    def _analysis_poly_order(self) -> int:
        return int(self.window.analysis_poly_order_spin.value())

    def _poly_order_summary_suffix(self) -> str:
        """' | Polynomial order N' for status/summary text - only when a poly
        fit actually produced the metric, matching Order's own visibility
        (sync_analysis_fitting_controls). Fitting = None reads the metric off
        the raw spectrum, so mentioning a polynomial order there would be
        misleading."""
        if self._analysis_fit_method_key() != "poly":
            return ""
        return f" | Polynomial order {self._analysis_poly_order()}"

    def _current_analysis_spectral_cube_range(self) -> tuple[int, int] | None:
        if not self.window._spectral_cube_values:
            return None
        start = int(self.window.analysis_start_spectral_cube_spin.value())
        end = int(self.window.analysis_end_spectral_cube_spin.value())
        if start > end:
            start, end = end, start
        return start, end

    def _sync_analysis_spectral_cube_range_controls(self) -> None:
        spectral_cube_enabled = bool(self.window._spectral_cube_values)
        self.window.analysis_start_spectral_cube_spin.setEnabled(spectral_cube_enabled)
        self.window.analysis_end_spectral_cube_spin.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_range_slider.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_select_all_button.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_axis_label.setText(self.window._spectral_cube_axis_label())
        if not spectral_cube_enabled:
            return

        spectral_cube_min = int(min(self.window._spectral_cube_values))
        spectral_cube_max = int(max(self.window._spectral_cube_values))
        stored_start = self.window._settings_int("analysis/spectral_cube_start", spectral_cube_min, minimum=spectral_cube_min, maximum=spectral_cube_max)
        stored_end = self.window._settings_int("analysis/spectral_cube_end", spectral_cube_max, minimum=spectral_cube_min, maximum=spectral_cube_max)
        if stored_start > stored_end:
            stored_start, stored_end = stored_end, stored_start

        self.window.analysis_start_spectral_cube_spin.blockSignals(True)
        self.window.analysis_end_spectral_cube_spin.blockSignals(True)
        self.window.analysis_start_spectral_cube_spin.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_end_spectral_cube_spin.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_start_spectral_cube_spin.setValue(stored_start)
        self.window.analysis_end_spectral_cube_spin.setValue(stored_end)
        self.window.analysis_start_spectral_cube_spin.blockSignals(False)
        self.window.analysis_end_spectral_cube_spin.blockSignals(False)
        self.window.analysis_spectral_cube_range_slider.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_spectral_cube_range_slider.setValues(stored_start, stored_end)

    def _analysis_wavelength_range(self) -> tuple[float, float] | None:
        if not self.window._wavelength_values:
            return None
        return float(self.window.analysis_wavelength_min_spin.value()), float(self.window.analysis_wavelength_max_spin.value())

    def _sync_analysis_wavelength_range_controls(self) -> None:
        wavelength_enabled = bool(self.window._wavelength_values)
        self.window.analysis_wavelength_min_spin.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_max_spin.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_range_slider.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_select_all_button.setEnabled(wavelength_enabled)
        if not wavelength_enabled:
            return

        wavelength_min = float(min(self.window._wavelength_values))
        wavelength_max = float(max(self.window._wavelength_values))
        decimals = max((self.window._decimal_places(value) for value in self.window._wavelength_values), default=0)
        decimals = min(max(decimals, 0), 4)
        stored_min = self.window._settings_float("analysis/wavelength_min_nm", wavelength_min, minimum=wavelength_min, maximum=wavelength_max)
        stored_max = self.window._settings_float("analysis/wavelength_max_nm", wavelength_max, minimum=wavelength_min, maximum=wavelength_max)
        if stored_min > stored_max:
            stored_min, stored_max = stored_max, stored_min

        self.window.analysis_wavelength_min_spin.blockSignals(True)
        self.window.analysis_wavelength_max_spin.blockSignals(True)
        self.window.analysis_wavelength_min_spin.setDecimals(decimals)
        self.window.analysis_wavelength_max_spin.setDecimals(decimals)
        self.window.analysis_wavelength_min_spin.setRange(wavelength_min, wavelength_max)
        self.window.analysis_wavelength_max_spin.setRange(wavelength_min, wavelength_max)
        self.window.analysis_wavelength_min_spin.setValue(stored_min)
        self.window.analysis_wavelength_max_spin.setValue(stored_max)
        self.window.analysis_wavelength_min_spin.blockSignals(False)
        self.window.analysis_wavelength_max_spin.blockSignals(False)
        low_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - stored_min))
        high_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - stored_max))
        self.window.analysis_wavelength_range_slider.setRange(0, max(len(self.window._wavelength_values) - 1, 0))
        self.window.analysis_wavelength_range_slider.setValues(low_index, high_index)

    def sync_analysis_fitting_controls(self) -> None:
        """Show/hide Order to match the chosen Fitting method - mirrors sLSPR
        acq's own sync_processing_crop_parameter_widget
        (main_window_processing.py): Order is poly-specific, same as acq's
        own poly_widgets_visible. Metric stays visible regardless of Fitting:
        Maximum/Centroid are computed straight off the raw absorbance
        spectrum when Fitting = None (metric_value_from_spectrum), so unlike
        Order there is no state where Metric has nothing to show."""
        fit_method = self._analysis_fit_method_key()
        order_visible = fit_method == "poly"
        self.window.analysis_poly_order_spin.setVisible(order_visible)
        order_title = getattr(self.window, "_analysis_fit_order_title_widget", None)
        if order_title is not None:
            order_title.setVisible(order_visible)

    def sync_analysis_roi_math_controls(self) -> None:
        """Show/hide Trim % to match the chosen Reduction method - only
        Trimmed mean uses it, same visibility-gating pattern as
        sync_analysis_fitting_controls's Order widget above."""
        is_trimmed_mean = str(self.window.analysis_reduction_method_combo.currentData() or "mean") == "trimmed_mean"
        self.window.analysis_trimmed_mean_spin.setVisible(is_trimmed_mean)
        trim_title = getattr(self.window, "_analysis_trim_fraction_title_widget", None)
        if trim_title is not None:
            trim_title.setVisible(is_trimmed_mean)

    def on_roi_math_settings_changed(self, *_args) -> None:
        """Reduction method / Trim % / Formula changed - these are session-
        scoped (area_roi_settings, not QSettings, see storage/workspace.py)
        since they change what a cached absorbance value means, unlike
        fit_method/metric/poly_order. Written back into state here, then the
        same cache-check-or-refresh logic as a fit-settings change applies
        (the changed reduction/formula are now part of the cache signature -
        see AnalysisController._roi_math_signature_elements - so a stale
        result will already miss and trigger a real recompute)."""
        settings = self.window._state.area_roi_settings
        settings.reduction_method = str(self.window.analysis_reduction_method_combo.currentData() or "mean")
        settings.trimmed_mean_fraction = float(self.window.analysis_trimmed_mean_spin.value()) / 100.0
        settings.formula_key = str(self.window.analysis_formula_combo.currentData() or "absorbance")
        self.sync_analysis_roi_math_controls()
        self.window._schedule_processing_state_save()
        self._on_analysis_fit_settings_changed(*_args)

    def refresh_roi_math_controls_from_state(self) -> None:
        """Push area_roi_settings' reduction/trim/formula into the ROI's-math
        widgets without re-triggering on_roi_math_settings_changed - called
        whenever a session/profile load replaces area_roi_settings wholesale,
        so the panel doesn't keep showing stale (default) values after
        loading a session that used a non-default Reduction or Formula."""
        window = self.window
        settings = window._state.area_roi_settings
        window.analysis_reduction_method_combo.blockSignals(True)
        window.analysis_trimmed_mean_spin.blockSignals(True)
        window.analysis_formula_combo.blockSignals(True)
        try:
            reduction_index = max(window.analysis_reduction_method_combo.findData(str(settings.reduction_method or "mean")), 0)
            window.analysis_reduction_method_combo.setCurrentIndex(reduction_index)
            window.analysis_trimmed_mean_spin.setValue(int(round(float(settings.trimmed_mean_fraction) * 100.0)))
            formula_index = max(window.analysis_formula_combo.findData(str(settings.formula_key or "absorbance")), 0)
            window.analysis_formula_combo.setCurrentIndex(formula_index)
        finally:
            window.analysis_reduction_method_combo.blockSignals(False)
            window.analysis_trimmed_mean_spin.blockSignals(False)
            window.analysis_formula_combo.blockSignals(False)
        self.sync_analysis_roi_math_controls()

    # ------------------------------------------------------------------
    # Statistics: post-processing applied to the already-computed
    # sensorgram trace (smoothing/spike-rejection/baseline/group
    # averaging) - never raw pixels, so no cache-signature changes are
    # needed anywhere here (see processing/trace_statistics.py).
    # ------------------------------------------------------------------

    def sync_statistics_controls(self) -> None:
        """Show/enable only the controls that matter for the current
        settings - Order only for Savitzky-Golay smoothing, Threshold only
        for Hampel spike rejection, and the method/param controls for each
        block only while its own toggle is on. Same visibility-gating idea
        as sync_analysis_fitting_controls/sync_analysis_roi_math_controls."""
        window = self.window
        is_savgol = str(window.analysis_smoothing_method_combo.currentData() or "none") == "savgol"
        window.analysis_smoothing_polyorder_spin.setVisible(is_savgol)
        order_title = getattr(window, "_analysis_smoothing_order_title_widget", None)
        if order_title is not None:
            order_title.setVisible(is_savgol)
        window.analysis_smoothing_window_spin.setEnabled(
            str(window.analysis_smoothing_method_combo.currentData() or "none") != "none"
        )

        spike_enabled = bool(window.analysis_spike_rejection_check.isChecked())
        window.analysis_spike_rejection_method_combo.setEnabled(spike_enabled)
        window.analysis_spike_rejection_window_spin.setEnabled(spike_enabled)
        is_hampel = str(window.analysis_spike_rejection_method_combo.currentData() or "hampel") == "hampel"
        window.analysis_spike_rejection_threshold_spin.setVisible(is_hampel)
        threshold_title = getattr(window, "_analysis_spike_threshold_title_widget", None)
        if threshold_title is not None:
            threshold_title.setVisible(is_hampel)
        window.analysis_spike_rejection_threshold_spin.setEnabled(spike_enabled)

        baseline_enabled = bool(window.analysis_baseline_check.isChecked())
        window.analysis_baseline_start_spin.setEnabled(baseline_enabled)
        window.analysis_baseline_end_spin.setEnabled(baseline_enabled)

        group_enabled = bool(window.analysis_group_stats_check.isChecked())
        window.analysis_group_stats_center_combo.setEnabled(group_enabled)
        window.analysis_group_stats_band_combo.setEnabled(group_enabled)
        window.analysis_calculate_group_button.setEnabled(group_enabled)

    def on_statistics_settings_changed(self, *_args) -> None:
        """Any Statistics control changed - write back to state (session-
        scoped, same reasoning as ROI's math), then just recompute the
        overlays from the already-stored raw sensorgram arrays. No cache
        invalidation is needed: this never touches the pixel-level or
        fitted-trace caches, only how the already-computed trace is drawn."""
        window = self.window
        settings = window._state.statistics_settings
        settings.smoothing_method = str(window.analysis_smoothing_method_combo.currentData() or "none")
        settings.smoothing_window = int(window.analysis_smoothing_window_spin.value())
        settings.smoothing_polyorder = int(window.analysis_smoothing_polyorder_spin.value())
        settings.spike_rejection_enabled = bool(window.analysis_spike_rejection_check.isChecked())
        settings.spike_rejection_method = str(window.analysis_spike_rejection_method_combo.currentData() or "hampel")
        settings.spike_rejection_window = int(window.analysis_spike_rejection_window_spin.value())
        settings.spike_rejection_threshold = float(window.analysis_spike_rejection_threshold_spin.value())
        settings.baseline_enabled = bool(window.analysis_baseline_check.isChecked())
        settings.baseline_window_start = float(window.analysis_baseline_start_spin.value())
        settings.baseline_window_end = float(window.analysis_baseline_end_spin.value())
        settings.group_stats_enabled = bool(window.analysis_group_stats_check.isChecked())
        settings.group_stats_center = str(window.analysis_group_stats_center_combo.currentData() or "mean")
        settings.group_stats_band = str(window.analysis_group_stats_band_combo.currentData() or "sd")
        self.sync_statistics_controls()
        window._schedule_processing_state_save()
        self._update_statistics_overlays()

    def refresh_statistics_controls_from_state(self) -> None:
        """Push statistics_settings into the Statistics widgets without
        re-triggering on_statistics_settings_changed - called whenever a
        session/profile load replaces statistics_settings wholesale, same
        pattern as refresh_roi_math_controls_from_state."""
        window = self.window
        settings = window._state.statistics_settings
        widgets = (
            window.analysis_smoothing_method_combo,
            window.analysis_smoothing_window_spin,
            window.analysis_smoothing_polyorder_spin,
            window.analysis_spike_rejection_check,
            window.analysis_spike_rejection_method_combo,
            window.analysis_spike_rejection_window_spin,
            window.analysis_spike_rejection_threshold_spin,
            window.analysis_baseline_check,
            window.analysis_baseline_start_spin,
            window.analysis_baseline_end_spin,
            window.analysis_group_stats_check,
            window.analysis_group_stats_center_combo,
            window.analysis_group_stats_band_combo,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            smoothing_index = max(window.analysis_smoothing_method_combo.findData(str(settings.smoothing_method or "none")), 0)
            window.analysis_smoothing_method_combo.setCurrentIndex(smoothing_index)
            window.analysis_smoothing_window_spin.setValue(int(settings.smoothing_window))
            window.analysis_smoothing_polyorder_spin.setValue(int(settings.smoothing_polyorder))
            window.analysis_spike_rejection_check.setChecked(bool(settings.spike_rejection_enabled))
            spike_index = max(
                window.analysis_spike_rejection_method_combo.findData(str(settings.spike_rejection_method or "hampel")), 0
            )
            window.analysis_spike_rejection_method_combo.setCurrentIndex(spike_index)
            window.analysis_spike_rejection_window_spin.setValue(int(settings.spike_rejection_window))
            window.analysis_spike_rejection_threshold_spin.setValue(float(settings.spike_rejection_threshold))
            window.analysis_baseline_check.setChecked(bool(settings.baseline_enabled))
            window.analysis_baseline_start_spin.setValue(float(settings.baseline_window_start or 0.0))
            window.analysis_baseline_end_spin.setValue(float(settings.baseline_window_end or 0.0))
            window.analysis_group_stats_check.setChecked(bool(settings.group_stats_enabled))
            group_center_index = max(
                window.analysis_group_stats_center_combo.findData(str(settings.group_stats_center or "mean")), 0
            )
            window.analysis_group_stats_center_combo.setCurrentIndex(group_center_index)
            group_band_index = max(window.analysis_group_stats_band_combo.findData(str(settings.group_stats_band or "sd")), 0)
            window.analysis_group_stats_band_combo.setCurrentIndex(group_band_index)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self.sync_statistics_controls()
        self._update_statistics_overlays()

    def _update_statistics_overlays(self) -> None:
        self._update_processed_trace_overlay()
        self._update_group_overlay()

    def _update_processed_trace_overlay(self) -> None:
        """Spike-rejection -> smoothing -> baseline, in that order (clean
        transients before smoothing blends them into neighbors; baseline is
        a simple offset applied last), drawn on sensorgram_processed_curve.
        Hidden whenever nothing is actually active, so an unused overlay
        doesn't sit on top of the raw trace."""
        window = self.window
        settings = window._state.statistics_settings
        spectral_cubes = window._sensorgram_spectral_cube_indices
        values = window._sensorgram_metric_values
        if spectral_cubes.size == 0:
            window.sensorgram_processed_curve.hide()
            return
        x_values = self._sensorgram_x_values(spectral_cubes)
        valid = np.isfinite(x_values) & np.isfinite(values)
        if not np.any(valid):
            window.sensorgram_processed_curve.hide()
            return
        order = np.argsort(x_values[valid])
        x = x_values[valid][order]
        y = values[valid][order].astype(np.float64, copy=True)

        active = False
        if settings.spike_rejection_enabled:
            active = True
            if settings.spike_rejection_method == "running_median":
                y = reject_spikes_running_median(y, window=settings.spike_rejection_window)
            else:
                y = reject_spikes_hampel(y, window=settings.spike_rejection_window, threshold=settings.spike_rejection_threshold)
        if settings.smoothing_method == "savgol":
            active = True
            y = smooth_savgol(y, window=settings.smoothing_window, polyorder=settings.smoothing_polyorder)
        elif settings.smoothing_method == "moving_average":
            active = True
            y = smooth_moving_average(y, window=settings.smoothing_window)
        if settings.baseline_enabled:
            active = True
            y, _baseline = normalize_to_baseline_window(x, y, settings.baseline_window_start, settings.baseline_window_end)

        if not active:
            window.sensorgram_processed_curve.hide()
            return
        finite = np.isfinite(y)
        if not np.any(finite):
            window.sensorgram_processed_curve.hide()
            return
        window.sensorgram_processed_curve.setData(x[finite], y[finite])
        window.sensorgram_processed_curve.show()

    # ------------------------------------------------------------------
    # Group statistics: aggregates each member ROI pair's own already-
    # computed sensorgram trace across an AreaRoiGroup, shown alongside
    # (not replacing) the individually-selected trace above.
    # ------------------------------------------------------------------

    def _group_members_for_current_selection(self) -> tuple[str, list[AreaRoi]] | None:
        """(group_name, member_area_rois) if the current selection is
        exactly one ROI pair belonging to a group with >=2 members, else
        None - group stats only make sense for a genuine multi-member group,
        not a lone ROI or an already-multi-selected combined view."""
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if len(selected_roi_ids) != 1:
            return None
        group = self.window._group_for_roi(int(selected_roi_ids[0]))
        if group is None or len(group.area_roi_ids) < 2:
            return None
        member_ids = {int(roi_id) for roi_id in group.area_roi_ids}
        members = [roi for roi in self.window._state.area_rois if int(roi.area_roi_id) in member_ids]
        if len(members) < 2:
            return None
        return group.name, members

    @staticmethod
    def _member_trace_aligned(result: SensorgramComputationResult, spectral_cubes: list[int]) -> np.ndarray:
        """Reindex a member's own (spectral_cube_indices, metric_signal)
        onto the group's full requested spectral-cube list, NaN where that
        member has no value for a given cube - keeps every member's array
        the same length/order for aggregate_group_traces regardless of
        whether one member happened to skip/fail a different frame."""
        value_by_cube = {
            int(index): float(value) for index, value in zip(result.spectral_cube_indices, result.metric_signal)
        }
        return np.asarray([value_by_cube.get(int(cube), float("nan")) for cube in spectral_cubes], dtype=np.float64)

    def _update_group_overlay(self) -> None:
        """Draws whatever is already available from cache - never triggers a
        computation itself (see calculate_group_sensorgram for that). Called
        after every sensorgram update and after a group calculation
        finishes, so the band reflects whatever member traces exist right
        now, live."""
        window = self.window
        settings = window._state.statistics_settings
        if not settings.group_stats_enabled:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        group_info = self._group_members_for_current_selection()
        spectral_cubes = self.available_analysis_spectral_cubes()
        if group_info is None or not spectral_cubes:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        _group_name, members = group_info
        member_traces: dict[int, np.ndarray] = {}
        for member_roi in members:
            member_id = int(member_roi.area_roi_id)
            signature = self._sensorgram_signature_for_selection(spectral_cubes, (member_id,), [member_roi])
            result = None if signature is None else window._sensorgram_cache.get(signature)
            if result is None:
                continue
            member_traces[member_id] = self._member_trace_aligned(result, spectral_cubes)
        if len(member_traces) < 2:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        x_values = self._sensorgram_x_values(spectral_cubes)
        center, low, high = aggregate_group_traces(
            member_traces, center=settings.group_stats_center, band=settings.group_stats_band
        )
        valid = np.isfinite(x_values) & np.isfinite(center) & np.isfinite(low) & np.isfinite(high)
        if not np.any(valid):
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        window.sensorgram_group_curve.setData(x_values[valid], center[valid])
        window.sensorgram_group_band_low_curve.setData(x_values[valid], low[valid])
        window.sensorgram_group_band_high_curve.setData(x_values[valid], high[valid])
        window.sensorgram_group_curve.show()
        window.sensorgram_group_band_fill_item.show()

    def calculate_group_sensorgram(self) -> None:
        """"Calculate group" button: computes (or reuses already-cached)
        every member's own single-ROI sensorgram, one at a time - reusing
        the normal single-ROI worker path (_start_sensorgram_worker) rather
        than a separate parallel implementation, so each member's result
        lands in the same window._sensorgram_cache the individual view
        already reads from. The visible primary trace flickers through each
        member while this runs (the same worker that updates the display is
        what's being reused per member) and is restored to the actual
        current selection once the whole group is done, see
        _finish_group_calculation."""
        group_info = self._group_members_for_current_selection()
        if group_info is None:
            self.window._set_status_text("Select a ROI pair that belongs to a multi-member group first.")
            return
        spectral_cubes = self.available_analysis_spectral_cubes()
        if not spectral_cubes:
            self.window._set_status_text("No spectral cubes are available in the selected range.")
            return
        _group_name, members = group_info
        self._group_calculation_spectral_cubes = spectral_cubes
        self._group_calculation_members_by_id = {int(m.area_roi_id): m for m in members}
        self._group_calculation_pending_member_ids = list(self._group_calculation_members_by_id.keys())
        self._group_calculation_active = True
        self.window._set_status_text(f"Calculating group ({len(members)} members)...")
        self._advance_group_calculation()

    def _advance_group_calculation(self) -> None:
        spectral_cubes = getattr(self, "_group_calculation_spectral_cubes", None)
        pending = getattr(self, "_group_calculation_pending_member_ids", None)
        members_by_id = getattr(self, "_group_calculation_members_by_id", None)
        if not spectral_cubes or pending is None or members_by_id is None:
            self._group_calculation_active = False
            return
        while pending:
            member_id = pending[0]
            member_roi = members_by_id[member_id]
            signature = self._sensorgram_signature_for_selection(spectral_cubes, (member_id,), [member_roi])
            if signature is None:
                pending.pop(0)
                continue
            if self.window._sensorgram_cache.get(signature) is not None:
                pending.pop(0)
                continue
            self._start_sensorgram_worker(signature, spectral_cubes, (member_id,), [member_roi])
            return
        self._finish_group_calculation()

    def _on_group_member_sensorgram_ready(self) -> None:
        pending = getattr(self, "_group_calculation_pending_member_ids", None)
        if pending:
            pending.pop(0)
        self._advance_group_calculation()

    def _finish_group_calculation(self) -> None:
        self._group_calculation_active = False
        member_count = len(getattr(self, "_group_calculation_members_by_id", {}) or {})
        self.window._set_status_text(f"Group calculated ({member_count} members).")
        # Restore the actual current selection's own trace/view - group
        # member calculations reused the same worker/display path, so the
        # plot currently shows whichever member finished last.
        self.calculate_sensorgram_for_range()

    def _set_sensorgram_summary_text(self, text: str) -> None:
        self.window.sensorgram_summary_label.setText(text)

    def _update_sensorgram_plot_labels(self) -> None:
        self.window.sensorgram_plot.setLabel("left", self._analysis_metric_axis_label())
        self.window.sensorgram_plot.setLabel("bottom", self.window._spectral_cube_axis_label())

    def _analysis_plot_spectral_cube_range(self) -> tuple[int, int] | None:
        if not self.window._spectral_cube_values:
            return None
        return int(min(self.window._spectral_cube_values)), int(max(self.window._spectral_cube_values))

    def _analysis_plot_wavelength_range(self) -> tuple[float, float] | None:
        if not self.window._wavelength_values:
            return None
        return float(min(self.window._wavelength_values)), float(max(self.window._wavelength_values))

    def _sensorgram_axis_range(self) -> tuple[float, float] | None:
        """The sensorgram plot's x-axis limits: elapsed-time range when
        acquisition metadata with per-image timing is loaded, otherwise the
        raw spectral-cube-index range (today's behavior, unchanged)."""
        if not self.window._spectral_cube_values:
            return None
        index_range = (float(min(self.window._spectral_cube_values)), float(max(self.window._spectral_cube_values)))
        if self._sensorgram_time_mode_metadata() is None:
            return index_range
        x_values = self._sensorgram_x_values(self.window._spectral_cube_values)
        finite = x_values[np.isfinite(x_values)]
        if finite.size == 0:
            return index_range
        return float(np.min(finite)), float(np.max(finite))

    def _sync_analysis_plot_axes(self) -> None:
        wavelength_range = self._analysis_plot_wavelength_range()
        if wavelength_range is not None:
            self.window.spectrum_plot.setLimits(xMin=wavelength_range[0], xMax=wavelength_range[1])
            self.window.spectrum_plot.setXRange(wavelength_range[0], wavelength_range[1], padding=0.03)
        sensorgram_range = self._sensorgram_axis_range()
        if sensorgram_range is not None:
            self.window.sensorgram_plot.setLimits(xMin=sensorgram_range[0], xMax=sensorgram_range[1])
            self.window.sensorgram_plot.setXRange(sensorgram_range[0], sensorgram_range[1], padding=0.03)

    def _sync_analysis_plot_cursors(self) -> None:
        has_dataset = bool(self.window._spectral_cube_values) and bool(self.window._wavelength_values)
        if not has_dataset:
            self.window.spectrum_cursor_line.hide()
            self.window.sensorgram_cursor_line.hide()
            return
        self.window.spectrum_cursor_line.show()
        self.window.sensorgram_cursor_line.show()
        current_spectral_cube = self.window._current_spectral_cube()
        current_wavelength = self.window._current_wavelength()
        if current_spectral_cube is None:
            current_spectral_cube = int(self.window._spectral_cube_values[0])
        if current_wavelength is None:
            current_wavelength = float(self.window._wavelength_values[0])
        cursor_color = self.window._chromatic_wavelength_color(float(current_wavelength))
        sensorgram_cursor_x = float(self._sensorgram_x_values([int(current_spectral_cube)])[0])
        if not np.isfinite(sensorgram_cursor_x):
            sensorgram_cursor_x = float(current_spectral_cube)
        self.window.spectrum_cursor_line.blockSignals(True)
        self.window.sensorgram_cursor_line.blockSignals(True)
        self.window.spectrum_cursor_line.setValue(float(current_wavelength))
        self.window.sensorgram_cursor_line.setValue(sensorgram_cursor_x)
        self.window.spectrum_cursor_line.setPen(pg.mkPen(cursor_color, width=2.2))
        self.window.spectrum_cursor_line.blockSignals(False)
        self.window.sensorgram_cursor_line.blockSignals(False)
        self.window._update_sensorgram_current_point()

    def _sync_analysis_plots(self) -> None:
        self._sync_analysis_plot_axes()
        self._sync_analysis_plot_cursors()

    def _on_spectrum_cursor_moved(self) -> None:
        if not self.window._wavelength_values:
            return
        wavelength = float(self.window.spectrum_cursor_line.value())
        nearest_index = min(
            range(len(self.window._wavelength_values)),
            key=lambda idx: abs(float(self.window._wavelength_values[idx]) - wavelength),
        )
        current_spectral_cube = self.window._current_spectral_cube()
        if current_spectral_cube is None and self.window._spectral_cube_values:
            current_spectral_cube = int(self.window._spectral_cube_values[self.window.spectral_cube_slider.value()])
        if current_spectral_cube is None:
            return
        target_wavelength = float(self.window._wavelength_values[nearest_index])
        self.window._set_current_spectral_cube_and_wavelength(int(current_spectral_cube), target_wavelength)

    def _on_sensorgram_cursor_moved(self) -> None:
        if not self.window._spectral_cube_values:
            return
        cursor_x = float(self.window.sensorgram_cursor_line.value())
        # Match against display x-values (elapsed time when in time mode,
        # same values _sync_analysis_plot_cursors placed the cursor with) -
        # any cube missing a timing entry falls back to its raw index so it
        # can still be matched against, consistent with the cursor-set side.
        display_x_values = self._sensorgram_x_values(self.window._spectral_cube_values)
        missing = ~np.isfinite(display_x_values)
        if np.any(missing):
            display_x_values = display_x_values.copy()
            display_x_values[missing] = np.asarray(self.window._spectral_cube_values, dtype=np.float64)[missing]
        nearest_index = int(np.argmin(np.abs(display_x_values - cursor_x)))
        current_wavelength = self.window._current_wavelength()
        if current_wavelength is None and self.window._wavelength_values:
            current_wavelength = float(self.window._wavelength_values[self.window.wavelength_slider.value()])
        if current_wavelength is None:
            return
        target_spectral_cube = int(self.window._spectral_cube_values[nearest_index])
        self.window._set_current_spectral_cube_and_wavelength(target_spectral_cube, float(current_wavelength))

    def _mark_sensorgram_stale(self, reason: str | None = None) -> None:
        if self.window._analysis_live_preview_enabled and self.window._analysis_enabled and self.window._state.dataset is not None:
            if reason is not None:
                self._set_sensorgram_summary_text(reason)
            self._schedule_sensorgram_refresh()
            return
        self.mark_stale(reason)

    def _invalidate_absorbance_spectrum_cache(self) -> None:
        self.window._absorbance_spectrum_cache.clear()
        self.window._absorbance_spectral_cube_cache.clear()
        self.window._roi_absorbance_cache.clear()
        self.window._absorbance_spectrum_dirty = True
        self.window._cached_roi_ids.clear()

    def _invalidate_caches_for_exclusion_change(self) -> None:
        """Clear every cached absorbance/sensorgram result.

        None of those caches' signatures include the exclusion rule set, so a
        result computed before a rule was added/removed would otherwise be
        served stale from cache -- silently ignoring the exclusion. Cheap
        enough to just clear everything: rule changes are rare, deliberate
        user actions, not a hot path.
        """
        self._invalidate_absorbance_spectrum_cache()
        self.window._sensorgram_cache.clear()
        self.window._sensorgram_spectral_cube_payload_cache.clear()
        self.mark_stale("Exclusion rules changed")

    def _schedule_sensorgram_refresh(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress or not self.window._analysis_live_preview_enabled:
            return
        if self.window._sensorgram_refresh_timer.isActive():
            self.window._sensorgram_refresh_timer.stop()
        self.window._sensorgram_refresh_timer.start()

    def _refresh_sensorgram(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress or not self.window._analysis_live_preview_enabled:
            return
        self.calculate_sensorgram()

    def _mark_absorbance_spectrum_dirty(self) -> None:
        self.window._absorbance_spectrum_dirty = True
        if not self.window._analysis_enabled:
            self.window._set_spectrum_summary_text("Analysis calculations are disabled for this panel.")
            self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if self.window._state.dataset is None:
            self.window._set_spectrum_summary_text("Load a dataset to show absorbance spectrum.")
            self.window._clear_sensorgram("Load a dataset to build the fitted sensorgram.")
            return
        if self.window._chromatic_setup_active:
            self.window._set_spectrum_summary_text("Spectral absorbance is hidden during chromatic setup.")
            self.window._clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        if not self._selected_spectrum_roi_ids():
            self.window._set_spectrum_summary_text("Select ROIs to show absorbance spectrum.")
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return
        self.window._set_spectrum_summary_text(
            f"{self._spectrum_selection_label()} | Spectrum is out of date | Press Calculate spectrum"
        )
        if not self.window._analysis_live_preview_enabled:
            self._mark_sensorgram_stale()

    def _selected_spectrum_roi_ids(self) -> tuple[int, ...]:
        return tuple(sorted(int(roi_id) for roi_id in self.window._selected_roi_ids))

    def _selected_source_rois_snapshot(self) -> list[AreaRoi]:
        selected_ids = self._selected_spectrum_roi_ids()
        if not selected_ids:
            self.window._selected_source_rois_cache_signature = None
            self.window._selected_source_rois_cache_value = tuple()
            return []
        signature_parts: list[object] = [selected_ids]
        source_rois: list[AreaRoi] = []
        roi_by_id = {int(roi.area_roi_id): roi for roi in self.window._state.area_rois}
        for roi_id in selected_ids:
            roi = roi_by_id.get(int(roi_id))
            if roi is None:
                self.window._selected_source_rois_cache_signature = None
                self.window._selected_source_rois_cache_value = tuple()
                return []
            source_rois.append(roi)
            signature_parts.append(
                (
                    int(roi.area_roi_id),
                    round(float(roi.center_x), 3),
                    round(float(roi.center_y), 3),
                    round(float(roi.sample_radius_px), 3),
                    round(float(roi.reference_inner_diameter_px or 0.0), 3),
                    round(float(roi.reference_outer_diameter_px or 0.0), 3),
                    roi.sample_color_hex or "",
                    roi.reference_color_hex or "",
                )
            )
        signature = tuple(signature_parts)
        if self.window._selected_source_rois_cache_signature == signature and self.window._selected_source_rois_cache_value:
            return list(self.window._selected_source_rois_cache_value)
        copied = tuple(deepcopy(roi) for roi in source_rois)
        self.window._selected_source_rois_cache_signature = signature
        self.window._selected_source_rois_cache_value = copied
        return list(copied)

    def _spectrum_selection_label(self) -> str:
        selected_ids = self._selected_spectrum_roi_ids()
        if not selected_ids:
            return "No ROIs"
        if self.window._selected_roi_ids:
            noun = "ROI" if len(selected_ids) == 1 else "ROIs"
            return f"{len(selected_ids)} selected {noun}"
        noun = "ROI" if len(selected_ids) == 1 else "ROIs"
        return f"All {len(selected_ids)} {noun}"

    def _schedule_absorbance_spectrum_refresh(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress:
            return
        self._mark_absorbance_spectrum_dirty()
        if self.window._analysis_live_preview_enabled:
            if self.window._absorbance_spectrum_timer.isActive():
                self.window._absorbance_spectrum_timer.stop()
            self.window._absorbance_spectrum_timer.start()

    def _start_absorbance_spectrum_preparation(
        self,
        signature: tuple[object, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> None:
        from PyQt6.QtWidgets import QApplication
        from lspr_imaging_app.gui.worker import FunctionWorker

        if self.window._absorbance_prep_running:
            return
        self.window._absorbance_prep_request_id += 1
        request_id = self.window._absorbance_prep_request_id
        self.window._absorbance_prep_running = True
        self.window._absorbance_prep_request_signature = signature
        self.window._absorbance_prep_started_at = time.perf_counter()
        self.window._append_workflow_log("Spec prep start", level="info")
        self.window._begin_busy("Preparing absorbance spectrum...", determinate=False)
        QApplication.processEvents()
        worker = FunctionWorker(self._prepare_absorbance_spectrum_payload, selected_source_rois)
        worker.signals.result.connect(
            lambda prepared, request_id=request_id, signature=signature: self._on_absorbance_spectrum_payload_ready(
                request_id,
                signature,
                prepared,
            )
        )
        worker.signals.error.connect(
            lambda message, request_id=request_id: self._on_absorbance_spectrum_payload_failed(request_id, message)
        )
        self.window._thread_pool.start(worker)

    def _cached_absorbance_result_for_selection(
        self,
        signature: tuple[object, ...],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> AbsorbanceSpectrumResult | None:
        if not selected_roi_ids:
            return None
        if len(selected_roi_ids) == 1:
            for cache_signature, cached_result in reversed(list(self.window._roi_absorbance_cache.items())):
                if self._absorbance_spectral_cube_signature(cache_signature) != self._absorbance_spectral_cube_signature(signature):
                    continue
                if self._absorbance_result_covers_roi_ids(cached_result, selected_roi_ids):
                    return cached_result
        spectral_cube_signature = self._absorbance_spectral_cube_signature(signature)
        if spectral_cube_signature is not None:
            cached_result = self.window._absorbance_spectral_cube_cache.get(spectral_cube_signature)
            if cached_result is not None and self._absorbance_result_covers_roi_ids(cached_result, selected_roi_ids):
                return cached_result
        for cache_signature, cached_result in reversed(list(self.window._absorbance_spectrum_cache.items())):
            if self._absorbance_spectral_cube_signature(cache_signature) != spectral_cube_signature:
                continue
            if self._absorbance_result_covers_roi_ids(cached_result, selected_roi_ids):
                return cached_result
        if selected_source_rois:
            cached_from_rois = self._cached_absorbance_result_from_roi_cache(selected_source_rois)
            if cached_from_rois is not None:
                return cached_from_rois
        return None

    def _roi_absorbance_signature(self, roi: AreaRoi) -> tuple[object, ...] | None:
        spectral_cube_index = self.window._current_spectral_cube()
        if spectral_cube_index is None or not self.window._wavelength_values:
            return None
        reduction_method, trimmed_mean_fraction, formula_key = self._roi_math_signature_elements()
        return _roi_absorbance_signature(
            int(spectral_cube_index),
            tuple(float(value) for value in self.window._wavelength_values),
            roi,
            tuple(
                self.window._chromatic_signature_for_image_key((int(spectral_cube_index), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
            reduction_method,
            trimmed_mean_fraction,
            formula_key,
        )

    def _roi_has_cached_absorbance(self, roi: AreaRoi) -> bool:
        signature = self._roi_absorbance_signature(roi)
        return signature is not None and self.window._roi_absorbance_cache.get(signature) is not None

    def _refresh_cached_roi_ids_snapshot(self) -> None:
        """Recompute the "which ROIs are already calculated" snapshot the ROI
        table/overlay read for their blue/white indicator.

        This is the only place that calls `_roi_has_cached_absorbance` for the
        whole ROI list. Call it after `_roi_absorbance_cache` actually changes
        (populated or cleared) - never from ROI selection/editing code, which
        should only ever read `self.window._cached_roi_ids`.
        """
        self.window._cached_roi_ids = {
            int(roi.area_roi_id) for roi in self.window._state.area_rois if self._roi_has_cached_absorbance(roi)
        }

    @staticmethod
    def _analysis_cache_signature_to_json(value):
        if isinstance(value, tuple):
            return [AnalysisController._analysis_cache_signature_to_json(item) for item in value]
        if isinstance(value, list):
            return [AnalysisController._analysis_cache_signature_to_json(item) for item in value]
        return value

    @staticmethod
    def _analysis_cache_signature_from_json(value):
        if isinstance(value, list):
            return tuple(AnalysisController._analysis_cache_signature_from_json(item) for item in value)
        return value

    @staticmethod
    def _absorbance_spectral_cube_signature(signature: tuple[object, ...] | None) -> tuple[object, ...] | None:
        if signature is None or len(signature) < 4:
            return None
        return (signature[0], signature[1], signature[3])

    @staticmethod
    def _absorbance_result_covers_roi_ids(result: AbsorbanceSpectrumResult, selected_roi_ids: tuple[int, ...]) -> bool:
        if not selected_roi_ids:
            return False
        if not result.area_roi_results:
            return len(selected_roi_ids) == 1
        available_ids = {int(roi_id) for roi_id in result.area_roi_results.keys()}
        return all(int(roi_id) in available_ids for roi_id in selected_roi_ids)

    def _analysis_cache_payload(self) -> dict:
        payload: dict[str, list[dict[str, object]]] = {
            "absorbance_spectrum_cache": [],
            "absorbance_spectral_cube_cache": [],
            "roi_absorbance_cache": [],
            "sensorgram_cache": [],
        }
        for signature, result in self.window._absorbance_spectrum_cache.items():
            payload["absorbance_spectrum_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self.window._absorbance_spectral_cube_cache.items():
            payload["absorbance_spectral_cube_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self.window._roi_absorbance_cache.items():
            payload["roi_absorbance_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self.window._sensorgram_cache.items():
            payload["sensorgram_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_sensorgram_result(result),
                }
            )
        return payload

    def _restore_analysis_caches(self, payload: dict | None) -> None:
        self.window._absorbance_spectrum_cache.clear()
        self.window._absorbance_spectral_cube_cache.clear()
        self.window._roi_absorbance_cache.clear()
        self.window._sensorgram_cache.clear()
        if not isinstance(payload, dict):
            return
        raw_absorbance = payload.get("absorbance_spectrum_cache", [])
        if isinstance(raw_absorbance, list):
            for entry in raw_absorbance:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._absorbance_spectrum_cache[signature] = result
                spectral_cube_signature = self._absorbance_spectral_cube_signature(signature)
                if spectral_cube_signature is not None:
                    self.window._absorbance_spectral_cube_cache[spectral_cube_signature] = result
                    self.window._absorbance_spectral_cube_cache.move_to_end(spectral_cube_signature)
                    while len(self.window._absorbance_spectral_cube_cache) > self.window.ABSORBANCE_SPECTRAL_CUBE_CACHE_SIZE:
                        self.window._absorbance_spectral_cube_cache.popitem(last=False)
        raw_absorbance_spectral_cubes = payload.get("absorbance_spectral_cube_cache", [])
        if isinstance(raw_absorbance_spectral_cubes, list):
            for entry in raw_absorbance_spectral_cubes:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._absorbance_spectral_cube_cache[signature] = result
                self.window._absorbance_spectral_cube_cache.move_to_end(signature)
                while len(self.window._absorbance_spectral_cube_cache) > self.window.ABSORBANCE_SPECTRAL_CUBE_CACHE_SIZE:
                    self.window._absorbance_spectral_cube_cache.popitem(last=False)
        raw_roi_absorbance = payload.get("roi_absorbance_cache", payload.get("spot_absorbance_cache", []))
        if isinstance(raw_roi_absorbance, list):
            for entry in raw_roi_absorbance:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._roi_absorbance_cache[signature] = result
        raw_sensorgram = payload.get("sensorgram_cache", [])
        if isinstance(raw_sensorgram, list):
            for entry in raw_sensorgram:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_sensorgram_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._sensorgram_cache[signature] = result
        self._refresh_cached_roi_ids_snapshot()

    def _toggle_analysis_live_preview(self) -> None:
        self.window._analysis_live_preview_enabled = not self.window._analysis_live_preview_enabled
        if not self.window._analysis_enabled and self.window._analysis_live_preview_enabled:
            self.window._analysis_live_preview_enabled = False
            self.window._settings.setValue("analysis/live_preview", False)
            self.window._update_analysis_control_state()
            self.window._set_status_text("Enable Analysis to use live preview.")
            return
        self.window._settings.setValue("analysis/live_preview", bool(self.window._analysis_live_preview_enabled))
        self.window._update_analysis_control_state()
        if self.window._analysis_live_preview_enabled:
            self._refresh_visible_spectrum_from_cache()
            self.preview_sensorgram_from_cache()
            self.window._set_status_text("Analysis live preview enabled.")
        else:
            self.window._set_status_text("Analysis live preview disabled.")

    def _available_analysis_spectral_cubes(self) -> list[int]:
        spectral_cube_range = self._current_analysis_spectral_cube_range()
        if spectral_cube_range is None:
            return []
        start, end = spectral_cube_range
        return [
            int(spectral_cube_index)
            for spectral_cube_index in self.window._spectral_cube_values
            if start <= int(spectral_cube_index) <= end
            and not is_cube_fully_excluded(self.window._state.image_exclusions, spectral_cube_index)
        ]

    def _stop_sensorgram_calculation(self) -> None:
        if not self.window._sensorgram_running or self.window._sensorgram_cancel_event is None:
            return
        self.window._sensorgram_cancel_event.set()
        self.window._pending_sensorgram_payload = None
        self._set_sensorgram_summary_text("Stopping sensorgram calculation...")
        self.window._set_status_text("Stopping sensorgram calculation...")

    def _start_pending_absorbance_spectrum_refresh(self, *, reuse_busy: bool = False) -> None:
        from lspr_imaging_app.gui.worker import FunctionWorker

        if self.window._pending_absorbance_spectrum_payload is None:
            return
        signature, payload, task_fn = self.window._pending_absorbance_spectrum_payload
        self.window._pending_absorbance_spectrum_payload = None
        request_id = self.window._absorbance_spectrum_request_id + 1
        self.window._absorbance_spectrum_request_id = request_id
        self.window._absorbance_spectrum_running = True
        self.window._absorbance_spectrum_running_signature = signature
        self.window._absorbance_spectrum_started_at = time.perf_counter()
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
        reduction_method, trimmed_mean_fraction, formula_key = self._roi_math_signature_elements()
        worker = FunctionWorker(
            task_fn,
            *payload,
            supports_progress=True,
            reduction_method=reduction_method,
            trimmed_mean_fraction=trimmed_mean_fraction,
            formula_key=formula_key,
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.result.connect(
            lambda result,
            request_id=request_id,
            signature=signature: self._on_absorbance_spectrum_ready(request_id, signature, result)
        )
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_absorbance_spectrum_failed(request_id, message))
        self.window._thread_pool.start(worker)

    def _refresh_visible_spectrum_from_cache(self) -> bool:
        if not self.window._analysis_enabled:
            return False
        selected_source_rois = self._selected_source_rois_snapshot()
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        roi_signature_single = None
        if len(selected_source_rois) == 1:
            roi_signature_single = self._roi_absorbance_signature(selected_source_rois[0])
            if roi_signature_single is not None:
                cached_roi_result = self.window._roi_absorbance_cache.get(roi_signature_single)
                if cached_roi_result is not None:
                    self._apply_absorbance_spectrum_result(cached_roi_result)
                    self.window._roi_absorbance_cache.move_to_end(roi_signature_single)
                    self.window._append_workflow_log("Spec repaint | roi cache", level="debug")
                    return True
        signature = self._absorbance_spectrum_signature()
        if signature is None:
            return False
        if not selected_source_rois:
            cached_result = self.window._absorbance_spectrum_cache.get(signature)
            if cached_result is not None:
                self._apply_absorbance_spectrum_result(cached_result)
                spectral_cube_signature = self._absorbance_spectral_cube_signature(signature)
                if spectral_cube_signature is not None and spectral_cube_signature in self.window._absorbance_spectral_cube_cache:
                    self.window._absorbance_spectral_cube_cache.move_to_end(spectral_cube_signature)
                self.window._append_workflow_log("Spec repaint | spectrum cache", level="debug")
                return True
            return False
        cached_result = self._cached_absorbance_result_for_selection(signature, selected_roi_ids)
        if cached_result is not None:
            self._apply_absorbance_spectrum_result(cached_result)
            spectral_cube_signature = self._absorbance_spectral_cube_signature(signature)
            if spectral_cube_signature is not None and spectral_cube_signature in self.window._absorbance_spectral_cube_cache:
                self.window._absorbance_spectral_cube_cache.move_to_end(spectral_cube_signature)
            self.window._append_workflow_log("Spec repaint | spectrum cache", level="debug")
            return True
        return False
