"""Lab for analysis step 1 (pixels -> reduced numbers): can the whole dataset be scanned in well under 10 minutes?

Read-only (dataset and session are never written). Prototype of the planned reducer, kept here until it is proven and moved
into `analysis/` (docs/analysis_redesign_plan_2026-10-19.md). Four sections:

  profile   where the time of today's per-ROI patch path goes (cProfile, self time)
  verify    the vectorised "find every ROI's pixels in one pass" gives the SAME pixels, counts and values as today's
            full-frame masks, for all four reduction methods; and the cached-ROI-exclusion frame preparation gives the
            same processed image as `apply_preprocessing`
  bench     per-frame stage times of the new path
  scan      the whole step end to end with worker threads (native TIFF decoder): wall time, frames/s, extrapolation, and
            a spot check of the scanned numbers against today's code

    python tools/frame_reduce_lab.py --tiff "D:/run/images" --session "D:/run/images/sessions/2026-09-27_143549"
    python tools/frame_reduce_lab.py ... --sections scan --scan-cubes 30 --threads 4
    python tools/frame_reduce_lab.py ... --sections scan --scan-all --threads 4 --save out.npz
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import analysis_stage_timing as st  # noqa: E402  (also puts the app sources on sys.path)
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from scipy import ndimage  # noqa: E402

from lspr_imaging_app.analysis.reduction import REDUCTION_METHODS, reduce_sample_and_reference  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools import preprocess as pp  # noqa: E402
from lspr_imaging_app.image_tools.background import estimate as bg_estimate  # noqa: E402
from lspr_imaging_app.image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix  # noqa: E402
from lspr_imaging_app.roi import rasterize as rz  # noqa: E402
from lspr_imaging_app.roi.detection import ignored_pixel_mask  # noqa: E402

EXCLUDE_ALL = "exclude_all_sample_rois"
_QT_APP = None


# -- ROI geometry as arrays ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoiSet:
    """The circle / annulus geometry of a list of ROIs as plain arrays, in the radii the existing rasteriser uses."""

    rois: tuple
    cx: np.ndarray
    cy: np.ndarray
    sample_r: np.ndarray  # outer radius of the sample disk
    ring_in_r: np.ndarray
    ring_out_r: np.ndarray
    sample_on: np.ndarray  # False: empty sample (radius 0)
    ring_on: np.ndarray  # False: no reference ring ("none" or outer diameter 0)

    @property
    def n(self) -> int:
        return len(self.rois)


def build_roi_set(rois, inner_default: float, outer_default: float) -> RoiSet:
    n = len(rois)
    cx = np.array([float(r.center_x) for r in rois], dtype=np.float64)
    cy = np.array([float(r.center_y) for r in rois], dtype=np.float64)
    sample_r = np.zeros(n)
    ring_in = np.zeros(n)
    ring_out = np.zeros(n)
    ring_on = np.zeros(n, dtype=bool)
    for i, roi in enumerate(rois):
        if (roi.sample_geometry_type == "mask" and roi.sample_mask is not None) or (
            roi.reference_geometry_type == "mask" and roi.reference_mask is not None
        ):
            raise NotImplementedError(f"ROI {roi.area_roi_id} has a free-form mask; the lab handles circle/annulus only")
        sample_r[i] = max(float(roi.sample_diameter_px) / 2.0, 0.0)
        if roi.reference_geometry_type != "none":
            inner_d, outer_d = rz.effective_reference_diameters(roi, inner_default, outer_default)
            if outer_d > 0.0:
                inner_r = max(inner_d, 0.0) / 2.0
                ring_in[i] = inner_r
                ring_out[i] = max(outer_d / 2.0, inner_r)
                ring_on[i] = True
    return RoiSet(tuple(rois), cx, cy, sample_r, ring_in, ring_out, sample_r > 0.0, ring_on)


# -- finding every ROI's pixels in one pass -------------------------------------------------------------------


def locate_pixels(rs: RoiSet, affine: np.ndarray, shape: tuple[int, int], chunk: int = 16):
    """Flat pixel indices (row-major, into an HxW image) of every ROI's sample disk and reference ring, as
    (roi_index, flat_index) pairs sorted by ROI and, within a ROI, by pixel. Same test as
    `rasterize._annulus_mask_in_box`: map the pixel back through the inverse affine and compare squared distance from the
    ROI centre with the radii (inclusive at both ends). One uniform window per ROI, so the arithmetic is a few array passes
    for all ROIs together instead of a Python call per ROI."""
    height, width = shape
    inv = invert_affine_matrix(affine)
    linear = np.asarray(affine[:, :2], dtype=np.float64)
    max_scale = max(float(np.max(np.linalg.svd(linear, compute_uv=False))), 1e-6)
    centres = apply_affine_to_points(np.column_stack((rs.cx, rs.cy)), affine)
    # The region of a ROI lies within (largest stretch of the affine) x (outer radius) of its transformed centre, so +1 px
    # is a safe margin (the existing code adds 3). A wider window never changes which pixels are members.
    reach = float(max(rs.sample_r.max(), rs.ring_out_r.max())) * max_scale + 1.0
    x0 = np.floor(centres[:, 0] - reach).astype(np.int64)
    y0 = np.floor(centres[:, 1] - reach).astype(np.int64)
    side = int(np.ceil(2.0 * reach)) + 2
    step = np.arange(side, dtype=np.float64)
    a, b, c = (float(v) for v in inv[0])
    d, e, f = (float(v) for v in inv[1])
    cells = side * side
    s_roi, s_flat, r_roi, r_flat = [], [], [], []
    s_rad2 = np.where(rs.sample_on, rs.sample_r * rs.sample_r, -1.0)  # -1: no pixel can match (squared distance >= 0)
    r_out2 = np.where(rs.ring_on, rs.ring_out_r * rs.ring_out_r, -1.0)
    r_in2 = rs.ring_in_r * rs.ring_in_r
    for start in range(0, rs.n, chunk):
        sl = slice(start, min(start + chunk, rs.n))
        xf = x0[sl].astype(np.float64)[:, None, None] + step[None, None, :]  # (m, 1, side)
        yf = y0[sl].astype(np.float64)[:, None, None] + step[None, :, None]  # (m, side, 1)
        sx = xf * a + yf * b
        sx += c
        sy = xf * d + yf * e
        sy += f
        sx -= rs.cx[sl][:, None, None]
        sy -= rs.cy[sl][:, None, None]
        sx *= sx
        sy *= sy
        sx += sy  # squared distance from the ROI centre in source space
        in_s = sx <= s_rad2[sl][:, None, None]
        in_r = (sx <= r_out2[sl][:, None, None]) & (sx >= r_in2[sl][:, None, None])
        if x0[sl].min() < 0 or y0[sl].min() < 0 or x0[sl].max() + side > width or y0[sl].max() + side > height:
            inside = ((xf >= 0) & (xf < width)) & ((yf >= 0) & (yf < height))  # only a window that leaves the image needs this
            in_s &= inside
            in_r &= inside
        # (np.nonzero returning row/column directly was tried and is slower than flatnonzero + division: 20.7 vs 14.7 ms)
        for flat_hits, roi_out, flat_out in ((np.flatnonzero(in_s), s_roi, s_flat), (np.flatnonzero(in_r), r_roi, r_flat)):
            local = flat_hits // cells
            rem = flat_hits - local * cells
            row = rem // side
            col = rem - row * side
            roi = local + start
            roi_out.append(roi)
            flat_out.append((y0[roi] + row) * width + (x0[roi] + col))
    return (np.concatenate(s_roi), np.concatenate(s_flat), np.concatenate(r_roi), np.concatenate(r_flat))


def segment_std(values: np.ndarray, offsets: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Sample standard deviation (ddof=1, float64) of each ROI's contiguous block of `values`, for all ROIs at once.
    NaN where a ROI has fewer than 2 pixels. Two-pass (mean first), so no cancellation."""
    out = np.full(counts.size, np.nan)
    ok = counts > 1
    if not ok.any():
        return out
    v = values.astype(np.float64)
    starts = offsets[:-1]
    sums = np.add.reduceat(np.append(v, 0.0), starts)  # sentinel keeps a trailing empty block's start in range
    mean = sums / np.maximum(counts, 1)
    dev = v - np.repeat(mean, counts)
    ss = np.add.reduceat(np.append(dev * dev, 0.0), starts)
    out[ok] = np.sqrt(ss[ok] / (counts[ok] - 1))
    return out


