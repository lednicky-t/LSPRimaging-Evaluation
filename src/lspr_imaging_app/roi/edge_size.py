"""Size of a blurred round spot, measured from the image (pure: numpy / scipy / cv2, no Qt).

Why: a spot's edge is feathered by the optics, so "the diameter" depends on where on the
edge you stop. The ROI should contain only pixels that look like the inside of the spot,
so the edge pixels (25 / 50 / 75 % of the way from spot to background) stay outside it.
Several definitions are implemented side by side so the lab (`tools/array_lab.py`) can
compare them on synthetic spots of known size and on real data; the winner stays and the
others are deleted (maintainer, 2026-10-08).

All lengths are **diameters in pixels** of the image passed in (processed image space; the
caller converts to the reference frame when it stores them). Images are expected as a
*contrast map* (`image_features.contrast_map`): the spot is **positive**, the background
about 0. Nothing here changes the image. Results are binary decisions (pixel in / out); no
fractional weighting (open scientific question).

Per spot, first measured from samples around the centre:
- plateau P: median over the core (r < 0.5 x rough radius),
- noise sigma: 1.4826 x MAD over the same core,
- background B: median over an outer annulus (1.5 .. 1.8 x rough radius, capped by `max_radius_px`).

Models (`EDGE_MODELS`):
- ``plateau_fraction``: a pixel is in the spot if it is within `fraction` of the way from P
  to B (value >= P - fraction x (P - B)). Diameter of the circle with the same area as the
  connected region around the centre.
- ``plateau_sigma``: same, tolerance `sigma_k` x sigma instead.
- ``plateau_combined``: tolerance = the larger of the two (the fraction alone is blind to
  noise, sigma alone collapses on very clean images).
- ``half_max``: radius where the azimuthally averaged radial profile crosses the midpoint
  between P and B (the true edge of a symmetrically blurred step).
- ``max_gradient``: radius of the steepest fall of the radial profile.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

EDGE_MODELS: tuple[str, ...] = ("plateau_fraction", "plateau_sigma", "plateau_combined", "half_max", "max_gradient")

_RADIAL_STEP_PX = 0.25
_ANGLES = 48


@dataclass(frozen=True)
class EdgeSizeParams:
    model: str = "plateau_fraction"
    fraction: float = 0.15
    """Plateau models: tolerance as a fraction of (P - B)."""
    sigma_k: float = 3.0
    """Plateau models: tolerance as a multiple of the core noise sigma."""
    max_radius_px: float | None = None
    """Search no further out than this (e.g. half the distance to the nearest neighbour)."""
    oversample: int = 1
    """Plateau models: threshold a bilinearly upsampled patch (1 = the real pixels)."""


@dataclass(frozen=True)
class SpotSize:
    diameter_px: float
    """NaN when the spot could not be measured (see `reason`)."""
    plateau: float
    background: float
    noise_sigma: float
    reason: str = "ok"

    @property
    def ok(self) -> bool:
        return math.isfinite(self.diameter_px)


def _failed(reason: str, plateau: float = math.nan, background: float = math.nan, sigma: float = math.nan) -> SpotSize:
    return SpotSize(math.nan, plateau, background, sigma, reason)


def measure_spot_sizes(
    contrast: np.ndarray,
    centers_xy: np.ndarray,
    rough_diameter_px: float,
    params: EdgeSizeParams | None = None,
    valid_mask: np.ndarray | None = None,
) -> list[SpotSize]:
    """Diameter of each spot at `centers_xy` (N, 2: x, y in pixels; pixel centres at integers).

    `rough_diameter_px` only sets where the core and the background are sampled; the model
    decides the final value. Spots whose surroundings leave the image or touch invalid
    pixels, or that have no contrast, come back with ``ok == False`` and a reason."""
    params = params or EdgeSizeParams()
    if params.model not in EDGE_MODELS:
        raise ValueError(f"unknown edge model {params.model!r}; choose one of {EDGE_MODELS}")
    if rough_diameter_px <= 0:
        raise ValueError("rough_diameter_px must be positive")
    centers = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    if len(centers) == 0:
        return []
    image = np.asarray(contrast, dtype=np.float32)
    rough_r = rough_diameter_px / 2.0
    r_max = 1.8 * rough_r if params.max_radius_px is None else max(float(params.max_radius_px), 1.2 * rough_r)
    radii = np.arange(0.0, r_max + _RADIAL_STEP_PX, _RADIAL_STEP_PX)
    angles = np.linspace(0.0, 2.0 * math.pi, _ANGLES, endpoint=False)
    samples = polar_samples(image, centers, radii, angles, valid_mask)  # (N, nr, na), NaN outside / invalid
    # Noise is measured on whole pixels (nearest sample): bilinear samples average neighbours and
    # understate the pixel noise that the plateau models threshold against.
    core_radii = radii[radii < 0.5 * rough_r]
    pixel_core = polar_samples(image, centers, core_radii, angles, valid_mask, order=0)

    out: list[SpotSize] = []
    for index, center in enumerate(centers):
        out.append(_measure_one(image, valid_mask, center, radii, samples[index], pixel_core[index], rough_r, r_max, params))
    return out


def polar_samples(
    image: np.ndarray,
    centers: np.ndarray,
    radii: np.ndarray,
    angles: np.ndarray,
    valid_mask: np.ndarray | None,
    order: int = 1,
) -> np.ndarray:
    """Samples (bilinear unless `order=0`) on (radius, angle) around every centre, one batched call."""
    cos, sin = np.cos(angles), np.sin(angles)
    xs = centers[:, 0, None, None] + radii[None, :, None] * cos[None, None, :]
    ys = centers[:, 1, None, None] + radii[None, :, None] * sin[None, None, :]
    height, width = image.shape
    inside = (xs >= 0) & (xs <= width - 1) & (ys >= 0) & (ys <= height - 1)
    values = ndimage.map_coordinates(image, [ys.ravel(), xs.ravel()], order=order, mode="nearest").reshape(xs.shape)
    values = values.astype(np.float64)
    values[~inside] = np.nan
    if valid_mask is not None:
        near = ndimage.map_coordinates(valid_mask.astype(np.float32), [ys.ravel(), xs.ravel()], order=1, mode="nearest")
        values[near.reshape(xs.shape) < 0.999] = np.nan
    values[~np.isfinite(values)] = np.nan
    return values


def _measure_one(
    image: np.ndarray,
    valid_mask: np.ndarray | None,
    center: np.ndarray,
    radii: np.ndarray,
    samples: np.ndarray,
    pixel_core: np.ndarray,
    rough_r: float,
    r_max: float,
    params: EdgeSizeParams,
) -> SpotSize:
    core = samples[radii < 0.5 * rough_r]
    background_zone = samples[(radii >= 1.5 * rough_r) & (radii <= r_max)]
    if np.isnan(core).mean() > 0.2:
        return _failed("core outside the image or on invalid pixels")
    if background_zone.size == 0 or np.isnan(background_zone).mean() > 0.5:
        return _failed("background ring outside the image or on invalid pixels")
    plateau = float(np.nanmedian(core))
    pixel_values = pixel_core[np.isfinite(pixel_core)]
    sigma = 1.4826 * float(np.median(np.abs(pixel_values - np.median(pixel_values)))) if pixel_values.size else 0.0
    background = float(np.nanmedian(background_zone))
    depth = plateau - background
    if not depth > 0:
        return _failed("no contrast between spot and background", plateau, background, sigma)

    if params.model in ("plateau_fraction", "plateau_sigma", "plateau_combined"):
        tolerance = {
            "plateau_fraction": params.fraction * depth,
            "plateau_sigma": params.sigma_k * sigma,
            "plateau_combined": max(params.fraction * depth, params.sigma_k * sigma),
        }[params.model]
        if tolerance >= depth:
            return _failed("tolerance is larger than the spot contrast", plateau, background, sigma)
        diameter = _plateau_diameter(image, valid_mask, center, plateau - tolerance, rough_r, r_max, params.oversample)
    else:
        profile = np.nanmean(samples, axis=1)  # azimuthal average, (nr,)
        if params.model == "half_max":
            diameter = _half_max_diameter(profile, radii, plateau, background)
        else:
            diameter = _max_gradient_diameter(profile, radii, rough_r)
    if not math.isfinite(diameter):
        return _failed("edge not found", plateau, background, sigma)
    return SpotSize(float(diameter), plateau, background, sigma)


def _plateau_diameter(
    image: np.ndarray,
    valid_mask: np.ndarray | None,
    center: np.ndarray,
    threshold: float,
    rough_r: float,
    r_max: float,
    oversample: int,
) -> float:
    """Equivalent-area diameter of the connected region >= threshold around the centre."""
    half = int(math.ceil(r_max)) + 1
    cx, cy = float(center[0]), float(center[1])
    x0, y0 = int(round(cx)) - half, int(round(cy)) - half
    x1, y1 = x0 + 2 * half + 1, y0 + 2 * half + 1
    if x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
        return math.nan
    patch = image[y0:y1, x0:x1]
    if valid_mask is not None and not valid_mask[y0:y1, x0:x1].all():
        return math.nan
    scale = max(int(oversample), 1)
    if scale > 1:
        import cv2

        patch = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    yy, xx = np.mgrid[0 : patch.shape[0], 0 : patch.shape[1]]
    # position of the centre inside the patch (pixel-centre convention, also after upsampling)
    px = ((cx - x0) + 0.5) * scale - 0.5
    py = ((cy - y0) + 0.5) * scale - 0.5
    inside_search = np.hypot(xx - px, yy - py) <= r_max * scale
    mask = (patch >= threshold) & inside_search
    labels, _count = ndimage.label(mask)
    # The region is the one that holds most of the core (one noisy centre pixel must not lose the spot).
    core_labels = labels[np.hypot(xx - px, yy - py) <= 0.3 * rough_r * scale]
    core_labels = core_labels[core_labels > 0]
    if core_labels.size == 0:
        return math.nan
    seed = int(np.bincount(core_labels).argmax())
    # Holes (a speck or scratch inside the spot) do not make the spot smaller: only its outline counts.
    area = float(ndimage.binary_fill_holes(labels == seed).sum()) / (scale * scale)
    return 2.0 * math.sqrt(area / math.pi)


def _half_max_diameter(profile: np.ndarray, radii: np.ndarray, plateau: float, background: float) -> float:
    level = background + 0.5 * (plateau - background)
    below = np.flatnonzero(np.isfinite(profile) & (profile <= level) & (radii > 0))
    if len(below) == 0 or below[0] == 0:
        return math.nan
    i = int(below[0])
    p0, p1 = profile[i - 1], profile[i]
    fraction = 0.0 if p0 == p1 else (p0 - level) / (p0 - p1)
    return 2.0 * float(radii[i - 1] + fraction * (radii[i] - radii[i - 1]))


def _max_gradient_diameter(profile: np.ndarray, radii: np.ndarray, rough_r: float) -> float:
    clean = np.where(np.isfinite(profile), profile, np.nan)
    if np.isnan(clean).any():
        return math.nan
    smooth = ndimage.gaussian_filter1d(clean, 1.0 / _RADIAL_STEP_PX)  # 1 px smoothing
    slope = np.gradient(smooth, radii)
    window = (radii >= 0.4 * rough_r) & (radii <= radii[-1] - _RADIAL_STEP_PX)
    if not window.any():
        return math.nan
    candidates = np.flatnonzero(window)
    i = int(candidates[np.argmin(slope[candidates])])
    if 0 < i < len(radii) - 1:  # parabolic refinement of the minimum
        a, b, c = slope[i - 1], slope[i], slope[i + 1]
        denominator = a - 2 * b + c
        shift = 0.0 if abs(denominator) < 1e-12 else float(np.clip(0.5 * (a - c) / denominator, -1.0, 1.0))
        return 2.0 * float(radii[i] + shift * _RADIAL_STEP_PX)
    return 2.0 * float(radii[i])


def summarize(sizes: Sequence[SpotSize]) -> dict[str, float]:
    """Median / spread / minimum over the spots that were measured (for the array-wide size options)."""
    values = np.array([s.diameter_px for s in sizes if s.ok], dtype=np.float64)
    if values.size == 0:
        return {"count": 0.0, "median": math.nan, "mad": math.nan, "min": math.nan}
    median = float(np.median(values))
    return {
        "count": float(values.size),
        "median": median,
        "mad": 1.4826 * float(np.median(np.abs(values - median))),
        "min": float(values.min()),
    }
