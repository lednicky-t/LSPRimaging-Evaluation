"""The magic wand of the selection toolbar (2026-11-14): one click on the image -> the connected region of pixels that belong
to the feature under it. Pure computation, no Qt (contract ``lspri-science-no-qt``), so it is tested on its own.

Three ways of deciding which pixels belong (``WandMethod``); the maintainer asked for all three:

- **Brightness** (the Photoshop wand): pixels whose smoothed value is within *tolerance* x noise of the clicked pixel's.
  Bigger tolerance = bigger region.
- **Contrast to background**: the image minus its smooth local background (a wide Gaussian, ``background_sigma_px``);
  pixels that stand out by at least *threshold* x noise, on the same side (brighter or darker) as the clicked pixel.
  Bigger threshold = only the strongest part of the feature. Works for bright and dark features, and on a sloping
  illumination where a plain brightness wand fails.
- **Edges**: the region grows from the click through pixels without a strong edge (gradient below *edge* x noise of
  the gradient), then a watershed puts the border on the ridge of the gradient, i.e. at the steepest point of the edge.
  For features with a clear outline but an uneven inside. Bigger edge value = weaker edges are crossed = bigger region.

**Noise units.** Every threshold is in multiples of the noise of the image the wand looks at, so the same number means
the same thing at every wavelength and exposure ("controls mean the same for every selection"). The pixel noise is
estimated robustly from neighbour differences (``estimate_pixel_noise``: median absolute deviation, insensitive to
smooth structure and to the minority of pixels on edges); the noise of the *smoothed* image (and of its gradient) is
that times the exact factor of the filter for white noise (``_white_noise_gain``: the filter applied to a single unit
impulse, root of the sum of squares). Pixel noise that is not white (correlated by the camera or by rotation
resampling) makes the factor only approximate; the tolerance is then still consistent between images, just not an
exact z-score.

**Always connected, no size limit.** The result is the 8-connected region that contains the clicked pixel. There is no
maximum size (2026-11-15, maintainer: with the selection undoable, a wand that ran out into the background is one Ctrl+Z
away, so the "Max size" leak guard that refused such a region was removed). Pixels without a value (NaN: outside the
rotated image) are never selected.

**Post-processing**: ``fill_holes`` (a ring-shaped particle becomes a solid disk) and ``grow_px`` (a margin to cover
the faint halo; the binary-mask counterpart of Photoshop's feathering, which needs soft masks this app does not have).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

__all__ = [
    "PreparedImage",
    "WandMethod",
    "WandResult",
    "WandSettings",
    "estimate_pixel_noise",
    "prepare_image",
    "wand_region",
]

_EIGHT = np.ones((3, 3), dtype=bool)


class WandMethod(enum.Enum):
    BRIGHTNESS = "brightness"
    CONTRAST = "contrast"
    EDGES = "edges"


@dataclass(frozen=True, slots=True)
class WandSettings:
    method: WandMethod = WandMethod.CONTRAST
    brightness_tolerance: float = 10.0  # Brightness: within this many noise sigmas of the clicked value
    contrast_threshold: float = 3.0  # Contrast: stands out from the background by at least this many sigmas
    edge_threshold: float = 4.0  # Edges: a gradient this many sigmas of the gradient noise is an edge
    smoothing_px: float = 1.0  # Gaussian sigma applied before deciding (0 = none)
    background_sigma_px: float = 24.0  # Contrast: width of the local background estimate
    grow_px: int = 0  # margin added around the region afterwards
    fill_holes: bool = True

    def prepare_key(self) -> tuple[float, float | None]:
        """What `prepare_image` depends on (the cache key part besides the image). The background is only made for the
        Contrast method (its wide Gaussian is most of the cost: ~2 s of 2.2 s on a 1 MP plane, measured 2026-11-14)."""
        background = round(float(self.background_sigma_px), 4) if self.method is WandMethod.CONTRAST else None
        return (round(float(self.smoothing_px), 4), background)


@dataclass(frozen=True, slots=True)
class WandResult:
    region: np.ndarray | None  # boolean, image-shaped; None when nothing was selected
    message: str  # what happened, for the status bar

    @property
    def area_px(self) -> int:
        return 0 if self.region is None else int(np.count_nonzero(self.region))


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """The slow part, done once per image and (smoothing, background) pair; every click reuses it."""

    smoothed: np.ndarray  # float32, NaN where the image has no value
    noise: float  # noise sigma of `smoothed`
    background: np.ndarray | None  # float32 smooth background (made for the Contrast method only)
    gradient: np.ndarray  # float32 gradient magnitude of `smoothed` (for Edges), NaN-free (NaN -> +inf: a wall)
    gradient_noise: float  # sigma of one gradient component in a flat, noisy area
    key: tuple[float, float | None]


def estimate_pixel_noise(image: np.ndarray) -> float:
    """Robust per-pixel noise sigma: median absolute deviation of horizontal and vertical neighbour differences,
    scaled to a Gaussian sigma (x1.4826) and divided by sqrt(2) (a difference of two pixels has twice the variance).
    Smooth structure barely changes neighbour differences and edges are a minority of pixels, so this measures the
    noise rather than the picture. 0.0 when there is nothing to measure."""
    values = np.asarray(image, dtype=np.float32)
    parts = []
    for diff in (np.diff(values, axis=1), np.diff(values, axis=0)):
        finite = diff[np.isfinite(diff)]
        if finite.size:
            parts.append(finite)
    if not parts:
        return 0.0
    diffs = np.concatenate(parts)
    mad = float(np.median(np.abs(diffs - np.median(diffs))))
    return 1.4826 * mad / np.sqrt(2.0)


def _normalized_gaussian(image: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing over valid pixels only, so NaN pixels neither spread nor pull on their neighbours (same rule as
    the Mask tab's Threshold tools; kept local, the image tools are independent modules). sigma <= 0 returns a copy."""
    if sigma <= 0.0:
        return np.where(valid, image, np.nan).astype(np.float32)
    values = np.where(valid, image, 0.0).astype(np.float32, copy=False)
    numerator = ndimage.gaussian_filter(values, sigma=sigma, mode="nearest")
    denominator = ndimage.gaussian_filter(valid.astype(np.float32), sigma=sigma, mode="nearest")
    out = np.full(image.shape, np.nan, dtype=np.float32)
    np.divide(numerator, denominator, out=out, where=denominator > 1e-3)
    out[~valid] = np.nan  # a pixel without a value stays without one
    return out


