from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt

from lspr_imaging_app.domain.models import RoiDefinition
from lspr_imaging_app.domain.roi_editor_tools import (
    create_rois_from_template,
    create_rois_from_template_grid,
    move_roi_from_template,
    roi_top_left_from_center,
)


class RectangleStampMixin:
    """The rectangle/circle "stamp" annotation tool (RoiDefinition), a
    separate feature from the sample/reference AreaRoi pair used for
    absorbance analysis - see CLAUDE.md's note on the two unrelated meanings
    of ROI in this app. Mixed into MainWindow (same pattern as
    MainWindowIcons): `self` here is the MainWindow instance, so these
    methods use the same window state/widgets as the rest of the class.
    """

    def _next_roi_id(self) -> str:
        self._roi_id_counter += 1
        return f"roi_{self._roi_id_counter}"

    def _reset_roi_id_counter_from_state(self) -> None:
        max_index = 0
        for roi in self._state.rois:
            if roi.roi_id.startswith("roi_"):
                suffix = roi.roi_id[4:]
                if suffix.isdigit():
                    max_index = max(max_index, int(suffix))
        self._roi_id_counter = max_index

    def _active_rectangle_template(self) -> RoiDefinition:
        if self._roi_editor_mode == "circles":
            sample_diameter = max(float(self._length_display_to_px(float(self.sample_diameter_spin.value()))), 2.0)
            reference_inner_diameter = max(float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))), 0.0)
            reference_outer_diameter = max(
                float(self._length_display_to_px(float(self.reference_outer_diameter_spin.value()))),
                reference_inner_diameter,
            )
            padding = max((reference_inner_diameter - sample_diameter) / 2.0, 0.0)
            width = max((reference_outer_diameter - reference_inner_diameter) / 2.0, 0.0)
            return RoiDefinition(
                roi_id="circle_template",
                name="Circle ROI",
                shape="circle",
                center_x=float(self._rectangle_template.center_x),
                center_y=float(self._rectangle_template.center_y),
                size_x=sample_diameter,
                size_y=sample_diameter,
                background_padding_px=padding,
                background_width_px=width,
                enabled=True,
            )
        self._rectangle_template.shape = "rectangle"
        return self._rectangle_template

    def _ensure_rectangle_roi(self) -> pg.RectROI | None:
        if self._current_processed_image is None:
            return None
        roi = self._active_rectangle_template()
        image_height, image_width = self._current_processed_image.shape[:2]
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        if self._rectangle_roi is None:
            if str(roi.shape).lower() in {"circle", "ellipse"}:
                self._rectangle_roi = pg.EllipseROI(
                    [x, y],
                    [width, height],
                    pen=pg.mkPen("#f59e0b", width=2),
                    movable=True,
                    resizable=True,
                    rotatable=False,
                )
                for _h in list(self._rectangle_roi.handles):
                    if _h["type"] == "r":
                        self._rectangle_roi.removeHandle(_h["item"])
            else:
                self._rectangle_roi = pg.RectROI(
                    [x, y],
                    [width, height],
                    pen=pg.mkPen("#f59e0b", width=2),
                    movable=True,
                    resizable=True,
                    rotatable=False,
                    sideScalers=True,
                )
            self._rectangle_roi.handleSize = 10
            self._rectangle_roi.sigRegionChanged.connect(lambda *_args: self._rectangle_roi_changed(finished=False))
            self._rectangle_roi.sigRegionChangeFinished.connect(lambda *_args: self._rectangle_roi_changed(finished=True))
            self.image_plot.addItem(self._rectangle_roi)
            self._rectangle_roi.setZValue(1.2)
        self._suspend_rectangle_sync = True
        self._rectangle_roi.setPos((x, y))
        self._rectangle_roi.setSize((width, height))
        self._suspend_rectangle_sync = False
        return self._rectangle_roi

    def _sync_rectangle_roi_visibility(self) -> None:
        if self._rectangle_roi is None:
            self._ensure_rectangle_roi()
        if self._rectangle_roi is not None:
            self._rectangle_roi.setVisible(
                self._roi_editor_mode == "rectangles"
                and not self._showing_background_profile_main
                and self._current_processed_image is not None
            )
        self._update_guide_overlays()

    def _sync_rectangle_roi_from_definition(self) -> None:
        roi = self._active_rectangle_template()
        if self._rectangle_roi is None:
            self._ensure_rectangle_roi()
        if self._rectangle_roi is None:
            return
        image_height, image_width = self._current_processed_image.shape[:2] if self._current_processed_image is not None else (0, 0)
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        self._suspend_rectangle_sync = True
        self._rectangle_roi.setPos((x, y))
        self._rectangle_roi.setSize((width, height))
        self._suspend_rectangle_sync = False
        self._update_rectangle_roi_summary()

    def _rectangle_roi_changed(self, *, finished: bool) -> None:
        if self._suspend_rectangle_sync or self._rectangle_roi is None:
            return
        pos = self._rectangle_roi.pos()
        size = self._rectangle_roi.size()
        roi = self._active_rectangle_template()
        roi.shape = str(roi.shape or "rectangle")
        roi.center_x = float(pos.x() + size.x() / 2.0)
        roi.center_y = float(pos.y() + size.y() / 2.0)
        roi.size_x = max(float(size.x()), 2.0)
        roi.size_y = max(float(size.y()), 2.0)
        if self._roi_editor_mode == "circles":
            self._rectangle_template.center_x = roi.center_x
            self._rectangle_template.center_y = roi.center_y
            self.sample_diameter_spin.blockSignals(True)
            self.sample_diameter_spin.setValue(self._length_px_to_display(float(max(roi.size_x, roi.size_y))))
            self.sample_diameter_spin.blockSignals(False)
            self._state.area_roi_settings.sample_radius_px = max(float(roi.size_x), 1.0) / 2.0
            self._state.area_roi_settings.reference_inner_radius_px = max(
                float(roi.size_x) / 2.0 + float(roi.background_padding_px),
                float(roi.size_x) / 2.0,
            )
            self._state.area_roi_settings.reference_outer_radius_px = max(
                float(self._state.area_roi_settings.reference_inner_radius_px) + float(roi.background_width_px),
                float(self._state.area_roi_settings.reference_inner_radius_px),
            )
        if finished:
            self._push_undo_point("Rectangle ROI")
            self._save_processing_state_for_dataset()
        if self._roi_editor_mode == "circles":
            self._update_roi_detection_labels(sync_controls=False)
        else:
            self._update_rectangle_roi_controls(sync_roi=False)
        self._update_rectangle_roi_summary()

    def _update_rectangle_roi_controls(self, *, sync_roi: bool = True) -> None:
        roi = self._active_rectangle_template()
        if sync_roi:
            self._sync_rectangle_roi_from_definition()
        self.rectangle_name_edit.blockSignals(True)
        self.rectangle_name_edit.setText(roi.name)
        self.rectangle_name_edit.blockSignals(False)
        self.rectangle_width_spin.blockSignals(True)
        self.rectangle_height_spin.blockSignals(True)
        self.rectangle_padding_spin.blockSignals(True)
        self.rectangle_background_width_spin.blockSignals(True)
        self.rectangle_width_spin.setValue(self._length_px_to_display(float(roi.size_x)))
        self.rectangle_height_spin.setValue(self._length_px_to_display(float(roi.size_y)))
        self.rectangle_padding_spin.setValue(self._length_px_to_display(float(roi.background_padding_px)))
        self.rectangle_background_width_spin.setValue(self._length_px_to_display(float(roi.background_width_px)))
        self.rectangle_width_spin.blockSignals(False)
        self.rectangle_height_spin.blockSignals(False)
        self.rectangle_padding_spin.blockSignals(False)
        self.rectangle_background_width_spin.blockSignals(False)

    def _update_rectangle_roi_summary(self) -> None:
        roi = self._active_rectangle_template()
        if self._roi_editor_mode == "circles":
            self.rectangle_summary_label.setText(
                (
                    f"Template center=({self._length_px_to_display(float(roi.center_x)):.1f}, "
                    f"{self._length_px_to_display(float(roi.center_y)):.1f}) "
                    f"ROI D={self._length_px_to_display(float(roi.size_x)):.1f} "
                    f"Ring pad={self._length_px_to_display(float(roi.background_padding_px)):.1f} "
                    f"Ring width={self._length_px_to_display(float(roi.background_width_px)):.1f}"
                )
            )
        else:
            self.rectangle_summary_label.setText(
                (
                    f"Center=({self._length_px_to_display(float(roi.center_x)):.1f}, "
                    f"{self._length_px_to_display(float(roi.center_y)):.1f}) "
                    f"Size={self._length_px_to_display(float(roi.size_x)):.1f}x{self._length_px_to_display(float(roi.size_y)):.1f}"
                )
            )

    def _commit_rectangle_roi_edits(self) -> None:
        roi = self._active_rectangle_template()
        if self._roi_editor_mode == "circles":
            roi.name = roi.name.strip() or "Circle ROI"
            roi.size_x = max(float(self._length_display_to_px(float(self.sample_diameter_spin.value()))), 2.0)
            roi.size_y = float(roi.size_x)
            roi.background_padding_px = max(
                float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))) - float(roi.size_x),
                0.0,
            ) / 2.0
            roi.background_width_px = max(
                float(self._length_display_to_px(float(self.reference_outer_diameter_spin.value())))
                - float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))),
                0.0,
            ) / 2.0
            self._update_roi_detection_labels(sync_controls=False)
        else:
            roi.name = self.rectangle_name_edit.text().strip() or roi.name
            roi.size_x = max(float(self._length_display_to_px(float(self.rectangle_width_spin.value()))), 2.0)
            roi.size_y = max(float(self._length_display_to_px(float(self.rectangle_height_spin.value()))), 2.0)
            roi.background_padding_px = max(float(self._length_display_to_px(float(self.rectangle_padding_spin.value()))), 0.0)
            roi.background_width_px = max(float(self._length_display_to_px(float(self.rectangle_background_width_spin.value()))), 0.0)
        self._sync_rectangle_roi_from_definition()
        self._save_processing_state_for_dataset()

    def _create_rectangle_stamp(self, roi: RoiDefinition) -> pg.RectROI | None:
        if self._current_processed_image is None:
            return None
        image_height, image_width = self._current_processed_image.shape[:2]
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        if str(roi.shape).lower() in {"circle", "ellipse"}:
            item = pg.EllipseROI(
                [x, y],
                [width, height],
                pen=pg.mkPen("#f59e0b", width=2),
                movable=True,
                resizable=True,
                rotatable=False,
            )
            for _h in list(item.handles):
                if _h["type"] == "r":
                    item.removeHandle(_h["item"])
        else:
            item = pg.RectROI(
                [x, y],
                [width, height],
                pen=pg.mkPen("#f59e0b", width=2),
                movable=True,
                resizable=True,
                rotatable=False,
                sideScalers=True,
            )
        item.handleSize = 10
        item.sigRegionChanged.connect(lambda *_args, roi=roi: self._rectangle_stamp_changed(roi, finished=False))
        item.sigRegionChangeFinished.connect(lambda *_args, roi=roi: self._rectangle_stamp_changed(roi, finished=True))
        self.image_plot.addItem(item)
        item.setZValue(1.15)
        return item

    def _sync_rectangle_stamp_overlays(self) -> None:
        for item in self._rectangle_stamp_items:
            self.image_plot.removeItem(item)
        self._rectangle_stamp_items.clear()
        for inner_c, outer_c in self._rectangle_stamp_ring_items.values():
            if inner_c is not None:
                self.image_plot.removeItem(inner_c)
            if outer_c is not None:
                self.image_plot.removeItem(outer_c)
        self._rectangle_stamp_ring_items.clear()
        if self._current_processed_image is None:
            return
        theta = np.linspace(0, 2 * np.pi, 200, dtype=np.float32)
        theta = np.append(theta, theta[0])
        for roi in self._state.rois:
            if str(roi.shape) not in {"rectangle", "circle", "ellipse"}:
                continue
            item = self._create_rectangle_stamp(roi)
            if item is None:
                continue
            setattr(item, "roi_id", roi.roi_id)
            self._rectangle_stamp_items.append(item)
            if str(roi.shape).lower() in {"circle", "ellipse"} and float(roi.background_width_px) > 0:
                inner_r = float(roi.size_x) / 2.0 + float(roi.background_padding_px)
                outer_r = inner_r + float(roi.background_width_px)
                inner_curve: pg.PlotCurveItem | None = None
                if inner_r > 0:
                    inner_curve = pg.PlotCurveItem()
                    inner_curve.setSkipFiniteCheck(True)
                    inner_curve.setData(
                        float(roi.center_x) + inner_r * np.cos(theta),
                        float(roi.center_y) + inner_r * np.sin(theta),
                    )
                    inner_curve.setPen(pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DashLine))
                    self.image_plot.addItem(inner_curve, ignoreBounds=True)
                    inner_curve.setZValue(1.14)
                outer_curve = pg.PlotCurveItem()
                outer_curve.setSkipFiniteCheck(True)
                outer_curve.setData(
                    float(roi.center_x) + outer_r * np.cos(theta),
                    float(roi.center_y) + outer_r * np.sin(theta),
                )
                outer_curve.setPen(pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DotLine))
                self.image_plot.addItem(outer_curve, ignoreBounds=True)
                outer_curve.setZValue(1.14)
                self._rectangle_stamp_ring_items[roi.roi_id] = (inner_curve, outer_curve)
        self._update_rectangle_stamp_visuals()

    def _update_rectangle_stamp_ring_position(self, roi: "RoiDefinition") -> None:
        ring_pair = self._rectangle_stamp_ring_items.get(roi.roi_id)
        if ring_pair is None:
            return
        inner_curve, outer_curve = ring_pair
        theta = np.linspace(0, 2 * np.pi, 200, dtype=np.float32)
        theta = np.append(theta, theta[0])
        inner_r = float(roi.size_x) / 2.0 + float(roi.background_padding_px)
        outer_r = inner_r + float(roi.background_width_px)
        cx, cy = float(roi.center_x), float(roi.center_y)
        if inner_curve is not None:
            inner_curve.setData(cx + inner_r * np.cos(theta), cy + inner_r * np.sin(theta))
        if outer_curve is not None:
            outer_curve.setData(cx + outer_r * np.cos(theta), cy + outer_r * np.sin(theta))

    def _update_rectangle_stamp_visuals(self) -> None:
        selected_pen = pg.mkPen("#fbbf24", width=2.8)
        normal_pen = pg.mkPen("#f59e0b", width=2)
        selected_ring_inner = pg.mkPen("#fbbf24", width=1.4, style=Qt.PenStyle.DashLine)
        selected_ring_outer = pg.mkPen("#fbbf24", width=1.4, style=Qt.PenStyle.DotLine)
        normal_ring_inner = pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DashLine)
        normal_ring_outer = pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DotLine)
        for item in self._rectangle_stamp_items:
            roi_id = str(getattr(item, "roi_id", ""))
            selected = roi_id in self._selected_rectangle_roi_ids
            item.setPen(selected_pen if selected else normal_pen)
            ring_pair = self._rectangle_stamp_ring_items.get(roi_id)
            if ring_pair is not None:
                inner_c, outer_c = ring_pair
                if inner_c is not None:
                    inner_c.setPen(selected_ring_inner if selected else normal_ring_inner)
                if outer_c is not None:
                    outer_c.setPen(selected_ring_outer if selected else normal_ring_outer)

    def _rectangle_stamp_at(self, point: tuple[float, float]) -> RoiDefinition | None:
        x = float(point[0])
        y = float(point[1])
        for roi in reversed(self._state.rois):
            if str(roi.shape) not in {"rectangle", "circle", "ellipse"}:
                continue
            left, top = roi_top_left_from_center(
                float(roi.center_x),
                float(roi.center_y),
                float(roi.size_x),
                float(roi.size_y),
                self._current_processed_image.shape if self._current_processed_image is not None else None,
            )
            right = left + float(roi.size_x)
            bottom = top + float(roi.size_y)
            if left <= x <= right and top <= y <= bottom:
                return roi
        return None

    def _select_rectangle_rois(self, roi_ids: set[str], *, additive: bool = False) -> None:
        if additive:
            self._selected_rectangle_roi_ids.update(roi_ids)
        else:
            self._selected_rectangle_roi_ids.clear()
            self._selected_rectangle_roi_ids.update(roi_ids)
        self._update_rectangle_stamp_visuals()

    def _clear_rectangle_roi_selection(self) -> None:
        if not self._selected_rectangle_roi_ids:
            return
        self._selected_rectangle_roi_ids.clear()
        self._update_rectangle_stamp_visuals()

    def _move_selected_rectangle_rois(self, dx: float, dy: float) -> None:
        if not self._selected_rectangle_roi_ids:
            return
        self._push_undo_point("Move ROIs")
        updated = False
        for roi in self._state.rois:
            if roi.roi_id not in self._selected_rectangle_roi_ids:
                continue
            moved = move_roi_from_template(
                roi,
                center_x=float(roi.center_x) + float(dx),
                center_y=float(roi.center_y) + float(dy),
                image_shape=self._current_processed_image.shape if self._current_processed_image is not None else None,
            )
            roi.center_x = moved.center_x
            roi.center_y = moved.center_y
            updated = True
        if updated:
            self._sync_rectangle_stamp_overlays()
            self._save_processing_state_for_dataset()
            self._schedule_processing_state_save()

    def _remove_selected_rectangle_rois(self) -> None:
        if not self._selected_rectangle_roi_ids:
            self.status_label.setText("Select ROI(s) first to remove them.")
            return
        self._push_undo_point("Remove ROIs")
        removed_count = len(self._selected_rectangle_roi_ids)
        self._state.rois = [
            roi for roi in self._state.rois if roi.roi_id not in self._selected_rectangle_roi_ids
        ]
        self._selected_rectangle_roi_ids.clear()
        self._sync_rectangle_stamp_overlays()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Removed {removed_count} selected rectangle ROI(s).")

    def _rectangle_stamp_changed(self, roi: RoiDefinition, *, finished: bool) -> None:
        item = next((stamp for stamp in self._rectangle_stamp_items if getattr(stamp, "roi_id", None) == roi.roi_id), None)
        if item is None:
            return
        pos = item.pos()
        size = item.size()
        updated = move_roi_from_template(
            roi,
            center_x=float(pos.x() + size.x() / 2.0),
            center_y=float(pos.y() + size.y() / 2.0),
            image_shape=self._current_processed_image.shape if self._current_processed_image is not None else None,
        )
        roi.center_x = updated.center_x
        roi.center_y = updated.center_y
        roi.size_x = max(float(size.x()), 2.0)
        roi.size_y = max(float(size.y()), 2.0)
        self._update_rectangle_stamp_ring_position(roi)
        if finished:
            self._push_undo_point("ROI")
            self._save_processing_state_for_dataset()
        if self._roi_editor_mode == "circles":
            self._update_roi_detection_labels(sync_controls=False)
        else:
            self._update_rectangle_roi_controls(sync_roi=False)

    def _add_rectangle_roi_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROIs.")
            return
        template = self._active_rectangle_template()
        if self._roi_editor_mode == "freehand":
            self.status_label.setText("Freehand ROI tools are not implemented yet.")
            return
        roi_id = self._next_roi_id()
        if self._roi_editor_mode == "circles":
            template_name = template.name.strip() or f"Circle ROI {len(self._state.rois) + 1}"
        else:
            template_name = template.name.strip() or f"Rectangle ROI {len(self._state.rois) + 1}"
        clone = create_rois_from_template(
            template,
            roi_id=roi_id,
            name=template_name,
            center_x=float(point[0]),
            center_y=float(point[1]),
            image_shape=self._current_processed_image.shape,
        )
        self._push_undo_point("Add ROI")
        self._state.rois.append(clone)
        self._select_rectangle_rois({clone.roi_id}, additive=False)
        self._sync_rectangle_stamp_overlays()
        self._update_rectangle_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI {clone.roi_id}.")

    def _add_stamp_array_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROI arrays.")
            return
        template = self._active_rectangle_template()
        if self._roi_editor_mode == "freehand":
            self.status_label.setText("Freehand ROI arrays are not implemented yet.")
            return
        rows = int(self.array_rows_spin.value())
        cols = int(self.array_cols_spin.value())
        spacing = max(float(self._length_display_to_px(float(self.array_spacing_spin.value()))), 0.0)
        if rows <= 0 or cols <= 0 or spacing <= 0.0:
            self.status_label.setText("Set array rows, columns, and spacing before stamping an ROI array.")
            return
        count = rows * cols
        start_index = self._roi_id_counter + 1
        self._roi_id_counter += count
        clones = create_rois_from_template_grid(
            template,
            rows=rows,
            cols=cols,
            spacing_x=spacing,
            spacing_y=spacing,
            anchor_center_x=float(point[0]),
            anchor_center_y=float(point[1]),
            start_index=start_index,
            image_shape=self._current_processed_image.shape,
            roi_id_factory=lambda index: f"roi_{index}",
        )
        if not clones:
            self._roi_id_counter -= count
            return
        self._push_undo_point("Add ROI array")
        self._state.rois.extend(clones)
        self._select_rectangle_rois({roi.roi_id for roi in clones}, additive=False)
        self._sync_rectangle_stamp_overlays()
        self._update_rectangle_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI array with {len(clones)} ROI(s).")
