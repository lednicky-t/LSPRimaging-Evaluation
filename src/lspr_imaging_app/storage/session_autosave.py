"""Debounced session autosave - the trigger layer `storage/session.py`
deliberately doesn't own (built 2026-09-23).

`save_session`/`load_session` were built the day before with nothing calling
them: no autosave, no save-on-close, no load-on-open. This is that wiring,
kept out of `session.py` because that file is Qt-free on purpose (it takes
and returns plain dataclasses so a test can drive it without a
`QApplication`), and a debounce needs a `QTimer`.

**The policy, ported from the old app** (`gui/main_window.py`'s
`_processing_state_save_timer` and `SessionStateManager
.save_processing_state_for_dataset`), including its interval and its
reasoning:

- **Coalesce edits, don't save per edit.** A single ROI drag, key-repeat
  nudge or brush stroke emits a change signal per step. Writing the session
  on each one puts a real disk write in the middle of an interaction; one
  write a couple of seconds after the user stops is indistinguishable in
  outcome and invisible in feel. This is an evaluation app working from data
  it never mutates, so a burst of edits collapsing into one write loses
  nothing - unlike the acquisition app, where CLAUDE.md's lossless rule
  forbids exactly this kind of coalescing for raw data.
- **2500 ms**, the same interval the old app settled on, for the same
  reason: seconds rather than milliseconds, because the timeout fires the
  real write.
- **Checkpoints bypass the debounce.** A dataset switch, a session load and
  application quit each need the file on disk *before* the next thing
  happens, so those call `flush()` rather than `schedule()`.

**Two things here that the old app does differently, both deliberate:**

1. **A plain dirty flag, not a content signature.** The old app rebuilds the
   whole payload and compares it against the last-written one to decide
   whether to skip a save - it has to, because its save triggers fire
   whether or not anything really changed, and rebuilding that payload is
   itself measured in seconds on a real dataset. Here a save is only ever
   scheduled by a module's own change signal, so "was anything scheduled
   since the last successful write" answers the same question for free. The
   trade-off is that a change signal that carries no real change still costs
   one write; that is a cheap wrong answer, where rebuilding a payload to
   find out is an expensive right one.
2. **The write is synchronous, on the GUI thread.** The old app dispatches
   it to a worker after finding it froze the UI for a beat. That may well
   need doing here too - but it needs a real measurement first (AGENTS.md's
   performance rules), and doing it safely means serializing concurrent
   saves to one file, which is more machinery than an unmeasured problem
   justifies. `capture()` already returns a defensive copy of every
   module's state, so moving just the write off-thread stays a small change
   when there is a number to justify it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from ..analysis.provenance import FrameNamingScheme
from .session import SessionState, save_session

logger = logging.getLogger(__name__)

AUTOSAVE_DEBOUNCE_MS = 2500
"""See the module docstring - the old app's own interval, kept."""


class SessionAutosave(QObject):
    """Turns "something changed" signals into at most one session write per
    `AUTOSAVE_DEBOUNCE_MS` of activity.

    Takes `capture`/`naming` as callables rather than module references, the
    same convention `AnalysisEngine` and `ImageRenderer` follow: this class
    then holds no module state of its own and a test can drive it with two
    plain functions.
    """

    def __init__(
        self,
        capture: Callable[[], SessionState],
        naming: Callable[[], FrameNamingScheme],
        *,
        save: Callable[[Path, SessionState, FrameNamingScheme], Path] = save_session,
        interval_ms: int = AUTOSAVE_DEBOUNCE_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._capture = capture
        self._naming = naming
        self._save = save
        self._root: Path | None = None
        self._enabled = True
        self._dirty = False
        self._suspended = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self._save_now)

    # -- which dataset is being saved --------------------------------------

    def root(self) -> Path | None:
        """The dataset folder being autosaved into, or `None` when nothing
        is loaded and every `schedule()` is a no-op."""
        return self._root

    def set_root(self, root: Path | None, *, enabled: bool = True) -> None:
        """Point this at a dataset's folder, **flushing anything still
        pending for the previous one first**.

        Without that flush, switching datasets within the debounce window
        would either lose the last edits to the old dataset or - worse -
        write them into the new dataset's folder, since the pending timer
        holds no root of its own.

        `enabled=False` accepts the root but refuses to ever write to it.
        That is for one specific case: a session file that exists but could
        not be read (corrupt, or a schema this build doesn't know). The
        modules are then sitting at defaults, which is *not* what that file
        describes, so autosaving would overwrite a recoverable file with
        blank state - the one genuinely destructive thing this class could
        do."""
        self.flush()
        self._root = None if root is None else Path(root)
        self._enabled = enabled
        self._dirty = False

    # -- the debounce ------------------------------------------------------

    def schedule(self, *_args: object) -> None:
        """Note that something changed and (re)start the debounce. Takes
        `*_args` so it can be connected straight to any module's change
        signal without a lambda per connection."""
        if self._suspended or self._root is None or not self._enabled:
            return
        self._dirty = True
        self._timer.start()

    def flush(self) -> None:
        """Write now, if anything is pending. Safe to call when nothing is
        loaded or nothing changed - both are no-ops, which is what makes it
        safe to hang off `aboutToQuit` unconditionally."""
        self._timer.stop()
        self._save_now()

    def _save_now(self) -> None:
        if not self._dirty or self._root is None or not self._enabled:
            return
        try:
            self._save(self._root, self._capture(), self._naming())
        except Exception:
            # A failed session write must not take the application down, and
            # must not be silent either - the same treatment every other
            # background failure in this app gets. `_dirty` stays set, so
            # the next edit or the quit flush tries again rather than
            # treating a failed write as a completed one.
            logger.exception("Session autosave failed for %s", self._root)
            return
        self._dirty = False
        logger.debug("Session autosaved to %s", self._root)

    @contextmanager
    def suspended(self) -> Iterator[None]:
        """Ignore change signals for the duration - for a session *restore*,
        which necessarily emits from every module it touches.

        Leaves the state clean on exit rather than merely un-suspending:
        what is in memory after a restore is exactly what was just read off
        disk, so there is nothing to write back. Suppressing the writes but
        leaving the dirty flag set would make the very next quit re-save an
        identical file. Any edit pending from *before* the restore is
        dropped with it, which is correct - the restore replaced whatever
        that edit was made to."""
        previous_suspended = self._suspended
        self._suspended = True
        self._timer.stop()
        try:
            yield
        finally:
            self._suspended = previous_suspended
            self._dirty = False
