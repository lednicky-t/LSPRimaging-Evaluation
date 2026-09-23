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

**Calibration/scale-bar commands added 2026-09-21**, ported from
`gui/measurement_calibration_mixin.py` and the relevant bits of
`gui/main_window.py`/`gui/overlay_manager.py` on `develop` (again, state
mutation only - the ruler overlay drawing, scale-bar rendering, and
spinbox/status-label wiring stay in the not-yet-built panel layer). These
fields are cosmetic (`GeometryCosmeticChange`, never analysis-invalidating
- confirmed by `transform.py` never reading them), but **not always
undo-tracked**: matching the old app exactly, only `apply_measurement_
calibration` pushed an undo point there (`_push_undo_point("Measurement
calibration")`); dragging the ruler anchors (`_on_measurement_marker_
moved`), toggling display units (`_toggle_display_units`), and toggling
the scale bar (`_on_scale_bar_toggled`) never did. So `set_measurement_
anchors`/`set_display_units`/`set_scale_bar_visible` are deliberately
**not** wired through `undo_manager` here, while `apply_measurement_
calibration` is - "cosmetic vs. computational" (recompute-triggering) and
"undo-tracked vs. not" are independent axes, not the same distinction
(`RoiToolbox.rename_group`/`recolor_group` are the existing counter-example
in the other direction: cosmetic *and* undo-tracked).

`can_display_micrometers()`/`microns_per_pixel_scalar()` are added to the
query interface as pure derived reads (ported from `_can_display_
micrometers`/`_microns_per_pixel_scalar`, which only ever read settings
fields) - the px<->um label-formatting helpers themselves (`develop`'s
`gui/ui_helpers.py`: `length_px_to_display` et al.) are trivial one-liners
left for whichever panel needs them, not duplicated here.

**Still not built**: `_normalize_display_units`'s defensive "silently
fall back to px if calibration was lost" repair isn't ported - there is no
command here that can *revoke* calibration once applied (matching the old
app: no "uncalibrate" action exists), so the invariant it protects
(`display_units == "um"` implies calibrated) can't currently be broken
through this module's own command surface. Revisit if `storage/session.py`
(not started) ever needs to load a session file with a corrupted/stale
combination - flagged here rather than guessed at.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from .model import CropDefinition, GeometryComputationalChange, GeometryCosmeticChange, GeometrySettings


