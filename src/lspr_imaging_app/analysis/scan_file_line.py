"""The Workflow panel's one line about the saved scan file, read off the GUI thread (2026-11-28).

**Why.** `scan_store.describe_file` opens `scan.h5` with h5py. The launch logs of 2026-11-28 (`gui.stall` records) showed the GUI thread
stuck in it for 2.7-4.3 s: the scan-results thread was reading the same file at that moment (`analysis.scan_loaded`, 4 s), and h5py has
**one lock for the whole library**, so a second reader has to wait for the first, even for 3 MB. The rule is that file reads are not done
on the GUI thread (`CLAUDE.md`, "Long work off the GUI thread"); this was the one that had been left.

**How.** `show(path)` starts a plain `threading.Thread` (HDF5 only, no OME-Zarr read, so the `QThreadPool` rule does not apply) and the
line is put into the status on the GUI thread when it is in. The newest request wins: a line for a path that is no longer the current
scan file (another session was opened meanwhile) is dropped. Until the line is in, the status reads "Reading the scan file...". A path with no file
says "No scan yet." at once.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .scan_store import describe_file

logger = logging.getLogger(__name__)

NO_SCAN_TEXT = "No scan yet."
READING_TEXT = "Reading the scan file..."


class ScanFileLine(QObject):
    _done = pyqtSignal(int, object, object)  # request number, path, line | None: thread -> GUI thread hand-off

    def __init__(
        self,
        set_status: Callable[[str], None],
        current_path: Callable[[], Path | None],
        parent: QObject | None = None,
        describe: Callable[[Path], str | None] = describe_file,
    ) -> None:
        super().__init__(parent)
        self._set_status = set_status
        self._current_path = current_path
        self._describe = describe
        self._request = 0
        self._done.connect(self._on_done)

    def show(self, path: Path | None) -> None:
        """Put the line about `path` into the status (GUI thread). No path: "No scan yet." at once; otherwise the file is read on a thread."""
        self._request += 1
        if path is None or not path.exists():  # nothing to read (a stat, not an HDF5 open): no need to wait for a thread
            self._set_status(NO_SCAN_TEXT)
            return
        self._set_status(READING_TEXT)
        number = self._request

        def read() -> None:
            try:
                line = self._describe(path)
            except Exception:  # nothing may escape a thread silently; the line is only a convenience
                logger.exception("Reading the scan file's description failed")
                line = None
            self._done.emit(number, path, line)

        threading.Thread(target=read, name="scan-file-line", daemon=True).start()

    def _on_done(self, number: int, path: object, line: object) -> None:
        if number != self._request or path != self._current_path():
            return  # a newer request exists, or the session changed while the file was being read
        self._set_status(line if isinstance(line, str) and line else NO_SCAN_TEXT)
