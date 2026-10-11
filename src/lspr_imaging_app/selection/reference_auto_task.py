"""Runs "Find reference" (best-contrast wavelength of the viewed cube) off the
GUI thread and stores the result in `ReferenceFrameModule`.

Follows `image_tools/chromatic_auto_task.py` (the reference for heavy tasks):
a **plain `threading.Thread`**, never `QThreadPool` (planes are OME-Zarr
reads), progress through `task_progress`, one `task_finished` per run, a
`cancel()`; everything the thread needs is snapshotted by value in `_start`.

Triggers: whenever Auto is the active mode and the viewed cube has no cached
best wavelength yet - on switching to Auto, and on every cube change. A cube
already scored is never scored again (the cache lives in the module and is
cleared with the dataset). One run at a time; a cube change during a run is
picked up when it finishes.
"""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from ..dataset.module import DatasetModule
from ..progress import Cancelled
from .module import SelectionModule
from .reference_frame_module import MODE_AUTO, ReferenceFrameModule
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
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._selection = selection
        self._reference_frame = reference_frame
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._generation = 0  # bumped when the dataset changes, so a late result is dropped
        self._finished.connect(self._on_finished)
        reference_frame.reference_frame_changed.connect(self._maybe_start)
        selection.cube_changed.connect(lambda _cube: self._maybe_start())
        dataset.dataset_loaded.connect(lambda _ds: self._on_dataset_changed())
        dataset.dataset_cleared.connect(self._on_dataset_changed)

    def is_running(self) -> bool:
        return self._thread is not None

    def is_pending(self, cube_index: int) -> bool:
        """Whether Auto is waiting on a best wavelength for `cube_index` (not
        found yet, and a run is on its way) - the UI shows "finding...". """
        return (
            self._reference_frame.mode() == MODE_AUTO
            and self._reference_frame.auto_wavelength(cube_index) is None
            and bool(self._dataset.wavelengths_for_cube(cube_index))
        )

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

    def _maybe_start(self) -> None:
        if self._thread is not None or self._reference_frame.mode() != MODE_AUTO:
            return
        cubes = self._dataset.spectral_cubes()
        if not cubes:
            return
        cube = int(self._selection.current_cube())
        if cube not in cubes or self._reference_frame.auto_wavelength(cube) is not None:
            return
        wavelengths = tuple(self._dataset.wavelengths_for_cube(cube))
        if not wavelengths:
            return
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
            if generation != self._generation:  # cancelled only because the dataset changed
                self._maybe_start()
            return
        if error is not None:
            logger.error("Find reference crashed", exc_info=error)
            self.task_finished.emit(TASK_ID, "failed", f"Unexpected error: {error}")
            return
        if generation != self._generation:
            self.task_finished.emit(TASK_ID, "cancelled", "dataset changed")
            self._maybe_start()
            return
        if best is None:
            self.task_finished.emit(TASK_ID, "failed", f"Cube {cube}: no readable image to score.")
            return
        self.task_finished.emit(TASK_ID, "completed", f"Cube {cube}: reference {float(best):.0f} nm")
        self._reference_frame.set_auto_wavelength(cube, float(best))
        self._maybe_start()  # the viewed cube may have changed during the run
