"""Which ROI is under a point of the image (phase 2.4 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``). Pure: no Qt, no files.

The Image panel works in **display** coordinates (the frame on screen: the chromatic affine applied, view cells ``[col, col + 1)``); a ROI lives in its
own (reference-frame, pixel-index) coordinates. To ask "is this point in that ROI's region" the display point is pulled back into the ROI's own space
(`display_point_to_source`), and the answer comes from ``roi.rasterize.region_contains``, the region test the scan's own rectangle and mask pixels are
made with. So what the user clicks on is what is measured.

A circle or a rectangle is placed around its display centre like the overlay draws it (``D + L (p - c)``, `D` including a per-wavelength nudge). **``D`` is the
centre as the canvas shows it** (`RoiQueries.view_centers`: the stored centre carried through the affine, plus half a cell, `overlay_geometry.CELL_OFFSET`), the
same coordinates as the pointer ``p``; for these two only the difference ``p - D`` enters, so the shift cancels. A mask is
**not** tied to the ROI's centre (that is only its label point): the scan warps its pixels through the frame's affine, so a point is looked up as
``A^-1 (p - 0.5)`` (the overlay draws the pixels' cells exactly, `overlay_geometry.CELL_OFFSET`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..domain.roi_model import AreaRoi
from ..domain.roi_shapes import Border, MaskShape, Rectangle
from .overlay_geometry import CELL_OFFSET
from .rasterize import region_contains


@dataclass(frozen=True)
class FrameMap:
    """A frame's 2 x 3 chromatic affine with its linear part inverted once: what pointing at a ROI needs. ``inverse`` is ``None`` for a degenerate affine
    (nothing can be pointed at then)."""

    linear: np.ndarray
    inverse: np.ndarray | None
    translation: np.ndarray

    @classmethod
    def of(cls, affine: np.ndarray) -> FrameMap:
        matrix = np.asarray(affine, dtype=np.float64)
        linear = matrix[:2, :2]
        try:
            inverse = np.linalg.inv(linear)
        except np.linalg.LinAlgError:
            inverse = None
        return cls(linear, inverse, matrix[:2, 2] if matrix.shape[1] > 2 else np.zeros(2))


def display_point_to_source(roi: AreaRoi, display_center_xy, frame: FrameMap, x: float, y: float) -> tuple[float, float]:
    """The point ``(x, y)`` of the frame on screen as ``(sx, sy)`` in the ROI's own pixel-index coordinates (`frame.inverse` must not be ``None``)."""
    if isinstance(roi.shape, MaskShape):  # the mask's pixels through the affine, whatever the ROI's centre says
        source = frame.inverse @ (np.array([x - CELL_OFFSET, y - CELL_OFFSET]) - frame.translation)
        return float(source[0]), float(source[1])
    offset = frame.inverse @ np.array([x - float(display_center_xy[0]), y - float(display_center_xy[1])])
    return float(roi.center_x) + float(offset[0]), float(roi.center_y) + float(offset[1])


def display_point_in_region(roi: AreaRoi, region: str, border: Border, display_center_xy, frame: FrameMap, x: float, y: float) -> bool:
    """Is the display point ``(x, y)`` in the ROI's ``region`` (``"sample"`` or ``"border"``)? Any shape; a border only if ``border.enabled``."""
    if frame.inverse is None:
        return False
    sx, sy = display_point_to_source(roi, display_center_xy, frame, x, y)
    return bool(region_contains(roi, region, border, np.array([sx]), np.array([sy]))[0])


# -- the handles of a selected rectangle (phase 3) ------------------------------------------------------------------

ZONE_WIDTH, ZONE_HEIGHT, ZONE_CORNER, ZONE_ROTATE = "rect_w", "rect_h", "rect_wh", "rect_rotate"
"""What a press on a rectangle's handles does: an edge changes the width / the height (about the centre), a corner both, the knob turns it."""
RECTANGLE_ZONES = (ZONE_WIDTH, ZONE_HEIGHT, ZONE_CORNER, ZONE_ROTATE)


