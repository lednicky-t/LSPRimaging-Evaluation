"""Find small features (dust, scratches, edge defects, bright or dark spots) on or around ROIs, from the image alone (2026-10-29).

Maintainer request: small features that overlap a sample disk or a reference ring should be found and masked. Purely image-based:
no sensorgram, no baseline or signal range is read. Pure numpy / scipy, no Qt, no ROI model (the caller hands over arrays), so it
runs on a worker thread, in a script or in a test alike.

**Idea.** A ROI of this app is a round object: a dark (or bright) disk with a soft edge on a flat background. A feature is whatever
the ROI shows *beyond what ROIs of this array normally show*. In three steps:

1. ``fit_patch`` (per ROI, the expensive step): fit the ROI's centre and its **radial profile** (the mean intensity at each distance
   from the centre, outliers left out). What is left, the residual, is every deviation from perfect circular symmetry, divided by the
   disk's contrast so ROIs of different brightness are comparable, and resampled on a common grid centred on the fitted centre.
2. ``score_rois`` (all ROIs together): the residuals of the ROIs of one array share structure that is *not* a defect (on the
   2026-10-29 dataset every disk has a small bright lobe at its lower-right rim, of different strength each time). So the expected
   residual of a ROI is a **median image plus a few principal components** over all ROIs, fitted to each ROI robustly (pixels that
   disagree are left out of the fit, so a defect cannot pull the model onto itself). What the family does not explain, divided by how
   much that pixel varies among ROIs (a robust per-pixel spread), is the score ``z``: roughly standard normal where nothing is
   wrong (measured 1.05-1.12 on real data), so a threshold in ``z`` means the same thing at the bright background and inside the
   dark disk.
3. ``find_features``: connected runs of the same sign with ``|z| >= z_grow`` that hold a seed (``|z| >= z_seed``), are small (area
   and width limits) and overlap the ROI's sample disk or reference ring. The result is the feature list and a mask (the features,
   grown by ``grow_px``).

**What it cannot do (known, by design).** It needs enough ROIs of one kind in the image (``MIN_ROIS``) to know what normal is; a
feature that every ROI shares is "normal" and not reported; a feature wider than ``max_width_px`` is not "small". It assumes round
ROIs (the radial profile). It says what *deviates*, not whether that hurts the measurement: ``ratio_effect`` estimates how much the
features move the sample/reference ratio, and the measurement it was tested against (see the doc) found no gain in noise.

Record, method choices and the real-data tests: ``docs/roi_feature_detection_2026-10-29.md``.
"""

from __future__ import annotations

import warnings
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

MIN_ROIS = 8  # fewer ROIs than this cannot say what a ROI normally looks like
_BIN_PX = 0.5  # width of a radial-profile bin
_CROSS8 = np.ones((3, 3), dtype=bool)
_CROSS4 = ndimage.generate_binary_structure(2, 1)


class NotEnoughRois(ValueError):
    """Fewer than ``MIN_ROIS`` ROIs were given (or fitted): the comparison between ROIs is not possible."""


@dataclass(frozen=True)
class FeatureSettings:
    z_seed: float = 5.0  # a feature must reach this score in at least ``seed_px`` pixels ...
    seed_px: int = 2
    z_grow: float = 3.0  # ... and extends over the connected pixels at or above this score
    min_area_px: int = 4
    max_area_px: int = 500
    max_width_px: float = 20.0  # widest inscribed circle of a "small" feature (the sample disk is ~34 px across)
    grow_px: int = 1  # the mask is the feature grown by this much (the edges are blurred by the optics)
    edge_margin_px: int = 3  # a component with `edge_share` of its pixels this close to pixels without data is the frame's edge, not a feature
    edge_share: float = 0.25
    modes: int = 3  # principal components of the expected-appearance family (0 = the median alone)
    mode_clip: float = 4.0  # pixels beyond this score are clipped before the components are computed (a defect must not shape one)
    spread_smooth_px: float = 1.5  # the per-pixel spread map is smoothed (it is estimated from a finite number of ROIs)
    spread_floor: float = 0.004  # and never below this (relative to the disk's contrast), so a quiet pixel cannot divide by ~0


