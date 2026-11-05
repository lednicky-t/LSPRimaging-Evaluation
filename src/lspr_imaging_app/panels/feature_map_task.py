"""Maps every small feature of the image, off the GUI thread (the Mask tab's "Feature map" tool, 2026-11-04).

The method is `image_tools/mask/feature_map.py` (dirt, faint dirt, missing / extra nanostructure, found from the pictures alone, no
ROIs); its heavy loops run on worker processes through `image_tools/mask/feature_map_run.py`. This QObject is the thread / progress /
Cancel wrapper and the keeper of the **result**, with the signals of the other Mask-tab tasks (`task_progress`, `task_finished` for
the status bar's task indicator). It replaced the ROI-based "ROI features" task (`roi_feature_task.py`, removed 2026-11-04;
maintainer: "map all dirt, so that changing ROIs never needs the mask again; the ROI search can be ditched").

**What a run does** (``start()`` only reads the modules, in microseconds, and hands plain values to the thread):

1. per wavelength, the mean of the raw planes of ``cubes`` spectral cubes from the one on screen on (dark and ignored frames left out;
   the features are static, the mean lowers the noise), cubes with the same chromatic matrix averaged together first;
2. each mean rotated / flipped / cropped like the display (`apply_preprocessing`, **without** background flattening: that may use the
   ROIs, and the map takes the shading out itself; rotation is linear, so rotating the mean is rotating every plane), then moved onto
   the pixels of the frame on screen by the chromatic model;
3. `feature_map.prepare` on worker processes (~20 s for 26 wavelengths and 160 disks with every core but one), `feature_map.score`
   here (~2 s, a short burst like the old tool's scoring);
4. every kind's mask mapped back to **raw pixels** (`histogram_highlight_mask_to_raw`), the space `MaskModule` stores masks in.

**The result keeps every kind**, so switching a kind on or off is instant (`set_kinds`: the pixels Apply adds are the union of the
enabled kinds). **The slow part is kept** too (`Prepared`, ~25 MB): a run on the same image with other limits re-scores in ~2 s. The
keep is tied to the frame, the cubes, the geometry, the chromatic matrices and the settings `prepare` depends on.

**The margin is not a search setting** (2026-11-05): it only grows the masks of the features already found, as the last step
(`feature_map.grow_mask`). So the result keeps each kind's *ungrown* mask (``kind_cores``, in the processed picture) next to the
grown raw pixels (``kind_pixels``), and `set_margin` grows them again, **off the GUI thread** (5 kinds: ~0.15 s of dilation and ~0.17 s
of mapping to raw pixels, measured 2026-11-05 on 773 x 1287 px with a 1.4 deg rotation): a plain thread, the newest margin wins, the
old pixels stay on screen until the new ones are ready (`is_regrowing()` is true meanwhile: Apply must wait). No new run, no re-score.
The pixels are exactly those of a run made with that margin (`grow_mask` is also what `score` uses).

**A result belongs to the picture it was made from**, not to the ROIs: moving or changing ROIs keeps it. It is dropped when the
geometry or the chromatic model changes (`forget_result`, wired by the Image panel), and a run that was going on meanwhile is refused
at the end. A dataset change stops the run (`invalidate`). The finder never looks at the mask.

Threads: a plain ``threading.Thread`` (never ``QThreadPool``: the planes may come from OME-Zarr), through `ThreadedTask`.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from scipy import ndimage

from ..applog.events import log_event
from ..dataset import DatasetModule
from ..image_tools import preprocess as pp
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.chromatic.affine import invert_affine_matrix
from ..image_tools.chromatic.module import ChromaticModule
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.geometry.module import GeometryModule
from ..image_tools.mask import feature_map as fm
from ..image_tools.mask.feature_map_run import ProcessMapper
from ..progress import Cancelled, throttled
from ..threaded_task import TaskRefused, ThreadedTask

logger = logging.getLogger(__name__)

TASK_ID = "mask_features"
TASK_LABEL = "Feature map"
DEFAULT_CUBES = 12  # the lab's choice on the 2026-10-22 dataset (noise of the mean ~1 % per wavelength)

Frame = tuple[int, float]

_READ_SHARE = 0.3  # of the progress bar: reading and averaging the planes (8 s of ~28 s with every core but one)
_SCORE_SHARE = 0.08  # scoring and the raw masks at the end


@dataclass(frozen=True)
class FeatureMapResult:
    frame: Frame
    cubes: tuple[int, ...]
    kind_pixels: dict[str, np.ndarray]  # every kind's mask in raw pixels, grown by `margin_px` (bool, read-only: shared with the preview)
    counts: dict[str, int]  # features per kind
    disks: int
    settings: fm.FeatureMapSettings  # the search settings; its `grow_px` is always 0: the margin is `margin_px`
    # what `set_margin` grows again (all read-only, shared with the thread that does it):
    kind_cores: dict[str, np.ndarray] = field(default_factory=dict, repr=False)  # every kind's ungrown mask, processed picture
    valid: np.ndarray | None = field(default=None, repr=False)  # the data of that picture (growing stays inside it)
    raw_shape: tuple[int, int] = (0, 0)
    geometry: GeometrySettings | None = None
    margin_px: int = 0  # the margin `kind_pixels` were grown by


@dataclass(frozen=True)
class FeatureMapSummary:
    frame: Frame
    cubes: int
    disks: int
    counts: dict[str, int]
    pixels: int  # what Apply would add now (the enabled kinds)
    seconds: float
    kept: bool  # the slow part of an earlier run was reused


@dataclass(frozen=True)
class _Keep:
    key: tuple
    prepared: fm.Prepared
    shape: tuple[int, int]


@dataclass(frozen=True)
class _Computed:
    result: FeatureMapResult
    keep: _Keep | None
    seconds: float
    kept: bool


def grown_raw_pixels(
    cores: dict[str, np.ndarray], valid: np.ndarray, margin_px: int, raw_shape: tuple[int, int], geometry: GeometrySettings
) -> dict[str, np.ndarray]:
    """Every kind's ungrown mask (processed picture) grown by `margin_px` inside `valid` (`feature_map.grow_mask`), then mapped to
    **raw pixels**, the space `MaskModule` stores masks in. Pure (no Qt, no module): it runs on the run's thread and on the margin's."""
    out: dict[str, np.ndarray] = {}
    for kind, core in cores.items():
        grown = fm.grow_mask(core, valid, margin_px)
        raw = np.asarray(pp.histogram_highlight_mask_to_raw(grown.astype(np.float32), 0.5, 1.5, raw_shape, geometry), dtype=bool)
        raw.setflags(write=False)
        out[kind] = raw
    return out


def choose_cubes(spectral_cubes: Sequence[int], cube: int, count: int) -> tuple[int, ...]:
    """`count` spectral cubes from `cube` on (the one on screen first); fewer at the end of the dataset are made up from before it."""
    cubes = list(spectral_cubes)
    if not cubes:
        return ()
    start = cubes.index(cube) if cube in cubes else min(range(len(cubes)), key=lambda i: abs(cubes[i] - cube))
    count = max(1, min(int(count), len(cubes)))
    start = min(start, len(cubes) - count)
    return tuple(cubes[start : start + count])


def aligned_log_stack(
    load_plane: Callable[[int, float], np.ndarray],
    frames: dict[float, list[int]],
    affines: dict[tuple[int, float], np.ndarray],
    screen_affine: np.ndarray,
    preprocess: Callable[[np.ndarray], np.ndarray],
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> np.ndarray:
    """Log intensity per wavelength (W, H, W'), each the mean of its cubes' raw planes, processed, and moved onto the pixels of the
    frame on screen. ``frames[wl]`` lists the cubes to average at wavelength `wl`; ``affines[(cube, wl)]`` is that frame's chromatic
    matrix (cubes with the same matrix are averaged before processing: one rotation per group)."""
    to_screen = np.vstack([invert_affine_matrix(screen_affine), [0.0, 0.0, 1.0]])
    total = sum(len(c) for c in frames.values())
    done = 0
    layers: list[np.ndarray] = []
    grid = None
    for wl, cubes in frames.items():
        groups: dict[bytes, list[int]] = {}
        for cube in cubes:
            groups.setdefault(np.asarray(affines[(cube, wl)], dtype=np.float64).tobytes(), []).append(cube)
        weighted = None
        weight = 0
        for members in groups.values():
            raw_sum = None
            for cube in members:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                plane = np.asarray(load_plane(cube, wl), dtype=np.float64)
                raw_sum = plane if raw_sum is None else raw_sum + plane
                done += 1
                if progress is not None:
                    progress(done / max(total, 1), f"reading image {done} of {total}")
            image = np.asarray(preprocess(raw_sum / len(members)), dtype=np.float64)
            matrix = np.vstack([np.asarray(affines[(members[0], wl)], dtype=np.float64), [0.0, 0.0, 1.0]]) @ to_screen
            if not np.allclose(matrix, np.eye(3), atol=1e-9):  # the frame on screen itself needs no move
                if grid is None:
                    grid = np.indices(image.shape, dtype=np.float64)
                yy, xx = grid
                sx = matrix[0, 0] * xx + matrix[0, 1] * yy + matrix[0, 2]
                sy = matrix[1, 0] * xx + matrix[1, 1] * yy + matrix[1, 2]
                image = ndimage.map_coordinates(image, [sy, sx], order=1, mode="constant", cval=np.nan)
            weighted = image * len(members) if weighted is None else weighted + image * len(members)
            weight += len(members)
        mean = weighted / weight
        with np.errstate(divide="ignore", invalid="ignore"):
            layers.append(np.log(np.where(mean > 0, mean, np.nan)).astype(np.float32))
    return np.stack(layers)


class FeatureMapTask(ThreadedTask):
    task_id = TASK_ID
    task_label = TASK_LABEL
    thread_name = "mask-feature-map"
    crash_log = "Feature map failed"
    stopped_message = "stopped: the dataset changed"

    result_changed = pyqtSignal()  # a result was installed or dropped, or the enabled kinds / the margin changed (the preview and the buttons follow)
    regrowing_changed = pyqtSignal(bool)  # the pixels for a new margin are being made (True) / are ready or given up (False)

    # Internal: the margin's thread -> GUI thread hand-off: (the result it grew, the margin, the raw pixels or None, the error or None)
    _regrown = pyqtSignal(object, int, object, object)

    def __init__(
        self,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        is_spectral: Callable[[int, float], bool] | None = None,
        parent: QObject | None = None,
        *,
        processes: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._chromatic = chromatic
        self._dataset = dataset
        self._is_spectral = is_spectral
        self._processes = processes  # None: lspr_core.perf decides (tests pass a number)
        self._result: FeatureMapResult | None = None
        self._keep: _Keep | None = None
        self._inputs_moved = False
        self._kinds: frozenset[str] = fm.FeatureMapSettings().kinds
        self._union: tuple[tuple, np.ndarray] | None = None  # (frame, kinds) -> the union of their masks
        self._margin = fm.FeatureMapSettings().grow_px  # the margin wanted (px); the result's own is `margin_px` until its pixels follow
        self._regrow_thread: threading.Thread | None = None
        self._regrown.connect(self._on_regrown)

    def shutdown(self) -> None:
        super().shutdown()
        thread = self._regrow_thread
        if thread is not None:  # so it never emits into an object Qt is tearing down
            thread.join(timeout=5.0)

    def set_worker_processes(self, count: int | None) -> None:
        """How many worker processes a run uses: ``None`` lets ``lspr_core.perf`` decide (the app), a number is exact and ``<= 1`` runs
        the map in the task's own thread (tests: the worker-process path is pinned by ``test_lspri_feature_map_run.py``; a pool per
        run in a test file that runs next to others made it slow enough to time out)."""
        self._processes = count

    # -- the result ---------------------------------------------------------------------------------------------

    def result(self) -> FeatureMapResult | None:
        return self._result

    def kinds(self) -> frozenset[str]:
        return self._kinds

    def set_kinds(self, kinds: frozenset[str] | set[str]) -> None:
        """Which kinds Apply adds and the preview shows. Instant: every kind of the result is kept."""
        kinds = frozenset(k for k in kinds if k in fm.KINDS)
        if kinds != self._kinds:
            self._kinds = kinds
            self._union = None
            if self._result is not None:
                self.result_changed.emit()

    def margin(self) -> int:
        """The margin (px) the masks are wanted at. The result's pixels may still be at an earlier one (see `is_regrowing`)."""
        return self._margin

    def set_margin(self, px: int) -> None:
        """Grow every feature by `px` pixels (the optics blur its edge). Needs no new run: the result keeps each kind's ungrown mask, and
        the new raw pixels are made on a thread of their own (~0.3 s); `result_changed` fires when they are in, `regrowing_changed`
        around it. A newer margin that arrives meanwhile is made right after (the newest wins). The old pixels stay until then."""
        px = max(0, int(px))
        if px != self._margin:
            self._margin = px
            self._regrow_if_needed()

    def is_regrowing(self) -> bool:
        """The pixels for a new margin are being made: what `pixels_for` returns is still the earlier margin's, so Apply must wait."""
        return self._regrow_thread is not None

    def _regrow_if_needed(self) -> None:
        result = self._result
        if result is None or self._regrow_thread is not None or result.margin_px == self._margin or result.valid is None:
            return  # nothing to grow, one is going on (it looks again when it ends), or the result already has this margin
        margin = self._margin
        cores, valid, raw_shape, geometry = result.kind_cores, result.valid, result.raw_shape, result.geometry
        assert geometry is not None

        def work() -> None:
            pixels: dict[str, np.ndarray] | None = None
            error: BaseException | None = None
            try:
                pixels = grown_raw_pixels(cores, valid, margin, raw_shape, geometry)
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._regrown.emit(result, margin, pixels, error)

        self._regrow_thread = threading.Thread(target=work, name="mask-feature-map-margin", daemon=True)
        self._regrow_thread.start()
        self.regrowing_changed.emit(True)

    def _on_regrown(self, grown: object, margin: int, pixels: object, error: object) -> None:
        """GUI thread: the margin's thread is done. Install its pixels if the result is still the one it grew."""
        thread, self._regrow_thread = self._regrow_thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        if isinstance(error, BaseException):
            self._log.error("Feature map: growing the masks failed", exc_info=error)
            self.regrowing_changed.emit(False)
            self.failed.emit(f"Feature map: the margin could not be applied: {error}")
            return  # the pixels stay at the earlier margin; the next margin change tries again
        if self._result is grown and isinstance(pixels, dict):
            self._result = dataclasses.replace(grown, kind_pixels=pixels, margin_px=margin)
            self._union = None
            self.result_changed.emit()
        self._regrow_if_needed()  # the margin may have moved again meanwhile (or the result is a new one)
        if self._regrow_thread is None:
            self.regrowing_changed.emit(False)

    def pixels_for(self, frame: Frame | None) -> np.ndarray | None:
        """The raw-space pixels of the enabled kinds if the result was made for `frame`, else ``None``."""
        result = self._result
        if result is None or frame is None or result.frame != (int(frame[0]), float(frame[1])):
            return None
        key = (result.frame, self._kinds, id(result))
        if self._union is None or self._union[0] != key:
            union = np.zeros(next(iter(result.kind_pixels.values())).shape, dtype=bool)
            for kind in self._kinds:
                union |= result.kind_pixels[kind]
            union.setflags(write=False)
            self._union = (key, union)
        return self._union[1]

    def forget_result(self, *_args: object) -> None:
        """The picture the result describes changed (geometry, chromatic model): drop it, and refuse the end of a run that was started
        before the change. O(1)."""
        if self._running:
            self._inputs_moved = True
        if self._result is not None:
            self._result = None
            self._union = None
            self.result_changed.emit()

    def invalidate(self) -> None:
        super().invalidate()
        self._keep = None
        self.forget_result()

    # -- starting -----------------------------------------------------------------------------------------------

    def start(self, frame: Frame, settings: fm.FeatureMapSettings, cubes: int = DEFAULT_CUBES) -> None:
        """Map the features of `frame` (the one on screen) from the mean of `cubes` spectral cubes. Reads the modules here, on the
        calling (GUI) thread; the work runs on a thread of its own and on worker processes."""
        if self._running:
            raise RuntimeError("The feature map is already running.")
        cube, wavelength = int(frame[0]), float(frame[1])
        frame = (cube, wavelength)
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            self.failed.emit("Load a dataset first: there is no image to look at.")
            return
        chosen = choose_cubes(self._dataset.spectral_cubes(), cube, cubes)
        spectral = self._is_spectral
        frames: dict[float, list[int]] = {}
        for c in chosen:
            for wl in self._dataset.wavelengths_for_cube(c):
                if spectral is not None and not spectral(c, float(wl)):
                    continue
                frames.setdefault(float(wl), []).append(c)
        frames = dict(sorted(frames.items()))
        if len(frames) < 3:
            self.failed.emit("The feature map needs at least 3 wavelengths (it tells dirt from nanostructure by their spectrum).")
            return
        affines = {(c, wl): np.array(self._chromatic.affine_for((c, wl)), dtype=np.float64) for wl, cs in frames.items() for c in cs}
        screen_affine = np.array(self._chromatic.affine_for(frame), dtype=np.float64)
        geometry = self._geometry.settings()
        background = BackgroundSettings()  # flattening off (its default); every other field belongs to the flattening
        key = (
            frame, chosen, geometry, tuple(frames), screen_affine.tobytes(),
            tuple(sorted((k, v.tobytes()) for k, v in affines.items())), fm.stage_a_key(settings),
        )
        keep = self._keep if self._keep is not None and self._keep.key == key else None
        load_plane = self._dataset.load_plane_uncached
        processes = self._processes
        # The margin is not part of the search: the run scores with 0 and grows the masks after (`grown_raw_pixels`), the same way
        # `set_margin` does later. `settings.grow_px` is the margin wanted now.
        margin = max(0, int(settings.grow_px))
        self._margin = margin
        search = dataclasses.replace(settings, grow_px=0)
        self._inputs_moved = False
        self._begin_run()
        progress = throttled(self._emit_progress)
        cancel_event = self._cancel_event

        def cancelled() -> bool:
            return cancel_event.is_set()

        def preprocess(plane: np.ndarray) -> np.ndarray:
            return pp.apply_preprocessing(plane, geometry, background)

        def compute() -> _Computed:
            started = time.perf_counter()
            read_seconds = prepare_seconds = 0.0
            if keep is not None:
                prepared, shape = keep.prepared, keep.shape
            else:
                logs = aligned_log_stack(
                    load_plane, frames, affines, screen_affine, preprocess,
                    progress=lambda f, m: progress(_READ_SHARE * f, m), cancelled=cancelled,
                )
                read_seconds = time.perf_counter() - started
                shape = logs.shape[1:]
                share = 1.0 - _READ_SHARE - _SCORE_SHARE

                def sub(f: float, m: str) -> None:
                    progress(_READ_SHARE + share * f, m)

                with ProcessMapper(processes=processes, progress=sub, cancelled=cancelled) as mapper:
                    prepared = fm.prepare(logs, settings, mapper=mapper, progress=sub, cancelled=cancelled)
                del logs
                prepare_seconds = time.perf_counter() - started - read_seconds
            progress(1.0 - _SCORE_SHARE, "finding the features")
            score_started = time.perf_counter()
            result_map = fm.score(prepared, search)
            if cancelled():
                raise Cancelled()
            cores = dict(result_map.kind_masks)  # grow_px is 0: not grown yet
            for core in cores.values():
                core.setflags(write=False)
            kind_pixels = grown_raw_pixels(cores, prepared.valid, margin, raw_shape, geometry)
            counts = {kind: sum(f.kind == kind for f in result_map.features) for kind in fm.KINDS}
            seconds = time.perf_counter() - started
            log_event(
                logger, "mask.feature_map", logging.INFO,
                cube=cube, wavelength=wavelength, cubes=len(chosen), wavelengths=len(frames), disks=len(prepared.disks),
                features=len(result_map.features), margin_px=margin, kept=keep is not None, read_ms=round(read_seconds * 1000.0),
                prepare_ms=round(prepare_seconds * 1000.0), score_ms=round((time.perf_counter() - score_started) * 1000.0),
                total_ms=round(seconds * 1000.0), **{f"n_{k}": v for k, v in counts.items()},
            )
            result = FeatureMapResult(
                frame, chosen, kind_pixels, counts, len(prepared.disks), search,
                kind_cores=cores, valid=prepared.valid, raw_shape=tuple(raw_shape), geometry=geometry, margin_px=margin,
            )
            new_keep = keep if keep is not None else _Keep(key, prepared, shape)
            return _Computed(result, new_keep, seconds, keep is not None)

        self._start_thread(compute)

    # -- back on the GUI thread ---------------------------------------------------------------------------------

    def _failure_message(self, error: BaseException) -> str:
        if isinstance(error, fm.rf.NotEnoughRois):
            return f"Feature map: {error}"
        return f"Feature map failed: {error}"

    def _on_result(self, result: object) -> tuple[str, object]:
        if self._inputs_moved:
            raise TaskRefused(
                "The feature map was stopped: the image settings (rotation / crop, chromatic correction) changed while it ran, so "
                "nothing was kept. Run it again."
            )
        assert isinstance(result, _Computed)
        self._result = result.result
        self._keep = result.keep
        self._union = None
        pixels = self.pixels_for(result.result.frame)
        summary = FeatureMapSummary(
            frame=result.result.frame,
            cubes=len(result.result.cubes),
            disks=result.result.disks,
            counts=result.result.counts,
            pixels=0 if pixels is None else int(np.count_nonzero(pixels)),
            seconds=result.seconds,
            kept=result.kept,
        )
        self.result_changed.emit()
        self._regrow_if_needed()  # the margin was changed while the run went on: its pixels follow the new one
        return f"{sum(summary.counts.values())} features ({summary.disks} disks)", summary
