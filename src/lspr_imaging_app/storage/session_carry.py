"""Carrying a session into the coordinates of an exported dataset (2026-10-22). Design record:
`docs/session_carry_export_2026-10-22.md`.

The OME-Zarr export writes the *processed* image: rotated, flipped, cropped, then placed in a frame whose origin may sit left
of / above the image (padding to align the chunk grid to the ROIs). Everything the user built on the source dataset (ROIs,
masks, chromatic correction, mask pointers) is expressed in the source's pixels, so it would be wrong, and silently so, on the
new pixel grid. This module moves it, so the exported dataset opens with a session that already fits.

**Nothing here is recalculated** (priority 1, correctness; 2, reproducibility). What each part needs follows from the space
it lives in (`docs/image_tools_coordinate_spaces.md`):

- *ROIs and chromatic models / landmarks live in processed space.* The export's pixels **are** processed space, shifted by the
  frame origin, so they move by exactly that shift. The chromatic warp is applied after the rotation, so its affine is
  conjugated by the same translation: ``A'(q) = A(q + o) - o``. Nothing is refitted.
- *Masks (and mask pointers' contours) live in raw space.* They are resampled once, with the same nearest-neighbour warp the
  app already applies at every use (`apply_spatial_mask`), so the masked pixels are the ones the source session masked.
- *Geometry becomes the identity* (rotation 0, no flip, no crop): that work is baked into the pixels. Calibration stays.
- Background, mask, detection, metric and statistics settings, frame roles and the selection do not depend on a pixel
  position and are copied.

The functions are **pure and Qt-free**, take the source state and return a new one; the input is never changed. A caller on
the GUI thread first makes `snapshot_state` (ROIs, groups and arrays are mutable objects the toolbox edits in place), then the
rest may run on any thread.

Not carried: analysis results (`analysis/data.h5`; a cell's input pixels changed by a rounding step, so each cell would have to
be recomputed anyway, and the rewrite does not produce any yet) and the undo history.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..image_tools.chromatic.model import (
    ChromaticLandmarkObservation,
    ChromaticSettings,
    ChromaticTransformModel,
    GridBoundsDefinition,
)
from ..image_tools.geometry.model import CropDefinition, GeometrySettings
from ..image_tools.geometry.transform import apply_spatial_mask, raw_to_processed_affine_xy, spatial_output_shape
from ..image_tools.mask.mask_pointers_state import MaskPointer, MaskPointersState
from ..image_tools.mask.model import MaskChange
from ..image_tools.mask.transient_detection import ContourMask
from ..progress import StageProgress
from ..roi.model import AreaRoi, RoiArrayGroup, RoiMask
from .session import SessionState, write_json_file

logger = logging.getLogger(__name__)

ORIGIN_FILE = "derived_from.json"
ORIGIN_SCHEMA_NAME = "lspri_session_origin"
ORIGIN_SCHEMA_VERSION = "1.0"

_IDENTITY_TOLERANCE = 1e-9


# -- the transform ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportTransform:
    """How the export maps the source's raw pixels onto the stored grid: the session's geometry, the raw plane's shape and the
    export frame (``(origin_x, origin_y, width, height)`` in the exported image's own pixels, origin negative = padding;
    ``None`` = the whole image, unshifted). Plain data, so a worker thread can hold it."""

    geometry: GeometrySettings
    raw_shape: tuple[int, int]
    frame: tuple[int, int, int, int] | None = None

    @property
    def processed_shape(self) -> tuple[int, int]:
        """(height, width) after rotation / flip / crop (the raw shape when the image tools are off)."""
        if not self.geometry.image_tools_enabled:
            return (int(self.raw_shape[0]), int(self.raw_shape[1]))
        height, width = spatial_output_shape(self.raw_shape, self.geometry)
        return (int(height), int(width))

    @property
    def origin(self) -> tuple[int, int]:
        """Where the stored grid's (0, 0) sits in processed space."""
        return (0, 0) if self.frame is None else (int(self.frame[0]), int(self.frame[1]))

    @property
    def stored_shape(self) -> tuple[int, int]:
        """(height, width) of the stored image."""
        if self.frame is None:
            return self.processed_shape
        return (int(self.frame[3]), int(self.frame[2]))

    def raw_to_processed(self) -> np.ndarray:
        return raw_to_processed_affine_xy(self.raw_shape, self.geometry)

    def raw_to_stored(self) -> np.ndarray:
        """2x3 affine from a raw pixel (x, y) to the stored grid."""
        matrix = np.array(self.raw_to_processed(), dtype=np.float64)
        matrix[0, 2] -= self.origin[0]
        matrix[1, 2] -= self.origin[1]
        return matrix

    @property
    def moves_pixels(self) -> bool:
        """True when rotation / flip / crop actually change the raw grid (so a raw mask has to be resampled); False for a pure
        shift by the frame origin."""
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        return self.processed_shape != tuple(self.raw_shape) or not np.allclose(
            self.raw_to_processed(), identity, atol=_IDENTITY_TOLERANCE
        )

    @property
    def lossless(self) -> bool:
        """Whether the stored grid holds the whole processed image (a frame may trim it)."""
        ox, oy = self.origin
        height, width = self.stored_shape
        processed_h, processed_w = self.processed_shape
        return ox <= 0 and oy <= 0 and ox + width >= processed_w and oy + height >= processed_h

    def point_from_processed(self, x: float, y: float) -> tuple[float, float]:
        return (float(x) - self.origin[0], float(y) - self.origin[1])

    def warp_raw_mask(self, mask: np.ndarray) -> np.ndarray:
        """A raw-space boolean mask on the stored grid: resampled like the app does at every use (nearest neighbour), then cut
        to / padded into the frame."""
        if tuple(mask.shape) != tuple(self.raw_shape):
            raise ValueError(f"mask shape {mask.shape} is not the raw plane's {tuple(self.raw_shape)}")
        processed = apply_spatial_mask(mask, self.geometry) if self.moves_pixels else np.asarray(mask, dtype=bool)
        return _embed(processed, self.origin, self.stored_shape)

    def describe(self) -> dict:
        """JSON-ready record of the transform (for `derived_from.json`)."""
        geometry = self.geometry
        crop = geometry.crop
        return {
            "image_tools_applied": bool(geometry.image_tools_enabled),
            "rotation_angle_deg": float(geometry.rotation_angle_deg),
            "flip_horizontal": bool(geometry.flip_horizontal),
            "flip_vertical": bool(geometry.flip_vertical),
            "crop": (
                {"x": int(crop.x), "y": int(crop.y), "width": int(crop.width), "height": int(crop.height)}
                if crop.enabled and crop.width > 0 and crop.height > 0
                else None
            ),
            "raw_shape_hw": [int(self.raw_shape[0]), int(self.raw_shape[1])],
            "processed_shape_hw": list(self.processed_shape),
            "frame": (
                None
                if self.frame is None
                else {
                    "origin_x": int(self.frame[0]),
                    "origin_y": int(self.frame[1]),
                    "width": int(self.frame[2]),
                    "height": int(self.frame[3]),
                }
            ),
            "stored_shape_hw": list(self.stored_shape),
            "lossless": bool(self.lossless),
            "affine_raw_to_stored": [[float(v) for v in row] for row in self.raw_to_stored()],
        }


