"""Chromatic model dataclasses (sketch §7 "Chromatic", §10).

``ChromaticTransformModel`` (named ``ChromaticModel`` in the sketch's own
prose, kept under its real, current name here rather than renamed, so a
future diff against the source stays obvious) and
``ChromaticLandmarkObservation`` ported verbatim from ``domain/models.py`` on
``develop``/``main``. No logic changed.

``ChromaticSettings``/``GridBoundsDefinition`` added 2026-09-20: split out of
the old app's ``PreprocessingSettings`` grab-bag (its ``chromatic_*``/
``reference_*`` fields) - see ``image_tools/geometry/model.py``'s docstring
for the full reasoning. Chromatic's own pure math (``affine.py``/``warp.py``/
``landmark_autotrack.py`` - originally one ``fitting.py``, split 2026-09-21)
never actually reads these - registration parameters are consumed by
``ChromaticModule``'s ``add_landmark``/not-yet-implemented ``refit``.

``ChromaticModelChange`` added 2026-09-21, alongside ``ChromaticModule``'s
``add_landmark``/``remove_landmark``/``clear_landmarks`` - every landmark
edit invalidates every fitted model (matching the old app's
``finalize_landmark_edit``: a landmark's position changing means the whole
fit it fed into is stale, not just one wavelength's), so unlike
``Roi``/``Geometry``/``Mask``'s cosmetic/computational split, Chromatic's
one signal covers both "the landmark set changed" and (once ``refit()``
exists) "the fitted models changed" - always whole-app-scope, so no
per-item identifier field is needed, just ``reason``.
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


@dataclass(frozen=True)
class ChromaticModelChange:
    """Emitted whenever the landmark set or fitted models change - see
    module docstring for why this is one type, not a cosmetic/
    computational pair."""

    reason: str  # "landmarks_changed" | "refit"


@dataclass(slots=True)
class GridBoundsDefinition:
    """A user-adjustable rectangle (image pixel space) that the chromatic
    reference-point search grid is laid out within, instead of nearly the
    whole image. `enabled=False` (the default) means "use the automatic
    full-image extent" -- unset until the user explicitly drags the overlay.
    """

    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enabled: bool = False


@dataclass(slots=True)
class ChromaticSettings:
    chromatic_correction_enabled: bool = False
    chromatic_registration_mode: str = "landmark_radial"
    chromatic_landmark_kind: str = "corner"
    chromatic_landmark_model: str = "similarity"
    chromatic_grid_bounds: GridBoundsDefinition = field(default_factory=GridBoundsDefinition)
    chromatic_sample_image_count: int = 5
    chromatic_feature_count: int = 15
    chromatic_subpixel_precision: int = 4
    chromatic_tile_size_px: int = 96
    chromatic_search_radius_px: int = 24
    reference_mode: str = "auto"
    reference_wavelength_nm: float | None = None
    reference_spectral_cube_index: int = 0