@dataclass(frozen=True)
class RoiPatch:
    """One ROI after step 1. Arrays are in the ROI's window unless said otherwise."""

    centre: tuple[float, float]  # fitted centre (x, y) in window pixels
    radius_px: float
    amplitude: float  # contrast of the radial profile (98th - 2nd percentile), in image units
    window_shape: tuple[int, int]
    valid: np.ndarray  # bool (window): the pixel has a value
    patch: np.ndarray  # float32 (grid, grid): residual / amplitude, centred on the fitted centre; NaN where nothing was seen
    fill: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 0)))  # float32 (window): the pixels, a median where invalid (kept for `refit_patch`)


@dataclass(frozen=True)
class RoiScore:
    z: np.ndarray  # float32 (window): the score; 0 where the pixel is not valid or beyond the fit radius
    deviation: np.ndarray  # float32 (window): what the ROI shows beyond the expected family, in image units


@dataclass(frozen=True)
class Feature:
    sign: int  # +1 brighter than expected, -1 darker
    area_px: int
    peak_z: float
    overlap_px: int  # pixels inside the sample disk or reference ring
    width_px: float  # diameter of the widest inscribed circle
    centroid: tuple[float, float]  # (x, y) in window pixels
    mask: np.ndarray = field(repr=False)  # bool (window)


@dataclass(frozen=True)
class RoiFeatures:
    features: tuple[Feature, ...]
    mask: np.ndarray  # bool (window): all features, grown


def grid_size(radius_px: float) -> int:
    """Side of the common patch grid for a fit radius."""
    return 2 * (int(np.ceil(radius_px)) + 1) + 1


# -- step 1: one ROI ------------------------------------------------------------------------------------------------------


def _radial_model(
    values: np.ndarray, xx: np.ndarray, yy: np.ndarray, cx: float, cy: float, keep: np.ndarray, nbins: int
) -> tuple[np.ndarray, np.ndarray]:
    """The mean of `values` at each distance from (cx, cy) over the pixels in `keep`, as an image, and the distance image."""
    r = np.hypot(xx - cx, yy - cy)
    index = np.minimum((r / _BIN_PX).astype(np.intp), nbins - 1)
    sums = np.bincount(index[keep], weights=values[keep], minlength=nbins)
    counts = np.bincount(index[keep], minlength=nbins).astype(np.float64)
    seen = counts > 0
    centres = (np.arange(nbins) + 0.5) * _BIN_PX
    profile = np.interp(centres, centres[seen], sums[seen] / counts[seen])
    return np.interp(r, centres, profile), r


