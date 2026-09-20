"""``GeometryModule`` - crop/rotate/flip (sketch §7 "Image Tools", §10).

Owns ``GeometrySettings`` (split out of the old app's ``PreprocessingSettings``
- see ``model.py``'s docstring). Emits ``geometry_changed`` (a computational
change - AGENTS.md non-negotiable invariant: rotation/flip/crop are not
display-only, they resample the actual pixel grid every downstream
calculation reads).

**Real command methods built 2026-09-20**, replacing the scaffold stubs -
ported from `gui/image_tools_controller.py` on `develop` (crop/rotate/flip/
image-tools-link *state mutation* only; that file's tool-activation,
overlay-drawing, and pyqtgraph-widget-sync code stays in the not-yet-built
panel layer, the same split RoiToolbox's own port used). Every setter takes
an already-resolved absolute value, never a delta or a "toggle from
current" - matching `RoiToolbox.move_roi`'s calling convention, so a caller
(a future panel, or a test) never has to read this module's state back out
before calling a command.

Wired through `undo.undo_manager` exactly like `RoiToolbox`'s commands -
crop/rotate/flip are computational, so unlike Selection they belong in undo
history (the old app pushed an undo point for every one of these actions
too, via `window._push_undo_point`). A caller doing a continuous gesture
(a rotation-dial drag, a live crop-box resize) is expected to wrap repeated
calls in `undo_manager.begin_batch()`/`end_batch()` itself, same as
`RoiToolbox`'s callers would for `move_roi` - no command method here
batches internally.

**Not built this pass, scope boundary**: the display-only calibration/
scale-bar/measurement-anchor fields on `GeometrySettings` (`display_units`
through `measurement_anchor2_y_px`) have no command methods yet. Porting
`gui/measurement_calibration_mixin.py`'s real logic (`_apply_measurement_
calibration`'s px<->um math) is a separate, smaller chunk left for a future
pass since nothing downstream is gated on it - no pure-math function in
`transform.py` reads those fields (see `model.py`'s docstring). A
`GeometryCosmeticChange` type isn't defined yet for the same reason - add
it alongside those commands rather than now, to avoid an unused type.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from .model import CropDefinition, GeometryComputationalChange, GeometrySettings


class GeometryModule(QObject):
    """Owns crop/rotate/flip settings for the active dataset."""

    geometry_changed = pyqtSignal(GeometryComputationalChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = GeometrySettings()

    # -- query interface ------------------------------------------------

    def settings(self) -> GeometrySettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it."""
        return replace(self._settings, crop=replace(self._settings.crop))

    # -- commands -----------------------------------------------------------

    @instrumented("GeometryModule.set_image_tools_enabled")
    def set_image_tools_enabled(self, enabled: bool) -> None:
        """Link/unlink the whole Image Tools stage - old app's "Image
        tools link" toggle (`on_image_tools_section_applied_changed` on
        `develop`). Unlinked means every stage below sees the raw,
        untransformed image (preview-only editing)."""
        enabled = bool(enabled)
        if enabled == self._settings.image_tools_enabled:
            return
        old = self._settings.image_tools_enabled

        def apply() -> None:
            self._settings.image_tools_enabled = enabled
            self.geometry_changed.emit(GeometryComputationalChange(reason="image_tools_enabled"))

        def revert() -> None:
            self._settings.image_tools_enabled = old
            self.geometry_changed.emit(GeometryComputationalChange(reason="image_tools_enabled"))

        apply()
        undo_manager.push(FunctionCommand("Image tools link", undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.set_rotation")
    def set_rotation(self, degrees: float) -> None:
        degrees = float(degrees)
        if degrees == self._settings.rotation_angle_deg:
            return
        old = self._settings.rotation_angle_deg

        def apply() -> None:
            self._settings.rotation_angle_deg = degrees
            self.geometry_changed.emit(GeometryComputationalChange(reason="rotation"))

        def revert() -> None:
            self._settings.rotation_angle_deg = old
            self.geometry_changed.emit(GeometryComputationalChange(reason="rotation"))

        apply()
        undo_manager.push(FunctionCommand("Adjust rotation", undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.set_rotation_fill_dark")
    def set_rotation_fill_dark(self, dark: bool) -> None:
        """True = new corner pixels created by rotation are filled with 0
        intensity ("not real data"); False = edge-stretch (copy the
        nearest source pixel) - see `transform.py`'s
        `apply_spatial_preprocessing` docstring for why this matters."""
        dark = bool(dark)
        if dark == self._settings.rotation_fill_dark:
            return
        old = self._settings.rotation_fill_dark

        def apply() -> None:
            self._settings.rotation_fill_dark = dark
            self.geometry_changed.emit(GeometryComputationalChange(reason="rotation_fill"))

        def revert() -> None:
            self._settings.rotation_fill_dark = old
            self.geometry_changed.emit(GeometryComputationalChange(reason="rotation_fill"))

        apply()
        label = "Rotation fill set to dark (0)" if dark else "Rotation fill set to edge-stretch"
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.set_flip")
    def set_flip(self, horizontal: bool, vertical: bool) -> None:
        horizontal, vertical = bool(horizontal), bool(vertical)
        old = (self._settings.flip_horizontal, self._settings.flip_vertical)
        new = (horizontal, vertical)
        if old == new:
            return

        def apply() -> None:
            self._settings.flip_horizontal, self._settings.flip_vertical = new
            self.geometry_changed.emit(GeometryComputationalChange(reason="flip"))

        def revert() -> None:
            self._settings.flip_horizontal, self._settings.flip_vertical = old
            self.geometry_changed.emit(GeometryComputationalChange(reason="flip"))

        apply()
        undo_manager.push(FunctionCommand("Flip", undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.set_crop")
    def set_crop(self, x: int, y: int, width: int, height: int) -> None:
        """Set and enable the crop rectangle - matching the old app's
        `crop_roi_changed`: moving/resizing the crop box always implies
        `enabled=True`. Coordinates are in post-rotate+flip space (see
        `transform.py`'s rotate -> flip -> crop order)."""
        new = CropDefinition(x=int(x), y=int(y), width=max(int(width), 1), height=max(int(height), 1), enabled=True)
        old = replace(self._settings.crop)
        if old == new:
            return

        def apply() -> None:
            self._settings.crop = replace(new)
            self.geometry_changed.emit(GeometryComputationalChange(reason="crop"))

        def revert() -> None:
            self._settings.crop = replace(old)
            self.geometry_changed.emit(GeometryComputationalChange(reason="crop"))

        apply()
        undo_manager.push(FunctionCommand("Crop", undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.clear_crop")
    def clear_crop(self) -> None:
        """Reset to no crop - old app's `reset_crop`. A no-op (no undo
        entry) if the crop was already at its default, matching every
        other no-op-skip command in this module and in `RoiToolbox`."""
        new = CropDefinition()
        old = replace(self._settings.crop)
        if old == new:
            return

        def apply() -> None:
            self._settings.crop = CropDefinition()
            self.geometry_changed.emit(GeometryComputationalChange(reason="crop"))

        def revert() -> None:
            self._settings.crop = replace(old)
            self.geometry_changed.emit(GeometryComputationalChange(reason="crop"))

        apply()
        undo_manager.push(FunctionCommand("Reset crop", undo_fn=revert, redo_fn=apply))
