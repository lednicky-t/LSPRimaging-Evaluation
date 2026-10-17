"""Writes every mask of the timeline as PNG files off the GUI thread (2026-10-17): the thread/signal wrapper around
`mask/export.export_masks`, with the same signals as the other tasks (`task_progress`, `task_finished` for the status bar's task
indicator). A **plain `threading.Thread`**; everything the thread needs (the masks, which are immutable and compressed, the folder, the
naming scheme) is handed in by value in `start()`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.provenance import FrameNamingScheme
from ..progress import Cancelled
from .mask.export import ExportResult, export_masks
from .mask.model import MaskChange

logger = logging.getLogger(__name__)

TASK_ID = "mask_export"
TASK_LABEL = "Export masks"


class MaskExportTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # ExportResult
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run
    _finished = pyqtSignal(object, object)  # thread -> GUI thread hand-off (result or error)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._thread is not None

    def start(self, changes: Sequence[MaskChange], folder: Path, naming: FrameNamingScheme) -> None:
        if self._thread is not None:
            raise RuntimeError("An export is already running.")
        if not changes:
            self.failed.emit("There are no masks to export.")
            return
        snapshot = tuple(changes)
        self._cancel_event.clear()

        def work() -> None:
            result: ExportResult | None = None
            error: BaseException | None = None
            try:
                result = export_masks(
                    snapshot, folder, naming,
                    progress=lambda fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message),
                    cancelled=self._cancel_event.is_set,
                )
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(result, error)

        self._thread = threading.Thread(target=work, name="mask-export", daemon=True)
        self.running_changed.emit(True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def shutdown(self) -> None:
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
            logger.error("The mask export failed", exc_info=error)
            message = f"The export failed: {error}"
            self.task_finished.emit(TASK_ID, "failed", message)
            self.failed.emit(message)
            return
        assert isinstance(result, ExportResult)
        self.task_finished.emit(TASK_ID, "completed", f"{result.written} masks written to {result.folder.name}")
        self.completed.emit(result)
