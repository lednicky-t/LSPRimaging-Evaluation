"""``SelectionModule`` (sketch §7 "Selection / Navigation", §10).

Owns current cube/wavelength and selected ROI ids. Does not itself trigger
analysis recompute - selecting/deselecting ROIs never implicitly triggers
computation (AGENTS.md, analysis store rules).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented


class SelectionModule(QObject):
    """Owns the current cube/wavelength/ROI-selection - the one
    intentionally shared piece of state."""

    cube_changed = pyqtSignal(int)
    wavelength_changed = pyqtSignal(float)
    roi_selection_changed = pyqtSignal(set)  # set[int]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cube_index = 0
        self._wavelength = 0.0
        self._selected_roi_ids: set[int] = set()

    def current_cube(self) -> int:
        return self._cube_index

    def current_wavelength(self) -> float:
        return self._wavelength

    def selected_roi_ids(self) -> frozenset[int]:
        return frozenset(self._selected_roi_ids)

    @instrumented("SelectionModule.set_cube")
    def set_cube(self, cube_index: int) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("SelectionModule.set_wavelength")
    def set_wavelength(self, wavelength: float) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("SelectionModule.set_roi_selection")
    def set_roi_selection(self, roi_ids: set[int]) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError
