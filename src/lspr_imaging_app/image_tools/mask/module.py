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

**Mostly not wired through `undo_manager`** - confirmed by grep: `mask_controller.py`
never calls `_push_undo_point` anywhere, for any mask action (load, save,
create, apply-delta, brush paint). Every command below matches that
exactly, the same rigor `SelectionModule`/parts of `GeometryModule` already
applied to actions the old app itself never undo-tracked.

**The one exception (2026-10-16, maintainer OK): the Mask rows' commands**
(`delete_persistent_mask`, `delete_individual_masks`, `make_mask_individual`,
`make_mask_persistent`, the right-click menu of the mark rows under the sliders) *are* undoable,
because deleting a hand-drawn mask by a click on a tiny mark must be recoverable. Painting,
loading and clearing still are not, so an undo entry could meet a timeline that changed
after it. Each entry therefore carries a **version token**: every write that is not an
undoable row command takes a fresh token (`_touch`), a row command remembers the token
before and after it, and its undo / redo is applied only while the timeline still has the
token that command left (undo) or found (redo). Otherwise nothing is touched and
`undo_skipped` says why - a newer stroke is never overwritten by an old undo.

**The Auto mask batch (2026-10-17, maintainer OK)**: `set_mask_changes` writes many masks at once - the masks the Auto mask step makes
from the scan's particle pointers, one individual mask per frame - as ONE undoable step with ONE `mask_changed` emission
(`reason="mask_batch"`, the frames in `frames`). It uses the same version-token guard as the row commands, so its undo is skipped, with a
message, if the timeline changed after it. Masks are stored compressed (`MaskChange`), so the undo entry of a batch of hundreds is a few KB.

