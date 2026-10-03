"""The "Edit" group's tool picker (2026-10-02, maintainer request):
"something like dropdown menu but without scroll feature... a pickup menu"
offering the five `MaskEditTool` kinds. A plain `QToolButton` +
`QMenu(InstantPopup)` *is* exactly that in Qt terms - a menu with five
entries never needs to scroll, so no extra "no-scroll" handling is needed;
asking for one is really asking not to reach for a scrollable list widget
(`QComboBox`) for a five-item choice.

The button's own face always shows the currently-picked tool's icon (the
same "icon face reflects current state" convention `MaskScopeToggle`'s
buttons and `MaskOverlayControls`'s toggle already use), and the menu marks
the current pick with a native checkmark (`QAction.setCheckable`) rather
than a second, redundant visual cue.

Real, directly-callable `QAction`s (`.trigger()`), matching CLAUDE.md's GUI-
testability rule - no screen coordinates needed to drive this from a test,
same as every other picker/toggle in this ribbon.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import GuiTheme, transparent_icon_button_stylesheet

from ...image_tools import MaskEditTool, MaskEditToolModule
from . import mask_edit_tool_icons as icons

_BUTTON_SIZE = 28  # matches this ribbon's other icon buttons (MaskScopeToggle, MaskOverlayControls)
_ICON_SIZE = 22

# Ordered exactly as the maintainer listed them. Each entry: the icon
# function (mask_edit_tool_icons.py) and the label shown in the menu and as
# the button's tooltip.
_TOOL_SPECS: tuple[tuple[MaskEditTool, str, str], ...] = (
    (MaskEditTool.HISTOGRAM_SELECTION, "Histogram selection", "histogram_selection_icon"),
    (MaskEditTool.THRESHOLD, "Threshold", "threshold_icon"),
    (MaskEditTool.LOCAL_CONTRAST, "Local contrast", "local_contrast_icon"),
    (MaskEditTool.MORPHOLOGY, "Morphology", "morphology_icon"),
    (MaskEditTool.DRAW, "Draw", "draw_icon"),
)


def _icon_for(tool: MaskEditTool):
    for spec_tool, _label, icon_fn_name in _TOOL_SPECS:
        if spec_tool is tool:
            return getattr(icons, icon_fn_name)()
    raise ValueError(f"no icon registered for {tool!r}")


def _label_for(tool: MaskEditTool) -> str:
    for spec_tool, label, _icon_fn_name in _TOOL_SPECS:
        if spec_tool is tool:
            return label
    raise ValueError(f"no label registered for {tool!r}")


class MaskEditToolPicker(QToolButton):
    """A five-option icon picker for `MaskEditToolModule` - the Edit group's
    "which mask-editing tool is active" selector."""

    def __init__(self, tool_module: MaskEditToolModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool_module = tool_module
        self.setAutoRaise(True)
        self.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self.setStyleSheet(transparent_icon_button_stylesheet())
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self._menu = QMenu(self)
        self._actions: dict[MaskEditTool, QAction] = {}
        for tool, label, icon_fn_name in _TOOL_SPECS:
            action = QAction(getattr(icons, icon_fn_name)(), label, self._menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, picked=tool: self._tool_module.set_tool(picked))
            self._menu.addAction(action)
            self._actions[tool] = action
        self.setMenu(self._menu)

        self._sync_from_module(tool_module.tool())
        tool_module.tool_changed.connect(self._sync_from_module)

    def _sync_from_module(self, tool: MaskEditTool) -> None:
        for spec_tool, action in self._actions.items():
            action.setChecked(spec_tool is tool)
        self.setIcon(_icon_for(tool))
        self.setToolTip(f"Mask editing tool: {_label_for(tool)}")

    def refresh_theme(self, _theme: GuiTheme) -> None:
        """Called by `panel.py` on every live theme switch - the icons
        themselves are static (see mask_edit_tool_icons.py's module
        docstring), only the button chrome needs re-applying."""
        self.setStyleSheet(transparent_icon_button_stylesheet())
