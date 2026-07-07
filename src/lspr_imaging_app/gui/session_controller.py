from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, CropDefinition, MaskSettings


class SessionController:
    def __init__(self, window, settings: QSettings) -> None:
        self.window = window
        self.settings = settings

    def restore_layout_preferences(self) -> None:
        self.window._restore_layout_preferences()

    def save_layout_preferences(self) -> None:
        self.window._save_layout_preferences()

    def restore_visual_preferences(self) -> None:
        self.window._restore_visual_preferences()

    def save_visual_preferences(self) -> None:
        self.window._save_visual_preferences()

    def load_processing_state_for_dataset(self) -> None:
        self.window._load_processing_state_for_dataset()

    def save_processing_state_for_dataset(self) -> None:
        self.window._save_processing_state_for_dataset()

    def normalize_mask_application_state(self) -> None:
        self.window._normalize_mask_application_state()

    def processing_state_signature(self) -> str:
        return self.window._processing_state_signature()

    def set_default_processing_state(self) -> None:
        self.window._state.preprocessing.rotation_angle_deg = 0.0
        self.window._state.preprocessing.rotation_fill_dark = False
        self.window._state.preprocessing.flip_horizontal = False
        self.window._state.preprocessing.flip_vertical = False
        self.window._state.preprocessing.crop = CropDefinition()
        self.window._state.preprocessing.flatten_background_enabled = False
        self.window._state.preprocessing.flatten_background_sigma_px = 48.0
        self.window._state.preprocessing.flatten_background_binning = 2
        self.window._state.preprocessing.flatten_background_exclude_area_rois = True
        self.window._state.preprocessing.flatten_background_exclude_mask = False
        self.window._state.preprocessing.chromatic_correction_enabled = False
        self.window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        self.window._state.preprocessing.chromatic_sample_image_count = 5
        self.window._state.preprocessing.chromatic_feature_count = 5
        self.window._state.preprocessing.chromatic_tile_size_px = 96
        self.window._state.preprocessing.chromatic_search_radius_px = 24
        self.window._state.preprocessing.reference_mode = "auto"
        self.window._state.preprocessing.reference_wavelength_nm = None
        self.window._state.preprocessing.reference_spectral_cube_index = 0
        self.window._state.area_roi_settings = AreaRoiDetectionSettings()
        self.window._state.mask = MaskSettings()
        self.window._state.area_rois.clear()
        self.window._state.area_roi_groups.clear()
        self.window._state.chromatic_models.clear()
        self.window._state.chromatic_landmarks.clear()