**Timeline redesign, 2026-09-21**: `set_file_mask`/`raw_mask` (one global
array) are replaced by `set_mask_change`/`resolve_mask_source` (a frame-
and-scope-tagged timeline - see `MaskChange`'s docstring in `model.py` for
the individual/persistent model). `apply_candidate`/`apply_morphology`/
`paint_brush` now take their starting canvas as an explicit `base_mask`
parameter instead of reading a single implicit mask - there's no longer
one canonical "the" mask to read, and `MaskModule` alone can't resolve the
CC-correct starting canvas for a given frame without reaching into
`ChromaticModule` (forbidden - CLAUDE.md's module-boundary rule). The
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

import itertools
import logging
from collections.abc import Iterable
from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from . import raster_tools
from .model import MaskChange, MaskComputationalChange, MaskCosmeticChange, MaskSettings

_SCOPES = ("individual", "persistent")

logger = logging.getLogger(__name__)


class MaskModule(QObject):
    """Owns the algorithmic + file-based exclusion mask, as a timeline of
    frame-tagged `MaskChange` records."""

    mask_changed = pyqtSignal(MaskComputationalChange)
    cosmetic_changed = pyqtSignal(MaskCosmeticChange)
    undo_skipped = pyqtSignal(str)  # an undo / redo of a row command was not applied (see the module docstring); the reason

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = MaskSettings()
        self._individual_changes: dict[tuple[int, float], MaskChange] = {}
        self._persistent_changes: dict[int, MaskChange] = {}
        self._tokens = itertools.count(1)
        self._version = 0  # which state the timeline is in; see `_touch` and the module docstring
        self._individual_index: dict[int, tuple[float, ...]] | None = None  # individual wavelengths by cube, built on demand

    def _touch(self) -> None:
        """The timeline was changed by something that is not an undoable row command: a fresh version token (so older
        undo entries of row commands know the timeline moved on), and the per-cube index of individual masks is stale."""
        self._version = next(self._tokens)
        self._individual_index = None

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

    def resolve_mask_source(self, frame: tuple[int, float]) -> tuple[tuple[int, float], np.ndarray, str] | None:
        """The raw stored mask that applies at `frame`, the frame it was
        authored at, and which timeline it came from (`"persistent"` or
        `"individual"`) - `None` if nothing applies yet (no persistent
        change has ever been set at or before `frame`'s cube, and no
        individual override exists at `frame` exactly).

        The scope is returned rather than left for the caller to
        re-derive (added 2026-09-23, while wiring `AnalysisEngine`): the
        analysis store records *which* timeline a mask came from in its
        provenance filename, and the only other way to learn that would be
        for the caller to inspect this module's private
        `_individual_changes`/`_persistent_changes` dicts - exactly the
        boundary violation the query interface exists to prevent.

        Returns the mask **as authored**, in its own frame's geometry, not
        warped into `frame`'s geometry - if the returned frame differs
        from the queried `frame`, the caller must warp it themselves
        (`ChromaticModule.warp_mask_between(mask, returned_frame, frame)`)
        before using it. This module holds no `ChromaticModule` reference
        and never calls into it (CLAUDE.md module-boundary rule) - the
        same one-directional pattern `roi/rasterize.py` already uses,
        taking `affine_matrix` as a plain parameter rather than reaching
        into Chromatic itself.

        An individual change at exactly `frame` wins over persistent
        (matches the maintainer's "analysis will always look at latest
        persistent change (or individual change for given frame)")."""
        change = self.resolve_mask_change(frame)
        if change is None:
            return None
        return change.frame, change.mask, change.scope

    def resolve_mask_change(self, frame: tuple[int, float]) -> MaskChange | None:
        """The stored `MaskChange` that applies at `frame` (the rule of `resolve_mask_source`, which decodes it), still compressed:
        a caller that makes many masks (Auto mask) takes the lookup here on the GUI thread, a few microseconds a frame, and decodes
        the pixels later on its own thread. `MaskChange` is immutable, so it can be handed to another thread as it is."""
        if frame in self._individual_changes:
            return self._individual_changes[frame]
        candidate_cubes = [cube for cube in self._persistent_changes if cube <= frame[0]]
        if not candidate_cubes:
            return None
        return self._persistent_changes[max(candidate_cubes)]

    def mask_changes(self) -> tuple[MaskChange, ...]:
        """The whole timeline, persistent changes first then individual
        ones, each group ordered by frame - for session persistence
        (added 2026-09-23).

        Deterministic ordering so that saving an unchanged session twice
        produces an identical file, which is what lets an autosave's
        "did anything change" check be a plain comparison. `MaskChange` is
        frozen and its `mask` was already copied on the way in
        (`set_mask_change` stores `normalized.copy()`), so handing the
        records out directly is safe."""
        persistent = [self._persistent_changes[cube] for cube in sorted(self._persistent_changes)]
        individual = [self._individual_changes[frame] for frame in sorted(self._individual_changes)]
        return tuple(persistent + individual)

    # -- what the Mask rows under the sliders show (2026-10-16) -----------------------

    def persistent_points(self) -> dict[int, float]:
        """Every cube with a persistent mask change of its own, ascending, with the wavelength it was authored at (the change
        applies to the whole cube and to every cube after it, until the next persistent change)."""
        return {cube: self._persistent_changes[cube].frame[1] for cube in sorted(self._persistent_changes)}

    def _individual_by_cube(self) -> dict[int, tuple[float, ...]]:
        if self._individual_index is None:
            grouped: dict[int, list[float]] = {}
            for cube, wavelength in self._individual_changes:
                grouped.setdefault(cube, []).append(wavelength)
            self._individual_index = {cube: tuple(sorted(wavelengths)) for cube, wavelengths in sorted(grouped.items())}
        return self._individual_index

    def individual_cubes(self) -> dict[int, tuple[float, ...]]:
        """Every cube with at least one individual mask change, ascending, with the wavelengths (ascending) that have one."""
        return dict(self._individual_by_cube())

    def individual_wavelengths(self, cube: int) -> tuple[float, ...]:
        """The wavelengths of `cube` that have an individual mask change of their own. O(1) after a change (an index by cube is
        built on first use), because the wavelength row asks it on every cube step."""
        return self._individual_by_cube().get(int(cube), ())

    def has_mask_changes(self) -> bool:
        return bool(self._persistent_changes or self._individual_changes)

    # -- undoable commands of the Mask rows ------------------------------------------------

    def _row_command(
        self, label: str, edits: list[tuple[str, object, MaskChange | None, MaskChange | None]], *, batch: bool = False
    ) -> None:
        """Apply `edits` ``(scope, key, before, after)`` as one undoable step (nothing if there are none). The undo / redo
        are applied only while the timeline is in the state this command left / found (version token, see the module
        docstring); otherwise `undo_skipped` explains and nothing changes. Mask arrays are shared between the entries, never
        modified in place (the module's convention: `set_mask_change` stores copies)."""
        if not edits:
            return
        before_token, after_token = self._version, next(self._tokens)

        def write(which: int) -> None:  # 0 = back to `before`, 1 = to `after`
            for scope, key, before, after in edits:
                store = self._individual_changes if scope == "individual" else self._persistent_changes
                target = (before, after)[which]
                if target is None:
                    store.pop(key, None)
                else:
                    store[key] = target
            self._individual_index = None
            self._version = (before_token, after_token)[which]
            if batch:  # one emission for the whole step, naming the frames (a signal per mask would start hundreds of redraws)
                frames = tuple((before if before is not None else after).frame for _scope, _key, before, after in edits)
                self.mask_changed.emit(MaskComputationalChange(reason="mask_batch", frame=None, scope=None, frames=frames))
                return
            for scope, key, before, after in edits:
                frame = (before if before is not None else after).frame
                self.mask_changed.emit(MaskComputationalChange(reason="mask_change", frame=frame, scope=scope))

        def guarded(which: int) -> None:
            if self._version != (after_token, before_token)[which]:
                what = "undone" if which == 0 else "redone"
                logger.info("mask row command %r not %s: the mask changed after it", label, what)
                self.undo_skipped.emit(f"'{label}' was not {what}: the mask was changed after it.")
                return
            write(which)

        write(1)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: guarded(0), redo_fn=lambda: guarded(1)))

    @instrumented("MaskModule.set_mask_changes")
    def set_mask_changes(
        self, items: Iterable[tuple[tuple[int, float], str, np.ndarray | MaskChange]], label: str = "Auto mask"
    ) -> int:
        """Write many mask changes at once, ``(frame, scope, mask)`` each, as one undoable step (one `mask_changed`, reason
        ``"mask_batch"``). Items that equal what is stored already are skipped, a frame given twice keeps its last mask. Returns how
        many masks were written (0 = nothing changed, nothing is emitted or pushed). The caller resolves what each mask is made
        of (for the Auto mask step: the contour merged onto the mask in effect, `ChromaticModule.warp_mask_between` already
        applied): like `apply_candidate`, this module does not reach into other modules. A mask may also be handed over as an
        already built `MaskChange` (the Auto mask task compresses each mask as it goes, so the commit itself is instant)."""
        pending: dict[tuple[str, object], list[MaskChange | None]] = {}
        for frame, scope, mask in items:
            if scope not in _SCOPES:
                raise ValueError(f"scope must be one of {_SCOPES}, got {scope!r}")
            frame = (int(frame[0]), float(frame[1]))
            key: object = frame if scope == "individual" else frame[0]
            store = self._individual_changes if scope == "individual" else self._persistent_changes
            entry = pending.setdefault((scope, key), [store.get(key), None])
            entry[1] = mask.moved(frame, scope) if isinstance(mask, MaskChange) else MaskChange(frame=frame, scope=scope, mask=mask)
        edits = [(scope, key, before, after) for (scope, key), (before, after) in pending.items() if before != after]
        self._row_command(label, edits, batch=True)
        return len(edits)

    @instrumented("MaskModule.delete_persistent_mask")
    def delete_persistent_mask(self, cube: int) -> None:
        """Delete the persistent mask change of `cube` (undoable). The cube then follows the persistent change before it, if any."""
        change = self._persistent_changes.get(int(cube))
        self._row_command("Delete mask", [] if change is None else [("persistent", int(cube), change, None)])

    @instrumented("MaskModule.delete_individual_masks")
    def delete_individual_masks(self, frames: Iterable[tuple[int, float]]) -> None:
        """Delete the individual mask changes of these frames (undoable); frames without one are skipped."""
        edits = []
        for cube, wavelength in frames:
            key = (int(cube), float(wavelength))
            change = self._individual_changes.get(key)
            if change is not None:
                edits.append(("individual", key, change, None))
        self._row_command("Delete mask" if len(edits) == 1 else "Delete masks", edits)

    @instrumented("MaskModule.make_mask_individual")
    def make_mask_individual(self, cube: int) -> None:
        """Turn the persistent mask change of `cube` into an individual one at the frame it was drawn at (undoable): the mask
        then applies to that frame only, and the cubes after it go back to the persistent change before this one (or to no mask).
        Raises ``ValueError`` if that frame already has an individual mask of its own."""
        change = self._persistent_changes.get(int(cube))
        if change is None:
            return
        if change.frame in self._individual_changes:
            raise ValueError(f"{change.frame[1]:g} nm of cube {int(cube)} already has an individual mask; delete it first.")
        moved = change.moved(change.frame, "individual")
        self._row_command(
            "Make mask individual", [("persistent", int(cube), change, None), ("individual", change.frame, None, moved)]
        )

    @instrumented("MaskModule.make_mask_persistent")
    def make_mask_persistent(self, frame: tuple[int, float]) -> None:
        """Turn the individual mask change of `frame` into the persistent one of its cube (undoable): it then applies to the whole
        cube and to every cube after it, until the next persistent change. Raises ``ValueError`` if the cube already has a
        persistent mask change of its own (it would be overwritten)."""
        key = (int(frame[0]), float(frame[1]))
        change = self._individual_changes.get(key)
        if change is None:
            return
        if key[0] in self._persistent_changes:
            raise ValueError(f"Cube {key[0]} already has its own persistent mask; delete it first.")
        moved = change.moved(key, "persistent")
        self._row_command("Make mask persistent", [("individual", key, change, None), ("persistent", key[0], None, moved)])

    # -- session restore ------------------------------------------------

    @instrumented("MaskModule.restore_state")
    def restore_state(self, settings: MaskSettings, changes: tuple[MaskChange, ...]) -> None:
        """Replace the settings *and* the whole change timeline as a
        session load - not undo-tracked, but emits, for the reasons
        `GeometryModule.restore_settings` documents.

        Rebuilds both timeline dicts from scratch rather than merging, so a
        restore always lands on exactly what was saved. The keying matches
        `set_mask_change`'s: individual changes by full frame, persistent
        ones by cube only (a persistent change applies from its cube
        onward, so only one per cube can be in effect)."""
        self._settings = replace(settings)
        self._individual_changes = {}
        self._persistent_changes = {}
        for change in changes:
            if change.scope == "individual":
                self._individual_changes[change.frame] = change
            elif change.scope == "persistent":
                self._persistent_changes[change.frame[0]] = change
            else:
                raise ValueError(f"scope must be one of {_SCOPES}, got {change.scope!r}")
        self._touch()
        self.mask_changed.emit(MaskComputationalChange(reason="session_restored", frame=None, scope=None))

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

        existing = store.get(key)
        incoming = None if mask is None else MaskChange(frame=frame, scope=scope, mask=mask)  # copies, and compresses
        if existing == incoming:  # both absent, or the same pixels at the same frame and scope
            return

        if incoming is None:
            store.pop(key, None)
        else:
            store[key] = incoming
        self._touch()
        self.mask_changed.emit(MaskComputationalChange(reason="mask_change", frame=frame, scope=scope))

    @instrumented("MaskModule.clear_all_masks")
    def clear_all_masks(self) -> None:
        """Wipe the *entire* mask timeline (every persistent and individual
        change, every cube/frame) - "start a new mask" (2026-10-02,
        maintainer request: an eraser/clear action for the Image panel's new
        "Automatic edit" group), not a per-frame `set_mask_change(frame,
        scope, None)` removal. No-op-skipped if the timeline is already
        empty, matching every other command here."""
        if not self._individual_changes and not self._persistent_changes:
            return
        self._individual_changes = {}
        self._persistent_changes = {}
        self._touch()
        self.mask_changed.emit(MaskComputationalChange(reason="cleared", frame=None, scope=None))

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

        `base_mask` is the caller's job to resolve - typically the mask out
        of `resolve_mask_source(target_frame)`'s triple, warped into
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
        restrict_to: np.ndarray | None = None,
    ) -> None:
        """`restrict_to` (2026-10-03, area selection): a raw-space boolean mask;
        when given, only pixels where it is ``True`` may change - everywhere
        else keeps `base_mask`'s value.

        Run a morphological operation (`"erode"`/`"dilate"`/`"open"`/
        `"close"`) against `base_mask` and commit the result as a direct
        replacement - **not** an `apply_candidate` OR/AND merge, unlike
        every other tool kind.

        **Corrected 2026-10-02, before this method had any real caller**
        (found while wiring the Image panel's new "Edit" picker, which
        exposes morphology as four direct operation buttons with no +/-
        concept - see `panels/image/mask_edit_panels.py`). The original
        version here routed through `apply_candidate(base_mask, candidate,
        subtract=...)`, faithfully matching the stable app's own
        `_finish_apply_mask_delta` (`np.logical_and(current, ~candidate) if
        subtract else np.logical_or(current, candidate)`) - but that merge
        is only mathematically correct for the two *growing* operations
        (dilate/close are extensive: `candidate ⊇ base_mask` always, so
        `OR(base_mask, candidate) == candidate` exactly). For the two
        *shrinking* operations (erode/open are anti-extensive: `candidate ⊆
        base_mask` always), neither merge direction gives the eroded/opened
        mask: OR is a no-op (`candidate` already `⊆ base_mask`), and AND-NOT
        produces the *removed* ring of pixels, not the *surviving* interior
        - the opposite of what "erode my mask" should do. Confirmed by
        worked example (a filled square, `radius_px=1`): AND-NOT gives back
        only the 1px boundary ring that erosion would strip away, not the
        shrunken interior. This was never exercised by any UI before this
        session (verified by grep - no caller existed), so there is no
        shipped behavior to preserve here; a plain replacement is correct
        for all four operations uniformly and matches the new UI's own
        design (four direct action buttons, not an add/subtract pair) -
        `subtract` is dropped from the signature rather than kept unused."""
        candidate = raster_tools.apply_morphology_to_mask(base_mask, operation, radius_px)
        if restrict_to is not None:
            candidate = np.where(restrict_to, candidate, np.asarray(base_mask, dtype=bool))
        self.set_mask_change(target_frame, scope, candidate)

    @instrumented("MaskModule.paint_brush")
    def paint_brush(
        self,
        base_mask: np.ndarray,
        center_xy: tuple[float, float],
        diameter_px: float,
        *,
        target_frame: tuple[int, float],
        scope: str,
        value: bool,
        restrict_to: np.ndarray | None = None,
    ) -> None:
        """`restrict_to` (2026-10-03, area selection): raw-space mask; pixels
        outside it keep `base_mask`'s value. Not called yet - there is no
        canvas paint gesture - but the Draw tool must pass it when built.

        Paint one circular brush stroke onto `base_mask`
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
        painted = raster_tools.apply_brush_stamp(base_mask, center_xy, diameter_px, value=value)
        if restrict_to is not None:
            painted = np.where(restrict_to, painted, np.asarray(base_mask, dtype=bool))
        self.set_mask_change(target_frame, scope, painted)
