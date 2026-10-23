"""Reads a session from disk off the GUI thread (2026-10-22). The pure part is `session.load_session`; this is the thread / progress /
Cancel wrapper the rule in CLAUDE.md asks for ("Heavy work reports progress and can be cancelled"), with the same signals as the other
tasks (`task_progress`, `task_finished` for the status bar's task indicator).

**Why** (maintainer rule, 2026-10-17: file I/O has no business on the GUI thread): a session with hundreds of masks takes seconds to
read (411 PNGs: about 4 s after the decode got its thread pool), and the window was frozen for all of it each time a dataset opened.

**When things happen** (`app_rewrite._wire_session_coordinator`)

1. The active session changes. The modules are reset to a blank session at once, so nothing of the previous session lingers, and
   **autosave stays off for the new root**: a blank state must never be written over the saved one.
2. ``load(root)`` reads the session on one plain ``threading.Thread`` (the PNG decode has its own small pool inside it). The window stays
   alive, and the status bar says what is being read.
3. Back on the GUI thread, ``completed`` hands the `LoadedSession` over; the app applies it and switches autosave on for the root.
   A load that fails or is cancelled leaves autosave off for that session (``ended`` says so), so the file on disk is never replaced
   by the blank state; the failure text is shown in the status bar (before this, a failed read was only written to the log).

A second ``load`` while one is running stops the first and starts after it has ended; a result that belongs to a session that has
been left since is dropped by `ThreadedTask` and never applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..progress import throttled
from ..threaded_task import ThreadedTask
from .session import SessionState, load_session

TASK_ID = "session_load"
TASK_LABEL = "Loading session"


@dataclass(frozen=True)
class LoadedSession:
    root: Path
    state: SessionState | None  # None: there was no session file after all


class SessionLoadTask(ThreadedTask):
    ended = pyqtSignal(object, str)  # root, outcome ("completed" | "failed" | "cancelled"); every run, a dropped one included

    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "session-load"
    crash_log = "The session could not be read"
    stopped_message = "stopped: another session was opened"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None  # the session the current (or just ended) run reads
        self._queued: Path | None = None

    def load(self, root: Path) -> None:
        """Read the session of `root` (GUI thread). Stops a load that is still running and starts this one when it has ended."""
        root = Path(root)
        if self._running:
            self._queued = root
            self.invalidate()
            return
        self._begin(root)

    def discard(self) -> None:
        """The session being loaded is no longer wanted (another one is opening, or the dataset was closed)."""
        self._queued = None
        self.invalidate()

    def _begin(self, root: Path) -> None:
        self._root = root
        self._begin_run()
        self._start_thread(lambda: load_session(root, progress=throttled(self._emit_progress), cancelled=self._cancel_event.is_set))

    def _failure_message(self, error: BaseException) -> str:
        return f"The session could not be read: {error}. Autosave is off for this session so its file is not overwritten."

    def _on_result(self, result: object) -> tuple[str, object]:
        assert self._root is not None
        state = result if isinstance(result, SessionState) else None
        text = "no saved session" if state is None else f"{len(state.mask_changes)} masks, {len(state.rois)} ROIs"
        return text, LoadedSession(self._root, state)

    def _report(self, outcome: str, message: str) -> None:
        if outcome == "cancelled" and not message:
            message = "The session was not loaded. Autosave is off for this session so its file is not overwritten."
        super()._report(outcome, message)
        self.ended.emit(self._root, outcome)

    def _after_run(self) -> None:
        if self._queued is not None:
            root, self._queued = self._queued, None
            self._begin(root)
