"""ROI dataclasses (sketch §7 "ROI Toolbox", §10).

Ported near-verbatim from ``domain/models.py`` on ``develop``/``main`` -
``RoiMask``, ``AreaRoi``, ``AreaRoiGroup``, ``RoiArrayGroup``,
``AreaRoiDetectionSettings`` (the ROI Toolbox's own owned state and
detection settings, per AGENTS.md's "Module boundaries"). No logic changed.

TODO: adopt ``docs/roi_system_roadmap.md``'s ``Pair`` vocabulary
(sample/reference linkage) and geometry-type dispatcher here rather than
keeping the ``sample_*``/``reference_*`` field-pair shape verbatim forever -
this port is a faithful move of the current representation, not yet the
roadmap's redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class RoiMask:
    """Cropped boolean mask fallback for an irregular sample/reference region.

    ``mask`` is cropped to its bounding box, not full-image sized; (x0, y0) is
    the top-left corner of that box in processed-image pixel coordinates.
    """

    x0: int
    y0: int
    mask: np.ndarray


@dataclass(slots=True)
class AreaRoi:
    area_roi_id: int
    center_x: float
    center_y: float
    sample_radius_px: float
    sample_color_hex: str | None = None
    reference_color_hex: str | None = None
    sample_diameter_px: float | None = None
    reference_inner_diameter_px: float | None = None
    reference_outer_diameter_px: float | None = None
    score: float = 0.0
    support_mean_radius_px: float = 0.0
    support_radius_std_px: float = 0.0
    support_value_mean: float = 0.0
    support_value_std: float = 0.0
    quality_score: float = 0.0
    inferred: bool = False
    # Geometry escape hatch: "circle"/"annulus" (default) reproduce the existing
    # radius-based behavior exactly; "mask" uses sample_mask/reference_mask instead.
    sample_geometry_type: str = "circle"
    sample_mask: RoiMask | None = None
    reference_geometry_type: str = "annulus"
    reference_mask: RoiMask | None = None
    array_id: str | None = None
    label: str | None = None
    created_by: str = "user"
    notes: str | None = None
    # Per-wavelength position overrides, keyed by the same (spectral_cube_index,
    # wavelength_nm) tuple used everywhere else as the image identity. Only one
    # position is stored for an ROI at all - center_x/center_y, on the reference
    # image. Every other (cube, wavelength) position is computed on demand from that center
    # through the chromatic-correction affine for that key and is never written back
    # here. The one exception is a manual nudge while viewing a non-reference
    # wavelength, which writes into this dict instead of center_x/center_y and
    # survives until the next chromatic re-fit clears it out from under it. So in
    # practice this dict is empty for the overwhelming majority of ROIs and only
    # ever holds a handful of deliberate manual edits.
    per_wavelength: dict[tuple[int, float], tuple[float, float]] | None = None


@dataclass(slots=True)
class AreaRoiGroup:
    group_id: str
    name: str
    sample_color_hex: str = "#f59e0b"
    reference_color_hex: str = "#38bdf8"
    area_roi_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RoiArrayGroup:
    """Persisted grid recipe tying together AreaRoi members stamped as a periodic array.

    This is the recipe, not a duplicate of member geometry: rows/cols/spacing/anchor
    can be edited later to regenerate or nudge the whole array as a unit.
    (anchor_x_px, anchor_y_px) is the position of the row=0, col=0 member, not
    the array's visual center.
    """

    array_id: str
    label: str
    rows: int
    cols: int
    spacing_x_px: float
    spacing_y_px: float
    anchor_x_px: float
    anchor_y_px: float
    rotation_deg: float = 0.0
    member_area_roi_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class AreaRoiDetectionSettings:
    mode: str = "dark"
    intensity_min_value: float | None = None
    intensity_max_value: float | None = None
    mask_mode: str = "absolute"
    mask_profile_sigma_px: float = 48.0
    mask_relative_threshold_fraction: float = 0.18
    mask_local_contrast_sigma_px: float = 8.0
    mask_local_contrast_z_threshold: float = 3.0
    sample_radius_px: float = 10.0
    reference_inner_radius_px: float = 14.0
    reference_outer_radius_px: float = 18.0
    ignore_marked_pixels: bool = False
    ignored_intensity_value: float | None = None
    ignored_intensity_min_value: float | None = None
    ignored_intensity_max_value: float | None = None
    array_rows: int = 0
    array_cols: int = 0
    array_spacing_px: int = 0
    # ROI's math: how each ROI pair's masked pixels become the per-wavelength
    # sample/reference value ("mean"/"median"/"trimmed_mean"/"plane_fit"), and
    # how those two values combine into the final value. Shared across every
    # ROI pair - no per-ROI override yet, see roi/reduction.py. No
    # trimmed_mean_fraction field: that's a fixed constant (roi/reduction.py's
    # DEFAULT_TRIMMED_MEAN_FRACTION, once ported), not a per-session setting.
    reduction_method: str = "mean"
    formula_key: str = "absorbance"
