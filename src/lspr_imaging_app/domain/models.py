from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(slots=True, frozen=True)
class ImageKey:
    wavelength_nm: float
    frame_index: int


@dataclass(slots=True)
class ImageRecord:
    key: ImageKey
    path: Path


@dataclass(slots=True)
class ImageDataset:
    folder: Path
    records: list[ImageRecord]
    source_format: str = "image_stack"

    @property
    def wavelengths_nm(self) -> list[float]:
        return sorted({record.key.wavelength_nm for record in self.records})

    @property
    def frame_indices(self) -> list[int]:
        return sorted({record.key.frame_index for record in self.records})

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
class PreprocessingSettings:
    image_tools_enabled: bool = True
    rotation_angle_deg: float = 0.0
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
    flatten_background_exclude_spots: bool = True
    flatten_background_exclude_mask: bool = False
    chromatic_correction_enabled: bool = False
    chromatic_registration_mode: str = "landmark_radial"
    chromatic_sample_image_count: int = 5
    chromatic_feature_count: int = 5
    chromatic_subpixel_precision: int = 4
    chromatic_tile_size_px: int = 96
    chromatic_search_radius_px: int = 24
    reference_mode: str = "auto"
    reference_wavelength_nm: float | None = None
    reference_frame_index: int = 0
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
class SpotDetectionSettings:
    mode: str = "dark"
    intensity_min_value: float | None = None
    intensity_max_value: float | None = None
    mask_mode: str = "absolute"
    mask_profile_sigma_px: float = 48.0
    mask_relative_threshold_fraction: float = 0.18
    mask_local_contrast_sigma_px: float = 8.0
    mask_local_contrast_z_threshold: float = 3.0
    spot_radius_px: float = 10.0
    ring_inner_radius_px: float = 14.0
    ring_outer_radius_px: float = 18.0
    ignore_marked_pixels: bool = False
    ignored_intensity_value: float | None = None
    ignored_intensity_min_value: float | None = None
    ignored_intensity_max_value: float | None = None
    array_rows: int = 0
    array_cols: int = 0
    array_spacing_px: int = 0


@dataclass(slots=True)
class RoiSpectrum:
    roi_name: str
    frame_index: int
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    roi_mean: np.ndarray
    background_mean: np.ndarray


@dataclass(slots=True)
class RoiMetricSeries:
    roi_name: str
    frame_indices: np.ndarray
    peak_wavelength_nm: np.ndarray
    centroid_nm: np.ndarray
    peak_absorbance: np.ndarray


@dataclass(slots=True)
class FitResult:
    fitted_wavelengths_nm: np.ndarray
    fitted_absorbance: np.ndarray
    coefficients: np.ndarray
    peak_wavelength_nm: float | None
    centroid_nm: float | None
    peak_absorbance: float | None


@dataclass(slots=True)
class AbsorbanceSpectrumResult:
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    spot_mean: np.ndarray
    ring_mean: np.ndarray
    spot_pixel_count: np.ndarray
    ring_pixel_count: np.ndarray
    load_seconds: float = 0.0
    roi_seconds: float = 0.0
    fit_seconds: float = 0.0
    total_seconds: float = 0.0
    spot_results: dict[int, "AbsorbanceSpectrumResult"] = field(default_factory=dict)


@dataclass(slots=True)
class DetectedSpot:
    spot_id: int
    center_x: float
    center_y: float
    radius_px: float
    spot_color_hex: str | None = None
    ring_color_hex: str | None = None
    spot_diameter_px: float | None = None
    ring_inner_diameter_px: float | None = None
    ring_outer_diameter_px: float | None = None
    score: float = 0.0
    support_mean_radius_px: float = 0.0
    support_radius_std_px: float = 0.0
    support_value_mean: float = 0.0
    support_value_std: float = 0.0
    quality_score: float = 0.0
    inferred: bool = False


@dataclass(slots=True)
class SpotGroup:
    group_id: str
    name: str
    spot_color_hex: str = "#f59e0b"
    ring_color_hex: str = "#38bdf8"
    spot_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ChromaticTransformModel:
    frame_index: int
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
    frame_index: int
    wavelength_nm: float
    x_px: float
    y_px: float


@dataclass(slots=True)
class RoiDefinition:
    roi_id: str
    name: str
    shape: str = "ellipse"
    center_x: float = 0.0
    center_y: float = 0.0
    size_x: float = 10.0
    size_y: float = 10.0
    background_padding_px: float = 10.0
    background_width_px: float = 12.0
    enabled: bool = True


@dataclass(slots=True)
class AnalysisState:
    dataset: ImageDataset | None = None
    rois: list[RoiDefinition] = field(default_factory=list)
    preprocessing: PreprocessingSettings = field(default_factory=PreprocessingSettings)
    spot_detection: SpotDetectionSettings = field(default_factory=SpotDetectionSettings)
    detected_spots: list[DetectedSpot] = field(default_factory=list)
    spot_groups: list[SpotGroup] = field(default_factory=list)
    chromatic_models: list[ChromaticTransformModel] = field(default_factory=list)
    chromatic_landmarks: list[ChromaticLandmarkObservation] = field(default_factory=list)
    mask: MaskSettings = field(default_factory=MaskSettings)