def reduce_frame(
    rs: RoiSet, processed: np.ndarray, ignore: np.ndarray | None, affine: np.ndarray, methods: tuple[str, ...],
    *, trimmed_fraction: float, exclusion_mode: str = EXCLUDE_ALL, chunk: int = 16, timers: dict | None = None,
) -> dict:
    """Step 1 for one frame and all ROIs: -> per method (n, 2) arrays [sample, reference] of value, plus std, n_valid and
    n_nominal. Pixels are found in bulk; each ROI's pixels are then handed to the unchanged `reduce_*` functions, so the
    values are the ones today's code produces."""
    clock = time.perf_counter
    t0 = clock()
    shape = processed.shape[:2]
    n = rs.n
    s_roi, s_flat, r_roi, r_flat = locate_pixels(rs, affine, shape, chunk)
    t1 = clock()
    flat_image = processed.reshape(-1)
    s_ok = np.ones(s_flat.size, dtype=bool)
    r_ok = np.ones(r_flat.size, dtype=bool)
    if exclusion_mode == EXCLUDE_ALL:
        union = np.zeros(flat_image.size, dtype=bool)
        union[s_flat] = True  # every ROI's sample aperture, before the ignore mask (as `_sample_exclusion_union`)
        r_ok &= ~union[r_flat]
    if ignore is not None and ignore.shape == shape:
        flat_ignore = ignore.reshape(-1)
        s_ok &= ~flat_ignore[s_flat]
        r_ok &= ~flat_ignore[r_flat]
    n_nominal = np.stack((np.bincount(s_roi[s_ok], minlength=n), np.bincount(r_roi[r_ok], minlength=n)), axis=1)
    s_val = flat_image[s_flat]
    s_ok &= np.isfinite(s_val)
    r_val = flat_image[r_flat]
    r_ok &= np.isfinite(r_val)
    s_roi, s_val = s_roi[s_ok], s_val[s_ok]
    r_roi, r_val, r_flat = r_roi[r_ok], r_val[r_ok], r_flat[r_ok]
    n_valid = np.stack((np.bincount(s_roi, minlength=n), np.bincount(r_roi, minlength=n)), axis=1)
    s_off = np.concatenate(([0], np.cumsum(n_valid[:, 0])))
    r_off = np.concatenate(([0], np.cumsum(n_valid[:, 1])))
    width = shape[1]
    r_xx = r_yy = None
    if "plane_fit" in methods:
        r_xx = (r_flat % width).astype(np.float64)
        r_yy = (r_flat // width).astype(np.float64)
    t2 = clock()

    std = np.stack((segment_std(s_val, s_off, n_valid[:, 0]), segment_std(r_val, r_off, n_valid[:, 1])), axis=1)
    t3 = clock()
    values = {m: np.full((n, 2), np.nan) for m in methods}
    for m in methods:
        out = values[m]
        for i in range(n):
            if n_valid[i, 0] == 0 or n_valid[i, 1] == 0:
                continue  # today: coverage flag "insufficient", both NaN
            a0, a1, b0, b1 = s_off[i], s_off[i + 1], r_off[i], r_off[i + 1]
            sample_value, reference_value = reduce_sample_and_reference(
                s_val[a0:a1], r_val[b0:b1], m, trimmed_mean_fraction=trimmed_fraction,
                reference_xx=None if r_xx is None else r_xx[b0:b1], reference_yy=None if r_yy is None else r_yy[b0:b1],
                sample_x=rs.cx[i], sample_y=rs.cy[i],
            )
            if np.isfinite(sample_value) and np.isfinite(reference_value):
                out[i, 0], out[i, 1] = sample_value, reference_value
    t4 = clock()
    if timers is not None:
        for key, value in (("find pixels (all ROIs, vectorised)", t1 - t0), ("exclude + gather + validity", t2 - t1),
                           ("std per ROI", t3 - t2), (f"reduce {'+'.join(methods)} per ROI", t4 - t3)):
            timers[key] = timers.get(key, 0.0) + value
    return {"values": values, "std": std, "n_valid": n_valid, "n_nominal": n_nominal,
            "_pixels": (s_val, r_val, s_off, r_off, r_xx, r_yy)}


# -- frame preparation with a cached ROI exclusion mask ---------------------------------------------------------


def prepare_frame_fast(raw, geometry, background, det, roi_exclusion, ignore_processed):
    """`apply_preprocessing` with the ROI part of the background exclusion mask supplied (it depends only on the ROI
    geometry and the image shape, so a run computes it once, not once per frame)."""
    processed = pp.apply_spatial_preprocessing(raw, geometry)
    if not background.flatten_background_enabled:
        return processed
    exclude_mask = background.flatten_background_exclude_mask
    external = None
    if ignore_processed is not None:
        candidate = np.asarray(ignore_processed, dtype=bool)
        if candidate.shape == processed.shape[:2]:
            external = candidate
    image = processed.astype(np.float32, copy=False)
    excluded = roi_exclusion.copy() if (background.flatten_background_exclude_area_rois and roi_exclusion is not None) \
        else np.zeros(image.shape[:2], dtype=bool)
    if exclude_mask:
        excluded |= ignored_pixel_mask(image, det, external_mask=external)
    excluded |= ~np.isfinite(image)
    dilation = max(int(getattr(background, "flatten_background_exclusion_dilation_px", 0)), 0)
    if dilation > 0 and np.any(excluded):
        excluded = ndimage.binary_dilation(excluded, iterations=dilation)
    estimate, baseline = bg_estimate._background_and_baseline(
        image, ~excluded, float(background.flatten_background_sigma_px), max(int(background.flatten_background_binning), 1)
    )
    return bg_estimate.apply_background(image, estimate, baseline)


# -- shared context --------------------------------------------------------------------------------------------


class Context:
    def __init__(self, modules, dataset):
        self.m = modules
        self.ds = dataset
        self.records = io.dataset_record_map(dataset)
        self.geometry = modules.geometry.settings()
        self.background = modules.background.settings()
        self.det = modules.roi_toolbox.detection_settings()
        self.engine = modules.analysis_engine
        self.inner, self.outer = self.engine._default_reference_diameters()
        self.trimmed = self.engine._trimmed_mean_fraction
        self.mode = self.engine._reference_exclusion_mode()
        self.timeline = bool(modules.roi_toolbox.has_timeline())
        self._cube_cache: dict[int, tuple] = {}
        self._processed_shape: tuple[int, int] | None = None

    def rois_for(self, cube: int):
        return list(self.m.roi_toolbox.rois_at(cube) if self.timeline else self.m.roi_toolbox.rois())

    def processed_shape(self) -> tuple[int, int]:
        if self._processed_shape is None:
            first = next(iter(self.records.values()))
            self._processed_shape = pp.apply_spatial_preprocessing(io.load_image_array_uncached(str(first.path)), self.geometry).shape[:2]
        return self._processed_shape

    def cube_context(self, cube: int):
        """(rois, RoiSet, ROI exclusion mask for the background) for a cube; one per cube with a timeline, else shared."""
        key = cube if self.timeline else -1
        if key not in self._cube_cache:
            rois = self.rois_for(cube)
            roi_exclusion = bg_estimate._roi_exclusion_mask(self.processed_shape(), rois)
            self._cube_cache[key] = (rois, build_roi_set(rois, self.inner, self.outer), roi_exclusion)
        return self._cube_cache[key]


def pick_frames(ctx: Context, cubes: list[int], per_cube: int = 4):
    """A spread of frames per cube plus every masked frame, each fully prepared the way today's code does it."""
    frames = []
    for cube in cubes:
        wls = st.spectral_wavelengths(ctx.m, cube)
        chosen = {wls[i] for i in np.linspace(0, len(wls) - 1, per_cube).astype(int)}
        for wl in wls:
            affine, ignore, _ = st.frame_inputs(ctx.m, cube, wl)
            if wl in chosen or ignore is not None:
                raw = io.load_image_array_uncached(str(ctx.records[(cube, wl)].path))
                rois, rs, _ = ctx.cube_context(cube)
                processed = pp.apply_preprocessing(raw, ctx.geometry, ctx.background, rois=rois, mask_settings=ctx.det, external_mask=ignore)
                frames.append((cube, wl, raw, processed, affine, ignore))
    return frames


# -- sections ----------------------------------------------------------------------------------------------------


def section_profile(ctx: Context, frames) -> None:
    cube, wl, raw, processed, affine, ignore = frames[0]
    rois, rs, _ = ctx.cube_context(cube)
    valid = np.isfinite(processed)
    centers = np.array([[r.center_x, r.center_y] for r in rois])
    radii = np.array([r.sample_diameter_px / 2.0 for r in rois])
    profile = cProfile.Profile()
    profile.enable()
    for roi in rois:
        st.roi_values_patch(roi, rois, centers, radii, processed, valid, affine, ignore, ctx.inner, ctx.outer, "mean", ctx.trimmed)
    profile.disable()
    print(f"  today's patch-scoped path, {len(rois)} ROIs on one frame, by self time (the function's own work):")
    stats_ = pstats.Stats(profile)
    stats_.sort_stats("tottime")
    rows = sorted(stats_.stats.items(), key=lambda kv: kv[1][2], reverse=True)[:10]
    total = sum(v[2] for v in stats_.stats.values())
    for (filename, line, name), (cc, nc, tt, ct, _callers) in rows:
        print(f"    {tt * 1000:7.1f} ms {100 * tt / total:5.1f} %  {nc:6d} calls  {Path(filename).name}:{line} {name}")


def section_verify(ctx: Context, frames) -> bool:
    ok = True
    methods = REDUCTION_METHODS
    tot = {"frames": 0, "rois": 0, "pixel_sets": 0, "counts": 0, "values": 0, "std": 0}
    worst_std = 0.0
    for cube, wl, raw, processed, affine, ignore in frames:
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        # (a) frame preparation
        fast = prepare_frame_fast(raw, ctx.geometry, ctx.background, ctx.det, roi_exclusion, ignore)
        same_image = np.array_equal(fast, processed, equal_nan=True)
        ok &= same_image
        # (b) ROI stage
        new = reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=ctx.trimmed, exclusion_mode=ctx.mode)
        s_val, r_val, s_off, r_off, r_xx, r_yy = new["_pixels"]
        valid = np.isfinite(processed)
        union = st._incremental_union(rois, processed.shape[:2], affine) if ctx.mode == EXCLUDE_ALL else None
        bad_sets = bad_counts = bad_values = bad_std = 0
        for i, roi in enumerate(rois):
            shape = processed.shape[:2]
            sm = rz.rasterize_sample(roi, shape, affine)
            rm = rz.rasterize_reference(roi, shape, affine, default_inner_diameter_px=ctx.inner, default_outer_diameter_px=ctx.outer)
            if union is not None:
                rm = rm & ~union
            if ignore is not None and ignore.shape == shape:
                sm = sm & ~ignore
                rm = rm & ~ignore
            nominal = (int(sm.sum()), int(rm.sum()))
            sp, rp = processed[sm & valid], processed[rm & valid]
            yy, xx = np.nonzero(rm & valid)
            mine_s, mine_r = s_val[s_off[i]:s_off[i + 1]], r_val[r_off[i]:r_off[i + 1]]
            if not (np.array_equal(sp, mine_s) and np.array_equal(rp, mine_r)):
                bad_sets += 1
                continue
            if (nominal[0], nominal[1]) != (int(new["n_nominal"][i, 0]), int(new["n_nominal"][i, 1])):
                bad_counts += 1
            if sp.size > 1 and rp.size > 1:
                ref_std = (sp.astype(np.float64).std(ddof=1), rp.astype(np.float64).std(ddof=1))
                if not (np.isclose(ref_std[0], new["std"][i, 0], rtol=1e-12, atol=0.0) and np.isclose(ref_std[1], new["std"][i, 1], rtol=1e-12, atol=0.0)):
                    bad_std += 1
                    worst_std = max(worst_std, abs(ref_std[0] - new["std"][i, 0]))
            for m in methods:
                if sp.size == 0 or rp.size == 0:
                    expected = (np.nan, np.nan)
                else:
                    expected = reduce_sample_and_reference(
                        sp, rp, m, trimmed_mean_fraction=ctx.trimmed, reference_xx=xx.astype(np.float64),
                        reference_yy=yy.astype(np.float64), sample_x=float(roi.center_x), sample_y=float(roi.center_y),
                    )
                    if not (np.isfinite(expected[0]) and np.isfinite(expected[1])):
                        expected = (np.nan, np.nan)
                got = new["values"][m][i]
                if not all(x == y or (np.isnan(x) and np.isnan(y)) for x, y in zip(expected, got)):
                    bad_values += 1
        tot["frames"] += 1
        tot["rois"] += len(rois)
        tot["pixel_sets"] += bad_sets
        tot["counts"] += bad_counts
        tot["values"] += bad_values
        tot["std"] += bad_std
        flag = "" if (same_image and not (bad_sets or bad_counts or bad_values or bad_std)) else "   <-- DIFFERENT"
        print(f"  cube {cube:3d} wl {wl:5.0f} {'mask' if ignore is not None else '    '}: processed image identical={same_image}, "
              f"ROIs with different pixel sets={bad_sets}, counts={bad_counts}, values(4 methods)={bad_values}, std={bad_std}{flag}")
        ok &= not (bad_sets or bad_counts or bad_values or bad_std)
    print(f"\n  total: {tot['frames']} frames x {tot['rois'] // max(tot['frames'], 1)} ROIs = {tot['rois']} ROI-frames; "
          f"different pixel sets {tot['pixel_sets']}, counts {tot['counts']}, values {tot['values']} (x4 methods), std {tot['std']}")
    print("  RESULT: " + ("IDENTICAL to today's code" if ok else "DIFFERENCES FOUND"))
    return ok


