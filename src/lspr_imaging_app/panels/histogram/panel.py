"""``HistogramPanel`` (sketch §7 "Display panels" > Histogram, §10).

Self-sufficient per the maintainer's own description: pulls "current
displayed image" from :class:`~lspr_imaging_app.panels.image.panel.ImagePanel`
(one narrow read), computes and plots its own histogram, emits its own
range-selection events - which the Mask module (not the Image panel)
subscribes to.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

from ..image.panel import ImagePanel

_REDRAW_COALESCE_MS = 100  # sketch §8


class HistogramPanel(QWidget):
    """Plots a histogram of the currently displayed image and emits
    range-selection events for Mask to consume."""

    range_selected = pyqtSignal(float, float)  # (low, high) - Mask subscribes to this

    def __init__(self, image_panel: ImagePanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image_panel = image_panel

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

    def _schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def _compute_histogram(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def _on_range_dragged(self, low: float, high: float) -> None:
        self.range_selected.emit(low, high)
