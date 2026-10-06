"""The "General" ribbon group (2026-10-03, maintainer request): a small,
always-visible, left-aligned group beside the ribbon tabs (not inside any
tab) holding tools that apply whichever tab is open - the cursor-readout
toggle and the area-selection picker (All / Rectangle / Lasso).

`AreaSelectionPicker` copies `MaskEditToolPicker`'s design on purpose
(maintainer: "design similar to mask tools"): one icon button whose face
shows the current pick, an instant-popup menu with a checkmark on it.

**Picker semantics.**
- *All* - nothing restricts; clears any selection and leaves the draw tool.
- *Rectangle* / *Lasso* - arms the drag tool (`ImageTool.SELECT_AREA`). The
  drawn selection then persists while another tool is used; the picker
  keeps showing its shape. Re-picking the same entry re-arms the draw tool
  (the user may have switched to Add ROI in between), which is why
  `set_active` is called on every pick even when the mode did not change.
- The face is tinted with the ribbon's tool-blue while the draw tool is on.

Real `QAction`s (`.trigger()`), per the GUI-testability rule.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...image_tools import ActiveToolModule, ImageTool
from ...selection import AreaSelectionMode, AreaSelectionModule

BUTTON_SIZE = 28  # same as the Mask tab's icon buttons (MaskEditToolPicker, MaskScopeToggle)
ICON_SIZE = 22
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue (canvas_tools._ADD_ROI_ACTIVE_COLOR)

_SPECS: tuple[tuple[AreaSelectionMode, str, str], ...] = (
    (AreaSelectionMode.ALL, "All (no selection)", "select-all-area"),
    (AreaSelectionMode.RECTANGLE, "Rectangular selection", "select-rectangle"),
    (AreaSelectionMode.LASSO, "Lasso (free-draw) selection", "select-lasso"),
)


def style_general_icon_button(button: QToolButton) -> None:
    """The ribbon's 28px icon-button look, for any button in this group (the
    cursor toggle is restyled through it so it matches the picker)."""
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())


def mirror_icon_button(source: QToolButton, parent: QWidget) -> QToolButton:
    """A second button that follows *source* (checked state, icon, tooltip,
    enabled) and forwards its clicks to it, so the same control can sit on
    two ribbon tabs. Call the returned button's `sync()` after *source*
    changes its look (icon/tooltip/enabled are not signals)."""
    mirror = QToolButton(parent)
    mirror.setCheckable(source.isCheckable())

    def sync() -> None:
        style_general_icon_button(mirror)
        blocked = mirror.blockSignals(True)
        try:
            mirror.setChecked(source.isChecked())
        finally:
            mirror.blockSignals(blocked)
        mirror.setIcon(source.icon())
        mirror.setToolTip(source.toolTip())
        mirror.setEnabled(source.isEnabled())

    mirror.clicked.connect(lambda _checked=False: source.click())
    source.toggled.connect(lambda _checked: sync())
    mirror.sync = sync  # type: ignore[attr-defined]
    sync()
    return mirror


class AreaSelectionPicker(QToolButton):
    """All / Rectangle / Lasso picker for `AreaSelectionModule`."""

    def __init__(
        self, selection: AreaSelectionModule, active_tool: ActiveToolModule, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._selection = selection
        self._active_tool = active_tool
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self._menu = QMenu(self)
        self._actions: dict[AreaSelectionMode, QAction] = {}
        for mode, label, icon_name in _SPECS:
            action = QAction(
                load_tabler_icon(icon_name, color=get_active_theme().text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH),
                label,
                self._menu,
            )
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, picked=mode: self._pick(picked))
            self._menu.addAction(action)
            self._actions[mode] = action
        self.setMenu(self._menu)

        self._refresh()
        selection.mode_changed.connect(self._refresh)
        active_tool.active_tool_changed.connect(self._refresh)

    def _pick(self, mode: AreaSelectionMode) -> None:
        self._selection.set_mode(mode)  # ALL also clears the selection
        if mode is AreaSelectionMode.ALL:
            self._active_tool.set_active(ImageTool.SELECT_AREA, False)
        else:
            self._active_tool.set_active(ImageTool.SELECT_AREA, True)
        self._refresh()

    def _refresh(self, *_args: object) -> None:
        mode = self._selection.mode()
        for spec_mode, action in self._actions.items():
            action.setChecked(spec_mode is mode)
        icon_name = next(name for spec_mode, _label, name in _SPECS if spec_mode is mode)
        label = next(text for spec_mode, text, _name in _SPECS if spec_mode is mode)
        armed = self._active_tool.active() is ImageTool.SELECT_AREA
        color = _ACTIVE_COLOR if armed else get_active_theme().text_primary
        self.setIcon(load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self.setToolTip(f"Area selection: {label}. Limits the editing tools (and the Histogram plot) to the selected area.")

    def refresh_theme(self, _theme: GuiTheme) -> None:
        self.setStyleSheet(transparent_icon_button_stylesheet())
        self._refresh()
