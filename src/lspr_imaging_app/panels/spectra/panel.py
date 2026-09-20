"""``SpectraPanel`` (sketch §7 "Display panels" > Spectra, §10).

Reads Analysis Engine + ROI Toolbox (for coloring/selection) + Selection.
Owns its own fit-curve display and range tools via :class:`SpectraPlot`.
Never computes a fit for "live preview" on the GUI thread (AGENTS.md
non-negotiable invariant).
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ...analysis import AnalysisEngine
from ...roi import RoiToolbox
from ...selection import SelectionModule
from .plot import SpectraPlot

_REDRAW_COALESCE_MS = 100  # sketch §8


class SpectraPanel(QWidget):
    """Displays per-ROI spectra from the Analysis Engine's store."""

    def __init__(
        self,
        analysis_engine: AnalysisEngine,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._analysis_engine = analysis_engine
        self._roi_toolbox = roi_toolbox
        self._selection = selection

        self._plot = SpectraPlot(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._plot)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

    def _schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Reads ``get_spectrum`` for the current selection; shows "needs
        analysis" where it returns ``None``, never computes itself. Not yet
        implemented - scaffolding only."""
        raise NotImplementedError
