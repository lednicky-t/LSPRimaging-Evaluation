"""The Image panel's "Export" ribbon tab (2026-10-20, maintainer request: move the Workflow panel's Dataset ->
Export section here, in the ribbon look - captioned sections, free-standing icons, frameless fields).

Screen purpose: export the loaded dataset to OME-Zarr, choosing the chunk grid it is written with.

Layout (one ribbon row):

    [rot]        |  [grid] [W] x [H] [search]  |  [Position v] [Shard v]  |  [zip]     [ban]       |  [export]
    Rotate/crop          Chunk grid               Position     Shard        Compress  Skip excl.     Export

- **Rotate/crop (2026-10-20, maintainer decision; default on):** export the image as shown - rotated (Lanczos4),
  flipped and cropped (`GeometryModule` settings, passed as `preprocessing`) - in the source's bit depth. Pixels the
  rotation creates are stored as 0 and listed in the file's `valid_mask`; readers load them as NaN
  (`dataset/io.py`, docs/rotated_zarr_export_interpolation_2026-10-20.md). The chunk grid, the ROI alignment and
  the size estimates then work in that processed space (the grid is drawn straight). Off: the raw pixels; the grid
  lies in raw pixels and is drawn tilted under a rotation. A session started on a rotated export needs no rotation:
  its pixels are this session's processed image, so ROI positions carry over 1:1.

- **Chunk grid:** show the grid on the image (state on `DatasetModule.chunk_grid_preview`, drawn by `ImagePanel`;
  this tab and the panel never read each other's state), chunk width x height in px, and Find best
  (`chunk_layout_dialog.py`; Apply sets width, height, grid position and the exact grid offsets of the chosen
  suggestion, kept until the size is edited by hand). Chunks are always W x H (no square/rectangle switch). The
  width field keeps the old `export/chunk_size` key, so a saved square size restores as width; the height
  defaults to the width until it is saved itself.
- **Position:** where the grid starts - image corner, or aligned to the ROIs, lossless (pads the left/top edge)
  or lossy (keeps only the chunk-aligned part around the ROIs). The alignment is computed from the ROI read
  boxes in the export's pixel space (`export_roi_layout.py`, `dataset.chunk_plan.align_grid`) whenever the size or position
  changes, or the ROIs/geometry/chromatic correction change (cheap integer arithmetic; the preview follows).
- **Shard:** one wavelength frame or one whole cube per shard file.
- **Compress / Skip excl.:** lz4 + bitshuffle on/off; leave out excluded images.
- **Export:** asks for a folder, then exports on the module's worker. While it runs the button is Cancel;
  progress is on the status bar's task indicator (`DatasetModule.task_progress`).

The Workflow section's live text lines have no room on a ribbon row: the chunk counts, the grid alignment and
the warnings (`chunk_plan.chunk_warnings`: chunks that will not compress or are small) are in the W x H fields'
tooltip and the panel's "i" icon (`info_html`), and the fields turn amber while a warning applies. One-off
messages (Find best without ROIs, an export that cannot start) go to the panel's status (`status` callback).

Scoped down from the stable
app, flagged rather than silently dropped: no name prompt (the dataset's folder name), no destination-collision
dialog (`export_ome_zarr_dataset` writes to a temp sibling and swaps in only on success), no plan confirmation.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...dataset import chunk_plan as cp
from ...dataset.io import build_ome_zarr_export_folder_name, probe_ome_zarr_export_shape, sanitize_ome_zarr_export_name
from ...dataset.model import ImageDataset
from ...image_tools import GeometryModule
from ...image_tools.geometry.transform import apply_spatial_preprocessing, spatial_output_shape
from ...io._zarr_export_worker import _to_stored, value_step
from ..ribbon_fields import style_ribbon_combo, style_ribbon_number_field
from ..ribbon_group import labeled_icon_group, ribbon_separator
from ..ui_state import UiStateStore
from .chunk_layout_dialog import ChunkLayoutDialog
from .export_roi_layout import ExportRoiLayout, gather_export_roi_layout
from .general_group import ICON_SIZE, style_general_icon_button

GRID_CORNER, GRID_LOSSLESS, GRID_LOSSY = "corner", "lossless", "lossy"
_SAMPLE_CUBES = 3
_SAMPLE_WAVELENGTHS = 5
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue
_GRID_ON_COLOR = "#84cc16"  # lime, as the grid toggle had in the Workflow section
_SKIP_ON_COLOR = "#ef4444"  # red: those images are left out
_CANCEL_COLOR = "#ef4444"
_CHUNK_WIDEST_TEXT = "8192"
_CHUNK_TIP = "Chunk {axis} in pixels (4 to 8192)."
_CHUNK_H_KEY = "export/chunk_h"  # read once (is a height saved yet?) and bound: one spelling for both

logger = logging.getLogger(__name__)


class ExportTab(QWidget):
    """See the module docstring. `info_changed` fires when the counts / warnings in `info_html` change."""

    info_changed = pyqtSignal()

    def __init__(
        self,
        dataset: DatasetModule,
        parent: QWidget | None = None,
        geometry: GeometryModule | None = None,
        ui_state: UiStateStore | None = None,
        roi_toolbox: object | None = None,
        chromatic: object | None = None,
        status: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        # Geometry: the calibration (um/px) written into the export's metadata, and the raw <-> processed map for the
        # ROI layout. The export never applies rotation/flip/crop to the pixels.
        self._geometry = geometry
        self._roi_toolbox = roi_toolbox
        self._chromatic = chromatic
        self._status = status
        # Probed once per dataset load (_on_dataset_loaded) from one real plane.
        self._image_width: int | None = None
        self._image_height: int | None = None
        self._image_dtype: np.dtype | None = None
        self._image_count = 0
        self._has_dataset = False
        self._roi_layout: ExportRoiLayout | None = None  # cached; dropped when ROIs/geometry/chromatic change
        self._applied: cp.ChunkLayout | None = None  # from Find best, kept while the size is unchanged
        self._dialog: ChunkLayoutDialog | None = None
        self._exporting = False
        self._export_percent = 0
        # What `info_html` and the fields' tooltip show (the Workflow section had a label for each).
        self._warning_text = ""
        self._estimate_text = ""
        self._layout_text = ""

        self._apply_tools_button = self._icon_button(
            "Apply rotation/crop: export the image as shown - rotated (Lanczos4), flipped and cropped - instead of the "
            "raw camera pixels. Pixels the rotation creates are stored as 0 and marked in the file's valid mask, so "
            "they load as empty (NaN). Off: the raw pixels, unchanged.",
            checkable=True,
        )
        self._apply_tools_button.setChecked(True)
        self._apply_tools_button.toggled.connect(self._on_apply_tools_toggled)

        self._chunk_grid_button = self._icon_button("Show the export chunk grid on top of the image.", checkable=True)
        self._chunk_grid_button.toggled.connect(self._on_chunk_grid_toggled)

        self._chunk_spin = QSpinBox(self)  # width (keeps its name and ui_state key from the square-only days)
        self._chunk_h_spin = QSpinBox(self)
        for spin in (self._chunk_spin, self._chunk_h_spin):
            spin.setRange(4, 8192)
            spin.setValue(64)
            spin.setKeyboardTracking(False)
            style_ribbon_number_field(spin, _CHUNK_WIDEST_TEXT)
            spin.valueChanged.connect(self._on_chunk_size_changed)
        self._field_style = self._chunk_spin.styleSheet()  # put back when a warning's amber goes away
        times = QLabel("×", self)

        self._find_best_button = self._icon_button("")
        self._find_best_button.clicked.connect(self._on_find_best_clicked)

        self._grid_combo = QComboBox(self)
        self._grid_combo.addItem("Corner", GRID_CORNER)
        self._grid_combo.addItem("ROIs, lossless", GRID_LOSSLESS)
        self._grid_combo.addItem("ROIs, lossy", GRID_LOSSY)
        self._grid_combo.setToolTip(
            "Where the chunk grid starts. Corner: at pixel (0, 0). ROIs: shifted so each ROI touches as few "
            "chunks as possible - lossless pads the left/top edge with empty pixels (nothing is lost), lossy "
            "keeps only the part of the image around the ROIs (the rest is not exported)."
        )
        style_ribbon_combo(self._grid_combo)
        self._grid_combo.currentIndexChanged.connect(self._on_grid_mode_changed)

        self._shard_combo = QComboBox(self)
        self._shard_combo.addItem("WL frame", "per_image")
        self._shard_combo.addItem("Cube", "per_spectral_cube")
        self._shard_combo.setToolTip(
            "How many images are packed into a single shard file on disk: one wavelength frame, or a whole spectral cube."
        )
        style_ribbon_combo(self._shard_combo)

        self._compression_button = self._icon_button(
            "Compression (lz4 + bitshuffle) on or off.", checkable=True
        )
        self._compression_button.setChecked(True)
        self._compression_button.toggled.connect(self._refresh_estimates)
        self._compression_button.toggled.connect(self._refresh_icons)

        self._skip_excluded_button = self._icon_button(
            "Skip excluded images: leave the pixel data of excluded images / wavelengths / cubes out of the export.",
            checkable=True,
        )
        self._skip_excluded_button.toggled.connect(self._refresh_icons)

        self._export_button = self._icon_button("")
        self._export_button.clicked.connect(self._on_export_clicked)

        chunk_row = QWidget(self)
        chunk_layout = QHBoxLayout(chunk_row)
        chunk_layout.setContentsMargins(0, 0, 0, 0)
        chunk_layout.setSpacing(2)
        chunk_layout.addWidget(self._chunk_grid_button)
        chunk_layout.addWidget(self._chunk_spin)
        chunk_layout.addWidget(times)
        chunk_layout.addWidget(self._chunk_h_spin)
        chunk_layout.addWidget(self._find_best_button)
        apply_group, _ = labeled_icon_group(self, self._apply_tools_button, "Rotate/crop")
        chunk_group, _ = labeled_icon_group(self, chunk_row, "Chunk grid")
        position_group, _ = labeled_icon_group(self, self._grid_combo, "Position")
        shard_group, _ = labeled_icon_group(self, self._shard_combo, "Shard")
        compression_group, _ = labeled_icon_group(self, self._compression_button, "Compress")
        skip_group, _ = labeled_icon_group(self, self._skip_excluded_button, "Skip excl.")
        export_group, self._export_caption = labeled_icon_group(self, self._export_button, "Export")

        self._separators: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, group in enumerate(
            (apply_group, chunk_group, (position_group, shard_group), (compression_group, skip_group), export_group)
        ):
            if index:
                separator = ribbon_separator(self)
                self._separators.append(separator)
                layout.addWidget(separator)
            for widget in group if isinstance(group, tuple) else (group,):
                layout.addWidget(widget)
        layout.addStretch(1)

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
            geometry.geometry_changed.connect(self._on_geometry_changed)
        if chromatic is not None:
            chromatic.chromatic_model_changed.connect(self._on_roi_layout_changed)

        if ui_state is not None:
            self.bind_ui_state(ui_state)
        self._refresh_icons()
        self._refresh_estimates()

    # -- public ---------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Restore the settings as last left and keep saving them (`panels/ui_state.py`). Same keys as the
        Workflow section had, so saved settings carry over. Sizes before the grid toggle."""
        store.bind("export/apply_image_tools", self._apply_tools_button)
        store.bind("export/chunk_size", self._chunk_spin)
        if store.get(_CHUNK_H_KEY) is None:
            self._chunk_h_spin.setValue(self._chunk_spin.value())  # a saved square size restores as W x W
        store.bind(_CHUNK_H_KEY, self._chunk_h_spin)
        store.bind("export/grid_position", self._grid_combo)
        store.bind("export/chunk_grid_preview", self._chunk_grid_button)
        store.bind("export/shard", self._shard_combo)
        store.bind("export/compression", self._compression_button)
        store.bind("export/skip_excluded", self._skip_excluded_button)

    def info_html(self) -> str:
        """The "i" icon's text while this tab is open: what the tab does, then the current counts and warnings."""
        lines = [
            "<b>Export to OME-Zarr</b><br>"
            "Writes the images in chunks of W&nbsp;&times;&nbsp;H pixels. Rotate/crop on: as shown - rotated "
            "(Lanczos4), flipped and cropped, same bit depth; pixels the rotation creates load as empty. Off: the raw "
            "camera pixels. Grid icon: show the chunk grid on the image. Magnifier: suggest chunk layouts that suit the ROIs. "
            "Position: start the grid at the image corner or align it to the ROIs. "
            "Shard: images per file on disk."
        ]
        for text in (self._estimate_text, self._layout_text):
            if text:
                lines.append(html.escape(text))
        if self._warning_text:
            lines.append(f"<span style='color: {get_active_theme().accent_gold};'>"
                         f"{html.escape(self._warning_text).replace(chr(10), '<br>')}</span>")
        return "<br><br>".join(lines)

    def refresh_theme(self) -> None:
        for button in self._buttons():
            style_general_icon_button(button)
        self._refresh_icons()
        self._refresh_estimates()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {get_active_theme().control_border};")

    # -- internals ------------------------------------------------------------

    def _icon_button(self, tooltip: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        style_general_icon_button(button)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        return button

    def _buttons(self) -> tuple[QToolButton, ...]:
        return (self._apply_tools_button, self._chunk_grid_button, self._find_best_button, self._compression_button,
                self._skip_excluded_button, self._export_button)

    def _icon(self, name: str, color: str):
        return load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)

    def _refresh_icons(self, *_args: object) -> None:
        theme = get_active_theme()
        dim = theme.text_dim
        self._apply_tools_button.setIcon(
            self._icon("rotate-clockwise-2", _ACTIVE_COLOR if self._apply_tools_button.isChecked() else dim))
        self._chunk_grid_button.setIcon(
            self._icon("grid-4x4", _GRID_ON_COLOR if self._chunk_grid_button.isChecked() else dim))
        self._find_best_button.setIcon(self._icon("search", dim))
        self._compression_button.setIcon(
            self._icon("file-zip", _ACTIVE_COLOR if self._compression_button.isChecked() else dim))
        self._skip_excluded_button.setIcon(
            self._icon("ban", _SKIP_ON_COLOR if self._skip_excluded_button.isChecked() else dim))
        if self._exporting:
            self._export_button.setIcon(self._icon("player-stop", _CANCEL_COLOR))
            self._export_button.setToolTip(f"Cancel the export ({self._export_percent}% done).")
            self._export_caption.setText("Cancel")
        else:
            self._export_button.setIcon(self._icon("database-export", dim))
            self._export_button.setToolTip(
                "Export the loaded dataset to OME-Zarr (choose a folder)." if self._has_dataset
                else "Export: load a dataset first."
            )
            self._export_caption.setText("Export")
        self._export_button.setEnabled(self._exporting or self._has_dataset)

    # -- dataset-driven state ---------------------------------------------------

    def _on_dataset_loaded(self, dataset: ImageDataset) -> None:
        # One real image read, once per load - not on every edit, which only needs the raw shape and the stored dtype.
        # The stored dtype comes from the file itself (`probe_ome_zarr_export_shape`, what the export writes), not from
        # `load_plane`, which is always float32: until 2026-10-20 that made the folder name say f32 and Find best count
        # 4 bytes per pixel (and sample float32 compression) for uint16 data.
        self._has_dataset = True
        self._image_width = self._image_height = None
        self._image_dtype = None
        self._roi_layout = None
        self._image_count = sum(
            len(self._dataset_module.wavelengths_for_cube(cube)) for cube in self._dataset_module.spectral_cubes()
        )
        try:
            self._image_height, self._image_width, self._image_dtype = probe_ome_zarr_export_shape(dataset, None)
        except Exception:
            logger.exception("Could not probe the dataset for the export estimate")
        self._refresh_icons()
        self._refresh_estimates()

    def _on_dataset_cleared(self) -> None:
        self._has_dataset = False
        self._image_width = self._image_height = None
        self._image_dtype = None
        self._image_count = 0
        self._roi_layout = None
        self._refresh_icons()
        self._refresh_estimates()

    def _on_roi_layout_changed(self, *_args: object) -> None:
        self._roi_layout = None
        self._applied = None  # its offsets were for the old layout
        if self._grid_mode() != GRID_CORNER:
            self._refresh_estimates()

    def _on_geometry_changed(self, *_args: object) -> None:
        # Moves the ROIs relative to the raw pixels, and (when the export applies it) changes the exported image size.
        self._roi_layout = None
        self._applied = None
        self._refresh_estimates()

    def _on_apply_tools_toggled(self, _checked: bool) -> None:
        self._roi_layout = None  # the layout lives in the other pixel space now
        self._applied = None
        self._refresh_icons()
        self._refresh_estimates()

    # -- layout -----------------------------------------------------------------

    def _applies_image_tools(self) -> bool:
        """Whether the export applies rotation/flip/crop: the toggle is on and the image tools are switched on."""
        return (
            self._apply_tools_button.isChecked()
            and self._geometry is not None
            and bool(self._geometry.settings().image_tools_enabled)
        )

    def _export_shape(self) -> tuple[int, int] | None:
        """(height, width) of the image the export stores before any frame: the processed image when it applies the
        image tools, else the raw one."""
        if self._image_width is None or self._image_height is None:
            return None
        raw = (int(self._image_height), int(self._image_width))
        if self._applies_image_tools():
            return tuple(int(v) for v in spatial_output_shape(raw, self._geometry.settings()))
        return raw

    def _grid_mode(self) -> str:
        return str(self._grid_combo.currentData() or GRID_CORNER)

    def _export_roi_layout(self) -> ExportRoiLayout | None:
        raw_shape = self._export_shape()
        if raw_shape is None or self._roi_toolbox is None:
            return None
        if self._roi_layout is None or self._roi_layout.raw_shape != raw_shape:
            # ROIs are in processed space: with the image tools applied that *is* the export's space (no mapping).
            geometry = None if self._applies_image_tools() else self._geometry
            try:
                self._roi_layout = gather_export_roi_layout(self._roi_toolbox, geometry, self._chromatic, raw_shape)
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
        raw_shape = self._export_shape()
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
        self._warning_text = "\n".join(cp.chunk_warnings(w, h, compression=self._compression_button.isChecked()))
        raw_shape = self._export_shape()
        self._find_best_button.setEnabled(raw_shape is not None and self._roi_toolbox is not None)
        self._find_best_button.setToolTip(
            "Find best: suggest chunk layouts that suit the ROI layout."
            if self._roi_toolbox is not None and raw_shape is not None
            else "Find best: load a dataset and place ROIs first - the suggestions are built from the ROI layout."
        )
        if raw_shape is None:
            self._estimate_text = self._layout_text = ""
        else:
            frame = self._current_frame()
            if frame is None:
                per_plane = -(-raw_shape[1] // w) * -(-raw_shape[0] // h)
                self._layout_text = (
                    "No ROIs to align to: the grid stays at the image corner." if self._grid_mode() != GRID_CORNER else ""
                )
            else:
                per_plane = frame.chunks_per_plane
                if frame.lossless:
                    self._layout_text = (f"Grid aligned to the ROIs: pads {-frame.origin_x} px left, {-frame.origin_y} px "
                                         f"top (stored {frame.width} x {frame.height} px, nothing lost).")
                else:
                    drop = 100.0 * frame.discarded_px / (raw_shape[0] * raw_shape[1])
                    self._layout_text = (f"Grid aligned to the ROIs: keeps {frame.width} x {frame.height} px from "
                                         f"({frame.origin_x}, {frame.origin_y}); {drop:.0f}% of the image is not exported.")
            self._estimate_text = (
                f"-> {per_plane} chunks/plane, {per_plane * self._image_count:,} chunks total ({self._image_count:,} images)"
            )
        self._refresh_fields()
        self._update_preview()
        self.info_changed.emit()

    def _refresh_fields(self) -> None:
        """Amber text while a warning applies; the counts, alignment and warnings in the fields' tooltip."""
        warn_color = get_active_theme().accent_gold
        style = self._field_style + (f" QAbstractSpinBox {{ color: {warn_color}; }}" if self._warning_text else "")
        details = "\n".join(text for text in (self._estimate_text, self._layout_text, self._warning_text) if text)
        for spin, axis in ((self._chunk_spin, "width"), (self._chunk_h_spin, "height")):
            if spin.styleSheet() != style:
                spin.setStyleSheet(style)
            tip = _CHUNK_TIP.format(axis=axis)
            spin.setToolTip(f"{tip}\n\n{details}" if details else tip)

    # -- chunk-grid preview -----------------------------------------------------

    def _on_chunk_size_changed(self, _value: int) -> None:
        self._refresh_estimates()

    def _on_grid_mode_changed(self, _index: int) -> None:
        self._refresh_estimates()

    def _on_chunk_grid_toggled(self, _checked: bool) -> None:
        self._update_preview()
        self._refresh_icons()

    def _update_preview(self) -> None:
        layout, _roi_layout = self.current_layout() if self._chunk_grid_button.isChecked() else (cp.ChunkLayout(
            self._chunk_spin.value(), self._chunk_h_spin.value()), None)
        self._dataset_module.set_chunk_grid_preview(
            self._chunk_grid_button.isChecked(), layout.chunk_w, layout.chunk_h, layout.offset_x, layout.offset_y,
            processed=self._applies_image_tools(),
        )

    # -- Find best ----------------------------------------------------------------

    def _report(self, text: str) -> None:
        if self._status is not None:
            self._status(text)

    def _on_find_best_clicked(self) -> None:
        self._roi_layout = None  # fresh: picks up group edits, which do not invalidate the cache
        roi_layout = self._export_roi_layout()
        if roi_layout is None or roi_layout.empty:
            self._report("Find best: place ROIs first - the suggestions are built from the ROI layout.")
            return
        if self._dialog is not None:
            self._dialog.close()
        dataset = self._dataset_module
        cubes = dataset.spectral_cubes()
        pick_cubes = [cubes[i] for i in sorted({0, len(cubes) // 2, len(cubes) - 1})][:_SAMPLE_CUBES] if cubes else []
        # Compression is sampled on what the export stores: rotated/cropped when it applies the image tools, in the
        # stored dtype (`load_plane` is float32; float32 tiles compress differently from uint16 ones).
        settings = self._geometry.settings() if self._applies_image_tools() else None
        stored_dtype = self._image_dtype if self._image_dtype is not None else np.dtype(np.float32)

        def load_sample_planes() -> list[np.ndarray]:  # runs on the dialog's worker thread
            planes = []
            for cube in pick_cubes:
                wls = dataset.wavelengths_for_cube(cube)
                for i in sorted({round(k * (len(wls) - 1) / max(_SAMPLE_WAVELENGTHS - 1, 1)) for k in range(_SAMPLE_WAVELENGTHS)}):
                    if wls:
                        plane = np.asarray(dataset.load_plane(cube, wls[int(i)]))
                        step = 1
                        if settings is not None:
                            # The export rounds rotated values onto the camera's step: sample the same values.
                            if np.issubdtype(stored_dtype, np.integer):
                                step = value_step(plane.astype(stored_dtype))
                            plane = apply_spatial_preprocessing(plane, settings)
                        planes.append(_to_stored(plane, stored_dtype, 0.0, step))
            return planes

        self._dialog = ChunkLayoutDialog(
            roi_layout,
            load_sample_planes,
            planes=self._image_count,
            bytes_per_pixel=int(self._image_dtype.itemsize) if self._image_dtype is not None else 2,
            compression=self._compression_button.isChecked(),
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

    # -- export -----------------------------------------------------------------

    def _on_export_clicked(self) -> None:
        if self._exporting:  # the button is Cancel while an export runs
            self._dataset_module.cancel_export()
            self._report("Cancelling the export...")
            return
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
                compression_enabled=self._compression_button.isChecked(),
                shard_mode=self._shard_combo.currentData(),
                skip_excluded=self._skip_excluded_button.isChecked(),
                pixel_size_um=self._calibrated_pixel_size_um(),
                preprocessing=self._geometry.settings() if self._applies_image_tools() else None,
            )
        except RuntimeError as exc:
            self._report(f"Export: {exc}")
            return
        self._set_exporting(True)

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
        source_height, source_width = self._export_shape()  # the exported image's size (rotated/cropped when applied)
        width, height = (frame.width, frame.height) if frame is not None else (source_width, source_height)
        return build_ome_zarr_export_folder_name(
            default_name,
            width=width,
            height=height,
            spectral_cube_count=len(self._dataset_module.spectral_cubes()),
            wavelength_count=len(self._dataset_module.wavelengths()),
            chunk_size_px=self._chunk_spin.value(),
            shard_mode=self._shard_combo.currentData(),
            compression_enabled=self._compression_button.isChecked(),
            dtype=self._image_dtype,
            chunk_shape=(self._chunk_h_spin.value(), self._chunk_spin.value()),
        )

    def _set_exporting(self, exporting: bool) -> None:
        self._exporting = exporting
        self._export_percent = 0
        self._refresh_icons()

    def _on_export_progress(self, percent: int, _message: str) -> None:
        self._export_percent = max(0, min(100, int(percent)))
        if self._exporting:
            self._export_button.setToolTip(f"Cancel the export ({self._export_percent}% done).")

    def _on_export_finished(self, _destination: Path) -> None:
        # The result (and the destination) is on the status bar's task indicator.
        self._set_exporting(False)

    def _on_export_failed(self, _message: str) -> None:
        # No dialog: a cancelled export reaches this same path; the reason is on the task indicator.
        self._set_exporting(False)
