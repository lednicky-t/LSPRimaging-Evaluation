"""Makes the masks of the scan's particle pointers ("Auto mask", 2026-10-17) and commits them to `MaskModule` as ONE undoable step.

The pure part is `mask/transient_masks.py` (which events count, what mask a frame gets). This QObject is the thread / progress /
Cancel wrapper the rule in CLAUDE.md asks for ("Heavy work reports progress and can be cancelled"), with the same signals as
`ChromaticAutoDetect` and `TransientScanTask` (`task_progress`, `task_finished` for the status bar's task indicator).

**Where the work runs** (changed 2026-10-17, audit B1; it used to run on the GUI thread in 30 ms slices). Per frame the task needs
the mask already in effect there. Only *finding* it needs the modules (`MaskModule.resolve_mask_change`, one dict lookup, and
`ChromaticModule.affine_between`, a 2x3 matrix, ~40 microseconds together): `start()` does that for all frames in one quick loop on the
GUI thread and hands the results to a plain `threading.Thread` as `BaseMask`es (an immutable compressed mask and a matrix). The thread
does the expensive part, which touches no module: decode the mask, warp it into the frame (3.2 ms of the 3.9 ms a frame costs), OR
the contour grown by N px, compress. The result comes back through a queued signal and is committed on the GUI thread, so the window
never stalls and a few hundred frames cost no slice of GUI time at all.

**A run belongs to the mask, dataset and session it started with** (audit A1, A2). Nothing is written until the end, and the end
refuses to write if the mask timeline changed meanwhile (a brush stroke on a frame that is also being auto-masked would otherwise be
replaced without a word; a session restore also announces itself as a mask change) or if `invalidate()` was called (the dataset was
loaded or cleared). Cancel and a failure change nothing either.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from ..progress import Cancelled, throttled
from .chromatic.warp import warp_boolean_mask_affine
from .mask.model import MaskChange
from .mask.module import MaskComputationalChange, MaskModule
from .mask.mask_pointers_state import MaskPointer
from .mask.transient_masks import BaseDecoder, BaseMask, frame_mask, group_by_frame, materialize_base, maskable

logger = logging.getLogger(__name__)

TASK_ID = "mask_auto"
TASK_LABEL = "Auto mask"

Frame = tuple[int, float]


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

    # Internal: thread -> GUI thread hand-off (the finished masks or an error, never both).
    _finished = pyqtSignal(object, object)

    def __init__(self, mask: MaskModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._running = False
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._generation = 0  # bumped by `invalidate`
        self._run_generation = 0
        self._mask_moved = False  # the mask timeline changed while the run was going on
        self._events_used = 0
        self._dilate_px = 0
        self._frames = 0
        self._started = 0.0
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        records: Sequence[MaskPointer],
        shape: tuple[int, int],
        dilate_px: int,
        plan_base: Callable[[Frame], BaseMask | None],
    ) -> None:
        """Make the masks of the maskable events among `records` (particles, not dismissed), grown by `dilate_px`, merged onto the
        mask in effect at each frame. ``plan_base(frame)`` says what is in effect there (a `BaseMask`, or ``None`` for nothing yet);
        it is called here, on the calling (GUI) thread, once per frame, and must be quick (`panels.mask_edit_common.plan_mask_edit_base`)."""
        if self._running:
            raise RuntimeError("Auto mask is already running.")
        selected = maskable(records)
        if not selected:
            self.failed.emit("There are no particle pointers to mask (the scan found none, or they were all dismissed).")
            return
        groups = list(group_by_frame(selected).items())
        shape = (int(shape[0]), int(shape[1]))
        self._dilate_px = max(int(dilate_px), 0)
        self._events_used = len(selected)
        self._frames = len(groups)
        self._cancel_event.clear()
        self._mask_moved = False
        self._run_generation = self._generation
        self._started = time.perf_counter()
        self._running = True
        self.running_changed.emit(True)
        plans: list[BaseMask | None] = []
        try:
            for frame, _group in groups:
                plans.append(plan_base(frame))
        except Exception as exc:  # noqa: BLE001 - a failing lookup (a chromatic model) must not leave a half-done run
            self._fail(groups[len(plans)][0], exc)
            return
        self._mask.mask_changed.connect(self._on_mask_changed)
        work = _Work(groups, plans, shape, self._dilate_px, self._cancel_event, self._emit_progress)
        self._thread = threading.Thread(target=self._run, args=(work,), name="mask-auto", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def invalidate(self) -> None:
        """The dataset was loaded or cleared under a running Auto mask: the frames it is making masks for may not exist any more. Stops
        it, and makes sure nothing is written even if it finishes first."""
        self._generation += 1
        if self._running:
            logger.info("Auto mask is stopped: the dataset changed")
            self._cancel_event.set()

    def shutdown(self) -> None:
        """Application is quitting: stop the thread and wait briefly, so it never emits into a widget Qt is tearing down."""
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    # -- the worker thread -----------------------------------------------------------------------------------

    def _emit_progress(self, fraction: float, message: str) -> None:
        self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message)

    def _run(self, work: _Work) -> None:
        masks: list[tuple[Frame, str, MaskChange]] | None = None
        error: BaseException | None = None
        try:
            masks = work.make_masks()
        except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
            error = exc
        self._finished.emit(masks, (error, work.current_frame) if error is not None else None)

    # -- back on the GUI thread ------------------------------------------------------------------------------

    def _on_mask_changed(self, _change: MaskComputationalChange) -> None:
        self._mask_moved = True

    def _fail(self, frame: Frame, exc: BaseException) -> None:
        logger.error("Auto mask failed at frame %s", frame, exc_info=exc)
        message = f"Auto mask stopped at cube {frame[0]}, {frame[1]:g} nm: {exc}"
        self._finish("failed", message)
        self.failed.emit(message)

    def _finish(self, outcome: str, message: str) -> None:
        self._running = False
        self._thread = None
        try:
            self._mask.mask_changed.disconnect(self._on_mask_changed)
        except (TypeError, RuntimeError):  # never connected (a failure while planning)
            pass
        self.running_changed.emit(False)
        self.task_finished.emit(TASK_ID, outcome, message)

    def _on_finished(self, masks: object, failure: object) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        moved = self._mask_moved
        if self._run_generation != self._generation:
            self._finish("cancelled", "stopped: the dataset changed")
            self.cancelled.emit()
            return
        if failure is not None:
            error, frame = failure  # type: ignore[misc]
            if isinstance(error, Cancelled):
                self._finish("cancelled", "")
                self.cancelled.emit()
            else:
                self._fail(frame, error)
            return
        if moved:
            message = "Auto mask was stopped: the mask was changed while it ran, so nothing was written. Run it again."
            self._finish("failed", message)
            self.failed.emit(message)
            return
        assert isinstance(masks, list)
        self._mask.mask_changed.disconnect(self._on_mask_changed)  # the commit below is our own change
        written = self._mask.set_mask_changes(masks, label=f"Auto mask ({self._frames} frames)")
        summary = AutoMaskSummary(
            frames_masked=written,
            frames_unchanged=self._frames - written,
            events_used=self._events_used,
            dilate_px=self._dilate_px,
            seconds=time.perf_counter() - self._started,
        )
        text = f"{written} masks from {self._events_used} particle events (+{self._dilate_px} px)"
        if summary.frames_unchanged:
            text += f"; {summary.frames_unchanged} already as they would be"
        self._finish("completed", text)
        self.completed.emit(summary)


class _Work:
    """Everything the worker thread needs, handed over by value: it never reads a module."""

    def __init__(
        self,
        groups: list[tuple[Frame, list[MaskPointer]]],
        plans: list[BaseMask | None],
        shape: tuple[int, int],
        dilate_px: int,
        cancel_event: threading.Event,
        progress: Callable[[float, str], None],
    ) -> None:
        self.groups = groups
        self.plans = plans
        self.shape = shape
        self.dilate_px = dilate_px
        self.cancel_event = cancel_event
        self.progress = throttled(progress)
        self.current_frame: Frame = groups[0][0]

    def make_masks(self) -> list[tuple[Frame, str, MaskChange]]:
        decoder = BaseDecoder()
        total = len(self.groups)
        items: list[tuple[Frame, str, MaskChange]] = []
        for index, ((frame, group), plan) in enumerate(zip(self.groups, self.plans)):
            if self.cancel_event.is_set():
                raise Cancelled()
            self.current_frame = frame
            base = materialize_base(plan, warp_boolean_mask_affine, decoder)
            plane = frame_mask(group, self.shape, self.dilate_px, base, copy_base=False)
            items.append((frame, "individual", MaskChange(frame=frame, scope="individual", mask=plane)))
            self.progress((index + 1) / total, f"mask {index + 1}/{total}")
        return items
