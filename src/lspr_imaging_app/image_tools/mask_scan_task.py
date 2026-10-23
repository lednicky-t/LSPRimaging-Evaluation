"""Runs the transient scan off the GUI thread and installs the result in `MaskPointersModule` (2026-10-17).

The pure computation is `mask/transient_scan.scan_transients`; this is only the thread/signal wrapper the rule in CLAUDE.md asks for
("Heavy work reports progress and can be cancelled"). The thread, signals, Cancel and the stale-result rule are in `ThreadedTask`.

- The thread is a plain `threading.Thread`: planes are read through `DatasetModule.load_plane_uncached`, which can be an OME-Zarr
  read, and the zarr rule forbids pool workers there. The scan's own reader threads are plain threads too.
- Progress is throttled to ~20 reports a second (`progress.throttled`): a scan reports once per frame.
- **The result belongs to the dataset and session the scan was started in** (2026-10-17, audit A1). The worker reads planes through
  the dataset module *as it is when each read happens*, so a dataset opened mid-scan would be read from and the result installed
  into the wrong session. `invalidate()` is called when the dataset is loaded or cleared, and when a session is restored.

Everything the thread needs is handed in by value in `start()` (the frame index, the plane loader, the settings); the thread
never reads a module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject

from ..progress import throttled
from .mask.transient_detection import KIND_ARTEFACT, KIND_GLOBAL, KIND_PARTICLE
from .mask.mask_pointers import MaskPointersChange, MaskPointersModule
from .mask.transient_scan import ScanParams, ScanResult, scan_transients
from ..threaded_task import ThreadedTask

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


class TransientScanTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "mask-transient-scan"
    crash_log = "The transient scan crashed"
    stopped_message = "stopped: the dataset or the session changed"

    def __init__(self, events: MaskPointersModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._events = events
        events.pointers_changed.connect(self._on_events_changed)

    def start(
        self,
        frame_index: Mapping[int, Sequence[float]],
        load_plane: Callable[[int, float], np.ndarray],
        params: ScanParams,
        worker_init: Callable[[], None] | None = None,
    ) -> None:
        """Begin a run over `frame_index` (cube -> wavelengths), reading planes with `load_plane` (called from several threads,
        so it must not touch the GUI or any shared cache)."""
        if self._running:
            raise RuntimeError("A scan is already running.")
        if not any(frame_index.values()):
            self.failed.emit("There is no dataset to scan.")
            return
        index = {int(c): tuple(float(w) for w in ws) for c, ws in frame_index.items()}  # a snapshot: the thread owns it now
        first, last = params.cube_range if params.cube_range is not None else (None, None)
        usable = [c for c, ws in index.items() if (first is None or c >= first) and (last is None or c <= last) and any(w > 0 for w in ws)]
        if len(usable) < 2:
            # Every frame is compared with the same wavelength in a neighbouring cube; with one cube nothing can be compared and
            # the scan would end with a clean-looking "0 particles".
            where = "the chosen range holds" if params.cube_range is not None else "the dataset has"
            self.failed.emit(
                "A scan compares each frame with the same wavelength in a neighbouring cube, so it needs at least two cubes; "
                f"{where} {len(usable)}."
            )
            return
        self._begin_run()
        self._start_thread(
            lambda: scan_transients(
                index,
                load_plane,
                params,
                progress=throttled(self._emit_progress),
                cancelled=self._cancel_event.is_set,
                worker_init=worker_init,
            )
        )

    def _on_events_changed(self, change: object) -> None:
        if isinstance(change, MaskPointersChange) and change.reason == "session_restored":
            self.invalidate()

    def _on_result(self, result: object) -> tuple[str, object]:
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
            f"{summary.artefacts} artefacts; {summary.frames_screened} of {summary.frames_in_range} frames compared; "
            f"{summary.seconds:.0f} s"
        )
        if summary.unreadable_frames:
            text += f"; {summary.unreadable_frames} frames could not be read"
        return text, summary