def _white_noise_gain(smoothing_px: float, *, sobel_axis: int | None = None) -> float:
    """How much the filter scales white noise: the filter's response to a unit impulse, root of the sum of squares.
    `sobel_axis` adds a normalised Sobel derivative after the smoothing (the gradient's own noise)."""
    radius = int(np.ceil(4.0 * max(smoothing_px, 0.0))) + 3
    impulse = np.zeros((2 * radius + 1, 2 * radius + 1), dtype=np.float64)
    impulse[radius, radius] = 1.0
    response = ndimage.gaussian_filter(impulse, sigma=smoothing_px, mode="constant") if smoothing_px > 0 else impulse
    if sobel_axis is not None:
        response = ndimage.sobel(response, axis=sobel_axis, mode="constant") / 8.0
    return float(np.sqrt(np.sum(response * response)))


def prepare_image(image: np.ndarray, settings: WandSettings) -> PreparedImage:
    """Smooth, estimate the noise, the background and the gradient. 0.1-1 s on a 1.2 MP plane (the background
    Gaussian dominates); run it off the GUI thread. `image` is not modified."""
    values = np.asarray(image, dtype=np.float32)
    valid = np.isfinite(values)
    smoothing = max(float(settings.smoothing_px), 0.0)
    smoothed = _normalized_gaussian(values, valid, smoothing)
    pixel_noise = estimate_pixel_noise(values)
    noise = pixel_noise * _white_noise_gain(smoothing)
    background = (
        _normalized_gaussian(values, valid, max(float(settings.background_sigma_px), 1.0))
        if settings.method is WandMethod.CONTRAST
        else None
    )
    filled = np.where(valid, smoothed, np.nanmean(smoothed) if valid.any() else 0.0).astype(np.float32)
    gx = ndimage.sobel(filled, axis=1, mode="nearest") / 8.0
    gy = ndimage.sobel(filled, axis=0, mode="nearest") / 8.0
    gradient = np.hypot(gx, gy).astype(np.float32)
    # A pixel without a value, or next to one, is a wall for the Edges method (the gradient there is not measured).
    gradient[~ndimage.binary_erosion(valid, structure=_EIGHT, border_value=1)] = np.inf
    gradient_noise = pixel_noise * _white_noise_gain(smoothing, sobel_axis=1)
    return PreparedImage(smoothed, float(noise), background, gradient, float(gradient_noise), settings.prepare_key())


def _component(candidate: np.ndarray, row: int, col: int) -> np.ndarray | None:
    if not candidate[row, col]:
        return None
    labels, _count = ndimage.label(candidate, structure=_EIGHT)
    return labels == labels[row, col]


