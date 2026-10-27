"""Analysis step 1 for one frame: processed pixels -> reduced numbers for every ROI at once.

Design record: ``docs/analysis_redesign_plan_2026-10-19.md`` (sections 10 and 11). No Qt import allowed in this file.

What it does. For one processed frame (after rotate / flip / crop and background flattening) and all ROIs, it returns for
the sample aperture and the reference ring of every ROI: the reduced **value** (per reduction method), the **standard
deviation** of the pixels, the number of **valid** pixels and the number of **nominal** pixels. Everything after this
(formula, spectra, fits, metrics) works on these stored numbers and never reads an image again.

How it differs from ``tasks.compute_cell`` (which stays the reference): the pixels of all ROIs are found in one
vectorised pass instead of one mask per ROI. The pixel sets, counts and the values of ``median``, ``trimmed_mean`` and
``plane_fit`` are the ones ``compute_cell`` produces (each ROI's pixels go to the unchanged ``reduce_*`` functions).
**The ``mean`` is the one deliberate numeric difference** (maintainer, 2026-10-19): it is accumulated in float64, where
``compute_cell`` takes ``np.mean`` of float32 pixels, which accumulates in float32. The float64 value is the more
accurate one; the two differ by about 1e-7 relative (measured: plan doc, section 12). The standard deviation is float64
too (it has no older counterpart).

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
  exclusions and the ignore mask but before this test; ``n_valid`` counts after it. ``n_masked`` is the part of the aperture the
  ignore mask removed (what the Sensorgram marks), so a mask and a ROI are linked per cell at no extra cost. Keeping both lets a minimum valid
  fraction be applied later, on the stored numbers, without rereading any image.
* A ROI whose sample or reference has no valid pixel gets NaN for both values (``compute_cell``'s "insufficient" cell with
  all thresholds at 0). Standard deviations are NaN for fewer than 2 pixels.

Units: value and standard deviation are in the image's own intensity units; counts are pixels.

Not here: reading images, the recipe, the store file, running many frames (``scan``), minimum-valid-fraction thresholds.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

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
    key: str = field(default="", compare=False)
    """A digest of everything above that decides which pixels a ROI covers (also free-form masks). Two RoiSets with the
    same key cover the same pixels; used only to find a cached `PixelLayout`, never stored."""

    @property
    def n(self) -> int:
        return len(self.rois)

    def subset(self, indices) -> RoiSet:
        """The ROIs at ``indices`` (positions in this set, in the order given) as a RoiSet of their own: row ``k`` of every result for
        it belongs to ``rois[indices[k]]``. Its ``key`` is made from this set's key and the indices, so it never equals another set's."""
        idx = np.asarray(tuple(indices), dtype=np.intp)
        key = hashlib.sha256(self.key.encode("ascii") + b"|subset|" + idx.astype(np.int64).tobytes()).hexdigest()
        return RoiSet(
            tuple(self.rois[i] for i in idx), self.cx[idx], self.cy[idx], self.sample_r[idx], self.ring_in_r[idx], self.ring_out_r[idx],
            self.sample_on[idx], self.ring_on[idx], self.vectorised[idx], self.inner_default_px, self.outer_default_px, key,
        )


NEIGHBOUR_REACH_MARGIN_PX = 2.0
"""Safety margin when asking whether one ROI's sample disk can touch another ROI's reference ring (`local_roi_keys`). The geometric test is exact
(two discs that do not overlap share no pixel), so this only covers floating-point edge cases; a larger margin costs a few needless re-reads,
never a wrong number."""

_UNBOUNDED_REACH_PX = 1.0e18  # a free-form (mask) ROI has no radius: it is treated as reaching everything


def roi_row_digests(rs: RoiSet) -> list[bytes]:
    """One digest per ROI of everything that decides which pixels that ROI covers: the per-ROI part of ``RoiSet.key`` (centre, radii, which
    parts exist, and for a free-form ROI its masks), plus the shared default ring diameters (a free-form ROI is rasterised with them)."""
    defaults = np.array([rs.inner_default_px, rs.outer_default_px], dtype=np.float64).tobytes()
    rows: list[bytes] = []
    for i in range(rs.n):
        digest = hashlib.blake2b(digest_size=16)
        digest.update(np.array([rs.cx[i], rs.cy[i], rs.sample_r[i], rs.ring_in_r[i], rs.ring_out_r[i]], dtype=np.float64).tobytes())
        digest.update(bytes((int(rs.ring_on[i]), int(rs.vectorised[i]))))
        digest.update(defaults)
        if not rs.vectorised[i]:
            roi = rs.rois[i]
            digest.update(repr((roi.sample_geometry_type, roi.reference_geometry_type, roi.sample_diameter_px,
                                roi.reference_inner_diameter_px, roi.reference_outer_diameter_px)).encode())
            for mask in (roi.sample_mask, roi.reference_mask):
                if mask is not None:
                    digest.update(repr((mask.x0, mask.y0, mask.mask.shape)).encode())
                    digest.update(np.ascontiguousarray(mask.mask, dtype=bool).tobytes())
        rows.append(digest.digest())
    return rows


def local_roi_keys(rs: RoiSet, exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE, margin_px: float = NEIGHBOUR_REACH_MARGIN_PX) -> list[bytes]:
    """One key per ROI that changes exactly when the *pixels that ROI reads* can change: its own geometry, and the geometry of the other ROIs
    whose sample disk can touch its reference ring (``exclude_all_sample_rois`` leaves those pixels out of the ring). Moving a ROI far from
    every ring changes only its own key; moving it into reach of a neighbour's ring changes the neighbour's key too.

    The test is made in the ROIs' own (source) space, where the discs are defined: a pixel is in both ring j and disc k only if its source
    position is within ``ring_out_j`` of ROI j's centre and within ``sample_r_k`` of ROI k's, so ``distance <= ring_out_j + sample_r_k`` is
    necessary, whatever the chromatic affine of the frame (it maps both discs alike). A free-form ROI has no radius and counts as reaching
    everything; a ROI without a ring (``reference_geometry_type == "none"``) is affected by no neighbour; ``exclusion_mode == "none"`` has
    no neighbours at all.

    Pure; O(n squared) arithmetic for n ROIs (160 ROIs: a few milliseconds)."""
    rows = roi_row_digests(rs)
    if exclusion_mode != EXCLUDE_ALL_SAMPLE_ROIS or rs.n < 2:
        return rows
    ring_reach = np.where(rs.vectorised, rs.ring_out_r, _UNBOUNDED_REACH_PX)
    sample_reach = np.where(rs.vectorised, rs.sample_r, _UNBOUNDED_REACH_PX)
    has_ring = rs.ring_on | ~rs.vectorised
    has_sample = rs.sample_on | ~rs.vectorised
    distance = np.hypot(rs.cx[:, None] - rs.cx[None, :], rs.cy[:, None] - rs.cy[None, :])
    near = (distance <= ring_reach[:, None] + sample_reach[None, :] + float(margin_px)) & has_ring[:, None] & has_sample[None, :]
    np.fill_diagonal(near, False)
    keys: list[bytes] = []
    for j in range(rs.n):
        digest = hashlib.blake2b(digest_size=16)
        digest.update(rows[j])
        for k in np.flatnonzero(near[j]):  # in ROI order, so the key does not depend on anything but the geometry
            digest.update(rows[int(k)])
        keys.append(digest.digest())
    return keys


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
    digest = hashlib.sha256()
    for array in (cx, cy, sample_r, ring_in, ring_out, ring_on, vectorised):
        digest.update(np.ascontiguousarray(array).tobytes())
    digest.update(np.array([default_inner_diameter_px, default_outer_diameter_px], dtype=np.float64).tobytes())
    for i in np.flatnonzero(~vectorised):  # a free-form mask ROI: its masks and geometry types decide its pixels
        roi = rois[i]
        digest.update(repr((i, roi.center_x, roi.center_y, roi.sample_diameter_px, roi.sample_geometry_type, roi.reference_geometry_type,
                            roi.reference_inner_diameter_px, roi.reference_outer_diameter_px)).encode())
        for mask in (roi.sample_mask, roi.reference_mask):
            if mask is not None:
                digest.update(repr((mask.x0, mask.y0, mask.mask.shape)).encode())
                digest.update(np.ascontiguousarray(mask.mask, dtype=bool).tobytes())
    return RoiSet(
        rois, cx, cy, sample_r, ring_in, ring_out, sample_r > 0.0, ring_on, vectorised,
        float(default_inner_diameter_px), float(default_outer_diameter_px), digest.hexdigest(),
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


def group_mean_std(values: np.ndarray, roi: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean and sample standard deviation (ddof=1) of the values of each ROI (``roi[k]`` is the ROI of ``values[k]``), for
    ``n`` ROIs at once, **accumulated in float64** (the pixels are float32). Mean is NaN for a ROI without values, standard
    deviation for fewer than 2. Two-pass (the mean first), so there is no cancellation.

    The float64 sum of up to ~1000 float32 intensities is exact (it needs ~36 of float64's 53 bits), so the mean is the
    correctly rounded mean whatever the order of summation: this ``bincount`` version and an ``add.reduceat`` version
    give the same mean bit for bit (2026-10-19). ``bincount`` is used because it is one fast pass per sum."""
    counts = np.bincount(roi, minlength=n)
    mean = np.full(n, np.nan)
    std = np.full(n, np.nan)
    has_any = counts > 0
    if not has_any.any():
        return mean, std
    v = values.astype(np.float64)
    sums = np.bincount(roi, weights=v, minlength=n)
    mean[has_any] = sums[has_any] / counts[has_any]
    has_two = counts > 1
    if has_two.any():
        dev = v - mean[roi]
        ss = np.bincount(roi, weights=dev * dev, minlength=n)
        std[has_two] = np.sqrt(ss[has_two] / (counts[has_two] - 1))
    return mean, std


def segment_mean_std(values: np.ndarray, offsets: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """:func:`group_mean_std` for values stored as contiguous blocks (block ``i`` is ``values[offsets[i]:offsets[i + 1]]``)."""
    del offsets  # the blocks are contiguous and in ROI order, so the counts alone give each value's ROI
    return group_mean_std(values, np.repeat(np.arange(counts.size), counts), int(counts.size))


def segment_std(values: np.ndarray, offsets: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """The standard-deviation half of :func:`segment_mean_std`."""
    return segment_mean_std(values, offsets, counts)[1]


@dataclass(frozen=True)
class PixelLayout:
    """Where every ROI's pixels are, for one (ROI geometry, chromatic affine, image shape, exclusion mode): the output of
    :func:`locate_pixels` with the sample-aperture exclusion already applied. It depends on nothing else (not on pixel
    values, not on the ignore mask), so a run builds it once per distinct affine (in a real run one per wavelength, the
    same for every cube: 26 layouts for 8,164 frames) and reuses it. Reusing it never changes a result."""

    shape: tuple[int, int]
    n_rois: int
    exclusion_mode: str
    sample_roi: np.ndarray  # intp, sorted by ROI then pixel
    sample_flat: np.ndarray
    ring_roi: np.ndarray
    ring_flat: np.ndarray
    sample_count: np.ndarray  # pixels per ROI before the ignore mask and the validity test
    ring_count: np.ndarray
    needed_flat: np.ndarray  # every pixel any ROI reads, sorted, unique: what `apply_preprocessing_at` must compute
    sample_pos: np.ndarray  # position of each sample pixel in `needed_flat`
    ring_pos: np.ndarray

    @property
    def nbytes(self) -> int:
        return sum(a.nbytes for a in (self.sample_roi, self.sample_flat, self.ring_roi, self.ring_flat, self.needed_flat, self.sample_pos, self.ring_pos))

    def restrict(self, indices) -> PixelLayout:
        """The layout of only the ROIs at ``indices`` (row ``k`` of the result is ROI ``indices[k]``), cut out of this one, so the pixels each of
        them reads, and in the same order, are exactly the ones this layout holds: the reference-ring exclusion was already applied with every
        ROI's sample disk, so a restricted layout still leaves out the neighbours' pixels. ``needed_flat`` shrinks to what these ROIs read."""
        idx = np.asarray(tuple(indices), dtype=np.intp)
        if idx.size != np.unique(idx).size or (idx.size and (idx.min() < 0 or idx.max() >= self.n_rois)):
            raise ValueError(f"indices must be distinct ROI positions in 0..{self.n_rois - 1}")
        s_flat, s_count = _blocks(self.sample_flat, self.sample_count, idx)
        r_flat, r_count = _blocks(self.ring_flat, self.ring_count, idx)
        positions = np.arange(idx.size, dtype=np.intp)
        return _assemble_layout(
            self.shape, int(idx.size), self.exclusion_mode, np.repeat(positions, s_count), s_flat, np.repeat(positions, r_count), r_flat
        )


def _blocks(flat: np.ndarray, counts: np.ndarray, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The pixels of the ROIs at ``indices`` from arrays that hold each ROI's pixels as one block, ROI after ROI, ``counts[i]`` long."""
    offsets = np.concatenate(([0], np.cumsum(counts)))
    parts = [flat[offsets[i]:offsets[i + 1]] for i in indices]
    return (np.concatenate(parts) if parts else np.zeros(0, dtype=flat.dtype)), counts[indices]


def _assemble_layout(
    shape: tuple[int, int], n_rois: int, exclusion_mode: str, s_roi: np.ndarray, s_flat: np.ndarray, r_roi: np.ndarray, r_flat: np.ndarray
) -> PixelLayout:
    """A :class:`PixelLayout` from the final pixel lists (exclusion already applied): the pixels any ROI reads and where each sits in them."""
    # A marker array over the image is linear time; np.unique + searchsorted (sorting ~570 k indices) made a build 137 ms instead of ~15
    # (2026-10-19).
    mark = np.zeros(shape[0] * shape[1], dtype=bool)
    mark[s_flat] = True
    mark[r_flat] = True
    needed = np.flatnonzero(mark)
    where = np.empty(mark.size, dtype=np.intp)
    where[needed] = np.arange(needed.size, dtype=np.intp)
    # Native index type throughout: int32 halved the memory but numpy converts it back on every gather and bincount,
    # +4 ms per frame of 160 ROIs (measured 2026-10-19).
    return PixelLayout(
        shape, n_rois, exclusion_mode, s_roi, s_flat, r_roi, r_flat,
        np.bincount(s_roi, minlength=n_rois), np.bincount(r_roi, minlength=n_rois),
        needed, where[s_flat], where[r_flat],
    )


def build_layout(
    rs: RoiSet, affine: np.ndarray, shape: tuple[int, int],
    exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE, block: int = DEFAULT_BLOCK_ROIS,
) -> PixelLayout:
    if exclusion_mode not in REFERENCE_EXCLUSION_MODES:
        raise ValueError(f"unknown reference exclusion mode {exclusion_mode!r}; known: {REFERENCE_EXCLUSION_MODES}")
    shape = (int(shape[0]), int(shape[1]))
    s_roi, s_flat, r_roi, r_flat = locate_pixels(rs, affine, shape, block)
    if exclusion_mode == EXCLUDE_ALL_SAMPLE_ROIS:
        union = np.zeros(shape[0] * shape[1], dtype=bool)
        union[s_flat] = True  # every ROI's sample aperture, before the ignore mask (as tasks._sample_exclusion_union)
        keep = ~union[r_flat]
        r_roi, r_flat = r_roi[keep], r_flat[keep]
    return _assemble_layout(shape, rs.n, exclusion_mode, s_roi, s_flat, r_roi, r_flat)


class LayoutCache:
    """Recently used :class:`PixelLayout` objects, keyed by (ROI geometry key, affine, shape, exclusion mode). Safe to share
    between threads. ``max_entries`` bounds the memory: one layout of 160 ROIs is ~9 MB, and a run needs one per
    wavelength per ROI layout in use (26 here), so the default holds one ROI layout's set with room to spare."""

    def __init__(self, max_entries: int = 40, max_subset_entries: int = 64) -> None:
        self._max = max(int(max_entries), 1)
        self._max_subsets = max(int(max_subset_entries), 1)
        self._items: OrderedDict = OrderedDict()
        self._subsets: OrderedDict = OrderedDict()  # restricted layouts, kept apart so they never push the full ones out
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, rs: RoiSet, affine: np.ndarray, shape: tuple[int, int],
            exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE, block: int = DEFAULT_BLOCK_ROIS,
            subset: tuple[int, ...] | None = None) -> PixelLayout:
        """The layout of ``rs`` for this affine and shape. With ``subset`` (positions in ``rs``): the layout of just those ROIs, cut out of the
        full one (:meth:`PixelLayout.restrict`), cached per subset."""
        if subset is not None:
            affine_bytes = np.ascontiguousarray(affine, dtype=np.float64).tobytes()
            sub_key = (rs.key, affine_bytes, (int(shape[0]), int(shape[1])), exclusion_mode, tuple(int(i) for i in subset))
            with self._lock:
                cached = self._subsets.get(sub_key)
                if cached is not None:
                    self._subsets.move_to_end(sub_key)
                    return cached
            restricted = self.get(rs, affine, shape, exclusion_mode, block).restrict(subset)
            with self._lock:
                self._subsets[sub_key] = restricted
                while len(self._subsets) > self._max_subsets:
                    self._subsets.popitem(last=False)
            return restricted
        key = (rs.key, np.ascontiguousarray(affine, dtype=np.float64).tobytes(), (int(shape[0]), int(shape[1])), exclusion_mode)
        with self._lock:
            layout = self._items.get(key)
            if layout is not None:
                self._items.move_to_end(key)
                self.hits += 1
                return layout
            self.misses += 1
        layout = build_layout(rs, affine, shape, exclusion_mode, block)  # outside the lock; a rare duplicate build is harmless
        with self._lock:
            self._items[key] = layout
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return layout


@dataclass(frozen=True)
class FrameReduction:
    """Result of :func:`reduce_frame`. Arrays are ``(n_rois, 2)``; column ``SAMPLE`` (0) / ``REFERENCE`` (1)."""

    methods: tuple[str, ...]
    values: dict[str, np.ndarray]  # method -> float64
    std: np.ndarray  # float64, standard deviation of the valid pixels, ddof=1
    n_valid: np.ndarray  # int32
    n_nominal: np.ndarray  # int32
    n_masked: np.ndarray  # int32, aperture pixels the ignore mask removed (pixels before the mask minus n_nominal); 0 without a mask


def reduce_frame(
    rs: RoiSet,
    processed: np.ndarray | None,
    ignore: np.ndarray | None,
    affine: np.ndarray,
    methods: tuple[str, ...] = ("mean",),
    *,
    trimmed_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
    exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE,
    block: int = DEFAULT_BLOCK_ROIS,
    timers: dict[str, float] | None = None,
    layout: PixelLayout | None = None,
    layout_values: np.ndarray | None = None,
) -> FrameReduction:
    """Step 1 for one frame and all ROIs; see the module docstring for the rules.

    ``processed``: the frame after ``apply_preprocessing`` (2-D float, NaN where there is no measurement).
    ``ignore``: bool image of the same shape, True = leave out; ignored when ``None`` or of another shape (as in
    ``compute_cell``). ``timers``, if given, accumulates seconds per stage (diagnostics only). ``layout``: a
    :class:`PixelLayout` for this ROI set, affine, shape and mode (from a :class:`LayoutCache`), so the pixels are not
    located again; the result is the same with or without it. ``layout_values`` (with ``processed=None`` and a
    ``layout``): the processed values at ``layout.needed_flat`` only, as `apply_preprocessing_at` returns them, instead
    of a whole processed frame; same result.

    ``mean`` is float64 (the one numeric difference from ``compute_cell``, see the module docstring); every other method
    gives the values ``compute_cell`` gives."""
    unknown = [m for m in methods if m not in REDUCTION_METHODS]
    if unknown:
        raise ValueError(f"unknown reduction method(s) {unknown}; known: {REDUCTION_METHODS}")
    if exclusion_mode not in REFERENCE_EXCLUSION_MODES:
        raise ValueError(f"unknown reference exclusion mode {exclusion_mode!r}; known: {REFERENCE_EXCLUSION_MODES}")
    clock = time.perf_counter
    t0 = clock()
    if processed is None:
        if layout is None or layout_values is None:
            raise ValueError("without a processed frame, a layout and its layout_values are needed")
        if layout_values.shape != layout.needed_flat.shape:
            raise ValueError("layout_values must hold one value per pixel of layout.needed_flat")
        shape = layout.shape
    else:
        shape = processed.shape[:2]
    n = rs.n
    if n == 0:
        empty = np.zeros((0, 2))
        return FrameReduction(tuple(methods), {m: empty.copy() for m in methods}, empty.copy(),
                              np.zeros((0, 2), dtype=np.int32), np.zeros((0, 2), dtype=np.int32), np.zeros((0, 2), dtype=np.int32))
    if layout is None:
        layout = build_layout(rs, affine, shape, exclusion_mode, block)
    elif layout.shape != (int(shape[0]), int(shape[1])) or layout.n_rois != n or layout.exclusion_mode != exclusion_mode:
        raise ValueError("the pixel layout was built for another image shape, ROI count or exclusion mode")
    s_roi, s_flat, r_roi, r_flat = layout.sample_roi, layout.sample_flat, layout.ring_roi, layout.ring_flat
    t1 = clock()
    if processed is None:
        s_val = layout_values[layout.sample_pos]
        r_val = layout_values[layout.ring_pos]
    else:
        flat_image = processed.reshape(-1)
        s_val = flat_image[s_flat]
        r_val = flat_image[r_flat]
    s_ok = np.isfinite(s_val)
    r_ok = np.isfinite(r_val)
    if ignore is not None and ignore.shape == shape:
        flat_ignore = ignore.reshape(-1)
        s_in = ~flat_ignore[s_flat]
        r_in = ~flat_ignore[r_flat]
        n_nominal = np.stack((np.bincount(s_roi[s_in], minlength=n), np.bincount(r_roi[r_in], minlength=n)), axis=1)
        s_ok &= s_in
        r_ok &= r_in
    else:
        n_nominal = np.stack((layout.sample_count, layout.ring_count), axis=1)
    n_masked = np.stack((layout.sample_count, layout.ring_count), axis=1) - n_nominal  # the link between a mask and a ROI, kept per cell
    if not s_ok.all():  # skip the copies in the common case where every pixel counts
        s_roi, s_val = s_roi[s_ok], s_val[s_ok]
    if not r_ok.all():
        r_roi, r_val, r_flat = r_roi[r_ok], r_val[r_ok], r_flat[r_ok]
    n_valid = np.stack((np.bincount(s_roi, minlength=n), np.bincount(r_roi, minlength=n)), axis=1)
    s_off = np.concatenate(([0], np.cumsum(n_valid[:, SAMPLE])))
    r_off = np.concatenate(([0], np.cumsum(n_valid[:, REFERENCE])))
    r_xx = r_yy = None
    if "plane_fit" in methods:
        r_xx = (r_flat % shape[1]).astype(np.float64)
        r_yy = (r_flat // shape[1]).astype(np.float64)
    t2 = clock()

    s_mean, s_std = group_mean_std(s_val, s_roi, n)
    r_mean, r_std = group_mean_std(r_val, r_roi, n)
    std = np.stack((s_std, r_std), axis=1)
    t3 = clock()
    values = {m: np.full((n, 2), np.nan) for m in methods}
    for method in methods:
        out = values[method]
        if method == "mean":  # float64, all ROIs at once (see the module docstring); no per-ROI loop
            both = (n_valid[:, SAMPLE] > 0) & (n_valid[:, REFERENCE] > 0)
            out[both, SAMPLE] = s_mean[both]
            out[both, REFERENCE] = r_mean[both]
            continue
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
            ("find pixels (or cached layout)", t1 - t0), ("exclude + gather + validity", t2 - t1), ("mean + std", t3 - t2),
            (f"reduce {'+'.join(methods)}", t4 - t3),
        ):
            timers[key] = timers.get(key, 0.0) + seconds
    return FrameReduction(tuple(methods), values, std, n_valid.astype(np.int32), n_nominal.astype(np.int32), n_masked.astype(np.int32))
