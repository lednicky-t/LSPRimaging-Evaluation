"""``ImageMotionModule``: the image-motion check of the Image panel's Analysis tab as a module (2026-11-07).

How far the image moves between the cubes of each experiment step (`analysis/image_motion.py`: phase correlation of one wavelength's plane of every cube against the step's first).
A sample ROI and its reference ROI sit at fixed pixels, so a moving image adds noise or a trend to their numbers; this check says whether that happens, and in which step.

**Run on request**, off the GUI thread: it reads one raw plane per cube (`DatasetModule.load_plane_uncached`, which leaves the Image panel's read cache alone and is safe on a worker
thread), one at a time, so memory stays at a plane or two. The cubes of a step are the cubes wholly inside it (`step_stats.assign_cubes`, as everywhere else). The result is a
`CheckReport` (`analysis/check_report.py`); it is kept until another dataset is opened or the next run replaces it. It reads images only: nothing is changed or saved.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.check_report import CheckReport, motion_report
from ..analysis.image_motion import StepMotion, step_motion
from ..analysis.step_stats import assign_cubes
from ..applog import log_event
from ..dataset.model import is_dark_frame_wavelength
from .background_run import BackgroundRun

if TYPE_CHECKING:  # only used in annotations
    from ..dataset.module import DatasetModule
    from .experiment_plan_module import ExperimentPlanModule

logger = logging.getLogger(__name__)

MIN_CUBES = 3  # a step with fewer cubes cannot be measured (`image_motion.step_motion`)


@dataclass(frozen=True)
class _Outcome:
    report: CheckReport | None  # None: cancelled


def nearest_wavelength(wavelengths: tuple[float, ...], wanted: float) -> float | None:
    """The spectral wavelength (the dark frame is not one) closest to ``wanted``, or None when there is none."""
    spectral = [float(w) for w in wavelengths if not is_dark_frame_wavelength(w)]
    return min(spectral, key=lambda w: abs(w - wanted)) if spectral else None


class ImageMotionModule(QObject):
    state_changed = pyqtSignal()
    """A run began, ended, or the report was dropped: re-read `is_running`, `report`."""
    notice = pyqtSignal(str)
    """A plain sentence for the user: nothing can be measured, and why."""
    started = pyqtSignal(str)
    progress = pyqtSignal(float, str)
    finished = pyqtSignal(str, str)
    """("completed" | "failed" | "cancelled", message)."""

    def __init__(self, dataset: DatasetModule, experiment_plan: ExperimentPlanModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._plan = experiment_plan
        self._report: CheckReport | None = None
        self._failure: str | None = None
        self._run = BackgroundRun("image-motion", self)
        self._run.done.connect(self._on_done)
        self._run.progress.connect(self.progress)
        dataset.dataset_cleared.connect(self.clear)

    def report(self) -> CheckReport | None:
        return self._report

    def failure(self) -> str | None:
        return self._failure

    def is_running(self) -> bool:
        return self._run.is_running()

    def clear(self) -> None:
        """Another dataset: drop the report and stop a run."""
        self._run.cancel()
        self._report, self._failure = None, None
        self.state_changed.emit()

    def cancel(self) -> None:
        self._run.cancel()

    def shutdown(self) -> None:
        self._run.shutdown()

    def run(self, wavelength_nm: float) -> bool:
        """Measure the motion in every step at the wavelength nearest ``wavelength_nm``, now, off the GUI thread. True if a run was started; False, with a `notice`, if not."""
        cubes = tuple(int(c) for c in self._dataset.spectral_cubes())
        if not cubes:
            self.notice.emit("Open a dataset first: the motion is measured on its images.")
            return False
        steps = self._plan.steps()
        if not steps:
            self.notice.emit("This dataset has no experiment steps: the motion is measured per step. Import the measurement notes in Workflow > Experimental plan.")
            return False
        wanted = nearest_wavelength(self._dataset.wavelengths(), wavelength_nm)
        if wanted is None:
            self.notice.emit("This dataset has no spectral images.")
            return False
        membership = assign_cubes(steps, *self._dataset.cube_spans_unix_ms(cubes))
        plane_of: dict[int, float] = {}  # the wavelength read in each cube (the nearest it has: a short cube may lack the exact one)
        for c in cubes:
            found = nearest_wavelength(self._dataset.wavelengths_for_cube(c), wanted)
            if found is not None:
                plane_of[c] = found
        todo: list[tuple[str, list[int]]] = []  # (step label, its cubes that have a plane)
        skipped: list[str] = []
        for k, step in enumerate(membership.steps):
            label = f"{step.order}: {step.label}" if step.order else str(step.label)
            in_step = [cubes[i] for i in np.flatnonzero(membership.used[k]) if cubes[i] in plane_of]
            if len(in_step) >= MIN_CUBES:
                todo.append((label, in_step))
            else:
                skipped.append(label)
        nearest_note = f" (the nearest to the {wavelength_nm:g} nm asked for)" if abs(wanted - wavelength_nm) > 0.05 else ""
        subtitle = f"Raw plane at {wanted:g} nm{nearest_note}, one per cube, against the first cube of each step. Checked at {datetime.now():%H:%M}."
        self._failure = None
        self._run.start(self._work(todo, skipped, plane_of, wanted, subtitle))
        self.started.emit("measuring the image motion")
        self.state_changed.emit()
        return True

    def _work(
        self, todo: list[tuple[str, list[int]]], skipped: list[str], plane_of: dict[int, float], wavelength: float, subtitle: str
    ) -> Callable[[Callable[[], bool], Callable[[float, str], None]], _Outcome]:
        load = self._dataset.load_plane_uncached
        total = sum(len(cubes) for _label, cubes in todo)

        def work(cancelled: Callable[[], bool], report: Callable[[float, str], None]) -> _Outcome:
            t0 = time.perf_counter()
            done = 0
            motions: list[StepMotion] = []

            def one_plane() -> None:
                nonlocal done
                done += 1
                report(done / total if total else 0.0, f"reading plane {done} of {total}")

            for label, cubes in todo:
                found = step_motion(label, cubes, lambda c: load(c, plane_of[c]), cancelled, one_plane)
                if found is None:  # cancelled (every step in `todo` has enough cubes)
                    return _Outcome(None)
                motions.append(found)
            log_event(logger, "image_motion.run", logging.DEBUG, steps=len(motions), planes=done, total_ms=round((time.perf_counter() - t0) * 1e3, 1))
            return _Outcome(motion_report(motions, skipped, wavelength, subtitle))

        return work

    def _on_done(self, token: object, outcome: object, error: object) -> None:
        if not self._run.is_current(token):
            return
        if isinstance(error, BaseException):
            self._failure = f"{type(error).__name__}: {error}"
            self.finished.emit("failed", self._failure)
        elif not isinstance(outcome, _Outcome) or outcome.report is None:
            self.finished.emit("cancelled", "")
        else:
            self._report, self._failure = outcome.report, None
            self.finished.emit("completed", f"{outcome.report.warnings} warning{'s' if outcome.report.warnings != 1 else ''}")
        self.state_changed.emit()