def _edges_region(prepared: PreparedImage, row: int, col: int, settings: WandSettings) -> np.ndarray | WandResult:
    from skimage.segmentation import watershed

    gradient = prepared.gradient
    if prepared.gradient_noise <= 0.0:
        return WandResult(None, "Wand: the image has no measurable noise, so no edge strength can be judged.")
    walls = gradient >= float(settings.edge_threshold) * prepared.gradient_noise
    # The edge-free ground, in connected pieces (one label call serves the click's own piece and the background).
    ground_labels, _count = ndimage.label(~walls, structure=_EIGHT)
    if walls[row, col]:
        # A small or rounded feature has no flat inside: everything under the click is slope. Start from the clicked
        # pixel alone and let the watershed find the ridge between it and the background.
        inside = np.zeros(gradient.shape, dtype=bool)
        inside[row, col] = True
        own_piece = 0
    else:
        own_piece = int(ground_labels[row, col])
        inside = ground_labels == own_piece
    # Put the border on the gradient's ridge: watershed between the inside and the background. "Background" is only the
    # edge-free ground that reaches the image's side: islands enclosed by edges (the disks of an array lying inside a
    # dust shadow) are not background and are left for the watershed to share out.
    rim = np.concatenate((ground_labels[0, :], ground_labels[-1, :], ground_labels[:, 0], ground_labels[:, -1]))
    on_rim = np.zeros(int(_count) + 1, dtype=bool)
    on_rim[rim] = True
    on_rim[0] = False  # label 0 = the walls
    on_rim[own_piece] = False  # the click's own piece is `inside`, not background
    background = on_rim[ground_labels]
    if not background.any():
        if own_piece == 0:
            return WandResult(None, "Wand (edges): no background without edges was found around the click.")
        # The click is on the open ground itself (the plain background): there is no outside to put a border against, so
        # the region is that ground up to the edges - the same "whole background" the other methods give for such a click.
        return inside
    markers = np.zeros(gradient.shape, dtype=np.int32)
    markers[background] = 2
    markers[inside] = 1
    finite_gradient = np.where(np.isfinite(gradient), gradient, np.finfo(np.float32).max)
    # The flood only needs the pixels that are not deep inside the background (those can never decide a border): skipping
    # them gives the very same labels, 86 ms instead of ~1 s on a 1.2 MP plane (measured 2026-11-15), so the wand looks at
    # the whole image with no search window.
    deep_background = ndimage.binary_erosion(background, structure=_EIGHT, border_value=1)
    labels = watershed(finite_gradient, markers, mask=~deep_background, connectivity=2)
    region = (labels == 1) & np.isfinite(prepared.smoothed)
    if not region[row, col]:
        return WandResult(None, "Wand (edges): nothing selected.")
    found = _component(region, row, col)
    assert found is not None
    return found


def wand_region(
    image: np.ndarray,
    row: int,
    col: int,
    settings: WandSettings,
    *,
    prepared: PreparedImage | None = None,
) -> WandResult:
    """The region the wand selects for a click on pixel (`row`, `col`) of `image` (2-D; NaN = no value). Pass
    `prepared` (from `prepare_image` with the same image and `settings.prepare_key()`) to skip the slow part."""
    values = np.asarray(image)
    if values.ndim != 2:
        raise ValueError(f"wand_region needs a 2-D image, got shape {values.shape}")
    if not (0 <= row < values.shape[0] and 0 <= col < values.shape[1]):
        return WandResult(None, "Wand: the click is outside the image.")
    if prepared is None or prepared.key != settings.prepare_key() or prepared.smoothed.shape != values.shape:
        prepared = prepare_image(values, settings)
    smoothed = prepared.smoothed
    if not np.isfinite(smoothed[row, col]):
        return WandResult(None, "Wand: the clicked pixel has no value (outside the rotated image).")
    finite = np.isfinite(smoothed)

    if settings.method is WandMethod.EDGES:
        outcome = _edges_region(prepared, row, col, settings)
        if isinstance(outcome, WandResult):
            return outcome
        region = outcome
    else:
        if prepared.noise <= 0.0:
            return WandResult(None, "Wand: the image has no measurable noise, so no tolerance can be judged.")
        if settings.method is WandMethod.BRIGHTNESS:
            limit = float(settings.brightness_tolerance) * prepared.noise
            with np.errstate(invalid="ignore"):
                candidate = (np.abs(smoothed - smoothed[row, col]) <= limit) & finite
        else:
            assert prepared.background is not None  # the key of a Contrast run includes the background
            residual = smoothed - prepared.background
            seed = float(residual[row, col])
            limit = float(settings.contrast_threshold) * prepared.noise
            if not np.isfinite(seed) or abs(seed) < limit:
                return WandResult(
                    None,
                    f"Wand (contrast): the clicked pixel differs from its background by {abs(seed) / prepared.noise:.1f} sigma, "
                    f"less than the threshold {float(settings.contrast_threshold):g}; nothing selected.",
                )
            with np.errstate(invalid="ignore"):
                candidate = (np.sign(seed) * residual >= limit) & finite
        region = _component(candidate, row, col)
        if region is None:  # cannot happen for a finite seed, kept as a guard
            return WandResult(None, "Wand: nothing selected.")

    if settings.fill_holes:
        region = ndimage.binary_fill_holes(region)
    grow = int(settings.grow_px)
    if grow > 0:
        disk = np.hypot(*np.mgrid[-grow : grow + 1, -grow : grow + 1]) <= grow + 1e-9
        region = ndimage.binary_dilation(region, structure=disk)
    region &= finite
    count = int(np.count_nonzero(region))
    return WandResult(region, f"Wand ({settings.method.value}): selected {count:,} px.")
