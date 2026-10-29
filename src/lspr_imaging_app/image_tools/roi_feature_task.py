"""Finds the small features on the ROIs of the image on screen, off the GUI thread (the Mask tab's "ROI features" tool, 2026-10-29).

The method is `mask/roi_features.py`, the job around it (crops, worker processes, findings) is `mask/roi_feature_run.py`; this QObject
is the thread / progress / Cancel wrapper and the keeper of the **result**, with the same signals as `MaskAutoTask` and
`TransientScanTask` (`task_progress`, `task_finished` for the status bar's task indicator).

**What a run does** (all of it off the GUI thread; ``start()`` only reads the modules, in a few microseconds, and hands plain values
to the thread):

1. load the plane of the frame on screen and process it like the display does (rotate / flip / crop, background flattening), but
   **without the mask**: the finder looks at the picture, so what it finds does not depend on what is already masked and a mask edit
   never makes a result stale;
2. cut a crop per ROI (`rasterize_*` with the frame's chromatic matrix: the ROIs sit where the display draws them);
3. fit the crops on worker processes (`fit_crops`, the 11 s step), score them against each other and extract the features
   (`find_in_crops`, 1.3 s);
4. map the mask back to **raw pixels**, the space `MaskModule` stores masks in (`histogram_highlight_mask_to_raw`, the call the
   Histogram selection uses for the same job).

The pixels are then kept here as the **result**; nothing is written to the mask by this task. Adding them is the Apply button's job
(`panels/mask_candidate_editor.py`, the editor Threshold / Local contrast use), at the scope the Persistent / Individual toggle says.

**The fit is kept** (crops and patches, ~30 MB) so that a second run on the same image with other numbers (strength, extent, largest
feature, margin: none of them touches the fit) takes the 1.3 s of step 3 and not the 11 s. The keep is tied to everything the fit
depends on (frame, geometry, background, ROI geometry, the matrix); anything that differs makes a run start from scratch.

**A result belongs to the picture it was made from.** It is dropped when the ROIs, the geometry, the background or the chromatic
model change (`forget_result`, wired by the Image panel), and a run that was going on meanwhile is refused at the end
("the ROIs changed while it ran") instead of installing pixels for a layout that is gone. A dataset change stops the run
(`invalidate`).

Threads: a plain ``threading.Thread`` (never ``QThreadPool``: the plane may come from OME-Zarr), through `ThreadedTask`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..applog.events import log_event
from ..dataset import DatasetModule
from ..progress import Cancelled, throttled
from ..roi import rasterize as rz
from ..roi.model import AreaRoi, AreaRoiDetectionSettings
from ..roi.toolbox import RoiToolbox
from ..threaded_task import TaskRefused, ThreadedTask
from . import preprocess as pp
from .background.model import BackgroundSettings
from .background.module import BackgroundModule
from .chromatic.module import ChromaticModule
from .geometry.model import GeometrySettings
from .geometry.module import GeometryModule
from .mask import roi_feature_run as run
from .mask import roi_features as rf

logger = logging.getLogger(__name__)

TASK_ID = "mask_features"
TASK_LABEL = "ROI features"

Frame = tuple[int, float]

_FIT_SHARE = 0.9  # of the progress bar: crops are quick, scoring is short, the fit is the run


@dataclass(frozen=True)
class FeatureResult:
    frame: Frame
    pixels: np.ndarray  # bool, raw pixels as `MaskModule` stores them; read-only (shared with whoever previews it)
    findings: tuple[run.RoiFinding, ...]
    rois_judged: int
    params: run.FeatureParams


@dataclass(frozen=True)
class FeatureSummary:
    frame: Frame
    rois_judged: int
    rois_with_features: int
    features: int
    left_out: tuple[int, ...]  # ROIs whose features would cover too much of the disk or the ring: not in the pixels
    pixels: int
    seconds: float
    fit_kept: bool  # the fit of an earlier run was reused


@dataclass(frozen=True)
class _FitKeep:
    key: tuple
    crops: tuple[run.RoiCrop, ...]
    patches: dict[int, rf.RoiPatch]


@dataclass(frozen=True)
class _Computed:
    result: FeatureResult
    keep: _FitKeep | None
    seconds: float
    fit_kept: bool


def _roi_signature(rois: list[AreaRoi]) -> tuple | None:
    """What of the ROIs the fit depends on, as plain numbers; ``None`` for ROIs with a free-form region (its pixels have no cheap
    identity: such a run is simply not kept)."""
    signature = []
    for roi in rois:
        if roi.sample_geometry_type == "mask" or roi.reference_geometry_type == "mask":
            return None
        signature.append(
            (
                roi.area_roi_id, roi.center_x, roi.center_y, roi.sample_diameter_px, roi.reference_inner_diameter_px,
                roi.reference_outer_diameter_px, roi.reference_geometry_type,
            )
        )
    return tuple(signature)


def crops_from_image(
    image: np.ndarray,
    rois: list[AreaRoi],
    affine: np.ndarray,
    default_inner_diameter_px: float,
    default_outer_diameter_px: float,
    cancelled: Callable[[], bool] | None = None,
) -> list[run.RoiCrop]:
    """One crop per ROI that has pixels in the image: the window around it (a copy), its sample disk and its reference ring (without
    the disk) as the display draws them (`affine` is the chromatic matrix of the frame). The nominal centre is the disk's centre of
    mass, which already includes the matrix."""
    shape = image.shape[:2]
    crops: list[run.RoiCrop] = []
    for roi in sorted(rois, key=lambda r: r.area_roi_id):
        if cancelled is not None and cancelled():
            raise Cancelled()
        sample_full = rz.rasterize_sample(roi, shape, affine)
        ys, xs = np.nonzero(sample_full)
        if xs.size == 0:
            continue
        ring_full = rz.rasterize_reference(
            roi, shape, affine, default_inner_diameter_px=default_inner_diameter_px, default_outer_diameter_px=default_outer_diameter_px
        )
        _inner, outer = rz.effective_reference_diameters(roi, default_inner_diameter_px, default_outer_diameter_px)
        radius = max(float(outer), float(roi.sample_diameter_px)) / 2.0 + run.FIT_MARGIN_PX
        half = int(np.ceil(radius)) + run.WINDOW_PAD_PX
        cx, cy = float(xs.mean()), float(ys.mean())
        y0, y1 = max(int(round(cy)) - half, 0), min(int(round(cy)) + half + 1, shape[0])
        x0, x1 = max(int(round(cx)) - half, 0), min(int(round(cx)) + half + 1, shape[1])
        crops.append(
            run.RoiCrop(
                roi_id=int(roi.area_roi_id),
                box=(y0, y1, x0, x1),
                window=np.array(image[y0:y1, x0:x1], copy=True),  # not a view: a kept crop must not hold the whole picture
                sample=sample_full[y0:y1, x0:x1].copy(),
                ring=(ring_full & ~sample_full)[y0:y1, x0:x1].copy(),
                centre_xy=(cx - x0, cy - y0),
                radius_px=radius,
            )
        )
    return crops


class RoiFeatureTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "mask-features"
    crash_log = "ROI features failed"
    stopped_message = "stopped: the dataset changed"

    result_changed = pyqtSignal()  # a result was installed or dropped (the preview and the buttons follow)

    def __init__(
        self,
        geometry: GeometryModule,
        background: BackgroundModule,
        chromatic: ChromaticModule,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._background = background
        self._chromatic = chromatic
        self._roi_toolbox = roi_toolbox
        self._dataset = dataset
        self._result: FeatureResult | None = None
        self._keep: _FitKeep | None = None
        self._inputs_moved = False  # the ROIs / geometry / background / chromatic model changed while a run was going on
        self._frame: Frame | None = None
        self._params = run.FeatureParams()

    # -- the result ---------------------------------------------------------------------------------------------

    def result(self) -> FeatureResult | None:
        return self._result

    def pixels_for(self, frame: Frame | None) -> np.ndarray | None:
        """The raw-space pixels of the result if it was made for `frame`, else ``None``."""
        result = self._result
        if result is None or frame is None or result.frame != (int(frame[0]), float(frame[1])):
            return None
        return result.pixels

    def forget_result(self, *_args: object) -> None:
        """The picture the result describes changed (ROIs, geometry, background, chromatic model): drop it, and refuse the end of a run
        that was started before the change. Cheap (called on every step of a ROI drag)."""
        if self._running:
            self._inputs_moved = True
        if self._result is not None:
            self._result = None
            self.result_changed.emit()

    def invalidate(self) -> None:
        super().invalidate()
        self._keep = None
        self.forget_result()

    # -- starting -----------------------------------------------------------------------------------------------

    def start(self, frame: Frame, params: run.FeatureParams) -> None:
        """Find the features on the ROIs of `frame` (the one on screen). Reads the modules here, on the calling (GUI) thread; the
        work runs on a thread of its own."""
        if self._running:
            raise RuntimeError("ROI features is already running.")
        cube, wavelength = int(frame[0]), float(frame[1])
        frame = (cube, wavelength)
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            self.failed.emit("Load a dataset first: there is no image to look at.")
            return
        rois = list(self._roi_toolbox.rois_at(cube))
        if len(rois) < rf.MIN_ROIS:
            self.failed.emit(
                f"ROI features needs at least {rf.MIN_ROIS} ROIs of one array to know what a ROI normally looks like; "
                f"there are {len(rois)}."
            )
            return
        geometry: GeometrySettings = self._geometry.settings()
        background: BackgroundSettings = self._background.settings()
        detection: AreaRoiDetectionSettings = self._roi_toolbox.detection_settings()
        affine = np.array(self._chromatic.affine_for(frame), dtype=np.float64)
        signature = _roi_signature(rois)
        key = None if signature is None else (
            frame, geometry, background, signature, detection.reference_inner_diameter_px, detection.reference_outer_diameter_px,
            affine.tobytes(),
        )
        keep = self._keep if key is not None and self._keep is not None and self._keep.key == key else None
        load_plane = self._dataset.load_plane_uncached
        self._params = params
        self._frame = frame
        self._inputs_moved = False
        self._begin_run()
        progress = throttled(self._emit_progress)
        cancel_event = self._cancel_event

        def cancelled() -> bool:
            return cancel_event.is_set()

        def compute() -> _Computed:
            started = time.perf_counter()
            progress(0.0, "reading the image")
            plane = load_plane(cube, wavelength)
            image = pp.apply_preprocessing(plane, geometry, background, rois=rois, mask_settings=detection, external_mask=None)
            if cancelled():
                raise Cancelled()
            fit_started = time.perf_counter()
            if keep is not None:
                crops, patches = list(keep.crops), keep.patches
            else:
                progress(0.02, "cutting out the ROIs")
                crops = crops_from_image(
                    image, rois, affine, float(detection.reference_inner_diameter_px), float(detection.reference_outer_diameter_px), cancelled
                )
                patches = run.fit_crops(
                    crops, progress=lambda fraction, message: progress(0.02 + (_FIT_SHARE - 0.02) * fraction, message), cancelled=cancelled
                )
            fit_seconds = time.perf_counter() - fit_started
            if cancelled():
                raise Cancelled()
            progress(_FIT_SHARE, "comparing the ROIs")
            find_started = time.perf_counter()
            outcome = run.find_in_crops(crops, patches, params, image.shape[:2])
            raw = pp.histogram_highlight_mask_to_raw(outcome.mask.astype(np.float32), 0.5, 1.5, raw_shape, geometry)
            raw = np.asarray(raw, dtype=bool)
            raw.setflags(write=False)
            seconds = time.perf_counter() - started
            log_event(
                logger, "mask.features", logging.INFO,
                cube=cube, wavelength=wavelength, rois=len(crops), fitted=len(patches), judged=outcome.rois_judged,
                with_features=len(outcome.findings), left_out=sum(f.left_out for f in outcome.findings), px=int(raw.sum()),
                fit_kept=keep is not None, fit_ms=round(fit_seconds * 1000.0), find_ms=round((time.perf_counter() - find_started) * 1000.0),
                total_ms=round(seconds * 1000.0),
            )
            result = FeatureResult(frame, raw, outcome.findings, outcome.rois_judged, params)
            new_keep = keep if keep is not None else (None if key is None else _FitKeep(key, tuple(crops), patches))
            return _Computed(result, new_keep, seconds, keep is not None)

        self._start_thread(compute)

    # -- back on the GUI thread ---------------------------------------------------------------------------------

    def _failure_message(self, error: BaseException) -> str:
        if isinstance(error, rf.NotEnoughRois):
            return f"ROI features: {error}"
        return f"ROI features failed: {error}"

    def _on_result(self, result: object) -> tuple[str, object]:
        if self._inputs_moved:
            raise TaskRefused(
                "ROI features was stopped: the ROIs or the image settings changed while it ran, so nothing was kept. Run it again."
            )
        assert isinstance(result, _Computed)
        self._result = result.result
        self._keep = result.keep
        findings = result.result.findings
        summary = FeatureSummary(
            frame=result.result.frame,
            rois_judged=result.result.rois_judged,
            rois_with_features=len(findings),
            features=sum(f.features for f in findings),
            left_out=tuple(f.roi_id for f in findings if f.left_out),
            pixels=int(np.count_nonzero(result.result.pixels)),
            seconds=result.seconds,
            fit_kept=result.fit_kept,
        )
        self.result_changed.emit()
        return f"{summary.features} features on {summary.rois_with_features} of {summary.rois_judged} ROIs", summary
