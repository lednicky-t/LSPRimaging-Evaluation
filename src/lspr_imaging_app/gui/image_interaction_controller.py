from __future__ import annotations

from math import hypot

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QRubberBand


class ImageInteractionController:
    """Handles all image-viewport mouse/keyboard interaction for MainWindow.

    MainWindow.eventFilter delegates to handle_event().  The six pure
    image-interaction helpers (_image_point_from_mouse_event,
    _selection_drag_threshold, _begin_image_pan, _update_image_pan,
    _end_image_pan, _crop_rect_contains_point) live here and access the
    window through self.window.
    """

    def __init__(self, window) -> None:
        self.window = window

    # ------------------------------------------------------------------
    # Public entry-point called from MainWindow.eventFilter
    # ------------------------------------------------------------------

    def handle_event(self, watched, event) -> bool:
        """Process a watched-object event.

        Returns True if the event was consumed, False to fall through to
        super().eventFilter().
        """
        w = self.window

        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if w._handle_global_page_shortcuts(event):
                return True

        workflow_log_view = getattr(w, "workflow_log_view", None)
        if workflow_log_view is not None and watched is workflow_log_view and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.matches(QKeySequence.StandardKey.Copy):
                w._copy_workflow_log()
                return True

        roi_table = getattr(w, "roi_table", None)
        if roi_table is not None and watched is roi_table and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if key_event.matches(QKeySequence.StandardKey.Undo):
                w._undo()
                return True
            if key_event.matches(QKeySequence.StandardKey.Redo):
                w._redo()
                return True
            if key_event.key() == Qt.Key.Key_PageUp:
                w._move_selected_rois_in_table(-1)
                return True
            if key_event.key() == Qt.Key.Key_PageDown:
                w._move_selected_rois_in_table(1)
                return True
            if key_event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
                w._remove_selected_rois()
                return True
            if key_event.matches(QKeySequence.StandardKey.Copy):
                w._copy_roi_properties_from_table()
                return True
            if key_event.matches(QKeySequence.StandardKey.Paste):
                w._paste_roi_properties_from_table()
                return True

        if watched is w.chromatic_landmark_id_spin and event.type() == QEvent.Type.KeyPress and w._active_tool == "chromatic_landmark":
            key_event = event
            if key_event.key() in {Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}:
                direction = -1 if key_event.key() == Qt.Key.Key_PageUp else 1
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if w._navigate_chromatic_sample(direction):
                        return True
                elif w._switch_chromatic_feature(direction):
                    return True

        if watched is w.wavelength_spin and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            w._step_wavelength_selection(step)
            return True

        if watched is w.spectral_cube_spin and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            w._step_spectral_cube_selection(step)
            return True

        if watched is w.wavelength_slider and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            w._step_wavelength_selection(step)
            return True

        if watched is w.spectral_cube_slider and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            w._step_spectral_cube_selection(step)
            return True

        if watched in {w.sample_diameter_spin, w.reference_inner_diameter_spin, w.reference_outer_diameter_spin} and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            spinbox = watched
            if hasattr(spinbox, "stepBy"):
                spinbox.stepBy(step)
            return True

        roi_list_viewport = w.roi_table.viewport() if hasattr(w, "roi_table") else None
        if roi_list_viewport is not None and watched is roi_list_viewport and event.type() == QEvent.Type.MouseButtonPress:
            mouse_event = event
            if mouse_event.button() == Qt.MouseButton.LeftButton:
                row = w.roi_table.rowAt(int(mouse_event.position().toPoint().y()))
                if row >= 0:
                    if mouse_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        anchor = w._roi_list_range_anchor_row
                        if anchor is None or anchor < 0 or anchor >= w.roi_table.rowCount():
                            anchor = w.roi_table.currentRow()
                        if anchor < 0:
                            anchor = row
                        start_row, end_row = sorted((anchor, row))
                        w._select_roi_table_rows(list(range(start_row, end_row + 1)))
                        return True
                    w._roi_list_range_anchor_row = row

        image_view = getattr(w, "image_view", None)
        if image_view is None:
            return False

        if watched is image_view.viewport() and event.type() == QEvent.Type.Resize:
            w._position_reference_star_label()
            return False

        if watched is image_view.viewport() and w._active_tool == "crop":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.MiddleButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                self._begin_image_pan(point)
                return True
            if event.type() == QEvent.Type.MouseMove and w._panning_image:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                self._update_image_pan(point)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.MiddleButton and w._panning_image:
                self._end_image_pan()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                if self._crop_rect_contains_point(point):
                    w._prepare_undo_snapshot("Crop")
                    w._dragging_crop = True
                    w._crop_drag_anchor = point
                    crop = w._state.preprocessing.crop
                    w._crop_drag_origin = (float(crop.x), float(crop.y))
                    return True
                return False
            if event.type() == QEvent.Type.MouseMove and w._dragging_crop and w._crop_drag_anchor is not None and w._crop_drag_origin is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                dx = float(point[0]) - float(w._crop_drag_anchor[0])
                dy = float(point[1]) - float(w._crop_drag_anchor[1])
                w._move_crop_roi_to(float(w._crop_drag_origin[0] + dx), float(w._crop_drag_origin[1] + dy))
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton and w._dragging_crop:
                w._dragging_crop = False
                w._crop_drag_anchor = None
                w._crop_drag_origin = None
                w._handle_image_tool_settings_changed("Crop moved.", preserve_view=True)
                w._commit_prepared_undo_snapshot()
                return True

        if watched is image_view.viewport() and w._active_tool == "chromatic_landmark":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                landmark_id = w._find_landmark_id_at(point)
                if landmark_id is not None:
                    w._selected_landmark_id = landmark_id
                    w.chromatic_landmark_id_spin.blockSignals(True)
                    w.chromatic_landmark_id_spin.setValue(landmark_id)
                    w.chromatic_landmark_id_spin.blockSignals(False)
                    w._prepare_undo_snapshot("Chromatic landmarks")
                    w._dragging_landmark = True
                    w._dragging_landmark_started = False
                    w._update_landmark_overlays()
                else:
                    w._selected_landmark_id = int(w._chromatic_landmark_marker_id)
                    w._set_current_landmark(point)
                return True
            if event.type() == QEvent.Type.MouseMove and w._dragging_landmark and w._selected_landmark_id is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                w._dragging_landmark_started = True
                mark = w._current_landmark(int(w._selected_landmark_id))
                if mark is not None:
                    max_x = float(w._current_processed_image.shape[1] - 1) if w._current_processed_image is not None else float(mark.x_px)
                    max_y = float(w._current_processed_image.shape[0] - 1) if w._current_processed_image is not None else float(mark.y_px)
                    mark.x_px = float(np.clip(point[0], 0.0, max_x))
                    mark.y_px = float(np.clip(point[1], 0.0, max_y))
                    w._update_landmark_overlays()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and w._dragging_landmark:
                w._dragging_landmark = False
                moved = w._dragging_landmark_started
                w._dragging_landmark_started = False
                if moved:
                    w._finalize_chromatic_landmark_edit(status_text=f"Adjusted reference point {w._selected_landmark_id}.")
                w._commit_prepared_undo_snapshot()
                return True

        if watched is w.image_view.viewport() and w._active_tool == "mask":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                w._prepare_undo_snapshot("Edit mask")
                w._mask_drawing = True
                w._apply_mask_brush(point)
                return True

            if event.type() == QEvent.Type.MouseMove and w._mask_drawing:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                w._apply_mask_brush(point)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and w._mask_drawing:
                w._mask_drawing = False
                w._finalize_mask_edit()
                return True

        selection_mode_active = (
            (w._active_tool == "roi")
            or (w._analysis_enabled and w._state.dataset is not None)
        )
        if watched is w.image_view.viewport() and selection_mode_active:
            allow_roi_add = w._active_tool == "roi" and w.roi_add_action.isChecked()
            allow_roi_array = w._active_tool == "roi" and w.roi_array_action.isChecked()
            allow_roi_move = w._active_tool == "roi" and w.roi_move_action.isChecked()
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                if allow_roi_array:
                    w._add_roi_array_at(point)
                    return True
                if allow_roi_add:
                    w._add_roi_at(point)
                    return True
                roi_id = w._find_roi_id_at(point)
                modifiers = event.modifiers()
                w._roi_selection_drag_start = point
                w._roi_selection_drag_button = Qt.MouseButton.LeftButton
                w._roi_selection_pressed_id = roi_id
                w._roi_selection_drag_modifiers = modifiers
                if w._roi_selection_rubber_band is None:
                    w._roi_selection_rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, w.image_view.viewport())
                start_point = w.image_view.mapFromScene(w.image_plot.vb.mapViewToScene(pg.Point(point[0], point[1])))
                w._roi_selection_rubber_band.setGeometry(start_point.x(), start_point.y(), 0, 0)
                w._roi_selection_rubber_band.hide()
                if roi_id is not None:
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        w._selected_roi_ids.add(roi_id)
                    else:
                        w._selected_roi_ids = {roi_id}
                w._update_roi_overlays()
                w._update_roi_summary()
                w._sync_roi_table_selection()
                w._update_selection_dependent_plots(prompt_live_preview=True)
                return True

            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                roi_id = w._find_roi_id_at(point)
                if allow_roi_move and w._is_current_reference_image() and roi_id is not None:
                    if w._selected_roi_ids:
                        drag_roi_ids = set(w._selected_roi_ids)
                    else:
                        w._selected_roi_ids = {roi_id}
                        drag_roi_ids = {roi_id}
                    w._update_roi_overlays()
                    w._update_roi_summary()
                    w._sync_roi_table_selection()
                    w._update_selection_dependent_plots(prompt_live_preview=True)
                    w._prepare_undo_snapshot("Move ROIs")
                    w._dragging_rois = True
                    w._drag_anchor = point
                    w._drag_original_positions = {
                        roi.area_roi_id: (roi.center_x, roi.center_y)
                        for roi in w._state.area_rois
                        if roi.area_roi_id in drag_roi_ids
                    }
                    w._roi_selection_drag_button = Qt.MouseButton.RightButton
                    w._roi_selection_drag_start = None
                    w._roi_selection_pressed_id = None
                    w._roi_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                    return True
                if w._analysis_enabled and roi_id is not None:
                    if roi_id not in w._selected_roi_ids:
                        w._selected_roi_ids = {roi_id}
                        w._update_roi_overlays()
                        w._update_roi_summary()
                        w._sync_roi_table_selection()
                        w._update_selection_dependent_plots(prompt_live_preview=True)
                    w._show_analysis_roi_context_menu(roi_id, event.globalPosition().toPoint())
                    return True
                return True

            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.RightButton:
                if not w._analysis_enabled:
                    return True
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                roi_id = w._find_roi_id_at(point)
                if roi_id is None:
                    return True
                if w._select_group_members_for_roi(roi_id):
                    w.status_label.setText(f"Selected group members for ROI {roi_id}.")
                return True

            if event.type() == QEvent.Type.MouseMove and w._dragging_rois and w._drag_anchor is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                dx = point[0] - w._drag_anchor[0]
                dy = point[1] - w._drag_anchor[1]
                for roi in w._state.area_rois:
                    if roi.area_roi_id not in w._selected_roi_ids or roi.area_roi_id not in w._drag_original_positions:
                        continue
                    base_x, base_y = w._drag_original_positions[roi.area_roi_id]
                    roi.center_x, roi.center_y = w._clamp_roi_position(roi, base_x + dx, base_y + dy)
                w._schedule_roi_overlay_refresh()
                return True

            if (
                event.type() == QEvent.Type.MouseMove
                and w._roi_selection_drag_start is not None
                and w._roi_selection_drag_button == Qt.MouseButton.LeftButton
                and w._roi_selection_rubber_band is not None
                and not w._dragging_rois
            ):
                current_point = self._image_point_from_mouse_event(event)
                if current_point is None:
                    return True
                start_scene = w.image_plot.vb.mapViewToScene(pg.Point(w._roi_selection_drag_start[0], w._roi_selection_drag_start[1]))
                current_scene = w.image_plot.vb.mapViewToScene(pg.Point(current_point[0], current_point[1]))
                if (
                    not w._roi_selection_rubber_band.isVisible()
                    and hypot(
                        float(current_point[0] - w._roi_selection_drag_start[0]),
                        float(current_point[1] - w._roi_selection_drag_start[1]),
                    ) < self._selection_drag_threshold()
                ):
                    return True
                w._roi_selection_rubber_band.show()
                top_left = QPointF(min(start_scene.x(), current_scene.x()), min(start_scene.y(), current_scene.y()))
                bottom_right = QPointF(max(start_scene.x(), current_scene.x()), max(start_scene.y(), current_scene.y()))
                rect = QRectF(top_left, bottom_right)
                viewport_rect = w.image_view.mapFromScene(rect).boundingRect()
                w._roi_selection_rubber_band.setGeometry(viewport_rect)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton and w._dragging_rois:
                w._dragging_rois = False
                w._drag_anchor = None
                w._drag_original_positions.clear()
                w._roi_selection_drag_button = None
                w._roi_overlay_refresh_timer.stop()
                w._update_roi_overlays()
                # _mark_roi_edit_refresh_pending() already saves+schedules
                # when the ROI tool is active, which this drag path requires
                # (see allow_roi_move above) - see roi_geometry_mixin.py's
                # _move_selected_rois for the matching keyboard-nudge fix.
                w._mark_roi_edit_refresh_pending()
                w.status_label.setText(f"Moved {len(w._selected_roi_ids)} selected ROIs.")
                return True

            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and w._roi_selection_drag_start is not None
                and w._roi_selection_drag_button == Qt.MouseButton.LeftButton
            ):
                if w._roi_selection_rubber_band is not None:
                    w._roi_selection_rubber_band.hide()
                end_point = self._image_point_from_mouse_event(event)
                start_x, start_y = w._roi_selection_drag_start
                drag_modifiers = w._roi_selection_drag_modifiers
                if end_point is None:
                    end_x, end_y = start_x, start_y
                else:
                    end_x, end_y = end_point
                drag_distance = hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
                if drag_distance < 2.0 and w._roi_selection_pressed_id is not None:
                    clicked_roi_id = w._roi_selection_pressed_id
                    if drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
                        w._selected_roi_ids.add(clicked_roi_id)
                    else:
                        w._selected_roi_ids = {clicked_roi_id}
                    w._roi_selection_drag_start = None
                    w._roi_selection_drag_button = None
                    w._roi_selection_pressed_id = None
                    w._roi_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                    w._update_roi_overlays()
                    w._update_roi_summary()
                    w._sync_roi_table_selection()
                    w._update_selection_dependent_plots(prompt_live_preview=True)
                    return True
                left = min(start_x, end_x)
                right = max(start_x, end_x)
                top = min(start_y, end_y)
                bottom = max(start_y, end_y)
                dragged_roi_ids = {
                    roi.area_roi_id
                    for roi in w._display_rois()
                    if left <= float(roi.center_x) <= right and top <= float(roi.center_y) <= bottom
                }
                if drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
                    w._selected_roi_ids.update(dragged_roi_ids)
                else:
                    w._selected_roi_ids = set(dragged_roi_ids)
                w._roi_selection_drag_start = None
                w._roi_selection_drag_button = None
                w._roi_selection_pressed_id = None
                w._roi_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                w._update_roi_overlays()
                w._update_roi_summary()
                w._sync_roi_table_selection()
                w._update_selection_dependent_plots(prompt_live_preview=True)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                w._roi_selection_drag_start = None
                w._roi_selection_drag_button = None
                w._roi_selection_pressed_id = None
                w._roi_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                return True

        return False

    # ------------------------------------------------------------------
    # Image-interaction helpers (moved from MainWindow)
    # ------------------------------------------------------------------

    def _image_point_from_mouse_event(self, event) -> tuple[float, float] | None:
        w = self.window
        scene_pos = w.image_view.mapToScene(event.position().toPoint())
        if not w.image_plot.sceneBoundingRect().contains(scene_pos):
            return None
        mouse_point = w.image_plot.vb.mapSceneToView(scene_pos)
        return float(mouse_point.x()), float(mouse_point.y())

    def _selection_drag_threshold(self) -> float:
        return 5.0

    def _begin_image_pan(self, point: tuple[float, float]) -> None:
        w = self.window
        if w._current_processed_image is None:
            return
        x_range, y_range = w.image_plot.vb.viewRange()
        w._panning_image = True
        w._pan_anchor_view = (float(point[0]), float(point[1]))
        w._pan_anchor_ranges = ((float(x_range[0]), float(x_range[1])), (float(y_range[0]), float(y_range[1])))

    def _update_image_pan(self, point: tuple[float, float]) -> None:
        w = self.window
        if not w._panning_image or w._pan_anchor_view is None or w._pan_anchor_ranges is None:
            return
        x_range, y_range = w._pan_anchor_ranges
        dx = float(point[0]) - float(w._pan_anchor_view[0])
        dy = float(point[1]) - float(w._pan_anchor_view[1])
        w.image_plot.vb.setRange(
            xRange=(x_range[0] - dx, x_range[1] - dx),
            yRange=(y_range[0] - dy, y_range[1] - dy),
            padding=0.0,
        )

    def _end_image_pan(self) -> None:
        w = self.window
        w._panning_image = False
        w._pan_anchor_view = None
        w._pan_anchor_ranges = None

    def _crop_rect_contains_point(self, point: tuple[float, float]) -> bool:
        w = self.window
        if w._crop_roi is None:
            return False
        pos = w._crop_roi.pos()
        size = w._crop_roi.size()
        x0 = float(pos.x())
        y0 = float(pos.y())
        x1 = x0 + float(size.x())
        y1 = y0 + float(size.y())
        x = float(point[0])
        y = float(point[1])
        return x0 <= x <= x1 and y0 <= y <= y1
