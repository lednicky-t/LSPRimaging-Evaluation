"""Pure mask/pixel-weight rasterization (sketch §10: "ports roi_rasterize.py,
extended with supersample-and-downsample per §6a").

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariant: cache per-ROI analysis masks at that ROI's own
small bounding box, never full-image-plane size (full-size caching measured
8-14GB RAM at realistic ROI counts).

Not yet ported. Current implementation lives in
``lspr_imaging_app/processing/roi_rasterize.py`` on the ``develop``/``main``
branches. ``rasterize_fractional`` is new work for §6a, not a port - one
shared supersample-and-downsample implementation for every geometry type
(circle/rectangle/polygon/arbitrary mask), not shape-specific formulas.
"""

from __future__ import annotations

import numpy as np

from .model import AreaRoi


def rasterize_binary(roi: AreaRoi, bounding_box: tuple[int, int, int, int]) -> np.ndarray:
    """Binary (center-in-shape) rasterization within ``bounding_box``. Not
    yet implemented - scaffolding only."""
    raise NotImplementedError


def rasterize_fractional(
    roi: AreaRoi, bounding_box: tuple[int, int, int, int], supersample_factor: int = 8
) -> np.ndarray:
    """§6a fractional pixel weighting via supersample-and-downsample. Not
    yet implemented - scaffolding only."""
    raise NotImplementedError
