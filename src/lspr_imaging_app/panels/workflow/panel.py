"""``WorkflowPanel`` (sketch §7 "Workflow shell", §10;
``docs/rewrite_gui_shell_design_2026-09.md`` §4).

A thin navigation/status host - hosts the stage tabs (Dataset -> Image Tools
-> ROI Selection -> Analysis, with Spectra/Sensorgram as pure downstream
consumers, per sketch §1), shows which stage is active, and reports status
text for the main window's status bar - and otherwise owns no scientific
state. Replaces ``MainWindow``'s role as a god object; should stay small
enough that removing it and rewiring the modules directly would be a
mechanical exercise, not a redesign.

**Not the five display panels.** Earlier scaffolding had this class host
Image/Histogram/ROI-table/Spectra/Sensorgram as its own tabs - that was
placeholder wiring, not the design: per the GUI shell design doc, those five
are each their own dock widget, and this panel is docked alongside them
(left, per the stable app's own precedent), not their container. Each stage
tab here holds that stage's *settings* (detection thresholds, crop/rotate
fields, ..., per the design doc §3's Workflow-vs-panel icon split) - real
settings forms are still future work; each tab is a placeholder for now.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QTabWidget, QWidget

logger = logging.getLogger(__name__)


class WorkflowStage(Enum):
    DATASET = auto()
    IMAGE_TOOLS = auto()
    ROI_SELECTION = auto()
    ANALYSIS = auto()


# Tab order fixes the index -> stage mapping _on_tab_changed relies on.
_STAGE_ORDER: tuple[tuple[WorkflowStage, str], ...] = (
    (WorkflowStage.DATASET, "Dataset"),
    (WorkflowStage.IMAGE_TOOLS, "Image Tools"),
    (WorkflowStage.ROI_SELECTION, "ROI Selection"),
    (WorkflowStage.ANALYSIS, "Analysis"),
)


def _stage_placeholder(stage_name: str) -> QWidget:
    label = QLabel(f"{stage_name} settings - not built yet.")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setStyleSheet("padding: 10px;")
    return label


class WorkflowPanel(QTabWidget):
    """Hosts the stage tabs. Owns no scientific state - it wires
    already-constructed modules' settings UI together, nothing more."""

    stage_changed = pyqtSignal(WorkflowStage)
    # State/performance text only (no hover-hint text, per the design doc) -
    # the main window connects this to its QStatusBar.
    status_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for stage, label in _STAGE_ORDER:
            self.addTab(_stage_placeholder(label), label)
        self.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < len(_STAGE_ORDER):
            stage, _label = _STAGE_ORDER[index]
            self.stage_changed.emit(stage)

    def set_status(self, message: str) -> None:
        self.status_requested.emit(message)
