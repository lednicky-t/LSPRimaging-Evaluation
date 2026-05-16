from __future__ import annotations

from copy import deepcopy

from lspr_imaging_app.domain.models import ChromaticLandmarkObservation, ChromaticTransformModel

class ChromaticController:
    def __init__(self, window) -> None:
        self.window = window

    def section_applied_changed(self, applied: bool) -> None:
        window = self.window
        if window.chromatic_apply_check.isChecked() != bool(applied):
            window.chromatic_apply_check.setChecked(bool(applied))

    def update_control_state(self) -> None:
        self.window._update_chromatic_control_state()

    def start_workflow(self) -> None:
        window = self.window
        if window._state.dataset is None or not window._wavelength_values:
            window._set_status_text("Load a dataset before starting the radial chromatic workflow.")
            return
        window._push_undo_point("Chromatic workflow")
        current_frame = window._current_frame()
        if current_frame is None:
            current_frame = window._frame_values[0] if window._frame_values else 0
        sample_minimum = 1 if len(window._wavelength_values) <= 1 else 3
        sample_count = window._normalized_odd_count(
            int(window.chromatic_sample_count_spin.value()),
            sample_minimum,
            min(max(len(window._wavelength_values), sample_minimum), 7),
        )
        feature_count = int(window._chromatic_feature_count_value())
        sample_wavelengths = window._sampled_wavelengths(window._wavelength_values, sample_count)
        middle_wavelength = sample_wavelengths[len(sample_wavelengths) // 2]
        window._enter_chromatic_setup_mode()
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._state.preprocessing.chromatic_sample_image_count = sample_count
        window._state.preprocessing.chromatic_feature_count = feature_count
        window._state.preprocessing.reference_mode = "manual"
        window._state.preprocessing.reference_frame_index = int(current_frame)
        window._state.preprocessing.reference_wavelength_nm = float(middle_wavelength)
        window._state.preprocessing.chromatic_correction_enabled = False
        window._state.chromatic_landmarks.clear()
        window._state.chromatic_models.clear()
        window._selected_landmark_id = 1
        window._chromatic_landmark_marker_id = 1
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window.chromatic_sample_count_spin.blockSignals(True)
        window.chromatic_sample_count_spin.setValue(sample_count)
        window.chromatic_sample_count_spin.blockSignals(False)
        window._set_chromatic_feature_count_value(feature_count)
        window.chromatic_landmark_id_spin.blockSignals(True)
        window.chromatic_landmark_id_spin.setMaximum(feature_count)
        window.chromatic_landmark_id_spin.setValue(1)
        window.chromatic_landmark_id_spin.blockSignals(False)
        window._update_reference_controls()
        window._update_reference_summary()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._schedule_processing_state_save()
        window._activate_chromatic_tool_after_refresh = True
        window._set_current_frame_and_wavelength(int(current_frame), float(sample_wavelengths[0]))
        window._append_workflow_log(
            f"Chromatic workflow start | samples {sample_count} | features {feature_count}",
            level="info",
        )
        window._set_status_text(
            f"Radial workflow started with {sample_count} sampled wavelength images and {feature_count} reference points."
        )
        self.auto_detect_landmarks(push_undo=False)

    def auto_detect_landmarks(self, *, push_undo: bool = True) -> None:
        window = self.window
        payload = window._chromatic_sample_payload()
        if not payload:
            window._set_status_text("Start the radial workflow before auto-detecting chromatic reference points.")
            return
        if push_undo:
            window._push_undo_point("Chromatic landmarks")
        window._chromatic_auto_restore_state = (
            deepcopy(window._state.chromatic_landmarks),
            deepcopy(window._state.chromatic_models),
            bool(window._state.preprocessing.chromatic_correction_enabled),
        )
        window._state.preprocessing.chromatic_correction_enabled = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        request_id = window._chromatic_auto_request_id + 1
        window._chromatic_auto_request_id = request_id
        window._chromatic_auto_running = True
        window._update_chromatic_control_state()
        from lspr_imaging_app.gui.main_window import FunctionWorker, _auto_chromatic_landmarks_task
        window._append_workflow_log(
            f"Chromatic auto-detect start | samples {len(payload)} | features {int(window._state.preprocessing.chromatic_feature_count)}",
            level="info",
        )
        worker = FunctionWorker(
            _auto_chromatic_landmarks_task,
            payload,
            deepcopy(window._state.preprocessing),
            int(window._state.preprocessing.chromatic_feature_count),
            int(getattr(window._state.preprocessing, "chromatic_subpixel_precision", 4)),
            supports_progress=True,
        )
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.result.connect(
            lambda observations, request_id=request_id: self._on_auto_ready(request_id, observations)
        )
        worker.signals.error.connect(
            lambda message, request_id=request_id: self._on_auto_error(request_id, message)
        )
        window._begin_busy("Auto-detecting chromatic reference points...", determinate=True)
        window._thread_pool.start(worker)

    def estimate_models(self) -> None:
        window = self.window
        dataset = window._state.dataset
        reference_key = window._reference_image_key()
        if dataset is None or reference_key is None:
            window._set_status_text("Load a dataset and set a reference image first.")
            return
        reference_record = window._reference_record()
        if reference_record is None:
            window._set_status_text("Reference image is missing from the dataset.")
            return
        mode = str(window._state.preprocessing.chromatic_registration_mode or "landmark_radial")
        sample_keys = window._chromatic_sample_image_keys()
        feature_ids = window._expected_chromatic_feature_ids()
        landmarks_payload = [
            (
                int(mark.landmark_id),
                int(mark.frame_index),
                float(mark.wavelength_nm),
                float(mark.x_px),
                float(mark.y_px),
            )
            for mark in window._state.chromatic_landmarks
        ]
        if mode == "landmark_radial":
            marks_by_sample: dict[tuple[int, float], set[int]] = {sample_key: set() for sample_key in sample_keys}
            for landmark_id, frame, wavelength, _x, _y in landmarks_payload:
                sample_key = (int(frame), float(wavelength))
                if sample_key in marks_by_sample:
                    marks_by_sample[sample_key].add(int(landmark_id))
            complete_samples = [
                sample_key
                for sample_key in sample_keys
                if all(feature_id in marks_by_sample[sample_key] for feature_id in feature_ids)
            ]
            if not complete_samples:
                best_sample_key = None
                best_count = -1
                best_missing: list[int] = []
                for sample_key in sample_keys:
                    missing = [feature_id for feature_id in feature_ids if feature_id not in marks_by_sample[sample_key]]
                    complete_count = len(feature_ids) - len(missing)
                    if complete_count > best_count:
                        best_count = complete_count
                        best_sample_key = sample_key
                        best_missing = missing
                missing_text = ", ".join(str(feature_id) for feature_id in best_missing[:6]) if best_missing else "unknown"
                if best_sample_key is None:
                    window._set_status_text("No chromatic reference points are available for the sampled images.")
                else:
                    window._set_status_text(
                        f"Mark all {len(feature_ids)} reference points on at least one sampled wavelength image before estimating "
                        f"chromatic transforms. Missing on the best sample: {missing_text}."
                    )
                return
            if reference_key not in complete_samples:
                reference_key = complete_samples[0]
        window._push_undo_point("Chromatic correction")
        window._chromatic_registration_request_id += 1
        request_id = window._chromatic_registration_request_id
        preprocessing = deepcopy(window._state.preprocessing)
        record_specs = [
            (int(record.key.frame_index), float(record.key.wavelength_nm), str(record.path))
            for record in dataset.records
        ]
        window._append_workflow_log(
            f"Chromatic estimation start | mode {mode} | records {len(record_specs)}",
            level="info",
        )
        from lspr_imaging_app.gui.main_window import FunctionWorker, _estimate_chromatic_models_task
        worker = FunctionWorker(
            _estimate_chromatic_models_task,
            record_specs,
            preprocessing,
            reference_key,
            landmarks_payload,
            supports_progress=True,
        )
        window._begin_busy("Estimating chromatic transforms...", determinate=True)
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.result.connect(
            lambda models, request_id=request_id: self._on_models_ready(request_id, models)
        )
        worker.signals.error.connect(lambda message: self._on_models_failed(message))
        window._thread_pool.start(worker)

    def _on_auto_ready(self, request_id: int, observations: list[tuple[int, int, float, float, float]]) -> None:
        window = self.window
        if request_id != window._chromatic_auto_request_id:
            window._end_busy()
            return
        window._chromatic_auto_running = False
        window._chromatic_auto_restore_state = None
        window._state.chromatic_landmarks = [
            ChromaticLandmarkObservation(
                landmark_id=int(feature_id),
                frame_index=int(frame),
                wavelength_nm=float(wavelength),
                x_px=float(x_px),
                y_px=float(y_px),
            )
            for feature_id, frame, wavelength, x_px, y_px in observations
        ]
        window._selected_landmark_id = 1
        window._chromatic_landmark_marker_id = 1
        window._sync_current_chromatic_feature_selection()
        window._update_landmark_overlays()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._schedule_processing_state_save()
        window._append_workflow_log(
            f"Chromatic auto-detect done | points {len(window._state.chromatic_landmarks)}",
            level="success",
        )
        window._end_busy("Automatic chromatic reference points detected. Adjust them if needed, then estimate transforms.")

    def _on_auto_error(self, request_id: int, message: str) -> None:
        window = self.window
        if request_id != window._chromatic_auto_request_id:
            window._end_busy()
            return
        window._chromatic_auto_running = False
        if window._chromatic_auto_restore_state is not None:
            landmarks, models, enabled = window._chromatic_auto_restore_state
            window._state.chromatic_landmarks = landmarks
            window._state.chromatic_models = models
            window._state.preprocessing.chromatic_correction_enabled = bool(enabled)
            window.chromatic_apply_check.blockSignals(True)
            window.chromatic_apply_check.setChecked(bool(enabled))
            window.chromatic_apply_check.blockSignals(False)
            window._invalidate_image_analysis_caches()
            window._invalidate_background_profile_cache()
        window._chromatic_auto_restore_state = None
        window._update_spot_overlays()
        window._update_ignore_mask_overlay()
        window._update_landmark_overlays()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._append_workflow_log(
            f"Chromatic auto-detect failed | {message}",
            level="error",
        )
        window._end_busy(f"Automatic chromatic reference-point detection failed: {message}")

    def _on_models_ready(self, request_id: int, models: list[ChromaticTransformModel]) -> None:
        window = self.window
        window._end_busy()
        if request_id != window._chromatic_registration_request_id:
            return
        window._state.chromatic_models = models
        window._state.preprocessing.chromatic_correction_enabled = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window._leave_chromatic_setup_mode()
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_chromatic_summary()
        window._schedule_processing_state_save()
        window._current_image_key = None
        window._schedule_image_refresh()
        window._append_workflow_log(
            f"Chromatic estimation done | models {len(models)}",
            level="success",
        )
        window._set_status_text(f"Estimated {len(models)} chromatic transform model(s). Use Apply radial transforms to enable them.")

    def _on_models_failed(self, message: str) -> None:
        self.window._end_busy()
        self.window._append_workflow_log(
            f"Chromatic estimation failed | {message}",
            level="error",
        )
        self.window._background_error("Chromatic correction", message)
