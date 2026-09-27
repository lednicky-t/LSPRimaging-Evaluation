"""Dataset stage - "Export" section: export the loaded dataset to OME-Zarr.

Ported from the stable app's OME-Zarr export controls
(`gui/main_window.py`'s `dataset_ome_zarr_*` widgets, `gui/dataset_
controller.py`'s `export_current_dataset_to_ome_zarr`), rebuilt 2026-09-25
per the maintainer's explicit spec: real chunk-size/shard-mode/compression/
skip-excluded settings (not just a bare destination field), Chunk size and
Shard as two `form_rows.stacked_field`s (the same width-budget-safe
label-above-field pattern `mask_settings.py`/`background_removal.py`
already use) placed side by side in one row (2026-09-26, per the
maintainer's follow-up request - both are narrow enough together to fit
the panel's width budget, unlike the longer-labeled rows those other
sections needed full-width stacking for), the live chunk/read-time
estimate labels underneath them, and the Export trigger as a labeled
button placed *after* all the settings - rather than the source's
icon-only button sitting first, above them.

**Scoped down from the source, flagged rather than silently dropped**:
- No name-prompt dialog (`QInputDialog`, "Name this export") - defaults to
  the dataset's own folder name, matching what the prompt itself defaults
  to anyway.
- No destination-collision comparison/replace dialog - `export_ome_zarr_
  dataset` still writes safely to a temp sibling and swaps in only on
  success either way (see that function's own docstring), so this is a
  missing confirmation prompt, not a missing safety net.
- No plan-confirmation dialog before starting.
- The chunk/total read-time estimates use `calibration=None`
  (`dataset.io.estimate_ome_zarr_export_chunk_plane_read`/
  `..._dataset_total_read`) - real chunk counts, no estimated milliseconds,
  since real timing needs `calibrate_zarr_read_overhead_ms`'s actual disk
  probe, a heavier step than this pass takes on.
- Compression/skip-excluded are plain `QCheckBox`es, not the source's
  icon-toggle-button + separate label pairs - same simplification already
  applied to Mask/Background removal's checkboxes.

The actual export still lives in `DatasetModule.export_to_ome_zarr`
(2026-09-24) - this widget computes the destination path (folder naming
via `dataset.io.build_ome_zarr_export_folder_name`, using a plane loaded
through `DatasetModule.load_plane` for width/height/dtype rather than any
new module surface - everything needed was already queryable) and drives
the module's existing progress/finished/failed signals.

**Chunk-grid preview toggle (2026-09-26)**: the icon button next to Chunk
size lets the maintainer see the actual chunk boundaries drawn over the
image before exporting. The on/off state and chunk size live on
`DatasetModule.chunk_grid_preview` (set via `set_chunk_grid_preview`), not
here - this section and `panels/image/panel.py`'s `ImagePanel` are sibling
panels with no direct reference to each other, so the state has to live on
a module both already depend on (AGENTS.md's module-boundary rule: a panel
only ever reads another panel's data through a shared module, never
directly). Shard's combo values are unchanged (`per_image`/
`per_spectral_cube`); only their on-screen labels became "WL frame"/"Cube"
per the maintainer's wording.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ...dataset import DatasetModule
from ...dataset.io import (
    build_ome_zarr_export_folder_name,
    estimate_ome_zarr_export_chunk_plane_read,
    estimate_ome_zarr_export_dataset_total_read,
    sanitize_ome_zarr_export_name,
)
from ...dataset.model import ImageDataset
from ..dock_container import _render_tabler_icon
from .form_rows import stacked_field

_CHUNK_GRID_ACTIVE_COLOR = "#84cc16"  # same lime the reference-frame row uses for its active toggle

logger = logging.getLogger(__name__)


class DatasetExportSection(QWidget):
    """Chunk size/Shard/estimates/Compression/Skip-excluded settings, then
    an Export button underneath all of them."""

    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        # Probed once per dataset load (_on_dataset_loaded) from one real
        # plane, not re-probed on every chunk-size edit - see that
        # method's own comment.
        self._image_width: int | None = None
        self._image_height: int | None = None
        self._image_dtype: np.dtype | None = None
        self._image_count = 0

        self._chunk_spin = QSpinBox(self)
        self._chunk_spin.setRange(4, 4096)
        self._chunk_spin.setValue(64)
        self._chunk_spin.setSuffix(" px")
        self._chunk_spin.setKeyboardTracking(False)
        self._chunk_spin.setMinimumWidth(90)
        self._chunk_spin.setToolTip(
            "Square spatial chunk size for Zarr export. Any value from 4 to 4096 px - does not need to be a power of 2."
        )
        self._chunk_spin.valueChanged.connect(self._on_chunk_size_changed)

        # Toggle for the chunk-grid preview drawn on the Image panel (state
        # lives on `DatasetModule.chunk_grid_preview` - see that method's
        # docstring for why this section can't just tell `ImagePanel`
        # directly). "grid-4x4" is the vendored icon closest to what the
        # overlay actually looks like.
        self._chunk_grid_button = QToolButton(self)
        self._chunk_grid_button.setCheckable(True)
        self._chunk_grid_button.setAutoRaise(True)
        self._chunk_grid_button.setFixedSize(24, 24)
        self._chunk_grid_button.setIconSize(QSize(18, 18))
        self._chunk_grid_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._chunk_grid_button.setToolTip("Show the export chunk grid on top of the image.")
        self._chunk_grid_button.toggled.connect(self._on_chunk_grid_toggled)
        self._refresh_chunk_grid_icon()

        self._shard_combo = QComboBox(self)
        self._shard_combo.addItem("WL frame", "per_image")
        self._shard_combo.addItem("Cube", "per_spectral_cube")
        self._shard_combo.setMinimumWidth(100)
        self._shard_combo.setToolTip(
            "How many images are packed into a single shard file on disk: one wavelength frame, or a whole spectral cube."
        )

        self._chunk_estimate_label = QLabel("", self)
        self._chunk_total_label = QLabel("", self)
        for info_label in (self._chunk_estimate_label, self._chunk_total_label):
            info_label.setWordWrap(True)
            info_label.setStyleSheet(f"color: {get_active_theme().text_muted}; font-size: 11px;")

        # Abbreviated label (2026-09-25, same width-budget reasoning as
        # background_removal.py's "Local reference norm.") - "Compression
        # (lz4 + bitshuffle)" alone measured ~384px, over the panel's
        # entire width; full wording moved to the tooltip.
        self._compression_check = QCheckBox("Compression", self)
        self._compression_check.setChecked(True)
        self._compression_check.setToolTip("Compression: lz4 + bitshuffle. Toggle Zarr compression on or off.")

        self._skip_excluded_check = QCheckBox("Skip excluded images", self)
        self._skip_excluded_check.setToolTip(
            "Omit pixel data for excluded images/wavelengths/spectral cubes from the export."
        )

        self._status_label = QLabel("No export running.", self)
        self._status_label.setWordWrap(True)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)

        self._export_button = QPushButton("Export", self)
        self._export_button.setToolTip("Export the currently loaded dataset to OME-Zarr.")
        self._export_button.clicked.connect(self._on_export_clicked)

        self._cancel_button = QPushButton("Cancel", self)
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)

        export_row = QHBoxLayout()
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(4)
        export_row.addWidget(self._export_button)
        export_row.addWidget(self._cancel_button)
        export_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        chunk_field_row = QHBoxLayout()
        chunk_field_row.setContentsMargins(0, 0, 0, 0)
        chunk_field_row.setSpacing(4)
        chunk_field_row.addWidget(self._chunk_spin)
        chunk_field_row.addWidget(self._chunk_grid_button)
        chunk_field = QWidget(self)
        chunk_field.setLayout(chunk_field_row)

        chunk_shard_row = QHBoxLayout()
        chunk_shard_row.setContentsMargins(0, 0, 0, 0)
        chunk_shard_row.setSpacing(6)
        chunk_shard_row.addWidget(stacked_field("Chunk size", chunk_field, self), 1)
        chunk_shard_row.addWidget(stacked_field("Shard", self._shard_combo, self), 1)
        layout.addLayout(chunk_shard_row)
        layout.addWidget(self._chunk_estimate_label)
        layout.addWidget(self._chunk_total_label)
        layout.addWidget(self._compression_check)
        layout.addWidget(self._skip_excluded_check)
        layout.addWidget(self._status_label)
        layout.addWidget(self._progress_bar)
        layout.addLayout(export_row)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_cleared.connect(self._on_dataset_cleared)
        self._dataset_module.export_progress.connect(self._on_export_progress)
        self._dataset_module.export_finished.connect(self._on_export_finished)
        self._dataset_module.export_failed.connect(self._on_export_failed)

    # -- dataset-driven state ---------------------------------------------------

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        # One real plane read, once per load - not on every chunk-size
        # edit, which only needs the shape/dtype this already gives it.
        self._image_width = self._image_height = None
        self._image_dtype = None
        self._image_count = sum(
            len(self._dataset_module.wavelengths_for_cube(cube)) for cube in self._dataset_module.spectral_cubes()
        )
        cubes = self._dataset_module.spectral_cubes()
        if cubes:
            wavelengths = self._dataset_module.wavelengths_for_cube(cubes[0])
            if wavelengths:
                try:
                    plane = self._dataset_module.load_plane(cubes[0], wavelengths[0])
                except Exception:
                    logger.exception("Could not probe a plane for the export estimate")
                else:
                    self._image_height, self._image_width = plane.shape[:2]
                    self._image_dtype = plane.dtype
        self._refresh_estimates()

    def _on_dataset_cleared(self) -> None:
        self._image_width = self._image_height = None
        self._image_dtype = None
        self._image_count = 0
        self._refresh_estimates()

    def _refresh_estimates(self, *_args: object) -> None:
        if self._image_width is None or self._image_height is None:
            self._chunk_estimate_label.setText("")
            self._chunk_total_label.setText("")
            return
        chunk_size_px = self._chunk_spin.value()
        self._chunk_estimate_label.setText(
            estimate_ome_zarr_export_chunk_plane_read(self._image_width, self._image_height, chunk_size_px, None)
        )
        self._chunk_total_label.setText(
            estimate_ome_zarr_export_dataset_total_read(
                self._image_width, self._image_height, chunk_size_px, self._image_count, None
            )
        )

    # -- chunk-grid preview -----------------------------------------------------

    def _on_chunk_size_changed(self, chunk_size_px: int) -> None:
        self._refresh_estimates()
        if self._chunk_grid_button.isChecked():
            self._dataset_module.set_chunk_grid_preview(True, chunk_size_px)

    def _on_chunk_grid_toggled(self, checked: bool) -> None:
        self._dataset_module.set_chunk_grid_preview(checked, self._chunk_spin.value())
        self._refresh_chunk_grid_icon()

    def _refresh_chunk_grid_icon(self) -> None:
        checked = self._chunk_grid_button.isChecked()
        color = _CHUNK_GRID_ACTIVE_COLOR if checked else get_active_theme().text_primary
        self._chunk_grid_button.setIcon(_render_tabler_icon("grid-4x4", color))

    # -- user actions ---------------------------------------------------------

    def _on_export_clicked(self) -> None:
        home = self._dataset_module.dataset_home()
        parent_dir = QFileDialog.getExistingDirectory(
            self, "Choose export location for Stack to Zarr", str(home) if home is not None else ""
        )
        if not parent_dir:
            return
        destination = Path(parent_dir) / self._build_export_folder_name(home)
        try:
            self._dataset_module.export_to_ome_zarr(
                destination,
                chunk_size_px=self._chunk_spin.value(),
                compression_enabled=self._compression_check.isChecked(),
                shard_mode=self._shard_combo.currentData(),
                skip_excluded=self._skip_excluded_check.isChecked(),
            )
        except RuntimeError as exc:
            self._status_label.setText(str(exc))
            return
        self._set_exporting(True)
        self._status_label.setText("Starting export...")

    def _build_export_folder_name(self, home: Path | None) -> str:
        default_name = home.name if home is not None else "export"
        if self._image_width is None or self._image_height is None or self._image_dtype is None:
            # No successful plane probe (e.g. a load failure this section
            # never saw) - a plain sanitized name still produces a valid,
            # if less descriptive, destination rather than failing here.
            return sanitize_ome_zarr_export_name(default_name)
        return build_ome_zarr_export_folder_name(
            default_name,
            width=self._image_width,
            height=self._image_height,
            spectral_cube_count=len(self._dataset_module.spectral_cubes()),
            wavelength_count=len(self._dataset_module.wavelengths()),
            chunk_size_px=self._chunk_spin.value(),
            shard_mode=self._shard_combo.currentData(),
            compression_enabled=self._compression_check.isChecked(),
            dtype=self._image_dtype,
        )

    def _on_cancel_clicked(self) -> None:
        self._dataset_module.cancel_export()
        self._status_label.setText("Cancelling...")

    # -- state -----------------------------------------------------------------

    def _set_exporting(self, exporting: bool) -> None:
        self._export_button.setEnabled(not exporting)
        self._cancel_button.setEnabled(exporting)
        self._progress_bar.setVisible(exporting)
        if not exporting:
            self._progress_bar.setValue(0)

    def _on_export_progress(self, percent: int, message: str) -> None:
        self._progress_bar.setValue(max(0, min(100, int(percent))))
        self._status_label.setText(message)

    def _on_export_finished(self, destination: Path) -> None:
        self._set_exporting(False)
        self._status_label.setText(f"Export finished: {destination}")

    def _on_export_failed(self, message: str) -> None:
        # No modal dialog here (unlike DatasetSummarySection's load
        # failure) - a cancelled export reaches this same path (see
        # DatasetModule.export_to_ome_zarr's docstring), and popping a
        # "failed" dialog in response to the user's own Cancel click would
        # be bad UX. A future pass could distinguish a real error from a
        # cancellation and only dialog on the former.
        self._set_exporting(False)
        self._status_label.setText(f"Export failed: {message}")
