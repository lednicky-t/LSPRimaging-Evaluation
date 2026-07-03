from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, MaskSettings
from lspr_imaging_app.io.dataset import dataset_record_map, load_dataset


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
        return (folder / "processing_profile.json").exists()

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
        if callable(progress):
            progress(48, "Loading dataset records...")
        self.window._record_map = dataset_record_map(dataset)
        self.window._record_key_by_path = {record.path: (int(record.key.frame_index), float(record.key.wavelength_nm)) for record in dataset.records}
        self.window._frame_values = dataset.frame_indices
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

        self.window._configure_slider(self.window.frame_slider, len(self.window._frame_values))
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
        destination = Path(parent_dir) / f"{dataset.folder.name}.ome.zarr"
        if destination.exists():
            confirm = QMessageBox.question(
                self.window,
                "Overwrite Stack to Zarr export?",
                f"{destination.name} already exists. Replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        try:
            chunk_size_px = int(self.window._current_ome_zarr_chunk_size())
            compression_enabled = bool(self.window._current_ome_zarr_compression_enabled())
            shard_mode = str(self.window._current_ome_zarr_shard_mode())
        except Exception as exc:
            QMessageBox.critical(self.window, "Stack to Zarr export failed", str(exc))
            self.window._set_status_text(f"Stack to Zarr export failed: {exc}")
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
        self.window._start_ome_zarr_export(destination, chunk_size_px, compression_enabled=compression_enabled, shard_mode=shard_mode)

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
        shard_label = "1 image per file" if shard_mode == "per_image" else "1 frame per file (all wavelengths)"
        lines.append(f"Shard: {shard_label}")
        lines.append(f"Compression: {'lz4 + bitshuffle (on)' if compression_enabled else 'none (off)'}")
        lines.append("")
        lines.append("Proceed with export?")
        return "\n".join(lines)
