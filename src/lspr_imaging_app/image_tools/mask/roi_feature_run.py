"""The ROI feature finder as one job: crops -> fits on worker processes -> findings and a mask (2026-10-29).

`roi_features.py` is the method (pure maths, one ROI or the whole array at a time). This is how it is run for the Mask tab's
"ROI features" tool, still without Qt and without any module, so the task (`image_tools/roi_feature_task.py`), a script and a test
call it alike:

1. The caller cuts a **crop** per ROI out of the processed image (`RoiCrop`: the pixels around the ROI, its sample disk and its
   reference ring). Rasterizing a ROI needs `roi/`, which `mask/` must not import, so that step is the caller's.
2. ``fit_crops`` fits every crop (the expensive step, 69 ms per ROI, 11 s for 160 ROIs on the development laptop) on **worker
   processes**. A process and not a thread because the fit is a few dozen small numpy calls, each of which needs the interpreter
   lock: a worker thread doing that for seconds starves the window of it (measured for the Sensorgram fits, 2026-10-24,
   `analysis/trace_pool.py`). The number of workers comes from ``lspr_core.perf`` like every fan-out of this app.
3. ``find_in_crops`` scores all ROIs against each other and extracts the features (1.3 s for 160 ROIs; run where the caller likes,
   the task runs it in its own worker thread).

**Tuning changes scheduling, never results.** A crop is fitted by exactly one task whatever the worker count and the fits do not
depend on each other, so the patches (and the mask) are identical at 1, 2 or 7 workers and in any completion order; the patches
are sorted by ROI id before they are compared with each other. Pinned by ``tests/unit/test_lspri_roi_feature_run.py``.

Record and real-data results: ``docs/roi_feature_detection_2026-10-29.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass

import numpy as np
from lspr_core.perf import get_runtime, lower_process_priority

from ...applog.events import log_event
from ...progress import Cancelled
from . import roi_features as rf

logger = logging.getLogger(__name__)

FIT_MARGIN_PX = 6.0  # the radial model reaches this far beyond the reference ring's outer edge
WINDOW_PAD_PX = 6  # a crop reaches this far beyond the radius of the model
_ROIS_PER_TASK = 4  # 4 x 69 ms: long enough that messages are no overhead, short enough that Cancel and the tuner react quickly
_WORKER_BYTES = 300 * 1024 * 1024  # memory one worker may need, for the automatic limit: measured 93 MB (numpy + scipy 92 MB, the fits add ~1 MB), kept at three times that
TASK_KEY = "mask.roi_features"


@dataclass(frozen=True)
class RoiCrop:
    """One ROI cut out of the processed image. Arrays share the window's shape."""

    roi_id: int
    box: tuple[int, int, int, int]  # y0, y1, x0, x1 of the window in the processed image
    window: np.ndarray  # the pixels; NaN where the pixel has no value
    sample: np.ndarray  # bool: the sample disk
    ring: np.ndarray  # bool: the reference ring without the sample disk
    centre_xy: tuple[float, float]  # nominal centre, in window pixels
    radius_px: float  # how far from the centre the radial model is fitted


@dataclass(frozen=True)
class FeatureParams:
    """What the user can set (the popup of the tool). ``strength`` and ``extent`` are scores against the other ROIs of the array,
    roughly standard-normal where nothing is wrong (so 5 is a pixel that stands out clearly)."""

    strength: float = 5.0  # a feature must reach this score in at least two pixels
    extent: float = 3.0  # and spreads over the connected pixels at or above this score
    max_width_px: float = 20.0  # wider than this is not "small" (the sample disk is ~34 px across)
    margin_px: int = 1  # the mask is the feature grown by this much
    max_share: float = 0.4  # a ROI whose features would cover more than this share of its sample disk or ring is left out

    def feature_settings(self) -> rf.FeatureSettings:
        return rf.FeatureSettings(
            z_seed=float(self.strength),
            z_grow=min(float(self.extent), float(self.strength)),  # the seed is part of what it grows from
            max_width_px=float(self.max_width_px),
            grow_px=int(self.margin_px),
        )


@dataclass(frozen=True)
class RoiFinding:
    """One ROI that has at least one feature (a row of the table)."""

    roi_id: int
    features: int
    dark: int
    bright: int
    area_px: int  # the features' own pixels
    masked_px: int  # the mask they make (grown by the margin)
    peak_z: float
    effect: float  # first-order effect of the features on the sample / reference ratio, as a fraction of it
    sample_share: float  # share of the sample disk the mask covers
    ring_share: float
    left_out: bool  # more than ``max_share`` of the disk or the ring: not in the mask (too little would be left to measure with)


@dataclass(frozen=True)
class FeatureOutcome:
    findings: tuple[RoiFinding, ...]  # ROIs with at least one feature, by id
    mask: np.ndarray  # bool, the processed image's shape: the features of every ROI that is not left out
    rois_judged: int  # ROIs that had enough pixels to be compared


# -- the expensive step on worker processes -------------------------------------------------------------------------------------


def _fit_one(job: tuple[int, np.ndarray, tuple[float, float], float]) -> tuple[int, rf.RoiPatch | None]:
    roi_id, window, centre_xy, radius_px = job
    valid = np.isfinite(window)
    return roi_id, rf.fit_patch(np.where(valid, window, 0.0), valid, centre_xy, radius_px)