def section_bench(ctx: Context, frames, methods: tuple[str, ...]) -> dict:
    timers: dict[str, float] = {}
    prep_ms, full_ms = [], []
    for cube, wl, raw, processed, affine, ignore in frames:
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        _, t = st.timed(pp.apply_preprocessing, raw, ctx.geometry, ctx.background, rois=rois, mask_settings=ctx.det, external_mask=ignore)
        full_ms.append(t)
        _, t = st.timed(prepare_frame_fast, raw, ctx.geometry, ctx.background, ctx.det, roi_exclusion, ignore)
        prep_ms.append(t)
        reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=ctx.trimmed, exclusion_mode=ctx.mode, timers=timers)
    n = len(frames)
    print(f"  frames timed: {n}   methods: {', '.join(methods)}")
    print(f"  frame preparation, today (apply_preprocessing)             {statistics.mean(full_ms):7.2f} ms")
    print(f"  frame preparation, ROI exclusion mask cached               {statistics.mean(prep_ms):7.2f} ms")
    total = 0.0
    for key, seconds in timers.items():
        print(f"  ROI stage: {key:<46s} {1000 * seconds / n:7.2f} ms per frame")
        total += seconds
    print(f"  ROI stage total (all {ctx.cube_context(frames[0][0])[1].n} ROIs){'':<31s} {1000 * total / n:7.2f} ms per frame")
    return {"prepare_today_ms": statistics.mean(full_ms), "prepare_cached_ms": statistics.mean(prep_ms), "roi_stage_ms": 1000 * total / n}


