from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMainWindow, QSplashScreen

from lspr_imaging_app.gui.theme import get_active_theme, startup_app_stylesheet


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
        stream_handler.setLevel(logging.INFO)
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


def _render_splash_pixmap(progress: int, message: str) -> QPixmap:
    theme = get_active_theme()
    pixmap = QPixmap(560, 300)
    pixmap.fill(QColor(theme.window_bg))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(0, 0, 560, 300, QColor(theme.window_bg))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.toolbar_section_bg))
    painter.drawRoundedRect(22, 22, 516, 256, 18, 18)
    track_x = 80
    track_y = 210
    track_width = 400
    track_height = 16
    progress = max(0, min(progress, 100))
    fill_width = max(int(round(track_width * (progress / 100.0))), 10 if progress > 0 else 0)
    painter.setBrush(QColor(theme.toolbar_bg))
    painter.drawRoundedRect(track_x, track_y, track_width, track_height, 8, 8)
    painter.setBrush(QColor(theme.control_bg_hover))
    painter.drawRoundedRect(track_x, track_y, track_width, track_height, 8, 8)
    painter.setBrush(QColor(theme.primary_action_bg))
    if fill_width > 0:
        painter.drawRoundedRect(track_x, track_y, fill_width, track_height, 8, 8)

    painter.setPen(QPen(QColor(theme.text_primary)))
    title_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
    body_font = QFont("Segoe UI", 9)
    progress_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
    painter.setFont(title_font)
    painter.drawText(40, 92, "LSPR Imaging")
    painter.setFont(body_font)
    painter.setPen(QColor(theme.control_border_hover))
    painter.drawText(40, 120, "Fast ROI-first workflow for hyperspectral image review")
    painter.setPen(QColor(theme.text_dim))
    painter.drawText(40, 164, "Preparing dark workspace, image loader, and analysis tools...")
    painter.drawText(40, 240, message)
    painter.setFont(progress_font)
    painter.setPen(QColor(theme.text_muted))
    progress_text = f"{progress}%"
    painter.drawText(track_x, track_y - 10, track_width, 16, Qt.AlignmentFlag.AlignCenter, progress_text)
    painter.end()
    return pixmap


def _build_splash() -> QSplashScreen:
    pixmap = _render_splash_pixmap(8, "Opening workspace...")
    splash = QSplashScreen(
        pixmap,
        Qt.WindowType.SplashScreen
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Tool,
    )
    splash.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    return splash


def _update_splash(app: QApplication, splash: QSplashScreen, progress: int, message: str) -> None:
    splash.setPixmap(_render_splash_pixmap(progress, message))
    app.processEvents()


def _apply_dark_theme(app: QApplication) -> None:
    app.setStyleSheet(startup_app_stylesheet())


def _apply_active_palette(app: QApplication) -> None:
    theme = get_active_theme()
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.toolbar_section_bg))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.control_bg))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.primary_action_bg))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.text_primary))
    app.setPalette(palette)


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


def main() -> None:
    log_path = _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("LSPR Imaging")
    app.setQuitOnLastWindowClosed(False)
    settings = QSettings("LSPR", "LSPRImaging")
    if str(settings.value("ui/theme", "blue")) == "gray":
        from lspr_imaging_app.gui.theme import GRAY_DARK_THEME, set_active_theme

        set_active_theme(GRAY_DARK_THEME)
    fast_startup = _fast_startup_enabled(settings)
    logging.getLogger("lspr_imaging_app.startup").info("Session log file: %s", log_path)
    _apply_active_palette(app)
    _apply_dark_theme(app)
    splash = _build_splash()
    splash.show()
    _update_splash(app, splash, 10, "Opening workspace...")
    _update_splash(app, splash, 20, "Loading application modules...")
    from lspr_imaging_app.gui.main_window import MainWindow
    default_folder = _resolve_default_dataset_folder()
    _update_splash(app, splash, 40, "Building workspace...")
    window = MainWindow(default_folder=default_folder, fast_startup=fast_startup)
    _update_splash(app, splash, 55, "Preparing window layout...")
    window.prepare_initial_show()
    window.setWindowState(Qt.WindowState.WindowNoState)
    window.hide()
    window.setEnabled(False)
    app.processEvents()

    def progress_callback(percent: int, message: str) -> None:
        logging.getLogger("lspr_imaging_app.startup").info("Splash | %s", message)
        _update_splash(app, splash, percent, message)

    def finish_startup() -> None:
        try:
            _update_splash(app, splash, 65, "Restoring saved layout...")
            window.run_startup_restore_flow(show_window=False, progress_callback=progress_callback)
            _update_splash(app, splash, 96, "Launching workspace...")
            window.setEnabled(True)
            window.show()
            window.showNormal()
            window._restore_saved_window_state_after_show()
            window.raise_()
            window.activateWindow()
            window._restore_saved_panel_layout_state()
            window._normalize_panel_layout()
            window._sync_panel_visibility_after_show()
            app.processEvents()
        finally:
            splash.close()
            splash.deleteLater()
            app.setQuitOnLastWindowClosed(True)
            _apply_windows_titlebar_theme(window)

    QTimer.singleShot(0, finish_startup)
    sys.exit(app.exec())
