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
`app_rewrite.build_main_window()`) - built 2026-09-20. `analysis/tasks.py`
is still not built and will need its own handler for per-ROI provenance
when it lands.

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
does implement, for *group* display order). Geometry-type switching
(circle/annulus <-> mask, with mask-drawing) isn't built - no mask-editing
UI exists yet in the rewrite to drive it.
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..change_events import RoiComputationalChange, RoiCosmeticChange
from ..diagnostics import instrumented
from ..image_tools.chromatic.affine import apply_affine_to_points
from ..undo import FunctionCommand, undo_manager
from .model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup, RoiArrayGroup


class RoiToolbox(QObject):
    """Owns ROI/group state; exposes a query interface plus the full
    command API. No other module or panel may hold its own copy of ROI or
    group state (AGENTS.md, "What NOT to do without checking in again first")."""

    geometry_changed = pyqtSignal(RoiComputationalChange)
    cosmetic_changed = pyqtSignal(RoiCosmeticChange)
    # {old_id: new_id} for every ROI renumbered by a delete - see module
    # docstring, "Cross-module consequence". Not itself a Cosmetic/
    # Computational change (it doesn't mean anything was recomputed or needs
    # redrawing on its own) - a separate, narrower signal for "if you hold a
    # roi_id, it may now be wrong".
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
        `reference_inner/outer_radius_px` (its default reference-ring radii
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
    def add_roi(self, x: float, y: float, *, sample_radius_px: float = 10.0) -> int:
        """Manually place one ROI at (x, y) - the click-to-add tool. Not in
        the original scaffold's stub list (a real gap: there was no command
        for the old app's `_add_roi_at`, the single most basic way to
        populate the toolbox short of running detection). IDs are already
        contiguous-by-construction here (the counter always hands out
        `len(rois) + 1` as long as `delete_rois` keeps its own renumbering
        promise), so no special undo bookkeeping beyond add/remove is
        needed."""
        roi_id = next(self._roi_id_counter)
        roi = AreaRoi(area_roi_id=roi_id, center_x=float(x), center_y=float(y), sample_radius_px=float(sample_radius_px))

        def apply() -> None:
            self._rois[roi_id] = roi
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="added"))

        def revert() -> None:
            self._rois.pop(roi_id, None)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="deleted"))

        apply()
        undo_manager.push(FunctionCommand("Add ROI", undo_fn=revert, redo_fn=apply))
        return roi_id

    @instrumented("RoiToolbox.move_roi")
    def move_roi(self, roi_id: int, x: float, y: float) -> None:
        roi = self._rois[roi_id]
        old_x, old_y = roi.center_x, roi.center_y
        new_x, new_y = float(x), float(y)
        if old_x == new_x and old_y == new_y:
            return

        def apply() -> None:
            roi.center_x, roi.center_y = new_x, new_y
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="moved"))

        def revert() -> None:
            roi.center_x, roi.center_y = old_x, old_y
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="moved"))

        apply()
        undo_manager.push(FunctionCommand("Move ROI", undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.resize_roi")
    def resize_roi(
        self,
        roi_id: int,
        *,
        sample_radius_px: float | None = None,
        reference_inner_diameter_px: float | None = None,
        reference_outer_diameter_px: float | None = None,
    ) -> None:
        """Set one or more of an ROI's sample radius / reference diameters.
        Any field left `None` is unchanged. A no-op (no undo entry pushed)
        if every given value already matches."""
        roi = self._rois[roi_id]
        old = (roi.sample_radius_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px)
        new = (
            float(sample_radius_px) if sample_radius_px is not None else roi.sample_radius_px,
            float(reference_inner_diameter_px) if reference_inner_diameter_px is not None else roi.reference_inner_diameter_px,
            float(reference_outer_diameter_px) if reference_outer_diameter_px is not None else roi.reference_outer_diameter_px,
        )
        if old == new:
            return

        def apply() -> None:
            roi.sample_radius_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px = new
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="resized"))

        def revert() -> None:
            roi.sample_radius_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px = old
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="resized"))

        apply()
        undo_manager.push(FunctionCommand("Resize ROI", undo_fn=revert, redo_fn=apply))

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
        groups_before = copy.deepcopy(self._groups)
        arrays_before = copy.deepcopy(self._array_groups)
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
            if id_map:
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
            self._groups = copy.deepcopy(groups_before)
            self._array_groups = copy.deepcopy(arrays_before)
            self._roi_id_counter = itertools.count(next_counter_before_delete)
            if reverse_id_map:
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
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="detected"))

        def revert() -> None:
            self._rois = dict(old_rois)
            self._groups = dict(old_groups)
            self._array_groups = dict(old_arrays)
            self._roi_id_counter = itertools.count(old_next_counter)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="detected"))

        apply()
        undo_manager.push(FunctionCommand("Detect ROIs", undo_fn=revert, redo_fn=apply))
        return list(new_rois.keys())

    # -- command API (§7): groups -----------------------------------------

    @instrumented("RoiToolbox.create_group")
    def create_group(self, name: str, *, sample_color_hex: str = "#f59e0b", reference_color_hex: str = "#38bdf8") -> str:
        group_id = f"group_{next(self._group_id_counter)}"
        group = AreaRoiGroup(group_id=group_id, name=name, sample_color_hex=sample_color_hex, reference_color_hex=reference_color_hex)

        def apply() -> None:
            self._groups[group_id] = group
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

        def revert() -> None:
            self._groups.pop(group_id, None)
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

        apply()
        undo_manager.push(FunctionCommand("Create group", undo_fn=revert, redo_fn=apply))
        return group_id

    @instrumented("RoiToolbox.rename_group")
    def rename_group(self, group_id: str, name: str) -> None:
        group = self._groups[group_id]
        old_name = group.name
        if old_name == name:
            return

        def apply() -> None:
            group.name = name
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="relabel"))

        def revert() -> None:
            group.name = old_name
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="relabel"))

        apply()
        undo_manager.push(FunctionCommand("Rename group", undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.recolor_group")
    def recolor_group(self, group_id: str, sample_color_hex: str, reference_color_hex: str | None = None) -> None:
        group = self._groups[group_id]
        old = (group.sample_color_hex, group.reference_color_hex)
        new = (sample_color_hex, reference_color_hex if reference_color_hex is not None else group.reference_color_hex)
        if old == new:
            return

        def apply() -> None:
            group.sample_color_hex, group.reference_color_hex = new
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="recolor"))

        def revert() -> None:
            group.sample_color_hex, group.reference_color_hex = old
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="recolor"))

        apply()
        undo_manager.push(FunctionCommand("Recolor group", undo_fn=revert, redo_fn=apply))

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

        def apply() -> None:
            self._groups = {gid: self._groups[gid] for gid in order}
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

        def revert() -> None:
            self._groups = {gid: self._groups[gid] for gid in old_order}
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

        apply()
        undo_manager.push(FunctionCommand("Reorder group", undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.add_to_group")
    def add_to_group(self, roi_id: int, group_id: str) -> None:
        """Enforces "at most one group per ROI": removes `roi_id` from
        whichever other group it currently belongs to (if any) before
        adding it to `group_id`."""
        if roi_id not in self._rois or group_id not in self._groups:
            return
        previous_group = self.group_for_roi(roi_id)
        if previous_group is not None and previous_group.group_id == group_id:
            return
        target_group = self._groups[group_id]

        def apply() -> None:
            if previous_group is not None:
                previous_group.area_roi_ids = [rid for rid in previous_group.area_roi_ids if rid != roi_id]
            if roi_id not in target_group.area_roi_ids:
                target_group.area_roi_ids = sorted(set(target_group.area_roi_ids) | {roi_id})
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

        def revert() -> None:
            target_group.area_roi_ids = [rid for rid in target_group.area_roi_ids if rid != roi_id]
            if previous_group is not None:
                previous_group.area_roi_ids = sorted(set(previous_group.area_roi_ids) | {roi_id})
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

        apply()
        undo_manager.push(FunctionCommand("Add ROI to group", undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.remove_from_group")
    def remove_from_group(self, roi_id: int, group_id: str) -> None:
        """Removes `roi_id` from `group_id`; prunes the group entirely if
        that empties it (matching the old app's `_ungroup_selected_rois`)."""
        group = self._groups.get(group_id)
        if group is None or roi_id not in group.area_roi_ids:
            return
        old_ids = list(group.area_roi_ids)

        def apply() -> None:
            group.area_roi_ids = [rid for rid in group.area_roi_ids if rid != roi_id]
            if not group.area_roi_ids:
                self._groups.pop(group_id, None)
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

        def revert() -> None:
            group.area_roi_ids = old_ids
            self._groups[group_id] = group
            self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

        apply()
        undo_manager.push(FunctionCommand("Remove ROI from group", undo_fn=revert, redo_fn=apply))

    # -- request methods (other modules/panels ask; this module decides) ---

    def request_move(self, roi_id: int, x: float, y: float) -> None:
        """Called by the Image panel to forward a drag gesture - the Image
        panel never mutates ROI state directly (sketch §7). Just forwards to
        `move_roi`; image-bounds clamping is the Image panel's job (it has
        the image, this module doesn't) - the old app's equivalent
        (`_clamp_roi_position`) read `self._current_processed_image.shape`
        directly, state this module has no access to and shouldn't need."""
        self.move_roi(roi_id, x, y)
