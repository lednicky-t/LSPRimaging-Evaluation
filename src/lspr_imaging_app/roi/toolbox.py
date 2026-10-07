"""``RoiToolbox`` (sketch §7 "ROI Toolbox", §10).

Owns ``AreaRoi``/``AreaRoiGroup``/``RoiArrayGroup`` and detection settings -
the single owner of every invariant (ROI IDs, "at most one group per ROI",
ordering). Meant to be called from multiple UI surfaces (Workflow panel's
Finding/Editing/Groups sub-tabs, the Image panel, the ROI/Group table panel)
- that's intended duplication of *affordances*, not of logic (sketch §7,
"Revised 2026-09-20: one backend, several front doors").

**Real command methods ported/built 2026-09-20**, replacing the earlier
scaffold stubs. Source logic is scattered across `gui/roi_geometry_mixin.py`
(553 lines) and `gui/roi_table_controller.py` (732 lines) on `develop`/
`main`, entangled with dialogs, undo snapshots, status-bar text, and table/
overlay redraw calls directly on `MainWindow` - the textbook god-object
pattern this rewrite exists to fix (see `docs/rewrite_feature_inventory_
2026-09.md`). This is consolidation + extraction of the real state-mutation
and invariant-enforcement logic, not a mechanical port: dialogs/status-text/
table-rendering stay in the not-yet-built panel layer, which will prompt for
input then call these command methods with already-resolved values.

**ROI IDs renumber contiguously on delete, matching the old app - reverted
back to this after a first attempt at stable IDs, per explicit maintainer
instruction 2026-09-20.** `delete_rois()` renumbers every surviving ROI to
stay contiguous (1..N), the same as the old app's `_reindex_detected_rois`,
instead of leaving gaps. The maintainer asked for this specifically *because*
undo/redo needed to be designed to handle it properly, not despite that -
see `delete_rois()`'s own docstring for how the renumbering itself is made
undoable (an explicit `old_id -> new_id` map, reversed on undo, not a generic
diff). `add_roi`/`detect_rois` were already contiguous-by-construction and
needed no change.

**Cross-module consequence**: renumbering on delete means any *other*
module holding onto a roi_id across a delete (`SelectionModule`'s current
selection; the future analysis store's per-ROI provenance, keyed by
roi_id) goes stale unless it also remaps. `delete_rois()` emits
`roi_ids_renumbered` (an `{old_id: new_id}` dict, for survivors only)
precisely so those modules *can* subscribe and remap their own state
without this module reaching into theirs (AGENTS.md's "no module reads or
writes another module's internals" rule). **`SelectionModule` now
subscribes** (`selection/module.py`'s `remap_roi_ids()`, wired in
`app_rewrite.build_main_window()`) - built 2026-09-20. **`AnalysisEngine`
subscribes too** (`remap_roi_ids()`, 2026-10-06): stored results are keyed by
roi_id, so they must follow their ROI to its new number or the survivor
would show the deleted ROI's spectrum. The signal's map covers every ROI that
survives; an id absent from it is dropped (empty map = a fresh detection, no
old ROI survives).

**Undo/redo**: every mutating command below pushes one
`undo.FunctionCommand` to the shared `undo.undo_manager` (see that module's
docstring for the cross-module design - the maintainer asked for a "proper,
not minimal" design scoped to handle every module, not just this one).
Every command's change signal is emitted from *inside* its `apply`/`revert`
closures, not once after the initial call - so an undo or a redo re-emits
the same signal a fresh call would, and panels/other modules actually learn
about a state change that happened via Ctrl+Z, not just a direct call.
Selection is deliberately **not** undoable, matching the old app (selecting/
deselecting was never wrapped in `_push_undo_point` there either) and
AGENTS.md's "selection changes must never implicitly trigger computation"
spirit - undo history is for state that affects results, not where the
cursor/selection happens to be.

**Selection ownership fixed, not duplicated**: the original scaffold stub
had both a `set_selection`/`selection_changed` pair here *and* on
`SelectionModule` - AGENTS.md is explicit that `selection/` is "the one
intentionally shared piece of state (current cube/wavelength/ROI
selection)". Removed the duplicate here; selection lives only on
`SelectionModule`, and callers that need both ROI mutation and selection
state talk to both modules directly (RoiToolbox never reaches into
SelectionModule per the "no module reaches into another's internals" rule).

**`display_position()` built 2026-09-21**, settled by strong precedent
rather than a fresh decision: every other cross-module boundary built this
session (`roi/rasterize.py`'s dispatchers, `MaskModule.resolve_mask_
source()`) takes an already-resolved `affine_matrix`/warp result as a
plain parameter rather than holding a reference to the module that
produced it, so `display_position()` follows the same shape - it takes
`affine_matrix` (the caller's job to obtain via `ChromaticModule.
affine_for(image_key)`), never reaching into Chromatic itself. A manual
per-wavelength nudge (`AreaRoi.per_wavelength`, see that field's own
docstring in `model.py`) wins outright when one exists for the queried
`image_key`, since it's already expressed in that wavelength's own display
space with nothing left to transform.

The old app's spatial array-reordering feature
(`_reorder_rois_by_position`/`_order_rois_as_array`/`_group_rois_by_column` -
renumbering ROIs into row/column order) is not ported here either - it's a
separate, UI-heavy feature distinct from `reorder_group` (which this module
does implement, for *group* display order).

**Bulk commands for the ROI/Group table (2026-10-06)**: a ROI's id is its
place in the list, so `reorder_rois`/`move_in_order` permute the ids (one undo
step; `roi_ids_renumbered` carries the full old->new map, which is how
selection and stored analysis results follow their ROI). Groups: `group_rois`,
`add_rois_to_group`, `remove_rois_from_groups`, `delete_group`. A group has a
base colour and each member a stored tint of it (`roi/palette.py`);
`set_roi_colors` sets one by hand. Geometry: `translate_rois`, `place_rois`,
`resize_rois`, `reset_roi_diameters` (bad sizes raise `ValueError` before
anything changes). Undo of a grouping change restores fields onto the *same*
group objects (`_snapshot`/`_restore`), never copies, so older undo steps that
hold a group stay valid. Geometry-type switching
(circle/annulus <-> mask, with mask-drawing) isn't built - no mask-editing
UI exists yet in the rewrite to drive it.
"""

from __future__ import annotations

import copy
import itertools
import logging
import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, fields, replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..change_events import RoiComputationalChange, RoiCosmeticChange
from ..diagnostics import instrumented
from ..image_tools.chromatic.affine import apply_affine_to_points
from ..undo import FunctionCommand, undo_manager
from .model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup, RoiArrayGroup, RoiMask
from .ordering import move_block, renumbering
from .palette import first_free_tint_index, next_group_color, normalize_hex, tint_color

logger = logging.getLogger(__name__)

MIN_SAMPLE_DIAMETER_PX = 2.0
"""Smallest sample diameter a ROI may be given (the stable app's dialogs used
the same floor)."""

DEFAULT_REFERENCE_COLOR_HEX = "#38bdf8"


