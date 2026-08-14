from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, CropDefinition, MaskSettings
from lspr_imaging_app.gui.analysis_tasks import _load_processing_state_task
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.storage.workspace import (
    build_preprocessing_payload,
    build_processing_profile_payload,
    load_processing_profile,
    save_processing_profile,
    write_processing_state_files,
)

_SAFE_SESSION_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_session_name(name: str) -> str:
    cleaned = _SAFE_SESSION_NAME_RE.sub("_", name.strip()).strip("_")
    return cleaned or "Default"


class SessionStateManager:
    def __init__(self, window) -> None:
        self._window = window

    def _processing_profile_path(self) -> Path | None:
        return self._window._processing_profile_path()

    def _session_mask_payload(self) -> dict | None:
        return self._window._session_mask_payload()

    def load_processing_state_for_dataset(self, *, on_done=None) -> None:
        window = self._window
        profile_path = window._processing_profile_path()
        preprocessing_path = window._preprocessing_path()
        window._report_startup_progress(42, "Loading processing profile...")
        worker = FunctionWorker(_load_processing_state_task, profile_path, preprocessing_path)
        worker.signals.result.connect(
            lambda result, on_done=on_done: self._on_processing_state_loaded(on_done, *result)
        )
        window._thread_pool.start(worker)

    def _on_processing_state_loaded(
        self,
        on_done,
        outcome: str,
        payload: object,
        profile_error: str | None,
        preprocessing_error: str | None,
    ) -> None:
        window = self._window
        if profile_error is not None:
            window.status_label.setText(f"Processing profile load failed: {profile_error}")

        if outcome == "profile":
            (
                preprocessing,
                area_roi_settings,
                area_rois,
                area_roi_groups,
                rois,
                chromatic_models,
                chromatic_landmarks,
                analysis_cache,
                session_mask,
                mask_settings,
                image_exclusions,
                area_roi_arrays,
            ) = payload
            window._state.preprocessing = preprocessing
            window._state.area_roi_settings = area_roi_settings
            window._state.mask = mask_settings
            window._state.area_rois = area_rois
            window._state.area_roi_groups = area_roi_groups
            window._state.area_roi_arrays = area_roi_arrays
            window._state.rois = rois
            window._reset_roi_id_counter_from_state()
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
            window._report_startup_progress(48, "Restoring ROI groups...")
            window._report_startup_progress(54, "Restoring chromatic transforms...")
            window._state.chromatic_models = chromatic_models
            window._state.chromatic_landmarks = chromatic_landmarks
            window._state.image_exclusions = image_exclusions
            window._restore_analysis_caches(analysis_cache)
            window._selected_rectangle_roi_ids.clear()
            window._sync_rectangle_stamp_overlays()
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
            window._update_roi_table()
            window._update_chromatic_control_state()
            window._restore_chromatic_view_after_load()
            window._report_startup_progress(60, "Processing profile restored.")
            if on_done is not None:
                on_done()
            return

        if preprocessing_error is not None:
            window.status_label.setText(f"Preprocessing load failed: {preprocessing_error}")

        if outcome == "preprocessing":
            window._state.preprocessing = payload
            window._state.area_roi_settings = AreaRoiDetectionSettings()
            window._state.mask = MaskSettings()
            window._state.area_rois.clear()
            window._state.area_roi_groups.clear()
            window._state.rois.clear()
            window._state.chromatic_models.clear()
            window._state.chromatic_landmarks.clear()
            window._state.image_exclusions.clear()
            window._current_file_mask = None
            window._current_file_mask_path = None
            window._current_file_mask_session_source_path = None
            window._selected_rectangle_roi_ids.clear()
            window._sync_rectangle_stamp_overlays()
            window._update_roi_table()
            window._normalize_mask_application_state()
            if on_done is not None:
                on_done()
            return

        self._reset_processing_state_to_defaults()
        if on_done is not None:
            on_done()

    def _reset_processing_state_to_defaults(self) -> None:
        window = self._window
        window._state.preprocessing.rotation_angle_deg = 0.0
        window._state.preprocessing.rotation_fill_dark = False
        window._state.preprocessing.flip_horizontal = False
        window._state.preprocessing.flip_vertical = False
        window._state.preprocessing.crop = CropDefinition()
        window._state.preprocessing.flatten_background_enabled = False
        window._state.preprocessing.flatten_background_sigma_px = 48.0
        window._state.preprocessing.flatten_background_binning = 2
        window._state.preprocessing.flatten_background_exclude_area_rois = True
        window._state.preprocessing.flatten_background_exclude_mask = False
        window._state.preprocessing.flatten_background_exclusion_dilation_px = 0
        window._state.preprocessing.chromatic_correction_enabled = False
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._state.preprocessing.chromatic_landmark_model = "similarity"
        window._state.preprocessing.chromatic_sample_image_count = 5
        window._state.preprocessing.chromatic_feature_count = 15
        window._state.preprocessing.chromatic_tile_size_px = 96
        window._state.preprocessing.chromatic_search_radius_px = 24
        window._state.preprocessing.reference_mode = "auto"
        window._state.preprocessing.reference_wavelength_nm = None
        window._state.preprocessing.reference_spectral_cube_index = 0
        window._state.area_roi_settings = AreaRoiDetectionSettings()
        window._state.mask = MaskSettings()
        window._state.area_rois.clear()
        window._state.area_roi_groups.clear()
        window._state.rois.clear()
        window._state.chromatic_models.clear()
        window._state.chromatic_landmarks.clear()
        window._state.image_exclusions.clear()
        window._current_file_mask = None
        window._current_file_mask_path = None
        window._current_file_mask_session_source_path = None
        window._selected_rectangle_roi_ids.clear()
        window._sync_rectangle_stamp_overlays()
        window._update_roi_table()

    def new_session(self) -> None:
        window = self._window
        dataset = window._state.dataset
        if dataset is None:
            window._set_status_text("Load a dataset before starting a new session.")
            return
        name, ok = QInputDialog.getText(
            window,
            "New session",
            "Name this session (leave blank for the default, unnamed session):",
        )
        if not ok:
            return
        name = _sanitize_session_name(name)
        if name != "Default" and (dataset.folder / "sessions" / name / "processing_profile.json").exists():
            confirm = QMessageBox.question(
                window,
                "Session already exists",
                f'A session named "{name}" already exists. Starting a new session with this name '
                "will reset its saved settings back to defaults. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        window._push_undo_point("New session")
        self._reset_processing_state_to_defaults()
        window._active_session_name = name
        window._save_active_session_pointer()
        window._current_image_key = None
        window._refresh_image()
        window._save_processing_state_for_dataset(force=True, reason="new session")
        window._set_status_text(f'Started new session "{name}".')
        window._append_workflow_log(f"New session started | name={name}", level="success")

    def load_session(self) -> None:
        window = self._window
        dataset = window._state.dataset
        if dataset is None:
            window._set_status_text("Load a dataset before switching sessions.")
            return
        names = window._list_available_sessions()
        current = window._active_session_name
        name, ok = QInputDialog.getItem(
            window,
            "Load session",
            "Choose a session to load:",
            names,
            names.index(current) if current in names else 0,
            False,
        )
        if not ok or name == current:
            return
        window._save_processing_state_for_dataset(force=True, reason="switch session")
        window._push_undo_point("Load session")
        window._active_session_name = name
        window._save_active_session_pointer()
        self.load_processing_state_for_dataset(on_done=lambda name=name: self._on_load_session_ready(name))

    def _on_load_session_ready(self, name: str) -> None:
        window = self._window
        window._current_image_key = None
        window._refresh_image()
        window._set_status_text(f'Loaded session "{name}".')
        window._append_workflow_log(f"Session loaded | name={name}", level="success")

    def save_processing_state_for_dataset(self, *, force: bool = False, reason: str | None = None) -> None:
        window = self._window
        if window._processing_state_save_timer.isActive():
            window._processing_state_save_timer.stop()
        if window._roi_state_save_timer.isActive():
            window._roi_state_save_timer.stop()
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
        # Building the payloads (dataclass -> dict, analysis-cache arrays ->
        # lists) is cheap, synchronous, in-memory work, and must happen here
        # on the GUI thread since it reads window._state directly and
        # nothing else is going to mutate that concurrently. The expensive
        # part -- json.dumps + writing to disk, which is not free once the
        # analysis cache holds a few hundred spectra -- is dispatched to a
        # background worker below instead of blocking the GUI thread the way
        # it used to (this callback fires from a debounced QTimer after
        # nearly every ROI drag, mask stroke, or exclusion toggle).
        try:
            # image_tools_enabled reflects only the user's explicit Image tools
            # "applied" toggle - it is never changed just because the
            # rotate/crop/measure tool preview is open (see
            # ImageToolsController.unlink_for_preview), so there is nothing to
            # restore here before saving.
            preprocessing_to_save = window._state.preprocessing
            preprocessing_payload = build_preprocessing_payload(preprocessing_to_save)
            session_mask_payload = window._session_mask_payload()
            profile_payload = build_processing_profile_payload(
                preprocessing_to_save,
                window._state.area_roi_settings,
                window._state.area_rois,
                window._state.area_roi_groups,
                window._state.rois,
                window._state.chromatic_models,
                window._state.chromatic_landmarks,
                window._analysis_cache_payload(),
                session_mask=session_mask_payload,
                mask_settings=window._state.mask,
                image_exclusions=window._state.image_exclusions,
                area_roi_arrays=window._state.area_roi_arrays,
            )
        except Exception as exc:
            window._append_workflow_log(
                f"Processing state save failed | path={profile_path.name} | {exc}",
                level="error",
            )
            window.status_label.setText(f"Preprocessing save failed: {exc}")
            return

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

        pending = (path, preprocessing_payload, profile_path, profile_payload, signature)
        if window._processing_profile_write_busy:
            # A previous save is still writing to disk. This newest payload
            # already reflects every edit made so far -- including whatever
            # the in-flight write is about to persist -- so just replace
            # whatever was pending rather than starting a second, unordered
            # write that could finish before or after this one.
            window._processing_profile_write_pending = pending
            return
        self._start_processing_state_write(pending)

    def _start_processing_state_write(self, pending: tuple) -> None:
        window = self._window
        path, preprocessing_payload, profile_path, profile_payload, signature = pending
        window._processing_profile_write_busy = True
        worker = FunctionWorker(write_processing_state_files, path, preprocessing_payload, profile_path, profile_payload)
        worker.signals.result.connect(
            lambda _result, path=path, profile_path=profile_path, signature=signature: (
                self._on_processing_state_write_done(path, profile_path, signature)
            )
        )
        worker.signals.error.connect(
            lambda message, profile_path=profile_path: self._on_processing_state_write_failed(profile_path, message)
        )
        window._thread_pool.start(worker)

    def _on_processing_state_write_done(self, path: Path, profile_path: Path, signature: str) -> None:
        window = self._window
        window._last_saved_processing_signature = signature
        window._last_saved_preprocessing_path = path
        window._last_saved_profile_path = profile_path
        window._append_workflow_log_throttled(
            "processing_state_saved",
            f"Processing state saved | path={profile_path.name}",
            level="debug",
            min_interval=1.0,
        )
        self._finish_processing_state_write()

    def _on_processing_state_write_failed(self, profile_path: Path, message: str) -> None:
        window = self._window
        window._append_workflow_log(
            f"Processing state save failed | path={profile_path.name} | {message}",
            level="error",
        )
        window.status_label.setText(f"Preprocessing save failed: {message}")
        self._finish_processing_state_write()

    def _finish_processing_state_write(self) -> None:
        window = self._window
        window._processing_profile_write_busy = False
        pending = window._processing_profile_write_pending
        if pending is not None:
            window._processing_profile_write_pending = None
            self._start_processing_state_write(pending)

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
                window._state.area_roi_settings,
                window._state.area_rois,
                window._state.area_roi_groups,
                window._state.rois,
                window._state.chromatic_models,
                window._state.chromatic_landmarks,
                window._analysis_cache_payload(),
                session_mask=window._session_mask_payload(),
                mask_settings=window._state.mask,
                image_exclusions=window._state.image_exclusions,
                area_roi_arrays=window._state.area_roi_arrays,
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
                area_roi_settings,
                area_rois,
                area_roi_groups,
                rois,
                chromatic_models,
                chromatic_landmarks,
                analysis_cache,
                session_mask,
                mask_settings,
                image_exclusions,
                area_roi_arrays,
            ) = load_processing_profile(Path(source))
            window._state.preprocessing = preprocessing
            window._state.area_roi_settings = area_roi_settings
            window._state.area_rois = area_rois
            window._state.area_roi_groups = area_roi_groups
            window._state.area_roi_arrays = area_roi_arrays
            window._state.rois = rois
            window._reset_roi_id_counter_from_state()
            window._state.chromatic_models = chromatic_models
            window._state.chromatic_landmarks = chromatic_landmarks
            window._state.image_exclusions = image_exclusions
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
            window._update_roi_table()
            window._restore_control_preferences()
            window._update_chromatic_control_state()
            window._restore_chromatic_view_after_load()
            window._sync_reference_selection_from_settings()
            window._sync_image_processing_controls()
            window._sync_roi_detection_controls()
            window._refresh_image_exclusion_manage_dialog()
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
