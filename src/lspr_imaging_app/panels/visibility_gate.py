"""``HiddenPanelGate`` - a display panel does no drawing work while nobody can see it (2026-10-14).

Measured: with the slideshow playing, the Histogram redraw alone was ~30 ms of GUI time per redraw and
the ROI table rebuilt all its rows; both kept running with their docks closed. The rule (also in
`apps/LSPRi/eva/CLAUDE.md`): **a display panel keeps the data it is handed (cheap) but does not compute
or draw while hidden, and catches up once when it is shown again.**

How a panel uses it::

    self._gate = HiddenPanelGate(on_shown=self._schedule_redraw)

    def _schedule_redraw(self):          # every request to redraw goes through here
        if self._gate.defer():           # hidden: remembered, nothing started
            return
        self._redraw_timer.start()

    def set_panel_visible(self, visible: bool):   # connected to the dock's `visibilityChanged`
        self._gate.set_visible(visible)

`visible` starts as True and only ever comes from the dock's `visibilityChanged` signal, so a panel that is
built without a dock (a test, an embedding) behaves exactly as before. Qt reports a dock hidden when it is
closed, when it is behind another tab, and when the main window is minimised.

**A second reason to wait, "held" (2026-11-19).** While the user is dragging a ROI the panel is visible but
must not rebuild either: its refresh waits for a quiet moment (~100 ms), and a pause of the mouse *is* a quiet
moment, so the rebuild ran in the middle of the drag and the next mouse move waited behind it (the ROI / Groups
table: ~0.5 s with 162 ROIs on a 8,478-plane dataset, measured from the app log). ``set_held(True)`` at the
start of the drag and ``set_held(False)`` at its end make `defer()` say True in between, and the same single
catch-up runs on release. Hidden and held are independent: the catch-up waits for both to end.
"""

from __future__ import annotations

from collections.abc import Callable


class HiddenPanelGate:
    def __init__(self, on_shown: Callable[[], None]) -> None:
        self._on_shown = on_shown
        self._visible = True
        self._held = False
        self._stale = False

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def held(self) -> bool:
        """An edit drag is running: the panel waits for its end, whether visible or not."""
        return self._held

    @property
    def stale(self) -> bool:
        """Work was requested while hidden (or held) and has not been done yet."""
        return self._stale

    def defer(self) -> bool:
        """True (and the request is remembered) while hidden or held: the caller skips its work."""
        if self._visible and not self._held:
            return False
        self._stale = True
        return True

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        self._catch_up()

    def set_held(self, held: bool) -> None:
        held = bool(held)
        if held == self._held:
            return
        self._held = held
        self._catch_up()

    def _catch_up(self) -> None:
        if self._visible and not self._held and self._stale:
            self._stale = False
            self._on_shown()  # one catch-up for everything that was requested while hidden or held
