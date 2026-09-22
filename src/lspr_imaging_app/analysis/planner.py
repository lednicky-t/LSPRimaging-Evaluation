"""``plan_recompute()`` - the recompute-dependency planner (sketch §6
"Recompute-dependency reasoning"). New code, not a port.

A pure function, no Qt/GUI dependency (same spirit as today's
``gui/analysis_tasks.py``, which has zero ``window.*`` references) -
independently testable. AGENTS.md testing rule: needs a deterministic unit
test per locality rule below.

Locality rules to encode (sketch §6):
- Global no-effect changes (grouping/color/name/range) never touch any
  cell's fingerprint - these are ``CosmeticChange``, the planner never even
  runs for them.
- A single ROI's own geometry change affects only that ROI's cells, *except*
  a reference-ring exclusion can carve into a neighboring ROI's sample
  circle - moving ROI X can change ROI Y's fingerprint if Y is close enough.
  Needs the same spatial-adjacency logic the current pixel-exclusion mask
  already uses, not a naive "only the touched ROI" assumption.
- A mask edit touches only cells whose ROI reach-box overlaps the changed
  region.
- Chromatic refit, background-model change, and reduction-method change are
  each global impact (touch every cell), by the simplified any-change rule.
- Formula/Fit method/Metric choice never touch the store at all.

**Correctness-first scope for this first pass (2026-09-22)**: the locality
rules above describe an *optimization* (skip recomputing a fingerprint you
can already tell wasn't touched) except for one, the ROI-adjacency
exception, which is a genuine *correctness* requirement (skipping it would
wrongly mark an actually-affected neighboring ROI as unchanged). This
implementation recomputes every in-scope cell's live fingerprint fresh and
compares it to what's stored - always correct, not yet optimized to skip
unaffected fingerprint recomputation. The ROI-adjacency exception itself
(does moving ROI X's reference ring actually change what's in ROI Y's own
mask-relevant reach box) is **not yet handled** - flagged as a named
follow-up needing its own investigation into the old app's exact mechanism,
not guessed at here. A mask/geometry/chromatic/background change that
genuinely affects ROI Y through this exception will currently only be
detected once the mask/chromatic snapshot content it depends on actually
changes at ROI Y's own position - which happens to catch the common case
(the change is dataset-wide) but not a purely spatial-adjacency-only effect
isolated to Y without touching Y's own stored inputs' content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .provenance import ProvenanceStore, SettingsSnapshot, compute_fingerprint


class AnalysisScope(Enum):
    ALL_ROIS = auto()
    SELECTED_ROIS = auto()


@dataclass(frozen=True)
class CurrentInputs:
    """Live settings/geometry, evaluated fresh, already gathered by the
    caller from the real modules (this file stays Qt/dataset-free - see
    module docstring). Cube-level settings (geometry/mask/chromatic/
    background/reduction) are shared across every ROI at that cube - only
    `roi_geometries` varies per ROI - so this is intentionally *not*
    duplicated per (roi, cube) pair.
    """

    reduction_method: str
    cube_settings: dict[int, dict[float, SettingsSnapshot]]  # cube_index -> {wavelength_nm: SettingsSnapshot}
    roi_geometries: dict[int, dict]  # roi_id -> this ROI's own AreaRoi geometry fields, as a plain dict
    settings_dir: Path


@dataclass(frozen=True)
class RecomputePlan:
    """Which (roi_id, cube_index) cells need recompute vs. can be skipped."""

    to_recompute: tuple[tuple[int, int], ...]
    to_skip: tuple[tuple[int, int], ...]


def _cells_in_scope(
    scope: AnalysisScope,
    all_roi_ids: tuple[int, ...],
    selected_roi_ids: tuple[int, ...],
    cube_indices: tuple[int, ...],
) -> tuple[tuple[int, int], ...]:
    roi_ids = all_roi_ids if scope is AnalysisScope.ALL_ROIS else selected_roi_ids
    return tuple((roi_id, cube_index) for roi_id in roi_ids for cube_index in cube_indices)


def plan_recompute(
    stored: ProvenanceStore,
    current_inputs: CurrentInputs,
    scope: AnalysisScope,
    *,
    all_roi_ids: tuple[int, ...],
    selected_roi_ids: tuple[int, ...] = (),
) -> RecomputePlan:
    """For every (roi, cube) pair in ``scope``: compute the current
    discretized fingerprint, compare it against ``stored``'s fingerprint for
    that cell (if any), and mark it recompute/skip.

    ``all_roi_ids``/``selected_roi_ids`` and the cube indices to consider
    come from ``current_inputs`` (``roi_geometries``'s keys and
    ``cube_settings``'s keys respectively) plus the explicit
    ``all_roi_ids``/``selected_roi_ids`` split, since ``CurrentInputs``
    itself doesn't distinguish "all" from "currently selected" - that's a
    UI-level notion the caller resolves before calling this.
    """
    cube_indices = tuple(sorted(current_inputs.cube_settings.keys()))
    cells = _cells_in_scope(scope, all_roi_ids, selected_roi_ids, cube_indices)

    to_recompute: list[tuple[int, int]] = []
    to_skip: list[tuple[int, int]] = []

    for roi_id, cube_index in cells:
        roi_geometry = current_inputs.roi_geometries.get(roi_id)
        wavelength_settings = current_inputs.cube_settings.get(cube_index)
        if roi_geometry is None or wavelength_settings is None:
            # Nothing live to compare against (e.g. a stale roi_id/cube_index
            # slipped into scope) - treat as needing recompute rather than
            # silently skipping, since "unknown" must never mean "assumed
            # already correct".
            to_recompute.append((roi_id, cube_index))
            continue

        live_fingerprint = compute_fingerprint(
            roi_geometry=roi_geometry,
            reduction_method=current_inputs.reduction_method,
            per_wavelength_settings=wavelength_settings,
            settings_dir=current_inputs.settings_dir,
        )
        stored_fingerprint = stored.fingerprint_for(roi_id, cube_index)
        if stored_fingerprint == live_fingerprint:
            to_skip.append((roi_id, cube_index))
        else:
            to_recompute.append((roi_id, cube_index))

    return RecomputePlan(to_recompute=tuple(to_recompute), to_skip=tuple(to_skip))
