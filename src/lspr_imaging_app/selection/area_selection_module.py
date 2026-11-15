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

**One query for every consumer**: ``mask(shape)`` returns a boolean array
(``True`` = editable) or ``None`` when nothing restricts ("All"). Consumers
never read the shape themselves, so inversion is handled in one place.

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


REPLACE = "replace"
ADD = "add"
SUBTRACT = "subtract"
COMBINE_MODES = (REPLACE, ADD, SUBTRACT)


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

    @staticmethod
    def of(vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool) -> "_Snapshot":
        kept = None
        if vertices is not None:
            kept = np.array(vertices, dtype=float)
            kept.setflags(write=False)
        packed = shape = None
        if region is not None:
            shape = (int(region.shape[0]), int(region.shape[1]))
            packed = zlib.compress(np.packbits(region.ravel(order="C")).tobytes(), 1)  # level 1: ~1 ms per 1 MP
        return _Snapshot(kept, shape, packed, bool(inverted))

    def region(self) -> np.ndarray | None:
        if self.region_packed is None or self.region_shape is None:
            return None
        height, width = self.region_shape
        bits = np.unpackbits(np.frombuffer(zlib.decompress(self.region_packed), dtype=np.uint8), count=height * width)
        region = bits.view(bool).reshape((height, width))
        region.setflags(write=False)
        return region


