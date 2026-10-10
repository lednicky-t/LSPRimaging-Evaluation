"""What the Groups section's buttons do (Image panel, ROIs tab). Calls only public interfaces.

`GroupControls` (buttons) -> this -> `RoiToolbox.group_rois` / `create_group` / `group_rois_each` (one undo
step each). The panel owns no group state. Group by rows / columns uses the selected ROIs, or all if none are
selected, and the same row finding as Reorder IDs (`roi/ordering.grid_lines`); each group takes the next
unused base colour of the palette (`roi/palette.py`). Grouping by rows / columns is refused while an analysis
runs, like the other commands that restructure the ROI list.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ...roi import RoiToolbox
from ...roi.ordering import grid_band_px, grid_lines
from ...selection import SelectionModule
from .group_controls import GroupControls


class GroupActions(QObject):
    status = pyqtSignal(str)

    def __init__(
        self,
        controls: GroupControls,
        *,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        analysis_running: Callable[[], bool],
        dialog_parent: QWidget | None = None,
        ask_name: Callable[[str, str], str | None] | None = None,
        tell: Callable[[str, str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._toolbox = toolbox
        self._selection = selection
        self._analysis_running = analysis_running
        self._ask_name = ask_name or (lambda title, default: self._dialog_name(dialog_parent, title, default))
        self._tell = tell or (lambda title, text: QMessageBox.warning(dialog_parent, title, text))
        controls.create_requested.connect(self._on_create)
        controls.by_rows_requested.connect(lambda: self._on_by_lines(column_major=False))
        controls.by_columns_requested.connect(lambda: self._on_by_lines(column_major=True))

    @staticmethod
    def _dialog_name(parent: QWidget | None, title: str, default: str) -> str | None:
        name, accepted = QInputDialog.getText(parent, title, "Group name", text=default)
        return name.strip() if accepted and name.strip() else None

    def _refuse(self, message: str) -> None:
        self.status.emit(f"Groups: {message}")
        self._tell("Groups", message)

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
        if self._analysis_running():
            self._refuse("An analysis is running. Wait for it to finish (or cancel it) before grouping the ROIs.")
            return
        selected = {int(i) for i in self._selection.selected_roi_ids()}
        cube = int(self._selection.current_cube())
        rois = [roi for roi in self._toolbox.rois_at(cube) if not selected or roi.area_roi_id in selected]
        if len(rois) < 2:
            self.status.emit("Groups: nothing to group (fewer than two ROIs).")
            return
        lines = grid_lines(
            [roi.area_roi_id for roi in rois],
            [(float(roi.center_x), float(roi.center_y)) for roi in rois],
            grid_band_px([roi.sample_diameter_px for roi in rois]),
            column_major=column_major,
        )
        noun = "Column" if column_major else "Row"
        try:
            self._toolbox.group_rois_each([(f"{noun} {n}", line) for n, line in enumerate(lines, start=1)])
        except (ValueError, KeyError) as error:
            self._refuse(str(error))
            return
        self.status.emit(f"Groups: made {len(lines)} groups, one per {noun.lower()}.")
