from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time

import numpy as np


class ImageRenderManager:
    def __init__(self, window) -> None:
        self.window = window

    def refresh_image(self) -> None:
        window = self.window
        dataset = window._state.dataset
        if dataset is None:
            return
        frame = window._current_frame()
        wavelength = window._current_wavelength()
        if frame is None or wavelength is None:
            return
        if str(window._state.preprocessing.reference_mode or "auto") != "manual":
            auto_key = window._auto_reference_image_key_for_frame(frame)
            if auto_key is not None:
                auto_frame, auto_wavelength = auto_key
                window._state.preprocessing.reference_mode = "auto"
                window._state.preprocessing.reference_frame_index = int(auto_frame)
                window._state.preprocessing.reference_wavelength_nm = float(auto_wavelength)
        window.frame_spin.blockSignals(True)
        window.frame_spin.setValue(int(frame))
        window.frame_spin.blockSignals(False)
        window.wavelength_spin.blockSignals(True)
        window.wavelength_spin.setValue(float(wavelength))
        window.wavelength_spin.blockSignals(False)
        window._update_reference_controls()
        window._update_reference_summary()
        window._update_sensorgram_current_point()
        record = window._record_map.get((frame, wavelength))
        if record is None:
            window.status_label.setText("Selected frame/wavelength combination is missing.")
            return
        image_key = (frame, wavelength)
        if window._current_image_key == image_key:
            return
        preprocessing = deepcopy(window._state.preprocessing)
        mask_settings = deepcopy(window._state.spot_detection) if preprocessing.flatten_background_exclude_mask else None
        spots = deepcopy(window._spots_for_preprocessing(image_key))
        _external_mask, external_mask_processed = window._effective_external_mask_for_record(record.path, processed_space=True)
        cache_key = window._processed_image_cache_key(record.path, image_key)
        signature = cache_key
        window._latest_image_refresh_signature = signature
        cached = window._get_processed_image_from_cache(cache_key)
        if cached is not None:
            started_at = time.perf_counter()
            window._apply_loaded_image(cached, record.path, image_key, frame, wavelength, record.path.name)
            elapsed = window._format_elapsed_seconds(time.perf_counter() - started_at)
            window._set_status_text(f"Img F{frame} {wavelength:g}nm | cache {elapsed}")
            return
        window._pending_image_refresh_payload = (
            signature,
            cache_key,
            str(record.path),
            record.path,
            image_key,
            frame,
            wavelength,
            record.path.name,
            (preprocessing, mask_settings, external_mask_processed),
            spots,
        )
        if not window._image_refresh_running:
            self.start_pending_image_refresh()
        window.status_label.setText(f"Loading {record.path.name}...")

    def start_pending_image_refresh(self) -> None:
        window = self.window
        if window._pending_image_refresh_payload is None:
            return
        (
            signature,
            cache_key,
            path_str,
            record_path,
            image_key,
            frame,
            wavelength,
            record_name,
            preprocessing,
            spots,
        ) = window._pending_image_refresh_payload
        from lspr_imaging_app.gui.main_window import FunctionWorker, _process_image_task

        external_mask, external_mask_processed = window._effective_external_mask_for_record(record_path, processed_space=True)
        window._pending_image_refresh_payload = None
        window._image_refresh_running = True
        window._image_refresh_started_at = time.perf_counter()
        window._begin_busy(f"Loading {record_name}...")
        if isinstance(preprocessing, tuple):
            preprocessing = (preprocessing[0], preprocessing[1], external_mask_processed)
        worker = FunctionWorker(
            _process_image_task,
            path_str,
            preprocessing,
            spots,
            external_mask,
            window._state.mask if window._mask_section_applied() else None,
        )
        worker.signals.result.connect(
            lambda processed,
            signature=signature,
            cache_key=cache_key,
            record_path=record_path,
            image_key=image_key,
            frame=frame,
            wavelength=wavelength,
            record_name=record_name: self.on_image_refresh_ready(
                signature,
                cache_key,
                record_path,
                image_key,
                frame,
                wavelength,
                record_name,
                processed,
            )
        )
        worker.signals.error.connect(lambda message: self.on_image_refresh_failed(message))
        window._thread_pool.start(worker)

    def on_image_refresh_ready(
        self,
        signature: tuple[object, ...],
        cache_key: tuple[object, ...],
        record_path: Path,
        image_key: tuple[int, float],
        frame: int,
        wavelength: float,
        record_name: str,
        processed: np.ndarray,
    ) -> None:
        window = self.window
        window._image_refresh_running = False
        started_at = window._image_refresh_started_at
        window._image_refresh_started_at = None
        window._end_busy()
        window._sync_busy_cursor_state()
        window._store_processed_image_in_cache(cache_key, processed)
        apply_started_at = time.perf_counter()
        if signature == window._latest_image_refresh_signature:
            self.apply_loaded_image(processed, record_path, image_key, frame, wavelength, record_name)
            apply_elapsed = window._format_elapsed_seconds(time.perf_counter() - apply_started_at)
            if apply_elapsed:
                window._append_workflow_log_throttled("image_apply", f"Image apply | {apply_elapsed}", level="debug", min_interval=2.0)
        elapsed = window._format_elapsed_seconds(time.perf_counter() - started_at) if started_at is not None else ""
        if elapsed:
            window._set_status_text(f"Img F{frame} {wavelength:g}nm | load {elapsed}")
        if window._pending_image_refresh_payload is not None:
            self.start_pending_image_refresh()

    def on_image_refresh_failed(self, message: str) -> None:
        window = self.window
        window._image_refresh_running = False
        window._image_refresh_started_at = None
        window._end_busy()
        window._sync_busy_cursor_state()
        window._background_error("Image refresh", message)
        if window._pending_image_refresh_payload is not None:
            self.start_pending_image_refresh()

    def apply_loaded_image(
        self,
        processed: np.ndarray,
        record_path: Path,
        image_key: tuple[int, float],
        frame: int,
        wavelength: float,
        record_name: str,
    ) -> None:
        window = self.window
        window._current_processed_image = processed
        window._current_record_path = record_path
        previous_image_key = window._current_image_key
        window._current_image_key = image_key
        window._auto_load_mask_for_current_record()
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_geometry_control_ranges(processed.shape)
        window._update_mask_file_button_state()
        window._refresh_mask_previews()
        is_reference_view = window._is_current_reference_image()
        window.detect_spots_button.setEnabled(is_reference_view)
        if not is_reference_view:
            window.mask_pencil_check.blockSignals(True)
            window.mask_pencil_check.setChecked(False)
            window.mask_pencil_check.blockSignals(False)
        window._sync_spot_edit_capabilities()
        window._sync_rectangle_roi_from_definition()
        window._sync_rectangle_roi_visibility()
        if hasattr(window, "flip_horizontal_action"):
            window.flip_horizontal_action.blockSignals(True)
            window.flip_horizontal_action.setChecked(window._state.preprocessing.flip_horizontal)
            window.flip_horizontal_action.blockSignals(False)
        if hasattr(window, "flip_vertical_action"):
            window.flip_vertical_action.blockSignals(True)
            window.flip_vertical_action.setChecked(window._state.preprocessing.flip_vertical)
            window.flip_vertical_action.blockSignals(False)
        window._seed_chromatic_landmarks_for_current_image()
        window._sync_current_chromatic_feature_selection()
        window._update_chromatic_control_state()
        window._sync_analysis_plots()
        if window._activate_chromatic_tool_after_refresh and window._is_chromatic_sample_image_key(window._current_image_key):
            window._activate_chromatic_tool_after_refresh = False
            window.chromatic_landmark_mark_button.setChecked(True)
        window._apply_main_image_content()
        window._update_reference_star_overlay()
        window._sync_ome_zarr_chunk_controls()
        window._update_ome_zarr_chunk_guide_overlay()
        restored_view = window._restore_chromatic_view_after_load()
        if not restored_view:
            restored_view = window._restore_pending_image_view_after_load()
        if not restored_view:
            restored_view = window._restore_saved_image_view_after_load()
        if bool(getattr(window, "_force_image_autorange_after_load", False)):
            window._force_image_autorange_after_load = False
            window.image_plot.autoRange()
            restored_view = True
        if not restored_view:
            window.image_plot.autoRange()
        flip_summary: list[str] = []
        if window._state.preprocessing.flip_horizontal:
            flip_summary.append("H")
        if window._state.preprocessing.flip_vertical:
            flip_summary.append("V")
        flip_text = ", ".join(flip_summary) if flip_summary else "None"
        window._update_image_name_overlay(None)
        if not window._chromatic_setup_active and not bool(getattr(window, "_image_tools_preview_only", False)):
            window._update_histogram(processed)
        window._sync_rotation_tool()
        window._sync_crop_tool(processed.shape)
        window._update_landmark_overlays()
        window._set_status_text(f"Img F{frame} {wavelength:g}nm")
        if not window._chromatic_setup_active and not bool(getattr(window, "_image_tools_preview_only", False)):
            window._schedule_processing_state_save()
            window._refresh_visible_spectrum_from_cache()
            window._analysis_controller.preview_sensorgram_from_cache()
        if (
            window._is_current_reference_image()
            and not window._chromatic_setup_active
            and not bool(getattr(window, "_image_tools_preview_only", False))
        ):
            window._request_spot_metrics_refresh(
                save_after=False,
                refresh_histogram=False,
            )
