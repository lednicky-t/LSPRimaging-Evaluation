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


@dataclass(frozen=True)
class MaskComputationalChange:
    """Mask's own computational-change payload (change_events.py's
    two-type pattern, §3) - whole-image scope like
    `GeometryComputationalChange`/`BackgroundComputationalChange`, no
    `roi_ids` field. Only the committed raster `file_mask` is computational
    - it's what `ignored_pixel_mask`/`flatten_background` actually read.
    Tool-tuning settings and the histogram-highlight selection are
    `MaskCosmeticChange` instead (see that type's docstring)."""

    reason: str  # "file_mask"


@dataclass(frozen=True)
class MaskCosmeticChange:
    """Mask's cosmetic-change payload. `relative`/`local_contrast`/
    `morphology`/`histogram` tool-tuning settings and the histogram-
    highlight drag selection don't themselves invalidate any stored result
    - unlike Geometry's crop/rotate/flip (which apply continuously, every
    render), a Mask tool's settings only affect anything once an explicit
    "apply" action (not built this pass - see module.py's docstring)
    merges a computed candidate into `file_mask`. Until then, changing a
    threshold slider is exactly like dragging Geometry's measurement ruler
    - a preview input, not a result-affecting one."""

    reason: str  # "tool_settings" | "histogram_highlight"


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
