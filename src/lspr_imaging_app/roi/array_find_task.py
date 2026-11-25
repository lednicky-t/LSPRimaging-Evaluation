"""Runs the array finder (`array_finder`) off the GUI thread: the slow search and the quick build.

Two jobs (the shape of the old Array group's runner, retired 2026-11-25) because the finder has two parts:

- `search(...)`: load the reference-wavelength image of the cube on screen (processed space), find the candidates (`find_candidates`: ~0.1 s with a ROI, ~1 s
  without). Reports progress (`task_progress`, joined to the status-bar `TaskIndicator` by the Image panel's owner), can be cancelled, and ends with exactly one
  `task_finished`.
- `build(...)`: turn cached candidates into an array (`build_array`: ~0.1 - 0.3 s). Run on every slider move, so a request that arrives while one is running
  waits and only the **newest** is run next; results carry the serial of the request so a late one is recognised and dropped.

A plain `threading.Thread`, never `QThreadPool`: the plane is an OME-Zarr read (the zarr rule). Everything a thread needs is snapshotted by value when it starts;
every result reaches the GUI thread through a queued signal.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import apply_preprocessing, resolve_external_mask
from ..progress import Cancelled
from .array_finder import Candidates, FinderError, FinderSeed, FinderSettings, build_array, find_candidates

logger = logging.getLogger(__name__)

TASK_ID = "roi_array_find"
TASK_LABEL = "Find array"


class ArrayFindTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    task_finished = pyqtSignal(str, str, str)  # task_id, "completed" | "failed" | "cancelled", message
    searched = pyqtSignal(object, object, str)  # key, `Candidates` or None, error text ("" when it worked)
    built = pyqtSignal(int, object, str)  # serial, `FinderResult` or None, error text
    running_changed = pyqtSignal(bool)

    _search_done = pyqtSignal(object, object, object)  # thread -> GUI thread: (key, candidates, error)
    _build_done = pyqtSignal(int, object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._search_thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._build_thread: threading.Thread | None = None
        self._pending_build: tuple[int, Candidates, FinderSettings] | None = None
        self._search_done.connect(self._on_search_done)
        self._build_done.connect(self._on_build_done)

    def is_searching(self) -> bool:
        return self._search_thread is not None

    def search(
        self,
        key: object,
        seeds: tuple[FinderSeed, ...],
        settings: FinderSettings,
        *,
        cube_index: int,
        wavelength_nm: float,
        geometry: GeometrySettings,
        background: BackgroundSettings,
        load_plane: Callable[[int, float], np.ndarray],
        authored_mask: np.ndarray | None = None,
        mask_warp_affine: np.ndarray | None = None,
    ) -> None:
        """Start a search; one already running is cancelled first (its result is dropped, it reports ``cancelled``). ``authored_mask`` / ``mask_warp_affine``: the user's mask
        as stored (raw pixels; `MaskModule.resolve_mask_source`) and the chromatic matrix that brings it to this frame (``None`` = none needed); used when ``settings.use_mask``."""
        if self._search_thread is not None:
            self.cancel()
            self._search_thread.join(timeout=5.0)
            self._finish_search_state()
        self._cancel = threading.Event()
        cancel = self._cancel

        def report(fraction: float, message: str) -> None:
            self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message)

        def work() -> None:
            candidates: Candidates | None = None
            error: BaseException | None = None
            try:
                report(0.0, "Find: loading the image...")
                image = apply_preprocessing(load_plane(int(cube_index), float(wavelength_nm)), geometry, background)
                external = resolve_external_mask(authored_mask, geometry, mask_warp_affine) if settings.use_mask else None  # (True = masked)
                valid = None if external is None else ~external
                candidates = find_candidates(image, seeds, settings, valid_mask=valid, progress=report, cancelled=cancel.is_set)
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._search_done.emit(key, candidates, error)

        self._search_thread = threading.Thread(target=work, name="roi-array-find", daemon=True)
        self.running_changed.emit(True)
        self._search_thread.start()

    def build(self, serial: int, candidates: Candidates, settings: FinderSettings) -> None:
        """Make the array of ``candidates`` under ``settings``; the newest request wins."""
        self._pending_build = (serial, candidates, settings)
        if self._build_thread is None:
            self._start_pending_build()

    def cancel(self) -> None:
        self._cancel.set()
        self._pending_build = None

    def shutdown(self) -> None:
        """The application is quitting: stop the threads and wait briefly, so they never emit into a widget Qt is tearing down."""
        self.cancel()
        for thread in (self._search_thread, self._build_thread):
            if thread is not None:
                thread.join(timeout=5.0)

    # -- internals ----------------------------------------------------------------------------

    def _start_pending_build(self) -> None:
        pending, self._pending_build = self._pending_build, None
        if pending is None:
            return
        serial, candidates, settings = pending

        def work() -> None:
            result = None
            error: BaseException | None = None
            try:
                result = build_array(candidates, settings)
            except BaseException as exc:  # noqa: BLE001
                error = exc
            self._build_done.emit(serial, result, error)

        self._build_thread = threading.Thread(target=work, name="roi-array-find-build", daemon=True)
        self._build_thread.start()

    def _finish_search_state(self) -> None:
        thread, self._search_thread = self._search_thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self.running_changed.emit(False)

    def _on_search_done(self, key: object, candidates: object, error: object) -> None:
        self._finish_search_state()
        if isinstance(error, Cancelled):
            self.task_finished.emit(TASK_ID, "cancelled", "")
            return
        if isinstance(error, FinderError):
            self.task_finished.emit(TASK_ID, "failed", str(error))
            self.searched.emit(key, None, str(error))
            return
        if error is not None:
            logger.error("Array finder search crashed", exc_info=error)
            self.task_finished.emit(TASK_ID, "failed", f"Unexpected error: {error}")
            self.searched.emit(key, None, f"Unexpected error: {error}")
            return
        assert isinstance(candidates, Candidates)
        self.task_finished.emit(TASK_ID, "completed", f"{len(candidates.points)} peaks")
        self.searched.emit(key, candidates, "")

    def _on_build_done(self, serial: int, result: object, error: object) -> None:
        thread, self._build_thread = self._build_thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        if self._pending_build is not None:
            self._start_pending_build()  # a newer request arrived meanwhile: this answer is already out of date, but cheap to show; the next follows
        if isinstance(error, FinderError):
            self.built.emit(serial, None, str(error))
        elif error is not None:
            logger.error("Array finder build crashed", exc_info=error)
            self.built.emit(serial, None, f"Unexpected error: {error}")
        else:
            self.built.emit(serial, result, "")
