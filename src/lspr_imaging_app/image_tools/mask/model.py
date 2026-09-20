"""Mask stage settings (sketch §7 "Image Tools" > Mask, §10).

``MaskSettings`` ported from the old app's ``domain/models.py``, extended
with the histogram-highlight range (``histogram_highlight_min_value``/
``histogram_highlight_max_value``) - moved out of the old
``PreprocessingSettings`` grab-bag (see
``image_tools/geometry/model.py``'s docstring) because it's the persisted
state of the histogram-mask-candidate selection (see
``gui/mask_controller.py``'s ``current_histogram_highlight_mask_raw`` on
``develop``/``main``) - a Mask concern, not a Geometry one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class MaskSettings:
    # Histogram-based mask (intensity ranges)
    histogram_min_value: float | None = None
    histogram_max_value: float | None = None

    # Figure-based mask (spatial tools)
    relative_threshold_fraction: float = 0.18
    relative_profile_sigma_px: float = 48.0
    local_contrast_sigma_px: float = 8.0
    local_contrast_z_threshold: float = 3.0

    # Morphology settings
    morphology_radius_px: int = 2

    # Drawing settings
    brush_size_px: int = 12

    # New mask system state
    histogram_enabled: bool = False
    histogram_mask: np.ndarray | None = None
    figure_enabled: bool = False
    figure_mask: np.ndarray | None = None

    # Persisted histogram-highlight selection (moved from PreprocessingSettings)
    histogram_highlight_min_value: float | None = None
    histogram_highlight_max_value: float | None = None
