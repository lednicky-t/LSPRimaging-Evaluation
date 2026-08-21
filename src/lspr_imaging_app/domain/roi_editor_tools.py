from __future__ import annotations

from lspr_imaging_app.domain.models import RoiArrayGroup


def build_grid_positions(
    *,
    rows: int,
    cols: int,
    spacing_x: float,
    spacing_y: float | None = None,
    anchor_center_x: float = 0.0,
    anchor_center_y: float = 0.0,
) -> list[tuple[float, float]]:
    if rows <= 0 or cols <= 0:
        return []
    step_y = float(spacing_y if spacing_y is not None else spacing_x)
    start_x = float(anchor_center_x) - float(cols - 1) * float(spacing_x) / 2.0
    start_y = float(anchor_center_y) - float(rows - 1) * step_y / 2.0
    positions: list[tuple[float, float]] = []
    for row in range(rows):
        for col in range(cols):
            positions.append((start_x + col * float(spacing_x), start_y + row * step_y))
    return positions


def build_roi_array_group(
    *,
    array_id: str,
    label: str,
    rows: int,
    cols: int,
    spacing_x: float,
    spacing_y: float | None = None,
    origin_x: float,
    origin_y: float,
    member_area_roi_ids: list[int],
    rotation_deg: float = 0.0,
) -> RoiArrayGroup:
    """Package a grid recipe plus its resulting member IDs into a persisted
    `RoiArrayGroup`. `(origin_x, origin_y)` is the position of the row=0,
    col=0 member (not the array's visual center — unambiguous without also
    knowing rows/cols). `member_area_roi_ids` must already be in row-major
    order (`for row: for col:`), matching both `build_grid_positions` and
    `processing.roi_detection._fit_grid_array`, so the recipe and the
    membership stay consistent.
    """
    return RoiArrayGroup(
        array_id=array_id,
        label=label,
        rows=int(rows),
        cols=int(cols),
        spacing_x_px=float(spacing_x),
        spacing_y_px=float(spacing_y if spacing_y is not None else spacing_x),
        anchor_x_px=float(origin_x),
        anchor_y_px=float(origin_y),
        rotation_deg=float(rotation_deg),
        member_area_roi_ids=list(member_area_roi_ids),
    )
