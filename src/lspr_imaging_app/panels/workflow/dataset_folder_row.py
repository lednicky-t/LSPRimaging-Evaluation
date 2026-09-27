"""Dataset stage - the folder path row that sits *above* the nested
Summary/Reference/Export/Metadata sections, matching the stable app's
placement exactly (`gui/layout_builder.py`'s ``top_row_widget``, added to
``dataset_inner_layout`` before the nested section group - not inside any
one section).

Owns the actual folder browse/load flow - moved here 2026-09-25 out of
``dataset_summary.py``, which now only *displays* the loaded dataset's
stats (see that file). Real icons this time: ``folder-search``/
``folder-open`` via the free-standing (chrome-less) icon widgets
(``free_standing.py``), matching the source's ``browse_button``/
``open_explorer_button`` instead of the plain ``QPushButton``s this had
before.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...dataset.io import (
    DatasetLoadChoice,
    resolve_remembered_dataset_choice,
    save_dataset_choice,
    summarize_dataset_candidate,
)
from ...dataset.model import ImageDataset
from ..dock_container import _render_tabler_icon
from .dataset_summary import _format_bytes
from .free_standing import make_free_standing_icon_label

logger = logging.getLogger(__name__)

_FORMAT_LABELS = {"image_stack": "TIFF image stack", "ome_zarr": "OME-Zarr"}


def _describe_candidate(candidate: ImageDataset) -> str:
    summary = summarize_dataset_candidate(candidate)
    format_label = _FORMAT_LABELS.get(summary.source_format, summary.source_format)
    return (
        f"{candidate.folder.name}/  ({format_label})\n"
        f"    {summary.image_count} images, {summary.spectral_cube_count} cubes x "
        f"{summary.wavelength_count} wavelengths, {_format_bytes(summary.total_bytes)}"
    )


class DatasetFolderRow(QWidget):
    """Folder path field + browse/open-in-explorer icons + a small status
    line for load feedback only (the loaded dataset's actual stats are
    ``DatasetSummarySection``'s job, not this widget's)."""

    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_module = dataset

        self._folder_edit = QLineEdit(self)
        self._folder_edit.setPlaceholderText("Dataset folder...")
        self._folder_edit.returnPressed.connect(self._on_folder_edit_returned)

        theme = get_active_theme()
        self._browse_icon = make_free_standing_icon_label(
            _render_tabler_icon("folder-search", "#38bdf8"),
            "Browse: choose a dataset folder.",
            parent=self,
        )
        self._browse_icon.clicked.connect(self._on_browse_clicked)

        self._explorer_icon = make_free_standing_icon_label(
            _render_tabler_icon("folder-open", "#f59e0b"),
            "Open the dataset folder in File Explorer.",
            parent=self,
        )
        self._explorer_icon.clicked.connect(self._on_open_explorer_clicked)

        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(4)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(self._browse_icon)
        folder_row.addWidget(self._explorer_icon)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {theme.text_muted};")
        self._status_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(4)
        layout.addLayout(folder_row)
        layout.addWidget(self._status_label)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_load_failed.connect(self._on_dataset_load_failed)
        self._dataset_module.dataset_choice_needed.connect(self._on_dataset_choice_needed)

    # -- user actions --------------------------------------------------------

    def _on_browse_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select dataset folder", self._folder_edit.text())
        if folder:
            self._folder_edit.setText(folder)
            self._start_load(Path(folder))

    def _on_folder_edit_returned(self) -> None:
        text = self._folder_edit.text().strip()
        if text:
            self._start_load(Path(text))

    def _on_open_explorer_clicked(self) -> None:
        folder = Path(self._folder_edit.text())
        if not folder.is_dir():
            self._set_status(f"Cannot open folder - path does not exist: {folder}")
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self._set_status(f"Could not open folder in File Explorer: {exc}")

    # -- loading --------------------------------------------------------------

    def _start_load(self, folder: Path) -> None:
        if self._dataset_module.is_loading():
            self._set_status("A dataset is already loading - please wait for it to finish.")
            return
        self._set_status(f"Loading dataset from {folder.name}...")
        try:
            self._dataset_module.load_dataset_from_folder(folder)
        except RuntimeError as exc:
            self._set_status(str(exc))

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        self._folder_edit.setText(str(dataset.home))
        self._set_status("")

    def _on_dataset_load_failed(self, message: str) -> None:
        self._set_status(f"Load failed: {message}")
        QMessageBox.critical(self, "Load failed", message)

    def _on_dataset_choice_needed(self, choice: DatasetLoadChoice) -> None:
        dataset = resolve_remembered_dataset_choice(choice)
        if dataset is not None:
            if choice.acquisition_metadata is not None:
                dataset.acquisition_metadata = choice.acquisition_metadata
            dataset.home_folder = choice.parent_folder
        else:
            dataset = self._prompt_dataset_candidate_choice(choice)
        if dataset is None:
            self._set_status("Load cancelled.")
            return
        # Emits dataset_loaded -> _on_dataset_loaded updates the folder field.
        self._dataset_module.load_dataset(dataset)

    def _prompt_dataset_candidate_choice(self, choice: DatasetLoadChoice) -> ImageDataset | None:
        """Ask which dataset to load when more than one TIFF-stack/OME-Zarr
        candidate was found one level under the chosen folder - ported from
        the stable app's
        `DatasetController._prompt_dataset_candidate_choice` (button-per-
        option `QMessageBox`)."""
        lines = [f"Found {len(choice.candidates)} datasets under {choice.parent_folder}:"]
        lines.extend(f"\n{_describe_candidate(candidate)}" for candidate in choice.candidates)
        box = QMessageBox(self)
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
            save_dataset_choice(choice.parent_folder, dataset.folder.name)
        return dataset