def _snapshot(obj: object) -> tuple[object, object]:
    """``(obj, a deep copy of it)``. Undo restores the copy's field values
    onto ``obj`` itself (`_restore`) rather than swapping in a copy, so every
    other closure on the undo stack that holds ``obj`` keeps pointing at the
    live object."""
    return obj, copy.deepcopy(obj)


def _restore(snapshot: tuple[object, object]) -> None:
    obj, saved = snapshot
    for field in fields(obj):
        setattr(obj, field.name, copy.deepcopy(getattr(saved, field.name)))


_CIRCLE_GEOMETRY_TYPES = ("circle", "annulus")
"""Geometry types whose only remapped field is the ROI center - radius/
diameter fields are invariant under this app's transform pipeline (rotate,
flip, crop - never a scale change), so there is nothing else to do for them."""


@dataclass(frozen=True)
class RoiRemapReport:
    """What one `RoiToolbox.remap_all` call actually did - enough for a
    caller to build a status message without re-deriving it from
    `RoiComputationalChange`."""

    remapped_roi_ids: tuple[int, ...]
    """Every ROI whose center (and, for a mask-geometry side, raster) moved."""
    mask_shapes_remapped: int
    """How many sample/reference sides were mask-geometry and were warped."""
    mask_shapes_lost: tuple[int, ...]
    """ROI ids whose mask-geometry side warped to zero area - expected to be
    empty in practice (see `roi.rasterize.remap_roi_mask`'s docstring) - and
    was therefore left at its old position rather than replaced with an
    empty mask; see `remap_all`'s docstring."""
    unsupported_shapes_skipped: tuple[int, ...]
    """ROI ids with a geometry type this pipeline doesn't remap yet
    (rectangle/polygon - not a shape any ROI can have today, see
    `_remap_roi_shape`'s docstring) - left entirely at their old position."""

    @property
    def needs_attention(self) -> bool:
        return bool(self.mask_shapes_lost or self.unsupported_shapes_skipped)


def _highest_group_number(groups: dict[str, AreaRoiGroup]) -> int:
    """The largest `n` among `"group_<n>"` ids, or 0 if there are none.

    `create_group` mints ids as `f"group_{next(counter)}"`, so restoring a
    session has to resume that counter past whatever was loaded, or the next
    group created would reuse an existing id and silently replace it. Ids
    that don't match the pattern (hand-edited, or from a future scheme) are
    ignored rather than treated as an error - they simply can't collide with
    a generated one."""
    highest = 0
    for group_id in groups:
        _, _, suffix = str(group_id).partition("group_")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def _remap_roi_shape(
    roi: AreaRoi,
    side: str,
    remap_mask: Callable[[RoiMask], RoiMask | None],
) -> tuple[RoiMask | None, bool, bool]:
    """One ROI's one side (`"sample"` or `"reference"`) through
    `RoiToolbox.remap_all`'s per-geometry-type dispatch. Returns
    `(new_mask_value, lost, unsupported)`: `new_mask_value` is what
    `{side}_mask` should become (unchanged from its current value unless the
    geometry type is `"mask"` and the warp succeeded); `lost`/`unsupported`
    are never both true, and one flag is on only when `new_mask_value` is
    the *old*, deliberately-unchanged value - see `remap_all`'s docstring
    for what each case means.

    This is the one place a new ROI shape's remap sub-pipeline gets added -
    a new `elif geometry_type == "rectangle": return _remap_rectangle(...)`
    branch (plus, separately, the ROI's own orientation-angle field, since a
    rectangle/polygon's rotation needs remapping the way a circle's radius
    never does) - not a change to `remap_all` itself."""
    geometry_type = getattr(roi, f"{side}_geometry_type")
    current_mask = getattr(roi, f"{side}_mask")
    if geometry_type in _CIRCLE_GEOMETRY_TYPES:
        return current_mask, False, False
    if geometry_type == "mask":
        if current_mask is None:  # inconsistent state (type says mask, field is empty) - nothing to warp
            return None, False, False
        remapped = remap_mask(current_mask)
        if remapped is None:
            logger.warning(
                "ROI %d's %s mask warped to zero area on a geometry edit - left at its old position.",
                roi.area_roi_id, side,
            )
            return current_mask, True, False
        return remapped, False, False
    # "rectangle"/"polygon" (roi_system_roadmap.md) or any other geometry
    # type this pipeline doesn't yet know how to remap - left untouched
    # rather than guessed at.
    return current_mask, False, True


