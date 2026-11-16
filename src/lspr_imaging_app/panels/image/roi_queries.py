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
from ...roi.rasterize import effective_reference_diameters, rasterize_reference, rasterize_sample
from ...selection import SelectionModule
from .roi_gestures import SelectedApertures


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
        centers = self._roi_toolbox.display_positions(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        radii = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois)) / 2.0
        distances = np.where(distances <= radii, distances, np.inf)
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
        inner_default, outer_default = detection.reference_inner_diameter_px, detection.reference_outer_diameter_px
        centers = self._roi_toolbox.display_positions(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        best: tuple[float, int] | None = None
        for index, roi in enumerate(rois):
            if roi.reference_geometry_type == "none":
                continue
            inner, outer = effective_reference_diameters(roi, inner_default, outer_default)
            if outer > inner and inner / 2.0 <= distances[index] <= outer / 2.0 and (best is None or distances[index] < best[0]):
                best = (float(distances[index]), index)
        if best is None:
            return None
        roi = rois[best[1]]
        ring = rasterize_reference(
            roi, shape, affine, default_inner_diameter_px=inner_default, default_outer_diameter_px=outer_default
        )
        return ring, f"ROI {roi.area_roi_id} reference ring"

    def rois_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> set[int]:
        """Ids of the ROIs whose display-space *centre* lies in the rectangle
        (the rubber-band select). Public for the same reason as `roi_at`."""
        rois = self._roi_toolbox.rois()
        if not rois:
            return set()
        frame = (self._current_cube(), self._current_wavelength())
        centers = self._roi_toolbox.display_positions(frame, self._chromatic.affine_for(frame))
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
        centers = self._roi_toolbox.display_positions(frame, self._chromatic.affine_for(frame))
        rows, cols = mask.shape[:2]
        hits: set[int] = set()
        for roi, (cx, cy) in zip(rois, centers, strict=True):
            radius = float(roi.sample_diameter_px) / 2.0
            if not (np.isfinite(cx) and np.isfinite(cy)):
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
        centers = self._roi_toolbox.display_positions(frame, self._chromatic.affine_for(frame))[keep]
        chosen = [roi for roi, hit in zip(rois, keep, strict=True) if hit]
        detection = self._roi_toolbox.detection_settings()
        rings = np.array(
            [
                effective_reference_diameters(roi, detection.reference_inner_diameter_px, detection.reference_outer_diameter_px)
                for roi in chosen
            ],
            dtype=np.float64,
        ).reshape(-1, 2)
        resizable = np.array([roi.sample_geometry_type != "mask" for roi in chosen], dtype=bool)
        return SelectedApertures(
            ids=np.array([roi.area_roi_id for roi in chosen], dtype=np.int64),
            centers=centers,
            diameters=np.array([roi.sample_diameter_px for roi in chosen], dtype=np.float64),
            resizable=resizable,
            ring_inner=rings[:, 0],
            ring_outer=rings[:, 1],
            ring_resizable=resizable & bool(self._reference_visible()) & (rings[:, 1] > rings[:, 0]),
        )

    def current_affine(self) -> np.ndarray:
        frame = (self._current_cube(), self._current_wavelength())
        return np.asarray(self._chromatic.affine_for(frame), dtype=np.float64)

    def display_linear(self) -> np.ndarray:
        """The linear (2x2) part of the shown frame's affine: how a drag in display space maps to the stored ROI centres."""
        return self.current_affine()[:2, :2]

    def display_to_reference(self, x: float, y: float) -> tuple[float, float]:
        """Display-space point -> reference-frame point (what `RoiToolbox` stores), by inverting the affine."""
        matrix = self.current_affine()
        linear, shift = matrix[:2, :2], matrix[:2, 2]
        try:
            reference = np.linalg.solve(linear, np.array([x, y]) - shift)
        except np.linalg.LinAlgError:
            return x, y
        return float(reference[0]), float(reference[1])
