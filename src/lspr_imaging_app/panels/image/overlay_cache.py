"""One cached picture of the whole ROI overlay per overlay *state* (2026-10-14, testing10 analysis).

**The problem.** While the slideshow plays, the image under the overlay changes every frame, so Qt repaints every overlay
item every frame: circles, rings, labels, group labels. Measured on the maintainer's data (160 ROIs): +26 ms of the
view's paint, i.e. ~25 ms of every ~65 ms cycle, plus 5-8 ms to re-draw the geometry when chromatic correction moves the
ROIs. But the overlay itself rarely changes: it depends on the wavelength (the chromatic shift) and not on the cube, so
the same 27 overlay states repeat for every cube.

**The mechanism.** The overlay items are children of two empty *layer* items (one in data coordinates, one in screen
coordinates; the label items live in screen space). A pass-through `QGraphicsEffect` on each layer renders the layer, with
its children, into a pixmap the first time a state is shown and keeps it; afterwards a repaint is one blit per layer.
`OverlayLayers.begin(epoch, state)` tells the caller whether a picture for this state already exists, so it can skip
re-drawing the items altogether.

**What is a state, what invalidates.** `state` = what changes from frame to frame (cube if ROIs have a timeline, the
chromatic affine). `epoch` = everything else that changes how the overlay looks (ROI revision, selection, style, labels,
group-label settings, line width, detection ring sizes): when it changes, every cached picture is dropped. The view
(zoom / pan / widget size / pixel ratio) is part of the key inside the effect, read live, so a pan or resize can never show a
picture made for another view. A picture is only kept once the epoch has been stable for a frame (a drag that changes it
at every step caches nothing).

**Memory policy.** A byte budget (default 256 MB; 0 switches the cache off). Chosen in the Options menu ("ROI Overlay Cache",
saved with the other UI state); ``LSPR_OVERLAY_CACHE_MB`` overrides the saved value at start-up (for comparing runs). Pictures are
**admitted until the budget is full and never evicted**: playback visits the states in a cycle, and a least-recently-used
cache smaller than the cycle gets *no* hits at all (measured: 11 of 27 pictures with LRU = 0 hits and slower than no
cache). A full budget therefore gives a partial hit rate, not a loss.

**Not pixel-identical to direct painting** in the last bit: anti-aliased edges are blended into a transparent layer first
and composited afterwards (`tests/integration/test_lspri_rewrite_overlay_layer_cache.py` measures the difference). Cached
pictures sit on the device pixel grid like any Qt item cache.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Hashable, Mapping

import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsEffect, QGraphicsItem

logger = logging.getLogger(__name__)

ENV_BUDGET_MB = "LSPR_OVERLAY_CACHE_MB"
DEFAULT_BUDGET_MB = 256
BUDGET_CHOICES_MB = (0, 128, 256, 512, 1024)  # the Options menu's choices
USELESS_AFTER_MISSES = 60  # this many misses in a row without one hit: the states do not repeat, stop making pictures
_LAYER_SCREEN_Z = 900.0  # the label items' own z values (900, 901) are relative to their parent: same stacking as before


def environment_budget_mb(environ: Mapping[str, str] | None = None) -> float | None:
    """``LSPR_OVERLAY_CACHE_MB`` as a number of megabytes (>= 0), or None if it is not set (or not a number: reported)."""
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV_BUDGET_MB, "").strip()
    if not raw:
        return None
    try:
        megabytes = float(raw)
    except ValueError:
        megabytes = -1.0
    if megabytes >= 0.0 and megabytes == megabytes and megabytes != float("inf"):
        return megabytes
    logger.warning("%s=%r is not a number of megabytes (>= 0): ignored", ENV_BUDGET_MB, raw)
    return None


def budget_bytes_from_environment(environ: Mapping[str, str] | None = None) -> int:
    """The cache budget in bytes: ``LSPR_OVERLAY_CACHE_MB`` if it is a number >= 0, else the default. 0 = cache off."""
    megabytes = environment_budget_mb(environ)
    return int((DEFAULT_BUDGET_MB if megabytes is None else megabytes) * 1024 * 1024)


class _Budget:
    def __init__(self, limit_bytes: int) -> None:
        self.limit = int(limit_bytes)
        self.used = 0

    def fits(self, size: int) -> bool:
        return self.used + size <= self.limit


class _Layer(pg.GraphicsObject):
    """An item that only holds the overlay items of one coordinate space; it paints nothing itself.

    Its bounding rectangle is the visible view (in its own coordinates), and it clips its children to it: the effect then
    needs only that cheap rectangle (testing12 profile: walking the children's bounding rectangles at every call was 8.7 %
    of the GUI thread) and the pictures are exactly view-sized."""

    def __init__(self, view_box: pg.ViewBox, *, screen_space: bool) -> None:
        super().__init__()
        self._vb = view_box
        self._screen_space = screen_space
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        # Screen space: the whole view box (the label items paint anywhere in it). Data space: the visible part of the data.
        return QRectF(self._vb.boundingRect()) if self._screen_space else QRectF(self._vb.viewRect())

    def viewTransformChanged(self) -> None:  # noqa: N802 - pyqtgraph naming
        self.prepareGeometryChange()  # the visible rectangle moved with the view
        super().viewTransformChanged()

    def paint(self, *_args: object) -> None:
        return None


class LayerCacheEffect(QGraphicsEffect):
    """Pass-through effect that keeps one device-coordinate picture of its item (and children) per `state`."""

    def __init__(self, view_box: pg.ViewBox, budget: _Budget, items_current: Callable[[], bool]) -> None:
        super().__init__()
        self._vb = view_box
        self._budget = budget
        self._items_current = items_current  # do the layer's items hold the overlay of `state` right now?
        self.state: Hashable | None = None  # which overlay state the next paint shows (None: just pass through)
        self.admit = False  # may a picture that is not cached yet be kept?
        self._entries: dict[Hashable, tuple[QPixmap, QPoint, int]] = {}
        self._entries_view: Hashable | None = None
        self._full = False  # the budget could not take another picture: later misses are painted directly, not rendered first

    # -- the view the pictures are valid for -------------------------------------------------------

    def _view_key(self) -> Hashable:
        view_range = self._vb.viewRange()
        widget = self._vb.getViewWidget()
        ratio = widget.devicePixelRatioF() if widget is not None else 1.0
        return (
            tuple(round(float(value), 4) for axis in view_range for value in axis),
            round(self._vb.width()), round(self._vb.height()), round(float(ratio), 3),
        )

    def _forget_other_views(self) -> None:
        view = self._view_key()
        if view != self._entries_view:
            self.clear()
            self._entries_view = view

    # -- cache ---------------------------------------------------------------------------------------

    def has(self, state: Hashable) -> bool:
        self._forget_other_views()
        return state in self._entries

    @property
    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        for _pixmap, _offset, size in self._entries.values():
            self._budget.used -= size
        self._entries.clear()
        self._full = False

    def draw(self, painter) -> None:  # noqa: N802 - Qt naming
        state = self.state
        if state is None:
            self.drawSource(painter)
            return
        self._forget_other_views()
        entry = self._entries.get(state)
        if entry is None:
            # A picture is made from the items as they are, so it may only be kept when they hold THIS state (after a
            # cache hit they still hold the previous one, e.g. when a zoom repaints before the panel refreshed them).
            if not self.admit or self._full or not self._items_current():
                self.drawSource(painter)  # not kept: paint as if there were no effect
                return
            pixmap, offset = self.sourcePixmap(Qt.CoordinateSystem.DeviceCoordinates)
            size = pixmap.width() * pixmap.height() * 4
            if self._budget.fits(size):
                self._entries[state] = (pixmap, offset, size)
                self._budget.used += size
            else:
                self._full = True  # this one is only blitted; from now on misses skip the offscreen render
        else:
            pixmap, offset, _size = entry
        painter.save()
        painter.setWorldTransform(QTransform())  # the picture is in device coordinates
        painter.drawPixmap(offset, pixmap)
        painter.restore()


class OverlayLayers:
    """The two layers (data space, screen space), their effects and the hit / miss bookkeeping."""

    def __init__(self, plot: pg.PlotItem, *, budget_bytes: int | None = None) -> None:
        view_box = plot.vb
        limit = budget_bytes_from_environment() if budget_bytes is None else int(budget_bytes)
        self._budget = _Budget(limit)
        self.enabled = limit > 0
        self.data = _Layer(view_box, screen_space=False)
        self.data.setParentItem(view_box.childGroup)  # next to the image items, as the overlay items used to be
        self.screen = _Layer(view_box, screen_space=True)
        self.screen.setParentItem(view_box)
        self.screen.setZValue(_LAYER_SCREEN_Z)
        self._effects = (
            LayerCacheEffect(view_box, self._budget, self.items_current),
            LayerCacheEffect(view_box, self._budget, self.items_current),
        )
        self.data.setGraphicsEffect(self._effects[0])
        self.screen.setGraphicsEffect(self._effects[1])
        for effect in self._effects:
            effect.setEnabled(False)  # until `begin` says a state is being shown
        self._epoch: Hashable | None = None
        self._stable = 0
        self.hits = 0
        self.misses = 0
        self._epoch_hits = 0  # since the epoch last changed
        self._epoch_misses = 0
        self._paused = False  # the states of this epoch do not repeat: no pictures are made until the epoch changes
        self.items_state: Hashable | None = None  # the state the items were last drawn for (None: unknown)
        self._shown_state: Hashable | None = None  # the state `begin` announced
        self._refresher: Callable[[], None] | None = None
        view_box.sigRangeChanged.connect(self._on_view_changed)

    # -- parenting -----------------------------------------------------------------------------

    def adopt_data(self, item: QGraphicsItem) -> None:
        """Make `item` (in data coordinates) a child of the data layer, after the items adopted before it."""
        item.setParentItem(self.data)

    def adopt_screen(self, item: QGraphicsItem) -> None:
        item.setParentItem(self.screen)

    # -- size ---------------------------------------------------------------------------------------

    @property
    def budget_megabytes(self) -> float:
        return self._budget.limit / (1024 * 1024)

    def set_budget_bytes(self, limit_bytes: int) -> None:
        """Change the memory budget at run time (the Options menu). 0 switches the cache off; a budget below what is
        already used drops the pictures. The caller re-draws the overlay items afterwards (they may be out of date
        relative to what the dropped pictures showed)."""
        self._budget.limit = max(int(limit_bytes), 0)
        self.enabled = self._budget.limit > 0
        if not self.enabled:
            self.invalidate()
        elif self._budget.used > self._budget.limit:
            self.clear()

    def status_text(self) -> str:
        """One line for the Options menu: what the cache holds right now."""
        if not self.enabled:
            return "Off: the overlay is painted directly"
        if self._paused:
            return "Paused: the overlay never repeated between frames (nothing worth keeping)"
        if self.cached_states == 0:
            return f"On (up to {self.budget_megabytes:.0f} MB): nothing cached yet"
        return f"{self.cached_states} wavelengths cached: {self.cached_megabytes:.0f} of {self.budget_megabytes:.0f} MB"

    # -- what the items hold -----------------------------------------------------------------------

    def mark_drawn(self, state: Hashable) -> None:
        """The caller has just drawn the items for `state`."""
        self.items_state = state

    def items_current(self) -> bool:
        return self._shown_state is not None and self.items_state == self._shown_state

    def set_refresher(self, refresher: Callable[[], None] | None) -> None:
        """`refresher()` must draw the items for the state being shown now and call `mark_drawn`. It is called when the
        view changes (zoom, pan, resize) while the items still hold an earlier state: a skipped re-draw after a cache hit
        must not show up as a wrong overlay in the repaint that follows."""
        self._refresher = refresher

    def _on_view_changed(self, *_args: object) -> None:
        if self.enabled and self._refresher is not None and self._shown_state is not None and not self.items_current():
            self._refresher()

    # -- per frame -------------------------------------------------------------------------------

    def begin(self, epoch: Hashable, state: Hashable) -> bool:
        """Announce the overlay state about to be shown. True: pictures for it exist, the items need not be re-drawn.
        False: the caller re-draws them; the pictures are made (and kept, if the epoch is stable) at the next paint."""
        if not self.enabled:
            return False
        self._shown_state = state
        if epoch != self._epoch:
            self.clear()
            self._epoch = epoch
            self._stable = 0
            self._epoch_hits = self._epoch_misses = 0
            self._paused = False
        else:
            self._stable += 1
        if self._paused:
            for effect in self._effects:
                effect.state = None
                effect.setEnabled(False)
            self.misses += 1
            return False
        for effect in self._effects:
            effect.state = state
            effect.admit = self._stable >= 1
            effect.setEnabled(True)
        hit = all(effect.has(state) for effect in self._effects)
        if hit:
            self.hits += 1
            self._epoch_hits += 1
        else:
            self.misses += 1
            self._epoch_misses += 1
            if self._epoch_hits == 0 and self._epoch_misses >= USELESS_AFTER_MISSES:
                # Many frames and not one repeated: pictures that are never used again only cost memory and a render each.
                self._paused = True
                self.clear()
        return hit

    def clear(self) -> None:
        for effect in self._effects:
            effect.clear()

    @property
    def paused(self) -> bool:
        return self._paused

    def invalidate(self) -> None:
        """The overlay is being changed or hidden by something other than a frame step: drop everything and paint plainly
        until the next `begin`."""
        self._epoch = None
        self._stable = 0
        self._shown_state = None
        self.items_state = None
        self.clear()
        for effect in self._effects:
            effect.state = None
            effect.setEnabled(False)

    # -- diagnostics -----------------------------------------------------------------------------

    @property
    def cached_states(self) -> int:
        return max(effect.count for effect in self._effects)

    @property
    def cached_megabytes(self) -> float:
        return self._budget.used / (1024 * 1024)
