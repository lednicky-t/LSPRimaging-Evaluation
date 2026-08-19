from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from scipy import ndimage

from lspr_imaging_app.domain.models import MaskSettings
from lspr_imaging_app.gui.analysis_tasks import _mask_candidate_task
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.io.dataset import dataset_load_plane, dataset_plane_shape, load_image_array, load_image_shape
from lspr_imaging_app.processing.preprocess import (
    apply_spatial_mask,
    create_figure_mask,
    rotation_fill_pixel_mask,
    spatial_coordinate_maps,
)


class MaskController:
    def __init__(self, window) -> None:
        self.window = window

    def preview_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...]:
        window = self.window
        target_key = image_key if image_key is not None else window._current_image_key
        return (
            bool(window._state.area_roi_settings.ignore_marked_pixels),
            int(window._mask_state_revision),
            window._external_mask_signature(target_key),
        )

    def normalize_application_state(self) -> None:
        window = self.window
        apply_mask = bool(
            window._state.area_roi_settings.ignore_marked_pixels or window._state.preprocessing.flatten_background_exclude_mask
        )
        window._state.area_roi_settings.ignore_marked_pixels = apply_mask
        window._state.preprocessing.flatten_background_exclude_mask = apply_mask

    def on_pencil_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            if not window._is_current_reference_image():
                window.mask_pencil_check.blockSignals(True)
                window.mask_pencil_check.setChecked(False)
                window.mask_pencil_check.blockSignals(False)
                window.status_label.setText("Mask drawing is available only on the reference image.")
                return
            window.rotate_action.blockSignals(True)
            window.rotate_action.setChecked(False)
            window.rotate_action.blockSignals(False)
            window.crop_action.blockSignals(True)
            window.crop_action.setChecked(False)
            window.crop_action.blockSignals(False)
            window.roi_edit_action.blockSignals(True)
            window.roi_edit_action.setChecked(False)
            window.roi_edit_action.blockSignals(False)
            window.chromatic_grid_button.blockSignals(True)
            window.chromatic_grid_button.setChecked(False)
            window.chromatic_grid_button.blockSignals(False)
            window._active_tool = "mask"
        elif window._active_tool == "mask":
            window._active_tool = None
        window._mask_drawing = False
        window._drag_anchor = None
        window._dragging_rois = False
        window._chromatic_controller.sync_grid_visibility()

    def sync_draw_mode_buttons(self) -> None:
        window = self.window
        mode = str(window.mask_draw_mode_combo.currentData() or "add")
        is_add = mode != "erase"
        for button, checked in (
            (window.mask_draw_add_button, is_add),
            (window.mask_draw_remove_button, not is_add),
        ):
            button.blockSignals(True)
            button.setChecked(bool(checked))
            button.blockSignals(False)

    def set_draw_mode(self, mode: str) -> None:
        window = self.window
        mode_key = "erase" if str(mode).strip().lower() == "erase" else "add"
        index = window.mask_draw_mode_combo.findData(mode_key)
        if index < 0:
            index = 0
        if window.mask_draw_mode_combo.currentIndex() != index:
            window.mask_draw_mode_combo.setCurrentIndex(index)
        self.sync_draw_mode_buttons()
        window._save_control_preferences()

    def on_section_applied_changed(self, applied: bool) -> None:
        window = self.window
        if window.ignore_marked_check.isChecked() != bool(applied):
            window.ignore_marked_check.setChecked(bool(applied))

    def on_background_section_applied_changed(self, applied: bool) -> None:
        window = self.window
        applied = bool(applied)
        window._append_workflow_log(f"Background link | {applied}", level="debug")
        if window.background_removal_link.isChecked() != applied:
            window.background_removal_link.blockSignals(True)
            window.background_removal_link.setChecked(applied)
            window.background_removal_link.blockSignals(False)
        if applied and window._showing_background_profile_main:
            window._on_background_profile_toggled(False)
        window._update_image_processing_settings()

    def load_mask_from_file(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to load a mask file.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to load the reference mask.")
            return
        default_path = window._current_mask_file_path()
        start_path = str(default_path if default_path is not None else Path(window.folder_edit.text()))
        source, _ = QFileDialog.getOpenFileName(
            window,
            "Load mask image",
            start_path,
            "Mask images (*.png *.bmp *.tif *.tiff);;All files (*)",
        )
        if not source:
            return
        source_path = Path(source)
        try:
            target_record_path = window._reference_record().path if window._reference_record() is not None else window._current_record_path
            target_key = window._image_key_for_record_path(target_record_path) if target_record_path is not None else None
            if target_key is not None and window._state.dataset is not None:
                target_shape = dataset_plane_shape(window._state.dataset, int(target_key[0]), float(target_key[1]))
            else:
                target_shape = load_image_shape(str(target_record_path))
            mask = window._read_mask_image(source_path, target_shape)
        except Exception as exc:
            QMessageBox.critical(window, "Load mask failed", str(exc))
            window._set_status_text(f"Load mask failed: {exc}")
            return
        self.clear_preview_overlays(clear_toggles=True)
        window._set_current_file_mask(mask, window._current_mask_file_path(), refresh_preview=True)
        window._save_processing_state_for_dataset(force=True, reason="mask load")
        window._append_workflow_log(
            f"Mask file loaded | source={source_path.name} | shape={mask.shape}",
            level="debug",
        )
        window._set_status_text(f"Loaded mask from {source_path.name}.{window._mask_change_status_suffix()}")

    def save_mask_to_file(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to save a mask.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to save the reference mask.")
            return
        destination = window._current_mask_file_path()
        if destination is None:
            window._set_status_text("No current image is available for mask export.")
            return
        if window._current_file_mask is not None:
            mask = window._current_file_mask.astype(bool, copy=False)
        else:
            current_key = window._image_key_for_record_path(window._current_record_path)
            if current_key is not None and window._state.dataset is not None:
                raw_image = dataset_load_plane(window._state.dataset, int(current_key[0]), float(current_key[1])).astype(np.float32, copy=False)
            else:
                raw_image = load_image_array(str(window._current_record_path)).astype(np.float32, copy=False)
            export_settings = deepcopy(window._state.area_roi_settings)
            export_settings.ignore_marked_pixels = True
            from lspr_imaging_app.processing.roi_detection import ignored_pixel_mask
            mask = ignored_pixel_mask(raw_image, export_settings, external_mask=None)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L").save(destination)
        except Exception as exc:
            QMessageBox.critical(window, "Save mask failed", str(exc))
            window._set_status_text(f"Save mask failed: {exc}")
            return
        window._set_current_file_mask(mask, destination, refresh_preview=True)
        window._save_processing_state_for_dataset(force=True, reason="mask save")
        window._append_workflow_log(
            f"Mask file saved | destination={destination.name} | shape={mask.shape}",
            level="debug",
        )
        window._set_status_text(f"Saved mask to {destination.name}.{window._mask_change_status_suffix()}")

    def create_new_mask(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to create a new mask.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to edit the reference mask.")
            return
        window._ensure_mask_section_applied()
        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_shape = dataset_plane_shape(window._state.dataset, int(current_key[0]), float(current_key[1]))
        else:
            raw_shape = load_image_shape(str(window._current_record_path))
        new_mask = np.zeros(raw_shape, dtype=bool)
        self.clear_preview_overlays(clear_toggles=True)
        window._set_current_file_mask(new_mask, window._current_mask_file_path(), refresh_preview=True)
        window._save_processing_state_for_dataset(force=True, reason="mask create")
        window._append_workflow_log(f"Mask file created | shape={new_mask.shape}", level="debug")
        window._set_status_text("Started a new blank mask.")

    def apply_histogram_mask(self) -> None:
        self.apply_mask_delta("histogram", subtract=False)

    def reset_histogram_mask(self) -> None:
        self.apply_mask_delta("histogram", subtract=True)

    def apply_relative_mask(self) -> None:
        self.apply_mask_delta("relative", subtract=False)

    def reset_relative_mask(self) -> None:
        self.apply_mask_delta("relative", subtract=True)

    def apply_local_contrast_mask(self) -> None:
        self.apply_mask_delta("local_contrast", subtract=False)

    def reset_local_contrast_mask(self) -> None:
        self.apply_mask_delta("local_contrast", subtract=True)

    def apply_morphology_mask(self) -> None:
        self.apply_mask_delta("morphology", subtract=False)

    def reset_morphology_mask(self) -> None:
        self.apply_mask_delta("morphology", subtract=True)

    def preview_toggled(self, preview_kind: str, checked: bool) -> None:
        self.on_preview_toggled(preview_kind, checked)

    def set_morphology_operation(self, operation: str, checked: bool) -> None:
        self._set_morphology_operation(operation, checked)

    def refresh_previews(self, *_args) -> None:
        self.refresh_mask_previews()

    def clear_preview_overlays(self, *, clear_toggles: bool = False) -> None:
        window = self.window
        window._mask_histogram_preview = None
        window._mask_figure_preview = None
        window._state.mask.histogram_enabled = False
        window._state.mask.histogram_mask = None
        window._state.mask.figure_enabled = False
        window._state.mask.figure_mask = None
        if clear_toggles:
            window.relative_mask_show_button.blockSignals(True)
            window.local_contrast_mask_show_button.blockSignals(True)
            window.morphology_mask_show_button.blockSignals(True)
            window.relative_mask_show_button.setChecked(False)
            window.local_contrast_mask_show_button.setChecked(False)
            window.morphology_mask_show_button.setChecked(False)
            window.relative_mask_show_button.blockSignals(False)
            window.local_contrast_mask_show_button.blockSignals(False)
            window.morphology_mask_show_button.blockSignals(False)
            window._set_mask_preview_button_icon(window.relative_mask_show_button, False)
            window._set_mask_preview_button_icon(window.local_contrast_mask_show_button, False)
            window._set_mask_preview_button_icon(window.morphology_mask_show_button, False)

    def on_preview_toggled(self, preview_kind: str, checked: bool) -> None:
        window = self.window
        if preview_kind == "relative":
            window._set_mask_preview_button_icon(window.relative_mask_show_button, checked)
        elif preview_kind == "local_contrast":
            window._set_mask_preview_button_icon(window.local_contrast_mask_show_button, checked)
        elif preview_kind == "morphology":
            window._set_mask_preview_button_icon(window.morphology_mask_show_button, checked)
        window._save_control_preferences()
        window._refresh_mask_previews()

    def _set_morphology_operation(self, operation: str, checked: bool) -> None:
        window = self.window
        selected_operation = str(operation)
        mapping = {
            "erode": window.mask_morphology_erode_button,
            "dilate": window.mask_morphology_dilate_button,
            "open": window.mask_morphology_open_button,
            "close": window.mask_morphology_close_button,
        }
        if checked:
            window._mask_morphology_operation = selected_operation
            for key, button in mapping.items():
                button.blockSignals(True)
                button.setChecked(key == selected_operation)
                button.blockSignals(False)
        elif str(window._mask_morphology_operation or "") == selected_operation:
            window._mask_morphology_operation = None
        window._save_control_preferences()
        window._refresh_mask_previews()

    def current_histogram_highlight_mask_raw(self) -> np.ndarray | None:
        window = self.window
        if window._current_processed_image is None or window._current_record_path is None:
            return None
        lower, upper = window.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        processed_selection = (
            np.isfinite(window._current_processed_image)
            & (window._current_processed_image >= float(lower))
            & (window._current_processed_image <= float(upper))
        )
        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_shape = dataset_plane_shape(window._state.dataset, int(current_key[0]), float(current_key[1]))
        else:
            raw_shape = load_image_shape(str(window._current_record_path))
        raw_mask = np.zeros(raw_shape, dtype=bool)
        coord_maps = window._processed_to_raw_maps()
        if coord_maps is None:
            return raw_mask
        x_map, y_map = coord_maps
        raw_x = np.rint(x_map[processed_selection]).astype(np.int32, copy=False)
        raw_y = np.rint(y_map[processed_selection]).astype(np.int32, copy=False)
        valid = (raw_x >= 0) & (raw_x < raw_shape[1]) & (raw_y >= 0) & (raw_y < raw_shape[0])
        raw_mask[raw_y[valid], raw_x[valid]] = True
        return raw_mask

    def mask_settings_from_controls(self) -> MaskSettings:
        window = self.window
        return MaskSettings(
            relative_threshold_fraction=float(window.mask_relative_threshold_spin.value()) / 100.0,
            relative_profile_sigma_px=float(window.mask_relative_profile_sigma_spin.value()),
            local_contrast_sigma_px=float(window.mask_local_contrast_sigma_spin.value()),
            local_contrast_z_threshold=float(window.mask_local_contrast_z_spin.value()),
        )

    def candidate_mask_for_tool(self, tool_key: str) -> np.ndarray | None:
        # Synchronous, authoritative computation for a mask candidate. Cheap for
        # "histogram"/"morphology" (no disk I/O - the histogram tool reads pixels already
        # in memory, morphology works off the in-memory mask canvas). "relative" and
        # "local_contrast" reload the raw image and run scipy filtering over it, which is
        # slow enough to freeze the GUI thread - callers that can tolerate an async answer
        # should use `request_mask_candidate` instead, which backgrounds exactly those two.
        window = self.window
        if window._current_record_path is None:
            return None
        if tool_key == "histogram":
            return self.current_histogram_highlight_mask_raw()
        if tool_key == "morphology":
            if not window._mask_morphology_operation:
                return None
            base = window._current_mask_canvas()
            if base is None:
                return None
            current_mask, _current_path = base
            if current_mask.size == 0:
                return None
            structure = window._mask_structure(int(window.mask_morphology_radius_spin.value()))
            operation = str(window._mask_morphology_operation or "")
            if operation == "erode":
                return ndimage.binary_erosion(current_mask, structure=structure)
            if operation == "open":
                return ndimage.binary_opening(current_mask, structure=structure)
            if operation == "close":
                return ndimage.binary_closing(current_mask, structure=structure)
            if operation == "dilate":
                return ndimage.binary_dilation(current_mask, structure=structure)
            return None

        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_image = dataset_load_plane(window._state.dataset, int(current_key[0]), float(current_key[1])).astype(np.float32, copy=False)
        else:
            raw_image = load_image_array(str(window._current_record_path)).astype(np.float32, copy=False)
        mask_settings = self.mask_settings_from_controls()
        if tool_key == "relative":
            return create_figure_mask(raw_image, mask_settings, "relative")
        if tool_key == "local_contrast":
            return create_figure_mask(raw_image, mask_settings, "local_contrast")
        return None

    def mask_candidate_cache_key(self, tool_key: str) -> tuple[object, ...] | None:
        window = self.window
        if window._current_record_path is None:
            return None
        return (
            str(window._current_record_path),
            tool_key,
            float(window.mask_relative_threshold_spin.value()),
            float(window.mask_relative_profile_sigma_spin.value()),
            float(window.mask_local_contrast_sigma_spin.value()),
            float(window.mask_local_contrast_z_spin.value()),
        )

    def request_mask_candidate(self, tool_key: str, on_ready) -> None:
        """Resolve a mask candidate for the current image, off the GUI thread when it's
        "relative" or "local_contrast" (the two that reload the raw image and run scipy
        filtering - see `candidate_mask_for_tool`). `on_ready(candidate)` is called with
        the result: synchronously, in this call, for the cheap tool kinds and for a cache
        hit; otherwise once the background computation finishes and the request is still
        for the currently-displayed image (a stale result - the user moved to a different
        image before it finished - is silently dropped)."""
        window = self.window
        if tool_key not in ("relative", "local_contrast"):
            on_ready(self.candidate_mask_for_tool(tool_key))
            return
        cache_key = self.mask_candidate_cache_key(tool_key)
        if cache_key is None:
            on_ready(None)
            return
        cached = window._mask_candidate_cache.get(cache_key)
        if cached is not None:
            window._mask_candidate_cache.move_to_end(cache_key)
            on_ready(cached)
            return
        window._mask_candidate_pending.setdefault(cache_key, []).append(on_ready)
        if cache_key in window._mask_candidate_in_flight:
            return
        window._mask_candidate_in_flight.add(cache_key)
        worker = FunctionWorker(
            _mask_candidate_task,
            str(window._current_record_path),
            self.mask_settings_from_controls(),
            tool_key,
        )
        worker.signals.result.connect(
            lambda candidate, cache_key=cache_key: self._on_mask_candidate_computed(cache_key, candidate)
        )
        worker.signals.error.connect(
            lambda message, cache_key=cache_key: self._on_mask_candidate_failed(cache_key, message)
        )
        window._thread_pool.start(worker)

    def _on_mask_candidate_computed(self, cache_key: tuple[object, ...], candidate: np.ndarray) -> None:
        window = self.window
        window._mask_candidate_in_flight.discard(cache_key)
        window._mask_candidate_cache[cache_key] = candidate
        window._mask_candidate_cache.move_to_end(cache_key)
        while len(window._mask_candidate_cache) > window.MASK_CANDIDATE_CACHE_SIZE:
            window._mask_candidate_cache.popitem(last=False)
        for callback in window._mask_candidate_pending.pop(cache_key, []):
            callback(candidate)

    def _on_mask_candidate_failed(self, cache_key: tuple[object, ...], message: str) -> None:
        window = self.window
        window._mask_candidate_in_flight.discard(cache_key)
        window._append_workflow_log(f"Mask candidate computation failed | {message}", level="error")
        for callback in window._mask_candidate_pending.pop(cache_key, []):
            callback(None)

    _MASK_TOOL_BUTTON_ATTRS = {
        "histogram": ("histogram_mask_apply_button", "histogram_mask_reset_button"),
        "relative": ("relative_mask_apply_button", "relative_mask_reset_button"),
        "local_contrast": ("local_contrast_mask_apply_button", "local_contrast_mask_reset_button"),
        "morphology": ("morphology_mask_apply_button", "morphology_mask_reset_button"),
    }

    def _set_mask_tool_buttons_busy(self, tool_key: str, busy: bool) -> None:
        window = self.window
        if busy:
            for attr in self._MASK_TOOL_BUTTON_ATTRS.get(tool_key, ()):
                button = getattr(window, attr, None)
                if button is not None:
                    button.setEnabled(False)
        else:
            # Re-enabling isn't just "not busy" - other conditions (e.g. viewing a
            # non-reference image under chromatic correction) may still disable these
            # buttons, so let the existing state computation decide.
            window._update_mask_file_button_state()

    def apply_mask_delta(self, tool_key: str, *, subtract: bool) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first.")
            return
        window._ensure_mask_section_applied()
        record_path = window._current_record_path
        self._set_mask_tool_buttons_busy(tool_key, True)
        if tool_key in ("relative", "local_contrast"):
            window._set_status_text(f"Computing {tool_key.replace('_', ' ')} mask...")
        self.request_mask_candidate(
            tool_key,
            lambda candidate, tool_key=tool_key, subtract=subtract, record_path=record_path: self._finish_apply_mask_delta(
                tool_key, subtract, record_path, candidate
            ),
        )

    def _finish_apply_mask_delta(
        self, tool_key: str, subtract: bool, record_path: Path, candidate: np.ndarray | None
    ) -> None:
        window = self.window
        self._set_mask_tool_buttons_busy(tool_key, False)
        if window._current_record_path != record_path:
            window._set_status_text(
                f"{tool_key.replace('_', ' ').title()} mask apply cancelled - the image changed before it finished."
            )
            return
        if candidate is None:
            window._set_status_text("No mask candidate is available yet.")
            return
        base = window._current_mask_canvas()
        if base is None:
            current_mask = np.zeros(candidate.shape, dtype=bool)
            current_path = window._current_mask_file_path()
        else:
            current_mask, current_path = base
            if current_mask.shape != candidate.shape:
                current_mask = np.zeros(candidate.shape, dtype=bool)
        updated_mask = np.logical_and(current_mask, ~candidate) if subtract else np.logical_or(current_mask, candidate)
        window._set_current_file_mask(updated_mask, current_path, refresh_preview=True)
        window._update_mask_file_button_state()
        window._refresh_mask_previews()
        window._save_processing_state_for_dataset(force=True, reason=f"mask {tool_key} {'subtract' if subtract else 'add'}")
        action = "Removed" if subtract else "Added"
        window._append_workflow_log(
            f"Mask tool {tool_key} | action={action.lower()} | changed={int(np.count_nonzero(candidate))} | shape={updated_mask.shape}",
            level="debug",
        )
        window._set_status_text(f"{action} {tool_key.replace('_', ' ')} mask pixels.")
        if window._mask_changes_affect_preprocessing():
            window._invalidate_image_analysis_caches()
            window._schedule_processing_state_save()
            window._schedule_image_refresh()
        window._set_status_text(window._mask_change_status_suffix())

    def refresh_mask_previews(self, *_args) -> None:
        window = self.window
        if not window._current_mask_canvas():
            window._mask_histogram_preview = None
            window._mask_figure_preview = None
            window._update_ignore_mask_overlay()
            return
        record_path = window._current_record_path
        if window.relative_mask_show_button.isChecked():
            self.request_mask_candidate(
                "relative",
                lambda candidate, record_path=record_path: self._on_mask_preview_ready("relative", record_path, candidate),
            )
        else:
            window._mask_histogram_preview = None
        if window.local_contrast_mask_show_button.isChecked():
            self.request_mask_candidate(
                "local_contrast",
                lambda candidate, record_path=record_path: self._on_mask_preview_ready(
                    "local_contrast", record_path, candidate
                ),
            )
        elif window.morphology_mask_show_button.isChecked():
            self.request_mask_candidate(
                "morphology",
                lambda candidate, record_path=record_path: self._on_mask_preview_ready(
                    "morphology", record_path, candidate
                ),
            )
        else:
            window._mask_figure_preview = None
        window._update_ignore_mask_overlay()

    def _on_mask_preview_ready(self, tool_key: str, record_path: Path, candidate: np.ndarray | None) -> None:
        window = self.window
        if window._current_record_path != record_path:
            return  # The user moved to a different image before this finished.
        if tool_key == "relative":
            if not window.relative_mask_show_button.isChecked():
                return
            window._mask_histogram_preview = candidate
        else:
            if tool_key == "local_contrast" and not window.local_contrast_mask_show_button.isChecked():
                return
            if tool_key == "morphology" and not window.morphology_mask_show_button.isChecked():
                return
            window._mask_figure_preview = candidate
        window._update_ignore_mask_overlay()

    def mask_section_applied(self) -> bool:
        window = self.window
        return bool(window._state.area_roi_settings.ignore_marked_pixels)

    def mask_changes_affect_preprocessing(self) -> bool:
        window = self.window
        return bool(
            window._state.preprocessing.flatten_background_enabled
            and window._state.preprocessing.flatten_background_exclude_mask
        )

    def mask_change_status_suffix(self) -> str:
        if self.mask_changes_affect_preprocessing():
            return " Background removal will use it on the next image refresh."
        return ""

    def session_mask_payload(self) -> dict | None:
        window = self.window
        if window._current_file_mask is None or window._current_record_path is None:
            return None
        source_record = window._reference_record_for_record_path(window._current_record_path)
        mask_record_path = source_record.path if source_record is not None else window._current_record_path
        return {
            "record_path": str(mask_record_path),
            "mask": window._current_file_mask.copy(),
        }

    def manual_mask_required(self, *, create_if_missing: bool) -> np.ndarray | None:
        window = self.window
        if window._current_processed_image is None:
            window._set_status_text("Load an image first to edit the mask.")
            return None
        if not window.ignore_marked_check.isChecked():
            window.ignore_marked_check.setChecked(True)
        if window._current_file_mask is None and create_if_missing:
            default_path = self.current_mask_file_path()
            raw_shape = (
                load_image_shape(str(window._current_record_path))
                if window._current_record_path is not None
                else window._current_processed_image.shape[:2]
            )
            blank_mask = np.zeros(raw_shape, dtype=bool)
            self.set_current_file_mask(blank_mask, default_path, refresh_preview=False)
        if window._current_file_mask is None:
            window._set_status_text("Load a file mask or start drawing with Pencil first.")
            return None
        return window._current_file_mask

    def mask_structure(self, radius_px: int) -> np.ndarray:
        radius = max(int(radius_px), 1)
        yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
        return (xx * xx + yy * yy) <= radius * radius

    def apply_mask_brush(self, point: tuple[float, float]) -> None:
        window = self.window
        mask = self.manual_mask_required(create_if_missing=True)
        if mask is None or window._current_processed_image is None:
            return
        brush_radius = max(float(window.mask_brush_size_spin.value()) / 2.0, 0.5)
        center_x = float(point[0])
        center_y = float(point[1])
        height, width = mask.shape
        x_min = max(int(np.floor(center_x - brush_radius)), 0)
        x_max = min(int(np.ceil(center_x + brush_radius)) + 1, width)
        y_min = max(int(np.floor(center_y - brush_radius)), 0)
        y_max = min(int(np.ceil(center_y + brush_radius)) + 1, height)
        if x_min >= x_max or y_min >= y_max:
            return
        yy, xx = np.ogrid[y_min:y_max, x_min:x_max]
        brush_mask = (xx - center_x) ** 2 + (yy - center_y) ** 2 <= brush_radius**2
        coord_maps = self.processed_to_raw_maps()
        if coord_maps is None:
            return
        x_map, y_map = coord_maps
        raw_x = np.rint(x_map[y_min:y_max, x_min:x_max][brush_mask]).astype(np.int32, copy=False)
        raw_y = np.rint(y_map[y_min:y_max, x_min:x_max][brush_mask]).astype(np.int32, copy=False)
        valid = (
            (raw_x >= 0)
            & (raw_y >= 0)
            & (raw_x < mask.shape[1])
            & (raw_y < mask.shape[0])
        )
        if not np.any(valid):
            return
        draw_mode = str(window.mask_draw_mode_combo.currentData() or "add")
        if draw_mode == "erase":
            mask[raw_y[valid], raw_x[valid]] = False
        else:
            mask[raw_y[valid], raw_x[valid]] = True
        window._append_workflow_log_throttled(
            "mask_brush",
            f"Mask brush | mode={draw_mode} | points={int(np.count_nonzero(valid))} | point=({center_x:.1f},{center_y:.1f})",
            level="debug",
            min_interval=0.5,
        )
        window._invalidate_image_analysis_caches()
        window._update_ignore_mask_overlay()

    def finalize_mask_edit(self) -> None:
        window = self.window
        if window._current_file_mask is None:
            return
        window._external_mask_revision += 1
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_ignore_mask_overlay()
        window._schedule_histogram_refresh()
        if window._mask_section_applied():
            window._current_image_key = None
            window._schedule_image_refresh()
        window._commit_prepared_undo_snapshot()
        window._append_workflow_log(
            f"Mask finalize | record={window._current_record_path} | shape={window._current_file_mask.shape}",
            level="debug",
        )
        window._save_processing_state_for_dataset()
        window._set_status_text(f"Mask updated. Save if you want to keep it on disk.{self.mask_change_status_suffix()}")

    def _resolve_sidecar_path(self, record_path: Path, suffix: str) -> Path:
        """Resolve a per-record sidecar file (mask/background) for `record_path`.

        New sidecars are written under `analysis/masks/` instead of next to
        the source TIFF. If an older dataset already has the file sitting
        next to the image (the pre-`analysis/` layout), that existing file
        is preferred so it keeps loading in place - it is never silently
        moved or duplicated, only found where it already is.
        """
        dataset_folder = self.window._dataset_folder_path()
        stem = record_path.stem
        if dataset_folder is not None:
            new_path = dataset_folder / "analysis" / "masks" / f"{stem}{suffix}"
            if new_path.exists():
                return new_path
            legacy_path = record_path.with_name(f"{stem}{suffix}")
            if legacy_path.exists():
                return legacy_path
            return new_path
        return record_path.with_name(f"{stem}{suffix}")

    def current_mask_file_path(self) -> Path | None:
        window = self.window
        if window._current_record_path is None:
            return None
        reference_record = window._reference_record_for_record_path(window._current_record_path)
        record_path = reference_record.path if reference_record is not None else window._current_record_path
        return self.mask_file_path_for_record(record_path)

    def mask_file_path_for_record(self, record_path: Path) -> Path:
        return self._resolve_sidecar_path(record_path, "_mask.png")

    def current_background_file_path(self) -> Path | None:
        window = self.window
        if window._current_record_path is None:
            return None
        reference_record = window._reference_record_for_record_path(window._current_record_path)
        record_path = reference_record.path if reference_record is not None else window._current_record_path
        return self._resolve_sidecar_path(record_path, "_background.png")

    def current_external_mask(self) -> np.ndarray | None:
        window = self.window
        if window._current_record_path is None:
            return None
        processed_mask, _processed = self.effective_external_mask_for_record(window._current_record_path, processed_space=True)
        return processed_mask

    def effective_external_mask_for_record(
        self,
        record_path: Path,
        *,
        processed_space: bool = False,
    ) -> tuple[np.ndarray | None, bool]:
        window = self.window
        if not window._mask_section_applied():
            return None, False
        return self.external_mask_for_record(record_path, processed_space=processed_space)

    def external_mask_for_record(self, record_path: Path, *, processed_space: bool = False) -> tuple[np.ndarray | None, bool]:
        window = self.window
        source_record_path = record_path
        reference_record = window._reference_record_for_record_path(record_path)
        if reference_record is not None:
            source_record_path = reference_record.path
        expected_mask_path = self.mask_file_path_for_record(source_record_path)
        if window._current_file_mask is not None and (
            window._current_file_mask_path == expected_mask_path
            or (
                window._current_file_mask_path is None
                and self.current_mask_file_path() == expected_mask_path
            )
        ):
            source_mask = window._current_file_mask
        else:
            if not expected_mask_path.exists():
                return None, False
            try:
                source_mask = self.read_mask_image(expected_mask_path, load_image_shape(str(source_record_path)))
            except Exception:
                return None, False

        if not processed_space:
            return source_mask, False

        if source_mask is None:
            return None, False

        # Keep the image pixels untouched here. Chromatic correction stays in the
        # analysis geometry path; masks remain in the spatially preprocessed space.
        return apply_spatial_mask(source_mask, window._state.preprocessing), True

    def external_mask_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...] | None:
        window = self.window
        if window._current_file_mask is None and window._current_record_path is None:
            return None
        target_key = image_key if image_key is not None else window._current_image_key
        return (
            window._external_mask_revision,
            str(window._current_file_mask_path) if window._current_file_mask_path is not None else None,
            None if window._current_file_mask is None else window._current_file_mask.shape,
            window._raw_preprocessing_signature(),
            window._chromatic_signature_for_image_key(target_key),
        )

    def processed_to_raw_maps(self) -> tuple[np.ndarray, np.ndarray] | None:
        window = self.window
        if window._current_record_path is None or window._current_processed_image is None:
            return None
        # Shape only - avoid decoding the full raw image just to check the cache below,
        # which used to happen on every call (including every mouse-move while painting
        # a mask, since this cache-signature check ran before the cache-hit check).
        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_shape = dataset_plane_shape(window._state.dataset, int(current_key[0]), float(current_key[1]))
        else:
            raw_shape = load_image_shape(str(window._current_record_path))
        signature = (
            str(window._current_record_path),
            raw_shape,
            window._raw_preprocessing_signature(),
        )
        if (
            window._processed_to_raw_map_signature == signature
            and window._processed_to_raw_x_map is not None
            and window._processed_to_raw_y_map is not None
        ):
            return window._processed_to_raw_x_map, window._processed_to_raw_y_map
        x_map, y_map = spatial_coordinate_maps(raw_shape, window._state.preprocessing)
        window._processed_to_raw_map_signature = signature
        window._processed_to_raw_x_map = x_map
        window._processed_to_raw_y_map = y_map
        return x_map, y_map

    def rotation_fill_mask(self) -> np.ndarray | None:
        """Processed-space mask of pixels the rotate/flip/crop tool
        synthesized (see processing.preprocess.rotation_fill_pixel_mask) for
        the currently displayed record - None when no rotation is active.
        Mirrors processed_to_raw_maps' caching (same raw-shape lookup, same
        signature shape) since both derive from the same raw-shape + current
        PreprocessingSettings inputs.
        """
        window = self.window
        if window._current_record_path is None or window._current_processed_image is None:
            return None
        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_shape = dataset_plane_shape(window._state.dataset, int(current_key[0]), float(current_key[1]))
        else:
            raw_shape = load_image_shape(str(window._current_record_path))
        signature = (
            str(window._current_record_path),
            raw_shape,
            window._raw_preprocessing_signature(),
        )
        if window._rotation_fill_mask_signature == signature:
            return window._rotation_fill_mask_value
        mask = rotation_fill_pixel_mask(raw_shape, window._state.preprocessing)
        window._rotation_fill_mask_signature = signature
        window._rotation_fill_mask_value = mask
        return mask

    def set_current_file_mask(
        self,
        mask: np.ndarray | None,
        path: Path | None,
        *,
        refresh_preview: bool,
    ) -> bool:
        window = self.window
        normalized = None if mask is None else np.asarray(mask, dtype=bool)
        normalized_path = path
        if normalized is not None and normalized_path is None:
            normalized_path = self.current_mask_file_path()
        same_path = window._current_file_mask_path == normalized_path
        same_mask = (
            (window._current_file_mask is None and normalized is None)
            or (
                window._current_file_mask is not None
                and normalized is not None
                and window._current_file_mask.shape == normalized.shape
                and np.array_equal(window._current_file_mask, normalized)
            )
        )
        if same_path and same_mask:
            window._append_workflow_log_throttled(
                "current_file_mask_unchanged",
                f"Current file mask unchanged | path={normalized_path} | shape={None if normalized is None else normalized.shape}",
                level="debug",
                min_interval=1.0,
            )
            return False

        window._current_file_mask = None if normalized is None else normalized.copy()
        window._current_file_mask_path = normalized_path
        window._current_file_mask_session_source_path = None
        window._external_mask_revision += 1
        window._append_workflow_log_throttled(
            "current_file_mask_set",
            f"Current file mask set | path={normalized_path} | shape={None if normalized is None else normalized.shape} | rev={window._external_mask_revision}",
            level="debug",
            min_interval=0.5,
        )
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        self.update_mask_file_button_state()

        if refresh_preview and window._current_processed_image is not None:
            window._update_ignore_mask_overlay()
            window._schedule_histogram_refresh()
            if window._mask_section_applied():
                window._current_image_key = None
                window._schedule_image_refresh()
        return True

    def read_mask_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        with Image.open(path) as image:
            mask = np.array(image.convert("L"), dtype=np.uint8)
        if mask.shape != expected_shape:
            raise ValueError(
                f"Mask size {mask.shape[1]} x {mask.shape[0]} px does not match the current image "
                f"{expected_shape[1]} x {expected_shape[0]} px."
            )
        return mask >= 128

    def auto_load_mask_for_current_record(self) -> None:
        window = self.window
        if window._current_record_path is None:
            self.set_current_file_mask(None, None, refresh_preview=False)
            return
        source_record = window._reference_record_for_record_path(window._current_record_path)
        mask_record_path = source_record.path if source_record is not None else window._current_record_path
        default_path = self.mask_file_path_for_record(mask_record_path)
        expected_shape = load_image_shape(str(mask_record_path))
        if window._current_file_mask is not None and window._current_file_mask_session_source_path is not None:
            if window._current_file_mask.shape == expected_shape:
                if window._current_file_mask_session_source_path == mask_record_path:
                    return
                if source_record is not None and window._current_file_mask_session_source_path == source_record.path:
                    return
            self.set_current_file_mask(None, None, refresh_preview=False)
            window._current_file_mask_session_source_path = None
            if default_path is None or not default_path.exists():
                return
        if (
            window._current_file_mask is not None
            and window._current_file_mask.shape == expected_shape
            and (
                window._current_file_mask_path == default_path
                or (
                    window._current_file_mask_path is None
                    and self.current_mask_file_path() == default_path
                )
            )
        ):
            # Keep unsaved in-memory edits for the current image/reference slot.
            return
        if default_path is None or not default_path.exists():
            self.set_current_file_mask(None, None, refresh_preview=False)
            return
        try:
            mask = self.read_mask_image(default_path, expected_shape)
        except Exception as exc:
            self.set_current_file_mask(None, None, refresh_preview=False)
            window._set_status_text(f"Skipped {default_path.name}: {exc}")
            return
        self.set_current_file_mask(mask, default_path, refresh_preview=False)

    def set_mask_preview_button_icon(self, button, shown: bool) -> None:
        window = self.window
        icon_name = "eye" if shown else "eye-closed"
        tooltip = "Hide preview." if shown else "Show preview."
        button.setIcon(window._mask_panel_icon(icon_name, color="#38bdf8", size=20))
        button.setToolTip(tooltip)

    def current_mask_canvas(self) -> tuple[np.ndarray, Path | None] | None:
        window = self.window
        if window._current_record_path is None:
            return None
        raw_shape = load_image_shape(str(window._current_record_path))
        if window._current_file_mask is not None and window._current_file_mask.shape == raw_shape:
            return window._current_file_mask.copy(), window._current_file_mask_path
        return np.zeros(raw_shape, dtype=bool), window._current_file_mask_path

    def ensure_mask_section_applied(self) -> None:
        window = self.window
        if not window._mask_section_applied():
            window.mask_section.set_applied(True)

    def refresh_after_mask_change(self, status: str) -> None:
        window = self.window
        window._mask_state_revision += 1
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_ignore_mask_overlay()
        window._schedule_histogram_refresh()
        if window._mask_section_applied():
            window._current_image_key = None
            window._schedule_image_refresh()
        window._set_status_text(status)

    def create_new_background(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to create a background image.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to create the reference background.")
            return
        if window._current_processed_image is None:
            window._set_status_text("Load an image first to create a background image.")
            return
        window._set_status_text("Computing background image...")
        window._request_background_profile_image(self._on_create_new_background_ready)

    def _on_create_new_background_ready(self, background: np.ndarray | None) -> None:
        window = self.window
        if background is None:
            window._set_status_text("Background image is not available yet.")
            return
        if window._showing_background_profile_main:
            # Re-derives from the (possibly by-now-updated) current view rather than
            # assuming `background` is still what should be displayed - see
            # `request_background_profile_image`'s docstring on why on_ready can fire
            # with a result for a view the user has since navigated away from.
            window._update_background_profile_preview()
        window._set_status_text("Created a new background image from the current parameters.")

    def load_background_from_file(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to load a background image.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to load the reference background.")
            return
        default_path = self.current_background_file_path()
        start_path = str(default_path if default_path is not None else Path(window.folder_edit.text()))
        source, _ = QFileDialog.getOpenFileName(
            window,
            "Load background image",
            start_path,
            "Background images (*.png *.bmp *.tif *.tiff);;All files (*)",
        )
        if not source:
            return
        source_path = Path(source)
        try:
            target_record_path = window._reference_record().path if window._reference_record() is not None else window._current_record_path
            expected_shape = window._current_processed_image.shape[:2] if window._current_processed_image is not None else load_image_shape(str(target_record_path))
            background = self.read_background_image(source_path, expected_shape)
        except Exception as exc:
            QMessageBox.critical(window, "Load background failed", str(exc))
            window._set_status_text(f"Load background failed: {exc}")
            return
        window._background_profile_cache_signature = window._background_profile_signature()
        window._background_profile_cache_image = background
        if window._showing_background_profile_main:
            window._apply_main_image_content()
        window._set_status_text(f"Loaded background from {source_path.name}.")

    def save_background_to_file(self) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first to save a background image.")
            return
        if window._state.preprocessing.chromatic_correction_enabled and not window._is_current_reference_image():
            window._set_status_text("Switch to the reference image to save the reference background.")
            return
        destination = self.current_background_file_path()
        if destination is None:
            window._set_status_text("No current image is available for background export.")
            return
        window._set_status_text("Computing background image...")
        window._request_background_profile_image(
            lambda background, destination=destination: self._on_save_background_ready(destination, background)
        )

    def _on_save_background_ready(self, destination: Path, background: np.ndarray | None) -> None:
        window = self.window
        if background is None:
            window._set_status_text("Background image is not available yet.")
            return
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            background_u16 = np.clip(np.rint(background), 0, 65535).astype(np.uint16, copy=False)
            Image.fromarray(background_u16, mode="I;16").save(destination)
        except Exception as exc:
            QMessageBox.critical(window, "Save background failed", str(exc))
            window._set_status_text(f"Save background failed: {exc}")
            return
        if window._showing_background_profile_main:
            window._update_background_profile_preview()
        window._set_status_text(f"Saved background to {destination.name}.")

    def read_background_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        with Image.open(path) as image:
            background = np.array(image.convert("I"), dtype=np.float32)
        if background.shape != expected_shape:
            raise ValueError(
                f"Background size {background.shape[1]} x {background.shape[0]} px does not match the current image "
                f"{expected_shape[1]} x {expected_shape[0]} px."
            )
        return background

    def update_mask_file_button_state(self) -> None:
        window = self.window
        has_image = window._current_processed_image is not None
        editable_mask = has_image and (window._is_current_reference_image() or not window._state.preprocessing.chromatic_correction_enabled)
        window.mask_create_new_button.setEnabled(editable_mask)
        window.mask_load_from_file_button.setEnabled(editable_mask)
        window.mask_save_button.setEnabled(editable_mask)
        window.histogram_mask_apply_button.setEnabled(editable_mask)
        window.histogram_mask_reset_button.setEnabled(editable_mask)
        window.relative_mask_apply_button.setEnabled(editable_mask)
        window.relative_mask_reset_button.setEnabled(editable_mask)
        window.relative_mask_show_button.setEnabled(has_image)
        window.local_contrast_mask_apply_button.setEnabled(editable_mask)
        window.local_contrast_mask_reset_button.setEnabled(editable_mask)
        window.local_contrast_mask_show_button.setEnabled(has_image)
        window.morphology_mask_apply_button.setEnabled(editable_mask)
        window.morphology_mask_reset_button.setEnabled(editable_mask)
        window.morphology_mask_show_button.setEnabled(has_image)
        window.mask_morphology_radius_spin.setEnabled(has_image)
        window.mask_morphology_erode_button.setEnabled(editable_mask)
        window.mask_morphology_dilate_button.setEnabled(editable_mask)
        window.mask_morphology_open_button.setEnabled(editable_mask)
        window.mask_morphology_close_button.setEnabled(editable_mask)
        window.background_create_new_button.setEnabled(editable_mask)
        window.background_load_from_file_button.setEnabled(editable_mask)
        window.background_save_button.setEnabled(editable_mask)
        window.mask_morph_radius_spin.setEnabled(has_image)
        window.mask_pencil_check.setEnabled(editable_mask)
        window.mask_draw_mode_combo.setEnabled(editable_mask)
        window.mask_draw_add_button.setEnabled(editable_mask and window.mask_pencil_check.isChecked())
        window.mask_draw_remove_button.setEnabled(editable_mask and window.mask_pencil_check.isChecked())
        window.mask_brush_size_spin.setEnabled(editable_mask)
        default_path = self.current_mask_file_path()
        default_name = default_path.name if default_path is not None else "current_image_mask.png"
        if window._current_file_mask_path is not None:
            current_source = window._current_file_mask_path.name
        elif window._current_file_mask_session_source_path is not None:
            current_source = "session mask"
        else:
            current_source = default_name
        background_default_path = self.current_background_file_path()
        background_default_name = background_default_path.name if background_default_path is not None else "current_image_background.png"
        window.mask_load_from_file_button.setToolTip(
            f"Load a black-and-white mask image for the current image.\nSuggested file name: {default_name}"
        )
        window.mask_save_button.setToolTip(
            f"Save the current mask as a black-and-white image.\nTarget file name: {default_name}\nCurrent source: {current_source}"
        )
        window.background_create_new_button.setToolTip(
            f"Create a new background image from the current parameters.\nTarget file name: {background_default_name}"
        )
        window.background_load_from_file_button.setToolTip(
            f"Load a background image for the current image.\nSuggested file name: {background_default_name}"
        )
        window.background_save_button.setToolTip(
            f"Save the current background image.\nTarget file name: {background_default_name}"
        )
