"""``PlotLineOrderModule``: the lines' stacking order the user set by hand in the Sensorgram, LbL and S_bulk plots (2026-11-22). A session value.

Owns one fact per plot: the **Manual draw order**, a list of line names, **first = in front** (the Line order table of the Points & lines menu edits it; ``panels/sensorgram/draw_order.py``
has the rules). Display only: it changes which line lies on top where lines overlap, never a number. Nothing here is recomputed because of it.

A line is named by what identifies it in its plot (the Sensorgram: ``Series.key``, "roi:5" / "group:Layer A"; the windows: the group's or ROI's name), so a ROI that is renumbered or a group
that is renamed drops out of the list and is treated as new (in front); nothing else is touched.

Saved in the session file (schema 1.16, block ``"plot_line_order"``: ``{"sensorgram": [...], "lbl": [...], "bulk": [...]}``), written only when a plot has a list, so a session that never used it
is written exactly as before; a 1.15 file has none and loads with every plot at its own order. An older build ignores the block. Restoring emits, so the plots redraw.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

PLOT_SENSORGRAM, PLOT_LBL, PLOT_BULK = "sensorgram", "lbl", "bulk"
PLOTS = (PLOT_SENSORGRAM, PLOT_LBL, PLOT_BULK)


class PlotLineOrderModule(QObject):
    changed = pyqtSignal(str)  # the plot whose list changed: it is redrawn, and the session is worth saving

    def __init__(self) -> None:
        super().__init__()
        self._orders: dict[str, tuple[str, ...]] = {}

    def order(self, plot: str) -> tuple[str, ...]:
        """The plot's manual list, first = in front (empty: none set)."""
        return self._orders.get(plot, ())

    def set_order(self, plot: str, keys: Iterable[str]) -> None:
        """Replace the plot's list (names once each, in the given order); an empty list clears it. Emits only when it changed."""
        if plot not in PLOTS:
            raise ValueError(f"unknown plot {plot!r}")
        new = tuple(dict.fromkeys(str(k) for k in keys))
        if new == self.order(plot):
            return
        if new:
            self._orders[plot] = new
        else:
            self._orders.pop(plot, None)
        self.changed.emit(plot)

    # -- the session -------------------------------------------------------------------------------------------------------------

    def state(self) -> dict | None:
        """The block for the session file (fresh lists, safe to hand to the writer thread); None when no plot has a list."""
        block = {plot: list(keys) for plot, keys in self._orders.items() if keys}
        return block or None

    def restore_state(self, payload: object) -> None:
        """Take the lists of a loaded session (None: none). Lenient like the other optional blocks: a plot whose list is not a list of names is skipped, the rest loads."""
        wanted: dict[str, tuple[str, ...]] = {}
        if isinstance(payload, dict):
            for plot in PLOTS:
                raw = payload.get(plot)
                if isinstance(raw, list) and all(isinstance(k, str) for k in raw):
                    names = tuple(dict.fromkeys(raw))
                    if names:
                        wanted[plot] = names
                elif raw is not None:
                    logger.warning("The session's plot line order for %r could not be read and was left out: %r", plot, raw)
        elif payload is not None:
            logger.warning("The session's plot_line_order block could not be read and was left out: %r", payload)
        before, self._orders = self._orders, wanted
        for plot in PLOTS:
            if before.get(plot, ()) != wanted.get(plot, ()):
                self.changed.emit(plot)