def _embed(plane: np.ndarray, origin: tuple[int, int], shape: tuple[int, int]) -> np.ndarray:
    """`plane` (processed space) placed on a stored grid of `shape` whose (0, 0) is `origin`; outside the plane is False."""
    out = np.zeros(shape, dtype=bool)
    ox, oy = origin
    height, width = plane.shape
    x0, y0 = max(ox, 0), max(oy, 0)
    x1, y1 = min(ox + shape[1], width), min(oy + shape[0], height)
    if x1 > x0 and y1 > y0:
        out[y0 - oy : y1 - oy, x0 - ox : x1 - ox] = plane[y0:y1, x0:x1]
    return out


# -- the report --------------------------------------------------------------------------------------------------------------


@dataclass
class CarryReport:
    """What a carry did, for the log, the status text and `derived_from.json`."""

    rois: int = 0
    rois_outside_image: int = 0
    chromatic_models: int = 0
    chromatic_landmarks: int = 0
    masks_carried: int = 0
    masks_dropped: int = 0
    masks_emptied: int = 0  # carried, but nothing of the mask lies inside the stored grid any more
    pointers_carried: int = 0
    pointers_dropped: int = 0
    lossless: bool = True

    def summary(self) -> str:
        parts = [f"{self.rois} ROIs", f"{self.masks_carried} masks", f"{self.chromatic_models} chromatic models"]
        if self.pointers_carried:
            parts.append(f"{self.pointers_carried} mask pointers")
        text = ", ".join(parts)
        lost = []
        if self.masks_dropped:
            lost.append(f"{self.masks_dropped} masks of another size left out")
        if self.pointers_dropped:
            lost.append(f"{self.pointers_dropped} pointers outside the image left out")
        if self.rois_outside_image:
            lost.append(f"{self.rois_outside_image} ROIs now outside the image")
        return text + (" (" + "; ".join(lost) + ")" if lost else "")


