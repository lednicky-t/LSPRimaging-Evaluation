"""Shared cosmetic/computational change-event vocabulary.

AGENTS.md ("Communication: typed signals, cosmetic vs. computational") and
docs/rewrite_architecture_sketch_2026-09.md §3 require every module's
mutating signals to distinguish "redraw only" changes from "analysis-store
may now be stale" changes as a *type*, not a naming convention.

``RoiCosmeticChange``/``RoiComputationalChange`` below are the ROI Toolbox's
own payload shape - the one concrete example the sketch works through (§3).
Other Image Tools modules (Geometry, Mask, Chromatic, Background) emit their
own computational-change signals (``geometry_changed``, ``mask_changed``,
etc.) but their affected-scope shape isn't roi_ids - each should define its
own analogous ``...CosmeticChange``/``...ComputationalChange`` pair, colocated
in its own module file, following this same two-type pattern. Not designed
here; scaffolding only covers the worked ROI example.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoiCosmeticChange:
    """Redraw-only ROI change (recolor/relabel/regroup). Never triggers
    Analysis Engine recompute."""

    roi_ids: tuple[int, ...]
    reason: str  # "recolor" | "relabel" | "regroup" - for logging only


@dataclass(frozen=True)
class RoiComputationalChange:
    """ROI change that may invalidate stored analysis results for the
    affected ROIs (and, per sketch §6's neighbor-exclusion rule, possibly
    adjacent ROIs too - that adjacency expansion is the planner's job, not
    this payload's)."""

    roi_ids: tuple[int, ...]
    reason: str  # "moved" | "resized" | "geometry_type_changed" | "added" | "deleted" | "detected"
