"""``RoiToolbox`` (sketch §7 "ROI Toolbox", §10).

Owns ``AreaRoi``/``AreaRoiGroup``/``RoiArrayGroup`` and detection settings -
the single owner of every invariant (ROI IDs, "at most one group per ROI",
ordering). Meant to be called from multiple UI surfaces (Workflow panel's
Finding/Editing/Groups sub-tabs, the Image panel, the ROI/Group table panel)
- that's intended duplication of *affordances*, not of logic (sketch §7,
"Revised 2026-09-20: one backend, several front doors").
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..change_events import RoiComputationalChange, RoiCosmeticChange
from ..diagnostics import instrumented
from .model import AreaRoi, AreaRoiGroup, RoiArrayGroup


class RoiToolbox(QObject):
    """Owns ROI/group state; exposes a query interface plus the full
    command API. No other module or panel may hold its own copy of ROI or
    group state (AGENTS.md, "What NOT to do without checking in again first")."""

    geometry_changed = pyqtSignal(RoiComputationalChange)
    cosmetic_changed = pyqtSignal(RoiCosmeticChange)
    selection_changed = pyqtSignal(set)  # set[int] currently selected roi_ids

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rois: dict[int, AreaRoi] = {}
        self._groups: dict[int, AreaRoiGroup] = {}
        self._array_groups: dict[int, RoiArrayGroup] = {}

    # -- query interface ------------------------------------------------

    def rois(self) -> tuple[AreaRoi, ...]:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def roi_by_id(self, roi_id: int) -> AreaRoi:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def array_groups(self) -> tuple[RoiArrayGroup, ...]:
        """Regular-array ROI-generation recipes (sketch §7: "Owns... RoiArrayGroup
        recipes"). Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def groups(self) -> tuple[AreaRoiGroup, ...]:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def display_position(self, roi_id: int, image_key: object) -> tuple[float, float]:
        """Resolve ``roi_id``'s position in ``image_key``'s display space,
        folding in Chromatic's ``affine_for`` internally - callers never
        compose the chromatic transform themselves (sketch §7). Not yet
        implemented - scaffolding only."""
        raise NotImplementedError

    # -- command API (§7) -------------------------------------------------

    @instrumented("RoiToolbox.move_roi")
    def move_roi(self, roi_id: int, x: float, y: float) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.resize_roi")
    def resize_roi(self, roi_id: int, *args: object, **kwargs: object) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.delete_roi")
    def delete_roi(self, roi_id: int) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.detect_rois")
    def detect_rois(self, *args: object, **kwargs: object) -> list[int]:
        """Run detection and add the resulting ROIs. Not yet implemented -
        scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.create_group")
    def create_group(self, name: str) -> int:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.rename_group")
    def rename_group(self, group_id: int, name: str) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.recolor_group")
    def recolor_group(self, group_id: int, color: object) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.reorder_group")
    def reorder_group(self, group_id: int, new_index: int) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.add_to_group")
    def add_to_group(self, roi_id: int, group_id: int) -> None:
        """Enforces "at most one group per ROI". Not yet implemented -
        scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.remove_from_group")
    def remove_from_group(self, roi_id: int, group_id: int) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("RoiToolbox.set_selection")
    def set_selection(self, roi_ids: set[int]) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    # -- request methods (other modules/panels ask; this module decides) ---

    def request_move(self, roi_id: int, x: float, y: float) -> None:
        """Called by the Image panel to forward a drag gesture - the Image
        panel never mutates ROI state directly (sketch §7). Not yet
        implemented - scaffolding only."""
        raise NotImplementedError
