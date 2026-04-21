from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_imaging_app.domain.models import CropDefinition, MaskSettings, SpotDetectionSettings
from lspr_imaging_app.storage.workspace import (
    load_preprocessing,
    load_processing_profile,
    save_preprocessing,
    save_processing_profile,
)


class SessionStateManager:
    def __init__(self, window) -> None:
        self._window = window

    def run_startup_restore_flow(
        self,
        *,
        show_window: bool = True,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> None:
        window = self._window
        window._startup_restore_in_progress = True
        window._startup_progress_callback = progress_callback
        window._append_workflow_log("Startup | restore begin", level="debug")
        window._report_startup_progress(8, "Restoring window layout...")
        window._restore_window_geometry()
        window._restore_layout_preferences()
        window._report_startup_progress(18, "Checking previous session...")
        window._dataset_controller.run_startup_restore_flow()
        if window._state.dataset is None:
            window._append_workflow_log("Startup | no dataset restored", level="warning")
            window._report_startup_progress(100, "Ready.")
            if window._fast_startup:
                window._set_status_text("Fast startup enabled. Load a dataset when ready.")
            if show_window:
                window.showNormal()
                window.raise_()
                window.activateWindow()
                window._sync_panel_visibility_after_show()
            window._startup_restore_in_progress = False
            window._startup_ready = True
            window._startup_progress_callback = None
            return
        window._report_startup_progress(58, "Loading analysis cache...")
        window._append_workflow_log("Startup | analysis cache restored", level="debug")
        window._sync_spot_detection_controls()
        window._restore_control_preferences()
        window._report_startup_progress(76, "Preparing the first image...")
        window._analysis_enabled = False
        window._analysis_live_preview_enabled = False
        window._set_section_applied(window.analysis_section, False)
        window._settings.setValue("analysis_section_applied", False)
        window._settings.setValue("analysis/live_preview", False)
        window._refresh_image()
        window._report_startup_progress(92, "Finalizing workspace...")
        if show_window:
            window.showNormal()
            window.raise_()
            window.activateWindow()
            window._sync_panel_visibility_after_show()
        window._startup_restore_in_progress = False
        window._startup_ready = True
        window._report_startup_progress(100, "Workspace ready.")
        window._append_workflow_log("Startup | workspace ready", level="success")
        window._startup_progress_callback = None
        window._end_busy("Loaded dataset. Showing the reference image.")

    def _processing_profile_path(self) -> Path | None:
        return self._window._processing_profile_path()

    def _session_mask_payload(self) -> dict | None:
        return self._window._session_mask_payload()

    def load_processing_state_for_dataset(self) -> None:
        window = self._window
        profile_path = window._processing_profile_path()
        preprocessing_path = window._preprocessing_path()

        if profile_path is not None and profile_path.exists():
            try:
                window._report_startup_progress(42, "Loading processing profile...")
                (
                    preprocessing,
                    spot_detection,
                    detected_spots,
                    spot_groups,
                    chromatic_models,
                    chromatic_landmarks,
                    analysis_cache,
                    session_mask,
                    mask_settings,
                ) = load_processing_profile(profile_path)
                window._state.preprocessing = preprocessing
                window._state.spot_detection = spot_detection
                window._state.mask = mask_settings
                window._state.detected_spots = detected_spots
                window._state.spot_groups = spot_groups
                window._current_file_mask = None
                window._current_file_mask_path = None
                window._current_file_mask_session_source_path = None
                window._append_workflow_log(
                    f"Mask settings restore | histogram={bool(mask_settings.histogram_enabled)} figure={bool(mask_settings.figure_enabled)}",
                    level="debug",
                )
                if session_mask is not None:
                    restored_mask = session_mask.get("mask")
                    restored_record_path = session_mask.get("record_path")
                    if isinstance(restored_mask, np.ndarray):
                        window._set_current_file_mask(restored_mask, None, refresh_preview=False)
                        window._current_file_mask_session_source_path = (
                            None if restored_record_path is None else Path(str(restored_record_path))
                        )
                window._report_startup_progress(48, "Restoring spot groups...")
                window._report_startup_progress(54, "Restoring chromatic transforms...")
                window._state.chromatic_models = chromatic_models
                window._state.chromatic_landmarks = chromatic_landmarks
                window._restore_analysis_caches(analysis_cache)
                window._append_workflow_log(
                    f"Mask settings restore | histogram={bool(mask_settings.histogram_enabled)} figure={bool(mask_settings.figure_enabled)}",
                    level="debug",
                )
                if session_mask is None:
                    window._append_workflow_log("Session mask restore | none", level="debug")
                else:
                    restored_mask = session_mask.get("mask")
                    restored_record_path = session_mask.get("record_path")
                    mask_shape = None if not isinstance(restored_mask, np.ndarray) else tuple(int(v) for v in restored_mask.shape)
                    window._append_workflow_log(
                        f"Session mask restore | record={restored_record_path} | shape={mask_shape}",
                        level="debug",
                    )
                window._normalize_mask_application_state()
                window._update_spot_list_table()
                window._update_chromatic_control_state()
                window._restore_chromatic_view_after_load()
                window._report_startup_progress(60, "Processing profile restored.")
                return
            except Exception as exc:
                window.status_label.setText(f"Processing profile load failed: {exc}")

        if preprocessing_path is not None and preprocessing_path.exists():
            try:
                window._state.preprocessing = load_preprocessing(preprocessing_path)
                window._state.spot_detection = SpotDetectionSettings()
                window._state.mask = MaskSettings()
                window._state.detected_spots.clear()
                window._state.spot_groups.clear()
                window._state.chromatic_models.clear()
                window._state.chromatic_landmarks.clear()
                window._current_file_mask = None
                window._current_file_mask_path = None
                window._current_file_mask_session_source_path = None
                window._update_spot_list_table()
                window._normalize_mask_application_state()
                return
            except Exception as exc:
                window.status_label.setText(f"Preprocessing load failed: {exc}")

        window._state.preprocessing.rotation_angle_deg = 0.0
        window._state.preprocessing.flip_horizontal = False
        window._state.preprocessing.flip_vertical = False
        window._state.preprocessing.crop = CropDefinition()
        window._state.preprocessing.flatten_background_enabled = False
        window._state.preprocessing.flatten_background_sigma_px = 48.0
        window._state.preprocessing.flatten_background_binning = 2
        window._state.preprocessing.flatten_background_exclude_spots = True
        window._state.preprocessing.flatten_background_exclude_mask = False
        window._state.preprocessing.chromatic_correction_enabled = False
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._state.preprocessing.chromatic_sample_image_count = 5
        window._state.preprocessing.chromatic_feature_count = 5
        window._state.preprocessing.chromatic_tile_size_px = 96
        window._state.preprocessing.chromatic_search_radius_px = 24
        window._state.preprocessing.reference_mode = "auto"
        window._state.preprocessing.reference_wavelength_nm = None
        window._state.preprocessing.reference_frame_index = 0
        window._state.spot_detection = SpotDetectionSettings()
        window._state.mask = MaskSettings()
        window._state.detected_spots.clear()
        window._state.spot_groups.clear()
        window._state.chromatic_models.clear()
        window._state.chromatic_landmarks.clear()
        window._current_file_mask = None
        window._current_file_mask_path = None
        window._current_file_mask_session_source_path = None
        window._update_spot_list_table()

    def save_processing_state_for_dataset(self, *, force: bool = False, reason: str | None = None) -> None:
        window = self._window
        if window._processing_state_save_timer.isActive():
            window._processing_state_save_timer.stop()
        if window._spot_state_save_timer.isActive():
            window._spot_state_save_timer.stop()
        path = window._preprocessing_path()
        profile_path = window._processing_profile_path()
        if path is None or profile_path is None:
            return
        signature = window._processing_state_signature()
        if force:
            window._append_workflow_log(
                f"Processing state save requested | reason={reason or 'unspecified'} | path={profile_path.name}",
                level="debug",
            )
        if (
            not force
            and window._last_saved_processing_signature == signature
            and window._last_saved_preprocessing_path == path
            and window._last_saved_profile_path == profile_path
        ):
            window._append_workflow_log_throttled(
                "processing_state_save_skipped",
                f"Processing state save skipped | path={profile_path.name} | unchanged",
                level="debug",
                min_interval=1.0,
            )
            return
        try:
            save_preprocessing(path, window._state.preprocessing)
            save_processing_profile(
                profile_path,
                window._state.preprocessing,
                window._state.spot_detection,
                window._state.detected_spots,
                window._state.spot_groups,
                window._state.chromatic_models,
                window._state.chromatic_landmarks,
                window._analysis_cache_payload(),
                session_mask=window._session_mask_payload(),
                mask_settings=window._state.mask,
            )
            session_mask_payload = window._session_mask_payload()
            if session_mask_payload is None:
                window._append_workflow_log_throttled("session_mask_save", "Session mask save | none", level="debug", min_interval=1.0)
            else:
                mask_shape = None if not isinstance(session_mask_payload.get("mask"), np.ndarray) else tuple(int(v) for v in session_mask_payload["mask"].shape)
                window._append_workflow_log_throttled(
                    "session_mask_save",
                    f"Session mask save | record={session_mask_payload.get('record_path')} | shape={mask_shape}",
                    level="debug",
                    min_interval=1.0,
                )
            window._append_workflow_log_throttled(
                "processing_state_saved",
                f"Processing state saved | path={profile_path.name}",
                level="debug",
                min_interval=1.0,
            )
            window._last_saved_processing_signature = signature
            window._last_saved_preprocessing_path = path
            window._last_saved_profile_path = profile_path
        except Exception as exc:
            window._append_workflow_log(
                f"Processing state save failed | path={profile_path.name} | {exc}",
                level="error",
            )
            window.status_label.setText(f"Preprocessing save failed: {exc}")

    def export_processing_profile(self) -> None:
        window = self._window
        dataset = window._state.dataset
        default_dir = dataset.folder if dataset is not None else Path(window.folder_edit.text())
        destination, _ = QFileDialog.getSaveFileName(
            window,
            "Export processing settings",
            str(default_dir / "processing_profile.json"),
            "JSON files (*.json)",
        )
        if not destination:
            return
        window._begin_busy("Exporting processing settings...")
        try:
            save_processing_profile(
                Path(destination),
                window._state.preprocessing,
                window._state.spot_detection,
                window._state.detected_spots,
                window._state.spot_groups,
                window._state.chromatic_models,
                window._state.chromatic_landmarks,
                window._analysis_cache_payload(),
                session_mask=window._session_mask_payload(),
                mask_settings=window._state.mask,
            )
            session_mask_payload = window._session_mask_payload()
            if session_mask_payload is None:
                window._append_workflow_log_throttled("session_mask_export", "Session mask export | none", level="debug", min_interval=1.0)
            else:
                mask_shape = None if not isinstance(session_mask_payload.get("mask"), np.ndarray) else tuple(int(v) for v in session_mask_payload["mask"].shape)
                window._append_workflow_log_throttled(
                    "session_mask_export",
                    f"Session mask export | record={session_mask_payload.get('record_path')} | shape={mask_shape}",
                    level="debug",
                    min_interval=1.0,
                )
            window._end_busy(f"Exported processing settings to {destination}")
        except Exception as exc:
            window._end_busy(f"Export failed: {exc}")
            QMessageBox.critical(window, "Export failed", str(exc))
        else:
            window._set_status_text("Processing settings exported.")
            window._append_workflow_log("Processing settings exported.", level="success")

    def import_processing_profile(self) -> None:
        window = self._window
        source, _ = QFileDialog.getOpenFileName(
            window,
            "Import processing settings",
            str(Path(window.folder_edit.text()) if window.folder_edit.text() else Path.cwd()),
            "JSON files (*.json)",
        )
        if not source:
            return
        window._push_undo_point("Import processing settings")
        window._begin_busy("Importing processing settings...")
        try:
            (
                preprocessing,
                spot_detection,
                detected_spots,
                spot_groups,
                chromatic_models,
                chromatic_landmarks,
                analysis_cache,
                session_mask,
                mask_settings,
            ) = load_processing_profile(Path(source))
            window._state.preprocessing = preprocessing
            window._state.spot_detection = spot_detection
            window._state.detected_spots = detected_spots
            window._state.spot_groups = spot_groups
            window._state.chromatic_models = chromatic_models
            window._state.chromatic_landmarks = chromatic_landmarks
            window._state.mask = mask_settings
            if session_mask is not None:
                restored_mask = session_mask.get("mask")
                restored_record_path = session_mask.get("record_path")
                if isinstance(restored_mask, np.ndarray):
                    window._set_current_file_mask(restored_mask, None, refresh_preview=False)
                    window._current_file_mask_session_source_path = (
                        None if restored_record_path is None else Path(str(restored_record_path))
                    )
            window._restore_analysis_caches(analysis_cache)
            window._update_spot_list_table()
            window._restore_control_preferences()
            window._update_chromatic_control_state()
            window._restore_chromatic_view_after_load()
            window._sync_reference_selection_from_settings()
            window._sync_image_processing_controls()
            window._sync_spot_detection_controls()
            window._current_image_key = None
            window._refresh_image()
            window._save_processing_state_for_dataset()
        except Exception as exc:
            window._end_busy(f"Import failed: {exc}")
            QMessageBox.critical(window, "Import failed", str(exc))
        else:
            window._set_status_text("Processing settings imported.")
            window._append_workflow_log("Processing settings imported.", level="success")
            window._end_busy(f"Imported processing settings from {source}")
        finally:
            window._end_busy("Processing settings imported.")
