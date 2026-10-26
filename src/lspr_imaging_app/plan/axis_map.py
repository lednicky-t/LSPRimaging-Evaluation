"""Time <-> the Sensorgram's x axis (pure, no Qt).

The plan's steps are placed in unix milliseconds (the clock of the image timings and the notes). The plot's x is either the experimental time in seconds
(t = 0 at the dataset's start) or the cube number. Both are known for every plotted cube, so the map is built from exactly those pairs (a cube's first-frame
time in unix ms, the x the plot gives that cube) and interpolates between them, the same code for both axes:

- Time mode: x is linear in ms, so the interpolation is exact.
- Cube mode: x is the cube number, so a boundary that falls in the middle of a cube is drawn inside that cube, in proportion.

Outside the first and last point the map extrapolates with the mean slope, so a step that begins before the first scanned cube or ends after the last
one (the last step runs to the end of the data) still has a position.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AxisMap:
    unix_ms: np.ndarray  # strictly increasing
    x: np.ndarray  # strictly increasing too (a cube that starts later has a larger x), same length

    @classmethod
    def build(cls, unix_ms: Sequence[float | None], x: Sequence[float | None]) -> "AxisMap | None":
        """From one pair per plotted cube. A pair with a missing or non-finite value is dropped (a cube without a start time has no x in Time mode).
        None when fewer than two usable points remain, or when x does not grow with time (nothing to map between)."""
        ms = np.array([np.nan if v is None else float(v) for v in unix_ms], dtype=np.float64)
        xs = np.array([np.nan if v is None else float(v) for v in x], dtype=np.float64)
        if ms.shape != xs.shape:
            raise ValueError("unix_ms and x must have one value per plotted cube")
        usable = np.isfinite(ms) & np.isfinite(xs)
        ms, xs = ms[usable], xs[usable]
        order = np.argsort(ms, kind="stable")
        ms, xs = ms[order], xs[order]
        keep = np.concatenate(([True], np.diff(ms) > 0)) if ms.size else np.zeros(0, dtype=bool)
        ms, xs = ms[keep], xs[keep]
        if ms.size < 2 or not np.all(np.diff(xs) > 0):
            return None
        return cls(unix_ms=ms, x=xs)

    def _slope(self) -> float:
        """Axis units per millisecond over the whole run."""
        return float((self.x[-1] - self.x[0]) / (self.unix_ms[-1] - self.unix_ms[0]))

    def x_at(self, unix_ms: float) -> float:
        """The plot's x at a time."""
        t = float(unix_ms)
        if t <= self.unix_ms[0]:
            return float(self.x[0] + (t - self.unix_ms[0]) * self._slope())
        if t >= self.unix_ms[-1]:
            return float(self.x[-1] + (t - self.unix_ms[-1]) * self._slope())
        return float(np.interp(t, self.unix_ms, self.x))

    def unix_ms_at(self, x: float) -> float:
        """The time (unix ms, not rounded) at a plot x."""
        v = float(x)
        if v <= self.x[0]:
            return float(self.unix_ms[0] + (v - self.x[0]) / self._slope())
        if v >= self.x[-1]:
            return float(self.unix_ms[-1] + (v - self.x[-1]) / self._slope())
        return float(np.interp(v, self.x, self.unix_ms))
