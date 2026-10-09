"""Reference-ring diameters for round spots (pure: numpy / scipy, no Qt).

A reference ring is the annulus around a sample disk whose pixels are averaged as the local
background. **Diameters only** (no radii), in pixels of the image passed in (processed image
space); same input as `edge_size.py`: a *contrast map* with the spot positive, background about 0.

Rules (maintainer, 2026-10-08)
- The inner diameter must be larger than the sample diameter.
- By default it is **measured**: as small as possible, but so that no pixel of the spot (the
  darker, feathered disk) falls inside the ring. Per direction the spot ends where the profile
  has fallen back to background (within a tolerance of B); the inner diameter is a high quantile
  of that over all directions (a scratch or a neighbouring spot in one direction must not decide).
  Alternatively a fixed **ratio** inner / sample (`inner_mode="ratio"`), which skips the image.
- The outer diameter follows a thickness rule: ``equal_area`` (ring area = sample disk area, the
  rule the app has used so far), ``thickness`` (fixed ring width in px) or ``outer_ratio``
  (outer / inner).
- Rings may overlap each other. A ring never counts pixels of sample disks: the analysis removes the
  union of all sample disks from every ring (`analysis/provenance.py`, ``exclude_all_sample_rois``).
  Sample disks are masked by the user's mask only, never by a ring. This module only reports when a
  ring would reach a neighbouring sample disk.

Array-wide size: the **maximum** inner diameter measured over the array keeps every ring clear of
every disk (for the sample diameter it is the minimum that is safe); one debris-affected spot can decide
it, so `summarize_rings` also gives `inner_robust` (maximum after dropping MAD outliers).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .edge_size import polar_samples

INNER_MODES: tuple[str, ...] = ("measured", "ratio")
THICKNESS_MODES: tuple[str, ...] = ("equal_area", "thickness", "outer_ratio")

_RADIAL_STEP_PX = 0.25
_ANGLES = 72


@dataclass(frozen=True)
class RingParams:
    inner_mode: str = "measured"
    background_fraction: float = 0.05
    """Measured inner: a pixel is background if it is within this fraction of (plateau - background) of B ..."""
    sigma_k: float = 3.0
    """... or within this many noise sigmas of B (the larger tolerance wins)."""
    quantile: float = 0.9
    """Measured inner: over the directions, use this quantile of the radius where the spot ends."""
    margin_px: float = 0.0
    """Measured inner: extra gap (diameter, px) added to the measured spot end."""
    inner_ratio: float = 1.4
    """``inner_mode="ratio"``: inner diameter = ratio x sample diameter."""
    min_gap_px: float = 1.0
    """The inner diameter is at least this much (diameter, px) larger than the sample diameter."""
    thickness_mode: str = "equal_area"
    thickness_px: float = 6.0
    """``thickness_mode="thickness"``: ring width, i.e. (outer - inner) / 2, in px."""
    outer_ratio: float = 1.25
    """``thickness_mode="outer_ratio"``: outer diameter = ratio x inner diameter."""
    max_radius_px: float | None = None
    """Search no further out than this radius (e.g. half the nearest-neighbour distance)."""


@dataclass(frozen=True)
class SpotRing:
    inner_diameter_px: float
    outer_diameter_px: float
    spot_end_diameter_px: float
    """Measured diameter where the spot has faded into the background (NaN in ratio mode / on failure)."""
    reason: str = "ok"

    @property
    def ok(self) -> bool:
        return math.isfinite(self.inner_diameter_px) and math.isfinite(self.outer_diameter_px)


def outer_from_inner(inner_diameter_px: float, sample_diameter_px: float, params: RingParams) -> float:
    """Outer diameter from the inner one by the thickness rule."""
    if params.thickness_mode == "equal_area":
        # pi/4 (outer^2 - inner^2) = pi/4 sample^2
        return math.hypot(inner_diameter_px, sample_diameter_px)
    if params.thickness_mode == "thickness":
        return inner_diameter_px + 2.0 * params.thickness_px
    if params.thickness_mode == "outer_ratio":
        return inner_diameter_px * params.outer_ratio
    raise ValueError(f"unknown thickness mode {params.thickness_mode!r}; choose one of {THICKNESS_MODES}")


def ring_from_inner(inner_diameter_px: float, sample_diameter_px: float, params: RingParams) -> tuple[float, float]:
    """(inner, outer) after enforcing inner > sample + `min_gap_px`."""
    inner = max(float(inner_diameter_px), float(sample_diameter_px) + params.min_gap_px)
    return inner, outer_from_inner(inner, sample_diameter_px, params)


def measure_rings(
    contrast: np.ndarray,
    centers_xy: np.ndarray,
    sample_diameters_px: float | Sequence[float] | np.ndarray,
    params: RingParams | None = None,
    valid_mask: np.ndarray | None = None,
) -> list[SpotRing]:
    """Ring (inner, outer) for each spot; `sample_diameters_px` is one value or one per spot."""
    params = params or RingParams()
    if params.inner_mode not in INNER_MODES:
        raise ValueError(f"unknown inner mode {params.inner_mode!r}; choose one of {INNER_MODES}")
    if params.thickness_mode not in THICKNESS_MODES:
        raise ValueError(f"unknown thickness mode {params.thickness_mode!r}; choose one of {THICKNESS_MODES}")
    centers = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    diameters = np.broadcast_to(np.asarray(sample_diameters_px, dtype=np.float64), (len(centers),)).copy()
    if len(centers) == 0:
        return []
    if not np.all(diameters > 0):
        raise ValueError("sample diameters must be positive")

    if params.inner_mode == "ratio":
        out = []
        for d in diameters:
            inner, outer = ring_from_inner(params.inner_ratio * d, d, params)
            out.append(SpotRing(inner, outer, math.nan))
        return out

    image = np.asarray(contrast, dtype=np.float32)
    rough_r = float(np.median(diameters)) / 2.0
    r_max = 2.2 * rough_r if params.max_radius_px is None else max(float(params.max_radius_px), 1.3 * rough_r)
    radii = np.arange(0.0, r_max + _RADIAL_STEP_PX, _RADIAL_STEP_PX)
    angles = np.linspace(0.0, 2.0 * math.pi, _ANGLES, endpoint=False)
    samples = polar_samples(image, centers, radii, angles, valid_mask)  # (N, nr, na)
    pixel_samples = polar_samples(image, centers, radii[radii >= 1.2 * rough_r], angles, valid_mask, order=0)
    out = []
    for index in range(len(centers)):
        out.append(_measure_one(samples[index], pixel_samples[index], radii, diameters[index], params))
    return out


def _measure_one(samples: np.ndarray, pixel_outer: np.ndarray, radii: np.ndarray, sample_d: float, params: RingParams) -> SpotRing:
    sample_r = sample_d / 2.0
    core = samples[radii < 0.5 * sample_r]
    far = samples[radii >= max(1.2 * sample_r, 0.8 * radii[-1])]
    if np.isnan(core).mean() > 0.2:
        return SpotRing(math.nan, math.nan, math.nan, "core outside the image or on invalid pixels")
    if far.size == 0 or np.isnan(far).mean() > 0.5:
        return SpotRing(math.nan, math.nan, math.nan, "background outside the image or on invalid pixels")
    plateau = float(np.nanmedian(core))
    background = float(np.nanmedian(far))
    depth = plateau - background
    if not depth > 0:
        return SpotRing(math.nan, math.nan, math.nan, "no contrast between spot and background")
    pixels = pixel_outer[np.isfinite(pixel_outer)]
    sigma = 1.4826 * float(np.median(np.abs(pixels - np.median(pixels)))) if pixels.size else 0.0
    tolerance = max(params.background_fraction * depth, params.sigma_k * sigma)
    threshold = background + tolerance
    if threshold >= plateau:
        return SpotRing(math.nan, math.nan, math.nan, "tolerance is larger than the spot contrast")

    smooth = ndimage.gaussian_filter1d(np.nan_to_num(samples, nan=background), 1.0 / _RADIAL_STEP_PX, axis=0)
    smooth[np.isnan(samples)] = np.nan
    # per direction: the outermost radius at which the profile is still above the background threshold
    above = np.where(np.isnan(smooth), False, smooth > threshold)
    last = np.where(above.any(axis=0), len(radii) - 1 - np.argmax(above[::-1], axis=0), 0)
    usable_directions = ~np.isnan(samples[-1])  # a direction that leaves the image before the search radius ends is dropped
    if usable_directions.sum() < 0.75 * _ANGLES:
        return SpotRing(math.nan, math.nan, math.nan, "too much of the surroundings is outside the image")
    ends = radii[last[usable_directions]]
    end_radius = float(np.quantile(ends, params.quantile))
    if end_radius >= radii[-1] - 2 * _RADIAL_STEP_PX:
        return SpotRing(math.nan, math.nan, math.nan, "the spot does not fade into the background inside the search radius")
    spot_end = 2.0 * end_radius
    inner, outer = ring_from_inner(spot_end + params.margin_px, sample_d, params)
    return SpotRing(inner, outer, spot_end)


def neighbour_warnings(
    centers_xy: np.ndarray, sample_diameters_px: float | Sequence[float] | np.ndarray, rings: Sequence[SpotRing]
) -> list[str]:
    """Spots whose ring reaches a *neighbouring sample disk* (allowed, but those pixels are excluded from the
    ring in the analysis, so the ring is thinner than it looks). Rings overlapping other rings are fine."""
    from scipy.spatial import cKDTree

    centers = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    if len(centers) < 2:
        return []
    diameters = np.broadcast_to(np.asarray(sample_diameters_px, dtype=np.float64), (len(centers),))
    tree = cKDTree(centers)
    messages: list[str] = []
    for index, ring in enumerate(rings):
        if not ring.ok:
            continue
        reach = ring.outer_diameter_px / 2.0 + float(diameters.max()) / 2.0
        for other in tree.query_ball_point(centers[index], reach):
            if other == index:
                continue
            distance = float(np.hypot(*(centers[index] - centers[other])))
            if distance < ring.outer_diameter_px / 2.0 + diameters[other] / 2.0:
                messages.append(f"Ring {index + 1} reaches the sample disk of {other + 1}.")
                break
    return messages


def summarize_rings(rings: Sequence[SpotRing]) -> dict[str, float]:
    """Array-wide numbers over the measured rings.

    ``inner_max`` is the plain maximum inner diameter (clear of every disk, but one debris-affected spot
    decides it); ``inner_robust`` is the maximum after dropping outliers more than 3 robust sigmas (MAD) above
    the median, which is the value to use for one ring size for the whole array. ``outliers`` counts the dropped."""
    measured = [r for r in rings if r.ok]
    if not measured:
        return {"count": 0.0, "inner_max": math.nan, "inner_robust": math.nan, "inner_median": math.nan, "outer_max": math.nan, "outliers": 0.0}
    inner = np.array([r.inner_diameter_px for r in measured])
    outer = np.array([r.outer_diameter_px for r in measured])
    median = float(np.median(inner))
    sigma = 1.4826 * float(np.median(np.abs(inner - median)))
    keep = inner <= median + 3.0 * max(sigma, 0.25)
    return {
        "count": float(len(measured)),
        "inner_max": float(inner.max()),
        "inner_robust": float(inner[keep].max()),
        "inner_median": median,
        "outer_max": float(outer.max()),
        "outliers": float((~keep).sum()),
    }