_W: dict = {}  # per worker process (multiprocessing path)


def _scan_one(path, affine, ignore, rs, roi_exclusion, geometry, background, det, mode, trimmed, methods):
    """One frame: read -> prepare -> every ROI. Touches no module; safe in a thread or a process."""
    t0 = time.perf_counter()
    raw = io.load_image_array_uncached(path)
    t1 = time.perf_counter()
    processed = prepare_frame_fast(raw, geometry, background, det, roi_exclusion, ignore)
    t2 = time.perf_counter()
    res = reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=trimmed, exclusion_mode=mode)
    t3 = time.perf_counter()
    return res, (t1 - t0, t2 - t1, t3 - t2)


def _mp_init(geometry, background, det, mode, trimmed, shape, methods, switch_interval) -> None:
    io.use_fast_tiff_decoder(True)
    if switch_interval:
        sys.setswitchinterval(switch_interval)
    _W.update(geometry=geometry, background=background, det=det, mode=mode, trimmed=trimmed, shape=shape, methods=methods)


def _mp_cube(task):
    """One cube in a worker process: all its frames, sequentially. Returns plain arrays."""
    cube_pos, rs, frames = task
    w = _W
    roi_exclusion = bg_estimate._roi_exclusion_mask(w["shape"], list(rs.rois))
    n_wl = len(frames)
    values = {m: np.full((rs.n, n_wl, 2), np.nan) for m in w["methods"]}
    std = np.full((rs.n, n_wl, 2), np.nan)
    n_valid = np.zeros((rs.n, n_wl, 2), dtype=np.int32)
    n_nominal = np.zeros((rs.n, n_wl, 2), dtype=np.int32)
    stage = np.zeros(3)
    for k, (path, affine, ignore) in enumerate(frames):
        res, timing = _scan_one(path, affine, ignore, rs, roi_exclusion, w["geometry"], w["background"], w["det"], w["mode"], w["trimmed"], w["methods"])
        for m in w["methods"]:
            values[m][:, k, :] = res["values"][m]
        std[:, k, :] = res["std"]
        n_valid[:, k, :] = res["n_valid"]
        n_nominal[:, k, :] = res["n_nominal"]
        stage += timing
    return cube_pos, values, std, n_valid, n_nominal, stage


