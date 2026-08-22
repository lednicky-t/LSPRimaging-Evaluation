from __future__ import annotations

import os
import shutil
import threading
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lspr_ui import APP_THEME
from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, ImageDataset, MaskSettings
from lspr_imaging_app.gui.analysis_tasks import _ome_zarr_export_task
from lspr_imaging_app.gui.ui_helpers import current_ome_zarr_compression_enabled
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.io.dataset import (
    DatasetLoadChoice,
    build_ome_zarr_export_folder_name,
    compare_ome_zarr_summaries,
    dataset_is_ome_zarr,
    dataset_record_map,
    describe_new_ome_zarr_export,
    load_dataset,
    read_existing_ome_zarr_summary,
    resolve_remembered_dataset_choice,
    sanitize_ome_zarr_export_name,
    save_dataset_choice,
    summarize_dataset_candidate,
)


class DatasetController:
    def __init__(self, window) -> None:
        self.window = window

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.window, "Select dataset folder", self.window.folder_edit.text())
        if folder:
            self.load_dataset_from_folder(Path(folder), reset_image_view=True)

    def load_dataset_from_text(self, *, reset_image_view: bool = True, on_done=None) -> None:
        self.load_dataset_from_folder(Path(self.window.folder_edit.text()), reset_image_view=reset_image_view, on_done=on_done)

    def open_dataset_folder_in_explorer(self) -> None:
        folder = Path(self.window.folder_edit.text())
        if not folder.is_dir():
            self.window._set_status_text(f"Cannot open folder - path does not exist: {folder}")
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self.window._set_status_text(f"Could not open folder in File Explorer: {exc}")

    def has_restorable_session(self, folder: Path) -> bool:
        active_name = self.window._load_active_session_name_for_folder(folder)
        if active_name and active_name != "Default":
            return (
                (folder / "analysis" / "sessions" / active_name / "processing_profile.json").exists()
                or (folder / "sessions" / active_name / "processing_profile.json").exists()
            )
        return (
            (folder / "analysis" / "processing_profile.json").exists()
            or (folder / "processing_profile.json").exists()
        )

    def run_startup_restore_flow(self, *, on_done=None) -> None:
        progress = getattr(self.window, "_report_startup_progress", None)
        if callable(progress):
            progress(20, "Checking startup preferences...")
        if getattr(self.window, "_fast_startup", False):
            if callable(progress):
                progress(100, "Fast startup enabled.")
            self.window._set_status_text("Fast startup enabled. Startup restore skipped.")
            if on_done is not None:
                on_done()
            return
        if self.window._state.dataset is not None:
            if callable(progress):
                progress(100, "Workspace already loaded.")
            if on_done is not None:
                on_done()
            return
        folder = Path(self.window.folder_edit.text())
        if not self.has_restorable_session(folder):
            if callable(progress):
                progress(100, "No previous session found.")
            self.window._set_status_text("Choose a dataset folder to begin.")
            if on_done is not None:
                on_done()
            return
        self.window._set_status_text("Restoring previous session...")
        if callable(progress):
            progress(30, "Loading previous session...")
        self.load_dataset_from_text(reset_image_view=False, on_done=on_done)

    def load_dataset_from_folder(self, folder: Path, *, reset_image_view: bool = True, on_done=None) -> None:
        window = self.window
        if window._dataset_load_in_flight:
            window._set_status_text("A dataset is already loading - please wait for it to finish.")
            if on_done is not None:
                on_done()
            return
        progress = getattr(window, "_report_startup_progress", None)
        if window._state.dataset is not None:
            window._push_undo_point("Load dataset")
            # Flush whatever's still pending for the outgoing dataset before
            # its state gets replaced below - mirrors the pre-switch flush
            # in SessionStateManager.load_session.
            window._save_processing_state_for_dataset(force=True, reason="switch dataset")
        window._leave_chromatic_setup_mode()
        window._dataset_load_in_flight = True
        self._set_dataset_load_controls_enabled(False)
        window._begin_busy(f"Loading dataset from {folder.name}...")
        if callable(progress):
            progress(35, f"Loading dataset from {folder.name}...")
        worker = FunctionWorker(load_dataset, folder)
        worker.signals.result.connect(
            lambda dataset, folder=folder, reset_image_view=reset_image_view, on_done=on_done:
                self._on_dataset_loaded(folder, reset_image_view, on_done, dataset)
        )
        worker.signals.error.connect(
            lambda message, on_done=on_done: self._on_dataset_load_failed(on_done, message)
        )
        window._thread_pool.start(worker)

    def _set_dataset_load_controls_enabled(self, enabled: bool) -> None:
        window = self.window
        window.browse_button.setEnabled(enabled)

    def _on_dataset_load_failed(self, on_done, message: str) -> None:
        window = self.window
        window._dataset_load_in_flight = False
        self._set_dataset_load_controls_enabled(True)
        window._end_busy(f"Load failed: {message}")
        QMessageBox.critical(window, "Load failed", message)
        if on_done is not None:
            on_done()

    def _prompt_dataset_candidate_choice(self, choice: DatasetLoadChoice) -> ImageDataset | None:
        """Ask the user which dataset to load when `load_dataset` found more
        than one TIFF-stack/OME-Zarr candidate one level under the folder
        they pointed at (see `discover_dataset_candidates`). Mirrors the
        button-per-option `QMessageBox` style already used by
        `_resolve_ome_zarr_destination_collision` for a similar choice.
        Returns the chosen candidate (with `choice.acquisition_metadata`
        attached if found - the candidate's own embedded metadata, if any,
        is kept otherwise) or None if the user cancelled.
        """
        window = self.window
        format_labels = {"image_stack": "TIFF image stack", "ome_zarr": "OME-Zarr"}
        lines = [f"Found {len(choice.candidates)} datasets under {choice.parent_folder}:"]
        for candidate in choice.candidates:
            summary = summarize_dataset_candidate(candidate)
            format_label = format_labels.get(summary.source_format, summary.source_format)
            lines.append(
                f"\n{candidate.folder.name}/  ({format_label})\n"
                f"    {summary.image_count} images, {summary.spectral_cube_count} cubes x "
                f"{summary.wavelength_count} wavelengths, {window._format_dataset_bytes(summary.total_bytes)}"
            )
        box = QMessageBox(window)
        box.setWindowTitle("Choose a dataset to load")
        box.setText("\n".join(lines) + "\n\nWhich one should be loaded?")
        candidate_by_button = {
            box.addButton(candidate.folder.name, QMessageBox.ButtonRole.AcceptRole): candidate
            for candidate in choice.candidates
        }
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        dataset = candidate_by_button.get(box.clickedButton())
        if dataset is not None:
            if choice.acquisition_metadata is not None:
                dataset.acquisition_metadata = choice.acquisition_metadata
            dataset.home_folder = choice.parent_folder
            if getattr(window, "_remember_dataset_format_choice", lambda: True)():
                save_dataset_choice(choice.parent_folder, dataset.folder.name)
        return dataset

    def _on_dataset_loaded(self, folder: Path, reset_image_view: bool, on_done, result) -> None:
        window = self.window
        if isinstance(result, DatasetLoadChoice):
            dataset = None
            if getattr(window, "_remember_dataset_format_choice", lambda: True)():
                dataset = resolve_remembered_dataset_choice(result)
                if dataset is not None:
                    if result.acquisition_metadata is not None:
                        dataset.acquisition_metadata = result.acquisition_metadata
                    dataset.home_folder = result.parent_folder
            if dataset is None:
                dataset = self._prompt_dataset_candidate_choice(result)
            if dataset is None:
                window._dataset_load_in_flight = False
                self._set_dataset_load_controls_enabled(True)
                window._end_busy("Load cancelled.")
                if on_done is not None:
                    on_done()
                return
        else:
            dataset = result
        progress = getattr(window, "_report_startup_progress", None)

        window._state.dataset = dataset
        window._active_session_name = window._load_active_session_name_for_folder(dataset.home)
        if callable(progress):
            progress(48, "Loading dataset records...")
        window._record_map = dataset_record_map(dataset)
        window._record_key_by_path = {record.path: (int(record.key.spectral_cube_index), float(record.key.wavelength_nm)) for record in dataset.records}
        window._spectral_cube_values = dataset.spectral_cube_indices
        window._wavelength_values = dataset.wavelengths_nm
        window._reference_contrast_cache.clear()
        window._current_record_path = None
        window._current_file_mask = None
        window._current_file_mask_path = None
        window._clear_processed_image_cache()
        window._processed_shape_cache.clear()
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._current_image_key = None
        window._force_image_autorange_after_load = bool(reset_image_view)
        window._sensorgram_cache.clear()
        window._sensorgram_running_signature = None
        window._pending_sensorgram_payload = None
        window._state.area_roi_settings = AreaRoiDetectionSettings()
        window._state.mask = MaskSettings()
        if callable(progress):
            progress(58, "Restoring processing profile...")
        window._load_processing_state_for_dataset(
            on_done=lambda: self._finish_load_dataset_from_folder(folder, on_done)
        )

    def _finish_load_dataset_from_folder(self, folder: Path, on_done) -> None:
        window = self.window
        progress = getattr(window, "_report_startup_progress", None)
        dataset = window._state.dataset
        if callable(progress):
            progress(72, "Restoring analysis cache...")
        self._update_dataset_stack_indicator(dataset)
        self._sync_ome_zarr_chunk_controls()
        window._sync_image_processing_controls()

        window._configure_slider(window.spectral_cube_slider, len(window._spectral_cube_values))
        window._configure_slider(window.wavelength_slider, len(window._wavelength_values))
        window._configure_navigation_inputs()
        window._sync_reference_selection_from_settings()
        window._update_analysis_control_state()
        window._update_dataset_summary_labels(dataset)
        window._update_metadata_status_labels(dataset)

        window.folder_edit.setText(str(dataset.home))
        window.folder_edit.setToolTip(
            str(dataset.home) if dataset.home == dataset.folder
            else f"{dataset.home}\n(images/OME-Zarr found in: {dataset.folder})"
        )
        window._set_status_text(f"Loaded dataset from {folder.name}. Preparing the first image.")
        if callable(progress):
            progress(88, "Preparing the first image...")
        window._refresh_image()
        if callable(progress):
            progress(96, "Dataset ready.")
        window._dataset_load_in_flight = False
        self._set_dataset_load_controls_enabled(True)
        window._end_busy()
        if on_done is not None:
            on_done()

    def export_current_dataset_to_ome_zarr(self) -> None:
        dataset = self.window._state.dataset
        if dataset is None:
            self.window._set_status_text("Load a dataset before exporting Stack to Zarr.")
            return
        try:
            chunk_size_px = int(self._current_ome_zarr_chunk_size())
            compression_enabled = bool(self._current_ome_zarr_compression_enabled())
            shard_mode = str(self._current_ome_zarr_shard_mode())
            skip_excluded_images = bool(self._current_ome_zarr_skip_excluded())
        except Exception as exc:
            QMessageBox.critical(self.window, "Stack to Zarr export failed", str(exc))
            self.window._set_status_text(f"Stack to Zarr export failed: {exc}")
            return

        name, name_ok = QInputDialog.getText(
            self.window,
            "Name this Stack to Zarr export",
            "Export name (used to build the destination folder name):",
            text=dataset.home.name,
        )
        if not name_ok:
            return
        name = sanitize_ome_zarr_export_name(name)

        # Default to the dataset's home folder (not the raw TIFF/OME-Zarr
        # folder itself) so a multi-GB zarr export doesn't land nested inside
        # it by default - the user can still browse elsewhere.
        default_parent = dataset.home if dataset.home.exists() else dataset.folder
        parent_dir = QFileDialog.getExistingDirectory(
            self.window,
            "Choose export location for Stack to Zarr",
            str(default_parent),
        )
        if not parent_dir:
            return

        preprocessing = self.window._state.preprocessing
        try:
            new_summary = describe_new_ome_zarr_export(
                dataset, preprocessing,
                chunk_size_px=chunk_size_px, compression_enabled=compression_enabled, shard_mode=shard_mode,
            )
        except Exception as exc:
            QMessageBox.critical(self.window, "Stack to Zarr export failed", str(exc))
            self.window._set_status_text(f"Stack to Zarr export failed: {exc}")
            return
        folder_name = build_ome_zarr_export_folder_name(
            name,
            width=new_summary.width, height=new_summary.height,
            spectral_cube_count=new_summary.spectral_cube_count, wavelength_count=new_summary.wavelength_count,
            chunk_size_px=new_summary.chunk_size_px, shard_mode=new_summary.shard_mode,
            compression_enabled=new_summary.compression_enabled, dtype=new_summary.dtype_str,
        )
        destination = Path(parent_dir) / folder_name

        destination = self._resolve_ome_zarr_destination_collision(destination, new_summary)
        if destination is None:
            return

        summary = self._describe_ome_zarr_export_plan(destination, chunk_size_px, compression_enabled, shard_mode)
        confirm = QMessageBox.question(
            self.window,
            "Confirm Stack to Zarr export",
            summary,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._start_ome_zarr_export(
            destination,
            chunk_size_px,
            compression_enabled=compression_enabled,
            shard_mode=shard_mode,
            skip_excluded_images=skip_excluded_images,
        )

    def _resolve_ome_zarr_destination_collision(self, destination: Path, new_summary) -> Path | None:
        """If `destination` already exists, compare its saved parameters against
        the prospective export and ask the user how to proceed. Returns the
        (possibly adjusted, e.g. auto-suffixed) destination to use, or None if
        the export should be cancelled.
        """
        while destination.exists():
            existing_summary = read_existing_ome_zarr_summary(destination)
            if existing_summary is None:
                confirm = QMessageBox.question(
                    self.window,
                    "Folder already exists",
                    f"{destination.name} already exists and doesn't look like a previous "
                    "OME-Zarr export from this app. Overwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                return destination if confirm == QMessageBox.StandardButton.Yes else None

            all_match, rows = compare_ome_zarr_summaries(existing_summary, new_summary)
            if all_match:
                confirm = QMessageBox.question(
                    self.window,
                    "Identical export already exists",
                    f"An OME-Zarr export with exactly these parameters already exists at:\n{destination}\n\nOverwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                return destination if confirm == QMessageBox.StandardButton.Yes else None

            table_lines = [f"{'Field':<24} {'Existing':<32} {'New'}"]
            table_lines.append("-" * 80)
            for label, existing_value, new_value in rows:
                marker = "  " if existing_value == new_value else "* "
                table_lines.append(f"{marker}{label:<22} {existing_value:<32} {new_value}")
            message = (
                f"A different OME-Zarr export already exists at:\n{destination}\n\n"
                + "\n".join(table_lines)
                + "\n\nAdd this as a new, separately-named export; replace the existing one; or cancel?"
            )
            box = QMessageBox(self.window)
            box.setWindowTitle("Existing export has different parameters")
            box.setText(message)
            add_button = box.addButton("Add as new", QMessageBox.ButtonRole.AcceptRole)
            replace_button = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(add_button)
            box.exec()
            clicked = box.clickedButton()
            if clicked is replace_button:
                return destination
            if clicked is add_button:
                destination = self._next_available_ome_zarr_path(destination)
                continue
            return None
        return destination

    @staticmethod
    def _next_available_ome_zarr_path(destination: Path) -> Path:
        suffix = ".ome.zarr"
        stem = destination.name[: -len(suffix)] if destination.name.endswith(suffix) else destination.stem
        counter = 2
        while True:
            candidate = destination.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _describe_ome_zarr_export_plan(self, destination: Path, chunk_size_px: int, compression_enabled: bool, shard_mode: str = "per_image") -> str:
        preprocessing = self.window._state.preprocessing
        tools_applied = bool(getattr(preprocessing, "image_tools_enabled", False))
        lines = [f"Destination: {destination}", ""]
        if tools_applied:
            lines.append("Image tools: APPLIED (linked) — exported pixels will be transformed.")
            angle = float(preprocessing.rotation_angle_deg)
            fill = "dark (0)" if bool(preprocessing.rotation_fill_dark) else "edge-stretch"
            lines.append(f"  - Rotation: {angle:.2f} deg, new-pixel fill = {fill}" if abs(angle) > 1e-9 else "  - Rotation: none")
            flips = []
            if preprocessing.flip_horizontal:
                flips.append("horizontal")
            if preprocessing.flip_vertical:
                flips.append("vertical")
            lines.append(f"  - Flip: {', '.join(flips) if flips else 'none'}")
            crop = preprocessing.crop
            if crop.enabled and crop.width > 0 and crop.height > 0:
                lines.append(f"  - Crop: x={crop.x}, y={crop.y}, width={crop.width}, height={crop.height}")
            else:
                lines.append("  - Crop: none")
            if bool(getattr(preprocessing, "calibration_enabled", False)):
                lines.append(
                    f"  - Pixel size metadata: {float(preprocessing.microns_per_pixel_x):.4f} um/px (X) x "
                    f"{float(preprocessing.microns_per_pixel_y):.4f} um/px (Y) will be embedded."
                )
            else:
                lines.append("  - Pixel size metadata: none (calibration is not enabled).")
        else:
            lines.append("Image tools: NOT applied — raw, untransformed source pixels will be exported.")
            lines.append("  - No pixel size metadata will be embedded (requires image tools applied + calibration enabled).")
        lines.append("")
        lines.append(f"Chunk tile: {int(chunk_size_px)} px")
        shard_label = "1 image per file" if shard_mode == "per_image" else "1 spectral cube per file (all wavelengths)"
        lines.append(f"Shard: {shard_label}")
        lines.append(f"Compression: {'lz4 + bitshuffle (on)' if compression_enabled else 'none (off)'}")
        excluded_count = len(self.window._state.image_exclusions)
        if self._current_ome_zarr_skip_excluded():
            lines.append(f"Skip excluded images: ON — {excluded_count} exclusion rule(s) will be left empty (no pixel data) in the export.")
        else:
            lines.append(f"Skip excluded images: off — all images will be exported, including {excluded_count} currently-excluded rule(s).")
        lines.append("")
        lines.append("Proceed with export?")
        return "\n".join(lines)

    def _update_dataset_stack_indicator(self, dataset=None) -> None:
        # Compact title-row indicator next to the "Dataset" section header -
        # shows "not loaded" rather than defaulting to the ImageStack look
        # when nothing is loaded.
        window = self.window
        dataset = window._state.dataset if dataset is None else dataset
        ome_zarr = dataset_is_ome_zarr(dataset)
        if dataset is None:
            window.dataset_type_icon.setPixmap(window._not_available_icon(size=16).pixmap(16, 16))
            window.dataset_type_label.setText("not loaded")
        else:
            window.dataset_type_icon.setPixmap(window._dataset_stack_icon_pixmap(16, ome_zarr=ome_zarr))
            window.dataset_type_label.setText("OME-Zarr" if ome_zarr else "ImageStack")

    def clear_dataset(self) -> None:
        # Inverse of _on_dataset_loaded/_finish_load_dataset_from_folder -
        # drops the dataset (and its acquisition metadata) back to the
        # pre-load state so it can be garbage collected without restarting
        # the app. Nothing on disk is touched.
        window = self.window
        window._state.dataset = None
        window._record_map = {}
        window._record_key_by_path = {}
        window._spectral_cube_values = []
        window._wavelength_values = []
        window._reference_contrast_cache.clear()
        window._current_record_path = None
        window._current_file_mask = None
        window._current_file_mask_path = None
        window._current_file_mask_session_source_path = None
        window._clear_processed_image_cache()
        window._processed_shape_cache.clear()
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._current_image_key = None
        window._sensorgram_cache.clear()
        window._sensorgram_running_signature = None
        window._pending_sensorgram_payload = None
        window.image_item.clear()
        self._update_dataset_stack_indicator(None)
        self._sync_ome_zarr_chunk_controls()
        window._configure_slider(window.spectral_cube_slider, 0)
        window._configure_slider(window.wavelength_slider, 0)
        window._configure_navigation_inputs()
        window._sync_image_processing_controls()
        window._update_analysis_control_state()
        window._update_dataset_summary_labels(None)
        window._update_metadata_status_labels(None)
        window._set_status_text("Dataset cleared from memory.")
        window._append_workflow_log("Dataset cleared from memory", level="success")

    def _current_ome_zarr_chunk_size(self) -> int:
        return max(int(self.window.ome_zarr_chunk_spin.value()), 4)

    def _current_ome_zarr_compression_enabled(self) -> bool:
        return current_ome_zarr_compression_enabled(self.window.ome_zarr_compression_button.isChecked())

    def _current_ome_zarr_skip_excluded(self) -> bool:
        return bool(self.window.ome_zarr_skip_excluded_button.isChecked())

    def _on_ome_zarr_skip_excluded_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            excluded_count = len(window._state.image_exclusions)
            if excluded_count == 0:
                confirm = QMessageBox.StandardButton.Yes
            else:
                confirm = QMessageBox.question(
                    window,
                    "Skip excluded images in export",
                    f"{excluded_count} exclusion rule(s) are set. Exported planes they cover will be "
                    "left empty (no pixel data) instead of containing real measurements.\n\n"
                    "This cannot be undone once the export is written. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            if confirm != QMessageBox.StandardButton.Yes:
                window.ome_zarr_skip_excluded_button.blockSignals(True)
                window.ome_zarr_skip_excluded_button.setChecked(False)
                window.ome_zarr_skip_excluded_button.blockSignals(False)
                checked = False
        color = "#ef4444" if checked else "#94a3b8"
        window.ome_zarr_skip_excluded_button.setIcon(window._mask_panel_icon("alert-triangle", color=color, size=APP_THEME.icon_button_inner))

    def _sync_ome_zarr_chunk_controls(self) -> None:
        self.window._ui_state_manager.sync_ome_zarr_chunk_controls()

    def _current_ome_zarr_shard_mode(self) -> str:
        return str(self.window.ome_zarr_shard_mode_combo.currentData() or "per_image")

    def _on_ome_zarr_chunk_size_changed(self, _value: int) -> None:
        self._sync_ome_zarr_chunk_controls()
        self.window._save_layout_preferences()

    def _on_ome_zarr_shard_mode_changed(self, _index: int) -> None:
        window = self.window
        window._settings.setValue("ome_zarr/shard_mode", window.ome_zarr_shard_mode_combo.currentData())
        window._save_layout_preferences()

    def _on_ome_zarr_chunk_guide_toggled(self, checked: bool) -> None:
        window = self.window
        window.ome_zarr_chunk_guide_button.setIcon(window._ome_zarr_grid_icon(bool(checked)))
        window._update_ome_zarr_chunk_guide_overlay()

    def _on_ome_zarr_compression_toggled(self, checked: bool) -> None:
        window = self.window
        window.ome_zarr_compression_button.setIcon(window._ome_zarr_compression_icon(bool(checked)))
        self._sync_ome_zarr_chunk_controls()

    @staticmethod
    def _folder_size_bytes(path: Path) -> int:
        total = 0
        if not path.exists():
            return total
        if path.is_file():
            try:
                return int(path.stat().st_size)
            except Exception:
                return total
        for root, _dirs, files in os.walk(path):
            for name in files:
                file_path = Path(root) / name
                try:
                    total += int(file_path.stat().st_size)
                except Exception:
                    continue
        return total

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        value = float(max(int(num_bytes), 0))
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(int(round(float(seconds))), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _set_ome_zarr_export_ui_running(self, running: bool) -> None:
        window = self.window
        window._ome_zarr_export_running = bool(running)
        window.dataset_ome_zarr_export_progress_row.setVisible(bool(running))
        window.dataset_ome_zarr_export_progress_bar.setValue(0 if running else 0)
        window.dataset_ome_zarr_export_progress_bar.setFormat("%p%")
        window.dataset_ome_zarr_export_eta_label.setText("ETA: --:--")
        window.dataset_ome_zarr_export_stop_button.setVisible(bool(running))
        window.dataset_ome_zarr_export_stop_button.setEnabled(bool(running))
        window.dataset_ome_zarr_export_button.setEnabled(not running and window._state.dataset is not None)
        window.ome_zarr_chunk_spin.setEnabled(not running)
        window.ome_zarr_chunk_guide_button.setEnabled(not running)
        window.ome_zarr_shard_mode_combo.setEnabled(not running)
        window.ome_zarr_compression_button.setEnabled(not running)
        window.dataset_ome_zarr_controls_row.setEnabled(not running)
        window.dataset_ome_zarr_options_row.setEnabled(not running)
        if running:
            window.dataset_ome_zarr_export_status_label.setText("Progress")
        self._sync_ome_zarr_chunk_controls()

    def _start_ome_zarr_export(
        self,
        destination: Path,
        chunk_size_px: int,
        *,
        compression_enabled: bool = True,
        shard_mode: str = "per_image",
        skip_excluded_images: bool = False,
    ) -> None:
        window = self.window
        dataset = window._state.dataset
        if dataset is None:
            window._set_status_text("Load a dataset before exporting Stack to Zarr.")
            return
        if window._ome_zarr_export_running:
            window._set_status_text("Stack to Zarr export is already running.")
            return
        # Snapshot preprocessing now so later edits in the GUI (while export runs
        # in the background) can't change the settings mid-export.
        preprocessing_snapshot = deepcopy(window._state.preprocessing)
        window._ome_zarr_export_request_id += 1
        request_id = window._ome_zarr_export_request_id
        window._ome_zarr_export_cancel_event = threading.Event()
        window._ome_zarr_export_destination = destination
        window._ome_zarr_export_started_at = time.perf_counter()
        self._set_ome_zarr_export_ui_running(True)
        window._set_status_text(f"Exporting Stack to Zarr to {destination.name}...")
        window._begin_busy("Exporting Stack to Zarr...", determinate=True)
        excluded_rules_snapshot = deepcopy(window._state.image_exclusions) if skip_excluded_images else None
        adaptive_enabled = window._ome_zarr_adaptive_enabled()
        adaptive_batch_mb = window._ome_zarr_adaptive_batch_mb()
        worker = FunctionWorker(
            _ome_zarr_export_task,
            dataset,
            destination,
            int(chunk_size_px),
            bool(compression_enabled),
            preprocessing_snapshot,
            shard_mode,
            excluded_rules=excluded_rules_snapshot,
            skip_excluded=bool(skip_excluded_images),
            cancel_event=window._ome_zarr_export_cancel_event,
            adaptive_workers_enabled=adaptive_enabled,
            adaptive_batch_mb=adaptive_batch_mb,
            supports_progress=True,
        )
        tools_text = "applied" if bool(getattr(preprocessing_snapshot, "image_tools_enabled", False)) else "ignored"
        shard_text = "per_spectral_cube" if shard_mode == "per_spectral_cube" else "per_image"
        excluded_text = f"on ({len(excluded_rules_snapshot)} rules)" if skip_excluded_images else "off"
        adaptive_text = f"on ({adaptive_batch_mb}MB)" if adaptive_enabled else "off"
        window._append_workflow_log(
            f"OME-Zarr export start | chunks {chunk_size_px}px | shard {shard_text} | compression {'on' if compression_enabled else 'off'} | image tools {tools_text} | skip excluded {excluded_text} | adaptive tuning {adaptive_text}",
            level="info",
        )
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.progress.connect(
            lambda percent, text, request_id=request_id: self._on_ome_zarr_export_progress(request_id, percent, text)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self._on_ome_zarr_export_finished(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_ome_zarr_export_failed(request_id, message))
        # Run in a dedicated daemon thread instead of QThreadPool so the export is
        # fully independent of the GUI thread pool (Qt tasks like image loading keep
        # their pool slots) and Qt signals are delivered via queued connections.
        window._ome_zarr_export_thread = threading.Thread(target=worker.run, daemon=True, name="ome-zarr-export")
        window._ome_zarr_export_thread.start()

    def _stop_ome_zarr_export(self) -> None:
        window = self.window
        if not window._ome_zarr_export_running or window._ome_zarr_export_cancel_event is None:
            return
        window._ome_zarr_export_cancel_event.set()
        window._set_status_text("Stopping Stack to Zarr export...")
        window.dataset_ome_zarr_export_eta_label.setText("ETA: stopping...")

    def _show_ome_zarr_adaptive_tuning_info(self) -> None:
        QMessageBox.information(
            self.window,
            "Adaptive worker tuning",
            "Stack-to-Zarr export writes many shard files in parallel using several "
            "worker processes — normally one per CPU core, since compressing image "
            "data is CPU-intensive.\n\n"
            "Adaptive worker tuning periodically checks, using real timing from the "
            "export itself, whether time is being spent waiting on the disk (reading "
            "source images, writing shard files) or on compression (CPU work). If "
            "it's mostly waiting on disk — common with slower drives, network "
            "drives, or antivirus scanning every new file — it adds a few extra "
            "worker processes so the CPU stays busy while others wait. On a fast "
            "local SSD, it typically makes no change.\n\n"
            "Checks happen after a configurable amount of data has been processed "
            "(default 1 GB, see \"Sample size for tuning decisions\"). A larger "
            "sample avoids mistaking a fast drive's temporary write-cache burst for "
            "its true sustained speed.\n\n"
            "Turn this off for a fixed, predictable worker count (equal to your "
            "CPU's core count) on every export — useful when comparing export times.",
        )

    def _on_ome_zarr_export_progress(self, request_id: int, percent: int, text: str) -> None:
        window = self.window
        if request_id != window._ome_zarr_export_request_id or not window._ome_zarr_export_running:
            return
        if text.startswith("ADAPTIVE_FLIP: "):
            # Internal worker-rebalancing decision, not an operator-actionable
            # event (the export keeps running the same either way) - the
            # user-facing summary of what adaptive tuning did overall still
            # comes through _finish_ome_zarr_export's start/done messages.
            window._append_workflow_log(text.removeprefix("ADAPTIVE_FLIP: "), level="debug")
            return
        current_percent = int(np.clip(percent, 0, 100))
        window.dataset_ome_zarr_export_progress_bar.setValue(current_percent)
        window.dataset_ome_zarr_export_progress_bar.setFormat(f"{current_percent}%")
        eta_text = "ETA: --:--"
        if window._ome_zarr_export_started_at is not None and current_percent > 0:
            elapsed = max(time.perf_counter() - window._ome_zarr_export_started_at, 1e-6)
            remaining = max((elapsed * (100.0 - current_percent)) / max(float(current_percent), 1.0), 0.0)
            eta_text = f"ETA: {window._format_elapsed_seconds(remaining) or '0:00'}"
        window.dataset_ome_zarr_export_eta_label.setText(eta_text)
        window._set_status_text(text)

    def _finish_ome_zarr_export(self, request_id: int, message: str | None = None, *, failed: bool = False) -> None:
        window = self.window
        if request_id != window._ome_zarr_export_request_id:
            return
        elapsed_text = ""
        if window._ome_zarr_export_started_at is not None:
            elapsed_text = f" in {self._format_duration(time.perf_counter() - window._ome_zarr_export_started_at)}"
        window._ome_zarr_export_running = False
        window._end_busy()
        window._ome_zarr_export_started_at = None
        window._ome_zarr_export_cancel_event = None
        window.dataset_ome_zarr_export_progress_bar.setValue(0)
        window.dataset_ome_zarr_export_progress_bar.setFormat("%p%")
        window.dataset_ome_zarr_export_eta_label.setText("ETA: --:--")
        window.dataset_ome_zarr_export_progress_row.hide()
        window.dataset_ome_zarr_export_stop_button.setVisible(False)
        window.dataset_ome_zarr_export_stop_button.setEnabled(False)
        window.dataset_ome_zarr_export_button.setEnabled(window._state.dataset is not None)
        window.ome_zarr_chunk_spin.setEnabled(True)
        window.ome_zarr_chunk_guide_button.setEnabled(True)
        window.ome_zarr_shard_mode_combo.setEnabled(True)
        window.ome_zarr_compression_button.setEnabled(True)
        window.dataset_ome_zarr_controls_row.setEnabled(True)
        window.dataset_ome_zarr_options_row.setEnabled(True)
        self._sync_ome_zarr_chunk_controls()
        window._append_workflow_log(
            f"OME-Zarr export {'failed' if failed else 'done'}{elapsed_text}",
            level="warning" if failed else "success",
        )
        if message:
            window._set_status_text(f"{message}{elapsed_text}")
        if failed and window._ome_zarr_export_destination is not None:
            try:
                if window._ome_zarr_export_destination.exists():
                    shutil.rmtree(window._ome_zarr_export_destination, ignore_errors=True)
            except Exception:
                pass
        window._ome_zarr_export_destination = None

    def _on_ome_zarr_export_finished(self, request_id: int, result: Path) -> None:
        window = self.window
        if request_id != window._ome_zarr_export_request_id:
            return
        destination = Path(result)
        size_text = self._format_bytes(self._folder_size_bytes(destination))
        self._finish_ome_zarr_export(request_id, f"Done. Exported {destination.name} ({size_text}).")

    def _on_ome_zarr_export_failed(self, request_id: int, message: str) -> None:
        window = self.window
        if request_id != window._ome_zarr_export_request_id:
            return
        if "cancelled" in message.lower():
            self._finish_ome_zarr_export(request_id, "Stack to Zarr export cancelled.", failed=True)
            return
        self._finish_ome_zarr_export(request_id, f"Stack to Zarr export failed: {message}", failed=True)
        QMessageBox.critical(window, "Stack to Zarr export failed", message)
