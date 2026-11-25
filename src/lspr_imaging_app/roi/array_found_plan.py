"""Turn a found array (`array_finder.FinderResult`) into new ROIs and a recipe (pure: no Qt, no undo; `RoiToolbox.place_found_array` stores the result).

The twin of `array_stamp.plan_stamp` for an array the image decided: the selected ROIs (the *seeds*) that sit on the array stay where they are and keep their
ids; every other spot gets a new ROI at the position the finder measured. Each new ROI is

* a copy of the first seed (shape, rim, role) when the finder has seeds and the type is fixed: its size follows the seed (size fixed) or the measured spot
  (circles take the measured diameter, rectangles are scaled by the same ratio; a pixel map is always copied as it is and moved by whole pixels);
* otherwise a circle of the measured diameter whose rim has the default gap and the area of the ROI itself (the same rule as "Rim as large as ROI").

A node with no spot that the finder was told to fill (``FinderSettings.place_missing``) becomes a ROI marked ``inferred``, as Detect marks a spot it did not find.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from ..domain.roi_model import AreaRoi, RoiArrayGroup, RoiGeometry
from ..domain.roi_shapes import Border, Circle, MaskShape, Rectangle
from .array_finder import FinderResult, FinderSettings
from .array_layout import ArraySpec, numbering_order
from .clipboard import ClippedRoi, RoiClip
from .ordering import renumbering
from .rim_area import equal_area_thickness

__all__ = ["FoundPlan", "plan_found"]


@dataclass(frozen=True, slots=True)
class FoundPlan:
    new_rois: tuple[AreaRoi, ...]
    """The ROIs to add, in member order (row-major, seeds left out)."""
    recipe: RoiArrayGroup
    id_map: dict[int, int]
    """``{id: new id}`` for the array's ROIs in numbering order (the seeds' ids and the new ones); equal ids included."""
    seed_ids: tuple[int, ...]
    """The seeds that are members (they join the array; the others stay as they are)."""


def plan_found(
    result: FinderResult,
    settings: FinderSettings,
    clip: RoiClip | None,
    seed_ids: Sequence[int],
    numbering: ArraySpec,
    *,
    first_id: int,
    array_id: str,
    label: str,
    default_gap_px: float,
    place: Callable[[RoiGeometry, float, float], RoiGeometry],
) -> FoundPlan:
    """``clip`` / ``seed_ids``: the guide ROIs (`RoiToolbox.copy_rois`, ascending id), ``None`` / empty without. ``numbering``: only its ``number_*`` fields are used.
    ``place(geometry, x, y)``: the toolbox's rule for putting a geometry's centre at a point. Raises ``ValueError`` when the result holds nothing to make."""
    if result.count == 0:
        raise ValueError("the finder found no spot to make a ROI of.")
    member_of_seed = _match_seeds(result, clip, seed_ids)
    template = _template(clip, result, settings)
    seed_size = float(np.median([s.diameter_px for s in result.seeds])) if result.seeds else math.nan

    new_rois: list[AreaRoi] = []
    ids = np.zeros(result.count, dtype=np.int64)
    next_id = first_id
    for member in range(result.count):
        if member in member_of_seed:
            ids[member] = member_of_seed[member]
            continue
        ids[member] = next_id
        x, y = float(result.member_xy[member, 0]), float(result.member_xy[member, 1])
        geometry, role = _geometry(template, float(result.member_diameter[member]), seed_size, default_gap_px, place, x, y)
        new_rois.append(
            AreaRoi(
                area_roi_id=next_id, center_x=geometry.center_x, center_y=geometry.center_y, shape=geometry.shape, border=geometry.border, role=role,
                inferred=bool(result.is_placed[member]), array_id=array_id, created_by="array",
            )
        )
        next_id += 1

    in_order = _numbering(result, numbering, ids)
    id_map = renumbering(in_order, sorted(in_order))
    anchor = result.origin if result.ordered else result.member_xy[0]
    recipe = RoiArrayGroup(
        array_id=array_id, label=label, rows=int(result.rows), cols=int(result.cols),
        spacing_x_px=float(result.pitch_x_px) if result.ordered else 0.0, spacing_y_px=float(result.pitch_y_px) if result.ordered else 0.0,
        anchor_x_px=float(anchor[0]), anchor_y_px=float(anchor[1]), rotation_deg=float(result.tilt_deg) if result.ordered else 0.0,
        member_area_roi_ids=[int(i) for i in ids],
    )
    return FoundPlan(tuple(new_rois), recipe, id_map, tuple(sorted(member_of_seed.values())))


def _template(clip: RoiClip | None, result: FinderResult, settings: FinderSettings) -> ClippedRoi | None:
    """The ROI the new ones are copies of: the first selected ROI, or (an area selection as the guide) the shape fitted to its blob; ``None``: plain circles."""
    if not settings.fix_type:
        return None
    if clip is not None and len(clip):
        return clip.rois[0]
    if result.seeds and result.seeds[0].shape is not None:
        seed = result.seeds[0]
        return ClippedRoi(RoiGeometry(seed.x, seed.y, seed.shape, seed.border), "sample")
    return None


def _match_seeds(result: FinderResult, clip: RoiClip | None, seed_ids: Sequence[int]) -> dict[int, int]:
    """``{member index: seed id}`` for the seeds that are members (the member the finder marked as that seed's, by distance)."""
    if clip is None or not len(clip):
        return {}
    taken: dict[int, int] = {}
    members = np.flatnonzero(result.is_seed)
    for index, seed_id in enumerate(seed_ids):
        if not len(members):
            break
        centre = clip.rois[index].geometry
        distance = np.hypot(result.member_xy[members, 0] - centre.center_x, result.member_xy[members, 1] - centre.center_y)
        best = int(members[int(np.argmin(distance))])
        if best not in taken and float(distance.min()) <= max(3.0, 0.5 * float(np.median(result.member_diameter))):
            taken[best] = int(seed_id)
    return taken


def _geometry(template, diameter: float, seed_size: float, default_gap_px: float, place, x: float, y: float) -> tuple[RoiGeometry, str]:
    if template is None:
        shape = Circle(float(diameter))
        gap = float(default_gap_px)
        return place(RoiGeometry(x, y, shape, Border(gap, equal_area_thickness(shape, gap))), x, y), "sample"
    source = template.geometry
    shape = source.shape
    ratio = diameter / seed_size if math.isfinite(seed_size) and seed_size > 0 and diameter > 0 else 1.0
    if isinstance(shape, Circle):
        shape = Circle(float(shape.diameter_px) * ratio)
    elif isinstance(shape, Rectangle):
        shape = Rectangle(float(shape.width_px) * ratio, float(shape.height_px) * ratio, float(shape.angle_deg))
    elif not isinstance(shape, MaskShape):
        raise TypeError(f"unknown shape {type(shape).__name__}")
    return place(RoiGeometry(source.center_x, source.center_y, shape, source.border), x, y), template.role


def _numbering(result: FinderResult, numbering: ArraySpec, ids: np.ndarray) -> list[int]:
    """The array's ids in the order the numbering visits them (rows or columns from a corner); a free set is read row by row, top left first."""
    if not result.ordered:
        return [int(i) for i in ids]
    by_node = {int(r) * result.cols + int(c): int(i) for r, c, i in zip(result.member_row, result.member_col, ids, strict=True)}
    return [by_node[node] for node in numbering_order(numbering, result.rows, result.cols) if node in by_node]
