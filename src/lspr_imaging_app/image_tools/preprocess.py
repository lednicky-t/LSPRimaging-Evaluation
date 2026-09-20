"""Pure function composing Geometry + Mask + Chromatic + Background into one
processed image (sketch §10: "ports processing/preprocess.py mostly as-is").

No Qt import allowed in this file (AGENTS.md testing rule). Not yet ported.
Current implementation lives in ``lspr_imaging_app/processing/preprocess.py``
on the ``develop``/``main`` branches.
"""

from __future__ import annotations

import numpy as np


def preprocess_image(
    raw_image: np.ndarray,
    *,
    geometry_settings: object,
    mask: np.ndarray | None,
    chromatic_affine: np.ndarray | None,
    background_model: object | None,
) -> np.ndarray:
    """Apply crop/rotate/flip, mask, chromatic warp, and background
    correction to ``raw_image``, in that order. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
