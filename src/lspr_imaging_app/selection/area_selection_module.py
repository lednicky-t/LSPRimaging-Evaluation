"""``AreaSelectionModule`` - the Photoshop-style "area selection" (rectangle
or lasso) that restricts the Image panel's *editing* tools and the Histogram
plot to a region of the image (2026-10-03, maintainer request).

**What it restricts, and what it deliberately does not.** It limits where
mask edits (Histogram-selection Add/Subtract, Morphology, Draw), ROI
creation/moves, and the Histogram's plotted pixels may act. It never changes
analysis: spectra, sensorgrams and every stored result are computed exactly
as before (maintainer, 2026-10-03: "selection is just for image area
editing"). So it is *not* a ``ComputationalChange`` and never triggers
``run_analysis``.

**Space.** The shape is stored in *displayed* image coordinates (the
processed image the Image panel shows: after rotate/flip/crop), in pyqtgraph
view units where pixel ``(row, col)`` covers ``[col, col+1) x [row, row+1)``.
A selection therefore stays where it was drawn when the wavelength changes
(it is "what you see"), and is cleared when the pixel grid itself changes
(``app_rewrite.py`` wires ``GeometryModule.geometry_changed`` to ``clear``).

**Shared, transient state** - same category as ``HighlightRangeModule`` and
``ActiveToolModule``: not persisted in a session, no ``MaskChange``.

**Undoable since 2026-11-15 (maintainer: "implement undo for selection")**, unlike the ROI / cube selection of
``SelectionModule``, which stays non-undoable. Why this one: the selection is now made by work that is lost by one
wrong click (a wand click that ran into the background, a Shift+click that added junk to a selection built click by
click), and the wand's "Max size" guard that used to refuse such a click was removed because Ctrl+Z replaces it. Every
change of the selection (rectangle, lasso, wand replace / add / subtract, invert, deselect) pushes one step on the
shared ``undo_manager`` that restores the whole previous state (shape or pixel map, and inversion). A step holds
*snapshots* (``_Snapshot``: the pixel map zlib-packed like ``MaskChange``, ~1 ms per 1 MP, a few KB for a particle), so
the 200-step stack of the manager stays small. Picking a tool (``set_mode``) is not a step: only the selection is.
What is not a step is ``reset`` (the pixel grid changed: rotate / flip / crop): it drops the selection and takes a fresh
**version token**, so an older step, which holds pixels of the old grid, is skipped with a message (``undo_skipped``)
instead of restoring a map that does not fit the image (same guard as ``MaskModule``'s row commands).

**The lock (2026-11-15, maintainer's idea: "a temporary crop without cropping")** separates two roles that the selection
used to play at once: the *fence* (where tools may act) and the *product* (what a selection tool picked). The lock is the
fence: a pixel map, **one level only** (a new lock replaces the old one, Unlock drops it). Everything outside it is
dimmed by the Image panel, and **every selection is clipped to it**: a rectangle, lasso or wand result can never reach
outside the lock, so a selection made inside a lock and then locked again simply narrows the fence. *Lock selection*
turns the selection into the lock and clears the selection ("a locked selection is not a selection any more"); with
nothing selected a lock does not make "everything" selected (``selected_mask`` stays ``None``, so Add selection to the
mask adds nothing). The lock is transient like the selection (not saved, not a ``ComputationalChange``, never touches
analysis), is part of every undo step's snapshot, and is dropped by ``reset``. The tools compute on the whole image and
are cut to the lock afterwards (maintainer, 2026-11-15), so a result inside a lock equals the unlocked result AND the lock.

**Combine modes**: Replace / Add / Subtract / **Intersect** (2026-11-15). The *sticky* mode (``combine_mode``) is what the
toolbar's mode button shows; Shift / Alt / Shift+Alt at the gesture override it for one gesture (Photoshop's keys).

**Two queries**: ``selected_mask(shape)`` is the selection itself (clipped to the lock; ``None`` when nothing is
selected): what *Add selection to mask* and the outline use. ``mask(shape)`` is the **region editing may reach** (``True`` =
editable): the selection if there is one, else the lock if there is one, else ``None`` ("All", nothing restricts). Every
tool that merely stays inside (mask edits, Histogram, ROI placement, the preview) keeps reading ``mask``; none of them
had to change when the lock arrived. Consumers never read the shape themselves, so inversion is handled in one place.

**Pixel-map selections** (2026-11-14, the magic wand): besides a polygon the
module can hold a boolean *region* of the displayed image (``combine_region``).
A click replaces the selection, Shift+click adds to it and Alt+click takes away
from it; adding to or taking from a rectangle / lasso first turns that shape into
its pixels (so the result is always exactly what ``mask`` gave before, plus or
minus the new pixels). A region made for one image size and asked for at another
(it can only happen in the moment between a geometry change and the ``clear``
that change triggers) restricts editing to *nothing*, never to everything.
"""

