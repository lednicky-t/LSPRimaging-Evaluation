"""ROI shapes and borders (phase 2 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``). Pure: no Qt, no files, imports nothing from the app.

A ROI's *shape* is what its own region is: a :class:`Circle`, a :class:`Rectangle` (rotated about its centre) or a :class:`MaskShape` (a pixel
mask). Its *border* is the built-in reference region: the pixels whose distance from the shape's edge is between the **gap** ``g`` and ``g + t``
(``t`` = thickness), measured outwards from the edge (a negative gap starts the border inside the shape). For a circle of radius ``R`` that is the
ring ``R + g <= |p - c| <= R + g + t``, the ring the scan always used; the border is therefore defined from the shape, so it follows the shape
when the shape is resized.

``AreaRoi.border`` is ``None`` for "the shared default border" (``AreaRoiDetectionSettings.default_border()``), a :class:`Border` for the ROI's
own, and ``NO_BORDER`` for none.

Units: pixels, in the processed image space (rotation, flip and crop applied), like every ROI coordinate. Angles: degrees, positive turns the
rectangle clockwise as seen on the screen (the image y axis points down).
"""

from __future__ import annotations

from dataclasses import dataclass

import math

import numpy as np


@dataclass(slots=True, eq=False)
class RoiMask:
    """Cropped boolean mask of an irregular region.

    ``mask`` is cropped to its bounding box, not full-image sized; (x0, y0) is the top-left corner of that box in processed-image pixel
    coordinates. Equal when the corner and the pixels are equal (a plain dataclass ``==`` would compare numpy arrays)."""

    x0: int
    y0: int
    mask: np.ndarray

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RoiMask):
            return NotImplemented
        return self.x0 == other.x0 and self.y0 == other.y0 and np.array_equal(self.mask, other.mask)

    __hash__ = None  # type: ignore[assignment]  # mutable and array-valued


@dataclass(frozen=True, slots=True)
class Circle:
    diameter_px: float


@dataclass(frozen=True, slots=True)
class Rectangle:
    """``width_px`` along the rectangle's own x axis, ``height_px`` along its y axis; both turned by ``angle_deg`` about the ROI centre."""

    width_px: float
    height_px: float
    angle_deg: float = 0.0


@dataclass(frozen=True, slots=True, eq=False)
class MaskShape:
    """A free-form region: its pixels, in the processed-image space the ROI lives in (see :class:`RoiMask`)."""

    mask: RoiMask

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MaskShape):
            return NotImplemented
        return self.mask == other.mask

    __hash__ = None  # type: ignore[assignment]


Shape = Circle | Rectangle | MaskShape

SHAPE_CIRCLE, SHAPE_RECTANGLE, SHAPE_MASK = "circle", "rectangle", "mask"
SHAPE_KINDS = (SHAPE_CIRCLE, SHAPE_RECTANGLE, SHAPE_MASK)


def shape_kind(shape: Shape) -> str:
    """``"circle"``, ``"rectangle"`` or ``"mask"``."""
    if isinstance(shape, Circle):
        return SHAPE_CIRCLE
    if isinstance(shape, Rectangle):
        return SHAPE_RECTANGLE
    if isinstance(shape, MaskShape):
        return SHAPE_MASK
    raise TypeError(f"not a ROI shape: {shape!r}")


@dataclass(frozen=True, slots=True)
class Border:
    """The built-in reference region of a shape: ``gap_px`` from its edge, ``thickness_px`` wide (>= 0). ``enabled=False``: no border."""

    gap_px: float
    thickness_px: float
    enabled: bool = True

    def __post_init__(self) -> None:
        if not (float(self.thickness_px) >= 0.0):  # also rejects NaN
            raise ValueError(f"a border's thickness must be >= 0, got {self.thickness_px!r}")

    @classmethod
    def from_ring_diameters(cls, sample_diameter_px: float, inner_diameter_px: float, outer_diameter_px: float) -> Border:
        """The border of a circle of ``sample_diameter_px`` whose ring runs from ``inner_diameter_px`` to ``outer_diameter_px`` (as ROIs
        were described before gap + thickness): same clamping as the old ring (inner >= 0, outer >= inner), and arithmetic arranged so that
        :func:`ring_radii` gives back exactly those radii (exact whenever the subtractions are, i.e. for any ordinary ring; tested)."""
        sample_radius = max(float(sample_diameter_px), 0.0) / 2.0
        inner_radius = max(float(inner_diameter_px), 0.0) / 2.0
        outer_radius = max(float(outer_diameter_px) / 2.0, inner_radius)
        return cls(inner_radius - sample_radius, outer_radius - inner_radius)


