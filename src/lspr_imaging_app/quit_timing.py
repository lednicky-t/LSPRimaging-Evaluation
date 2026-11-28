"""Where the time goes when the application is closed (2026-11-28; `docs/startup_and_close_2026-11-28.md`).

Before this, the log ended at ``warm worker pool closed`` and nothing said how long the rest of the close took. Four marks, all INFO, each with
``since_quit_ms`` (time since the first ``aboutToQuit`` slot ran):

* ``app.quit``: the quit began (the first slot connected to ``aboutToQuit``).
* ``app.quit_slots_done``: every slot connected before ``main`` started the event loop has run (autosave flush, window state, thread / pool shutdowns).
* ``app.exec_returned``: the event loop ended; ``main`` is about to ``sys.exit``.
* ``app.exit``: Python is exiting and has joined its non-daemon threads (the helper process executors are joined there); ``threads`` names the
  threads still alive. Time after this line (module teardown, process end) is not visible in the log.

Slots are *not* wrapped to time each one: a wrapper keeps the receiver alive and calls it even after Qt would have disconnected it (the sip crash-on-close
history makes that not worth it). A slow single step logs its own time (``WarmPool.shutdown``, ``ThreadedTask.shutdown``).
"""

from __future__ import annotations

import atexit
import logging
import threading
import time

from PyQt6.QtWidgets import QApplication

from .applog import log_event

logger = logging.getLogger(__name__)


class QuitClock:
    """Logs the four marks above. One per application; ``main`` makes it before the window is built."""

    def __init__(self) -> None:
        self._quit_at: float | None = None

    def _since_quit_ms(self) -> float | None:
        return None if self._quit_at is None else round((time.perf_counter() - self._quit_at) * 1000, 1)

    def connect_start(self, app: QApplication) -> None:
        """Connect first, so that its slot runs before every other ``aboutToQuit`` slot."""
        app.aboutToQuit.connect(self._on_quit)
        atexit.register(self._on_exit)  # registered after the log system's own, so it runs before the log is closed

    def connect_end(self, app: QApplication) -> None:
        """Connect last (just before the event loop starts), so that its slot runs after the slots connected so far."""
        app.aboutToQuit.connect(lambda: log_event(logger, "app.quit_slots_done", logging.INFO, since_quit_ms=self._since_quit_ms()))

    def exec_returned(self) -> None:
        log_event(logger, "app.exec_returned", logging.INFO, since_quit_ms=self._since_quit_ms())

    def _on_quit(self) -> None:
        self._quit_at = time.perf_counter()
        log_event(logger, "app.quit", logging.INFO)

    def _on_exit(self) -> None:
        if self._quit_at is None:
            return  # the process ended without a normal quit (an error before the event loop): nothing to time
        alive = sorted(t.name for t in threading.enumerate() if t is not threading.main_thread())
        log_event(logger, "app.exit", logging.INFO, since_quit_ms=self._since_quit_ms(), threads=",".join(alive))
