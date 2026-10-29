"""The Sensorgram ribbon's "Evaluation" group (2026-10-29): which step is the baseline and which is the signal.

Screen purpose: say once, in the plan, which stretch of the experiment is the baseline (``B1``) and which is the signal (``S1``) of evaluation pair 1. Everything that compares a signal with
a baseline (ROI position tuning, the map of which ROI or group gives the best signal) reads these two steps from the plan module instead of asking for cube numbers. The same choice can be
made in a step's right-click menu ("Use as baseline", "Use as signal"; those items are live while this tab is active); the bar shows ``B1`` / ``S1`` on the steps.

Layout (left to right): ``[baseline step v] [signal step v]``, two `StepPicker`s that list the plan's steps by number and note with the step's colour.

Display only: the group holds no choice. It is filled by ``set_steps`` from the module's steps and roles and reports the user's pick as ``role_chosen(role, step reference or None)``;
`StepOverlays` turns that into ``ExperimentPlanModule.set_role``. Greyed out when the dataset has no steps.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget

from ...plan.edits import EffectiveStep
from ...plan.state import StepRef
from ..step_picker import StepPicker

BASELINE_TIP = (
    "The baseline step (B1) of the evaluation: the stretch of the experiment taken as the starting level. Everything that compares a signal with a baseline reads it from here. "
    "The same as 'Use as baseline' in a step's right-click menu."
)
SIGNAL_TIP = (
    "The signal step (S1) of the evaluation: the stretch of the experiment whose level is compared with the baseline. "
    "The same as 'Use as signal' in a step's right-click menu."
)
NO_STEPS_TIP = "This dataset has no experiment steps. Import the measurement notes (and the pump plan) in Workflow > Experimental plan to get them."


class EvaluationControls(QObject):
    role_chosen = pyqtSignal(str, object)  # ("baseline" | "signal", the step's reference or None)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._baseline = StepPicker("B1: not set", parent, prefix="B1: ")
        self._signal = StepPicker("S1: not set", parent, prefix="S1: ")
        self._baseline.setToolTip(BASELINE_TIP)
        self._signal.setToolTip(SIGNAL_TIP)
        self._baseline.step_chosen.connect(lambda ref: self.role_chosen.emit("baseline", ref))
        self._signal.step_chosen.connect(lambda ref: self.role_chosen.emit("signal", ref))
        self.set_available(False)

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._baseline, self._signal)

    def set_available(self, available: bool) -> None:
        for picker, tip in ((self._baseline, BASELINE_TIP), (self._signal, SIGNAL_TIP)):
            picker.setEnabled(bool(available))
            picker.setToolTip(tip if available else NO_STEPS_TIP)

    def set_steps(
        self, steps: Sequence[EffectiveStep], colours: Sequence[str] | None, baseline: StepRef | None, signal: StepRef | None
    ) -> None:
        """Show the plan's steps and the two roles (a reference to a step that is not in `steps` shows as not set). Emits nothing."""
        self._baseline.set_steps(steps, colours, baseline)
        self._signal.set_steps(steps, colours, signal)
        self.set_available(bool(steps))

    def baseline_ref(self) -> StepRef | None:
        return self._baseline.current_ref()

    def signal_ref(self) -> StepRef | None:
        return self._signal.current_ref()

    def refresh_theme(self) -> None:
        self._baseline.refresh_theme()
        self._signal.refresh_theme()
