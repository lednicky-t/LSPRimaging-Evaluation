"""How the axes of a plot are drawn: tick spacing, display range, grid (2026-11-27). No Qt, no numbers of the analysis.

Display only: nothing here changes a value, only which part of the data is in view and where the tick marks stand. The options belong to the **quantity an axis shows**
(its label: "Cube (#)", "Peak position (nm)"), not to the axis alone, because a step of 50 or a range of 600-700 means nothing once the axis shows another quantity (time instead of
cubes, a shift instead of a position). The session keeps them per plot (Sensorgram, LbL, S_bulk) and per quantity (`selection/plot_axes_module.py`).

An empty value means "automatic" (pyqtgraph's own choice), so a plot that was never touched looks as before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .axis_breaks import AxisBreak, normalized
from .guide_lines import Guide
from .guide_lines import normalized as normalized_guides

MAX_TICKS = 400  # a tick step that would put more ticks than this across the visible range is ignored (an accidental 0.001 on a 700 nm axis must not hang the plot)
DEFAULT_GRID_ALPHA = 0.15  # the grid strength the plots always had
GRID_ALPHA_RANGE = (0.03, 1.0)
SIDES = ("x", "y")


def _positive(value: object) -> float | None:
    """`value` as a finite positive float, or None."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class AxisOptions:
    major: float | None = None  # distance between the labelled ticks (axis units); None = automatic
    minor: float | None = None  # distance between the minor ticks; only with a major step, and smaller than it
    low: float | None = None  # the display range's lower end; None = the autoscale's own
    high: float | None = None  # ... and its upper end. Either may be set alone: the other end stays automatic
    grid: bool = True  # the grid lines of this axis
    zero: float | None = None  # the axis value (data units) printed as 0: the labels count from it (time zero); only the Sensorgram's x axis uses it. None = the data's own numbers
    guides: tuple[Guide, ...] = ()  # reference lines at a value of this axis (`guide_lines.py`); the Sensorgram, LbL and S_bulk draw those of their y axis
    join_lines: bool = True  # across a break a line continues (True, Origin's way) or ends and starts again (False); only the Sensorgram's x axis, only while it has breaks
    breaks: tuple[AxisBreak, ...] = ()  # stretches of this axis left out and drawn as a small gap (`axis_breaks.py`); only the Sensorgram's x axis draws them

    def sanitized(self) -> AxisOptions:
        """The same options with every value valid: steps positive, a minor step only below a major one, a range with its ends in order (else both ends are dropped)."""
        major = _positive(self.major)
        minor = _positive(self.minor) if major is not None else None
        if major is not None and minor is not None and minor >= major:
            minor = None
        low, high = _finite(self.low), _finite(self.high)
        if low is not None and high is not None and low >= high:
            low = high = None
        return AxisOptions(major=major, minor=minor, low=low, high=high, grid=bool(self.grid), zero=_finite(self.zero), guides=normalized_guides(self.guides), join_lines=bool(self.join_lines), breaks=normalized(self.breaks))

    def is_default(self) -> bool:
        return self == AxisOptions()

    def shown(self, value: float) -> float:
        """The number printed for the data value `value`: counted from `zero` (0 when none is set)."""
        return float(value) - (0.0 if self.zero is None else self.zero)

    def has_range(self) -> bool:
        return self.low is not None or self.high is not None

    def spacing_for(self, low: float, high: float) -> tuple[float, float | None] | None:
        """`(major, minor)` to draw the ticks of the visible range [low, high] with, or None for the automatic ticks (no step set, or the step would draw more than `MAX_TICKS`).
        A minor step that would draw more than `MAX_TICKS` is left out while the major step is kept."""
        if self.major is None:
            return None
        span = abs(float(high) - float(low))
        if span / self.major > MAX_TICKS:
            return None
        minor = self.minor if self.minor is not None and span / self.minor <= MAX_TICKS else None
        return self.major, minor

    def to_payload(self) -> dict:
        """Only what differs from the default (the session file stays small, and a default is whatever the build then calls default)."""
        out: dict = {}
        for key in ("major", "minor", "low", "high", "zero"):
            value = getattr(self, key)
            if value is not None:
                out[key] = float(value)
        if not self.grid:
            out["grid"] = False
        if not self.join_lines:
            out["join"] = False
        if self.guides:
            out["guides"] = [g.to_payload() for g in self.guides]
        if self.breaks:
            out["breaks"] = [b.to_payload() for b in self.breaks]
        return out

    @staticmethod
    def from_payload(raw: object) -> AxisOptions:
        """Lenient: a field that is not a usable number is left automatic; anything but a dict gives the defaults."""
        if not isinstance(raw, dict):
            return AxisOptions()
        raw_breaks = raw.get("breaks")
        breaks = tuple(b for b in (AxisBreak.from_payload(item) for item in (raw_breaks if isinstance(raw_breaks, list) else ())) if b is not None)
        raw_guides = raw.get("guides")
        guides = tuple(g for g in (Guide.from_payload(item) for item in (raw_guides if isinstance(raw_guides, list) else ())) if g is not None)
        return AxisOptions(
            major=_positive(raw.get("major")), minor=_positive(raw.get("minor")), low=_finite(raw.get("low")), high=_finite(raw.get("high")), grid=raw.get("grid") is not False,
            zero=_finite(raw.get("zero")), guides=guides, join_lines=raw.get("join") is not False, breaks=breaks,
        ).sanitized()


