"""The order the lines of a plot are drawn in, i.e. which line lies on top where they overlap (2026-11-22). Pure: no Qt, no dataset. Display only: no number changes.

Three modes (the Points & lines menu's "Draw order"):

- **Auto**: the lines are stacked by how rough they are, the roughest at the back and the smoothest in front, so a smooth line is never scribbled over by a noisy one. Roughness is the
  *median* of the absolute second differences of the line's finite points (`roughness`: a straight ramp or a slow drift scores 0, jitter scores its size; the median ignores the few big
  jumps at a step's edge). The numbers are in the plot's own units, so they compare. Equal roughness keeps the lines' own order, the later line in front (as the plots always drew).
- **Legend**: the stacking follows the legend rows: **the first row is in front**, the last row at the back.
- **Manual**: a list the user edits (the Line order table): the **first row is in front**. A line that is not in the list (a new one) goes in front of the listed ones.

"First = in front" is the same rule in the Legend mode, the table and the table's "Move to top" (= bring to front).

All functions return **indices into the plot's own list of lines**, never reorder that list: legends, exports and the Sensorgram's step table keep the lines' own order.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

DRAW_AUTO, DRAW_LEGEND, DRAW_MANUAL = "auto", "legend", "manual"
DRAW_MODES = (DRAW_AUTO, DRAW_LEGEND, DRAW_MANUAL)

MOVE_TOP, MOVE_UP, MOVE_DOWN, MOVE_BOTTOM = "top", "up", "down", "bottom"
MOVES = (MOVE_TOP, MOVE_UP, MOVE_DOWN, MOVE_BOTTOM)


def roughness(y: Sequence[float] | np.ndarray) -> float:
    """The median absolute second difference of the finite points of `y` (in the unit of `y`); 0 for fewer than three points."""
    values = np.asarray(y, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return 0.0
    return float(np.median(np.abs(values[2:] - 2.0 * values[1:-1] + values[:-2])))


def back_to_front(
    keys: Sequence[str],
    mode: str,
    *,
    legend_order: Sequence[int] = (),
    roughness_of: Sequence[float] = (),
    manual: Sequence[str] = (),
) -> list[int]:
    """The indices of the lines in the order they are drawn: the first is at the back, the last in front.

    `keys[i]` names line i (the Manual list's key). `legend_order`: the indices in the legend's row order (first row first), for Legend. `roughness_of[i]`: line i's roughness, for Auto.
    `manual`: the user's list, first = in front, for Manual. A mode with nothing to go by (an empty legend order, no roughness) draws the lines in their own order."""
    count = len(keys)
    own = list(range(count))
    if mode == DRAW_LEGEND and len(legend_order) == count:
        return list(legend_order)[::-1]
    if mode == DRAW_AUTO and len(roughness_of) == count:
        return sorted(own, key=lambda i: (-_finite(roughness_of[i]), i))
    if mode == DRAW_MANUAL:
        place: dict[str, int] = {}
        for position, key in enumerate(manual):
            place.setdefault(key, position)
        new = [i for i in own if keys[i] not in place]  # not in the list: in front of the listed ones, in their own order
        listed = sorted((i for i in own if keys[i] in place), key=lambda i: (place[keys[i]], i))
        return (new + listed)[::-1]
    return own


def manual_rows(keys: Sequence[str], manual: Sequence[str]) -> list[str]:
    """The Line order table's rows, front first: the lines that are not in the saved list (in their own order), then the saved list's keys that are still there (in the saved order).
    A key twice in `keys` is one row."""
    present = list(dict.fromkeys(keys))
    saved = list(dict.fromkeys(k for k in manual if k in set(present)))
    listed = set(saved)
    return [k for k in present if k not in listed] + saved


def moved(rows: Sequence[str], selected: set[str], how: str) -> list[str]:
    """`rows` (front first) with the `selected` ones moved: to the top, one place up, one place down, or to the bottom. The selected rows keep their order among themselves; a row that
    cannot move further (already at the end, or blocked by another selected row) stays."""
    rows = list(rows)
    chosen = [r for r in rows if r in selected]
    rest = [r for r in rows if r not in selected]
    if how == MOVE_TOP:
        return chosen + rest
    if how == MOVE_BOTTOM:
        return rest + chosen
    if how == MOVE_UP:
        for i in range(1, len(rows)):
            if rows[i] in selected and rows[i - 1] not in selected:
                rows[i - 1], rows[i] = rows[i], rows[i - 1]
        return rows
    if how == MOVE_DOWN:
        for i in range(len(rows) - 2, -1, -1):
            if rows[i] in selected and rows[i + 1] not in selected:
                rows[i + 1], rows[i] = rows[i], rows[i + 1]
        return rows
    return rows


def can_move(rows: Sequence[str], selected: set[str], how: str) -> bool:
    """Whether `moved` would change anything (a menu offers only what can be done now)."""
    return bool(selected) and moved(rows, selected, how) != list(rows)


def _finite(value: float) -> float:
    return float(value) if value is not None and math.isfinite(float(value)) else 0.0
