"""``RibbonDrawer`` - a ribbon that rolls up to a small "..." handle (2026-10-13 for
the slider ribbon; made general 2026-10-16 so the top tool ribbon uses the same one).

The ribbon is shown next to a handle. Rolled up, only the handle (three dots) is
left, as a thin strip, and a click on it rolls the ribbon down again. The handle
is hidden while the ribbon is shown. The height change is animated
(`set_expanded(..., animate=False)` for tests and restores).

What triggers the roll-up is the owner's business (a double-click on the empty
space of the bar): it calls `set_expanded(False)`. The drawer only does the
rolling and reports `expanded_changed` so the owner can remember the state.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

_QWIDGETSIZE_MAX = 16777215
_HANDLE_WIDTH = 18
_HANDLE_HEIGHT = 14
_ANIMATION_MS = 160


class RibbonDrawer(QWidget):
    """*ribbon* (any widget; it becomes this drawer's child) plus the handle on its
    left. *handle_tooltip* says what the handle shows ("Show the slider ribbon.").
    *handle_alignment* is where the handle sits next to the ribbon while both are
    visible (during the animation); rolled up it is alone in the row."""

    expanded_changed = pyqtSignal(bool)

    def __init__(
        self,
        ribbon: QWidget,
        handle_tooltip: str,
        parent: QWidget | None = None,
        *,
        handle_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignBottom,
    ) -> None:
        super().__init__(parent)
        self.ribbon = ribbon
        self._handle_tooltip = handle_tooltip
        ribbon.setParent(self)
        self._expanded = True
        self._handle = QToolButton(self)
        self._handle.setAutoRaise(True)
        self._handle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._handle.setFixedSize(_HANDLE_WIDTH, _HANDLE_HEIGHT)
        self._handle.setIconSize(QSize(10, 10))
        self._handle.clicked.connect(lambda: self.set_expanded(True))
        self._animation = QPropertyAnimation(ribbon, b"maximumHeight", self)
        self._animation.setDuration(_ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._animation.finished.connect(self._on_animation_finished)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._handle, 0, Qt.AlignmentFlag.AlignLeft | handle_alignment)
        layout.addWidget(ribbon, 1)
        self.refresh_theme()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, *, animate: bool = True) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._animation.stop()
        full = max(self.ribbon.sizeHint().height(), _HANDLE_HEIGHT)
        if expanded:
            self.ribbon.show()
        if animate and self.isVisible():
            self._animation.setStartValue(self.ribbon.height() if self.ribbon.maximumHeight() < _QWIDGETSIZE_MAX else (0 if expanded else full))
            self._animation.setEndValue(full if expanded else 0)
            self._animation.start()
        else:
            self.ribbon.setMaximumHeight(_QWIDGETSIZE_MAX if expanded else 0)
            self.ribbon.setVisible(expanded)
        self._refresh_handle()
        self.expanded_changed.emit(expanded)

    def _on_animation_finished(self) -> None:
        if self._expanded:
            self.ribbon.setMaximumHeight(_QWIDGETSIZE_MAX)  # free again, so a theme / font change can resize it
        else:
            self.ribbon.hide()

    def _refresh_handle(self) -> None:
        theme = get_active_theme()
        self._handle.setIcon(
            load_tabler_icon("dots", color=theme.text_muted, size=20, stroke_width=3.2)
        )
        self._handle.setToolTip(self._handle_tooltip)
        self._handle.setVisible(not self._expanded)

    def refresh_theme(self) -> None:
        """Restyle the handle. The ribbon is themed by its owner."""
        self._handle.setStyleSheet(transparent_icon_button_stylesheet(hover=get_active_theme().control_bg_hover))
        self._refresh_handle()