def _noise_table(residual: np.ndarray, model: np.ndarray, sel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Robust noise (1.4826 x MAD) of the residual as a function of the model's level, by intensity bins (the dark disk is
    quieter in absolute terms than the bright background)."""
    levels = model[sel]
    edges = np.unique(np.quantile(levels, np.linspace(0.0, 1.0, 7)))
    centres: list[float] = []
    sigma: list[float] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        chosen = sel & (model >= lo) & (model <= hi)
        if int(chosen.sum()) < 60:
            continue
        v = residual[chosen]
        centres.append(float(np.median(model[chosen])))
        sigma.append(1.4826 * float(np.median(np.abs(v - np.median(v)))))
    if not centres:
        return np.array([0.0, 1.0]), np.array([1.0, 1.0])
    return np.asarray(centres), np.asarray(sigma)


def fit_patch(
    window: np.ndarray, valid: np.ndarray, centre_xy: tuple[float, float], radius_px: float, *, max_iterations: int = 40
) -> RoiPatch | None:
    """Step 1 for one ROI. `window` is a crop of the image around the ROI (any value where `valid` is False), `centre_xy` the ROI's
    nominal centre in window pixels, `radius_px` how far from the centre the model is fitted (reference ring's outer radius plus a
    margin). ``None`` when too little of the window has data. The centre is moved up to a few pixels to where the disk really is."""
    if valid.mean() < 0.6 or not valid.any():
        return None
    fill = np.where(valid, window, np.median(window[valid])).astype(np.float64)
    yy, xx = np.indices(window.shape, dtype=np.float64)
    nbins = int(radius_px / _BIN_PX) + 2

    def inside(cx: float, cy: float) -> np.ndarray:
        return np.hypot(xx - cx, yy - cy) <= radius_px

    p = np.array(centre_xy, dtype=np.float64)
    keep = inside(*p) & valid
    model, _ = _radial_model(fill, xx, yy, p[0], p[1], keep, nbins)
    table = _noise_table(fill - model, model, keep)
    for _ in range(3):  # alternate: move the centre, re-judge which pixels are outliers
        def cost(q: np.ndarray, keep: np.ndarray = keep, table: tuple[np.ndarray, np.ndarray] = table) -> float:
            m, r = _radial_model(fill, xx, yy, q[0], q[1], keep, nbins)
            sel = (r <= radius_px) & valid
            limit = 3.0 * np.interp(m[sel], table[0], table[1])
            return float(np.sum(np.minimum((fill[sel] - m[sel]) ** 2, limit**2)))

        p = optimize.minimize(cost, p, method="Nelder-Mead", options={"xatol": 0.05, "fatol": 1e-3, "maxiter": max_iterations}).x
        sel = inside(*p) & valid
        model, _ = _radial_model(fill, xx, yy, p[0], p[1], keep, nbins)
        table = _noise_table(fill - model, model, sel)
        keep = sel & (np.abs(fill - model) < 3.0 * np.interp(model, table[0], table[1]))
    return _make_patch(fill, valid, (float(p[0]), float(p[1])), radius_px, keep)


def _make_patch(fill: np.ndarray, valid: np.ndarray, centre: tuple[float, float], radius_px: float, keep: np.ndarray) -> RoiPatch | None:
    """The residual patch of a ROI for a given centre and set of pixels the radial profile is built from."""
    yy, xx = np.indices(fill.shape, dtype=np.float64)
    model, r = _radial_model(fill, xx, yy, centre[0], centre[1], keep, int(radius_px / _BIN_PX) + 2)
    region = r <= radius_px
    amplitude = float(np.percentile(model[region], 98) - np.percentile(model[region], 2))
    if not np.isfinite(amplitude) or amplitude <= 0.0:
        return None
    residual = np.where(valid & region, fill - model, np.nan)  # beyond the fit radius the model has no data: nothing is said there
    patch = _to_grid(residual, centre, radius_px) / amplitude
    return RoiPatch(centre, float(radius_px), amplitude, fill.shape, valid.copy(), patch.astype(np.float32), fill.astype(np.float32))


def refit_patch(patch: RoiPatch, exclude: np.ndarray) -> RoiPatch | None:
    """The same ROI with the pixels in `exclude` left out of its radial profile (the centre stays). Used for the second pass: a feature
    found in the first pass must not pull the profile it is measured against (a long streak through the centre makes a dark twin)."""
    yy, xx = np.indices(patch.window_shape, dtype=np.float64)
    keep = (np.hypot(xx - patch.centre[0], yy - patch.centre[1]) <= patch.radius_px) & patch.valid & ~exclude
    if int(keep.sum()) < 50:
        return patch
    return _make_patch(patch.fill.astype(np.float64), patch.valid, patch.centre, patch.radius_px, keep) or patch


def _to_grid(a: np.ndarray, centre: tuple[float, float], radius_px: float) -> np.ndarray:
    """A window array resampled on the common grid centred on `centre` (bilinear; NaN outside the window)."""
    half = grid_size(radius_px) // 2
    dy, dx = np.mgrid[-half : half + 1, -half : half + 1].astype(np.float64)
    return ndimage.map_coordinates(a, [dy + centre[1], dx + centre[0]], order=1, mode="constant", cval=np.nan)


def _from_grid(g: np.ndarray, shape: tuple[int, int], centre: tuple[float, float], radius_px: float) -> np.ndarray:
    """The inverse: a grid array back on the window's pixels (bilinear; 0 outside the grid)."""
    half = grid_size(radius_px) // 2
    yy, xx = np.indices(shape, dtype=np.float64)
    return ndimage.map_coordinates(g, [yy - centre[1] + half, xx - centre[0] + half], order=1, mode="constant", cval=0.0)


