from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from scipy import ndimage

from lspr_imaging_app.domain.models import MaskSettings
from lspr_imaging_app.io.dataset import dataset_load_plane, dataset_plane_shape, load_image_array, load_image_shape
from lspr_imaging_app.processing.preprocess import create_figure_mask


class MaskController:
    def __init__(self, window) -> None:
        self.window = window

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
            export_settings = deepcopy(window._state.spot_detection)
            export_settings.ignore_marked_pixels = True
            from lspr_imaging_app.processing.spot_detection import ignored_pixel_mask
            mask = ignored_pixel_mask(raw_image, export_settings, external_mask=None)
        try:
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

    def candidate_mask_for_tool(self, tool_key: str) -> np.ndarray | None:
        window = self.window
        if window._current_record_path is None:
            return None
        if tool_key == "histogram":
            return self.current_histogram_highlight_mask_raw()

        current_key = window._image_key_for_record_path(window._current_record_path)
        if current_key is not None and window._state.dataset is not None:
            raw_image = dataset_load_plane(window._state.dataset, int(current_key[0]), float(current_key[1])).astype(np.float32, copy=False)
        else:
            raw_image = load_image_array(str(window._current_record_path)).astype(np.float32, copy=False)
        mask_settings = MaskSettings(
            relative_threshold_fraction=float(window.mask_relative_threshold_spin.value()) / 100.0,
            relative_profile_sigma_px=float(window.mask_relative_profile_sigma_spin.value()),
            local_contrast_sigma_px=float(window.mask_local_contrast_sigma_spin.value()),
            local_contrast_z_threshold=float(window.mask_local_contrast_z_spin.value()),
        )

        if tool_key == "relative":
            return create_figure_mask(raw_image, mask_settings, "relative")
        if tool_key == "local_contrast":
            return create_figure_mask(raw_image, mask_settings, "local_contrast")
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

    def apply_mask_delta(self, tool_key: str, *, subtract: bool) -> None:
        window = self.window
        if window._current_record_path is None:
            window._set_status_text("Load an image first.")
            return
        window._ensure_mask_section_applied()
        candidate = self.candidate_mask_for_tool(tool_key)
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
        if window.relative_mask_show_button.isChecked():
            window._mask_histogram_preview = self.candidate_mask_for_tool("relative")
        else:
            window._mask_histogram_preview = None
        if window.local_contrast_mask_show_button.isChecked():
            window._mask_figure_preview = self.candidate_mask_for_tool("local_contrast")
        elif window.morphology_mask_show_button.isChecked():
            window._mask_figure_preview = self.candidate_mask_for_tool("morphology")
        else:
            window._mask_figure_preview = None
        window._update_ignore_mask_overlay()
