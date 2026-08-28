from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from lspr_core import ImagingAcquisitionMetadata

from lspr_imaging_app.domain.exclusions import ImageExclusionRule


@dataclass(slots=True, frozen=True)
class ImageKey:
    wavelength_nm: float
    spectral_cube_index: int


@dataclass(slots=True)
class ImageRecord:
    key: ImageKey
    path: Path


@dataclass(slots=True)
class ImageDataset:
    folder: Path
    records: list[ImageRecord]
    source_format: str = "image_stack"
    acquisition_metadata: ImagingAcquisitionMetadata | None = None
    home_folder: Path | None = None
    """The folder this dataset was loaded *from*, when that differs from
    `folder` (the actual TIFF/OME-Zarr location) - e.g. `load_dataset`
    discovering the data one level below the folder it was pointed at (see
    `io/dataset.py`'s `discover_dataset_candidates`). `None` when they're the
    same (the common case: pointing directly at the data). Use the `home`
    property rather than this field directly."""

    @property
    def home(self) -> Path:
        """Where this app's own state (analysis/, sessions, ROI table, masks,
        acquisition metadata sidecar) is saved - `home_folder` if set,
        otherwise `folder`. Kept separate from `folder` so that data never
        gets written into a raw TIFF/OME-Zarr folder it doesn't own."""
        return self.home_folder if self.home_folder is not None else self.folder

    @property
    def wavelengths_nm(self) -> list[float]:
        return sorted({record.key.wavelength_nm for record in self.records})

    @property
    def spectral_cube_indices(self) -> list[int]:
        return sorted({record.key.spectral_cube_index for record in self.records})

    @property
    def is_ome_zarr(self) -> bool:
        return str(self.source_format).lower() in {"ome_zarr", "ome-zarr", "zarr"}

    @property
    def is_image_stack(self) -> bool:
        return not self.is_ome_zarr

    @property
    def format_label(self) -> str:
        return "OME-Zarr" if self.is_ome_zarr else "ImageStack"


@dataclass(slots=True)
class CropDefinition:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    enabled: bool = False


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
class PreprocessingSettings:
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
    flatten_background_enabled: bool = False
    flatten_background_sigma_px: float = 48.0
    flatten_background_binning: int = 2
    flatten_background_exclude_area_rois: bool = True
    flatten_background_exclude_mask: bool = False
    flatten_background_exclusion_dilation_px: int = 0
    local_reference_normalization_enabled: bool = False
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
    histogram_highlight_min_value: float | None = None
    histogram_highlight_max_value: float | None = None


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


@dataclass(slots=True)
class AreaRoiDetectionSettings:
    mode: str = "dark"
    intensity_min_value: float | None = None
    intensity_max_value: float | None = None
    mask_mode: str = "absolute"
    mask_profile_sigma_px: float = 48.0
    mask_relative_threshold_fraction: float = 0.18
    mask_local_contrast_sigma_px: float = 8.0
    mask_local_contrast_z_threshold: float = 3.0
    sample_radius_px: float = 10.0
    reference_inner_radius_px: float = 14.0
    reference_outer_radius_px: float = 18.0
    ignore_marked_pixels: bool = False
    ignored_intensity_value: float | None = None
    ignored_intensity_min_value: float | None = None
    ignored_intensity_max_value: float | None = None
    array_rows: int = 0
    array_cols: int = 0
    array_spacing_px: int = 0
    # ROI's math: how each ROI pair's masked pixels become the per-wavelength
    # sample/reference value ("mean"/"median"/"trimmed_mean"/"plane_fit"), and
    # how those two values combine into the final value. Shared across every
    # ROI pair - no per-ROI override yet, see processing/roi_math.py.
    reduction_method: str = "mean"
    trimmed_mean_fraction: float = 0.10
    formula_key: str = "absorbance"


@dataclass(slots=True)
class FitResult:
    fitted_wavelengths_nm: np.ndarray
    fitted_values: np.ndarray
    coefficients: np.ndarray
    peak_wavelength_nm: float | None
    centroid_nm: float | None
    peak_value: float | None


@dataclass(slots=True)
class FormulaSpectrumResult:
    wavelengths_nm: np.ndarray
    # Whatever formula (formula_key below) actually computed - "absorbance"
    # (-log10) is only the default. See processing/analysis.py:formula_value.
    formula_values: np.ndarray
    sample_mean: np.ndarray
    reference_mean: np.ndarray
    sample_pixel_count: np.ndarray
    reference_pixel_count: np.ndarray
    load_seconds: float = 0.0
    roi_seconds: float = 0.0
    fit_seconds: float = 0.0
    total_seconds: float = 0.0
    area_roi_results: dict[int, "FormulaSpectrumResult"] = field(default_factory=dict)
    # Self-describing: the ROI's-math settings actually in effect when this
    # result was computed, so a cached result stays meaningful even if the
    # live settings have since changed. Defaults match the pre-existing
    # hardcoded behavior (plain mean, A = log10(reference/sample)).
    reduction_method: str = "mean"
    formula_key: str = "absorbance"


@dataclass(slots=True)
class RoiMask:
    """Cropped boolean mask fallback for an irregular sample/reference region.

    ``mask`` is cropped to its bounding box, not full-image sized; (x0, y0) is
    the top-left corner of that box in processed-image pixel coordinates.
    """

    x0: int
    y0: int
    mask: np.ndarray


