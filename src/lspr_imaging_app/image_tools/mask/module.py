"""``MaskModule`` - algorithmic + raster file mask (sketch §7 "Image Tools", §10).

Owns ``MaskSettings`` (see ``model.py``) and the raster file-mask. Emits
``mask_changed`` (computational). Masks are authored and stored in raw pixel
space (AGENTS.md non-negotiable invariant) - this module calls
:meth:`~lspr_imaging_app.image_tools.chromatic.module.ChromaticModule.warp_mask`
to forward-transform into processed/wavelength space at each use site; it
never does the reverse.

**Real command methods built 2026-09-21**, replacing the scaffold stubs -
scoped narrower than `GeometryModule`/`BackgroundModule` after reading
`gui/mask_controller.py` (1216 lines) in full: Mask's real "apply" flow is
inherently async-worker-backed and file-I/O-heavy (`request_mask_candidate`
backgrounds two of its four tool kinds via `FunctionWorker`; load/save go
through `QFileDialog` and disk I/O), unlike Geometry/Background's plain
in-memory settings mutation. This pass builds the two things that *are*
plain state ownership - tool-tuning settings, and the committed raster
mask - and deliberately stops there.

**Two real findings from actually reading the old app, not assumed from
the sketch** (matching this build log's established working method -
"read the real file first, check its actual dependencies"):

1. **`window._state.mask` (`domain.models.MaskSettings`, `develop`) is
   essentially unused for its own tunable fields.** Grepped every write to
   it: the *only* writes are `clear_preview_overlays()` resetting
   `histogram_enabled`/`histogram_mask`/`figure_enabled`/`figure_mask` to
   their defaults - the "New mask system state" fields `model.py` already
   flags. Every actual tool-tuning value (`relative_threshold_fraction`,
   `*_sigma_px`, `*_z_threshold`, `morphology_radius_px`) is read straight
   from its Qt spinbox each time (`mask_settings_from_controls()`), never
   persisted through this dataclass at all - so this rewrite's
   `MaskModule` is this settings group's *first real owner*, not a port of
   an existing setter (there isn't one to port). `set_tool_settings()`
   below is new design on this specific point, following the same
   combined-Apply shape `BackgroundModule.set_flatten_background_settings`
   already established, since these are conceptually the same kind of
   settings-panel-with-tunables. The `histogram_enabled`/`histogram_mask`/
   `figure_enabled`/`figure_mask` fields stay **unbuilt and unexplained**
   here too - carried over as-is, no command methods added for them; they
   were dead in the old app and nothing in this rewrite gives them meaning
   yet either.

2. **`create_histogram_mask()` (`raster_tools.py`, named `creation.py`
   until the 2026-09-21 mask/ROI design conversation renamed it) is dead
   code in the old app - never called.** The "histogram" mask tool's real
   candidate comes from `current_histogram_highlight_mask_raw()` in
   `mask_controller.py` instead: a completely different algorithm (selects
   pixels by *displayed* value range, then maps the selection through
   processed<->raw coordinate maps) that was never ported into
   `raster_tools.py` as a pure function. `histogram_min_value`/
   `histogram_max_value` (the fields `set_tool_settings()` stores, that a
   future `create_histogram_mask()` call would read) are included
   regardless, as real dataclass fields regardless of current dead-code
   status - but porting the real coordinate-map algorithm is future work,
   not assumed done here.

**Not wired through `undo_manager`** - confirmed by grep: `mask_controller.py`
never calls `_push_undo_point` anywhere, for any mask action (load, save,
create, apply-delta, brush paint). Every command below matches that
exactly, the same rigor `SelectionModule`/parts of `GeometryModule` already
applied to actions the old app itself never undo-tracked.

**"Apply" command methods built 2026-09-21**, closing most of the gap the
previous addendum flagged: `apply_candidate()`/`apply_morphology()`/
`paint_brush()`, calling into `raster_tools.py`'s now-built pure functions.
Ported the real old-app semantics after actually reading the button
wiring, not guessed: `gui/main_window.py`'s morphology buttons are
literally tooltipped "Add the current morphology preview to the current
mask" / "Subtract the current morphology preview from the current mask" -
morphology, like the threshold/contrast tools, produces a *candidate*
(here: the current mask run through erode/dilate/open/close) that the user
then merges in additively or subtractively, **not** a direct replacement
of the mask with the morphed result. So all four old-app tools
(histogram/relative/local_contrast/morphology) share the exact same
merge step (`apply_candidate`) - they only differ in how the candidate
itself gets computed, which is why `apply_candidate` takes an
already-computed candidate rather than a tool-kind string: the
image-based tools (histogram/relative/local_contrast) need a raw image
this module doesn't own (`Dataset`'s job), so their candidate has to be
computed by the caller anyway (`raster_tools.create_relative_contrast_
mask(image, ...)` etc., using this module's own `settings()`) - only
`apply_morphology` gets a convenience wrapper, since morphology's
candidate needs nothing but this module's own current mask.

**Still not built** (the genuinely async/file-I/O-heavy remainder):
`request_mask_candidate`'s worker/cache machinery for the two image-based
tools that are actually slow (relative/local_contrast reload the raw
image + run scipy filtering - histogram/morphology are cheap and
synchronous even in the old app); mask file load/save
(`load_mask_from_file`/`save_mask_to_file`); per-wavelength mask diffs
(`_current_file_mask_wavelength_diffs` - the off-reference,
chromatic-correction-enabled branch of `apply_mask_brush`, where a stroke
accumulates into a sparse diff instead of touching the canonical mask;
`paint_brush()` below only implements the on-reference/CC-disabled direct-
write branch, deliberately - see its own docstring for why the other
branch needs a design decision this module can't make alone). All flagged
rather than guessed at.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from . import raster_tools
from .model import MaskComputationalChange, MaskCosmeticChange, MaskSettings


class MaskModule(QObject):
    """Owns the algorithmic + file-based exclusion mask, in raw pixel space."""

    mask_changed = pyqtSignal(MaskComputationalChange)
    cosmetic_changed = pyqtSignal(MaskCosmeticChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = MaskSettings()
        self._file_mask: np.ndarray | None = None

    # -- query interface ------------------------------------------------

    def settings(self) -> MaskSettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it (same guarantee `GeometryModule.settings()` makes). Does
        NOT include `file_mask`/`histogram_mask`/`figure_mask` (dataclass
        fields of type `np.ndarray | None` - `dataclasses.replace()` copies
        the reference, not the array; `raw_mask()` is the real, defensively
        `.copy()`-ing accessor for the committed mask)."""
        return replace(self._settings)

    def raw_mask(self) -> np.ndarray | None:
        """The current committed raster mask, in raw pixel space, or
        `None` if none is set - a defensive copy, matching `settings()`'s
        guarantee."""
        return None if self._file_mask is None else self._file_mask.copy()

    # -- commands -----------------------------------------------------------

    @instrumented("MaskModule.set_tool_settings")
    def set_tool_settings(
        self,
        *,
        histogram_min_value: float | None,
        histogram_max_value: float | None,
        relative_threshold_fraction: float,
        relative_profile_sigma_px: float,
        local_contrast_sigma_px: float,
        local_contrast_z_threshold: float,
        morphology_radius_px: int,
        brush_size_px: int,
    ) -> None:
        """Set every mask-tool tuning parameter at once - same
        combined-Apply shape as `BackgroundModule.set_flatten_background_
        settings` (see module docstring: this rewrite is these settings'
        first real owner, there's no old-app single-field setter to
        match). Cosmetic (see `MaskCosmeticChange`'s docstring): nothing
        downstream reads these until a not-yet-built "apply" command
        merges a computed candidate into `file_mask`."""
        new_tunables = (
            None if histogram_min_value is None else float(histogram_min_value),
            None if histogram_max_value is None else float(histogram_max_value),
            max(float(relative_threshold_fraction), 0.0),
            max(float(relative_profile_sigma_px), 1.0),
            max(float(local_contrast_sigma_px), 1.0),
            max(float(local_contrast_z_threshold), 0.1),
            max(int(morphology_radius_px), 1),
            max(int(brush_size_px), 1),
        )
        old_tunables = (
            self._settings.histogram_min_value,
            self._settings.histogram_max_value,
            self._settings.relative_threshold_fraction,
            self._settings.relative_profile_sigma_px,
            self._settings.local_contrast_sigma_px,
            self._settings.local_contrast_z_threshold,
            self._settings.morphology_radius_px,
            self._settings.brush_size_px,
        )
        # Compared as plain tuples of scalars, never via a dataclass `==`
        # on the whole MaskSettings - that would also compare the two
        # np.ndarray | None fields below, and `ndarray == ndarray` returns
        # an array, not a bool, which breaks a plain `if` no-op check.
        if new_tunables == old_tunables:
            return
        (
            self._settings.histogram_min_value,
            self._settings.histogram_max_value,
            self._settings.relative_threshold_fraction,
            self._settings.relative_profile_sigma_px,
            self._settings.local_contrast_sigma_px,
            self._settings.local_contrast_z_threshold,
            self._settings.morphology_radius_px,
            self._settings.brush_size_px,
        ) = new_tunables
        self.cosmetic_changed.emit(MaskCosmeticChange(reason="tool_settings"))

    @instrumented("MaskModule.set_histogram_highlight_range")
    def set_histogram_highlight_range(self, min_value: float | None, max_value: float | None) -> None:
        """Move the histogram widget's draggable selection region - matches
        the old app's live drag state (`hist_region.getRegion()`, read
        directly by `current_histogram_highlight_mask_raw()` each time, see
        module docstring). Cosmetic, same reasoning as
        `GeometryModule.set_measurement_anchors`."""
        new_min = None if min_value is None else float(min_value)
        new_max = None if max_value is None else float(max_value)
        if new_min == self._settings.histogram_highlight_min_value and new_max == self._settings.histogram_highlight_max_value:
            return
        self._settings.histogram_highlight_min_value = new_min
        self._settings.histogram_highlight_max_value = new_max
        self.cosmetic_changed.emit(MaskCosmeticChange(reason="histogram_highlight"))

    @instrumented("MaskModule.set_file_mask")
    def set_file_mask(self, mask: np.ndarray | None) -> None:
        """Replace the committed raster mask - the raw-pixel-space boolean
        array `ignored_pixel_mask`/`flatten_background` actually read
        (`window._current_file_mask` in the old app; there is no shape
        validation here, since this module doesn't own image dimensions -
        the caller is responsible for a `mask` shaped to match the current
        raw image, same convention as every other module that takes an
        already-resolved value). `None` means "no file mask configured".
        Computational (see `MaskComputationalChange`'s docstring) - this is
        the one Mask concept that actually feeds a real computation."""
        normalized = None if mask is None else np.asarray(mask, dtype=bool)
        same = (self._file_mask is None and normalized is None) or (
            self._file_mask is not None
            and normalized is not None
            and self._file_mask.shape == normalized.shape
            and np.array_equal(self._file_mask, normalized)
        )
        if same:
            return
        self._file_mask = None if normalized is None else normalized.copy()
        self.mask_changed.emit(MaskComputationalChange(reason="file_mask"))

    @instrumented("MaskModule.apply_candidate")
    def apply_candidate(self, candidate: np.ndarray, *, subtract: bool = False) -> None:
        """Merge an already-computed candidate mask into `file_mask` -
        `subtract=False` (the old app's "Apply") ORs it in, `subtract=True`
        ("Reset"/really "Subtract") ANDs it out (`raster_tools.merge_mask_
        candidate`). The caller computes `candidate` itself - a raw image
        this module doesn't own is needed for the histogram/relative/
        local-contrast tools (`raster_tools.create_histogram_mask`/
        `create_relative_contrast_mask`/`create_local_contrast_mask`
        against a `Dataset`-supplied image and this module's own
        `settings()`) - matching every other "commands take already-
        resolved values" convention in this codebase
        (`RoiToolbox.detect_rois`'s calling convention). `apply_morphology`
        below is the one exception with its own convenience wrapper, since
        its candidate needs nothing but this module's own current mask.

        A missing `file_mask` is treated as all-unmasked (matching the old
        app's `_finish_apply_mask_delta`, which does the same when no
        current mask canvas exists yet)."""
        candidate = np.asarray(candidate, dtype=bool)
        base = self._file_mask if self._file_mask is not None else np.zeros(candidate.shape, dtype=bool)
        if base.shape != candidate.shape:
            raise ValueError(f"candidate shape {candidate.shape} does not match file_mask shape {base.shape}")
        merged = raster_tools.merge_mask_candidate(base, candidate, subtract=subtract)
        self.set_file_mask(merged)

    @instrumented("MaskModule.apply_morphology")
    def apply_morphology(self, operation: str, radius_px: int, *, subtract: bool = False) -> None:
        """Run a morphological operation (`"erode"`/`"dilate"`/`"open"`/
        `"close"`) against the *current* `file_mask` to get a candidate,
        then merge it in exactly like `apply_candidate` - see this
        module's own docstring for why morphology is a candidate-then-
        merge operation here, not a direct replacement. A no-op if there's
        no current mask to run morphology against."""
        if self._file_mask is None:
            return
        candidate = raster_tools.apply_morphology_to_mask(self._file_mask, operation, radius_px)
        self.apply_candidate(candidate, subtract=subtract)

    @instrumented("MaskModule.paint_brush")
    def paint_brush(self, center_xy: tuple[float, float], radius_px: float, *, value: bool) -> None:
        """Paint one circular brush stroke directly into `file_mask`, in
        raw pixel space (`raster_tools.apply_brush_stamp`) -
        `value=True` to add to the mask, `False` to erase from it. This is
        the old app's on-reference-image (or chromatic-correction-disabled)
        branch of `apply_mask_brush` - the *only* branch built here.

        The other branch - off-reference with chromatic correction on,
        where a stroke accumulates into a sparse per-wavelength diff
        instead of touching this canonical mask at all (see
        `raster_tools.brush_stamp_bounds`'s docstring) - isn't built:
        it needs this module to know the current chromatic-correction
        state and whether the displayed wavelength is the reference,
        cross-module facts it doesn't own and hasn't been designed to
        receive yet (as a parameter from the caller, most likely - not
        decided). Requires an existing `file_mask`; unlike the old app's
        `manual_mask_required(create_if_missing=True)`, this module can't
        default one into existence on the caller's behalf, since it has no
        image-shape knowledge of its own - the caller creates one first
        (`set_file_mask(np.zeros(raw_shape, dtype=bool))`)."""
        if self._file_mask is None:
            raise ValueError("paint_brush requires an existing file_mask - call set_file_mask first.")
        painted = raster_tools.apply_brush_stamp(self._file_mask, center_xy, radius_px, value=value)
        self.set_file_mask(painted)