class GeometryModule(QObject):
    """Owns crop/rotate/flip settings for the active dataset."""

    geometry_changed = pyqtSignal(GeometryComputationalChange)
    cosmetic_changed = pyqtSignal(GeometryCosmeticChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = GeometrySettings()

    # -- query interface ------------------------------------------------

    def settings(self) -> GeometrySettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it."""
        return replace(self._settings, crop=replace(self._settings.crop))

    # -- session restore ------------------------------------------------

    def restore_settings(self, settings: GeometrySettings) -> None:
        """Replace this module's whole state, as a *session load* rather
        than a user edit (added 2026-09-23 for `storage/session.py`).

        Deliberately **not** undo-tracked, unlike every command method
        here. Loading a session is not an edit to undo *past* - Ctrl+Z
        immediately after opening a dataset should do nothing, not
        rewind the file that was just opened. The caller clears the undo
        stack after restoring every module, so what the user does next is
        the first undoable thing.

        Still emits both change signals: panels have to redraw against the
        restored state, and a restore is exactly as much a reason to redraw
        as an edit is."""
        self._settings = replace(settings, crop=replace(settings.crop))
        self.geometry_changed.emit(GeometryComputationalChange(reason="session_restored"))
        self.cosmetic_changed.emit(GeometryCosmeticChange(reason="session_restored"))

    def can_display_micrometers(self) -> bool:
        """True once a real (both-axes-positive) calibration has been
        applied - ported from `_can_display_micrometers` (`develop`)."""
        settings = self._settings
        return bool(settings.calibration_enabled) and settings.microns_per_pixel_x > 0.0 and settings.microns_per_pixel_y > 0.0

    def microns_per_pixel_scalar(self) -> float:
        """The effective isotropic µm/px scale - the average of the x/y
        calibration factors, each floored at a tiny epsilon to avoid a
        division-by-zero in any caller that divides by this (ported from
        `_microns_per_pixel_scalar`, `develop`)."""
        settings = self._settings
        scale_x = max(float(settings.microns_per_pixel_x), 1e-9)
        scale_y = max(float(settings.microns_per_pixel_y), 1e-9)
        return 0.5 * (scale_x + scale_y)

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

    # -- commands: calibration/scale-bar (cosmetic) --------------------------

    @instrumented("GeometryModule.set_measurement_anchors")
    def set_measurement_anchors(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Move the two ruler markers `apply_measurement_calibration` reads
        from - matching the old app's live drag update
        (`_on_measurement_marker_moved`, `develop`), which never pushed an
        undo point (see module docstring): repositioning the ruler is a
        measuring aid, not itself a result-affecting action."""
        new = (float(x1), float(y1), float(x2), float(y2))
        old = (
            self._settings.measurement_anchor1_x_px,
            self._settings.measurement_anchor1_y_px,
            self._settings.measurement_anchor2_x_px,
            self._settings.measurement_anchor2_y_px,
        )
        if old == new:
            return
        (
            self._settings.measurement_anchor1_x_px,
            self._settings.measurement_anchor1_y_px,
            self._settings.measurement_anchor2_x_px,
            self._settings.measurement_anchor2_y_px,
        ) = new
        self.cosmetic_changed.emit(GeometryCosmeticChange(reason="measurement_anchors"))

    @instrumented("GeometryModule.apply_measurement_calibration")
    def apply_measurement_calibration(self, dx_um: float, dy_um: float) -> None:
        """Convert the current ruler measurement (the two measurement
        anchors) into a px<->um calibration - ported from
        `_apply_measurement_calibration` (`develop`), including its exact
        validation and asymmetric-axis fallback (only dx given -> y follows
        x, and vice versa). Raises `ValueError` for the same three
        preconditions the old app refused via a status-bar message instead
        - this module has no status bar, so the panel layer is responsible
        for catching this and showing it to the user, the same convention
        `SelectionModule.set_cube`'s negative-index guard already uses."""
        dx_um, dy_um = float(dx_um), float(dy_um)
        if dx_um <= 0.0 and dy_um <= 0.0:
            raise ValueError("Enter a real dx and/or dy in um before applying calibration.")
        dx_px = self._settings.measurement_anchor2_x_px - self._settings.measurement_anchor1_x_px
        dy_px = self._settings.measurement_anchor2_y_px - self._settings.measurement_anchor1_y_px
        if dx_um > 0.0 and abs(dx_px) < 1e-6:
            raise ValueError("dx between the ruler guides is zero, so dx calibration cannot be applied.")
        if dy_um > 0.0 and abs(dy_px) < 1e-6:
            raise ValueError("dy between the ruler guides is zero, so dy calibration cannot be applied.")

        old = (
            self._settings.microns_per_pixel_x,
            self._settings.microns_per_pixel_y,
            self._settings.calibration_enabled,
            self._settings.display_units,
        )
        new_x, new_y = self._settings.microns_per_pixel_x, self._settings.microns_per_pixel_y
        if dx_um > 0.0:
            new_x = abs(dx_um / dx_px)
        if dy_um > 0.0:
            new_y = abs(dy_um / dy_px)
        if dx_um > 0.0 and dy_um <= 0.0:
            new_y = new_x
        if dy_um > 0.0 and dx_um <= 0.0:
            new_x = new_y
        new = (new_x, new_y, True, "um")

        def apply() -> None:
            (
                self._settings.microns_per_pixel_x,
                self._settings.microns_per_pixel_y,
                self._settings.calibration_enabled,
                self._settings.display_units,
            ) = new
            self.cosmetic_changed.emit(GeometryCosmeticChange(reason="calibration"))

        def revert() -> None:
            (
                self._settings.microns_per_pixel_x,
                self._settings.microns_per_pixel_y,
                self._settings.calibration_enabled,
                self._settings.display_units,
            ) = old
            self.cosmetic_changed.emit(GeometryCosmeticChange(reason="calibration"))

        apply()
        undo_manager.push(FunctionCommand("Measurement calibration", undo_fn=revert, redo_fn=apply))

    @instrumented("GeometryModule.set_display_units")
    def set_display_units(self, units: str) -> None:
        """Toggle px<->um display - old app's `_toggle_display_units`. Not
        undo-tracked (see module docstring). Raises `ValueError` for "um"
        before a real calibration exists, mirroring the old app's
        status-bar refusal ("Calibrate the ruler first...")."""
        units = str(units)
        if units not in ("px", "um"):
            raise ValueError(f"units must be 'px' or 'um', got {units!r}")
        if units == "um" and not self.can_display_micrometers():
            raise ValueError("Calibrate the ruler first before switching to micrometers.")
        if units == self._settings.display_units:
            return
        self._settings.display_units = units
        self.cosmetic_changed.emit(GeometryCosmeticChange(reason="display_units"))

    @instrumented("GeometryModule.set_scale_bar_visible")
    def set_scale_bar_visible(self, visible: bool) -> None:
        """Not undo-tracked (see module docstring) - old app's
        `_on_scale_bar_toggled`."""
        visible = bool(visible)
        if visible == self._settings.scale_bar_visible:
            return
        self._settings.scale_bar_visible = visible
        self.cosmetic_changed.emit(GeometryCosmeticChange(reason="scale_bar_visible"))