def _axes(rect: Rectangle, linear: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The rectangle's own x and y axes as they lie on screen: ``L R(angle) (1, 0)`` and ``L R(angle) (0, 1)`` (not normalised)."""
    angle = math.radians(float(rect.angle_deg))
    c, s = math.cos(angle), math.sin(angle)
    rotation = np.array([[c, -s], [s, c]])
    turned = np.asarray(linear, dtype=np.float64) @ rotation
    return turned[:, 0], turned[:, 1]


HANDLE_NAMES = ("ne", "nw", "se", "sw", "n", "s", "e", "w", "knob")
"""The order of the handles in `rectangle_handle_arrays`: the four corners, the four edge middles, the rotation knob."""
_HANDLE_U = np.array([1.0, -1.0, 1.0, -1.0, 0.0, 0.0, 1.0, -1.0, 0.0])  # each handle's place along the rectangle's own x axis, in half widths
_HANDLE_V = np.array([-1.0, -1.0, 1.0, 1.0, -1.0, 1.0, 0.0, 0.0, -1.0])  # ... and along its y axis, in half heights (the knob is further out: see below)


def rectangle_handle_arrays(widths, heights, angles_deg, display_centers, linear: np.ndarray, knob_display: float) -> np.ndarray:
    """Where the handles of many rectangles are drawn: an ``(N, 9, 2)`` array in display coordinates, the handles in `HANDLE_NAMES` order. One pass of numpy
    arithmetic for all rectangles (the drawing keeps this and paints from it; `rectangle_handle_points` is the one-rectangle form of the same numbers)."""
    widths = np.asarray(widths, dtype=np.float64).reshape(-1)
    heights = np.asarray(heights, dtype=np.float64).reshape(-1)
    angle = np.radians(np.asarray(angles_deg, dtype=np.float64).reshape(-1))
    centres = np.asarray(display_centers, dtype=np.float64).reshape(-1, 2)
    lin = np.asarray(linear, dtype=np.float64)[:2, :2]
    c, s = np.cos(angle), np.sin(angle)
    e_u = np.stack((lin[0, 0] * c + lin[0, 1] * s, lin[1, 0] * c + lin[1, 1] * s), axis=1)  # L R(angle) (1, 0): the rectangle's x axis on screen
    e_v = np.stack((-lin[0, 0] * s + lin[0, 1] * c, -lin[1, 0] * s + lin[1, 1] * c), axis=1)  # L R(angle) (0, 1): its y axis on screen
    half_w, half_h = widths / 2.0, heights / 2.0
    k_ref = float(knob_display) / np.maximum(np.hypot(e_v[:, 0], e_v[:, 1]), 1e-9)  # the knob's distance in the rectangles' own pixels
    u = half_w[:, None] * _HANDLE_U[None, :]
    v = half_h[:, None] * _HANDLE_V[None, :]
    v[:, 8] = -half_h - k_ref
    return centres[:, None, :] + u[:, :, None] * e_u[:, None, :] + v[:, :, None] * e_v[:, None, :]


def rectangle_handle_points(rect: Rectangle, display_center, linear: np.ndarray, knob_display: float) -> dict[str, tuple[float, float]]:
    """Where the handles of a rectangle are drawn, in display coordinates: the four corners ``"ne" "nw" "se" "sw"`` (n = the side of the rectangle's own
    negative y axis, which is up on the screen for an unturned rectangle), the four edge middles ``"n" "s" "e" "w"`` and the rotation ``"knob"``, which sits
    ``knob_display`` (display units: the caller scales a screen distance by the view's pixel size) beyond the middle of the ``"n"`` edge, straight out."""
    points = rectangle_handle_arrays([rect.width_px], [rect.height_px], [rect.angle_deg], [display_center], linear, knob_display)[0]
    return {name: (float(x), float(y)) for name, (x, y) in zip(HANDLE_NAMES, points, strict=True)}


def rectangle_handle_zone(
    rect: Rectangle, display_center, frame: FrameMap, x: float, y: float, tolerance: float, knob_display: float
) -> str | None:
    """Which handle of a (selected) rectangle the display point ``(x, y)`` is on, or ``None``. ``tolerance``: display units, how far from an edge still counts
    (as for a circle's border); the zone reaches ``tolerance`` outward but at most half the side inward, so a small rectangle keeps a middle to grab it
    by. Priority: the knob, then the corners, then the edges. Pure."""
    if frame.inverse is None:
        return None
    angle = math.radians(float(rect.angle_deg))
    c, s = math.cos(angle), math.sin(angle)
    offset = frame.inverse @ np.array([x - float(display_center[0]), y - float(display_center[1])])
    u, v = offset[0] * c + offset[1] * s, offset[1] * c - offset[0] * s  # the point in the rectangle's own axes, reference pixels
    scale = math.sqrt(abs(float(np.linalg.det(frame.linear)))) or 1.0
    tol = float(tolerance) / scale
    hw, hh = float(rect.width_px) / 2.0, float(rect.height_px) / 2.0
    _e_u, e_v = _axes(rect, frame.linear)
    knob_v = -hh - float(knob_display) / max(float(np.hypot(*e_v)), 1e-9)
    if math.hypot(u, v - knob_v) <= tol * 1.3:
        return ZONE_ROTATE
    corner_reach = min(tol * 1.5, 0.5 * math.hypot(hw, hh))  # never reaching the middle: a tiny rectangle keeps somewhere to be grabbed by
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            if math.hypot(u - sx * hw, v - sy * hh) <= corner_reach:
                return ZONE_CORNER
    inner_u, inner_v = min(tol, hw / 2.0), min(tol, hh / 2.0)
    on_width_edge = hw - inner_u <= abs(u) <= hw + tol and abs(v) <= hh + tol
    on_height_edge = hh - inner_v <= abs(v) <= hh + tol and abs(u) <= hw + tol
    if on_width_edge and on_height_edge:
        return ZONE_WIDTH if abs(abs(u) - hw) <= abs(abs(v) - hh) else ZONE_HEIGHT
    if on_width_edge:
        return ZONE_WIDTH
    if on_height_edge:
        return ZONE_HEIGHT
    return None


def rectangle_size_at(
    zone: str, rect_angle_deg: float, display_center, frame: FrameMap, x: float, y: float
) -> dict[str, float]:
    """What dragging a handle to the display point ``(x, y)`` sets, as the keyword arguments of ``RoiToolbox.resize_rectangles`` (without the minimum size, which
    the toolbox enforces): an edge or corner sets the width and / or height to twice the pointer's distance from the centre along the rectangle's own axes (the
    centre stays), the knob sets the angle so that the knob points at the pointer. ``rect_angle_deg`` is the rectangle's angle when the drag began."""
    if frame.inverse is None:
        return {}
    offset = frame.inverse @ np.array([x - float(display_center[0]), y - float(display_center[1])])
    if zone == ZONE_ROTATE:
        return {"angle_deg": math.degrees(math.atan2(offset[0], -offset[1]))}  # the knob lies along (sin a, -cos a)
    angle = math.radians(float(rect_angle_deg))
    c, s = math.cos(angle), math.sin(angle)
    u, v = offset[0] * c + offset[1] * s, offset[1] * c - offset[0] * s
    sizes: dict[str, float] = {}
    if zone in (ZONE_WIDTH, ZONE_CORNER):
        sizes["width_px"] = 2.0 * abs(u)
    if zone in (ZONE_HEIGHT, ZONE_CORNER):
        sizes["height_px"] = 2.0 * abs(v)
    return sizes


# -- the border of a selected rectangle (the band of the built-in reference region) ----------------------------------------

ZONE_BORDER_INNER, ZONE_BORDER_OUTER = "rect_border_inner", "rect_border_outer"
"""What a press on the two edges of a rectangle's border band does: the inner edge changes the gap (the thickness stays), the outer edge the thickness."""
BORDER_ZONES = (ZONE_BORDER_INNER, ZONE_BORDER_OUTER)


def _own_axes(rect: Rectangle, frame: FrameMap, x: float, y: float, display_center) -> tuple[float, float]:
    """The display point ``(x, y)`` in the rectangle's own axes about its centre, in reference pixels (``frame.inverse`` must not be ``None``)."""
    angle = math.radians(float(rect.angle_deg))
    c, s = math.cos(angle), math.sin(angle)
    offset = frame.inverse @ np.array([x - float(display_center[0]), y - float(display_center[1])])
    return float(offset[0] * c + offset[1] * s), float(offset[1] * c - offset[0] * s)


def rectangle_signed_distance(rect: Rectangle, display_center, frame: FrameMap, x: float, y: float) -> float | None:
    """How far the display point ``(x, y)`` is from the rectangle's edge, in reference pixels: positive outside, negative inside (``None`` for a degenerate
    frame). This is the distance a border is measured by (`roi_shapes.Border`: the pixels ``gap`` to ``gap + thickness`` from the edge), so the points at
    one distance outside are the rounded outline `overlay_geometry.rectangle_points` draws for that offset, and inside they are a smaller sharp rectangle."""
    if frame.inverse is None:
        return None
    u, v = _own_axes(rect, frame, x, y, display_center)
    qx, qy = abs(u) - float(rect.width_px) / 2.0, abs(v) - float(rect.height_px) / 2.0
    return float(math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0))


