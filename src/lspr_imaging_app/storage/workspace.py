from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lspr_imaging_app.domain.models import (
    ChromaticLandmarkObservation,
    ChromaticTransformModel,
    CropDefinition,
    DetectedSpot,
    MaskSettings,
    PreprocessingSettings,
    RoiDefinition,
    SpotGroup,
    SpotDetectionSettings,
)


def _encode_mask_payload(mask: np.ndarray) -> dict:
    mask_array = np.asarray(mask, dtype=bool)
    flat = np.packbits(mask_array.ravel(order="C"))
    return {
        "shape": [int(mask_array.shape[0]), int(mask_array.shape[1])] if mask_array.ndim == 2 else list(mask_array.shape),
        "data": base64.b64encode(flat.tobytes()).decode("ascii"),
    }


def _decode_mask_payload(payload: dict) -> np.ndarray | None:
    shape_raw = payload.get("shape", [])
    data_raw = payload.get("data", "")
    if not (isinstance(shape_raw, list) and len(shape_raw) == 2 and isinstance(data_raw, str)):
        return None
    try:
        height = int(shape_raw[0])
        width = int(shape_raw[1])
        if height <= 0 or width <= 0:
            return None
        packed = base64.b64decode(data_raw.encode("ascii"))
        unpacked = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), count=height * width)
        return unpacked.astype(bool, copy=False).reshape((height, width))
    except Exception:
        return None


def _encode_mask_settings(mask_settings: MaskSettings) -> dict:
    payload: dict[str, object] = {
        "histogram_min_value": mask_settings.histogram_min_value,
        "histogram_max_value": mask_settings.histogram_max_value,
        "relative_threshold_fraction": float(mask_settings.relative_threshold_fraction),
        "relative_profile_sigma_px": float(mask_settings.relative_profile_sigma_px),
        "local_contrast_sigma_px": float(mask_settings.local_contrast_sigma_px),
        "local_contrast_z_threshold": float(mask_settings.local_contrast_z_threshold),
        "morphology_radius_px": int(mask_settings.morphology_radius_px),
        "brush_size_px": int(mask_settings.brush_size_px),
        "histogram_enabled": bool(mask_settings.histogram_enabled),
        "figure_enabled": bool(mask_settings.figure_enabled),
    }
    if mask_settings.histogram_mask is not None:
        payload["histogram_mask"] = _encode_mask_payload(mask_settings.histogram_mask)
    if mask_settings.figure_mask is not None:
        payload["figure_mask"] = _encode_mask_payload(mask_settings.figure_mask)
    return payload


def _decode_mask_settings(payload: dict) -> MaskSettings:
    histogram_mask = payload.get("histogram_mask")
    figure_mask = payload.get("figure_mask")
    return MaskSettings(
        histogram_min_value=(
            None if payload.get("histogram_min_value") is None else float(payload.get("histogram_min_value"))
        ),
        histogram_max_value=(
            None if payload.get("histogram_max_value") is None else float(payload.get("histogram_max_value"))
        ),
        relative_threshold_fraction=float(payload.get("relative_threshold_fraction", 0.18)),
        relative_profile_sigma_px=float(payload.get("relative_profile_sigma_px", 48.0)),
        local_contrast_sigma_px=float(payload.get("local_contrast_sigma_px", 8.0)),
        local_contrast_z_threshold=float(payload.get("local_contrast_z_threshold", 3.0)),
        morphology_radius_px=int(payload.get("morphology_radius_px", 2)),
        brush_size_px=int(payload.get("brush_size_px", 12)),
        histogram_enabled=bool(payload.get("histogram_enabled", False)),
        histogram_mask=(
            _decode_mask_payload(histogram_mask) if isinstance(histogram_mask, dict) else None
        ),
        figure_enabled=bool(payload.get("figure_enabled", False)),
        figure_mask=(
            _decode_mask_payload(figure_mask) if isinstance(figure_mask, dict) else None
        ),
    )