# -- step 2: all ROIs together --------------------------------------------------------------------------------------------


def score_rois(patches: Mapping[Hashable, RoiPatch], settings: FeatureSettings = FeatureSettings()) -> dict[Hashable, RoiScore]:
    """Step 2: the score of every ROI against the family of expected appearances estimated from all of them. Raises `NotEnoughRois`."""
    keys = list(patches)
    if len(keys) < MIN_ROIS:
        raise NotEnoughRois(f"{len(keys)} ROIs with usable pixels: at least {MIN_ROIS} are needed to know what a ROI normally looks like.")
    stack = np.stack([patches[k].patch for k in keys]).astype(np.float64)  # (N, G, G), NaN where unseen
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # "all-NaN slice": a grid pixel no ROI saw (the corners)
        median = np.nanmedian(stack, axis=0)
        delta = stack - median
        spread0 = 1.4826 * np.nanmedian(np.abs(delta), axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    spread0 = _spread_map(spread0, settings)
    finite = np.isfinite(delta)
    delta = np.where(finite, delta, 0.0)
    modes = min(int(settings.modes), max(len(keys) // 10, 0))  # a component needs ROIs to be estimated from
    components = np.zeros((0, delta.shape[1] * delta.shape[2]))
    if modes > 0:
        clipped = np.clip(delta / spread0, -settings.mode_clip, settings.mode_clip) * spread0  # a defect in one ROI must not shape a mode
        flat = clipped.reshape(len(keys), -1)
        _, _, vt = np.linalg.svd(flat - flat.mean(axis=0), full_matrices=False)
        components = vt[:modes]
    residual = np.zeros_like(delta)
    for n in range(len(keys)):
        d = delta[n].ravel()
        w = finite[n].ravel()
        fit = np.zeros(len(d))
        if modes > 0:
            a = np.zeros(modes)
            for _ in range(4):  # least squares on the pixels that agree with the model so far
                use = w & (np.abs(d - fit) < 3.0 * spread0.ravel())
                if int(use.sum()) < 10 * modes:
                    break
                a = np.linalg.lstsq(components[:, use].T, d[use], rcond=None)[0]
                fit = a @ components
        residual[n] = ((d - fit) * w).reshape(delta.shape[1:])
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spread = 1.4826 * np.nanmedian(np.where(finite, np.abs(residual), np.nan), axis=0)
    spread = _spread_map(spread, settings)
    out: dict[Hashable, RoiScore] = {}
    for n, key in enumerate(keys):
        p = patches[key]
        z_grid = np.where(finite[n], residual[n] / spread, 0.0)
        dev_grid = np.where(finite[n], residual[n] * p.amplitude, 0.0)
        yy, xx = np.indices(p.window_shape, dtype=np.float64)
        inside = p.valid & (np.hypot(xx - p.centre[0], yy - p.centre[1]) <= p.radius_px)
        out[key] = RoiScore(
            np.where(inside, _from_grid(z_grid, p.window_shape, p.centre, p.radius_px), 0.0).astype(np.float32),
            np.where(inside, _from_grid(dev_grid, p.window_shape, p.centre, p.radius_px), 0.0).astype(np.float32),
        )
    return out


def _spread_map(spread: np.ndarray, settings: FeatureSettings) -> np.ndarray:
    filled = np.where(np.isfinite(spread), spread, np.nanmedian(spread[np.isfinite(spread)]))
    return np.maximum(ndimage.gaussian_filter(filled, settings.spread_smooth_px), settings.spread_floor)


# -- step 3: features -----------------------------------------------------------------------------------------------------


def find_features(
    z: np.ndarray, region: np.ndarray, settings: FeatureSettings = FeatureSettings(), valid: np.ndarray | None = None
) -> RoiFeatures:
    """Step 3: the features in one ROI's score map. `region` is the sample disk plus the reference ring (bool, same window); `valid`
    (optional) says which pixels have data, so that what only reflects the edge of the data (a rotated frame's border darkens smoothly over
    ~20 px, which the radial model cannot follow) is not reported as a feature."""
    features: list[Feature] = []
    kept = np.zeros(z.shape, dtype=bool)
    near_edge = None
    if valid is not None and settings.edge_margin_px > 0 and not valid.all():
        near_edge = ~ndimage.binary_erosion(valid, iterations=settings.edge_margin_px, border_value=1)  # the window's own border is not an edge of data
    for sign in (+1, -1):
        signed = z * sign
        low = signed >= settings.z_grow
        labels, count = ndimage.label(low, structure=_CROSS8)
        if not count:
            continue
        index = np.arange(1, count + 1)
        area = ndimage.sum_labels(low, labels, index)
        peak = ndimage.maximum(signed, labels, index)
        seeds = ndimage.sum_labels(signed >= settings.z_seed, labels, index)
        overlap = ndimage.sum_labels(region, labels, index)
        for i, a, pk, sn, ov in zip(index, area, peak, seeds, overlap):
            if a < settings.min_area_px or a > settings.max_area_px or sn < settings.seed_px or ov < 1:
                continue
            m = labels == i
            if near_edge is not None and int((m & near_edge).sum()) >= settings.edge_share * a:
                continue
            width = 2.0 * float(ndimage.distance_transform_edt(np.pad(m, 1)).max())
            if width > settings.max_width_px:
                continue
            ys, xs = np.nonzero(m)
            features.append(Feature(sign, int(a), float(pk), int(ov), width, (float(xs.mean()), float(ys.mean())), m))
            kept |= m
    if settings.grow_px > 0 and kept.any():
        kept = ndimage.binary_dilation(kept, structure=_CROSS4, iterations=settings.grow_px)
    return RoiFeatures(tuple(features), kept)


def detect(
    patches: Mapping[Hashable, RoiPatch],
    regions: Mapping[Hashable, np.ndarray],
    settings: FeatureSettings = FeatureSettings(),
    *,
    second_pass: bool = True,
) -> dict[Hashable, tuple[RoiScore, RoiFeatures]]:
    """Steps 2 and 3 for every ROI: ``regions[key]`` is that ROI's sample disk plus reference ring in its window (bool).

    With ``second_pass`` (the default) everything suspicious in the first pass (``|z| >= z_grow``, grown by 2 px) is left out of the
    radial profiles and the whole array is scored again, so a feature cannot bias what it is measured against."""
    scores = score_rois(patches, settings)
    if second_pass:
        refit = {}
        for key, patch in patches.items():
            suspicious = np.abs(scores[key].z) >= settings.z_grow
            refit[key] = patch if not suspicious.any() else refit_patch(patch, ndimage.binary_dilation(suspicious, _CROSS4, iterations=2))
        scores = score_rois(refit, settings)
    return {key: (score, find_features(score.z, regions[key], settings, patches[key].valid)) for key, score in scores.items()}


def masked_share(mask: np.ndarray, region: np.ndarray) -> float:
    """The fraction of `region` (a sample disk, or a reference ring) that `mask` covers: what is left to measure with is 1 minus this."""
    total = int(region.sum())
    return float((mask & region).sum() / total) if total else 0.0


def ratio_effect(
    image: np.ndarray, deviation: np.ndarray, sample: np.ndarray, reference: np.ndarray, mask: np.ndarray
) -> tuple[float, float]:
    """How much the masked features move the ROI's mean intensities, relative to those means: ``(sample, reference)`` fractions.
    The effect on the sample/reference ratio is ``sample - reference`` (to first order). `deviation` is `RoiScore.deviation`; the
    sample disk and the reference ring are the bool windows of this ROI (the ring without the disk). Zero for an empty region."""
    ok = np.isfinite(image)
    out = []
    for region in (sample, reference):
        chosen = region & ok
        if not chosen.any():
            out.append(0.0)
            continue
        mean = float(image[chosen].mean())
        out.append(float(deviation[chosen & mask].sum() / chosen.sum() / mean) if mean != 0.0 else 0.0)
    return out[0], out[1]
