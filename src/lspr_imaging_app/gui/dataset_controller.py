from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, MaskSettings
from lspr_imaging_app.io.dataset import (
    build_ome_zarr_export_folder_name,
    compare_ome_zarr_summaries,
    dataset_record_map,
    describe_new_ome_zarr_export,
    load_dataset,
    read_existing_ome_zarr_summary,
    sanitize_ome_zarr_export_name,
)


class DatasetController:
    def __init__(self, window) -> None:
        self.window = window

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.window, "Select dataset folder", self.window.folder_edit.text())
        if folder:
            self.load_dataset_from_folder(Path(folder), reset_image_view=True)

    def load_dataset_from_text(self, *, reset_image_view: bool = True) -> None:
        self.load_dataset_from_folder(Path(self.window.folder_edit.text()), reset_image_view=reset_image_view)

    def has_restorable_session(self, folder: Path) -> bool:
        if (folder / "processing_profile.json").exists():
            return True
        active_name = self.window._load_active_session_name_for_folder(folder)
        if active_name and active_name != "Default":
            return (folder / "sessions" / active_name / "processing_profile.json").exists()
        return False

    def run_startup_restore_flow(self) -> None:
        progress = getattr(self.window, "_report_startup_progress", None)
        if callable(progress):
            progress(20, "Checking startup preferences...")
        if getattr(self.window, "_fast_startup", False):
            if callable(progress):
                progress(100, "Fast startup enabled.")
            self.window._set_status_text("Fast startup enabled. Startup restore skipped.")
            return
        if self.window._state.dataset is not None:
            if callable(progress):
                progress(100, "Workspace already loaded.")
            return
        folder = Path(self.window.folder_edit.text())
        if not self.has_restorable_session(folder):
            if callable(progress):
                progress(100, "No previous session found.")
            self.window._set_status_text("Choose a dataset folder to begin.")
            return
        self.window._set_status_text("Restoring previous session...")
        if callable(progress):
            progress(30, "Loading previous session...")
        self.load_dataset_from_text(reset_image_view=False)

    def load_dataset_from_folder(self, folder: Path, *, reset_image_view: bool = True) -> None:
        progress = getattr(self.window, "_report_startup_progress", None)
        if self.window._state.dataset is not None:
            self.window._push_undo_point("Load dataset")
        self.window._leave_chromatic_setup_mode()
        self.window._begin_busy(f"Loading dataset from {folder.name}...")
        if callable(progress):
            progress(35, f"Loading dataset from {folder.name}...")
        try:
            dataset = load_dataset(folder)
        except Exception as exc:
            self.window._end_busy(f"Load failed: {exc}")
            QMessageBox.critical(self.window, "Load failed", str(exc))
            return

        self.window._state.dataset = dataset
        self.window._active_session_name = self.window._load_active_session_name_for_folder(dataset.folder)
        if callable(progress):
            progress(48, "Loading dataset records...")
        self.window._record_map = dataset_record_map(dataset)
        self.window._record_key_by_path = {record.path: (int(record.key.spectral_cube_index), float(record.key.wavelength_nm)) for record in dataset.records}
        self.window._spectral_cube_values = dataset.spectral_cube_indices
        self.window._wavelength_values = dataset.wavelengths_nm
        self.window._reference_contrast_cache.clear()
        self.window._current_record_path = None
        self.window._current_file_mask = None
        self.window._current_file_mask_path = None
        self.window._processed_image_cache.clear()
        self.window._processed_shape_cache.clear()
        self.window._invalidate_image_analysis_caches()
        self.window._invalidate_background_profile_cache()
        self.window._current_image_key = None
        self.window._force_image_autorange_after_load = bool(reset_image_view)
        self.window._sensorgram_cache.clear()
        self.window._sensorgram_running_signature = None
        self.window._pending_sensorgram_payload = None
        self.window._state.area_roi_settings = AreaRoiDetectionSettings()
        self.window._state.mask = MaskSettings()
        if callable(progress):
            progress(58, "Restoring processing profile...")
        self.window._load_processing_state_for_dataset()
        if callable(progress):
            progress(72, "Restoring analysis cache...")
        self.window._update_dataset_stack_indicator(dataset)
        self.window._sync_ome_zarr_chunk_controls()
        self.window._sync_image_processing_controls()

        self.window._configure_slider(self.window.spectral_cube_slider, len(self.window._spectral_cube_values))
        self.window._configure_slider(self.window.wavelength_slider, len(self.window._wavelength_values))
        self.window._configure_navigation_inputs()
        self.window._sync_reference_selection_from_settings()
        self.window._update_analysis_control_state()
        self.window.dataset_summary.setText(self.window._dataset_summary_text(dataset))

        self.window.folder_edit.setText(str(dataset.folder))
        self.window.folder_edit.setToolTip(str(dataset.folder))
        self.window._set_status_text(f"Loaded dataset from {folder.name}. Preparing the first image.")
        if callable(progress):
            progress(88, "Preparing the first image...")
        self.window._refresh_image()
        if callable(progress):
            progress(96, "Dataset ready.")

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
            text=dataset.folder.name,
        )
        if not name_ok:
            return
        name = sanitize_ome_zarr_export_name(name)

        default_parent = dataset.folder if dataset.folder.exists() else dataset.folder.parent
        if not default_parent.exists():
            default_parent = dataset.folder.parent if dataset.folder.parent.exists() else dataset.folder
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