def save_rois(path: Path, rois: list[RoiDefinition]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"rois": [asdict(roi) for roi in rois]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_rois(path: Path) -> list[RoiDefinition]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rois_raw = payload.get("rois", [])
    rois: list[RoiDefinition] = []
    for raw in rois_raw:
        if not isinstance(raw, dict):
            continue
        rois.append(
            RoiDefinition(
                roi_id=str(raw.get("roi_id", "")),
                name=str(raw.get("name", "ROI")),
                shape=str(raw.get("shape", "ellipse")),
                center_x=float(raw.get("center_x", 0.0)),
                center_y=float(raw.get("center_y", 0.0)),
                size_x=float(raw.get("size_x", 10.0)),
                size_y=float(raw.get("size_y", 10.0)),
                background_padding_px=float(raw.get("background_padding_px", 10.0)),
                background_width_px=float(raw.get("background_width_px", 12.0)),
                enabled=bool(raw.get("enabled", True)),
            )
        )
    return rois


def save_preprocessing(path: Path, settings: PreprocessingSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "image_tools_enabled": bool(getattr(settings, "image_tools_enabled", True)),
        "rotation_angle_deg": float(settings.rotation_angle_deg),
        "flip_horizontal": bool(settings.flip_horizontal),
        "flip_vertical": bool(settings.flip_vertical),
        "crop": asdict(settings.crop),
        "display_units": str(getattr(settings, "display_units", "px")),
        "scale_bar_visible": bool(getattr(settings, "scale_bar_visible", False)),
        "calibration_enabled": bool(getattr(settings, "calibration_enabled", False)),
        "microns_per_pixel_x": float(getattr(settings, "microns_per_pixel_x", 1.0)),
        "microns_per_pixel_y": float(getattr(settings, "microns_per_pixel_y", 1.0)),
        "measurement_anchor1_x_px": float(getattr(settings, "measurement_anchor1_x_px", 0.0)),
        "measurement_anchor1_y_px": float(getattr(settings, "measurement_anchor1_y_px", 0.0)),
        "measurement_anchor2_x_px": float(getattr(settings, "measurement_anchor2_x_px", 100.0)),
        "measurement_anchor2_y_px": float(getattr(settings, "measurement_anchor2_y_px", 0.0)),
        "flatten_background_enabled": bool(settings.flatten_background_enabled),
        "flatten_background_sigma_px": float(settings.flatten_background_sigma_px),
        "flatten_background_binning": int(settings.flatten_background_binning),
        "flatten_background_exclude_spots": bool(settings.flatten_background_exclude_spots),
        "flatten_background_exclude_mask": bool(settings.flatten_background_exclude_mask),
        "chromatic_correction_enabled": bool(settings.chromatic_correction_enabled),
        "chromatic_registration_mode": str(settings.chromatic_registration_mode),
        "chromatic_sample_image_count": int(settings.chromatic_sample_image_count),
        "chromatic_feature_count": int(settings.chromatic_feature_count),
        "chromatic_subpixel_precision": int(getattr(settings, "chromatic_subpixel_precision", 4)),
        "chromatic_tile_size_px": int(settings.chromatic_tile_size_px),
        "chromatic_search_radius_px": int(settings.chromatic_search_radius_px),
        "reference_mode": str(settings.reference_mode),
        "reference_wavelength_nm": settings.reference_wavelength_nm,
        "reference_frame_index": int(settings.reference_frame_index),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_preprocessing(path: Path) -> PreprocessingSettings:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_crop = payload.get("crop", {})
    if not isinstance(raw_crop, dict):
        raw_crop = {}
    return PreprocessingSettings(
        image_tools_enabled=bool(payload.get("image_tools_enabled", True)),
        rotation_angle_deg=float(payload.get("rotation_angle_deg", 0.0)),
        flip_horizontal=bool(payload.get("flip_horizontal", False)),
        flip_vertical=bool(payload.get("flip_vertical", False)),
        crop=CropDefinition(
            x=int(raw_crop.get("x", 0)),
            y=int(raw_crop.get("y", 0)),
            width=int(raw_crop.get("width", 0)),
            height=int(raw_crop.get("height", 0)),
            enabled=bool(raw_crop.get("enabled", False)),
        ),
        display_units=str(payload.get("display_units", "px")),
        scale_bar_visible=bool(payload.get("scale_bar_visible", False)),
        calibration_enabled=bool(payload.get("calibration_enabled", False)),
        microns_per_pixel_x=float(payload.get("microns_per_pixel_x", 1.0)),
        microns_per_pixel_y=float(payload.get("microns_per_pixel_y", 1.0)),
        measurement_anchor1_x_px=float(payload.get("measurement_anchor1_x_px", 0.0)),
        measurement_anchor1_y_px=float(payload.get("measurement_anchor1_y_px", 0.0)),
        measurement_anchor2_x_px=float(payload.get("measurement_anchor2_x_px", 100.0)),
        measurement_anchor2_y_px=float(payload.get("measurement_anchor2_y_px", 0.0)),
        flatten_background_enabled=bool(payload.get("flatten_background_enabled", False)),
        flatten_background_sigma_px=float(payload.get("flatten_background_sigma_px", 48.0)),
        flatten_background_binning=max(int(payload.get("flatten_background_binning", 2)), 1),
        flatten_background_exclude_spots=bool(payload.get("flatten_background_exclude_spots", True)),
        flatten_background_exclude_mask=bool(payload.get("flatten_background_exclude_mask", False)),
        chromatic_correction_enabled=bool(payload.get("chromatic_correction_enabled", False)),
        chromatic_registration_mode=str(payload.get("chromatic_registration_mode", "landmark_radial")),
        chromatic_sample_image_count=int(payload.get("chromatic_sample_image_count", 5)),
        chromatic_feature_count=int(payload.get("chromatic_feature_count", 5)),
        chromatic_subpixel_precision=int(payload.get("chromatic_subpixel_precision", 4)),
        chromatic_tile_size_px=int(payload.get("chromatic_tile_size_px", 96)),
        chromatic_search_radius_px=int(payload.get("chromatic_search_radius_px", 24)),
        reference_mode=str(payload.get("reference_mode", "auto")),
        reference_wavelength_nm=(
            None if payload.get("reference_wavelength_nm") is None else float(payload.get("reference_wavelength_nm"))
        ),
        reference_frame_index=int(payload.get("reference_frame_index", 0)),
    )


def save_processing_profile(
    path: Path,
    preprocessing: PreprocessingSettings,
    spot_detection: SpotDetectionSettings,
    detected_spots: list[DetectedSpot],
    spot_groups: list[SpotGroup] | None = None,
    chromatic_models: list[ChromaticTransformModel] | None = None,
    chromatic_landmarks: list[ChromaticLandmarkObservation] | None = None,
    analysis_cache: dict | None = None,
    session_mask: dict | None = None,
    mask_settings: MaskSettings | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile_type": "lspr_imaging_processing",
        "profile_version": 1,
        "preprocessing": {
            "image_tools_enabled": bool(getattr(preprocessing, "image_tools_enabled", True)),
            "rotation_angle_deg": float(preprocessing.rotation_angle_deg),
            "flip_horizontal": bool(preprocessing.flip_horizontal),
            "flip_vertical": bool(preprocessing.flip_vertical),
            "crop": asdict(preprocessing.crop),
            "display_units": str(getattr(preprocessing, "display_units", "px")),
            "scale_bar_visible": bool(getattr(preprocessing, "scale_bar_visible", False)),
            "calibration_enabled": bool(getattr(preprocessing, "calibration_enabled", False)),
            "microns_per_pixel_x": float(getattr(preprocessing, "microns_per_pixel_x", 1.0)),
            "microns_per_pixel_y": float(getattr(preprocessing, "microns_per_pixel_y", 1.0)),
            "measurement_anchor1_x_px": float(getattr(preprocessing, "measurement_anchor1_x_px", 0.0)),
            "measurement_anchor1_y_px": float(getattr(preprocessing, "measurement_anchor1_y_px", 0.0)),
            "measurement_anchor2_x_px": float(getattr(preprocessing, "measurement_anchor2_x_px", 100.0)),
            "measurement_anchor2_y_px": float(getattr(preprocessing, "measurement_anchor2_y_px", 0.0)),
            "flatten_background_enabled": bool(preprocessing.flatten_background_enabled),
            "flatten_background_sigma_px": float(preprocessing.flatten_background_sigma_px),
            "flatten_background_binning": int(preprocessing.flatten_background_binning),
            "flatten_background_exclude_spots": bool(preprocessing.flatten_background_exclude_spots),
            "flatten_background_exclude_mask": bool(preprocessing.flatten_background_exclude_mask),
            "chromatic_correction_enabled": bool(preprocessing.chromatic_correction_enabled),
            "chromatic_registration_mode": str(preprocessing.chromatic_registration_mode),
            "chromatic_sample_image_count": int(preprocessing.chromatic_sample_image_count),
            "chromatic_feature_count": int(preprocessing.chromatic_feature_count),
            "chromatic_subpixel_precision": int(getattr(preprocessing, "chromatic_subpixel_precision", 4)),
            "chromatic_tile_size_px": int(preprocessing.chromatic_tile_size_px),
            "chromatic_search_radius_px": int(preprocessing.chromatic_search_radius_px),
            "reference_mode": str(preprocessing.reference_mode),
            "reference_wavelength_nm": preprocessing.reference_wavelength_nm,
            "reference_frame_index": int(preprocessing.reference_frame_index),
            "histogram_highlight_min_value": preprocessing.histogram_highlight_min_value,
            "histogram_highlight_max_value": preprocessing.histogram_highlight_max_value,
        },
        "spot_detection": asdict(spot_detection),
        "detected_spots": [asdict(spot) for spot in detected_spots],
        "spot_groups": [asdict(group) for group in (spot_groups or [])],
        "chromatic_models": [asdict(model) for model in (chromatic_models or [])],
        "chromatic_landmarks": [asdict(mark) for mark in (chromatic_landmarks or [])],
    }
    if mask_settings is not None:
        payload["mask_settings"] = _encode_mask_settings(mask_settings)
    if analysis_cache:
        payload["analysis_cache"] = analysis_cache
    if isinstance(session_mask, dict):
        mask = session_mask.get("mask")
        record_path = session_mask.get("record_path")
        if mask is not None:
            payload["session_mask"] = {
                "record_path": None if record_path is None else str(record_path),
                "mask": _encode_mask_payload(np.asarray(mask, dtype=bool)),
            }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_processing_profile(
    path: Path,
) -> tuple[
    PreprocessingSettings,
    SpotDetectionSettings,
    list[DetectedSpot],
    list[SpotGroup],
    list[ChromaticTransformModel],
    list[ChromaticLandmarkObservation],
    dict,
    dict | None,
    MaskSettings,
]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    preprocessing_payload = payload.get("preprocessing", payload)
    if not isinstance(preprocessing_payload, dict):
        raise ValueError("Invalid processing profile format.")

    raw_crop = preprocessing_payload.get("crop", {})
    if not isinstance(raw_crop, dict):
        raw_crop = {}

    preprocessing = PreprocessingSettings(
        image_tools_enabled=bool(preprocessing_payload.get("image_tools_enabled", True)),
        rotation_angle_deg=float(preprocessing_payload.get("rotation_angle_deg", 0.0)),
        flip_horizontal=bool(preprocessing_payload.get("flip_horizontal", False)),
        flip_vertical=bool(preprocessing_payload.get("flip_vertical", False)),
        crop=CropDefinition(
            x=int(raw_crop.get("x", 0)),
            y=int(raw_crop.get("y", 0)),
            width=int(raw_crop.get("width", 0)),
            height=int(raw_crop.get("height", 0)),
            enabled=bool(raw_crop.get("enabled", False)),
        ),
        display_units=str(preprocessing_payload.get("display_units", "px")),
        scale_bar_visible=bool(preprocessing_payload.get("scale_bar_visible", False)),
        calibration_enabled=bool(preprocessing_payload.get("calibration_enabled", False)),
        microns_per_pixel_x=float(preprocessing_payload.get("microns_per_pixel_x", 1.0)),
        microns_per_pixel_y=float(preprocessing_payload.get("microns_per_pixel_y", 1.0)),
        measurement_anchor1_x_px=float(preprocessing_payload.get("measurement_anchor1_x_px", 0.0)),
        measurement_anchor1_y_px=float(preprocessing_payload.get("measurement_anchor1_y_px", 0.0)),
        measurement_anchor2_x_px=float(preprocessing_payload.get("measurement_anchor2_x_px", 100.0)),
        measurement_anchor2_y_px=float(preprocessing_payload.get("measurement_anchor2_y_px", 0.0)),
        flatten_background_enabled=bool(preprocessing_payload.get("flatten_background_enabled", False)),
        flatten_background_sigma_px=float(preprocessing_payload.get("flatten_background_sigma_px", 48.0)),
        flatten_background_binning=max(int(preprocessing_payload.get("flatten_background_binning", 2)), 1),
        flatten_background_exclude_spots=bool(preprocessing_payload.get("flatten_background_exclude_spots", True)),
        flatten_background_exclude_mask=bool(preprocessing_payload.get("flatten_background_exclude_mask", False)),
        chromatic_correction_enabled=bool(preprocessing_payload.get("chromatic_correction_enabled", False)),
        chromatic_registration_mode=str(preprocessing_payload.get("chromatic_registration_mode", "landmark_radial")),
        chromatic_sample_image_count=int(preprocessing_payload.get("chromatic_sample_image_count", 5)),
        chromatic_feature_count=int(preprocessing_payload.get("chromatic_feature_count", 5)),
        chromatic_subpixel_precision=int(preprocessing_payload.get("chromatic_subpixel_precision", 4)),
        chromatic_tile_size_px=int(preprocessing_payload.get("chromatic_tile_size_px", 96)),
        chromatic_search_radius_px=int(preprocessing_payload.get("chromatic_search_radius_px", 24)),
        reference_mode=str(preprocessing_payload.get("reference_mode", "auto")),
        reference_wavelength_nm=(
            None
            if preprocessing_payload.get("reference_wavelength_nm") is None
            else float(preprocessing_payload.get("reference_wavelength_nm"))
        ),
        reference_frame_index=int(preprocessing_payload.get("reference_frame_index", 0)),
        histogram_highlight_min_value=(
            None
            if preprocessing_payload.get("histogram_highlight_min_value") is None
            else float(preprocessing_payload.get("histogram_highlight_min_value"))
        ),
        histogram_highlight_max_value=(
            None
            if preprocessing_payload.get("histogram_highlight_max_value") is None
            else float(preprocessing_payload.get("histogram_highlight_max_value"))
        ),
    )

    raw_detection = payload.get("spot_detection", {})
    if not isinstance(raw_detection, dict):
        raw_detection = {}
    detection = SpotDetectionSettings(
        mode=str(raw_detection.get("mode", "dark")),
        intensity_min_value=(
            None if raw_detection.get("intensity_min_value") is None else float(raw_detection.get("intensity_min_value"))
        ),
        intensity_max_value=(
            None if raw_detection.get("intensity_max_value") is None else float(raw_detection.get("intensity_max_value"))
        ),
        mask_mode=str(raw_detection.get("mask_mode", "absolute")),
        mask_profile_sigma_px=float(raw_detection.get("mask_profile_sigma_px", 48.0)),
        mask_relative_threshold_fraction=float(raw_detection.get("mask_relative_threshold_fraction", 0.18)),
        mask_local_contrast_sigma_px=float(raw_detection.get("mask_local_contrast_sigma_px", 8.0)),
        mask_local_contrast_z_threshold=float(raw_detection.get("mask_local_contrast_z_threshold", 3.0)),
        spot_radius_px=float(raw_detection.get("spot_radius_px", 10.0)),
        ring_inner_radius_px=max(
            float(raw_detection.get("ring_inner_radius_px", max(float(raw_detection.get("spot_radius_px", 10)) + 4.0, 14.0))),
            0.0,
        ),
        ring_outer_radius_px=max(
            float(raw_detection.get("ring_outer_radius_px", max(float(raw_detection.get("spot_radius_px", 10)) + 8.0, 18.0))),
            0.0,
        ),
        ignore_marked_pixels=bool(
            raw_detection.get("ignore_marked_pixels", raw_detection.get("ignore_large_dark_objects", False))
        ),
        ignored_intensity_value=(
            None
            if raw_detection.get("ignored_intensity_value") is None
            else float(raw_detection.get("ignored_intensity_value"))
        ),
        ignored_intensity_min_value=(
            None
            if raw_detection.get("ignored_intensity_min_value") is None
            else float(raw_detection.get("ignored_intensity_min_value"))
        ),
        ignored_intensity_max_value=(
            None
            if raw_detection.get("ignored_intensity_max_value") is None
            else float(raw_detection.get("ignored_intensity_max_value"))
        ),
        array_rows=int(raw_detection.get("array_rows", 0)),
        array_cols=int(raw_detection.get("array_cols", 0)),
        array_spacing_px=int(raw_detection.get("array_spacing_px", raw_detection.get("min_distance_px", 0))),
    )
    if detection.ignored_intensity_min_value is None and detection.ignored_intensity_max_value is None:
        legacy_threshold = detection.ignored_intensity_value
        if legacy_threshold is not None:
            if detection.mode == "dark":
                detection.ignored_intensity_min_value = 0.0
                detection.ignored_intensity_max_value = float(legacy_threshold)
            else:
                detection.ignored_intensity_min_value = float(legacy_threshold)
                detection.ignored_intensity_max_value = 65535.0
    detection.ring_outer_radius_px = max(detection.ring_outer_radius_px, detection.ring_inner_radius_px)

    raw_spots = payload.get("detected_spots", [])
    spots: list[DetectedSpot] = []
    if isinstance(raw_spots, list):
        for raw in raw_spots:
            if not isinstance(raw, dict):
                continue
            spots.append(
                DetectedSpot(
                    spot_id=int(raw.get("spot_id", len(spots) + 1)),
                    center_x=float(raw.get("center_x", 0.0)),
                    center_y=float(raw.get("center_y", 0.0)),
                    radius_px=float(raw.get("radius_px", 0.0)),
                    spot_color_hex=(None if raw.get("spot_color_hex") in (None, "") else str(raw.get("spot_color_hex"))),
                    ring_color_hex=(None if raw.get("ring_color_hex") in (None, "") else str(raw.get("ring_color_hex"))),
                    spot_diameter_px=(
                        None if raw.get("spot_diameter_px") is None else float(raw.get("spot_diameter_px"))
                    ),
                    ring_inner_diameter_px=(
                        None if raw.get("ring_inner_diameter_px") is None else float(raw.get("ring_inner_diameter_px"))
                    ),
                    ring_outer_diameter_px=(
                        None if raw.get("ring_outer_diameter_px") is None else float(raw.get("ring_outer_diameter_px"))
                    ),
                    score=float(raw.get("score", 0.0)),
                    support_mean_radius_px=float(raw.get("support_mean_radius_px", 0.0)),
                    support_radius_std_px=float(raw.get("support_radius_std_px", 0.0)),
                    support_value_mean=float(raw.get("support_value_mean", 0.0)),
                    support_value_std=float(raw.get("support_value_std", 0.0)),
                    quality_score=float(raw.get("quality_score", 0.0)),
                    inferred=bool(raw.get("inferred", False)),
                )
            )

    raw_groups = payload.get("spot_groups", [])
    groups: list[SpotGroup] = []
    if isinstance(raw_groups, list):
        for raw in raw_groups:
            if not isinstance(raw, dict):
                continue
            spot_ids_raw = raw.get("spot_ids", [])
            spot_ids = [int(spot_id) for spot_id in spot_ids_raw] if isinstance(spot_ids_raw, list) else []
            groups.append(
                SpotGroup(
                    group_id=str(raw.get("group_id", f"group_{len(groups) + 1}")),
                    name=str(raw.get("name", f"Group {len(groups) + 1}")),
                    spot_color_hex=str(raw.get("spot_color_hex", raw.get("color_hex", "#f59e0b"))),
                    ring_color_hex=str(raw.get("ring_color_hex", raw.get("color_hex", "#38bdf8"))),
                    spot_ids=spot_ids,
                )
            )

    raw_models = payload.get("chromatic_models", [])
    chromatic_models: list[ChromaticTransformModel] = []
    if isinstance(raw_models, list):
        for raw in raw_models:
            if not isinstance(raw, dict):
                continue
            matrix_raw = raw.get("affine_matrix", [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            if not (
                isinstance(matrix_raw, list)
                and len(matrix_raw) == 2
                and all(isinstance(row, list) and len(row) == 3 for row in matrix_raw)
            ):
                matrix_raw = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
            chromatic_models.append(
                ChromaticTransformModel(
                    frame_index=int(raw.get("frame_index", preprocessing.reference_frame_index)),
                    wavelength_nm=float(raw.get("wavelength_nm", preprocessing.reference_wavelength_nm or 0.0)),
                    model_kind=str(raw.get("model_kind", "image_affine")),
                    affine_matrix=[[float(value) for value in row] for row in matrix_raw],
                    global_shift_x_px=float(raw.get("global_shift_x_px", 0.0)),
                    global_shift_y_px=float(raw.get("global_shift_y_px", 0.0)),
                    rmse_px=float(raw.get("rmse_px", 0.0)),
                    mean_score=float(raw.get("mean_score", 0.0)),
                    min_score=float(raw.get("min_score", 0.0)),
                    tile_count=int(raw.get("tile_count", 0)),
                    inlier_count=int(raw.get("inlier_count", 0)),
                )
            )
    raw_landmarks = payload.get("chromatic_landmarks", [])
    chromatic_landmarks: list[ChromaticLandmarkObservation] = []
    if isinstance(raw_landmarks, list):
        for raw in raw_landmarks:
            if not isinstance(raw, dict):
                continue
            chromatic_landmarks.append(
                ChromaticLandmarkObservation(
                    landmark_id=int(raw.get("landmark_id", 1)),
                    frame_index=int(raw.get("frame_index", preprocessing.reference_frame_index)),
                    wavelength_nm=float(raw.get("wavelength_nm", preprocessing.reference_wavelength_nm or 0.0)),
                    x_px=float(raw.get("x_px", 0.0)),
                    y_px=float(raw.get("y_px", 0.0)),
                )
            )

    analysis_cache = payload.get("analysis_cache", {})
    if not isinstance(analysis_cache, dict):
        analysis_cache = {}
    raw_mask_settings = payload.get("mask_settings", {})
    mask_settings = _decode_mask_settings(raw_mask_settings) if isinstance(raw_mask_settings, dict) else MaskSettings()
    session_mask_payload = payload.get("session_mask", None)
    session_mask: dict | None = None
    if isinstance(session_mask_payload, dict):
        decoded_mask = _decode_mask_payload(session_mask_payload.get("mask", {})) if isinstance(session_mask_payload.get("mask"), dict) else None
        record_path = session_mask_payload.get("record_path")
        if decoded_mask is not None:
            session_mask = {
                "record_path": None if record_path is None else str(record_path),
                "mask": decoded_mask,
            }

    return preprocessing, detection, spots, groups, chromatic_models, chromatic_landmarks, analysis_cache, session_mask, mask_settings
