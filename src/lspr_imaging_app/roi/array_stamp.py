"""Turn a manual array layout into new ROIs and a recipe (pure: no Qt, no undo; `RoiToolbox.stamp_array` stores the result).

The selection (the *seed*) stays where it is and keeps its ids; every other node of the lattice gets a copy of each seed ROI: same shape, rim and role,
no label, colour or group (what a copy is: `roi/clipboard.py`). Links between seed ROIs are made again between the copies of one node.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.roi_model import AreaRoi, RoiArrayGroup, RoiGeometry
from .array_layout import ArrayLayout, ArraySpec, array_layout, numbering_order
from .clipboard import RoiClip
from .ordering import renumbering

ROLE_REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class StampPlan:
    layout: ArrayLayout
    new_rois: tuple[AreaRoi, ...]
    """The copies, node by node in row-major order (the seed node left out), each node in the order of the seed ids."""
    recipe: RoiArrayGroup
    id_map: dict[int, int]
    """``{id: new id}`` for the array's ROIs (the seed and the copies): in numbering order (`ArraySpec.number_by` and start corner) the ROIs take, one by one,
    the ids they hold together. Equal ids (nothing to renumber) are included."""


def plan_stamp(
    clip: RoiClip,
    seed_ids: tuple[int, ...],
    spec: ArraySpec,
    *,
    first_id: int,
    array_id: str,
    label: str,
    place: Callable[[RoiGeometry, float, float], RoiGeometry],
) -> StampPlan:
    """Where every copy goes and what it is. ``clip``: the seed ROIs (`RoiToolbox.copy_rois`, ascending id), ``seed_ids`` the same ids in the
    same order; ``first_id``: the id of the first copy; ``place(geometry, x, y)``: the toolbox's rule for putting a geometry's centre at a point
    (a pixel map moves by whole pixels). Raises ``ValueError`` for an invalid spec."""
    seed_xy = [(item.geometry.center_x, item.geometry.center_y) for item in clip.rois]
    layout = array_layout(spec, seed_xy)
    members = len(clip)
    member_ids: list[int] = []
    new_rois: list[AreaRoi] = []
    next_id = first_id
    for node in range(layout.count):
        if node == layout.seed_node:
            member_ids.extend(seed_ids)
            continue
        node_ids = tuple(range(next_id, next_id + members))
        next_id += members
        member_ids.extend(node_ids)
        for index, item in enumerate(clip.rois):
            x, y = layout.member_xy[node, index]
            placed = place(item.geometry, float(x), float(y))
            links = tuple(sorted(node_ids[k] for k in item.linked_to)) if item.role != ROLE_REFERENCE else ()
            new_rois.append(
                AreaRoi(
                    area_roi_id=node_ids[index], center_x=placed.center_x, center_y=placed.center_y, shape=placed.shape, border=placed.border,
                    role=item.role, reference_roi_ids=links, array_id=array_id, created_by="array",
                )
            )
    resolved = spec.resolved()
    in_order = [member_ids[node * members + m] for node in numbering_order(spec, layout.rows, layout.cols) for m in range(members)]
    id_map = renumbering(in_order, sorted(in_order))
    centroid_x, centroid_y = layout.node_xy[layout.seed_node]
    recipe = RoiArrayGroup(
        array_id=array_id,
        label=label,
        rows=layout.rows,
        cols=layout.cols,
        spacing_x_px=float(resolved.pitch_x_px),
        spacing_y_px=float(resolved.pitch_y_px),
        anchor_x_px=float(centroid_x),
        anchor_y_px=float(centroid_y),
        rotation_deg=float(resolved.angle_deg),
        member_area_roi_ids=member_ids,
        spec=spec.to_dict(),
        unit_size=members,
        seed_node=layout.seed_node,
    )
    return StampPlan(layout, tuple(new_rois), recipe, id_map)