from __future__ import annotations

import enum
import itertools
import logging
import zlib
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from skimage.draw import polygon2mask

from ..diagnostics import instrumented
from ..undo import FunctionCommand, undo_manager

logger = logging.getLogger(__name__)


class AreaSelectionMode(enum.Enum):
    """Which picker entry is chosen. ``ALL`` = no restriction."""

    ALL = "all"
    RECTANGLE = "rectangle"
    LASSO = "lasso"
    WAND = "wand"  # 2026-11-14: a click selects the feature under it (image_tools/selection_wand.py)
    BRUSH = "brush"  # 2026-11-15: drag to paint into the selection (Alt = erase), `image_tools/brush_stroke.py`
    # 2026-11-15, "Select by value" split into three tools (`panels/image/selection_by_value.py`): armed like the wand; a click on the image
    # selects the pixels the method finds (the point clicked does not matter: they are image-wide methods).
    HISTOGRAM = "histogram"
    THRESHOLD = "threshold"
    CONTRAST = "contrast"


VALUE_MODES = (AreaSelectionMode.HISTOGRAM, AreaSelectionMode.THRESHOLD, AreaSelectionMode.CONTRAST)
# The tools that make their selection with a click, not a drag (the canvas does not start a rectangle / lasso / stroke for them).
CLICK_MODES = frozenset({AreaSelectionMode.WAND, *VALUE_MODES})


REPLACE = "replace"
ADD = "add"
SUBTRACT = "subtract"
INTERSECT = "intersect"  # 2026-11-15: keep only what the old selection and the new region share
COMBINE_MODES = (REPLACE, ADD, SUBTRACT, INTERSECT)

_KEEP = object()  # `_apply`: leave the lock as it is


def how_for_keys(shift: bool, alt: bool, sticky: str) -> str:
    """How a selection gesture combines with the selection: Shift adds, Alt takes away, Shift+Alt intersects (Photoshop's keys);
    with no key, the *sticky* mode of the toolbar's mode button. Pure (no Qt), so the canvas, the toolbar's one-shot buttons and the
    tests share one rule."""
    if shift and alt:
        return INTERSECT
    if alt:
        return SUBTRACT
    if shift:
        return ADD
    return sticky


