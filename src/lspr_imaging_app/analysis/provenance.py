"""Per-cell provenance records + fingerprint computation + dedup table
(sketch §5 "The analysis store: one file, per-cell provenance"). New code,
not a port.

No Qt import allowed in this file (AGENTS.md testing rule). Still open
(sketch §9, item 1): ``provenance_table.json``'s deduplication scheme (how
cells reference a shared fingerprint blob without repeating it) is named but
not designed - this file's dedup-table shape is a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProvenanceRecord:
    """The narrow, complete set of discretized inputs that produced one
    (ROI, cube) cell's value: this ROI's own geometry, the mask state within
    this ROI's own reach box only, the chromatic affine for that image key,
    the background model, the reduction method (sketch §5). Position/
    radius/model values are compared with a small fixed rounding (e.g.
    1e-9) to absorb floating-point re-serialization noise, not as a
    materiality judgment (sketch §5, "Simplified 2026-09-20")."""


class ProvenanceStore:
    """Read-only view of what's on disk now - the ``stored`` argument to
    :func:`~lspr_imaging_app.analysis.planner.plan_recompute`. Not yet
    implemented - scaffolding only."""

    def fingerprint_for(self, roi_id: int, cube_index: int) -> ProvenanceRecord | None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError


def compute_fingerprint(*args: object, **kwargs: object) -> ProvenanceRecord:
    """Compute the current, live fingerprint for one (ROI, cube) cell -
    cheap, no pixel access, just reading current settings/geometry (sketch
    §5). Not yet implemented - scaffolding only."""
    raise NotImplementedError
