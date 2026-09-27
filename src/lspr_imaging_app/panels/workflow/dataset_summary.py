"""Dataset stage - "Summary" section: the loaded dataset's size/count
stats, real data ported from the stable app's ``_update_dataset_summary_
labels`` (``gui/main_window.py``).

**Folder browse/load moved out** (2026-09-25) to ``dataset_folder_row.py``,
matching the source's own placement - `top_row_widget` (folder field +
browse/explorer icons) sits *above* the nested Summary/Reference/Export/
Metadata sections, not inside Summary. This file is display-only now.

**Two display surfaces, both real**, matching the source exactly:

1. A compact one-line stats readout meant for the section's own title row
   (``header_stats_label``, passed as ``header_extra`` by ``panel.py``) -
   per the maintainer's explicit choice, this shows Size/Cubes/Wavelengths
   only (the source also includes Images - dropped here as not worth the
   title row's space).
2. The expanded body: paired rows (Images+Size, Cubes+Wavelengths),
   Resolution (read from the first image file), Dataset's date (earliest
   file's mtime) - all of it, matching the source, per the maintainer's
   "same as in stable version" instruction for the body.

Plus a **conditional OME-Zarr block**, visible only when the loaded
dataset is an OME-Zarr export with real persisted metadata
(``read_existing_ome_zarr_summary``) - reuses that summary's own
``field_lines()`` (chunk/shard/compression/dtype/image-tools-applied/
rotation/flip/crop/pixel-size) rather than re-deriving the same
formatting a second time.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...dataset.io import dataset_is_ome_zarr, load_image_shape, read_existing_ome_zarr_summary
from ...dataset.model import ImageDataset

logger = logging.getLogger(__name__)


def _format_bytes(num_bytes: int) -> str:
    """Ported verbatim from the stable app's
    `DatasetController._format_bytes`."""
    value = float(max(int(num_bytes), 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


class DatasetSummarySection(QWidget):
    """Size/count stats, live from `DatasetModule`. `header_stats_label`
    and `dataset_header_stats_label` are separate, public `QLabel`s - not
    part of this widget's own layout - meant to be re-parented into a
    `CollapsibleSection`'s title row (`header_extra=`): `header_stats_label`
    into this content's own "Summary" section (same relationship the
    source's `summary_header_stats_label` has to `dataset_section`),
    `dataset_header_stats_label` into the *top-level* "Dataset:" section
    one level up (2026-09-27, maintainer request) - a terser
    type/size/cube-wavelength readout visible even while "Summary" itself
    is collapsed, since that's the one thing every dataset always has
    regardless of source format."""

    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        theme = get_active_theme()

        self.header_stats_label = QLabel("", parent)
        self.header_stats_label.setStyleSheet(f"color: {theme.text_dim};")

        self.dataset_header_stats_label = QLabel("", parent)
        self.dataset_header_stats_label.setStyleSheet(f"color: {theme.text_dim};")

        self._images_label = QLabel("Images: -", self)
        self._size_label = QLabel("Dataset size: -", self)
        self._cubes_label = QLabel("Spectral cubes: -", self)
        self._wavelengths_label = QLabel("Wavelengths: -", self)
        self._resolution_label = QLabel("Resolution: -", self)
        self._date_label = QLabel("Dataset's date: -", self)

        self._base_rows = QWidget(self)
        base_layout = QVBoxLayout(self._base_rows)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(2)
        # Paired rows (two stats sharing a line), matching the source's
        # summary_images_row/summary_cubes_row - a plain QHBoxLayout pair
        # reads closer to the source than a QFormLayout here.
        base_layout.addWidget(self._paired_row(self._images_label, self._size_label))
        base_layout.addWidget(self._paired_row(self._cubes_label, self._wavelengths_label))
        base_layout.addWidget(self._resolution_label)
        base_layout.addWidget(self._date_label)
        self._base_rows.setVisible(False)

        self._ome_zarr_rows = QWidget(self)
        self._ome_zarr_layout = QVBoxLayout(self._ome_zarr_rows)
        self._ome_zarr_layout.setContentsMargins(0, 0, 0, 0)
        self._ome_zarr_layout.setSpacing(2)
        self._ome_zarr_rows.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addWidget(self._base_rows)
        layout.addWidget(self._ome_zarr_rows)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_cleared.connect(self._on_dataset_cleared)

    @staticmethod
    def _paired_row(left: QLabel, right: QLabel) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(16)
        row_layout.addWidget(left)
        row_layout.addWidget(right)
        row_layout.addStretch(1)
        return row

    def _on_dataset_cleared(self) -> None:
        self.header_stats_label.setText("")
        self.dataset_header_stats_label.setText("")
        self._base_rows.setVisible(False)
        self._ome_zarr_rows.setVisible(False)

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        records = dataset.records
        spectral_cube_values = dataset.spectral_cube_indices
        wavelength_values = dataset.wavelengths_nm

        resolution_text = "Unknown"
        if records:
            try:
                height, width = load_image_shape(str(records[0].path))
                resolution_text = f"{width} x {height} px"
            except Exception:
                resolution_text = "Unknown"

        is_ome_zarr = dataset_is_ome_zarr(dataset)
        if is_ome_zarr:
            # OME-Zarr records carry synthetic per-plane "paths" (keys into
            # the zarr array, not real files) - the real, statable files
            # are the shard/metadata files under the dataset's actual
            # .ome.zarr folder instead. Same reasoning as the source's
            # _update_dataset_summary_labels.
            size_bytes = 0
            mtimes: list[float] = []
            try:
                for entry in dataset.folder.rglob("*"):
                    if not entry.is_file():
                        continue
                    try:
                        stat_result = entry.stat()
                    except OSError:
                        continue
                    size_bytes += int(stat_result.st_size)
                    mtimes.append(stat_result.st_mtime)
            except OSError:
                pass
            dataset_date = datetime.fromtimestamp(min(mtimes)).strftime("%Y-%m-%d") if mtimes else "Unknown"
        else:
            size_bytes = 0
            for record in records:
                try:
                    size_bytes += int(record.path.stat().st_size)
                except OSError:
                    continue
            try:
                dataset_date = (
                    datetime.fromtimestamp(min(record.path.stat().st_mtime for record in records)).strftime("%Y-%m-%d")
                    if records
                    else "Unknown"
                )
            except Exception:
                dataset_date = "Unknown"

        cube_text = str(len(spectral_cube_values))
        wavelength_text = str(len(wavelength_values))
        size_text = _format_bytes(size_bytes)

        # Images deliberately left out here (maintainer's explicit call,
        # 2026-09-25) - kept in the expanded body below.
        self.header_stats_label.setText(f"Size: {size_text}, Cubes: {cube_text}, WL: {wavelength_text}")

        type_text = "OME-Zarr" if is_ome_zarr else "TIFF Stack"
        self.dataset_header_stats_label.setText(f"{type_text} · {size_text} · C/WL: {cube_text}/{wavelength_text}")

        self._images_label.setText(f"Images: {len(records)}")
        self._size_label.setText(f"Dataset size: {size_text}")
        self._cubes_label.setText(f"Spectral cubes: {cube_text}")
        self._wavelengths_label.setText(f"Wavelengths: {wavelength_text}")
        self._resolution_label.setText(f"Resolution: {resolution_text}")
        self._date_label.setText(f"Dataset's date: {dataset_date}")
        self._base_rows.setVisible(True)

        zarr_summary = read_existing_ome_zarr_summary(dataset.folder) if is_ome_zarr else None
        while self._ome_zarr_layout.count():
            item = self._ome_zarr_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if zarr_summary is not None:
            for label, value in zarr_summary.field_lines():
                if label == "Source folder":
                    # Redundant with the folder path field above
                    # (dataset_folder_row.py) and a full absolute path is the
                    # one field long enough to blow the panel's fixed width
                    # budget (see test_lspri_workflow_panel_width_budget.py) -
                    # the stable app's own summary never shows this field
                    # either (gui/main_window.py:1165-1183 lists every other
                    # field_lines() entry but this one).
                    continue
                field_label = QLabel(f"{label}: {value}", self)
                field_label.setWordWrap(True)
                self._ome_zarr_layout.addWidget(field_label)
        self._ome_zarr_rows.setVisible(zarr_summary is not None)
