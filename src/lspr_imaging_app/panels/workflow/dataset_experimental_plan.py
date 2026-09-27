"""Dataset stage - "Experimental plan" section (renamed from "Metadata",
2026-09-25, maintainer request) - the acquisition metadata (camera/
illumination settings, per-image timing, operator notes, pump-plan/comment
log) attached to the loaded dataset.

**Real import/export this time**, unlike the read-only first pass
(`dataset_metadata.py`, now superseded): a file-path field + Import/Export
icon buttons in one row, matching the maintainer's spec. Ported from the
stable app's `MetadataController` (`gui/metadata_controller.py`):
`import_metadata_files` (`io/metadata_import.py` - a pure, Qt-free
classify-then-import function, reused as-is, no GUI dependency to strip)
identifies any mix of legacy CSV/TXT, a native v6.4 file, or a previously
exported sidecar JSON by content, not filename. `DatasetModule.
set_acquisition_metadata`/`rehydrated_acquisition_metadata` (added
2026-09-25 alongside this file) give this widget the command/query surface
the source's controller had direct `window._state.dataset` access to.

**The file-path field only reflects an explicit Import this session** -
it does not attempt to show whichever file `dataset.io.load_dataset`
auto-discovered at load time (that path isn't tracked anywhere; guessing
it back via a second `find_native_imaging_measurement_file`-style search
would risk showing the wrong file if discovery logic ever changes).
Flagged rather than silently pretending to know something this app
doesn't track.

**Live comment/step preview, linked to `SelectionModule`** - the
maintainer's "linked preview of comments and step based on actually
previewed image." A pump-plan "step" and a "comment" are the same
underlying thing here (`ImagingAcquisitionMetadata.comment_events` - see
`lspr_core.imaging_models.ImagingCommentEvent`'s own docstring: a sparse
transition log a legacy CSV's per-row "Note pump plan" column imports
into). Resolved via `timing_for(cube, wavelength)` -> `acquired_at_unix_ms`
-> `comment_at(...)`, using `rehydrated_acquisition_metadata()` rather
than the module's plain `acquisition_metadata()` - the latter may already
be timing-compacted, which would break the `timing_for` lookup for a
dataset with more than a "few" frames (see `dataset.model.
compact_dataset_image_timings`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from lspr_core import (
    SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV,
    SOURCE_FORMAT_LSPRI_ACQUISITION_V6_4,
    ImagingAcquisitionMetadata,
)
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ...dataset import DatasetModule
from ...dataset.model import ImageDataset
from ...io.metadata_import import import_metadata_files
from ...selection import SelectionModule
from ...storage.workspace import save_acquisition_metadata_sidecar
from ..dock_container import _render_tabler_icon

logger = logging.getLogger(__name__)

_SOURCE_FORMAT_LABELS = {
    SOURCE_FORMAT_LSPRI_ACQUISITION_V6_4: "Native acquisition file (v6.4)",
    SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV: "Legacy export (measureing_times.csv + metaData.txt)",
}


def _describe_metadata(metadata: ImagingAcquisitionMetadata) -> str:
    format_label = _SOURCE_FORMAT_LABELS.get(metadata.source_format, metadata.source_format)
    lines = [format_label]
    if metadata.operator:
        lines.append(f"Operator: {metadata.operator}")
    if metadata.started_at_utc:
        lines.append(f"Started: {metadata.started_at_utc}")
    lines.append(f"{len(metadata.wavelengths_nm)} wavelength(s) with camera/illumination settings")
    lines.append(f"{len(metadata.image_timings)} per-image timing record(s)")
    if metadata.comment_events:
        lines.append(f"{len(metadata.comment_events)} comment event(s)")
    if metadata.notes:
        lines.append(f"Notes: {metadata.notes}")
    return "\n".join(lines)


class ExperimentalPlanSection(QWidget):
    """File path + Import/Export row, the existing read-only summary, and
    a live current-frame comment/step preview."""

    def __init__(self, dataset: DatasetModule, selection: SelectionModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        self._selection_module = selection
        theme = get_active_theme()

        self._path_edit = QLineEdit(self)
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("No metadata file imported this session")

        self._import_button = QToolButton(self)
        self._import_button.setAutoRaise(True)
        self._import_button.setFixedSize(24, 24)
        self._import_button.setIconSize(QSize(18, 18))
        self._import_button.setIcon(_render_tabler_icon("file-import", "#38bdf8"))
        self._import_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._import_button.setToolTip(
            "Import acquisition metadata: a measuring-times CSV, metaData.txt, a native measurement file, or a "
            "previously exported metadata file. You can select multiple files at once - each one is identified "
            "by its content, not its name."
        )
        self._import_button.clicked.connect(self._on_import_clicked)

        self._export_button = QToolButton(self)
        self._export_button.setAutoRaise(True)
        self._export_button.setFixedSize(24, 24)
        self._export_button.setIconSize(QSize(18, 18))
        self._export_button.setIcon(_render_tabler_icon("file-export", "#22c55e"))
        self._export_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._export_button.setToolTip("Export the currently loaded acquisition metadata as a JSON file.")
        self._export_button.clicked.connect(self._on_export_clicked)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(4)
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(self._import_button)
        path_row.addWidget(self._export_button)

        self._status_label = QLabel("No dataset loaded.", self)
        self._status_label.setWordWrap(True)

        self._current_frame_label = QLabel("", self)
        self._current_frame_label.setWordWrap(True)
        self._current_frame_label.setStyleSheet(f"color: {theme.text_dim};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.addLayout(path_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._current_frame_label)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_cleared.connect(self._on_dataset_cleared)
        self._selection_module.cube_changed.connect(self._refresh_current_frame_preview)
        self._selection_module.wavelength_changed.connect(self._refresh_current_frame_preview)

    # -- summary + live preview ------------------------------------------------

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        metadata = dataset.acquisition_metadata
        if metadata is None:
            self._status_label.setText("No acquisition metadata found for this dataset.")
        else:
            self._status_label.setText(_describe_metadata(metadata))
        self._refresh_current_frame_preview()

    def _on_dataset_cleared(self) -> None:
        self._status_label.setText("No dataset loaded.")
        self._current_frame_label.setText("")

    def _refresh_current_frame_preview(self, *_args: object) -> None:
        metadata = self._dataset_module.rehydrated_acquisition_metadata()
        if metadata is None:
            self._current_frame_label.setText("")
            return
        cube = self._selection_module.current_cube()
        wavelength = self._selection_module.current_wavelength()
        timing = metadata.timing_for(cube, wavelength)
        if timing is None:
            self._current_frame_label.setText(f"Cube {cube}, WL {wavelength:g}: no timing recorded.")
            return
        comment = metadata.comment_at(timing.acquired_at_unix_ms)
        comment_text = comment if comment else "(no comment/step recorded)"
        self._current_frame_label.setText(f"Cube {cube}, WL {wavelength:g}: {comment_text}")

    # -- import/export --------------------------------------------------------

    def _on_import_clicked(self) -> None:
        start_dir = self._dataset_module.dataset_home()
        paths_str, _ = QFileDialog.getOpenFileNames(
            self,
            "Import acquisition metadata",
            str(start_dir) if start_dir is not None else "",
            "Metadata files (*.csv *.txt *.h5 *.hdf5 *.json);;All files (*)",
        )
        if not paths_str:
            return
        paths = [Path(p) for p in paths_str]
        try:
            result = import_metadata_files(paths)
        except Exception as exc:  # noqa: BLE001 - reported via dialog, not re-raised
            QMessageBox.critical(self, "Metadata import failed", str(exc))
            return
        try:
            self._dataset_module.set_acquisition_metadata(result.metadata)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Metadata import failed", str(exc))
            return
        self._path_edit.setText("; ".join(path.name for path in paths))
        QMessageBox.information(
            self, "Metadata imported", "\n".join(result.notes) if result.notes else "Metadata imported."
        )

    def _on_export_clicked(self) -> None:
        metadata = self._dataset_module.rehydrated_acquisition_metadata()
        if metadata is None:
            QMessageBox.information(self, "Nothing to export", "No acquisition metadata is currently loaded.")
            return
        start_dir = self._dataset_module.dataset_home()
        default_dir = (start_dir / "analysis") if start_dir is not None else Path.home()
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export acquisition metadata", str(default_dir / "acquisition_metadata.json"), "JSON Files (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            save_acquisition_metadata_sidecar(path, metadata)
        except OSError as exc:
            QMessageBox.critical(self, "Metadata export failed", str(exc))
            return
        self._path_edit.setText(path.name)
