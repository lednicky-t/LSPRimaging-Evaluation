"""Which timeline a newly-authored mask edit lands in: persistent (this cube
and every cube after it) or individual (this exact (cube, wavelength) frame
only).

One tiny shared object, mirroring ``active_tool.py``'s ``ActiveToolModule``
exactly - so the Workflow panel's Mask section
(``panels/workflow/mask_highlight_actions.py``) and the Image panel's own
"Mask" ribbon tab (``panels/image/mask_scope_toggle.py``) show and drive the
same live selection without knowing about each other, the same "one shared
state machine for two visually-separate toolbars" shape ``ActiveToolModule``
already uses for Rotate/Crop/Measure (see that class's own docstring).

Extracted 2026-10-02 (maintainer request: copy the Persistent/Individual
toggle into the Image panel's new "Mask" tab) - before this, the toggle was
``MaskHighlightActions``'s own private ``QButtonGroup``, which a second copy
elsewhere could only have duplicated, not stayed in sync with; two
out-of-sync toggles would have made "which scope does Add/Subtract actually
use" ambiguous, a real correctness risk for where a mask edit lands, not
just a cosmetic mismatch.

Deliberately not undoable, not persisted, and not a ``MaskChange`` -
transient interaction state, same category as ``ActiveToolModule`` (see that
module's own docstring): which scope a *future* edit would land in is not
part of the document; only the edit itself, once actually committed via
``MaskModule.apply_candidate``, is.
"""

from __future__ import annotations

import enum

from PyQt6.QtCore import QObject, pyqtSignal


class MaskScope(enum.Enum):
    PERSISTENT = "persistent"
    INDIVIDUAL = "individual"


class MaskScopeModule(QObject):
    """Holds the current ``MaskScope`` and announces changes."""

    scope_changed = pyqtSignal(object)  # emits the new MaskScope

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scope = MaskScope.PERSISTENT

    def scope(self) -> MaskScope:
        return self._scope

    def set_scope(self, scope: MaskScope) -> None:
        if scope is self._scope:
            return
        self._scope = scope
        self.scope_changed.emit(scope)
