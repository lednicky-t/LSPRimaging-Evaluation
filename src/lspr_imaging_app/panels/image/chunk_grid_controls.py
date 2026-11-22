"""The View tab's toggle for the loaded dataset's own chunk grid (2026-10-22).

An OME-Zarr stores its pixels in chunks; this draws the lines between them over the image (blue, `DecorationOverlays`
`update_source_grid`, decoration_overlays.py). A TIFF stack has no chunks, so there the button is greyed out. Off it
is grey like the other View toggles, on it is blue. The state lives on `DatasetModule` (`set_source_chunk_grid_visible`), like the Export tab's grid preview, which is
independent of this one (both can show at once, to compare). The Export tab's grid is the *planned* export chunking,
this one is the chunking of what is loaded now.

**The export's grid is in this icon's right-click menu (2026-11-22, maintainer request)**: `ViewTab` puts a copy of the Export tab's grid switch there, so the
View controls have one chunk icon, not two. That menu is about the export, not the loaded images, so it opens on a greyed-out button too (`MenuOpener(also_disabled=True)`):
a TIFF stack, the usual thing to export, has no chunks of its own and still has an export grid to preview. The icon used to disappear for a TIFF stack (the whole group was hidden).

The button is a plain checkable `QToolButton`, so `UiStateStore.bind` can remember it. A remembered "on" survives a TIFF
load (the button is greyed out, nothing is drawn) and is there again with the next OME-Zarr.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_MENU_HINT = "\nRight-click: show the chunk grid an export would use."


class ChunkGridControls(QToolButton):
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
        available = self.source_chunk_available()
        shown = bool(self._dataset.source_chunk_grid_visible())
        if self.isChecked() != shown:
            self.setChecked(shown)
        self.setEnabled(available)  # greyed out for a TIFF stack (and with nothing loaded); the right-click menu still opens (`MenuOpener.also_disabled`)
        color = theme.accent_blue if shown else theme.text_muted  # as the other View toggles (scale bar)
        self.setIcon(load_tabler_icon("grid-4x4", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        if not available:
            self.setToolTip("The loaded images have no chunks (a TIFF stack, or nothing loaded): there is no chunk grid of the dataset to show." + _MENU_HINT)
        elif shown:
            self.setToolTip("Chunk grid of the loaded OME-Zarr shown (click to hide)." + _MENU_HINT)
        else:
            self.setToolTip("Show the chunk grid of the loaded OME-Zarr." + _MENU_HINT)
