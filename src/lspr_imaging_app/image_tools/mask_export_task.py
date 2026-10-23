"""Writes every mask of the timeline as PNG files off the GUI thread (2026-10-17): the `ThreadedTask` around
`mask/export.export_masks`, with the same signals as the other tasks (`task_progress`, `task_finished` for the status bar's task
indicator). Everything the thread needs (the masks, which are immutable and compressed, the folder, the naming scheme) is handed in
by value in `start()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..analysis.provenance import FrameNamingScheme
from .mask.export import ExportResult, export_masks
from .mask.model import MaskChange
from ..threaded_task import ThreadedTask

TASK_ID = "mask_export"
TASK_LABEL = "Export masks"


class MaskExportTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "mask-export"
    crash_log = "The mask export failed"

    def start(self, changes: Sequence[MaskChange], folder: Path, naming: FrameNamingScheme) -> None:
        if self._running:
            raise RuntimeError("An export is already running.")
        if not changes:
            self.failed.emit("There are no masks to export.")
            return
        snapshot = tuple(changes)
        self._begin_run()
        self._start_thread(
            lambda: export_masks(snapshot, folder, naming, progress=self._emit_progress, cancelled=self._cancel_event.is_set)
        )

    def _failure_message(self, error: BaseException) -> str:
        return f"The export failed: {error}"

    def _on_result(self, result: object) -> tuple[str, object]:
        assert isinstance(result, ExportResult)
        return f"{result.written} masks written to {result.folder.name}", result
