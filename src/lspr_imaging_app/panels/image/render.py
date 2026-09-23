"""Off-GUI-thread image rendering for :class:`~lspr_imaging_app.panels.image.panel.ImagePanel`.

Two rules shape this file, both non-negotiable:

**Never block the GUI thread.** Loading a plane and running
``apply_preprocessing`` on it is real work (a full-image rotate/crop, and
background flattening when enabled). Doing it in a paint/signal handler
freezes the window, which is what CLAUDE.md's "long ... image processing
must run off the main thread" rule exists to prevent.

**Never use ``QThreadPool``.** AGENTS.md's zarr rule: a ``QThreadPool``
worker touching an OME-Zarr read - even indirectly, via a blocking wait -
was root-caused to a native ``STATUS_HEAP_CORRUPTION`` crash. This is a
plain ``threading.Thread``, same choice ``analysis/worker.py`` makes and
for the same reason.

**Latest request wins; older pending ones are dropped.** Dragging a
wavelength slider can queue dozens of renders a second and only the last
one is ever seen. This is the "lossy UI" half of the acquisition app's
lossless-acquisition/lossy-UI rule applied to display: skipping a stale
*display* frame loses nothing, because nothing is stored from it. Note the
contrast with ``AnalysisWorker``, which drops nothing - there, every cell
requested is a cell the user asked to have computed and kept.

Emits results as a Qt signal, so they arrive on the GUI thread: Qt queues a
cross-thread ``.emit()`` onto the receiver's own thread automatically (the
same guarantee ``analysis/worker.py``'s docstring documents).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...image_tools.background.model import BackgroundSettings
from ...image_tools.geometry.model import GeometrySettings
from ...image_tools.preprocess import apply_preprocessing, resolve_external_mask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderRequest:
    """One "show me this frame, processed this way" request.

    Carries settings *by value*, snapshotted at request time by the panel,
    rather than letting the worker call back into the modules: the worker
    runs on a background thread, and reading a module's state from there
    while the GUI thread may be mutating it is exactly the kind of race
    this architecture's "modules are only touched from the GUI thread"
    convention avoids. ``GeometryModule.settings()`` and friends already
    return defensive copies, so snapshotting is free."""

    cube_index: int
    wavelength_nm: float
    geometry: GeometrySettings
    background: BackgroundSettings
    authored_mask: np.ndarray | None
    mask_warp_affine: np.ndarray | None
    serial: int
    """Monotonic per-panel counter. The panel ignores a result whose serial
    isn't the newest it asked for - belt and braces next to the worker's own
    drop-the-stale-one logic, since a request can be superseded *after* the
    worker has already started on it."""


@dataclass(frozen=True)
class RenderResult:
    request: RenderRequest
    image: np.ndarray | None
    error: str | None = None
    """A human-readable failure, for the panel to show instead of an image.
    Errors are reported rather than raised: a missing plane (a cube short a
    wavelength) or an unreadable file is an ordinary thing to land on while
    clicking around, not a crash."""


class ImageRenderer(QObject):
    """Renders processed frames on one background thread, newest first.

    Takes ``load_plane`` as a callable rather than a ``DatasetModule``
    reference, the same convention ``AnalysisEngine`` uses - it keeps this
    class testable with a plain function and makes the one thing it reads
    from another module explicit."""

    rendered = pyqtSignal(object)  # RenderResult

    def __init__(
        self,
        load_plane: Callable[[int, float], np.ndarray],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_plane = load_plane
        self._lock = threading.Lock()
        self._pending: RenderRequest | None = None
        self._wake = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="ImageRenderer", daemon=True)
        self._thread.start()

    def submit(self, request: RenderRequest) -> None:
        """Queue `request`, replacing any request not yet started. Returns
        immediately - the result arrives later via `rendered`."""
        with self._lock:
            self._pending = request
        self._wake.set()

    def stop(self) -> None:
        """Ask the worker thread to exit. Safe to call more than once, and
        safe to never call at all - the thread is a daemon, so it does not
        keep the process alive on its own. The panel calls this on
        destruction so a closed window stops rendering into nothing."""
        self._stopping = True
        self._wake.set()

    def _take_pending(self) -> RenderRequest | None:
        with self._lock:
            request, self._pending = self._pending, None
        return request

    def _loop(self) -> None:
        while not self._stopping:
            self._wake.wait()
            self._wake.clear()
            while not self._stopping:
                request = self._take_pending()
                if request is None:
                    break
                result = self._render(request)
                # Re-check after rendering, not just before: `stop()` is
                # typically called during application shutdown, and a render
                # that was already in flight when it arrived must not emit
                # into a widget Qt is in the middle of destroying. This is
                # the same class of shutdown race behind this app's
                # documented PyQt6-sip crash-on-close.
                if self._stopping:
                    return
                self.rendered.emit(result)

    def _render(self, request: RenderRequest) -> RenderResult:
        started = time.perf_counter()
        try:
            raw = self._load_plane(request.cube_index, request.wavelength_nm)
        except (KeyError, RuntimeError, OSError) as exc:
            # KeyError: no record at this (cube, wavelength) - normal for a
            # cube short a wavelength. RuntimeError: no dataset loaded yet.
            # OSError: the file moved or is unreadable.
            return RenderResult(request=request, image=None, error=str(exc))
        load_seconds = time.perf_counter() - started

        processed_started = time.perf_counter()
        try:
            processed = apply_preprocessing(
                raw,
                request.geometry,
                request.background,
                # The same resolution analysis uses, so what is displayed and
                # what is measured cannot drift apart (see resolve_external_mask).
                external_mask=resolve_external_mask(
                    request.authored_mask, request.geometry, request.mask_warp_affine
                ),
                external_mask_processed=True,
            )
        except Exception as exc:  # noqa: BLE001 - a render must never kill the thread
            logger.exception("Image render failed for cube=%s wl=%s", request.cube_index, request.wavelength_nm)
            return RenderResult(request=request, image=None, error=str(exc))

        # One line per rendered frame, DEBUG only (AGENTS.md performance rule:
        # instrument at write time, aggregate, never log inside an inner loop).
        logger.debug(
            "ImageRenderer cube=%s wl=%s: load=%.3fs preprocess=%.3fs shape=%s",
            request.cube_index, request.wavelength_nm,
            load_seconds, time.perf_counter() - processed_started, processed.shape,
        )
        return RenderResult(request=request, image=processed)
