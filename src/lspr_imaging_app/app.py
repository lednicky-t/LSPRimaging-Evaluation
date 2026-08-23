from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, Qt, QPropertyAnimation, QSettings, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QStyleOptionProgressBar,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    APP_ID_LSPRI_EVALUATION,
    BRIGHT_THEME,
    GRAY_DARK_THEME,
    app_icon,
    get_active_theme,
    set_active_theme,
    set_windows_app_user_model_id,
)
from lspr_imaging_app.gui.app_theme import apply_app_theme
from lspr_imaging_app.version import APP_NAME, APP_VERSION


def _configure_logging() -> Path:
    root_logger = logging.getLogger()
    existing_path = getattr(root_logger, "_lspr_session_log_path", None)
    if getattr(root_logger, "_lspr_logging_configured", False) and isinstance(existing_path, Path):
        return existing_path
    root_logger.setLevel(logging.DEBUG)

    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"lspr_imaging_{session_stamp}.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    console_logging_enabled = os.environ.get("LSPR_CONSOLE_LOG", "").strip().lower() in {"1", "true", "yes", "on"}
    if console_logging_enabled:
        stream_handler = logging.StreamHandler()
        # DEBUG, matching the file handler - the suite launcher's Console
        # panel and "App exited unexpectedly" crash tail are both fed from
        # this stream (see apps/suite_launcher's _forward_process_output),
        # and every closeEvent()-time log line (panel visibility syncs,
        # "Processing state saved") is DEBUG level - an INFO filter here
        # silently hid exactly the detail those diagnostics exist to show.
        stream_handler.setLevel(logging.DEBUG)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)
    root_logger._lspr_logging_configured = True  # type: ignore[attr-defined]
    root_logger._lspr_session_log_path = log_path  # type: ignore[attr-defined]

    logging.captureWarnings(True)

    def _log_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("lspr_imaging_app.unhandled").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def _log_thread_exception(args) -> None:
        logging.getLogger("lspr_imaging_app.unhandled").critical(
            "Unhandled thread exception in %s",
            getattr(args, "thread", None).name if getattr(args, "thread", None) is not None else "thread",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _log_unhandled_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = _log_thread_exception  # type: ignore[assignment]
    return log_path


class StartupProgressBar(QProgressBar):
    """Gradient progress bar matching the sLSPR Acquisition launch screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glow_phase = 0.0
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(20)
        self._glow_timer.timeout.connect(self._advance_glow)
        self._glow_timer.start()

    def _advance_glow(self) -> None:
        self._glow_phase = (self._glow_phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            rect = self.rect().adjusted(0, 0, -1, -1)
            radius = 8.0
            theme = get_active_theme()
            bg = QColor(theme.control_bg)
            border = QColor(theme.control_border)
            # The five fill_* stops below are the app's fixed "spectrum"
            # brand gradient (shared with sLSPR Acquisition's identical
            # startup progress bar) - decorative, not theme chrome, so they
            # deliberately stay literal across both themes.
            fill_left = QColor("#7a5cff")
            fill_mid1 = QColor("#4f88ff")
            fill_mid2 = QColor("#39c7ba")
            fill_mid3 = QColor("#e8d85f")
            fill_right = QColor("#ef6b58")

            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)

            value = max(min(int(option.progress), int(option.maximum)), int(option.minimum))
            span = max(option.maximum - option.minimum, 1)
            fill_width = int(round(rect.width() * (value - option.minimum) / span))
            fill_rect = rect.adjusted(1, 1, 0, -1)
            fill_rect.setWidth(max(fill_width - 1, 0))
            if fill_rect.width() > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QLinearGradient(fill_rect.left(), fill_rect.top(), fill_rect.right(), fill_rect.top())
                fill.setColorAt(0.0, fill_left)
                fill.setColorAt(0.22, fill_mid1)
                fill.setColorAt(0.45, fill_mid2)
                fill.setColorAt(0.72, fill_mid3)
                fill.setColorAt(1.0, fill_right)
                painter.setBrush(fill)
                painter.drawRoundedRect(fill_rect, radius, radius)

                glow_width = max(int(fill_rect.width() * 0.42), 92)
                travel_width = fill_rect.width() + glow_width
                glow_center = fill_rect.left() + int(travel_width * self._glow_phase)
                glow_left = int(glow_center - glow_width / 2)
                glow_rect = fill_rect.adjusted(0, 0, 0, 0)
                glow_rect.setLeft(glow_left)
                glow_rect.setWidth(glow_width)
                if glow_rect.right() >= fill_rect.left() and glow_rect.left() <= fill_rect.right():
                    visible_left = max(glow_rect.left(), rect.left())
                    visible_right = min(glow_rect.right(), fill_rect.right())
                    if visible_right > visible_left:
                        shine = QLinearGradient(visible_left, glow_rect.top(), visible_right, glow_rect.top())
                        shine.setColorAt(0.0, QColor(255, 255, 255, 0))
                        shine.setColorAt(0.36, QColor(255, 255, 255, 18))
                        shine.setColorAt(0.50, QColor(255, 255, 255, 130))
                        shine.setColorAt(0.64, QColor(255, 255, 255, 22))
                        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
                        painter.save()
                        painter.setClipRect(fill_rect)
                        painter.setBrush(shine)
                        painter.drawRoundedRect(fill_rect, radius, radius)
                        painter.restore()
        finally:
            painter.end()


class StartupSplash(QWidget):
    """Launch screen matching the sLSPR Acquisition app's startup splash."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("startupSplash")
        self.setWindowTitle("Starting LSPR Imaging")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._max_progress_seen = 0

        accent = QFrame(self)
        accent.setObjectName("startupAccent")
        accent.setFixedHeight(4)
        self._icon_opacity_effect = QGraphicsOpacityEffect(self)
        self._icon_opacity_effect.setOpacity(0.25)
        self._icon_label = QLabel(self)
        self._icon_label.setObjectName("startupIcon")
        self._icon_label.setFixedSize(108, 108)
        self._icon_label.setPixmap(app_icon().pixmap(92, 92))
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setGraphicsEffect(self._icon_opacity_effect)
        self._icon_opacity_anim = QPropertyAnimation(self._icon_opacity_effect, b"opacity", self)
        self._icon_opacity_anim.setDuration(220)
        self._icon_opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        title = QLabel(APP_NAME, self)
        title.setObjectName("startupTitle")
        title.setContentsMargins(0, 0, 0, 4)

        version_label = QLabel(f"ver. {APP_VERSION}", self)
        version_label.setObjectName("startupVersion")
        version_label.setContentsMargins(0, 0, 0, 4)

        subtitle = QLabel("Fast ROI-first workflow for hyperspectral image review.", self)
        subtitle.setObjectName("startupSubtitle")
        self._status_label = QLabel("Starting...", self)
        self._status_label.setObjectName("startupStatus")
        self._progress = StartupProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(accent)

        body = QWidget(self)
        content_row = QWidget(body)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)

        title_row = QWidget(content_row)
        title_row_layout = QHBoxLayout()
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(8)
        title_row_layout.addWidget(title, 1)
        title_row_layout.addWidget(version_label, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        title_row.setLayout(title_row_layout)

        content_layout.addWidget(title_row)
        content_layout.addWidget(subtitle)
        content_layout.addWidget(self._status_label)
        content_layout.addWidget(self._progress)
        content_row.setLayout(content_layout)

        header_row = QWidget(body)
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        header_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header_row.setLayout(header_layout)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(24, 16, 24, 18)
        body_layout.setSpacing(14)
        body_layout.addWidget(header_row, 0, Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(content_row, 1)
        body.setLayout(body_layout)

        layout.addWidget(body)
        self.setLayout(layout)
        self.setFixedSize(560, 190)
        theme = get_active_theme()
        # The qlineargradient stops below are the app's fixed "spectrum"
        # brand gradient (shared with sLSPR Acquisition's identical startup
        # accent bar) - decorative, not theme chrome, so they deliberately
        # stay literal across both themes.
        self.setStyleSheet(
            f"""
            QWidget#startupSplash {{
                background: {theme.window_bg};
                border: 1px solid {theme.toolbar_border};
                border-radius: 18px;
            }}
            QFrame#startupAccent {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #7a5cff,
                    stop: 0.22 #4f88ff,
                    stop: 0.45 #39c7ba,
                    stop: 0.72 #e8d85f,
                    stop: 1 #ef6b58
                );
                border-top-left-radius: 18px;
                border-top-right-radius: 18px;
            }}
            QLabel#startupIcon {{
                background: transparent;
            }}
            QLabel#startupTitle {{
                font-size: 18px;
                font-weight: 700;
                color: {theme.text_primary};
                min-height: 24px;
                padding-bottom: 0px;
            }}
            QLabel#startupVersion {{
                color: {theme.text_dim};
                font-size: 10px;
                padding-top: 2px;
            }}
            QLabel#startupSubtitle {{
                color: {theme.text_dim};
                font-size: 12px;
            }}
            QLabel#startupStatus {{
                color: {theme.text_secondary};
                font-size: 12px;
            }}
            QProgressBar {{
                border: none;
                background: transparent;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
            }}
            """
        )

    def show_centered(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x_pos = available.x() + (available.width() - self.width()) // 2
            y_pos = available.y() + (available.height() - self.height()) // 2
            self.move(x_pos, y_pos)
        self.show()
        QApplication.processEvents()

    def update_progress(self, value: int, text: str) -> None:
        # The startup flow is composed of several independently-numbered sub-phases
        # (dataset restore, processing-profile restore, ...) whose own 0-100 scales
        # don't line up with each other or with the outer flow. Clamping to the
        # running maximum keeps the status text current while guaranteeing the bar
        # itself never visibly moves backward.
        value = max(0, min(value, 100))
        self._max_progress_seen = max(self._max_progress_seen, value)
        self._progress.setValue(self._max_progress_seen)
        self._status_label.setText(text)
        self._animate_icon_opacity(self._max_progress_seen)
        QApplication.processEvents()

    def _animate_icon_opacity(self, progress_value: int) -> None:
        target = 0.25 + (max(0, min(progress_value, 100)) / 100.0) * 0.75
        self._icon_opacity_anim.stop()
        self._icon_opacity_anim.setStartValue(self._icon_opacity_effect.opacity())
        self._icon_opacity_anim.setEndValue(target)
        self._icon_opacity_anim.start()


class _ComboBoxClickToOpenFilter(QObject):
    """Restores click-to-open anywhere on a QComboBox once its arrow
    subcontrol is styled to zero width (see app_theme.combo_box_no_arrow_stylesheet).

    Every QComboBox in this app is made editable-but-read-only elsewhere
    (see _apply_right_aligned_control_text: combo.setEditable(True) plus a
    read-only lineEdit()) purely so its text can be right-aligned - Qt has
    no other way to align a plain non-editable combo box's text. That means
    a click on the box almost always lands on that internal QLineEdit, not
    the QComboBox itself, so it has to be handled here too. Before the arrow
    was hidden, only that small arrow subcontrol opened the popup on click;
    hiding it left no clickable region at all, so this restores click-to-open
    across the whole box instead of just the old arrow-sized sliver.
    """

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Type.MouseButtonPress or event.button() != Qt.MouseButton.LeftButton:
            return False
        combo = obj if isinstance(obj, QComboBox) else None
        if combo is None and isinstance(obj, QLineEdit) and isinstance(obj.parent(), QComboBox):
            combo = obj.parent()
        if combo is not None and combo.isEnabled() and not combo.view().isVisible():
            combo.showPopup()
            return True
        return False


def _apply_windows_titlebar_theme(window: QMainWindow) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        value = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attribute in (20, 19):  # Windows 11 / Windows 10 fallback
            dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def _fast_startup_enabled(settings: QSettings) -> bool:
    env_value = os.environ.get("LSPR_FAST_STARTUP")
    if env_value is not None:
        return env_value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(settings.value("startup/fast_startup", False, type=bool))


def _resolve_default_dataset_folder() -> Path:
    env_value = os.environ.get("LSPR_DATA_DIR") or os.environ.get("LSPR_DEFAULT_DATASET_DIR")
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.exists():
            return candidate

    root = Path(__file__).resolve().parents[2]
    sibling_data_root = root.parent / "LSPRimaging_data"
    candidates = (
        sibling_data_root / "One_frame",
        sibling_data_root / "data corrected",
        sibling_data_root / "data_cutted",
        root / "data" / "datasets" / "One_frame",
        root / "data" / "One_frame",
        root / "One_frame",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def _apply_ui_scale_factor() -> None:
    """Set QT_SCALE_FACTOR from saved preference before QApplication is created.

    QT_SCALE_FACTOR is a multiplier on top of Qt's automatic OS-DPI scaling.
    1.25 = 25 % larger than OS default, 0.85 = 15 % smaller, etc.
    "auto" (or missing) = leave Qt's default DPI handling untouched.
    """
    try:
        # QSettings("org", "app") works before QApplication on all platforms.
        raw = str(QSettings("LSPR", "LSPRImaging").value("ui/scale_factor", "auto") or "auto").strip()
        if raw and raw != "auto":
            factor = float(raw)
            if 0.5 <= factor <= 3.0:
                # setdefault: respect QT_SCALE_FACTOR already set in the environment.
                os.environ.setdefault("QT_SCALE_FACTOR", f"{factor:.6g}")
    except Exception:
        pass


def main() -> None:
    set_windows_app_user_model_id(APP_ID_LSPRI_EVALUATION)
    _apply_ui_scale_factor()
    log_path = _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("LSPRi Evaluation")
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    combo_box_click_filter = _ComboBoxClickToOpenFilter(app)
    app.installEventFilter(combo_box_click_filter)
    settings = QSettings("LSPR", "LSPRImaging")
    set_active_theme(BRIGHT_THEME if str(settings.value("ui/theme", "dark")) == "bright" else GRAY_DARK_THEME)
    fast_startup = _fast_startup_enabled(settings)
    logging.getLogger("lspr_imaging_app.startup").info("Session log file: %s", log_path)
    apply_app_theme(app)
    splash = StartupSplash()
    splash.show_centered()
    splash.update_progress(10, "Opening workspace...")
    splash.update_progress(20, "Loading application modules...")
    try:
        from lspr_imaging_app.gui.main_window import MainWindow
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "")
        if missing in {"zarr", "numcodecs", "ome_zarr"}:
            logging.getLogger("lspr_imaging_app.startup").critical(
                "Missing imaging dependency '%s'. Install the imaging app dependencies before launching.",
                missing,
                exc_info=True,
            )
        raise
    default_folder = _resolve_default_dataset_folder()
    splash.update_progress(40, "Building workspace...")
    window = MainWindow(default_folder=default_folder, fast_startup=fast_startup)
    # Applied again later (harmless - DwmSetWindowAttribute is idempotent),
    # but doing it here too, before the window's native handle is ever shown,
    # is what actually matters: DWM decides the window's initial backdrop
    # colour (a solid white placeholder by default) at the moment the native
    # window is first realized, before Qt gets a chance to paint anything of
    # its own - if the dark-mode attribute is only set after
    # _restore_saved_window_state_after_show()'s first show() (as it used to
    # be, in the finally block below), that first DWM-drawn frame is already
    # the wrong (white) one, which is exactly the startup flash this avoids.
    _apply_windows_titlebar_theme(window)
    splash.update_progress(55, "Preparing window layout...")
    window.prepare_initial_show()
    window.setWindowState(Qt.WindowState.WindowNoState)
    window.hide()
    window.setEnabled(False)
    app.processEvents()

    # run_startup_restore_flow() reports its own 0-100 sub-progress; remap it into
    # the splash's remaining 65-95 span so the bar never jumps backward.
    restore_flow_start, restore_flow_end = 65, 95

    def progress_callback(percent: int, message: str) -> None:
        logging.getLogger("lspr_imaging_app.startup").info("Splash | %s", message)
        percent = max(0, min(percent, 100))
        remapped = restore_flow_start + round((restore_flow_end - restore_flow_start) * (percent / 100.0))
        splash.update_progress(remapped, message)

    def close_splash() -> None:
        splash.close()
        splash.deleteLater()

    def finish_startup() -> None:
        # Do every bit of window/panel/splitter restoration while the window is
        # still hidden, and show it exactly once, already in its final state.
        # An earlier version of this function called window.show()/showNormal()
        # first and only *then* ran panel/splitter restoration plus
        # _restore_saved_window_state_after_show() (which can itself call
        # showMaximized()/showFullScreen(), a second, different show). With no
        # event-loop turn in between show() and that later work, Windows showed
        # a blank "ghost" placeholder for the whole gap (correct title/icon,
        # empty content) -- fixed by giving Qt a paint turn, but that then
        # exposed the *real* problem this reorder fixes: two visibly distinct
        # show states painted back to back (small/default layout at whatever
        # showNormal() first drew, then a visible resize/relayout once the
        # saved splitter state and the real maximized/fullscreen state applied
        # a moment later). Restoring everything before the single show() call
        # avoids that "blink and rescale" entirely, and keeps the ghost-window
        # fix, since show() is still followed by a processEvents() turn before
        # more work (closing the splash) runs.
        #
        # run_startup_restore_flow() dispatches the dataset/processing-profile load to
        # a background thread and returns immediately (see DatasetController /
        # SessionStateManager) rather than blocking here - after_restore_flow is its
        # completion callback, so everything that depends on that data being loaded
        # (panel layout restore, the real show()) runs after it, not immediately after
        # this call returns.
        def after_restore_flow() -> None:
            try:
                splash.update_progress(85, "Restoring panel layout...")
                window._restore_saved_panel_layout_state()
                window._normalize_panel_layout()
                window._sync_panel_visibility_after_show()
                splash.update_progress(96, "Launching workspace...")
                window.setEnabled(True)
                window._restore_saved_window_state_after_show()  # the one show() call: normal/maximized/fullscreen
                # A floating dock widget's own shown/hidden state does not reliably
                # survive its ancestor (this window) going from hidden to shown - the
                # earlier correction inside _restore_saved_panel_layout_state() ran
                # while window was still window.hide()-hidden, so it could be lost the
                # moment the window above actually appears. Re-assert it now that the
                # window is genuinely on screen, and re-sync the View menu checkboxes
                # to match whatever that correction just changed.
                window._ensure_panel_visibility_restored()
                window._sync_panel_visibility_after_show()
                window.raise_()
                window.activateWindow()
                app.processEvents()
            finally:
                app.setQuitOnLastWindowClosed(True)
                _apply_windows_titlebar_theme(window)
                # Small extra safety buffer on top of the processEvents() above, so the
                # splash still covers the tail end of this paint settling.
                QTimer.singleShot(200, close_splash)

        try:
            splash.update_progress(restore_flow_start, "Restoring saved layout...")
            window.run_startup_restore_flow(
                show_window=False, progress_callback=progress_callback, on_done=after_restore_flow
            )
        except Exception:
            # run_startup_restore_flow() raised before it could even dispatch the
            # background load - after_restore_flow() will never fire on its own, so
            # run the same cleanup here directly rather than leaving the splash on
            # screen forever.
            app.setQuitOnLastWindowClosed(True)
            _apply_windows_titlebar_theme(window)
            QTimer.singleShot(200, close_splash)
            raise

    QTimer.singleShot(0, finish_startup)
    sys.exit(app.exec())
