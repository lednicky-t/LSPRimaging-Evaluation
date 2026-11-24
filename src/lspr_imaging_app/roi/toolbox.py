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
without this module reaching into theirs (CLAUDE.md's "no module reads or
writes another module's internals" rule). **`SelectionModule` now
subscribes** (`selection/module.py`'s `remap_roi_ids()`, wired in
`app_rewrite.build_main_window()`) - built 2026-09-20. (The former
`AnalysisEngine` subscribed too, because its stored results were keyed by
roi_id; it was removed on 2026-10-29.) The signal's map covers every ROI that
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
CLAUDE.md's "selection changes must never implicitly trigger computation"
spirit - undo history is for state that affects results, not where the
cursor/selection happens to be.

**Selection ownership fixed, not duplicated**: the original scaffold stub
had both a `set_selection`/`selection_changed` pair here *and* on
`SelectionModule` - CLAUDE.md is explicit that `selection/` is "the one
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
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..applog.events import emit_timing
from ..change_events import RoiComputationalChange, RoiCosmeticChange, RoiLinkChange
from ..diagnostics import instrumented
from ..image_tools.chromatic.affine import apply_affine_to_points, identity_affine_matrix
from ..undo import FunctionCommand, undo_manager
from ..domain.roi_model import (
    GEOMETRY_SCOPES,
    SCOPE_INDIVIDUAL,
    SCOPE_PERSISTENT,
    AreaRoi,
    AreaRoiDetectionSettings,
    AreaRoiGroup,
    LayoutPoint,
    RoiArrayGroup,
    RoiGeometry,
    RoiMask,
    RoiTimeline,
    base_geometry,
    geometry_at,
    resolved_at,
)
from ..domain.roi_shapes import (
    Border,
    Circle,
    MaskShape,
    Rectangle,
    Shape,
    border_switched_off,
    border_switched_on,
    effective_border,
    remap_rectangle,
    ring_diameters,
    shifted_mask,
    whole_pixels,
)
from ..domain.roi_links import (
    ROI_ROLES,
    ROLE_REFERENCE,
    ROLE_SAMPLE,
    cleaned_links,
    is_reference,
    link_refusal,
    normalized_ids,
    reference_source,
    samples_linked_to,
)
from .mask_regions import mask_regions
from .ordering import move_block, renumbering
from .rasterize import rasterize_reference, rasterize_sample
from .palette import first_free_tint_index, gradient_tints, next_group_color, normalize_hex, tint_color

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


"""Geometry types whose only remapped field is the ROI center - radius/
diameter fields are invariant under this app's transform pipeline (rotate,
flip, crop - never a scale change), so there is nothing else to do for them."""


POSITION_DECIMALS = 1
"""ROI centres are always stored to 0.1 px. (Until 2026-10-19 this was an Options-menu preference, on by default;
the preference was removed, the rounding stayed.)"""


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
    remap_point: Callable[[float, float], tuple[float, float]],
    remap_mask: Callable[[RoiMask], RoiMask | None],
) -> tuple[Shape, bool]:
    """One ROI's shape through `RoiToolbox.remap_all`'s per-shape dispatch. Returns `(new_shape, lost)`:
    a circle is unchanged (only its centre moves; this pipeline never scales), a rectangle keeps its size and gets the angle its own
    x axis now points in (`roi_shapes.remap_rectangle`), a mask is re-warped by `remap_mask`. `lost` is True only when a mask warped to
    zero area: `new_shape` is then the *old* shape, deliberately unchanged (an empty mask would read as "nothing here", not "this needs
    attention"), and the caller reports it."""
    shape = roi.shape
    if isinstance(shape, Rectangle):
        return remap_rectangle(shape, roi.center_x, roi.center_y, remap_point), False
    if isinstance(shape, MaskShape):
        remapped = remap_mask(shape.mask)
        if remapped is None:
            logger.warning("ROI %d's mask warped to zero area on a geometry edit - left at its old position.", roi.area_roi_id)
            return shape, True
        return MaskShape(remapped), False
    return shape, False


class RoiToolbox(QObject):
    """Owns ROI/group state; exposes a query interface plus the full
    command API. No other module or panel may hold its own copy of ROI or
    group state (CLAUDE.md, "What NOT to do without checking in again first")."""

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
    # A sample's set of linked reference ROIs changed (`RoiLinkChange`): derived numbers must be rebuilt, nothing stored is stale.
    links_changed = pyqtSignal(RoiLinkChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rois: dict[int, AreaRoi] = {}
        self._groups: dict[str, AreaRoiGroup] = {}
        self._array_groups: dict[str, RoiArrayGroup] = {}
        self._detection_settings = AreaRoiDetectionSettings()
        self._roi_id_counter = itertools.count(1)
        self._group_id_counter = itertools.count(1)

    @staticmethod
    def _rounded(value: float) -> float:
        """A ROI centre coordinate as stored: rounded to `POSITION_DECIMALS` (0.1 px)."""
        return round(float(value), POSITION_DECIMALS)

    # -- query interface ------------------------------------------------

    def rois(self) -> tuple[AreaRoi, ...]:
        return tuple(self._rois.values())

    def detection_settings(self) -> AreaRoiDetectionSettings:
        """The shared ROI detection/reduction settings - a defensive copy,
        same guarantee `GeometryModule.settings()` makes.

        **Ownership decided 2026-09-23, while wiring the former `AnalysisEngine`**:
        nothing owned `AreaRoiDetectionSettings` anywhere in the rewrite -
        every module took it as a function *parameter* (`roi/detection.py`,
        `image_tools/background/estimate.py`, `image_tools/preprocess.py`,
        `image_tools/chromatic/landmark_autotrack.py`, removed 2026-10-29), so there was no
        module to read it from when the engine needed
        the default reference ring (since 2026-11-18 `default_border()`: its gap and
        thickness for ROIs that have no border of their own) and `reduction_method`. Put here
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
        g = geometry_at(roi, image_key[0])  # the geometry valid on this cube
        point = np.asarray([[g.center_x, g.center_y]], dtype=np.float64)
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
        cube = image_key[0]
        centers = np.asarray(
            [(g.center_x, g.center_y) for g in (geometry_at(roi, cube) for roi in rois)], dtype=np.float64
        )
        positions = apply_affine_to_points(centers, affine_matrix)
        for index, roi in enumerate(rois):
            if roi.per_wavelength:
                nudge = roi.per_wavelength.get(image_key)
                if nudge is not None:
                    positions[index] = (float(nudge[0]), float(nudge[1]))
        return positions

    # -- session restore ------------------------------------------------

    @instrumented("RoiToolbox.restore_state")
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
        for problem in cleaned_links(rois):  # a hand-edited or inconsistent file: repaired and said, never silently
            logger.warning("Session ROI links repaired: %s", problem)
        self._rois = {int(roi.area_roi_id): roi for roi in rois}
        self._groups = {str(group.group_id): group for group in groups}
        self._array_groups = {str(array_group.array_id): array_group for array_group in arrays}
        self._roi_id_counter = itertools.count(max(self._rois, default=0) + 1)
        self._group_id_counter = itertools.count(_highest_group_number(self._groups) + 1)
        self.geometry_changed.emit(RoiComputationalChange(roi_ids=(), reason="session_restored"))
        self.cosmetic_changed.emit(RoiCosmeticChange(roi_ids=(), reason="session_restored"))
        self.links_changed.emit(RoiLinkChange(sample_ids=(), reason="session_restored"))

    # -- sample / reference roles and links (domain/roi_links.py) ---------
    #
    # Every command is one undo step, validates before changing anything, and holds the ROI *objects* in its closures (so
    # it stays correct whatever renumbering happens around it). Roles change which pixels a scan reads for a ROI (a reference
    # region leaves out every sample's pixels), so a role change is a `RoiComputationalChange`; a link change is only a
    # `RoiLinkChange` (nothing stored goes stale).

    def references_of(self, sample_id: int) -> tuple[int, ...]:
        """The reference ROIs ``sample_id`` is linked to, ascending (empty: it uses its built-in reference, if any)."""
        return tuple(self._rois[int(sample_id)].reference_roi_ids)

    def samples_of(self, reference_id: int) -> tuple[int, ...]:
        """The sample ROIs linked to ``reference_id``, ascending."""
        return samples_linked_to(self._rois.values(), reference_id)

    def reference_source(self, roi_id: int) -> str:
        """``"linked"`` / ``"builtin"`` / ``"none"`` / ``"is_reference"`` (see `domain.roi_links.reference_source`)."""
        return reference_source(self._rois[int(roi_id)])

    def sample_ids(self, roi_ids: Iterable[int]) -> tuple[int, ...]:
        """``roi_ids`` without the reference ROIs, in the order given. Only a sample is an analysis unit (Spectra, Sensorgram, step table, groups, Signal
        map, S_bulk); a reference ROI only serves the samples linked to it. An id this toolbox does not know is kept, so the callers' own "not in the scan" paths
        behave as they always did."""
        return tuple(i for i in (int(r) for r in roi_ids) if i not in self._rois or not is_reference(self._rois[i]))

    def linked_sample_ids(self, roi_ids: Iterable[int]) -> tuple[int, ...]:
        """The ids among ``roi_ids`` of sample ROIs that have linked reference ROIs, in the order given (position tuning asks: it does not follow links yet)."""
        return tuple(
            i for i in (int(r) for r in roi_ids) if i in self._rois and not is_reference(self._rois[i]) and self._rois[i].reference_roi_ids
        )

    def _apply_link_state(self, states: list[tuple[AreaRoi, str, tuple[int, ...]]]) -> None:
        for roi, role, links in states:
            roi.role = role
            roi.reference_roi_ids = links

    def _push_link_edit(
        self,
        label: str,
        before: list[tuple[AreaRoi, str, tuple[int, ...]]],
        after: list[tuple[AreaRoi, str, tuple[int, ...]]],
        *,
        role_changed: tuple[AreaRoi, ...],
        reason: str,
    ) -> None:
        """Apply ``after`` (``(roi, role, links)`` per touched ROI) as one undo step; ``before`` is what undo puts back."""
        changed_links = tuple(roi for (roi, _, old), (_, _, new) in zip(before, after) if old != new)

        def emit() -> None:
            if role_changed:
                self.geometry_changed.emit(
                    RoiComputationalChange(roi_ids=tuple(sorted(roi.area_roi_id for roi in role_changed)), reason="role_changed")
                )
            samples = {roi.area_roi_id for roi in changed_links} | {
                roi.area_roi_id for roi in role_changed if roi.role != ROLE_REFERENCE
            }
            if samples:  # never empty here: an empty tuple means "every sample" (session restore)
                self.links_changed.emit(RoiLinkChange(sample_ids=tuple(sorted(samples)), reason=reason))

        def apply() -> None:
            self._apply_link_state(after)
            emit()

        def revert() -> None:
            self._apply_link_state(before)
            emit()

        apply()
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.set_roi_role")
    def set_roi_role(self, roi_ids: Collection[int], role: str) -> None:
        """Make the ROIs ``role`` (``"sample"`` or ``"reference"``), one undo step.

        Making a ROI a reference drops its own links (a reference has none). Making a reference a sample takes it out of every
        sample's links (those samples fall back to their built-in reference, or have none: shown as missing). The ROI's ring
        diameters are never touched, so switching back restores exactly what it was. ROIs already in ``role`` are left alone;
        nothing to change pushes no undo step."""
        if role not in ROI_ROLES:
            raise ValueError(f"role must be one of {ROI_ROLES}, got {role!r}")
        targets = [roi for roi in self._require_rois(roi_ids) if roi.role != role]
        if not targets:
            return
        target_ids = {roi.area_roi_id for roi in targets}
        touched: dict[int, tuple[AreaRoi, str, tuple[int, ...]]] = {}
        for roi in targets:
            touched[roi.area_roi_id] = (roi, role, () if role == ROLE_REFERENCE else roi.reference_roi_ids)
        if role != ROLE_REFERENCE:  # former references leave every sample's links
            for roi in self._rois.values():
                if roi.area_roi_id in target_ids or not (target_ids & set(roi.reference_roi_ids)):
                    continue
                touched[roi.area_roi_id] = (roi, roi.role, tuple(i for i in roi.reference_roi_ids if i not in target_ids))
        before = [(roi, roi.role, roi.reference_roi_ids) for roi, _, _ in touched.values()]
        after = list(touched.values())
        noun = "reference" if role == ROLE_REFERENCE else "sample"
        label = f"Make ROI {noun}" if len(targets) == 1 else f"Make {len(targets)} ROIs {noun}s"
        self._push_link_edit(label, before, after, role_changed=tuple(targets), reason="role_changed")

    @instrumented("RoiToolbox.link_references")
    def link_references(self, sample_ids: Collection[int], reference_ids: Collection[int]) -> None:
        """Link every sample in ``sample_ids`` to every reference in ``reference_ids`` (added to the links each sample already
        has), one undo step. Refused with `ValueError` (message for the status line, `domain.roi_links.link_refusal`) when a
        side is empty, an id is unknown, or a ROI has the wrong role; nothing changes then."""
        refusal = link_refusal(self._rois, sample_ids, reference_ids)
        if refusal:
            raise ValueError(refusal)
        new_refs = set(normalized_ids(reference_ids))
        samples = [self._rois[i] for i in normalized_ids(sample_ids)]
        before = [(roi, roi.role, roi.reference_roi_ids) for roi in samples]
        after = [(roi, roi.role, normalized_ids({*roi.reference_roi_ids, *new_refs})) for roi in samples]
        if all(old == new for (_, _, old), (_, _, new) in zip(before, after)):
            return
        label = "Link ROI to reference" if len(samples) == 1 else f"Link {len(samples)} ROIs to references"
        self._push_link_edit(label, before, after, role_changed=(), reason="linked")

    @instrumented("RoiToolbox.unlink_references")
    def unlink_references(self, sample_ids: Collection[int], reference_ids: Collection[int] | None = None) -> None:
        """Remove the links of ``sample_ids`` to ``reference_ids`` (``None``: all their links), one undo step. A sample left
        without links uses its built-in reference again, or has none (shown as missing). Nothing to remove pushes no step."""
        samples = self._require_rois(sample_ids)
        drop = None if reference_ids is None else set(normalized_ids(reference_ids))
        before = [(roi, roi.role, roi.reference_roi_ids) for roi in samples]
        after = [
            (roi, roi.role, () if drop is None else tuple(i for i in roi.reference_roi_ids if i not in drop)) for roi in samples
        ]
        if all(old == new for (_, _, old), (_, _, new) in zip(before, after)):
            return
        label = "Unlink ROI" if len(samples) == 1 else f"Unlink {len(samples)} ROIs"
        self._push_link_edit(label, before, after, role_changed=(), reason="unlinked")

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
        """Manually place one ROI at (x, y) - the right-click menu's Add ROI > Circle. Not in
        the original scaffold's stub list (a real gap: there was no command
        for the old app's `_add_roi_at`, the single most basic way to
        populate the toolbox short of running detection). IDs are already
        contiguous-by-construction here (the counter always hands out
        `len(rois) + 1` as long as `delete_rois` keeps its own renumbering
        promise), so no special undo bookkeeping beyond add/remove is
        needed."""
        return self.add_shaped_roi(x, y, Circle(float(sample_diameter_px)))

    @instrumented("RoiToolbox.add_shaped_roi")
    def add_shaped_roi(self, x: float, y: float, shape: Shape, *, border: Border | None = None, role: str = "sample") -> int:
        """Place one ROI of any shape (`roi_shapes`: circle, rectangle, mask) with its centre at (x, y); one undo step. ``border`` ``None`` follows
        the shared default border, ``NO_BORDER`` has none. ``role`` ``"reference"`` makes it a reference ROI (it has no border of its own then: its
        own border is ignored, as everywhere). A mask's pixels are where they were authored (processed-image space), whatever ``(x, y)`` says:
        the centre is the point the ROI is labelled and grouped by. Returns the new id."""
        if role not in ROI_ROLES:
            raise ValueError(f"unknown ROI role {role!r}; known: {ROI_ROLES}")
        roi_id = next(self._roi_id_counter)
        roi = AreaRoi(area_roi_id=roi_id, center_x=self._rounded(x), center_y=self._rounded(y), shape=shape, border=border, role=role)

        def apply() -> None:
            self._rois[roi_id] = roi
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="added"))

        def revert() -> None:
            self._rois.pop(roi_id, None)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=(roi_id,), reason="deleted"))

        apply()
        undo_manager.push(FunctionCommand("Add ROI", undo_fn=revert, redo_fn=apply))
        return roi_id

    @instrumented("RoiToolbox.add_mask_rois")
    def add_mask_rois(self, plane: np.ndarray, *, together: bool, role: str = "reference") -> tuple[int, ...]:
        """Make pixel-mask ROIs from the ``True`` pixels of ``plane`` (a bool image in the processed-image space ROIs live in), one undo step and one
        signal however many there are: ``together=True`` one ROI holding every selected pixel, ``together=False`` one per separate blob
        (`mask_regions`). ``role`` defaults to ``"reference"``: a region picked out of the image is usually a patch to measure against, and it is linked
        to nothing until the user links it. A mask ROI follows the shared default border if it is made a sample later. Returns the new ids in order; an
        empty selection raises `ValueError` (the message is for the status line) and changes nothing."""
        if role not in ROI_ROLES:
            raise ValueError(f"unknown ROI role {role!r}; known: {ROI_ROLES}")
        regions = mask_regions(plane, together=together)
        if not regions:
            raise ValueError("There is nothing selected to make a ROI from.")
        new_rois = []
        for mask, cx, cy in regions:
            roi_id = next(self._roi_id_counter)
            new_rois.append(AreaRoi(area_roi_id=roi_id, center_x=self._rounded(cx), center_y=self._rounded(cy), shape=MaskShape(mask), border=None, role=role))
        ids = tuple(roi.area_roi_id for roi in new_rois)

        def apply() -> None:
            for roi in new_rois:
                self._rois[roi.area_roi_id] = roi
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=ids, reason="added"))

        def revert() -> None:
            for roi in new_rois:
                self._rois.pop(roi.area_roi_id, None)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=ids, reason="deleted"))

        apply()
        undo_manager.push(FunctionCommand("Add ROI from selection" if len(new_rois) == 1 else f"Add {len(new_rois)} ROIs from selection", undo_fn=revert, redo_fn=apply))
        return ids

    # -- command API: rim pair <-> linked pair (plan 2026-11-19, "Rim pairs, linked pairs, the Type column") ----------

    # The rim's on / off switch belongs to the ROI, not to a cube (maintainer, 2026-11-19): a ROI is "with rim" or "rim-less" on every cube. A rim's *size* (offset, thickness) is
    # a geometry edit and follows the scope rules like every other; the switch is read and written over every state the ROI has (its base geometry and each timeline change).

    @staticmethod
    def _rim_borders(roi: AreaRoi) -> list[Border | None]:
        """The border of ``roi`` in every state it has: the base geometry, then each change of its timeline."""
        borders = [roi.border]
        if roi.timeline:
            borders.extend(change.geometry.border for change in roi.timeline.changes())
        return borders

    def _rim_states(self, roi: AreaRoi) -> list[bool]:
        """Whether the rim is on, in each state of ``roi`` (`_rim_borders`). Always one value per state; all equal unless an older file had them differ."""
        default = self._detection_settings.default_border()
        return [effective_border(border, default).enabled for border in self._rim_borders(roi)]

    def rim_refusal(self, roi_id: int) -> str | None:
        """Why ROI ``roi_id``'s rim cannot become a linked reference, or ``None`` when it can: only a sample that has a rim and no links has a *rim pair*."""
        roi = self._rois[int(roi_id)]
        if is_reference(roi):
            return f"ROI {roi.area_roi_id} is a reference: it has no rim of its own."
        if roi.reference_roi_ids:
            return f"ROI {roi.area_roi_id} is already linked to reference ROIs: it measures against those, not against a rim (Turn rim off drops the rim it still has)."
        if not any(self._rim_states(roi)):
            return f"ROI {roi.area_roi_id} has no rim."
        return None

    def can_turn_rim_off(self, roi_id: int) -> bool:
        """True when `turn_rim_off` would change ROI ``roi_id``: a sample (linked or not) whose rim is on."""
        roi = self._rois[int(roi_id)]
        return not is_reference(roi) and any(self._rim_states(roi))

    @instrumented("RoiToolbox.turn_rim_off")
    def turn_rim_off(self, roi_ids: Collection[int]) -> None:
        """Switch the rim of each sample ROI in ``roi_ids`` off, so it is a rim-less ROI: its shape (circle, rectangle, pixel map) is untouched and **its rim's offset and
        thickness are kept** (`add_rim` brings the same rim back). On every cube (the switch is the ROI's, not a cube's). One undo step; a ROI that `can_turn_rim_off` says
        no to is left alone, and nothing to change pushes no step. Raises ``KeyError`` for an unknown id."""
        targets = [roi for roi in self._require_rois(roi_ids) if self.can_turn_rim_off(roi.area_roi_id)]
        if targets:
            self._switch_rims(targets, on=False, label="Turn rim off" if len(targets) == 1 else f"Turn {len(targets)} rims off")

    def _switch_rims(self, rois: Sequence[AreaRoi], *, on: bool, label: str) -> None:
        """Turn the rim of every ROI in ``rois`` on or off in **every state** (`_rim_borders`), keeping its numbers (`roi_shapes.border_switched_on / _off`): one undo step, one
        signal, nothing pushed when no state changes. Only the switch changes: shape, centre and the rim's offset and thickness stay."""
        default = self._detection_settings.default_border()
        switch = border_switched_on if on else border_switched_off

        def switched(geometry: RoiGeometry) -> RoiGeometry:
            border = switch(geometry.border, default)
            return geometry if border is geometry.border else geometry._replace(border=border)  # (the helpers hand back the same object when there is nothing to switch)

        def switched_change(change):
            geometry = switched(change.geometry)
            return change if geometry is change.geometry else replace(change, geometry=geometry)

        states = []
        for roi in rois:
            base, timeline = base_geometry(roi), roi.timeline
            new_base = switched(base)
            new_timeline = timeline
            changed = new_base is not base
            if timeline:
                persistent = tuple(switched_change(c) for c in timeline.persistent)
                individual = tuple(switched_change(c) for c in timeline.individual)
                if any(a is not b for a, b in zip(persistent + individual, timeline.persistent + timeline.individual)):
                    new_timeline, changed = RoiTimeline(persistent, individual), True
            if changed:
                states.append((roi, (base, timeline), (new_base, new_timeline)))
        self._write_states(states, label, "resized")

    @instrumented("RoiToolbox.rims_to_references")
    def rims_to_references(self, sample_ids: Collection[int], image_shape: tuple[int, int], *, cube: int | None = None) -> tuple[int, ...]:
        """Turn the rim of each sample ROI in ``sample_ids`` into a reference ROI of its own, linked to that sample: a *rim pair* becomes a *linked pair*. One undo step,
        one new ROI per sample (appended after the existing ROIs), the new ids returned in the ascending order of the samples' ids.

        The new reference is a **pixel map of the rim's pixels** at the reference wavelength (identity chromatic affine, the frame ``image_shape`` = (rows, columns) of the
        processed image the ROIs live in), a frozen copy: from now on it is a ROI like any other (it does not follow its sample when that is moved or resized; being a
        map it moves by whole pixels: `_at_position`). **The rim is where the sample is on cube ``cube``** (the one on screen; ``None``: the base geometry): a move made on a later cube
        is a timeline change and not the base position (a rim taken from the base position landed where the sample used to be). The new ROI has no timeline, so it holds on every
        cube. All blobs of a rim form the one new ROI. The pixels of the sample itself are left out of it.

        The sample is then a **rim-less ROI** (`turn_rim_off`: the rim is switched off on every cube, its offset and thickness kept): it measures against the linked reference only,
        and `add_rim` brings its rim back. (Scan side: see the test `test_only_the_converted_sample_and_the_new_reference_go_stale`.)

        Raises ``ValueError`` (the message is for the status line), before anything changes, for a ROI that is a reference, is linked already, has no rim, or whose rim
        has no pixel inside the image."""
        rois = self._require_rois(sample_ids)  # ascending ids
        height, width = int(image_shape[0]), int(image_shape[1])
        default_border = self._detection_settings.default_border()
        identity = identity_affine_matrix()
        planned: list[tuple[AreaRoi, RoiMask, float, float]] = []
        for roi in rois:
            refusal = self.rim_refusal(roi.area_roi_id)
            if refusal:
                raise ValueError(refusal)
            where = resolved_at(roi, cube)  # the sample as it is on the cube on screen (the ROI itself when it has no timeline)
            rim = rasterize_reference(where, (height, width), identity, default_border=default_border)
            rim &= ~rasterize_sample(where, (height, width), identity)  # the sample's own pixels are not its reference (a negative gap reaches inside it)
            regions = mask_regions(rim, together=True)
            if not regions:
                raise ValueError(f"ROI {roi.area_roi_id}: its rim has no pixel inside the image.")
            mask, cx, cy = regions[0]
            planned.append((roi, mask, cx, cy))
        new_rois: list[AreaRoi] = []
        for _sample, mask, cx, cy in planned:
            new_rois.append(
                AreaRoi(area_roi_id=next(self._roi_id_counter), center_x=self._rounded(cx), center_y=self._rounded(cy), shape=MaskShape(mask), border=None, role=ROLE_REFERENCE)
            )
        pairs = [(roi, new.area_roi_id) for (roi, *_), new in zip(planned, new_rois)]
        before = {roi.area_roi_id: roi.reference_roi_ids for roi, _ in pairs}
        new_ids = tuple(new.area_roi_id for new in new_rois)
        sample_set = tuple(sorted(before))

        def apply() -> None:
            for new in new_rois:
                self._rois[new.area_roi_id] = new
            for roi, new_id in pairs:
                roi.reference_roi_ids = (new_id,)
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(new_ids)), reason="added"))
            self.links_changed.emit(RoiLinkChange(sample_ids=sample_set, reason="linked"))

        def revert() -> None:
            for new in new_rois:
                self._rois.pop(new.area_roi_id, None)
            for roi, _ in pairs:
                roi.reference_roi_ids = before[roi.area_roi_id]
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(new_ids)), reason="deleted"))
            self.links_changed.emit(RoiLinkChange(sample_ids=sample_set, reason="unlinked"))

        label = "Turn rim into linked reference" if len(pairs) == 1 else f"Turn {len(pairs)} rims into linked references"
        undo_manager.begin_batch(label)  # the new reference, the link and the sample's rim going off are one step for the user
        try:
            apply()
            undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))
            self._switch_rims([roi for roi, _new_id in pairs], on=False, label="Turn rim off")
        finally:
            undo_manager.end_batch()
        return new_ids

    def can_add_rim(self, roi_id: int) -> bool:
        """True when `add_rim` would change ROI ``roi_id``: a reference that nobody uses, or a sample whose rim is switched off (a reference that samples are linked to keeps its job)."""
        roi = self._rois[int(roi_id)]
        if is_reference(roi):
            return not samples_linked_to(self._rois.values(), roi.area_roi_id)
        return not all(self._rim_states(roi))

    @instrumented("RoiToolbox.add_rim")
    def add_rim(self, roi_ids: Collection[int]) -> None:
        """Give each ROI in ``roi_ids`` a rim: an unused reference becomes a sample (it then measures against its rim), and a sample whose rim is switched off gets its rim back **with
        the offset and thickness it had** (`turn_rim_off` keeps them; the shared default size when it never had a rim of its own size). On every cube, like the switch it undoes. Any
        shape; for a pixel map every blob gets its own. One undo step; a ROI that `can_add_rim` says no to is left alone, and nothing to change pushes no step. Raises ``KeyError`` for
        an unknown id."""
        rois = self._require_rois(roi_ids)
        targets = [roi for roi in rois if self.can_add_rim(roi.area_roi_id)]
        if not targets:
            return
        needs_role = [roi.area_roi_id for roi in targets if is_reference(roi)]
        needs_rim = [roi for roi in targets if not all(self._rim_states(roi))]
        label = "Add rim" if len(targets) == 1 else f"Add rim to {len(targets)} ROIs"
        undo_manager.begin_batch(label)  # the role and the rim are one step for the user
        try:
            if needs_role:
                self.set_roi_role(needs_role, ROLE_SAMPLE)
            if needs_rim:
                self._switch_rims(needs_rim, on=True, label="Add rim")
        finally:
            undo_manager.end_batch()

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

    # -- the geometry timeline (docs/roi_timeline_design_2026-10-08.md) ------------------
    #
    # A ROI's *geometry* (centre, sample diameter, ring diameters) may differ by cube; its identity never does.
    # Every geometry command takes ``cube`` and ``scope``: ``cube=None`` edits the **base** geometry (valid on
    # every cube; what every ROI had before the timeline existed); a cube with ``scope="persistent"`` writes a
    # change valid from that cube on until a later persistent change; ``scope="individual"`` writes a change for
    # that cube only (the Mask rule). Queries read geometry at a cube through `rois_at` / `geometry_at`.

    def has_timeline(self) -> bool:
        """True if any ROI has a geometry change (False: every ROI has one geometry for every cube)."""
        return any(roi.timeline for roi in self._rois.values())

    def timeline_cubes(self) -> tuple[int, ...]:
        """Every cube at which any ROI has a geometry change (for a marker on the cube slider)."""
        cubes: set[int] = set()
        for roi in self._rois.values():
            if roi.timeline:
                cubes.update(roi.timeline.cubes())
        return tuple(sorted(cubes))

    # -- the layout timeline (2026-10-16): all ROIs together ---------------------------------------
    #
    # The *layout* is the set of every ROI's geometry. Storage stays per ROI (so a change recomputes only the cells of
    # the ROIs it touched), but a cube is one kind of point for the whole layout: persistent (from this cube on) or
    # individual (this cube only), never both. `_write_geometry` enforces it; the Image panel's ROI row reads it here.

    def layout_point_kind(self, cube: int) -> str | None:
        """``"persistent"`` / ``"individual"`` if any ROI has a change authored at `cube`, else ``None``."""
        individual = False
        for roi in self._rois.values():
            timeline = roi.timeline
            if not timeline:
                continue
            if timeline.has_persistent_at(cube):
                return SCOPE_PERSISTENT
            individual = individual or timeline.has_individual_at(cube)
        return SCOPE_INDIVIDUAL if individual else None

    def layout_points(self, roi_ids: Collection[int] | None = None) -> dict[int, LayoutPoint]:
        """Every cube where the layout changes, ascending: its kind and the ROIs with a change authored there (only
        `roi_ids` when given). The start layout (the ROIs' base geometry, valid from the first cube) is not listed: it is
        no timeline entry."""
        wanted = None if roi_ids is None else {int(i) for i in roi_ids}
        persistent: dict[int, list[int]] = {}
        individual: dict[int, list[int]] = {}
        for roi_id, roi in self._rois.items():
            if not roi.timeline or (wanted is not None and roi_id not in wanted):
                continue
            for change in roi.timeline.persistent:
                persistent.setdefault(change.cube, []).append(roi_id)
            for change in roi.timeline.individual:
                individual.setdefault(change.cube, []).append(roi_id)
        points = {cube: LayoutPoint(SCOPE_INDIVIDUAL, tuple(sorted(ids))) for cube, ids in individual.items()}
        points.update({cube: LayoutPoint(SCOPE_PERSISTENT, tuple(sorted(ids))) for cube, ids in persistent.items()})
        return dict(sorted(points.items()))

    def _change_layout_point(self, change: Callable[[RoiTimeline], RoiTimeline], label: str) -> None:
        """Rewrite the timeline of every ROI with `change` as one undo step (ROIs it leaves as they were are not touched)."""
        states = []
        for roi in self._rois.values():
            if roi.timeline:
                changed = change(roi.timeline)
                if changed != roi.timeline:
                    states.append((roi, (base_geometry(roi), roi.timeline), (base_geometry(roi), changed)))
        self._write_states(states, label, "resized")

    @instrumented("RoiToolbox.delete_layout_point")
    def delete_layout_point(self, cube: int) -> None:
        """Take away the whole layout change at `cube`: every ROI's change authored there, whatever its kind. The cube then
        follows the earlier layout again. (The start layout, valid from the first cube, is no change and cannot be deleted.)"""
        self._change_layout_point(lambda timeline: timeline.without_cube(int(cube)), "Delete layout change")

    @instrumented("RoiToolbox.make_layout_point_individual")
    def make_layout_point_individual(self, cube: int) -> None:
        """A persistent layout change at `cube` becomes individual (this cube only) for every ROI that has one there: the
        cubes after it go back to the layout before. Nothing happens at a cube that is not a persistent point."""
        self._change_layout_point(lambda timeline: timeline.persistent_made_individual(int(cube)), "Make layout change individual")

    @instrumented("RoiToolbox.make_layout_point_persistent")
    def make_layout_point_persistent(self, cube: int) -> None:
        """An individual layout change at `cube` becomes persistent (from this cube on, until the next persistent change) for
        every ROI that has one there. Nothing happens at a cube that is not an individual point."""
        self._change_layout_point(lambda timeline: timeline.individual_made_persistent(int(cube)), "Make layout change persistent")

    def edit_refusal(self, cube: int | None, scope: str) -> str | None:
        """Why a geometry edit at (`cube`, `scope`) would be refused, as a sentence for the user, or ``None``.

        An Individual edit on a cube that already starts a persistent layout: the cube would be both kinds at once, and
        "this cube only" cannot be told apart from the layout that holds from here on. (The first cube is the same case,
        but the toolbox does not know which cube is first: `RoiEditTarget.kwargs` refuses that one.)"""
        if cube is not None and scope == SCOPE_INDIVIDUAL and self.layout_point_kind(cube) == SCOPE_PERSISTENT:
            return (
                f"Cube {int(cube)} starts a persistent layout, so it cannot take an individual change. "
                "Edit it as Persistent, or remove its change first."
            )
        return None

    def geometry_segment(self, cube: int | None) -> tuple:
        """A label for "which geometry do the ROIs have on `cube`": two cubes with the same label have the same geometry for
        every ROI, so what is drawn from it (the overlay) is the same. ``()`` when no ROI has a timeline (one geometry for every
        cube). Otherwise ``("segment", n)``: the cubes between two consecutive *persistent* changes (a persistent change is valid
        from its cube on), or ``("single", cube)``: a cube with an *individual* change (valid on that cube only).

        The Image panel's overlay cache keys on this instead of the cube number (2026-10-14, testing12: three ROIs with one
        persistent change at cube 79 made all 314 cubes look different, and nothing the cache kept was ever used again)."""
        if cube is None:
            return ()
        persistent: set[int] = set()
        individual: set[int] = set()
        for roi in self._rois.values():
            timeline = roi.timeline
            if timeline:
                persistent.update(change.cube for change in timeline.persistent)
                individual.update(change.cube for change in timeline.individual)
        if not persistent and not individual:
            return ()
        cube = int(cube)
        if cube in individual:
            return ("single", cube)
        return ("segment", sum(1 for change_cube in persistent if change_cube <= cube))

    def geometry_at(self, roi_id: int, cube: int | None) -> RoiGeometry:
        """The geometry of `roi_id` on `cube` (``None``: the base geometry)."""
        return geometry_at(self._rois[roi_id], cube)

    def rois_at(self, cube: int | None) -> tuple[AreaRoi, ...]:
        """Every ROI with the geometry valid on `cube`. With no timeline anywhere these are the stored ROIs
        themselves; otherwise ROIs with a timeline are copies (geometry fields resolved, no timeline), so a reader
        (drawing, analysis) never sees the timeline. Do not mutate the result."""
        if cube is None or not self.has_timeline():
            return self.rois()
        return tuple(resolved_at(roi, cube) for roi in self._rois.values())

    def _write_geometry(
        self,
        changes: list[tuple[AreaRoi, RoiGeometry]],
        cube: int | None,
        scope: str,
        label: str,
        reason: str,
    ) -> None:
        """Set the geometry of each ROI at (`cube`, `scope`) as one undo step; a no-op (no undo entry) if nothing
        differs from what is already valid there.

        The layout rule (maintainer, 2026-10-16): a cube is one kind of point for all ROIs together.
        - Individual on a cube that starts a persistent layout: refused (``ValueError``, `edit_refusal`).
        - Persistent on a cube with individual points: the individual changes of **every** ROI there become persistent
          too (same geometry, now valid from this cube on), in the same undo step. A ROI nobody edited can therefore
          have its later cubes change: the layout at this cube is what carries on."""
        if scope not in GEOMETRY_SCOPES:
            raise ValueError(f"scope must be one of {GEOMETRY_SCOPES}, got {scope!r}")
        # Stage timing, DEBUG only, one line per write (CLAUDE.md's performance rule: instrument the hot path,
        # aggregate, never log per ROI). A geometry write is the hot path of a ROI drag - one per mouse step - and
        # the 2026-10-21 "GUI slow while moving ROIs" report could not be told apart from its listeners without
        # this split: `emit_ms` is every listener on `geometry_changed`, `states_ms` the timeline arithmetic.
        started = time.perf_counter()
        refusal = self.edit_refusal(cube, scope)
        if refusal is not None:
            raise ValueError(refusal)
        refusal_seconds = time.perf_counter() - started
        states: list[tuple[AreaRoi, tuple[RoiGeometry, RoiTimeline | None], tuple[RoiGeometry, RoiTimeline | None]]] = []
        for roi, new in changes:
            if geometry_at(roi, cube) == new:
                continue
            old_state = (base_geometry(roi), roi.timeline)
            if cube is None:
                new_state = (new, roi.timeline)
            else:
                timeline = roi.timeline or RoiTimeline()
                if scope == SCOPE_PERSISTENT:
                    timeline = timeline.without_individual_at(cube)  # one kind per cube: this ROI's nudge here is replaced
                new_state = (base_geometry(roi), timeline.with_change(cube, scope, new))
            states.append((roi, old_state, new_state))
        if states and cube is not None and scope == SCOPE_PERSISTENT and self.layout_point_kind(cube) == SCOPE_INDIVIDUAL:
            edited = {roi.area_roi_id for roi, _old, _new in states}
            for roi in self._rois.values():
                if roi.area_roi_id in edited or not roi.timeline or not roi.timeline.has_individual_at(cube):
                    continue
                states.append(
                    (roi, (base_geometry(roi), roi.timeline), (base_geometry(roi), roi.timeline.individual_made_persistent(cube)))
                )
        states_seconds = time.perf_counter() - started - refusal_seconds
        emit_seconds = self._write_states(states, label, reason)
        if not states:
            return  # nothing changed: no undo entry, no signal, and nothing worth a timing line either
        total = time.perf_counter() - started
        emit_timing(
            logger, "roi.geometry_write", total * 1000.0,
            refusal_ms=round(refusal_seconds * 1000.0, 2),
            states_ms=round(states_seconds * 1000.0, 2),
            emit_ms=round(emit_seconds * 1000.0, 2),
            write_ms=round((total - refusal_seconds - states_seconds - emit_seconds) * 1000.0, 2),
            n=len(states), rois=len(self._rois), cube=-1 if cube is None else int(cube), scope=scope, reason=reason,
        )

    def _at_position(self, g: RoiGeometry, x: float, y: float) -> RoiGeometry:
        """The geometry ``g`` has with its centre put at ``(x, y)``: the one rule behind every position edit (a drag, a typed X / Y, a wheel step, a paste, Accept).

        A circle or a rectangle moves to the point (rounded to ``POSITION_DECIMALS``). **A pixel map moves by whole pixels** (`roi_shapes.shifted_mask`): its pixels
        are the ROI, and a fraction of a pixel would mean resampling them. The shift is the whole-pixel distance to the asked point, and the label point (the centre)
        moves by exactly that, so it stays where it was in the map: the X / Y the table shows afterwards are the real ones, up to half a pixel from what was asked."""
        if isinstance(g.shape, MaskShape):
            dx, dy = whole_pixels(x - g.center_x), whole_pixels(y - g.center_y)
            if dx == 0 and dy == 0:
                return g
            return g._replace(
                center_x=self._rounded(g.center_x + dx), center_y=self._rounded(g.center_y + dy), shape=MaskShape(shifted_mask(g.shape.mask, dx, dy))
            )
        return g._replace(center_x=self._rounded(x), center_y=self._rounded(y))

    @staticmethod
    def _stayed_put_refusal(stayed: Sequence[int]) -> str:
        names = ", ".join(str(i) for i in stayed)
        return (
            f"ROI {names} {'is a pixel map' if len(stayed) == 1 else 'are pixel maps'}: it moves by whole pixels, and that is less than half a pixel, "
            "so nothing was moved."
        )

    def _set_positions(
        self, positions: dict[int, tuple[float, float]], label: str, *, cube: int | None = None, scope: str = SCOPE_PERSISTENT
    ) -> None:
        rois = self._require_rois(positions)
        changes: list[tuple[AreaRoi, RoiGeometry]] = []
        stayed: list[int] = []  # pixel maps asked to move by less than half a pixel
        for roi in rois:
            x, y = float(positions[roi.area_roi_id][0]), float(positions[roi.area_roi_id][1])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"ROI {roi.area_roi_id}: position must be finite, got ({x}, {y})")
            g = geometry_at(roi, cube)
            moved = self._at_position(g, x, y)
            if isinstance(g.shape, MaskShape) and moved is g and (x != g.center_x or y != g.center_y):
                stayed.append(roi.area_roi_id)
            changes.append((roi, moved))
        if stayed and all(new == geometry_at(roi, cube) for roi, new in changes):  # nothing at all moved although a move was asked: say so (a mixed selection moves what it can)
            raise ValueError(self._stayed_put_refusal(stayed))
        self._write_geometry(changes, cube, scope, label, "moved")

    @instrumented("RoiToolbox.move_roi")
    def move_roi(self, roi_id: int, x: float, y: float, *, cube: int | None = None, scope: str = SCOPE_PERSISTENT) -> None:
        self._set_positions({roi_id: (x, y)}, "Move ROI", cube=cube, scope=scope)

    @instrumented("RoiToolbox.move_rois")
    def move_rois(self, positions: Mapping[int, tuple[float, float]], *, label: str = "Move ROIs") -> None:
        """Put each ROI in ``positions`` (id -> (x, y), processed px) at its own new centre: one undo step and one signal, however many ROIs, each centre
        rounded to ``POSITION_DECIMALS`` like every position edit. All or nothing: an unknown id or a position that is not finite raises before anything
        changes. A ROI that is already there is left alone; if nothing differs there is no undo entry and no signal. (``translate_rois`` shifts ROIs by
        one common vector; this is for different positions, e.g. the answer of ROI position tuning.)"""
        self._set_positions({int(roi_id): (float(x), float(y)) for roi_id, (x, y) in positions.items()}, label)

    @instrumented("RoiToolbox.translate_rois")
    def translate_rois(
        self, roi_ids: Collection[int], dx: float, dy: float, *, cube: int | None = None, scope: str = SCOPE_PERSISTENT
    ) -> None:
        """Shift every ROI in ``roi_ids`` by (dx, dy) pixels: the multi-select
        position edit that keeps the ROIs' arrangement. A pixel map moves by the nearest whole number of pixels (`_at_position`); when that is nothing for every ROI
        of the call, ``ValueError`` (nothing moved). A caller that moves a map in small steps (a drag) adds up the distance itself and hands over whole pixels."""
        rois = self._require_rois(roi_ids)
        positions = {}
        for roi in rois:
            g = geometry_at(roi, cube)
            positions[roi.area_roi_id] = (g.center_x + float(dx), g.center_y + float(dy))
        self._set_positions(positions, "Move ROIs", cube=cube, scope=scope)

    @instrumented("RoiToolbox.place_rois")
    def place_rois(
        self,
        roi_ids: Collection[int],
        *,
        x: float | None = None,
        y: float | None = None,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """Set the x and/or y of every ROI in ``roi_ids`` to one value
        (``None`` leaves that coordinate alone). Typically one coordinate, to
        line up a row or a column; giving both stacks the ROIs on one point."""
        if x is None and y is None:
            return
        rois = self._require_rois(roi_ids)
        positions = {}
        for roi in rois:
            g = geometry_at(roi, cube)
            positions[roi.area_roi_id] = (g.center_x if x is None else float(x), g.center_y if y is None else float(y))
        self._set_positions(positions, "Align ROIs", cube=cube, scope=scope)

    def _set_shapes(
        self,
        targets: list[tuple[AreaRoi, Shape, Border | None]],
        label: str,
        *,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        changes = [(roi, geometry_at(roi, cube)._replace(shape=shape, border=border)) for roi, shape, border in targets]
        self._write_geometry(changes, cube, scope, label, "resized")

    @instrumented("RoiToolbox.resize_rois")
    def resize_rois(
        self,
        roi_ids: Collection[int],
        *,
        sample_diameter_px: float | None = None,
        reference_inner_diameter_px: float | None = None,
        reference_outer_diameter_px: float | None = None,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """Set one or more of the sample / reference-ring diameters on every
        circular ROI in ``roi_ids``; a field left ``None`` is unchanged. A no-op (no
        undo entry) if nothing differs.

        The ring is the ROI's *border*, kept as a gap and thickness from the sample's edge (`roi_shapes`): a ring edit
        (inner and / or outer diameter) stores the border that gives exactly those diameters at the (new) sample size, and a
        change of the sample diameter alone **carries the border with it** (the ring keeps its gap and thickness).

        Raises ``ValueError``, before changing anything, if a ROI is not a circle, if the sample
        diameter is below `MIN_SAMPLE_DIAMETER_PX` or not finite, or if a
        ROI's reference ring would not satisfy ``0 <= inner < outer``. The
        ring is checked against the diameters the ROI will actually use, so a
        ROI that still follows the default border is checked against that."""
        rois = self._require_rois(roi_ids)
        default_border = self._detection_settings.default_border()
        self._check_circle_inputs(sample_diameter_px, reference_inner_diameter_px, reference_outer_diameter_px)
        targets: list[tuple[AreaRoi, Shape, Border | None]] = []
        for roi in rois:
            shape, border = self._resized_circle(
                roi, geometry_at(roi, cube), default_border, sample_diameter_px, reference_inner_diameter_px, reference_outer_diameter_px
            )
            targets.append((roi, shape, border))
        self._set_shapes(targets, "Resize ROI" if len(targets) == 1 else "Resize ROIs", cube=cube, scope=scope)

    @staticmethod
    def _check_circle_inputs(sample_diameter_px: float | None, inner_px: float | None, outer_px: float | None) -> None:
        """The checks of the values themselves (not of the ROI): finite, and a sample diameter of at least `MIN_SAMPLE_DIAMETER_PX`."""
        for value, name in (
            (sample_diameter_px, "sample diameter"),
            (inner_px, "reference inner diameter"),
            (outer_px, "reference outer diameter"),
        ):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        if sample_diameter_px is not None and float(sample_diameter_px) < MIN_SAMPLE_DIAMETER_PX:
            raise ValueError(f"sample diameter must be at least {MIN_SAMPLE_DIAMETER_PX:g} px, got {float(sample_diameter_px):g}")

    @staticmethod
    def _resized_circle(
        roi: AreaRoi, g: RoiGeometry, default_border: Border, sample_diameter_px: float | None, inner_px: float | None, outer_px: float | None
    ) -> tuple[Circle, Border | None]:
        """The circle and border `roi` (geometry `g`) gets from new diameters (``None`` = unchanged): the one rule behind `resize_rois` and `set_cell_values`.
        A ring edit stores the border that gives exactly those diameters at the (new) sample size; a sample change alone carries the border with it."""
        if not isinstance(g.shape, Circle):
            raise ValueError(f"ROI {roi.area_roi_id} is not a circle: its size is not a diameter")
        diameter = g.shape.diameter_px if sample_diameter_px is None else float(sample_diameter_px)
        border = g.border
        if inner_px is not None or outer_px is not None:
            current_inner, current_outer = ring_diameters(g.shape, effective_border(g.border, default_border))
            inner = current_inner if inner_px is None else float(inner_px)
            outer = current_outer if outer_px is None else float(outer_px)
            if not 0.0 <= inner < outer:
                raise ValueError(f"ROI {roi.area_roi_id}: the rim needs 0 <= inner < outer (inner {inner:g} px, outer {outer:g} px)")
            border = Border.from_ring_diameters(diameter, inner, outer)
        return Circle(diameter), border

    @instrumented("RoiToolbox.resize_rectangles")
    def resize_rectangles(
        self,
        roi_ids: Collection[int],
        *,
        width_px: float | None = None,
        height_px: float | None = None,
        angle_deg: float | None = None,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """Set the width, height and / or angle of every rectangular ROI in ``roi_ids``; a field left ``None`` is unchanged, a no-op (no undo entry) if
        nothing differs. The width and height are along the rectangle's own axes and the angle turns it clockwise on the screen about its centre
        (`roi_shapes.Rectangle`). The border follows the shape, as everywhere (a resize carries the band with it).

        Raises ``ValueError``, before changing anything, for a ROI that is not a rectangle, a value that is not finite, or a width or height below
        `MIN_SAMPLE_DIAMETER_PX`."""
        rois = self._require_rois(roi_ids)
        self._check_rectangle_inputs(width_px, height_px, angle_deg)
        targets: list[tuple[AreaRoi, Shape, Border | None]] = []
        for roi in rois:
            g = geometry_at(roi, cube)
            shape = self._resized_rectangle(roi, g, width_px, height_px, angle_deg)
            if shape != g.shape:
                targets.append((roi, shape, g.border))
        if targets:
            self._set_shapes(targets, "Resize rectangle" if len(targets) == 1 else "Resize rectangles", cube=cube, scope=scope)

    @staticmethod
    def _check_rectangle_inputs(width_px: float | None, height_px: float | None, angle_deg: float | None) -> None:
        """The checks of the values themselves: finite, and a width and height of at least `MIN_SAMPLE_DIAMETER_PX`."""
        for value, name in ((width_px, "width"), (height_px, "height"), (angle_deg, "angle")):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        for value, name in ((width_px, "width"), (height_px, "height")):
            if value is not None and float(value) < MIN_SAMPLE_DIAMETER_PX:
                raise ValueError(f"{name} must be at least {MIN_SAMPLE_DIAMETER_PX:g} px, got {float(value):g}")

    @staticmethod
    def _resized_rectangle(roi: AreaRoi, g: RoiGeometry, width_px: float | None, height_px: float | None, angle_deg: float | None) -> Rectangle:
        """The rectangle `roi` (geometry `g`) gets from a new width, height and / or angle (``None`` = unchanged): the one rule behind `resize_rectangles` and `set_cell_values`."""
        if not isinstance(g.shape, Rectangle):
            raise ValueError(f"ROI {roi.area_roi_id} is not a rectangle: it has no width, height or angle")
        return Rectangle(
            g.shape.width_px if width_px is None else float(width_px),
            g.shape.height_px if height_px is None else float(height_px),
            g.shape.angle_deg if angle_deg is None else float(angle_deg),
        )

    CELL_FIELDS = (
        "x", "y", "sample_diameter_px", "reference_inner_diameter_px", "reference_outer_diameter_px", "width_px", "height_px", "angle_deg",
        "rim_offset_px", "rim_thickness_px",
    )
    """The numbers `set_cell_values` can give a ROI (the ROI table's number columns: the table's Offset and Thickness are ``rim_offset_px`` / ``rim_thickness_px``, the same for every shape)."""

    def _edited_rim(self, roi: AreaRoi, border: Border | None, default_border: Border, offset_px: float | None, thickness_px: float | None) -> Border:
        """The rim ``roi`` gets when its offset (gap from the shape's edge, negative = starts inside it) and / or thickness are set (``None`` = unchanged): the one rule behind
        `set_rim` and `set_cell_values`, **the same for a circle, a rectangle and a pixel map**. ``border`` is the ROI's own border (``None`` = the shared default). Raises
        ``ValueError`` for a reference ROI or a ROI whose rim is off (they have none to set), a number that is not finite, or a negative thickness."""
        if is_reference(roi):
            raise ValueError(f"ROI {roi.area_roi_id} is a reference: it has no rim of its own.")
        current = effective_border(border, default_border)
        if not current.enabled:
            raise ValueError(f"ROI {roi.area_roi_id} has no rim (use Add rim first).")
        for value, name in ((offset_px, "offset"), (thickness_px, "thickness")):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"the rim's {name} must be a finite number, got {value!r}")
        if thickness_px is not None and float(thickness_px) < 0.0:
            raise ValueError(f"the rim's thickness cannot be negative, got {float(thickness_px):g} px")
        return Border(current.gap_px if offset_px is None else float(offset_px), current.thickness_px if thickness_px is None else float(thickness_px))

    @instrumented("RoiToolbox.set_rim")
    def set_rim(
        self, roi_ids: Collection[int], *, offset_px: float | None = None, thickness_px: float | None = None, cube: int | None = None, scope: str = SCOPE_PERSISTENT
    ) -> None:
        """Set the rim's offset (px from the shape's edge, negative = starts inside it) and / or thickness (px) of every ROI in ``roi_ids``; a field left ``None`` is unchanged. **The same
        for a circle, a rectangle and a pixel map**; the shape is left alone. One undo step; a no-op (no undo entry) if nothing differs. A ROI that follows the shared default rim gets a rim of
        its own with the new number and the default's other one. Raises ``ValueError`` (naming the ROI), before changing anything, for a reference ROI, a ROI without a rim, a number that is not
        finite or a negative thickness."""
        rois = self._require_rois(roi_ids)
        default_border = self._detection_settings.default_border()
        targets: list[tuple[AreaRoi, Shape, Border | None]] = []
        for roi in rois:
            g = geometry_at(roi, cube)
            targets.append((roi, g.shape, self._edited_rim(roi, g.border, default_border, offset_px, thickness_px)))
        self._set_shapes(targets, "Set rim" if len(targets) == 1 else "Set rims", cube=cube, scope=scope)

    @instrumented("RoiToolbox.set_cell_values")
    def set_cell_values(
        self, values: Mapping[int, Mapping[str, float]], *, cube: int | None = None, scope: str = SCOPE_PERSISTENT, label: str = "Paste cells"
    ) -> None:
        """Give each ROI in ``values`` (id -> {field: number}, fields `CELL_FIELDS`) its own new numbers: **one undo step and one signal**, however many ROIs and fields. This is
        what pasting cells of the ROI table does (each ROI may get a different value, which ``resize_rois`` / ``place_rois`` cannot say in one step).

        The rules are the ones of typing the value: a position of a pixel map moves it by whole pixels (`_at_position`), a diameter needs a circle, a width / height / angle a rectangle, the sample
        diameter, width and height at least `MIN_SAMPLE_DIAMETER_PX`, the ring ``0 <= inner < outer``, a rim's offset and thickness (any shape) need a rim (not a reference, not switched off)
        and a thickness of at least 0. **All or nothing**: the first problem raises ``ValueError`` (naming the ROI)
        before anything changes. A ROI whose numbers do not differ is left alone; if nothing differs there is no undo entry."""
        unknown = {field for fields in values.values() for field in fields} - set(self.CELL_FIELDS)
        if unknown:
            raise ValueError(f"unknown field {sorted(unknown)}; known: {self.CELL_FIELDS}")
        rois = self._require_rois(values)
        default_border = self._detection_settings.default_border()
        changes: list[tuple[AreaRoi, RoiGeometry]] = []
        stayed: list[int] = []  # pixel maps asked to move by less than half a pixel
        for roi in rois:
            fields = values[roi.area_roi_id]
            g = geometry_at(roi, cube)
            shape, border, x, y = g.shape, g.border, g.center_x, g.center_y
            try:
                for name in ("x", "y"):
                    if name in fields and not math.isfinite(float(fields[name])):
                        raise ValueError(f"position {name} must be a finite number, got {fields[name]!r}")
                if "x" in fields:
                    x = float(fields["x"])
                if "y" in fields:
                    y = float(fields["y"])
                circle = (fields.get("sample_diameter_px"), fields.get("reference_inner_diameter_px"), fields.get("reference_outer_diameter_px"))
                if any(value is not None for value in circle):
                    self._check_circle_inputs(*circle)
                    shape, border = self._resized_circle(roi, g, default_border, *circle)
                rectangle = (fields.get("width_px"), fields.get("height_px"), fields.get("angle_deg"))
                if any(value is not None for value in rectangle):
                    self._check_rectangle_inputs(*rectangle)
                    shape = self._resized_rectangle(roi, g, *rectangle)
                rim = (fields.get("rim_offset_px"), fields.get("rim_thickness_px"))
                if any(value is not None for value in rim):
                    border = self._edited_rim(roi, border, default_border, *rim)
            except ValueError as exc:
                text = str(exc)
                raise ValueError(text if text.startswith("ROI ") else f"ROI {roi.area_roi_id}: {text}") from None
            edited = g._replace(shape=shape, border=border)
            moved = self._at_position(edited, x, y)
            if isinstance(shape, MaskShape) and moved.shape is shape and (x != g.center_x or y != g.center_y):
                stayed.append(roi.area_roi_id)
            changes.append((roi, moved))
        if stayed and all(new == geometry_at(roi, cube) for roi, new in changes):  # a paste of positions that moves no pixel map and changes nothing else
            raise ValueError(self._stayed_put_refusal(stayed))
        self._write_geometry(changes, cube, scope, label, "pasted")

    @instrumented("RoiToolbox.set_borders")
    def set_borders(self, borders: Mapping[int, Border], *, cube: int | None = None, scope: str = SCOPE_PERSISTENT) -> None:
        """Give every ROI in ``borders`` (id -> :class:`Border`) the border of its own (gap and thickness from its shape's edge, `roi_shapes`); the shape is
        left alone. This is what dragging the edges of a rectangle's border band stores (a circle's ring goes through `resize_rois`, which says it as
        diameters). One undo step; a no-op (no undo entry) if every ROI already has exactly that border.

        Raises ``ValueError``, before changing anything, for an unknown id or a gap that is not finite (a thickness below zero is refused by
        :class:`Border` itself)."""
        rois = self._require_rois(borders)
        for roi in rois:
            border = borders[roi.area_roi_id]
            if not math.isfinite(float(border.gap_px)) or not math.isfinite(float(border.thickness_px)):
                raise ValueError(f"ROI {roi.area_roi_id}: a rim needs a finite gap and thickness, got {border!r}")
        targets: list[tuple[AreaRoi, Shape, Border | None]] = [(roi, geometry_at(roi, cube).shape, borders[roi.area_roi_id]) for roi in rois]
        self._set_shapes(targets, "Resize rim" if len(targets) == 1 else "Resize rims", cube=cube, scope=scope)

    @instrumented("RoiToolbox.resize_roi")
    def resize_roi(
        self,
        roi_id: int,
        *,
        sample_diameter_px: float | None = None,
        reference_inner_diameter_px: float | None = None,
        reference_outer_diameter_px: float | None = None,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """One-ROI form of `resize_rois`."""
        self.resize_rois(
            (roi_id,),
            sample_diameter_px=sample_diameter_px,
            reference_inner_diameter_px=reference_inner_diameter_px,
            reference_outer_diameter_px=reference_outer_diameter_px,
            cube=cube,
            scope=scope,
        )

    @instrumented("RoiToolbox.reset_roi_diameters")
    def reset_roi_diameters(
        self,
        roi_ids: Collection[int],
        *,
        sample: bool = True,
        reference: bool = True,
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """Put the chosen sizes back to the shared defaults.

        ``sample``: a circle's diameter becomes the detection settings'
        sample diameter (other shapes keep their size). ``reference``: the ROI's
        own border is removed (``None``), so it follows the shared default
        border again, including when that changes later."""
        rois = self._require_rois(roi_ids)
        defaults = self._detection_settings
        targets = []
        for roi in rois:
            g = geometry_at(roi, cube)
            shape = Circle(float(defaults.sample_diameter_px)) if sample and isinstance(g.shape, Circle) else g.shape
            targets.append((roi, shape, None if reference else g.border))
        self._set_shapes(targets, "Reset ROI diameters", cube=cube, scope=scope)

    @instrumented("RoiToolbox.apply_to_all_cubes")
    def apply_to_all_cubes(self, roi_ids: Collection[int], cube: int) -> None:
        """Make the geometry the ROIs have on `cube` their geometry on **every** cube (the timeline is dropped):
        today's behaviour of a ROI edit, in one step."""
        rois = self._require_rois(roi_ids)
        states = []
        for roi in rois:
            if not roi.timeline and base_geometry(roi) == geometry_at(roi, cube):
                continue
            states.append((roi, (base_geometry(roi), roi.timeline), (geometry_at(roi, cube), None)))
        self._write_states(states, "Apply to all cubes", "resized")

    @instrumented("RoiToolbox.remove_cube_edit")
    def remove_cube_edit(self, roi_ids: Collection[int], cube: int) -> None:
        """Remove the geometry changes the ROIs have *authored at* `cube` (both scopes): the cube then follows the
        earlier changes (or the base geometry) again."""
        rois = self._require_rois(roi_ids)
        states = []
        for roi in rois:
            if roi.timeline and cube in roi.timeline.cubes():
                states.append((roi, (base_geometry(roi), roi.timeline), (base_geometry(roi), roi.timeline.without_cube(cube))))
        self._write_states(states, "Remove cube edit", "resized")

    def _write_states(self, states: list, label: str, reason: str) -> float:
        """Apply `states`, emit one `geometry_changed`, push one undo entry. Returns how long that emit took, in
        seconds - every listener runs inside it, which is what `_write_geometry`'s timing line reports."""
        if not states:
            return 0.0
        emit_seconds = 0.0

        def write(which: int) -> None:
            nonlocal emit_seconds
            for roi, old_state, new_state in states:
                base, timeline = (old_state, new_state)[which]
                roi.center_x, roi.center_y = base.center_x, base.center_y
                roi.shape = base.shape
                roi.border = base.border
                roi.timeline = timeline if timeline else None
            # `emit` runs every listener synchronously, so its time is part of the command the user sees in the
            # log (a 2026-10-21 drag report measured 300-660 ms on `translate_rois`, of which the write itself
            # was ~1 ms - everything else was in here). `_write_geometry` logs the split.
            emit_started = time.perf_counter()
            self.geometry_changed.emit(
                RoiComputationalChange(roi_ids=tuple(sorted(roi.area_roi_id for roi, _o, _n in states)), reason=reason)
            )
            emit_seconds = time.perf_counter() - emit_started

        write(1)
        undo_manager.push(FunctionCommand(label, undo_fn=lambda: write(0), redo_fn=lambda: write(1)))
        return emit_seconds

    def _survivor_links(
        self, id_map: Mapping[int, int]
    ) -> tuple[list[tuple[AreaRoi, tuple[int, ...]]], list[tuple[AreaRoi, tuple[int, ...]]], tuple[AreaRoi, ...]]:
        """For a command that removes ROIs and renumbers the survivors by ``id_map`` (``{old: new}``, survivors only): the
        links of the surviving samples before, after (renumbered; a link to a removed reference is dropped, so that sample
        falls back to its built-in reference or has none), and the samples that lost a link. Computed once, up front, like
        the id map, so redo and undo replay exactly."""
        linked = [self._rois[old_id] for old_id in sorted(id_map) if self._rois[old_id].reference_roi_ids]
        before = [(roi, roi.reference_roi_ids) for roi in linked]
        after = [(roi, tuple(sorted(id_map[i] for i in roi.reference_roi_ids if i in id_map))) for roi in linked]
        lost = tuple(roi for (roi, old), (_, new) in zip(before, after) if len(new) < len(old))
        return before, after, lost

    def _emit_lost_links(self, lost: tuple[AreaRoi, ...]) -> None:
        if lost:
            self.links_changed.emit(RoiLinkChange(tuple(sorted(roi.area_roi_id for roi in lost)), reason="deleted"))

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
        links_before, links_after, lost_link = self._survivor_links(id_map)

        def apply() -> None:
            renumbered: dict[int, AreaRoi] = {}
            for old_id, new_id in id_map.items():
                roi = self._rois[old_id]
                roi.area_roi_id = new_id
                renumbered[new_id] = roi
            self._rois = renumbered
            for roi, links in links_after:
                roi.reference_roi_ids = links
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
            self._emit_lost_links(lost_link)

        def revert() -> None:
            restored: dict[int, AreaRoi] = {}
            for new_id, roi in self._rois.items():
                old_id = reverse_id_map[new_id]
                roi.area_roi_id = old_id
                restored[old_id] = roi
            restored.update(removed_rois)
            self._rois = restored
            for roi, links in links_before:
                roi.reference_roi_ids = links
            for snapshot in groups_before:
                _restore(snapshot)
            for snapshot in arrays_before:
                _restore(snapshot)
            self._groups = {snapshot[0].group_id: snapshot[0] for snapshot in groups_before}
            self._array_groups = {snapshot[0].array_id: snapshot[0] for snapshot in arrays_before}
            self._roi_id_counter = itertools.count(next_counter_before_delete)
            self.roi_ids_renumbered.emit(dict(reverse_id_map))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=tuple(sorted(removed_rois)), reason="added"))
            self._emit_lost_links(lost_link)

        apply()
        label = "Delete ROI" if len(ids_to_delete) == 1 else f"Delete {len(ids_to_delete)} ROIs"
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))

    @instrumented("RoiToolbox.detect_rois")
    def detect_rois(self, detected_rois: list[AreaRoi], array_groups: tuple[RoiArrayGroup, ...] = ()) -> list[int]:
        """Replace every current ROI/group/array with a fresh detection
        result. Takes already-detected ROIs rather than running detection
        itself (`roi.detection.detect_rois()`, a potentially-slow call that
        must run off the GUI thread per CLAUDE.md's non-negotiable
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
        for roi in detected_rois:
            roi.center_x, roi.center_y = self._rounded(roi.center_x), self._rounded(roi.center_y)
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

    # -- command API: arrays ------------------------------------------------
    #
    # The Image panel's "Array" group (docs/roi_array_section_audit_2026-10-08.md). The pixel analysis
    # (`roi/array_pipeline.py`) runs elsewhere, off the GUI thread; these two commands only store its result,
    # each as one undo step. All lengths are diameters in pixels, positions are reference-frame pixels.

    @instrumented("RoiToolbox.place_array")
    def place_array(
        self,
        centers_xy: Sequence[Sequence[float]],
        sample_diameters_px: Sequence[float],
        ring_inner_diameters_px: Sequence[float],
        ring_outer_diameters_px: Sequence[float],
        *,
        rows: int,
        cols: int,
        pitch_x_px: float,
        pitch_y_px: float,
        rotation_deg: float = 0.0,
        located: Sequence[bool] | None = None,
        replace_ids: Collection[int] = (),
        label: str = "Array",
    ) -> list[int]:
        """Add one array of ROIs (row-major, ``rows * cols`` of them) and its `RoiArrayGroup` recipe, replacing
        the ROIs in ``replace_ids`` (the selection the user asked to replace; ``()`` replaces none). Returns the
        new ROIs' ids.

        Everything else keeps its relative order and is renumbered to stay contiguous (the new ROIs are
        numbered after them), exactly as `delete_rois` does, and `roi_ids_renumbered` carries the map so stored
        results follow the survivors and are dropped for the replaced ROIs. Groups and arrays that lose all
        members are pruned; undo restores everything. ``located[i] = False`` marks a ROI the detector could not
        find (it sits at its lattice position: ``inferred``).

        Raises ``ValueError`` before changing anything for non-finite values, a sample diameter below
        `MIN_SAMPLE_DIAMETER_PX`, or a ring that does not satisfy ``0 <= inner < outer``."""
        centers = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
        count = len(centers)
        sample = np.asarray(sample_diameters_px, dtype=np.float64)
        inner = np.asarray(ring_inner_diameters_px, dtype=np.float64)
        outer = np.asarray(ring_outer_diameters_px, dtype=np.float64)
        if count == 0 or not (len(sample) == len(inner) == len(outer) == count):
            raise ValueError("an array needs at least one ROI, with one sample / inner / outer diameter each")
        if count != int(rows) * int(cols):
            raise ValueError(f"{count} ROIs do not make a {rows} x {cols} array")
        if not (np.isfinite(centers).all() and np.isfinite(sample).all() and np.isfinite(inner).all() and np.isfinite(outer).all()):
            raise ValueError("array positions and diameters must be finite numbers")
        if (sample < MIN_SAMPLE_DIAMETER_PX).any():
            raise ValueError(f"sample diameter must be at least {MIN_SAMPLE_DIAMETER_PX:g} px")
        if not ((inner >= 0.0) & (inner < outer)).all():
            raise ValueError("the rim needs 0 <= inner < outer for every ROI")
        located_flags = [True] * count if located is None else [bool(v) for v in located]

        replaced = {int(i) for i in replace_ids if int(i) in self._rois}
        removed_rois = {rid: self._rois[rid] for rid in replaced}
        groups_before = [_snapshot(group) for group in self._groups.values()]
        arrays_before = [_snapshot(array) for array in self._array_groups.values()]
        old_counter_value = (max(self._rois) if self._rois else 0) + 1

        survivors = sorted(rid for rid in self._rois if rid not in replaced)
        id_map = {old_id: new_id for new_id, old_id in enumerate(survivors, start=1)}
        reverse_id_map = {new_id: old_id for old_id, new_id in id_map.items()}
        first_new_id = len(survivors) + 1
        links_before, links_after, lost_link = self._survivor_links(id_map)
        array_number = 1
        for existing in self._array_groups:
            tail = existing.rsplit("_", 1)[-1]
            if tail.isdigit():
                array_number = max(array_number, int(tail) + 1)
        array_id = f"array_{array_number}"
        new_rois: list[AreaRoi] = []
        for index in range(count):
            new_rois.append(
                AreaRoi(
                    area_roi_id=first_new_id + index,
                    center_x=self._rounded(centers[index, 0]),
                    center_y=self._rounded(centers[index, 1]),
                    shape=Circle(float(sample[index])),
                    border=Border.from_ring_diameters(float(sample[index]), float(inner[index]), float(outer[index])),
                    inferred=not located_flags[index],
                    array_id=array_id,
                    created_by="array",
                )
            )
        new_ids = tuple(roi.area_roi_id for roi in new_rois)
        first = new_rois[0]
        recipe = RoiArrayGroup(
            array_id=array_id,
            label=label,
            rows=int(rows),
            cols=int(cols),
            spacing_x_px=float(pitch_x_px),
            spacing_y_px=float(pitch_y_px),
            anchor_x_px=first.center_x,
            anchor_y_px=first.center_y,
            rotation_deg=float(rotation_deg),
            member_area_roi_ids=list(new_ids),
        )

        def apply() -> None:
            renumbered: dict[int, AreaRoi] = {}
            for old_id, new_id in id_map.items():
                roi = self._rois[old_id]
                roi.area_roi_id = new_id
                renumbered[new_id] = roi
            for roi in new_rois:
                renumbered[roi.area_roi_id] = roi
            self._rois = renumbered
            for roi, links in links_after:
                roi.reference_roi_ids = links
            for group in self._groups.values():
                group.area_roi_ids = sorted({id_map[rid] for rid in group.area_roi_ids if rid in id_map})
            self._groups = {gid: group for gid, group in self._groups.items() if group.area_roi_ids}
            for array in self._array_groups.values():
                array.member_area_roi_ids = [id_map[rid] for rid in array.member_area_roi_ids if rid in id_map]
            self._array_groups = {aid: array for aid, array in self._array_groups.items() if array.member_area_roi_ids}
            self._array_groups[array_id] = recipe
            self._roi_id_counter = itertools.count(first_new_id + count)
            self.roi_ids_renumbered.emit(dict(id_map))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=new_ids, reason="added"))
            self._emit_lost_links(lost_link)

        def revert() -> None:
            restored: dict[int, AreaRoi] = {}
            for new_id, roi in self._rois.items():
                if new_id in reverse_id_map:
                    old_id = reverse_id_map[new_id]
                    roi.area_roi_id = old_id
                    restored[old_id] = roi
            restored.update(removed_rois)
            self._rois = dict(sorted(restored.items()))  # rois() is in id order
            for roi, links in links_before:
                roi.reference_roi_ids = links
            self._emit_lost_links(lost_link)
            for snapshot in groups_before:
                _restore(snapshot)
            for snapshot in arrays_before:
                _restore(snapshot)
            self._groups = {snapshot[0].group_id: snapshot[0] for snapshot in groups_before}
            self._array_groups = {snapshot[0].array_id: snapshot[0] for snapshot in arrays_before}
            self._roi_id_counter = itertools.count(old_counter_value)
            self.roi_ids_renumbered.emit(dict(reverse_id_map))
            self.geometry_changed.emit(RoiComputationalChange(roi_ids=new_ids, reason="deleted"))

        apply()
        undo_manager.push(FunctionCommand(label, undo_fn=revert, redo_fn=apply))
        return list(new_ids)

    @instrumented("RoiToolbox.refine_rois")
    def refine_rois(
        self,
        updates: dict[int, tuple[float, float, float, float, float]],
        *,
        label: str = "Refine array",
        cube: int | None = None,
        scope: str = SCOPE_PERSISTENT,
    ) -> None:
        """Set position and diameters of existing ROIs in one undo step: ``{roi_id: (x, y, sample, ring_inner,
        ring_outer)}``. Nothing is renumbered. Raises ``KeyError`` for a missing ROI and ``ValueError``, before
        changing anything, for non-finite values, a sample diameter below `MIN_SAMPLE_DIAMETER_PX` or a ring with
        ``0 <= inner < outer`` violated."""
        rois = self._require_rois(updates)
        targets: list[tuple[AreaRoi, Shape, Border | None]] = []
        for roi in rois:
            if not isinstance(geometry_at(roi, cube).shape, Circle):
                raise ValueError(f"ROI {roi.area_roi_id} is not a circle: refining sets diameters")
            x, y, sample, inner, outer = (float(v) for v in updates[roi.area_roi_id])
            if not all(math.isfinite(v) for v in (x, y, sample, inner, outer)):
                raise ValueError(f"ROI {roi.area_roi_id}: refined values must be finite numbers")
            if sample < MIN_SAMPLE_DIAMETER_PX:
                raise ValueError(f"ROI {roi.area_roi_id}: sample diameter must be at least {MIN_SAMPLE_DIAMETER_PX:g} px")
            if not 0.0 <= inner < outer:
                raise ValueError(f"ROI {roi.area_roi_id}: the rim needs 0 <= inner < outer")
            targets.append((roi, Circle(sample), Border.from_ring_diameters(sample, inner, outer)))
        undo_manager.begin_batch(label)
        try:
            self._set_positions({roi.area_roi_id: tuple(updates[roi.area_roi_id][:2]) for roi in rois}, label, cube=cube, scope=scope)
            self._set_shapes(targets, label, cube=cube, scope=scope)
        finally:
            undo_manager.end_batch()

    # -- command API (§7): ROI order ---------------------------------------
    #
    # A ROI's id is its place in the list, so reordering is a permutation of
    # the ids. Nothing is recomputed: the ROIs themselves are unchanged, and
    # whatever holds a ROI id follows them through `roi_ids_renumbered`.

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
            for roi in self._rois.values():  # links follow their references' new numbers
                if roi.reference_roi_ids:
                    roi.reference_roi_ids = tuple(sorted(mapping[i] for i in roi.reference_roi_ids))
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

    @instrumented("RoiToolbox.reorder_subset")
    def reorder_subset(self, new_order: Sequence[int]) -> None:
        """Put the listed ROIs in ``new_order`` among the id numbers they already hold (the ROI first in the
        list takes the smallest of those numbers, and so on). Every other ROI keeps its number, so reordering a
        selection never changes the numbers of ROIs outside it. One undo step."""
        order = [int(roi_id) for roi_id in new_order]
        unknown = [roi_id for roi_id in order if roi_id not in self._rois]
        if unknown or len(set(order)) != len(order):
            raise ValueError(f"new_order must list existing ROI ids once each (bad: {unknown})")
        placement = renumbering(order, sorted(order))
        self._renumber({roi_id: placement.get(roi_id, roi_id) for roi_id in sorted(self._rois)}, "Reorder ROIs")

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
        by shape (`roi_shapes`) via `_remap_roi_shape` - not a circle-only special case with everything
        else bolted on:
        - circle: only the center moves (the diameter is invariant - this pipeline never scales).
        - rectangle: the center moves and the angle turns with the image (`roi_shapes.remap_rectangle`).
        - mask: the stored raster is re-warped via `remap_mask`
          (`roi.rasterize.remap_roi_mask`). In the near-unreachable case that
          warping leaves no area at all (see that function's docstring for
          why this pipeline essentially never does), the mask is left
          at its **old** pixels rather than silently replaced
          with an empty one, and reported in `mask_shapes_lost` - an empty
          mask would read as "nothing here", not "this needs
          attention".

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
        old_positions: dict[int, tuple] = {}
        new_positions: dict[int, tuple] = {}
        mask_shapes_remapped = 0
        mask_shapes_lost: list[int] = []

        for roi_id, roi in self._rois.items():
            old_positions[roi_id] = (
                roi.center_x,
                roi.center_y,
                None if roi.per_wavelength is None else dict(roi.per_wavelength),
                roi.shape,
                roi.timeline,
            )
            new_center = remap_point(roi.center_x, roi.center_y)
            # the geometry timeline keeps its own centres in the same processed space: they follow the edit too
            new_timeline = roi.timeline.mapped(remap_point) if roi.timeline else None
            new_per_wavelength = (
                None
                if roi.per_wavelength is None
                else {key: remap_point(*value) for key, value in roi.per_wavelength.items()}
            )
            new_shape, lost = _remap_roi_shape(roi, remap_point, remap_mask)
            if lost:
                mask_shapes_lost.append(roi_id)
            mask_shapes_remapped += isinstance(roi.shape, MaskShape) and not lost
            new_positions[roi_id] = (new_center[0], new_center[1], new_per_wavelength, new_shape, new_timeline)

        if not old_positions:
            return RoiRemapReport((), 0, (), ())

        def _write(values: dict[int, tuple]) -> None:
            for roi_id, (x, y, per_wavelength, shape, timeline) in values.items():
                roi = self._rois[roi_id]
                roi.center_x, roi.center_y = x, y
                roi.per_wavelength = per_wavelength
                roi.shape = shape
                roi.timeline = timeline

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
            unsupported_shapes_skipped=(),
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

    def _join_group(self, group: AreaRoiGroup, rois: Sequence[AreaRoi], *, gradient: bool = False) -> None:
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
        if gradient:  # a new group: its members, in id order, run from the group's colour to a lighter shade
            ordered = sorted(joining, key=lambda roi: roi.area_roi_id)
            for roi, color in zip(ordered, gradient_tints(group.sample_color_hex, len(ordered))):
                roi.sample_color_hex = color
            return
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
            self._join_group(group, rois, gradient=True)

        self._grouping_command("Group ROIs", rois, "regroup", mutate)
        return created[0]

    @instrumented("RoiToolbox.group_rois_each")
    def group_rois_each(self, groups: Sequence[tuple[str, Collection[int]]]) -> list[str]:
        """One new group per ``(name, roi_ids)`` entry, in one undo step ("group an array by rows"). Each group
        takes the next unused base colour of the palette in turn, and its members get tints of it. ROIs leave
        any group they were in."""
        entries = [(name, self._require_rois(roi_ids)) for name, roi_ids in groups]
        created: list[str] = []

        def mutate() -> None:
            for name, rois in entries:
                group = self._new_group(name, None, DEFAULT_REFERENCE_COLOR_HEX)
                created.append(group.group_id)
                self._join_group(group, rois, gradient=True)

        self._grouping_command("Group ROIs", [roi for _name, rois in entries for roi in rois], "regroup", mutate)
        return created

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
        default) every member is given a shade of the new colour, running
        smoothly in order of id (as when the group is created), **replacing any colour set on a member by hand**; without
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
                ordered = sorted(members, key=lambda roi: roi.area_roi_id)
                for roi, color in zip(ordered, gradient_tints(base, len(ordered))):
                    roi.sample_color_hex = color

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
