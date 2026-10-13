"""``FrameRolesModule`` - what role each (cube, wavelength) frame plays:
``normal``, ``reference``, ``dark`` or ``ignored`` (2026-10-13, maintainer request).

One owner and one saved value (`FrameRolesState`) for all four, so every panel
asks the same question (`role_of`) and a session saves them in one block.

- **dark**: the dark-background wavelength (LED off). One wavelength; because a
  wavelength exists in every cube, every cube has its own dark frame. Not a
  spectral point: it is only meant to be subtracted from other frames' ROI
  values, later in analysis, **unscaled** (exposure scaling is not decided yet).
  Auto-assigned to 0 nm when the dataset has it and the user has not chosen.
- **ignored**: a wavelength (in every cube) or a whole cube. Data stays
  untouched on disk; consumers decide what to do with an ignored frame.
- **reference**: *derived*, not stored here. `ReferenceFrameModule` still owns
  the mode, the manual frame and Auto's per-cube cache (the `ReferenceFrameRow`
  and Auto-find task are built around it); this module reads it through its
  public queries and saves/restores it together with the other roles.

Precedence when a frame matches more than one: ignored > dark > reference.
The commands keep the stored roles exclusive (making a wavelength the dark
removes it from the ignored wavelengths; ignoring the dark wavelength clears
the dark), so the precedence only matters for an ignored *cube*, which covers
everything in it, and for a reference that was picked before the frame became
dark or ignored.

`is_spectral` (role normal or reference) is the question analysis and fitting
ask: "may this frame be a point in a spectrum?".

Does not connect to `dataset_loaded` on purpose: the session restore
(`app_rewrite.apply_session`) runs after every dataset load and calls
`restore_state`, and a second reset on the load signal could wipe what it just
restored depending on connection order. `dataset_cleared` does reset.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..dataset.model import DARK_FRAME_WAVELENGTH_NM
from ..dataset.module import DatasetModule
from ..diagnostics import instrumented
from ..undo import FunctionCommand, undo_manager
from .frame_roles_state import FrameRolesState
from .reference_frame_module import MODE_AUTO, MODE_MANUAL, ReferenceFrameModule

ROLE_NORMAL = "normal"
ROLE_REFERENCE = "reference"
ROLE_DARK = "dark"
ROLE_IGNORED = "ignored"

# (dark wavelength, dark_user_set, ignored wavelengths, ignored cubes)
_Stored = tuple[float | None, bool, frozenset[float], frozenset[int]]


class FrameRolesModule(QObject):
    roles_changed = pyqtSignal()

    def __init__(self, dataset: DatasetModule, reference_frame: ReferenceFrameModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._reference = reference_frame
        self._dark: float | None = None
        self._dark_user_set = False
        self._ignored_wavelengths: frozenset[float] = frozenset()
        self._ignored_cubes: frozenset[int] = frozenset()
        # A reference change is a role change (the reference *is* a role).
        reference_frame.reference_frame_changed.connect(self.roles_changed)
        dataset.dataset_cleared.connect(self._on_dataset_cleared)

    # -- queries ----------------------------------------------------------------

    def dark_wavelength(self) -> float | None:
        return self._dark

    def ignored_wavelengths(self) -> tuple[float, ...]:
        return tuple(sorted(self._ignored_wavelengths))

    def ignored_cubes(self) -> tuple[int, ...]:
        return tuple(sorted(self._ignored_cubes))

    def reference_wavelength_for(self, cube_index: int) -> float | None:
        """The reference wavelength within `cube_index`: the manual frame's if it
        is in that cube, else (Auto) that cube's cached best wavelength."""
        cube_index = int(cube_index)
        if self._reference.mode() == MODE_MANUAL:
            frame = self._reference.manual_frame()
            return frame[1] if frame is not None and frame[0] == cube_index else None
        return self._reference.auto_wavelength(cube_index)

    def role_of(self, cube_index: int, wavelength_nm: float) -> str:
        cube_index, wavelength_nm = int(cube_index), float(wavelength_nm)
        if cube_index in self._ignored_cubes or wavelength_nm in self._ignored_wavelengths:
            return ROLE_IGNORED
        if self._dark is not None and wavelength_nm == self._dark:
            return ROLE_DARK
        if self.reference_wavelength_for(cube_index) == wavelength_nm:
            return ROLE_REFERENCE
        return ROLE_NORMAL

    def is_spectral(self, cube_index: int, wavelength_nm: float) -> bool:
        """Whether the frame may be a point in a spectrum / fit (normal or reference)."""
        return self.role_of(cube_index, wavelength_nm) in (ROLE_NORMAL, ROLE_REFERENCE)

    def state(self) -> FrameRolesState:
        frame = self._reference.manual_frame()
        return FrameRolesState(
            dark_wavelength_nm=self._dark,
            dark_user_set=self._dark_user_set,
            ignored_wavelengths=self.ignored_wavelengths(),
            ignored_cubes=self.ignored_cubes(),
            reference_mode=self._reference.mode(),
            reference_cube=frame[0] if frame else None,
            reference_wavelength_nm=frame[1] if frame else None,
        )

    # -- session restore ----------------------------------------------------------

    @instrumented("FrameRolesModule.restore_state")
    def restore_state(self, state: FrameRolesState) -> None:
        """Replace everything as a session load (not undo-tracked, but emits).
        A state where the user never chose a dark re-runs the 0 nm auto-assign."""
        self._ignored_wavelengths = frozenset(float(w) for w in state.ignored_wavelengths)
        self._ignored_cubes = frozenset(int(c) for c in state.ignored_cubes)
        self._dark_user_set = bool(state.dark_user_set)
        if self._dark_user_set:
            self._dark = None if state.dark_wavelength_nm is None else float(state.dark_wavelength_nm)
        else:
            self._dark = self._auto_dark()
        self._reference.reset()
        if state.reference_cube is not None and state.reference_wavelength_nm is not None:
            self._reference.set_manual_frame(state.reference_cube, state.reference_wavelength_nm)
            if state.reference_mode == MODE_AUTO:
                self._reference.set_mode(MODE_AUTO)
        self.roles_changed.emit()

    def _auto_dark(self) -> float | None:
        return DARK_FRAME_WAVELENGTH_NM if DARK_FRAME_WAVELENGTH_NM in self._dataset.wavelengths() else None

    def _on_dataset_cleared(self) -> None:
        self._dark = None
        self._dark_user_set = False
        self._ignored_wavelengths = frozenset()
        self._ignored_cubes = frozenset()
        self._reference.reset()
        self.roles_changed.emit()

    # -- commands (undoable) --------------------------------------------------------

    def _stored(self) -> _Stored:
        return (self._dark, self._dark_user_set, self._ignored_wavelengths, self._ignored_cubes)

    def _set_stored(self, stored: _Stored) -> None:
        self._dark, self._dark_user_set, self._ignored_wavelengths, self._ignored_cubes = stored
        self.roles_changed.emit()

    def _commit(self, label: str, new: _Stored) -> None:
        old = self._stored()
        if old == new:
            return
        self._set_stored(new)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: self._set_stored(old), redo_fn=lambda: self._set_stored(new)))

    @instrumented("FrameRolesModule.set_dark")
    def set_dark(self, wavelength_nm: float) -> None:
        """Make `wavelength_nm` the dark background (it must exist in the dataset)."""
        wavelength_nm = float(wavelength_nm)
        if wavelength_nm not in self._dataset.wavelengths():
            raise ValueError(f"{wavelength_nm:g} nm is not a wavelength of the loaded dataset")
        _dark, _user, ignored_wl, ignored_cubes = self._stored()
        self._commit("Dark background", (wavelength_nm, True, ignored_wl - {wavelength_nm}, ignored_cubes))

    @instrumented("FrameRolesModule.clear_dark")
    def clear_dark(self) -> None:
        """No dark frame. Remembered as the user's choice: 0 nm is not auto-assigned again."""
        _dark, _user, ignored_wl, ignored_cubes = self._stored()
        self._commit("Clear dark background", (None, True, ignored_wl, ignored_cubes))

    @instrumented("FrameRolesModule.set_wavelength_ignored")
    def set_wavelength_ignored(self, wavelength_nm: float, ignored: bool) -> None:
        """Ignore (or stop ignoring) a wavelength in every cube. Ignoring the dark
        wavelength clears the dark."""
        wavelength_nm = float(wavelength_nm)
        dark, user_set, ignored_wl, ignored_cubes = self._stored()
        if ignored:
            if dark is not None and wavelength_nm == dark:
                dark, user_set = None, True
            ignored_wl = ignored_wl | {wavelength_nm}
        else:
            ignored_wl = ignored_wl - {wavelength_nm}
        self._commit("Ignore wavelength" if ignored else "Stop ignoring wavelength", (dark, user_set, ignored_wl, ignored_cubes))

    @instrumented("FrameRolesModule.set_cube_ignored")
    def set_cube_ignored(self, cube_index: int, ignored: bool) -> None:
        dark, user_set, ignored_wl, ignored_cubes = self._stored()
        cube_index = int(cube_index)
        ignored_cubes = ignored_cubes | {cube_index} if ignored else ignored_cubes - {cube_index}
        self._commit("Ignore cube" if ignored else "Stop ignoring cube", (dark, user_set, ignored_wl, ignored_cubes))
