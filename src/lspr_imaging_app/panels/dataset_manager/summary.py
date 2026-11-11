"""Dataset Manager -> Overview page: the loaded dataset's folder, type, size and counts.

Display only, live from `DatasetModule`. Was the Workflow panel's "Summary" section until the Workflow panel was
retired (2026-11-11); the one-line header readouts it fed there are gone (the menu bar's info row,
`panels/dataset_info_bar.py`, shows type / size / cubes / wavelengths at all times).

Plus a **conditional OME-Zarr block**, visible only when the loaded dataset is an OME-Zarr export with persisted
metadata (``read_existing_ome_zarr_summary``): it reuses that summary's own ``field_lines()`` (chunk / shard /
compression / dtype / image tools applied / rotation / flip / crop / pixel size).
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from ...dataset import DatasetModule
from ...dataset.io import dataset_is_ome_zarr, load_image_shape, read_existing_ome_zarr_summary
from ...dataset.model import ImageDataset

logger = logging.getLogger(__name__)


def format_bytes(num_bytes: int) -> str:
    """Ported verbatim from the old app's `DatasetController._format_bytes`."""
    value = float(max(int(num_bytes), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _value_label(parent: QWidget) -> QLabel:
    label = QLabel("-", parent)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # a path or a number can be copied
    return label


class DatasetSummarySection(QWidget):
    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        self._shown: ImageDataset | None = None

        self._empty_label = QLabel("No dataset loaded.", self)
        self._folder_label = _value_label(self)
        self._data_folder_label = _value_label(self)
        self._type_label = _value_label(self)
        self._images_label = _value_label(self)
        self._size_label = _value_label(self)
        self._cubes_label = _value_label(self)
        self._wavelengths_label = _value_label(self)
        self._resolution_label = _value_label(self)
        self._date_label = _value_label(self)

        self._base_rows = QWidget(self)
        form = QFormLayout(self._base_rows)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Folder:", self._folder_label)
        form.addRow("Images in:", self._data_folder_label)
        form.addRow("Type:", self._type_label)
        form.addRow("Images:", self._images_label)
        form.addRow("Size:", self._size_label)
        form.addRow("Spectral cubes:", self._cubes_label)
        form.addRow("Wavelengths:", self._wavelengths_label)
        form.addRow("Resolution:", self._resolution_label)
        form.addRow("Dataset's date:", self._date_label)
        self._data_folder_row_label = form.labelForField(self._data_folder_label)
        self._base_rows.setVisible(False)

        self._ome_zarr_rows = QWidget(self)
        self._ome_zarr_layout = QFormLayout(self._ome_zarr_rows)
        self._ome_zarr_layout.setContentsMargins(0, 0, 0, 0)
        self._ome_zarr_rows.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._base_rows)
        layout.addWidget(self._ome_zarr_rows)
        layout.addStretch(1)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_cleared.connect(self._on_dataset_cleared)
        self._dataset_module.file_stats_ready.connect(self._on_file_stats_ready)  # size and date are counted in the background (2026-10-24)
        # No catch-up read of an already-loaded dataset: the Dataset Manager is built (hidden) with the window, before any
        # dataset opens, so it hears every `dataset_loaded` like the Workflow panel did.

    def _on_file_stats_ready(self, ready: ImageDataset) -> None:
        if ready is self._shown:
            self._show_size_and_date(ready)

    def _show_size_and_date(self, dataset: ImageDataset) -> None:
        """Size and date are counted on a background thread, so right after a load they read "..." and this runs again
        when `file_stats_ready` arrives."""
        # An OME-Zarr's records are synthetic paths, so the count stats the files under the .ome.zarr folder instead
        # (`DatasetModule.dataset_file_stats`, one count per dataset, shared with the menu bar's info row).
        stats = self._dataset_module.dataset_file_stats(dataset)
        if stats is None:
            size_text, date_text = "...", "..."
        else:
            size_text = format_bytes(stats[0])
            date_text = datetime.fromtimestamp(stats[1]).strftime("%Y-%m-%d") if stats[1] is not None else "Unknown"
        self._size_label.setText(size_text)
        self._date_label.setText(date_text)

    def _on_dataset_cleared(self) -> None:
        self._shown = None
        self._empty_label.setVisible(True)
        self._base_rows.setVisible(False)
        self._ome_zarr_rows.setVisible(False)

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        records = dataset.records
        resolution_text = "Unknown"
        if records:
            try:
                height, width = load_image_shape(str(records[0].path))
                resolution_text = f"{width} x {height} px"
            except Exception:  # noqa: BLE001 - the summary shows "Unknown" instead
                resolution_text = "Unknown"

        is_ome_zarr = dataset_is_ome_zarr(dataset)
        self._shown = dataset
        self._folder_label.setText(str(dataset.home))
        self._data_folder_label.setText(str(dataset.folder))
        separate = dataset.folder != dataset.home
        self._data_folder_label.setVisible(separate)
        if self._data_folder_row_label is not None:
            self._data_folder_row_label.setVisible(separate)
        self._type_label.setText("OME-Zarr" if is_ome_zarr else "TIFF Stack")
        self._images_label.setText(str(len(records)))
        self._cubes_label.setText(str(len(dataset.spectral_cube_indices)))
        self._wavelengths_label.setText(str(len(dataset.wavelengths_nm)))
        self._resolution_label.setText(resolution_text)
        self._show_size_and_date(dataset)
        self._empty_label.setVisible(False)
        self._base_rows.setVisible(True)

        zarr_summary = read_existing_ome_zarr_summary(dataset.folder) if is_ome_zarr else None
        while self._ome_zarr_layout.rowCount():
            self._ome_zarr_layout.removeRow(0)
        if zarr_summary is not None:
            for label, value in zarr_summary.field_lines():
                if label == "Source folder":  # the Folder row above already shows where the dataset is
                    continue
                self._ome_zarr_layout.addRow(f"{label}:", _value_label(self._ome_zarr_rows))
                field = self._ome_zarr_layout.itemAt(self._ome_zarr_layout.rowCount() - 1, QFormLayout.ItemRole.FieldRole).widget()
                field.setText(str(value))
        self._ome_zarr_rows.setVisible(zarr_summary is not None)
