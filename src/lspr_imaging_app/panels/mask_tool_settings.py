"""Pushing the Mask tools' tuning numbers into `MaskModule` (2026-11-15: lifted out of `panels/image/mask_edit_panels.py`).

`MaskModule.set_tool_settings` takes every tunable at once; a form that edits only some of them (the toolbar's "Select by value" and
"Modify selection", the Mask tab's Draw / brush size) reads the current settings and overrides just its own fields. The numbers are
saved with the session (`MaskSettings`), so they are remembered like any other control.
"""

from __future__ import annotations

from ..image_tools import MaskModule
from ..image_tools.mask.model import MaskSettings


def push_tool_settings(mask: MaskModule, **overrides: object) -> None:
    """Set the named tool settings (`relative_threshold_fraction`, `relative_profile_sigma_px`, `local_contrast_sigma_px`,
    `local_contrast_z_threshold`, `morphology_radius_px`, `brush_size_px`, ...) and leave the others as they are."""
    current: MaskSettings = mask.settings()
    fields: dict[str, object] = {
        "histogram_min_value": current.histogram_min_value,
        "histogram_max_value": current.histogram_max_value,
        "relative_threshold_fraction": current.relative_threshold_fraction,
        "relative_profile_sigma_px": current.relative_profile_sigma_px,
        "local_contrast_sigma_px": current.local_contrast_sigma_px,
        "local_contrast_z_threshold": current.local_contrast_z_threshold,
        "morphology_radius_px": current.morphology_radius_px,
        "brush_size_px": current.brush_size_px,
    }
    fields.update(overrides)
    mask.set_tool_settings(**fields)  # type: ignore[arg-type]
