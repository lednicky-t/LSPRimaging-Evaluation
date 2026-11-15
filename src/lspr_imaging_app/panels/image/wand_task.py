"""Runs the magic wand for a click on the image (2026-11-14): the computation off the GUI thread, the result into the
shared area selection.

**What the wand looks at**: the image on screen (`ImagePanel.image_rendered`: after rotation / flip / crop and, when it
is on, background flattening), because the selection is in displayed coordinates and the user clicks what they see.
Averaging several wavelengths or cubes first is not offered: without the chromatic correction the planes do not line up
(each wavelength is shifted), so a plain mean would blur the edges the wand looks for.

**Thread**: a plain ``threading.Thread`` (the image may come from an OME-Zarr read; `QThreadPool` is never used near
the dataset, see ``CLAUDE.md``). scipy's filters release the interpreter lock. ``prepare_image`` (smoothing, noise,
background, gradient: 0.1-1 s on a 1.2 MP plane) is cached for the image on screen and the settings it depends on, so a
second click on the same image costs only the region itself (20-60 ms). At most one computation runs; a click made
meanwhile waits in a single slot (a newer click replaces it).

**Stale results**: a result is dropped, with a message, if the image size changed while it was computed (the selection
is in that image's pixels). A result for the same size but an older frame is still used: the user clicked on what was
on screen then.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...applog.events import log_event
from ...image_tools.selection_wand import PreparedImage, WandResult, WandSettings, prepare_image, wand_region
from ...selection import AreaSelectionModule

logger = logging.getLogger(__name__)


class WandTask(QObject):
    busy_changed = pyqtSignal(bool)
    # Internal: worker thread -> GUI thread. result, how, image shape, seconds, prepared (or None on failure).
    _done = pyqtSignal(object, str, object, float, object)

    def __init__(
        self,
        selection: AreaSelectionModule,
        settings: Callable[[], WandSettings],
        status: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._selection = selection
        self._settings = settings
        self._status = status
        self._image: np.ndarray | None = None
        self._prepared: tuple[int, PreparedImage] | None = None  # (id of the image it was made from, result)
        self._running = False
        self._pending: tuple[int, int, str] | None = None
        self._done.connect(self._on_done)

    # -- the image on screen ------------------------------------------------------------------------

    def set_image(self, image: np.ndarray | None) -> None:
        """The image now on screen (shared, never modified here)."""
        if image is not None and image.ndim != 2:
            image = None
        if image is not self._image:
            self._image = image
            self._prepared = None

    # -- clicks ---------------------------------------------------------------------------------------

    def is_busy(self) -> bool:
        return self._running

    def click(self, x: float, y: float, how: str) -> None:
        """A click at view point (`x`, `y`) with combine mode `how` (`area_selection_module.REPLACE` / `ADD` /
        `SUBTRACT`)."""
        if self._image is None:
            self._status("Wand: no image on screen.")
            return
        col, row = int(np.floor(x)), int(np.floor(y))
        if not (0 <= row < self._image.shape[0] and 0 <= col < self._image.shape[1]):
            return  # off the image: not a click on anything
        if self._running:
            self._pending = (row, col, how)
            return
        self._start(row, col, how)

    def _start(self, row: int, col: int, how: str) -> None:
        image = self._image
        assert image is not None
        settings = self._settings()
        cached = self._prepared
        prepared = cached[1] if cached is not None and cached[0] == id(image) and cached[1].key == settings.prepare_key() else None
        self._running = True
        self.busy_changed.emit(True)
        if prepared is None:
            self._status("Wand: looking at the image...")

        def work() -> None:
            started = time.perf_counter()
            made = prepared
            try:
                if made is None:
                    made = prepare_image(image, settings)
                result = wand_region(image, row, col, settings, prepared=made)
            except Exception as exc:  # reported, never lost
                logger.exception("wand failed")
                result = WandResult(None, f"Wand failed: {exc}")
                made = None
            self._done.emit(result, how, image.shape, time.perf_counter() - started, (id(image), made) if made else None)

        threading.Thread(target=work, name="selection-wand", daemon=True).start()

    def _on_done(self, result: WandResult, how: str, shape: tuple[int, int], seconds: float, prepared: object) -> None:
        self._running = False
        if prepared is not None and self._image is not None and prepared[0] == id(self._image):
            self._prepared = prepared  # type: ignore[assignment]
        log_event(
            logger, "wand.click", logging.DEBUG, method=self._settings().method.value, how=how,
            area_px=result.area_px, ms=round(seconds * 1000.0, 1),
        )
        if result.region is None:
            self._status(result.message)
        elif self._image is None or tuple(self._image.shape) != tuple(shape):
            self._status("Wand: the image size changed while it was working; click again.")
        else:
            self._selection.combine_region(result.region, how)
            verb = {"add": "added", "subtract": "took away"}.get(how, "selected")
            self._status(f"{result.message.split(':')[0]}: {verb} {result.area_px:,} px.")
        pending, self._pending = self._pending, None
        if pending is not None and self._image is not None:
            self._start(*pending)
        else:
            self.busy_changed.emit(False)
