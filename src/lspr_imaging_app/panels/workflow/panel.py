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

from enum import Enum, auto

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QWidget


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
        """Not yet implemented - scaffolding only (no stages/tabs added
        yet)."""
        raise NotImplementedError

    def set_status(self, message: str) -> None:
        """State/performance status only - no hover-hint text (per the
        settled decision). Not yet implemented - scaffolding only."""
        raise NotImplementedError
