"""Reuse of pyqtgraph items between redraws (2026-11-02, ``docs/spectra_sensorgram_draw_cost_2026-11-02.md``).

Removing every item from the plot and creating new ones at each redraw cost ~1 s on the GUI thread for 160 Sensorgram lines (adding an
item to a ViewBox re-parents it and updates its view, per item). A pool keeps the items in the scene: a redraw takes the ones it needs
(``take``), gives them new data, and the ones left over are hidden (a hidden item is not drawn and does not count in the auto-range).

Use: ``begin()``, then ``take(kind, factory)`` for every item to draw, then ``end()``. Items of one ``kind`` must be interchangeable
(same class, same ``ignore_bounds``); the caller sets everything that differs (data, pen, ...) on every take.
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg


class PlotItemPool:
    def __init__(self, plot_item: pg.PlotItem) -> None:
        self._plot = plot_item
        self._free: dict[str, list[pg.GraphicsObject]] = {}
        self._used: dict[str, list[pg.GraphicsObject]] = {}
        self.drawn: list[pg.GraphicsObject] = []  # the items taken since `begin`, in the order taken

    def begin(self) -> None:
        for kind, items in self._used.items():
            self._free.setdefault(kind, []).extend(items)
        self._used = {}
        self.drawn = []

    def take(self, kind: str, factory: Callable[[], pg.GraphicsObject], *, ignore_bounds: bool = False, z: float | None = None) -> pg.GraphicsObject:
        free = self._free.get(kind)
        if free:
            item = free.pop(0)  # oldest first: the stacking order between items of a kind stays the order they were made in
        else:
            item = factory()
            if z is not None:
                item.setZValue(z)
            self._plot.addItem(item, ignoreBounds=ignore_bounds)
        item.setVisible(True)
        self._used.setdefault(kind, []).append(item)
        self.drawn.append(item)
        return item

    def end(self) -> None:
        """Hide what this redraw did not take."""
        for items in self._free.values():
            for item in items:
                if item.isVisible():
                    item.setVisible(False)

    def clear(self) -> None:
        """Nothing drawn (the items stay in the scene, hidden, for the next redraw)."""
        self.begin()
        self.end()
