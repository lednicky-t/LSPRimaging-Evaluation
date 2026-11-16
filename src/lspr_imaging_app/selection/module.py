"""``SelectionModule`` (sketch §7 "Selection / Navigation", §10).

Owns current cube/wavelength and selected ROI ids. Does not itself trigger
analysis recompute - selecting/deselecting ROIs never implicitly triggers
computation (CLAUDE.md, analysis store rules).

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
signal to drive, per CLAUDE.md's "no module reaches into another's
internals" rule). Drops any selected id that was deleted (absent from the
map) and rewrites survivors to their new id, so a delete-driven renumber in
`RoiToolbox` never leaves this module's selection silently stale.

**The ROI selection lock (2026-11-16, maintainer request: "lock selection of ROIs, so clicking and so on would not influence
it").** While it is on, `set_roi_selection` changes nothing: every gesture that selects (a click or a rubber band on the image, a click
in the ROI table, "Select whole group", "Select ROIs in selection", a new ROI) goes through that one method, so one check there covers them all,
and each refused change emits `roi_selection_refused` so the panels can say why nothing happened (a refusal is never silent). Three things
deliberately pass the lock: `remap_roi_ids` (a ROI that is deleted or renumbered must leave or follow the selection, or the selection
would point at the wrong ROI), `restore_roi_selection` (a session load brings a whole new state), and the lock itself.
**The lock is part of the session** (maintainer, 2026-11-16: "should be saved in session, to load with a new one"): saved with the selection it
holds (`SessionState.selection_locked`, session schema 1.10, written only when locked), and a session load sets it (`app_rewrite.apply_session`),
so a blank or older session opens unlocked and each session has its own. It is not a UI setting that is remembered across restarts. Like the
selection, it is not undoable.
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
    roi_selection_lock_changed = pyqtSignal(bool)  # the lock went on / off
    roi_selection_refused = pyqtSignal()  # a change of the selection was asked for while it is locked and was not made

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cube_index = 0
        self._wavelength = 0.0
        self._selected_roi_ids: set[int] = set()
        self._roi_selection_locked = False

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

    def roi_selection_locked(self) -> bool:
        return self._roi_selection_locked

    @instrumented("SelectionModule.set_roi_selection_locked")
    def set_roi_selection_locked(self, locked: bool) -> None:
        """Lock the ROI selection as it is (or release it). With nothing selected the lock keeps "nothing selected"."""
        locked = bool(locked)
        if locked == self._roi_selection_locked:
            return
        self._roi_selection_locked = locked
        self.roi_selection_lock_changed.emit(locked)

    @instrumented("SelectionModule.set_roi_selection")
    def set_roi_selection(self, roi_ids: set[int]) -> None:
        new_selection = {int(roi_id) for roi_id in roi_ids}
        if new_selection == self._selected_roi_ids:
            return
        if self._roi_selection_locked:
            self.roi_selection_refused.emit()
            return
        self._selected_roi_ids = new_selection
        self.roi_selection_changed.emit(set(new_selection))

    @instrumented("SelectionModule.restore_roi_selection")
    def restore_roi_selection(self, roi_ids: set[int]) -> None:
        """Put a whole saved selection in place (a session load), lock or no lock: see the module docstring."""
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
