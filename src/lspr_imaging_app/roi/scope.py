"""Which timeline a ROI geometry edit lands in: Persistent or Individual (the ROI twin of `image_tools/mask_scope.py`).

`RoiScopeModule` is the one shared, transient piece of interaction state (not undoable, not part of the document):
the ribbon toggle shows and drives it. `RoiEditTarget` turns "the scope plus the cube being viewed" into the keyword
arguments the `RoiToolbox` geometry commands take, so every front door (canvas gestures, table, Array actions) edits
the same place.

**Persistent edit on the first cube** is written as an edit of the ROI's *base* geometry (no ``cube``): "this cube and
every later cube" is then every cube, which is exactly what a ROI edit always was. A timeline appears only when the
user edits at a later cube or uses Individual. Design: docs/roi_timeline_design_2026-10-08.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from .model import GEOMETRY_SCOPES, SCOPE_INDIVIDUAL, SCOPE_PERSISTENT

__all__ = ["RoiEditTarget", "RoiScopeModule", "SCOPE_INDIVIDUAL", "SCOPE_PERSISTENT"]


class RoiScopeModule(QObject):
    scope_changed = pyqtSignal(str)  # the new scope

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scope = SCOPE_PERSISTENT

    def scope(self) -> str:
        return self._scope

    def set_scope(self, scope: str) -> None:
        if scope not in GEOMETRY_SCOPES:
            raise ValueError(f"scope must be one of {GEOMETRY_SCOPES}, got {scope!r}")
        if scope == self._scope:
            return
        self._scope = scope
        self.scope_changed.emit(scope)


class RoiEditTarget:
    """The place a geometry edit goes: the shared scope + the cube being viewed."""

    def __init__(
        self,
        scope: RoiScopeModule,
        current_cube: Callable[[], int],
        first_cube: Callable[[], int | None],
    ) -> None:
        self._scope = scope
        self._current_cube = current_cube
        self._first_cube = first_cube

    def scope(self) -> str:
        return self._scope.scope()

    def cube(self) -> int:
        """The cube whose geometry is shown and edited."""
        return int(self._current_cube())

    def kwargs(self) -> dict:
        """Keyword arguments for the `RoiToolbox` geometry commands (``cube`` / ``scope``); empty = edit the base."""
        cube = self.cube()
        first = self._first_cube()
        if self._scope.scope() == SCOPE_PERSISTENT and (first is None or cube <= int(first)):
            return {}
        return {"cube": cube, "scope": self._scope.scope()}