class AreaSelectionModule(QObject):
    """Owns the picker mode and the current selection shape."""

    selection_changed = pyqtSignal()
    mode_changed = pyqtSignal(object)  # AreaSelectionMode
    undo_skipped = pyqtSignal(str)  # an undo / redo was not applied (the pixel grid changed after it); the reason

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = AreaSelectionMode.ALL
        self._vertices: np.ndarray | None = None  # (N, 2) x/y, open polygon
        self._region: np.ndarray | None = None  # read-only boolean pixel map (the wand); never set together with _vertices
        self._inverted = False
        self._mask_cache: tuple[tuple[int, int], np.ndarray] | None = None
        self._current: _Snapshot | None = None  # the packed copy of the state now, made when an undo step needs it
        self._tokens = itertools.count(1)
        self._version = 0  # which state the history is in; see `_push_undo` and `reset`

    # -- queries --------------------------------------------------------------

    def mode(self) -> AreaSelectionMode:
        return self._mode

    def has_selection(self) -> bool:
        return self._vertices is not None or self._region is not None

    def is_inverted(self) -> bool:
        return self._inverted

    def vertices(self) -> np.ndarray | None:
        """The shape's outline vertices (copy), or ``None``."""
        return None if self._vertices is None else self._vertices.copy()

    def region(self) -> np.ndarray | None:
        """The pixel-map selection as made (not inverted; read-only), or ``None`` when the selection is a shape or there
        is none."""
        return self._region

    def mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """``True`` where editing is allowed; ``None`` when unrestricted.
        Only the first two dimensions of *shape* are used. The returned array
        is shared - treat it as read-only."""
        if not self.has_selection():
            return None
        shape_2d = (int(shape[0]), int(shape[1]))
        if self._mask_cache is not None and self._mask_cache[0] == shape_2d:
            return self._mask_cache[1]
        if self._region is not None:
            # A stale region (other image size) restricts to nothing: inverted or not, never "edit everything".
            if self._region.shape != shape_2d:
                result = np.zeros(shape_2d, dtype=bool)
                result.setflags(write=False)
                self._mask_cache = (shape_2d, result)
                return result
            inside = self._region
        else:
            assert self._vertices is not None
            inside = rasterize_polygon(self._vertices, shape_2d)
        result = ~inside if self._inverted else inside
        result.setflags(write=False)
        self._mask_cache = (shape_2d, result)
        return result

    def contains(self, x: float, y: float, shape: tuple[int, ...]) -> bool:
        """Whether view point ``(x, y)`` is editable. ``True`` with no
        selection; ``False`` off the image when one exists."""
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

    @instrumented("AreaSelectionModule.set_rectangle")
    def set_rectangle(self, x0: float, y0: float, x1: float, y1: float) -> None:
        left, right = sorted((float(x0), float(x1)))
        top, bottom = sorted((float(y0), float(y1)))
        if right - left <= 0.0 or bottom - top <= 0.0:
            return  # a click without a drag is not a selection
        self._apply("Rectangle selection", np.array([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=float), None, False)

    @instrumented("AreaSelectionModule.set_polygon")
    def set_polygon(self, points: list[tuple[float, float]]) -> None:
        if len(points) < 3:
            return
        self._apply("Lasso selection", np.asarray(points, dtype=float), None, False)

    @instrumented("AreaSelectionModule.combine_region")
    def combine_region(self, region: np.ndarray, how: str = REPLACE) -> bool:
        """Make *region* (boolean, the displayed image's shape) the selection (``REPLACE``), add it to the current one
        (``ADD``) or take it away (``SUBTRACT``). The current selection is combined as it edits now (its inversion
        included); the result is a plain, not inverted, pixel map. An empty result clears the selection. Returns whether
        the selection changed."""
        if how not in COMBINE_MODES:
            raise ValueError(f"how must be one of {COMBINE_MODES}, got {how!r}")
        new = np.asarray(region, dtype=bool)
        if new.ndim != 2:
            raise ValueError(f"region must be 2-D, got shape {new.shape}")
        current = self.mask(new.shape) if how != REPLACE else None
        if how == ADD and current is not None:
            new = current | new
        elif how == SUBTRACT:
            if current is None:
                return False  # nothing selected: nothing to take away from
            new = current & ~new
        label = {REPLACE: "Select with the wand", ADD: "Add to selection", SUBTRACT: "Take away from selection"}[how]
        if not new.any():
            if not self.has_selection():
                return False
            self._apply(label, None, None, False)  # the result is empty: nothing selected any more
            return True
        if self._region is not None and not self._inverted and self._region.shape == new.shape and np.array_equal(self._region, new):
            return False
        stored = new.copy()
        stored.setflags(write=False)
        self._apply(label, None, stored, False)
        return True

    @instrumented("AreaSelectionModule.invert")
    def invert(self) -> None:
        if not self.has_selection():
            return
        self._apply("Invert selection", self._vertices, self._region, not self._inverted)

    @instrumented("AreaSelectionModule.clear")
    def clear(self) -> None:
        """Drops the selection (the picker mode is left as it is). An undo step: Deselect."""
        if not self.has_selection():
            return
        self._apply("Deselect", None, None, False)

    @instrumented("AreaSelectionModule.reset")
    def reset(self) -> None:
        """Drops the selection because the pixel grid it was made on is gone (rotate / flip / crop). Not an undo step; the
        older steps are void from now on (they hold pixels of the old grid), so their undo is skipped with a message."""
        self._version = next(self._tokens)
        if not self.has_selection():
            return
        self._set_state(None, None, False)
        self.selection_changed.emit()

    # -- state and undo ---------------------------------------------------------

    def _set_state(self, vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool) -> None:
        self._vertices = vertices
        self._region = region
        self._inverted = inverted
        self._mask_cache = None
        self._current = None

    def _snapshot(self) -> _Snapshot:
        if self._current is None:
            self._current = _Snapshot.of(self._vertices, self._region, self._inverted)
        return self._current

    def _apply(self, label: str, vertices: np.ndarray | None, region: np.ndarray | None, inverted: bool) -> None:
        """Make this the selection and record it as one undo step (a fresh shape replaces, never inherits, inversion: the
        callers pass `inverted` explicitly)."""
        before = self._snapshot()
        self._set_state(vertices, region, inverted)
        self._push_undo(label, before, self._snapshot())
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
        self._set_state(snapshot.vertices, snapshot.region(), snapshot.inverted)
        self._current = snapshot  # the state now is exactly this snapshot
        self.selection_changed.emit()