@dataclass(frozen=True)
class PlotAxes:
    """What a plot draws its two axes with now: the options of the quantity each axis shows, and the grid strength."""

    x: AxisOptions = AxisOptions()
    y: AxisOptions = AxisOptions()
    grid_alpha: float = DEFAULT_GRID_ALPHA

    def side(self, name: str) -> AxisOptions:
        return self.x if name == "x" else self.y

    def with_side(self, name: str, options: AxisOptions) -> PlotAxes:
        return replace(self, x=options) if name == "x" else replace(self, y=options)

    def sanitized(self) -> PlotAxes:
        alpha = _finite(self.grid_alpha)
        alpha = DEFAULT_GRID_ALPHA if alpha is None else min(max(alpha, GRID_ALPHA_RANGE[0]), GRID_ALPHA_RANGE[1])
        return PlotAxes(self.x.sanitized(), self.y.sanitized(), alpha)

    def is_default(self) -> bool:
        return self.sanitized() == PlotAxes()

    def needs_rerange(self, other: PlotAxes) -> bool:
        """True when going to `other` changes which part of the data is in view: a typed range changed, or the x zero moved while an x range is typed (a typed x range is in printed
        numbers). Ticks, grid, guides and the breaks' own switches do not (the plot lays the breaks out itself)."""
        a, b = self.sanitized(), other.sanitized()
        if (a.x.low, a.x.high, a.y.low, a.y.high) != (b.x.low, b.x.high, b.y.low, b.y.high):
            return True
        return a.x.zero != b.x.zero and b.x.has_range()

    def needs_redraw(self, other: PlotAxes) -> bool:
        """True when a window that draws its own items (LbL, S_bulk) must draw them again to show `other`: a range changed (it is applied after a redraw), or a tick step was set or cleared
        (LbL's cycle labels give way to a step). A new step value, the grid and the guides are applied at once."""
        a, b = self.sanitized(), other.sanitized()
        if self.needs_rerange(other):
            return True
        return any((getattr(a, side).major is None) != (getattr(b, side).major is None) for side in ("x", "y"))

    def only_guides_differ(self, other: PlotAxes) -> bool:
        """True when `other` differs from these axes in nothing but the guides (the reference lines move; the view, the ticks and the grid stay as they are)."""

        def bare(axes: PlotAxes) -> PlotAxes:
            clean = axes.sanitized()
            return replace(clean, x=replace(clean.x, guides=()), y=replace(clean.y, guides=()))

        return bare(self) == bare(other)


def pinned_range(options: AxisOptions, auto_low: float, auto_high: float) -> tuple[float, float] | None:
    """The range to show given the autoscale's `[auto_low, auto_high]` and the pinned ends of `options`: a pinned end replaces the automatic one, an unpinned end stays automatic.
    None when nothing is pinned, or when the result would not be a range (a pinned lower end above the automatic upper end: the pin wins only if it still leaves a range)."""
    if not options.has_range():
        return None
    low = auto_low if options.low is None else options.low
    high = auto_high if options.high is None else options.high
    if low >= high:
        if options.low is not None and options.high is None:
            high = low + max(abs(low) * 0.1, 1.0)  # only the lower end is pinned and the data end is below it: show a window above the pin
        elif options.high is not None and options.low is None:
            low = high - max(abs(high) * 0.1, 1.0)
        else:
            return None
    return float(low), float(high)
