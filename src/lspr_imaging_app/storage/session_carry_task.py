"""Carries the session along with an OME-Zarr export (2026-10-22). The pure part is `session_carry.py`; this QObject is the
thread / progress / Cancel wrapper the rule in CLAUDE.md asks for ("Heavy work reports progress and can be cancelled"), with
the same signals as the other tasks (`task_progress`, `task_finished` for the status bar's task indicator).

**When things happen**

1. ``prepare()`` runs on the GUI thread at the moment the user starts an export: it reads the session from the modules and
   copies the mutable parts (`snapshot_state`), so the session that is carried is the one the export was started with, however
   much the user edits while the images are written. It does nothing else.
2. The export runs (`DatasetModule.export_to_ome_zarr`). If it fails or is cancelled, the prepared session is dropped.
3. When the export finishes, one plain ``threading.Thread`` (never a ``QThreadPool``: it is next to the zarr layer) moves the
   session into the new coordinates (`carry_session`), creates a session in the exported dataset's own ``sessions/`` folder,
   writes it and `derived_from.json`, and emits ``carried``. The window never stalls: saving is not the GUI thread's business.
4. The app asks the user whether to open the exported dataset; opening it makes the carried session the active one, exactly as
   if it had been chosen by hand (`SessionCoordinator.bind_dataset` finds the index and uses its active session).

The source dataset and its session are only read. The exported dataset's session is **written once, here**; the autosave takes
over when it is opened.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.provenance import FrameNamingScheme
from ..dataset.export_stats import ExportStats
from ..progress import Cancelled, throttled
from . import session_carry as carry
from . import session_index
from .session import SessionState, SessionWriter, prepare_session

logger = logging.getLogger(__name__)

TASK_ID = "session_carry"
TASK_LABEL = "Carry session"

_WRITE_SHARE = 0.1  # of the bar: the carry itself is 0..0.9, the files 0.9..1


@dataclass(frozen=True)
class CarriedResult:
    """What a finished carry hands to the app."""

    destination: Path  # the exported dataset (its home)
    session_id: str
    report: carry.CarryReport
    seconds: float
    export_stats: ExportStats | None = None  # how the export that came with it went (`DatasetModule.export_stats_ready`)


@dataclass(frozen=True)
class _Pending:
    state: SessionState
    transform: carry.ExportTransform
    naming: FrameNamingScheme
    source_folder: Path | None
    source_session_id: str | None
    source_name: str


class SessionCarry(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome ("completed" | "failed" | "cancelled"), message
    carried = pyqtSignal(object)  # CarriedResult
    failed = pyqtSignal(str)  # readable message
    running_changed = pyqtSignal(bool)

    def __init__(
        self,
        capture: Callable[[], SessionState],
        naming: Callable[[], FrameNamingScheme],
        source: Callable[[], tuple[Path | None, str | None]],
        parent: QObject | None = None,
    ) -> None:
        """`capture` reads the session from the modules (`app_rewrite.capture_session`), `naming` is the frame-tag scheme, `source`
        gives the source dataset's home folder and the active session id (for the provenance record). All three are called on
        the GUI thread, in `prepare`."""
        super().__init__(parent)
        self._capture = capture
        self._naming = naming
        self._source = source
        self._pending: _Pending | None = None
        self._export_stats: ExportStats | None = None  # arrives just before `start`, from the same export
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    # -- commands (GUI thread) ----------------------------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def prepare(self, transform: carry.ExportTransform) -> None:
        """Remember the session as it is now, to be carried when the export that is about to start finishes. A second call
        replaces the first. Raises ``RuntimeError`` if an earlier carry is still writing (the caller reports it)."""
        if self.is_running():
            raise RuntimeError("The previous session carry is still running.")
        self._export_stats = None
        folder, session_id = self._source()
        self._pending = _Pending(
            state=carry.snapshot_state(self._capture()),
            transform=transform,
            naming=self._naming(),
            source_folder=folder,
            source_session_id=session_id,
            source_name=Path(folder).name if folder is not None else "the source dataset",
        )

    def discard(self) -> None:
        """Forget the prepared session (the export failed or was cancelled)."""
        self._pending = None
        self._export_stats = None

    def set_export_stats(self, stats: object) -> None:
        """Remember how the export that is finishing went, to be shown with the carried session (`DatasetModule.export_stats_ready`,
        which is emitted just before `export_finished`)."""
        self._export_stats = stats if isinstance(stats, ExportStats) else None

    def has_pending(self) -> bool:
        return self._pending is not None

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, destination: Path) -> None:
        """Begin carrying the prepared session into the exported dataset at `destination`. Does nothing if none was prepared."""
        pending, self._pending = self._pending, None
        if pending is None:
            return
        self._cancel.clear()
        self.running_changed.emit(True)
        export_stats, self._export_stats = self._export_stats, None
        self._thread = threading.Thread(
            target=self._run, args=(pending, Path(destination), export_stats), name="session-carry", daemon=True
        )
        self._thread.start()

    # -- the worker thread --------------------------------------------------------------------------------------------

    def _run(self, pending: _Pending, destination: Path, export_stats: ExportStats | None = None) -> None:
        started = time.perf_counter()
        report_progress = throttled(lambda fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message))
        self.task_progress.emit(TASK_ID, TASK_LABEL, 0.0, "Moving the session into the exported dataset...")
        session_dir: Path | None = None
        index_before: session_index.SessionIndex | None = None
        try:
            result = carry.carry_session(
                pending.state,
                pending.transform,
                lambda fraction, message: report_progress(fraction * (1.0 - _WRITE_SHARE), message),
                self._cancel.is_set,
            )
            report_progress(1.0 - _WRITE_SHARE, "Writing the session...")
            index_before = session_index.load_session_index(destination)
            label = f"Carried from {pending.source_name}"
            record = session_index.create_session(destination, label=label)
            session_dir = session_index.session_dir_for(destination, record.session_id)
            if self._cancel.is_set():
                raise Cancelled()
            SessionWriter().write(session_dir, prepare_session(result.state, pending.naming))
            carry.write_origin(
                session_dir,
                source_folder=pending.source_folder,
                source_session_id=pending.source_session_id,
                transform=pending.transform,
                report=result.report,
                zarr_attributes=carry.read_zarr_attributes(destination),
            )
        except Cancelled:
            self._roll_back(destination, session_dir, index_before)
            self._finish("cancelled", "")
            return
        except Exception as exc:  # noqa: BLE001 - reported through the signals, not re-raised on a worker thread
            logger.error("Carrying the session into %s failed", destination, exc_info=exc)
            self._roll_back(destination, session_dir, index_before)
            self._finish("failed", str(exc))
            self.failed.emit(str(exc))
            return
        seconds = time.perf_counter() - started
        logger.info(
            "session.carry destination=%s session=%s seconds=%.1f %s",
            destination.name, record.session_id, seconds, result.report.summary(),
        )
        self._finish("completed", result.report.summary())
        self.carried.emit(CarriedResult(destination, record.session_id, result.report, seconds, export_stats))

    def _roll_back(
        self, destination: Path, session_dir: Path | None, index_before: session_index.SessionIndex | None
    ) -> None:
        """A half-written session is worse than none: take its folder and its index entry away again."""
        try:
            if session_dir is not None:
                shutil.rmtree(session_dir, ignore_errors=True)
            if index_before is not None:
                session_index.save_session_index(destination, index_before)
        except Exception:  # noqa: BLE001 - nothing more can be done; the log has the rest
            logger.warning("Could not undo a half-written carried session in %s", destination, exc_info=True)

    def _finish(self, outcome: str, message: str) -> None:
        self.task_finished.emit(TASK_ID, outcome, message)
        self.running_changed.emit(False)
