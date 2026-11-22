"""The order of a plot's legend rows (2026-11-22). Pure: no Qt, no dataset. Display only: the lines, their colours and every number stay as they are, only the rows move.

Screen purpose: the Legend menu of every plot (Sensorgram, LbL, S_bulk) can put its rows in order: **Group** (the order the lines come in: a group's number, a ROI's id; this is what the
legend always showed), **Name** (the label, with numbers read as numbers: "ROI 2" before "ROI 10") or **Value** (a number of the line). Each has a direction, ascending or descending.
**Value** is one of two numbers per line (`VALUE_MEAN`: the mean of the line as drawn over the whole plot; `VALUE_STEP`: the line's value at one step: the step selected on the Sensorgram's
plan bar, the cycle or the step the cursor stands on in the LbL and S_bulk plots).

Rules: a line without a number (NaN) is always last, whatever the direction (a missing value is not "the smallest"); equal keys keep the default order, so the result never depends on how the
sort happens to break a tie; a Value order with no value to read (no step picked) is the default order, and the plot says why (`needs_step`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

SORT_GROUP, SORT_NAME, SORT_VALUE = "group", "name", "value"
SORT_KEYS = (SORT_GROUP, SORT_NAME, SORT_VALUE)
VALUE_MEAN, VALUE_STEP = "mean", "step"
VALUE_SOURCES = (VALUE_MEAN, VALUE_STEP)

_NUMBER = re.compile(r"(\d+)")


@dataclass(frozen=True)
class LegendOrder:
    key: str = SORT_GROUP
    descending: bool = False
    value: str = VALUE_MEAN

    def sanitized(self) -> LegendOrder:
        return LegendOrder(
            key=self.key if self.key in SORT_KEYS else SORT_GROUP,
            descending=bool(self.descending),
            value=self.value if self.value in VALUE_SOURCES else VALUE_MEAN,
        )

    def needs_step(self) -> bool:
        """True when the order reads the value at a step (so the plot has to say which step)."""
        return self.key == SORT_VALUE and self.value == VALUE_STEP


def natural_key(text: str) -> tuple:
    """A sort key that reads digits as numbers and ignores case: "ROI 2" < "ROI 10", "group a" == "Group A"."""
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in _NUMBER.split(text) if part != "")


def order_rows(names: Sequence[str], values: Sequence[float] | None, order: LegendOrder) -> list[int]:
    """The indices of the lines in the order their legend rows should have. `names[i]` and `values[i]` belong to line i (the default order is 0, 1, 2 ...).
    `values` None: the order asked for needs a number nobody has (Value with no step picked): the default order."""
    count = len(names)
    order = order.sanitized()
    indices = list(range(count))
    if order.key == SORT_GROUP:
        return indices[::-1] if order.descending else indices
    # `sorted` is stable, also with reverse=True: lines with equal keys keep the default order in both directions.
    if order.key == SORT_NAME:
        return sorted(indices, key=lambda i: natural_key(names[i]), reverse=order.descending)
    if values is None or len(values) != count:
        return indices
    finite = sorted((i for i in indices if _is_number(values[i])), key=lambda i: float(values[i]), reverse=order.descending)
    missing = [i for i in indices if not _is_number(values[i])]
    return finite + missing


def _is_number(value: float) -> bool:
    return value is not None and math.isfinite(float(value))
