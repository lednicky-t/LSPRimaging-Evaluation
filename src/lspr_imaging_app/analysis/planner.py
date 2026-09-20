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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .provenance import ProvenanceStore


class AnalysisScope(Enum):
    ALL_ROIS = auto()
    SELECTED_ROIS = auto()


@dataclass(frozen=True)
class CurrentInputs:
    """Live settings/geometry, evaluated fresh. Placeholder shape - TODO:
    fill in once the Image Tools / ROI Toolbox query interfaces exist."""


@dataclass(frozen=True)
class RecomputePlan:
    """Which (roi_id, cube_index) cells need recompute vs. can be skipped."""

    to_recompute: tuple[tuple[int, int], ...]
    to_skip: tuple[tuple[int, int], ...]


def plan_recompute(
    stored: ProvenanceStore,
    current_inputs: CurrentInputs,
    scope: AnalysisScope,
) -> RecomputePlan:
    """For every (roi, cube) pair in ``scope``: compute the current
    discretized fingerprint, compare it against ``stored``'s fingerprint for
    that cell (if any), and mark it recompute/skip. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
