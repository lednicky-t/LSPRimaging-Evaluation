"""``AxisStyler``: draws a pyqtgraph plot with the axis options of the Plot menu (tick spacing, grid, display range), 2026-11-27. Display only.

One styler per plot (Sensorgram, LbL, S_bulk); the owner calls `set_axes` when the options or the quantity an axis shows change, and `pin_ranges` after it has ranged the plot itself
(the Sensorgram after its autoscale and the room for the plan bar; a window after a redraw, with `release_unpinned` before it). Nothing here reads a module or changes a value.

How each option is applied:
- **Tick spacing**: `AxisItem.setTickSpacing(levels=...)`, which pyqtgraph evaluates at every paint. A step that would draw more than `MAX_TICKS` ticks across the *visible* range is ignored
  (`AxisOptions.spacing_for`), so zooming far out never hangs the plot; the styler checks again whenever the visible range changes. LbL's own explicit cycle ticks (`AxisItem.setTicks`)
  take precedence over a spacing in pyqtgraph, so the styler removes them while a major step is set; the window puts them back by calling `set_axes` again when it redraws.
- **Grid**: `PlotItem.showGrid(x, y, alpha)`. pyqtgraph draws the minor level at half the major level's strength.
- **Display range**: a typed x range is in the numbers the axis prints (counted from the x zero, `AxisOptions.zero`); the styler adds the zero back. A pinned end replaces the autoscale's end (`pinned_range`); an end left empty stays automatic. While an axis is pinned its auto-range is off, so pyqtgraph's own
  re-ranging when items are added does not undo the pin; when the pin is cleared the auto-range is switched back on.
"""

from __future__ import annotations

import pyqtgraph as pg

from ...domain.plot_axes import AxisOptions, PlotAxes, pinned_range

_SIDE_AXIS = {"x": "bottom", "y": "left"}


class AxisStyler:
    def __init__(self, item: pg.PlotItem) -> None:
        self._item = item
        self._axes = PlotAxes()
        self._x_map = None  # the plot's `BreakMap` while its x axis has breaks: a pinned x range is in data units, the view in drawn ones
        self._pinned = {"x": False, "y": False}  # the sides whose auto-range this styler switched off
        self._drawn: dict[str, tuple | None] = {"x": None, "y": None}  # the spacing each axis was last given (None: automatic), to touch pyqtgraph only on a change
        view_box = item.getViewBox()
        view_box.sigXRangeChanged.connect(lambda *_args: self._apply_ticks("x"))
        view_box.sigYRangeChanged.connect(lambda *_args: self._apply_ticks("y"))

    def axes(self) -> PlotAxes:
        return self._axes

    def set_axes(self, axes: PlotAxes) -> None:
        """Draw with `axes` from now on: ticks and grid at once. The range is left to the owner (`pin_ranges`), which knows when it ranges the plot."""
        self._axes = axes.sanitized()
        self._item.showGrid(x=self._axes.x.grid, y=self._axes.y.grid, alpha=self._axes.grid_alpha)
        for side in ("x", "y"):
            self._drawn[side] = ("forced",)  # something may have replaced the ticks (LbL's own): put them right whatever was drawn before
            self._apply_ticks(side)

    def set_x_map(self, x_map) -> None:
        self._x_map = x_map

    def has_pins(self) -> bool:
        return self._axes.x.has_range() or self._axes.y.has_range()

    def release_unpinned(self) -> None:
        """A window calls this at the start of a redraw: a side whose auto-range was switched off for a pin (or by the autoscale before it) and is no longer pinned gets its auto-range back,
        so pyqtgraph ranges the new items itself. (The Sensorgram ranges itself and never calls this: it never relies on pyqtgraph's auto-range.)"""
        view_box = self._item.getViewBox()
        for side in ("x", "y"):
            if self._pinned[side] and not self._axes.side(side).has_range():
                view_box.enableAutoRange(axis=pg.ViewBox.XAxis if side == "x" else pg.ViewBox.YAxis)
                self._pinned[side] = False

    def pin_ranges(self, *, autoscale_first: bool = False) -> None:
        """Show the pinned ranges. Call it right after the plot was ranged automatically (the Sensorgram: after its autoscale and the room for the bar), or with `autoscale_first=True`
        and the styler does that range first (a window, after it drew its items: `autoRange` gives the end that is not pinned). Does nothing while nothing is pinned."""
        if not self.has_pins():
            return
        view_box = self._item.getViewBox()
        if autoscale_first:
            view_box.autoRange()  # switches the auto-range of both axes off: `release_unpinned` gives it back at the next redraw
            self._pinned = {"x": True, "y": True}
        (x_low, x_high), (y_low, y_high) = view_box.viewRange()
        broken = self._x_map is not None and self._x_map.active
        if broken:
            x_low, x_high = float(self._x_map.inverse(x_low)), float(self._x_map.inverse(x_high))
        zero = self._axes.x.zero or 0.0  # a typed x range is in the numbers the axis prints, i.e. counted from the zero
        x_range = pinned_range(self._axes.x, x_low - zero, x_high - zero)
        if x_range is not None:
            x_range = (x_range[0] + zero, x_range[1] + zero)
        if x_range is not None and broken:
            x_range = (float(self._x_map.forward(x_range[0])), float(self._x_map.forward(x_range[1])))
        y_range = pinned_range(self._axes.y, y_low, y_high)
        if x_range is not None:
            view_box.setXRange(*x_range, padding=0.0)
            self._pinned["x"] = True
        if y_range is not None:
            view_box.setYRange(*y_range, padding=0.0)
            self._pinned["y"] = True

    # -- ticks ---------------------------------------------------------------------------------------------------------------------

    def _apply_ticks(self, side: str) -> None:
        options: AxisOptions = self._axes.side(side)
        axis = self._item.getAxis(_SIDE_AXIS[side])
        index = 0 if side == "x" else 1
        low, high = self._item.getViewBox().viewRange()[index]
        spacing = options.spacing_for(low, high)
        if spacing == self._drawn[side]:
            return
        self._drawn[side] = spacing
        if spacing is None:
            axis.setTickSpacing()  # pyqtgraph's own choice (an explicit `setTicks` of the owner, if any, is untouched)
            return
        major, minor = spacing
        axis.setTicks(None)  # explicit ticks would win over a spacing
        axis.setTickSpacing(levels=[(major, 0.0)] + ([(minor, 0.0)] if minor is not None else []))

    def ticks_are_pinned(self, side: str) -> bool:
        """True while a tick step is set for `side` (the owner then leaves its own explicit ticks out)."""
        return self._axes.side(side).major is not None