@dataclass(frozen=True)
class CarriedSession:
    state: SessionState
    report: CarryReport


# -- the parts ---------------------------------------------------------------------------------------------------------------


def snapshot_state(state: SessionState) -> SessionState:
    """A copy of `state` that later edits in the app cannot change: the mutable objects (ROIs with their dicts, groups, arrays and
    the settings dataclasses) are copied; the immutable ones (mask changes, mask pointers, chromatic models, which the modules replace
    rather than edit) are shared. Cheap (160 ROIs: well under a millisecond each), so it may run on the GUI thread."""
    return replace(
        state,
        geometry=replace(state.geometry, crop=replace(state.geometry.crop)),
        background=replace(state.background),
        mask_settings=replace(state.mask_settings),
        chromatic_settings=replace(
            state.chromatic_settings, chromatic_grid_bounds=replace(state.chromatic_settings.chromatic_grid_bounds)
        ),
        detection_settings=replace(state.detection_settings),
        metric_settings=replace(state.metric_settings),
        statistics_settings=replace(state.statistics_settings),
        rois=tuple(_copy_roi(roi) for roi in state.rois),
        groups=tuple(replace(group, area_roi_ids=list(group.area_roi_ids)) for group in state.groups),
        arrays=tuple(replace(array, member_area_roi_ids=list(array.member_area_roi_ids)) for array in state.arrays),
    )


def _copy_roi(roi: AreaRoi) -> AreaRoi:
    return replace(roi, per_wavelength=None if roi.per_wavelength is None else dict(roi.per_wavelength))


def carried_geometry(geometry: GeometrySettings) -> GeometrySettings:
    """The new session's geometry: what the export baked in is gone; calibration and display choices stay."""
    return replace(
        geometry, rotation_angle_deg=0.0, flip_horizontal=False, flip_vertical=False, crop=CropDefinition()
    )


def _carry_roi(roi: AreaRoi, transform: ExportTransform) -> AreaRoi:
    shift = transform.point_from_processed
    center_x, center_y = shift(roi.center_x, roi.center_y)
    per_wavelength = (
        None
        if roi.per_wavelength is None
        else {key: shift(x, y) for key, (x, y) in roi.per_wavelength.items()}
    )
    return replace(
        roi,
        center_x=center_x,
        center_y=center_y,
        per_wavelength=per_wavelength,
        timeline=None if roi.timeline is None else roi.timeline.mapped(shift),
        sample_mask=_carry_roi_mask(roi.sample_mask, transform),
        reference_mask=_carry_roi_mask(roi.reference_mask, transform),
    )


def _carry_roi_mask(mask: RoiMask | None, transform: ExportTransform) -> RoiMask | None:
    if mask is None:
        return None
    ox, oy = transform.origin
    return RoiMask(x0=int(mask.x0) - ox, y0=int(mask.y0) - oy, mask=mask.mask)


