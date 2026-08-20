from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtGui import QColor

from lspr_imaging_app.gui.worker import UndoSnapshot
from lspr_imaging_app.io.dataset import dataset_record_map


class UndoManager:
    """Owns the undo/redo stack and full-window-state snapshots.

    Snapshots capture window._state plus the visual preferences (colors,
    alpha, visibility toggles) that live outside window._state, since undo
    is meant to restore "what the user saw," not just the scientific data.
    """

    UNDO_STACK_LIMIT = 5

    def __init__(self, window) -> None:
        self.window = window

    def make_snapshot(self, label: str) -> UndoSnapshot:
        window = self.window
        return UndoSnapshot(
            label=label,
            state=deepcopy(window._state),
            folder_text=window.folder_edit.text(),
            spectral_cube_slider_value=int(window.spectral_cube_slider.value()) if hasattr(window, "spectral_cube_slider") else 0,
            wavelength_slider_value=int(window.wavelength_slider.value()) if hasattr(window, "wavelength_slider") else 0,
            selected_roi_ids=set(window._selected_roi_ids),
            sample_visual_color=window._sample_visual_color.name(),
            reference_visual_color=window._reference_visual_color.name(),
            mask_visual_color=window._mask_visual_color.name(),
            histogram_mask_visual_color=window._histogram_mask_visual_color.name(),
            figure_mask_visual_color=window._figure_mask_visual_color.name(),
            highlight_visual_color=window._highlight_visual_color.name(),
            roi_alpha=float(window._roi_alpha),
            reference_alpha=float(window._reference_alpha),
            mask_alpha=float(window._mask_alpha),
            histogram_mask_alpha=float(window._mask_alpha),  # Use same alpha for now
            figure_mask_alpha=float(window._mask_alpha),     # Use same alpha for now
            highlight_alpha=float(window._highlight_alpha),
            rois_visible=bool(window._rois_visible),
            reference_rois_visible=bool(window._reference_visible),
            mask_visible=bool(window._mask_visible),
            reference_points_visible=bool(window._reference_points_visible),
            histogram_mask_visible=bool(window._mask_visible),  # Use same visibility for now
            figure_mask_visible=bool(window._mask_visible),     # Use same visibility for now
            highlight_visible=bool(window._highlight_visible),
            file_mask=None if window._current_file_mask is None else window._current_file_mask.copy(),
            file_mask_path=None if window._current_file_mask_path is None else str(window._current_file_mask_path),
            file_mask_revision=int(window._external_mask_revision),
        )

    def signature(self, snapshot) -> tuple[object, ...]:
        dataset_folder = None if snapshot.state.dataset is None else str(snapshot.state.dataset.folder)
        return (
            dataset_folder,
            snapshot.folder_text,
            snapshot.spectral_cube_slider_value,
            snapshot.wavelength_slider_value,
            repr(asdict(snapshot.state.preprocessing)),
            repr(asdict(snapshot.state.area_roi_settings)),
            repr([asdict(roi) for roi in snapshot.state.rois]),
            repr([asdict(roi) for roi in snapshot.state.area_rois]),
            repr([asdict(group) for group in snapshot.state.area_roi_groups]),
            repr([asdict(array_group) for array_group in snapshot.state.area_roi_arrays]),
            tuple(sorted(snapshot.selected_roi_ids)),
            snapshot.sample_visual_color,
            snapshot.reference_visual_color,
            snapshot.mask_visual_color,
            snapshot.highlight_visual_color,
            round(snapshot.roi_alpha, 4),
            round(snapshot.reference_alpha, 4),
            round(snapshot.mask_alpha, 4),
            round(snapshot.highlight_alpha, 4),
            snapshot.rois_visible,
            snapshot.reference_rois_visible,
            snapshot.mask_visible,
            snapshot.reference_points_visible,
            snapshot.highlight_visible,
            snapshot.file_mask_revision,
            snapshot.file_mask_path,
        )

    def current_signature(self) -> tuple[object, ...]:
        return self.signature(self.make_snapshot("current"))

    def update_action_state(self) -> None:
        window = self.window
        window.undo_action.setEnabled(bool(window._undo_stack) and window._busy_operation_count == 0)
        if hasattr(window, "redo_action"):
            window.redo_action.setEnabled(bool(window._redo_stack) and window._busy_operation_count == 0)

    def push(self, label: str) -> None:
        window = self.window
        if window._restoring_undo:
            return
        snapshot = self.make_snapshot(label)
        if window._undo_stack and self.signature(window._undo_stack[-1]) == self.signature(snapshot):
            return
        window._undo_stack.append(snapshot)
        window._redo_stack.clear()
        if len(window._undo_stack) > self.UNDO_STACK_LIMIT:
            window._undo_stack = window._undo_stack[-self.UNDO_STACK_LIMIT :]
        self.update_action_state()

    def prepare(self, label: str) -> None:
        window = self.window
        if window._restoring_undo or window._prepared_undo_snapshot is not None:
            return
        window._prepared_undo_snapshot = self.make_snapshot(label)

    def commit_prepared(self) -> None:
        window = self.window
        if window._prepared_undo_snapshot is None:
            return
        if self.signature(window._prepared_undo_snapshot) != self.current_signature():
            window._undo_stack.append(window._prepared_undo_snapshot)
            window._redo_stack.clear()
            if len(window._undo_stack) > self.UNDO_STACK_LIMIT:
                window._undo_stack = window._undo_stack[-self.UNDO_STACK_LIMIT :]
        window._prepared_undo_snapshot = None
        self.update_action_state()

    def clear_prepared(self) -> None:
        self.window._prepared_undo_snapshot = None

    def restore(self, snapshot) -> None:
        window = self.window
        window._restoring_undo = True
        try:
            self.clear_prepared()
            window._pending_image_refresh_payload = None
            window._latest_image_refresh_signature = None
            window._roi_detection_request_id += 1
            window._showing_background_profile_main = False
            window._state = deepcopy(snapshot.state)
            window._reset_roi_id_counter_from_state()
            window.folder_edit.setText(snapshot.folder_text)
            window._selected_roi_ids = set(snapshot.selected_roi_ids)
            window._sample_visual_color = QColor(snapshot.sample_visual_color)
            window._reference_visual_color = QColor(snapshot.reference_visual_color)
            window._mask_visual_color = QColor(snapshot.mask_visual_color)
            window._histogram_mask_visual_color = QColor(snapshot.histogram_mask_visual_color)
            window._figure_mask_visual_color = QColor(snapshot.figure_mask_visual_color)
            window._highlight_visual_color = QColor(snapshot.highlight_visual_color)
            window._roi_alpha = snapshot.roi_alpha
            window._reference_alpha = snapshot.reference_alpha
            window._mask_alpha = snapshot.mask_alpha
            window._highlight_alpha = snapshot.highlight_alpha
            window._rois_visible = snapshot.rois_visible
            window._reference_visible = snapshot.reference_rois_visible
            window._mask_visible = snapshot.mask_visible
            window._reference_points_visible = snapshot.reference_points_visible
            window._highlight_visible = snapshot.highlight_visible
            window._current_file_mask = None if snapshot.file_mask is None else snapshot.file_mask.copy()
            window._current_file_mask_path = None if snapshot.file_mask_path is None else Path(snapshot.file_mask_path)
            window._external_mask_revision = int(snapshot.file_mask_revision)
            window._selected_rectangle_roi_ids.clear()
            window._processed_image_cache.clear()
            window._invalidate_image_analysis_caches()
            window._invalidate_background_profile_cache()
            window._sync_rectangle_stamp_overlays()

            dataset = window._state.dataset
            window._record_map = dataset_record_map(dataset) if dataset is not None else {}
            window._record_key_by_path = (
                {record.path: (int(record.key.spectral_cube_index), float(record.key.wavelength_nm)) for record in dataset.records}
                if dataset is not None
                else {}
            )
            window._spectral_cube_values = dataset.spectral_cube_indices if dataset is not None else []
            window._wavelength_values = dataset.wavelengths_nm if dataset is not None else []
            window._current_record_path = None
            window._current_image_key = None
            window._processed_shape_cache.clear()
            window._update_dataset_summary_labels(dataset)

            window._sync_image_processing_controls()
            window._configure_navigation_inputs()
            window._update_analysis_control_state()
            window._sync_roi_detection_controls()
            window._update_mask_file_button_state()
            window._update_color_button_styles()
            window.show_rois_check.blockSignals(True)
            window.bottom_roi_labels_button.blockSignals(True)
            window.roi_editor_labels_button.blockSignals(True)
            window.show_reference_check.blockSignals(True)
            window.show_mask_check.blockSignals(True)
            window.show_reference_points_check.blockSignals(True)
            window.show_highlight_check.blockSignals(True)
            window.show_rois_check.setChecked(window._rois_visible)
            window.bottom_roi_labels_button.setChecked(window._roi_labels_visible)
            window.roi_editor_labels_button.setChecked(window._roi_labels_visible)
            window.show_reference_check.setChecked(window._reference_visible)
            window.show_mask_check.setChecked(window._mask_visible)
            window.show_reference_points_check.setChecked(window._reference_points_visible)
            window.show_highlight_check.setChecked(window._highlight_visible)
            window.show_rois_check.blockSignals(False)
            window.bottom_roi_labels_button.blockSignals(False)
            window.roi_editor_labels_button.blockSignals(False)
            window.show_reference_check.blockSignals(False)
            window.show_mask_check.blockSignals(False)
            window.show_reference_points_check.blockSignals(False)
            window.show_highlight_check.blockSignals(False)
            window._refresh_view_toggle_icons()
            window._update_roi_label_button_icon(bool(window._roi_labels_visible))
            window.roi_alpha_slider.blockSignals(True)
            window.reference_alpha_slider.blockSignals(True)
            window.mask_alpha_slider.blockSignals(True)
            window.highlight_alpha_slider.blockSignals(True)
            window.roi_alpha_slider.setValue(int(round(window._roi_alpha * 100.0)))
            window.reference_alpha_slider.setValue(int(round(window._reference_alpha * 100.0)))
            window.mask_alpha_slider.setValue(int(round(window._mask_alpha * 100.0)))
            window.highlight_alpha_slider.setValue(int(round(window._highlight_alpha * 100.0)))
            window.roi_alpha_slider.blockSignals(False)
            window.reference_alpha_slider.blockSignals(False)
            window.mask_alpha_slider.blockSignals(False)
            window.highlight_alpha_slider.blockSignals(False)

            window._configure_slider(window.spectral_cube_slider, len(window._spectral_cube_values))
            window._configure_slider(window.wavelength_slider, len(window._wavelength_values))
            window._configure_navigation_inputs()
            if dataset is not None and window._spectral_cube_values and window._wavelength_values:
                spectral_cube_value = min(max(snapshot.spectral_cube_slider_value, 0), len(window._spectral_cube_values) - 1)
                wavelength_value = min(max(snapshot.wavelength_slider_value, 0), len(window._wavelength_values) - 1)
                window.spectral_cube_slider.blockSignals(True)
                window.wavelength_slider.blockSignals(True)
                window.spectral_cube_slider.setValue(spectral_cube_value)
                window.wavelength_slider.setValue(wavelength_value)
                window.spectral_cube_slider.blockSignals(False)
                window.wavelength_slider.blockSignals(False)
                window._refresh_image()
            window._update_selection_dependent_plots(force=True)
            window._save_visual_preferences()
            window._schedule_processing_state_save()
        finally:
            window._restoring_undo = False

    def undo(self) -> None:
        window = self.window
        if window._busy_operation_count > 0:
            window._set_status_text("Wait for the current operation to finish before undo.")
            return
        if not window._undo_stack:
            window._set_status_text("Nothing to undo.")
            return
        snapshot = window._undo_stack.pop()
        window._redo_stack.append(self.make_snapshot(snapshot.label))
        if len(window._redo_stack) > self.UNDO_STACK_LIMIT:
            window._redo_stack = window._redo_stack[-self.UNDO_STACK_LIMIT :]
        self.restore(snapshot)
        self.update_action_state()
        window._set_status_text(f"Undid: {snapshot.label}")

    def redo(self) -> None:
        window = self.window
        if window._busy_operation_count > 0:
            window._set_status_text("Wait for the current operation to finish before redo.")
            return
        if not window._redo_stack:
            window._set_status_text("Nothing to redo.")
            return
        snapshot = window._redo_stack.pop()
        window._undo_stack.append(self.make_snapshot(snapshot.label))
        if len(window._undo_stack) > self.UNDO_STACK_LIMIT:
            window._undo_stack = window._undo_stack[-self.UNDO_STACK_LIMIT :]
        self.restore(snapshot)
        self.update_action_state()
        window._set_status_text(f"Redid: {snapshot.label}")
