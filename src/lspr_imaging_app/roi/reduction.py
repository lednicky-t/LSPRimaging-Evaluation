"""Pure per-ROI pixel reduction math (sketch §10: "ports roi_math.py,
extended with weighted variants per §6a").

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariants: never pool pixels across ROIs before computing
sample/reference ratios; always average already-fitted per-ROI values,
never average raw spectra and fit once.

Not yet ported. Current implementation lives in
``lspr_imaging_app/processing/roi_math.py`` on the ``develop``/``main``
branches. The ``weighted_*`` variants are new work for §6a (fractional
pixel weighting), not a port - ``median``/``trimmed_mean`` need genuine
weighted-median-style algorithms, not just "pass weights through" (sketch
§6a).
"""

from __future__ import annotations

import numpy as np


def mean(values: np.ndarray) -> float:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError


def median(values: np.ndarray) -> float:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError


def trimmed_mean(values: np.ndarray, trim_fraction: float) -> float:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError


def plane_fit(values: np.ndarray) -> float:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting. Not yet implemented - scaffolding
    only."""
    raise NotImplementedError


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting - needs a genuine weighted-median
    algorithm, not a pass-through. Not yet implemented - scaffolding only."""
    raise NotImplementedError


def weighted_trimmed_mean(values: np.ndarray, weights: np.ndarray, trim_fraction: float) -> float:
    """§6a fractional pixel weighting - needs a genuine weighted variant, not
    a pass-through. Not yet implemented - scaffolding only."""
    raise NotImplementedError


def weighted_plane_fit(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting. Not yet implemented - scaffolding
    only."""
    raise NotImplementedError