def _carry_array(array: RoiArrayGroup, transform: ExportTransform) -> RoiArrayGroup:
    anchor_x, anchor_y = transform.point_from_processed(array.anchor_x_px, array.anchor_y_px)
    return replace(array, anchor_x_px=anchor_x, anchor_y_px=anchor_y)


def carry_affine(matrix: list[list[float]], origin: tuple[int, int]) -> list[list[float]]:
    """A chromatic 2x3 affine seen from a grid whose (0, 0) sits at `origin` in the old one: ``A'(q) = A(q + o) - o``. The
    linear part is unchanged; the translation absorbs the shift."""
    ox, oy = float(origin[0]), float(origin[1])
    (a, b, tx), (c, d, ty) = ([float(v) for v in row] for row in matrix)
    return [[a, b, tx + a * ox + b * oy - ox], [c, d, ty + c * ox + d * oy - oy]]


def _carry_model(model: ChromaticTransformModel, origin: tuple[int, int]) -> ChromaticTransformModel:
    matrix = carry_affine(model.affine_matrix, origin)
    return replace(
        model,
        affine_matrix=matrix,
        global_shift_x_px=float(matrix[0][2]),
        global_shift_y_px=float(matrix[1][2]),
    )


def _carry_grid_bounds(bounds: GridBoundsDefinition, transform: ExportTransform) -> GridBoundsDefinition:
    """The chromatic landmark search area, shifted and clipped to the stored grid (off when nothing of it is left)."""
    if not bounds.enabled:
        return replace(bounds)
    ox, oy = transform.origin
    height, width = transform.stored_shape
    x0, y0 = max(int(bounds.x) - ox, 0), max(int(bounds.y) - oy, 0)
    x1, y1 = min(int(bounds.x) + int(bounds.width) - ox, width), min(int(bounds.y) + int(bounds.height) - oy, height)
    if x1 <= x0 or y1 <= y0:
        return GridBoundsDefinition()
    return GridBoundsDefinition(x=x0, y=y0, width=x1 - x0, height=y1 - y0, enabled=True)


def _carry_chromatic(
    state: SessionState, transform: ExportTransform
) -> tuple[ChromaticSettings, tuple[ChromaticTransformModel, ...], tuple[ChromaticLandmarkObservation, ...]]:
    settings = replace(
        state.chromatic_settings,
        chromatic_grid_bounds=_carry_grid_bounds(state.chromatic_settings.chromatic_grid_bounds, transform),
    )
    ox, oy = transform.origin
    if (ox, oy) == (0, 0):
        return settings, tuple(state.chromatic_models), tuple(state.chromatic_landmarks)
    models = tuple(_carry_model(model, (ox, oy)) for model in state.chromatic_models)
    landmarks = tuple(replace(lm, x_px=float(lm.x_px) - ox, y_px=float(lm.y_px) - oy) for lm in state.chromatic_landmarks)
    return settings, models, landmarks