def section_scan(ctx: Context, cubes: list[int], threads: int, processes: int, methods: tuple[str, ...], save: Path | None,
                 switch_interval: float = 0.0) -> None:
    geometry, background, det = ctx.geometry, ctx.background, ctx.det
    shape = ctx.processed_shape()
    # Everything that touches a module is resolved here, on this thread; the workers see plain data only.
    t_setup = time.perf_counter()
    jobs = []  # (cube_pos, wl_pos, path, affine, ignore, rs, roi_exclusion)
    for cube_pos, cube in enumerate(cubes):
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        for wl_pos, wl in enumerate(st.spectral_wavelengths(ctx.m, cube)):
            affine, ignore, _ = st.frame_inputs(ctx.m, cube, wl)
            jobs.append((cube_pos, wl_pos, str(ctx.records[(cube, wl)].path), affine, ignore, rs, roi_exclusion))
    n_rois = jobs[0][5].n
    n_wl = max(j[1] for j in jobs) + 1
    setup_s = time.perf_counter() - t_setup
    out = {m: np.full((n_rois, len(cubes), n_wl, 2), np.nan) for m in methods}
    out_std = np.full((n_rois, len(cubes), n_wl, 2), np.nan)
    out_n = np.zeros((n_rois, len(cubes), n_wl, 2), dtype=np.int32)
    out_nom = np.zeros((n_rois, len(cubes), n_wl, 2), dtype=np.int32)
    stage = np.zeros(3)
    if switch_interval:
        sys.setswitchinterval(switch_interval)

    started = time.perf_counter()
    if processes > 0:
        by_cube: dict[int, list] = {}
        for cube_pos, wl_pos, path, affine, ignore, rs, _roi_exclusion in jobs:
            by_cube.setdefault(cube_pos, []).append((path, affine, ignore))
        tasks = [(cube_pos, jobs[next(i for i, j in enumerate(jobs) if j[0] == cube_pos)][5], frames) for cube_pos, frames in by_cube.items()]
        initargs = (geometry, background, det, ctx.mode, ctx.trimmed, shape, methods, switch_interval)
        with ProcessPoolExecutor(max_workers=processes, initializer=_mp_init, initargs=initargs) as pool:
            first_result_at = None
            for cube_pos, values, std, n_valid, n_nominal, stage_here in pool.map(_mp_cube, tasks):
                if first_result_at is None:
                    first_result_at = time.perf_counter() - started
                k = std.shape[1]
                for m in methods:
                    out[m][:, cube_pos, :k, :] = values[m]
                out_std[:, cube_pos, :k, :] = std
                out_n[:, cube_pos, :k, :] = n_valid
                out_nom[:, cube_pos, :k, :] = n_nominal
                stage += stage_here
        label = f"{processes} worker process(es)" + f" (first result after {first_result_at:.1f} s: process start-up included in the wall time)"
    else:
        def init_worker() -> None:
            io.use_fast_tiff_decoder(True)

        def work(job):
            cube_pos, wl_pos, path, affine, ignore, rs, roi_exclusion = job
            res, timing = _scan_one(path, affine, ignore, rs, roi_exclusion, geometry, background, det, ctx.mode, ctx.trimmed, methods)
            for m in methods:
                out[m][:, cube_pos, wl_pos, :] = res["values"][m]
            out_std[:, cube_pos, wl_pos, :] = res["std"]
            out_n[:, cube_pos, wl_pos, :] = res["n_valid"]
            out_nom[:, cube_pos, wl_pos, :] = res["n_nominal"]
            return timing

        with ThreadPoolExecutor(max_workers=threads, initializer=init_worker) as pool:
            for timing in pool.map(work, jobs):
                stage += timing
        label = f"{threads} worker thread(s)"
    wall = time.perf_counter() - started
    frames = len(jobs)
    total_cubes = len(list(ctx.m.dataset.spectral_cubes()))
    print(f"  {frames} frames ({len(cubes)} cubes x {n_wl} wavelengths) x {n_rois} ROIs, methods {', '.join(methods)}, {label}"
          + (f", GIL switch interval {switch_interval * 1000:.2f} ms" if switch_interval else ""))
    print(f"  setup on the main thread (inputs of all frames): {setup_s:.1f} s")
    print(f"  WALL TIME of the scan: {wall:.1f} s  =  {frames / wall:.1f} frames/s  ({1000 * wall / frames:.1f} ms per frame)")
    print("  busy time per stage, summed over workers (ms per frame): "
          + ", ".join(f"{k} {1000 * v / frames:.1f}" for k, v in zip(("read", "prepare", "roi stage"), stage)))
    scale = total_cubes / len(cubes)
    print(f"  => whole dataset ({total_cubes} cubes): about {(wall + setup_s) * scale / 60:.1f} min  (scan {wall * scale / 60:.1f} + setup {setup_s * scale / 60:.1f})")
    finite = np.isfinite(out[methods[0]][..., 0])
    print(f"  results: {int(finite.sum())} of {finite.size} (ROI, cube, wavelength) cells have a value; "
          f"mean n_valid sample {out_n[..., 0].mean():.0f} / reference {out_n[..., 1].mean():.0f} pixels")

    # Spot check against today's code path (apply_preprocessing + full-frame masks + reduce) on random scanned frames.
    rng = random.Random(1)
    checked = differ = 0
    for job in rng.sample(jobs, min(6, len(jobs))):
        cube_pos, wl_pos, path, affine, ignore, rs, _ = job
        rois = list(rs.rois)
        raw = io.load_image_array_uncached(path)
        processed = pp.apply_preprocessing(raw, geometry, background, rois=rois, mask_settings=det, external_mask=ignore)
        valid = np.isfinite(processed)
        union = st._incremental_union(rois, processed.shape[:2], affine) if ctx.mode == EXCLUDE_ALL else None
        for i, roi in enumerate(rois):
            if union is None:
                continue
            old = st.roi_values_full(roi, processed, valid, affine, ignore, union, ctx.inner, ctx.outer, methods[0], ctx.trimmed)
            new = out[methods[0]][i, cube_pos, wl_pos]
            checked += 1
            if not all(x == y or (np.isnan(x) and np.isnan(y)) for x, y in zip(old, new)):
                differ += 1
    print(f"  spot check against today's code: {checked - differ} of {checked} ROI values identical (6 random frames)")
    if save is not None:
        np.savez_compressed(save, cubes=np.array(cubes), **{f"value_{m}": out[m] for m in methods}, std=out_std, n_valid=out_n, n_nominal=out_nom)
        print(f"  saved: {save} ({save.stat().st_size / 1e6:.1f} MB)")