class RoiToolbox(QObject):
    """Owns ROI/group state; exposes a query interface plus the full
    command API. No other module or panel may hold its own copy of ROI or
    group state (AGENTS.md, "What NOT to do without checking in again first")."""

    geometry_changed = pyqtSignal(RoiComputationalChange)
    cosmetic_changed = pyqtSignal(RoiCosmeticChange)
    # {old_id: new_id} for **every ROI that still exists** after a delete or a
    # fresh detection - see module docstring, "Cross-module consequence". An
    # old id absent from the map is a ROI that is gone, so a subscriber drops
    # whatever it holds for it; an empty map means no old ROI survives. Not
    # itself a Cosmetic/Computational change (it doesn't mean anything was
    # recomputed or needs redrawing on its own) - a separate, narrower signal
    # for "if you hold a roi_id, it may now be wrong".
    roi_ids_renumbered = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rois: dict[int, AreaRoi] = {}
        self._groups: dict[str, AreaRoiGroup] = {}
        self._array_groups: dict[str, RoiArrayGroup] = {}
        self._detection_settings = AreaRoiDetectionSettings()
        self._roi_id_counter = itertools.count(1)
        self._group_id_counter = itertools.count(1)

    # -- query interface ------------------------------------------------

    def rois(self) -> tuple[AreaRoi, ...]:
        return tuple(self._rois.values())

    def detection_settings(self) -> AreaRoiDetectionSettings:
        """The shared ROI detection/reduction settings - a defensive copy,
        same guarantee `GeometryModule.settings()` makes.

        **Ownership decided 2026-09-23, while wiring `AnalysisEngine`**:
        nothing owned `AreaRoiDetectionSettings` anywhere in the rewrite -
        every module took it as a function *parameter* (`roi/detection.py`,
        `image_tools/background/estimate.py`, `image_tools/preprocess.py`,
        `image_tools/chromatic/landmark_autotrack.py`), so there was no
        module to read it from when the engine needed
        `reference_inner/outer_diameter_px` (its default reference-ring radii
        for ROIs that don't override them) and `reduction_method`. Put here
        because this is the ROI stage's state owner and the dataclass is
        ROI-stage settings throughout.

        **Open question, deliberately not answered here**: `reduction_method`
        and `formula_key` are arguably analysis-stage rather than ROI-stage
        concerns. They live in this one dataclass because the old app put
        them there (`domain/models.py`), and splitting a ported dataclass is
        a design change on its own - flagged rather than done silently."""
        return replace(self._detection_settings)

    def roi_by_id(self, roi_id: int) -> AreaRoi:
        return self._rois[roi_id]

    def array_groups(self) -> tuple[RoiArrayGroup, ...]:
        """Regular-array ROI-generation recipes (sketch §7: "Owns... RoiArrayGroup
        recipes")."""
        return tuple(self._array_groups.values())

    def groups(self) -> tuple[AreaRoiGroup, ...]:
        return tuple(self._groups.values())

    def group_for_roi(self, roi_id: int) -> AreaRoiGroup | None:
        for group in self._groups.values():
            if roi_id in group.area_roi_ids:
                return group
        return None

    def display_position(self, roi_id: int, image_key: tuple[int, float], affine_matrix: np.ndarray) -> tuple[float, float]:
        """Resolve `roi_id`'s position in `image_key`'s display space -
        callers never compose the chromatic transform themselves (sketch
        §7), they just fetch `affine_matrix` from `ChromaticModule.
        affine_for(image_key)` and hand it over (see module docstring for
        why this module takes it as a parameter rather than reaching into
        Chromatic itself).

        A manual per-wavelength nudge (`roi.per_wavelength`, written while
        viewing a non-reference wavelength - see that field's docstring in
        `model.py`) wins outright when one exists for `image_key` exactly:
        it's already expressed directly in that wavelength's own display
        space, nothing left to transform. Otherwise the ROI's reference-
        frame center is mapped through `affine_matrix`."""
        roi = self._rois[roi_id]
        if roi.per_wavelength:
            nudge = roi.per_wavelength.get(image_key)
            if nudge is not None:
                return float(nudge[0]), float(nudge[1])
        point = np.asarray([[roi.center_x, roi.center_y]], dtype=np.float64)
        transformed = apply_affine_to_points(point, affine_matrix)
        return float(transformed[0, 0]), float(transformed[0, 1])

    def display_positions(self, image_key: tuple[int, float], affine_matrix: np.ndarray) -> np.ndarray:
        """``display_position`` for every ROI at once: an (N, 2) array in
        `rois()` order. One affine multiplication for all centres instead of
        one per ROI (the Image panel and hit-testing call this on every
        redraw); a manual per-wavelength nudge still wins for its ROI."""
        rois = tuple(self._rois.values())
        if not rois:
            return np.empty((0, 2), dtype=np.float64)
        centers = np.asarray([(roi.center_x, roi.center_y) for roi in rois], dtype=np.float64)
        positions = apply_affine_to_points(centers, affine_matrix)
        for index, roi in enumerate(rois):
            if roi.per_wavelength:
                nudge = roi.per_wavelength.get(image_key)
                if nudge is not None:
                    positions[index] = (float(nudge[0]), float(nudge[1]))
        return positions

    # -- session restore ------------------------------------------------

    def restore_state(
        self,
        detection_settings: AreaRoiDetectionSettings,
        rois: tuple[AreaRoi, ...],
        groups: tuple[AreaRoiGroup, ...],
        arrays: tuple[RoiArrayGroup, ...],
    ) -> None:
        """Replace every piece of this module's state as a session load -
        not undo-tracked, but emits, for the reasons
        `GeometryModule.restore_settings` documents.

        **The id counter is restored too, not reset.** `add_roi` hands out
        `next(self._roi_id_counter)`, and `delete_rois` keeps ids
        contiguous - so after restoring N ROIs the counter has to resume at
        the highest restored id plus one. Resetting it to 1 would make the
        very first ROI added after opening a session collide with an
        existing one and silently replace it. Same for group ids, whose
        `"group-<n>"` form is parsed back out for the same reason."""
        self._detection_settings = replace(detection_settings)
        self._rois = {int(roi.area_roi_id): roi for roi in rois}
        self._groups = {str(group.group_id): group for group in groups}
        self._array_groups = {str(array_group.array_id): array_group for array_group in arrays}
        self._roi_id_counter = itertools.count(max(self._rois, default=0) + 1)
        self._group_id_counter = itertools.count(_highest_group_number(self._groups) + 1)
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=(), reason="session_restored"))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="session_restored"))

    # -- command API (§7): single-ROI mutation ---------------------------

    @instrumented("RoiToolbox.set_detection_settings")
    def set_detection_settings(self, settings: AreaRoiDetectionSettings) -> None:
        """Replace the shared detection/reduction settings wholesale.

        One combined command rather than a setter per field, deliberately -
        the same shape (and the same reasoning) as
        `BackgroundModule.set_flatten_background_settings`: the old app's
        real UI is a settings panel with an Apply button pushing exactly one
        `_push_undo_point("Detection settings")` (`gui/main_window.py`) for
        the whole group, not one undo entry per spinbox. Undo label matches
        the old app's exactly.

        Emits `geometry_changed`, not `cosmetic_changed`: every field here
        either changes which ROIs detection finds or how their pixels reduce
        to a value, so anything already computed against the old settings is
        stale. `reason="detection_settings"` with an empty `roi_ids` -
        nothing about any *individual* ROI changed, the shared inputs did."""
        if settings == self._detection_settings:
            return
        old_settings = self._detection_settings
        new_settings = replace(settings)

        def apply() -> None:
            self._detection_settings = new_settings
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(), reason="detection_settings"))

        def revert() -> None:
            self._detection_settings = old_settings
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(), reason="detection_settings"))

        apply()
        undo_manager.push(FunctionCommand("Detection settings", undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.add_roi")
    def add_roi(self, x: float, y: float, *, sample_diameter_px: float = 20.0) -> int:
        """Manually place one ROI at (x, y) - the click-to-add tool. Not in
        the original scaffold's stub list (a real gap: there was no command
        for the old app's `_add_roi_at`, the single most basic way to
        populate the toolbox short of running detection). IDs are already
        contiguous-by-construction here (the counter always hands out
        `len(rois) + 1` as long as `delete_rois` keeps its own renumbering
        promise), so no special undo bookkeeping beyond add/remove is
        needed."""
        roi_id = next(self._roi_id_counter)
        roi = AreaRoi(area_roi_id=roi_id, center_x=float(x), center_y=float(y), sample_diameter_px=float(sample_diameter_px))

        def apply() -> None:
            self._rois[roi_id] = roi
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="added"))

        def revert() -> None:
            self._rois.pop(roi_id, None)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="deleted"))

        apply()
        undo_manager.push(FunctionCommand("Add ROI", undo_fn=revert, redo_fn=apply))
        return roi_id

    # -- multi-ROI helpers ----------------------------------------------

    def _require_rois(self, roi_ids: Collection[int]) -> list[AreaRoi]:
        """The ROI objects for ``roi_ids``, in ascending id order. `KeyError`
        naming any id that does not exist - a stale id is a caller bug, not
        something to skip silently."""
        ids = sorted({int(roi_id) for roi_id in roi_ids})
        missing = [roi_id for roi_id in ids if roi_id not in self._rois]
        if missing:
            raise KeyError(f"no ROI with id {missing}")
        return [self._rois[roi_id] for roi_id in ids]

    # -- command API (§7): geometry of one or many ROIs --------------------
    #
    # Every bulk command is one undo step and one signal, however many ROIs
    # it touches, and validates every ROI before changing any (it either
    # applies to all or raises). Undo/redo closures hold the ROI *objects*,
    # not ids, so they stay correct whatever renumbering happened around them.

    def _set_positions(self, positions: dict[int, tuple[float, float]], label: str) -> None:
        rois = self._require_rois(positions)
        moves: list[tuple[AreaRoi, tuple[float, float], tuple[float, float]]] = []
        for roi in rois:
            x, y = float(positions[roi.area_roi_id][0]), float(positions[roi.area_roi_id][1])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"ROI {roi.area_roi_id}: position must be finite, got ({x}, {y})")
            if (roi.center_x, roi.center_y) != (x, y):
                moves.append((roi, (roi.center_x, roi.center_y), (x, y)))
        if not moves:
            return

        def write(pairs: list[tuple[AreaRoi, tuple[float, float]]]) -> None:
            for roi, (x, y) in pairs:
                roi.center_x, roi.center_y = x, y
            ids = tuple(sorted(roi.area_roi_id for roi, _ in pairs))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=ids, reason="moved"))

        new_pairs = [(roi, new) for roi, _old, new in moves]
        old_pairs = [(roi, old) for roi, old, _new in moves]
        write(new_pairs)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: write(old_pairs), redo_fn=lambda: write(new_pairs)))

    @instrumented("RoiToolbox.move_roi")
    def move_roi(self, roi_id: int, x: float, y: float) -> None:
        self._set_positions({roi_id: (x, y)}, "Move ROI")

    @instrumented("RoiToolbox.translate_rois")
    def translate_rois(self, roi_ids: Collection[int], dx: float, dy: float) -> None:
        """Shift every ROI in ``roi_ids`` by (dx, dy) pixels: the multi-select
        position edit that keeps the ROIs' arrangement."""
        rois = self._require_rois(roi_ids)
        self._set_positions(
            {roi.area_roi_id: (roi.center_x + float(dx), roi.center_y + float(dy)) for roi in rois}, "Move ROIs"
        )

    @instrumented("RoiToolbox.place_rois")
    def place_rois(self, roi_ids: Collection[int], *, x: float | None = None, y: float | None = None) -> None:
        """Set the x and/or y of every ROI in ``roi_ids`` to one value
        (``None`` leaves that coordinate alone). Typically one coordinate, to
        line up a row or a column; giving both stacks the ROIs on one point."""
        if x is None and y is None:
            return
        rois = self._require_rois(roi_ids)
        self._set_positions(
            {
                roi.area_roi_id: (roi.center_x if x is None else float(x), roi.center_y if y is None else float(y))
                for roi in rois
            },
            "Align ROIs",
        )

    def _set_diameters(
        self,
        targets: list[tuple[AreaRoi, tuple[float, float | None, float | None]]],
        label: str,
    ) -> None:
        changes = []
        for roi, new in targets:
            old = (roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px)
            if old != new:
                changes.append((roi, old, new))
        if not changes:
            return

        def write(pairs: list[tuple[AreaRoi, tuple[float, float | None, float | None]]]) -> None:
            for roi, (sample, inner, outer) in pairs:
                roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px = (
                    sample, inner, outer,
                )
            ids = tuple(sorted(roi.area_roi_id for roi, _ in pairs))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=ids, reason="resized"))

        new_pairs = [(roi, new) for roi, _old, new in changes]
        old_pairs = [(roi, old) for roi, old, _new in changes]
        write(new_pairs)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: write(old_pairs), redo_fn=lambda: write(new_pairs)))

    @instrumented("RoiToolbox.resize_rois")
    def resize_rois(
        self,
        roi_ids: Collection[int],
        *,
        sample_diameter_px: float | None = None,
        reference_inner_diameter_px: float | None = None,
        reference_outer_diameter_px: float | None = None,
    ) -> None:
        """Set one or more of the sample / reference-ring diameters on every
        ROI in ``roi_ids``; a field left ``None`` is unchanged. A no-op (no
        undo entry) if nothing differs.

        Raises ``ValueError``, before changing anything, if the sample
        diameter is below `MIN_SAMPLE_DIAMETER_PX` or not finite, or if a
        ROI's reference ring would not satisfy ``0 <= inner < outer``. The
        ring is checked against the diameters the ROI will actually use, so a
        ROI that still inherits the default outer diameter is checked against
        that default."""
        rois = self._require_rois(roi_ids)
        defaults = self._detection_settings
        for value, name in (
            (sample_diameter_px, "sample diameter"),
            (reference_inner_diameter_px, "reference inner diameter"),
            (reference_outer_diameter_px, "reference outer diameter"),
        ):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        if sample_diameter_px is not None and float(sample_diameter_px) < MIN_SAMPLE_DIAMETER_PX:
            raise ValueError(f"sample diameter must be at least {MIN_SAMPLE_DIAMETER_PX:g} px, got {float(sample_diameter_px):g}")
        targets: list[tuple[AreaRoi, tuple[float, float | None, float | None]]] = []
        for roi in rois:
            new = (
                roi.sample_diameter_px if sample_diameter_px is None else float(sample_diameter_px),
                roi.reference_inner_diameter_px if reference_inner_diameter_px is None else float(reference_inner_diameter_px),
                roi.reference_outer_diameter_px if reference_outer_diameter_px is None else float(reference_outer_diameter_px),
            )
            if reference_inner_diameter_px is not None or reference_outer_diameter_px is not None:
                inner = new[1] if new[1] is not None else defaults.reference_inner_diameter_px
                outer = new[2] if new[2] is not None else defaults.reference_outer_diameter_px
                if not 0.0 <= inner < outer:
                    raise ValueError(
                        f"ROI {roi.area_roi_id}: the reference ring needs 0 <= inner < outer "
                        f"(inner {inner:g} px, outer {outer:g} px)"
                    )
            targets.append((roi, new))
        self._set_diameters(targets, "Resize ROI" if len(targets) == 1 else "Resize ROIs")

    @instrumented("RoiToolbox.resize_roi")
    def resize_roi(
        self,
        roi_id: int,
        *,
        sample_diameter_px: float | None = None,
        reference_inner_diameter_px: float | None = None,
        reference_outer_diameter_px: float | None = None,
    ) -> None:
        """One-ROI form of `resize_rois`."""
        self.resize_rois(
            (roi_id,),
            sample_diameter_px=sample_diameter_px,
            reference_inner_diameter_px=reference_inner_diameter_px,
            reference_outer_diameter_px=reference_outer_diameter_px,
        )

    @instrumented("RoiToolbox.reset_roi_diameters")
    def reset_roi_diameters(self, roi_ids: Collection[int], *, sample: bool = True, reference: bool = True) -> None:
        """Put the chosen diameters back to the shared defaults.

        ``sample``: the sample diameter becomes the detection settings'
        sample diameter. ``reference``: the ROI's own inner/outer overrides
        are removed (``None``), so it follows the shared reference diameters
        again, including when those change later."""
        rois = self._require_rois(roi_ids)
        defaults = self._detection_settings
        targets = [
            (
                roi,
                (
                    float(defaults.sample_diameter_px) if sample else roi.sample_diameter_px,
                    None if reference else roi.reference_inner_diameter_px,
                    None if reference else roi.reference_outer_diameter_px,
                ),
            )
            for roi in rois
        ]
        self._set_diameters(targets, "Reset ROI diameters")

    @instrumented("RoiToolbox.delete_roi")
    def delete_roi(self, roi_id: int) -> None:
        self.delete_rois((roi_id,))

    @instrumented("RoiToolbox.delete_rois")
    def delete_rois(self, roi_ids: tuple[int, ...]) -> None:
        """Bulk delete - the old app's "Remove selected" toolbar action
        always acts on a set, not one ROI at a time; `delete_roi` above is a
        one-ROI convenience wrapper over this, the real primitive.

        **Renumbers every surviving ROI to stay contiguous** (1..N),
        matching the old app's `_reindex_detected_rois` - deleting ROI 3 out
        of {1,2,3,4,5} leaves {1,2,3,4}, not {1,2,4,5}. The `old_id -> new_id`
        map is computed once, up front, from the *sorted surviving ids* (so
        relative order is preserved) - both `apply()` and `revert()` reuse
        that same fixed map (reversed for `revert()`), rather than
        recomputing it from whatever the live state happens to be when undo
        or redo runs. That's what makes this safe under undo/redo: by the
        time `revert()` runs, every command pushed after this one has
        already been undone in reverse order (a linear stack - see
        `undo/manager.py`), so the toolbox is guaranteed to be in exactly the
        post-`apply()` state this map was computed against.

        Also prunes any group/array that becomes empty as a result (matching
        the old app's own behavior), and restores exactly those pruned
        groups/arrays on undo - captured via a small `deepcopy` of only the
        directly-linked groups/arrays (a handful of objects), not the whole
        app state (see undo/manager.py's docstring on why that distinction
        matters for cost). Emits `roi_ids_renumbered` (see module docstring)
        on both `apply()` and `revert()`, with the map and its reverse
        respectively, so anything holding onto a roi_id can follow along in
        either direction.
        """
        ids_to_delete = {int(roi_id) for roi_id in roi_ids if int(roi_id) in self._rois}
        if not ids_to_delete:
            return
        removed_rois = {roi_id: self._rois[roi_id] for roi_id in ids_to_delete}
        groups_before = [_snapshot(group) for group in self._groups.values()]
        arrays_before = [_snapshot(array) for array in self._array_groups.values()]
        original_max_id = max(self._rois) if self._rois else 0
        next_counter_before_delete = original_max_id + 1

        surviving_old_ids_sorted = sorted(rid for rid in self._rois if rid not in ids_to_delete)
        id_map = {old_id: new_id for new_id, old_id in enumerate(surviving_old_ids_sorted, start=1)}
        reverse_id_map = {new_id: old_id for old_id, new_id in id_map.items()}
        next_counter_after_delete = len(surviving_old_ids_sorted) + 1

        def apply() -> None:
            renumbered: dict[int, AreaRoi] = {}
            for old_id, new_id in id_map.items():
                roi = self._rois[old_id]
                roi.area_roi_id = new_id
                renumbered[new_id] = roi
            self._rois = renumbered
            for group in self._groups.values():
                group.area_roi_ids = sorted({id_map[rid] for rid in group.area_roi_ids if rid in id_map})
            self._groups = {group_id: group for group_id, group in self._groups.items() if group.area_roi_ids}
            for array in self._array_groups.values():
                array.member_area_roi_ids = [id_map[rid] for rid in array.member_area_roi_ids if rid in id_map]
            self._array_groups = {
                array_id: array for array_id, array in self._array_groups.items() if array.member_area_roi_ids
            }
            self._roi_id_counter = itertools.count(next_counter_after_delete)
            # Always emitted, even when no ROI survives (empty map): a
            # subscriber drops what it holds for every id absent from the map.
            self.roi_ids_renumbered.emit(dict(id_map))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(ids_to_delete)), reason="deleted"))

        def revert() -> None:
            restored: dict[int, AreaRoi] = {}
            for new_id, roi in self._rois.items():
                old_id = reverse_id_map[new_id]
                roi.area_roi_id = old_id
                restored[old_id] = roi
            restored.update(removed_rois)
            self._rois = restored
            for snapshot in groups_before:
                _restore(snapshot)
            for snapshot in arrays_before:
                _restore(snapshot)
            self._groups = {snapshot[0].group_id: snapshot[0] for snapshot in groups_before}
            self._array_groups = {snapshot[0].array_id: snapshot[0] for snapshot in arrays_before}
            self._roi_id_counter = itertools.count(next_counter_before_delete)
            self.roi_ids_renumbered.emit(dict(reverse_id_map))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(removed_rois)), reason="added"))

        apply()
        label = "Delete ROI" if len(ids_to_delete) == 1 else f"Delete {len(ids_to_delete)} ROIs"
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.detect_rois")
    def detect_rois(self, detected_rois: list[AreaRoi], array_groups: tuple[RoiArrayGroup, ...] = ()) -> list[int]:
        """Replace every current ROI/group/array with a fresh detection
        result. Takes already-detected ROIs rather than running detection
        itself (`roi.detection.detect_rois()`, a potentially-slow call that
        must run off the GUI thread per AGENTS.md's non-negotiable
        invariants) - the caller is responsible for running detection on a
        worker and only calling this once a result exists, the same
        threading boundary the old app's async worker + `_on_detect_rois_
        ready` callback already enforced. `detected_rois` already carries
        contiguous 1..N ids (`roi.detection.detect_rois()`'s own contract),
        so no renumbering is needed here the way `delete_rois` needs it.

        Emits `roi_ids_renumbered` with an **empty map**, in `apply()` and in
        `revert()`: every previous ROI is gone and the new ones merely reuse
        the numbers 1..N. Without it, results stored for the old ROI 1 would
        be shown for the new ROI 1. (Selection is cleared by the same signal,
        which is right: the old selection means nothing now.)
        """
        old_rois = dict(self._rois)
        old_groups = dict(self._groups)
        old_arrays = dict(self._array_groups)
        old_next_counter = (max(old_rois) + 1) if old_rois else 1
        new_rois = {roi.area_roi_id: roi for roi in detected_rois}
        new_arrays = {array.array_id: array for array in array_groups}
        new_next_counter = (max(new_rois) + 1) if new_rois else 1
        affected_ids = tuple(sorted(set(old_rois) | set(new_rois)))

        def apply() -> None:
            self._rois = dict(new_rois)
            self._groups = {}
            self._array_groups = dict(new_arrays)
            self._roi_id_counter = itertools.count(new_next_counter)
            self.roi_ids_renumbered.emit({})
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="detected"))

        def revert() -> None:
            self._rois = dict(old_rois)
            self._groups = dict(old_groups)
            self._array_groups = dict(old_arrays)
            self._roi_id_counter = itertools.count(old_next_counter)
            self.roi_ids_renumbered.emit({})
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="detected"))

        apply()
        undo_manager.push(FunctionCommand("Detect ROIs", undo_fn=revert, redo_fn=apply))
        return list(new_rois.keys())

    # -- command API (§7): ROI order ---------------------------------------
    #
    # A ROI's id is its place in the list, so reordering is a permutation of
    # the ids. Nothing is recomputed: the ROIs themselves are unchanged, and
    # stored analysis results follow them through `roi_ids_renumbered` (see
    # `AnalysisEngine.remap_roi_ids`). **Callers should not reorder while an
    # analysis run is in flight** (`AnalysisEngine.is_running()`); the engine
    # cancels the run if they do.

    def _renumber(self, id_map: dict[int, int], label: str) -> None:
        """Apply ``id_map`` (``{old_id: new_id}``, a permutation covering
        **every** current ROI) as one undo step. Group and array membership
        follow their ROIs. Emits `roi_ids_renumbered` with the full map (the
        contract documented on that signal) and a cosmetic change for the ROIs
        whose number changed."""
        if set(id_map) != set(self._rois) or set(id_map.values()) != set(self._rois):
            raise ValueError("a renumbering must be a permutation of the current ROI ids")
        if all(old == new for old, new in id_map.items()):
            return
        reverse = {new: old for old, new in id_map.items()}

        def apply(mapping: dict[int, int]) -> None:
            by_current_id = dict(self._rois)
            renumbered: dict[int, AreaRoi] = {}
            for old, new in mapping.items():
                roi = by_current_id[old]
                roi.area_roi_id = new
                renumbered[new] = roi
            self._rois = dict(sorted(renumbered.items()))
            for group in self._groups.values():
                group.area_roi_ids = sorted(mapping[roi_id] for roi_id in group.area_roi_ids)
            for array in self._array_groups.values():
                array.member_area_roi_ids = [mapping[roi_id] for roi_id in array.member_area_roi_ids]
            changed = tuple(sorted(new for old, new in mapping.items() if old != new))
            self.roi_ids_renumbered.emit(dict(mapping))
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=changed, reason="renumbered"))

        apply(id_map)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: apply(reverse), redo_fn=lambda: apply(id_map)))

    @instrumented("RoiToolbox.reorder_rois")
    def reorder_rois(self, new_order: Sequence[int]) -> None:
        """Put the ROIs in ``new_order`` - every current id, once each, in
        the order they should now appear. The ROI at position j takes the
        j-th smallest id (1, 2, 3, ... when ids are contiguous)."""
        order = [int(roi_id) for roi_id in new_order]
        slots = sorted(self._rois)
        if sorted(order) != slots:
            raise ValueError("new_order must list every ROI id exactly once")
        self._renumber(renumbering(order, slots), "Reorder ROIs")

    @instrumented("RoiToolbox.move_in_order")
    def move_in_order(
        self, roi_ids: Collection[int], target_index: int, *, scope_ids: Collection[int] | None = None
    ) -> None:
        """Move the ROIs in ``roi_ids`` (as a block, keeping their relative
        order) so the block starts at ``target_index``; see
        `ordering.move_block` for how the index counts.

        ``scope_ids`` is the list the user is looking at: omitted, all ROIs;
        for a group's members, only those. Only the id numbers those ROIs
        already hold are shuffled among themselves, so moving a ROI within
        its group never changes the numbers of ROIs in other groups. Moving
        one place up is ``target_index = position - 1``, down is
        ``position + 1``, where position is the ROI's index in the scope list
        sorted by id."""
        slots = sorted(self._rois) if scope_ids is None else sorted({int(roi_id) for roi_id in scope_ids})
        unknown = [roi_id for roi_id in slots if roi_id not in self._rois]
        if unknown:
            raise KeyError(f"no ROI with id {unknown}")
        moved = sorted({int(roi_id) for roi_id in roi_ids})
        outside = [roi_id for roi_id in moved if roi_id not in slots]
        if outside:
            raise ValueError(f"ROI ids {outside} are not in the list being reordered")
        new_scope_order = move_block(slots, moved, target_index)
        placement = renumbering(new_scope_order, slots)
        self._renumber({roi_id: placement.get(roi_id, roi_id) for roi_id in sorted(self._rois)}, "Move ROIs")

    @instrumented("RoiToolbox.remap_all")
    def remap_all(
        self,
        remap_point: Callable[[float, float], tuple[float, float]],
        remap_mask: Callable[[RoiMask], RoiMask | None],
    ) -> RoiRemapReport:
        """Move every ROI's stored position - and, for a freeform-mask-
        geometry side, its raster - to match a rotation/flip/crop edit that
        just happened. Closes `docs/image_tools_coordinate_spaces.md`'s
        "known gap" (ROI positions silently going stale after a geometry
        edit) instead of leaving it for the maintainer to work around by
        hand.

        Called by `RoiGeometrySync` (app-level wiring) whenever a geometry
        edit actually changes rotation/flip/crop - never by this module or
        `GeometryModule` reaching into the other directly. `remap_point`/
        `remap_mask` are supplied ready-made by the caller, the same one-
        directional convention `display_position()`'s `affine_matrix`
        parameter already uses for Chromatic: this module has no import of
        `image_tools.geometry` and does not need to know a raw image shape
        or a before/after `GeometrySettings` exists.

        **Dispatches per ROI side by geometry type**
        (`sample_geometry_type`/`reference_geometry_type`) via
        `_remap_roi_shape` - not a circle-only special case with everything
        else bolted on:
        - `"circle"`/`"annulus"`: only the center moves (radius/diameter
          fields are invariant - this pipeline never scales).
        - `"mask"`: the stored raster is re-warped via `remap_mask`
          (`roi.rasterize.remap_roi_mask`). In the near-unreachable case that
          warping leaves no area at all (see that function's docstring for
          why this pipeline essentially never does), that one side is left
          at its **old** mask and position rather than silently replaced
          with an empty one, and reported in `mask_shapes_lost` - an empty
          mask would read as "nothing excluded here", not "this needs
          attention".
        - `"rectangle"`/`"polygon"`: not a shape any ROI can have yet (no
          such ROI editor exists in the rewrite - `roi_system_roadmap.md`),
          so there is nothing to remap; reported in
          `unsupported_shapes_skipped` so this is visible now rather than a
          silent gap discovered only once those shapes exist.

        Every ROI's center is also checked against `per_wavelength` - a
        manual per-wavelength position override is stored in the exact same
        processed-space convention as `center_x`/`center_y` (see that
        field's own docstring) and would otherwise go stale exactly like the
        center itself.

        One undo entry for the whole batch (`detect_rois`'s pattern): a
        rotate/flip/crop edit and the ROI shift it causes are one user
        gesture, not two - the caller (`RoiGeometrySync`, wired inside the
        same `undo_manager.begin_batch()`/`end_batch()` window as the
        geometry edit itself) relies on this being exactly one push."""
        old_positions: dict[int, tuple[float, float, dict | None, RoiMask | None, RoiMask | None]] = {}
        new_positions: dict[int, tuple[float, float, dict | None, RoiMask | None, RoiMask | None]] = {}
        mask_shapes_remapped = 0
        mask_shapes_lost: list[int] = []
        unsupported_shapes_skipped: list[int] = []

        for roi_id, roi in self._rois.items():
            old_positions[roi_id] = (
                roi.center_x,
                roi.center_y,
                None if roi.per_wavelength is None else dict(roi.per_wavelength),
                roi.sample_mask,
                roi.reference_mask,
            )
            new_center = remap_point(roi.center_x, roi.center_y)
            new_per_wavelength = (
                None
                if roi.per_wavelength is None
                else {key: remap_point(*value) for key, value in roi.per_wavelength.items()}
            )
            new_sample_mask, sample_lost, sample_unsupported = _remap_roi_shape(
                roi, "sample", remap_mask
            )
            new_reference_mask, reference_lost, reference_unsupported = _remap_roi_shape(
                roi, "reference", remap_mask
            )
            if sample_lost or reference_lost:
                mask_shapes_lost.append(roi_id)
            if sample_unsupported or reference_unsupported:
                unsupported_shapes_skipped.append(roi_id)
            mask_shapes_remapped += (
                roi.sample_geometry_type == "mask" and not sample_lost
            ) + (roi.reference_geometry_type == "mask" and not reference_lost)
            new_positions[roi_id] = (
                new_center[0], new_center[1], new_per_wavelength, new_sample_mask, new_reference_mask,
            )

        if not old_positions:
            return RoiRemapReport((), 0, (), ())

        def _write(values: dict[int, tuple[float, float, dict | None, RoiMask | None, RoiMask | None]]) -> None:
            for roi_id, (x, y, per_wavelength, sample_mask, reference_mask) in values.items():
                roi = self._rois[roi_id]
                roi.center_x, roi.center_y = x, y
                roi.per_wavelength = per_wavelength
                roi.sample_mask = sample_mask
                roi.reference_mask = reference_mask

        affected_ids = tuple(sorted(old_positions))

        def apply() -> None:
            _write(new_positions)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="remapped"))

        def revert() -> None:
            _write(old_positions)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="remapped"))

        apply()
        label = "Reposition ROI" if len(affected_ids) == 1 else f"Reposition {len(affected_ids)} ROIs"
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))
        return RoiRemapReport(
            remapped_roi_ids=affected_ids,
            mask_shapes_remapped=mask_shapes_remapped,
            mask_shapes_lost=tuple(mask_shapes_lost),
            unsupported_shapes_skipped=tuple(unsupported_shapes_skipped),
        )

    # -- command API (§7): groups -----------------------------------------

    # -- command API (§7): groups and colours -------------------------------
    #
    # A group has a base colour; a ROI that joins it is given a tint of that
    # colour, stored on the ROI (`roi/palette.py`). Every command here is one
    # undo step. Undo/redo restore the groups' fields onto the *same* group
    # objects (never copies), so older undo entries that hold a group object
    # stay valid when a later one is undone or redone.

    def _require_group(self, group_id: str) -> AreaRoiGroup:
        try:
            return self._groups[group_id]
        except KeyError:
            raise KeyError(f"no group with id {group_id!r}") from None

    def _capture_grouping(self, rois: Sequence[AreaRoi]) -> tuple:
        groups = [_snapshot(group) for group in self._groups.values()]
        return groups, [(roi, roi.sample_color_hex) for roi in rois]

    def _restore_grouping(self, state: tuple) -> None:
        groups, colors = state
        for snapshot in groups:
            _restore(snapshot)
        self._groups = {snapshot[0].group_id: snapshot[0] for snapshot in groups}
        for roi, color in colors:
            roi.sample_color_hex = color

    def _grouping_command(self, label: str, rois: Sequence[AreaRoi], reason: str, mutate: Callable[[], None]) -> None:
        """Run ``mutate`` and record the change to the groups, and to the
        stored colour of ``rois``, as one undo step. Nothing is recorded if
        it changed nothing."""
        before = self._capture_grouping(rois)
        mutate()
        after = self._capture_grouping(rois)
        if [saved for _obj, saved in before[0]] == [saved for _obj, saved in after[0]] and before[1] == after[1]:
            return

        def emit() -> None:
            ids = tuple(sorted(roi.area_roi_id for roi in rois))
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=ids, reason=reason))

        def undo() -> None:
            self._restore_grouping(before)
            emit()

        def redo() -> None:
            self._restore_grouping(after)
            emit()

        emit()
        undo_manager.push(FunctionCommand(label, undo_fn=undo, redo_fn=redo))

    def _join_group(self, group: AreaRoiGroup, rois: Sequence[AreaRoi]) -> None:
        """Move ``rois`` into ``group`` (out of whatever group they were in),
        giving each newcomer the first tint of the group's colour that no
        member shows yet. A group emptied by the move is removed; a group that
        was empty to begin with (created on purpose) is left alone."""
        members = set(group.area_roi_ids)
        joining = [roi for roi in rois if roi.area_roi_id not in members]
        if not joining:
            return
        joining_ids = {roi.area_roi_id for roi in joining}
        for other in list(self._groups.values()):
            if other is group or not (joining_ids & set(other.area_roi_ids)):
                continue
            other.area_roi_ids = [roi_id for roi_id in other.area_roi_ids if roi_id not in joining_ids]
            if not other.area_roi_ids:
                del self._groups[other.group_id]
        used = [self._rois[roi_id].sample_color_hex for roi_id in group.area_roi_ids]
        group.area_roi_ids = sorted(members | joining_ids)
        for roi in joining:
            roi.sample_color_hex = tint_color(group.sample_color_hex, first_free_tint_index(group.sample_color_hex, used))
            used.append(roi.sample_color_hex)

    def _new_group(self, name: str, sample_color_hex: str | None, reference_color_hex: str) -> AreaRoiGroup:
        name = name.strip()
        if not name:
            raise ValueError("a group needs a name")
        base = next_group_color(g.sample_color_hex for g in self._groups.values()) if sample_color_hex is None else normalize_hex(sample_color_hex)
        group = AreaRoiGroup(
            group_id=f"group_{next(self._group_id_counter)}",
            name=name,
            sample_color_hex=base,
            reference_color_hex=normalize_hex(reference_color_hex),
        )
        self._groups[group.group_id] = group
        return group

    @instrumented("RoiToolbox.create_group")
    def create_group(
        self,
        name: str,
        *,
        sample_color_hex: str | None = None,
        reference_color_hex: str = DEFAULT_REFERENCE_COLOR_HEX,
    ) -> str:
        """A new, empty group. ``sample_color_hex`` is its base colour;
        omitted, the next unused colour of `palette.GROUP_BASE_COLORS`."""
        created: list[str] = []
        self._grouping_command(
            "Create group", [], "regroup",
            lambda: created.append(self._new_group(name, sample_color_hex, reference_color_hex).group_id),
        )
        return created[0]

    @instrumented("RoiToolbox.group_rois")
    def group_rois(
        self,
        roi_ids: Collection[int],
        name: str,
        *,
        sample_color_hex: str | None = None,
        reference_color_hex: str = DEFAULT_REFERENCE_COLOR_HEX,
    ) -> str:
        """A new group holding ``roi_ids`` (taken out of any group they were
        in), each with its own tint: "group the selection" in one undo step."""
        rois = self._require_rois(roi_ids)
        created: list[str] = []

        def mutate() -> None:
            group = self._new_group(name, sample_color_hex, reference_color_hex)
            created.append(group.group_id)
            self._join_group(group, rois)

        self._grouping_command("Group ROIs", rois, "regroup", mutate)
        return created[0]

    @instrumented("RoiToolbox.add_rois_to_group")
    def add_rois_to_group(self, roi_ids: Collection[int], group_id: str) -> None:
        """Enforces "at most one group per ROI": each ROI leaves its current
        group (which is removed if that empties it) and takes the next free
        tint of ``group_id``'s colour. ROIs already in the group are left as
        they are."""
        group = self._require_group(group_id)
        rois = self._require_rois(roi_ids)
        self._grouping_command("Add ROIs to group", rois, "regroup", lambda: self._join_group(group, rois))

    @instrumented("RoiToolbox.add_to_group")
    def add_to_group(self, roi_id: int, group_id: str) -> None:
        """One-ROI form of `add_rois_to_group`."""
        self.add_rois_to_group((roi_id,), group_id)

    @instrumented("RoiToolbox.remove_rois_from_groups")
    def remove_rois_from_groups(self, roi_ids: Collection[int], *, group_id: str | None = None) -> None:
        """Take ``roi_ids`` out of their groups ("ungroup"), or, with
        ``group_id``, only out of that group. A group this empties is removed.
        A ROI that leaves a group loses its stored tint (back to the default
        colour)."""
        if group_id is not None:
            self._require_group(group_id)
        rois = self._require_rois(roi_ids)

        def mutate() -> None:
            ids = {roi.area_roi_id for roi in rois}
            ungrouped: set[int] = set()
            for group in list(self._groups.values()):
                if group_id is not None and group.group_id != group_id:
                    continue
                leaving = ids & set(group.area_roi_ids)
                if not leaving:
                    continue
                ungrouped |= leaving
                group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in leaving]
                if not group.area_roi_ids:
                    del self._groups[group.group_id]
            for roi in rois:
                if roi.area_roi_id in ungrouped:
                    roi.sample_color_hex = None

        self._grouping_command("Ungroup ROIs", rois, "regroup", mutate)

    @instrumented("RoiToolbox.remove_from_group")
    def remove_from_group(self, roi_id: int, group_id: str) -> None:
        """One-ROI form of `remove_rois_from_groups`, for one group."""
        self.remove_rois_from_groups((roi_id,), group_id=group_id)

    @instrumented("RoiToolbox.delete_group")
    def delete_group(self, group_id: str) -> None:
        """Remove a group; its ROIs become ungrouped (and lose their tint).
        The ROIs themselves are not deleted."""
        group = self._require_group(group_id)
        members = self._require_rois(group.area_roi_ids)

        def mutate() -> None:
            del self._groups[group_id]
            for roi in members:
                roi.sample_color_hex = None

        self._grouping_command("Delete group", members, "regroup", mutate)

    @instrumented("RoiToolbox.rename_group")
    def rename_group(self, group_id: str, name: str) -> None:
        group = self._require_group(group_id)
        name = name.strip()
        if not name:
            raise ValueError("a group needs a name")
        members = self._require_rois(group.area_roi_ids)
        self._grouping_command("Rename group", members, "relabel", lambda: setattr(group, "name", name))

    @instrumented("RoiToolbox.recolor_group")
    def recolor_group(
        self,
        group_id: str,
        sample_color_hex: str,
        reference_color_hex: str | None = None,
        *,
        repaint_members: bool = True,
    ) -> None:
        """Change the group's base colour. With ``repaint_members`` (the
        default) every member is given a fresh tint of the new colour, in
        order of id, **replacing any colour set on a member by hand**; without
        it only the group's own colour changes."""
        group = self._require_group(group_id)
        base = normalize_hex(sample_color_hex)
        reference = None if reference_color_hex is None else normalize_hex(reference_color_hex)
        members = self._require_rois(group.area_roi_ids)

        def mutate() -> None:
            group.sample_color_hex = base
            if reference is not None:
                group.reference_color_hex = reference
            if repaint_members:
                for index, roi in enumerate(members):
                    roi.sample_color_hex = tint_color(base, index)

        self._grouping_command("Recolor group", members, "recolor", mutate)

    @instrumented("RoiToolbox.set_roi_colors")
    def set_roi_colors(self, roi_ids: Collection[int], sample_color_hex: str | None) -> None:
        """Give every ROI in ``roi_ids`` this colour by hand, or, with
        ``None``, remove its stored colour (the default colour is used). Stays
        until the ROI joins a group or its group is recolored."""
        rois = self._require_rois(roi_ids)
        color = None if sample_color_hex is None else normalize_hex(sample_color_hex)

        def mutate() -> None:
            for roi in rois:
                roi.sample_color_hex = color

        self._grouping_command("Set ROI color" if len(rois) == 1 else "Set ROI colors", rois, "recolor", mutate)

    @instrumented("RoiToolbox.set_roi_label")
    def set_roi_label(self, roi_id: int, label: str | None) -> None:
        """The ROI's user-facing name; empty or ``None`` removes it."""
        (roi,) = self._require_rois((roi_id,))
        old, new = roi.label, ((label or "").strip() or None)
        if old == new:
            return

        def write(value: str | None) -> None:
            roi.label = value
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi.area_roi_id,), reason="relabel"))

        write(new)
        undo_manager.push(FunctionCommand("Rename ROI", undo_fn=lambda: write(old), redo_fn=lambda: write(new)))

    @instrumented("RoiToolbox.reorder_group")
    def reorder_group(self, group_id: str, new_index: int) -> None:
        """Reorders this module's own group display order (the Group
        table's row order) - unrelated to `AreaRoi.area_roi_id` numbering or
        the old app's spatial array-reordering feature (see module
        docstring)."""
        order = list(self._groups.keys())
        if group_id not in order:
            return
        old_order = list(order)
        order.remove(group_id)
        new_index = max(0, min(int(new_index), len(order)))
        order.insert(new_index, group_id)
        if order == old_order:
            return

        def write(ids: list[str]) -> None:
            self._groups = {gid: self._groups[gid] for gid in ids}
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

        write(order)
        undo_manager.push(
            FunctionCommand("Reorder group", undo_fn=lambda: write(old_order), redo_fn=lambda: write(order))
        )

    # -- request methods (other modules/panels ask; this module decides) ---

    def request_move(self, roi_id: int, x: float, y: float) -> None:
        """Called by the Image panel to forward a drag gesture - the Image
        panel never mutates ROI state directly (sketch §7). Just forwards to
        `move_roi`; image-bounds clamping is the Image panel's job (it has
        the image, this module doesn't) - the old app's equivalent
        (`_clamp_roi_position`) read `self._current_processed_image.shape`
        directly, state this module has no access to and shouldn't need."""
        self.move_roi(roi_id, x, y)