def carry_masks(
    changes: tuple[MaskChange, ...],
    transform: ExportTransform,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[tuple[MaskChange, ...], CarryReport]:
    """Every mask change on the stored grid, same frame, same scope, same order. A change whose shape is not the raw plane's (a
    session restored over another dataset) is left out and counted. Progress per mask; `cancelled` is checked at each."""
    stages = StageProgress({"masks": 1.0}, progress, cancelled)
    report = CarryReport()
    carried: list[MaskChange] = []
    total = max(len(changes), 1)
    for index, change in enumerate(changes):
        stages("masks", index / total, f"Mask {index + 1} of {len(changes)}")
        if tuple(change.shape) != tuple(transform.raw_shape):
            report.masks_dropped += 1
            continue
        warped = transform.warp_raw_mask(change.plane_uncached())
        if not warped.any():
            report.masks_emptied += 1
        carried.append(MaskChange(change.frame, change.scope, warped))
        report.masks_carried += 1
    return tuple(carried), report


def _carry_pointer(pointer: MaskPointer, transform: ExportTransform) -> MaskPointer | None:
    """The pointer on the stored grid, or ``None`` when its object lies outside it."""
    event = pointer.event
    height, width = transform.stored_shape
    matrix = transform.raw_to_stored()
    cx, cy = (float(v) for v in matrix @ np.array([event.centroid_xy[0], event.centroid_xy[1], 1.0]))
    if event.contour is not None:
        contour = _carry_contour(event.contour, transform)
        if contour is None:
            return None
        bbox = (contour.x0, contour.y0, contour.x0 + contour.mask.shape[1], contour.y0 + contour.mask.shape[0])
        return replace(
            pointer,
            event=replace(event, centroid_xy=(cx, cy), bbox_xyxy=bbox, contour=contour, area_px=contour.area_px),
        )
    x0, y0, x1, y1 = event.bbox_xyxy
    corners = np.array([[x0, x1, x0, x1], [y0, y0, y1, y1], [1.0, 1.0, 1.0, 1.0]])
    moved = matrix @ corners
    bx0, by0 = max(int(np.floor(moved[0].min())), 0), max(int(np.floor(moved[1].min())), 0)
    bx1, by1 = min(int(np.ceil(moved[0].max())), width), min(int(np.ceil(moved[1].max())), height)
    if bx1 <= bx0 or by1 <= by0:
        return None
    return replace(pointer, event=replace(event, centroid_xy=(cx, cy), bbox_xyxy=(bx0, by0, bx1, by1)))


def _carry_contour(contour: ContourMask, transform: ExportTransform) -> ContourMask | None:
    """A contour (small boolean box in raw space) on the stored grid; ``None`` if nothing of it remains. A pure shift (no
    rotation / flip / crop) moves the box and clips it; otherwise the box is resampled through a raw-sized plane, so the pixels are
    exactly those a mask built from it would get."""
    if contour.mask.size == 0:
        return None
    if not transform.moves_pixels:
        plane = contour.to_array(transform.raw_shape)
        warped = _embed(plane, transform.origin, transform.stored_shape)
    else:
        warped = transform.warp_raw_mask(contour.to_array(transform.raw_shape))
    rows, cols = np.flatnonzero(warped.any(axis=1)), np.flatnonzero(warped.any(axis=0))
    if rows.size == 0:
        return None
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    return ContourMask(x0=x0, y0=y0, mask=warped[y0:y1, x0:x1].copy())


def carry_mask_pointers(
    state: MaskPointersState,
    transform: ExportTransform,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[MaskPointersState, CarryReport]:
    """The pointers on the stored grid. Ids and the scan's info are kept (a gap in the ids is fine); a pointer whose object is
    outside the stored grid is dropped and counted."""
    stages = StageProgress({"pointers": 1.0}, progress, cancelled)
    report = CarryReport()
    carried: list[MaskPointer] = []
    total = max(len(state.records), 1)
    for index, pointer in enumerate(state.records):
        stages("pointers", index / total, f"Mask pointer {index + 1} of {len(state.records)}")
        moved = _carry_pointer(pointer, transform)
        if moved is None:
            report.pointers_dropped += 1
        else:
            carried.append(moved)
            report.pointers_carried += 1
    return MaskPointersState(info=state.info, records=tuple(carried)), report


def carry_session(
    state: SessionState,
    transform: ExportTransform,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> CarriedSession:
    """`state` (a `snapshot_state`) in the stored grid's coordinates. The input is not changed. The masks and the pointers cost
    almost all of the time (a few ms per mask); the rest is instant. ``progress`` gets the overall fraction 0..1."""
    stages = StageProgress({"settings": 0.02, "masks": 0.7, "pointers": 0.28}, progress, cancelled)
    stages("settings", 0.0, "Moving ROIs and chromatic correction")
    chromatic_settings, models, landmarks = _carry_chromatic(state, transform)
    rois = tuple(_carry_roi(roi, transform) for roi in state.rois)
    stored_h, stored_w = transform.stored_shape
    report = CarryReport(
        rois=len(rois),
        rois_outside_image=sum(1 for roi in rois if not (0 <= roi.center_x < stored_w and 0 <= roi.center_y < stored_h)),
        chromatic_models=len(models),
        chromatic_landmarks=len(landmarks),
        lossless=transform.lossless,
    )
    stages("settings", 1.0)

    mask_changes, mask_report = carry_masks(
        state.mask_changes, transform, lambda f, m: stages("masks", f, m), cancelled
    )
    report.masks_carried, report.masks_dropped, report.masks_emptied = (
        mask_report.masks_carried, mask_report.masks_dropped, mask_report.masks_emptied
    )
    pointers, pointer_report = carry_mask_pointers(
        state.mask_pointers, transform, lambda f, m: stages("pointers", f, m), cancelled
    )
    report.pointers_carried, report.pointers_dropped = pointer_report.pointers_carried, pointer_report.pointers_dropped

    carried = replace(
        state,
        geometry=carried_geometry(state.geometry),
        chromatic_settings=chromatic_settings,
        chromatic_models=models,
        chromatic_landmarks=landmarks,
        rois=rois,
        groups=tuple(state.groups),
        arrays=tuple(_carry_array(array, transform) for array in state.arrays),
        mask_settings=replace(state.mask_settings, histogram_mask=None, figure_mask=None),
        mask_changes=mask_changes,
        mask_pointers=pointers,
        mask_versions={},
    )
    return CarriedSession(carried, report)


# -- provenance --------------------------------------------------------------------------------------------------------------


def origin_path(session_root: Path) -> Path:
    """`derived_from.json` sits in the session folder, next to `session/`. The autosave never touches it, so it stays as written."""
    return Path(session_root) / ORIGIN_FILE


def write_origin(
    session_root: Path,
    *,
    source_folder: Path | None,
    source_session_id: str | None,
    transform: ExportTransform,
    report: CarryReport,
    zarr_attributes: dict | None,
) -> Path:
    """Record where a carried session came from and what was done to it: the source dataset and session, every transform
    parameter (so the new pixels can be traced back to the raw ones), what was carried, and the Zarr's own `lspr` attributes
    (chunks, shards, compression, dtype, interpolation, value step ...)."""
    payload = {
        "schema_name": ORIGIN_SCHEMA_NAME,
        "schema_version": ORIGIN_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "dataset_folder": None if source_folder is None else str(source_folder),
            "session_id": source_session_id,
        },
        "transform": transform.describe(),
        "carried": {
            "rois": report.rois,
            "rois_outside_image": report.rois_outside_image,
            "chromatic_models": report.chromatic_models,
            "chromatic_landmarks": report.chromatic_landmarks,
            "masks": report.masks_carried,
            "masks_left_out": report.masks_dropped,
            "masks_empty_after_crop": report.masks_emptied,
            "mask_pointers": report.pointers_carried,
            "mask_pointers_left_out": report.pointers_dropped,
            "not_carried": ["analysis results", "undo history"],
        },
        "zarr": zarr_attributes,
    }
    path = origin_path(session_root)
    write_json_file(path, payload)
    return path


def read_origin(session_root: Path) -> dict | None:
    """The `derived_from.json` of a session, or ``None`` if it has none (it was not made by a carry)."""
    path = origin_path(session_root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_name") != ORIGIN_SCHEMA_NAME:
        raise ValueError(f"{path} is not a session origin file")
    return payload


def read_zarr_attributes(zarr_root: Path) -> dict | None:
    """The export's own ``lspr`` attributes, read straight from its metadata file (no zarr library: this may run beside a writer
    thread, and the invariant is that zarr is only touched by the code built for it). ``None`` if unreadable."""
    for name, key_path in (("zarr.json", ("attributes", "lspr")), (".zattrs", ("lspr",))):
        path = Path(zarr_root) / name
        if not path.is_file():
            continue
        try:
            node = json.loads(path.read_text(encoding="utf-8"))
            for key in key_path:
                node = node[key]
            return dict(node)
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read the export's lspr attributes from %s", path, exc_info=True)
            return None
    return None
