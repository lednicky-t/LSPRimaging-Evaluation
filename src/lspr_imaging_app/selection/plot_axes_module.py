"""``PlotAxesModule``: the axis options the user set in the Sensorgram, LbL and S_bulk plots (tick spacing, display range, grid), 2026-11-27. A session value.

Owns, per plot and per **quantity an axis shows** (its label), one `AxisOptions` (`domain/plot_axes.py` has the rules), and per plot the grid strength. Display only: it changes which
part of the data is in view and where ticks stand, never a number. Nothing is recomputed because of it.

Saved in the session file (schema 1.17, block ``"plot_axes"``: ``{plot: {"grid_alpha": a, "axes": [{"side": "x", "label": "Cube (#)", "major": 50, ...}, ...]}}``), written only when a plot
has something set, so a session that never used it is written exactly as before; a 1.16 file has none and loads with every plot automatic. An older build ignores the block.
Restoring emits, so the plots redraw.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from ..domain.plot_axes import DEFAULT_GRID_ALPHA, SIDES, AxisOptions, PlotAxes

logger = logging.getLogger(__name__)

PLOT_SENSORGRAM, PLOT_LBL, PLOT_BULK = "sensorgram", "lbl", "bulk"
PLOTS = (PLOT_SENSORGRAM, PLOT_LBL, PLOT_BULK)


class PlotAxesModule(QObject):
    changed = pyqtSignal(str)  # the plot whose options changed: it is redrawn, and the session is worth saving

    def __init__(self) -> None:
        super().__init__()
        self._options: dict[str, dict[tuple[str, str], AxisOptions]] = {}  # plot -> (side, label) -> options (never a default one)
        self._alpha: dict[str, float] = {}  # plot -> grid strength (never the default)

    def axes(self, plot: str, x_label: str, y_label: str) -> PlotAxes:
        """What `plot` draws its axes with while they show `x_label` and `y_label`."""
        table = self._options.get(plot, {})
        return PlotAxes(table.get(("x", x_label), AxisOptions()), table.get(("y", y_label), AxisOptions()), self._alpha.get(plot, DEFAULT_GRID_ALPHA))

    def set_axes(self, plot: str, x_label: str, y_label: str, axes: PlotAxes) -> None:
        """Replace the options of the two quantities and the grid strength of `plot`. Emits only when something changed."""
        self._check(plot)
        axes = axes.sanitized()
        before = (self._options.get(plot, {}).copy(), self._alpha.get(plot))
        table = self._options.setdefault(plot, {})
        for side, label in (("x", x_label), ("y", y_label)):
            options = axes.side(side)
            if options.is_default():
                table.pop((side, label), None)
            else:
                table[(side, label)] = options
        if not table:
            self._options.pop(plot, None)
        if axes.grid_alpha == DEFAULT_GRID_ALPHA:
            self._alpha.pop(plot, None)
        else:
            self._alpha[plot] = axes.grid_alpha
        if (self._options.get(plot, {}), self._alpha.get(plot)) != before:
            self.changed.emit(plot)

    def reset(self, plot: str, x_label: str, y_label: str) -> None:
        """Back to automatic for the two quantities `plot` shows now, and the default grid strength. Other quantities keep theirs."""
        self.set_axes(plot, x_label, y_label, PlotAxes())

    def _check(self, plot: str) -> None:
        if plot not in PLOTS:
            raise ValueError(f"unknown plot {plot!r}")

    # -- the session -------------------------------------------------------------------------------------------------------------

    def state(self) -> dict | None:
        """The block for the session file (fresh plain data, safe to hand to the writer thread); None when no plot has anything set."""
        block: dict = {}
        for plot in PLOTS:
            rows = [{"side": side, "label": label, **options.to_payload()} for (side, label), options in sorted(self._options.get(plot, {}).items())]
            entry: dict = {}
            if rows:
                entry["axes"] = rows
            if plot in self._alpha:
                entry["grid_alpha"] = float(self._alpha[plot])
            if entry:
                block[plot] = entry
        return block or None

    def restore_state(self, payload: object) -> None:
        """Take the options of a loaded session (None: none). Lenient like the other optional blocks: a row that cannot be read is skipped (with a WARNING), the rest loads."""
        options: dict[str, dict[tuple[str, str], AxisOptions]] = {}
        alpha: dict[str, float] = {}
        if isinstance(payload, dict):
            for plot in PLOTS:
                entry = payload.get(plot)
                if not isinstance(entry, dict):
                    if entry is not None:
                        logger.warning("The session's plot axes for %r could not be read and were left out: %r", plot, entry)
                    continue
                rows = entry.get("axes")
                for row in rows if isinstance(rows, list) else ():
                    if isinstance(row, dict) and row.get("side") in SIDES and isinstance(row.get("label"), str):
                        parsed = AxisOptions.from_payload(row)
                        if not parsed.is_default():
                            options.setdefault(plot, {})[(row["side"], row["label"])] = parsed
                    else:
                        logger.warning("A row of the session's plot axes for %r could not be read and was left out: %r", plot, row)
                restored = PlotAxes(grid_alpha=entry.get("grid_alpha", DEFAULT_GRID_ALPHA)).sanitized().grid_alpha
                if restored != DEFAULT_GRID_ALPHA:
                    alpha[plot] = restored
        elif payload is not None:
            logger.warning("The session's plot_axes block could not be read and was left out: %r", payload)
        before_options, before_alpha = self._options, self._alpha
        self._options, self._alpha = options, alpha
        for plot in PLOTS:
            if before_options.get(plot, {}) != options.get(plot, {}) or before_alpha.get(plot) != alpha.get(plot):
                self.changed.emit(plot)
