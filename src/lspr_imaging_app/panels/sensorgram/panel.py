"""``SensorgramPanel`` (sketch §7 "Display panels" > Sensorgram, §10).

Same shape as Spectra, plus reads Dataset's acquisition metadata narrowly
for the proposed pump-plan-step overlay. Its own redraw coalescing (§8)
replaces today's shared "Live" toggle entirely.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ...analysis import AnalysisEngine
from ...dataset import DatasetModule
from ...roi import RoiToolbox
from ...selection import SelectionModule
from .plot import SensorgramPlot

_REDRAW_COALESCE_MS = 100  # sketch §8


class SensorgramPanel(QWidget):
    """Displays per-ROI/group time series from the Analysis Engine's store."""

    def __init__(
        self,
        analysis_engine: AnalysisEngine,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        selection: SelectionModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._analysis_engine = analysis_engine
        self._roi_toolbox = roi_toolbox
        self._dataset = dataset
        self._selection = selection

        self._plot = SensorgramPlot(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._plot)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

    def _schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError
