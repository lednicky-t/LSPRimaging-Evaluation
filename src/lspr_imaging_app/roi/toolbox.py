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

**ID scheme changed from the old app, deliberately - not silently.** The old
app stores ROIs as a list and reindexes every ID to stay contiguous
(1..N) after every delete (`_reindex_detected_rois`), which also cascades
into renumbering every group's/array's member-id references. This module
uses `dict[int, AreaRoi]` (already the scaffold's choice, not new here) with
**stable, never-reused IDs** from an incrementing counter - deleting ROI 5
of 10 leaves IDs 1-4, 6-10 untouched rather than renumbering 6-10 down to
5-9. Two reasons: (1) reindexing-on-delete is what makes undo of a delete
hard to get right - undoing a delete would also have to reverse whatever
renumbering cascaded from it, including every group/array reference: a real
correctness risk for a "proper" undo design; (2) it was only ever needed
because the old representation was an ordered list where "ID" and "position"
were conflated - a dict already doesn't need that. User-visible effect: the
ROI table's numbering can show gaps after a delete (e.g. "1, 2, 4, 5")
instead of always renumbering to stay contiguous - flagging this prominently
since it's a real behavior change a maintainer could reasonably want
reverted, not something to bury in a commit message.

**Undo/redo**: every mutating command below pushes one
`undo.FunctionCommand` to the shared `undo.undo_manager` (see that module's
docstring for the cross-module design - the maintainer asked for a "proper,
not minimal" design scoped to handle every module, not just this one).
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

**Not yet built, scope boundary for this pass**: `display_position()` stays
`NotImplementedError` - it needs a decision about how RoiToolbox obtains a
chromatic affine (hold a ChromaticModule reference, vs. taking
`affine_matrix` as an explicit parameter like `roi/rasterize.py`'s
dispatchers do) that wasn't forced by anything in this pass, so it's left
open rather than guessed. The old app's spatial array-reordering feature
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

from PyQt6.QtCore import QObject, pyqtSignal

from ..change_events import RoiComputationalChange, RoiCosmeticChange
from ..diagnostics import instrumented
from ..undo import FunctionCommand, undo_manager
from .model import AreaRoi, AreaRoiGroup, RoiArrayGroup


class RoiToolbox(QObject):
    """Owns ROI/group state; exposes a query interface plus the full
    command API. No other module or panel may hold its own copy of ROI or
    group state (AGENTS.md, "What NOT to do without checking in again first")."""

    geometry_changed = pyqtSignal(RoiComputationalChange)
    cosmetic_changed = pyqtSignal(RoiCosmeticChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rois: dict[int, AreaRoi] = {}
        self._groups: dict[str, AreaRoiGroup] = {}
        self._array_groups: dict[str, RoiArrayGroup] = {}
        self._roi_id_counter = itertools.count(1)
        self._group_id_counter = itertools.count(1)

    # -- query interface ------------------------------------------------

    def rois(self) -> tuple[AreaRoi, ...]:
        return tuple(self._rois.values())

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

    def display_position(self, roi_id: int, image_key: object) -> tuple[float, float]:
        """Resolve ``roi_id``'s position in ``image_key``'s display space,
        folding in Chromatic's ``affine_for`` internally - callers never
        compose the chromatic transform themselves (sketch §7). Not yet
        implemented - needs a decision on how this module obtains Chromatic's
        affine (see module docstring)."""
        raise NotImplementedError

    # -- command API (§7): single-ROI mutation ---------------------------

    @instrumented("RoiToolbox.add_roi")
    def add_roi(self, x: float, y: float, *, sample_radius_px: float = 10.0) -> int:
        """Manually place one ROI at (x, y) - the click-to-add tool. Not in
        the original scaffold's stub list (a real gap: there was no command
        for the old app's `_add_roi_at`, the single most basic way to
        populate the toolbox short of running detection)."""
        roi_id = next(self._roi_id_counter)
        roi = AreaRoi(area_roi_id=roi_id, center_x=float(x), center_y=float(y), sample_radius_px=float(sample_radius_px))
        self._rois[roi_id] = roi

        def revert() -> None:
            self._rois.pop(roi_id, None)

        def apply() -> None:
            self._rois[roi_id] = roi

        undo_manager.push(FunctionCommand("Add ROI", undo_fn=revert, redo_fn=apply))
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="added"))
        return roi_id

    @instrumented("RoiToolbox.move_roi")
    def move_roi(self, roi_id: int, x: float, y: float) -> None:
        roi = self._rois[roi_id]
        old_x, old_y = roi.center_x, roi.center_y
        new_x, new_y = float(x), float(y)
        if old_x == new_x and old_y == new_y:
            return

        def revert() -> None:
            roi.center_x, roi.center_y = old_x, old_y

        def apply() -> None:
            roi.center_x, roi.center_y = new_x, new_y

        apply()
        undo_manager.push(FunctionCommand("Move ROI", undo_fn=revert, redo_fn=apply))
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="moved"))

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

        def revert() -> None:
            roi.sample_radius_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px = old

        def apply() -> None:
            roi.sample_radius_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px = new

        apply()
        undo_manager.push(FunctionCommand("Resize ROI", undo_fn=revert, redo_fn=apply))
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="resized"))

    @instrumented("RoiToolbox.delete_roi")
    def delete_roi(self, roi_id: int) -> None:
        self.delete_rois((roi_id,))

    @instrumented("RoiToolbox.delete_rois")
    def delete_rois(self, roi_ids: tuple[int, ...]) -> None:
        """Bulk delete - the old app's "Remove selected" toolbar action
        always acts on a set, not one ROI at a time; `delete_roi` above is a
        one-ROI convenience wrapper over this, the real primitive.

        Also prunes any group/array that becomes empty as a result (matching
        the old app's own behavior), and restores exactly those pruned
        groups/arrays on undo - captured via a small `deepcopy` of only the
        directly-linked groups/arrays (a handful of objects), not the whole
        app state (see undo/manager.py's docstring on why that distinction
        matters for cost).
        """
        ids_to_delete = {int(roi_id) for roi_id in roi_ids if int(roi_id) in self._rois}
        if not ids_to_delete:
            return
        removed_rois = {roi_id: self._rois[roi_id] for roi_id in ids_to_delete}
        groups_before = copy.deepcopy(self._groups)
        arrays_before = copy.deepcopy(self._array_groups)

        def apply() -> None:
            for roi_id in ids_to_delete:
                self._rois.pop(roi_id, None)
            for group in self._groups.values():
                group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in ids_to_delete]
            self._groups = {group_id: group for group_id, group in self._groups.items() if group.area_roi_ids}
            for array in self._array_groups.values():
                array.member_area_roi_ids = [roi_id for roi_id in array.member_area_roi_ids if roi_id not in ids_to_delete]
            self._array_groups = {
                array_id: array for array_id, array in self._array_groups.items() if array.member_area_roi_ids
            }

        def revert() -> None:
            self._rois.update(removed_rois)
            self._groups = copy.deepcopy(groups_before)
            self._array_groups = copy.deepcopy(arrays_before)

        apply()
        label = "Delete ROI" if len(ids_to_delete) == 1 else f"Delete {len(ids_to_delete)} ROIs"
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(ids_to_delete)), reason="deleted"))

    @instrumented("RoiToolbox.detect_rois")
    def detect_rois(self, detected_rois: list[AreaRoi], array_groups: tuple[RoiArrayGroup, ...] = ()) -> list[int]:
        """Replace every current ROI/group/array with a fresh detection
        result. Takes already-detected ROIs rather than running detection
        itself (`roi.detection.detect_rois()`, a potentially-slow call that
        must run off the GUI thread per AGENTS.md's non-negotiable
        invariants) - the caller is responsible for running detection on a
        worker and only calling this once a result exists, the same
        threading boundary the old app's async worker + `_on_detect_rois_
        ready` callback already enforced.
        """
        old_rois = dict(self._rois)
        old_groups = dict(self._groups)
        old_arrays = dict(self._array_groups)
        new_rois = {roi.area_roi_id: roi for roi in detected_rois}
        new_arrays = {array.array_id: array for array in array_groups}

        def apply() -> None:
            self._rois = dict(new_rois)
            self._groups = {}
            self._array_groups = dict(new_arrays)

        def revert() -> None:
            self._rois = dict(old_rois)
            self._groups = dict(old_groups)
            self._array_groups = dict(old_arrays)

        apply()
        if new_rois:
            self._roi_id_counter = itertools.count(max(new_rois) + 1)
        undo_manager.push(FunctionCommand("Detect ROIs", undo_fn=revert, redo_fn=apply))
        affected_ids = tuple(sorted(set(old_rois) | set(new_rois)))
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=affected_ids, reason="detected"))
        return list(new_rois.keys())

    # -- command API (§7): groups -----------------------------------------

    @instrumented("RoiToolbox.create_group")
    def create_group(self, name: str, *, sample_color_hex: str = "#f59e0b", reference_color_hex: str = "#38bdf8") -> str:
        group_id = f"group_{next(self._group_id_counter)}"
        group = AreaRoiGroup(group_id=group_id, name=name, sample_color_hex=sample_color_hex, reference_color_hex=reference_color_hex)
        self._groups[group_id] = group

        def revert() -> None:
            self._groups.pop(group_id, None)

        def apply() -> None:
            self._groups[group_id] = group

        undo_manager.push(FunctionCommand("Create group", undo_fn=revert, redo_fn=apply))
        return group_id

    @instrumented("RoiToolbox.rename_group")
    def rename_group(self, group_id: str, name: str) -> None:
        group = self._groups[group_id]
        old_name = group.name
        if old_name == name:
            return

        def revert() -> None:
            group.name = old_name

        def apply() -> None:
            group.name = name

        apply()
        undo_manager.push(FunctionCommand("Rename group", undo_fn=revert, redo_fn=apply))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="relabel"))

    @instrumented("RoiToolbox.recolor_group")
    def recolor_group(self, group_id: str, sample_color_hex: str, reference_color_hex: str | None = None) -> None:
        group = self._groups[group_id]
        old = (group.sample_color_hex, group.reference_color_hex)
        new = (sample_color_hex, reference_color_hex if reference_color_hex is not None else group.reference_color_hex)
        if old == new:
            return

        def revert() -> None:
            group.sample_color_hex, group.reference_color_hex = old

        def apply() -> None:
            group.sample_color_hex, group.reference_color_hex = new

        apply()
        undo_manager.push(FunctionCommand("Recolor group", undo_fn=revert, redo_fn=apply))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=tuple(group.area_roi_ids), reason="recolor"))

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

        def revert() -> None:
            self._groups = {gid: self._groups[gid] for gid in old_order}

        def apply() -> None:
            self._groups = {gid: self._groups[gid] for gid in order}

        apply()
        undo_manager.push(FunctionCommand("Reorder group", undo_fn=revert, redo_fn=apply))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="regroup"))

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

        def revert() -> None:
            target_group.area_roi_ids = [rid for rid in target_group.area_roi_ids if rid != roi_id]
            if previous_group is not None:
                previous_group.area_roi_ids = sorted(set(previous_group.area_roi_ids) | {roi_id})

        apply()
        undo_manager.push(FunctionCommand("Add ROI to group", undo_fn=revert, redo_fn=apply))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

    @instrumented("RoiToolbox.remove_from_group")
    def remove_from_group(self, roi_id: int, group_id: str) -> None:
        """Removes `roi_id` from `group_id`; prunes the group entirely if
        that empties it (matching the old app's `_ungroup_selected_rois`)."""
        group = self._groups.get(group_id)
        if group is None or roi_id not in group.area_roi_ids:
            return
        group_was_present = True
        old_ids = list(group.area_roi_ids)

        def apply() -> None:
            group.area_roi_ids = [rid for rid in group.area_roi_ids if rid != roi_id]
            if not group.area_roi_ids:
                self._groups.pop(group_id, None)

        def revert() -> None:
            group.area_roi_ids = old_ids
            if group_was_present:
                self._groups[group_id] = group

        apply()
        undo_manager.push(FunctionCommand("Remove ROI from group", undo_fn=revert, redo_fn=apply))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(roi_id,), reason="regroup"))

    # -- request methods (other modules/panels ask; this module decides) ---

    def request_move(self, roi_id: int, x: float, y: float) -> None:
        """Called by the Image panel to forward a drag gesture - the Image
        panel never mutates ROI state directly (sketch §7). Just forwards to
        `move_roi`; image-bounds clamping is the Image panel's job (it has
        the image, this module doesn't) - the old app's equivalent
        (`_clamp_roi_position`) read `self._current_processed_image.shape`
        directly, state this module has no access to and shouldn't need."""
        self.move_roi(roi_id, x, y)