@dataclass(slots=True)
class AreaRoi:
    area_roi_id: int
    center_x: float
    center_y: float
    sample_radius_px: float
    sample_color_hex: str | None = None
    reference_color_hex: str | None = None
    sample_diameter_px: float | None = None
    reference_inner_diameter_px: float | None = None
    reference_outer_diameter_px: float | None = None
    score: float = 0.0
    support_mean_radius_px: float = 0.0
    support_radius_std_px: float = 0.0
    support_value_mean: float = 0.0
    support_value_std: float = 0.0
    quality_score: float = 0.0
    inferred: bool = False
    # Geometry escape hatch: "circle"/"annulus" (default) reproduce the existing
    # radius-based behavior exactly; "mask" uses sample_mask/reference_mask instead.
    sample_geometry_type: str = "circle"
    sample_mask: RoiMask | None = None
    reference_geometry_type: str = "annulus"
    reference_mask: RoiMask | None = None
    array_id: str | None = None
    label: str | None = None
    created_by: str = "user"
    notes: str | None = None
    # Per-wavelength position overrides, keyed by the same (spectral_cube_index,
    # wavelength_nm) tuple used everywhere else as the image identity. Only one
    # position is stored for an ROI at all - center_x/center_y, on the reference
    # image. Every other (cube, wavelength) position is computed on demand from that center
    # through the chromatic-correction affine for that key (see
    # ChromaticController.affine_for_image_key, ImageRenderManager.display_rois)
    # and is never written back here. The one exception is a manual nudge while
    # viewing a non-reference wavelength, which writes into this dict instead of
    # center_x/center_y (see RoiGeometryMixin._set_roi_position_for_current_view)
    # and survives until the next chromatic re-fit clears window._state.chromatic_models
    # out from under it. So in practice this dict is empty for the overwhelming
    # majority of ROIs and only ever holds a handful of deliberate manual edits.
    per_wavelength: dict[tuple[int, float], tuple[float, float]] | None = None


@dataclass(slots=True)
class AreaRoiGroup:
    group_id: str
    name: str
    sample_color_hex: str = "#f59e0b"
    reference_color_hex: str = "#38bdf8"
    area_roi_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RoiArrayGroup:
    """Persisted grid recipe tying together AreaRoi members stamped as a periodic array.

    This is the recipe, not a duplicate of member geometry: rows/cols/spacing/anchor
    can be edited later to regenerate or nudge the whole array as a unit.
    (anchor_x_px, anchor_y_px) is the position of the row=0, col=0 member, not
    the array's visual center.
    """

    array_id: str
    label: str
    rows: int
    cols: int
    spacing_x_px: float
    spacing_y_px: float
    anchor_x_px: float
    anchor_y_px: float
    rotation_deg: float = 0.0
    member_area_roi_ids: list[int] = field(default_factory=list)


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


@dataclass(slots=True)
class StatisticsSettings:
    """Post-processing applied to an already-computed sensorgram trace (see
    processing/trace_statistics.py) - never touches raw pixels, so none of
    this affects the ROI's-math/Metric-trace cache signatures. Persisted with
    the dataset's saved session (same reasoning as AreaRoiDetectionSettings'
    reduction/formula fields): these reshape what the plotted numbers mean
    (a baseline-corrected trace reads as relative shift, not absolute value),
    so they should travel with the session rather than being a silent
    per-machine QSettings preference.

    smoothing/spike-rejection order: spike rejection runs first (cleans
    transient outliers before smoothing blends them into neighbors), then
    smoothing, then baseline correction (a simple offset, applied last).

    baseline_window_start/end are in the sensorgram's current x-axis units -
    elapsed seconds if acquisition timing metadata is loaded, otherwise raw
    spectral-cube index (see AnalysisController._sensorgram_x_values).
    """

    smoothing_method: str = "none"  # "none" | "savgol" | "moving_average"
    smoothing_window: int = 15
    smoothing_polyorder: int = 2
    spike_rejection_enabled: bool = False
    spike_rejection_method: str = "hampel"  # "hampel" | "running_median"
    spike_rejection_window: int = 5
    spike_rejection_threshold: float = 3.5
    baseline_enabled: bool = False
    baseline_window_start: float | None = None
    baseline_window_end: float | None = None
    group_stats_enabled: bool = False
    group_stats_center: str = "mean"  # "mean" | "median"
    group_stats_band: str = "sd"  # "sd" | "sem"


@dataclass(slots=True)
class AnalysisState:
    dataset: ImageDataset | None = None
    preprocessing: PreprocessingSettings = field(default_factory=PreprocessingSettings)
    area_roi_settings: AreaRoiDetectionSettings = field(default_factory=AreaRoiDetectionSettings)
    area_rois: list[AreaRoi] = field(default_factory=list)
    area_roi_groups: list[AreaRoiGroup] = field(default_factory=list)
    area_roi_arrays: list[RoiArrayGroup] = field(default_factory=list)
    chromatic_models: list[ChromaticTransformModel] = field(default_factory=list)
    chromatic_landmarks: list[ChromaticLandmarkObservation] = field(default_factory=list)
    mask: MaskSettings = field(default_factory=MaskSettings)
    image_exclusions: list[ImageExclusionRule] = field(default_factory=list)
    statistics_settings: StatisticsSettings = field(default_factory=StatisticsSettings)