# -- main ------------------------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiff", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--sections", type=str, default="profile,verify,bench,scan")
    parser.add_argument("--cube-list", type=str, default="27,231,100,298", help="cubes for profile/verify/bench")
    parser.add_argument("--methods", type=str, default="mean", help="reduction methods for bench/scan (default mean)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--processes", type=int, default=0, help="scan with this many worker PROCESSES instead of threads")
    parser.add_argument("--switch-interval", type=float, default=0.0, help="Python GIL switch interval in seconds (default 0.005); try 0.0005")
    parser.add_argument("--scan-cubes", type=int, default=20, help="cubes for the scan, spread over the run")
    parser.add_argument("--scan-all", action="store_true", help="scan every cube")
    parser.add_argument("--save", type=Path, default=None, help="write the scan's numbers to this .npz")
    args = parser.parse_args()
    sections = {s.strip() for s in args.sections.split(",")}
    methods = tuple(m.strip() for m in args.methods.split(","))

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])  # the modules are QObjects; keep the application alive
    dataset = st.load_one(args.tiff)
    modules, _state = st.build_modules(dataset, args.session)
    ctx = Context(modules, dataset)
    print(f"{len(ctx.m.roi_toolbox.rois())} ROIs, exclusion mode {ctx.mode}, timeline {ctx.timeline}, {args.threads} threads")
    cubes = [int(c) for c in args.cube_list.split(",")]
    frames = pick_frames(ctx, cubes) if sections & {"profile", "verify", "bench"} else []
    if "profile" in sections:
        st.header("PROFILE")
        section_profile(ctx, frames)
    if "verify" in sections:
        st.header("VERIFY: new reducer and cached frame preparation vs today's code")
        section_verify(ctx, frames)
    if "bench" in sections:
        st.header("BENCH: stage times per frame")
        section_bench(ctx, frames, methods)
    if "scan" in sections:
        st.header("SCAN: step 1 end to end")
        all_cubes = list(ctx.m.dataset.spectral_cubes())
        scan_cubes = all_cubes if args.scan_all else st.pick_cubes(ctx.m, args.scan_cubes)
        section_scan(ctx, scan_cubes, args.threads, args.processes, methods, args.save, args.switch_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
