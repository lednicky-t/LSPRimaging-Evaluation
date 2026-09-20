"""Geometry stage settings (sketch §7 "Image Tools" > Geometry, §10).

Split out of the current app's single ``PreprocessingSettings`` (domain/
models.py on ``develop``/``main``), which mixes geometry, background-flatten,
and chromatic-correction fields in one dataclass - see the rewrite build
log's 2026-09-20 "preprocess.py scope-check" entry for the full reasoning.
Only the fields Geometry's own pure-math functions (``transform.py``)
actually read (``image_tools_enabled`` through ``crop``) plus spatial/
calibration metadata with no better home (``display_units`` through
``measurement_anchor2_y_px`` - none of these are read by any pure-math
function, they're display-only, ported here as the closest fit).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CropDefinition:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enabled: bool = False


@dataclass(slots=True)
class GeometrySettings:
    image_tools_enabled: bool = True
    rotation_angle_deg: float = 0.0
    rotation_fill_dark: bool = False
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: CropDefinition = field(default_factory=CropDefinition)
    display_units: str = "px"
    scale_bar_visible: bool = False
    calibration_enabled: bool = False
    microns_per_pixel_x: float = 1.0
    microns_per_pixel_y: float = 1.0
    measurement_anchor1_x_px: float = 0.0
    measurement_anchor1_y_px: float = 0.0
    measurement_anchor2_x_px: float = 100.0
    measurement_anchor2_y_px: float = 0.0
