"""``MaskModule`` - algorithmic + raster file mask (sketch §7 "Image Tools", §10).

Owns ``MaskSettings`` (see ``model.py``) and the ignore mask, stored as a
**timeline of `MaskChange` records** (2026-09-21 mask/ROI design
conversation - see the rewrite build log's matching entry for the full
reasoning), not one single array. Masks are authored and stored exactly as
drawn, at whatever frame (`(cube_index, wavelength_nm)`) they were edited
at - never normalized back to the reference frame, since
:meth:`~lspr_imaging_app.image_tools.chromatic.module.ChromaticModule.warp_mask_between`
can re-express any stored mask in any other frame's geometry on demand.
Emits ``mask_changed`` (computational) with the affected `frame`/`scope`.

**Real command methods built 2026-09-21**, replacing the scaffold stubs -
scoped narrower than `GeometryModule`/`BackgroundModule` after reading
`gui/mask_controller.py` (1216 lines) in full: Mask's real "apply" flow is
inherently async-worker-backed and file-I/O-heavy (`request_mask_candidate`
backgrounds two of its four tool kinds via `FunctionWorker`; load/save go
through `QFileDialog` and disk I/O), unlike Geometry/Background's plain
in-memory settings mutation. This module builds the things that *are*
plain state ownership - tool-tuning settings, and committing mask changes -
and deliberately stops there.

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

**Timeline redesign, 2026-09-21**: `set_file_mask`/`raw_mask` (one global
array) are replaced by `set_mask_change`/`resolve_mask_source` (a frame-
and-scope-tagged timeline - see `MaskChange`'s docstring in `model.py` for
the individual/persistent model). `apply_candidate`/`apply_morphology`/
`paint_brush` now take their starting canvas as an explicit `base_mask`
parameter instead of reading a single implicit mask - there's no longer
one canonical "the" mask to read, and `MaskModule` alone can't resolve the
CC-correct starting canvas for a given frame without reaching into
`ChromaticModule` (forbidden - AGENTS.md's module-boundary rule). The
caller resolves the base first (`resolve_mask_source` + `ChromaticModule.
warp_mask_between` if the source frame differs from the target), the same
"commands take already-resolved values" convention `RoiToolbox.detect_rois`
already established. Nothing in the rewrite calls any of the old
signatures yet (verified by grep before this session's mask work began, no
panel exists) - zero-migration-cost redesign, same as the `creation.py` ->
`raster_tools.py` rename.

**Still not built** (the genuinely async/file-I/O-heavy remainder, and the
UI layer the maintainer explicitly deferred - "we will need to build
additional tools... but that's UI"): `request_mask_candidate`'s worker/
cache machinery for the two image-based tools that are actually slow
(relative/local_contrast reload the raw image + run scipy filtering);
mask file load/save (`load_mask_from_file`/`save_mask_to_file`); HDF5
persistence of `MaskChange` records (`storage/session.py` doesn't exist
yet); the per-wavelength-diff mechanism from the old app's
`apply_mask_brush` (superseded by the timeline model for *persistent*
edits, but the old app's narrower "just this one wavelength, sparse diff"
idea is now just `scope="individual"` - no separate diff dict needed).
All flagged rather than guessed at.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from . import raster_tools
from .model import MaskChange, MaskComputationalChange, MaskCosmeticChange, MaskSettings

_SCOPES = ("individual", "persistent")


class MaskModule(QObject):
    """Owns the algorithmic + file-based exclusion mask, as a timeline of
    frame-tagged `MaskChange` records."""

    mask_changed = pyqtSignal(MaskComputationalChange)
    cosmetic_changed = pyqtSignal(MaskCosmeticChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = MaskSettings()
        self._individual_changes: dict[tuple[int, float], MaskChange] = {}
        self._persistent_changes: dict[int, MaskChange] = {}

    # -- query interface ------------------------------------------------

    def settings(self) -> MaskSettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it (same guarantee `GeometryModule.settings()` makes). Does
        NOT include `histogram_mask`/`figure_mask` (dataclass fields of
        type `np.ndarray | None` - `dataclasses.replace()` copies the
        reference, not the array; unused/dead fields regardless, see the
        module docstring's finding 1)."""
        return replace(self._settings)

    def resolve_mask_source(self, frame: tuple[int, float]) -> tuple[tuple[int, float], np.ndarray] | None:
        """The raw stored mask that applies at `frame`, and the frame it
        was authored at - `None` if nothing applies yet (no persistent
        change has ever been set at or before `frame`'s cube, and no
        individual override exists at `frame` exactly).

        Returns the mask **as authored**, in its own frame's geometry, not
        warped into `frame`'s geometry - if the returned frame differs
        from the queried `frame`, the caller must warp it themselves
        (`ChromaticModule.warp_mask_between(mask, returned_frame, frame)`)
        before using it. This module holds no `ChromaticModule` reference
        and never calls into it (AGENTS.md module-boundary rule) - the
        same one-directional pattern `roi/rasterize.py` already uses,
        taking `affine_matrix` as a plain parameter rather than reaching
        into Chromatic itself.

        An individual change at exactly `frame` wins over persistent
        (matches the maintainer's "analysis will always look at latest
        persistent change (or individual change for given frame)")."""
        if frame in self._individual_changes:
            change = self._individual_changes[frame]
            return change.frame, change.mask
        candidate_cubes = [cube for cube in self._persistent_changes if cube <= frame[0]]
        if not candidate_cubes:
            return None
        change = self._persistent_changes[max(candidate_cubes)]
        return change.frame, change.mask

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
        downstream reads these until an "apply" command merges a computed
        candidate into a mask change."""
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

    @instrumented("MaskModule.set_mask_change")
    def set_mask_change(self, frame: tuple[int, float], scope: str, mask: np.ndarray | None) -> None:
        """Write - or, with `mask=None`, remove - the mask change at
        `frame` with the given `scope` (`"individual"` or `"persistent"`,
        see `MaskChange`'s docstring in `model.py`). Removing a persistent
        change means resolution simply falls through to whatever was
        previously in effect at that cube - no reshuffling of other
        entries, since each is keyed by its own starting cube, not a
        position in a sequence.

        No-op-skipped like every other setter in this codebase (an
        identical mask already stored at this exact `frame`/`scope`
        doesn't re-emit). Not undo-tracked, matching every other Mask
        command (see module docstring)."""
        if scope not in _SCOPES:
            raise ValueError(f"scope must be one of {_SCOPES}, got {scope!r}")
        frame = (int(frame[0]), float(frame[1]))
        store: dict = self._individual_changes if scope == "individual" else self._persistent_changes
        key: object = frame if scope == "individual" else frame[0]

        normalized = None if mask is None else np.asarray(mask, dtype=bool)
        existing = store.get(key)
        same = (existing is None and normalized is None) or (
            existing is not None
            and normalized is not None
            and existing.mask.shape == normalized.shape
            and np.array_equal(existing.mask, normalized)
        )
        if same:
            return

        if normalized is None:
            store.pop(key, None)
        else:
            store[key] = MaskChange(frame=frame, scope=scope, mask=normalized.copy())
        self.mask_changed.emit(MaskComputationalChange(reason="mask_change", frame=frame, scope=scope))

    @instrumented("MaskModule.apply_candidate")
    def apply_candidate(
        self,
        base_mask: np.ndarray,
        candidate: np.ndarray,
        *,
        target_frame: tuple[int, float],
        scope: str,
        subtract: bool = False,
    ) -> None:
        """Merge an already-computed candidate mask onto `base_mask` and
        commit the result as a new mask change at `target_frame`/`scope` -
        `subtract=False` (the old app's "Apply") ORs it in, `subtract=True`
        ("Reset"/really "Subtract") ANDs it out (`raster_tools.merge_mask_
        candidate`).

        `base_mask` is the caller's job to resolve - typically
        `resolve_mask_source(target_frame)`'s result, warped into
        `target_frame`'s geometry via `ChromaticModule.warp_mask_between`
        if it came from a different frame (see module docstring: this
        module can't do that warp itself). Painting/merging onto whatever
        is *already in effect* at the target frame - not a blank canvas -
        is what makes editing a fresh cube build on the last persistent
        change instead of discarding it.

        `candidate` itself needs a raw image this module doesn't own for
        the histogram/relative/local-contrast tools
        (`raster_tools.create_histogram_mask`/`create_relative_contrast_
        mask`/`create_local_contrast_mask` against a `Dataset`-supplied
        image and this module's own `settings()`) - matching every other
        "commands take already-resolved values" convention in this
        codebase (`RoiToolbox.detect_rois`'s calling convention).
        `apply_morphology` below is the one exception with its own
        convenience wrapper, since its candidate needs nothing but
        `base_mask` itself."""
        base_mask = np.asarray(base_mask, dtype=bool)
        candidate = np.asarray(candidate, dtype=bool)
        if base_mask.shape != candidate.shape:
            raise ValueError(f"candidate shape {candidate.shape} does not match base_mask shape {base_mask.shape}")
        merged = raster_tools.merge_mask_candidate(base_mask, candidate, subtract=subtract)
        self.set_mask_change(target_frame, scope, merged)

    @instrumented("MaskModule.apply_morphology")
    def apply_morphology(
        self,
        base_mask: np.ndarray,
        operation: str,
        radius_px: int,
        *,
        target_frame: tuple[int, float],
        scope: str,
        subtract: bool = False,
    ) -> None:
        """Run a morphological operation (`"erode"`/`"dilate"`/`"open"`/
        `"close"`) against `base_mask` to get a candidate, then merge it in
        exactly like `apply_candidate` - see this module's own docstring
        for why morphology is a candidate-then-merge operation here, not a
        direct replacement."""
        candidate = raster_tools.apply_morphology_to_mask(base_mask, operation, radius_px)
        self.apply_candidate(base_mask, candidate, target_frame=target_frame, scope=scope, subtract=subtract)

    @instrumented("MaskModule.paint_brush")
    def paint_brush(
        self,
        base_mask: np.ndarray,
        center_xy: tuple[float, float],
        radius_px: float,
        *,
        target_frame: tuple[int, float],
        scope: str,
        value: bool,
    ) -> None:
        """Paint one circular brush stroke onto `base_mask`
        (`raster_tools.apply_brush_stamp`) and commit the result as a new
        mask change at `target_frame`/`scope` - `value=True` to add to the
        mask, `False` to erase from it.

        The old app's distinct "on-reference direct write" vs. "off-
        reference sparse diff" branches (`apply_mask_brush`) are both
        subsumed by the timeline model now: painting with `scope=
        "persistent"` is the direct-write case (a new full mask, effective
        from this cube onward); painting with `scope="individual"` is the
        old sparse-diff case's replacement (a touch-up for this exact
        frame only) - no separate diff-dict mechanism needed, see the
        module docstring."""
        painted = raster_tools.apply_brush_stamp(base_mask, center_xy, radius_px, value=value)
        self.set_mask_change(target_frame, scope, painted)
