"""``GeometryModule`` - crop/rotate/flip (sketch §7 "Image Tools", §10).

Owns ``PreprocessingSettings``' spatial fields. Emits ``geometry_changed``
(a computational change - AGENTS.md non-negotiable invariant: rotation/flip/
crop are not display-only, they resample the actual pixel grid every
downstream calculation reads).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented


class GeometryModule(QObject):
    """Owns crop/rotate/flip settings for the active dataset."""

    geometry_changed = pyqtSignal()  # computational - TODO: payload shape (see change_events.py)

    @instrumented("GeometryModule.set_crop")
    def set_crop(self, x: int, y: int, width: int, height: int) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("GeometryModule.set_rotation")
    def set_rotation(self, degrees: float) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("GeometryModule.set_flip")
    def set_flip(self, horizontal: bool, vertical: bool) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError
