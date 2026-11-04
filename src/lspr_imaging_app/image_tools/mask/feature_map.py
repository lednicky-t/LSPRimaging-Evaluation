"""A map of every small feature in the image, sorted into four kinds, without ROIs (2026-11-03).

Maintainer (2026-11-03): map all dirt, also where it does not touch a ROI, so that moving or changing ROIs never needs the mask
again; tell dirt from damaged nanostructure, dark from bright, each kind switchable. Supersedes the ROI-based finders of the same
day. Record and real-data results: ``docs/roi_feature_detection_2026-10-29.md`` s.10.

**Input**: log intensity at every wavelength (W, H, W'), all aligned on one frame's pixels (chromatic correction applied by the
caller), geometry applied, no background flattening (this module takes the shading out itself). Usually a mean of several cubes:
the features are static.

**Steps**
1. ``find_disks``: the nanostructure disks are found in the picture (dark, round, of one size), not taken from the ROIs; each centre
   is refined by the radial fit of ``roi_features.fit_patch``.
2. Per wavelength: shading (``shading``: a local plane through block medians) and every disk's own round model (the median over all
   directions at each distance from its centre, from the pixels nearest to it; its own edge sharpness and halo), twice in turn, so
   neither takes part of the other. The deviation from both is fitted per pixel over the wavelengths as ``g + s * c(wl)``, ``c`` the
   disk model at that pixel: ``g`` = **dirt** (flat over wavelength, ``exp(g) - 1`` is the fraction of light), ``s`` = **change of
   nanostructure** as a fraction of its normal contrast (only on the disks, where ``c`` has a spectral shape).
3. Score: each map divided by its robust spread at that distance from the nearest disk (pooled over all disks: the edge is noisier
   than the flat background). A feature needs ``z >= certainty`` and the physical size (``dirt_min``, ``structure_min``), extends
   down to ``extent`` of both. Kinds: ``dirt_dark``, ``dirt_bright``, ``structure_missing`` (bright), ``structure_extra`` (dark).

Pure numpy / scipy, no Qt, no ROI model.
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

from ...progress import Cancelled
from . import roi_features as rf

DIRT_DARK = "dirt_dark"
DIRT_BRIGHT = "dirt_bright"
STRUCTURE_MISSING = "structure_missing"  # less nanostructure than normal: looks brighter
STRUCTURE_EXTRA = "structure_extra"  # more (or denser) nanostructure: looks darker
DIRT_FAINT = "dirt_faint"  # dark dirt that dims gently (under `faint_slope` per pixel): faint specks, broad soft shadows of dust out of focus
_DETECTED = (DIRT_DARK, DIRT_BRIGHT, STRUCTURE_MISSING, STRUCTURE_EXTRA)  # searched for; faint dirt is dark dirt sorted out after
KINDS = (*_DETECTED, DIRT_FAINT)
KIND_LABELS = {
    DIRT_DARK: "Dirt, dark",
    DIRT_BRIGHT: "Dirt, bright",
    STRUCTURE_MISSING: "Nanostructure missing (bright)",
    STRUCTURE_EXTRA: "Nanostructure extra (dark)",
    DIRT_FAINT: "Dirt, faint (gentle)",
}
_KIND_MAP = {DIRT_DARK: ("grey", -1), DIRT_BRIGHT: ("grey", +1), STRUCTURE_MISSING: ("structure", -1), STRUCTURE_EXTRA: ("structure", +1), DIRT_FAINT: ("grey", -1)}
_CROSS4 = ndimage.generate_binary_structure(2, 1)
_BIN_PX = 0.5


@dataclass(frozen=True)
class FeatureMapSettings:
    dirt_min: float = 0.02  # dirt must take (or add) at least this fraction of the light (2 %)
    structure_min: float = 0.10  # nanostructure must differ by at least this fraction of its normal contrast (10 %)
    certainty: float = 5.0  # score of a seed pixel (units of the spread of that map at that distance from a disk)
    extent: float = 0.6  # a feature extends over neighbours reaching this share of both limits ...
    core_share: float = 0.3  # ... and at least this share of its own peak (a particle, not its faint shadow; 0 = off)
    seed_px: int = 2
    min_area_px: int = 4
    structure_max_share: float = 0.6  # a nanostructure feature covers at most this share of its disk (more: the disk is different)
    block_px: int = 8  # shading: median per block ...
    window_blocks: int = 5  # ... and a local plane over this many blocks (5 x 8 = 40 px: broader shading is taken out)
    edge_margin_px: int = 3  # the last pixels at the border of the data are left out (blurred by the rotation's interpolation)
    unlit_level: float = 0.3  # an area connected to the border below this share of the bright level is unlit (no data)
    border_zone_px: int = 15  # nanostructure is judged from here inwards; a feature with `border_share` of its pixels this close ...
    border_share: float = 0.5  # ... and is long (3 x the zone) follows the border: the field's dark edge or corner, not dirt (a
    # particle cut by the border, or a scratch that runs into it, stays)
    grow_px: int = 1
    disk_contrast: float = -0.15  # a disk pixel is at least this much darker (log) than the bright level around it
    halo_factor: float = 2.0  # a disk's model reaches this many disk radii from its centre
    min_contrast_share: float = 0.25  # nanostructure is measured where c(wl) varies at least this share of a disk centre's
    # a dark dirt feature whose steepest step is below this (fraction of light per pixel) is faint dirt. The optics blur every edge
    # over ~5 px, so this mostly says how deep it is: measured 2026-11-03, the broad soft shadows 0.6-1.6 %/px, faint specks ~1 %/px,
    # particles 3.5-15 %/px, grey inside damaged disks 2-3.3 %/px
    faint_slope: float = 0.017
    # which kinds go into the combined mask: not faint dirt by default (a static grey dimming of a few % moves no peak and
    # adds no noise; masking one costs a disk's worth of pixels)
    kinds: frozenset[str] = frozenset(_DETECTED)

    def limit(self, kind: str) -> float:
        return self.dirt_min if _KIND_MAP[kind][0] == "grey" else self.structure_min


@dataclass(frozen=True)
class Disk:
    centre: tuple[float, float]  # (x, y) pixels
    radius: float
    cut: bool = False  # cut by the border of the data: its centre is the lattice point (or a circle fit), not a radial fit


@dataclass(frozen=True)
class MapFeature:
    kind: str
    area_px: int
    peak_z: float
    size: float  # median over the feature: fraction of light (dirt) or of the normal contrast (nanostructure), signed
    centroid: tuple[float, float]  # (x, y) pixels
    disk: int  # index of the nearest disk
    box: tuple[int, int, int, int]  # y0, y1, x0, x1
    mask: np.ndarray = field(repr=False)  # bool, the box only


@dataclass(frozen=True)
class FeatureMap:
    disks: tuple[Disk, ...]
    features: tuple[MapFeature, ...]
    kind_masks: dict[str, np.ndarray]  # every kind (image-sized, grown), enabled or not
    mask: np.ndarray  # union of the enabled kinds
    grey: np.ndarray = field(repr=False)  # log dirt factor per pixel
    structure: np.ndarray = field(repr=False)  # nanostructure change per pixel (NaN off the disks)
    grey_z: np.ndarray = field(repr=False)
    structure_z: np.ndarray = field(repr=False)


# -- shading --------------------------------------------------------------------------------------------------------------


def _local_plane(coarse: np.ndarray, window: int) -> np.ndarray:
    """At each cell, the least-squares plane through the finite cells of its `window` x `window` neighbourhood, evaluated there. A
    plane, not a median: next to a disk the neighbourhood is one-sided, and on a slope a median would then be biased."""
    w = np.isfinite(coarse).astype(np.float64)
    v = np.where(w > 0, coarse, 0.0)
    half = window // 2
    dy, dx = np.mgrid[-half : half + 1, -half : half + 1].astype(np.float64)

    def box(a: np.ndarray, kernel: np.ndarray) -> np.ndarray:
        return ndimage.correlate(a, kernel, mode="constant", cval=0.0)

    one = np.ones_like(dx)
    s1, sx, sy = box(w, one), box(w, dx), box(w, dy)
    sxx, sxy, syy = box(w, dx * dx), box(w, dx * dy), box(w, dy * dy)
    sv, sxv, syv = box(v, one), box(v, dx), box(v, dy)
    a = np.stack([np.stack([s1, sx, sy], -1), np.stack([sx, sxx, sxy], -1), np.stack([sy, sxy, syy], -1)], -2)
    b = np.stack([sv, sxv, syv], -1)
    out = np.full(coarse.shape, np.nan)
    ok = (s1 >= 6) & (np.abs(np.linalg.det(a)) > 1e-6)
    out[ok] = np.linalg.solve(a[ok], b[ok][..., None])[:, 0, 0]
    flat = (s1 >= 1) & ~ok  # too few cells, or all in a line: their mean
    out[flat] = sv[flat] / s1[flat]
    return out


def shading(values: np.ndarray, use: np.ndarray, block_px: int, window_blocks: int, reject_floor: float = 0.005) -> np.ndarray:
    """Smooth shading under `values` (H, W) from the pixels in `use`: the median of each block, a local plane through the blocks
    (once more without blocks farther than 3 robust spreads, never closer than `reject_floor`, from it), interpolated to every pixel."""
    h, w = values.shape
    bh, bw = -(-h // block_px), -(-w // block_px)
    padded = np.full((bh * block_px, bw * block_px), np.nan)
    padded[:h, :w] = np.where(use, values, np.nan)
    blocks = padded.reshape(bh, block_px, bw, block_px).transpose(0, 2, 1, 3).reshape(bh, bw, -1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks
        coarse = np.nanmedian(blocks, axis=2)
    coarse[np.isfinite(blocks).sum(axis=2) < block_px * block_px // 4] = np.nan  # a block mostly cut away says little
    smooth = _local_plane(coarse, window_blocks)
    known = np.isfinite(coarse) & np.isfinite(smooth)
    if known.sum() > 20:
        r = (coarse - smooth)[known]
        mad = 1.4826 * float(np.median(np.abs(r - np.median(r))))
        smooth = _local_plane(np.where(np.abs(coarse - smooth) <= max(3.0 * mad, reject_floor), coarse, np.nan), window_blocks)
    missing = ~np.isfinite(smooth)
    if missing.all():
        return np.zeros_like(values)
    if missing.any():
        idx = ndimage.distance_transform_edt(missing, return_distances=False, return_indices=True)
        smooth = smooth[tuple(idx)]
    yy, xx = np.indices((h, w), dtype=np.float64)
    return ndimage.map_coordinates(smooth, [(yy + 0.5) / block_px - 0.5, (xx + 0.5) / block_px - 0.5], order=1, mode="nearest")


# -- disks ----------------------------------------------------------------------------------------------------------------


def find_disks(mean_log: np.ndarray, settings: FeatureMapSettings = FeatureMapSettings()) -> tuple[Disk, ...]:
    """The nanostructure disks in the picture: pixels clearly darker than the bright level around them, in round blobs of the
    common size; the centre refined by a radial fit (a speck of dirt on the edge does not pull it)."""
    valid = np.isfinite(mean_log)
    values = np.where(valid, mean_log, np.nan)
    b = 8
    h, w = values.shape
    bh, bw = -(-h // b), -(-w // b)
    padded = np.full((bh * b, bw * b), np.nan)
    padded[:h, :w] = values
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        coarse = np.nanpercentile(padded.reshape(bh, b, bw, b).transpose(0, 2, 1, 3).reshape(bh, bw, -1), 90, axis=2)
    coarse = np.where(np.isfinite(coarse), coarse, np.nanmedian(coarse))
    bright = ndimage.maximum_filter(ndimage.median_filter(coarse, size=3), size=9)  # the bright level within ~70 px
    yy, xx = np.indices((h, w), dtype=np.float64)
    bright_full = ndimage.map_coordinates(bright, [(yy + 0.5) / b - 0.5, (xx + 0.5) / b - 0.5], order=1, mode="nearest")
    dark = valid & (values - bright_full < settings.disk_contrast)
    dark = ndimage.binary_opening(dark, structure=np.ones((5, 5), dtype=bool))  # thin lines of dirt are not disks
    labels, count = ndimage.label(dark)
    if not count:
        return ()
    areas = ndimage.sum_labels(dark, labels, np.arange(1, count + 1))
    big = areas[areas >= 200]
    if big.size == 0:
        return ()
    typical = float(np.median(big))
    radius = float(np.sqrt(typical / np.pi))
    centroids = ndimage.center_of_mass(dark, labels, np.arange(1, count + 1))
    disks = []
    intensity = np.exp(np.where(valid, mean_log, np.nanmedian(mean_log)))
    no_data = ndimage.binary_dilation(~valid, iterations=2)
    for label, area, (cy, cx) in zip(range(1, count + 1), areas, centroids):
        if area > 2.5 * typical:
            continue
        blob = labels == label
        if (blob & no_data).any():
            # a disk cut by the border of the data: its centroid is off; a circle of the common radius on its visible edge (on an
            # array it then takes its lattice point)
            if area < 0.1 * typical:
                continue
            cx, cy = _circle_on_edge(blob & ~ndimage.binary_erosion(blob) & ~no_data, radius, (cx, cy))
            disks.append(Disk((float(cx), float(cy)), radius, cut=True))
            continue
        if area < 0.4 * typical:
            continue
        half = int(np.ceil(radius * 1.6)) + 4
        y0, y1 = max(int(cy) - half, 0), min(int(cy) + half + 1, h)
        x0, x1 = max(int(cx) - half, 0), min(int(cx) + half + 1, w)
        win, ok = intensity[y0:y1, x0:x1], valid[y0:y1, x0:x1]
        try:
            fit = rf.fit_patch(np.where(ok, win, 0.0), ok, (cx - x0, cy - y0), radius * 1.4)
        except (IndexError, ValueError):  # the fit wandered off (not a round disk): keep the centroid
            fit = None
        if fit is not None and np.hypot(fit.centre[0] + x0 - cx, fit.centre[1] + y0 - cy) < radius * 0.3:
            cx, cy = fit.centre[0] + x0, fit.centre[1] + y0
        disks.append(Disk((float(cx), float(cy)), radius))
    return _complete_on_lattice(disks, values, bright_full, valid, radius, settings)


def _complete_on_lattice(
    disks: list[Disk], values: np.ndarray, bright: np.ndarray, valid: np.ndarray, radius: float, settings: FeatureMapSettings
) -> tuple[Disk, ...]:
    """When the disks form an array: a found "disk" off the lattice is a smudge of dirt (dropped, so it is reported as dirt), and a
    dark lattice point without a disk is one that merged with dirt or with the dark border of the data (added at the lattice point)."""
    if not disks:
        return ()
    centres = np.array([d.centre for d in disks])
    whole = np.array([not d.cut for d in disks])
    lattice = _lattice(centres[whole])  # fitted to whole disks only: a cut disk's own centre can be pixels off
    if lattice is None:
        return tuple(disks)
    origin, basis, _ = lattice
    resid = np.hypot(*(centres - (origin + np.rint(np.linalg.solve(basis, (centres - origin).T).T) @ basis.T)).T)
    pitch = float(min(np.hypot(*basis[:, 0]), np.hypot(*basis[:, 1])))
    on = resid < 0.25 * pitch
    k = np.rint(np.linalg.solve(basis, (centres - origin).T).T).astype(int)
    h, w = values.shape

    def dark_at(cx: float, cy: float) -> bool:
        # a disk there: enough data within 0.7 radius of the point, and clearly darker than the bright level around it
        y0, y1 = max(int(cy - radius), 0), min(int(cy + radius) + 2, h)
        x0, x1 = max(int(cx - radius), 0), min(int(cx + radius) + 2, w)
        if y1 <= y0 or x1 <= x0:
            return False
        yy, xx = np.indices((y1 - y0, x1 - x0), dtype=np.float64)
        sel = valid[y0:y1, x0:x1] & (np.hypot(xx + x0 - cx, yy + y0 - cy) <= 0.7 * radius)
        return int(sel.sum()) >= 15 and float(np.median((values - bright)[y0:y1, x0:x1][sel])) < settings.disk_contrast

    kept = []
    taken: set[tuple[int, int]] = set()
    for d, ok, kk in sorted(zip(disks, on, k), key=lambda t: t[0].cut):  # whole disks first: one disk per lattice point
        if not ok or tuple(kk) in taken:
            continue
        if d.cut:  # a cut disk takes its lattice point (a circle fit to a cut, soft edge can be pixels off), if that is dark
            cx, cy = origin + basis @ kk.astype(np.float64)
            if not dark_at(cx, cy):
                continue
            d = Disk((float(cx), float(cy)), radius, cut=True)
        taken.add(tuple(kk))
        kept.append(d)
    have = taken
    span = (int(k[:, 0].max() - k[:, 0].min()) + 3) * (int(k[:, 1].max() - k[:, 1].min()) + 3)
    if span > 4 * len(disks) + 100:  # the lattice does not describe these disks (a guard: never thousands of points)
        return tuple(disks)
    for i in range(int(k[:, 0].min()) - 1, int(k[:, 0].max()) + 2):
        for j in range(int(k[:, 1].min()) - 1, int(k[:, 1].max()) + 2):
            if (i, j) in have:
                continue
            cx, cy = origin + basis @ np.array([i, j], dtype=np.float64)
            if dark_at(cx, cy):
                kept.append(Disk((float(cx), float(cy)), radius, cut=True))
    return tuple(kept)


def refine_centres(mean_log: np.ndarray, disks: tuple[Disk, ...], shade: np.ndarray) -> tuple[Disk, ...]:
    """The radial fit of each whole disk once more, on the picture without its shading (`mean_log - shade`): where the light falls
    off across a disk (the field's edge) the fit on the raw picture is pulled sideways by a fraction of a pixel, which at the steep
    disk edge shows up as a light / dark pair."""
    valid = np.isfinite(mean_log)
    intensity = np.exp(np.where(valid, mean_log - shade, 0.0))
    h, w = mean_log.shape
    out = []
    for d in disks:  # a cut disk starts from its lattice point; where enough of it has data (fit_patch: 60 %) it is refined too
        cx, cy = d.centre
        half = int(np.ceil(d.radius * 1.6)) + 4
        y0, y1 = max(int(cy) - half, 0), min(int(cy) + half + 1, h)
        x0, x1 = max(int(cx) - half, 0), min(int(cx) + half + 1, w)
        ok = valid[y0:y1, x0:x1]
        try:
            fit = rf.fit_patch(np.where(ok, intensity[y0:y1, x0:x1], 0.0), ok, (cx - x0, cy - y0), d.radius * 1.4)
        except (IndexError, ValueError):  # the fit wandered off (a badly damaged disk): keep the first centre
            fit = None
        if fit is not None and np.hypot(fit.centre[0] + x0 - cx, fit.centre[1] + y0 - cy) < 1.5:  # a correction, not a new place
            d = Disk((float(fit.centre[0] + x0), float(fit.centre[1] + y0)), d.radius, cut=d.cut)
        out.append(d)
    return tuple(out)


def _lattice(centres: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Origin and two step vectors of the array the centres sit on, and each centre's distance from its lattice point; None when the
    centres do not form a clear lattice (then the found disks are used as they are)."""
    n = len(centres)
    if n < 12:
        return None
    dist = np.hypot(centres[:, None, 0] - centres[None, :, 0], centres[:, None, 1] - centres[None, :, 1])
    np.fill_diagonal(dist, np.inf)
    pitch = float(np.median(dist.min(axis=1)))
    steps = []
    for i in range(n):
        for j in np.argsort(dist[i])[:4]:
            if dist[i, j] < 1.3 * pitch:
                steps.append(centres[j] - centres[i])
    steps_arr = np.array(steps)
    if steps_arr.size == 0:
        return None
    angle = np.mod(np.arctan2(steps_arr[:, 1], steps_arr[:, 0]), np.pi)  # direction without orientation
    main = 0.5 * float(np.angle(np.mean(np.exp(2j * angle))))  # the mean of doubled angles: no wrap-around at 0 / 180 deg
    first = np.abs(np.angle(np.exp(2j * (angle - main)))) < np.radians(30)  # within 15 deg of the main direction

    def oriented_median(vectors: np.ndarray) -> np.ndarray:
        # each step points along its group's mean direction before the median: a sign rule on x would split near-vertical steps
        # into up and down (their median ~0; found 2026-11-03 on a synthetic array)
        ref = np.exp(1j * 0.5 * np.angle(np.mean(np.exp(2j * np.arctan2(vectors[:, 1], vectors[:, 0])))))
        sign = np.sign(vectors[:, 0] * ref.real + vectors[:, 1] * ref.imag)
        return np.median(vectors * np.where(sign == 0, 1.0, sign)[:, None], axis=0)

    if first.sum() < 5 or (~first).sum() < 5:
        return None
    a, b = oriented_median(steps_arr[first]), oriented_median(steps_arr[~first])
    basis = np.column_stack([a, b])
    if abs(float(np.linalg.det(basis))) < 0.5 * pitch * pitch or min(np.hypot(*a), np.hypot(*b)) < 0.7 * pitch:
        return None  # nearly parallel or too short: not a clear 2-D array
    origin = centres[np.argmin(np.hypot(*(centres - np.median(centres, axis=0)).T))]
    k = np.rint(np.linalg.solve(basis, (centres - origin).T).T)
    for _ in range(2):  # least squares on the centres that sit on it
        design = np.column_stack([np.ones(n), k[:, 0], k[:, 1]])
        resid = np.hypot(*(centres - (origin + k @ basis.T)).T)
        good = resid < 0.25 * pitch
        if good.sum() < 12:
            return None
        coef = np.linalg.lstsq(design[good], centres[good], rcond=None)[0]
        origin, basis = coef[0], coef[1:].T
        k = np.rint(np.linalg.solve(basis, (centres - origin).T).T)
    resid = np.hypot(*(centres - (origin + k @ basis.T)).T)
    if np.mean(resid < 0.25 * pitch) < 0.8:  # most found disks must sit on it, or it is not an array
        return None
    return origin, basis, resid


def _circle_on_edge(edge: np.ndarray, radius: float, start: tuple[float, float]) -> tuple[float, float]:
    """Centre of the circle of `radius` that best follows the pixels of `edge` (least squares on the distance to the circle)."""
    ys, xs = np.nonzero(edge)
    if xs.size < 10:
        return start

    def cost(c: np.ndarray) -> np.ndarray:
        return np.hypot(xs - c[0], ys - c[1]) - radius

    return tuple(optimize.least_squares(cost, np.array(start, dtype=np.float64)).x)  # type: ignore[return-value]


def nearest_disk(shape: tuple[int, int], disks: tuple[Disk, ...]) -> tuple[np.ndarray, np.ndarray]:
    """For every pixel: the index of the nearest disk and the distance to its centre."""
    seeds = np.zeros(shape, dtype=np.int32)
    for i, d in enumerate(disks):
        x, y = int(round(d.centre[0])), int(round(d.centre[1]))
        if 0 <= y < shape[0] and 0 <= x < shape[1]:
            seeds[y, x] = i + 1
    idx = ndimage.distance_transform_edt(seeds == 0, return_distances=False, return_indices=True)
    which = seeds[idx[0], idx[1]] - 1
    cx = np.array([d.centre[0] for d in disks])[which]
    cy = np.array([d.centre[1] for d in disks])[which]
    yy, xx = np.indices(shape, dtype=np.float64)
    return which, np.hypot(xx - cx, yy - cy)


def _half_sample_mode(v: np.ndarray) -> float:
    """The most common value of sorted `v` (the half-sample mode: keep the half with the smallest range, repeat). Unlike a median it
    stays on the larger cluster when a big part of the values is shifted (a band of missing nanostructure through a disk's centre)."""
    while v.size > 3:
        h = (v.size + 1) // 2
        i = int(np.argmin(v[h - 1 :] - v[: v.size - h + 1]))
        v = v[i : i + h]
    return float(np.mean(v)) if v.size else 0.0


def _mode_per_disk(values: np.ndarray, ids: np.ndarray, n: int) -> np.ndarray:
    """`_half_sample_mode` of `values` per disk index in `ids` (0 for a disk without values)."""
    order = np.lexsort((values, ids))
    values, ids = values[order], ids[order]
    uniq, start, count = np.unique(ids, return_index=True, return_counts=True)
    out = np.zeros(n)
    for u, s0, c in zip(uniq, start, count):
        out[u] = _half_sample_mode(values[s0 : s0 + c])
    return out


def _local_level(level: np.ndarray, centres: np.ndarray, near: np.ndarray, tolerance: float = 0.05) -> np.ndarray:
    """Per disk: its own `level`, unless that is far (more than `tolerance`, or 3 robust spreads of the neighbours) from a plane through
    the levels of its nearest disks (`near`; fitted, then again without the ones far off it); then the plane's value. A heavily damaged
    disk's own level is wrong, a normal disk's is right, and its neighbours may be the damaged ones."""
    out = level.copy()
    for i, idx in enumerate(near):
        idx = idx[idx != i]  # the disk itself is not its own reference
        if idx.size < 4:
            continue
        d = centres[idx] - centres[i]
        design = np.column_stack([np.ones(idx.size), d[:, 0], d[:, 1]])
        v = level[idx]
        # obvious outliers (a damaged neighbour) out before the first fit, by their distance from the neighbours' median: one of them
        # would tilt a least-squares plane, most of all where it is extrapolated (the field's corners)
        mad0 = 1.4826 * float(np.median(np.abs(v - np.median(v))))
        use = np.abs(v - np.median(v)) <= max(3.0 * mad0, tolerance)
        coef = np.array([np.median(v), 0.0, 0.0])
        for _ in range(2):
            if use.sum() < 4:
                break
            coef = np.linalg.lstsq(design[use], v[use], rcond=None)[0]
            r = v - design @ coef
            mad = 1.4826 * float(np.median(np.abs(r[use] - np.median(r[use]))))
            use = np.abs(r) <= max(3.0 * mad, 0.01)
        r = v - design @ coef
        spread = 1.4826 * float(np.median(np.abs(r[use] - np.median(r[use])))) if use.any() else 0.0
        if abs(level[i] - coef[0]) > max(3.0 * spread, tolerance):
            out[i] = coef[0]
    return out


class _RadialModel:
    """Each disk's round model of a picture, as a function of the distance from its centre (0.5 px bins, linear between, zero
    beyond `reach`). At the edge and in the halo (from `radius - edge_px` out) it is the disk's own profile: the median over its
    nearest pixels at that distance (its own focus: the edge is softer where the image is less sharp). Inside it is the median of the
    profiles of its `neighbours` nearest disks: a disk with half its nanostructure gone would otherwise pull its own model halfway
    and its intact half would look like extra nanostructure (ROI 76 of the 2026-10-22 dataset)."""

    def __init__(
        self, which: np.ndarray, r: np.ndarray, valid: np.ndarray, disks: tuple[Disk, ...], reach: float, edge_px: float = 6.0, neighbours: int = 9
    ) -> None:
        n_disks = len(disks)
        centres = np.array([d.centre for d in disks])
        dist = np.hypot(centres[:, None, 0] - centres[None, :, 0], centres[:, None, 1] - centres[None, :, 1])
        self.near = np.argsort(dist, axis=1)[:, : min(neighbours, n_disks)]
        self.centres = centres
        radius = float(np.median([d.radius for d in disks]))
        radii = (np.arange(int(reach / _BIN_PX) + 2) + 0.5) * _BIN_PX
        self.own_weight = np.clip((radii - (radius - edge_px)) / 4.0, 0.0, 1.0)  # 0 inside, 1 from the edge out, blended over 4 px
        self.inside = valid & (r <= reach)
        self.n_bins = int(reach / _BIN_PX) + 2
        self.key = which[self.inside].astype(np.int64) * self.n_bins + (r[self.inside] / _BIN_PX).astype(np.int64)
        t = r / _BIN_PX - 0.5
        self.b0 = np.clip(np.floor(t).astype(np.int64), 0, self.n_bins - 2)
        self.frac = np.clip(t - self.b0, 0.0, 1.0)
        self.which, self.n_disks, self.reach, self.r = which, n_disks, reach, r
        self.core = self.inside & (r < radius - edge_px)  # the disk's inner part, where its level is measured

    def __call__(self, picture: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
        use = self.inside & np.isfinite(picture) if exclude is None else self.inside & np.isfinite(picture) & ~exclude
        sel = use[self.inside]
        keys, vals = self.key[sel], picture[self.inside][sel]
        order = np.lexsort((vals, keys))
        keys, vals = keys[order], vals[order]
        uniq, start, count = np.unique(keys, return_index=True, return_counts=True)
        med = 0.5 * (vals[start + (count - 1) // 2] + vals[start + count // 2])
        table = np.full(self.n_disks * self.n_bins, np.nan)
        table[uniq] = med
        table = table.reshape(self.n_disks, self.n_bins)
        centres = (np.arange(self.n_bins) + 0.5) * _BIN_PX
        for i in range(self.n_disks):  # bins without pixels: linear between their neighbours
            row = table[i]
            seen = np.isfinite(row)
            table[i] = np.interp(centres, centres[seen], row[seen]) if seen.any() else 0.0
        local = np.median(table[self.near], axis=1)  # (n_disks, n_bins): the profile of the disks around each one
        # ... at the level this disk should have: where the light is weaker (the field's corners) a disk is deeper in log. Each disk's
        # own level (median of own - neighbours over the inner distances) is not trusted alone (a disk with half its nanostructure gone
        # moves it): the level is a robust local plane through the levels of the disks around it, evaluated at this one
        core = self.core & use
        if core.any():
            ids = self.which[core]
            b0, fr = self.b0[core], self.frac[core]
            diff = picture[core] - ((1.0 - fr) * local[ids, b0] + fr * local[ids, b0 + 1])
            own_level = _mode_per_disk(diff, ids, self.n_disks)
            local = local + _local_level(own_level, self.centres, self.near)[:, None]
        table = self.own_weight * table + (1.0 - self.own_weight) * local
        model = (1.0 - self.frac) * table[self.which, self.b0] + self.frac * table[self.which, self.b0 + 1]
        return np.where(self.r <= self.reach, model, 0.0)


# -- the map --------------------------------------------------------------------------------------------------------------


def _spread_by_distance(values: np.ndarray, use: np.ndarray, bins: np.ndarray, n_bins: int, floor: float) -> np.ndarray:
    """Robust spread of `values` per distance bin (pooled over all disks), lightly smoothed over the bins, as a per-pixel image."""
    out = np.full(n_bins + 1, np.nan)
    b = bins[use]
    v = values[use]
    order = np.lexsort((v, b))
    b, v = b[order], v[order]
    uniq, start, count = np.unique(b, return_index=True, return_counts=True)
    for u, s, c in zip(uniq, start, count):
        if c >= 30:
            seg = v[s : s + c]
            out[u] = 1.4826 * float(np.median(np.abs(seg - np.median(seg))))
    seen = np.isfinite(out)
    if not seen.any():
        return np.full(values.shape, 1.0)
    idx = np.arange(out.size)
    out = np.interp(idx, idx[seen], out[seen])
    out[:-1] = ndimage.median_filter(out[:-1], size=3, mode="nearest")
    return np.maximum(out, floor)[bins]


def _outliers(
    mean_log: np.ndarray, valid: np.ndarray, far: np.ndarray, model: "_RadialModel", bins: np.ndarray, n_bins: int, settings: FeatureMapSettings
) -> np.ndarray:
    """Pixels that clearly stand out of the wavelength-averaged picture (3 spreads and half the dirt limit), grown by 2 px: left out
    of every shading and disk model, so that a large dark smudge does not pull them (that gave it a bright rim)."""
    values = np.where(valid, mean_log, 0.0)
    sh = shading(values, far, settings.block_px, 2 * settings.window_blocks - 1)
    disk = model(mean_log - sh)
    sh = shading(values - disk, valid, settings.block_px, settings.window_blocks)
    res = np.where(valid, mean_log - sh - model(mean_log - sh), 0.0)
    spread = _spread_by_distance(res, valid, bins, n_bins, 1e-3)
    out = valid & (np.abs(res) > 3.0 * spread) & (np.abs(res) > 0.5 * settings.dirt_min)
    return ndimage.binary_dilation(out, iterations=2)


def _boxes_touch(a: "MapFeature", b: "MapFeature", gap: int = 2) -> bool:
    """Whether the pixels of two features come within `gap` px of each other."""
    if a.box[0] - gap >= b.box[1] or b.box[0] - gap >= a.box[1] or a.box[2] - gap >= b.box[3] or b.box[2] - gap >= a.box[3]:
        return False
    y0, y1 = min(a.box[0], b.box[0]) - gap, max(a.box[1], b.box[1]) + gap
    x0, x1 = min(a.box[2], b.box[2]) - gap, max(a.box[3], b.box[3]) + gap
    canvas_a = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    canvas_b = np.zeros_like(canvas_a)
    canvas_a[a.box[0] - y0 : a.box[1] - y0, a.box[2] - x0 : a.box[3] - x0] = a.mask
    canvas_b[b.box[0] - y0 : b.box[1] - y0, b.box[2] - x0 : b.box[3] - x0] = b.mask
    return bool((ndimage.binary_dilation(canvas_a, iterations=gap) & canvas_b).any())


def _unlit_border(mean_log: np.ndarray, valid: np.ndarray, settings: FeatureMapSettings) -> np.ndarray:
    """Large areas connected to the border of the data that get almost no light (below `unlit_level` of the field's bright level: the
    rounded corners of the illuminated field), grown by 8 px. A disk is at ~60 % of the bright level, a particle is small."""
    bright = float(np.nanpercentile(mean_log[valid], 90))
    dark = valid & (mean_log < bright + np.log(settings.unlit_level))
    if not dark.any():
        return np.zeros_like(valid)
    border = ndimage.binary_dilation(np.pad(~valid, 1, constant_values=True), iterations=2)[1:-1, 1:-1]
    ys, xs = np.nonzero(valid)
    zone = settings.border_zone_px
    top, bottom, left, right = ys.min() + zone, ys.max() - zone, xs.min() + zone, xs.max() - zone
    labels, count = ndimage.label(dark, structure=np.ones((3, 3), dtype=bool))
    out = np.zeros_like(valid)
    for i, box in enumerate(ndimage.find_objects(labels), start=1):
        m = labels[box] == i
        if int(m.sum()) < 50 or not (m & border[box]).any():
            continue
        long_side = max(box[0].stop - box[0].start, box[1].stop - box[1].start)
        sides = int(box[0].start <= top) + int(box[0].stop - 1 >= bottom) + int(box[1].start <= left) + int(box[1].stop - 1 >= right)
        if long_side >= 3 * zone or sides >= 2:  # along a border (an edge strip), or in a corner; a particle is short, on one side
            out[box] |= m
    return ndimage.binary_dilation(out, iterations=8) & valid  # and its soft edge (the aperture is blurred over ~10 px)


def _core(component: np.ndarray, strength: np.ndarray, seed: np.ndarray, share: float) -> np.ndarray:
    """The part of `component` where its `strength` (signed so that the feature is positive) reaches `share` of its own peak (95th
    percentile over its seed pixels), the connected pieces holding a seed. A strong particle in a faint wide shadow is the particle,
    not the shadow (ROI 105: 39 % at the particle, 2-4 % over ~1700 px of shadow); for faint dirt the usual floor still decides."""
    if share <= 0.0:
        return component
    peak = float(np.percentile(strength[component & seed], 95))
    core = component & (strength >= share * peak)
    labels, _ = ndimage.label(core, structure=np.ones((3, 3), dtype=bool))
    held = np.unique(labels[core & seed])
    return np.isin(labels, held[held > 0])


def _faint_dirt(features: list["MapFeature"], dirt: np.ndarray, edge: float) -> list["MapFeature"]:
    """Dark dirt whose sharpest step (98th percentile of the slope of the lightly smoothed dirt map, over the feature grown by 2 px) is
    below `edge` is faint dirt: a faint speck, or the soft shadow of dust out of focus (the round blobs under ROI 3, at 31/32, 48/64,
    119, 101 of the 2026-10-22 dataset). A particle inside a soft halo still has its own steep step, so it stays dirt."""
    smooth = ndimage.gaussian_filter(np.where(np.isfinite(dirt), dirt, 0.0), 1.0)
    out = []
    for f in features:
        if f.kind == DIRT_DARK:
            y0, y1, x0, x1 = f.box
            pad = 3
            ya, yb, xa, xb = max(y0 - pad, 0), min(y1 + pad, dirt.shape[0]), max(x0 - pad, 0), min(x1 + pad, dirt.shape[1])
            region = np.zeros((yb - ya, xb - xa), dtype=bool)
            region[y0 - ya : y1 - ya, x0 - xa : x1 - xa] = f.mask
            region = ndimage.binary_dilation(region, iterations=2)
            part = smooth[ya:yb, xa:xb]
            slope = np.hypot(ndimage.sobel(part, 0), ndimage.sobel(part, 1)) / 8.0
            if float(np.percentile(slope[region], 98)) < edge:
                f = dataclasses.replace(f, kind=DIRT_FAINT)
        out.append(f)
    return out


def _notch_rims(features: list["MapFeature"], r: np.ndarray, radius: float, structure: np.ndarray) -> list["MapFeature"]:
    """A bright "dirt" patch mostly at a disk's rim that touches missing nanostructure is the rim of that notch: at the very rim the
    contrast has too little spectral shape to tell the two apart, and bright dirt is rare while notches are common."""
    missing = [f for f in features if f.kind == STRUCTURE_MISSING]
    if not missing:
        return features
    out = []
    for f in features:
        if f.kind == DIRT_BRIGHT:
            y0, y1, x0, x1 = f.box
            on_rim = float(np.mean(r[y0:y1, x0:x1][f.mask] <= radius + 4.0)) >= 0.5
            if on_rim and any(_boxes_touch(f, m) for m in missing):
                values = structure[y0:y1, x0:x1][f.mask]
                values = values[np.isfinite(values)]  # at the very rim the nanostructure change is not defined
                f = dataclasses.replace(f, kind=STRUCTURE_MISSING, size=float(np.median(values)) if values.size else float("nan"))
        out.append(f)
    return out


def feature_map(
    logs: np.ndarray,
    settings: FeatureMapSettings = FeatureMapSettings(),
    *,
    disks: tuple[Disk, ...] | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> FeatureMap:
    """Everything above for `logs` (W, H, W'): log intensity per wavelength, aligned, NaN where no data."""

    def step(fraction: float, message: str) -> None:
        if cancelled is not None and cancelled():
            raise Cancelled()
        if progress is not None:
            progress(fraction, message)

    n_wl = logs.shape[0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean_log = np.nanmean(logs, axis=0)
    valid = np.isfinite(logs).sum(axis=0) >= max(3, n_wl // 2)
    valid &= ~_unlit_border(mean_log, valid, settings)  # the field's black corners are no data, not dirt
    mean_log = np.where(valid, mean_log, np.nan)
    step(0.0, "finding the disks")
    found_here = disks is None
    if disks is None:
        disks = find_disks(mean_log, settings)
    if not disks:
        raise rf.NotEnoughRois("No nanostructure disks found in the picture.")
    radius = float(np.median([d.radius for d in disks]))
    reach = settings.halo_factor * radius
    which, r = nearest_disk(mean_log.shape, disks)
    if found_here:
        step(0.03, "refining the disk centres")
        far0 = valid & (r > reach)
        disks = refine_centres(mean_log, disks, shading(np.where(valid, mean_log, 0.0), far0, settings.block_px, 2 * settings.window_blocks - 1))
        which, r = nearest_disk(mean_log.shape, disks)
    model = _RadialModel(which, r, valid, disks, reach)
    far = valid & (r > reach)
    n_bins = int(reach / _BIN_PX) + 1
    bins = np.where(r <= reach, (r / _BIN_PX).astype(np.int64), n_bins).clip(0, n_bins)
    step(0.04, "a first look for outliers")
    exclude = _outliers(mean_log, valid, far, model, bins, n_bins, settings)
    dev = np.empty(logs.shape, dtype=np.float32)
    con = np.empty(logs.shape, dtype=np.float32)
    for w in range(n_wl):
        step(0.05 + 0.75 * w / n_wl, f"modelling wavelength {w + 1} of {n_wl}")
        f = np.where(valid, logs[w], np.nan)
        values = np.where(valid, f, 0.0)
        sh = shading(values, far & np.isfinite(f) & ~exclude, settings.block_px, 2 * settings.window_blocks - 1)
        disk_model = model(f - sh, exclude)
        # once more: the first shading had to bridge the disks from sparse pixels; with the disks out it can use every pixel
        sh = shading(values - disk_model, valid & np.isfinite(f) & ~exclude, settings.block_px, settings.window_blocks)
        disk_model = model(f - sh, exclude)
        dev[w] = np.where(valid, f - sh - disk_model, np.nan)
        con[w] = disk_model
    step(0.8, "splitting dirt from nanostructure")
    finite = np.isfinite(dev)
    fw = finite.astype(np.float32)
    d = np.where(finite, dev, 0.0)
    n, sc, scc = fw.sum(0), (fw * con).sum(0), (fw * con * con).sum(0)
    sd, scd = (fw * d).sum(0), (fw * con * d).sum(0)
    det = n * scc - sc * sc
    spread_c = con.std(axis=0)
    defined = valid & (spread_c >= settings.min_contrast_share * float(np.percentile(spread_c[valid], 99.5)))
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(defined, (scc * sd - sc * scd) / det, sd / np.maximum(n, 1))
        s = np.where(defined, (n * scd - sc * sd) / det, np.nan)
    g = np.where(valid, g, np.nan).astype(np.float64)
    step(0.9, "scoring")
    inner = ndimage.binary_erosion(valid, iterations=settings.edge_margin_px, border_value=0) if settings.edge_margin_px > 0 else valid
    near_border = ndimage.distance_transform_edt(np.pad(valid, 1))[1:-1, 1:-1] <= settings.border_zone_px  # the image array's edge too
    # nor dirt at the rim of a disk the border cuts: its round model sees only part of it
    inner = inner & ~(near_border & (r <= radius + 6.0))
    z_g = np.where(inner, g / _spread_by_distance(g, inner & np.isfinite(g), bins, n_bins, 1e-3), 0.0)
    s_ok = inner & np.isfinite(s) & ~near_border  # a disk cut by the border cannot be judged right at the cut (its model sees part of it)
    z_s = np.where(s_ok, s / _spread_by_distance(np.where(s_ok, s, 0.0), s_ok, bins, n_bins, 1e-3), 0.0)
    maps = {"grey": (z_g, np.where(inner, np.expm1(g), 0.0), inner), "structure": (z_s, np.where(s_ok, s, 0.0), s_ok)}
    disk_area = np.pi * radius * radius
    features: list[MapFeature] = []
    kind_masks: dict[str, np.ndarray] = {}
    for kind in _DETECTED:
        name, sign = _KIND_MAP[kind]
        z, size, use = maps[name]
        limit = settings.limit(kind)
        sz, ss = sign * z, sign * size
        seed = use & (sz >= settings.certainty) & (ss >= limit)
        grow = use & (sz >= settings.certainty * settings.extent) & (ss >= limit * settings.extent)
        labels, count = ndimage.label(grow, structure=np.ones((3, 3), dtype=bool))
        if count:
            index = np.arange(1, count + 1)
            seeds = ndimage.sum_labels(seed, labels, index)
            max_area = settings.structure_max_share * disk_area if name == "structure" else np.inf
            for i, box in zip(index, ndimage.find_objects(labels)):
                if seeds[i - 1] < settings.seed_px:
                    continue
                m = _core(labels[box] == i, ss[box], seed[box], settings.core_share)
                area = int(m.sum())
                if area < settings.min_area_px or area > max_area:
                    continue
                long_side = max(box[0].stop - box[0].start, box[1].stop - box[1].start)
                if long_side >= 3 * settings.border_zone_px and float(near_border[box][m].mean()) >= settings.border_share:
                    continue  # it runs along the border of the data: the field's dark edge
                ys, xs = np.nonzero(m)
                cy, cx = float(ys.mean() + box[0].start), float(xs.mean() + box[1].start)
                features.append(MapFeature(
                    kind, area, float(sz[box][m].max()), float(np.median(size[box][m])), (cx, cy),
                    int(which[int(round(cy)), int(round(cx))]), (box[0].start, box[0].stop, box[1].start, box[1].stop), m,
                ))
    features = _notch_rims(features, r, radius, s)
    features = _faint_dirt(features, maps["grey"][1], settings.faint_slope)
    for kind in KINDS:
        kept = np.zeros(valid.shape, dtype=bool)
        for f in features:
            if f.kind == kind:
                kept[f.box[0] : f.box[1], f.box[2] : f.box[3]] |= f.mask
        if settings.grow_px > 0 and kept.any():
            kept = ndimage.binary_dilation(kept, structure=_CROSS4, iterations=settings.grow_px) & valid
        kind_masks[kind] = kept
    combined = np.zeros(valid.shape, dtype=bool)
    for kind in settings.kinds:
        combined |= kind_masks[kind]
    step(1.0, "done")
    return FeatureMap(tuple(disks), tuple(features), kind_masks, combined, g.astype(np.float32), s.astype(np.float32),
                      z_g.astype(np.float32), z_s.astype(np.float32))
