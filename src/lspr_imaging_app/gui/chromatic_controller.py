from __future__ import annotations

from copy import deepcopy

import numpy as np
import pyqtgraph as pg

from lspr_imaging_app.domain.models import ChromaticLandmarkObservation, ChromaticTransformModel
from lspr_imaging_app.gui.analysis_tasks import _sampled_wavelengths
from lspr_imaging_app.gui.ui_helpers import chromatic_feature_count_value, chromatic_subpixel_precision_value
from lspr_imaging_app.gui.worker import ChromaticLandmarkAllOverlayBundle
from lspr_imaging_app.processing.chromatic import identity_affine_matrix

class ChromaticController:
    def __init__(self, window) -> None:
        self.window = window

    def feature_count_options(self) -> tuple[int, ...]:
        return (5, 15, 30)

    def feature_count_value(self) -> int:
        window = self.window
        return chromatic_feature_count_value(window.chromatic_feature_count_spin.currentData(), self.feature_count_options())

    def set_feature_count_value(self, value: int) -> None:
        window = self.window
        target = int(value)
        if target not in self.feature_count_options():
            target = 15
        index = max(window.chromatic_feature_count_spin.findData(target), 0)
        window.chromatic_feature_count_spin.blockSignals(True)
        window.chromatic_feature_count_spin.setCurrentIndex(index)
        window.chromatic_feature_count_spin.blockSignals(False)

    def subpixel_precision_options(self) -> tuple[int, ...]:
        return (1, 4, 9)

    def subpixel_precision_value(self) -> int:
        window = self.window
        return chromatic_subpixel_precision_value(window.chromatic_subpixel_precision_combo.currentData())

    def model_for_image_key(self, image_key: tuple[int, float] | None) -> ChromaticTransformModel | None:
        window = self.window
        if image_key is None:
            return None
        spectral_cube_index, wavelength = image_key
        for model in window._state.chromatic_models:
            if int(model.spectral_cube_index) == int(spectral_cube_index) and abs(float(model.wavelength_nm) - float(wavelength)) < 1e-6:
                return model
        return None

    def affine_for_image_key(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        window = self.window
        if image_key is None or window._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        if not window._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def affine_for_image_key_any(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        window = self.window
        if image_key is None or window._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def signature_for_image_key(self, image_key: tuple[int, float] | None) -> tuple[object, ...] | None:
        window = self.window
        if image_key is None or not window._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return (
            int(model.spectral_cube_index),
            round(float(model.wavelength_nm), 6),
            tuple(tuple(round(float(value), 6) for value in row) for row in model.affine_matrix),
        )

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
        current_spectral_cube = window._current_spectral_cube()
        if current_spectral_cube is None:
            current_spectral_cube = window._spectral_cube_values[0] if window._spectral_cube_values else 0
        sample_minimum = 1 if len(window._wavelength_values) <= 1 else 3
        sample_count = window._normalized_odd_count(
            int(window.chromatic_sample_count_spin.value()),
            sample_minimum,
            min(max(len(window._wavelength_values), sample_minimum), 7),
        )
        feature_count = int(self.feature_count_value())
        sample_wavelengths = _sampled_wavelengths(list(window._wavelength_values), int(sample_count))
        middle_wavelength = sample_wavelengths[len(sample_wavelengths) // 2]
        window._enter_chromatic_setup_mode()
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._state.preprocessing.chromatic_sample_image_count = sample_count
        window._state.preprocessing.chromatic_feature_count = feature_count
        window._state.preprocessing.reference_mode = "manual"
        window._state.preprocessing.reference_spectral_cube_index = int(current_spectral_cube)
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
        window._set_current_spectral_cube_and_wavelength(int(current_spectral_cube), float(sample_wavelengths[0]))
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
                int(mark.spectral_cube_index),
                float(mark.wavelength_nm),
                float(mark.x_px),
                float(mark.y_px),
            )
            for mark in window._state.chromatic_landmarks
        ]
        if mode == "landmark_radial":
            marks_by_sample: dict[tuple[int, float], set[int]] = {sample_key: set() for sample_key in sample_keys}
            for landmark_id, spectral_cube_index, wavelength, _x, _y in landmarks_payload:
                sample_key = (int(spectral_cube_index), float(wavelength))
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
            (int(record.key.spectral_cube_index), float(record.key.wavelength_nm), str(record.path))
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
                spectral_cube_index=int(spectral_cube_index),
                wavelength_nm=float(wavelength),
                x_px=float(x_px),
                y_px=float(y_px),
            )
            for feature_id, spectral_cube_index, wavelength, x_px, y_px in observations
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
        window._update_roi_overlays()
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

    def clear_all_landmark_overlays(self) -> None:
        window = self.window
        for bundle in window._chromatic_all_landmark_overlay_items.values():
            window.image_plot.removeItem(bundle.points)
            if bundle.active_cross is not None:
                window.image_plot.removeItem(bundle.active_cross)
            window.image_plot.removeItem(bundle.label)
        window._chromatic_all_landmark_overlay_items.clear()

    def update_all_landmark_overlays(self) -> None:
        window = self.window
        if window._showing_background_profile_main:
            self.clear_all_landmark_overlays()
            return
        current_key = window._current_image_key
        if current_key is None or not window._state.chromatic_landmarks:
            self.clear_all_landmark_overlays()
            return
        linked_preview = bool(
            window._state.preprocessing.chromatic_correction_enabled
            and window._state.chromatic_models
        )

        grouped: dict[int, list[tuple[float, float, float, tuple[int, float]]]] = {}
        for mark in window._state.chromatic_landmarks:
            grouped.setdefault(int(mark.landmark_id), []).append(
                (float(mark.x_px), float(mark.y_px), float(mark.wavelength_nm), (int(mark.spectral_cube_index), float(mark.wavelength_nm)))
            )

        current_ids = set(grouped)
        for landmark_id in list(window._chromatic_all_landmark_overlay_items):
            if landmark_id not in current_ids:
                bundle = window._chromatic_all_landmark_overlay_items.pop(landmark_id)
                window.image_plot.removeItem(bundle.points)
                window.image_plot.removeItem(bundle.label)

        for landmark_id, items in grouped.items():
            bundle = window._chromatic_all_landmark_overlay_items.get(landmark_id)
            if bundle is None:
                points = pg.ScatterPlotItem(pxMode=True)
                points.setZValue(42)
                window.image_plot.addItem(points, ignoreBounds=True)
                active_cross = pg.PlotCurveItem()
                active_cross.setSkipFiniteCheck(True)
                active_cross.setZValue(44)
                window.image_plot.addItem(active_cross, ignoreBounds=True)
                label = pg.TextItem(anchor=(0.0, 1.0))
                label.setZValue(43)
                window.image_plot.addItem(label, ignoreBounds=True)
                bundle = ChromaticLandmarkAllOverlayBundle(points=points, active_cross=active_cross, label=label)
                window._chromatic_all_landmark_overlay_items[landmark_id] = bundle
            xs: list[float] = []
            ys: list[float] = []
            colors = [window._chromatic_wavelength_color(item[2]) for item in items]
            pen_colors = [colors[i] for i in range(len(colors))]
            brush_colors = [colors[i] for i in range(len(colors))]
            for item_x, item_y, _wavelength, source_key in items:
                display_point = (float(item_x), float(item_y))
                if linked_preview:
                    transformed = window._transform_chromatic_point_between_keys(display_point, source_key, current_key)
                    if transformed is not None:
                        display_point = transformed
                xs.append(float(display_point[0]))
                ys.append(float(display_point[1]))
            bundle.points.setData(
                x=xs,
                y=ys,
                size=10.5,
                symbol="+",
                pen=[pg.mkPen(color, width=1.3) for color in pen_colors],
                brush=None,
            )
            bundle.points.setVisible(True)
            current_item = next((item for item in items if item[3] == current_key), None)
            if current_item is not None:
                idx = items.index(current_item)
                cross_size = 8.0
                cross_xs = np.asarray(
                    [xs[idx] - cross_size, xs[idx] + cross_size, np.nan, xs[idx], xs[idx]],
                    dtype=np.float64,
                )
                cross_ys = np.asarray(
                    [ys[idx], ys[idx], np.nan, ys[idx] - cross_size, ys[idx] + cross_size],
                    dtype=np.float64,
                )
                bundle.active_cross.setData(cross_xs, cross_ys)
                bundle.active_cross.setPen(pg.mkPen("#f8fafc", width=3.4))
                bundle.active_cross.setVisible(True)
            elif bundle.active_cross is not None:
                bundle.active_cross.setVisible(False)

            representative_index = 0
            current_wavelength = float(current_key[1])
            for index, item in enumerate(items):
                if abs(float(item[2]) - current_wavelength) < 1e-6:
                    representative_index = index
                    break
            _rep_x, _rep_y, rep_wavelength, _ = items[representative_index]
            rep_display_x = xs[representative_index]
            rep_display_y = ys[representative_index]
            label_color = window._chromatic_wavelength_color(rep_wavelength)
            bundle.label.setHtml(
                "<span style="
                f"'color:{label_color.name()}; "
                "font-size:10pt; "
                "font-style:italic; "
                "font-weight:700; "
                f"background:{'#0f172a'}; "
                f"border:1px solid {label_color.name()}; "
                "border-radius:4px; "
                "padding:2px 5px;'"
                f">{landmark_id}</span>"
            )
            bundle.label.setPos(float(rep_display_x) + 8.0, float(rep_display_y) - 8.0)
            bundle.label.setVisible(True)

    def _update_chromatic_summary(self) -> None:
        if not self.window._state.dataset:
            self.window.chromatic_summary.setText("No dataset loaded.")
            self.window.chromatic_progress_label.setText("No dataset loaded.")
            self.window.chromatic_transform_button.setEnabled(False)
            self.window.chromatic_apply_check.setEnabled(False)
            self.window.chromatic_section.set_apply_enabled(False)
            self.window._set_section_applied(self.window.chromatic_section, bool(self.window._state.preprocessing.chromatic_correction_enabled))
            self.window.chromatic_transform_button.setPixmap(self.window._chromatic_transform_icon(False).pixmap(24, 24))
            self.window.chromatic_transform_button.setToolTip(
                "Estimate chromatic transforms."
            )
            return
        model_count = len(self.window._state.chromatic_models)
        sample_keys = self.window._chromatic_sample_image_keys()
        feature_ids = self.window._expected_chromatic_feature_ids()
        filled_samples = 0
        current_index = self.window._current_chromatic_sample_index()
        for sample_key in sample_keys:
            sample_marks = {
                int(mark.landmark_id)
                for mark in self.window._state.chromatic_landmarks
                if int(mark.spectral_cube_index) == int(sample_key[0]) and abs(float(mark.wavelength_nm) - float(sample_key[1])) < 1e-6
            }
            if all(feature_id in sample_marks for feature_id in feature_ids):
                filled_samples += 1
        can_estimate = bool(sample_keys) and filled_samples == len(sample_keys) and len(feature_ids) >= 2
        controls_locked = self.window._chromatic_auto_running
        can_apply_models = model_count > 0 and not controls_locked
        can_toggle_transform = (can_estimate or model_count > 0) and not controls_locked
        self.window.chromatic_transform_button.setEnabled(can_toggle_transform)
        self.window.chromatic_transform_button.setPixmap(self.window._chromatic_transform_icon(model_count > 0).pixmap(24, 24))
        if model_count > 0:
            self.window.chromatic_transform_button.setToolTip("Clear saved chromatic transforms.")
        elif can_estimate:
            self.window.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        else:
            self.window.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        self.window.chromatic_apply_check.setEnabled(can_apply_models)
        self.window.chromatic_section.set_apply_enabled(can_apply_models)
        self.window._set_section_applied(self.window.chromatic_section, bool(self.window._state.preprocessing.chromatic_correction_enabled))
        self.window.chromatic_summary.setText("Radial setup ready." if self.window._chromatic_setup_active else "Radial workflow ready.")
        if sample_keys and current_index is not None:
            current_key = sample_keys[current_index]
            marked_current = len(
                {
                    int(mark.landmark_id)
                    for mark in self.window._state.chromatic_landmarks
                    if int(mark.spectral_cube_index) == int(current_key[0]) and abs(float(mark.wavelength_nm) - float(current_key[1])) < 1e-6
                }
            )
            self.window.chromatic_progress_label.setText(
                f"Sample image {current_index + 1}/{len(sample_keys)} at {current_key[1]:g} nm | "
                f"reference points marked: {marked_current}/{len(feature_ids)} | "
                f"completed sample images: {filled_samples}/{len(sample_keys)}"
            )
        elif sample_keys:
            middle_index = len(sample_keys) // 2
            self.window.chromatic_progress_label.setText(
                f"Procedure ready: {len(sample_keys)} sampled wavelengths, middle reference at {sample_keys[middle_index][1]:g} nm."
            )
        else:
            self.window.chromatic_progress_label.setText("Edit the radial workflow to choose sampled wavelengths.")
        if model_count == 0:
            return
        rmses = [float(model.rmse_px) for model in self.window._state.chromatic_models if model.rmse_px > 0.0]
        rmse_text = f"{float(np.mean(rmses)):.2f} px" if rmses else "0.00 px"
        self.window.chromatic_summary.setText("Transforms estimated.")
        self.window.chromatic_progress_label.setText(
            f"Transforms ready for {model_count} image(s) | mean fit RMSE: {rmse_text} | "
            f"{'applied' if self.window._state.preprocessing.chromatic_correction_enabled else 'ready to apply'}"
        )


    def _on_chromatic_reference_points_all_toggled(self, checked: bool) -> None:
        self.window._chromatic_reference_points_all_visible = bool(checked)
        self.window._update_landmark_overlays()
        self.window._schedule_processing_state_save()


    def _on_chromatic_landmark_id_changed(self, value: int) -> None:
        feature_id = min(max(int(value), 1), len(self.window._expected_chromatic_feature_ids()))
        self.window._chromatic_landmark_marker_id = feature_id
        self.window._selected_landmark_id = feature_id
        self.window._select_chromatic_feature(feature_id, center_view=True)


    def _on_chromatic_landmark_tool_toggled(self, checked: bool) -> None:
        self.window.chromatic_start_button.setIcon(self.window._make_spot_edit_icon(bool(checked)))
        if checked:
            if not self.window._is_chromatic_sample_image_key(self.window._current_image_key):
                sample_keys = self.window._chromatic_sample_image_keys()
                if sample_keys:
                    self.window._set_current_spectral_cube_and_wavelength(int(sample_keys[0][0]), float(sample_keys[0][1]))
                    self.window._set_status_text("Chromatic edit activated. Navigating to the first sampled wavelength image.")
                    self.window._append_workflow_log(
                        "Chromatic edit activated | navigated to first sampled wavelength image",
                        level="info",
                    )
                else:
                    self.window._set_status_text("Start the radial workflow before editing chromatic reference points.")
                    self.window._append_workflow_log(
                        "Chromatic edit rejected | no sampled chromatic images available",
                        level="warning",
                    )
                    self.window.chromatic_start_button.blockSignals(True)
                    self.window.chromatic_start_button.setChecked(False)
                    self.window.chromatic_start_button.blockSignals(False)
                    self.window.chromatic_start_button.setIcon(self.window._make_spot_edit_icon(False))
                    return
            self.window.rotate_action.blockSignals(True)
            self.window.rotate_action.setChecked(False)
            self.window.rotate_action.blockSignals(False)
            self.window.crop_action.blockSignals(True)
            self.window.crop_action.setChecked(False)
            self.window.crop_action.blockSignals(False)
            self.window.spot_edit_action.blockSignals(True)
            self.window.spot_edit_action.setChecked(False)
            self.window.spot_edit_action.blockSignals(False)
            self.window.mask_pencil_check.blockSignals(True)
            self.window.mask_pencil_check.setChecked(False)
            self.window.mask_pencil_check.blockSignals(False)
            self.window._active_tool = "chromatic_landmark"
            self.window._selected_landmark_id = self.window._chromatic_landmark_marker_id
            if hasattr(self, "image_panel"):
                self.window.image_panel.raise_()
            if hasattr(self, "image_view") and self.window.image_view is not None:
                self.window.image_view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                viewport = self.window.image_view.viewport()
                if viewport is not None:
                    viewport.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.window._set_status_text(
                f"Chromatic reference point editor active. Click to place point {self.window._chromatic_landmark_marker_id}, "
                "drag to adjust, PageUp/PageDown to switch reference points."
            )
            self.window._append_workflow_log("Chromatic edit activated", level="info")
        elif self.window._active_tool == "chromatic_landmark":
            self.window._active_tool = None
            self.window._append_workflow_log("Chromatic edit deactivated", level="info")
        self.window._update_landmark_overlays()


    def _on_chromatic_sample_count_changed(self, value: int) -> None:
        sample_minimum = 1 if len(self.window._wavelength_values) <= 1 else 3
        normalized = self.window._normalized_odd_count(
            int(value),
            sample_minimum,
            min(max(len(self.window._wavelength_values), sample_minimum), 7),
        )
        if normalized != int(value):
            self.window.chromatic_sample_count_spin.blockSignals(True)
            self.window.chromatic_sample_count_spin.setValue(normalized)
            self.window.chromatic_sample_count_spin.blockSignals(False)
        self.window._state.preprocessing.chromatic_sample_image_count = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()


    def _on_chromatic_feature_count_changed(self, value: int) -> None:
        normalized = self.feature_count_value()
        if normalized != self.window._state.preprocessing.chromatic_feature_count:
            self.window._state.preprocessing.chromatic_feature_count = normalized
        max_feature = len(self.window._expected_chromatic_feature_ids())
        self.window._chromatic_landmark_marker_id = min(self.window._chromatic_landmark_marker_id, max_feature)
        self.window._selected_landmark_id = None if self.window._selected_landmark_id is None else min(self.window._selected_landmark_id, max_feature)
        self.window.chromatic_landmark_id_spin.blockSignals(True)
        self.window.chromatic_landmark_id_spin.setMaximum(max_feature)
        self.window.chromatic_landmark_id_spin.setValue(self.window._chromatic_landmark_marker_id)
        self.window.chromatic_landmark_id_spin.blockSignals(False)
        self._update_chromatic_summary()
        self.window._update_landmark_overlays()
        self.window._schedule_processing_state_save()


    def _on_chromatic_subpixel_precision_changed(self, _value: int) -> None:
        normalized = self.subpixel_precision_value()
        if normalized != int(getattr(self.window._state.preprocessing, "chromatic_subpixel_precision", 4)):
            self.window._state.preprocessing.chromatic_subpixel_precision = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()


    def _seed_chromatic_landmarks_for_current_image(self) -> None:
        if self.window._chromatic_auto_running:
            return
        image_key = self.window._current_image_key
        if not self.window._is_chromatic_sample_image_key(image_key):
            return
        assert image_key is not None
        expected_ids = self.window._expected_chromatic_feature_ids()
        existing_ids = {int(mark.landmark_id) for mark in self.window._current_image_landmarks()}
        missing_ids = [feature_id for feature_id in expected_ids if feature_id not in existing_ids]
        if not missing_ids:
            return
        sample_keys = self.window._chromatic_sample_image_keys()
        current_index = sample_keys.index(image_key)
        candidate_keys: list[tuple[int, float]] = []
        for index in range(current_index - 1, -1, -1):
            candidate_keys.append(sample_keys[index])
        reference_key = self.window._reference_image_key()
        if reference_key is not None and reference_key not in candidate_keys and reference_key != image_key:
            candidate_keys.append(reference_key)
        for index in range(current_index + 1, len(sample_keys)):
            candidate_keys.append(sample_keys[index])
        source_marks: dict[int, tuple[float, float]] | None = None
        for candidate_key in candidate_keys:
            marks = {
                int(mark.landmark_id): (float(mark.x_px), float(mark.y_px))
                for mark in self.window._state.chromatic_landmarks
                if int(mark.spectral_cube_index) == int(candidate_key[0])
                and abs(float(mark.wavelength_nm) - float(candidate_key[1])) < 1e-6
            }
            if any(feature_id in marks for feature_id in missing_ids):
                source_marks = marks
                break
        if source_marks is None and self.window._current_processed_image is not None:
            current_marks = self.window._current_image_landmarks()
            if not current_marks:
                source_marks = self.window._default_chromatic_feature_points(
                    self.window._current_processed_image.shape[:2],
                    len(expected_ids),
                )
        if source_marks is None:
            return
        changed = False
        for feature_id in missing_ids:
            point = source_marks.get(feature_id)
            if point is None:
                continue
            self.window._upsert_current_landmark(feature_id, point, clear_models=False)
            changed = True
        if changed:
            self.window._finalize_chromatic_landmark_edit()
            self.window._set_status_text(
                f"Seeded missing reference points for {image_key[1]:g} nm from the nearest marked sample image."
            )


    def _navigate_chromatic_sample(self, direction: int) -> bool:
        sample_keys = self.window._chromatic_sample_image_keys()
        if not sample_keys:
            return False
        current_index = self.window._current_chromatic_sample_index()
        if current_index is None:
            if self.window._current_image_key is None:
                current_index = 0
            else:
                current_wavelength = float(self.window._current_image_key[1])
                current_index = min(
                    range(len(sample_keys)),
                    key=lambda idx: abs(float(sample_keys[idx][1]) - current_wavelength),
                )
        target_index = min(max(current_index + int(direction), 0), len(sample_keys) - 1)
        target_spectral_cube, target_wavelength = sample_keys[target_index]
        self.window._set_current_spectral_cube_and_wavelength(target_spectral_cube, target_wavelength)
        return True
