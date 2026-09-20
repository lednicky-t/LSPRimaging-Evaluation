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

**Not built this pass** (the async/file-I/O-heavy remainder): computing an
actual mask *candidate* from these settings and merging it into
`file_mask` (`apply_histogram_mask`/`apply_relative_mask`/
`apply_local_contrast_mask`/`apply_morphology_mask` and their `reset_*`
counterparts, all backed by `request_mask_candidate`'s worker/cache
machinery); brush painting (`apply_mask_brush`); mask file load/save
(`load_mask_from_file`/`save_mask_to_file`); per-wavelength mask diffs
(`_current_file_mask_wavelength_diffs`, needed once off-reference painting
under chromatic correction matters). All flagged rather than guessed at -
a distinctly bigger chunk than Geometry's calibration deferral was.

**2026-09-21 addendum**: `raster_tools.py`'s pure functions (threshold/
contrast candidate generation, morphology, brush footprint, candidate
merge) are now built and shared-ready - see that file's module docstring
for the mask/ROI design conversation this came out of, and
`roi/rasterize.py`'s docstring for the matching not-yet-built ROI-mask
chromatic-warp task. The command methods that would actually *call*
`raster_tools.py` (the "apply" flow listed above) still aren't built -
this addendum only changes what those commands, once written, will call
into, not the command surface itself.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
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
