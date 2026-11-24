"""What a copied set of ROIs is (2026-11-24): plain, frozen data, no Qt. `RoiToolbox.copy_rois` makes one, `RoiToolbox.paste_rois` turns it into new ROIs.

A clip keeps what a ROI *is*: its shape, its rim, its role and, between ROIs that were copied together, its links. It does not keep what a ROI is
*called or filed under*: id, label, colours, groups and array membership belong to the ROI that was copied (a pasted ROI gets the next free id and
its own palette colour, like any new ROI). Geometry is the one the ROI had at the cube on screen when it was copied (its timeline is not copied).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.roi_model import RoiGeometry


@dataclass(frozen=True, slots=True)
class ClippedRoi:
    geometry: RoiGeometry  # centre (reference frame, px), shape and rim as copied
    role: str
    linked_to: tuple[int, ...] = ()
    """Where, in ``RoiClip.rois``, the references this sample is linked to are (positions in the clip, not ROI ids). Only links between ROIs copied together."""


@dataclass(frozen=True, slots=True)
class RoiClip:
    rois: tuple[ClippedRoi, ...]
    anchor: tuple[float, float]
    """The middle of the box round the copied centres: the point that lands on the pointer when the clip is pasted, so the arrangement is kept."""

    def __len__(self) -> int:
        return len(self.rois)
