from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, ImageDataset, MaskSettings
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.io.dataset import (
    DatasetLoadChoice,
    build_ome_zarr_export_folder_name,
    compare_ome_zarr_summaries,
    dataset_record_map,
    describe_new_ome_zarr_export,
    load_dataset,
    read_existing_ome_zarr_summary,
    sanitize_ome_zarr_export_name,
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
        return dataset

    def _on_dataset_loaded(self, folder: Path, reset_image_view: bool, on_done, result) -> None:
        window = self.window
        if isinstance(result, DatasetLoadChoice):
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
        window._processed_image_cache.clear()
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
        window._update_dataset_stack_indicator(dataset)
        window._sync_ome_zarr_chunk_controls()
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
            chunk_size_px = int(self.window._current_ome_zarr_chunk_size())
            compression_enabled = bool(self.window._current_ome_zarr_compression_enabled())
            shard_mode = str(self.window._current_ome_zarr_shard_mode())
            skip_excluded_images = bool(self.window._current_ome_zarr_skip_excluded())
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
        self.window._start_ome_zarr_export(
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
        if self.window._current_ome_zarr_skip_excluded():
            lines.append(f"Skip excluded images: ON — {excluded_count} exclusion rule(s) will be left empty (no pixel data) in the export.")
        else:
            lines.append(f"Skip excluded images: off — all images will be exported, including {excluded_count} currently-excluded rule(s).")
        lines.append("")
        lines.append("Proceed with export?")
        return "\n".join(lines)
