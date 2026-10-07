"""The union of every ROI's sample and reference region, for the Histogram
panel's two ROI curves: cached, and computed off the GUI thread when there
are many ROIs (2026-10-07).

Why this exists: the Image panel re-renders on every ROI change **and on every
ROI selection click**, and each render makes the Histogram redraw. Rebuilding
the masks each time cost 0.9 s at 1000 ROIs (after `union_roi_masks` already
cut it from 4.2 s), on the GUI thread, even when nothing about the ROIs had
changed. So:

- A result is cached under (ROI generation, image shape, chromatic affine,
  default ring diameters). Selecting a ROI, stepping through cubes at one
  wavelength, or any cosmetic redraw is then free. The ROI generation is
  bumped by `invalidate()`, which the panel calls on every
  `RoiToolbox.geometry_changed`.
- Up to `SYNC_ROI_LIMIT` ROIs are computed inline (tens of milliseconds,
  and the curves are right the moment the redraw ends). More than that run
  on a plain `threading.Thread` (never `QThreadPool`: CLAUDE.md), on a
  snapshot of the ROIs - ROI objects are edited in place on the GUI thread,
  so the worker must not read the live ones. **Latest request wins**: a
  newer request cancels the running one (`union_roi_masks(cancelled=...)`)
  and replaces whatever was queued. `ready(key)` fires on the GUI thread when
  the masks for the most recently wanted key are available.

Not wired to the app-wide `TaskIndicator`: this is not something the user
started, and it is a sub-second-to-two-second background refresh whose result
simply appears in the plot; a progress bar and a Cancel button for it would be
noise. (The cancellation hook the rule asks for is there; progress is not.)
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from typing import Hashable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...analysis.worker import AnalysisWorker
from ...progress import Cancelled
from ...roi.model import AreaRoi
from ...roi.rasterize import union_roi_masks

logger = logging.getLogger(__name__)

RoiMasks = tuple[np.ndarray, np.ndarray]
"""``(sample, reference)`` full-image bool planes."""

SYNC_ROI_LIMIT = 40
"""At or below this many ROIs the union is built inline (about 40 ms)."""

_JOIN_SECONDS = 5.0


class RoiMaskProvider(QObject):
    ready = pyqtSignal(object)  # the request key whose masks can now be fetched with `cached(key)`
    _computed = pyqtSignal(object, object)  # (key, masks or None); emitted from the worker thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._cache: tuple[Hashable, RoiMasks] | None = None
        self._wanted_key: Hashable | None = None
        self._running_key: Hashable | None = None
        self._queued: tuple | None = None
        self._worker = AnalysisWorker()
        # Emitted from the worker thread, received on this object's (GUI) thread: Qt queues it.
        self._computed.connect(self._on_computed)

    # -- cache control --------------------------------------------------------

    def invalidate(self) -> None:
        """The ROIs changed: nothing computed so far can be reused, and a run
        in progress is computing a stale answer."""
        self._generation += 1
        self._cache = None
        self._queued = None
        self._wanted_key = None
        if self._worker.is_running():
            self._worker.cancel()

    def cached(self, key: Hashable) -> RoiMasks | None:
        return self._cache[1] if self._cache is not None and self._cache[0] == key else None

    def stop(self) -> None:
        """Cancel and wait for the worker (a thread must not emit into a Qt
        object that is being destroyed - same rule as `ImageRenderer.stop`)."""
        self._queued = None
        self._worker.cancel()
        self._worker.join(_JOIN_SECONDS)

    # -- requests ---------------------------------------------------------------

    def request(
        self,
        rois: Sequence[AreaRoi],
        image_shape: tuple[int, ...],
        affine_matrix: np.ndarray,
        default_inner_diameter_px: float,
        default_outer_diameter_px: float,
    ) -> tuple[Hashable, RoiMasks | None]:
        """``(key, masks)``. `masks` is `None` when they are still being
        computed in the background; `ready(key)` fires when they arrive."""
        shape = (int(image_shape[0]), int(image_shape[1]))
        affine = np.array(affine_matrix, dtype=np.float64)
        inner, outer = float(default_inner_diameter_px), float(default_outer_diameter_px)
        key = (self._generation, shape, affine.tobytes(), inner, outer)
        self._wanted_key = key
        hit = self.cached(key)
        if hit is not None:
            return key, hit
        if len(rois) <= SYNC_ROI_LIMIT:
            masks = union_roi_masks(
                rois, shape, affine, default_inner_diameter_px=inner, default_outer_diameter_px=outer
            )
            self._cache = (key, masks)
            return key, masks
        if key == self._running_key or (self._queued is not None and self._queued[0] == key):
            return key, None  # already on its way
        job = (key, tuple(copy.copy(roi) for roi in rois), shape, affine, inner, outer)
        if self._worker.is_running():
            self._queued = job  # replaces any older queued request: latest wins
            self._worker.cancel()
        else:
            self._start(job)
        return key, None

    # -- background job -----------------------------------------------------------

    def _start(self, job: tuple) -> None:
        key, snapshot, shape, affine, inner, outer = job
        self._worker.join(_JOIN_SECONDS)  # the previous task has emitted and is just exiting
        self._running_key = key
        cancel_event = self._worker.cancel_event

        def task() -> None:
            masks: RoiMasks | None
            try:
                masks = union_roi_masks(
                    snapshot, shape, affine,
                    default_inner_diameter_px=inner, default_outer_diameter_px=outer,
                    cancelled=cancel_event.is_set,
                )
            except Cancelled:
                masks = None
            except Exception:
                logger.exception("ROI mask union failed")
                masks = None
            self._computed.emit(key, masks)

        self._worker.submit(task)

    def _on_computed(self, key: Hashable, masks: RoiMasks | None) -> None:
        self._running_key = None
        if masks is not None and key[0] == self._generation:  # type: ignore[index]
            self._cache = (key, masks)
            if key == self._wanted_key:
                self.ready.emit(key)
        queued, self._queued = self._queued, None
        if queued is not None and queued[0][0] == self._generation:
            self._start(queued)
