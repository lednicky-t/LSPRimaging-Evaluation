"""The Sensorgram ribbon's "Checks" group (2026-11-07): one button that opens the data-checks report and says at a glance whether the data look fine.

Screen purpose: tell the user when the data behave systematically wrong (a noisy step, a drifting step, a method that wastes signal, ROIs that change pixels ...), without
asking them to know where to look. The checks themselves are `analysis/step_diagnostics.py`; the report is `panels/check_report_window.py`.

Layout: ONE icon button (a check box) on the Experimental plan tab, after "Step stats", with a small status dot at its lower right corner:

- grey: not run yet, or out of date (something the checks use changed since the last run);
- blue: running; green: the last run found nothing to warn about; amber: at least one warning; red: the last run failed.

A click emits ``open_requested``: the presenter (`checks_presenter.py`) opens the report window and runs the checks when the report is not current. Display only: the group
holds no choice and runs nothing. ``set_state`` tells it what to show; ``set_available`` greys it out when the dataset has no experiment steps (the tooltip says why).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QPointF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.step_diagnostics import WARN
from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_DOT_FRACTION = 0.2  # the dot's radius, as a share of the icon's size
NO_STEPS_TIP = "This dataset has no experiment steps, so there is nothing to check. Import the measurement notes (and the pump plan) in Workflow > Experimental plan."
BASE_TIP = (
    "Checks: look for things that behave systematically wrong in the data (a noisy or drifting step, a method that wastes signal, ROIs that change pixels, uneven signal across "
    "the image ...). Opens the report; it checks the data now unless the last run is still current."
)
STATE_TIPS = {
    "not_run": "Not run yet.",
    "running": "Checking the data now ...",
    "stale": "The last run is out of date: something the checks use has changed since.",
    "failed": "The last run failed. Open the report to see why.",
}


class ChecksControls(QObject):
    open_requested = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._status = "not_run"
        self._level: str | None = None
        self._summary = ""
        self._available = False
        self._button = QToolButton(parent)
        self._button.clicked.connect(lambda _checked=False: self.open_requested.emit())
        self.refresh_theme()
        self.set_available(False)

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._button,)

    def button(self) -> QToolButton:
        return self._button

    def set_available(self, available: bool) -> None:
        self._available = bool(available)
        self._button.setEnabled(self._available)
        self._tip()

    def is_available(self) -> bool:
        return self._available

    def set_state(self, status: str, level: str | None, summary: str = "") -> None:
        """``status``: the module's (not_run / running / current / stale / failed); ``level``: ``warn`` or ``ok`` for a report (None: no report); ``summary``: e.g. "2 warnings"."""
        if (status, level, summary) == (self._status, self._level, self._summary):
            return  # the module reports on every settings change; the icon is only redrawn when the dot or the tooltip would differ
        self._status, self._level, self._summary = status, level, summary
        self._button.setIcon(self._icon())
        self._tip()

    def dot_name(self) -> str:
        """The colour of the dot as a word (what the tests and the tooltip use): grey, blue, green, amber or red."""
        if self._status == "running":
            return "blue"
        if self._status == "failed":
            return "red"
        if self._status == "current":
            return "amber" if self._level == WARN else "green"
        return "grey"

    def refresh_theme(self) -> None:
        style_general_icon_button(self._button)
        self._button.setIcon(self._icon())

    def _tip(self) -> None:
        if not self._available:
            self._button.setToolTip(NO_STEPS_TIP)
            return
        if self._status == "current":
            state = f"Last run: {self._summary or ('warnings found' if self._level == WARN else 'nothing to warn about')}."
        else:
            state = STATE_TIPS.get(self._status, "")
        self._button.setToolTip(f"{BASE_TIP}\n\n{state}" if state else BASE_TIP)

    def _icon(self) -> QIcon:
        theme = get_active_theme()
        base = load_tabler_icon("checkbox", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        pixmap = base.pixmap(QSize(_RENDER_SIZE, _RENDER_SIZE))
        colour = {"blue": theme.accent_blue, "red": theme.accent_red, "green": theme.accent_green, "amber": theme.accent_gold, "grey": theme.text_dim}[self.dot_name()]
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = _DOT_FRACTION * pixmap.width()
        centre = QPointF(pixmap.width() - radius - 1, pixmap.height() - radius - 1)
        painter.setPen(QPen(QColor(theme.window_bg), max(1.0, radius * 0.35)))  # a ring in the background colour keeps the dot apart from the icon's strokes
        painter.setBrush(QColor(colour))
        painter.drawEllipse(centre, radius, radius)
        painter.end()
        return QIcon(pixmap)
