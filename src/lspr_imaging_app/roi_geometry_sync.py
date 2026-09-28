"""``RoiGeometrySync`` - keeps ROI positions aligned with rotation/flip/crop.

Closes `docs/image_tools_coordinate_spaces.md`'s "known gap": changing
rotation/flip/crop used to leave every ROI's stored `center_x`/`center_y`
(and a freeform ROI's raster) pointing at the *old* processed-space pixels,
silently. This is the coordinator the maintainer asked for (2026-09-28
design discussion) - it listens to `GeometryModule` and calls
`RoiToolbox.remap_all`, so a rotation/flip/crop edit moves existing ROIs
with it instead of leaving them behind.

**Deliberately its own top-level module, not inside `image_tools/` or
`roi/`.** `roi/rasterize.py` already imports from `image_tools.*` (a real,
one-directional dependency: ROI rasterization needs Chromatic's/Geometry's
affine math). Putting this coordinator inside either package and having it
import the other would add the reverse edge, turning that one-directional
relationship into a two-way one between the packages. Living here instead,
it imports both `roi` and `image_tools`/`dataset` freely, and neither of
them imports it back - the same reasoning that keeps `GeometryModule`
itself with zero knowledge that `RoiToolbox` exists (AGENTS.md's module-
boundary rule; see `RoiToolbox.remap_all`'s own docstring for the matching
half of this).

**Transient wiring, not domain state** - much like `ActiveToolModule`, this
holds no state anything else needs to read (its private "last known
settings"/raw-shape cache exists purely so it can compute a delta on the
*next* geometry change, nothing more), and it is rebuilt fresh, not
persisted, on every app start.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from .dataset import DatasetModule
from .image_tools import GeometryModule
from .image_tools.geometry.model import GeometryComputationalChange, GeometrySettings
from .image_tools.geometry.transform import remap_point_for_geometry_change
from .roi.model import RoiMask
from .roi.rasterize import remap_roi_mask
from .roi.toolbox import RoiRemapReport, RoiToolbox

logger = logging.getLogger(__name__)

# The only `GeometryComputationalChange.reason` values that actually move a
# pixel's meaning (see that dataclass's own docstring for the full list).
# "image_tools_enabled" toggles whether geometry is applied at all, without
# changing any of rotation_angle_deg/flip_*/crop; "rotation_fill" only
# changes what color a synthesized corner pixel gets. Neither moves an
# existing ROI. "session_restored" is excluded separately (see
# `_on_geometry_changed`) - it is not one of the reasons a live edit uses,
# but `GeometryModule.restore_settings` does emit it.
_REMAPPED_REASONS = frozenset({"rotation", "flip", "crop"})


class RoiGeometrySync(QObject):
    """Wired once in `app_rewrite.build_main_window`, alongside
    `ActiveToolModule`. See module docstring."""

    # A human-readable summary of what the last remap did (or didn't do) -
    # for a status bar/log line. Empty string means nothing happened.
    status_changed = pyqtSignal(str)

    def __init__(
        self,
        geometry: GeometryModule,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._roi_toolbox = roi_toolbox
        self._dataset = dataset
        # The baseline every future delta is computed against - must be
        # updated on *every* geometry_changed (not just the ones that get
        # remapped), or a later remapped edit would compute its delta
        # against a stale, multiple-edits-ago baseline instead of the one
        # right before it.
        self._last_settings: GeometrySettings = geometry.settings()
        self._raw_shape: tuple[int, int] | None = dataset.raw_plane_shape()

        dataset.dataset_loaded.connect(self._on_dataset_loaded)
        dataset.dataset_cleared.connect(self._on_dataset_cleared)
        geometry.geometry_changed.connect(self._on_geometry_changed)

    def _on_dataset_loaded(self, _dataset: object) -> None:
        self._raw_shape = self._dataset.raw_plane_shape()
        # A dataset that did not exist a moment ago has nothing to remap
        # against; re-baseline rather than let the next edit diff against
        # whatever the *previous* dataset's geometry happened to be.
        self._last_settings = self._geometry.settings()

    def _on_dataset_cleared(self) -> None:
        self._raw_shape = None
        self._last_settings = self._geometry.settings()

    def _on_geometry_changed(self, change: GeometryComputationalChange) -> None:
        old_settings = self._last_settings
        new_settings = self._geometry.settings()
        self._last_settings = new_settings

        if change.reason not in _REMAPPED_REASONS:
            # Includes "session_restored": a loaded session's ROIs were
            # saved alongside that same geometry and are already consistent
            # with it - remapping them again would corrupt them, the same
            # reasoning `GeometryModule.restore_settings` itself documents
            # for why a restore isn't undo-tracked either.
            return
        if self._raw_shape is None:
            logger.warning("Geometry changed (%s) with no dataset loaded - nothing to remap.", change.reason)
            return
        raw_shape = self._raw_shape

        def remap_point(x: float, y: float) -> tuple[float, float]:
            return remap_point_for_geometry_change((x, y), raw_shape, old_settings, new_settings)

        def remap_mask(roi_mask: RoiMask) -> RoiMask | None:
            return remap_roi_mask(roi_mask, raw_shape, old_settings, new_settings)

        report = self._roi_toolbox.remap_all(remap_point, remap_mask)
        if report.remapped_roi_ids:
            self.status_changed.emit(_status_text(change.reason, report))


def _status_text(reason: str, report: RoiRemapReport) -> str:
    count = len(report.remapped_roi_ids)
    text = f"{count} ROI{'s' if count != 1 else ''} repositioned for the new {reason}."
    if report.mask_shapes_lost:
        text += f" {len(report.mask_shapes_lost)} mask-shaped ROI region(s) could not be repositioned - check them."
    if report.unsupported_shapes_skipped:
        text += f" {len(report.unsupported_shapes_skipped)} ROI(s) have a shape this app can't yet reposition automatically."
    return text
