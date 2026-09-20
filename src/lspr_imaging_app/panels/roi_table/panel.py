"""``RoiTablePanel`` (sketch §7 "ROI Toolbox" front doors, §10).

Calls ``RoiToolbox``'s command API for anything naturally tabular - rename,
recolor, reorder (the "#" column and Move Up/Down), bulk multi-select
operations - so the user doesn't have to leave the panel they're already
looking at (Image or Sensorgram) just to rename a group. Holds no ROI/group
state or logic of its own - a thin renderer of ``rois()``/``groups()``.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from ...roi import RoiToolbox

_REDRAW_COALESCE_MS = 100  # sketch §8


class RoiTablePanel(QWidget):
    """Tabular ROI/group view and editor - a thin renderer over
    ``RoiToolbox``'s command API."""

    def __init__(self, roi_toolbox: RoiToolbox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._roi_toolbox = roi_toolbox

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

    def _schedule_redraw(self) -> None:
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def _on_rename_group(self, group_id: str, name: str) -> None:
        self._roi_toolbox.rename_group(group_id, name)

    def _on_move_group(self, group_id: str, new_index: int) -> None:
        self._roi_toolbox.reorder_group(group_id, new_index)
