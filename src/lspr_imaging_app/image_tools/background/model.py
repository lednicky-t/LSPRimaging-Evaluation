"""Background stage settings (sketch §7 "Image Tools" > Background, §10).

Split out of the old app's ``PreprocessingSettings`` grab-bag - see
``image_tools/geometry/model.py``'s docstring for why.
``local_reference_normalization_enabled`` moves here (not Geometry) because
its GUI control lives under the Background section
(``gui/main_window.py``'s ``background_local_reference_check`` on
``develop``/``main``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BackgroundSettings:
    flatten_background_enabled: bool = False
    flatten_background_sigma_px: float = 48.0
    flatten_background_binning: int = 2
    flatten_background_exclude_area_rois: bool = True
    flatten_background_exclude_mask: bool = False
    flatten_background_exclusion_dilation_px: int = 0
    local_reference_normalization_enabled: bool = False
