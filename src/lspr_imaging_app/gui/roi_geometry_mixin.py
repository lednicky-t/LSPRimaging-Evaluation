from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QInputDialog, QMenu

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup


class RoiGeometryMixin:
    """AreaRoi (sample/reference ROI pair) geometry and collection
    management: adding, moving, clamping to the image, reordering into a
    row/column array, and grouping/ungrouping. Mixed into MainWindow (same
    pattern as MainWindowIcons): `self` here is the MainWindow instance, so
    these methods use the same window state/widgets as the rest of the
    class.
    """

    def _move_selected_rois(self, dx: float, dy: float) -> None:
        if not self._selected_roi_ids:
            return
        self._append_workflow_log(f"ROIs | move {len(self._selected_roi_ids)} by dx={dx:g}, dy={dy:g}", level="debug")
        self._prepare_undo_snapshot("Move ROIs")
        for roi in self._state.area_rois:
            if roi.area_roi_id not in self._selected_roi_ids:
                continue
            roi.center_x, roi.center_y = self._clamp_roi_position(roi, roi.center_x + dx, roi.center_y + dy)
        # Only the moved ROI(s) need their overlay bundle rebuilt - with many
        # ROIs on screen, redrawing every curve/label and restyling the whole
        # ROI table (the unscoped default) on every single arrow-key repeat
        # is what made nudging feel sluggish independent of the save-timing
        # fix above.
        self._update_roi_overlays(roi_ids=set(self._selected_roi_ids))
        # _mark_roi_edit_refresh_pending() already saves+schedules when the
        # ROI tool is active, which is the only context this method runs in
        # (see shortcut_manager.py's `_active_tool == "roi"` guard) - calling
        # save again here duplicated a full processing-state payload build
        # (including a copy of the file mask) on every single arrow-key
        # nudge, which made keyboard ROI moves feel sluggish.
        # commit_undo=False: committing on every repeat (the default, right
        # for the once-per-gesture callers) would clear the prepared undo
        # snapshot every keystroke, forcing _prepare_undo_snapshot() above to
        # deepcopy() the whole app state again on the very next repeat
        # instead of reusing the pending one - by far the most expensive
        # single thing in this whole path. Deferring the commit lets a
        # burst of nudges share one snapshot, the same way a mouse drag
        # already does.
        self._mark_roi_edit_refresh_pending(commit_undo=False)

    def _add_roi_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROIs.")
            return
        self._push_undo_point("Add ROI")
        radius = float(max(self._state.area_roi_settings.sample_radius_px, 1))
        provisional = AreaRoi(
            area_roi_id=len(self._state.area_rois) + 1,
            center_x=point[0],
            center_y=point[1],
            sample_radius_px=radius,
            score=0.0,
        )
        provisional.center_x, provisional.center_y = self._clamp_roi_position(provisional, provisional.center_x, provisional.center_y)
        self._state.area_rois.append(provisional)
        self._selected_roi_ids = {provisional.area_roi_id}
        self._update_roi_overlays()
        self._update_roi_table()
        self._update_roi_summary()
        # _mark_roi_edit_refresh_pending() already saves when the ROI tool
        # is active, which is the only context this method runs in.
        self._mark_roi_edit_refresh_pending()
        self.status_label.setText(f"Added ROI {provisional.area_roi_id}.")

    def _add_roi_array_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROI array.")
            return
        rows = int(self.array_rows_spin.value())
        cols = int(self.array_cols_spin.value())
        spacing = max(float(self._length_display_to_px(float(self.array_spacing_spin.value()))), 0.0)
        if rows <= 0 or cols <= 0 or spacing <= 0.0:
            self.status_label.setText("Set array rows, columns, and spacing before stamping an ROI array.")
            return
        radius = float(max(self._state.area_roi_settings.sample_radius_px, 1))
        image_height, image_width = self._current_processed_image.shape[:2]
        step = spacing
        start_x = float(point[0]) - (cols - 1) * step / 2.0
        start_y = float(point[1]) - (rows - 1) * step / 2.0
        self._push_undo_point("Add ROI array")
        next_id = len(self._state.area_rois) + 1
        new_rois: list[AreaRoi] = []
        for row in range(rows):
            for col in range(cols):
                cx = start_x + col * step
                cy = start_y + row * step
                cx = float(min(max(cx, radius), max(float(image_width - 1) - radius, radius)))
                cy = float(min(max(cy, radius), max(float(image_height - 1) - radius, radius)))
                roi = AreaRoi(area_roi_id=next_id, center_x=cx, center_y=cy, sample_radius_px=radius, score=0.0)
                new_rois.append(roi)
                next_id += 1
        self._state.area_rois.extend(new_rois)
        self._selected_roi_ids = {s.area_roi_id for s in new_rois}
        self._update_roi_overlays()
        self._update_roi_table()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI array: {len(new_rois)} ROIs.")

    def _clamp_roi_position(self, roi: AreaRoi, x: float, y: float) -> tuple[float, float]:
        if self._current_processed_image is None:
            return x, y
        image_height, image_width = self._current_processed_image.shape[:2]
        radius = max(float(roi.sample_radius_px), 1.0)
        clamped_x = min(max(x, radius), max(float(image_width - 1) - radius, radius))
        clamped_y = min(max(y, radius), max(float(image_height - 1) - radius, radius))
        return clamped_x, clamped_y

    def _reorder_rois_by_position(self, *, column_major: bool = False) -> None:
        if not self._state.area_rois:
            self.status_label.setText("No ROIs available to reorder.")
            return
        self._push_undo_point("Reorder ROIs by position")
        rows = max(int(self._state.area_roi_settings.array_rows), 0)
        cols = max(int(self._state.area_roi_settings.array_cols), 0)
        rois = list(self._state.area_rois)
        order_func = self._order_rois_as_array_column_major if column_major else self._order_rois_as_array
        if rows > 0 and cols > 0 and rows * cols == len(rois):
            ordered = order_func(rois, rows=rows, cols=cols)
        else:
            ordered = order_func(rois, rows=rows if rows > 0 else None, cols=cols if cols > 0 else None)
        id_map = {roi.area_roi_id: new_id for new_id, roi in enumerate(ordered, start=1)}
        for new_id, roi in enumerate(ordered, start=1):
            roi.area_roi_id = new_id
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [id_map.get(roi_id, roi_id) for roi_id in group.area_roi_ids]
            group.area_roi_ids = sorted(dict.fromkeys(group.area_roi_ids))
        for array in self._state.area_roi_arrays:
            # Order matters here (row-major/column-major grid recipe) - unlike
            # group.area_roi_ids above, this must not be sorted/deduped.
            array.member_area_roi_ids = [id_map.get(roi_id, roi_id) for roi_id in array.member_area_roi_ids]
        self._state.area_rois = ordered
        self._selected_roi_ids = {id_map.get(roi_id, roi_id) for roi_id in self._selected_roi_ids if roi_id in id_map}
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_selection_dependent_plots(force=True)
        self._save_processing_state_for_dataset()
        self._update_roi_table()
        self.status_label.setText(
            "Reordered ROIs by image position, column by column." if column_major
            else "Reordered ROIs by image position, row by row."
        )

    def _roi_reorder_row_band(self) -> float:
        spacing = max(float(self._state.area_roi_settings.array_spacing_px), 0.0)
        diameters = [
            float(roi.sample_diameter_px)
            for roi in self._state.area_rois
            if roi.sample_diameter_px is not None and float(roi.sample_diameter_px) > 0.0
        ]
        if diameters:
            diameter_scale = float(np.median(np.asarray(diameters, dtype=np.float64)))
        else:
            diameter_scale = float(max(self._state.area_roi_settings.sample_radius_px * 2.0, 1.0))
        band_from_spacing = spacing * 0.45 if spacing > 0.0 else 0.0
        band_from_diameter = diameter_scale * 0.75
        return float(max(band_from_spacing, band_from_diameter, 5.0))

    def _order_rois_as_array(
        self,
        rois: list[AreaRoi],
        *,
        rows: int | None,
        cols: int | None,
    ) -> list[AreaRoi]:
        if not rois:
            return []
        sorted_rois = sorted(rois, key=lambda roi: (float(roi.center_y), float(roi.center_x), int(roi.area_roi_id)))
        row_band = self._roi_reorder_row_band()
        row_groups: list[list[AreaRoi]] = []
        row_centers: list[float] = []

        for roi in sorted_rois:
            y = float(roi.center_y)
            best_index = -1
            best_distance = float("inf")
            for index, center_y in enumerate(row_centers):
                distance = abs(y - center_y)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index >= 0 and best_distance <= row_band:
                row_groups[best_index].append(roi)
                row_centers[best_index] = float(np.mean([float(item.center_y) for item in row_groups[best_index]]))
            else:
                row_groups.append([roi])
                row_centers.append(y)

        if rows is not None and rows > 0 and len(row_groups) != rows:
            row_groups = [list(group) for group in np.array_split(np.asarray(sorted_rois, dtype=object), rows)]

        row_groups = [sorted(group, key=lambda roi: (float(roi.center_x), int(roi.area_roi_id))) for group in row_groups]
        row_groups.sort(key=lambda group: float(np.mean([float(roi.center_y) for roi in group])) if group else 0.0)

        ordered: list[AreaRoi] = []
        for row_group in row_groups:
            if cols is not None and cols > 0:
                ordered.extend(row_group[:cols])
            else:
                ordered.extend(row_group)
        return ordered

    def _order_rois_as_array_column_major(
        self,
        rois: list[AreaRoi],
        *,
        rows: int | None,
        cols: int | None,
    ) -> list[AreaRoi]:
        """Mirrors _order_rois_as_array with x/y (and rows/cols) swapped
        throughout: groups ROIs into columns first, then numbers top-to-
        bottom within each column, left column to right column - instead of
        row-major's rows-first, left-to-right-within-row numbering."""
        if not rois:
            return []
        sorted_rois = sorted(rois, key=lambda roi: (float(roi.center_x), float(roi.center_y), int(roi.area_roi_id)))
        col_band = self._roi_reorder_row_band()
        col_groups: list[list[AreaRoi]] = []
        col_centers: list[float] = []

        for roi in sorted_rois:
            x = float(roi.center_x)
            best_index = -1
            best_distance = float("inf")
            for index, center_x in enumerate(col_centers):
                distance = abs(x - center_x)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index >= 0 and best_distance <= col_band:
                col_groups[best_index].append(roi)
                col_centers[best_index] = float(np.mean([float(item.center_x) for item in col_groups[best_index]]))
            else:
                col_groups.append([roi])
                col_centers.append(x)

        if cols is not None and cols > 0 and len(col_groups) != cols:
            col_groups = [list(group) for group in np.array_split(np.asarray(sorted_rois, dtype=object), cols)]

        col_groups = [sorted(group, key=lambda roi: (float(roi.center_y), int(roi.area_roi_id))) for group in col_groups]
        col_groups.sort(key=lambda group: float(np.mean([float(roi.center_x) for roi in group])) if group else 0.0)

        ordered: list[AreaRoi] = []
        for col_group in col_groups:
            if rows is not None and rows > 0:
                ordered.extend(col_group[:rows])
            else:
                ordered.extend(col_group)
        return ordered

    def _group_for_roi(self, roi_id: int) -> AreaRoiGroup | None:
        for group in self._state.area_roi_groups:
            if roi_id in group.area_roi_ids:
                return group
        return None

    def _groups_for_roi(self, roi_id: int) -> list[AreaRoiGroup]:
        return [group for group in self._state.area_roi_groups if roi_id in group.area_roi_ids]

    def _select_group_members_for_roi(self, roi_id: int) -> bool:
        groups = self._groups_for_roi(roi_id)
        if not groups:
            return False
        selected_ids = {int(member_id) for group in groups for member_id in group.area_roi_ids}
        if not selected_ids:
            return False
        if selected_ids == self._selected_roi_ids:
            return True
        self._selected_roi_ids = selected_ids
        self._update_roi_overlays()
        self._update_roi_summary()
        self._sync_roi_table_selection()
        self._update_selection_dependent_plots(prompt_live_preview=True)
        return True

    def _ungroup_selected_rois(self) -> None:
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to ungroup them.")
            return
        if not any(group.area_roi_ids for group in self._state.area_roi_groups):
            self.status_label.setText("No grouped ROIs are selected.")
            return
        self._append_workflow_log(f"Groups | ungroup {len(self._selected_roi_ids)} ROI(s)", level="warning")
        self._push_undo_point("Ungroup ROIs")
        selected_ids = set(self._selected_roi_ids)
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in selected_ids]
        self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()
        self._save_processing_state_for_dataset()
        self.status_label.setText("Removed selected ROIs from their groups.")

    def _destroy_groups_for_roi(self, roi_id: int) -> None:
        groups = self._groups_for_roi(roi_id)
        if not groups:
            self.status_label.setText("No group is assigned to the selected ROI.")
            return
        self._push_undo_point("Destroy group")
        self._append_workflow_log(f"Groups | destroy for ROI {roi_id}", level="warning")
        group_names = [group.name for group in groups if group.name]
        remaining_groups = [group for group in self._state.area_roi_groups if group not in groups]
        self._state.area_roi_groups = remaining_groups
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()
        self._save_processing_state_for_dataset()
        group_text = ", ".join(group_names) if group_names else "group"
        self.status_label.setText(f"Destroyed {group_text}; member ROIs are now free.")

    def _show_analysis_roi_context_menu(self, roi_id: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        group_action = menu.addAction("Group...")
        select_group_action = None
        ungroup_action = None
        destroy_group_action = None
        groups = self._groups_for_roi(roi_id)
        if groups:
            select_group_action = menu.addAction("Select group members")
            ungroup_action = menu.addAction("Ungroup")
            destroy_group_action = menu.addAction("Destroy group")
            menu.addSeparator()
        action = menu.exec(global_pos)
        if action is None:
            return
        if action is group_action:
            self._group_selected_rois()
        elif select_group_action is not None and action is select_group_action:
            if self._select_group_members_for_roi(roi_id):
                self.status_label.setText(f"Selected group members for ROI {roi_id}.")
        elif ungroup_action is not None and action is ungroup_action:
            self._ungroup_selected_rois()
        elif destroy_group_action is not None and action is destroy_group_action:
            self._destroy_groups_for_roi(roi_id)

    def _reindex_detected_rois(self) -> None:
        roi_id_map: dict[int, int] = {}
        for new_id, roi in enumerate(self._state.area_rois, start=1):
            roi_id_map[roi.area_roi_id] = new_id
            roi.area_roi_id = new_id

        updated_groups: list[AreaRoiGroup] = []
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id_map[roi_id] for roi_id in group.area_roi_ids if roi_id in roi_id_map]
            if group.area_roi_ids:
                updated_groups.append(group)
        self._state.area_roi_groups = updated_groups

        updated_arrays = []
        for array in self._state.area_roi_arrays:
            array.member_area_roi_ids = [
                roi_id_map[roi_id] for roi_id in array.member_area_roi_ids if roi_id in roi_id_map
            ]
            if array.member_area_roi_ids:
                updated_arrays.append(array)
        self._state.area_roi_arrays = updated_arrays

    def _remove_selected_rois(self) -> None:
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to remove them.")
            return
        self._append_workflow_log(f"ROIs | remove {len(self._selected_roi_ids)} selected", level="warning")
        self._push_undo_point("Remove ROIs")
        removed_count = len(self._selected_roi_ids)
        self._state.area_rois = [
            roi for roi in self._state.area_rois if roi.area_roi_id not in self._selected_roi_ids
        ]
        self._reindex_detected_rois()
        self._selected_roi_ids.clear()
        self._update_roi_overlays()
        if self._active_tool == "roi":
            self._mark_roi_edit_refresh_pending()
        else:
            self._schedule_histogram_refresh()
        self._update_roi_summary()
        if self._active_tool != "roi":
            self._save_processing_state_for_dataset()
        self.status_label.setText(f"Removed {removed_count} selected ROI(s).")

    def _group_selected_rois(self) -> None:
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to create a group.")
            return

        current_group = self._group_for_roi(min(self._selected_roi_ids))
        default_name = current_group.name if current_group is not None else f"Group {len(self._state.area_roi_groups) + 1}"
        name, accepted = QInputDialog.getText(self, "ROI group", "Group name", text=default_name)
        if not accepted:
            return
        name = name.strip()
        if not name:
            self.status_label.setText("Group creation cancelled: name is required.")
            return

        initial_color = QColor(current_group.sample_color_hex) if current_group is not None else QColor("#f59e0b")
        color = QColorDialog.getColor(initial_color, self, "ROI group color")
        if not color.isValid():
            return
        self._push_undo_point("Group ROIs")
        self._append_workflow_log(
            f"Groups | create '{name}' with {len(self._selected_roi_ids)} ROI(s)",
            level="success",
        )

        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in self._selected_roi_ids]
        self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]

        target_group = next((group for group in self._state.area_roi_groups if group.name == name), None)
        if target_group is None:
            target_group = AreaRoiGroup(
                group_id=f"group_{len(self._state.area_roi_groups) + 1}",
                name=name,
                sample_color_hex=color.name(),
                reference_color_hex=self._reference_visual_color.name(),
                area_roi_ids=sorted(self._selected_roi_ids),
            )
            self._state.area_roi_groups.append(target_group)
        else:
            target_group.sample_color_hex = color.name()
            target_group.area_roi_ids = sorted(set(target_group.area_roi_ids).union(self._selected_roi_ids))

        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self.status_label.setText(f"Grouped {len(self._selected_roi_ids)} ROI(s) as '{name}'.")
