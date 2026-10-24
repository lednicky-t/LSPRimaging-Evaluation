"""The launch screen and the launch's stage timing (2026-10-24, docs/startup_gui_thread_audit_2026-10-24.md).

Before this, nothing was on screen for ~8 s after starting the app: ~3.5 s importing the app's modules, then ~4.3 s building every panel,
and the window is shown only when that is done. `LaunchSplash` is a small window shown **before** the heavy imports, so there is something
to look at (and a message saying what is going on) from the first second. It does not make the launch faster, only less empty.

`StageClock` is the other half: it times each step of `build_main_window` (one `app.build_stage` DEBUG event per step, field `ms`), which
is how we learn what the 4.3 s consists of (CLAUDE.md: slow-feeling code gets stage timing when it is written). It also feeds the splash
its message. Read the result with `python -m lspr_imaging_app.applog grep FILE.jsonl app.build_stage`.

**Import weight matters here:** this module is imported before the app's own modules, so it may import only PyQt6 and the (light) logging
helpers, nothing from `app_rewrite`, `lspr_ui` or the panels. The colours below are therefore written out, not taken from the theme.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen, QWidget

from .applog.events import log_event

logger = logging.getLogger(__name__)

_WIDTH, _HEIGHT = 460, 150
_BACKGROUND = "#1e2227"  # close to the dark theme's window colour; the splash is dark in both themes (it is shown before the theme is read)
_TITLE_COLOR = "#e6e9ee"
_MESSAGE_COLOR = "#9aa4b2"
_ACCENT = "#4aa3ff"


class LaunchSplash:
    """Dark card with the app name and one line saying what the launch is doing. Never raises: a launch screen is a courtesy, so any
    problem creating or updating it just means there is none."""

    def __init__(self, title: str = "LSPR Imaging") -> None:
        self._splash: QSplashScreen | None = None
        try:
            pixmap = QPixmap(_WIDTH, _HEIGHT)
            pixmap.fill(QColor(_BACKGROUND))
            painter = QPainter(pixmap)
            painter.setPen(QColor(_ACCENT))
            painter.drawRect(0, 0, _WIDTH - 1, _HEIGHT - 1)
            painter.setPen(QColor(_TITLE_COLOR))
            title_font = QFont()
            title_font.setPointSize(18)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.drawText(24, 20, _WIDTH - 48, 60, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), title)
            painter.end()
            self._splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
            self._splash.setFont(QFont())
        except Exception:  # noqa: BLE001 - see the class docstring
            logger.debug("The launch screen could not be created", exc_info=True)
            self._splash = None

    def show(self, message: str) -> None:
        """Show the card (first call) and set its message; drawn at once, even though the event loop is not running yet."""
        splash = self._splash
        if splash is None:
            return
        try:
            splash.showMessage(
                f"  {message}", int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom), QColor(_MESSAGE_COLOR)
            )
            if not splash.isVisible():
                splash.show()
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()  # once, so the native window appears; later messages repaint themselves (showMessage)
        except Exception:  # noqa: BLE001
            logger.debug("The launch screen could not be updated", exc_info=True)

    def finish(self, window: QWidget) -> None:
        """Close the card once `window` is on screen."""
        splash, self._splash = self._splash, None
        if splash is not None:
            splash.finish(window)


class StageClock:
    """Times the steps of the launch. `done("name")` logs how long the step that just ended took (`app.build_stage`, DEBUG) and, with
    `next_message`, tells the launch screen what comes next. Both are optional: with no `progress` callable it only times."""

    def __init__(self, progress: Callable[[str], None] | None = None) -> None:
        self._progress = progress
        self._start = self._last = time.perf_counter()

    def done(self, stage: str, next_message: str | None = None) -> None:
        now = time.perf_counter()
        log_event(logger, "app.build_stage", logging.DEBUG, stage=stage, ms=round((now - self._last) * 1000))
        self._last = now
        if next_message is not None and self._progress is not None:
            self._progress(next_message)

    def finish(self) -> None:
        log_event(logger, "app.build_stage", logging.DEBUG, stage="total", ms=round((time.perf_counter() - self._start) * 1000))
