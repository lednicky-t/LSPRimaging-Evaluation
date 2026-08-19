from __future__ import annotations

import csv
from copy import deepcopy
from datetime import datetime
from math import hypot

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.exclusions import is_excluded
from lspr_imaging_app.domain.models import ChromaticLandmarkObservation, ChromaticTransformModel, GridBoundsDefinition
from lspr_imaging_app.gui.analysis_tasks import _sampled_wavelengths
from lspr_imaging_app.gui.ui_helpers import chromatic_feature_count_value, chromatic_subpixel_precision_value
from lspr_imaging_app.gui.worker import ChromaticLandmarkAllOverlayBundle
from lspr_imaging_app.processing.chromatic import (
    apply_affine_to_points,
    default_landmark_anchors,
    identity_affine_matrix,
    invert_affine_matrix,
    _landmark_regions,
)

class ChromaticController:
    def __init__(self, window) -> None:
        self.window = window

    def feature_count_options(self) -> tuple[int, ...]:
        return (5, 15, 30)

    def feature_count_value(self) -> int:
        window = self.window
        return chromatic_feature_count_value(window.chromatic_feature_count_spin.currentData(), self.feature_count_options())

    def set_feature_count_value(self, value: int) -> None:
        window = self.window
        target = int(value)
        if target not in self.feature_count_options():
            target = 15
        index = max(window.chromatic_feature_count_spin.findData(target), 0)
        window.chromatic_feature_count_spin.blockSignals(True)
        window.chromatic_feature_count_spin.setCurrentIndex(index)
        window.chromatic_feature_count_spin.blockSignals(False)

    def subpixel_precision_options(self) -> tuple[int, ...]:
        return (1, 4, 9)

    def subpixel_precision_value(self) -> int:
        window = self.window
        return chromatic_subpixel_precision_value(window.chromatic_subpixel_precision_combo.currentData())

    def landmark_kind_options(self) -> tuple[str, ...]:
        return ("corner", "centroid", "both")

    def landmark_kind_value(self) -> str:
        window = self.window
        value = str(window.chromatic_landmark_kind_combo.currentData() or "corner")
        return value if value in self.landmark_kind_options() else "corner"

    def set_landmark_kind_value(self, value: str) -> None:
        window = self.window
        target = str(value)
        if target not in self.landmark_kind_options():
            target = "corner"
        index = max(window.chromatic_landmark_kind_combo.findData(target), 0)
        window.chromatic_landmark_kind_combo.blockSignals(True)
        window.chromatic_landmark_kind_combo.setCurrentIndex(index)
        window.chromatic_landmark_kind_combo.blockSignals(False)

    def landmark_model_options(self) -> tuple[str, ...]:
        return ("similarity", "affine")

    def landmark_model_value(self) -> str:
        window = self.window
        value = str(window.chromatic_landmark_model_combo.currentData() or "similarity")
        return value if value in self.landmark_model_options() else "similarity"

    def set_landmark_model_value(self, value: str) -> None:
        window = self.window
        target = str(value)
        if target not in self.landmark_model_options():
            target = "similarity"
        index = max(window.chromatic_landmark_model_combo.findData(target), 0)
        window.chromatic_landmark_model_combo.blockSignals(True)
        window.chromatic_landmark_model_combo.setCurrentIndex(index)
        window.chromatic_landmark_model_combo.blockSignals(False)

    def model_for_image_key(self, image_key: tuple[int, float] | None) -> ChromaticTransformModel | None:
        window = self.window
        if image_key is None:
            return None
        spectral_cube_index, wavelength = image_key
        for model in window._state.chromatic_models:
            if int(model.spectral_cube_index) == int(spectral_cube_index) and abs(float(model.wavelength_nm) - float(wavelength)) < 1e-6:
                return model
        return None

    def affine_for_image_key(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        window = self.window
        if image_key is None or window._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        if not window._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def affine_for_image_key_any(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        window = self.window
        if image_key is None or window._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def signature_for_image_key(self, image_key: tuple[int, float] | None) -> tuple[object, ...] | None:
        window = self.window
        if image_key is None or not window._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self.model_for_image_key(image_key)
        if model is None:
            return None
        return (
            int(model.spectral_cube_index),
            round(float(model.wavelength_nm), 6),
            tuple(tuple(round(float(value), 6) for value in row) for row in model.affine_matrix),
        )

    def enter_setup_mode(self) -> None:
        window = self.window
        if window._chromatic_setup_saved_visibility is None:
            window._chromatic_setup_saved_visibility = (
                bool(window._rois_visible),
                bool(window._reference_visible),
                bool(window._mask_visible),
                bool(window._reference_points_visible),
                bool(window._highlight_visible),
            )
        window._chromatic_setup_active = True
        window._set_view_overlay_visibility(
            rois_visible=False,
            reference_rois_visible=False,
            mask_visible=False,
            reference_points_visible=True,
            highlight_visible=False,
        )

    def leave_setup_mode(self) -> None:
        window = self.window
        window._chromatic_setup_active = False
        if window._chromatic_setup_saved_visibility is not None:
            rois_visible, reference_rois_visible, mask_visible, reference_points_visible, highlight_visible = window._chromatic_setup_saved_visibility
            window._set_view_overlay_visibility(
                rois_visible=rois_visible,
                reference_rois_visible=reference_rois_visible,
                mask_visible=mask_visible,
                reference_points_visible=reference_points_visible,
                highlight_visible=highlight_visible,
            )
        window._chromatic_setup_saved_visibility = None
        window._chromatic_pending_view_ranges = None

    def capture_view_ranges(self) -> None:
        window = self.window
        if not window._chromatic_setup_active or window._current_processed_image is None:
            window._chromatic_pending_view_ranges = None
            return
        x_range, y_range = window.image_plot.vb.viewRange()
        window._chromatic_pending_view_ranges = (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )

    def restore_view_after_load(self) -> bool:
        window = self.window
        if not window._chromatic_setup_active:
            return False
        previous_ranges = window._chromatic_pending_view_ranges
        window._chromatic_pending_view_ranges = None
        if previous_ranges is None:
            return False
        return window._set_clamped_image_view_ranges(previous_ranges[0], previous_ranges[1])

    def default_feature_points(self, image_shape: tuple[int, int], feature_count: int) -> dict[int, tuple[float, float]]:
        return default_landmark_anchors(image_shape, feature_count, bounds=self.current_grid_bounds())

    def current_grid_bounds(self) -> tuple[int, int, int, int] | None:
        """The user-adjusted `(x, y, width, height)` search-area rectangle
        (image pixel space), or `None` to use the automatic full-image
        extent -- see `chromatic_grid_bounds` on `PreprocessingSettings` and
        `processing.chromatic._landmark_sector_layout`.
        """
        bounds = getattr(self.window._state.preprocessing, "chromatic_grid_bounds", None)
        if bounds is None or not bounds.enabled or bounds.width <= 0 or bounds.height <= 0:
            return None
        return (int(bounds.x), int(bounds.y), int(bounds.width), int(bounds.height))

    def default_grid_bounds_for_image(self, image_shape: tuple[int, int]) -> tuple[int, int, int, int]:
        """The automatic full-image extent `_landmark_sector_layout` would
        use with no custom bounds -- the sensible starting rectangle to seed
        the resizable overlay with, so the user adjusts *from* what's
        currently in effect rather than from an arbitrary default.
        """
        image_height, image_width = image_shape[:2]
        margin = max(min(image_width, image_height) * 0.06, 14.0)
        width = max(image_width - 2 * margin, 1.0)
        height = max(image_height - 2 * margin, 1.0)
        return (int(round(margin)), int(round(margin)), int(round(width)), int(round(height)))

    def sync_grid_tool(self, image_shape: tuple[int, int]) -> None:
        window = self.window
        image_height, image_width = image_shape[:2]
        bounds = window._state.preprocessing.chromatic_grid_bounds
        default_x, default_y, default_width, default_height = self.default_grid_bounds_for_image(image_shape)
        width = bounds.width if bounds.enabled and bounds.width > 0 else default_width
        height = bounds.height if bounds.enabled and bounds.height > 0 else default_height
        x = bounds.x if bounds.enabled and bounds.width > 0 else default_x
        y = bounds.y if bounds.enabled and bounds.height > 0 else default_y
        x = min(max(x, 0), max(image_width - width, 0))
        y = min(max(y, 0), max(image_height - height, 0))
        width = min(width, image_width)
        height = min(height, image_height)

        window._suspend_chromatic_grid_sync = True
        try:
            if window._chromatic_grid_roi is None:
                window._chromatic_grid_roi = pg.RectROI(
                    [x, y],
                    [width, height],
                    pen=pg.mkPen("#facc15", width=2),
                    movable=False,
                    resizable=True,
                    rotatable=False,
                    sideScalers=False,
                )
                window._chromatic_grid_roi.handleSize = 10
                window._chromatic_grid_roi.sigRegionChangeFinished.connect(self.grid_roi_changed)
                window._chromatic_grid_roi.sigRegionChanged.connect(lambda *_args: self._update_chromatic_grid_overlay())
                window._chromatic_grid_roi.addScaleHandle([0.0, 0.0], [1.0, 1.0], name="top_left")
                window._chromatic_grid_roi.addScaleHandle([1.0, 0.0], [0.0, 1.0], name="top_right")
                window._chromatic_grid_roi.addScaleHandle([0.0, 1.0], [1.0, 0.0], name="bottom_left")
                window._chromatic_grid_roi.addScaleHandle([1.0, 1.0], [0.0, 0.0], name="bottom_right")
                for fraction in (0.25, 0.5, 0.75):
                    window._chromatic_grid_roi.addScaleHandle([0.0, fraction], [1.0, fraction], name=f"left_{fraction:g}")
                    window._chromatic_grid_roi.addScaleHandle([1.0, fraction], [0.0, fraction], name=f"right_{fraction:g}")
                    window._chromatic_grid_roi.addScaleHandle([fraction, 0.0], [fraction, 1.0], name=f"top_{fraction:g}")
                    window._chromatic_grid_roi.addScaleHandle([fraction, 1.0], [fraction, 0.0], name=f"bottom_{fraction:g}")
                window.image_plot.addItem(window._chromatic_grid_roi)
                window._chromatic_grid_roi.setZValue(1.0)

            window._chromatic_grid_roi.setPos((x, y))
            window._chromatic_grid_roi.setSize((width, height))
        finally:
            window._suspend_chromatic_grid_sync = False
        self.sync_grid_visibility()

    def sync_grid_visibility(self) -> None:
        window = self.window
        if window._chromatic_grid_roi is not None:
            window._chromatic_grid_roi.setVisible(window._active_tool == "chromatic_grid_bounds")
        self._update_chromatic_grid_overlay()

    def on_grid_tool_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            for other in (
                window.chromatic_start_button,
                window.rotate_action,
                window.crop_action,
                window.measure_action,
                window.mask_pencil_check,
                window.roi_edit_action,
                window.roi_array_action,
            ):
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
            window._active_tool = "chromatic_grid_bounds"
            image = window._current_processed_image
            if image is not None:
                self.sync_grid_tool(image.shape[:2])
            window._set_status_text(
                "Drag the yellow rectangle's edges/corners to set the area reference points are searched within."
            )
        elif window._active_tool == "chromatic_grid_bounds":
            window._active_tool = None
        self.sync_grid_visibility()

    def grid_roi_changed(self) -> None:
        window = self.window
        if window._suspend_chromatic_grid_sync or window._chromatic_grid_roi is None:
            return
        pos = window._chromatic_grid_roi.pos()
        size = window._chromatic_grid_roi.size()
        window._push_undo_point("Chromatic search area")
        window._state.preprocessing.chromatic_grid_bounds = GridBoundsDefinition(
            x=max(int(round(pos.x())), 0),
            y=max(int(round(pos.y())), 0),
            width=max(int(round(size.x())), 1),
            height=max(int(round(size.y())), 1),
            enabled=True,
        )
        self._update_chromatic_grid_overlay()
        window._schedule_processing_state_save()
        window._set_status_text("Chromatic search area updated.")

    def reset_grid_bounds(self) -> None:
        window = self.window
        window._push_undo_point("Reset chromatic search area")
        window._state.preprocessing.chromatic_grid_bounds = GridBoundsDefinition()
        image = window._current_processed_image
        if image is not None:
            self.sync_grid_tool(image.shape[:2])
        window._schedule_processing_state_save()
        window._set_status_text("Chromatic search area reset to the automatic full-image area.")

    def _update_chromatic_grid_overlay(self) -> None:
        window = self.window
        show = window._active_tool in ("chromatic_grid_bounds", "chromatic_landmark") and window._state.dataset is not None
        image = window._current_processed_image
        if not show or image is None:
            if window._chromatic_grid_overlay_item is not None:
                window._chromatic_grid_overlay_item.setVisible(False)
            return
        feature_count = int(window._state.preprocessing.chromatic_feature_count)
        regions = _landmark_regions(image.shape[:2], feature_count, bounds=self.current_grid_bounds())
        xs: list[float] = []
        ys: list[float] = []
        for x0, x1, y0, y1 in regions.values():
            xs.extend([float(x0), float(x1), float(x1), float(x0), float(x0), float("nan")])
            ys.extend([float(y0), float(y0), float(y1), float(y1), float(y0), float("nan")])
        if window._chromatic_grid_overlay_item is None:
            curve = pg.PlotCurveItem()
            curve.setSkipFiniteCheck(True)
            window.image_plot.addItem(curve, ignoreBounds=True)
            window._chromatic_grid_overlay_item = curve
            window._chromatic_grid_overlay_item.setPen(pg.mkPen("#facc15", width=1.2, style=Qt.PenStyle.DashLine))
        window._chromatic_grid_overlay_item.setData(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64))
        window._chromatic_grid_overlay_item.setVisible(True)

    def expected_feature_ids(self) -> list[int]:
        window = self.window
        return list(range(1, max(int(window._state.preprocessing.chromatic_feature_count), 1) + 1))

    def candidate_chromatic_wavelengths(self, spectral_cube_index: int) -> list[float]:
        """Wavelengths eligible for chromatic landmark sampling.

        Excludes wavelength 0 nm -- the broadband/no-filter reference frame
        some acquisitions capture at every timepoint alongside the narrowband
        LCTF wavelengths. Its very different illumination character breaks
        the phase-correlation tracking used to follow landmarks from one
        sampled wavelength to the next, which otherwise silently corrupts
        every landmark position once the sweep passes through it. Also
        excludes any exclusion-marked wavelengths, as before.
        """
        window = self.window
        return [
            wavelength
            for wavelength in window._wavelength_values
            if float(wavelength) != 0.0
            and not is_excluded(window._state.image_exclusions, int(spectral_cube_index), float(wavelength))
        ]

    def sample_image_keys(self) -> list[tuple[int, float]]:
        window = self.window
        reference_key = window._reference_image_key()
        if reference_key is None:
            return []
        spectral_cube_index = int(reference_key[0])
        candidate_wavelengths = self.candidate_chromatic_wavelengths(spectral_cube_index)
        return [(spectral_cube_index, wavelength) for wavelength in _sampled_wavelengths(candidate_wavelengths, window._state.preprocessing.chromatic_sample_image_count)]

    def is_sample_image_key(self, image_key: tuple[int, float] | None) -> bool:
        return image_key is not None and image_key in self.sample_image_keys()

    def current_sample_index(self) -> int | None:
        window = self.window
        if window._current_image_key is None:
            return None
        sample_keys = self.sample_image_keys()
        try:
            return sample_keys.index(window._current_image_key)
        except ValueError:
            return None

    def current_image_landmarks(self) -> list[ChromaticLandmarkObservation]:
        window = self.window
        key = window._current_image_key
        if key is None:
            return []
        spectral_cube_index, wavelength = key
        return [
            mark
            for mark in window._state.chromatic_landmarks
            if int(mark.spectral_cube_index) == int(spectral_cube_index) and abs(float(mark.wavelength_nm) - float(wavelength)) < 1e-6
        ]

    def current_landmark(self, landmark_id: int) -> ChromaticLandmarkObservation | None:
        for mark in self.current_image_landmarks():
            if int(mark.landmark_id) == int(landmark_id):
                return mark
        return None

    def find_landmark_id_at(self, point: tuple[float, float]) -> int | None:
        nearest_id: int | None = None
        nearest_distance = float("inf")
        for mark in self.current_image_landmarks():
            distance = hypot(point[0] - float(mark.x_px), point[1] - float(mark.y_px))
            if distance <= 10.0 and distance < nearest_distance:
                nearest_distance = distance
                nearest_id = int(mark.landmark_id)
        return nearest_id

    def upsert_current_landmark(
        self,
        landmark_id: int,
        point: tuple[float, float],
        *,
        clear_models: bool,
    ) -> bool:
        window = self.window
        key = window._current_image_key
        if key is None:
            return False
        spectral_cube_index, wavelength = key
        updated = False
        for mark in window._state.chromatic_landmarks:
            if (
                int(mark.landmark_id) == int(landmark_id)
                and int(mark.spectral_cube_index) == int(spectral_cube_index)
                and abs(float(mark.wavelength_nm) - float(wavelength)) < 1e-6
            ):
                mark.x_px = float(point[0])
                mark.y_px = float(point[1])
                updated = True
                break
        if not updated:
            window._state.chromatic_landmarks.append(
                ChromaticLandmarkObservation(
                    landmark_id=int(landmark_id),
                    spectral_cube_index=int(spectral_cube_index),
                    wavelength_nm=float(wavelength),
                    x_px=float(point[0]),
                    y_px=float(point[1]),
                )
            )
        window._selected_landmark_id = int(landmark_id)
        window._chromatic_landmark_marker_id = int(landmark_id)
        if clear_models:
            self.finalize_landmark_edit()
        else:
            window._update_landmark_overlays()
        return updated

    def set_current_landmark(self, point: tuple[float, float], *, auto_advance: bool = False) -> None:
        window = self.window
        key = window._current_image_key
        if key is None:
            window._set_status_text("No image is selected for reference-point marking.")
            return
        window._push_undo_point("Chromatic landmarks")
        landmark_id = int(window._chromatic_landmark_marker_id)
        self.upsert_current_landmark(landmark_id, point, clear_models=True)
        if auto_advance:
            expected_ids = self.expected_feature_ids()
            next_id = landmark_id + 1 if landmark_id < len(expected_ids) else landmark_id
            window.chromatic_landmark_id_spin.blockSignals(True)
            window.chromatic_landmark_id_spin.setValue(next_id)
            window.chromatic_landmark_id_spin.blockSignals(False)
            window._chromatic_landmark_marker_id = next_id
            window._selected_landmark_id = landmark_id
        window._set_status_text(
            f"Stored reference point {landmark_id} at {key[1]:g} nm spectral cube {key[0]}."
        )

    def clear_landmarks(self, *, push_undo: bool = True) -> None:
        window = self.window
        if not window._state.chromatic_landmarks:
            return
        if push_undo:
            window._push_undo_point("Chromatic landmarks")
        window._state.chromatic_landmarks.clear()
        window._state.chromatic_models.clear()
        window._state.preprocessing.chromatic_correction_enabled = False
        window._chromatic_reference_points_all_visible = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        if hasattr(window, "chromatic_reference_points_all_button"):
            window.chromatic_reference_points_all_button.blockSignals(True)
            window.chromatic_reference_points_all_button.setChecked(False)
            window.chromatic_reference_points_all_button.blockSignals(False)
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_roi_overlays()
        window._schedule_histogram_refresh()
        window._update_landmark_overlays()
        window._update_chromatic_summary()
        window._schedule_processing_state_save()
        window._selected_landmark_id = None
        window._set_status_text("Cleared chromatic landmarks and models.")

    def sample_payload(self) -> list[tuple[int, float, str]]:
        window = self.window
        payload: list[tuple[int, float, str]] = []
        for spectral_cube_index, wavelength in self.sample_image_keys():
            record = window._record_map.get((spectral_cube_index, wavelength))
            if record is None:
                continue
            payload.append((int(spectral_cube_index), float(wavelength), str(record.path)))
        return payload

    def finalize_landmark_edit(self, *, status_text: str | None = None) -> None:
        window = self.window
        had_models = bool(window._state.chromatic_models)
        if had_models:
            window._state.chromatic_models.clear()
            window._state.preprocessing.chromatic_correction_enabled = False
            window.chromatic_apply_check.blockSignals(True)
            window.chromatic_apply_check.setChecked(False)
            window.chromatic_apply_check.blockSignals(False)
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_chromatic_summary()
        window._update_roi_overlays()
        window._update_ignore_mask_overlay()
        if had_models:
            window._schedule_histogram_refresh()
        window._update_landmark_overlays()
        window._schedule_processing_state_save()
        if status_text:
            window._set_status_text(status_text)

    def sync_current_feature_selection(self) -> None:
        window = self.window
        if not self.is_sample_image_key(window._current_image_key):
            return
        existing_ids = {int(mark.landmark_id) for mark in self.current_image_landmarks()}
        for feature_id in self.expected_feature_ids():
            if feature_id not in existing_ids:
                self.select_feature(feature_id, center_view=False)
                return
        if window._selected_landmark_id is not None:
            self.select_feature(int(window._selected_landmark_id), center_view=False)

    def center_view_on_landmark(self, landmark: ChromaticLandmarkObservation) -> None:
        window = self.window
        if window._current_processed_image is None:
            return
        x_range, y_range = window.image_plot.vb.viewRange()
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        image_height, image_width = window._current_processed_image.shape[:2]
        half_width = view_width / 2.0
        half_height = view_height / 2.0
        center_x = float(np.clip(float(landmark.x_px), half_width, max(float(image_width) - half_width, half_width)))
        center_y = float(np.clip(float(landmark.y_px), half_height, max(float(image_height) - half_height, half_height)))
        window.image_plot.vb.setRange(
            xRange=(center_x - half_width, center_x + half_width),
            yRange=(center_y - half_height, center_y + half_height),
            padding=0.0,
        )

    def select_feature(self, feature_id: int, *, center_view: bool = True) -> bool:
        window = self.window
        feature_ids = self.expected_feature_ids()
        if feature_id not in feature_ids:
            return False
        window._selected_landmark_id = int(feature_id)
        window._chromatic_landmark_marker_id = int(feature_id)
        window.chromatic_landmark_id_spin.blockSignals(True)
        window.chromatic_landmark_id_spin.setValue(int(feature_id))
        window.chromatic_landmark_id_spin.blockSignals(False)
        mark = self.current_landmark(int(feature_id))
        if mark is not None and center_view:
            self.center_view_on_landmark(mark)
        window._update_landmark_overlays()
        return True

    def move_selected_landmark(self, dx: float, dy: float) -> bool:
        window = self.window
        if window._selected_landmark_id is None:
            return False
        mark = self.current_landmark(int(window._selected_landmark_id))
        if mark is None:
            return False
        window._prepare_undo_snapshot("Chromatic landmarks")
        max_x = float(window._current_processed_image.shape[1] - 1) if window._current_processed_image is not None else float(mark.x_px)
        max_y = float(window._current_processed_image.shape[0] - 1) if window._current_processed_image is not None else float(mark.y_px)
        point = (
            float(np.clip(float(mark.x_px) + dx, 0.0, max_x)),
            float(np.clip(float(mark.y_px) + dy, 0.0, max_y)),
        )
        self.upsert_current_landmark(int(window._selected_landmark_id), point, clear_models=True)
        self.center_view_on_landmark(mark)
        window._commit_prepared_undo_snapshot()
        return True

    def switch_feature(self, direction: int) -> bool:
        window = self.window
        feature_ids = self.expected_feature_ids()
        if not feature_ids:
            return False
        current_id = int(window._selected_landmark_id or window._chromatic_landmark_marker_id or feature_ids[0])
        try:
            current_index = feature_ids.index(current_id)
        except ValueError:
            current_index = 0
        next_index = min(max(current_index + int(direction), 0), len(feature_ids) - 1)
        return self.select_feature(feature_ids[next_index], center_view=True)

    def update_settings(self) -> None:
        window = self.window
        window._push_undo_point("Chromatic correction")
        window._append_workflow_log("Chromatic link | toggled", level="debug")
        window._state.preprocessing.chromatic_correction_enabled = bool(window.chromatic_apply_check.isChecked())
        window.chromatic_apply_check.setIcon(window._make_link_toggle_icon(bool(window.chromatic_apply_check.isChecked())))
        window._set_section_applied(window.chromatic_section, bool(window._state.preprocessing.chromatic_correction_enabled))
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        self.update_control_state()
        window._update_chromatic_summary()
        window._schedule_processing_state_save()
        window._current_image_key = None
        window._schedule_image_refresh()

    def clear_models(self, *, push_undo: bool = True) -> None:
        window = self.window
        if push_undo:
            window._push_undo_point("Chromatic correction")
        window._append_workflow_log("Clearing chromatic transforms.", level="warning")
        window._state.chromatic_models.clear()
        window._state.preprocessing.chromatic_correction_enabled = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_chromatic_summary()
        window._schedule_processing_state_save()
        window._current_image_key = None
        window._schedule_image_refresh()
        window._set_status_text("Cleared chromatic transforms.")

    def on_transform_button_clicked(self) -> None:
        window = self.window
        if not window._state.dataset or window._chromatic_auto_running:
            return
        if window._state.chromatic_models:
            self.clear_models()
            return
        self.estimate_models()

    def wavelength_color(self, wavelength_nm: float) -> QColor:
        wavelength = float(np.clip(float(wavelength_nm), 380.0, 780.0))
        if wavelength < 440.0:
            red = -(wavelength - 440.0) / 60.0
            green = 0.0
            blue = 1.0
        elif wavelength < 490.0:
            red = 0.0
            green = (wavelength - 440.0) / 50.0
            blue = 1.0
        elif wavelength < 510.0:
            red = 0.0
            green = 1.0
            blue = -(wavelength - 510.0) / 20.0
        elif wavelength < 580.0:
            red = (wavelength - 510.0) / 70.0
            green = 1.0
            blue = 0.0
        elif wavelength < 645.0:
            red = 1.0
            green = -(wavelength - 645.0) / 65.0
            blue = 0.0
        else:
            red = 1.0
            green = 0.0
            blue = 0.0
        if wavelength < 420.0:
            factor = 0.28 + 0.72 * (wavelength - 380.0) / 40.0
        elif wavelength > 700.0:
            factor = 0.28 + 0.72 * (780.0 - wavelength) / 80.0
        else:
            factor = 1.0
        gamma = 0.85
        red = float(np.clip((red * factor) ** gamma, 0.0, 1.0))
        green = float(np.clip((green * factor) ** gamma, 0.0, 1.0))
        blue = float(np.clip((blue * factor) ** gamma, 0.0, 1.0))
        return QColor.fromRgbF(red, green, blue, 1.0)

    def transform_point_between_keys(
        self,
        point_xy: tuple[float, float],
        source_key: tuple[int, float],
        target_key: tuple[int, float],
    ) -> tuple[float, float] | None:
        window = self.window
        if source_key == target_key:
            return float(point_xy[0]), float(point_xy[1])
        source_affine = self.affine_for_image_key_any(source_key)
        target_affine = self.affine_for_image_key_any(target_key)
        if source_affine is None or target_affine is None:
            return None
        source_matrix = np.asarray(source_affine, dtype=np.float64)
        target_matrix = np.asarray(target_affine, dtype=np.float64)
        if window._is_reference_image_key(source_key):
            source_to_reference = identity_affine_matrix()
        else:
            source_to_reference = invert_affine_matrix(source_matrix)
        if window._is_reference_image_key(target_key):
            reference_to_target = identity_affine_matrix()
        else:
            reference_to_target = target_matrix
        point = np.asarray([[float(point_xy[0]), float(point_xy[1])]], dtype=np.float64)
        reference_point = apply_affine_to_points(point, source_to_reference)[0]
        target_point = apply_affine_to_points(np.asarray([reference_point], dtype=np.float64), reference_to_target)[0]
        return float(target_point[0]), float(target_point[1])

    def section_applied_changed(self, applied: bool) -> None:
        window = self.window
        if window.chromatic_apply_check.isChecked() != bool(applied):
            window.chromatic_apply_check.setChecked(bool(applied))

    def update_control_state(self) -> None:
        self.window._update_chromatic_control_state()

    def start_workflow(self) -> None:
        window = self.window
        if window._state.dataset is None or not window._wavelength_values:
            window._set_status_text("Load a dataset before starting the radial chromatic workflow.")
            return
        window._push_undo_point("Chromatic workflow")
        current_spectral_cube = window._current_spectral_cube()
        if current_spectral_cube is None:
            current_spectral_cube = window._spectral_cube_values[0] if window._spectral_cube_values else 0
        candidate_wavelengths = self.candidate_chromatic_wavelengths(int(current_spectral_cube))
        if not candidate_wavelengths:
            window._set_status_text(
                "No narrowband wavelengths are available for this spectral cube (all excluded, or only a "
                "broadband/0 nm frame is present); pick a different spectral cube."
            )
            return
        sample_minimum = 1 if len(candidate_wavelengths) <= 1 else 3
        sample_count = window._normalized_odd_count(
            int(window.chromatic_sample_count_spin.value()),
            sample_minimum,
            min(max(len(candidate_wavelengths), sample_minimum), 7),
        )
        feature_count = int(self.feature_count_value())
        sample_wavelengths = _sampled_wavelengths(candidate_wavelengths, int(sample_count))
        middle_wavelength = sample_wavelengths[len(sample_wavelengths) // 2]
        self.enter_setup_mode()
        window._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        window._state.preprocessing.chromatic_sample_image_count = sample_count
        window._state.preprocessing.chromatic_feature_count = feature_count
        window._state.preprocessing.reference_mode = "manual"
        window._state.preprocessing.reference_spectral_cube_index = int(current_spectral_cube)
        window._state.preprocessing.reference_wavelength_nm = float(middle_wavelength)
        window._state.preprocessing.chromatic_correction_enabled = False
        window._state.chromatic_landmarks.clear()
        window._state.chromatic_models.clear()
        window._selected_landmark_id = 1
        window._chromatic_landmark_marker_id = 1
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window.chromatic_sample_count_spin.blockSignals(True)
        window.chromatic_sample_count_spin.setValue(sample_count)
        window.chromatic_sample_count_spin.blockSignals(False)
        window._set_chromatic_feature_count_value(feature_count)
        window.chromatic_landmark_id_spin.blockSignals(True)
        window.chromatic_landmark_id_spin.setMaximum(feature_count)
        window.chromatic_landmark_id_spin.setValue(1)
        window.chromatic_landmark_id_spin.blockSignals(False)
        window._update_reference_controls()
        window._update_reference_summary()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._schedule_processing_state_save()
        window._activate_chromatic_tool_after_refresh = True
        window._set_current_spectral_cube_and_wavelength(int(current_spectral_cube), float(sample_wavelengths[0]))
        window._append_workflow_log(
            f"Chromatic workflow start | samples {sample_count} | features {feature_count}",
            level="info",
        )
        window._set_status_text(
            f"Radial workflow started with {sample_count} sampled wavelength images and {feature_count} reference points."
        )
        self.auto_detect_landmarks(push_undo=False)

    def auto_detect_landmarks(self, *, push_undo: bool = True) -> None:
        window = self.window
        payload = self.sample_payload()
        if not payload:
            window._set_status_text("Start the radial workflow before auto-detecting chromatic reference points.")
            return
        if push_undo:
            window._push_undo_point("Chromatic landmarks")
        window._chromatic_auto_restore_state = (
            deepcopy(window._state.chromatic_landmarks),
            deepcopy(window._state.chromatic_models),
            bool(window._state.preprocessing.chromatic_correction_enabled),
        )
        window._state.preprocessing.chromatic_correction_enabled = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        request_id = window._chromatic_auto_request_id + 1
        window._chromatic_auto_request_id = request_id
        window._chromatic_auto_running = True
        window._update_chromatic_control_state()
        from lspr_imaging_app.gui.main_window import FunctionWorker, _auto_chromatic_landmarks_task
        window._append_workflow_log(
            f"Chromatic auto-detect start | samples {len(payload)} | features {int(window._state.preprocessing.chromatic_feature_count)}",
            level="info",
        )
        worker = FunctionWorker(
            _auto_chromatic_landmarks_task,
            payload,
            deepcopy(window._state.preprocessing),
            int(window._state.preprocessing.chromatic_feature_count),
            int(getattr(window._state.preprocessing, "chromatic_subpixel_precision", 4)),
            float(getattr(window._state.area_roi_settings, "sample_radius_px", 10.0)),
            str(getattr(window._state.area_roi_settings, "mode", "dark") or "dark"),
            deepcopy(window._state.area_roi_settings),
            supports_progress=True,
        )
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.result.connect(
            lambda observations, request_id=request_id: self._on_auto_ready(request_id, observations)
        )
        worker.signals.error.connect(
            lambda message, request_id=request_id: self._on_auto_error(request_id, message)
        )
        window._begin_busy("Auto-detecting chromatic reference points...", determinate=True)
        window._thread_pool.start(worker)

    def estimate_models(self) -> None:
        window = self.window
        dataset = window._state.dataset
        reference_key = window._reference_image_key()
        if dataset is None or reference_key is None:
            window._set_status_text("Load a dataset and set a reference image first.")
            return
        reference_record = window._reference_record()
        if reference_record is None:
            window._set_status_text("Reference image is missing from the dataset.")
            return
        mode = str(window._state.preprocessing.chromatic_registration_mode or "landmark_radial")
        sample_keys = self.sample_image_keys()
        feature_ids = self.expected_feature_ids()
        landmarks_payload = [
            (
                int(mark.landmark_id),
                int(mark.spectral_cube_index),
                float(mark.wavelength_nm),
                float(mark.x_px),
                float(mark.y_px),
            )
            for mark in window._state.chromatic_landmarks
        ]
        if mode == "landmark_radial":
            marks_by_sample: dict[tuple[int, float], set[int]] = {sample_key: set() for sample_key in sample_keys}
            for landmark_id, spectral_cube_index, wavelength, _x, _y in landmarks_payload:
                sample_key = (int(spectral_cube_index), float(wavelength))
                if sample_key in marks_by_sample:
                    marks_by_sample[sample_key].add(int(landmark_id))
            complete_samples = [
                sample_key
                for sample_key in sample_keys
                if all(feature_id in marks_by_sample[sample_key] for feature_id in feature_ids)
            ]
            if not complete_samples:
                best_sample_key = None
                best_count = -1
                best_missing: list[int] = []
                for sample_key in sample_keys:
                    missing = [feature_id for feature_id in feature_ids if feature_id not in marks_by_sample[sample_key]]
                    complete_count = len(feature_ids) - len(missing)
                    if complete_count > best_count:
                        best_count = complete_count
                        best_sample_key = sample_key
                        best_missing = missing
                missing_text = ", ".join(str(feature_id) for feature_id in best_missing[:6]) if best_missing else "unknown"
                if best_sample_key is None:
                    window._set_status_text("No chromatic reference points are available for the sampled images.")
                else:
                    window._set_status_text(
                        f"Mark all {len(feature_ids)} reference points on at least one sampled wavelength image before estimating "
                        f"chromatic transforms. Missing on the best sample: {missing_text}."
                    )
                return
            if reference_key not in complete_samples:
                reference_key = complete_samples[0]
        window._push_undo_point("Chromatic correction")
        window._chromatic_registration_request_id += 1
        request_id = window._chromatic_registration_request_id
        preprocessing = deepcopy(window._state.preprocessing)
        record_specs = [
            (int(record.key.spectral_cube_index), float(record.key.wavelength_nm), str(record.path))
            for record in dataset.records
            if not is_excluded(window._state.image_exclusions, record.key.spectral_cube_index, record.key.wavelength_nm)
        ]
        window._append_workflow_log(
            f"Chromatic estimation start | mode {mode} | model {window._state.preprocessing.chromatic_landmark_model} | records {len(record_specs)}",
            level="info",
        )
        from lspr_imaging_app.gui.main_window import FunctionWorker, _estimate_chromatic_models_task
        worker = FunctionWorker(
            _estimate_chromatic_models_task,
            record_specs,
            preprocessing,
            reference_key,
            landmarks_payload,
            supports_progress=True,
        )
        window._begin_busy("Estimating chromatic transforms...", determinate=True)
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.result.connect(
            lambda models, request_id=request_id: self._on_models_ready(request_id, models)
        )
        worker.signals.error.connect(lambda message: self._on_models_failed(message))
        window._thread_pool.start(worker)

    def _on_auto_ready(self, request_id: int, observations: list[tuple[int, int, float, float, float]]) -> None:
        window = self.window
        if request_id != window._chromatic_auto_request_id:
            window._end_busy()
            return
        window._chromatic_auto_running = False
        window._chromatic_auto_restore_state = None
        window._state.chromatic_landmarks = [
            ChromaticLandmarkObservation(
                landmark_id=int(feature_id),
                spectral_cube_index=int(spectral_cube_index),
                wavelength_nm=float(wavelength),
                x_px=float(x_px),
                y_px=float(y_px),
            )
            for feature_id, spectral_cube_index, wavelength, x_px, y_px in observations
        ]
        window._selected_landmark_id = 1
        window._chromatic_landmark_marker_id = 1
        self.sync_current_feature_selection()
        window._update_landmark_overlays()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._schedule_processing_state_save()
        window._append_workflow_log(
            f"Chromatic auto-detect done | points {len(window._state.chromatic_landmarks)}",
            level="success",
        )
        window._end_busy("Automatic chromatic reference points detected. Adjust them if needed, then estimate transforms.")

    def _on_auto_error(self, request_id: int, message: str) -> None:
        window = self.window
        if request_id != window._chromatic_auto_request_id:
            window._end_busy()
            return
        window._chromatic_auto_running = False
        if window._chromatic_auto_restore_state is not None:
            landmarks, models, enabled = window._chromatic_auto_restore_state
            window._state.chromatic_landmarks = landmarks
            window._state.chromatic_models = models
            window._state.preprocessing.chromatic_correction_enabled = bool(enabled)
            window.chromatic_apply_check.blockSignals(True)
            window.chromatic_apply_check.setChecked(bool(enabled))
            window.chromatic_apply_check.blockSignals(False)
            window._invalidate_image_analysis_caches()
            window._invalidate_background_profile_cache()
        window._chromatic_auto_restore_state = None
        window._update_roi_overlays()
        window._update_ignore_mask_overlay()
        window._update_landmark_overlays()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._append_workflow_log(
            f"Chromatic auto-detect failed | {message}",
            level="error",
        )
        window._end_busy(f"Automatic chromatic reference-point detection failed: {message}")

    def _on_models_ready(self, request_id: int, models: list[ChromaticTransformModel]) -> None:
        window = self.window
        window._end_busy()
        if request_id != window._chromatic_registration_request_id:
            return
        window._state.chromatic_models = models
        window._state.preprocessing.chromatic_correction_enabled = False
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_apply_check.setChecked(False)
        window.chromatic_apply_check.blockSignals(False)
        self.leave_setup_mode()
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        window._update_chromatic_summary()
        window._schedule_processing_state_save()
        window._current_image_key = None
        window._schedule_image_refresh()
        window._append_workflow_log(
            f"Chromatic estimation done | models {len(models)}",
            level="success",
        )
        window._set_status_text(f"Estimated {len(models)} chromatic transform model(s). Use Apply radial transforms to enable them.")

    def _on_models_failed(self, message: str) -> None:
        self.window._end_busy()
        self.window._append_workflow_log(
            f"Chromatic estimation failed | {message}",
            level="error",
        )
        self.window._background_error("Chromatic correction", message)

    def export_landmarks_csv(self) -> None:
        """Dump every marked chromatic reference point to a CSV file for offline analysis.

        For each point, in addition to its raw tracked position, this recomputes the same
        per-point residual the estimation fit already computes internally and then discards
        (`_estimate_chromatic_models_task` only keeps the aggregate `rmse_px`) -- the predicted
        position (this wavelength's fitted model applied to the point's own reference-wavelength
        position) minus the actually-tracked position. Plotting `residual_x_px`/`residual_y_px`
        against `raw_x_px`/`raw_y_px` as a vector field is the fastest way to see whether the
        leftover per-point error has spatial structure (e.g. grows with distance from center,
        or points a consistent direction) that would justify a different correction model, versus
        being unstructured noise.
        """
        window = self.window
        folder = window._dataset_folder_path()
        if folder is None:
            window._set_status_text("Load a dataset before exporting chromatic reference points.")
            return
        landmarks = window._state.chromatic_landmarks
        if not landmarks:
            window._set_status_text("No chromatic reference points to export yet.")
            return

        models_by_key: dict[tuple[int, float], ChromaticTransformModel] = {
            (int(model.spectral_cube_index), float(model.wavelength_nm)): model
            for model in window._state.chromatic_models
        }
        reference_positions: dict[int, tuple[float, float]] = {}
        reference_key = window._reference_image_key()
        if reference_key is not None:
            ref_cube, ref_wavelength = int(reference_key[0]), float(reference_key[1])
            for mark in landmarks:
                if int(mark.spectral_cube_index) == ref_cube and abs(float(mark.wavelength_nm) - ref_wavelength) < 1e-6:
                    reference_positions[int(mark.landmark_id)] = (float(mark.x_px), float(mark.y_px))

        destination_folder = folder / "analysis" / "chromatic"
        destination_folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = destination_folder / f"chromatic_landmarks_{stamp}.csv"
        ordered_marks = sorted(landmarks, key=lambda item: (item.spectral_cube_index, item.wavelength_nm, item.landmark_id))
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "landmark_id",
                    "spectral_cube_index",
                    "wavelength_nm",
                    "raw_x_px",
                    "raw_y_px",
                    "reference_x_px",
                    "reference_y_px",
                    "predicted_x_px",
                    "predicted_y_px",
                    "residual_x_px",
                    "residual_y_px",
                    "residual_px",
                    "model_kind",
                    "model_rmse_px",
                ]
            )
            for mark in ordered_marks:
                landmark_id = int(mark.landmark_id)
                spectral_cube_index = int(mark.spectral_cube_index)
                wavelength = float(mark.wavelength_nm)
                raw_x, raw_y = float(mark.x_px), float(mark.y_px)
                reference_point = reference_positions.get(landmark_id)
                model = models_by_key.get((spectral_cube_index, wavelength))
                predicted_x = predicted_y = residual_x = residual_y = residual_magnitude = ""
                model_kind = model.model_kind if model is not None else ""
                model_rmse = model.rmse_px if model is not None else ""
                if reference_point is not None and model is not None:
                    matrix = np.asarray(model.affine_matrix, dtype=np.float64)
                    predicted = apply_affine_to_points(np.asarray([reference_point], dtype=np.float64), matrix)[0]
                    predicted_x, predicted_y = float(predicted[0]), float(predicted[1])
                    residual_x = predicted_x - raw_x
                    residual_y = predicted_y - raw_y
                    residual_magnitude = float(hypot(residual_x, residual_y))
                writer.writerow(
                    [
                        landmark_id,
                        spectral_cube_index,
                        wavelength,
                        raw_x,
                        raw_y,
                        "" if reference_point is None else reference_point[0],
                        "" if reference_point is None else reference_point[1],
                        predicted_x,
                        predicted_y,
                        residual_x,
                        residual_y,
                        residual_magnitude,
                        model_kind,
                        model_rmse,
                    ]
                )
        window._set_status_text(f"Exported {len(landmarks)} chromatic reference point(s) to {path.name}.")
        window._append_workflow_log(f"Chromatic reference points exported | {path.name}", level="info")

    def clear_all_landmark_overlays(self) -> None:
        window = self.window
        for bundle in window._chromatic_all_landmark_overlay_items.values():
            window.image_plot.removeItem(bundle.points)
            if bundle.active_cross is not None:
                window.image_plot.removeItem(bundle.active_cross)
            window.image_plot.removeItem(bundle.label)
        window._chromatic_all_landmark_overlay_items.clear()

    def update_all_landmark_overlays(self) -> None:
        window = self.window
        if window._showing_background_profile_main:
            self.clear_all_landmark_overlays()
            return
        current_key = window._current_image_key
        if current_key is None or not window._state.chromatic_landmarks:
            self.clear_all_landmark_overlays()
            return
        linked_preview = bool(
            window._state.preprocessing.chromatic_correction_enabled
            and window._state.chromatic_models
        )

        grouped: dict[int, list[tuple[float, float, float, tuple[int, float]]]] = {}
        for mark in window._state.chromatic_landmarks:
            grouped.setdefault(int(mark.landmark_id), []).append(
                (float(mark.x_px), float(mark.y_px), float(mark.wavelength_nm), (int(mark.spectral_cube_index), float(mark.wavelength_nm)))
            )

        current_ids = set(grouped)
        for landmark_id in list(window._chromatic_all_landmark_overlay_items):
            if landmark_id not in current_ids:
                bundle = window._chromatic_all_landmark_overlay_items.pop(landmark_id)
                window.image_plot.removeItem(bundle.points)
                if bundle.active_cross is not None:
                    window.image_plot.removeItem(bundle.active_cross)
                window.image_plot.removeItem(bundle.label)

        for landmark_id, items in grouped.items():
            bundle = window._chromatic_all_landmark_overlay_items.get(landmark_id)
            if bundle is None:
                points = pg.ScatterPlotItem(pxMode=True)
                points.setZValue(42)
                window.image_plot.addItem(points, ignoreBounds=True)
                active_cross = pg.PlotCurveItem()
                active_cross.setSkipFiniteCheck(True)
                active_cross.setZValue(44)
                window.image_plot.addItem(active_cross, ignoreBounds=True)
                label = pg.TextItem(anchor=(0.0, 1.0))
                label.setZValue(43)
                window.image_plot.addItem(label, ignoreBounds=True)
                bundle = ChromaticLandmarkAllOverlayBundle(points=points, active_cross=active_cross, label=label)
                window._chromatic_all_landmark_overlay_items[landmark_id] = bundle
            xs: list[float] = []
            ys: list[float] = []
            colors = [self.wavelength_color(item[2]) for item in items]
            pen_colors = [colors[i] for i in range(len(colors))]
            for item_x, item_y, _wavelength, source_key in items:
                display_point = (float(item_x), float(item_y))
                if linked_preview:
                    transformed = self.transform_point_between_keys(display_point, source_key, current_key)
                    if transformed is not None:
                        display_point = transformed
                xs.append(float(display_point[0]))
                ys.append(float(display_point[1]))
            bundle.points.setData(
                x=xs,
                y=ys,
                size=10.5,
                symbol="+",
                pen=[pg.mkPen(color, width=1.3) for color in pen_colors],
                brush=None,
            )
            bundle.points.setVisible(True)
            current_item = next((item for item in items if item[3] == current_key), None)
            if current_item is not None:
                idx = items.index(current_item)
                cross_size = 8.0
                cross_xs = np.asarray(
                    [xs[idx] - cross_size, xs[idx] + cross_size, np.nan, xs[idx], xs[idx]],
                    dtype=np.float64,
                )
                cross_ys = np.asarray(
                    [ys[idx], ys[idx], np.nan, ys[idx] - cross_size, ys[idx] + cross_size],
                    dtype=np.float64,
                )
                bundle.active_cross.setData(cross_xs, cross_ys)
                bundle.active_cross.setPen(pg.mkPen("#f8fafc", width=3.4))
                bundle.active_cross.setVisible(True)
            elif bundle.active_cross is not None:
                bundle.active_cross.setVisible(False)

            representative_index = 0
            current_wavelength = float(current_key[1])
            for index, item in enumerate(items):
                if abs(float(item[2]) - current_wavelength) < 1e-6:
                    representative_index = index
                    break
            _rep_x, _rep_y, rep_wavelength, _ = items[representative_index]
            rep_display_x = xs[representative_index]
            rep_display_y = ys[representative_index]
            label_color = self.wavelength_color(rep_wavelength)
            bundle.label.setHtml(
                "<span style="
                f"'color:{label_color.name()}; "
                "font-size:10pt; "
                "font-style:italic; "
                "font-weight:700; "
                f"background:{'#0f172a'}; "
                f"border:1px solid {label_color.name()}; "
                "border-radius:4px; "
                "padding:2px 5px;'"
                f">{landmark_id}</span>"
            )
            bundle.label.setPos(float(rep_display_x) + 8.0, float(rep_display_y) - 8.0)
            bundle.label.setVisible(True)

    def _update_chromatic_summary(self) -> None:
        if not self.window._state.dataset:
            self.window.chromatic_summary.setText("No dataset loaded.")
            self.window.chromatic_progress_label.setText("No dataset loaded.")
            self.window.chromatic_transform_button.setEnabled(False)
            self.window.chromatic_apply_check.setEnabled(False)
            self.window.chromatic_section.set_apply_enabled(False)
            self.window._set_section_applied(self.window.chromatic_section, bool(self.window._state.preprocessing.chromatic_correction_enabled))
            self.window.chromatic_transform_button.setPixmap(self.window._chromatic_transform_icon(False).pixmap(24, 24))
            self.window.chromatic_transform_button.setToolTip(
                "Estimate chromatic transforms."
            )
            return
        model_count = len(self.window._state.chromatic_models)
        sample_keys = self.sample_image_keys()
        feature_ids = self.expected_feature_ids()
        filled_samples = 0
        current_index = self.current_sample_index()
        for sample_key in sample_keys:
            sample_marks = {
                int(mark.landmark_id)
                for mark in self.window._state.chromatic_landmarks
                if int(mark.spectral_cube_index) == int(sample_key[0]) and abs(float(mark.wavelength_nm) - float(sample_key[1])) < 1e-6
            }
            if all(feature_id in sample_marks for feature_id in feature_ids):
                filled_samples += 1
        can_estimate = bool(sample_keys) and filled_samples == len(sample_keys) and len(feature_ids) >= 2
        controls_locked = self.window._chromatic_auto_running
        can_apply_models = model_count > 0 and not controls_locked
        can_toggle_transform = (can_estimate or model_count > 0) and not controls_locked
        self.window.chromatic_transform_button.setEnabled(can_toggle_transform)
        self.window.chromatic_transform_button.setPixmap(self.window._chromatic_transform_icon(model_count > 0).pixmap(24, 24))
        if model_count > 0:
            self.window.chromatic_transform_button.setToolTip("Clear saved chromatic transforms.")
        elif can_estimate:
            self.window.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        else:
            self.window.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        self.window.chromatic_apply_check.setEnabled(can_apply_models)
        self.window.chromatic_section.set_apply_enabled(can_apply_models)
        self.window._set_section_applied(self.window.chromatic_section, bool(self.window._state.preprocessing.chromatic_correction_enabled))
        self.window.chromatic_summary.setText("Radial setup ready." if self.window._chromatic_setup_active else "Radial workflow ready.")
        if sample_keys and current_index is not None:
            current_key = sample_keys[current_index]
            marked_current = len(
                {
                    int(mark.landmark_id)
                    for mark in self.window._state.chromatic_landmarks
                    if int(mark.spectral_cube_index) == int(current_key[0]) and abs(float(mark.wavelength_nm) - float(current_key[1])) < 1e-6
                }
            )
            self.window.chromatic_progress_label.setText(
                f"Sample image {current_index + 1}/{len(sample_keys)} at {current_key[1]:g} nm | "
                f"reference points marked: {marked_current}/{len(feature_ids)} | "
                f"completed sample images: {filled_samples}/{len(sample_keys)}"
            )
        elif sample_keys:
            middle_index = len(sample_keys) // 2
            self.window.chromatic_progress_label.setText(
                f"Procedure ready: {len(sample_keys)} sampled wavelengths, middle reference at {sample_keys[middle_index][1]:g} nm."
            )
        else:
            self.window.chromatic_progress_label.setText("Edit the radial workflow to choose sampled wavelengths.")
        if model_count == 0:
            return
        rmses = [float(model.rmse_px) for model in self.window._state.chromatic_models if model.rmse_px > 0.0]
        rmse_text = f"{float(np.mean(rmses)):.2f} px" if rmses else "0.00 px"
        self.window.chromatic_summary.setText("Transforms estimated.")
        self.window.chromatic_progress_label.setText(
            f"Transforms ready for {model_count} image(s) | mean fit RMSE: {rmse_text} | "
            f"{'applied' if self.window._state.preprocessing.chromatic_correction_enabled else 'ready to apply'}"
        )


    def _on_chromatic_reference_points_all_toggled(self, checked: bool) -> None:
        window = self.window
        window._chromatic_reference_points_all_visible = bool(checked)
        if checked and not window._reference_points_visible:
            window._reference_points_visible = True
            window.show_reference_points_check.blockSignals(True)
            window.show_reference_points_check.setChecked(True)
            window.show_reference_points_check.blockSignals(False)
        window._update_landmark_overlays()
        window._schedule_processing_state_save()


    def _on_chromatic_landmark_id_changed(self, value: int) -> None:
        feature_id = min(max(int(value), 1), len(self.expected_feature_ids()))
        self.window._chromatic_landmark_marker_id = feature_id
        self.window._selected_landmark_id = feature_id
        self.select_feature(feature_id, center_view=True)


    def _on_chromatic_landmark_tool_toggled(self, checked: bool) -> None:
        self.window.chromatic_start_button.setIcon(self.window._make_roi_edit_icon(bool(checked)))
        if checked:
            if not self.is_sample_image_key(self.window._current_image_key):
                sample_keys = self.sample_image_keys()
                if sample_keys:
                    self.window._set_current_spectral_cube_and_wavelength(int(sample_keys[0][0]), float(sample_keys[0][1]))
                    self.window._set_status_text("Chromatic edit activated. Navigating to the first sampled wavelength image.")
                    self.window._append_workflow_log(
                        "Chromatic edit activated | navigated to first sampled wavelength image",
                        level="info",
                    )
                else:
                    self.window._set_status_text("Start the radial workflow before editing chromatic reference points.")
                    self.window._append_workflow_log(
                        "Chromatic edit rejected | no sampled chromatic images available",
                        level="warning",
                    )
                    self.window.chromatic_start_button.blockSignals(True)
                    self.window.chromatic_start_button.setChecked(False)
                    self.window.chromatic_start_button.blockSignals(False)
                    self.window.chromatic_start_button.setIcon(self.window._make_roi_edit_icon(False))
                    return
            self.window.rotate_action.blockSignals(True)
            self.window.rotate_action.setChecked(False)
            self.window.rotate_action.blockSignals(False)
            self.window.crop_action.blockSignals(True)
            self.window.crop_action.setChecked(False)
            self.window.crop_action.blockSignals(False)
            self.window.roi_edit_action.blockSignals(True)
            self.window.roi_edit_action.setChecked(False)
            self.window.roi_edit_action.blockSignals(False)
            self.window.mask_pencil_check.blockSignals(True)
            self.window.mask_pencil_check.setChecked(False)
            self.window.mask_pencil_check.blockSignals(False)
            self.window.chromatic_grid_button.blockSignals(True)
            self.window.chromatic_grid_button.setChecked(False)
            self.window.chromatic_grid_button.blockSignals(False)
            self.window._active_tool = "chromatic_landmark"
            self.window._selected_landmark_id = self.window._chromatic_landmark_marker_id
            if hasattr(self.window, "image_panel"):
                self.window.image_panel.raise_()
            if hasattr(self.window, "image_view") and self.window.image_view is not None:
                self.window.image_view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                viewport = self.window.image_view.viewport()
                if viewport is not None:
                    viewport.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.window._set_status_text(
                f"Chromatic reference point editor active. Click to place point {self.window._chromatic_landmark_marker_id}, "
                "drag to adjust, PageUp/PageDown to switch reference points."
            )
            self.window._append_workflow_log("Chromatic edit activated", level="info")
        elif self.window._active_tool == "chromatic_landmark":
            self.window._active_tool = None
            self.window._append_workflow_log("Chromatic edit deactivated", level="info")
        self.window._update_landmark_overlays()
        self.sync_grid_visibility()


    def _on_chromatic_sample_count_changed(self, value: int) -> None:
        sample_minimum = 1 if len(self.window._wavelength_values) <= 1 else 3
        normalized = self.window._normalized_odd_count(
            int(value),
            sample_minimum,
            min(max(len(self.window._wavelength_values), sample_minimum), 7),
        )
        if normalized != int(value):
            self.window.chromatic_sample_count_spin.blockSignals(True)
            self.window.chromatic_sample_count_spin.setValue(normalized)
            self.window.chromatic_sample_count_spin.blockSignals(False)
        self.window._state.preprocessing.chromatic_sample_image_count = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()


    def _on_chromatic_feature_count_changed(self, value: int) -> None:
        normalized = self.feature_count_value()
        if normalized != self.window._state.preprocessing.chromatic_feature_count:
            self.window._state.preprocessing.chromatic_feature_count = normalized
        max_feature = len(self.expected_feature_ids())
        self.window._chromatic_landmark_marker_id = min(self.window._chromatic_landmark_marker_id, max_feature)
        self.window._selected_landmark_id = None if self.window._selected_landmark_id is None else min(self.window._selected_landmark_id, max_feature)
        self.window.chromatic_landmark_id_spin.blockSignals(True)
        self.window.chromatic_landmark_id_spin.setMaximum(max_feature)
        self.window.chromatic_landmark_id_spin.setValue(self.window._chromatic_landmark_marker_id)
        self.window.chromatic_landmark_id_spin.blockSignals(False)
        self._update_chromatic_summary()
        self.window._update_landmark_overlays()
        self._update_chromatic_grid_overlay()
        self.window._schedule_processing_state_save()


    def _on_chromatic_subpixel_precision_changed(self, _value: int) -> None:
        normalized = self.subpixel_precision_value()
        if normalized != int(getattr(self.window._state.preprocessing, "chromatic_subpixel_precision", 4)):
            self.window._state.preprocessing.chromatic_subpixel_precision = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()

    def _on_chromatic_landmark_kind_changed(self, _index: int) -> None:
        normalized = self.landmark_kind_value()
        if normalized != str(getattr(self.window._state.preprocessing, "chromatic_landmark_kind", "corner")):
            self.window._state.preprocessing.chromatic_landmark_kind = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()

    def _on_chromatic_landmark_model_changed(self, _index: int) -> None:
        normalized = self.landmark_model_value()
        if normalized != str(getattr(self.window._state.preprocessing, "chromatic_landmark_model", "similarity")):
            self.window._state.preprocessing.chromatic_landmark_model = normalized
        self._update_chromatic_summary()
        self.window._schedule_processing_state_save()


    def _seed_chromatic_landmarks_for_current_image(self) -> None:
        if self.window._chromatic_auto_running:
            return
        image_key = self.window._current_image_key
        if not self.is_sample_image_key(image_key):
            return
        assert image_key is not None
        expected_ids = self.expected_feature_ids()
        existing_ids = {int(mark.landmark_id) for mark in self.current_image_landmarks()}
        missing_ids = [feature_id for feature_id in expected_ids if feature_id not in existing_ids]
        if not missing_ids:
            return
        sample_keys = self.sample_image_keys()
        current_index = sample_keys.index(image_key)
        candidate_keys: list[tuple[int, float]] = []
        for index in range(current_index - 1, -1, -1):
            candidate_keys.append(sample_keys[index])
        reference_key = self.window._reference_image_key()
        if reference_key is not None and reference_key not in candidate_keys and reference_key != image_key:
            candidate_keys.append(reference_key)
        for index in range(current_index + 1, len(sample_keys)):
            candidate_keys.append(sample_keys[index])
        source_marks: dict[int, tuple[float, float]] | None = None
        for candidate_key in candidate_keys:
            marks = {
                int(mark.landmark_id): (float(mark.x_px), float(mark.y_px))
                for mark in self.window._state.chromatic_landmarks
                if int(mark.spectral_cube_index) == int(candidate_key[0])
                and abs(float(mark.wavelength_nm) - float(candidate_key[1])) < 1e-6
            }
            if any(feature_id in marks for feature_id in missing_ids):
                source_marks = marks
                break
        if source_marks is None and self.window._current_processed_image is not None:
            current_marks = self.current_image_landmarks()
            if not current_marks:
                source_marks = self.default_feature_points(
                    self.window._current_processed_image.shape[:2],
                    len(expected_ids),
                )
        if source_marks is None:
            return
        changed = False
        for feature_id in missing_ids:
            point = source_marks.get(feature_id)
            if point is None:
                continue
            self.upsert_current_landmark(feature_id, point, clear_models=False)
            changed = True
        if changed:
            self.finalize_landmark_edit()
            self.window._set_status_text(
                f"Seeded missing reference points for {image_key[1]:g} nm from the nearest marked sample image."
            )


    def _navigate_chromatic_sample(self, direction: int) -> bool:
        sample_keys = self.sample_image_keys()
        if not sample_keys:
            return False
        current_index = self.current_sample_index()
        if current_index is None:
            if self.window._current_image_key is None:
                current_index = 0
            else:
                current_wavelength = float(self.window._current_image_key[1])
                current_index = min(
                    range(len(sample_keys)),
                    key=lambda idx: abs(float(sample_keys[idx][1]) - current_wavelength),
                )
        target_index = min(max(current_index + int(direction), 0), len(sample_keys) - 1)
        target_spectral_cube, target_wavelength = sample_keys[target_index]
        self.window._set_current_spectral_cube_and_wavelength(target_spectral_cube, target_wavelength)
        return True
