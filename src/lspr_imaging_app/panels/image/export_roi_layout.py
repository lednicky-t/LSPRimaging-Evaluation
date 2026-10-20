"""The ROI layout in the OME-Zarr export's pixel space, for the Export section's chunk tools (2026-10-20).

The export writes **raw** pixels (`docs/zarr_chunk_planner_2026-10-19.md`, decision 2026-10-20), while ROIs live in
processed space (after rotation/flip/crop). This gathers, from the modules' public queries only:

- each ROI's read box (`dataset.chunk_plan.roi_read_boxes`: sample disk + reference ring, united over the fitted
  chromatic affines when the correction is on), mapped to raw pixels through the inverse of the image tools
  (`chunk_plan.map_boxes`; a rotation widens a box to the axis-aligned box around it);
- the ROI centres in raw pixels (pitch estimate), and the ROI-table groups as index lists.

Gathered on the GUI thread (cheap: integer box arithmetic, ~10 ms for 160 ROIs and 26 affines); the suggestions and
the compression sampling that use it run on a worker (`chunk_layout_dialog.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...dataset import chunk_plan as cp
from ...image_tools.chromatic.affine import invert_affine_matrix
from ...image_tools.geometry.transform import raw_to_processed_affine_xy


@dataclass(frozen=True)
class ExportRoiLayout:
    boxes: np.ndarray
    """(N, 4) half-open read boxes in raw pixels."""
    centers: np.ndarray
    """(N, 2) ROI centres in raw pixels."""
    groups: tuple[tuple[int, ...], ...]
    """ROI-table groups as indices into `boxes`."""
    raw_shape: tuple[int, int]

    @property
    def empty(self) -> bool:
        return not bool(((self.boxes[:, 2] > self.boxes[:, 0]) & (self.boxes[:, 3] > self.boxes[:, 1])).any())


def gather_export_roi_layout(toolbox, geometry, chromatic, raw_shape: tuple[int, int]) -> ExportRoiLayout:
    """``toolbox``: `RoiToolbox`; ``geometry``: `GeometryModule` or None (raw = processed); ``chromatic``:
    `ChromaticModule` or None (no chromatic shift)."""
    rois = tuple(toolbox.rois())
    raw_shape = (int(raw_shape[0]), int(raw_shape[1]))
    if not rois:
        return ExportRoiLayout(np.zeros((0, 4), dtype=np.int64), np.zeros((0, 2)), (), raw_shape)
    settings = toolbox.detection_settings()
    affines = None
    if chromatic is not None and bool(chromatic.settings().chromatic_correction_enabled):
        unique = {tuple(np.round(np.asarray(m.affine_matrix, dtype=np.float64).ravel(), 6)) for m in chromatic.models()}
        affines = [np.array(a).reshape(2, 3) for a in unique] or None
    to_raw = (
        invert_affine_matrix(raw_to_processed_affine_xy(raw_shape, geometry.settings()))
        if geometry is not None
        else np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    )
    # Processed-space boxes are not clipped to any image here (a huge shape): clipping happens once, in raw space.
    huge = (1 << 30, 1 << 30)
    boxes = cp.roi_read_boxes(rois, huge, default_inner_diameter_px=settings.reference_inner_diameter_px,
                              default_outer_diameter_px=settings.reference_outer_diameter_px, affines=affines)
    raw_boxes = cp.map_boxes(boxes, to_raw, raw_shape)
    centres = np.array([(float(r.center_x), float(r.center_y)) for r in rois], dtype=np.float64)
    raw_centres = centres @ to_raw[:, :2].T + to_raw[:, 2]
    index = {r.area_roi_id: i for i, r in enumerate(rois)}
    groups = tuple(
        tuple(index[i] for i in g.area_roi_ids if i in index) for g in toolbox.groups() if any(i in index for i in g.area_roi_ids)
    )
    return ExportRoiLayout(raw_boxes, raw_centres, groups, raw_shape)
