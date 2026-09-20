"""``WorkflowPanel`` (sketch §7 "Workflow shell", §10).

A thin navigation/status host - hosts the stage tabs (Dataset -> Image Tools
-> ROI Selection -> Analysis, with Spectra/Sensorgram as pure downstream
consumers, per sketch §1), shows which stage is active, hosts the status
bar (state/performance only, no hover-hint text) - and otherwise owns no
scientific state. Replaces ``MainWindow``'s role as a god object; should
stay small enough that removing it and rewiring the modules directly would
be a mechanical exercise, not a redesign.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QWidget

logger = logging.getLogger(__name__)


class WorkflowStage(Enum):
    DATASET = auto()
    IMAGE_TOOLS = auto()
    ROI_SELECTION = auto()
    ANALYSIS = auto()


class WorkflowPanel(QTabWidget):
    """Hosts the stage tabs and status bar. Owns no scientific state - it
    wires already-constructed panels together, nothing more."""

    stage_changed = pyqtSignal(WorkflowStage)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        """Deliberately a safe no-op, not ``NotImplementedError`` like this
        module's other stubs: Qt fires ``currentChanged`` itself (including
        as soon as the first tab is added), so this runs the moment any tab
        exists, well before the real index->``WorkflowStage`` mapping and
        ``stage_changed`` emission are designed. Not yet implemented -
        scaffolding only."""
        logger.debug("WorkflowPanel tab changed to index %s (stage mapping not yet implemented)", index)

    def set_status(self, message: str) -> None:
        """State/performance status only - no hover-hint text (per the
        settled decision). Not yet implemented - scaffolding only."""
        raise NotImplementedError
