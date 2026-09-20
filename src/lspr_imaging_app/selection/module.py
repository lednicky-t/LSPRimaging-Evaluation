"""``SelectionModule`` (sketch §7 "Selection / Navigation", §10).

Owns current cube/wavelength and selected ROI ids. Does not itself trigger
analysis recompute - selecting/deselecting ROIs never implicitly triggers
computation (AGENTS.md, analysis store rules).

**Command methods built 2026-09-20**, replacing the scaffold stubs - the
first real consumer of `RoiToolbox.roi_ids_renumbered` (see that module's
docstring, "Cross-module consequence"). Deliberately **not** wired through
`undo.undo_manager`: matching the old app (selecting/deselecting was never
part of `_push_undo_point` there either) and the sketch's "Selection can
never trigger recompute" rule (§9) - undo history is for state that affects
results, not where the cursor/selection happens to be. Every setter is a
no-op (no signal emitted) if the new value equals the current one, matching
the no-op-skip convention `RoiToolbox`'s command methods already use, so
panels don't redraw for a reselect of the same state.

**`remap_roi_ids()`**: subscribed to `RoiToolbox.roi_ids_renumbered` in
`app_rewrite.build_main_window()` (this module has no reference to
`RoiToolbox` itself - it only exposes a public method for another module's
signal to drive, per AGENTS.md's "no module reaches into another's
internals" rule). Drops any selected id that was deleted (absent from the
map) and rewrites survivors to their new id, so a delete-driven renumber in
`RoiToolbox` never leaves this module's selection silently stale.
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
        cube_index = int(cube_index)
        if cube_index < 0:
            raise ValueError(f"cube_index must be >= 0, got {cube_index}")
        if cube_index == self._cube_index:
            return
        self._cube_index = cube_index
        self.cube_changed.emit(cube_index)

    @instrumented("SelectionModule.set_wavelength")
    def set_wavelength(self, wavelength: float) -> None:
        wavelength = float(wavelength)
        if wavelength == self._wavelength:
            return
        self._wavelength = wavelength
        self.wavelength_changed.emit(wavelength)

    @instrumented("SelectionModule.set_roi_selection")
    def set_roi_selection(self, roi_ids: set[int]) -> None:
        new_selection = {int(roi_id) for roi_id in roi_ids}
        if new_selection == self._selected_roi_ids:
            return
        self._selected_roi_ids = new_selection
        self.roi_selection_changed.emit(set(new_selection))

    @instrumented("SelectionModule.remap_roi_ids")
    def remap_roi_ids(self, id_map: dict[int, int]) -> None:
        """Rewrite the current selection through `id_map` (`{old_id:
        new_id}`, as emitted by `RoiToolbox.roi_ids_renumbered`). Any
        selected id absent from `id_map` was deleted, not renumbered, and is
        dropped rather than raising - a delete of a currently-selected ROI
        is an expected, ordinary event, not an error."""
        if not self._selected_roi_ids:
            return
        remapped = {id_map[roi_id] for roi_id in self._selected_roi_ids if roi_id in id_map}
        if remapped == self._selected_roi_ids:
            return
        self._selected_roi_ids = remapped
        self.roi_selection_changed.emit(set(remapped))
