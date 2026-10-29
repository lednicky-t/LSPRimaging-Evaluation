"""``ReferenceFrameModule`` - which (cube, wavelength) counts as the
"reference frame", auto or manual (2026-09-25, maintainer request to
replace the stable app's standalone "Reference" section with a compact
row - see ``panels/workflow/reference_frame_row.py``).

**New module, not part of the original sketch's module list** - added
because nothing else owns this state. Investigated first (see the
Dataset-section build log entries from 2026-09-24): `ChromaticModule` has
a same-named but unrelated `reference_mode`/`reference_wavelength_nm`
(chromatic correction's own registration-target wavelength), and neither
`SelectionModule` (current cube/wavelength being *viewed*) nor
`DatasetModule` claims "which frame is the reference" as their concern.

**Deliberately narrow, per CLAUDE.md's module-boundary rule ("no module
reaches into another's internals")**: this module does not hold a
`SelectionModule` reference and cannot resolve "auto" mode's frame by
itself. It only owns `mode` and the manual snapshot; the caller (the
Workflow panel's reference-frame row) reads `SelectionModule.current_cube()`/
`current_wavelength()` itself for "auto" mode, and calls
`set_manual_frame()` with an already-resolved cube/wavelength for
"manual" - the same "commands take already-resolved values" convention
`RoiToolbox.detect_rois`/`MaskModule.apply_candidate` already established.

**Auto = "Find reference" (2026-10-11)**: like the stable app, the best-contrast
wavelength of the cube being viewed (`reference_scoring.best_contrast_wavelength`,
run in the background by `reference_auto_task.ReferenceAutoFind`), cached here
per cube. It no longer mirrors the wavelength slider. Manual = "Set reference".
Not ported: the stable app's per-image exclusions (no such subsystem here).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented

MODE_AUTO = "auto"
MODE_MANUAL = "manual"
_MODES = (MODE_AUTO, MODE_MANUAL)


class ReferenceFrameModule(QObject):
    """Owns the reference-frame mode and the manual snapshot."""

    reference_frame_changed = pyqtSignal()
    find_requested = pyqtSignal()  # the user clicked Find reference (handled by `ReferenceAutoFind`)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = MODE_AUTO
        self._manual_cube: int | None = None
        self._manual_wavelength: float | None = None
        self._auto_wavelengths: dict[int, float] = {}

    def mode(self) -> str:
        return self._mode

    # -- "Find reference" (Auto) --------------------------------------------
    # Auto = the best-contrast wavelength *of the cube being viewed*, found by
    # `ReferenceAutoFind` (selection/reference_auto_task.py) and cached here
    # per cube. It does not follow the wavelength slider (2026-10-11,
    # maintainer request; replaces the earlier "mirror the view" Auto).

    def request_find(self) -> None:
        """The user clicked "Find reference": score the viewed cube once and store
        the winner as the reference frame. Never called automatically."""
        self.find_requested.emit()

    def auto_wavelength(self, cube_index: int) -> float | None:
        """The cached best wavelength of `cube_index`, or `None` if it has
        not been found yet."""
        return self._auto_wavelengths.get(int(cube_index))

    def set_auto_wavelength(self, cube_index: int, wavelength: float) -> None:
        cube_index, wavelength = int(cube_index), float(wavelength)
        if self._auto_wavelengths.get(cube_index) == wavelength:
            return
        self._auto_wavelengths[cube_index] = wavelength
        self.reference_frame_changed.emit()

    def resolve(self, current_cube: int | None) -> tuple[int, float] | None:
        """The reference frame in effect while `current_cube` is viewed:
        the manual snapshot, or (Auto) that cube's cached best wavelength.
        `None` = none yet (Auto not computed for this cube, or no cube)."""
        if self._mode == MODE_MANUAL:
            return self.manual_frame()
        if current_cube is None:
            return None
        wavelength = self._auto_wavelengths.get(int(current_cube))
        return None if wavelength is None else (int(current_cube), wavelength)

    def manual_frame(self) -> tuple[int, float] | None:
        if self._manual_cube is None or self._manual_wavelength is None:
            return None
        return (self._manual_cube, self._manual_wavelength)

    @instrumented("ReferenceFrameModule.set_mode")
    def set_mode(self, mode: str) -> None:
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        self.reference_frame_changed.emit()

    @instrumented("ReferenceFrameModule.set_manual_frame")
    def set_manual_frame(self, cube_index: int, wavelength: float) -> None:
        """Switches to manual mode (if not already) and snapshots
        `(cube_index, wavelength)` as the manual reference - matches the
        source's Manual button, which is a "store the current view" action,
        not just a mode switch. Always re-snapshots, even if already in
        manual mode with the same values, so re-clicking Manual after
        navigating elsewhere reliably updates the stored frame."""
        cube_index = int(cube_index)
        wavelength = float(wavelength)
        self._mode = MODE_MANUAL
        self._manual_cube = cube_index
        self._manual_wavelength = wavelength
        self.reference_frame_changed.emit()

    @instrumented("ReferenceFrameModule.reset")
    def reset(self) -> None:
        """Back to defaults - called when the dataset changes/clears
        (wired in `app_rewrite.build_main_window`) so a manual reference
        can never silently point at a frame from a dataset that is no
        longer loaded."""
        if (
            self._mode == MODE_AUTO
            and self._manual_cube is None
            and self._manual_wavelength is None
            and not self._auto_wavelengths
        ):
            return
        self._auto_wavelengths.clear()
        self._mode = MODE_AUTO
        self._manual_cube = None
        self._manual_wavelength = None
        self.reference_frame_changed.emit()
