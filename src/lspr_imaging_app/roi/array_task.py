"""Runs one Array action (detect / refine / place) off the GUI thread.

The pure computation is `array_pipeline`; this QObject is the thread / signal wrapper the rewrite
rules ask for ("heavy work reports progress and can be cancelled"), the same shape as
`image_tools/chromatic_auto_task.ChromaticAutoDetect`:

- `task_progress(task_id, label, fraction, message)` and `task_finished(task_id, outcome, message)`
  are the app-wide `TaskIndicator` shapes (`outcome`: "completed" / "failed" / "cancelled").
- A **plain `threading.Thread`**, never `QThreadPool`: the plane is read through
  `DatasetModule.load_plane` (an OME-Zarr read), which the zarr rule forbids pool workers to touch.
- Cross-thread `emit()` is queued onto the receiver's thread by Qt, so every slot (and every
  toolbox mutation) runs on the GUI thread. The thread never reads a module: everything it needs is
  snapshotted by value in `start()`.

The image: **the reference wavelength of the cube being viewed** (not necessarily the reference
cube), in processed space (rotate / flip / crop, plus background removal when it is applied), i.e.
what the Image panel shows at that wavelength.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import apply_preprocessing
from ..progress import Cancelled
from .array_pipeline import ArrayError, ArrayResult, ArraySettings, detect_array, place_array, refine_array

logger = logging.getLogger(__name__)

TASK_ID = "roi_array"
TASK_LABEL = "ROI array"

DETECT, REFINE, PLACE = "detect", "refine", "place"


@dataclass(frozen=True)
class ArrayOutcome:
    """What the panel gets back: the result and what it was asked to do."""

    kind: str
    result: ArrayResult
    settings: ArraySettings
    cube_index: int
    wavelength_nm: float
    roi_ids: tuple[int, ...]
    """Detect / place: the ROIs to replace. Refine: the ROIs that were refined, in the order of the result."""


class ArrayAction(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # ArrayOutcome
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # once per run

    _finished = pyqtSignal(object, object)  # thread -> GUI thread: (outcome, error)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._thread is not None

    def start(
        self,
        kind: str,
        settings: ArraySettings,
        *,
        cube_index: int,
        wavelength_nm: float,
        geometry: GeometrySettings,
        background: BackgroundSettings,
        load_plane: Callable[[int, float], np.ndarray],
        roi_ids: tuple[int, ...] = (),
        centers_xy: np.ndarray | None = None,
        sample_diameters_px: np.ndarray | None = None,
        grid: tuple[int, int, float, float] | None = None,
    ) -> None:
        """Begin a run. ``kind`` is `DETECT`, `REFINE` (needs `centers_xy` / `sample_diameters_px`, one per ROI
        in `roi_ids`) or `PLACE` (needs `grid` = (rows, cols, pitch_x_px, pitch_y_px); the plane is only
        read when ``settings.snap_to_image``)."""
        if self._thread is not None:
            raise RuntimeError("An array action is already running.")
        if kind not in (DETECT, REFINE, PLACE):
            raise ValueError(f"unknown array action {kind!r}")
        if kind == REFINE and (centers_xy is None or sample_diameters_px is None):
            raise ValueError("refining needs the ROIs' positions and diameters")
        if kind == PLACE and grid is None:
            raise ValueError("placing needs rows, columns and the pitch")
        centers = None if centers_xy is None else np.array(centers_xy, dtype=np.float64)
        diameters = None if sample_diameters_px is None else np.array(sample_diameters_px, dtype=np.float64)
        self._cancel_event.clear()

        def report(fraction: float, message: str) -> None:
            self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message)

        def load_image() -> np.ndarray:
            report(0.0, "Array: loading the image...")
            return apply_preprocessing(load_plane(int(cube_index), float(wavelength_nm)), geometry, background)

        def work() -> None:
            outcome: ArrayOutcome | None = None
            error: BaseException | None = None
            try:
                if kind == DETECT:
                    result = detect_array(load_image(), settings, progress=report, cancelled=self._cancel_event.is_set)
                elif kind == REFINE:
                    assert centers is not None and diameters is not None
                    result = refine_array(load_image(), centers, diameters, settings, progress=report, cancelled=self._cancel_event.is_set)
                else:
                    assert grid is not None
                    rows, cols, pitch_x, pitch_y = grid
                    image = load_image() if settings.snap_to_image else None
                    result = place_array(
                        settings, rows=rows, cols=cols, pitch_x_px=pitch_x, pitch_y_px=pitch_y, image=image,
                        progress=report, cancelled=self._cancel_event.is_set,
                    )
                outcome = ArrayOutcome(kind, result, settings, int(cube_index), float(wavelength_nm), tuple(roi_ids))
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(outcome, error)

        self._thread = threading.Thread(target=work, name="roi-array", daemon=True)
        self.running_changed.emit(True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def shutdown(self) -> None:
        """Application is quitting: stop the thread and wait briefly, so it never emits into a widget Qt is
        tearing down."""
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def _on_finished(self, outcome: object, error: object) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self.running_changed.emit(False)

        def fail(message: str) -> None:
            self.task_finished.emit(TASK_ID, "failed", message)
            self.failed.emit(message)

        if isinstance(error, Cancelled):
            self.task_finished.emit(TASK_ID, "cancelled", "")
            self.cancelled.emit()
            return
        if isinstance(error, ArrayError):
            fail(str(error))
            return
        if error is not None:
            logger.error("ROI array action crashed", exc_info=error)
            fail(f"Unexpected error: {error}")
            return
        assert isinstance(outcome, ArrayOutcome)
        self.task_finished.emit(TASK_ID, "completed", outcome.result.report)
        self.completed.emit(outcome)
