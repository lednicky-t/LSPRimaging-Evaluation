"""The Analysis tab's Mode pick-up: Normal or Performance (2026-10-27, maintainer request; the mode may get more settings later).

Screen purpose: choose how the analysis scan gets its worker processes.

- **Normal**: a scan starts its workers and closes them when it ends. Nothing is held while you are not scanning.
- **Performance**: the workers are kept ready between scans, so a scan (typically a Fast scan after moving one ROI) starts at once instead
  of after several seconds. They are started when the mode is switched on and stay until it is switched off or the app quits; each holds about 150 MB
  while idle. What the mode means for the processes is ``analysis/scan_pool.py``.

Layout: one icon button in the ribbon, captioned "Mode" (the tab adds the caption). Its face is the current mode: a bolt in amber for
Performance, a struck-through bolt in the dim colour for Normal. An instant-popup menu with a checkmark on the current entry, same design as
the other pick-up menus of this ribbon (`ArrayModePicker`); real `QAction`s, so tests can `.trigger()` them.

States the tooltip tells: Normal; Performance with the workers starting; Performance ready (how many workers, roughly how much memory);
Performance whose workers could not be started (scans then start their own, as in Normal). The tab disables the button while a scan runs.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.scan_pool import IDLE_WORKER_MB, MODE_NORMAL, MODE_PERFORMANCE, MODES
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
PERFORMANCE_COLOR = "#f59e0b"  # amber: the mode that holds resources is the one that stands out

LABELS = {MODE_NORMAL: "Normal", MODE_PERFORMANCE: "Performance"}
MENU_TEXT = {
    MODE_NORMAL: "Normal: workers start for each scan",
    MODE_PERFORMANCE: "Performance: workers stay ready between scans",
}
DESCRIPTIONS = {
    MODE_NORMAL: "a scan starts its worker processes and closes them when it ends; nothing is kept while you are not scanning",
    MODE_PERFORMANCE: (
        f"the worker processes are kept ready between scans, so a scan starts at once instead of after several seconds (a Fast scan after "
        f"moving a ROI is mostly that wait); they are started when you switch this on and stay until you switch it off or quit; each holds "
        f"about {IDLE_WORKER_MB} MB while idle and the processor works hard while they start"
    ),
}


def pool_state_text(state: str, workers: int) -> str:
    """One line about the worker pool of Performance mode, for the tooltip and the tab's text."""
    if state == "starting":
        return f"Starting {workers} worker processes..." if workers else "Starting the worker processes..."
    if state == "ready":
        return f"Ready: {workers} worker processes are running (about {workers * IDLE_WORKER_MB} MB while idle)."
    if state == "failed":
        return "The worker processes could not be started; scans start their own, as in Normal mode."
    return ""


class AnalysisModePicker(QToolButton):
    mode_changed = pyqtSignal(str)  # "normal" | "performance"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = MODE_NORMAL
        self._pool_state = ""
        self._pool_workers = 0
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)
        group = QActionGroup(self)
        self._actions: dict[str, QAction] = {}
        for mode in MODES:
            action = QAction(MENU_TEXT[mode], group)
            action.setCheckable(True)
            action.setToolTip(DESCRIPTIONS[mode][0].upper() + DESCRIPTIONS[mode][1:] + ".")
            action.triggered.connect(lambda _checked=False, picked=mode: self.set_mode(picked))
            self._menu.addAction(action)
            self._actions[mode] = action
        self.setMenu(self._menu)
        self.refresh_theme()

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, *, announce: bool = True) -> None:
        """Pick a mode (an unknown name is ignored). Emits ``mode_changed`` when it changes and ``announce`` is true (a remembered mode that is
        put back at launch passes False: the owner sets the task itself)."""
        if mode not in self._actions:
            return
        changed = mode != self._mode
        self._mode = mode
        if mode != MODE_PERFORMANCE:
            self._pool_state, self._pool_workers = "", 0
        self._refresh()
        if changed and announce:
            self.mode_changed.emit(mode)

    def set_pool_state(self, state: str, workers: int = 0) -> None:
        """What the worker pool of Performance mode is doing ("starting", "ready", "failed", "off"); shown in the tooltip."""
        self._pool_state, self._pool_workers = ("" if state == "off" else state), int(workers)
        self._refresh()

    def pool_state_line(self) -> str:
        return pool_state_text(self._pool_state, self._pool_workers) if self._mode == MODE_PERFORMANCE else ""

    def tooltip_text(self) -> str:
        lines = [f"Analysis mode: {LABELS[self._mode]}. {DESCRIPTIONS[self._mode][0].upper()}{DESCRIPTIONS[self._mode][1:]}."]
        state = self.pool_state_line()
        if state:
            lines.append(state)
        other = MODE_NORMAL if self._mode == MODE_PERFORMANCE else MODE_PERFORMANCE
        lines.append(f"Click to choose {LABELS[other]}.")
        return "\n\n".join(lines)

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        theme = get_active_theme()
        for mode, action in self._actions.items():
            action.setIcon(self._icon(mode, theme))
        self._refresh()

    def _icon(self, mode: str, theme) -> QIcon:
        if mode == MODE_PERFORMANCE:
            return load_tabler_icon("bolt", color=PERFORMANCE_COLOR, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        return load_tabler_icon("bolt-off", color=theme.text_dim, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)

    def _refresh(self) -> None:
        self._actions[self._mode].setChecked(True)
        self.setIcon(self._icon(self._mode, get_active_theme()))
        self.setToolTip(self.tooltip_text())
