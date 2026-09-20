"""Pure per-cell compute (sketch §10: "ports analysis_tasks.py largely
as-is").

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariants: never pool pixels across ROIs before computing
sample/reference ratios; always average already-fitted per-ROI values,
never average raw spectra and fit once.

Not yet ported. Current implementation lives in
``lspr_imaging_app/gui/analysis_tasks.py`` on the ``develop``/``main``
branches (despite living under ``gui/`` today, the feature inventory found
it has zero ``window.*`` references - already clean, pure-compute code).
"""

from __future__ import annotations

from .provenance import ProvenanceRecord


def compute_cell(roi_id: int, cube_index: int, *args: object, **kwargs: object) -> tuple[float, ProvenanceRecord]:
    """Compute one (ROI, cube) cell's reduced value plus the provenance
    record that describes what produced it. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
