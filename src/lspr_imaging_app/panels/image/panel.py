"""``ImagePanel`` (sketch §7 "Display panels" > Image, §10).

Reads Dataset + Image Tools (to render the processed image) + ROI Toolbox
(``display_position``, for overlays) + Selection. Forwards drag/click as
``RoiToolbox.request_move(...)`` etc. - never touches ROI state directly
(resolving the current app's "lots of connection between Image panel and
ROI section" complaint from the feature inventory).
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from ...dataset import DatasetModule
from ...image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
from ...roi import RoiToolbox
from ...selection import SelectionModule

_REDRAW_COALESCE_MS = 100  # sketch §8 - the already-validated coalescing window


class ImagePanel(QWidget):
    """Renders the current processed image with ROI overlays. Owns no
    computation and no ROI/group state (AGENTS.md)."""

    def __init__(
        self,
        dataset: DatasetModule,
        geometry: GeometryModule,
        mask: MaskModule,
        chromatic: ChromaticModule,
        background: BackgroundModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._geometry = geometry
        self._mask = mask
        self._chromatic = chromatic
        self._background = background
        self._roi_toolbox = roi_toolbox
        self._selection = selection

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        # TODO: connect dataset_loaded/geometry_changed/mask_changed/
        # chromatic_model_changed/background_model_changed/roi geometry+
        # cosmetic+selection signals to self._schedule_redraw, once each
        # module has a real implementation to react to.

    def _schedule_redraw(self) -> None:
        """Coalesce many events into one redraw ~100ms later (sketch §8).
        Not wired to anything yet - scaffolding only."""
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def _on_drag(self, roi_id: int, x: float, y: float) -> None:
        """Forwards a drag gesture to the owning module - never mutates ROI
        state directly. Not yet implemented - scaffolding only."""
        self._roi_toolbox.request_move(roi_id, x, y)