def _fit_task(jobs: list[tuple[int, np.ndarray, tuple[float, float], float]]) -> list[tuple[int, rf.RoiPatch | None]]:
    return [_fit_one(job) for job in jobs]


def _init_process(low_priority: bool) -> None:
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)


def fit_crops(
    crops: Sequence[RoiCrop],
    *,
    processes: int | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[int, rf.RoiPatch]:
    """Step 1 of the method for every crop; a crop with too little data is left out. ``processes=None`` lets ``lspr_core.perf`` choose
    the number of worker processes (and tune it when the user asked for that); a number is exact and ``<= 1`` runs in this process.
    ``progress(fraction, message)`` after each finished task; ``cancelled()`` is looked at about every 100 ms and on True the tasks not
    yet started are dropped and `Cancelled` is raised. The result is sorted by ROI id."""
    jobs = [(c.roi_id, c.window, c.centre_xy, c.radius_px) for c in crops]
    total = len(jobs)
    fitted: dict[int, rf.RoiPatch] = {}
    if total == 0:
        return fitted
    if processes is not None and processes <= 1:
        for done, job in enumerate(jobs, start=1):
            if cancelled is not None and cancelled():
                raise Cancelled()
            roi_id, patch = _fit_one(job)
            if patch is not None:
                fitted[roi_id] = patch
            if progress is not None:
                progress(done / total, f"fitting ROI {done} of {total}")
        return dict(sorted(fitted.items()))
    tasks = [jobs[i : i + _ROIS_PER_TASK] for i in range(0, total, _ROIS_PER_TASK)]
    run = None if processes is not None else get_runtime().begin(TASK_KEY, total_units=len(tasks), per_worker_bytes=_WORKER_BYTES, max_useful=len(tasks))
    pool_size = max(int(processes), 1) if run is None else run.maximum
    in_flight = (lambda: pool_size) if run is None else (lambda: run.workers)
    low_priority = True if run is None else run.low_priority
    pool = ProcessPoolExecutor(max_workers=pool_size, initializer=_init_process, initargs=(low_priority,))
    done_rois = 0
    try:
        queue = iter(tasks)
        pending: set = set()

        def top_up() -> None:
            while len(pending) < in_flight():
                chunk = next(queue, None)
                if chunk is None:
                    return
                pending.add(pool.submit(_fit_task, chunk))

        try:
            top_up()
            while pending:
                finished, still = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                pending.clear()
                pending.update(still)
                for future in finished:
                    for roi_id, patch in future.result():  # an error inside a fit is raised here, to the caller
                        done_rois += 1
                        if patch is not None:
                            fitted[roi_id] = patch
                    if run is not None:
                        run.unit_done(1)
                    if progress is not None:
                        progress(done_rois / total, f"fitting ROI {done_rois} of {total}")
                if cancelled is not None and cancelled():
                    raise Cancelled()
                top_up()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    finally:
        pool.shutdown(wait=True)  # the workers still running a task finish it (a fraction of a second), then close
    if run is not None:
        log_event(logger, "perf.run", logging.INFO, **run.finish().as_fields())
    return dict(sorted(fitted.items()))


# -- the cheap step ---------------------------------------------------------------------------------------------------------------


def find_in_crops(
    crops: Sequence[RoiCrop],
    patches: dict[int, rf.RoiPatch],
    params: FeatureParams,
    shape: tuple[int, int],
) -> FeatureOutcome:
    """Steps 2 and 3 for the fitted ROIs: score them against each other, extract the features, and build the mask of the processed
    image (shape `shape`). Raises `roi_features.NotEnoughRois` when fewer than ``MIN_ROIS`` ROIs were fitted."""
    by_id = {c.roi_id: c for c in crops}
    ordered = {roi_id: patches[roi_id] for roi_id in sorted(patches)}
    regions = {roi_id: by_id[roi_id].sample | by_id[roi_id].ring for roi_id in ordered}
    found = rf.detect(ordered, regions, params.feature_settings())
    mask = np.zeros(shape, dtype=bool)
    findings: list[RoiFinding] = []
    for roi_id, (score, features) in found.items():
        if not features.features:
            continue
        crop = by_id[roi_id]
        effect_sample, effect_ring = rf.ratio_effect(crop.window, score.deviation, crop.sample, crop.ring, features.mask)
        sample_share = rf.masked_share(features.mask, crop.sample)
        ring_share = rf.masked_share(features.mask, crop.ring)
        left_out = max(sample_share, ring_share) > params.max_share
        if not left_out:
            y0, y1, x0, x1 = crop.box
            mask[y0:y1, x0:x1] |= features.mask
        findings.append(
            RoiFinding(
                roi_id=roi_id,
                features=len(features.features),
                dark=sum(f.sign < 0 for f in features.features),
                bright=sum(f.sign > 0 for f in features.features),
                area_px=sum(f.area_px for f in features.features),
                masked_px=int(features.mask.sum()),
                peak_z=max(f.peak_z for f in features.features),
                effect=float(effect_sample - effect_ring),
                sample_share=sample_share,
                ring_share=ring_share,
                left_out=left_out,
            )
        )
    return FeatureOutcome(tuple(findings), mask, rois_judged=len(ordered))
