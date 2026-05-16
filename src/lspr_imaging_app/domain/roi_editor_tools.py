from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Callable

import numpy as np

from lspr_imaging_app.domain.models import RoiDefinition


def clamp_center_to_image(
    center_x: float,
    center_y: float,
    size_x: float,
    size_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None,
) -> tuple[float, float]:
    if image_shape is None:
        return float(center_x), float(center_y)
    image_height = int(image_shape[0])
    image_width = int(image_shape[1])
    width = max(float(size_x), 2.0)
    height = max(float(size_y), 2.0)
    x = float(np.clip(float(center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
    y = float(np.clip(float(center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
    return x + width / 2.0, y + height / 2.0


def roi_top_left_from_center(
    center_x: float,
    center_y: float,
    size_x: float,
    size_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None,
) -> tuple[float, float]:
    if image_shape is None:
        return float(center_x) - float(size_x) / 2.0, float(center_y) - float(size_y) / 2.0
    image_height = int(image_shape[0])
    image_width = int(image_shape[1])
    width = max(float(size_x), 2.0)
    height = max(float(size_y), 2.0)
    x = float(np.clip(float(center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
    y = float(np.clip(float(center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
    return x, y


def move_rectangle_roi(
    roi: RoiDefinition,
    *,
    center_x: float,
    center_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
) -> RoiDefinition:
    moved = replace(roi)
    moved.center_x, moved.center_y = clamp_center_to_image(
        center_x,
        center_y,
        float(moved.size_x),
        float(moved.size_y),
        image_shape,
    )
    moved.shape = str(roi.shape)
    return moved


def move_roi_from_template(
    roi: RoiDefinition,
    *,
    center_x: float,
    center_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
) -> RoiDefinition:
    return move_rectangle_roi(roi, center_x=center_x, center_y=center_y, image_shape=image_shape)


def clone_rectangle_template(
    template: RoiDefinition,
    *,
    roi_id: str,
    name: str | None = None,
    center_x: float,
    center_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
) -> RoiDefinition:
    clone = deepcopy(template)
    clone.roi_id = roi_id
    clone.name = name or clone.name or roi_id
    clone.shape = str(template.shape)
    clone.center_x, clone.center_y = clamp_center_to_image(
        center_x,
        center_y,
        float(clone.size_x),
        float(clone.size_y),
        image_shape,
    )
    return clone


def clone_roi_template(
    template: RoiDefinition,
    *,
    roi_id: str,
    name: str | None = None,
    center_x: float,
    center_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
) -> RoiDefinition:
    return clone_rectangle_template(
        template,
        roi_id=roi_id,
        name=name,
        center_x=center_x,
        center_y=center_y,
        image_shape=image_shape,
    )


def create_rois_from_template(
    template: RoiDefinition,
    *,
    roi_id: str,
    name: str | None = None,
    center_x: float,
    center_y: float,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
) -> RoiDefinition:
    return clone_roi_template(
        template,
        roi_id=roi_id,
        name=name,
        center_x=center_x,
        center_y=center_y,
        image_shape=image_shape,
    )


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


def clone_rectangle_template_grid(
    template: RoiDefinition,
    *,
    rows: int,
    cols: int,
    spacing_x: float,
    spacing_y: float | None = None,
    anchor_center_x: float = 0.0,
    anchor_center_y: float = 0.0,
    start_index: int = 1,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
    roi_id_factory: Callable[[int], str] | None = None,
) -> list[RoiDefinition]:
    positions = build_grid_positions(
        rows=rows,
        cols=cols,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        anchor_center_x=anchor_center_x,
        anchor_center_y=anchor_center_y,
    )
    clones: list[RoiDefinition] = []
    for index, (center_x, center_y) in enumerate(positions, start=start_index):
        roi_id = roi_id_factory(index) if roi_id_factory is not None else f"roi_{index}"
        clones.append(
            clone_rectangle_template(
                template,
                roi_id=roi_id,
                name=f"{template.name} {index}" if template.name else roi_id,
                center_x=center_x,
                center_y=center_y,
                image_shape=image_shape,
            )
        )
    return clones


def clone_roi_template_grid(
    template: RoiDefinition,
    *,
    rows: int,
    cols: int,
    spacing_x: float,
    spacing_y: float | None = None,
    anchor_center_x: float = 0.0,
    anchor_center_y: float = 0.0,
    start_index: int = 1,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
    roi_id_factory: Callable[[int], str] | None = None,
) -> list[RoiDefinition]:
    return clone_rectangle_template_grid(
        template,
        rows=rows,
        cols=cols,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        anchor_center_x=anchor_center_x,
        anchor_center_y=anchor_center_y,
        start_index=start_index,
        image_shape=image_shape,
        roi_id_factory=roi_id_factory,
    )


def create_rois_from_template_grid(
    template: RoiDefinition,
    *,
    rows: int,
    cols: int,
    spacing_x: float,
    spacing_y: float | None = None,
    anchor_center_x: float = 0.0,
    anchor_center_y: float = 0.0,
    start_index: int = 1,
    image_shape: tuple[int, int] | tuple[int, int, int] | None = None,
    roi_id_factory: Callable[[int], str] | None = None,
) -> list[RoiDefinition]:
    return clone_roi_template_grid(
        template,
        rows=rows,
        cols=cols,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        anchor_center_x=anchor_center_x,
        anchor_center_y=anchor_center_y,
        start_index=start_index,
        image_shape=image_shape,
        roi_id_factory=roi_id_factory,
    )
