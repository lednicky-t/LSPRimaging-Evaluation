"""Runs the transient scan off the GUI thread and installs the result in `TransientEventsModule` (2026-10-17).

The pure computation is `mask/transient_scan.scan_transients`; this QObject is only the thread/signal wrapper the rule in CLAUDE.md
asks for ("Heavy work reports progress and can be cancelled"). It copies the shape of `chromatic_auto_task.ChromaticAutoDetect`:

- `task_progress(task_id, label, fraction, message)` and `task_finished(task_id, outcome, message)` are the app-wide task-indicator
  shapes (`panels/task_indicator.py`); `task_finished` is emitted once per run, after success, failure or cancel alike.
- A **plain `threading.Thread`**, never `QThreadPool`: planes are read through `DatasetModule.load_plane_uncached`, which can be an
  OME-Zarr read, and the zarr rule forbids pool workers there. The scan's own reader threads are plain threads too.
- Cross-thread `emit()` is queued onto the receiver's thread by Qt, so the result is installed (and every slot runs) on the GUI
  thread.

Everything the thread needs is handed in by value in `start()` (the frame index, the plane loader, the settings); the thread
never reads a module.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..progress import Cancelled
from .mask.transient_detection import KIND_ARTEFACT, KIND_GLOBAL, KIND_PARTICLE
from .mask.transient_events import TransientEventsModule
from .mask.transient_scan import ScanParams, ScanResult, scan_transients

logger = logging.getLogger(__name__)

TASK_ID = "mask_transient_scan"
TASK_LABEL = "Mask scan"


@dataclass(frozen=True)
class ScanSummary:
    """What the Scan group shows after a successful run."""

    particles: int
    artefacts: int
    globals_: int
    passages: int
    frames_with_events: int
    frames_in_range: int
    frames_screened: int
    unreadable_frames: int
    seconds: float


class TransientScanTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # ScanSummary
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run

    # Internal: thread -> GUI thread hand-off (result or error, never both).
    _finished = pyqtSignal(object, object)

    def __init__(self, events: TransientEventsModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._events = events
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._thread is not None

    def start(
        self,
        frame_index: Mapping[int, Sequence[float]],
        load_plane: Callable[[int, float], np.ndarray],
        params: ScanParams,
        worker_init: Callable[[], None] | None = None,
    ) -> None:
        """Begin a run over `frame_index` (cube -> wavelengths), reading planes with `load_plane` (called from several threads,
        so it must not touch the GUI or any shared cache)."""
        if self._thread is not None:
            raise RuntimeError("A scan is already running.")
        if not any(frame_index.values()):
            self.failed.emit("There is no dataset to scan.")
            return
        index = {int(c): tuple(float(w) for w in ws) for c, ws in frame_index.items()}  # a snapshot: the thread owns it now
        self._cancel_event.clear()

        def work() -> None:
            result: ScanResult | None = None
            error: BaseException | None = None
            try:
                result = scan_transients(
                    index,
                    load_plane,
                    params,
                    progress=lambda fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message),
                    cancelled=self._cancel_event.is_set,
                    worker_init=worker_init,
                )
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(result, error)

        self._thread = threading.Thread(target=work, name="mask-transient-scan", daemon=True)
        self.running_changed.emit(True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def shutdown(self) -> None:
        """Application is quitting: stop the thread and wait briefly, so it never emits into a widget Qt is tearing down."""
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    def _on_finished(self, result: object, error: object) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self.running_changed.emit(False)

        if isinstance(error, Cancelled):
            self.task_finished.emit(TASK_ID, "cancelled", "")
            self.cancelled.emit()
            return
        if error is not None:
            logger.error("The transient scan crashed", exc_info=error)
            message = f"Unexpected error: {error}"
            self.task_finished.emit(TASK_ID, "failed", message)
            self.failed.emit(message)
            return
        assert isinstance(result, ScanResult)
        self._events.set_scan_result(result)
        kinds = [e.kind for e in result.events]
        summary = ScanSummary(
            particles=kinds.count(KIND_PARTICLE),
            artefacts=kinds.count(KIND_ARTEFACT),
            globals_=kinds.count(KIND_GLOBAL),
            passages=len({e.passage_id for e in result.events if e.passage_id is not None}),
            frames_with_events=len({e.frame for e in result.events}),
            frames_in_range=result.frames_in_range,
            frames_screened=result.frames_screened,
            unreadable_frames=len(result.failed_frames),
            seconds=float(result.seconds),
        )
        text = (
            f"{summary.particles} particles in {summary.passages} passages, {summary.globals_} global, "
            f"{summary.artefacts} artefacts; {summary.seconds:.0f} s"
        )
        if summary.unreadable_frames:
            text += f"; {summary.unreadable_frames} frames could not be read"
        self.task_finished.emit(TASK_ID, "completed", text)
        self.completed.emit(summary)
