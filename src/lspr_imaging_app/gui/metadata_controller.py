from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_imaging_app.io.dataset import acquisition_metadata_sidecar_path
from lspr_imaging_app.io.metadata_import import import_metadata_files
from lspr_imaging_app.storage.workspace import save_acquisition_metadata_sidecar


class MetadataController:
    def __init__(self, window) -> None:
        self.window = window

    def _dataset_folder(self) -> Path | None:
        return self.window._dataset_folder_path() if hasattr(self.window, "_dataset_folder_path") else None

    def import_metadata(self) -> None:
        """Import acquisition metadata from one or more files, identified by
        content (see `io/metadata_import.py`'s classifier) rather than by
        filename - any mix of a legacy measuring-times CSV, metaData.txt, a
        native measurement file, or a previously exported metadata JSON, in
        one file dialog. Fully replaces whatever metadata is currently
        loaded and saves it as this dataset's `analysis/acquisition_
        metadata.json` sidecar so it's still there on the next load.
        """
        folder = self._dataset_folder()
        if folder is None:
            self.window.status_label.setText("Load a dataset before importing acquisition metadata.")
            return
        paths_str, _ = QFileDialog.getOpenFileNames(
            self.window,
            "Import acquisition metadata",
            str(folder),
            "Metadata files (*.csv *.txt *.h5 *.hdf5 *.json);;All files (*)",
        )
        if not paths_str:
            return
        paths = [Path(p) for p in paths_str]
        try:
            result = import_metadata_files(paths)
        except ValueError as exc:
            QMessageBox.critical(self.window, "Metadata import failed", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self.window, "Metadata import failed", f"Could not import metadata: {exc}")
            return

        dataset = self.window._state.dataset
        dataset.acquisition_metadata = result.metadata
        try:
            save_acquisition_metadata_sidecar(acquisition_metadata_sidecar_path(folder), result.metadata)
        except OSError as exc:
            result.notes.append(f"Could not save metadata sidecar: {exc}")
        self.window._update_metadata_status_labels(dataset)

        QMessageBox.information(
            self.window, "Metadata imported", "\n".join(result.notes) if result.notes else "Metadata imported."
        )
        self.window.status_label.setText("Acquisition metadata imported.")

    def export_metadata(self) -> None:
        """Save the currently loaded metadata as a standalone JSON file -
        re-importing that same file (see `import_metadata`) restores it
        exactly, including any edits made in the preview/edit dialog."""
        dataset = self.window._state.dataset
        metadata = getattr(dataset, "acquisition_metadata", None) if dataset is not None else None
        if metadata is None:
            QMessageBox.information(self.window, "Nothing to export", "No acquisition metadata is currently loaded.")
            return
        folder = self._dataset_folder()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_dir = (folder / "analysis") if folder is not None else Path.home()
        path_str, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export acquisition metadata",
            str(default_dir / f"acquisition_metadata_{stamp}.json"),
            "JSON Files (*.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            save_acquisition_metadata_sidecar(path, metadata)
        except OSError as exc:
            QMessageBox.critical(self.window, "Metadata export failed", str(exc))
            return
        self.window.status_label.setText(f"Exported acquisition metadata to {path.name}.")

    def open_preview_dialog(self) -> None:
        dataset = self.window._state.dataset
        metadata = getattr(dataset, "acquisition_metadata", None) if dataset is not None else None
        if metadata is None:
            return
        from lspr_imaging_app.gui.metadata_edit_dialog import MetadataEditDialog

        dialog = MetadataEditDialog(metadata, parent=self.window)
        if not dialog.exec():
            return
        updated = dialog.result_metadata()
        dataset.acquisition_metadata = updated
        folder = self._dataset_folder()
        if folder is not None:
            try:
                save_acquisition_metadata_sidecar(acquisition_metadata_sidecar_path(folder), updated)
            except OSError as exc:
                QMessageBox.warning(self.window, "Could not save edits", str(exc))
        self.window._update_metadata_status_labels(dataset)
