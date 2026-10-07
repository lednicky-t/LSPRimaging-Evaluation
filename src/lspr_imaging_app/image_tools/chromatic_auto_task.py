"""Runs automatic landmark detection off the GUI thread and installs the
result in `ChromaticModule`.

The pure computation is `auto_landmarks.detect_and_track`; this QObject is
only the thread/signal wrapper the rule in CLAUDE.md asks for ("Heavy work
reports progress and can be cancelled"):

- `task_progress(task_id, label, fraction, message)` is the app-wide progress
  shape - one signal a future shared loading indicator can listen to for any
  task. The Chromatic tab and `panels/task_indicator.TaskIndicator` consume it.
- `task_finished(task_id, outcome, message)` closes that shape: emitted once
  per run, after success, failure or cancel alike (`outcome` is "completed",
  "failed" or "cancelled"; `message` is a one-line result for the indicator
  and the log). It is emitted just before the specific `completed`/`failed`/
  `cancelled` signal.
- A **plain `threading.Thread`**, never `QThreadPool`: planes are read
  through `DatasetModule.load_plane` (an OME-Zarr read) and the zarr rule
  forbids pool workers there.
- Cross-thread `emit()` is queued onto the receiver's thread by Qt, so every
  slot (and every module mutation) runs on the GUI thread.

Everything the thread needs is snapshotted by value in `start()` (geometry
settings, the plane loader, wavelengths); the thread never reads a module.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..progress import Cancelled
from .background.model import BackgroundSettings
from .chromatic import wavelength_interpolation
from .chromatic.auto_landmarks import AutoLandmarkError, AutoLandmarkParams, AutoLandmarkResult, detect_and_track
from .chromatic.model import ChromaticLandmarkObservation
from .chromatic.module import ChromaticModule
from .geometry.model import GeometrySettings
from .preprocess import apply_preprocessing

logger = logging.getLogger(__name__)

TASK_ID = "chromatic_auto_landmarks"
TASK_LABEL = "Chromatic landmarks"


@dataclass(frozen=True)
class AutoDetectSummary:
    """What the tab shows after a successful run."""

    kept_count: int
    requested_count: int
    tracked_wavelength_count: int
    total_wavelength_count: int
    loo_mean_px: float
    loo_max_px: float
    feature_diameter_px: float
    dropped: tuple[str, ...]
    spread_fraction: tuple[float, float] = (1.0, 1.0)


class ChromaticAutoDetect(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # AutoDetectSummary
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run

    # Internal: thread -> GUI thread hand-off (result or error, never both).
    _finished = pyqtSignal(object, object)

    def __init__(self, chromatic: ChromaticModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._chromatic = chromatic
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._pending: dict | None = None
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._thread is not None

    def start(
        self,
        *,
        cube_index: int,
        reference_wavelength_nm: float,
        image_keys: list[tuple[int, float]],
        landmark_count: int,
        sample_image_count: int,
        border_fraction: float,
        max_step_px: float,
        feature_diameter_px: float | None,
        geometry: GeometrySettings,
        load_plane: Callable[[int, float], np.ndarray],
    ) -> None:
        """Begin a run. `image_keys` = every (cube, wavelength) a model is
        wanted for (dark frame excluded); the reference cube's own entries
        define the wavelengths to track."""
        if self._thread is not None:
            raise RuntimeError("Automatic landmark detection is already running.")
        wavelengths = tuple(sorted({float(w) for c, w in image_keys if int(c) == int(cube_index)}))
        if len(wavelengths) < 2:
            self.failed.emit("The reference cube needs at least two wavelengths to measure chromatic shifts.")
            return
        # Same selection `ChromaticModule.refit` will make from the same inputs.
        samples = tuple(wavelength_interpolation.sampled_wavelengths(list(wavelengths), int(sample_image_count)))
        params = AutoLandmarkParams(
            reference_wavelength_nm=float(reference_wavelength_nm),
            all_wavelengths_nm=wavelengths,
            sample_wavelengths_nm=samples,
            landmark_count=int(landmark_count),
            border_fraction=float(border_fraction),
            max_step_px=float(max_step_px),
            feature_diameter_px=feature_diameter_px,
        )
        background = BackgroundSettings()  # flattening off: the contrast map does its own background removal

        def load_image(wavelength_nm: float) -> np.ndarray:
            return apply_preprocessing(load_plane(int(cube_index), float(wavelength_nm)), geometry, background)

        self._pending = dict(
            cube_index=int(cube_index),
            reference_wavelength_nm=float(reference_wavelength_nm),
            image_keys=list(image_keys),
            sample_image_count=int(sample_image_count),
            total_wavelengths=len(wavelengths),
        )
        self._cancel_event.clear()

        def work() -> None:
            result: AutoLandmarkResult | None = None
            error: BaseException | None = None
            try:
                result = detect_and_track(
                    params,
                    load_image,
                    progress=lambda fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message),
                    cancelled=self._cancel_event.is_set,
                )
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(result, error)

        self._thread = threading.Thread(target=work, name="chromatic-auto-landmarks", daemon=True)
        self.running_changed.emit(True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def shutdown(self) -> None:
        """Application is quitting: stop the thread and wait briefly, so it
        never emits into a widget Qt is tearing down (same reason
        `ImageRenderer.stop` is hooked to `aboutToQuit`)."""
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def _on_finished(self, result: object, error: object) -> None:
        thread, self._thread = self._thread, None
        pending, self._pending = self._pending, None
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
        if isinstance(error, AutoLandmarkError):
            fail(str(error))
            return
        if error is not None:
            logger.error("Automatic chromatic landmark detection crashed", exc_info=error)
            fail(f"Unexpected error: {error}")
            return
        assert isinstance(result, AutoLandmarkResult) and pending is not None
        try:
            self._apply(result, pending)
        except ValueError as exc:
            fail(str(exc))
            return
        self.task_finished.emit(
            TASK_ID,
            "completed",
            f"{result.kept_count}/{result.requested_count} landmarks kept, "
            f"{len(result.wavelengths_nm)}/{pending['total_wavelengths']} wavelengths tracked",
        )
        self.completed.emit(
            AutoDetectSummary(
                kept_count=result.kept_count,
                requested_count=result.requested_count,
                tracked_wavelength_count=len(result.wavelengths_nm),
                total_wavelength_count=pending["total_wavelengths"],
                loo_mean_px=result.loo_mean_px,
                loo_max_px=result.loo_max_px,
                feature_diameter_px=result.feature_diameter_px,
                dropped=result.dropped,
                spread_fraction=result.spread_fraction,
            )
        )

    def _apply(self, result: AutoLandmarkResult, pending: dict) -> None:
        cube = pending["cube_index"]
        observations = [
            ChromaticLandmarkObservation(
                landmark_id=landmark + 1,
                spectral_cube_index=cube,
                wavelength_nm=float(wavelength),
                x_px=float(result.positions_px[index, landmark, 0]),
                y_px=float(result.positions_px[index, landmark, 1]),
            )
            for index, wavelength in enumerate(result.wavelengths_nm)
            for landmark in range(result.kept_count)
        ]
        self._chromatic.apply_automatic_result(
            sample_image_count=pending["sample_image_count"],
            reference_key=(cube, pending["reference_wavelength_nm"]),
            observations=observations,
            image_keys=pending["image_keys"],
        )
