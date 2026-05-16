from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox, QTableWidgetItem

from lspr_imaging_app.domain.models import SpotGroup
from lspr_imaging_app.gui.spot_table_helpers import append_spot_table_row, format_xy_value, spot_table_headers, SpotTableRowData


class SpotTableController:
    def __init__(self, window) -> None:
        self.window = window

    def _clear_row_style(self, row: int) -> None:
        for column in range(self.window.spot_list_table.columnCount()):
            item = self.window.spot_list_table.item(row, column)
            if item is None:
                continue
            item.setForeground(QBrush())
            item.setBackground(QBrush())

    def _dim_uncached_row(self, row: int, *, spot_color: QColor, ring_color: QColor) -> None:
        dim_foreground = QBrush(QColor("#64748b"))
        for column in range(self.window.spot_list_table.columnCount()):
            item = self.window.spot_list_table.item(row, column)
            if item is None:
                continue
            item.setForeground(dim_foreground)
            item.setBackground(QBrush(QColor("#111827")) if column in {0, 1, 4, 5, 6, 7, 8} else item.background())
            if column == 2:
                item.setIcon(make_color_swatch_icon(spot_color.darker(170)))
            elif column == 3:
                item.setIcon(make_color_swatch_icon(ring_color.darker(170)))

    def refresh_cached_row_styles(self) -> None:
        if self.window.spot_list_table.rowCount() == 0:
            return
        spots = sorted(self.window._state.detected_spots, key=lambda spot: spot.spot_id)
        if not spots:
            return
        group_by_spot: dict[int, SpotGroup] = {}
        for group in self.window._state.spot_groups:
            for spot_id in group.spot_ids:
                group_by_spot[int(spot_id)] = group
        cached_spot_ids = {int(spot.spot_id) for spot in spots if self.window._spot_has_cached_absorbance(spot)}
        spot_color = QColor(self.window._spot_visual_color)
        ring_color = QColor(self.window._ring_visual_color)
        self.window._spot_list_table_updating = True
        self.window.spot_list_table.blockSignals(True)
        try:
            for row in range(self.window.spot_list_table.rowCount()):
                spot_id = self.spot_id_for_row(row)
                if spot_id is None:
                    continue
                spot = self.window._spot_by_id(spot_id)
                if spot is None:
                    continue
                group = group_by_spot.get(int(spot_id))
                spot_row_color = QColor(spot.spot_color_hex) if spot.spot_color_hex and QColor(spot.spot_color_hex).isValid() else (QColor(group.spot_color_hex) if group is not None and QColor(group.spot_color_hex).isValid() else spot_color)
                ring_row_color = QColor(spot.ring_color_hex) if spot.ring_color_hex and QColor(spot.ring_color_hex).isValid() else (QColor(group.ring_color_hex) if group is not None and QColor(group.ring_color_hex).isValid() else ring_color)
                self._clear_row_style(row)
                spot_item = self.window.spot_list_table.item(row, 2)
                if spot_item is not None:
                    spot_item.setIcon(make_color_swatch_icon(spot_row_color))
                ring_item = self.window.spot_list_table.item(row, 3)
                if ring_item is not None:
                    ring_item.setIcon(make_color_swatch_icon(ring_row_color))
                if self.window._cached_spots_only_visible and int(spot_id) not in cached_spot_ids and int(spot_id) not in self.window._selected_spot_ids:
                    self._dim_uncached_row(row, spot_color=spot_row_color, ring_color=ring_row_color)
        finally:
            self.window.spot_list_table.blockSignals(False)
            self.window._spot_list_table_updating = False

    def on_toggled(self, checked: bool) -> None:
        self.window.spot_list_panel.setVisible(checked)
        self.window._settings.setValue("layout/spot_list_visible", bool(checked))
        self.window._refresh_spot_list_action_icon()
        if checked:
            self.window._update_spot_list_table()

    def on_selection_changed(self) -> None:
        if getattr(self.window, "_spot_list_selection_syncing", False):
            return
        selected_ids: set[int] = set()
        for row in range(self.window.spot_list_table.rowCount()):
            if not self.window.spot_list_table.isRowHidden(row) and self.window.spot_list_table.selectionModel().isRowSelected(row, self.window.spot_list_table.rootIndex()):
                item = self.window.spot_list_table.item(row, 0)
                if item is not None:
                    try:
                        selected_ids.add(int(item.text()))
                    except ValueError:
                        continue
        if selected_ids == self.window._selected_spot_ids:
            return
        self.window._selected_spot_ids = selected_ids
        self.window._update_spot_overlays()
        self.window._update_spot_summary()

    def sync_selection(self) -> None:
        if not self.window.spot_list_table.isVisible():
            return
        selection_model = self.window.spot_list_table.selectionModel()
        if selection_model is None:
            return
        self.window._spot_list_selection_syncing = True
        try:
            selection_model.clearSelection()
            for row in range(self.window.spot_list_table.rowCount()):
                item = self.window.spot_list_table.item(row, 0)
                if item is None:
                    continue
                try:
                    spot_id = int(item.text())
                except ValueError:
                    continue
                if spot_id in self.window._selected_spot_ids:
                    selection_model.select(
                        self.window.spot_list_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self.window._spot_list_selection_syncing = False

    def spot_id_for_row(self, row: int) -> int | None:
        if row < 0 or row >= self.window.spot_list_table.rowCount():
            return None
        item = self.window.spot_list_table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def selected_rows(self) -> list[int]:
        selection_model = self.window.spot_list_table.selectionModel()
        if selection_model is None:
            return []
        return [row for row in range(self.window.spot_list_table.rowCount()) if selection_model.isRowSelected(row, self.window.spot_list_table.rootIndex())]

    def selected_spot_ids(self) -> list[int]:
        return [spot_id for row in self.selected_rows() if (spot_id := self.spot_id_for_row(row)) is not None]

    def refresh_headers(self) -> None:
        spot_table_headers(self.window.spot_list_table)

    def _dataset_folder(self) -> Path | None:
        return self.window._dataset_folder_path() if hasattr(self.window, "_dataset_folder_path") else None

    def _spot_table_signature(self) -> str:
        spots = sorted(self.window._state.detected_spots, key=lambda spot: spot.spot_id)
        parts: list[str] = []
        for spot in spots:
            group = self.window._group_for_spot(spot.spot_id)
            parts.append(
                "|".join(
                    [
                        str(int(spot.spot_id)),
                        group.name if group is not None else "",
                        group.spot_color_hex if group is not None else "",
                        group.ring_color_hex if group is not None else "",
                        spot.spot_color_hex or "",
                        spot.ring_color_hex or "",
                        f"{float(spot.center_x):.3f}",
                        f"{float(spot.center_y):.3f}",
                        "" if spot.spot_diameter_px is None else f"{float(spot.spot_diameter_px):.3f}",
                        "" if spot.ring_inner_diameter_px is None else f"{float(spot.ring_inner_diameter_px):.3f}",
                        "" if spot.ring_outer_diameter_px is None else f"{float(spot.ring_outer_diameter_px):.3f}",
                    ]
                )
            )
        return "\n".join(parts)

    def _spot_table_rows(self) -> list[list[object]]:
        rows: list[list[object]] = []
        for order, spot in enumerate(sorted(self.window._state.detected_spots, key=lambda item: item.spot_id)):
            group = self.window._group_for_spot(spot.spot_id)
            rows.append(
                [
                    spot.spot_id,
                    group.name if group is not None else "",
                    group.spot_color_hex if group is not None else "",
                    group.ring_color_hex if group is not None else "",
                    spot.spot_color_hex or "",
                    spot.ring_color_hex or "",
                    self.window._spot_visual_color.name(),
                    self.window._ring_visual_color.name(),
                    spot.center_x,
                    spot.center_y,
                    order,
                    "" if spot.spot_diameter_px is None else spot.spot_diameter_px,
                    "" if spot.ring_inner_diameter_px is None else spot.ring_inner_diameter_px,
                    "" if spot.ring_outer_diameter_px is None else spot.ring_outer_diameter_px,
                ]
            )
        return rows

    def _spot_table_snapshot_path(self, folder: Path) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return folder / f"spot_table_{stamp}.csv"

    def _latest_spot_table_path(self, folder: Path) -> Path | None:
        candidates = sorted(folder.glob("spot_table_*.csv"), key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
        return candidates[0] if candidates else None

    def _save_spot_table_snapshot(self, *, force: bool = False) -> Path | None:
        folder = self._dataset_folder()
        if folder is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        signature = self._spot_table_signature()
        if not force and self.window._last_saved_spot_table_signature == signature and self.window._last_saved_spot_table_path is not None:
            return self.window._last_saved_spot_table_path
        rows = self._spot_table_rows()
        path = self._spot_table_snapshot_path(folder)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "spot_id",
                "group_name",
                "group_spot_color",
                "group_ring_color",
                "spot_color",
                "ring_color",
                "spot_visual_color",
                "ring_visual_color",
                "center_x",
                "center_y",
                "spot_order",
                "spot_diameter_px",
                "ring_inner_diameter_px",
                "ring_outer_diameter_px",
            ])
            if rows:
                writer.writerows(rows)
        self.window._last_saved_spot_table_signature = signature
        self.window._last_saved_spot_table_path = path
        return path

    def on_item_changed(self, item: QTableWidgetItem) -> None:
        if self.window._spot_list_table_updating:
            return
        spot_id = self.spot_id_for_row(item.row())
        if spot_id is None:
            return
        if item.column() == 1:
            self.rename_group(spot_id, item.text().strip())
        elif item.column() in {2, 3, 4}:
            self.edit_diameter_cells(spot_id, item.row())

    def on_cell_double_clicked(self, row: int, column: int) -> None:
        spot_id = self.spot_id_for_row(row)
        if spot_id is None:
            return
        if column == 2:
            self.edit_spot_color(spot_id)
        elif column == 3:
            self.edit_ring_color()
        elif column == 4:
            self.edit_spot_geometry(spot_id)
        elif column == 5:
            self.edit_ring_geometry(spot_id)

    def show_context_menu(self, pos) -> None:
        table = self.window.spot_list_table
        item = table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        spot_id = self.spot_id_for_row(row)
        if spot_id is None:
            return
        menu = QMenu(table)
        group_action = menu.addAction("Group...")
        select_group_action = None
        ungroup_action = None
        destroy_group_action = None
        groups = self.window._groups_for_spot(spot_id)
        if groups:
            select_group_action = menu.addAction("Select group members")
            ungroup_action = menu.addAction("Ungroup")
            destroy_group_action = menu.addAction("Destroy group")
            menu.addSeparator()
        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is group_action:
            self.window._group_selected_spots()
        elif select_group_action is not None and action is select_group_action:
            if self.window._select_group_members_for_spot(spot_id):
                self.window.status_label.setText(f"Selected group members for spot {spot_id}.")
        elif ungroup_action is not None and action is ungroup_action:
            self.window._ungroup_selected_spots()
        elif destroy_group_action is not None and action is destroy_group_action:
            self.window._destroy_groups_for_spot(spot_id)

    def update_table(self) -> None:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        self.window._spot_list_table_updating = True
        sorting_enabled = self.window.spot_list_table.isSortingEnabled()
        sort_column = self.window.spot_list_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.window.spot_list_table.horizontalHeader().sortIndicatorOrder()
        self.window.spot_list_table.setSortingEnabled(False)
        self.window.spot_list_table.blockSignals(True)
        total_rows = 0
        cached_rows = 0
        try:
            self.window.spot_list_table.setRowCount(0)
            spots = sorted(self.window._state.detected_spots, key=lambda spot: spot.spot_id)
            group_by_spot: dict[int, SpotGroup] = {}
            for group in self.window._state.spot_groups:
                for spot_id in group.spot_ids:
                    group_by_spot[int(spot_id)] = group
            cached_spot_ids = {int(spot.spot_id) for spot in spots if self.window._spot_has_cached_absorbance(spot)}
            self.refresh_headers()
            if not spots:
                return
            spot_color = QColor(self.window._spot_visual_color)
            ring_color = QColor(self.window._ring_visual_color)
            for spot in spots:
                try:
                    group = group_by_spot.get(int(spot.spot_id))
                    group_name = group.name if group is not None else "-"
                    spot_row_color = QColor(spot.spot_color_hex) if spot.spot_color_hex and QColor(spot.spot_color_hex).isValid() else (QColor(group.spot_color_hex) if group is not None and QColor(group.spot_color_hex).isValid() else spot_color)
                    ring_row_color = QColor(spot.ring_color_hex) if spot.ring_color_hex and QColor(spot.ring_color_hex).isValid() else (QColor(group.ring_color_hex) if group is not None and QColor(group.ring_color_hex).isValid() else ring_color)
                    spot_diameter_value = float(spot.spot_diameter_px if spot.spot_diameter_px is not None else 2.0 * self.window._state.spot_detection.spot_radius_px)
                    ring_inner_value = float(spot.ring_inner_diameter_px if spot.ring_inner_diameter_px is not None else 2.0 * self.window._state.spot_detection.ring_inner_radius_px)
                    ring_outer_value = float(spot.ring_outer_diameter_px if spot.ring_outer_diameter_px is not None else 2.0 * self.window._state.spot_detection.ring_outer_radius_px)
                    scale = self.window._microns_per_pixel_scalar() if self.window._state.preprocessing.display_units == "um" and self.window._can_display_micrometers() else None
                    spot_diameter_text = (
                        f"{self.window._format_length_display_value(spot_diameter_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(spot_diameter_value):.1f}"
                    )
                    ring_inner_text = (
                        f"{self.window._format_length_display_value(ring_inner_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(ring_inner_value):.1f}"
                    )
                    ring_outer_text = (
                        f"{self.window._format_length_display_value(ring_outer_value):.0f}"
                        if self.window._display_uses_micrometers()
                        else f"{self.window._format_length_display_value(ring_outer_value):.1f}"
                    )
                    append_spot_table_row(
                        self.window.spot_list_table,
                        SpotTableRowData(
                            spot_id=spot.spot_id,
                            group_name=group_name,
                            spot_color=spot_row_color,
                            ring_color=ring_row_color,
                            spot_diameter_text=spot_diameter_text,
                            ring_inner_text=ring_inner_text,
                            ring_outer_text=ring_outer_text,
                            x_text=format_xy_value(spot.center_x, self.window._state.preprocessing.display_units, scale),
                            y_text=format_xy_value(spot.center_y, self.window._state.preprocessing.display_units, scale),
                        ),
                    )
                    row = self.window.spot_list_table.rowCount() - 1
                    self.window.spot_list_table.item(row, 1).setToolTip("Double-click to create a group for the selected spots." if group is None else f"{group.name} (spot {group.spot_color_hex}, ring {group.ring_color_hex})")
                    self.window.spot_list_table.item(row, 2).setToolTip("Double-click to change the spot or group color.")
                    self.window.spot_list_table.item(row, 3).setToolTip("Double-click to change the reference-ring color.")
                    total_rows += 1
                    spot_cached = int(spot.spot_id) in cached_spot_ids
                    if spot_cached:
                        cached_rows += 1
                    if self.window._cached_spots_only_visible and not spot_cached:
                        if spot.spot_id not in self.window._selected_spot_ids:
                            self._dim_uncached_row(row, spot_color=spot_row_color, ring_color=ring_row_color)
                except Exception:
                    logger.exception("Spot table row build failed | spot_id=%s", int(spot.spot_id))
            self.window.spot_list_table.resizeColumnsToContents()
            self.window.spot_list_table.setColumnWidth(0, 34)
            self.window.spot_list_table.setColumnWidth(1, 96)
            self.window.spot_list_table.setColumnWidth(2, 22)
            self.window.spot_list_table.setColumnWidth(3, 22)
            self.window.spot_list_table.setColumnWidth(4, 58)
            self.window.spot_list_table.setColumnWidth(5, 58)
            self.window.spot_list_table.setColumnWidth(6, 58)
            self.window.spot_list_table.setColumnWidth(7, 64)
            self.window.spot_list_table.setColumnWidth(8, 64)
            self.window.spot_list_table.horizontalHeader().setStretchLastSection(False)
        finally:
            self.window.spot_list_table.blockSignals(False)
            self.window.spot_list_table.setSortingEnabled(sorting_enabled)
            if sorting_enabled and sort_column >= 0:
                self.window.spot_list_table.sortItems(sort_column, sort_order)
            self.window._spot_list_table_updating = False
        self.window._append_workflow_log_throttled(
            "spot_table_update",
            f"Spot table updated | rows={int(total_rows)} cached={int(cached_rows)} cached_only={bool(self.window._cached_spots_only_visible)}",
            level="debug",
            min_interval=2.0,
        )
        self.sync_selection()
        self._save_spot_table_snapshot()

    def rename_group(self, spot_id: int, new_name: str) -> None:
        self.window._rename_spot_group_from_table(spot_id, new_name)

    def edit_spot_color(self, spot_id: int) -> None:
        self.window._edit_spot_color_from_table(spot_id)

    def edit_ring_color(self) -> None:
        self.window._edit_ring_color_from_table()

    def edit_spot_geometry(self, spot_id: int) -> None:
        self.window._edit_spot_geometry_from_table(spot_id)

    def edit_ring_geometry(self, spot_id: int) -> None:
        self.window._edit_ring_geometry_from_table(spot_id)

    def edit_diameter_cells(self, spot_id: int, row: int) -> None:
        self.window._edit_spot_diameter_cells_from_table(spot_id, row)

    def rename_group_from_row(self, spot_id: int, new_name: str) -> None:
        self.window._rename_spot_group_from_table(spot_id, new_name)

    def copy_properties(self) -> None:
        self.window._copy_spot_properties_from_table()

    def paste_properties(self) -> None:
        self.window._paste_spot_properties_from_table()

    def move_selected(self, direction: int) -> None:
        self.window._move_selected_spots_in_table(direction)

    def edit_spot_color_from_row(self, spot_id: int) -> None:
        self.window._edit_spot_color_from_table(spot_id)

    def edit_ring_color_from_selection(self) -> None:
        self.window._edit_ring_color_from_table()

    def edit_spot_geometry_from_row(self, spot_id: int) -> None:
        self.window._edit_spot_geometry_from_table(spot_id)

    def edit_ring_geometry_from_row(self, spot_id: int) -> None:
        self.window._edit_ring_geometry_from_table(spot_id)

    def edit_diameter_cells_from_row(self, spot_id: int, row: int) -> None:
        self.window._edit_spot_diameter_cells_from_table(spot_id, row)

    def export_csv(self) -> None:
        path = self._save_spot_table_snapshot(force=True)
        if path is None:
            self.window.status_label.setText("No dataset available for spot table export.")
            return
        self.window.status_label.setText(f"Saved spot table snapshot to {path.name}.")

    def import_csv(self) -> None:
        folder = self._dataset_folder()
        if folder is None:
            self.window.status_label.setText("Load a dataset before importing a spot table.")
            return
        latest = self._latest_spot_table_path(folder)
        path: Path | None = None
        if latest is not None:
            answer = QMessageBox.question(
                self.window,
                "Load latest spot table",
                f"Load the latest saved spot table?\n\n{latest.name}\n\nChoose No to browse for another file.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                path = latest
            elif answer == QMessageBox.StandardButton.No:
                path_str, _ = QFileDialog.getOpenFileName(
                    self.window,
                    "Load spot table CSV",
                    str(latest),
                    "CSV Files (*.csv)",
                )
                if not path_str:
                    return
                path = Path(path_str)
            else:
                return
        else:
            path_str, _ = QFileDialog.getOpenFileName(
                self.window,
                "Load spot table CSV",
                str(folder),
                "CSV Files (*.csv)",
            )
            if not path_str:
                return
            path = Path(path_str)
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        if not rows:
            self.window.status_label.setText("CSV file is empty.")
            return
        self.window._push_undo_point("Import spot list CSV")
        groups_by_name = {group.name: group for group in self.window._state.spot_groups}
        ordered_rows = sorted(rows, key=lambda row: int(row.get("spot_order", row.get("spot_id", 0)) or 0))
        for row in ordered_rows:
            try:
                spot_id = int(row.get("spot_id", ""))
            except ValueError:
                continue
            spot = self.window._spot_by_id(spot_id)
            if spot is None:
                continue
            group_name = str(row.get("group_name", "")).strip()
            group_spot_color = str(row.get("group_spot_color", row.get("group_color", ""))).strip() or "#f59e0b"
            group_ring_color = str(row.get("group_ring_color", "")).strip() or self.window._ring_visual_color.name()
            if group_name:
                group = groups_by_name.get(group_name)
                if group is None:
                    group = SpotGroup(
                        group_id=f"group_{len(groups_by_name) + 1}",
                        name=group_name,
                        spot_color_hex=group_spot_color,
                        ring_color_hex=group_ring_color,
                        spot_ids=[],
                    )
                    self.window._state.spot_groups.append(group)
                    groups_by_name[group_name] = group
                if spot_id not in group.spot_ids:
                    group.spot_ids.append(spot_id)
                group.spot_color_hex = group_spot_color
                group.ring_color_hex = group_ring_color
            spot.spot_diameter_px = None if row.get("spot_diameter_px", "") == "" else float(row["spot_diameter_px"])
            spot.ring_inner_diameter_px = None if row.get("ring_inner_diameter_px", "") == "" else float(row["ring_inner_diameter_px"])
            spot.ring_outer_diameter_px = None if row.get("ring_outer_diameter_px", "") == "" else float(row["ring_outer_diameter_px"])
            if row.get("spot_color", ""):
                self.window._spot_visual_color = QColor(str(row["spot_color"]))
            if row.get("ring_color", ""):
                self.window._ring_visual_color = QColor(str(row["ring_color"]))
        self.window._update_color_button_styles()
        self.window._update_spot_overlays()
        self.window._update_spot_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_spot_list_table()
        self.window.status_label.setText(f"Loaded spot table from {path.name}.")
