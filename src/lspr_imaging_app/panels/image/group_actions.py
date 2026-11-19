"""What the Groups section's buttons do (Image panel, ROIs tab). Calls only public interfaces.

`GroupControls` (buttons) -> this -> `RoiToolbox.group_rois` / `create_group` / `group_rois_each` (one undo
step each). The panel owns no group state. Group by rows / columns uses the selected ROIs, or all if none are
selected, and the same row finding as Reorder IDs (`roi/ordering.grid_lines`); each group takes the next
unused base colour of the palette (`roi/palette.py`).

**Ctrl+G / Ctrl+Shift+G** (2026-11-19, `shortcuts.py` ``groups.group`` / ``groups.ungroup``) run the same two commands as the Group / Ungroup
buttons, from anywhere in the application (the ROI table is another dock, and the ROI selection is shared). The two actions live on the
Image panel itself (``action_parent``), not on the ribbon's buttons: a ribbon tab that is not the open one is hidden, and a hidden widget's
shortcut does not fire (Qt). Like Ctrl+L / Ctrl+K they always run and say in the status bar why when there is nothing to do.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ...domain.roi_shapes import nominal_diameter
from ...roi import RoiToolbox
from ...roi.ordering import grid_band_px, grid_lines
from ...selection import SelectionModule
from ...shortcuts import bind_action
from .group_controls import GroupControls


class GroupActions(QObject):
    status = pyqtSignal(str)

    def __init__(
        self,
        controls: GroupControls,
        *,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        dialog_parent: QWidget | None = None,
        ask_name: Callable[[str, str], str | None] | None = None,
        tell: Callable[[str, str], None] | None = None,
        action_parent: QWidget | None = None,
        parent: QObject | None = None,
    ) -> None:
        """``action_parent``: the widget the Ctrl+G / Ctrl+Shift+G actions live on (the Image panel: it is visible whenever its dock is; the
        default is ``controls``, which is hidden while another ribbon tab is open)."""
        super().__init__(parent)
        self._toolbox = toolbox
        self._selection = selection
        self._ask_name = ask_name or (lambda title, default: self._dialog_name(dialog_parent, title, default))
        self._tell = tell or (lambda title, text: QMessageBox.warning(dialog_parent, title, text))
        self._controls = controls
        controls.create_requested.connect(self._on_create)
        controls.ungroup_requested.connect(self._on_ungroup)
        holder = action_parent if action_parent is not None else controls
        self.group_action = QAction("Group selected ROIs", holder)
        self.ungroup_action = QAction("Ungroup", holder)
        for action, binding_id, command in ((self.group_action, "groups.group", self._on_create), (self.ungroup_action, "groups.ungroup", self._on_ungroup)):
            bind_action(action, binding_id, context=Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(lambda _checked=False, run=command: run())
            holder.addAction(action)
        selection.roi_selection_changed.connect(self._refresh_mode)
        toolbox.cosmetic_changed.connect(self._refresh_mode)  # groups made / dissolved
        self._refresh_mode()
        controls.by_rows_requested.connect(lambda: self._on_by_lines(column_major=False))
        controls.by_columns_requested.connect(lambda: self._on_by_lines(column_major=True))

    @staticmethod
    def _dialog_name(parent: QWidget | None, title: str, default: str) -> str | None:
        name, accepted = QInputDialog.getText(parent, title, "Group name", text=default)
        return name.strip() if accepted and name.strip() else None

    def _refuse(self, message: str) -> None:
        self.status.emit(f"Groups: {message}")
        self._tell("Groups", message)

    def _grouped_selection(self) -> list[int]:
        """The selected ROIs that are in a group."""
        selected = {int(i) for i in self._selection.selected_roi_ids()}
        in_group = {roi_id for group in self._toolbox.groups() for roi_id in group.area_roi_ids}
        return sorted(selected & in_group)

    def _refresh_mode(self, *_args: object) -> None:
        self._controls.set_ungroup_mode(bool(self._grouped_selection()))

    def _on_ungroup(self) -> None:
        """Take the selected ROIs out of their groups (a group left empty goes away); the ROIs stay."""
        ids = self._grouped_selection()
        if not ids:  # (the button only shows while the selection holds grouped ROIs; Ctrl+Shift+G can be pressed at any time)
            self.status.emit("Groups: none of the selected ROIs is in a group.")
            return
        try:
            self._toolbox.remove_rois_from_groups(ids)
        except (ValueError, KeyError) as error:
            self._refuse(str(error))
            return
        self.status.emit(f"Groups: took {len(ids)} ROI{'' if len(ids) == 1 else 's'} out of their groups.")

    def _on_create(self) -> None:
        ids = tuple(sorted(self._selection.selected_roi_ids()))
        name = self._ask_name("Group ROIs" if ids else "New group", f"Group {len(self._toolbox.groups()) + 1}")
        if name is None:
            return
        try:
            if ids:
                self._toolbox.group_rois(ids, name)
            else:
                self._toolbox.create_group(name)
        except (ValueError, KeyError) as error:
            self._refuse(str(error))
            return
        self.status.emit(f"Groups: created {name!r}" + (f" with {len(ids)} ROIs." if ids else " (empty)."))

    def _on_by_lines(self, *, column_major: bool) -> None:
        selected = {int(i) for i in self._selection.selected_roi_ids()}
        cube = int(self._selection.current_cube())
        rois = [roi for roi in self._toolbox.rois_at(cube) if not selected or roi.area_roi_id in selected]
        if len(rois) < 2:
            self.status.emit("Groups: nothing to group (fewer than two ROIs).")
            return
        lines = grid_lines(
            [roi.area_roi_id for roi in rois],
            [(float(roi.center_x), float(roi.center_y)) for roi in rois],
            grid_band_px([nominal_diameter(roi.shape, roi.center_x, roi.center_y) for roi in rois]),
            column_major=column_major,
        )
        noun = "Column" if column_major else "Row"
        # Zero-padded numbers ("Row 01" ... "Row 12") so that sorting by name keeps the order 1, 2, ... 10, 11
        # (plain text sorting puts "Row 10" before "Row 2"). Two digits at least; wider only past 99 lines.
        digits = max(2, len(str(len(lines))))
        try:
            self._toolbox.group_rois_each([(f"{noun} {n:0{digits}d}", line) for n, line in enumerate(lines, start=1)])
        except (ValueError, KeyError) as error:
            self._refuse(str(error))
            return
        self.status.emit(f"Groups: made {len(lines)} groups, one per {noun.lower()}.")
