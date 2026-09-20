"""Chromatic model dataclasses (sketch §7 "Chromatic", §10).

Ported verbatim from ``domain/models.py`` on ``develop``/``main`` -
``ChromaticTransformModel`` (named ``ChromaticModel`` in the sketch's own
prose, kept under its real, current name here rather than renamed, so a
future diff against the source stays obvious) and
``ChromaticLandmarkObservation``. No logic changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ChromaticTransformModel:
    spectral_cube_index: int
    wavelength_nm: float
    model_kind: str = "image_affine"
    affine_matrix: list[list[float]] = field(default_factory=lambda: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    global_shift_x_px: float = 0.0
    global_shift_y_px: float = 0.0
    rmse_px: float = 0.0
    mean_score: float = 0.0
    min_score: float = 0.0
    tile_count: int = 0
    inlier_count: int = 0


@dataclass(slots=True)
class ChromaticLandmarkObservation:
    landmark_id: int
    spectral_cube_index: int
    wavelength_nm: float
    x_px: float
    y_px: float
