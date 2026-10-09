"""ROI dataclasses (sketch §7 "ROI Toolbox", §10).

Ported near-verbatim from ``domain/models.py`` on ``develop``/``main`` -
``RoiMask``, ``AreaRoi``, ``AreaRoiGroup``, ``RoiArrayGroup``,
``AreaRoiDetectionSettings`` (the ROI Toolbox's own owned state and
detection settings, per CLAUDE.md's "Module boundaries"). No logic changed.

TODO: adopt ``docs/roi_system_roadmap.md``'s ``Pair`` vocabulary
(sample/reference linkage) and geometry-type dispatcher here rather than
keeping the ``sample_*``/``reference_*`` field-pair shape verbatim forever -
this port is a faithful move of the current representation, not yet the
roadmap's redesign.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import NamedTuple

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


SCOPE_PERSISTENT = "persistent"
SCOPE_INDIVIDUAL = "individual"
GEOMETRY_SCOPES = (SCOPE_PERSISTENT, SCOPE_INDIVIDUAL)


class RoiGeometry(NamedTuple):
    """The part of a ROI that can differ from cube to cube (diameters, never radii; pixels)."""

    center_x: float
    center_y: float
    sample_diameter_px: float
    reference_inner_diameter_px: float | None
    reference_outer_diameter_px: float | None


@dataclass(frozen=True, slots=True)
class RoiGeometryChange:
    """One committed edit of a ROI's geometry at a cube (the ROI twin of the mask's `MaskChange`).

    ``scope="persistent"``: this cube and every later cube, until a later persistent change supersedes it.
    ``scope="individual"``: this cube only. By **cube**, not by wavelength: the position is stored in the
    reference frame and the chromatic correction carries it to each wavelength of the cube."""

    cube: int
    scope: str
    geometry: RoiGeometry


@dataclass(frozen=True, slots=True)
class RoiTimeline:
    """The geometry changes of one ROI, immutable (an edit builds a new timeline, so undo just puts the old one back).

    Resolution at a cube (`geometry_at`), the Mask rule: an individual change at exactly that cube wins, else the
    latest persistent change at or before it, else ``None`` (the ROI's base geometry applies)."""

    persistent: tuple[RoiGeometryChange, ...] = ()
    """Sorted by cube, at most one per cube."""
    individual: tuple[RoiGeometryChange, ...] = ()
    """Sorted by cube, at most one per cube."""

    def __bool__(self) -> bool:
        return bool(self.persistent or self.individual)

    def changes(self) -> tuple[RoiGeometryChange, ...]:
        return self.persistent + self.individual

    def geometry_at(self, cube: int) -> RoiGeometry | None:
        for change in self.individual:
            if change.cube == cube:
                return change.geometry
            if change.cube > cube:
                break
        latest: RoiGeometry | None = None
        for change in self.persistent:
            if change.cube > cube:
                break
            latest = change.geometry
        return latest

    def with_change(self, cube: int, scope: str, geometry: RoiGeometry) -> RoiTimeline:
        """A new timeline with this change written (replacing a change of the same scope at the same cube)."""
        if scope not in GEOMETRY_SCOPES:
            raise ValueError(f"scope must be one of {GEOMETRY_SCOPES}, got {scope!r}")
        change = RoiGeometryChange(int(cube), scope, geometry)
        kept = [c for c in (self.persistent if scope == SCOPE_PERSISTENT else self.individual) if c.cube != change.cube]
        merged = tuple(sorted([*kept, change], key=lambda c: c.cube))
        return replace(self, persistent=merged) if scope == SCOPE_PERSISTENT else replace(self, individual=merged)

    def without_cube(self, cube: int) -> RoiTimeline:
        """A new timeline with every change authored at `cube` removed (both scopes)."""
        return RoiTimeline(
            tuple(c for c in self.persistent if c.cube != cube), tuple(c for c in self.individual if c.cube != cube)
        )

    def mapped(self, remap_point: Callable[[float, float], tuple[float, float]]) -> RoiTimeline:
        """The same changes with every centre passed through `remap_point` (rotate / flip / crop follow)."""

        def moved(change: RoiGeometryChange) -> RoiGeometryChange:
            x, y = remap_point(change.geometry.center_x, change.geometry.center_y)
            return RoiGeometryChange(change.cube, change.scope, change.geometry._replace(center_x=x, center_y=y))

        return RoiTimeline(tuple(moved(c) for c in self.persistent), tuple(moved(c) for c in self.individual))

    def cubes(self) -> tuple[int, ...]:
        """Every cube at which a change was authored."""
        return tuple(sorted({c.cube for c in self.changes()}))


@dataclass(slots=True)
class AreaRoi:
    area_roi_id: int
    center_x: float
    center_y: float
    sample_diameter_px: float
    sample_color_hex: str | None = None
    reference_color_hex: str | None = None
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
    timeline: RoiTimeline | None = None
    """Geometry edits by cube (see `RoiTimeline`). ``None`` / empty: the ROI has one geometry for every cube, the fields
    above (its **base** geometry). Identity (id, label, colours, group, array) is never on the timeline."""


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
    sample_diameter_px: float = 20.0
    reference_inner_diameter_px: float = 28.0
    reference_outer_diameter_px: float = 36.0
    ignore_marked_pixels: bool = False
    ignored_intensity_value: float | None = None
    ignored_intensity_min_value: float | None = None
    ignored_intensity_max_value: float | None = None
    array_rows: int = 0
    array_cols: int = 0
    array_spacing_px: int = 0
    # How each ROI pair's masked pixels become the per-wavelength
    # sample/reference value ("mean"/"median"/"trimmed_mean"/"plane_fit"), and
    # how those two values combine into the final value - the actual reduction
    # math lives in analysis/reduction.py (moved out of roi/ 2026-09-21), this
    # is just the selected method name. Shared across every ROI pair - no
    # per-ROI override yet. No trimmed_mean_fraction field: that's a fixed
    # constant (analysis/reduction.py's DEFAULT_TRIMMED_MEAN_FRACTION), not a
    # per-session setting.
    reduction_method: str = "mean"
    formula_key: str = "absorbance"


def base_geometry(roi: AreaRoi) -> RoiGeometry:
    return RoiGeometry(
        roi.center_x, roi.center_y, roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px
    )


def geometry_at(roi: AreaRoi, cube: int | None) -> RoiGeometry:
    """The ROI's geometry at `cube`: the timeline's answer if it has one there, else the base geometry
    (``cube=None``: the base)."""
    if cube is not None and roi.timeline:
        resolved = roi.timeline.geometry_at(int(cube))
        if resolved is not None:
            return resolved
    return base_geometry(roi)


def resolved_at(roi: AreaRoi, cube: int | None) -> AreaRoi:
    """`roi` itself when it has no timeline, else a copy whose geometry fields are the ones valid at `cube`
    (no timeline on the copy). What drawing and analysis read, so they never see the timeline."""
    if not roi.timeline:
        return roi
    g = geometry_at(roi, cube)
    return replace(
        roi,
        center_x=g.center_x,
        center_y=g.center_y,
        sample_diameter_px=g.sample_diameter_px,
        reference_inner_diameter_px=g.reference_inner_diameter_px,
        reference_outer_diameter_px=g.reference_outer_diameter_px,
        timeline=None,
    )
