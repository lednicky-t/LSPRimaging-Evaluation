"""Axis breaks: the stretches of an x axis that are left out and drawn as a small gap (2026-11-27). No Qt, no numbers of the analysis.

A break `[start, end]` (data units) removes that stretch from the drawn axis and puts a small fixed gap in its place, as in a scientific figure with a broken axis. `BreakMap` is the
whole of the arithmetic: `forward(x)` turns a data x into its *drawn* x (the number the plot's items are placed at), `inverse(v)` turns a drawn x back. Everything else in the plot
(lines, plan bar, cursor, ticks, clicks) goes through those two, so the data never change: only where they are drawn.

Rules (each is tested in `tests/unit/test_lspri_axis_breaks.py`):
- `forward` is increasing and continuous, and `inverse(forward(x)) == x` for every x, also inside a break (the gap is the removed stretch squeezed proportionally, so a click or a step edge
  that lands in the gap still means a place in the data).
- A **data point** inside a break is not drawn (`inside`); the line is drawn across the gap from the last point before it to the first one after it (Origin's way).
- The gap is a fixed fraction (`GAP_FRACTION`) of the length that is kept, so it looks the same however long the stretch removed. The length kept needs the data's extent.
- Breaks are sorted and merged if they overlap or touch; one that is not inside the extent (it would cut off the start or the end of the data: that is a range, not a break) is left out.
- Display only: nothing here changes a stored number.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

GAP_FRACTION = 0.012  # of the drawn length that is kept: ~10 px on a 900 px wide plot
MIN_BREAK_FRACTION = 0.002  # a break shorter than this fraction of the extent is ignored when it is added (a click, not a stretch)


@dataclass(frozen=True)
class AxisBreak:
    start: float  # data units, start < end
    end: float

    def sanitized(self) -> AxisBreak | None:
        """The break with its ends in order, or None when an end is not a finite number or the two are equal."""
        try:
            a, b = float(self.start), float(self.end)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(a) and math.isfinite(b)) or a == b:
            return None
        return AxisBreak(min(a, b), max(a, b))

    def to_payload(self) -> dict:
        return {"start": float(self.start), "end": float(self.end)}

    @staticmethod
    def from_payload(raw: object) -> AxisBreak | None:
        if not isinstance(raw, dict):
            return None
        return AxisBreak(raw.get("start"), raw.get("end")).sanitized()  # type: ignore[arg-type]


def normalized(breaks: Iterable[AxisBreak], extent: tuple[float, float] | None = None) -> tuple[AxisBreak, ...]:
    """Valid breaks, sorted, overlapping or touching ones merged. With an `extent` (the data's lowest and highest x) a break must lie inside it: one that reaches or passes an end is dropped."""
    valid = sorted((b for b in (item.sanitized() for item in breaks) if b is not None), key=lambda b: b.start)
    if extent is not None:
        low, high = extent
        valid = [b for b in valid if b.start > low and b.end < high]
    merged: list[AxisBreak] = []
    for item in valid:
        if merged and item.start <= merged[-1].end:
            merged[-1] = AxisBreak(merged[-1].start, max(merged[-1].end, item.end))
        else:
            merged.append(item)
    return tuple(merged)


class BreakMap:
    """The data <-> drawn x mapping of a set of breaks over the data's `extent` (lowest, highest x). With no break it is the identity."""

    def __init__(self, breaks: Iterable[AxisBreak] = (), extent: tuple[float, float] | None = None, gap_fraction: float = GAP_FRACTION) -> None:
        self.extent = extent
        self._gap_fraction = float(gap_fraction)
        self.breaks = normalized(breaks, extent) if extent is not None else ()
        self._starts = np.array([b.start for b in self.breaks], dtype=np.float64)
        self._ends = np.array([b.end for b in self.breaks], dtype=np.float64)
        lengths = self._ends - self._starts
        self._removed = np.concatenate(([0.0], np.cumsum(lengths)))  # removed[k]: the length removed by the first k breaks
        if self.breaks and extent is not None:
            kept = (extent[1] - extent[0]) - float(self._removed[-1])
            self.gap = max(kept, 1e-12) * float(gap_fraction)
        else:
            self.gap = 0.0
        count = np.arange(len(self.breaks))
        self._gap_start = self._starts - self._removed[:-1] + count * self.gap  # where the i-th gap begins, drawn
        self._lengths = lengths

    @property
    def active(self) -> bool:
        return bool(self.breaks)

    # -- the mapping ---------------------------------------------------------------------------------------------------------------

    def forward(self, x):
        """Drawn x of data `x` (a float or an array; NaN stays NaN). Inside a break: its place in the gap, in proportion."""
        if not self.active:
            return x
        scalar = np.ndim(x) == 0
        values = np.atleast_1d(np.asarray(x, dtype=np.float64))
        idx = np.searchsorted(self._starts, values, side="right")  # breaks that have started at x
        prev = np.clip(idx - 1, 0, None)
        inside = (idx > 0) & (values < self._ends[prev])
        passed = idx - inside  # breaks that are over
        outside = values - self._removed[passed] + passed * self.gap
        share = (values - self._starts[prev]) / self._lengths[prev]
        within = self._gap_start[prev] + self.gap * share
        out = np.where(inside, within, outside)
        return float(out[0]) if scalar else out

    def inverse(self, v):
        """Data x of drawn `v`: the opposite of `forward`, exactly."""
        if not self.active:
            return v
        scalar = np.ndim(v) == 0
        values = np.atleast_1d(np.asarray(v, dtype=np.float64))
        idx = np.searchsorted(self._gap_start, values, side="right")  # gaps that have started at v
        prev = np.clip(idx - 1, 0, None)
        inside = (idx > 0) & (values < self._gap_start[prev] + self.gap)
        passed = idx - inside
        outside = values + self._removed[passed] - passed * self.gap
        share = (values - self._gap_start[prev]) / self.gap
        within = self._starts[prev] + share * self._lengths[prev]
        out = np.where(inside, within, outside)
        return float(out[0]) if scalar else out

    def inside(self, x):
        """True where data `x` lies strictly inside a break (such a point is not drawn). NaN is not inside."""
        if not self.active:
            return False if np.ndim(x) == 0 else np.zeros(np.shape(x), dtype=bool)
        scalar = np.ndim(x) == 0
        values = np.atleast_1d(np.asarray(x, dtype=np.float64))
        idx = np.searchsorted(self._starts, values, side="right")
        prev = np.clip(idx - 1, 0, None)
        out = (idx > 0) & (values < self._ends[prev]) & (values > self._starts[prev])
        return bool(out[0]) if scalar else out

    # -- what the axis needs -------------------------------------------------------------------------------------------------------

    def segments(self) -> list[tuple[float, float]]:
        """The stretches of data that are drawn, in order: from the extent's low end to the first break, between breaks, and from the last break to the extent's high end."""
        if self.extent is None:
            return []
        edges = [self.extent[0]]
        for b in self.breaks:
            edges += [b.start, b.end]
        edges.append(self.extent[1])
        return [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]

    def gaps(self) -> list[tuple[float, float]]:
        """Where each break is drawn: (drawn x where the gap begins, drawn x where it ends)."""
        return [(float(start), float(start + self.gap)) for start in self._gap_start]

    def drawn_extent(self) -> tuple[float, float] | None:
        return None if self.extent is None else (float(self.forward(self.extent[0])), float(self.forward(self.extent[1])))

    def break_at(self, x: float) -> int | None:
        """The index of the break whose stretch contains data `x` (ends included), else None."""
        for i, b in enumerate(self.breaks):
            if b.start <= x <= b.end:
                return i
        return None

    def without(self, index: int) -> BreakMap:
        """The same map with the `index`-th break left out (a handle dragged over the data reads its place from this: the break under the pointer must not move the ground it stands on)."""
        return BreakMap([b for i, b in enumerate(self.breaks) if i != index], self.extent, self._gap_fraction)
