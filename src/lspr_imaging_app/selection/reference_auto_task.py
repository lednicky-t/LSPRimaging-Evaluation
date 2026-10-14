"""Runs "Find reference" (best-contrast wavelength of the viewed cube) off the
GUI thread and stores the result in `ReferenceFrameModule`.

Follows `image_tools/chromatic_auto_task.py` (the reference for heavy tasks):
a **plain `threading.Thread`**, never `QThreadPool` (planes are OME-Zarr
reads), progress through `task_progress`, one `task_finished` per run, a
`cancel()`; everything the thread needs is snapshotted by value in `_start`.

Trigger: only an explicit click on "Find reference" (`ReferenceFrameModule.
request_find`). It used to run on every cube change, which replaced the
reference while the user previewed other cubes (changed 2026-10-14, maintainer).
The winner is stored as a fixed reference frame, the same as "Set reference".
One run at a time.
"""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from ..applog.events import log_event
from ..dataset.module import DatasetModule
from ..progress import Cancelled
from .frame_roles_module import FrameRolesModule
from .module import SelectionModule
from .reference_frame_module import ReferenceFrameModule
from .reference_scoring import best_contrast_wavelength

logger = logging.getLogger(__name__)

TASK_ID = "find_reference_frame"
TASK_LABEL = "Find reference"


class ReferenceAutoFind(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run
    running_changed = pyqtSignal(bool)

    # Internal: thread -> GUI thread hand-off (cube, generation, wavelength|None, error|None).
    _finished = pyqtSignal(int, int, object, object)

    def __init__(
        self,
        dataset: DatasetModule,
        selection: SelectionModule,
        reference_frame: ReferenceFrameModule,
        frame_roles: FrameRolesModule | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._selection = selection
        self._reference_frame = reference_frame
        self._frame_roles = frame_roles
        self._thread: threading.Thread | None = None
        self._last_candidates: tuple[float, ...] = ()
        self._cancel_event = threading.Event()
        self._generation = 0  # bumped when the dataset changes, so a late result is dropped
        self._finished.connect(self._on_finished)
        # Only an explicit click starts a scan (2026-10-14, maintainer): it used to
        # run on every cube change, which replaced the reference while previewing.
        reference_frame.find_requested.connect(self._start)
        dataset.dataset_loaded.connect(lambda _ds: self._on_dataset_changed())
        dataset.dataset_cleared.connect(self._on_dataset_changed)

    def _candidate_wavelengths(self, cube_index: int) -> tuple[float, ...]:
        """The cube's wavelengths that may be a reference: not the dark, not ignored."""
        wavelengths = tuple(self._dataset.wavelengths_for_cube(cube_index))
        if self._frame_roles is None:
            return wavelengths
        return tuple(wl for wl in wavelengths if self._frame_roles.is_spectral(cube_index, wl))

    def is_running(self) -> bool:
        return self._thread is not None

    def cancel(self) -> None:
        self._cancel_event.set()

    def shutdown(self) -> None:
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def _on_dataset_changed(self) -> None:
        self._generation += 1
        self._cancel_event.set()  # the running scan (if any) is for the old dataset

    def _start(self) -> None:
        """Score every candidate wavelength of the cube viewed now (background thread)."""
        if self._thread is not None:
            return
        cubes = self._dataset.spectral_cubes()
        if not cubes:
            return
        cube = int(self._selection.current_cube())
        if cube not in cubes:
            return
        wavelengths = self._candidate_wavelengths(cube)
        if not wavelengths:
            return
        self._last_candidates = wavelengths
        load_plane = self._dataset.load_plane
        generation = self._generation
        self._cancel_event.clear()

        def work() -> None:
            best: float | None = None
            error: BaseException | None = None
            try:
                best = best_contrast_wavelength(
                    wavelengths,
                    lambda wl: load_plane(cube, wl),
                    progress=lambda fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message),
                    cancelled=self._cancel_event.is_set,
                )
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(cube, generation, best, error)

        self._thread = threading.Thread(target=work, name="find-reference-frame", daemon=True)
        self.running_changed.emit(True)
        self._thread.start()

    def _on_finished(self, cube: int, generation: int, best: object, error: object) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self.running_changed.emit(False)
        if isinstance(error, Cancelled):
            self.task_finished.emit(TASK_ID, "cancelled", "")
            return
        if error is not None:
            logger.error("Find reference crashed", exc_info=error)
            self.task_finished.emit(TASK_ID, "failed", f"Unexpected error: {error}")
            return
        if generation != self._generation:
            self.task_finished.emit(TASK_ID, "cancelled", "dataset changed")
            return
        if best is None:
            self.task_finished.emit(TASK_ID, "failed", f"Cube {cube}: no readable image to score.")
            return
        self.task_finished.emit(TASK_ID, "completed", f"Cube {cube}: reference {float(best):.0f} nm")
        log_event(logger, "reference.find", logging.INFO, cube=cube, wl=float(best), planes=len(self._last_candidates))
        # A fixed reference, the same as Set reference: it does not change when other cubes are viewed.
        self._reference_frame.set_manual_frame(cube, float(best))
