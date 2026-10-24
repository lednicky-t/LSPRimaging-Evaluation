"""``LiveToggle``: the Live / Paused icon button (2026-10-24), the same one on the Analysis tab, the Spectra ribbon and the Sensorgram ribbon.

Shows the state it is in, not the action: a green play triangle = live (the plots follow every change), an amber pause = paused (nothing is
recomputed until it is switched back). A click flips the shared `AnalysisLiveModule`; every toggle bound to the module follows. Display only.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..analysis.live_state import AnalysisLiveModule
from .image.general_group import style_general_icon_button
from .ribbon_icon_size import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
LIVE_TIP = "Live: the plots follow every change you make. Click to pause."
PAUSED_TIP = "Paused: nothing is recomputed, the plots keep what they show. Click to go live again (they catch up once)."


class LiveToggle(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._module: AnalysisLiveModule | None = None
        self.clicked.connect(self._on_clicked)
        self.refresh_theme()

    def bind(self, module: AnalysisLiveModule) -> None:
        self._module = module
        module.live_changed.connect(lambda _live: self._refresh())
        self._refresh()

    def is_live(self) -> bool:
        return self._module is None or self._module.is_live()

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._refresh()

    def _on_clicked(self, _checked: bool = False) -> None:
        if self._module is not None:
            self._module.set_live(not self._module.is_live())

    def _refresh(self) -> None:
        theme = get_active_theme()
        live = self.is_live()
        name, color = ("player-play", theme.accent_green) if live else ("player-pause", theme.accent_gold)
        self.setIcon(load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self.setToolTip(LIVE_TIP if live else PAUSED_TIP)