def rasterize_polygon(vertices: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Boolean mask of the pixels whose *centers* lie inside the polygon.

    *vertices* is ``(N, 2)`` of ``(x, y)`` view coordinates. Pure
    computation - no Qt - so it is unit-testable on its own."""
    if len(vertices) < 3:
        return np.zeros(shape, dtype=bool)
    # polygon2mask wants (row, col) with integers at pixel centers; view
    # coordinates have pixel centers at +0.5.
    rows_cols = np.column_stack((vertices[:, 1] - 0.5, vertices[:, 0] - 0.5))
    return polygon2mask(shape, rows_cols)


@dataclass(frozen=True, slots=True, eq=False)
class _Snapshot:
    """One state of the selection, kept by an undo step. Immutable; the pixel map is zlib-packed (see the module docstring)."""

    vertices: np.ndarray | None  # read-only
    region_shape: tuple[int, int] | None
    region_packed: bytes | None
    inverted: bool
    lock_shape: tuple[int, int] | None = None  # the lock (2026-11-15), packed like the region
    lock_packed: bytes | None = None

    @staticmethod
    def _pack(pixels: np.ndarray | None) -> tuple[tuple[int, int] | None, bytes | None]:
        if pixels is None:
            return None, None
        shape = (int(pixels.shape[0]), int(pixels.shape[1]))
        return shape, zlib.compress(np.packbits(pixels.ravel(order="C")).tobytes(), 1)  # level 1: ~1 ms per 1 MP

    @staticmethod
    def _unpack(shape: tuple[int, int] | None, packed: bytes | None) -> np.ndarray | None:
        if packed is None or shape is None:
            return None
        height, width = shape
        bits = np.unpackbits(np.frombuffer(zlib.decompress(packed), dtype=np.uint8), count=height * width)
        pixels = bits.view(bool).reshape((height, width))
        pixels.setflags(write=False)
        return pixels

    @staticmethod
    def of(
        vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool, lock: np.ndarray | None = None
    ) -> "_Snapshot":
        kept = None
        if vertices is not None:
            kept = np.array(vertices, dtype=float)
            kept.setflags(write=False)
        shape, packed = _Snapshot._pack(region)
        lock_shape, lock_packed = _Snapshot._pack(lock)
        return _Snapshot(kept, shape, packed, bool(inverted), lock_shape, lock_packed)

    def region(self) -> np.ndarray | None:
        return self._unpack(self.region_shape, self.region_packed)

    def lock(self) -> np.ndarray | None:
        return self._unpack(self.lock_shape, self.lock_packed)


class AreaSelectionModule(QObject):
    """Owns the picker mode, the combine mode, the current selection shape and the lock."""

    selection_changed = pyqtSignal()  # the selection, its inversion or the lock changed: the region editing may reach may differ
    lock_changed = pyqtSignal()  # the lock was made, dropped or restored (emitted just before `selection_changed`)
    mode_changed = pyqtSignal(object)  # AreaSelectionMode
    combine_mode_changed = pyqtSignal(str)  # REPLACE / ADD / SUBTRACT / INTERSECT: the sticky mode of the toolbar's mode button
    undo_skipped = pyqtSignal(str)  # an undo / redo was not applied (the pixel grid changed after it); the reason

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = AreaSelectionMode.ALL
        self._combine = REPLACE
        self._vertices: np.ndarray | None = None  # (N, 2) x/y, open polygon; never set while a lock is on (see `_set_shape`)
        self._region: np.ndarray | None = None  # read-only boolean pixel map (the wand); never set together with _vertices
        self._inverted = False
        self._lock: np.ndarray | None = None  # read-only boolean pixel map: the fence (one level)
        self._selected_cache: tuple[tuple[int, int], np.ndarray] | None = None
        self._current: _Snapshot | None = None  # the packed copy of the state now, made when an undo step needs it
        self._tokens = itertools.count(1)
        self._version = 0  # which state the history is in; see `_push_undo` and `reset`

    # -- queries --------------------------------------------------------------

    def mode(self) -> AreaSelectionMode:
        return self._mode

    def combine_mode(self) -> str:
        """The sticky combine mode (``REPLACE`` / ``ADD`` / ``SUBTRACT`` / ``INTERSECT``) of the toolbar's mode button."""
        return self._combine

    def has_selection(self) -> bool:
        """Whether something is *selected* (the product). A lock alone is not a selection."""
        return self._vertices is not None or self._region is not None

    def has_lock(self) -> bool:
        return self._lock is not None

    def is_inverted(self) -> bool:
        return self._inverted

    def vertices(self) -> np.ndarray | None:
        """The shape's outline vertices (copy), or ``None``."""
        return None if self._vertices is None else self._vertices.copy()

    def region(self) -> np.ndarray | None:
        """The pixel-map selection as made (not inverted; read-only), or ``None`` when the selection is a shape or there
        is none."""
        return self._region

    def lock_region(self) -> np.ndarray | None:
        """The lock as made (read-only boolean pixel map of the displayed image), or ``None``."""
        return self._lock

    def lock_mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """``True`` inside the lock; ``None`` with no lock. A lock made for another image size (only in the moment between
        a geometry change and the ``reset`` it triggers) is "nothing", never "everything"."""
        if self._lock is None:
            return None
        shape_2d = (int(shape[0]), int(shape[1]))
        if self._lock.shape == shape_2d:
            return self._lock
        stale = np.zeros(shape_2d, dtype=bool)
        stale.setflags(write=False)
        return stale

    def selected_mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """The selection itself: ``True`` where something is selected, already clipped to the lock; ``None`` when nothing is
        selected (a lock alone selects nothing). Only the first two dimensions of *shape* are used. The returned array is
        shared - treat it as read-only."""
        if not self.has_selection():
            return None
        shape_2d = (int(shape[0]), int(shape[1]))
        if self._selected_cache is not None and self._selected_cache[0] == shape_2d:
            return self._selected_cache[1]
        if self._region is not None:
            # A stale region (other image size) restricts to nothing: inverted or not, never "edit everything".
            if self._region.shape != shape_2d:
                result = np.zeros(shape_2d, dtype=bool)
                result.setflags(write=False)
                self._selected_cache = (shape_2d, result)
                return result
            inside = self._region
        else:
            assert self._vertices is not None
            inside = rasterize_polygon(self._vertices, shape_2d)
        result = ~inside if self._inverted else inside
        fence = self.lock_mask(shape_2d)
        if fence is not None:
            result = result & fence
        result.setflags(write=False)
        self._selected_cache = (shape_2d, result)
        return result

    def mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """``True`` where editing is allowed; ``None`` when unrestricted. The selection (clipped to the lock) when there is
        one, else the lock, else ``None``. Only the first two dimensions of *shape* are used. The returned array is
        shared - treat it as read-only."""
        selected = self.selected_mask(shape)
        if selected is not None:
            return selected
        return self.lock_mask(shape)

    def canvas_mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """The region that is the *canvas* (2026-11-15): the lock if there is one (it stays put while selections are made inside
        it), else the selection (before the lock existed the Histogram narrowed to a selection; with nothing locked it still does),
        else ``None`` (the whole image). What the Histogram plots."""
        fence = self.lock_mask(shape)
        return fence if fence is not None else self.mask(shape)

    def contains(self, x: float, y: float, shape: tuple[int, ...]) -> bool:
        """Whether view point ``(x, y)`` is editable. ``True`` with no
        selection and no lock; ``False`` off the image when one exists."""
        mask = self.mask(shape)
        if mask is None:
            return True
        col, row = int(np.floor(x)), int(np.floor(y))
        if not (0 <= row < mask.shape[0] and 0 <= col < mask.shape[1]):
            return False
        return bool(mask[row, col])

    # -- commands -------------------------------------------------------------

    @instrumented("AreaSelectionModule.set_mode")
    def set_mode(self, mode: AreaSelectionMode) -> None:
        """Picking ``ALL`` clears the selection (nothing restricts)."""
        if mode is self._mode:
            return
        self._mode = mode
        self.mode_changed.emit(mode)
        if mode is AreaSelectionMode.ALL:
            self.clear()

    def set_combine_mode(self, how: str) -> None:
        """Pick the sticky combine mode. Not an undo step (it is a tool setting, like the picker mode)."""
        if how not in COMBINE_MODES:
            raise ValueError(f"how must be one of {COMBINE_MODES}, got {how!r}")
        if how == self._combine:
            return
        self._combine = how
        self.combine_mode_changed.emit(how)

    @instrumented("AreaSelectionModule.set_rectangle")
    def set_rectangle(
        self, x0: float, y0: float, x1: float, y1: float, how: str = REPLACE, shape: tuple[int, ...] | None = None
    ) -> None:
        """Select the rectangle. ``how`` other than ``REPLACE`` needs the displayed image's ``shape`` (the shape is then turned
        into pixels, like the wand's region); so does any rectangle made while a lock is on (its pixels are clipped)."""
        left, right = sorted((float(x0), float(x1)))
        top, bottom = sorted((float(y0), float(y1)))
        if right - left <= 0.0 or bottom - top <= 0.0:
            return  # a click without a drag is not a selection
        vertices = np.array([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=float)
        self._set_shape("Rectangle selection", vertices, how, shape)

    @instrumented("AreaSelectionModule.set_polygon")
    def set_polygon(self, points: list[tuple[float, float]], how: str = REPLACE, shape: tuple[int, ...] | None = None) -> None:
        """As `set_rectangle`, for a lasso outline."""
        if len(points) < 3:
            return
        self._set_shape("Lasso selection", np.asarray(points, dtype=float), how, shape)

    def _set_shape(self, label: str, vertices: np.ndarray, how: str, shape: tuple[int, ...] | None) -> None:
        if how not in COMBINE_MODES:
            raise ValueError(f"how must be one of {COMBINE_MODES}, got {how!r}")
        if how == REPLACE and self._lock is None:
            self._apply(label, vertices, None, False)  # the plain case keeps the exact outline
            return
        # Combined with the old selection, or clipped to a lock: the shape is turned into the pixels it covers.
        shape_2d = self._lock.shape if self._lock is not None else shape
        if shape_2d is None:
            raise ValueError("the displayed image's size is needed to combine a shape with the selection")
        self.combine_region(rasterize_polygon(vertices, (int(shape_2d[0]), int(shape_2d[1]))), how, label)

    @instrumented("AreaSelectionModule.combine_region")
    def combine_region(self, region: np.ndarray, how: str = REPLACE, label: str | None = None) -> bool:
        """Make *region* (boolean, the displayed image's shape) the selection (``REPLACE``), add it to the current one
        (``ADD``), take it away (``SUBTRACT``) or keep only what both share (``INTERSECT``; with nothing selected it is the
        region alone). The current selection is combined as it edits now (its inversion included); the result is **clipped
        to the lock** and is a plain, not inverted, pixel map. An empty result clears the selection. Returns whether the
        selection changed."""
        if how not in COMBINE_MODES:
            raise ValueError(f"how must be one of {COMBINE_MODES}, got {how!r}")
        new = np.asarray(region, dtype=bool)
        if new.ndim != 2:
            raise ValueError(f"region must be 2-D, got shape {new.shape}")
        # The *selection* (a lock alone is not one): with only a lock, ADD must not add the whole lock to the new region.
        current = self.selected_mask(new.shape) if how != REPLACE else None
        if how == ADD and current is not None:
            new = current | new
        elif how == SUBTRACT:
            if current is None:
                return False  # nothing selected: nothing to take away from
            new = current & ~new
        elif how == INTERSECT and current is not None:
            new = current & new
        fence = self.lock_mask(new.shape)
        if fence is not None:
            new = new & fence
        text = label or {
            REPLACE: "Select with the wand", ADD: "Add to selection", SUBTRACT: "Take away from selection",
            INTERSECT: "Intersect with selection",
        }[how]
        if not new.any():
            if not self.has_selection():
                return False
            self._apply(text, None, None, False)  # the result is empty: nothing selected any more
            return True
        if self._region is not None and not self._inverted and self._region.shape == new.shape and np.array_equal(self._region, new):
            return False
        stored = new.copy()
        stored.setflags(write=False)
        self._apply(text, None, stored, False)
        return True

    @instrumented("AreaSelectionModule.invert")
    def invert(self) -> None:
        if not self.has_selection():
            return
        self._apply("Invert selection", self._vertices, self._region, not self._inverted)

    @instrumented("AreaSelectionModule.clear")
    def clear(self) -> None:
        """Drops the selection (the picker mode and the lock are left as they are). An undo step: Deselect."""
        if not self.has_selection():
            return
        self._apply("Deselect", None, None, False)

    @instrumented("AreaSelectionModule.lock_selection")
    def lock_selection(self, shape: tuple[int, ...]) -> bool:
        """Make the selection (as it edits now, inside the old lock) the lock, and clear the selection. A new lock replaces
        the old one: it can only be smaller, because a selection never reaches outside the lock. Returns ``False`` (nothing
        done) when nothing is selected or the selection covers no pixel. One undo step."""
        selected = self.selected_mask(shape)
        if selected is None or not selected.any():
            return False
        fence = selected.copy()
        fence.setflags(write=False)
        self._apply("Lock selection", None, None, False, lock=fence)
        return True

    @instrumented("AreaSelectionModule.unlock")
    def unlock(self) -> bool:
        """Drop the lock. The selection, if any, stays exactly as it is seen now (an inverted one becomes the pixels it
        showed inside the lock, so nothing outside the old lock suddenly counts). Returns ``False`` with no lock. One undo
        step."""
        if self._lock is None:
            return False
        selected = self.selected_mask(self._lock.shape)
        kept = None
        if selected is not None and selected.any():
            kept = selected.copy()
            kept.setflags(write=False)
        self._apply("Unlock", None, kept, False, lock=None)
        return True

    @instrumented("AreaSelectionModule.reset")
    def reset(self) -> None:
        """Drops the selection and the lock because the pixel grid they were made on is gone (rotate / flip / crop). Not an
        undo step; the older steps are void from now on (they hold pixels of the old grid), so their undo is skipped with a
        message."""
        self._version = next(self._tokens)
        if not self.has_selection() and self._lock is None:
            return
        had_lock = self._lock is not None
        self._set_state(None, None, False, None)
        if had_lock:
            self.lock_changed.emit()
        self.selection_changed.emit()

    # -- state and undo ---------------------------------------------------------

    def _set_state(
        self, vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool, lock: np.ndarray | None
    ) -> None:
        self._vertices = vertices
        self._region = region
        self._inverted = inverted
        self._lock = lock
        self._selected_cache = None
        self._current = None

    def _snapshot(self) -> _Snapshot:
        if self._current is None:
            self._current = _Snapshot.of(self._vertices, self._region, self._inverted, self._lock)
        return self._current

    def _apply(
        self, label: str, vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool, lock: object = _KEEP
    ) -> None:
        """Make this the selection (and, when `lock` is given, the lock) and record it as one undo step (a fresh shape
        replaces, never inherits, inversion: the callers pass `inverted` explicitly)."""
        new_lock = self._lock if lock is _KEEP else lock
        before = self._snapshot()
        lock_changed = new_lock is not self._lock
        self._set_state(vertices, region, inverted, new_lock)  # type: ignore[arg-type]
        self._push_undo(label, before, self._snapshot())
        if lock_changed:
            self.lock_changed.emit()
        self.selection_changed.emit()

    def _push_undo(self, label: str, before: _Snapshot, after: _Snapshot) -> None:
        """One step: undo restores `before`, redo `after`, each only while the history is in the state the step left
        (undo) or found (redo): the version token. A `reset` in between makes the step void and says so."""
        before_token, after_token = self._version, next(self._tokens)
        self._version = after_token

        def go(which: int) -> None:  # 0 = undo (to `before`), 1 = redo (to `after`)
            if self._version != (after_token, before_token)[which]:
                what = "undone" if which == 0 else "redone"
                logger.info("selection step %r not %s: the image changed after it", label, what)
                self.undo_skipped.emit(f"'{label}' was not {what}: the image was rotated, flipped or cropped after it.")
                return
            self._restore((before, after)[which])
            self._version = (before_token, after_token)[which]

        undo_manager.push(FunctionCommand(label, undo_fn=lambda: go(0), redo_fn=lambda: go(1)))

    def _restore(self, snapshot: _Snapshot) -> None:
        had_lock = self._lock is not None
        self._set_state(snapshot.vertices, snapshot.region(), snapshot.inverted, snapshot.lock())
        self._current = snapshot  # the state now is exactly this snapshot
        if had_lock or self._lock is not None:
            self.lock_changed.emit()
        self.selection_changed.emit()
