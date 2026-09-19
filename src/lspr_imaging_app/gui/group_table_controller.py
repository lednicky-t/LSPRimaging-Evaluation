from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QHeaderView, QInputDialog, QMenu, QTableWidgetItem

from lspr_imaging_app.domain.models import AreaRoiGroup
from lspr_imaging_app.gui.roi_table_helpers import GroupTableRowData, append_group_table_row, group_table_headers

_logger = logging.getLogger("lspr_imaging_app.workflow")


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

    def _index_for_group_id(self, group_id: str) -> int | None:
        return next(
            (i for i, group in enumerate(self.window._state.area_roi_groups) if group.group_id == group_id), None
        )

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
        table.blockSignals(True)
        self.window._group_table_updating = True
        try:
            table.setRowCount(0)
            group_table_headers(table)
            for position, group in enumerate(self.window._state.area_roi_groups, start=1):
                append_group_table_row(
                    table,
                    GroupTableRowData(
                        group_id=group.group_id,
                        name=group.name,
                        sample_color=QColor(group.sample_color_hex),
                        reference_color=QColor(group.reference_color_hex),
                        roi_count=len(group.area_roi_ids),
                        position=position,
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
                        position=None,
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
            table.setColumnWidth(0, 24)
            table.setColumnWidth(1, 100)
            table.setColumnWidth(2, 22)
            table.setColumnWidth(3, 22)
            table.setColumnWidth(4, 50)
            header.setStretchLastSection(False)
        finally:
            table.blockSignals(False)
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
        if item.column() != 1:
            return
        group_id = self._group_id_for_row(item.row())
        if group_id is None:
            return
        self.rename_group(group_id, item.text().strip())

    def on_cell_double_clicked(self, row: int, column: int) -> None:
        group_id = self._group_id_for_row(row)
        if group_id is None:
            return
        if column == 2:
            self.recolor_sample(group_id)
        elif column == 3:
            self.recolor_reference(group_id)

    # ---- context menu ----

    def show_context_menu(self, pos) -> None:
        table = self.window.group_table
        item = table.itemAt(pos)
        # Diagnostic for a "right-click does nothing" report (2026-09-19):
        # this line firing at all proves Qt delivered the right-click as far
        # as this Python slot - if a repro's log has no "Group table |
        # context menu requested" line, the click never reached here, which
        # points at Qt/OS-level event delivery rather than this method's own
        # logic (which the rest of this function's early-returns already
        # cover: item is None means empty table area, group_id is None means
        # the Ungrouped row - neither shows a menu, by design).
        _logger.debug("Group table | context menu requested | pos=%s | item_row=%s", pos, None if item is None else item.row())
        if item is None:
            return
        group_id = self._group_id_for_row(item.row())
        if group_id is None:
            return  # Ungrouped row: nothing to rename/recolor/delete.
        group = self._group_by_id(group_id)
        if group is None:
            return
        group_index = self._index_for_group_id(group_id)
        menu = QMenu(table)
        menu.setToolTipsVisible(True)
        rename_action = menu.addAction("Rename...")
        recolor_sample_action = menu.addAction("Recolor sample...")
        recolor_reference_action = menu.addAction("Recolor reference...")
        menu.addSeparator()
        move_up_action = menu.addAction("Move up")
        move_up_action.setEnabled(group_index is not None and group_index > 0)
        move_down_action = menu.addAction("Move down")
        move_down_action.setEnabled(
            group_index is not None and group_index < len(self.window._state.area_roi_groups) - 1
        )
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
        elif action is move_up_action:
            self.move_group(group_id, -1)
        elif action is move_down_action:
            self.move_group(group_id, 1)
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

    def move_selected(self, direction: int) -> None:
        """Toolbar/keyboard entry point (group_move_up/down_button, PageUp/
        PageDown on window.group_table): moves the single selected group by
        one position. No-ops on the synthetic Ungrouped row or when more/
        fewer than one row is selected, since a multi-row swap has no single
        well-defined target position."""
        table = self.window.group_table
        selection_model = table.selectionModel()
        if selection_model is None:
            return
        selected_rows = [
            row for row in range(table.rowCount()) if selection_model.isRowSelected(row, table.rootIndex())
        ]
        if len(selected_rows) != 1:
            self.window.status_label.setText("Select exactly one group to move it.")
            return
        group_id = self._group_id_for_row(selected_rows[0])
        if group_id is None:
            self.window.status_label.setText("The Ungrouped row can't be reordered.")
            return
        self.move_group(group_id, direction)

    def move_group(self, group_id: str, direction: int) -> None:
        """Swaps the group's position with its neighbor in
        window._state.area_roi_groups - the group's `group_id` is stable and
        not positional, so this is a plain list swap with no id remapping
        (unlike _move_selected_rois_in_table's ROI-id renumbering). List
        order is what already determines default group colors
        (_next_group_palette_color/_apply_group_color_palette) and the
        sensorgram "Average by group" legend order, so this directly
        controls both, and is exactly what the Group table's "#" column
        displays.

        Deliberately skips _update_roi_overlays()/_update_roi_summary() -
        unlike rename/recolor/add-remove-ROI, a pure reorder changes no
        ROI's position, color, or group membership, so a full overlay
        rebuild (one pyqtgraph curve rebuilt per ROI, see
        OverlayManager._update_roi_overlays, measured ~19ms/call at 200
        ROIs) would be pure wasted work on every single click.

        Also refreshes window.group_table directly rather than going through
        window._update_roi_table() - that method's debounced timer rebuilds
        *both* the ROI table and the Group table (they share one timer,
        since most group mutations - rename, recolor, add/remove ROI - do
        change what the ROI table's Group/color columns show). A pure
        reorder changes neither, so paying for a full ROI-table rebuild
        (measured ~28ms/call at 200 ROIs - the actual dominant cost of a
        move click, bigger than the overlay rebuild above or the undo
        snapshot below) on every arrow click would still be pure waste even
        after dropping the overlay refresh.

        Real-dataset log evidence (2026-09-19, ~160 ROIs, never-yet-analyzed
        selection) found two more per-click costs the small synthetic
        benchmark above didn't surface: the undo snapshot's deepcopy
        (300-450ms - real ImageDataset/records are far heavier than the
        synthetic test's) and _render_sensorgram_display() itself
        (460-620ms - a cold RAM cache falls through to a real per-ROI HDF5
        backup probe, see _sensorgram_trace_for_roi). Both are now debounced
        the same way _roi_move_undo_commit_timer already debounces keyboard
        ROI-nudging's identical deepcopy-per-repeat problem: a burst of
        rapid clicks (the normal way to move a group several positions)
        pays for one deepcopy and one sensorgram resolve after the user
        stops, not one of each per click. The list swap and the Group
        table's own "#" refresh below stay synchronous/immediate - only the
        two expensive, purely-cosmetic-until-settled steps are deferred."""
        groups = self.window._state.area_roi_groups
        index = self._index_for_group_id(group_id)
        if index is None or direction == 0:
            return
        task_started = time.perf_counter()
        new_index = index + direction
        if new_index < 0 or new_index >= len(groups):
            return
        self.window._prepare_undo_snapshot("Reorder groups")
        undo_seconds = time.perf_counter() - task_started
        groups[index], groups[new_index] = groups[new_index], groups[index]
        self.window._append_workflow_log(
            f"Groups | move '{groups[new_index].name}' {'up' if direction < 0 else 'down'}", level="info"
        )
        undo_timer = self.window._group_reorder_undo_commit_timer
        if undo_timer.isActive():
            undo_timer.stop()
        undo_timer.start()
        sensorgram_timer = self.window._group_reorder_sensorgram_timer
        if sensorgram_timer.isActive():
            sensorgram_timer.stop()
        sensorgram_timer.start()
        self.window._save_processing_state_for_dataset()
        table_started = time.perf_counter()
        self.update_table()
        table_seconds = time.perf_counter() - table_started
        # Debug-only stage timing (CLAUDE.md's Performance Work convention -
        # cheap enough to leave on unconditionally). undo/sensorgram here are
        # now just the prepare()/timer-arming cost, not the deferred work.
        _logger.debug(
            "Groups | move stage timing | undo_prepare=%.1fms table=%.1fms total=%.1fms",
            undo_seconds * 1000.0,
            table_seconds * 1000.0,
            (time.perf_counter() - task_started) * 1000.0,
        )

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
        # If ROIs are selected at creation time, the new group starts with
        # them already as members (moved out of whatever group they were
        # in, same "at most one group" rule as add_selected_rois_to_group)
        # instead of forcing a separate "Add selected ROIs" step right after.
        selected_ids = set(self.window._selected_roi_ids)
        if selected_ids:
            for other in self.window._state.area_roi_groups:
                other.area_roi_ids = [rid for rid in other.area_roi_ids if rid not in selected_ids]
            self.window._state.area_roi_groups = [
                candidate for candidate in self.window._state.area_roi_groups if candidate.area_roi_ids
            ]
        group = AreaRoiGroup(
            group_id=f"group_{len(self.window._state.area_roi_groups) + 1}",
            name=name,
            sample_color_hex=color.name(),
            reference_color_hex=self.window._reference_visual_color.name(),
            area_roi_ids=sorted(selected_ids),
        )
        self.window._state.area_roi_groups.append(group)
        if selected_ids:
            self.window._append_workflow_log(
                f"Groups | create '{name}' with {len(selected_ids)} selected ROI(s)", level="success"
            )
            self.window._update_roi_overlays()
            self.window._update_roi_summary()
            self.window._save_processing_state_for_dataset()
            self.window._update_roi_table()
            self.window.status_label.setText(f"Created group '{name}' with {len(selected_ids)} selected ROI(s).")
        else:
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

    def delete_selected_groups(self) -> None:
        """Toolbar-button counterpart to delete_group's context-menu action:
        deletes every group with a selected row in window.group_table (the
        synthetic Ungrouped row has no group_id and is silently skipped)."""
        table = self.window.group_table
        selection_model = table.selectionModel()
        if selection_model is None:
            return
        group_ids = {
            group_id
            for row in range(table.rowCount())
            if selection_model.isRowSelected(row, table.rootIndex())
            for group_id in [self._group_id_for_row(row)]
            if group_id is not None
        }
        if not group_ids:
            self.window.status_label.setText("Select a group first to delete it.")
            return
        groups = [group for group in self.window._state.area_roi_groups if group.group_id in group_ids]
        if not groups:
            return
        self.window._push_undo_point("Delete group" if len(groups) == 1 else "Delete groups")
        for group in groups:
            self.window._append_workflow_log(f"Groups | delete '{group.name}'", level="warning")
        self.window._state.area_roi_groups = [
            candidate for candidate in self.window._state.area_roi_groups if candidate.group_id not in group_ids
        ]
        self.window._update_roi_overlays()
        self.window._update_roi_summary()
        self.window._save_processing_state_for_dataset()
        self.window._update_roi_table()
        if len(groups) == 1:
            self.window.status_label.setText(f"Deleted group '{groups[0].name}'; its ROIs are now ungrouped.")
        else:
            self.window.status_label.setText(f"Deleted {len(groups)} groups; their ROIs are now ungrouped.")

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
