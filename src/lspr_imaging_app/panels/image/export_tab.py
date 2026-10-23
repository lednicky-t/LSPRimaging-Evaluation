"""The Image panel's "Export" ribbon tab (2026-10-20, maintainer request: move the Workflow panel's Dataset ->
Export section here, in the ribbon look - captioned sections, free-standing icons, frameless fields).

Screen purpose: export the loaded dataset to OME-Zarr, choosing the chunk grid it is written with.

Layout (one ribbon row):

    [grid] [W] x [H] [style v]  |  [Shard v]  [zip]     [ban]       |  [export]
          Chunk grid                   Shard        Compress  Skip excl.     Export

- **Rotate/crop (2026-10-20; always on since 2026-10-21, the on/off icon was removed):** export the image as shown - rotated (Lanczos4),
  flipped and cropped (`GeometryModule` settings, passed as `preprocessing`) - in the source's bit depth. Pixels the
  rotation creates are stored as 0 and listed in the file's `valid_mask`; readers load them as NaN
  (`dataset/io.py`, docs/rotated_zarr_export_interpolation_2026-10-20.md). The chunk grid, the ROI alignment and
  the size estimates then work in that processed space (the grid is drawn straight). Only when the Image tools
  are switched off does the export fall back to the raw pixels. A session started on a rotated export needs no rotation:
  its pixels are this session's processed image, so ROI positions carry over 1:1.

- **Chunk grid:** show the grid on the image (state on `DatasetModule.chunk_grid_preview`, drawn by `ImagePanel`;
  this tab and the panel never read each other's state), chunk width x height in px, and the chunk-style menu
  (`chunk_style_picker.py`, 2026-10-21, replaced the Find best dialog: 1 ROI / column / row per chunk; a
  pick applies at once - width, height, grid position and the exact grid offsets of that
  `chunk_plan.suggest_layouts` entry, kept until the size is edited by hand; no scores or compression sample any
  more, `chunk_layout_dialog.py` is no longer used). Chunks are always W x H (no square/rectangle switch). The
  width field keeps the old `export/chunk_size` key, so a saved square size restores as width; the height
  defaults to the width until it is saved itself.
- **Grid position (no field since 2026-10-21):** always aligned to the ROIs, so each ROI touches as few chunks as
  possible; the left/top edge is padded with empty pixels (less than one chunk, nothing is lost). Without ROIs the grid
  starts at the image corner. The old Corner and lossy-trim choices were removed: auto crop leaves a border, and parts
  of the image are left out by cropping in the Image tools. The alignment is computed from the ROI read
  boxes in the export's pixel space (`export_roi_layout.py`, `dataset.chunk_plan.align_grid`) whenever the size
  changes, or the ROIs/geometry/chromatic correction change (cheap integer arithmetic; the preview follows).
- **Shard:** one wavelength frame or one whole cube per shard file.
- **Compress / Skip excl.:** lz4 + bitshuffle on/off; leave out excluded images.
- **Export:** asks for a folder, then exports on the module's worker. While it runs the button is Cancel;
  progress is on the status bar's task indicator (`DatasetModule.task_progress`).

The Workflow section's live text lines have no room on a ribbon row: the chunk counts, the grid alignment and
the warnings (`chunk_plan.chunk_warnings`: chunks that will not compress or are small) are in the W x H fields'
tooltip and the panel's "i" icon (`info_html`), and the fields turn amber while a warning applies. One-off
messages (a chunk style without ROIs, an export that cannot start) go to the panel's status (`status` callback).

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
from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...dataset import chunk_plan as cp
from ...dataset.io import build_ome_zarr_export_folder_name, probe_ome_zarr_export_shape, sanitize_ome_zarr_export_name
from ...dataset.model import ImageDataset
from ...image_tools import GeometryModule
from ...image_tools.geometry.transform import spatial_output_shape
from ...storage.session_carry import ExportTransform
from ...storage.session_carry_task import SessionCarry
from ...storage.ui_state_keys import EXPORT_SHARD, read
from ..ribbon_fields import style_ribbon_number_field
from ..ribbon_group import labeled_icon_group, ribbon_separator
from ..ui_state import UiStateStore
from .chunk_style_picker import ChunkStylePicker, style_icon
from .export_roi_layout import ExportRoiLayout, gather_export_roi_layout
from .general_group import ICON_SIZE, style_general_icon_button
from .mark_colors import cube_color, wavelength_color

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue
_GRID_ON_COLOR = "#84cc16"  # lime, as the grid toggle had in the Workflow section
_SKIP_ON_COLOR = "#ef4444"  # red: those images are left out
_CANCEL_COLOR = "#ef4444"
_CHUNK_WIDEST_TEXT = "8192"
_CHUNK_TIP = "Chunk {axis} in pixels (4 to 8192)."
_CARRY_ON_COLOR = "#a78bfa"  # violet: the session goes with the export
_CARRY_TIP = (
    "Session: take the session with the export. ROIs, masks, chromatic correction and mask pointers are moved into the "
    "exported pixels' coordinates (rotation, crop and the grid padding are already in the pixels) and saved as a session "
    "of the exported dataset. Nothing is recalculated, and this dataset's session is not touched."
)
_CHUNK_H_KEY = "export/chunk_h"  # read once (is a height saved yet?) and bound: one spelling for both
_REFRESH_DEBOUNCE_MS = 150
"""How long the counts/alignment recompute waits after the last ROI or geometry edit (`_schedule_refresh`)."""

logger = logging.getLogger(__name__)


# key (the `shard_mode` the export takes), Tabler icon, menu text
_SHARD_MODES = (
    ("per_image", "lambda", "WL frame: one wavelength frame per shard file"),
    ("per_spectral_cube", "cube", "Cube: one whole spectral cube per shard file"),
)


class ShardPicker(QToolButton):
    """Icon button with a pick-up menu, like the chunk-style button; its icon shows the mode in effect."""

    mode_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._mode = _SHARD_MODES[0][0]
        self._menu = QMenu(self)
        self._actions: dict[str, QAction] = {}
        for key, _icon, text in _SHARD_MODES:
            action = QAction(text, self._menu)
            action.triggered.connect(lambda _checked=False, picked=key: self.set_mode(picked))
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, key: str) -> None:
        if key not in self._actions or key == self._mode:
            return
        self._mode = key
        self.mode_changed.emit(key)

    def refresh_icons(self, make_icon: Callable[[str, str], QIcon]) -> None:
        """Each icon wears the colour of its slider (`mark_colors`): lambda = wavelength slider, cube = Cube slider."""
        colors = {"per_image": wavelength_color(), "per_spectral_cube": cube_color()}
        for key, icon_name, _text in _SHARD_MODES:
            icon = make_icon(icon_name, colors[key])
            self._actions[key].setIcon(icon)
            if key == self._mode:
                self.setIcon(icon)


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
        session_carry: SessionCarry | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset_module = dataset
        self._session_carry = session_carry  # moves the session into the exported pixels' coordinates (`storage/session_carry.py`)
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
        # The chromatic `(shift, stretch)` the read boxes are widened by: measured from the fitted models, which
        # a ROI move does not touch, so it is kept until the correction itself changes (`_on_chromatic_changed`).
        self._chromatic_reach: tuple[float, float] | None = None
        self._applied: cp.ChunkLayout | None = None  # from the chunk-style menu, kept while the size is unchanged
        self._applied_style: str | None = None  # the menu key behind `_applied`; the button's icon shows it
        self._exporting = False
        self._icon_cache: dict[tuple[str, str], QIcon] = {}  # (name, colour) -> icon; every refresh asks for the same dozen
        self._export_percent = 0
        # What `info_html` and the fields' tooltip show (the Workflow section had a label for each).
        self._warning_text = ""
        self._estimate_text = ""
        self._layout_text = ""

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
        # Both numbers hug the "×": width right-aligned (the default), height left-aligned. Right-aligned, a 3-digit
        # height left a digit-wide gap after the "×" because the box is sized for 4 digits (8192).
        self._chunk_h_spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._field_style = self._chunk_spin.styleSheet()  # put back when a warning's amber goes away
        times = QLabel("×", self)

        self._find_best_button = ChunkStylePicker(self)  # the chunk-style pick-up menu (was the Find best dialog)
        self._find_best_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._find_best_button.style_picked.connect(self._on_style_picked)

        self._shard_button = ShardPicker(self)  # icon = lambda (one wavelength frame per file) or cube (a whole cube)
        self._shard_button.setToolTip(
            "Shard: how many images are packed into a single shard file on disk - one wavelength frame (lambda), "
            "or a whole spectral cube (cube)."
        )
        self._shard_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._shard_button.mode_changed.connect(lambda _key: self._refresh_icons())

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

        self._carry_button = self._icon_button(_CARRY_TIP, checkable=True)
        self._carry_button.setChecked(True)
        self._carry_button.setEnabled(session_carry is not None)
        self._carry_button.toggled.connect(self._refresh_icons)

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
        chunk_group, _ = labeled_icon_group(self, chunk_row, "Chunk grid")
        shard_group, _ = labeled_icon_group(self, self._shard_button, "Shard")
        compression_group, _ = labeled_icon_group(self, self._compression_button, "Compress")
        skip_group, _ = labeled_icon_group(self, self._skip_excluded_button, "Skip excl.")
        carry_group, _ = labeled_icon_group(self, self._carry_button, "Session")
        export_group, self._export_caption = labeled_icon_group(self, self._export_button, "Export")

        self._separators: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, group in enumerate(
            (chunk_group, (shard_group, compression_group, skip_group), (carry_group, export_group))
        ):
            if index:
                separator = ribbon_separator(self)
                self._separators.append(separator)
                layout.addWidget(separator)
            for widget in group if isinstance(group, tuple) else (group,):
                layout.addWidget(widget)
        layout.addStretch(1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(_REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self._refresh_estimates)

        self._dataset_module.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset_module.dataset_cleared.connect(self._on_dataset_cleared)
        self._dataset_module.export_progress.connect(self._on_export_progress)
        self._dataset_module.export_finished.connect(self._on_export_finished)
        self._dataset_module.export_failed.connect(self._on_export_failed)
        # What moves a ROI's read box. Group edits (cosmetic) only matter to the chunk-style menu, which re-reads the layout.
        if roi_toolbox is not None:
            roi_toolbox.geometry_changed.connect(self._on_roi_layout_changed)
            roi_toolbox.roi_ids_renumbered.connect(self._on_roi_layout_changed)
        if geometry is not None:
            geometry.geometry_changed.connect(self._on_geometry_changed)
        if chromatic is not None:
            chromatic.chromatic_model_changed.connect(self._on_chromatic_changed)

        if ui_state is not None:
            self.bind_ui_state(ui_state)
        self._refresh_icons()
        self._refresh_estimates()

    # -- public ---------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Restore the settings as last left and keep saving them (`panels/ui_state.py`). Same keys as the
        Workflow section had, so saved settings carry over. Sizes before the grid toggle."""
        store.bind("export/chunk_size", self._chunk_spin)
        if store.get(_CHUNK_H_KEY) is None:
            self._chunk_h_spin.setValue(self._chunk_spin.value())  # a saved square size restores as W x W
        store.bind(_CHUNK_H_KEY, self._chunk_h_spin)
        store.bind("export/chunk_grid_preview", self._chunk_grid_button)
        self._shard_button.set_mode(str(read(store, EXPORT_SHARD)))  # ignored if unknown
        self._shard_button.mode_changed.connect(lambda key: store.set(EXPORT_SHARD.key, key))
        store.bind("export/compression", self._compression_button)
        store.bind("export/skip_excluded", self._skip_excluded_button)
        store.bind("export/carry_session", self._carry_button)

    def info_html(self) -> str:
        """The "i" icon's text while this tab is open: what the tab does, then the current counts and warnings."""
        lines = [
            "<b>Export to OME-Zarr</b><br>"
            "Writes the images in chunks of W&nbsp;&times;&nbsp;H pixels, as shown: rotated "
            "(Lanczos4), flipped and cropped, same bit depth; pixels the rotation creates load as empty. "
            "Grid icon: show the chunk grid on the image. Magnifier menu: set the chunk to hold 1 ROI, a column or a row of ROIs. "
            "The grid is always aligned to the ROIs (the left/top edge is padded with empty pixels). "
            "Shard: images per file on disk. "
            "Session icon: take the session along - ROIs, masks, chromatic correction and mask pointers are moved into the "
            "exported pixels' coordinates and saved as a session of the exported dataset (nothing is recalculated)."
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
        self._find_best_button.refresh_menu_icons()
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
        return (self._chunk_grid_button, self._find_best_button, self._shard_button, self._compression_button,
                self._skip_excluded_button, self._carry_button, self._export_button)

    def _icon(self, name: str, color: str):
        key = (name, color)
        icon = self._icon_cache.get(key)
        if icon is None:
            icon = self._icon_cache[key] = load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        return icon

    def _grid_export_icon(self, color: str) -> QIcon:
        """The grid icon with a small arrow up in its top-right corner: the grid of the *export* (the View tab's plain grid is the dataset's own)."""
        cached = self._icon_cache.get(("<grid-export>", color))
        if cached is None:
            cached = self._icon_cache[("<grid-export>", color)] = self._draw_grid_export_icon(color)
        return cached

    def _draw_grid_export_icon(self, color: str) -> QIcon:
        grid = self._icon("grid-4x4", color).pixmap(_RENDER_SIZE, _RENDER_SIZE)
        arrow = load_tabler_icon("arrow-up", color=color, size=_RENDER_SIZE, stroke_width=3.6).pixmap(_RENDER_SIZE, _RENDER_SIZE)
        u = _RENDER_SIZE / 24  # the Tabler icons are drawn on a 24-unit grid
        corner = QRectF(12.5 * u, 0, 11.5 * u, 11.5 * u)
        pixmap = QPixmap(grid)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(corner, Qt.GlobalColor.transparent)  # clear the grid lines under the arrow so it reads
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawPixmap(corner, arrow, QRectF(0, 0, _RENDER_SIZE, _RENDER_SIZE))
        painter.end()
        return QIcon(pixmap)

    def _refresh_icons(self, *_args: object) -> None:
        theme = get_active_theme()
        dim = theme.text_dim
        self._chunk_grid_button.setIcon(
            self._grid_export_icon(_GRID_ON_COLOR if self._chunk_grid_button.isChecked() else dim))
        self._shard_button.refresh_icons(self._icon)
        active_style = self._active_style()
        self._find_best_button.setIcon(
            style_icon(active_style, _ACTIVE_COLOR) if active_style is not None else self._icon("search", dim))
        self._compression_button.setIcon(
            self._icon("file-zip", _ACTIVE_COLOR if self._compression_button.isChecked() else dim))
        self._skip_excluded_button.setIcon(
            self._icon("ban", _SKIP_ON_COLOR if self._skip_excluded_button.isChecked() else dim))
        self._carry_button.setIcon(
            self._icon("copy", _CARRY_ON_COLOR if self._carry_button.isChecked() else dim))
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
        self._chromatic_reach = None  # another dataset's correction says nothing about this one's
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
        self._chromatic_reach = None
        self._refresh_icons()
        self._refresh_estimates()

    def _on_roi_layout_changed(self, *_args: object) -> None:
        self._roi_layout = None
        self._applied = None  # its offsets were for the old layout
        self._schedule_refresh()

    def _on_geometry_changed(self, *_args: object) -> None:
        # Moves the ROIs relative to the raw pixels, and (when the export applies it) changes the exported image size.
        self._roi_layout = None
        self._applied = None
        self._schedule_refresh()

    def _on_chromatic_changed(self, *_args: object) -> None:
        """The correction was refitted or switched: the reach has to be measured again (nothing else does)."""
        self._chromatic_reach = None
        self._on_roi_layout_changed()

    def _schedule_refresh(self, *_args: object) -> None:
        """Coalesce the recompute (2026-10-21). `geometry_changed` fires on every sample of a ROI drag or a
        rotation drag, and with the grid aligned to the ROIs this recompute walked the ROI layout and the fitted
        chromatic models: 267 ms per sample at 3000 fitted models, 755 ms at 8478. What it feeds - the counts in
        the fields' tooltip, the "i" text and the grid preview - nobody reads mid-drag."""
        self._refresh_timer.start()

    # -- layout -----------------------------------------------------------------

    def _applies_image_tools(self) -> bool:
        """Whether the export applies rotation/flip/crop: always, as long as the image tools are switched on."""
        return self._geometry is not None and bool(self._geometry.settings().image_tools_enabled)

    def _export_shape(self) -> tuple[int, int] | None:
        """(height, width) of the image the export stores before any frame: the processed image when it applies the
        image tools, else the raw one."""
        if self._image_width is None or self._image_height is None:
            return None
        raw = (int(self._image_height), int(self._image_width))
        if self._applies_image_tools():
            return tuple(int(v) for v in spatial_output_shape(raw, self._geometry.settings()))
        return raw

    def _export_roi_layout(self) -> ExportRoiLayout | None:
        raw_shape = self._export_shape()
        if raw_shape is None or self._roi_toolbox is None:
            return None
        if self._roi_layout is None or self._roi_layout.raw_shape != raw_shape:
            # ROIs are in processed space: with the image tools applied that *is* the export's space (no mapping).
            geometry = None if self._applies_image_tools() else self._geometry
            try:
                self._roi_layout = gather_export_roi_layout(
                    self._roi_toolbox, geometry, self._chromatic, raw_shape, reach=self._chromatic_reach
                )
            except Exception:
                logger.exception("Could not gather the ROI layout for the export")
                return None
            self._chromatic_reach = self._roi_layout.reach  # measured once per chromatic change, not per ROI edit
        return self._roi_layout

    def current_layout(self) -> tuple[cp.ChunkLayout, ExportRoiLayout | None]:
        """The chunk layout the export would use now (size from the fields, grid offsets aligned to the ROIs)."""
        w, h = self._chunk_spin.value(), self._chunk_h_spin.value()
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
        return cp.export_frame(layout, roi_layout.boxes, raw_shape, lossless=True, bytes_per_pixel=bpp)

    def _refresh_estimates(self, *_args: object) -> None:
        w, h = self._chunk_spin.value(), self._chunk_h_spin.value()
        self._warning_text = "\n".join(cp.chunk_warnings(w, h, compression=self._compression_button.isChecked()))
        raw_shape = self._export_shape()
        self._find_best_button.setEnabled(raw_shape is not None and self._roi_toolbox is not None)
        self._find_best_button.setToolTip(
            "Chunk style: pick what one chunk holds (1 ROI, a column or a row of ROIs); sets the chunk size and "
            "aligns the grid to the ROIs."
            if self._roi_toolbox is not None and raw_shape is not None
            else "Chunk style: load a dataset and place ROIs first - the sizes are built from the ROI layout."
        )
        if raw_shape is None:
            self._estimate_text = self._layout_text = ""
        else:
            frame = self._current_frame()
            if frame is None:
                per_plane = -(-raw_shape[1] // w) * -(-raw_shape[0] // h)
                self._layout_text = "No ROIs to align to: the grid starts at the image corner."
            else:
                per_plane = frame.chunks_per_plane
                self._layout_text = (f"Grid aligned to the ROIs: pads {-frame.origin_x} px left, {-frame.origin_y} px "
                                     f"top (stored {frame.width} x {frame.height} px, nothing lost).")
            self._estimate_text = (
                f"-> {per_plane} chunks/plane, {per_plane * self._image_count:,} chunks total ({self._image_count:,} images)"
            )
        self._refresh_fields()
        self._refresh_icons()
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

    # -- Chunk style ----------------------------------------------------------------

    def _report(self, text: str) -> None:
        if self._status is not None:
            self._status(text)

    def _on_style_picked(self, key: str) -> None:
        """Apply a chunk style from the menu straight away: chunk W x H and the grid aligned to the ROIs."""
        self._roi_layout = None  # fresh: picks up group edits, which do not invalidate the cache
        roi_layout = self._export_roi_layout()
        if roi_layout is None or roi_layout.empty:
            self._report("Chunk style: place ROIs first - the sizes are built from the ROI layout.")
            return
        suggestions = cp.suggest_layouts(
            roi_layout.boxes, roi_layout.raw_shape, centers_xy=roi_layout.centers,
            groups=[list(g) for g in roi_layout.groups] or None,
            bytes_per_pixel=int(self._image_dtype.itemsize) if self._image_dtype is not None else 2,
            compression=self._compression_button.isChecked(), only=key,
        )
        chosen = next((s for s in suggestions if s.key == key), None)
        if chosen is None:
            self._report("Chunk style: no regular ROI array found (needs at least two rows and two columns of ROIs).")
            return
        self._on_layout_chosen(chosen)
        self._report(f"Chunk style: {chosen.name}, grid aligned to the ROIs.")

    def _on_layout_chosen(self, suggestion: cp.Suggestion) -> None:
        layout = suggestion.layout
        self._applied = layout  # first: the refreshes the setters below trigger use its exact offsets
        self._applied_style = suggestion.key
        self._chunk_spin.setValue(layout.chunk_w)
        self._chunk_h_spin.setValue(layout.chunk_h)
        self._refresh_estimates()

    def _active_style(self) -> str | None:
        """The chunk style in effect: the picked one, until the ROIs change or the chunk size is edited by hand."""
        if self._applied is None:
            return None
        if (self._applied.chunk_w, self._applied.chunk_h) != (self._chunk_spin.value(), self._chunk_h_spin.value()):
            return None
        return self._applied_style

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
        carrying = self._prepare_session_carry(frame)
        try:
            self._dataset_module.export_to_ome_zarr(
                destination,
                chunk_size_px=layout.chunk_w,
                chunk_shape=(layout.chunk_h, layout.chunk_w),
                frame=(frame.origin_x, frame.origin_y, frame.width, frame.height) if frame is not None else None,
                compression_enabled=self._compression_button.isChecked(),
                shard_mode=self._shard_button.mode(),
                skip_excluded=self._skip_excluded_button.isChecked(),
                pixel_size_um=self._calibrated_pixel_size_um(),
                preprocessing=self._geometry.settings() if self._applies_image_tools() else None,
            )
        except RuntimeError as exc:
            if carrying:
                self._session_carry.discard()
            self._report(f"Export: {exc}")
            return
        self._set_exporting(True)

    def _prepare_session_carry(self, frame: cp.ExportFrame | None) -> bool:
        """Remember the session as it is now, if it is to go with this export. The transform is what the export itself applies:
        the geometry (only when the image tools are on, which is when it rotates / flips / crops) and the frame."""
        if (
            self._session_carry is None
            or self._geometry is None
            or not self._carry_button.isChecked()
            or self._image_width is None
            or self._image_height is None
        ):
            return False
        transform = ExportTransform(
            geometry=self._geometry.settings(),
            raw_shape=(int(self._image_height), int(self._image_width)),
            frame=(frame.origin_x, frame.origin_y, frame.width, frame.height) if frame is not None else None,
        )
        try:
            self._session_carry.prepare(transform)
        except RuntimeError as exc:  # the previous carry is still writing: export without a session rather than not at all
            self._report(f"Session not carried: {exc}")
            return False
        return True

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
            shard_mode=self._shard_button.mode(),
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
