"""Makes the masks of the scan's particle pointers ("Auto mask", 2026-10-17) and commits them to `MaskModule` as ONE undoable step.

The pure part is `mask/transient_masks.py` (which events count, what mask a frame gets). This QObject is the progress / Cancel wrapper
the rule in CLAUDE.md asks for ("Heavy work reports progress and can be cancelled"), with the same signals as `ChromaticAutoDetect`
and `TransientScanTask` (`task_progress`, `task_finished` for the status bar's task indicator).

**Why this one runs on the GUI thread, in small slices** (a deliberate exception, measured): every frame needs the mask already in
effect there (`MaskModule.resolve_mask_source` and, when it was authored at another frame, `ChromaticModule.warp_mask_between`), and
those modules are read on the GUI thread only. Per frame the work is ~5 ms (the contour grown on its small box, one plane copy, one
compression), so a scan's few hundred frames are ~1-2 s. The task therefore works in slices of ~30 ms from the event loop: the window
stays live, progress is shown, Cancel works between slices, and nothing is written until the end (a cancelled run changes nothing).
Each mask is compressed while it is made (`MaskChange`), so the final commit is instant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .mask.model import MaskChange
from .mask.module import MaskModule
from .mask.transient_events_state import EventRecord
from .mask.transient_masks import frame_mask, group_by_frame, maskable

logger = logging.getLogger(__name__)

TASK_ID = "mask_auto"
TASK_LABEL = "Auto mask"
_SLICE_SECONDS = 0.03


@dataclass(frozen=True)
class AutoMaskSummary:
    frames_masked: int  # masks written (a frame whose mask did not change is not counted)
    frames_unchanged: int  # frames whose mask was already as it would be made
    events_used: int
    dilate_px: int
    seconds: float


class MaskAutoTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # AutoMaskSummary
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome, message; once per run

    def __init__(self, mask: MaskModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._running = False
        self._cancel = False
        self._groups: list[tuple[tuple[int, float], list[EventRecord]]] = []
        self._items: list[tuple[tuple[int, float], str, MaskChange]] = []
        self._position = 0
        self._shape: tuple[int, int] = (0, 0)
        self._dilate_px = 0
        self._resolve_base: Callable[[tuple[int, float]], np.ndarray | None] = lambda _frame: None
        self._events_used = 0
        self._started = 0.0

    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        records: Sequence[EventRecord],
        shape: tuple[int, int],
        dilate_px: int,
        resolve_base: Callable[[tuple[int, float]], np.ndarray | None],
    ) -> None:
        """Make the masks of the maskable events among `records` (particles, not dismissed), grown by `dilate_px`, merged onto the
        mask `resolve_base(frame)` gives (the mask in effect there, already in that frame's geometry)."""
        if self._running:
            raise RuntimeError("Auto mask is already running.")
        selected = maskable(records)
        if not selected:
            self.failed.emit("There are no particle pointers to mask (the scan found none, or they were all dismissed).")
            return
        self._groups = list(group_by_frame(selected).items())
        self._items = []
        self._position = 0
        self._shape = (int(shape[0]), int(shape[1]))
        self._dilate_px = max(int(dilate_px), 0)
        self._resolve_base = resolve_base
        self._events_used = len(selected)
        self._cancel = False
        self._running = True
        self._started = time.perf_counter()
        self.running_changed.emit(True)
        QTimer.singleShot(0, self._step)

    def cancel(self) -> None:
        self._cancel = True

    def _finish(self, outcome: str, message: str) -> None:
        self._running = False
        self._groups, self._items = [], []
        self.running_changed.emit(False)
        self.task_finished.emit(TASK_ID, outcome, message)

    def _step(self) -> None:
        if not self._running:
            return
        if self._cancel:
            self._finish("cancelled", "")
            self.cancelled.emit()
            return
        deadline = time.perf_counter() + _SLICE_SECONDS
        try:
            while self._position < len(self._groups) and time.perf_counter() < deadline:
                frame, group = self._groups[self._position]
                plane = frame_mask(group, self._shape, self._dilate_px, self._resolve_base(frame))
                self._items.append((frame, "individual", MaskChange(frame=frame, scope="individual", mask=plane)))
                self._position += 1
        except Exception as exc:  # noqa: BLE001 - a failing base (a chromatic model, a bad shape) must not leave a half-done batch
            logger.exception("Auto mask failed at frame %s", self._groups[self._position][0])
            message = f"Auto mask stopped at cube {self._groups[self._position][0][0]}, {self._groups[self._position][0][1]:g} nm: {exc}"
            self._finish("failed", message)
            self.failed.emit(message)
            return
        total = len(self._groups)
        self.task_progress.emit(TASK_ID, TASK_LABEL, self._position / total, f"mask {self._position}/{total}")
        if self._position < total:
            QTimer.singleShot(0, self._step)
            return
        written = self._mask.set_mask_changes(self._items, label=f"Auto mask ({total} frames)")
        summary = AutoMaskSummary(
            frames_masked=written,
            frames_unchanged=total - written,
            events_used=self._events_used,
            dilate_px=self._dilate_px,
            seconds=time.perf_counter() - self._started,
        )
        text = f"{written} masks from {self._events_used} particle events (+{self._dilate_px} px)"
        if summary.frames_unchanged:
            text += f"; {summary.frames_unchanged} already as they would be"
        self._finish("completed", text)
        self.completed.emit(summary)