NO_BORDER = Border(0.0, 0.0, enabled=False)


def effective_border(own: Border | None, default: Border) -> Border:
    """The border a ROI uses: its own, else the shared default (``None`` means "follow the default")."""
    return default if own is None else own


def ring_radii(circle: Circle, border: Border) -> tuple[float, float]:
    """``(inner_radius, outer_radius)`` of a circle's border ring, in pixels: ``R + g`` (never below 0) and that plus the thickness. The one
    place the ring radii are computed, in this order, so every reader (scan, drawing, tables) gets the same float64 numbers."""
    inner = max(max(float(circle.diameter_px), 0.0) / 2.0 + float(border.gap_px), 0.0)
    return inner, inner + float(border.thickness_px)


def ring_diameters(circle: Circle, border: Border) -> tuple[float, float]:
    """The same ring as ``(inner_diameter, outer_diameter)``: what the ROI table and the ring-resize gestures show."""
    inner, outer = ring_radii(circle, border)
    return inner * 2.0, outer * 2.0


def border_from_edit(circle: Circle, inner_diameter_px: float, outer_diameter_px: float) -> Border:
    """A circle's own border from a ring edit given as diameters (a table cell, a gesture)."""
    return Border.from_ring_diameters(circle.diameter_px, inner_diameter_px, outer_diameter_px)


def reach_radius(shape: Shape, center_x: float, center_y: float) -> float:
    """Radius of the circle about ``(center_x, center_y)`` that holds the whole shape: a circle's radius, a rectangle's half diagonal, and for
    a mask the distance to the far corner of its bounding box. For code that only needs "how far from the centre can this ROI reach"
    (a background exclusion zone, a read box), never for deciding which pixels belong to the ROI."""
    if isinstance(shape, Circle):
        return float(shape.diameter_px) / 2.0
    if isinstance(shape, Rectangle):
        return math.hypot(float(shape.width_px), float(shape.height_px)) / 2.0
    height, width = shape.mask.mask.shape[:2]
    left, top = float(shape.mask.x0) - 0.5, float(shape.mask.y0) - 0.5
    return math.hypot(max(abs(left - center_x), abs(left + width - center_x)), max(abs(top - center_y), abs(top + height - center_y)))


def scaled_shape(shape: Shape, scale: float) -> Shape:
    """The shape with its sizes multiplied by ``scale`` (a circle keeps at least 2 px across): what a ROI looks like after a chromatic
    affine of that largest stretch, for the overlay's drawing copy. A mask is not resampled (its drawing copy is only ever read for
    its centre and reach)."""
    if isinstance(shape, Circle):
        return Circle(max(float(shape.diameter_px) * scale, 2.0))
    if isinstance(shape, Rectangle):
        return Rectangle(float(shape.width_px) * scale, float(shape.height_px) * scale, shape.angle_deg)
    return shape


def remap_rectangle(rect: Rectangle, center_x: float, center_y: float, remap_point) -> Rectangle:
    """The rectangle after a rotate / flip / crop edit of the image: ``remap_point(x, y)`` maps a point of the old processed space to the new
    one. The size is unchanged (this pipeline never scales); the angle becomes that of the direction the rectangle's own x axis now
    points in. Exact for rotation, flip and crop (orthogonal maps and translations)."""
    angle = math.radians(float(rect.angle_deg))
    x0, y0 = remap_point(center_x, center_y)
    x1, y1 = remap_point(center_x + math.cos(angle), center_y + math.sin(angle))
    return Rectangle(rect.width_px, rect.height_px, math.degrees(math.atan2(y1 - y0, x1 - x0)))


def nominal_diameter(shape: Shape, center_x: float, center_y: float) -> float:
    """One diameter-like number for a ROI of any shape (twice its reach radius; a circle's own diameter), for code that only needs a size
    to scale a grouping band, a label spacing or a default (never to decide which pixels belong to the ROI)."""
    return 2.0 * reach_radius(shape, center_x, center_y)
