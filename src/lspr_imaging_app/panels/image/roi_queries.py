"""Questions the Image canvas asks about the ROIs at the frame on screen: which one is under a point, which are inside a rectangle
or touch the area selection, where the selected ones are, and how a display-space point maps to the reference frame.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 2). The code is the code that was in `ImagePanel`; the panel still has
`roi_at` and `rois_in_rect` as its public hit-testing API (tests drive them directly) and forwards to this class. Read-only: nothing here
changes a ROI, drags go through `RoiToolbox`.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ...image_tools import ChromaticModule
from ...roi import RoiToolbox
from ...domain.roi_links import is_reference
from ...domain.roi_shapes import NO_BORDER, Circle, Rectangle, effective_border, reach_radius, ring_diameters
from ...roi.overlay_geometry import CELL_OFFSET
from ...roi.rasterize import rasterize_reference, rasterize_sample, rasterize_sample_for_patch
from ...roi.shape_hits import FrameMap, display_point_in_region, rectangle_border_zone, rectangle_handle_zone
from ...selection import SelectionModule
from .roi_gestures import SelectedApertures


def _border_zone_finder(roi, centre, frame: FrameMap, default_border):
    """``border(x, y, tolerance)`` for a selected rectangle whose border band can be dragged (`rectangle_border_zone`), else ``None``: a reference ROI is one
    region with no border of its own, and a border that is switched off or has no thickness has no edges to grab."""
    border = effective_border(roi.border, default_border)
    if not isinstance(roi.shape, Rectangle) or is_reference(roi) or not border.enabled or border.thickness_px <= 0.0:
        return None
    return lambda x, y, tolerance: rectangle_border_zone(roi.shape, centre, frame, x, y, border, tolerance)


def _stretch(linear: np.ndarray) -> float:
    """The largest stretch of a linear map (at least 1): a region of reach ``r`` around a centre stays within ``r * stretch`` of its display centre."""
    return max(float(np.max(np.linalg.svd(linear, compute_uv=False))), 1.0)


class RoiQueries:
    def __init__(
        self,
        selection: SelectionModule,
        roi_toolbox: RoiToolbox,
        chromatic: ChromaticModule,
        *,
        current_cube: Callable[[], int],
        current_wavelength: Callable[[], float],
        reference_visible: Callable[[], bool],
    ) -> None:
        """``current_cube()`` / ``current_wavelength()``: the frame on screen. ``reference_visible()``: the reference rings are drawn
        (only then can their edge be dragged)."""
        self._selection = selection
        self._roi_toolbox = roi_toolbox
        self._chromatic = chromatic
        self._current_cube = current_cube
        self._current_wavelength = current_wavelength
        self._reference_visible = reference_visible

    def view_centers(self, frame: tuple[int, float], affine: np.ndarray) -> np.ndarray:
        """Where every ROI's centre is **on the canvas** at ``frame``: an (N, 2) array in ``rois()`` order, in the view's coordinates (the pointer's and the
        drawing's). `RoiToolbox.display_positions` are in pixel-index coordinates, the ones the scan measures in; the canvas draws pixel ``(row, col)`` over
        ``[col, col + 1)``, so the two differ by `CELL_OFFSET` (see its docstring). Every drawing of a ROI and every question about where the pointer is
        relative to one goes through here, so a circle outlines the pixels it measures."""
        return self._roi_toolbox.display_positions(frame, affine) + CELL_OFFSET

    def roi_at(self, x: float, y: float) -> int | None:
        """The ROI whose sample aperture contains display-space point
        (x, y), or `None`. Public because it is the one piece of this
        panel's hit-testing worth driving directly from a test - clicking by
        screen coordinate is exactly what CLAUDE.md's testability rule says
        to avoid needing.

        Nearest-center wins when apertures overlap, so a small ROI sitting
        inside a large one stays reachable."""
        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        rois = self._roi_toolbox.rois_at(frame[0])
        if not rois:
            return None
        centers = self.view_centers(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        # a rectangle or a mask is hit within the circle that holds it until the shape UI (plan phase 3) gives each its own test
        radii = np.fromiter((reach_radius(roi.shape, roi.center_x, roi.center_y) for roi in rois), dtype=np.float64, count=len(rois))
        within = distances <= radii
        if not all(isinstance(roi.shape, Circle) for roi in rois):
            # A rectangle or a mask is hit only inside its own region (phase 2.4): the reach circle (stretched by the affine, plus a cell) only
            # says where to look, and the exact test is the one the scan's pixels are made with.
            frame_map = FrameMap.of(affine)
            for index, roi in enumerate(rois):
                if isinstance(roi.shape, Circle):
                    continue
                within[index] = display_point_in_region(roi, "sample", NO_BORDER, centers[index], frame_map, x, y)
        distances = np.where(within, distances, np.inf)
        nearest = int(np.argmin(distances))  # first of equal distances wins, as before
        return rois[nearest].area_roi_id if np.isfinite(distances[nearest]) else None

    def region_at(
        self, x: float, y: float, shape: tuple[int, int], *, sample: bool = True, reference: bool = True
    ) -> tuple[np.ndarray, str] | None:
        """The ROI layer's wand (2026-11-16, `layer_wand.py`): the pixels of the sample disk under display point (x, y), or, when the point
        is on no disk, of the reference ring under it; ``(mask of shape, what it is)`` or ``None``. Only the parts drawn are looked at
        (``sample`` / ``reference``: the ROIs tab's show switches). The pixels are the scan's own (`rasterize_sample` / `rasterize_reference`
        at the frame's chromatic affine), so a selection from them covers what the ROI measures."""
        frame = (self._current_cube(), self._current_wavelength())
        rois = self._roi_toolbox.rois_at(frame[0])
        if not rois:
            return None
        affine = self._chromatic.affine_for(frame)
        if sample:
            roi_id = self.roi_at(x, y)
            if roi_id is not None:
                roi = next(r for r in rois if r.area_roi_id == roi_id)
                return rasterize_sample(roi, shape, affine), f"ROI {roi_id} disk"
        if not reference:
            return None
        detection = self._roi_toolbox.detection_settings()
        default_border = detection.default_border()
        centers = self.view_centers(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        frame_map = FrameMap.of(affine)
        best: tuple[float, int] | None = None
        for index, roi in enumerate(rois):
            border = effective_border(roi.border, default_border)
            if not border.enabled or is_reference(roi):  # a reference ROI is one region: it has no border to point at
                continue
            if isinstance(roi.shape, Circle):  # a circle's border is a ring, found by distance
                inner, outer = ring_diameters(roi.shape, border)
                found = outer > inner and inner / 2.0 <= distances[index] <= outer / 2.0
            else:  # any other shape: the band at gap .. gap + thickness from its edge, by the scan's own region test
                found = display_point_in_region(roi, "border", border, centers[index], frame_map, x, y)
            if found and (best is None or distances[index] < best[0]):
                best = (float(distances[index]), index)
        if best is None:
            return None
        roi = rois[best[1]]
        ring = rasterize_reference(roi, shape, affine, default_border=default_border)
        return ring, f"ROI {roi.area_roi_id} reference {'ring' if isinstance(roi.shape, Circle) else 'border'}"

    def rois_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> set[int]:
        """Ids of the ROIs whose display-space *centre* lies in the rectangle
        (the rubber-band select). Public for the same reason as `roi_at`."""
        rois = self._roi_toolbox.rois()
        if not rois:
            return set()
        frame = (self._current_cube(), self._current_wavelength())
        centers = self.view_centers(frame, self._chromatic.affine_for(frame))
        inside = (centers[:, 0] >= x0) & (centers[:, 0] <= x1) & (centers[:, 1] >= y0) & (centers[:, 1] <= y1)
        return {roi.area_roi_id for roi, hit in zip(rois, inside, strict=True) if hit}

    def rois_touching(self, mask: np.ndarray) -> set[int]:
        """Ids of the ROIs (at the frame on screen) whose sample aperture overlaps at least one ``True`` pixel of *mask*
        (display space, ``mask[row, col]`` covers x in [col, col + 1), y in [row, row + 1)) - inside or partly inside an
        area selection (2026-11-15, maintainer request). Exact disc-square overlap: the point of each pixel's square
        nearest to the ROI centre lies within the sample radius. Only a box around each ROI is looked at."""
        frame = (self._current_cube(), self._current_wavelength())
        rois = self._roi_toolbox.rois_at(frame[0])
        if not rois or not mask.any():
            return set()
        affine = self._chromatic.affine_for(frame)
        centers = self.view_centers(frame, affine)
        stretch = _stretch(np.asarray(affine, dtype=np.float64)[:2, :2])
        rows, cols = mask.shape[:2]
        hits: set[int] = set()
        for roi, (cx, cy) in zip(rois, centers, strict=True):
            radius = reach_radius(roi.shape, roi.center_x, roi.center_y)
            if not (np.isfinite(cx) and np.isfinite(cy)):
                continue
            if not isinstance(roi.shape, Circle):
                # A rectangle or a mask: the pixels it measures against the selected ones (its reach box only says where to look).
                reach = radius * stretch + 2.0
                c0, c1 = max(int(np.floor(cx - reach)), 0), min(int(np.floor(cx + reach)) + 1, cols)
                r0, r1 = max(int(np.floor(cy - reach)), 0), min(int(np.floor(cy + reach)) + 1, rows)
                if c0 < c1 and r0 < r1 and mask[r0:r1, c0:c1].any():
                    own = rasterize_sample_for_patch(roi, (c0, r0), (r1 - r0, c1 - c0), affine)
                    if bool(np.any(own & mask[r0:r1, c0:c1])):
                        hits.add(roi.area_roi_id)
                continue
            c0, c1 = max(int(np.floor(cx - radius)), 0), min(int(np.floor(cx + radius)) + 1, cols)
            r0, r1 = max(int(np.floor(cy - radius)), 0), min(int(np.floor(cy + radius)) + 1, rows)
            if c0 >= c1 or r0 >= r1:
                continue
            box_rows, box_cols = np.nonzero(mask[r0:r1, c0:c1])
            if box_rows.size == 0:
                continue
            x = box_cols + c0
            y = box_rows + r0
            dx = np.clip(cx, x, x + 1) - cx  # nearest point of each pixel's square to the centre
            dy = np.clip(cy, y, y + 1) - cy
            if bool(np.any(dx * dx + dy * dy <= radius * radius)):
                hits.add(roi.area_roi_id)
        return hits

    def selected_apertures(self) -> SelectedApertures | None:
        """The selected ROIs' display centres and diameters, for the border/inside hit test; `None` with no selection."""
        selected = self._selection.selected_roi_ids()
        rois = self._roi_toolbox.rois_at(self._current_cube())
        if not selected or not rois:
            return None
        keep = np.fromiter((roi.area_roi_id in selected for roi in rois), dtype=bool, count=len(rois))
        if not keep.any():
            return None
        frame = (self._current_cube(), self._current_wavelength())
        centers = self.view_centers(frame, self._chromatic.affine_for(frame))[keep]
        chosen = [roi for roi, hit in zip(rois, keep, strict=True) if hit]
        detection = self._roi_toolbox.detection_settings()
        default_border = detection.default_border()
        rings = np.array(
            [
                ring_diameters(roi.shape, effective_border(roi.border, default_border)) if isinstance(roi.shape, Circle) else (0.0, 0.0)
                for roi in chosen
            ],
            dtype=np.float64,
        ).reshape(-1, 2)
        resizable = np.array([isinstance(roi.shape, Circle) for roi in chosen], dtype=bool)  # the aperture handles resize a diameter
        body = None
        handles = None
        borders = None
        if any(isinstance(roi.shape, Rectangle) for roi in chosen):
            frame_for_handles = FrameMap.of(self.current_affine())
            if self._reference_visible():  # an invisible border must not be grabbable (as for a circle's ring)
                found = tuple(
                    _border_zone_finder(roi, centre, frame_for_handles, default_border) for roi, centre in zip(chosen, centers, strict=True)
                )
                borders = found if any(finder is not None for finder in found) else None
            handles = tuple(
                None if not isinstance(roi.shape, Rectangle)
                else (
                    lambda x, y, tolerance, knob, roi=roi, centre=centre: rectangle_handle_zone(
                        roi.shape, centre, frame_for_handles, x, y, tolerance, knob
                    )
                )
                for roi, centre in zip(chosen, centers, strict=True)
            )
        if not resizable.all():
            # Inside a rectangle means inside its region, so the selection can be moved by it. A pixel mask cannot be moved (its pixels stay: `RoiToolbox.move_refusal`),
            # so it gets no inside test and a drag that starts on it is a rubber band, as before. No edge handles for either.
            frame_map = FrameMap.of(self.current_affine())
            body = tuple(
                None if not isinstance(roi.shape, Rectangle)
                else (lambda x, y, roi=roi, centre=centre: display_point_in_region(roi, "sample", NO_BORDER, centre, frame_map, x, y))
                for roi, centre in zip(chosen, centers, strict=True)
            )
        return SelectedApertures(
            ids=np.array([roi.area_roi_id for roi in chosen], dtype=np.int64),
            centers=centers,
            diameters=np.array([roi.shape.diameter_px if isinstance(roi.shape, Circle) else 0.0 for roi in chosen], dtype=np.float64),
            resizable=resizable,
            ring_inner=rings[:, 0],
            ring_outer=rings[:, 1],
            ring_resizable=resizable & bool(self._reference_visible()) & (rings[:, 1] > rings[:, 0]),
            body=body,
            handles=handles,
            borders=borders,
        )

    def current_affine(self) -> np.ndarray:
        frame = (self._current_cube(), self._current_wavelength())
        return np.asarray(self._chromatic.affine_for(frame), dtype=np.float64)

    def display_linear(self) -> np.ndarray:
        """The linear (2x2) part of the shown frame's affine: how a drag in display space maps to the stored ROI centres."""
        return self.current_affine()[:2, :2]

    def display_to_reference(self, x: float, y: float) -> tuple[float, float]:
        """View point (what the pointer reports) -> reference-frame point in pixel-index coordinates (what `RoiToolbox` stores): the cell shift is taken
        off first (`CELL_OFFSET`), then the affine is inverted. The exact inverse of `view_centers`."""
        matrix = self.current_affine()
        linear, shift = matrix[:2, :2], matrix[:2, 2]
        try:
            reference = np.linalg.solve(linear, np.array([x - CELL_OFFSET, y - CELL_OFFSET]) - shift)
        except np.linalg.LinAlgError:
            return x, y
        return float(reference[0]), float(reference[1])
