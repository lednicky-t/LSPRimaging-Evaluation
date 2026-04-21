from __future__ import annotations

import numpy as np
from copy import deepcopy
from PyQt6.QtGui import QColor
import pyqtgraph as pg


class AnalysisController:
    def __init__(self, window) -> None:
        self.window = window

    def _sensorgram_selection_color(self) -> QColor:
        selected_spot_ids = tuple(self.window._selected_spectrum_spot_ids())
        if len(selected_spot_ids) == 1:
            return QColor(self.window._spot_spectrum_color(int(selected_spot_ids[0])))
        if selected_spot_ids:
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
        spot_results = getattr(result, "spot_results", None)
        if not spot_results:
            return
        spot_by_id = {int(spot.spot_id): spot for spot in self.window._state.detected_spots}
        for spot_id, spot_result in spot_results.items():
            spot = spot_by_id.get(int(spot_id))
            if spot is None:
                continue
            signature = self.window._spot_absorbance_signature(spot)
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
            f"{self.window._analysis_metric_label()} | Calculated {result.completed_count}/{result.total_count} frames"
            f" | Polynomial order {self.window._analysis_poly_order()}"
        )
        if result.cancelled:
            summary = (
                f"{self.window._analysis_metric_label()} | Stopped after {result.completed_count}/{result.total_count} frames"
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
        return self.window._prepare_absorbance_spectrum_payload()

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
        selected_spot_ids = self.window._selected_spectrum_spot_ids()
        if not selected_spot_ids:
            self.clear_sensorgram("Detect or select spots before calculating the sensorgram.")
            return
        selected_spot_id_set = set(selected_spot_ids)
        selected_source_spots = [deepcopy(spot) for spot in self.window._state.detected_spots if spot.spot_id in selected_spot_id_set]
        if not selected_source_spots:
            self.clear_sensorgram("Detect or select spots before calculating the sensorgram.")
            return
        frames = self.available_analysis_frames()
        if not frames:
            self.clear_sensorgram("No frames are available in the selected range.")
            return
        signature = self.window._sensorgram_signature_for_selection(frames, selected_spot_ids, selected_source_spots)
        if signature is None:
            self.clear_sensorgram("No spectra are available in the selected frame range.")
            return
        if self.window._sensorgram_running and self.window._sensorgram_running_signature == signature:
            return
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is not None and not self.window._sensorgram_running:
            self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
            return
        if self.window._sensorgram_running:
            self.window._pending_sensorgram_payload = (signature, frames, selected_spot_ids, selected_source_spots)
            self.window._set_sensorgram_summary_text(
                f"{self.window._analysis_metric_label()} | Updating {len(frames)} frames"
            )
            return
        self._start_sensorgram_worker(signature, frames, selected_spot_ids, selected_source_spots)

    def preview_sensorgram_from_cache(self) -> bool:
        if not self.window._analysis_enabled or self.window._state.dataset is None or self.window._chromatic_setup_active:
            return False
        selected_spot_ids = self.window._selected_spectrum_spot_ids()
        if not selected_spot_ids:
            return False
        selected_spot_id_set = set(selected_spot_ids)
        selected_source_spots = [deepcopy(spot) for spot in self.window._state.detected_spots if spot.spot_id in selected_spot_id_set]
        if not selected_source_spots:
            return False
        frames = self.available_analysis_frames()
        if not frames:
            return False
        signature = self.window._sensorgram_signature_for_selection(frames, selected_spot_ids, selected_source_spots)
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
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot],
    ) -> None:
        import time

        self.window._sensorgram_request_id += 1
        request_id = self.window._sensorgram_request_id
        self.window._sensorgram_running = True
        self.window._sensorgram_running_signature = signature
        import threading

        from lspr_imaging_app.gui.main_window import FunctionWorker, _sensorgram_metric_task

        self.window._sensorgram_cancel_event = threading.Event()
        self.window._sensorgram_started_at = time.perf_counter()
        self.window._pending_sensorgram_payload = None
        self.clear_sensorgram("")
        self.window._update_analysis_control_state()
        self.window._set_sensorgram_summary_text(
            f"{self.window._analysis_metric_label()} | Preparing {len(frames)} frames"
            f" | Range {frames[0]}-{frames[-1]}"
        )
        self.window._set_status_text("Preparing fitted sensorgram...")
        self.window._begin_busy("Preparing fitted sensorgram...", determinate=True)
        worker = FunctionWorker(
            _sensorgram_metric_task,
            frames,
            self.window._analysis_poly_order(),
            self.window._analysis_metric_key(),
            cancel_event=self.window._sensorgram_cancel_event,
            supports_progress=True,
            supports_partial=True,
            frame_payload_builder=lambda frame, selected_spot_ids=selected_spot_ids, selected_source_spots=selected_source_spots: self.window._cached_sensorgram_frame_payload(
                frame,
                selected_spot_ids,
                selected_source_spots,
            ),
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
            summary_text=f"{self.window._analysis_metric_label()} | Calculating {self.window._sensorgram_frame_indices.size}/{total_count} frames",
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
        signature, frames, selected_spot_ids, selected_source_spots = self.window._pending_sensorgram_payload
        self.window._pending_sensorgram_payload = None
        cached_result = self.window._sensorgram_cache.get(signature)
        if cached_result is not None:
            self._apply_cached_sensorgram_result(signature, cached_result, preview=True)
            return
        self._start_sensorgram_worker(signature, list(frames), tuple(selected_spot_ids), list(selected_source_spots))

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
            range_text = f" | Frames {frame_range[0]}-{frame_range[1]}"
        message = reason or f"{metric_label} sensorgram is out of date | Press Calculate all frames{range_text}"
        self.clear_sensorgram(message)
