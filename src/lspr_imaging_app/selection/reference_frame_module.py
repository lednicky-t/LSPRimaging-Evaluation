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

**Deliberately narrow, per AGENTS.md's module-boundary rule ("no module
reaches into another's internals")**: this module does not hold a
`SelectionModule` reference and cannot resolve "auto" mode's frame by
itself. It only owns `mode` and the manual snapshot; the caller (the
Workflow panel's reference-frame row) reads `SelectionModule.current_cube()`/
`current_wavelength()` itself for "auto" mode, and calls
`set_manual_frame()` with an already-resolved cube/wavelength for
"manual" - the same "commands take already-resolved values" convention
`RoiToolbox.detect_rois`/`MaskModule.apply_candidate` already established.

**Scoped down from the stable app's "Auto" mode, flagged rather than
silently simplified**: the source's Auto mode picks the *best-contrast*
wavelength in the current cube (`MainWindow._auto_reference_image_key_for_
spectral_cube`/`_reference_contrast_score` - real pixel loading + scoring
for every candidate wavelength). This rewrite's Auto mode is simpler: it
always mirrors whatever `SelectionModule` says is currently being viewed,
live - no contrast scoring, no picking. Revisit if/when a real image
canvas exists to preview the difference against.
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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode = MODE_AUTO
        self._manual_cube: int | None = None
        self._manual_wavelength: float | None = None

    def mode(self) -> str:
        return self._mode

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
        if self._mode == MODE_AUTO and self._manual_cube is None and self._manual_wavelength is None:
            return
        self._mode = MODE_AUTO
        self._manual_cube = None
        self._manual_wavelength = None
        self.reference_frame_changed.emit()
