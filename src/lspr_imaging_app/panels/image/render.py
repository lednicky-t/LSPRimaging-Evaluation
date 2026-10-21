"""Off-GUI-thread image rendering for :class:`~lspr_imaging_app.panels.image.panel.ImagePanel`.

Two rules shape this file, both non-negotiable:

**Never block the GUI thread.** Loading a plane and running
``apply_preprocessing`` on it is real work (a full-image rotate/crop, and
background flattening when enabled). Doing it in a paint/signal handler
freezes the window, which is what CLAUDE.md's "long ... image processing
must run off the main thread" rule exists to prevent.

**Never use ``QThreadPool``.** CLAUDE.md's zarr rule: a ``QThreadPool``
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
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...applog.events import emit_timing
from ...dataset.io import use_fast_tiff_decoder
from .display_picture import FrameDisplay, prepare_frame_display
from ...image_tools.background.estimate import BackgroundWeightsCache, roi_exclusion_mask
from ...image_tools.background.model import BackgroundSettings
from ...image_tools.geometry.model import GeometrySettings
from ...image_tools.geometry.transform import spatial_output_shape
from ...image_tools.preprocess import apply_preprocessing, estimate_background_image, resolve_external_mask
from ...roi.model import AreaRoi, AreaRoiDetectionSettings

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
    rois: tuple[AreaRoi, ...]
    """Every ROI, for the background estimate's ROI exclusion - not the
    selected ones, and nothing to do with the overlay (the panel draws that
    itself, on the GUI thread). Carried here for the same reason analysis
    passes it (2026-09-23): with `flatten_background_exclude_area_rois` on,
    which is the default, an ROI's own bright spot pulling on the local
    background average is exactly what the toggle exists to prevent - and
    if the displayed image left it in while the analysed one took it out,
    the two would show different pixel values for the same frame."""
    detection: AreaRoiDetectionSettings | None
    """`RoiToolbox.detection_settings()`, for the background estimate's
    ignore-mask exclusion (`flatten_background_exclude_mask`). Same
    reasoning as `rois`; see `analysis/tasks.py`'s module docstring.

    Neither this nor `rois` has a default, deliberately: the value that
    would be the obvious default for both ("nothing to exclude") is exactly
    what silently disabled these toggles on the analysis path for a week,
    so a new call site has to say what it means rather than inherit the
    inert case by omission."""
    serial: int
    """Monotonic per-panel counter. The panel ignores a result whose serial
    isn't the newest it asked for - belt and braces next to the worker's own
    drop-the-stale-one logic, since a request can be superseded *after* the
    worker has already started on it."""
    show_background: bool = False
    """Render the background *estimate* for this frame instead of the
    processed image (the Background tab's "show background" toggle)."""
    submitted_at: float = 0.0
    """`time.perf_counter()` when `ImageRenderer.submit` took the request (set by
    the renderer; diagnostics only: it splits "queue" time in the playback stats)."""
    submitted_while_prefetching: bool = False
    """The plane this request needs was being read ahead (by a reader thread) when the request arrived, so the
    render waits for that read (diagnostics only: the Play stats' "behind a read-ahead")."""
    fast_rotation: bool = False
    """Rotate with bilinear instead of Lanczos4 (2026-10-20, "fast while moving, exact at rest"): set while the
    slideshow plays; the panel asks for the frame again, exact, when Play stops. The Histogram, Mask tools and the
    cursor readout read the frame at rest, so they always work on the exact image."""
    display_levels: tuple[float, float] | None = None
    """The fixed display range ``(black point, white point)`` from the View tab, or None = every frame at its own
    automatic range. Display only: it changes how the frame is shown, never a pixel value."""


@dataclass(frozen=True)
class RenderResult:
    request: RenderRequest
    image: np.ndarray | None
    started_at: float = 0.0  # perf_counter when the render thread began this request
    finished_at: float = 0.0  # perf_counter when it handed the result to the GUI thread
    load_seconds: float = 0.0  # reading the plane (disk, or the plane cache)
    preprocess_seconds: float = 0.0  # geometry / background on the render thread
    display: FrameDisplay | None = None
    """The frame's value range and, for a frame with NaN pixels, its ready-made picture
    (`display_picture.py`), worked out here so the GUI thread does not have to. None = the panel does it."""
    display_seconds: float = 0.0
    error: str | None = None
    """A human-readable failure, for the panel to show instead of an image.
    Errors are reported rather than raised: a missing plane (a cube short a
    wavelength) or an unreadable file is an ordinary thing to land on while
    clicking around, not a crash."""


def request_uses_ignore_mask(request: RenderRequest) -> bool:
    """Whether rendering `request` reads the resolved ignore mask at all. `apply_preprocessing` /
    `estimate_background_image` only pass it on to the background estimate, and only with the Background
    tab's "exclude mask" toggle on (and, for the processed image, flattening on). Resolving it otherwise
    cost the render thread ~10 ms of every frame while a mask was merely *shown* (testing8, 2026-10-14:
    `prep_ms` 2 -> 13) for a value nothing used."""
    background = request.background
    return bool(
        background.flatten_background_exclude_mask
        and (request.show_background or background.flatten_background_enabled)
    )


class ImageRenderer(QObject):
    """Renders processed frames on one background thread, newest first, and reads planes ahead on
    separate reader threads.

    Takes ``load_plane`` as a callable rather than a ``DatasetModule``
    reference, the same convention ``AnalysisEngine`` uses - it keeps this
    class testable with a plain function and makes the one thing it reads
    from another module explicit.

    **Read-ahead has its own threads (2026-10-14, testing7/8).** It used to run on the render thread between
    two renders. A plane read takes 20-40 ms and cannot be interrupted, so every render request that arrived
    during one waited for it (`thread_ms` ~26-29 ms of a ~57 ms Play cycle, "100 % behind a read-ahead"), and one
    thread doing read + render caps Play at ~20 frames/s. Now ``READER_THREADS`` plain threads read the planes
    `prefetch` lists; the render thread only renders (a plane it needs is in the plane cache by then) and, if
    the plane it needs is being read at that moment, waits for that read instead of decoding the file twice."""

    rendered = pyqtSignal(object)  # RenderResult

    READER_THREADS = 2
    """Parallel decoding scales to 2 threads on the maintainer's TIFFs (11.5 -> 6.6 ms per plane; 3-4 add nothing)
    and Play needs ~15-30 planes/s."""
    READER_IDLE_SECONDS = 3.0
    """A reader thread that finds nothing to read for this long exits (a new one starts on the next `prefetch`):
    no sleeping threads accumulate in an idle session or in a test process that builds many panels."""
    READ_WAIT_SECONDS = 10.0
    """The longest a render waits for a read-ahead of its own plane: a stuck read must not stop the display."""

    def __init__(
        self,
        load_plane: Callable[[int, float], np.ndarray],
        parent: QObject | None = None,
        *,
        readers: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_plane = load_plane
        self._max_readers = max(int(self.READER_THREADS if readers is None else readers), 1)
        self._lock = threading.Lock()
        self._work = threading.Condition(self._lock)  # the readers sleep on this; same lock as the fields below
        self._pending: RenderRequest | None = None
        self._wake = threading.Event()
        self._stopping = False
        # Planes to read ahead (`prefetch`), the planes being read right now (one Event each, set when the read
        # ended) and the reader threads that are alive - all guarded by `_lock`.
        self._prefetch_queue: deque[tuple[int, float]] = deque()
        self._prefetch_not_before = 0.0
        self._reading: dict[tuple[int, float], threading.Event] = {}
        self._reader_threads: list[threading.Thread] = []
        # Per-frame work that is the same for every frame of a Play (2026-10-21, see `_frame_caches`). Only the render
        # thread touches `_roi_exclusion_cache`; `BackgroundWeightsCache` has its own lock.
        self._weights_cache = BackgroundWeightsCache()
        self._roi_exclusion_cache: tuple[tuple, np.ndarray] | None = None
        self._thread = threading.Thread(target=self._loop, name="ImageRenderer", daemon=True)
        self._thread.start()

    def submit(self, request: RenderRequest) -> None:
        """Queue `request`, replacing any request not yet started. Returns
        immediately - the result arrives later via `rendered`."""
        with self._lock:
            self._pending = replace(
                request,
                submitted_at=time.perf_counter(),
                submitted_while_prefetching=(request.cube_index, request.wavelength_nm) in self._reading,
            )
        self._wake.set()

    PREFETCH_QUIET_SECONDS = 0.25
    """No read-ahead starts until this long after the latest `prefetch` call, so
    a slider drag (one call per rendered frame) never competes with its own renders."""

    def prefetch(self, keys: Sequence[tuple[int, float]], *, quiet_seconds: float | None = None) -> None:
        """Read these (cube, wavelength) planes into the plane cache on the reader
        threads, most wanted first; replaces any earlier list (reads already running
        finish). Returns immediately.

        A failed read is ignored - the renderer reports a missing plane properly if the
        user ever navigates there. Reader threads are plain `threading.Thread`s (the
        zarr rule: never `QThreadPool`), started on demand and stopped by `stop()`."""
        with self._work:
            if self._stopping:
                return
            self._prefetch_queue = deque(keys)
            quiet = self.PREFETCH_QUIET_SECONDS if quiet_seconds is None else quiet_seconds
            self._prefetch_not_before = time.monotonic() + quiet
            wanted = min(self._max_readers, len(self._prefetch_queue))
            self._reader_threads = [t for t in self._reader_threads if t.is_alive()]
            while len(self._reader_threads) < wanted:
                thread = threading.Thread(
                    target=self._reader_loop, name=f"ImageReader-{len(self._reader_threads)}", daemon=True
                )
                self._reader_threads.append(thread)
                thread.start()
            self._work.notify_all()

    def clear_prefetch(self) -> None:
        """Forget the read-ahead list (the dataset was cleared or replaced)."""
        with self._lock:
            self._prefetch_queue.clear()

    def stop(self) -> None:
        """Ask the worker threads to exit, and wait (bounded) for them to
        actually finish before returning. Safe to call more than once, and
        safe to never call at all - the threads are daemons, so they do not
        keep the process alive on its own. The panel calls this on
        destruction so a closed window stops rendering into nothing.

        **The bounded join was added 2026-10-02, after a real crash** -
        this used to just set the two flags and return immediately,
        without waiting for the thread to actually exit `_loop`. A caller
        (a test's `tearDown`, or the real app's `aboutToQuit`) could then
        let Qt's parent-child ownership cascade start destroying this
        `ImageRenderer`'s owning `ImagePanel` while the background thread
        was still mid-`_render()` - and once it finished, `self.rendered.
        emit(result)` would fire on a QObject whose C++ side might already
        be gone, a genuine use-after-free. Reproduced as a deterministic
        Windows access violation in this rewrite's own test suite (not a
        rare/probabilistic hit - every run, same test, same `_pump()` call
        site) once enough `ImagePanel` instances were being constructed and
        torn down across one test process for the timing window to be hit
        reliably. The same class of bug as this app's own documented
        PyQt6-sip crash-on-close pattern (see that memory note) - a
        background thread not actually stopped before Qt teardown begins.
        Bounded, not an unconditional `join()`, so a thread genuinely stuck
        mid-`apply_preprocessing` can't hang shutdown forever - same
        reasoning the stable app's own `closeEvent` fix for the identical
        class of bug uses. The reader threads never touch Qt (they only call
        `load_plane`), but they are joined here too, so nothing of this
        object is still running when it is destroyed."""
        with self._work:
            self._stopping = True
            readers = list(self._reader_threads)
            self._work.notify_all()
        self._wake.set()
        self._thread.join(timeout=5.0)
        for reader in readers:
            reader.join(timeout=5.0)

    def _take_pending(self) -> RenderRequest | None:
        with self._lock:
            request, self._pending = self._pending, None
        return request

    def _next_read_ahead(self) -> tuple[int, float] | None:
        """The next plane for a reader to read (marked as being read), waiting for the quiet period and for work;
        None = this reader should exit (stopping, or idle for `READER_IDLE_SECONDS`)."""
        with self._work:
            idle_since = time.monotonic()
            while True:
                if self._stopping:
                    return None
                if self._prefetch_queue:
                    idle_since = time.monotonic()
                    delay = self._prefetch_not_before - time.monotonic()
                    if delay > 0:
                        self._work.wait(delay)
                        continue
                    key = self._prefetch_queue.popleft()
                    if key in self._reading:  # another reader has this plane in hand
                        continue
                    self._reading[key] = threading.Event()
                    return key
                remaining = self.READER_IDLE_SECONDS - (time.monotonic() - idle_since)
                if remaining <= 0:
                    # Leaving is decided under the same lock `prefetch` uses to start readers, so a list added
                    # right now either finds this thread still counted or starts a new one.
                    self._reader_threads = [t for t in self._reader_threads if t is not threading.current_thread()]
                    return None
                self._work.wait(remaining)

    def _reader_loop(self) -> None:
        use_fast_tiff_decoder(True)  # this thread only: plane reads here use the native TIFF decoder (dataset/io.py)
        while True:
            key = self._next_read_ahead()
            if key is None:
                return
            try:
                self._load_plane(*key)
            except Exception as exc:  # noqa: BLE001 - read-ahead must never kill a reader thread
                logger.debug("Prefetch of cube=%s wl=%s skipped: %s", key[0], key[1], exc)
            finally:
                with self._lock:
                    done = self._reading.pop(key, None)
                if done is not None:
                    done.set()

    def _load_plane_after_read_ahead(self, cube_index: int, wavelength_nm: float) -> np.ndarray:
        """`load_plane` for a render: if a reader is reading this very plane right now, wait for it (the plane is
        then in the cache) instead of decoding the same file a second time."""
        with self._lock:
            reading = self._reading.get((cube_index, wavelength_nm))
        if reading is not None:
            reading.wait(timeout=self.READ_WAIT_SECONDS)
        return self._load_plane(cube_index, wavelength_nm)

    def _loop(self) -> None:
        use_fast_tiff_decoder(True)  # this thread only: plane reads here use the native TIFF decoder (dataset/io.py)
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
        result = self._render_inner(request, started)
        return replace(result, started_at=started, finished_at=time.perf_counter())

    def _frame_caches(self, request: RenderRequest, raw_shape: tuple[int, int]) -> dict:
        """The two per-frame results of the background flatten that do not depend on the frame's pixels, so Play
        works them out once instead of for every frame (2026-10-21, Play at 12-15 instead of 20-25 frames/s: with
        160 ROIs the flatten cost ~50 ms of every ~55 ms render, ~14 ms of it the ROI exclusion mask built by a
        Python loop over the ROIs and ~5 ms the valid-mask weights).

        * `roi_exclusion`: the ROI part of the background's exclusion mask. It depends only on the ROIs'
          centres/diameters and the processed image's shape, so it is rebuilt only when one of those changes.
        * `weights_cache`: the valid-mask half of the estimate, keyed by the mask's content (see
          `BackgroundWeightsCache`), so a changed ignore mask can never reuse a stale entry.

        Same output as without them, bit for bit (`apply_preprocessing`'s documented contract, pinned by a test);
        render thread only. Nothing is passed when flattening is off, so that path is exactly as before."""
        background = request.background
        if not background.flatten_background_enabled:
            return {}
        caches: dict = {"weights_cache": self._weights_cache}
        if background.flatten_background_exclude_area_rois and request.rois:
            shape = spatial_output_shape(raw_shape, request.geometry)
            key = (shape, tuple((roi.center_x, roi.center_y, roi.sample_diameter_px) for roi in request.rois))
            cached = self._roi_exclusion_cache
            if cached is None or cached[0] != key:
                mask = roi_exclusion_mask(shape, list(request.rois))
                mask.setflags(write=False)  # shared between frames; the flatten copies it before it adds to it
                cached = (key, mask)
                self._roi_exclusion_cache = cached
            caches["roi_exclusion"] = cached[1]
        return caches

    def _render_inner(self, request: RenderRequest, started: float) -> RenderResult:
        try:
            raw = self._load_plane_after_read_ahead(request.cube_index, request.wavelength_nm)
        except (KeyError, RuntimeError, OSError) as exc:
            # KeyError: no record at this (cube, wavelength) - normal for a
            # cube short a wavelength. RuntimeError: no dataset loaded yet.
            # OSError: the file moved or is unreadable.
            return RenderResult(request=request, image=None, error=str(exc))
        load_seconds = time.perf_counter() - started

        processed_started = time.perf_counter()
        build = estimate_background_image if request.show_background else apply_preprocessing
        # Only `apply_preprocessing` takes these; the background-estimate view is not a Play view.
        caches = {} if request.show_background else self._frame_caches(request, raw.shape[:2])
        try:
            processed = build(
                raw,
                request.geometry,
                request.background,
                # Both only feed the background estimate, and each is gated
                # on its own BackgroundSettings toggle inside - passing them
                # unconditionally changes nothing when flattening is off.
                # Same arguments analysis/tasks.py passes, so a displayed
                # pixel and an analysed pixel are the same number.
                rois=list(request.rois) or None,
                mask_settings=request.detection,
                # The same resolution analysis uses, so what is displayed and
                # what is measured cannot drift apart (see resolve_external_mask).
                external_mask=resolve_external_mask(
                    request.authored_mask, request.geometry, request.mask_warp_affine
                ) if request_uses_ignore_mask(request) else None,
                fast_rotation=request.fast_rotation,
                **caches,
            )
        except Exception as exc:  # noqa: BLE001 - a render must never kill the thread
            logger.exception("Image render failed for cube=%s wl=%s", request.cube_index, request.wavelength_nm)
            return RenderResult(request=request, image=None, error=str(exc))

        # One line per rendered frame, DEBUG only (CLAUDE.md performance rule:
        # instrument at write time, aggregate, never log inside an inner loop).
        preprocess_seconds = time.perf_counter() - processed_started
        display = None
        display_started = time.perf_counter()
        if not request.show_background:  # the background estimate is drawn with the data's levels instead
            try:
                display = prepare_frame_display(processed, request.display_levels)
            except Exception:  # noqa: BLE001 - the panel then prepares the frame itself, as before
                logger.exception("Preparing the display picture failed for cube=%s wl=%s", request.cube_index, request.wavelength_nm)
        display_seconds = time.perf_counter() - display_started
        emit_timing(
            logger, "render.frame", (load_seconds + preprocess_seconds + display_seconds) * 1000.0,
            cube=request.cube_index, wl=request.wavelength_nm,
            load_ms=round(load_seconds * 1000.0, 2), prep_ms=round(preprocess_seconds * 1000.0, 2),
            pic_ms=round(display_seconds * 1000.0, 2), prepared=bool(display is not None and display.picture is not None),
            h=int(processed.shape[0]), w=int(processed.shape[1]),
        )
        return RenderResult(
            request=request, image=processed, load_seconds=load_seconds, preprocess_seconds=preprocess_seconds,
            display=display, display_seconds=display_seconds,
        )
