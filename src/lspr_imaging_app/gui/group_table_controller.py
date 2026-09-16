from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QHeaderView, QInputDialog, QMenu, QTableWidgetItem

from lspr_imaging_app.domain.models import AreaRoiGroup
from lspr_imaging_app.gui.roi_table_helpers import GroupTableRowData, append_group_table_row, group_table_headers


class GroupTableController:
    """Drives window.group_table - the Group view of the ROI/Group panel
    toggle (see PanelContainer's title_options in widgets.py). Mirrors
    RoiTableController's shape (rebuild/selection-sync/context-menu/double-
    click) but one row per AreaRoiGroup instead of per AreaRoi, plus a
    synthetic "Ungrouped" row (group_id None) for ROIs not in any group."""

    def __init__(self, window) -> None:
        self.window = window

    # ---- row <-> group lookups ----

    def _group_id_for_row(self, row: int) -> str | None:
        """None means either an invalid row or the synthetic Ungrouped row -
        callers that must tell those apart should range-check `row` first."""
        table = self.window.group_table
        if row < 0 or row >= table.rowCount():
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _group_by_id(self, group_id: str) -> AreaRoiGroup | None:
        return next((group for group in self.window._state.area_roi_groups if group.group_id == group_id), None)

    def _ungrouped_roi_ids(self) -> set[int]:
        grouped_ids = {int(rid) for group in self.window._state.area_roi_groups for rid in group.area_roi_ids}
        return {roi.area_roi_id for roi in self.window._state.area_rois if roi.area_roi_id not in grouped_ids}

    def _member_ids_for_row(self, row: int) -> set[int]:
        group_id = self._group_id_for_row(row)
        if group_id is None:
            return self._ungrouped_roi_ids()
        group = self._group_by_id(group_id)
        return {int(rid) for rid in group.area_roi_ids} if group is not None else set()

    # ---- rebuild ----

    def update_table(self) -> None:
        table = self.window.group_table
        table.setSortingEnabled(False)
        table.blockSignals(True)
        self.window._group_table_updating = True
        try:
            table.setRowCount(0)
            group_table_headers(table)
            for group in self.window._state.area_roi_groups:
                append_group_table_row(
                    table,
                    GroupTableRowData(
                        group_id=group.group_id,
                        name=group.name,
                        sample_color=QColor(group.sample_color_hex),
                        reference_color=QColor(group.reference_color_hex),
                        roi_count=len(group.area_roi_ids),
                    ),
                )
            ungrouped_ids = self._ungrouped_roi_ids()
            if ungrouped_ids:
                append_group_table_row(
                    table,
                    GroupTableRowData(
                        group_id=None,
                        name="Ungrouped",
                        sample_color=QColor(self.window._sample_visual_color),
                        reference_color=QColor(self.window._reference_visual_color),
                        roi_count=len(ungrouped_ids),
                    ),
                )
            # All columns need to switch out of the table's default
            # ResizeToContents mode before an explicit width sticks - a
            # ResizeToContents section keeps recomputing its own width (to
            # fit its header text, which is what made the icon-only Sample/
            # Reference columns end up far wider than their 16px swatch) and
            # silently ignores setColumnWidth() otherwise.
            header = table.horizontalHeader()
            for column in range(table.columnCount()):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            # Qt also clamps any explicit width to header.minimumSectionSize()
            # regardless of resize mode (default here computes to 32px, from
            # font metrics) - without lowering it, the 22px swatch columns
            # below would silently end up wider than requested too.
            header.setMinimumSectionSize(16)
            table.setColumnWidth(0, 110)
            table.setColumnWidth(1, 22)
            table.setColumnWidth(2, 22)
            table.setColumnWidth(3, 50)
            header.setStretchLastSection(False)
        finally:
            table.blockSignals(False)
            table.setSortingEnabled(True)
            self.window._group_table_updating = False
        self.sync_selection()

    # ---- selection ----

    def on_selection_changed(self) -> None:
        if self.window._group_list_selection_syncing:
            return
        table = self.window.group_table
        selected_ids: set[int] = set()
        for row in range(table.rowCount()):
            if table.selectionModel().isRowSelected(row, table.rootIndex()):
                selected_ids |= self._member_ids_for_row(row)
        if selected_ids == self.window._selected_roi_ids:
            return
        self.window._apply_roi_selection(selected_ids)

    def sync_selection(self) -> None:
        table = self.window.group_table
        if not table.isVisible():
            return
        selection_model = table.selectionModel()
        if selection_model is None:
            return
        self.window._group_list_selection_syncing = True
        try:
            selection_model.clearSelection()
            for row in range(table.rowCount()):
                member_ids = self._member_ids_for_row(row)
                # Deliberately skip empty groups here - an empty set is a
                # subset of every set, so without this guard a freshly
                # created, not-yet-populated group would show as "selected"
                # any time _selected_roi_ids is non-empty.
                if member_ids and member_ids.issubset(self.window._selected_roi_ids):
                    selection_model.select(
                        table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self.window._group_list_selection_syncing = False

    # ---- inline editing ----

    def on_item_changed(self, item: QTableWidgetItem) -> None:
        if self.window._group_table_updating:
            return
        if item.column() != 0:
            return
        group_id = self._group_id_for_row(item.row())
        if group_id is None:
            return
        self.rename_group(group_id, item.text().strip())

    def on_cell_double_clicked(self, row: int, column: int) -> None:
        group_id = self._group_id_for_row(row)
        if group_id is None:
            return
        if column == 1:
            self.recolor_sample(group_id)
        elif column == 2:
            self.recolor_reference(group_id)

    # ---- context menu ----

    def show_context_menu(self, pos) -> None:
        table = self.window.group_table
        item = table.itemAt(pos)
        if item is None:
            return
        group_id = self._group_id_for_row(item.row())
        if group_id is None:
            return  # Ungrouped row: nothing to rename/recolor/delete.
        group = self._group_by_id(group_id)
        if group is None:
            return
        menu = QMenu(table)
        menu.setToolTipsVisible(True)
        rename_action = menu.addAction("Rename...")
        recolor_sample_action = menu.addAction("Recolor sample...")
        recolor_reference_action = menu.addAction("Recolor reference...")
        menu.addSeparator()
        has_selection = bool(self.window._selected_roi_ids)
        add_action = menu.addAction("Add selected ROIs to this group")
        add_action.setEnabled(has_selection)
        add_action.setToolTip(
            "Select ROI(s) in ROI view first."
            if not has_selection
            else "Move the currently selected ROI(s) into this group."
        )
        can_remove = has_selection and bool(self.window._selected_roi_ids & set(group.area_roi_ids))
        remove_action = menu.addAction("Remove selected ROIs from this group")
        remove_action.setEnabled(can_remove)
        menu.addSeparator()
        delete_action = menu.addAction("Delete group")
        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is rename_action:
            self._rename_group_dialog(group_id)
        elif action is recolor_sample_action:
            self.recolor_sample(group_id)
        elif action is recolor_reference_action:
            self.recolor_reference(group_id)
        elif action is add_action:
            self.add_selected_rois_to_group(group_id)
        elif action is remove_action:
            self.remove_selected_rois_from_group(group_id)
        elif action is delete_action:
            self.delete_group(group_id)

    # ---- mutations ----
    # Every mutation below follows the same standard sequence used
    # throughout roi_geometry_mixin.py's existing group actions: push an
    # undo point before mutating, refresh overlays/summary, save the
    # processing state, then refresh both tables via window._update_roi_table()
    # (the shared debounce timer - see MainWindow._refresh_roi_and_group_tables).

    def _rename_group_dialog(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        name, accepted = QInputDialog.getText(self.window, "Rename group", "Group name", text=group.name)
        if not accepted:
            return
        self.rename_group(group_id, name.strip())

    def rename_group(self, group_id: str, new_name: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        if not new_name:
            self.window.status_label.setText("Group name cannot be empty.")
            self.window._update_roi_table()
            return
        if group.name == new_name:
            return
        self.window._push_undo_point("Rename group")
        group.name = new_name
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()

    def _recolor(self, group_id: str, *, field: str, dialog_title: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        current = QColor(getattr(group, field))
        color = QColorDialog.getColor(current, self.window, dialog_title)
        if not color.isValid():
            return
        self.window._push_undo_point("Recolor group")
        setattr(group, field, color.name())
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._refresh_visible_spectrum_from_cache()
        if hasattr(self.window, "_analysis_controller"):
            self.window._analysis_controller._render_sensorgram_display()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()

    def recolor_sample(self, group_id: str) -> None:
        self._recolor(group_id, field="sample_color_hex", dialog_title="Choose group sample color")

    def recolor_reference(self, group_id: str) -> None:
        self._recolor(group_id, field="reference_color_hex", dialog_title="Choose group reference color")

    def create_group(self) -> None:
        default_name = f"Group {len(self.window._state.area_roi_groups) + 1}"
        name, accepted = QInputDialog.getText(self.window, "New group", "Group name", text=default_name)
        if not accepted:
            return
        name = name.strip()
        if not name:
            self.window.status_label.setText("Group creation cancelled: name is required.")
            return
        color = QColorDialog.getColor(self.window._next_group_palette_color(), self.window, "New group color")
        if not color.isValid():
            return
        self.window._push_undo_point("New group")
        group = AreaRoiGroup(
            group_id=f"group_{len(self.window._state.area_roi_groups) + 1}",
            name=name,
            sample_color_hex=color.name(),
            reference_color_hex=self.window._reference_visual_color.name(),
            area_roi_ids=[],
        )
        # Deliberately not filtered by "drop if area_roi_ids is empty" - unlike
        # _ungroup_selected_rois/_destroy_groups_for_roi, where an empty group
        # is always an accidental byproduct, this one is intentionally empty
        # until the user adds ROIs to it via "Add selected ROIs to this group".
        self.window._state.area_roi_groups.append(group)
        self.window._append_workflow_log(f"Groups | create empty '{name}'", level="success")
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()
        self.window.status_label.setText(f"Created empty group '{name}'. Add ROIs to it from its right-click menu.")

    def add_selected_rois_to_group(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        selected_ids = set(self.window._selected_roi_ids)
        if not selected_ids:
            self.window.status_label.setText("Select ROI(s) first to add them to a group.")
            return
        self.window._push_undo_point("Add ROIs to group")
        self.window._append_workflow_log(
            f"Groups | add {len(selected_ids)} ROI(s) to '{group.name}'", level="success"
        )
        # An ROI belongs to at most one group - pull the selection out of
        # every other group first (same rule _group_selected_rois follows),
        # pruning any that become empty as a side effect of that removal.
        for other in self.window._state.area_roi_groups:
            if other is not group:
                other.area_roi_ids = [rid for rid in other.area_roi_ids if rid not in selected_ids]
        self.window._state.area_roi_groups = [
            candidate for candidate in self.window._state.area_roi_groups if candidate is group or candidate.area_roi_ids
        ]
        group.area_roi_ids = sorted(set(group.area_roi_ids).union(selected_ids))
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()
        self.window.status_label.setText(f"Added {len(selected_ids)} ROI(s) to '{group.name}'.")

    def remove_selected_rois_from_group(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        removable = set(self.window._selected_roi_ids) & set(group.area_roi_ids)
        if not removable:
            self.window.status_label.setText("None of the selected ROIs are in this group.")
            return
        self.window._push_undo_point("Remove ROIs from group")
        self.window._append_workflow_log(
            f"Groups | remove {len(removable)} ROI(s) from '{group.name}'", level="warning"
        )
        group.area_roi_ids = [rid for rid in group.area_roi_ids if rid not in removable]
        if not group.area_roi_ids:
            # Emptied by this removal, not by intentional creation - prune it,
            # matching _ungroup_selected_rois/_destroy_groups_for_roi.
            self.window._state.area_roi_groups = [
                candidate for candidate in self.window._state.area_roi_groups if candidate is not group
            ]
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()
        self.window.status_label.setText(f"Removed {len(removable)} ROI(s) from '{group.name}'.")

    def delete_group(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        self.window._push_undo_point("Delete group")
        self.window._append_workflow_log(f"Groups | delete '{group.name}'", level="warning")
        self.window._state.area_roi_groups = [
            candidate for candidate in self.window._state.area_roi_groups if candidate is not group
        ]
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()
        self.window.status_label.setText(f"Deleted group '{group.name}'; its ROIs are now ungrouped.")