def rectangle_border_zone(
    rect: Rectangle, display_center, frame: FrameMap, x: float, y: float, border: Border, tolerance: float
) -> tuple[str, float, float] | None:
    """Which edge of the rectangle's border band the display point is on: ``(zone, distance from that edge, distance from the rectangle's own edge)`` or
    ``None``. As for a circle's ring, the zone reaches ``tolerance`` (display units) either side of the edge but at most half the band's thickness, so
    the two edges never swallow each other, and a band with no thickness (or a disabled border) has nothing to grab. The two distances are in reference
    pixels, so the caller can let the rectangle's own edge win where it is nearer. Pure."""
    if not border.enabled or float(border.thickness_px) <= 0.0:
        return None
    distance = rectangle_signed_distance(rect, display_center, frame, x, y)
    if distance is None:
        return None
    scale = math.sqrt(abs(float(np.linalg.det(frame.linear)))) or 1.0
    band = min(float(tolerance) / scale, float(border.thickness_px) / 2.0)
    from_inner = abs(distance - float(border.gap_px))
    from_outer = abs(distance - (float(border.gap_px) + float(border.thickness_px)))
    if from_inner <= band and from_inner <= from_outer:
        return ZONE_BORDER_INNER, from_inner, abs(distance)
    if from_outer <= band:
        return ZONE_BORDER_OUTER, from_outer, abs(distance)
    return None


