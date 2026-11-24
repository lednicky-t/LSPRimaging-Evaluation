"""Esc leaves the active canvas tool (2026-11-19, maintainer: "make every tool exit by Esc, add it as a shortcut").

Every canvas tool is an `ImageTool` of the shared `ActiveToolModule`: Rotate, Crop, Measure, and SELECT_AREA, which is the whole selection toolbar
(Rectangle, Lasso, Wand, Draw, Histogram range, Threshold, Local contrast). One key, ``tools.cancel`` (`shortcuts.py`), leaves whichever is on.

**Esc in two steps.** The first Esc drops what the tool is in the middle of (Rotate's / Measure's first point, Crop's edit that is not applied yet:
`CanvasInteraction.handle_key`, which did that on its own before); the next Esc leaves the tool (`ActiveToolModule.set_active(tool, False)`: the same as the
right-click menu's "Exit tool" / "Cancel ...", and as a second click on the tool's button). A tool with nothing in progress (every selection tool)
is left by the first Esc. Leaving a tool never changes the image or the selection (the selection stays); a selection drag that is in progress is dropped
(`ImagePanel._on_active_tool_changed` cancels the gesture).

**Where it works.** The action lives on the Image panel with the `WidgetWithChildrenShortcut` context: from the image, the selection toolbar or any ribbon
control of the panel, not from another panel (the ROI table has its own Esc). A menu of the panel that is open takes the Esc (it closes). The action is
**disabled while no tool is on**, so then Esc reaches whatever has focus as it always did, and **while a text field has focus** (a spin box, a line edit): Esc
there belongs to the field (it puts the old value back), it must not also switch the tool off under the user's hands.

Display only: no state of its own, the tool state is `ActiveToolModule`'s.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from ...image_tools import ActiveToolModule
from ...shortcuts import bind_action

_TEXT_FIELDS = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)


class ToolEscape(QObject):
    def __init__(
        self,
        active_tool: ActiveToolModule,
        handle_key: Callable[[int, Qt.KeyboardModifier], bool],
        *,
        action_parent: QWidget,
    ) -> None:
        """``handle_key(key, modifiers)``: what the tools do with a key (`CanvasInteraction.handle_key`; True when a tool used it, which for Esc means it dropped
        something it was doing). ``action_parent``: the widget the action lives on (the Image panel)."""
        super().__init__(action_parent)
        self._active_tool = active_tool
        self._handle_key = handle_key
        self.action = QAction("Exit tool", action_parent)
        bind_action(self.action, "tools.cancel", context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.action.triggered.connect(lambda _checked=False: self.escape())
        action_parent.addAction(self.action)
        active_tool.active_tool_changed.connect(self._sync)
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._sync)  # (a QObject receiver: the connection goes when the panel does)
        self._sync()

    def _sync(self, *_args: object) -> None:
        """Enabled while a tool is on and no text field has focus (see the module docstring)."""
        self.action.setEnabled(self._active_tool.active() is not None and not isinstance(QApplication.focusWidget(), _TEXT_FIELDS))

    def escape(self) -> bool:
        """One Esc: the tool drops what it was doing, or, with nothing in progress, the active tool is left. Returns whether anything was done."""
        if self._handle_key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier):
            return True
        tool = self._active_tool.active()
        if tool is None:
            return False
        self._active_tool.set_active(tool, False)
        return True
