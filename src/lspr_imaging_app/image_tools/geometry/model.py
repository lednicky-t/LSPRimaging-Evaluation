"""Geometry stage settings (sketch §7 "Image Tools" > Geometry, §10).

Split out of the current app's single ``PreprocessingSettings`` (domain/
models.py on ``develop``/``main``), which mixes geometry, background-flatten,
and chromatic-correction fields in one dataclass - see the rewrite build
log's 2026-09-20 "preprocess.py scope-check" entry for the full reasoning.
Only the fields Geometry's own pure-math functions (``transform.py``)
actually read (``image_tools_enabled`` through ``crop``) plus spatial/
calibration metadata with no better home (``display_units`` through
``measurement_anchor2_y_px`` - none of these are read by any pure-math
function, they're display-only, ported here as the closest fit).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GeometryComputationalChange:
    """Geometry's own computational-change payload (change_events.py's
    two-type pattern, §3) - whole-image scope, not per-ROI, so unlike
    `RoiComputationalChange` there is no `roi_ids` field: crop/rotate/flip
    change what every pixel in the processed image is, not a subset of
    ROIs. Colocated here rather than in the shared `change_events.py`
    (that file's own docstring: "each should define its own analogous
    pair, colocated in its own module file")."""

    reason: str  # "image_tools_enabled" | "rotation" | "rotation_fill" | "flip" | "crop"


@dataclass(frozen=True)
class GeometryCosmeticChange:
    """Geometry's cosmetic-change payload - the calibration/scale-bar/
    measurement-anchor fields (`display_units` through
    `measurement_anchor2_y_px`). Never invalidates stored analysis results:
    confirmed by `transform.py`'s pure math never reading any of these
    fields (see this file's own module docstring), matching
    `RoiCosmeticChange`'s "never triggers Analysis Engine recompute"
    contract. Added 2026-09-21 alongside the command methods that emit it -
    left undefined (and unused) in the 2026-09-20 pass that built only the
    computational commands, to avoid a dead type."""

    reason: str  # "measurement_anchors" | "calibration" | "display_units" | "scale_bar_visible"


@dataclass(slots=True)
class CropDefinition:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enabled: bool = False


@dataclass(slots=True)
class GeometrySettings:
    image_tools_enabled: bool = True
    rotation_angle_deg: float = 0.0
    rotation_fill_dark: bool = False
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: CropDefinition = field(default_factory=CropDefinition)
    display_units: str = "px"
    scale_bar_visible: bool = False
    calibration_enabled: bool = False
    microns_per_pixel_x: float = 1.0
    microns_per_pixel_y: float = 1.0
    measurement_anchor1_x_px: float = 0.0
    measurement_anchor1_y_px: float = 0.0
    measurement_anchor2_x_px: float = 100.0
    measurement_anchor2_y_px: float = 0.0