def rectangle_zone_direction(zone: str, rect: Rectangle, display_center, frame: FrameMap, x: float, y: float) -> tuple[float, float]:
    """The direction on the screen along which a drag of ``zone`` moves, for the two-headed arrow cursor (only the line matters, not the sense): a width edge
    along the rectangle's own x axis, a height edge along its y axis, a corner along the diagonal it lies on, a border edge along the outward normal of the
    rectangle where the pointer is (diagonal around a corner). ``(1, 0)`` for a degenerate frame."""
    if frame.inverse is None:
        return 1.0, 0.0
    e_u, e_v = _axes(rect, frame.linear)
    if zone == ZONE_WIDTH:
        return float(e_u[0]), float(e_u[1])
    if zone == ZONE_HEIGHT:
        return float(e_v[0]), float(e_v[1])
    u, v = _own_axes(rect, frame, x, y, display_center)
    sign_u, sign_v = (1.0 if u >= 0.0 else -1.0), (1.0 if v >= 0.0 else -1.0)
    if zone == ZONE_CORNER:
        du, dv = sign_u * float(rect.width_px) / 2.0, sign_v * float(rect.height_px) / 2.0
    else:  # a border edge: the way the distance to the rectangle grows at the pointer
        qx, qy = abs(u) - float(rect.width_px) / 2.0, abs(v) - float(rect.height_px) / 2.0
        if qx > 0.0 and qy > 0.0:
            du, dv = sign_u * qx, sign_v * qy
        elif qx >= qy:
            du, dv = sign_u, 0.0
        else:
            du, dv = 0.0, sign_v
    return float(du * e_u[0] + dv * e_v[0]), float(du * e_u[1] + dv * e_v[1])
