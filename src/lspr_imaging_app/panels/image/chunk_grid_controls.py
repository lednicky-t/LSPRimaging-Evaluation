"""The View tab's toggle for the loaded dataset's own chunk grid (2026-10-22).

An OME-Zarr stores its pixels in chunks; this draws the lines between them over the image (blue, `ImagePanel`
`_update_source_grid`). A TIFF stack has no chunks, so there the whole group is hidden (`availability_changed`). Off it
is grey like the other View toggles, on it is blue. The state lives on `DatasetModule` (`set_source_chunk_grid_visible`), like the Export tab's grid preview, which is
independent of this one (both can show at once, to compare). The Export tab's grid is the *planned* export chunking,
this one is the chunking of what is loaded now.

The button is a plain checkable `QToolButton`, so `UiStateStore.bind` can remember it. A remembered "on" survives a TIFF
load (the button is disabled, nothing is drawn) and is there again with the next OME-Zarr.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


class ChunkGridControls(QToolButton):
    availability_changed = pyqtSignal(bool)  # True: an OME-Zarr is loaded (chunks exist)

    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self.setCheckable(True)
        self.toggled.connect(lambda checked: self._dataset.set_source_chunk_grid_visible(bool(checked)))
        dataset.source_chunk_grid_changed.connect(self.sync)
        dataset.dataset_loaded.connect(self.sync)
        dataset.dataset_cleared.connect(self.sync)
        self.refresh_theme()

    def source_chunk_available(self) -> bool:
        return self._dataset.source_chunk_shape() is not None

    def refresh_theme(self, *_args: object) -> None:
        style_general_icon_button(self)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.sync()

    def sync(self, *_args: object) -> None:
        """Follow the module and the loaded dataset's format."""
        theme = get_active_theme()
        available = self._dataset.source_chunk_shape() is not None
        shown = bool(self._dataset.source_chunk_grid_visible())
        if self.isChecked() != shown:
            self.setChecked(shown)
        color = theme.accent_blue if shown else theme.text_muted  # as the other View toggles (scale bar)
        self.setIcon(load_tabler_icon("grid-4x4", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self.availability_changed.emit(available)  # the View tab hides the whole group (caption included) for a TIFF stack
        if shown:
            self.setToolTip("Chunk grid of the loaded OME-Zarr shown (click to hide).")
        else:
            self.setToolTip("Show the chunk grid of the loaded OME-Zarr.")
