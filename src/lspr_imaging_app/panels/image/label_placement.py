"""Where a label sits around the circle it belongs to: one of nine places, the cells of a 3 x 3 grid.

The middle cell centres the label on the circle; the eight around it put the label just *outside* the circle
(above, below, left, right, or off one of the four corners). Shared by the ROI labels (`roi_label_overlay.py`) and
the group labels (`group_label_overlay.py`); picked with `label_placement_picker.py`.

Pure geometry, no Qt: `placement_origin` works on plain floats, so the ROI labels can place hundreds of labels per
repaint without creating a Qt object for each. Screen pixels, y down ("top" is the top of the screen).
"""

from __future__ import annotations

import math

# Reading order of the 3 x 3 grid: the picker lays its cells out in this order, left to right, top to bottom.
PLACEMENTS = (
    "top-left", "top", "top-right",
    "left", "center", "right",
    "bottom-left", "bottom", "bottom-right",
)
DEFAULT_ROI_PLACEMENT = "right"  # where the ROI labels have always been drawn
DEFAULT_GROUP_PLACEMENT = "top"

# Per placement: (column, row), each -1 (left / top), 0 (middle) or +1 (right / bottom).
_CELL = {name: (index % 3 - 1, index // 3 - 1) for index, name in enumerate(PLACEMENTS)}


def placement_origin(
    anchor_x: float, anchor_y: float, radius: float, placement: str, width: float, height: float, gap: float
) -> tuple[float, float]:
    """The top-left corner of a ``width`` x ``height`` label placed at ``placement`` of the circle of ``radius``
    around ``(anchor_x, anchor_y)``.

    The middle cell centres the label on the anchor. A side cell (top, bottom, left, right) leaves ``gap`` between
    the circle and the label and centres the label across the other axis. A corner cell puts the label's nearest
    corner on the circle's 45-degree point, ``gap`` beyond it, so the label never overlaps the circle."""
    try:
        column, row = _CELL[placement]
    except KeyError:
        raise ValueError(f"placement must be one of {PLACEMENTS}, got {placement!r}") from None
    reach = radius + gap
    if column and row:
        reach /= math.sqrt(2.0)  # the same distance from the centre, along the diagonal
    if column < 0:
        left = anchor_x - reach - width
    elif column > 0:
        left = anchor_x + reach
    else:
        left = anchor_x - width / 2.0
    if row < 0:
        top = anchor_y - reach - height
    elif row > 0:
        top = anchor_y + reach
    else:
        top = anchor_y - height / 2.0
    return left, top
