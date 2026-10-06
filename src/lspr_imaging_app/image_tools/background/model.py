"""Background stage settings (sketch §7 "Image Tools" > Background, §10).

Split out of the old app's ``PreprocessingSettings`` grab-bag - see
``image_tools/geometry/model.py``'s docstring for why.

The old app's ``local_reference_normalization_enabled`` toggle is deliberately
not carried over (removed 2026-10-06): nothing ever read it, so it changed no
result. Sessions that still contain it load fine (unknown keys are ignored).
"""

from __future__ import annotations

from dataclasses import dataclass


BINNING_OPTIONS = (1, 2, 4, 8)
SIGMA_PER_BIN = 6.0


def max_binning_for_sigma(sigma_px: float) -> int:
    """Largest binning option that keeps the Gaussian at least `SIGMA_PER_BIN`
    (6) binned cells wide (`bin <= sigma / 6`). Coarser than that and the blur
    barely smooths the binned grid, so the estimate loses accuracy: measured
    on real frames (2026-10-06), quality is unchanged up to sigma/bin = 3 and
    degrades beyond; 6 keeps a safety margin. Sigma 48 -> 8, 24 -> 4, 12 -> 2."""
    limit = max(float(sigma_px), 1.0) / SIGMA_PER_BIN
    return max([option for option in BINNING_OPTIONS if option <= limit], default=1)


@dataclass(frozen=True)
class BackgroundComputationalChange:
    """Background's own computational-change payload (change_events.py's
    two-type pattern, §3) - whole-image scope like
    `GeometryComputationalChange`, no `roi_ids` field. Every field on
    `BackgroundSettings` feeds `flatten_background()`
    (`processing/preprocess.py` on `develop`/`main`), so unlike Geometry
    there is no cosmetic half here - one payload type is enough."""

    reason: str  # currently always "flatten_background_settings" - one combined command, see module.py


@dataclass(slots=True)
class BackgroundSettings:
    flatten_background_enabled: bool = False
    flatten_background_sigma_px: float = 48.0
    flatten_background_binning: int = 8
    flatten_background_exclude_area_rois: bool = True
    flatten_background_exclude_mask: bool = False
    flatten_background_exclusion_dilation_px: int = 0
    flatten_background_mode: str = "divide"
    """How the estimate is removed: always "divide" (image / background *
    baseline - illumination is a gain). Not a user choice; it is recorded in
    the analysis fingerprint so cells computed by the earlier subtraction
    (before 2026-10-06, no such field) are recognised as stale."""
