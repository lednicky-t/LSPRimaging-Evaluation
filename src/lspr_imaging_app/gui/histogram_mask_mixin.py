from __future__ import annotations

import numpy as np

from lspr_imaging_app.processing.chromatic import transformed_annulus_mask, transformed_disk_mask


class HistogramMaskMixin:
    """Builds the sample/reference/ignored pixel masks the histogram and
    intensity-overlay code reads from, including the chromatic-corrected
    case where each displayed ROI position must be transformed back to its
    source (reference-image) position before masking. Mixed into MainWindow
    (same pattern as MainWindowIcons): `self` here is the MainWindow
    instance, so these methods use the same window state/widgets as the
    rest of the class.
    """

    def _roi_area_masks(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        signature = self._histogram_source_signature(image)
        if self._roi_mask_cache_signature == signature and self._roi_mask_cache_values is not None:
            return self._roi_mask_cache_values

        image_f32 = image.astype(np.float32, copy=False)
        image_height, image_width = image_f32.shape[:2]
        ignored_mask = self._ignored_mask(image_f32)
        roi_mask = np.zeros((image_height, image_width), dtype=bool)
        reference_mask = np.zeros((image_height, image_width), dtype=bool)
        display_rois = self._display_rois()
        if display_rois:
            affine_matrix = self._chromatic_affine_for_image_key(self._current_image_key)
            reference_inner_radius = float(max(self._state.area_roi_settings.reference_inner_radius_px, 0.0))
            reference_outer_radius = float(max(self._state.area_roi_settings.reference_outer_radius_px, reference_inner_radius))
            if affine_matrix is None or self._is_current_reference_image():
                yy, xx = np.indices((image_height, image_width), dtype=np.float32)
                for roi in display_rois:
                    distance_sq = (xx - float(roi.center_x)) ** 2 + (yy - float(roi.center_y)) ** 2
                    roi_mask |= distance_sq <= float(roi.sample_radius_px) ** 2
                    if reference_outer_radius > 0.0:
                        outer_mask = distance_sq <= reference_outer_radius ** 2
                        inner_mask = distance_sq < reference_inner_radius ** 2 if reference_inner_radius > 0.0 else np.zeros_like(outer_mask)
                        reference_mask |= outer_mask & ~inner_mask
            else:
                source_roi_map = {roi.area_roi_id: roi for roi in self._state.area_rois}
                for roi in display_rois:
                    source_roi = source_roi_map.get(roi.area_roi_id, roi)
                    roi_mask |= transformed_disk_mask(
                        (image_height, image_width),
                        (float(source_roi.center_x), float(source_roi.center_y)),
                        float(source_roi.sample_radius_px),
                        affine_matrix,
                    )
                    if reference_outer_radius > 0.0:
                        reference_mask |= transformed_annulus_mask(
                            (image_height, image_width),
                            (float(source_roi.center_x), float(source_roi.center_y)),
                            float(reference_inner_radius),
                            float(reference_outer_radius),
                            affine_matrix,
                        )
        roi_mask &= ~ignored_mask
        reference_mask &= ~ignored_mask
        reference_mask &= ~roi_mask
        residual_mask = ~(roi_mask | reference_mask | ignored_mask)
        cached = (roi_mask, reference_mask, ignored_mask, residual_mask)
        self._roi_mask_cache_signature = signature
        self._roi_mask_cache_values = cached
        return cached

    def _roi_intensity_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image_f32 = image.astype(np.float32, copy=False)
        roi_mask, reference_mask, ignored_mask, _residual_mask = self._roi_area_masks(image_f32)
        return (
            image_f32[roi_mask],
            image_f32[reference_mask],
            image_f32[ignored_mask],
        )
