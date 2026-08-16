from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox, QTableWidgetItem

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup, RoiArrayGroup
from lspr_imaging_app.domain.roi_editor_tools import build_roi_array_group
from lspr_imaging_app.gui.roi_table_helpers import (
    append_roi_table_row,
    format_xy_value,
    make_color_swatch_icon,
    roi_table_headers,
    RoiTableRowData,
)
from lspr_imaging_app.storage.workspace import load_roi_table, save_roi_table


class RoiTableController:
    def __init__(self, window) -> None:
        self.window = window

    def _clear_row_style(self, row: int) -> None:
        for column in range(self.window.roi_table.columnCount()):
            item = self.window.roi_table.item(row, column)
            if item is None:
                continue
            item.setForeground(QBrush())
            item.setBackground(QBrush())

    def _dim_uncached_row(self, row: int, *, sample_color: QColor, reference_color: QColor) -> None:
        dim_foreground = QBrush(QColor("#64748b"))
        for column in range(self.window.roi_table.columnCount()):
            item = self.window.roi_table.item(row, column)
            if item is None:
                continue
            item.setForeground(dim_foreground)
            item.setBackground(QBrush(QColor("#111827")) if column in {0, 1, 4, 5, 6, 7, 8} else item.background())
            if column == 2:
                item.setIcon(make_color_swatch_icon(sample_color.darker(170)))
            elif column == 3:
                item.setIcon(make_color_swatch_icon(reference_color.darker(170)))

    def refresh_cached_row_styles(self) -> None:
        if self.window.roi_table.rowCount() == 0:
            return
        rois = sorted(self.window._state.area_rois, key=lambda roi: roi.area_roi_id)
        if not rois:
            return
        group_by_roi: dict[int, AreaRoiGroup] = {}
        for group in self.window._state.area_roi_groups:
            for roi_id in group.area_roi_ids:
                group_by_roi[int(roi_id)] = group
        cached_roi_ids = {int(roi.area_roi_id) for roi in rois if self.window._roi_has_cached_absorbance(roi)}
        sample_color = QColor(self.window._sample_visual_color)
        reference_color = QColor(self.window._reference_visual_color)
        self.window._roi_table_updating = True
        self.window.roi_table.blockSignals(True)
        try:
            for row in range(self.window.roi_table.rowCount()):
                roi_id = self.roi_id_for_row(row)
                if roi_id is None:
                    continue
                roi = self.window._roi_by_id(roi_id)
                if roi is None:
                    continue
                group = group_by_roi.get(int(roi_id))
                sample_row_color = QColor(roi.sample_color_hex) if roi.sample_color_hex and QColor(roi.sample_color_hex).isValid() else (QColor(group.sample_color_hex) if group is not None and QColor(group.sample_color_hex).isValid() else sample_color)
                reference_row_color = QColor(roi.reference_color_hex) if roi.reference_color_hex and QColor(roi.reference_color_hex).isValid() else (QColor(group.reference_color_hex) if group is not None and QColor(group.reference_color_hex).isValid() else reference_color)
                self._clear_row_style(row)
                sample_item = self.window.roi_table.item(row, 2)
                if sample_item is not None:
                    sample_item.setIcon(make_color_swatch_icon(sample_row_color))
                reference_item = self.window.roi_table.item(row, 3)
                if reference_item is not None:
                    reference_item.setIcon(make_color_swatch_icon(reference_row_color))
                if self.window._cached_rois_only_visible and int(roi_id) not in cached_roi_ids and int(roi_id) not in self.window._selected_roi_ids:
                    self._dim_uncached_row(row, sample_color=sample_row_color, reference_color=reference_row_color)
        finally:
            self.window.roi_table.blockSignals(False)
            self.window._roi_table_updating = False

    def on_toggled(self, checked: bool) -> None:
        self.window.roi_list_panel.setVisible(checked)
        self.window._settings.setValue("layout/roi_list_visible", bool(checked))
        self.window._refresh_roi_list_action_icon()
        if checked:
            self.window._update_roi_table()

    def on_selection_changed(self) -> None:
        if getattr(self.window, "_roi_list_selection_syncing", False):
            return
        selected_ids: set[int] = set()
        for row in range(self.window.roi_table.rowCount()):
            if not self.window.roi_table.isRowHidden(row) and self.window.roi_table.selectionModel().isRowSelected(row, self.window.roi_table.rootIndex()):
                item = self.window.roi_table.item(row, 0)
                if item is not None:
                    try:
                        selected_ids.add(int(item.text()))
                    except ValueError:
                        continue
        if selected_ids == self.window._selected_roi_ids:
            return
        self.window._selected_roi_ids = selected_ids
        self.window._update_roi_overlays()
        self.window._update_roi_summary()

    def sync_selection(self) -> None:
        if not self.window.roi_table.isVisible():
            return
        selection_model = self.window.roi_table.selectionModel()
        if selection_model is None:
            return
        self.window._roi_list_selection_syncing = True
        try:
            selection_model.clearSelection()
            for row in range(self.window.roi_table.rowCount()):
                item = self.window.roi_table.item(row, 0)
                if item is None:
                    continue
                try:
                    roi_id = int(item.text())
                except ValueError:
                    continue
                if roi_id in self.window._selected_roi_ids:
                    selection_model.select(
                        self.window.roi_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self.window._roi_list_selection_syncing = False

    def roi_id_for_row(self, row: int) -> int | None:
        if row < 0 or row >= self.window.roi_table.rowCount():
            return None
        item = self.window.roi_table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def selected_rows(self) -> list[int]:
        selection_model = self.window.roi_table.selectionModel()
        if selection_model is None:
            return []
        return [row for row in range(self.window.roi_table.rowCount()) if selection_model.isRowSelected(row, self.window.roi_table.rootIndex())]

    def selected_roi_ids_list(self) -> list[int]:
        return [roi_id for row in self.selected_rows() if (roi_id := self.roi_id_for_row(row)) is not None]

    def refresh_headers(self) -> None:
        roi_table_headers(self.window.roi_table)

    def _dataset_folder(self) -> Path | None:
        return self.window._dataset_folder_path() if hasattr(self.window, "_dataset_folder_path") else None

    def _roi_table_signature_snapshot(self) -> str:
        rois = sorted(self.window._state.area_rois, key=lambda roi: roi.area_roi_id)
        parts: list[str] = []
        for roi in rois:
            group = self.window._group_for_roi(roi.area_roi_id)
            parts.append(
                "|".join(
                    [
                        str(int(roi.area_roi_id)),
                        group.name if group is not None else "",
                        group.sample_color_hex if group is not None else "",
                        group.reference_color_hex if group is not None else "",
                        roi.sample_color_hex or "",
                        roi.reference_color_hex or "",
                        f"{float(roi.center_x):.3f}",
                        f"{float(roi.center_y):.3f}",
                        roi.sample_geometry_type,
                        roi.reference_geometry_type,
                        "" if roi.sample_diameter_px is None else f"{float(roi.sample_diameter_px):.3f}",
                        "" if roi.reference_inner_diameter_px is None else f"{float(roi.reference_inner_diameter_px):.3f}",
                        "" if roi.reference_outer_diameter_px is None else f"{float(roi.reference_outer_diameter_px):.3f}",
                        "1" if roi.sample_mask is not None else "0",
                        "1" if roi.reference_mask is not None else "0",
                    ]
                )
            )
        return "\n".join(parts)

    def _save_roi_table_snapshot(self, *, force: bool = False) -> Path | None:
        """Keep `analysis/roi_table.json` in sync with the current ROI table.

        Always overwrites the same file (no timestamped history - use
        `export_roi_table()` for a named backup copy). Full-fidelity: every
        ROI kind the app supports round-trips here, unlike the old
        `roi_table_*.csv` snapshots this replaced, which could only carry
        circular sample/reference geometry.
        """
        path = self.window._roi_table_json_path()
        if path is None:
            return None
        signature = self._roi_table_signature_snapshot()
        if not force and self.window._last_saved_roi_table_signature == signature and self.window._last_saved_roi_table_path == path:
            return path
        save_roi_table(
            path,
            self.window._state.area_rois,
            self.window._state.area_roi_groups,
            self.window._state.area_roi_arrays,
            sample_visual_color=self.window._sample_visual_color.name(),
            reference_visual_color=self.window._reference_visual_color.name(),
        )
        self.window._last_saved_roi_table_signature = signature
        self.window._last_saved_roi_table_path = path
        return path

    def on_item_changed(self, item: QTableWidgetItem) -> None:
        if self.window._roi_table_updating:
            return
        roi_id = self.roi_id_for_row(item.row())
        if roi_id is None:
            return
        if item.column() == 1:
            self.rename_group(roi_id, item.text().strip())
        elif item.column() in {2, 3, 4}:
            self.edit_diameter_cells(roi_id, item.row())

    def on_cell_double_clicked(self, row: int, column: int) -> None:
        roi_id = self.roi_id_for_row(row)
        if roi_id is None:
            return
        if column == 2:
            self.edit_roi_color(roi_id)
        elif column == 3:
            self.edit_reference_color()
        elif column == 4:
            self.edit_roi_geometry(roi_id)
        elif column == 5:
            self.edit_reference_geometry(roi_id)

    def show_context_menu(self, pos) -> None:
        table = self.window.roi_table
        item = table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        roi_id = self.roi_id_for_row(row)
        if roi_id is None:
            return
        menu = QMenu(table)
        menu.setToolTipsVisible(True)
        group_action = menu.addAction("Group...")
        select_group_action = None
        ungroup_action = None
        destroy_group_action = None
        groups = self.window._groups_for_roi(roi_id)
        if groups:
            select_group_action = menu.addAction("Select group members")
            ungroup_action = menu.addAction("Ungroup")
            destroy_group_action = menu.addAction("Destroy group")
            menu.addSeparator()
        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is group_action:
            self.window._group_selected_rois()
        elif select_group_action is not None and action is select_group_action:
            if self.window._select_group_members_for_roi(roi_id):
                self.window.status_label.setText(f"Selected group members for ROI {roi_id}.")
        elif ungroup_action is not None and action is ungroup_action:
            self.window._ungroup_selected_rois()
        elif destroy_group_action is not None and action is destroy_group_action:
            self.window._destroy_groups_for_roi(roi_id)

    def update_table(self) -> None:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        self.window._roi_table_updating = True
        sorting_enabled = self.window.roi_table.isSortingEnabled()
        sort_column = self.window.roi_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.window.roi_table.horizontalHeader().sortIndicatorOrder()
        self.window.roi_table.setSortingEnabled(False)
        self.window.roi_table.blockSignals(True)
        total_rows = 0
        cached_rows = 0
        try:
            self.window.roi_table.setRowCount(0)
            rois = sorted(self.window._state.area_rois, key=lambda roi: roi.area_roi_id)
            group_by_roi: dict[int, AreaRoiGroup] = {}
            for group in self.window._state.area_roi_groups:
                for roi_id in group.area_roi_ids:
                    group_by_roi[int(roi_id)] = group
            cached_roi_ids = {int(roi.area_roi_id) for roi in rois if self.window._roi_has_cached_absorbance(roi)}
            self.refresh_headers()
            if not rois:
                return
            sample_color = QColor(self.window._sample_visual_color)
            reference_color = QColor(self.window._reference_visual_color)
            for roi in rois:
                try:
                    group = group_by_roi.get(int(roi.area_roi_id))
                    group_name = group.name if group is not None else "-"
                    sample_row_color = QColor(roi.sample_color_hex) if roi.sample_color_hex and QColor(roi.sample_color_hex).isValid() else (QColor(group.sample_color_hex) if group is not None and QColor(group.sample_color_hex).isValid() else sample_color)
                    reference_row_color = QColor(roi.reference_color_hex) if roi.reference_color_hex and QColor(roi.reference_color_hex).isValid() else (QColor(group.reference_color_hex) if group is not None and QColor(group.reference_color_hex).isValid() else reference_color)
                    sample_diameter_value = float(roi.sample_diameter_px if roi.sample_diameter_px is not None else 2.0 * self.window._state.area_roi_settings.sample_radius_px)
                    reference_inner_value = float(roi.reference_inner_diameter_px if roi.reference_inner_diameter_px is not None else 2.0 * self.window._state.area_roi_settings.reference_inner_radius_px)
                    reference_outer_value = float(roi.reference_outer_diameter_px if roi.reference_outer_diameter_px is not None else 2.0 * self.window._state.area_roi_settings.reference_outer_radius_px)
                    scale = self.window._microns_per_pixel_scalar() if self.window._state.preprocessing.display_units == "um" and self.window._can_display_micrometers() else None
                    sample_diameter_text = (
                        f"{self.window._format_length_display_value(sample_diameter_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(sample_diameter_value):.1f}"
                    )
                    reference_inner_text = (
                        f"{self.window._format_length_display_value(reference_inner_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(reference_inner_value):.1f}"
                    )
                    reference_outer_text = (
                        f"{self.window._format_length_display_value(reference_outer_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(reference_outer_value):.1f}"
                    )
                    append_roi_table_row(
                        self.window.roi_table,
                        RoiTableRowData(
                            area_roi_id=roi.area_roi_id,
                            group_name=group_name,
                            sample_color=sample_row_color,
                            reference_color=reference_row_color,
                            sample_diameter_text=sample_diameter_text,
                            reference_inner_text=reference_inner_text,
                            reference_outer_text=reference_outer_text,
                            x_text=format_xy_value(roi.center_x, self.window._state.preprocessing.display_units, scale),
                            y_text=format_xy_value(roi.center_y, self.window._state.preprocessing.display_units, scale),
                        ),
                    )
                    row = self.window.roi_table.rowCount() - 1
                    self.window.roi_table.item(row, 1).setToolTip("Double-click to create a group for the selected ROIs." if group is None else f"{group.name} (sample {group.sample_color_hex}, reference {group.reference_color_hex})")
                    self.window.roi_table.item(row, 2).setToolTip("Double-click to change the ROI or group color.")
                    self.window.roi_table.item(row, 3).setToolTip("Double-click to change the reference ROI color.")
                    total_rows += 1
                    roi_cached = int(roi.area_roi_id) in cached_roi_ids
                    if roi_cached:
                        cached_rows += 1
                    if self.window._cached_rois_only_visible and not roi_cached:
                        if roi.area_roi_id not in self.window._selected_roi_ids:
                            self._dim_uncached_row(row, sample_color=sample_row_color, reference_color=reference_row_color)
                except Exception:
                    logger.exception("ROI table row build failed | area_roi_id=%s", int(roi.area_roi_id))
            self.window.roi_table.resizeColumnsToContents()
            self.window.roi_table.setColumnWidth(0, 34)
            self.window.roi_table.setColumnWidth(1, 96)
            self.window.roi_table.setColumnWidth(2, 22)
            self.window.roi_table.setColumnWidth(3, 22)
            self.window.roi_table.setColumnWidth(4, 58)
            self.window.roi_table.setColumnWidth(5, 58)
            self.window.roi_table.setColumnWidth(6, 58)
            self.window.roi_table.setColumnWidth(7, 64)
            self.window.roi_table.setColumnWidth(8, 64)
            self.window.roi_table.horizontalHeader().setStretchLastSection(False)
        finally:
            self.window.roi_table.blockSignals(False)
            self.window.roi_table.setSortingEnabled(sorting_enabled)
            if sorting_enabled and sort_column >= 0:
                self.window.roi_table.sortItems(sort_column, sort_order)
            self.window._roi_table_updating = False
        self.window._append_workflow_log_throttled(
            "roi_table_update",
            f"ROI table updated | rows={int(total_rows)} cached={int(cached_rows)} cached_only={bool(self.window._cached_rois_only_visible)}",
            level="debug",
            min_interval=2.0,
        )
        self.sync_selection()
        self._save_roi_table_snapshot()

    def rename_group(self, roi_id: int, new_name: str) -> None:
        self.window._rename_roi_group_from_table(roi_id, new_name)

    def edit_roi_color(self, roi_id: int) -> None:
        self.window._edit_roi_color_from_table(roi_id)

    def edit_reference_color(self) -> None:
        self.window._edit_reference_color_from_table()

    def edit_roi_geometry(self, roi_id: int) -> None:
        self.window._edit_roi_geometry_from_table(roi_id)

    def edit_reference_geometry(self, roi_id: int) -> None:
        self.window._edit_reference_geometry_from_table(roi_id)

    def edit_diameter_cells(self, roi_id: int, row: int) -> None:
        self.window._edit_roi_diameter_cells_from_table(roi_id, row)

    def rename_group_from_row(self, roi_id: int, new_name: str) -> None:
        self.window._rename_roi_group_from_table(roi_id, new_name)

    def copy_properties(self) -> None:
        self.window._copy_roi_properties_from_table()

    def paste_properties(self) -> None:
        self.window._paste_roi_properties_from_table()

    def move_selected(self, direction: int) -> None:
        self.window._move_selected_rois_in_table(direction)

    def edit_roi_color_from_row(self, roi_id: int) -> None:
        self.window._edit_roi_color_from_table(roi_id)

    def edit_reference_color_from_selection(self) -> None:
        self.window._edit_reference_color_from_table()

    def edit_roi_geometry_from_row(self, roi_id: int) -> None:
        self.window._edit_roi_geometry_from_table(roi_id)

    def edit_reference_geometry_from_row(self, roi_id: int) -> None:
        self.window._edit_reference_geometry_from_table(roi_id)

    def edit_diameter_cells_from_row(self, roi_id: int, row: int) -> None:
        self.window._edit_roi_diameter_cells_from_table(roi_id, row)

    def export_roi_table(self) -> None:
        """Save a separate, explicitly named backup copy (Save As).

        `analysis/roi_table.json` is kept current automatically as you edit
        ROIs, so this button is for a distinct snapshot you want to keep on
        hand - e.g. before a batch re-detect - not for flushing the live file.
        """
        folder = self._dataset_folder()
        if folder is None:
            self.window.status_label.setText("No dataset available for ROI table export.")
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_path = folder / "analysis" / f"roi_table_backup_{stamp}.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save ROI table backup",
            str(default_path),
            "JSON Files (*.json)",
        )
        if not path_str:
            return
        path = Path(path_str)
        save_roi_table(
            path,
            self.window._state.area_rois,
            self.window._state.area_roi_groups,
            self.window._state.area_roi_arrays,
            sample_visual_color=self.window._sample_visual_color.name(),
            reference_visual_color=self.window._reference_visual_color.name(),
        )
        self.window.status_label.setText(f"Saved ROI table backup to {path.name}.")

    def import_roi_table(self) -> None:
        """Restore the ROI table from a `roi_table.json` file (full replace).

        Full-fidelity JSON means there's no lossy subset left to merge
        in-place the way the old CSV import did (it could only tweak colors
        and diameters on ROIs that already existed) - this fully replaces
        the current ROI table, including freeform mask geometry and array
        recipes, protected by the usual undo point.
        """
        folder = self._dataset_folder()
        if folder is None:
            self.window.status_label.setText("Load a dataset before importing an ROI table.")
            return
        default_path = self.window._roi_table_json_path()
        path: Path | None = None
        if default_path is not None and default_path.exists():
            answer = QMessageBox.question(
                self.window,
                "Load ROI table",
                f"Load the saved ROI table?\n\n{default_path.name}\n\nChoose No to browse for another file.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                path = default_path
            elif answer == QMessageBox.StandardButton.No:
                path_str, _ = QFileDialog.getOpenFileName(
                    self.window,
                    "Load ROI table",
                    str(default_path.parent),
                    "JSON Files (*.json)",
                )
                if not path_str:
                    return
                path = Path(path_str)
            else:
                return
        else:
            path_str, _ = QFileDialog.getOpenFileName(
                self.window,
                "Load ROI table",
                str(folder / "analysis"),
                "JSON Files (*.json)",
            )
            if not path_str:
                return
            path = Path(path_str)
        try:
            area_rois, area_roi_groups, area_roi_arrays, sample_color, reference_color = load_roi_table(path)
        except Exception as exc:
            self.window.status_label.setText(f"ROI table import failed: {exc}")
            return
        if not area_rois:
            self.window.status_label.setText("ROI table file has no ROIs.")
            return
        self.window._push_undo_point("Import ROI table")
        self.window._state.area_rois = area_rois
        self.window._state.area_roi_groups = area_roi_groups
        self.window._state.area_roi_arrays = area_roi_arrays
        self.window._reset_roi_id_counter_from_state()
        if sample_color:
            self.window._sample_visual_color = QColor(sample_color)
        if reference_color:
            self.window._reference_visual_color = QColor(reference_color)
        self.window._selected_roi_ids.clear()
        self.window._update_color_button_styles()
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset(force=True, reason="import ROI table")
        self.window._update_roi_table()
        self.window.status_label.setText(f"Loaded ROI table from {path.name}.")

    def _on_roi_list_toggled(self, checked: bool) -> None:
        self.window.roi_list_panel.setVisible(checked)
        self.window._settings.setValue("layout/roi_list_visible", bool(checked))
        self.window._refresh_roi_list_action_icon()
        if checked:
            self.window._update_roi_table()


    def _on_cached_rois_only_toggled(self, checked: bool) -> None:
        self.window._cached_rois_only_visible = bool(checked)
        self.window._settings.setValue("layout/cached_rois_only_visible", bool(checked))
        self.window.roi_list_cached_button.setIcon(self.window._make_cached_rois_icon(bool(checked)))
        self.window._roi_table_controller.refresh_cached_row_styles()
        self.window._update_roi_overlays()
        self.window._refresh_visible_spectrum_from_cache()
        self.window._analysis_controller.preview_sensorgram_from_cache()


    def _on_roi_panel_visibility_changed(self, visible: bool) -> None:
        if self.window.roi_list_action.isChecked() == bool(visible):
            return
        self.window.roi_list_action.blockSignals(True)
        self.window.roi_list_action.setChecked(bool(visible))
        self.window.roi_list_action.blockSignals(False)
        self.window._settings.setValue("layout/roi_list_visible", bool(visible))
        self.window._refresh_roi_list_action_icon()
        if visible:
            self.window._update_roi_table()




    def _on_roi_list_item_changed(self, item: QTableWidgetItem) -> None:
        if self.window._roi_table_updating:
            return
        roi_id = self.window._roi_list_roi_id_for_row(item.row())
        if roi_id is None:
            return
        if item.column() == 1:
            self.window._rename_roi_group_from_table(roi_id, item.text().strip())
        elif item.column() in {2, 3, 4}:
            self.window._edit_roi_diameter_cells_from_table(roi_id, item.row())


    def _on_roi_list_cell_double_clicked(self, row: int, column: int) -> None:
        roi_id = self.window._roi_list_roi_id_for_row(row)
        if roi_id is None:
            return
        if column == 2:
            self.window._edit_roi_color_from_table(roi_id)
        elif column == 3:
            self.window._edit_reference_color_from_table()
        elif column == 4:
            self.window._edit_roi_geometry_from_table(roi_id)
        elif column == 5:
            self.window._edit_reference_geometry_from_table(roi_id)


    def _on_roi_metrics_ready(
        self,
        request_id: int,
        image_key: tuple[int, float],
        refreshed_rois: list[AreaRoi],
        save_after: bool,
        status_text: str | None,
        refresh_histogram: bool,
    ) -> None:
        self.window._end_busy()
        if request_id != self.window._roi_metrics_request_id:
            return
        if self.window._current_image_key != image_key:
            return
        self.window._state.area_rois = refreshed_rois
        if refresh_histogram:
            self.window._invalidate_image_analysis_caches()
            self.window._schedule_histogram_refresh()
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._append_workflow_log(
            f"ROI metrics refresh done | rois {len(refreshed_rois)}",
            level="success",
        )
        if save_after:
            self.window._schedule_processing_state_save()
        if status_text:
            self.window._set_status_text(status_text)


    def _on_roi_metrics_failed(self, message: str) -> None:
        self.window._end_busy()
        self.window._append_workflow_log(f"ROI metrics refresh failed | {message}", level="error")
        self.window._background_error("ROI metric refresh", message)


    def _build_array_group_from_detection(self, detected_rois: list[AreaRoi]) -> list[RoiArrayGroup]:
        """If array detection settings were configured and detection actually
        produced a full grid, record the recipe as a persisted `RoiArrayGroup`
        so rows/cols/spacing survive save/reload and can be edited later as a
        unit, instead of being a one-shot detection parameter.

        `detect_rois`/`_fit_grid_array` (processing/roi_detection.py) always
        fill every grid cell (with an inferred ROI where no signal was found)
        when grid fitting runs, so `len(detected_rois) == rows * cols` is a
        reliable signal that fitting happened, without this GUI-layer code
        needing to know detection internals.
        """
        settings = self.window._state.area_roi_settings
        rows = int(settings.array_rows)
        cols = int(settings.array_cols)
        spacing = float(settings.array_spacing_px)
        if rows <= 0 or cols <= 0 or spacing <= 0.0 or len(detected_rois) != rows * cols:
            return []
        first = detected_rois[0]
        array_group = build_roi_array_group(
            array_id="array_1",
            label="Detected array",
            rows=rows,
            cols=cols,
            spacing_x=spacing,
            origin_x=float(first.center_x),
            origin_y=float(first.center_y),
            member_area_roi_ids=[int(roi.area_roi_id) for roi in detected_rois],
        )
        for roi in detected_rois:
            roi.array_id = array_group.array_id
        return [array_group]

    def _on_detect_rois_ready(
        self,
        request_id: int,
        image_key: tuple[int, float] | None,
        detected_rois: list[AreaRoi],
    ) -> None:
        self.window._end_busy()
        if request_id != self.window._roi_detection_request_id:
            return
        if self.window._current_image_key != image_key:
            return
        self.window._state.area_rois = detected_rois
        self.window._state.area_roi_groups.clear()
        self.window._state.area_roi_arrays = self._build_array_group_from_detection(detected_rois)
        self.window._selected_roi_ids.clear()
        self.window._invalidate_image_analysis_caches()
        self.window._invalidate_background_profile_cache()
        self.window._update_roi_overlays()
        self.window._schedule_histogram_refresh()
        self.window._update_roi_summary()
        self.window._update_selection_dependent_plots(force=True)
        if self.window._showing_background_profile_main:
            self.window._update_background_profile_preview()
        self.window._schedule_processing_state_save()
        roi_count = len(self.window._state.area_rois)
        if roi_count == 0:
            self.window._set_status_text(
                "Detected 0 rois. Check that the histogram highlight range (blue band) covers your sample ROI intensities."
            )
        else:
            self.window._set_status_text(f"Detected {roi_count} rois on the reference image.")
        self.window._append_workflow_log(
            f"ROI detection done | rois {len(self.window._state.area_rois)}",
            level="success",
        )


    def _on_detect_rois_failed(self, message: str) -> None:
        self.window._end_busy()
        self.window._background_error("ROI detection", message)


    def _on_roi_diameter_spin_changed(self, value: int) -> None:
        diameter_px = max(self.window._length_display_to_px(float(value)), 2.0)
        self.window._append_workflow_log_throttled(
            "sample_diameter_change",
            f"Sample diameter changed | display={value} | px={diameter_px:.2f}",
            level="debug",
            min_interval=1.0,
        )
        self.window._state.area_roi_settings.sample_radius_px = max(float(diameter_px / 2.0), 1.0)
        self.window._update_geometry_settings(save=False, recalculate=False)


    def _on_reference_inner_diameter_spin_changed(self, value: int) -> None:
        self.window._append_workflow_log_throttled(
            "reference_inner_change",
            f"Reference inner changed | display={value}",
            level="debug",
            min_interval=1.0,
        )
        self.window._update_geometry_settings(
            save=False,
            recalculate=False,
            normalize_relation=True,
        )


    def _on_reference_outer_diameter_spin_changed(self, value: int) -> None:
        self.window._append_workflow_log_throttled(
            "reference_outer_change",
            f"Reference outer changed | display={value}",
            level="debug",
            min_interval=1.0,
        )
        self.window._update_geometry_settings(
            save=False,
            recalculate=False,
            normalize_relation=True,
        )
