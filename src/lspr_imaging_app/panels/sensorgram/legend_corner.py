"""Which corner of the plot area the legend sits in (2026-11-22). Pure rule, no Qt.

The Legend menu of every plot (Sensorgram, LbL, S_bulk) has a 2 x 2 block of cells, one per corner of the plot area (`legend_corner_picker.py`); this module is what the plots
read to turn the pick into a position. pyqtgraph's `LegendItem.setOffset((x, y))` anchors the legend to the corner its signs name: a positive number is a distance from the
left / top edge, a negative one from the right / bottom edge. So a corner is just the signs of the two gaps.
"""

from __future__ import annotations

TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT = "top-left", "top-right", "bottom-left", "bottom-right"
# Reading order of the 2 x 2 block: the picker lays its cells out in this order, left to right, top to bottom.
CORNERS = (TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT)
DEFAULT_CORNER = TOP_LEFT  # where the legend has always been


CORNER_STEP_TABLE_NOTE = (
    "The step table is on, so the legend stays at its edge: the table's rows line up with the legend's rows. Switch the table off (Stats menu) to put the legend in a corner."
)


def sanitize_corner(corner: object) -> str:
    """`corner` when it is one of the four, else the default (a remembered value from another version)."""
    return corner if isinstance(corner, str) and corner in CORNERS else DEFAULT_CORNER


def corner_offset(corner: str, across: float, down: float) -> tuple[float, float]:
    """The `setOffset` pair that puts a legend `across` pixels from the left / right edge and `down` pixels from the top / bottom edge of its parent, in `corner`.

    Both distances are kept above zero: pyqtgraph reads an offset of exactly 0 as "from the right / bottom", which would put a left / top legend on the wrong side."""
    across, down = max(abs(float(across)), 0.01), max(abs(float(down)), 0.01)
    return (across if corner in (TOP_LEFT, BOTTOM_LEFT) else -across, down if corner in (TOP_LEFT, TOP_RIGHT) else -down)
