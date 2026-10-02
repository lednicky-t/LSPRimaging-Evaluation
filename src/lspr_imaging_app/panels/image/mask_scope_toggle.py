"""Persistent/Individual mask-edit scope toggle (2026-10-02): a two-button
icon-only ``QButtonGroup``, reading and driving the shared ``MaskScopeModule``
(``image_tools/mask_scope.py`` - see that module's docstring for why this
needed to become shared state rather than private widget state). Used in two
places - the Workflow panel's Mask section
(``panels/workflow/mask_highlight_actions.py``) and the Image panel's own
"Mask" ribbon tab (``panel.py``'s ``_build_ui``) - both showing and driving
the one live selection, never owning it themselves.

Icon choice ("stack-3"/"focus-2") and tooltips ported unchanged from
``mask_highlight_actions.py``'s original private buttons, which this class
replaces.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...image_tools.mask_scope import MaskScope, MaskScopeModule

_BUTTON_SIZE = 28
_ICON_SIZE = 22
_RENDER_SIZE = _ICON_SIZE * 2  # rendered at 2x, scaled down - crisper than a native bitmap
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # Crop/Measure's own "tool active" blue, reused for "scope selected"

_ICON_BY_SCOPE = {
    MaskScope.PERSISTENT: "stack-3",  # a layered stack - this cube and every one after it
    MaskScope.INDIVIDUAL: "focus-2",  # one exact frame
}
_TOOLTIP_BY_SCOPE = {
    MaskScope.PERSISTENT: "Persistent scope: new mask edits apply to this cube and every cube after it.",
    MaskScope.INDIVIDUAL: "Individual scope: new mask edits apply to this exact (cube, wavelength) frame only.",
}


def _scope_button(parent: QWidget, scope: MaskScope) -> QToolButton:
    button = QToolButton(parent)
    button.setCheckable(True)
    button.setAutoRaise(True)
    button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
    button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
    button.setToolTip(_TOOLTIP_BY_SCOPE[scope])
    return button


class MaskScopeToggle(QWidget):
    """Persistent/Individual icon pair. Carries no scope state of its own -
    every instance reads and writes the ``MaskScopeModule`` it's given, so
    any number of instances (today: one per place this toggle appears) stay
    in sync for free via that module's own ``scope_changed`` signal, the same
    "second front door onto the same backend" shape this rewrite already
    uses for ``TransformsSection``."""

    def __init__(self, scope_module: MaskScopeModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scope_module = scope_module

        self._persistent_button = _scope_button(self, MaskScope.PERSISTENT)
        self._individual_button = _scope_button(self, MaskScope.INDIVIDUAL)

        self._scope_group = QButtonGroup(self)
        self._scope_group.setExclusive(True)
        self._scope_group.addButton(self._persistent_button)
        self._scope_group.addButton(self._individual_button)

        # Exclusivity means only one toggled signal needs watching: checking
        # Persistent off always means Individual just got checked on, and
        # vice versa - `MaskScopeModule.set_scope`'s own no-op-if-unchanged
        # guard (mirroring `ActiveToolModule._set`) is what stops this from
        # looping back through `_sync_from_module` below.
        self._persistent_button.toggled.connect(self._on_persistent_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._persistent_button)
        layout.addWidget(self._individual_button)

        self._sync_from_module(scope_module.scope())
        self._scope_module.scope_changed.connect(self._sync_from_module)

    def _on_persistent_toggled(self, checked: bool) -> None:
        self._scope_module.set_scope(MaskScope.PERSISTENT if checked else MaskScope.INDIVIDUAL)

    def _sync_from_module(self, scope: MaskScope) -> None:
        button = self._persistent_button if scope is MaskScope.PERSISTENT else self._individual_button
        if not button.isChecked():
            button.setChecked(True)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        theme = get_active_theme()
        for button, scope in ((self._persistent_button, MaskScope.PERSISTENT), (self._individual_button, MaskScope.INDIVIDUAL)):
            color = _ACTIVE_COLOR if button.isChecked() else theme.text_dim
            button.setIcon(load_tabler_icon(_ICON_BY_SCOPE[scope], color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

    def refresh_theme(self, _theme: GuiTheme) -> None:
        for button in (self._persistent_button, self._individual_button):
            button.setStyleSheet(transparent_icon_button_stylesheet())
        self._refresh_icons()
