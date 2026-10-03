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
``ActiveToolModule``: not undoable, not persisted in a session, no
``MaskChange``.

**One query for every consumer**: ``mask(shape)`` returns a boolean array
(``True`` = editable) or ``None`` when nothing restricts ("All"). Consumers
never read the shape themselves, so inversion is handled in one place.
"""

from __future__ import annotations

import enum

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from skimage.draw import polygon2mask

from ..diagnostics import instrumented


class AreaSelectionMode(enum.Enum):
    """Which picker entry is chosen. ``ALL`` = no restriction."""

    ALL = "all"
    RECTANGLE = "rectangle"
    LASSO = "lasso"


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


class AreaSelectionModule(QObject):
    """Owns the picker mode and the current selection shape."""

    selection_changed = pyqtSignal()
    mode_changed = pyqtSignal(object)  # AreaSelectionMode

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = AreaSelectionMode.ALL
        self._vertices: np.ndarray | None = None  # (N, 2) x/y, open polygon
        self._inverted = False
        self._mask_cache: tuple[tuple[int, int], np.ndarray] | None = None

    # -- queries --------------------------------------------------------------

    def mode(self) -> AreaSelectionMode:
        return self._mode

    def has_selection(self) -> bool:
        return self._vertices is not None

    def is_inverted(self) -> bool:
        return self._inverted

    def vertices(self) -> np.ndarray | None:
        """The shape's outline vertices (copy), or ``None``."""
        return None if self._vertices is None else self._vertices.copy()

    def mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """``True`` where editing is allowed; ``None`` when unrestricted.
        Only the first two dimensions of *shape* are used. The returned array
        is shared - treat it as read-only."""
        if self._vertices is None:
            return None
        shape_2d = (int(shape[0]), int(shape[1]))
        if self._mask_cache is not None and self._mask_cache[0] == shape_2d:
            return self._mask_cache[1]
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
        self._set_vertices(np.array([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=float))

    @instrumented("AreaSelectionModule.set_polygon")
    def set_polygon(self, points: list[tuple[float, float]]) -> None:
        if len(points) < 3:
            return
        self._set_vertices(np.asarray(points, dtype=float))

    @instrumented("AreaSelectionModule.invert")
    def invert(self) -> None:
        if self._vertices is None:
            return
        self._inverted = not self._inverted
        self._mask_cache = None
        self.selection_changed.emit()

    @instrumented("AreaSelectionModule.clear")
    def clear(self) -> None:
        """Drops the selection (the picker mode is left as it is)."""
        if self._vertices is None:
            return
        self._vertices = None
        self._inverted = False
        self._mask_cache = None
        self.selection_changed.emit()

    def _set_vertices(self, vertices: np.ndarray) -> None:
        self._vertices = vertices
        self._inverted = False  # a fresh shape replaces, never inherits, inversion
        self._mask_cache = None
        self.selection_changed.emit()
