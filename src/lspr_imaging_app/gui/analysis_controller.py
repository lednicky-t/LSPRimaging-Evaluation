from __future__ import annotations

import logging
import time
import numpy as np
from copy import deepcopy
from PyQt6.QtGui import QColor
import pyqtgraph as pg

from lspr_imaging_app.domain.models import AreaRoi, AbsorbanceSpectrumResult
from lspr_imaging_app.gui.worker import SensorgramComputationResult
from lspr_imaging_app.processing.analysis import metric_value_from_fit


class AnalysisController:
    def __init__(self, window) -> None:
        self.window = window

    def _sensorgram_selection_color(self) -> QColor:
        selected_roi_ids = tuple(self.window._selected_spectrum_spot_ids())
        if len(selected_roi_ids) == 1:
            return QColor(self.window._spot_spectrum_color(int(selected_roi_ids[0])))
        if selected_roi_ids:
            return QColor("#38bdf8")
        return QColor("#22c55e")

    def update_selection_highlight(self, *, force: bool = False) -> None:
        selected_signature = tuple(self.window._selected_spectrum_spot_ids())
        if not force and selected_signature == getattr(self.window, "_sensorgram_selection_highlight_signature", None):
            return
        self.window._sensorgram_selection_highlight_signature = selected_signature
        has_data = self.window._sensorgram_frame_indices.size > 0
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

    def _store_spot_absorbance_cache(self, result) -> None:
        area_roi_results = getattr(result, "area_roi_results", None)
        if not area_roi_results:
            return
        area_roi_by_id = {int(area_roi.area_roi_id): area_roi for area_roi in self.window._state.area_rois}
        for area_roi_id, spot_result in area_roi_results.items():
            area_roi = area_roi_by_id.get(int(area_roi_id))
            if area_roi is None:
                continue
            signature = self.window._roi_absorbance_signature(area_roi)
            if signature is None:
                continue
            self.window._spot_absorbance_cache[signature] = spot_result
            self.window._spot_absorbance_cache.move_to_end(signature)
            while len(self.window._spot_absorbance_cache) > self.window.SPOT_ABSORBANCE_CACHE_SIZE:
                self.window._spot_absorbance_cache.popitem(last=False)

    def _apply_cached_sensorgram_result(self, signature, result, *, preview: bool = False) -> None:
        self.window._sensorgram_running = False
        self.window._sensorgram_running_signature = None
        self.window._sensorgram_cancel_event = None
        self.window._end_busy()
        self.window._sync_busy_cursor_state()
        self.window._sensorgram_frame_indices = np.asarray(result.frame_indices, dtype=np.int32)
        self.window._sensorgram_metric_values = np.asarray(result.metric_values, dtype=np.float64)
        self.window._sensorgram_metric_signal = np.asarray(result.metric_signal, dtype=np.float64)
        if signature:
            self.window._sensorgram_cache[signature] = result
            self.window._sensorgram_cache.move_to_end(signature)
            while len(self.window._sensorgram_cache) > self.window.SENSORGRAM_CACHE_SIZE:
                self.window._sensorgram_cache.popitem(last=False)
        self.set_sensorgram_series(self.window._sensorgram_frame_indices, self.window._sensorgram_metric_values)
        summary = (
            f"{self.window._analysis_metric_label()} | Calculated {result.completed_count}/{result.total_count} spectral cubes"
            f" | Polynomial order {self.window._analysis_poly_order()}"
        )
        if result.cancelled:
            summary = (
                f"{self.window._analysis_metric_label()} | Stopped after {result.completed_count}/{result.total_count} spectral cubes"
                f" | Polynomial order {self.window._analysis_poly_order()}"
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

    def on_frame_range_changed(self, *_args) -> None:
        self.window._on_analysis_frame_range_changed(*_args)

    def update_control_state(self) -> None:
        self.window._update_analysis_control_state()

    def update_plot_labels(self) -> None:
        self.window._update_sensorgram_plot_labels()

    def clear_sensorgram(self, summary_text: str) -> None:
        self.window._sensorgram_frame_indices = np.asarray([], dtype=np.int32)
        self.window._sensorgram_metric_values = np.asarray([], dtype=np.float64)
        self.window._sensorgram_metric_signal = np.asarray([], dtype=np.float64)
        self.window._pending_sensorgram_payload = None
        self.window.sensorgram_curve.setData([], [])
        self.window.sensorgram_current_point.setData([], [])
        self.update_plot_labels()
        self.update_selection_highlight(force=True)
        self.window.sensorgram_summary_label.setText(summary_text)

    def set_sensorgram_series(self, frame_indices, metric_values, *, summary_text: str | None = None) -> None:
        frames = np.asarray(frame_indices, dtype=np.int32)
        metrics = np.asarray(metric_values, dtype=np.float64)
        valid_mask = np.isfinite(frames) & np.isfinite(metrics)
        self.window._sensorgram_frame_indices = frames.copy()
        self.window._sensorgram_metric_values = metrics.copy()
        self.window.sensorgram_curve.setData(frames[valid_mask], metrics[valid_mask])
        self.update_plot_labels()
        self.update_selection_highlight(force=True)
        if np.any(valid_mask):
            x_values = frames[valid_mask].astype(np.float64, copy=False)
            y_values = metrics[valid_mask].astype(np.float64, copy=False)
            self.window.sensorgram_plot.setXRange(float(np.min(x_values)), float(np.max(x_values)), padding=0.03)
            y_min = float(np.min(y_values))
            y_max = float(np.max(y_values))
            y_span = max(y_max - y_min, 0.05)
            self.window.sensorgram_plot.setYRange(y_min - y_span * 0.08, y_max + y_span * 0.12, padding=0.0)
        self.update_current_point()
        if summary_text is not None:
            self.window.sensorgram_summary_label.setText(summary_text)

    def prepare_absorbance_spectrum_payload(self):
        return self._prepare_absorbance_spectrum_payload()

    def available_analysis_frames(self) -> list[int]:
        return self.window._available_analysis_frames()

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
        selected_roi_ids = self.window._selected_spectrum_spot_ids()
        if not selected_roi_ids:
            self.clear_sensorgram("Detect or select ROIs before calculating the sensorgram.")
            return
        selected_roi_id_set = set(selected_roi_ids)
        selected_source_rois = [deepcopy(roi) for roi in self.window._state.area_rois if roi.area_roi_id in selected_roi_id_set]
        if not selected_source_rois:
            self.clear_sensorgram("Detect or select ROIs before calculating the sensorgram.")
            return
        frames = self.available_analysis_frames()
        if not frames:
            self.clear_sensorgram("No spectral cubes are available in the selected range.")
            return
        signature = self.window._sensorgram_signature_for_selection(frames, selected_roi_ids, selected_source_rois)
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
            self.window._pending_sensorgram_payload = (signature, frames, selected_roi_ids, selected_source_rois)
            self.window._set_sensorgram_summary_text(
                f"{self.window._analysis_metric_label()} | Updating {len(frames)} spectral cubes"
            )
            return
        self._start_sensorgram_worker(signature, frames, selected_roi_ids, selected_source_rois)

    def preview_sensorgram_from_cache(self) -> bool:
        if not self.window._analysis_enabled or self.window._state.dataset is None or self.window._chromatic_setup_active:
            return False
        selected_roi_ids = self.window._selected_spectrum_spot_ids()
        if not selected_roi_ids:
            return False
        selected_roi_id_set = set(selected_roi_ids)
        selected_source_rois = [deepcopy(roi) for roi in self.window._state.area_rois if roi.area_roi_id in selected_roi_id_set]
        if not selected_source_rois:
            return False
        frames = self.available_analysis_frames()
        if not frames:
            return False
        signature = self.window._sensorgram_signature_for_selection(frames, selected_roi_ids, selected_source_rois)
        if signature is None:
            return False
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is None:
            return False
        self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
        return True

    def _start_sensorgram_worker(
        self,
        signature: tuple[object, ...],
        frames: list[int],
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

        preprocessing = self.window._state.preprocessing
        dataset = self.window._state.dataset
        use_fast_path = (
            len(selected_roi_ids) == 1
            and bool(getattr(preprocessing, "local_ring_normalization_enabled", False))
            and dataset is not None
            and dataset.is_ome_zarr
            and float(getattr(preprocessing, "rotation_angle_deg", 0.0)) == 0.0
            and not bool(getattr(preprocessing, "flip_horizontal", False))
            and not bool(getattr(preprocessing, "flip_vertical", False))
        )

        self.window._sensorgram_cancel_event = threading.Event()
        self.window._sensorgram_started_at = time.perf_counter()
        self.window._pending_sensorgram_payload = None
        self.clear_sensorgram("")
        self.window._update_analysis_control_state()
        fast_label = " [fast]" if use_fast_path else ""
        self.window._set_sensorgram_summary_text(
            f"{self.window._analysis_metric_label()}{fast_label} | Preparing {len(frames)} spectral cubes"
            f" | Range {frames[0]}-{frames[-1]}"
        )
        self.window._set_status_text("Preparing fitted sensorgram...")
        self.window._begin_busy("Preparing fitted sensorgram...", determinate=True)

        if use_fast_path:
            frame_payload_builder = lambda frame, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois: self._prepare_fast_spectrum_payload_for_frame(
                frame,
                selected_roi_ids,
                selected_source_rois,
            )
            task_fn = _absorbance_spectrum_fast_task
        else:
            frame_payload_builder = lambda frame, selected_roi_ids=selected_roi_ids, selected_source_rois=selected_source_rois: self._cached_sensorgram_frame_payload(
                frame,
                selected_roi_ids,
                selected_source_rois,
            )
            task_fn = None

        worker = FunctionWorker(
            _sensorgram_metric_task,
            frames,
            self.window._analysis_poly_order(),
            self.window._analysis_metric_key(),
            cancel_event=self.window._sensorgram_cancel_event,
            supports_progress=True,
            supports_partial=True,
            frame_payload_builder=frame_payload_builder,
            task_fn=task_fn,
        )
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.partial.connect(
            lambda point, request_id=request_id, total=len(frames): self.on_sensorgram_partial_result(request_id, total, point)
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
        self.window._sensorgram_frame_indices = np.append(self.window._sensorgram_frame_indices, int(point.frame_index)).astype(np.int32, copy=False)
        self.window._sensorgram_metric_values = np.append(self.window._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self.window._sensorgram_metric_signal = np.append(self.window._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        self.set_sensorgram_series(
            self.window._sensorgram_frame_indices,
            self.window._sensorgram_metric_values,
            summary_text=f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_frame_indices.size}/{total_count} spectral cubes",
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
        if self.window._pending_sensorgram_payload is not None:
            self.start_pending_sensorgram_refresh()

    def start_pending_sensorgram_refresh(self) -> None:
        if self.window._pending_sensorgram_payload is None:
            return
        signature, frames, selected_roi_ids, selected_source_rois = self.window._pending_sensorgram_payload
        self.window._pending_sensorgram_payload = None
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is not None:
            self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
            return
        self._start_sensorgram_worker(signature, list(frames), tuple(selected_roi_ids), list(selected_source_rois))

    def start_pending_absorbance_spectrum_refresh(self) -> None:
        if self.window._pending_absorbance_spectrum_payload is None:
            return
        from lspr_imaging_app.gui.main_window import FunctionWorker, _absorbance_spectrum_task

        signature, payload = self.window._pending_absorbance_spectrum_payload
        self.window._pending_absorbance_spectrum_payload = None
        request_id = self.window._absorbance_spectrum_request_id + 1
        self.window._absorbance_spectrum_request_id = request_id
        self.window._absorbance_spectrum_running = True
        self.window._absorbance_spectrum_running_signature = signature
        self.window._begin_busy("Updating absorbance spectrum...", determinate=True)
        worker = FunctionWorker(_absorbance_spectrum_task, *payload, supports_progress=True)
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.result.connect(lambda result, request_id=request_id, signature=signature: self.on_absorbance_spectrum_ready(request_id, signature, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self.on_absorbance_spectrum_failed(request_id, message))
        self.window._thread_pool.start(worker)

    def on_absorbance_spectrum_ready(self, request_id: int, signature, result) -> None:
        import time

        self.window._absorbance_spectrum_running = False
        self.window._absorbance_spectrum_running_signature = None
        self.window._end_busy()
        if request_id != self.window._absorbance_spectrum_request_id:
            if self.window._pending_absorbance_spectrum_payload is not None:
                self.start_pending_absorbance_spectrum_refresh()
            return
        started_at = self.window._absorbance_spectrum_started_at
        elapsed = self.window._format_elapsed_seconds(time.perf_counter() - started_at) if started_at is not None else ""
        self.window._absorbance_spectrum_cache[signature] = result
        self.window._absorbance_spectrum_cache.move_to_end(signature)
        while len(self.window._absorbance_spectrum_cache) > self.window.ABSORBANCE_SPECTRUM_CACHE_SIZE:
            self.window._absorbance_spectrum_cache.popitem(last=False)
        self._store_spot_absorbance_cache(result)
        self.window._absorbance_spectrum_dirty = False
        self.window._apply_absorbance_spectrum_result(result)
        if elapsed:
            self.window._set_status_text(f"Absorbance spectrum updated in {elapsed}.")
        if self.window._pending_absorbance_spectrum_payload is not None:
            self.start_pending_absorbance_spectrum_refresh()

    def on_absorbance_spectrum_failed(self, request_id: int, message: str) -> None:
        self.window._absorbance_spectrum_running = False
        self.window._absorbance_spectrum_running_signature = None
        self.window._end_busy()
        if request_id == self.window._absorbance_spectrum_request_id:
            self.window._background_error("Spectral absorbance", message)
        if self.window._pending_absorbance_spectrum_payload is not None:
            self.start_pending_absorbance_spectrum_refresh()

    def update_current_point(self) -> None:
        current_frame = self.window._current_frame()
        if current_frame is None or self.window._sensorgram_frame_indices.size == 0:
            self.window.sensorgram_current_point.setData([], [])
            return
        matches = np.where(self.window._sensorgram_frame_indices == int(current_frame))[0]
        if matches.size == 0:
            self.window.sensorgram_current_point.setData([], [])
            return
        index = int(matches[-1])
        value = float(self.window._sensorgram_metric_values[index])
        if not np.isfinite(value):
            self.window.sensorgram_current_point.setData([], [])
            return
        self.window.sensorgram_current_point.setData([int(self.window._sensorgram_frame_indices[index])], [value])

    def mark_stale(self, reason: str | None = None) -> None:
        if self.window._sensorgram_running:
            return
        metric_label = self.window._analysis_metric_label()
        range_text = ""
        frame_range = self.window._current_analysis_frame_range()
        if frame_range is not None:
            range_text = f" | Spectral cubes {frame_range[0]}-{frame_range[1]}"
        message = reason or f"{metric_label} sensorgram is out of date | Press Calculate all spectral cubes{range_text}"
        self.clear_sensorgram(message)

    # ------------------------------------------------------------------
    # Cache / signature / serialization / payload-builder methods
    # (moved from MainWindow)
    # ------------------------------------------------------------------

    def _sensorgram_signature_for_selection(
        self,
        frames: list[int],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_roi_ids or not selected_source_rois or not frames:
            return None
        frame_signatures: list[tuple[object, ...]] = []
        for frame in frames:
            frame_signatures.append(
                (
                    int(frame),
                    tuple(
                        self.window._preprocessing_signature((int(frame), float(wavelength)))
                        for wavelength in self.window._wavelength_values
                    ),
                )
            )
        dataset_key = str(self.window._state.dataset.folder)
        return (
            dataset_key,
            tuple(selected_roi_ids),
            self.window._roi_signature(selected_source_rois),
            self.window._analysis_metric_key(),
            int(self.window._analysis_poly_order()),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            tuple(frame_signatures),
            round(float(self.window._state.area_roi_settings.reference_inner_radius_px), 3),
            round(float(self.window._state.area_roi_settings.reference_outer_radius_px), 3),
        )

    def _sensorgram_frame_payload_signature(
        self,
        frame: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        if self.window._state.dataset is None or not selected_roi_ids or not selected_source_rois:
            return None
        return (
            str(self.window._state.dataset.folder),
            int(frame),
            tuple(selected_roi_ids),
            self.window._roi_signature(selected_source_rois),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            tuple(
                self.window._preprocessing_signature((int(frame), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
            round(float(self.window._state.area_roi_settings.reference_inner_radius_px), 3),
            round(float(self.window._state.area_roi_settings.reference_outer_radius_px), 3),
        )

    def _cached_sensorgram_frame_payload(
        self,
        frame: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        signature = self._sensorgram_frame_payload_signature(frame, selected_roi_ids, selected_source_rois)
        if signature is None:
            return None
        with self.window._analysis_cache_lock:
            cached = self.window._sensorgram_frame_payload_cache.get(signature)
            if cached is not None:
                self.window._sensorgram_frame_payload_cache.move_to_end(signature)
                logger.debug(
                    "SG payload cache hit | frame=%s rois=%s",
                    int(frame),
                    len(selected_roi_ids),
                )
                return cached
        payload = self._prepare_absorbance_spectrum_payload_for_frame(frame, selected_roi_ids, selected_source_rois)
        if payload is None:
            return None
        with self.window._analysis_cache_lock:
            self.window._sensorgram_frame_payload_cache[signature] = payload
            self.window._sensorgram_frame_payload_cache.move_to_end(signature)
            while len(self.window._sensorgram_frame_payload_cache) > self.window.SENSORGRAM_FRAME_PAYLOAD_CACHE_SIZE:
                self.window._sensorgram_frame_payload_cache.popitem(last=False)
        logger.debug(
            "SG payload cache built | frame=%s rois=%s",
            int(frame),
            len(selected_roi_ids),
        )
        return payload

    def _absorbance_spectrum_signature_for_source_spots(
        self,
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        frame = self.window._current_frame()
        if frame is None or not selected_source_rois:
            return None
        selected_roi_ids = tuple(int(roi.area_roi_id) for roi in selected_source_rois)
        return (
            int(frame),
            tuple(round(float(value), 6) for value in self.window._wavelength_values),
            selected_roi_ids,
            tuple(
                self.window._chromatic_signature_for_image_key((int(frame), float(wavelength)))
                for wavelength in self.window._wavelength_values
            ),
        )

    def _absorbance_spectrum_signature(self) -> tuple[object, ...] | None:
        return self._absorbance_spectrum_signature_for_source_spots(self.window._selected_source_rois_snapshot())

    def _cached_absorbance_result_from_spot_cache(
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
            cached_result = self.window._spot_absorbance_cache.get(roi_signature)
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
            "area_roi_results": {
                str(int(spot_id)): AnalysisController._serialize_absorbance_result(spot_result)
                for spot_id, spot_result in (result.area_roi_results or {}).items()
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
                    spot_id = int(key)
                except Exception:
                    continue
                roi_results[spot_id] = AnalysisController._deserialize_absorbance_result(value)
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
            area_roi_results=roi_results,
        )

    @staticmethod
    def _serialize_sensorgram_result(result: SensorgramComputationResult) -> dict:
        return {
            "frame_indices": [int(value) for value in np.asarray(result.frame_indices, dtype=np.int32)],
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
                frame_indices=np.asarray([], dtype=np.int32),
                metric_values=np.asarray([], dtype=np.float64),
                metric_signal=np.asarray([], dtype=np.float64),
                completed_count=0,
                total_count=0,
                cancelled=False,
            )
        return SensorgramComputationResult(
            frame_indices=np.asarray(payload.get("frame_indices", []), dtype=np.int32),
            metric_values=np.asarray(payload.get("metric_values", []), dtype=np.float64),
            metric_signal=np.asarray(payload.get("metric_signal", []), dtype=np.float64),
            completed_count=int(payload.get("completed_count", 0)),
            total_count=int(payload.get("total_count", 0)),
            prep_seconds=float(payload.get("prep_seconds", 0.0)),
            fit_seconds=float(payload.get("fit_seconds", 0.0)),
            total_seconds=float(payload.get("total_seconds", 0.0)),
            cancelled=bool(payload.get("cancelled", False)),
        )

    def _prepare_absorbance_spectrum_payload_for_frame(
        self,
        frame: int,
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
            record = self.window._record_map.get((frame, wavelength))
            if record is None:
                continue
            image_key = (frame, float(wavelength))
            preprocessing_spots = deepcopy(self.window._rois_for_preprocessing(image_key))
            affine_matrix = self.window._chromatic_affine_for_image_key(image_key)
            if affine_matrix is not None:
                affine_matrix = np.asarray(affine_matrix, dtype=np.float64)
            external_mask, external_mask_processed = self.window._effective_external_mask_for_record(record.path, processed_space=True)
            measurement_payload.append(
                (
                    float(wavelength),
                    str(record.path),
                    preprocessing_spots,
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

    def _prepare_fast_spectrum_payload_for_frame(
        self,
        frame: int,
        selected_roi_ids: tuple,
        selected_source_rois: list,
    ) -> tuple | None:
        """Build a lightweight payload for _absorbance_spectrum_fast_task.

        Used when local_ring_normalization_enabled is True and the dataset is OME-Zarr
        with no rotation or flip transforms.  The fast task reads only the ROI bounding
        box from the zarr array and skips global background flattening.
        """
        if self.window._state.dataset is None or not selected_source_rois or len(selected_source_rois) != 1:
            return None
        roi = selected_source_rois[0]
        preprocessing = self.window._state.preprocessing
        crop_x = int(preprocessing.crop.x) if preprocessing.crop.enabled else 0
        crop_y = int(preprocessing.crop.y) if preprocessing.crop.enabled else 0
        return (
            self.window._state.dataset,
            int(frame),
            list(self.window._wavelength_values),
            dict(self.window._record_map),
            deepcopy(roi),
            float(self.window._state.area_roi_settings.sample_radius_px),
            float(self.window._state.area_roi_settings.reference_inner_radius_px),
            float(self.window._state.area_roi_settings.reference_outer_radius_px),
            crop_x,
            crop_y,
        )

    def _prepare_absorbance_spectrum_payload(
        self,
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> tuple[tuple[object, ...], tuple[object, ...]] | None:
        if self.window._state.dataset is None:
            return None
        selected_source_rois = self.window._selected_source_rois_snapshot() if selected_source_rois is None else list(selected_source_rois)
        if not selected_source_rois:
            return None
        signature = self._absorbance_spectrum_signature_for_source_spots(selected_source_rois)
        if signature is None:
            return None
        frame = int(signature[0])
        selected_roi_ids = tuple(roi.area_roi_id for roi in selected_source_rois)
        payload = self._prepare_absorbance_spectrum_payload_for_frame(frame, selected_roi_ids, selected_source_rois)
        if payload is None:
            return None
        return signature, payload

    # ------------------------------------------------------------------
    # Result / event handler methods (moved from MainWindow)
    # ------------------------------------------------------------------

    def _on_absorbance_spectrum_payload_ready(
        self,
        request_id: int,
        expected_signature: tuple[object, ...],
        prepared: tuple[tuple[object, ...], tuple[object, ...]] | None,
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
        signature, payload = prepared
        if signature != expected_signature:
            self.window._absorbance_spectrum_dirty = True
            self.window._end_busy("Select ROIs to show absorbance spectrum.")
            return
        self.window._pending_absorbance_spectrum_payload = (signature, payload)
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
            cached_spot_result = self.window._spot_absorbance_cache.get(roi_signature)
            if cached_spot_result is not None:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_spot_result)
                self.window._spot_absorbance_cache.move_to_end(roi_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._absorbance_spectrum_signature_for_source_spots(selected_source_rois)
        if signature is not None:
            cached_result = self.window._cached_absorbance_result_for_selection(signature, selected_roi_ids, selected_source_rois)
            if cached_result is not None:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_result)
                frame_signature = self.window._absorbance_frame_signature(signature)
                if frame_signature is not None and frame_signature in self.window._absorbance_frame_cache:
                    self.window._absorbance_frame_cache.move_to_end(frame_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        missing_source_spots = [
            roi
            for roi, signature_value in zip(selected_source_rois, roi_signatures, strict=False)
            if signature_value is None or self.window._spot_absorbance_cache.get(signature_value) is None
        ]
        target_source_spots = missing_source_spots if missing_source_spots else selected_source_rois
        signature = self._absorbance_spectrum_signature_for_source_spots(target_source_spots)
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
        self.window._start_absorbance_spectrum_preparation(signature, target_source_spots)

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
            if roi_signature is not None and roi_signature in self.window._spot_absorbance_cache and not self.window._absorbance_spectrum_dirty:
                self.window._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(self.window._spot_absorbance_cache[roi_signature])
                self.window._spot_absorbance_cache.move_to_end(roi_signature)
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

    def _on_analysis_frame_range_changed(self, *_args) -> None:
        self.window._save_control_preferences()
        if self.window.analysis_start_frame_spin.value() > self.window.analysis_end_frame_spin.value():
            self.window.analysis_start_frame_spin.blockSignals(True)
            self.window.analysis_end_frame_spin.blockSignals(True)
            start = min(self.window.analysis_start_frame_spin.value(), self.window.analysis_end_frame_spin.value())
            end = max(self.window.analysis_start_frame_spin.value(), self.window.analysis_end_frame_spin.value())
            self.window.analysis_start_frame_spin.setValue(start)
            self.window.analysis_end_frame_spin.setValue(end)
            self.window.analysis_start_frame_spin.blockSignals(False)
            self.window.analysis_end_frame_spin.blockSignals(False)
        if self.window._analysis_live_preview_enabled and not self.preview_sensorgram_from_cache():
            self.mark_stale(
                f"{self.window._analysis_metric_label()} sensorgram is out of date | Press Calculate all spectral cubes"
            )
        elif not self.window._analysis_live_preview_enabled:
            self.window._mark_sensorgram_stale()

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
        selected_roi_ids = self.window._selected_spectrum_spot_ids()
        if not selected_roi_ids:
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if not selected_source_rois:
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return

        frames = self.window._available_analysis_frames()
        if not frames:
            self.window._clear_sensorgram("No spectral cubes are available in the selected range.")
            return

        cached_signature = self.window._sensorgram_signature_for_selection(frames, selected_roi_ids, selected_source_rois)
        if cached_signature is not None:
            with self.window._analysis_cache_lock:
                cached_sensorgram = self.window._sensorgram_cache.get(cached_signature)
                if cached_sensorgram is not None:
                    self.window._sensorgram_cache.move_to_end(cached_signature)
                    self.window._append_workflow_log(
                        f"SG cache hit | frames {len(frames)} | metric {self.window._analysis_metric_label()}",
                        level="debug",
                    )
                    self.window._append_workflow_log(
                        f"SG cache summary | payload hit {len(frames)} build 0 | result hit 1 build 0",
                        level="debug",
                    )
                    self.window._sensorgram_frame_indices = np.asarray(cached_sensorgram.frame_indices, dtype=np.int32)
                    self.window._sensorgram_metric_values = np.asarray(cached_sensorgram.metric_values, dtype=np.float64)
                    self.window._sensorgram_metric_signal = np.asarray(cached_sensorgram.metric_signal, dtype=np.float64)
                    self.window._set_sensorgram_series(self.window._sensorgram_frame_indices, self.window._sensorgram_metric_values)
                    summary = (
                        f"{self.window._analysis_metric_label()} | Cached {cached_sensorgram.completed_count}/"
                        f"{cached_sensorgram.total_count} spectral cubes | Polynomial order {self.window._analysis_poly_order()}"
                    )
                    self.window._set_sensorgram_summary_text(summary)
                    self.window._set_status_text("Sensorgram cache used.")
                    return
        self.window._sensorgram_running_signature = cached_signature

        self.window._append_workflow_log(
            f"SG calc start | rois {len(selected_roi_ids)} | frames {len(frames)} | metric {self.window._analysis_metric_label()}",
            level="info",
        )
        self._start_sensorgram_worker(cached_signature, frames, selected_roi_ids, selected_source_rois)

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
        self.window._sensorgram_frame_indices = np.append(self.window._sensorgram_frame_indices, int(point.frame_index)).astype(np.int32, copy=False)
        self.window._sensorgram_metric_values = np.append(self.window._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self.window._sensorgram_metric_signal = np.append(self.window._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        self.window._set_sensorgram_series(
            self.window._sensorgram_frame_indices,
            self.window._sensorgram_metric_values,
            summary_text=(
                f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_frame_indices.size}/{total_count} spectral cubes"
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
        self.window._sensorgram_frame_indices = np.asarray(result.frame_indices, dtype=np.int32)
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
                f"SG cache store | frames {int(result.completed_count)}/{int(result.total_count)}",
                level="debug",
            )
            self.window._append_workflow_log(
                f"SG cache summary | payload result cached | prep {self.window._format_elapsed_seconds(result.prep_seconds)}",
                level="debug",
            )
        self.window._set_sensorgram_series(self.window._sensorgram_frame_indices, self.window._sensorgram_metric_values)
        self.window._append_workflow_log(
            f"SG done | prep {self.window._format_elapsed_seconds(result.prep_seconds)} | fit {self.window._format_elapsed_seconds(result.fit_seconds)}",
            level="success",
        )
        summary = (
            f"{self.window._analysis_metric_label()} | Calculated {result.completed_count}/{result.total_count} spectral cubes"
            f" | Polynomial order {self.window._analysis_poly_order()}"
        )
        if result.cancelled:
            summary = (
                f"{self.window._analysis_metric_label()} | Stopped after {result.completed_count}/{result.total_count} spectral cubes"
                f" | Polynomial order {self.window._analysis_poly_order()}"
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
        frame_signature = self.window._absorbance_frame_signature(signature)
        if frame_signature is not None:
            self.window._absorbance_frame_cache[frame_signature] = result
            self.window._absorbance_frame_cache.move_to_end(frame_signature)
            while len(self.window._absorbance_frame_cache) > self.window.ABSORBANCE_FRAME_CACHE_SIZE:
                self.window._absorbance_frame_cache.popitem(last=False)
            self.window._append_workflow_log("Spec frame cache store", level="debug")
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
        selected_roi_ids = self.window._selected_spectrum_spot_ids()
        series_payloads: list[tuple[str, int, AbsorbanceSpectrumResult]] = []
        if result.area_roi_results:
            if selected_roi_ids:
                for spot_id in selected_roi_ids:
                    spot_result = result.area_roi_results.get(int(spot_id))
                    if spot_result is not None:
                        series_payloads.append((f"ROI {int(spot_id)}", int(spot_id), spot_result))
            else:
                for spot_id in sorted(result.area_roi_results):
                    series_payloads.append((f"ROI {int(spot_id)}", int(spot_id), result.area_roi_results[int(spot_id)]))
        if selected_roi_ids and len(series_payloads) < len(selected_roi_ids):
            existing_ids = {int(spot_id) for _, spot_id, _ in series_payloads}
            for spot_id in selected_roi_ids:
                if int(spot_id) in existing_ids:
                    continue
                roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(spot_id)), None)
                if roi is None:
                    continue
                roi_signature = self.window._roi_absorbance_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._spot_absorbance_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(spot_id)}", int(spot_id), cached_result))
        if not series_payloads and len(selected_roi_ids) > 1:
            for spot_id in selected_roi_ids:
                roi = next((roi for roi in self.window._state.area_rois if int(roi.area_roi_id) == int(spot_id)), None)
                if roi is None:
                    continue
                roi_signature = self.window._roi_absorbance_signature(roi)
                if roi_signature is None:
                    continue
                cached_result = self.window._spot_absorbance_cache.get(roi_signature)
                if cached_result is not None:
                    series_payloads.append((f"ROI {int(spot_id)}", int(spot_id), cached_result))
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
        for label, spot_id, spot_result in series_payloads:
            rendered = self.window._add_spectrum_series(
                spot_id=spot_id,
                result=spot_result,
                label=label,
                highlighted=bool(highlighted_ids) and int(spot_id) in highlighted_ids,
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
                if metric_value is not None and metric_signal is not None and np.isfinite(metric_value) and np.isfinite(metric_signal):
                    self.window.spectrum_metric_point.setData([float(metric_value)], [float(metric_signal)])
                else:
                    self.window.spectrum_metric_point.setData([], [])
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
                fit_text = (
                    f" | {self.window._analysis_metric_label()} {float(metric_value):.3f} nm"
                    f" | Poly {self.window._analysis_poly_order()}"
                )
        else:
            self.window.spectrum_current_point.setData([], [])
            self.window.spectrum_metric_point.setData([], [])
            fit_text = f" | {len(series_payloads)} ROI series"
        fit_seconds = time.perf_counter() - fit_started
        self.window._last_absorbance_fit_seconds = fit_seconds

        frame = self.window._current_frame()
        sample_pixels = int(np.nanmax(primary_result.sample_pixel_count)) if primary_result.sample_pixel_count.size else 0
        reference_pixels = int(np.nanmax(primary_result.reference_pixel_count)) if primary_result.reference_pixel_count.size else 0
        self.window._set_spectrum_summary_text(
            f"{self.window._spectrum_selection_label()} | Spectral cube {frame if frame is not None else '-'}"
            f" | ROI px: sample {sample_pixels}, reference {reference_pixels}{current_text}{fit_text}"
        )
        self.window._update_single_frame_sensorgram(metric_value, metric_signal)
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
