"""Dataset stage - "Export" section: export the loaded dataset to OME-Zarr.

Ported from the stable app's OME-Zarr export controls
(`gui/main_window.py`'s `dataset_ome_zarr_*` widgets, `gui/dataset_
controller.py`'s `export_current_dataset_to_ome_zarr`), rebuilt 2026-09-25
per the maintainer's explicit spec: real chunk-size/shard-mode/compression/
skip-excluded settings (not just a bare destination field), the chunk fields
and Shard side by side as `form_rows.stacked_field`s, the live chunk-count
labels underneath them, and the Export trigger as a labeled button placed
*after* all the settings.

**Scoped down from the source, flagged rather than silently dropped**:
- No name-prompt dialog (`QInputDialog`, "Name this export") - defaults to
  the dataset's own folder name, matching what the prompt itself defaults
  to anyway.
- No destination-collision comparison/replace dialog - `export_ome_zarr_
  dataset` still writes safely to a temp sibling and swaps in only on
  success either way (see that function's own docstring), so this is a
  missing confirmation prompt, not a missing safety net.
- No plan-confirmation dialog before starting.
- Compression/skip-excluded are plain `QCheckBox`es.

The actual export still lives in `DatasetModule.export_to_ome_zarr`
(2026-09-24) - this widget computes the destination path (folder naming
via `dataset.io.build_ome_zarr_export_folder_name`) and drives the module's
existing progress/finished/failed signals. The export writes **raw** pixels
(no rotation/flip/crop), decided again 2026-10-20.

**Chunk-grid preview toggle (2026-09-26)**: the icon button next to the
chunk fields draws the chunk boundaries over the image. The state lives on
`DatasetModule.chunk_grid_preview` (set via `set_chunk_grid_preview`), not
here - this section and `ImagePanel` are sibling panels (CLAUDE.md
module-boundary rule).

**Chunk layout tools (2026-10-20, `docs/zarr_chunk_planner_2026-10-19.md`)**:
- Chunks are always width x height (the maintainer: no square/rectangle
  switch; equal sides are just a special case). The width field keeps the
  old `export/chunk_size` key, so a saved square size restores as width;
  the height defaults to the width until it is saved itself.
- "Grid position": image corner (as before), or aligned to the ROIs -
  lossless (pads the left/top edge with fill) or lossy (keeps only the
  chunk-aligned part around the ROIs). The alignment is computed from the
  ROI read boxes in raw pixels (`export_roi_layout.py`,
  `dataset.chunk_plan.align_grid`) whenever the size or position changes,
  or when the ROIs/geometry/chromatic correction change (cheap integer
  arithmetic; the preview grid follows).
- Find best (magnifier): `chunk_layout_dialog.py`; Apply sets width,
  height, grid position and the exact grid offsets of the chosen
  suggestion (kept until the size is edited by hand).
- Live warnings under the fields (`chunk_plan.chunk_warnings`): chunks that
  will not compress (pixel count not a multiple of 8) or are small.
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
from ...dataset import chunk_plan as cp
from ...dataset.io import build_ome_zarr_export_folder_name, sanitize_ome_zarr_export_name
from ...dataset.model import ImageDataset
from ...image_tools import GeometryModule
from ..dock_container import _render_tabler_icon
from ..ui_state import UiStateStore
from .chunk_layout_dialog import ChunkLayoutDialog
from .eliding_label import ElidingLabel
from .export_roi_layout import ExportRoiLayout, gather_export_roi_layout
from .form_rows import stacked_field

_CHUNK_GRID_ACTIVE_COLOR = "#84cc16"  # same lime the reference-frame row uses for its active toggle
GRID_CORNER, GRID_LOSSLESS, GRID_LOSSY = "corner", "lossless", "lossy"
_SAMPLE_CUBES = 3
_SAMPLE_WAVELENGTHS = 5

logger = logging.getLogger(__name__)


class DatasetExportSection(QWidget):
    """Chunk W x H / Shard / grid position / counts / Compression / Skip-excluded settings, then an Export button."""

    def __init__(
        self,
        dataset: DatasetModule,
        parent: QWidget | None = None,
        geometry: GeometryModule | None = None,
        ui_state: UiStateStore | None = None,
        roi_toolbox: object | None = None,
        chromatic: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        # Geometry: the calibration (um/px) written into the export's metadata, and the raw <-> processed map for the
        # ROI layout. The export never applies rotation/flip/crop to the pixels.
        self._geometry = geometry
        self._roi_toolbox = roi_toolbox
        self._chromatic = chromatic
        # Probed once per dataset load (_on_dataset_loaded) from one real plane.
        self._image_width: int | None = None
        self._image_height: int | None = None
        self._image_dtype: np.dtype | None = None
        self._image_count = 0
        self._roi_layout: ExportRoiLayout | None = None  # cached; dropped when ROIs/geometry/chromatic change
        self._applied: cp.ChunkLayout | None = None  # from Find best, kept while the size is unchanged
        self._dialog: ChunkLayoutDialog | None = None

        self._chunk_spin = QSpinBox(self)  # width (keeps its name and ui_state key from the square-only days)
        self._chunk_h_spin = QSpinBox(self)
        for spin, axis in ((self._chunk_spin, "width"), (self._chunk_h_spin, "height")):
            spin.setRange(4, 8192)
            spin.setValue(64)
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(64)
            spin.setToolTip(f"Chunk {axis} in pixels (any value from 4 to 8192).")
            spin.valueChanged.connect(self._on_chunk_size_changed)
        times = QLabel("×", self)

        # Toggle for the chunk-grid preview drawn on the Image panel (state on `DatasetModule.chunk_grid_preview`).
        self._chunk_grid_button = QToolButton(self)
        self._chunk_grid_button.setCheckable(True)
        self._chunk_grid_button.setAutoRaise(True)
        self._chunk_grid_button.setFixedSize(24, 24)
        self._chunk_grid_button.setIconSize(QSize(18, 18))
        self._chunk_grid_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._chunk_grid_button.setToolTip("Show the export chunk grid on top of the image.")
        self._chunk_grid_button.toggled.connect(self._on_chunk_grid_toggled)
        self._refresh_chunk_grid_icon()

        self._find_best_button = QToolButton(self)
        self._find_best_button.setAutoRaise(True)
        self._find_best_button.setFixedSize(24, 24)
        self._find_best_button.setIconSize(QSize(18, 18))
        self._find_best_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._find_best_button.setIcon(_render_tabler_icon("search", get_active_theme().text_primary))
        self._find_best_button.clicked.connect(self._on_find_best_clicked)

        self._shard_combo = QComboBox(self)
        self._shard_combo.addItem("WL frame", "per_image")
        self._shard_combo.addItem("Cube", "per_spectral_cube")
        self._shard_combo.setMinimumWidth(100)
        self._shard_combo.setToolTip(
            "How many images are packed into a single shard file on disk: one wavelength frame, or a whole spectral cube."
        )

        self._grid_combo = QComboBox(self)
        self._grid_combo.addItem("Image corner", GRID_CORNER)
        self._grid_combo.addItem("Aligned to ROIs, lossless", GRID_LOSSLESS)
        self._grid_combo.addItem("Aligned to ROIs, lossy", GRID_LOSSY)
        self._grid_combo.setToolTip(
            "Where the chunk grid starts. Image corner: at pixel (0, 0). Aligned to ROIs: shifted so each ROI touches "
            "as few chunks as possible - lossless pads the left/top edge with empty pixels (nothing is lost), lossy "
            "keeps only the part of the image around the ROIs (the rest is not exported)."
        )
        self._grid_combo.currentIndexChanged.connect(self._on_grid_mode_changed)

        self._chunk_warning_label = QLabel("", self)
        self._chunk_warning_label.setWordWrap(True)
        self._chunk_warning_label.setStyleSheet(f"color: {get_active_theme().accent_gold}; font-size: 11px;")
        self._chunk_estimate_label = QLabel("", self)
        self._chunk_total_label = QLabel("", self)
        for info_label in (self._chunk_estimate_label, self._chunk_total_label):
            info_label.setWordWrap(True)
            info_label.setStyleSheet(f"color: {get_active_theme().text_muted}; font-size: 11px;")

        # Abbreviated label (2026-09-25): "Compression (lz4 + bitshuffle)" alone measured ~384px.
        self._compression_check = QCheckBox("Compression", self)
        self._compression_check.setChecked(True)
        self._compression_check.setToolTip("Compression: lz4 + bitshuffle. Toggle Zarr compression on or off.")
        self._compression_check.toggled.connect(self._refresh_estimates)

        self._skip_excluded_check = QCheckBox("Skip excluded images", self)
        self._skip_excluded_check.setToolTip(
            "Omit pixel data for excluded images/wavelengths/spectral cubes from the export."
        )

        # ElidingLabel: the text can carry a full destination path or an OSError message (eliding_label.py).
        self._status_label = ElidingLabel("No export running.", self)

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
        chunk_field_row.addWidget(times)
        chunk_field_row.addWidget(self._chunk_h_spin)
        chunk_field_row.addWidget(self._chunk_grid_button)
        chunk_field_row.addWidget(self._find_best_button)
        chunk_field = QWidget(self)
        chunk_field.setLayout(chunk_field_row)

        layout.addWidget(stacked_field("Chunk W × H", chunk_field, self))
        layout.addWidget(self._chunk_warning_label)
        grid_shard_row = QHBoxLayout()
        grid_shard_row.setContentsMargins(0, 0, 0, 0)
        grid_shard_row.setSpacing(6)
        grid_shard_row.addWidget(stacked_field("Grid position", self._grid_combo, self), 2)
        grid_shard_row.addWidget(stacked_field("Shard", self._shard_combo, self), 1)
        layout.addLayout(grid_shard_row)
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
        # What moves a ROI's read box. Group edits (cosmetic) only matter to Find best, which re-reads the layout.
        if roi_toolbox is not None:
            roi_toolbox.geometry_changed.connect(self._on_roi_layout_changed)
            roi_toolbox.roi_ids_renumbered.connect(self._on_roi_layout_changed)
        if geometry is not None:
            geometry.geometry_changed.connect(self._on_roi_layout_changed)
        if chromatic is not None:
            chromatic.chromatic_model_changed.connect(self._on_roi_layout_changed)

        if ui_state is not None:  # remembered across restarts (panels/ui_state.py); sizes before the grid toggle
            ui_state.bind("export/chunk_size", self._chunk_spin)
            if ui_state.get("export/chunk_h") is None:
                self._chunk_h_spin.setValue(self._chunk_spin.value())  # a saved square size restores as W x W
            ui_state.bind("export/chunk_h", self._chunk_h_spin)
            ui_state.bind("export/grid_position", self._grid_combo)
            ui_state.bind("export/chunk_grid_preview", self._chunk_grid_button)
            ui_state.bind("export/shard", self._shard_combo)
            ui_state.bind("export/compression", self._compression_check)
            ui_state.bind("export/skip_excluded", self._skip_excluded_check)
        self._refresh_estimates()

    # -- dataset-driven state ---------------------------------------------------

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        # One real plane read, once per load - not on every edit, which only needs the shape/dtype this gives.
        self._image_width = self._image_height = None
        self._image_dtype = None
        self._roi_layout = None
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
        self._roi_layout = None
        self._refresh_estimates()

    def _on_roi_layout_changed(self, *_args: object) -> None:
        self._roi_layout = None
        self._applied = None  # its offsets were for the old layout
        if self._grid_mode() != GRID_CORNER:
            self._refresh_estimates()

    # -- layout -----------------------------------------------------------------

    def _raw_shape(self) -> tuple[int, int] | None:
        if self._image_width is None or self._image_height is None:
            return None
        return int(self._image_height), int(self._image_width)

    def _grid_mode(self) -> str:
        return str(self._grid_combo.currentData() or GRID_CORNER)

    def _export_roi_layout(self) -> ExportRoiLayout | None:
        raw_shape = self._raw_shape()
        if raw_shape is None or self._roi_toolbox is None:
            return None
        if self._roi_layout is None or self._roi_layout.raw_shape != raw_shape:
            try:
                self._roi_layout = gather_export_roi_layout(self._roi_toolbox, self._geometry, self._chromatic, raw_shape)
            except Exception:
                logger.exception("Could not gather the ROI layout for the export")
                return None
        return self._roi_layout

    def current_layout(self) -> tuple[cp.ChunkLayout, ExportRoiLayout | None]:
        """The chunk layout the export would use now (size from the fields, grid offsets from the grid position)."""
        w, h = self._chunk_spin.value(), self._chunk_h_spin.value()
        if self._grid_mode() == GRID_CORNER:
            return cp.ChunkLayout(w, h), None
        roi_layout = self._export_roi_layout()
        if roi_layout is None or roi_layout.empty:
            return cp.ChunkLayout(w, h), None
        if self._applied is not None and (self._applied.chunk_w, self._applied.chunk_h) == (w, h):
            return self._applied, roi_layout
        layout = cp.align_grid(roi_layout.boxes, w, h) or cp.ChunkLayout(w, h)
        raw_h, raw_w = roi_layout.raw_shape
        # A chunk spanning a whole axis must start at the edge, or padding would add a second chunk on that axis.
        ox = 0 if w >= raw_w else layout.offset_x
        oy = 0 if h >= raw_h else layout.offset_y
        return cp.ChunkLayout(w, h, ox, oy, layout.tolerance_x, layout.tolerance_y), roi_layout

    def _current_frame(self) -> cp.ExportFrame | None:
        """The stored window for the current settings; ``None`` = the whole image, unshifted."""
        raw_shape = self._raw_shape()
        if raw_shape is None:
            return None
        layout, roi_layout = self.current_layout()
        if roi_layout is None:
            return None
        bpp = int(self._image_dtype.itemsize) if self._image_dtype is not None else 2
        return cp.export_frame(layout, roi_layout.boxes, raw_shape, lossless=self._grid_mode() == GRID_LOSSLESS,
                               bytes_per_pixel=bpp)

    def _refresh_estimates(self, *_args: object) -> None:
        w, h = self._chunk_spin.value(), self._chunk_h_spin.value()
        warnings = cp.chunk_warnings(w, h, compression=self._compression_check.isChecked())
        self._chunk_warning_label.setText("\n".join(warnings))
        self._chunk_warning_label.setVisible(bool(warnings))
        raw_shape = self._raw_shape()
        self._find_best_button.setEnabled(raw_shape is not None and self._roi_toolbox is not None)
        self._find_best_button.setToolTip(
            "Find best: suggest chunk layouts that suit the ROI layout."
            if self._roi_toolbox is not None and raw_shape is not None
            else "Find best: load a dataset and place ROIs first - the suggestions are built from the ROI layout."
        )
        if raw_shape is None:
            self._chunk_estimate_label.setText("")
            self._chunk_total_label.setText("")
            self._update_preview()
            return
        frame = self._current_frame()
        if frame is None:
            nx, ny = -(-raw_shape[1] // w), -(-raw_shape[0] // h)
            per_plane = nx * ny
            if self._grid_mode() != GRID_CORNER:
                second = "No ROIs to align to: the grid stays at the image corner."
            else:
                second = ""
        else:
            per_plane = frame.chunks_per_plane
            if frame.lossless:
                second = (f"Grid aligned to the ROIs: pads {-frame.origin_x} px left, {-frame.origin_y} px top "
                          f"(stored {frame.width} x {frame.height} px, nothing lost).")
            else:
                drop = 100.0 * frame.discarded_px / (raw_shape[0] * raw_shape[1])
                second = (f"Grid aligned to the ROIs: keeps {frame.width} x {frame.height} px from "
                          f"({frame.origin_x}, {frame.origin_y}); {drop:.0f}% of the image is not exported.")
        self._chunk_estimate_label.setText(
            f"-> {per_plane} chunks/plane, {per_plane * self._image_count:,} chunks total ({self._image_count:,} images)"
        )
        self._chunk_total_label.setText(second)
        self._chunk_total_label.setVisible(bool(second))
        self._update_preview()

    # -- chunk-grid preview -----------------------------------------------------

    def _on_chunk_size_changed(self, _value: int) -> None:
        self._refresh_estimates()

    def _on_grid_mode_changed(self, _index: int) -> None:
        self._refresh_estimates()

    def _on_chunk_grid_toggled(self, _checked: bool) -> None:
        self._update_preview()
        self._refresh_chunk_grid_icon()

    def _update_preview(self) -> None:
        layout, _roi_layout = self.current_layout() if self._chunk_grid_button.isChecked() else (cp.ChunkLayout(
            self._chunk_spin.value(), self._chunk_h_spin.value()), None)
        self._dataset_module.set_chunk_grid_preview(
            self._chunk_grid_button.isChecked(), layout.chunk_w, layout.chunk_h, layout.offset_x, layout.offset_y
        )

    def _refresh_chunk_grid_icon(self) -> None:
        checked = self._chunk_grid_button.isChecked()
        color = _CHUNK_GRID_ACTIVE_COLOR if checked else get_active_theme().text_primary
        self._chunk_grid_button.setIcon(_render_tabler_icon("grid-4x4", color))

    # -- Find best ----------------------------------------------------------------

    def _on_find_best_clicked(self) -> None:
        self._roi_layout = None  # fresh: picks up group edits, which do not invalidate the cache
        roi_layout = self._export_roi_layout()
        if roi_layout is None or roi_layout.empty:
            self._status_label.setText("Find best: place ROIs first - the suggestions are built from the ROI layout.")
            return
        if self._dialog is not None:
            self._dialog.close()
        dataset = self._dataset_module
        cubes = dataset.spectral_cubes()
        pick_cubes = [cubes[i] for i in sorted({0, len(cubes) // 2, len(cubes) - 1})][:_SAMPLE_CUBES] if cubes else []

        def load_sample_planes() -> list[np.ndarray]:  # runs on the dialog's worker thread
            planes = []
            for cube in pick_cubes:
                wls = dataset.wavelengths_for_cube(cube)
                for i in sorted({round(k * (len(wls) - 1) / max(_SAMPLE_WAVELENGTHS - 1, 1)) for k in range(_SAMPLE_WAVELENGTHS)}):
                    if wls:
                        planes.append(np.asarray(dataset.load_plane(cube, wls[int(i)])))
            return planes

        self._dialog = ChunkLayoutDialog(
            roi_layout,
            load_sample_planes,
            planes=self._image_count,
            bytes_per_pixel=int(self._image_dtype.itemsize) if self._image_dtype is not None else 2,
            compression=self._compression_check.isChecked(),
            lossless=self._grid_mode() != GRID_LOSSY,
            parent=self,
        )
        self._dialog.layout_chosen.connect(self._on_layout_chosen)
        self._dialog.show()

    def _on_layout_chosen(self, suggestion: cp.Suggestion, lossless: bool) -> None:
        layout = suggestion.layout
        self._applied = layout  # first: the refreshes the setters below trigger use its exact offsets
        self._chunk_spin.setValue(layout.chunk_w)
        self._chunk_h_spin.setValue(layout.chunk_h)
        self._grid_combo.setCurrentIndex(self._grid_combo.findData(GRID_LOSSLESS if lossless else GRID_LOSSY))
        self._refresh_estimates()

    # -- user actions ---------------------------------------------------------

    def _on_export_clicked(self) -> None:
        home = self._dataset_module.dataset_home()
        parent_dir = QFileDialog.getExistingDirectory(
            self, "Choose export location for Stack to Zarr", str(home) if home is not None else ""
        )
        if not parent_dir:
            return
        destination = Path(parent_dir) / self._build_export_folder_name(home)
        layout, _roi_layout = self.current_layout()
        frame = self._current_frame()
        try:
            self._dataset_module.export_to_ome_zarr(
                destination,
                chunk_size_px=layout.chunk_w,
                chunk_shape=(layout.chunk_h, layout.chunk_w),
                frame=(frame.origin_x, frame.origin_y, frame.width, frame.height) if frame is not None else None,
                compression_enabled=self._compression_check.isChecked(),
                shard_mode=self._shard_combo.currentData(),
                skip_excluded=self._skip_excluded_check.isChecked(),
                pixel_size_um=self._calibrated_pixel_size_um(),
            )
        except RuntimeError as exc:
            self._status_label.setText(str(exc))
            return
        self._set_exporting(True)
        self._status_label.setText("Starting export...")

    def _calibrated_pixel_size_um(self) -> tuple[float, float] | None:
        """(x, y) um/px when a calibration is switched on, else None. Valid for
        the raw pixels: rotation, flip and crop never change the scale."""
        if self._geometry is None or not self._geometry.can_display_micrometers():
            return None
        settings = self._geometry.settings()
        return float(settings.microns_per_pixel_x), float(settings.microns_per_pixel_y)

    def _build_export_folder_name(self, home: Path | None) -> str:
        default_name = home.name if home is not None else "export"
        if self._image_width is None or self._image_height is None or self._image_dtype is None:
            # No successful plane probe - a plain sanitized name is still a valid destination.
            return sanitize_ome_zarr_export_name(default_name)
        frame = self._current_frame()
        width, height = (frame.width, frame.height) if frame is not None else (self._image_width, self._image_height)
        return build_ome_zarr_export_folder_name(
            default_name,
            width=width,
            height=height,
            spectral_cube_count=len(self._dataset_module.spectral_cubes()),
            wavelength_count=len(self._dataset_module.wavelengths()),
            chunk_size_px=self._chunk_spin.value(),
            shard_mode=self._shard_combo.currentData(),
            compression_enabled=self._compression_check.isChecked(),
            dtype=self._image_dtype,
            chunk_shape=(self._chunk_h_spin.value(), self._chunk_spin.value()),
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
        # No modal dialog: a cancelled export reaches this same path, and a "failed" dialog after the user's own
        # Cancel click would be bad UX.
        self._set_exporting(False)
        self._status_label.setText(f"Export failed: {message}")
