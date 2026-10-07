"""Rotation-by-line math: the angle the two-click rotate tool applies.

The user clicks two points that *should* lie on a horizontal line in the
displayed image (a row of features, a channel edge...). This module turns
those two points into the rotation change that makes them horizontal.

No Qt import allowed in this file (CLAUDE.md testing rule).

**Conventions, verified against the real transform** (not assumed - see
``tests/unit/test_lspri_rewrite_rotation_alignment.py``, which builds a line
of known tilt, applies the returned correction through
``apply_spatial_preprocessing`` and checks the line comes out level):

- Points are ``(x, y)`` = ``(column, row)`` in the *displayed* image, y
  growing downward (image row 0 at the top).
- The tilt of the line is positive when it slopes downward to the right.
- Adding **+tilt** to ``GeometrySettings.rotation_angle_deg`` levels it.
- The displayed image has already been rotated *and then flipped*
  (``transform.py``: rotate -> flip -> crop). A single flip (horizontal XOR
  vertical) mirrors the tilt's sign, so the correction must be negated; both
  flips together are a 180 degree turn, which keeps the tilt as is.

Two solutions exist for any line (rotate by ``t`` or by ``t - 180``, and they
level the line equally well, one just leaves the picture upside down). The
tool wants the smaller one, so the result is folded into (-90, 90].
"""

from __future__ import annotations

import math

MIN_SEGMENT_PX = 2.0
"""Two points closer than this define no usable direction. One pixel of
placement error over 2px is already a ~26 degree error, so anything shorter
is noise rather than a measurement; the tool reports it instead of rotating.
In practice a user picks points far apart, where the same 1px error is a
fraction of a degree."""


def fold_to_half_turn(angle_deg: float) -> float:
    """Fold `angle_deg` into (-90, 90] - the smaller of the two angles that
    are 180 degrees apart. Exactly 90 stays +90 (a perfectly vertical line
    has two equally small solutions; this picks one, deterministically)."""
    folded = math.fmod(angle_deg, 180.0)
    if folded > 90.0:
        folded -= 180.0
    elif folded <= -90.0:
        folded += 180.0
    return folded


def line_tilt_deg(p1: tuple[float, float], p2: tuple[float, float]) -> float | None:
    """Tilt of the line p1 -> p2 from horizontal, in (-90, 90], positive when
    it slopes downward to the right in the displayed image. `None` when the
    points are closer than `MIN_SEGMENT_PX`."""
    dx = float(p2[0]) - float(p1[0])
    dy = float(p2[1]) - float(p1[1])
    if math.hypot(dx, dy) < MIN_SEGMENT_PX:
        return None
    return fold_to_half_turn(math.degrees(math.atan2(dy, dx)))


def rotation_correction_deg(
    p1: tuple[float, float],
    p2: tuple[float, float],
    *,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
) -> float | None:
    """The change to add to ``rotation_angle_deg`` so p1 -> p2 becomes
    horizontal in the displayed image, or `None` if the points are too close
    together (see `MIN_SEGMENT_PX`). `flip_*` are the *current* flip settings
    (the points were clicked on the flipped image)."""
    tilt = line_tilt_deg(p1, p2)
    if tilt is None:
        return None
    mirrored = bool(flip_horizontal) != bool(flip_vertical)
    return -tilt if mirrored else tilt
