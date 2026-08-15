from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QBrush, QColor, QPen, QPainterPath

from lspr_imaging_app.gui.worker import (
    LandmarkOverlayBundle,
    MeasurementOverlayBundle,
    RoiOverlayBundle,
)
from lspr_imaging_app.gui.roi_overlay_helpers import resolved_reference_color, resolved_roi_color
from lspr_imaging_app.processing.preprocess import apply_spatial_mask


class OverlayManager:
    """Handles all overlay and histogram-event methods extracted from MainWindow."""

    def __init__(self, window) -> None:
        self.window = window

    # ------------------------------------------------------------------
    # ROI overlays
    # ------------------------------------------------------------------

    def _refresh_roi_overlays_during_drag(self) -> None:
        self._update_roi_overlays(update_hidden_details=False)

    def _update_roi_overlays(self, *, update_hidden_details: bool = True) -> None:
        w = self.window
        display_rois = w._display_rois()
        source_roi_map = {roi.area_roi_id: roi for roi in w._state.area_rois}
        if w._showing_background_profile_main:
            for bundle in w._roi_overlay_items.values():
                bundle.curve.setVisible(False)
                if bundle.reference_fill is not None:
                    bundle.reference_fill.setVisible(False)
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
                if bundle.label is not None:
                    bundle.label.setVisible(False)
            self._update_guide_overlays()
            return
        current_ids = {roi.area_roi_id for roi in display_rois}
        for roi_id in list(w._roi_overlay_items):
            if roi_id not in current_ids:
                w._remove_roi_overlay_bundle(roi_id)

        reference_inner_radius = float(max(w._state.area_roi_settings.reference_inner_radius_px, 0))
        reference_outer_radius = float(max(w._state.area_roi_settings.reference_outer_radius_px, w._state.area_roi_settings.reference_inner_radius_px))
        for roi in display_rois:
            source_roi = source_roi_map.get(roi.area_roi_id, roi)
            bundle = w._roi_overlay_items.get(roi.area_roi_id)
            if bundle is None:
                curve = pg.PlotCurveItem()
                curve.setSkipFiniteCheck(True)
                w.image_plot.addItem(curve, ignoreBounds=True)
                bundle = RoiOverlayBundle(curve=curve)
                w._roi_overlay_items[roi.area_roi_id] = bundle
            xs, ys = w._roi_curve_points(source_roi, roi, source_roi.sample_radius_px)
            group = w._group_for_roi(roi.area_roi_id)
            pen_color = resolved_roi_color(roi, group, w._sample_visual_color)
            pen_color.setAlphaF(w._alpha01(w._roi_alpha))
            fill_color = resolved_roi_color(roi, group, w._sample_visual_color)
            fill_color.setAlphaF(w._alpha01(max(w._roi_alpha * 0.22, 0.08)))
            roi_signature = w._roi_absorbance_signature(source_roi)
            roi_cached = bool(roi_signature is not None and w._roi_absorbance_cache.get(roi_signature) is not None)
            if w._cached_rois_only_visible and not roi_cached and roi.area_roi_id not in w._selected_roi_ids:
                dim_pen = QColor("#94a3b8")
                dim_pen.setAlphaF(w._alpha01(0.16))
                dim_fill = QColor("#94a3b8")
                dim_fill.setAlphaF(w._alpha01(0.04))
                pen_color = dim_pen
                fill_color = dim_fill
            elif w._cached_rois_only_visible and roi_cached and roi.area_roi_id not in w._selected_roi_ids:
                cached_pen = QColor("#22c55e")
                cached_pen.setAlphaF(w._alpha01(max(w._roi_alpha, 0.9)))
                cached_fill = QColor("#22c55e")
                cached_fill.setAlphaF(w._alpha01(max(w._roi_alpha * 0.16, 0.06)))
                pen_color = cached_pen
                fill_color = cached_fill
            pen = pg.mkPen(pen_color, width=2)
            brush = pg.mkBrush(fill_color)
            if roi.inferred:
                inferred_color = QColor("#f59e0b")
                inferred_color.setAlphaF(w._alpha01(max(w._roi_alpha, 0.45)))
                pen = pg.mkPen(inferred_color, width=2, style=Qt.PenStyle.DashLine)
                inferred_fill = QColor("#f59e0b")
                inferred_fill.setAlphaF(w._alpha01(max(w._roi_alpha * 0.15, 0.08)))
                brush = pg.mkBrush(inferred_fill)
            if roi.area_roi_id in w._selected_roi_ids:
                selected_color = QColor("#38bdf8")
                selected_color.setAlphaF(1.0)
                pen = pg.mkPen(selected_color, width=3)
                selected_fill = QColor("#38bdf8")
                selected_fill.setAlphaF(0.2)
                brush = pg.mkBrush(selected_fill)
            bundle.curve.setData(xs, ys)
            bundle.curve.setPen(pen)
            bundle.curve.setFillLevel(roi.center_y)
            bundle.curve.setBrush(brush)
            bundle.curve.setVisible(w._rois_visible)
            if w._reference_visible and reference_outer_radius > 0.0:
                reference_color = resolved_reference_color(roi, group, w._reference_visual_color)
                reference_color.setAlphaF(w._alpha01(max(w._reference_alpha * 1.3, 0.18)))
                reference_fill = resolved_reference_color(roi, group, w._reference_visual_color)
                reference_fill.setAlphaF(w._alpha01(max(w._reference_alpha, 0.03)))
                if roi.area_roi_id in w._selected_roi_ids:
                    reference_color = QColor("#38bdf8")
                    reference_color.setAlphaF(w._alpha01(0.85))
                    reference_fill = QColor("#38bdf8")
                    reference_fill.setAlphaF(w._alpha01(max(w._reference_alpha, 0.08)))
                inner_pen = pg.mkPen(reference_color, width=1.4, style=Qt.PenStyle.DashLine)
                outer_pen = pg.mkPen(reference_color, width=1.4, style=Qt.PenStyle.DotLine)
                if reference_inner_radius > 0.0:
                    if bundle.inner_curve is None:
                        bundle.inner_curve = pg.PlotCurveItem()
                        bundle.inner_curve.setSkipFiniteCheck(True)
                        w.image_plot.addItem(bundle.inner_curve, ignoreBounds=True)
                    inner_xs, inner_ys = w._roi_curve_points(source_roi, roi, reference_inner_radius)
                    bundle.inner_curve.setData(inner_xs, inner_ys)
                    bundle.inner_curve.setPen(inner_pen)
                    bundle.inner_curve.setVisible(True)
                elif bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.reference_fill is None:
                    from PyQt6.QtWidgets import QGraphicsPathItem
                    bundle.reference_fill = QGraphicsPathItem()
                    w.image_plot.addItem(bundle.reference_fill, ignoreBounds=True)
                bundle.reference_fill.setPath(
                    w._create_reference_fill_path(
                        roi.center_x,
                        roi.center_y,
                        reference_inner_radius,
                        reference_outer_radius,
                    )
                )
                bundle.reference_fill.setBrush(QBrush(reference_fill))
                bundle.reference_fill.setPen(QPen(Qt.PenStyle.NoPen))
                bundle.reference_fill.setVisible(True)
                if bundle.outer_curve is None:
                    bundle.outer_curve = pg.PlotCurveItem()
                    bundle.outer_curve.setSkipFiniteCheck(True)
                    w.image_plot.addItem(bundle.outer_curve, ignoreBounds=True)
                outer_xs, outer_ys = w._roi_curve_points(source_roi, roi, reference_outer_radius)
                bundle.outer_curve.setData(outer_xs, outer_ys)
                bundle.outer_curve.setPen(outer_pen)
                bundle.outer_curve.setVisible(True)
            elif update_hidden_details:
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.reference_fill is not None:
                    bundle.reference_fill.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
            label = w._array_label_for_roi(roi.area_roi_id)
            if label is not None and w._roi_labels_visible:
                is_selected = roi.area_roi_id in w._selected_roi_ids
                if w._cached_rois_only_visible and not roi_cached and not is_selected:
                    if bundle.label is not None:
                        bundle.label.setVisible(False)
                    continue
                label_color = "#f8fafc" if is_selected else "#ffffff"
                label_background = "#0f766e" if is_selected else "#0f172a"
                label_border = "#5eead4" if is_selected else "#94a3b8"
                label_text = label
                if is_selected:
                    label_text = f"{label}<br><span style='font-size:8.5pt; font-weight:600;'>x={roi.center_x:.1f}, y={roi.center_y:.1f}</span>"
                if bundle.label is None:
                    bundle.label = pg.TextItem(anchor=(0.0, 1.0))
                    w.image_plot.addItem(bundle.label, ignoreBounds=True)
                bundle.label.setHtml(
                    "<span style="
                    f"'color:{label_color}; "
                    "font-size:10pt; "
                    "font-weight:700; "
                    f"background:{label_background}; "
                    f"border:1px solid {label_border}; "
                    "border-radius:4px; "
                    "padding:2px 5px;'"
                    f">{label_text}</span>"
                )
                bundle.label.setPos(roi.center_x + roi.sample_radius_px + 4.0, roi.center_y - roi.sample_radius_px - 2.0)
                bundle.label.setVisible(w._rois_visible and w._roi_labels_visible)
            elif update_hidden_details and bundle.label is not None:
                bundle.label.setVisible(False)
        self._update_guide_overlays()
        if w.roi_table.isVisible():
            w._update_roi_table()

    # ------------------------------------------------------------------
    # Landmark overlays
    # ------------------------------------------------------------------

    def _update_landmark_overlays(self) -> None:
        w = self.window
        if w._showing_background_profile_main:
            for bundle in w._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            for bundle in w._chromatic_all_landmark_overlay_items.values():
                bundle.points.setVisible(False)
                if bundle.active_cross is not None:
                    bundle.active_cross.setVisible(False)
                bundle.label.setVisible(False)
            return

        if w._chromatic_reference_points_all_visible:
            for bundle in w._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            w._update_chromatic_all_landmark_overlays()
            return

        if not w._reference_points_visible:
            for bundle in w._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            for bundle in w._chromatic_all_landmark_overlay_items.values():
                bundle.points.setVisible(False)
                if bundle.active_cross is not None:
                    bundle.active_cross.setVisible(False)
                bundle.label.setVisible(False)
            return

        w._clear_chromatic_all_landmark_overlays()
        current_landmarks = w._current_image_landmarks()
        current_ids = {int(mark.landmark_id) for mark in current_landmarks}
        for landmark_id in list(w._landmark_overlay_items):
            if landmark_id not in current_ids:
                bundle = w._landmark_overlay_items.pop(landmark_id)
                w.image_plot.removeItem(bundle.curve)
                w.image_plot.removeItem(bundle.label)

        for mark in current_landmarks:
            landmark_id = int(mark.landmark_id)
            bundle = w._landmark_overlay_items.get(landmark_id)
            if bundle is None:
                curve = pg.PlotCurveItem()
                curve.setSkipFiniteCheck(True)
                w.image_plot.addItem(curve, ignoreBounds=True)
                label = pg.TextItem(anchor=(0.0, 1.0))
                w.image_plot.addItem(label, ignoreBounds=True)
                bundle = LandmarkOverlayBundle(curve=curve, label=label)
                w._landmark_overlay_items[landmark_id] = bundle
            cross_size = 7.0
            xs = np.asarray(
                [
                    float(mark.x_px) - cross_size,
                    float(mark.x_px) + cross_size,
                    np.nan,
                    float(mark.x_px),
                    float(mark.x_px),
                ],
                dtype=np.float64,
            )
            ys = np.asarray(
                [
                    float(mark.y_px),
                    float(mark.y_px),
                    np.nan,
                    float(mark.y_px) - cross_size,
                    float(mark.y_px) + cross_size,
                ],
                dtype=np.float64,
            )
            is_selected = landmark_id == int(w._selected_landmark_id or w._chromatic_landmark_marker_id)
            pen_color = QColor("#f8fafc" if not is_selected else "#38bdf8")
            bundle.curve.setData(xs, ys)
            bundle.curve.setPen(pg.mkPen(pen_color, width=3.0 if is_selected else 2.2))
            bundle.curve.setVisible(True)
            bundle.label.setHtml(
                "<span style="
                f"'color:{pen_color.name()}; "
                "font-size:10pt; "
                "font-style:italic; "
                "font-weight:700; "
                f"background:{'#0f766e' if is_selected else '#0f172a'}; "
                f"border:1px solid {('#5eead4' if is_selected else '#94a3b8')}; "
                "border-radius:4px; "
                "padding:2px 5px;'"
                f">{landmark_id}</span>"
            )
            bundle.label.setPos(float(mark.x_px) + 8.0, float(mark.y_px) - 8.0)
            bundle.label.setVisible(True)

    # ------------------------------------------------------------------
    # Scale bar overlay
    # ------------------------------------------------------------------

    def _refresh_scale_bar_overlay(self) -> None:
        w = self.window
        if w._scale_bar_overlay is None and w._current_processed_image is None:
            return
        overlay = w._ensure_scale_bar_overlay() if w._current_processed_image is not None else w._scale_bar_overlay
        if overlay is not None:
            overlay.line.setPen(pg.mkPen(w._scale_bar_visual_color, width=2.4))
            overlay.left_tick.setPen(pg.mkPen(w._scale_bar_visual_color, width=2.0))
            overlay.right_tick.setPen(pg.mkPen(w._scale_bar_visual_color, width=2.0))
            overlay.outline_line.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=5.0))
            overlay.outline_left_tick.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
            overlay.outline_right_tick.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
            overlay.dark_outline_line.setPen(pg.mkPen(QColor(0, 0, 0, 200), width=6.6))
            overlay.dark_outline_left_tick.setPen(pg.mkPen(QColor(0, 0, 0, 200), width=5.8))
            overlay.dark_outline_right_tick.setPen(pg.mkPen(QColor(0, 0, 0, 200), width=5.8))
        if overlay is None or w._showing_background_profile_main or w._current_processed_image is None or not bool(w._state.preprocessing.scale_bar_visible):
            if overlay is not None:
                overlay.dark_outline_line.setVisible(False)
                overlay.outline_line.setVisible(False)
                overlay.line.setVisible(False)
                overlay.dark_outline_left_tick.setVisible(False)
                overlay.outline_left_tick.setVisible(False)
                overlay.left_tick.setVisible(False)
                overlay.dark_outline_right_tick.setVisible(False)
                overlay.outline_right_tick.setVisible(False)
                overlay.right_tick.setVisible(False)
                overlay.label.setVisible(False)
            return
        x_range, y_range = w.image_plot.vb.viewRange()
        x_left, x_right = sorted((float(x_range[0]), float(x_range[1])))
        y_top, y_bottom = sorted((float(y_range[0]), float(y_range[1])))
        visible_width = max(x_right - x_left, 1.0)
        visible_height = max(y_bottom - y_top, 1.0)
        available_px = visible_width * 0.22
        if w._display_uses_micrometers():
            bar_value = w._nice_scale_bar_value(available_px * w._microns_per_pixel_scalar())
            bar_length_px = bar_value / w._microns_per_pixel_scalar()
            label_text = f"{bar_value:g} µm"
        else:
            bar_value = w._nice_scale_bar_value(available_px)
            bar_length_px = bar_value
            label_text = f"{bar_value:g} px"
        margin_x = max(visible_width * 0.03, 12.0)
        margin_y = max(visible_height * 0.05, 12.0)
        x1 = x_right - margin_x
        x0 = max(x_left + margin_x, x1 - float(bar_length_px))
        y = y_bottom - margin_y
        tick_half = float(np.clip(visible_height * 0.02, 4.0, 8.0))
        overlay.dark_outline_line.setData([x0, x1], [y, y])
        overlay.outline_line.setData([x0, x1], [y, y])
        overlay.line.setData([x0, x1], [y, y])
        overlay.dark_outline_left_tick.setData([x0, x0], [y - tick_half, y + tick_half])
        overlay.outline_left_tick.setData([x0, x0], [y - tick_half, y + tick_half])
        overlay.left_tick.setData([x0, x0], [y - tick_half, y + tick_half])
        overlay.dark_outline_right_tick.setData([x1, x1], [y - tick_half, y + tick_half])
        overlay.outline_right_tick.setData([x1, x1], [y - tick_half, y + tick_half])
        overlay.right_tick.setData([x1, x1], [y - tick_half, y + tick_half])
        # Solid chip instead of a text halo: two same-size overlapping TextItems
        # can't form a real outline (the top one fully covers the one beneath),
        # which is why the old rendering looked inconsistent across backgrounds.
        overlay.label.setHtml(
            "<span style="
            "'color:#f8fafc; "
            "font-size:9.5pt; "
            "font-weight:700; "
            "background:rgba(15,23,42,0.78); "
            "border:1px solid rgba(226,232,240,0.85); "
            "border-radius:4px; "
            "padding:1px 5px;'"
            f">{label_text}</span>"
        )
        label_x = (x0 + x1) * 0.5
        label_y = y - tick_half - max(visible_height * 0.015, 3.0)
        overlay.label.setPos(label_x, label_y)
        overlay.dark_outline_line.setVisible(True)
        overlay.outline_line.setVisible(True)
        overlay.line.setVisible(True)
        overlay.dark_outline_left_tick.setVisible(True)
        overlay.outline_left_tick.setVisible(True)
        overlay.left_tick.setVisible(True)
        overlay.dark_outline_right_tick.setVisible(True)
        overlay.outline_right_tick.setVisible(True)
        overlay.right_tick.setVisible(True)
        overlay.label.setVisible(True)

    # ------------------------------------------------------------------
    # Guide overlays
    # ------------------------------------------------------------------

    def _update_guide_overlays(self) -> None:
        w = self.window
        guide = w._ensure_image_tool_guide()
        if guide is None or w._current_processed_image is None or w._showing_background_profile_main:
            for bundle in w._guide_overlay_items.values():
                bundle.vertical.setVisible(False)
                bundle.horizontal.setVisible(False)
                bundle.marker.setVisible(False)
            self._hide_measurement_overlay()
            self._refresh_scale_bar_overlay()
            return
        visible = w._active_tool in {"rotate", "crop"}
        if not visible:
            guide.vertical.setVisible(False)
            guide.horizontal.setVisible(False)
            guide.marker.setVisible(False)
        else:
            image_height, image_width = w._current_processed_image.shape[:2]
            current_pos = guide.marker.pos()
            x = float(np.clip(current_pos.x(), 0.0, max(float(image_width - 1), 0.0)))
            y = float(np.clip(current_pos.y(), 0.0, max(float(image_height - 1), 0.0)))
            if abs(float(current_pos.x()) - x) > 1e-6 or abs(float(current_pos.y()) - y) > 1e-6:
                guide.marker.blockSignals(True)
                guide.marker.setPos((x, y))
                guide.marker.blockSignals(False)
            guide.vertical.setData([x, x], [0.0, max(float(image_height - 1), 0.0)])
            guide.horizontal.setData([0.0, max(float(image_width - 1), 0.0)], [y, y])
            guide.vertical.setVisible(True)
            guide.horizontal.setVisible(True)
            guide.marker.setVisible(True)
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()

    # ------------------------------------------------------------------
    # Ignore mask overlay
    # ------------------------------------------------------------------

    def _update_ignore_mask_overlay(self) -> None:
        w = self.window
        if w._showing_background_profile_main or w._current_processed_image is None:
            w.ignore_mask_item.hide()
            w.histogram_mask_item.hide()
            w.figure_mask_item.hide()
            return

        mask = w._ignored_mask(w._current_processed_image)
        if w._mask_visible and np.any(mask):
            overlay = np.zeros((*mask.shape, 4), dtype=np.uint8)
            overlay_color = np.array(
                [
                    w._mask_visual_color.red(),
                    w._mask_visual_color.green(),
                    w._mask_visual_color.blue(),
                    int(round(w._mask_alpha * 255.0)),
                ],
                dtype=np.uint8,
            )
            overlay[mask] = overlay_color
            w.ignore_mask_item.setImage(np.transpose(overlay, (1, 0, 2)), autoLevels=False)
            w.ignore_mask_item.show()
        else:
            w.ignore_mask_item.hide()

        w.histogram_mask_item.hide()

        if w._mask_figure_preview is not None:
            preview_mask = apply_spatial_mask(w._mask_figure_preview, w._state.preprocessing)
            if preview_mask.shape == mask.shape and np.any(preview_mask):
                fig_overlay = np.zeros((*preview_mask.shape, 4), dtype=np.uint8)
                fig_color = np.array([56, 189, 248, 110], dtype=np.uint8)
                fig_overlay[preview_mask] = fig_color
                w.figure_mask_item.setImage(np.transpose(fig_overlay, (1, 0, 2)), autoLevels=False)
                w.figure_mask_item.show()
            else:
                w.figure_mask_item.hide()
        else:
            w.figure_mask_item.hide()

    # ------------------------------------------------------------------
    # Selected-intensity (highlight) overlay
    # ------------------------------------------------------------------

    def _update_selected_intensity_overlay(self) -> None:
        w = self.window
        if w._showing_background_profile_main or w._current_processed_image is None or not w._highlight_visible:
            w.intensity_highlight_item.hide()
            return

        lower, upper = w.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        lower = float(np.clip(lower, w.HISTOGRAM_MIN_INTENSITY, w.HISTOGRAM_MAX_INTENSITY))
        upper = float(np.clip(upper, w.HISTOGRAM_MIN_INTENSITY, w.HISTOGRAM_MAX_INTENSITY))
        if lower <= w.HISTOGRAM_MIN_INTENSITY and upper >= w.HISTOGRAM_MAX_INTENSITY:
            w.intensity_highlight_item.hide()
            return

        image = w._current_processed_image.astype(np.float32, copy=False)
        selection_mask = (image >= lower) & (image <= upper)
        if not np.any(selection_mask):
            w.intensity_highlight_item.hide()
            return

        overlay = np.zeros((*selection_mask.shape, 4), dtype=np.uint8)
        overlay[selection_mask] = np.array(
            [
                w._highlight_visual_color.red(),
                w._highlight_visual_color.green(),
                w._highlight_visual_color.blue(),
                int(round(w._highlight_alpha * 255.0)),
            ],
            dtype=np.uint8,
        )
        w.intensity_highlight_item.setImage(np.transpose(overlay, (1, 0, 2)), autoLevels=False)
        w.intensity_highlight_item.show()

    # ------------------------------------------------------------------
    # Reference star overlay
    # ------------------------------------------------------------------

    def _update_reference_star_overlay(self) -> None:
        w = self.window
        star = w._ensure_reference_star_label()
        visible = (
            w._current_processed_image is not None
            and w._is_current_reference_image()
            and not w._showing_background_profile_main
        )
        star.setVisible(bool(visible))
        if visible:
            w._position_reference_star_label()

    # ------------------------------------------------------------------
    # Crop overlay
    # ------------------------------------------------------------------

    def _update_crop_overlay(self) -> None:
        w = self.window
        overlay = w._ensure_crop_overlay()
        if (
            w._crop_roi is None
            or w._current_processed_image is None
            or w._showing_background_profile_main
            or w._active_tool != "crop"
        ):
            overlay.setVisible(False)
            return

        image_height, image_width = w._current_processed_image.shape[:2]
        width = max(float(image_width), 1.0)
        height = max(float(image_height), 1.0)
        pos = w._crop_roi.pos()
        size = w._crop_roi.size()
        x = float(np.clip(pos.x(), 0.0, max(width - 1.0, 0.0)))
        y = float(np.clip(pos.y(), 0.0, max(height - 1.0, 0.0)))
        crop_width = float(np.clip(size.x(), 1.0, width))
        crop_height = float(np.clip(size.y(), 1.0, height))
        x = float(np.clip(x, 0.0, max(width - crop_width, 0.0)))
        y = float(np.clip(y, 0.0, max(height - crop_height, 0.0)))

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(0.0, 0.0, width, height))
        path.addRect(QRectF(x, y, crop_width, crop_height))
        overlay.setPath(path)
        overlay.setVisible(True)

    # ------------------------------------------------------------------
    # Measurement overlay
    # ------------------------------------------------------------------

    def _hide_measurement_overlay(self) -> None:
        w = self.window
        if w._measurement_overlay is None:
            return
        w._measurement_overlay.connector.setVisible(False)
        w._measurement_overlay.marker_a.setVisible(False)
        w._measurement_overlay.marker_b.setVisible(False)
        w._measurement_overlay.label.setVisible(False)

    def _ensure_measurement_overlay(self) -> MeasurementOverlayBundle:
        w = self.window
        if w._measurement_overlay is not None:
            return w._measurement_overlay
        settings = w._state.preprocessing
        marker_a = pg.TargetItem(
            pos=(float(settings.measurement_anchor1_x_px), float(settings.measurement_anchor1_y_px)),
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverPen=pg.mkPen("#22c55e", width=2.0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            size=12,
        )
        marker_b = pg.TargetItem(
            pos=(float(settings.measurement_anchor2_x_px), float(settings.measurement_anchor2_y_px)),
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverPen=pg.mkPen("#22c55e", width=2.0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            size=12,
        )
        connector = pg.PlotCurveItem(pen=pg.mkPen(QColor(34, 197, 94, 180), width=1.8))
        label = pg.TextItem(anchor=(0.5, 1.0))
        marker_a.sigPositionChanged.connect(w._on_measurement_marker_moved)
        marker_b.sigPositionChanged.connect(w._on_measurement_marker_moved)
        marker_a.sigPositionChangeFinished.connect(w._on_measurement_marker_moved)
        marker_b.sigPositionChangeFinished.connect(w._on_measurement_marker_moved)
        w.image_plot.addItem(connector, ignoreBounds=True)
        w.image_plot.addItem(marker_a, ignoreBounds=True)
        w.image_plot.addItem(marker_b, ignoreBounds=True)
        w.image_plot.addItem(label, ignoreBounds=True)
        w._measurement_overlay = MeasurementOverlayBundle(
            connector=connector,
            marker_a=marker_a,
            marker_b=marker_b,
            label=label,
        )
        return w._measurement_overlay

    def _update_measurement_overlay(self) -> None:
        w = self.window
        if w._current_processed_image is None or w._showing_background_profile_main or w._active_tool != "measure":
            self._hide_measurement_overlay()
            return
        overlay = self._ensure_measurement_overlay()
        image_height, image_width = w._current_processed_image.shape[:2]
        settings = w._state.preprocessing
        points = [
            (overlay.marker_a, "measurement_anchor1_x_px", "measurement_anchor1_y_px"),
            (overlay.marker_b, "measurement_anchor2_x_px", "measurement_anchor2_y_px"),
        ]
        for marker, x_attr, y_attr in points:
            x_value = float(np.clip(float(getattr(settings, x_attr)), 0.0, max(float(image_width - 1), 0.0)))
            y_value = float(np.clip(float(getattr(settings, y_attr)), 0.0, max(float(image_height - 1), 0.0)))
            setattr(settings, x_attr, x_value)
            setattr(settings, y_attr, y_value)
            current_pos = marker.pos()
            if abs(float(current_pos.x()) - x_value) > 1e-6 or abs(float(current_pos.y()) - y_value) > 1e-6:
                marker.blockSignals(True)
                marker.setPos((x_value, y_value))
                marker.blockSignals(False)
            marker.setVisible(True)
        ax = float(settings.measurement_anchor1_x_px)
        ay = float(settings.measurement_anchor1_y_px)
        bx = float(settings.measurement_anchor2_x_px)
        by = float(settings.measurement_anchor2_y_px)
        overlay.connector.setData([ax, bx], [ay, by])
        overlay.connector.setVisible(True)
        dx, dy, distance = w._measurement_delta_components_px()
        w._normalize_display_units()
        if w._can_display_micrometers():
            distance_um = w._microns_per_pixel_scalar() * distance
            if w._display_uses_micrometers():
                bar_label = f"{distance_um:.1f} µm | {distance:.1f} px"
            else:
                bar_label = f"{distance:.1f} px | {distance_um:.1f} µm"
        else:
            bar_label = f"{distance:.1f} px"
        overlay.label.setHtml(
            "<span style="
            "'color:#22c55e; font-size:10pt; font-weight:700; background:#0f172a; "
            "border:1px solid #22c55e; border-radius:4px; padding:2px 5px;'"
            f">{bar_label}</span>"
        )
        overlay.label.setPos((ax + bx) * 0.5, min(ay, by) - 8.0)
        overlay.label.setVisible(True)
        self._update_measurement_status_label(dx_px=dx, dy_px=dy, distance_px=distance)

    def _on_measurement_marker_moved(self, *_args) -> None:
        w = self.window
        if w._measurement_overlay is None:
            return
        settings = w._state.preprocessing
        settings.measurement_anchor1_x_px = float(w._measurement_overlay.marker_a.pos().x())
        settings.measurement_anchor1_y_px = float(w._measurement_overlay.marker_a.pos().y())
        settings.measurement_anchor2_x_px = float(w._measurement_overlay.marker_b.pos().x())
        settings.measurement_anchor2_y_px = float(w._measurement_overlay.marker_b.pos().y())
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()
        w._schedule_processing_state_save()

    def _update_measurement_status_label(
        self,
        *,
        dx_px: float | None = None,
        dy_px: float | None = None,
        distance_px: float | None = None,
    ) -> None:
        w = self.window
        if dx_px is None or dy_px is None or distance_px is None:
            dx_px, dy_px, distance_px = w._measurement_delta_components_px()
        if w._can_display_micrometers():
            dx_um = abs(w._microns_per_pixel_scalar() * dx_px)
            dy_um = abs(w._microns_per_pixel_scalar() * dy_px)
            distance_um = w._microns_per_pixel_scalar() * distance_px
            w.measurement_status_label.setText(
                f"Δx {abs(dx_px):.1f} px / {dx_um:.1f} µm | "
                f"Δy {abs(dy_px):.1f} px / {dy_um:.1f} µm | "
                f"d {distance_px:.1f} px / {distance_um:.1f} µm"
            )
        else:
            w.measurement_status_label.setText(
                f"Δx {abs(dx_px):.1f} px | Δy {abs(dy_px):.1f} px | d {distance_px:.1f} px"
            )

    # ------------------------------------------------------------------
    # Image tool guide
    # ------------------------------------------------------------------

    def _on_image_tool_guide_moved(self, *_args) -> None:
        self._update_guide_overlays()

    # ------------------------------------------------------------------
    # Histogram events
    # ------------------------------------------------------------------

    def _refresh_histogram_if_available(self) -> None:
        self.window._plot_manager.refresh_histogram_if_available()

    def _on_histogram_y_scale_toggled(self, checked: bool) -> None:
        self.window._plot_manager.on_histogram_y_scale_toggled(checked)

    def _on_histogram_view_range_changed(self, *_args) -> None:
        self.window._plot_manager.on_histogram_view_range_changed(*_args)

    def _on_histogram_region_changed(self) -> None:
        w = self.window
        w._push_undo_point("Highlight range")
        lower, upper = w.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        w._state.area_roi_settings.intensity_min_value = float(
            np.clip(lower, w.HISTOGRAM_MIN_INTENSITY, w.HISTOGRAM_MAX_INTENSITY)
        )
        w._state.area_roi_settings.intensity_max_value = float(
            np.clip(upper, w.HISTOGRAM_MIN_INTENSITY, w.HISTOGRAM_MAX_INTENSITY)
        )
        w._state.preprocessing.histogram_highlight_min_value = float(lower)
        w._state.preprocessing.histogram_highlight_max_value = float(upper)
        self._update_selected_intensity_overlay()
        w._save_control_preferences()
        w._save_processing_state_for_dataset()

    def _on_ignore_region_changed(self) -> None:
        return

    def _on_histogram_bins_changed(self, _value: int) -> None:
        self.window._schedule_histogram_refresh()
