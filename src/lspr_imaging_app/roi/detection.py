"""Pure ROI-detection math (sketch §10: "ports roi_detection.py /
roi_array_geometry.py largely as-is").

No Qt import allowed in this file (AGENTS.md testing rule). Not yet ported.
Current implementation lives in
``lspr_imaging_app/processing/roi_detection.py`` and
``lspr_imaging_app/processing/roi_array_geometry.py`` on the
``develop``/``main`` branches.
"""

from __future__ import annotations

import numpy as np

from .model import AreaRoi


def detect_rois(image: np.ndarray, *args: object, **kwargs: object) -> list[AreaRoi]:
    """Detect ROIs in ``image``. Not yet implemented - scaffolding only."""
    raise NotImplementedError
