"""Analysis step 1 for one frame: processed pixels -> reduced numbers for every ROI at once.

Design record: ``docs/analysis_redesign_plan_2026-10-19.md`` (sections 10 and 11). No Qt import allowed in this file.

What it does. For one processed frame (after rotate / flip / crop and background flattening) and all ROIs, it returns for
the sample aperture and the reference ring of every ROI: the reduced **value** (per reduction method), the **standard
deviation** of the pixels, the number of **valid** pixels and the number of **nominal** pixels. Everything after this
(formula, spectra, fits, metrics) works on these stored numbers and never reads an image again.

How it differs from ``tasks.compute_cell`` (which stays the reference): the pixels of all ROIs are found in one
vectorised pass instead of one mask per ROI, and each ROI's pixels are then handed to the unchanged ``reduce_*``
functions, so the values are the ones ``compute_cell`` produces (checked bit for bit on real data, see the plan doc).

Rules it follows, taken from ``compute_cell`` and the invariants in ``apps/LSPRi/eva/CLAUDE.md``:

* A ROI's pixels are never pooled with another ROI's before reducing: every ROI is reduced on its own.
* A pixel belongs to a ROI if, after mapping its (integer) coordinates back through the frame's inverse chromatic affine,
  its squared distance from the ROI centre is within the squared radii (inclusive at both ends): the same test as
  ``roi.rasterize``. Coordinates are in **processed image space**; ``affine`` is the frame's 2x3 chromatic matrix
  (identity when there is none).
* ``exclude_all_sample_rois`` (default): a reference ring never counts a pixel inside **any** ROI's sample aperture,
  built from every ROI passed in, never a selected subset. ``none``: rings overlap sample apertures freely.
* The ignore mask (processed space) removes pixels from both apertures. It does not change the image.
* A pixel without a value (NaN, e.g. created by rotation) is never reduced. ``n_nominal`` counts the aperture after
  exclusions and the ignore mask but before this test; ``n_valid`` counts after it. Keeping both lets a minimum valid
  fraction be applied later, on the stored numbers, without rereading any image.
* A ROI whose sample or reference has no valid pixel gets NaN for both values (``compute_cell``'s "insufficient" cell with
  all thresholds at 0). Standard deviations are NaN for fewer than 2 pixels.

Units: value and standard deviation are in the image's own intensity units; counts are pixels.

Not here: reading images, the recipe, the store file, running many frames (``scan``), minimum-valid-fraction thresholds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix
from ..roi import rasterize as rz
from ..roi.model import AreaRoi
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE, REFERENCE_EXCLUSION_MODES
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION, REDUCTION_METHODS, reduce_sample_and_reference

EXCLUDE_ALL_SAMPLE_ROIS = "exclude_all_sample_rois"

DEFAULT_BLOCK_ROIS = 16
"""ROIs per block of the vectorised distance arithmetic. Measured 2026-10-19 on a laptop: 2 -> 18.9 ms per frame of 160 ROIs,
16 -> 14.7, 160 -> 28.8 (the block's arrays should stay in cache). A speed knob only; it never changes a result."""

SAMPLE, REFERENCE = 0, 1
"""Column order of every (n_rois, 2) result array."""


@dataclass(frozen=True)
class RoiSet:
    """The geometry of an ordered list of ROIs as plain arrays, in the radii ``roi.rasterize`` uses.

    ``vectorised[i]`` is False for a ROI with a free-form mask on either side: that ROI is rasterised by the existing
    per-ROI code (slower, same result). Row ``i`` of every result array belongs to ``rois[i]``."""

    rois: tuple[AreaRoi, ...]
    cx: np.ndarray
    cy: np.ndarray
    sample_r: np.ndarray  # outer radius of the sample disk, px
    ring_in_r: np.ndarray
    ring_out_r: np.ndarray
    sample_on: np.ndarray  # False: empty sample aperture (radius 0)
    ring_on: np.ndarray  # False: no reference ring ("none", or outer diameter 0)
    vectorised: np.ndarray
    inner_default_px: float  # reference-ring diameters used for a ROI without its own, see rasterize.effective_reference_diameters
    outer_default_px: float

    @property
    def n(self) -> int:
        return len(self.rois)


def build_roi_set(rois, default_inner_diameter_px: float, default_outer_diameter_px: float) -> RoiSet:
    rois = tuple(rois)
    n = len(rois)
    cx = np.array([float(r.center_x) for r in rois], dtype=np.float64)
    cy = np.array([float(r.center_y) for r in rois], dtype=np.float64)
    sample_r = np.zeros(n)
    ring_in = np.zeros(n)
    ring_out = np.zeros(n)
    ring_on = np.zeros(n, dtype=bool)
    vectorised = np.ones(n, dtype=bool)
    for i, roi in enumerate(rois):
        if (roi.sample_geometry_type == "mask" and roi.sample_mask is not None) or (
            roi.reference_geometry_type == "mask" and roi.reference_mask is not None
        ):
            vectorised[i] = False
            continue
        sample_r[i] = max(float(roi.sample_diameter_px) / 2.0, 0.0)
        if roi.reference_geometry_type != "none":
            inner_d, outer_d = rz.effective_reference_diameters(roi, default_inner_diameter_px, default_outer_diameter_px)
            if outer_d > 0.0:
                inner_r = max(inner_d, 0.0) / 2.0
                ring_in[i] = inner_r
                ring_out[i] = max(outer_d / 2.0, inner_r)
                ring_on[i] = True
    return RoiSet(
        rois, cx, cy, sample_r, ring_in, ring_out, sample_r > 0.0, ring_on, vectorised,
        float(default_inner_diameter_px), float(default_outer_diameter_px),
    )


def locate_pixels(
    rs: RoiSet, affine: np.ndarray, shape: tuple[int, int], block: int = DEFAULT_BLOCK_ROIS
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Flat pixel indices (row-major into an ``shape`` image) of every ROI's sample disk and reference ring:
    ``(sample_roi, sample_flat, ring_roi, ring_flat)``, each pair sorted by ROI and, within a ROI, by pixel (so the order
    of a ROI's pixels is the order ``image[mask]`` gives). Not yet reduced by any exclusion.

    One window of the same size around every ROI, so the arithmetic is a few passes over (block, side, side) arrays
    rather than a Python call per ROI. The window only has to contain the ROI's region, which lies within
    (largest stretch of the affine) x (outer radius) of the transformed centre; a larger window never adds a member."""
    height, width = shape
    n = rs.n
    parts: dict[str, list[np.ndarray]] = {"s_roi": [], "s_flat": [], "r_roi": [], "r_flat": []}
    index = np.flatnonzero(rs.vectorised)
    if index.size:
        every = index.size == n
        cx, cy = (rs.cx, rs.cy) if every else (rs.cx[index], rs.cy[index])
        sample_r, ring_in_r, ring_out_r = (
            (rs.sample_r, rs.ring_in_r, rs.ring_out_r) if every else (rs.sample_r[index], rs.ring_in_r[index], rs.ring_out_r[index])
        )
        sample_on, ring_on = (rs.sample_on, rs.ring_on) if every else (rs.sample_on[index], rs.ring_on[index])
        inv = invert_affine_matrix(affine)
        linear = np.asarray(affine[:, :2], dtype=np.float64)
        max_scale = max(float(np.max(np.linalg.svd(linear, compute_uv=False))), 1e-6)
        centres = apply_affine_to_points(np.column_stack((cx, cy)), affine)
        reach = float(max(sample_r.max(), ring_out_r.max())) * max_scale + 1.0
        x0 = np.floor(centres[:, 0] - reach).astype(np.int64)
        y0 = np.floor(centres[:, 1] - reach).astype(np.int64)
        side = int(np.ceil(2.0 * reach)) + 2
        step = np.arange(side, dtype=np.float64)
        a, b, c = (float(v) for v in inv[0])
        d, e, f = (float(v) for v in inv[1])
        cells = side * side
        s_rad2 = np.where(sample_on, sample_r * sample_r, -1.0)  # -1: no pixel can match (a squared distance is >= 0)
        r_out2 = np.where(ring_on, ring_out_r * ring_out_r, -1.0)
        r_in2 = ring_in_r * ring_in_r
        for start in range(0, index.size, block):
            sl = slice(start, min(start + block, index.size))
            xf = x0[sl].astype(np.float64)[:, None, None] + step[None, None, :]  # (m, 1, side): pixel x
            yf = y0[sl].astype(np.float64)[:, None, None] + step[None, :, None]  # (m, side, 1): pixel y
            sx = xf * a + yf * b
            sx += c
            sy = xf * d + yf * e
            sy += f
            sx -= cx[sl][:, None, None]
            sy -= cy[sl][:, None, None]
            sx *= sx
            sy *= sy
            sx += sy  # squared distance from the ROI centre, in the ROI's own (source) space
            in_s = sx <= s_rad2[sl][:, None, None]
            in_r = (sx <= r_out2[sl][:, None, None]) & (sx >= r_in2[sl][:, None, None])
            if x0[sl].min() < 0 or y0[sl].min() < 0 or x0[sl].max() + side > width or y0[sl].max() + side > height:
                inside = ((xf >= 0) & (xf < width)) & ((yf >= 0) & (yf < height))  # only a window leaving the image needs this
                in_s &= inside
                in_r &= inside
            for hits, roi_key, flat_key in ((np.flatnonzero(in_s), "s_roi", "s_flat"), (np.flatnonzero(in_r), "r_roi", "r_flat")):
                local = hits // cells
                rem = hits - local * cells
                row = rem // side
                col = rem - row * side
                local += start
                parts[roi_key].append(local if every else index[local])
                parts[flat_key].append((y0[local] + row) * width + (x0[local] + col))
    for i in np.flatnonzero(~rs.vectorised):
        roi = rs.rois[i]
        masks = (
            rz.rasterize_sample(roi, shape, affine),
            rz.rasterize_reference(
                roi, shape, affine, default_inner_diameter_px=rs.inner_default_px, default_outer_diameter_px=rs.outer_default_px
            ),
        )
        for mask, roi_key, flat_key in zip(masks, ("s_roi", "r_roi"), ("s_flat", "r_flat")):
            flat = np.flatnonzero(mask)
            parts[roi_key].append(np.full(flat.size, i, dtype=np.int64))
            parts[flat_key].append(flat.astype(np.int64, copy=False))
    out = {key: (np.concatenate(arrays) if arrays else np.zeros(0, dtype=np.int64)) for key, arrays in parts.items()}
    if not rs.vectorised.all():
        for roi_key, flat_key in (("s_roi", "s_flat"), ("r_roi", "r_flat")):
            order = np.argsort(out[roi_key], kind="stable")  # restore ROI order; the order within a ROI is already by pixel
            out[roi_key], out[flat_key] = out[roi_key][order], out[flat_key][order]
    return out["s_roi"], out["s_flat"], out["r_roi"], out["r_flat"]


def segment_std(values: np.ndarray, offsets: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Sample standard deviation (ddof=1, float64) of each ROI's contiguous block of ``values`` (block ``i`` is
    ``values[offsets[i]:offsets[i + 1]]``), for all ROIs at once. NaN for a block of fewer than 2 values. Two passes (the
    mean first), so there is no cancellation."""
    out = np.full(counts.size, np.nan)
    ok = counts > 1
    if not ok.any():
        return out
    v = values.astype(np.float64)
    starts = offsets[:-1]
    sums = np.add.reduceat(np.append(v, 0.0), starts)  # the sentinel keeps the start of a trailing empty block in range
    mean = sums / np.maximum(counts, 1)
    dev = v - np.repeat(mean, counts)
    ss = np.add.reduceat(np.append(dev * dev, 0.0), starts)
    out[ok] = np.sqrt(ss[ok] / (counts[ok] - 1))
    return out


@dataclass(frozen=True)
class FrameReduction:
    """Result of :func:`reduce_frame`. Arrays are ``(n_rois, 2)``; column ``SAMPLE`` (0) / ``REFERENCE`` (1)."""

    methods: tuple[str, ...]
    values: dict[str, np.ndarray]  # method -> float64
    std: np.ndarray  # float64, standard deviation of the valid pixels, ddof=1
    n_valid: np.ndarray  # int32
    n_nominal: np.ndarray  # int32


def reduce_frame(
    rs: RoiSet,
    processed: np.ndarray,
    ignore: np.ndarray | None,
    affine: np.ndarray,
    methods: tuple[str, ...] = ("mean",),
    *,
    trimmed_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
    exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE,
    block: int = DEFAULT_BLOCK_ROIS,
    timers: dict[str, float] | None = None,
) -> FrameReduction:
    """Step 1 for one frame and all ROIs; see the module docstring for the rules.

    ``processed``: the frame after ``apply_preprocessing`` (2-D float, NaN where there is no measurement).
    ``ignore``: bool image of the same shape, True = leave out; ignored when ``None`` or of another shape (as in
    ``compute_cell``). ``timers``, if given, accumulates seconds per stage (diagnostics only)."""
    unknown = [m for m in methods if m not in REDUCTION_METHODS]
    if unknown:
        raise ValueError(f"unknown reduction method(s) {unknown}; known: {REDUCTION_METHODS}")
    if exclusion_mode not in REFERENCE_EXCLUSION_MODES:
        raise ValueError(f"unknown reference exclusion mode {exclusion_mode!r}; known: {REFERENCE_EXCLUSION_MODES}")
    clock = time.perf_counter
    t0 = clock()
    shape = processed.shape[:2]
    n = rs.n
    if n == 0:
        empty = np.zeros((0, 2))
        return FrameReduction(tuple(methods), {m: empty.copy() for m in methods}, empty.copy(),
                              np.zeros((0, 2), dtype=np.int32), np.zeros((0, 2), dtype=np.int32))
    s_roi, s_flat, r_roi, r_flat = locate_pixels(rs, affine, shape, block)
    t1 = clock()
    flat_image = processed.reshape(-1)
    s_ok = np.ones(s_flat.size, dtype=bool)
    r_ok = np.ones(r_flat.size, dtype=bool)
    if exclusion_mode == EXCLUDE_ALL_SAMPLE_ROIS:
        union = np.zeros(flat_image.size, dtype=bool)
        union[s_flat] = True  # every ROI's sample aperture, before the ignore mask (as tasks._sample_exclusion_union)
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
    s_off = np.concatenate(([0], np.cumsum(n_valid[:, SAMPLE])))
    r_off = np.concatenate(([0], np.cumsum(n_valid[:, REFERENCE])))
    r_xx = r_yy = None
    if "plane_fit" in methods:
        r_xx = (r_flat % shape[1]).astype(np.float64)
        r_yy = (r_flat // shape[1]).astype(np.float64)
    t2 = clock()

    std = np.stack((segment_std(s_val, s_off, n_valid[:, SAMPLE]), segment_std(r_val, r_off, n_valid[:, REFERENCE])), axis=1)
    t3 = clock()
    values = {m: np.full((n, 2), np.nan) for m in methods}
    for method in methods:
        out = values[method]
        for i in range(n):
            if n_valid[i, SAMPLE] == 0 or n_valid[i, REFERENCE] == 0:
                continue
            a0, a1, b0, b1 = s_off[i], s_off[i + 1], r_off[i], r_off[i + 1]
            sample_value, reference_value = reduce_sample_and_reference(
                s_val[a0:a1], r_val[b0:b1], method, trimmed_mean_fraction=trimmed_fraction,
                reference_xx=None if r_xx is None else r_xx[b0:b1], reference_yy=None if r_yy is None else r_yy[b0:b1],
                sample_x=float(rs.cx[i]), sample_y=float(rs.cy[i]),
            )
            if np.isfinite(sample_value) and np.isfinite(reference_value):  # e.g. a plane fit with too few ring pixels
                out[i, SAMPLE], out[i, REFERENCE] = sample_value, reference_value
    t4 = clock()
    if timers is not None:
        for key, seconds in (
            ("find pixels", t1 - t0), ("exclude + gather + validity", t2 - t1), ("std", t3 - t2),
            (f"reduce {'+'.join(methods)}", t4 - t3),
        ):
            timers[key] = timers.get(key, 0.0) + seconds
    return FrameReduction(tuple(methods), values, std, n_valid.astype(np.int32), n_nominal.astype(np.int32))
