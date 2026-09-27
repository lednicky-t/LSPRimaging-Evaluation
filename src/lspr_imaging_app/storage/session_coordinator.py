"""``SessionCoordinator`` - which named session is active for the currently
loaded dataset, and the Qt signal that tells the rest of the app to restore
into it (2026-09-26).

Lives beside ``session_autosave.py`` rather than under ``dataset/``: like
that class, this is a ``QObject`` coordinating *around* the dataset, not a
piece of the Dataset stage's own state (``dataset/module.py``'s "no other
module may read dataset state any other way" rule is about the loaded
dataset itself - which images exist - not about which derived-data folder
a session lives in).

This class knows nothing about the scientific modules a session restore
touches (geometry, ROIs, masks, ...) - same reason ``AnalysisEngine`` and
``SessionAutosave`` take callables instead of holding module references:
``app_rewrite.py`` stays the one place that knows about every module,
subscribing to :attr:`active_session_changed` to actually run a restore
(see ``_wire_session_coordinator``). This class only ever touches
``storage/session_index.py``'s plain-data functions and its own signals.

**Sessions always start blank** (maintainer's explicit decision,
2026-09-26) - :meth:`create_new` never copies a prior session's settings,
so there is no "clone" method here to be tempted into adding without
checking that decision again first.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from . import session_index
from .session_index import SessionRecord

logger = logging.getLogger(__name__)


class SessionCoordinator(QObject):
    """Owns "which session is active for this dataset." Construct once per
    window; call :meth:`bind_dataset`/:meth:`unbind` from `DatasetModule`'s
    `dataset_loaded`/`dataset_cleared` signals."""

    #: Every known session for the current dataset, and which one is active
    #: (``None`` when no dataset is loaded) - a UI session picker redraws
    #: from this.
    sessions_changed = pyqtSignal(tuple, object)  # tuple[SessionRecord, ...], str | None

    #: The active session's own folder (``sessions/<id>/`` under the
    #: dataset's ``home``), or ``None`` when no dataset is loaded. This is
    #: the one signal `app_rewrite.py` actually restores from.
    active_session_changed = pyqtSignal(object)  # Path | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._home: Path | None = None

    def home(self) -> Path | None:
        return self._home

    def sessions(self) -> tuple[SessionRecord, ...]:
        if self._home is None:
            return ()
        return session_index.load_session_index(self._home).sessions

    def active_session_id(self) -> str | None:
        if self._home is None:
            return None
        return session_index.load_session_index(self._home).active_session_id

    def bind_dataset(self, home: Path) -> None:
        """Point this coordinator at `home` (a dataset's `.home` folder) and
        resolve its active session - creating a first, blank one
        automatically if `home` has never used sessions before. That covers
        both a brand-new dataset and one only ever opened in the stable app
        (whose old `analysis/roi_table.json` etc. sit directly under `home`,
        untouched - see `session_index.py`'s module docstring)."""
        home = Path(home)
        self._home = home
        index = session_index.load_session_index(home)
        if not index.sessions:
            session_index.create_session(home)
            index = session_index.load_session_index(home)
        self._emit_current(index)

    def unbind(self) -> None:
        """Call when the dataset is cleared - no session is active until
        another dataset loads."""
        self._home = None
        self.sessions_changed.emit((), None)
        self.active_session_changed.emit(None)

    def switch_to(self, session_id: str) -> None:
        if self._home is None:
            raise RuntimeError("No dataset is loaded - there is no session to switch to.")
        session_index.set_active_session(self._home, session_id)
        self._emit_current(session_index.load_session_index(self._home))

    def create_new(self, label: str | None = None) -> SessionRecord:
        """Create a new, blank session and switch to it immediately."""
        if self._home is None:
            raise RuntimeError("No dataset is loaded - there is nowhere to create a session.")
        record = session_index.create_session(self._home, label=label)
        self._emit_current(session_index.load_session_index(self._home))
        return record

    def _emit_current(self, index: session_index.SessionIndex) -> None:
        self.sessions_changed.emit(index.sessions, index.active_session_id)
        root = None
        if self._home is not None and index.active_session_id is not None:
            root = session_index.session_dir_for(self._home, index.active_session_id)
        self.active_session_changed.emit(root)
