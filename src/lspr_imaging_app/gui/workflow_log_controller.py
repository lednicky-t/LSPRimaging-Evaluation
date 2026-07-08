from __future__ import annotations

import logging
import time
from html import escape

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from .worker import WorkflowLogBridge, WorkflowLogHandler

SUCCESS_LOG_LEVEL = 25
logging.addLevelName(SUCCESS_LOG_LEVEL, "SUCCESS")


class WorkflowLogController:
    """Owns the workflow log console, status bar text/hint, and the status
    hint shown for the currently active tool. State (log buffer, handler,
    autoscroll flag, etc.) still lives on the window itself; this class only
    owns the logic that reads/writes it.
    """

    def __init__(self, window) -> None:
        self.window = window

    def background_error(self, context: str, message: str) -> None:
        self.append_workflow_log(f"{context} failed: {message}", level="error")
        self.set_status_text(f"{context} failed: {message}")

    def set_workflow_log_autoscroll_enabled(self, enabled: bool) -> None:
        window = self.window
        window._workflow_log_autoscroll_enabled = bool(enabled)
        if hasattr(window, "workflow_log_autoscroll_button"):
            color = "#38bdf8" if enabled else "#94a3b8"
            window.workflow_log_autoscroll_button.setIcon(window._mask_panel_icon("arrow-down", color=color, size=20))

    def copy_workflow_log(self) -> None:
        window = self.window
        if not hasattr(window, "workflow_log_view"):
            return
        QApplication.clipboard().setText(window.workflow_log_view.toPlainText())
        self.append_workflow_log("Workflow log copied to clipboard.", level="debug")

    def setup_workflow_logging(self) -> None:
        window = self.window
        if getattr(window, "_workflow_log_handler", None) is not None:
            return
        window._workflow_log_bridge = WorkflowLogBridge(window)
        window._workflow_log_bridge.record_received.connect(self.append_workflow_log_entry)
        handler = WorkflowLogHandler(window._workflow_log_bridge)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%H:%M:%S"))
        handler._lspr_gui_handler = True  # type: ignore[attr-defined]
        package_logger = logging.getLogger("lspr_imaging_app")
        package_logger.setLevel(logging.DEBUG)
        if not any(getattr(existing, "_lspr_gui_handler", False) for existing in package_logger.handlers):
            package_logger.addHandler(handler)
        window._workflow_logger = logging.getLogger("lspr_imaging_app.workflow")
        window._workflow_logger.setLevel(logging.DEBUG)
        window._workflow_log_handler = handler
        window._workflow_log_buffer: list[tuple[int, str]] = []
        window._workflow_log_buffer_timer = QTimer(window)
        window._workflow_log_buffer_timer.setSingleShot(True)
        window._workflow_log_buffer_timer.timeout.connect(self.flush_workflow_log_buffer)
        window._workflow_log_throttle_state: dict[str, tuple[float, str]] = {}

    def remove_workflow_logging(self) -> None:
        window = self.window
        handler = getattr(window, "_workflow_log_handler", None)
        if handler is None:
            return
        package_logger = logging.getLogger("lspr_imaging_app")
        if handler in package_logger.handlers:
            package_logger.removeHandler(handler)
        window._workflow_log_handler = None
        bridge = getattr(window, "_workflow_log_bridge", None)
        if bridge is not None:
            try:
                bridge.record_received.disconnect(self.append_workflow_log_entry)
            except Exception:
                pass
            window._workflow_log_bridge = None
        buffer_timer = getattr(window, "_workflow_log_buffer_timer", None)
        if buffer_timer is not None:
            try:
                buffer_timer.stop()
            except Exception:
                pass
        window._workflow_log_buffer = []
        window._workflow_log_throttle_state = {}

    def append_workflow_log_entry(self, levelno: int, text: str) -> None:
        window = self.window
        line = str(text).rstrip()
        if not line or not hasattr(window, "workflow_log_view"):
            return
        if int(levelno) < logging.INFO:
            buffer = getattr(window, "_workflow_log_buffer", None)
            buffer_timer = getattr(window, "_workflow_log_buffer_timer", None)
            if buffer is not None and buffer_timer is not None:
                buffer.append((int(levelno), line))
                if not buffer_timer.isActive():
                    buffer_timer.start(150)
                return
        self.append_workflow_log_entry_now(levelno, line)

    def flush_workflow_log_buffer(self) -> None:
        window = self.window
        buffer = getattr(window, "_workflow_log_buffer", None)
        if not buffer:
            return
        batch = list(buffer)
        buffer.clear()
        for levelno, line in self.collapse_workflow_log_batch(batch):
            self.append_workflow_log_entry_now(levelno, line)

    @staticmethod
    def collapse_workflow_log_batch(batch: list[tuple[int, str]]) -> list[tuple[int, str]]:
        if not batch:
            return []
        collapsed: list[tuple[int, str]] = []
        image_hits = 0
        image_builds = 0
        roi_hits = 0
        roi_builds = 0
        for levelno, line in batch:
            if "Image cache hit |" in line:
                image_hits += 1
                continue
            if "Image cache built |" in line:
                image_builds += 1
                continue
            if "ROI cache hit |" in line:
                roi_hits += 1
                continue
            if "ROI cache built |" in line:
                roi_builds += 1
                continue
            collapsed.append((levelno, line))
        if image_hits or image_builds:
            collapsed.append((logging.DEBUG, f"Image cache batch | hit={image_hits} build={image_builds}"))
        if roi_hits or roi_builds:
            collapsed.append((logging.DEBUG, f"ROI cache batch | hit={roi_hits} build={roi_builds}"))
        return collapsed

    def append_workflow_log_entry_now(self, levelno: int, text: str) -> None:
        window = self.window
        color = {
            logging.DEBUG: "#60a5fa",
            logging.INFO: "#cbd5e1",
            SUCCESS_LOG_LEVEL: "#22c55e",
            logging.WARNING: "#f59e0b",
            logging.ERROR: "#ef4444",
            logging.CRITICAL: "#f43f5e",
        }.get(int(levelno), "#cbd5e1")
        escaped = escape(text).replace("\n", "<br>")
        html = f'<div style="color:{color}; white-space:pre-wrap; margin:0;">{escaped}</div>'
        window.workflow_log_view.moveCursor(QTextCursor.MoveOperation.End)
        window.workflow_log_view.insertHtml(html)
        window.workflow_log_view.insertHtml("<br>")
        if window._workflow_log_autoscroll_enabled:
            scrollbar = window.workflow_log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def append_workflow_log(self, message: str, *, level: str = "info") -> None:
        window = self.window
        line = str(message).strip()
        if not line:
            return
        level_key = str(level).strip().lower()
        levelno = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "success": SUCCESS_LOG_LEVEL,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(level_key, logging.INFO)
        logger = getattr(window, "_workflow_logger", logging.getLogger("lspr_imaging_app.workflow"))
        logger.log(levelno, line)

    def append_workflow_log_throttled(self, key: str, message: str, *, level: str = "debug", min_interval: float = 2.0) -> None:
        window = self.window
        line = str(message).strip()
        if not line:
            return
        now = time.perf_counter()
        state = getattr(window, "_workflow_log_throttle_state", None)
        if state is None:
            state = {}
            window._workflow_log_throttle_state = state
        previous = state.get(str(key))
        if previous is not None:
            previous_at, previous_line = previous
            if previous_line == line and (now - previous_at) < float(min_interval):
                return
        state[str(key)] = (now, line)
        self.append_workflow_log(line, level=level)

    def set_status_text(self, text: str) -> None:
        window = self.window
        previous_text = window._status_bar_message.text()
        window.status_label.setText(text)
        window._status_bar_message.setText(text)
        if previous_text and previous_text != text:
            window._status_bar_last_action.setText(f"Last action: {previous_text}")

    def set_status_hint(self, text: str) -> None:
        self.window._status_bar_hint.setText(f"Hint: {text}")

    @staticmethod
    def format_elapsed_seconds(seconds: float | None) -> str:
        if seconds is None or not np.isfinite(seconds) or seconds < 0:
            return ""
        total_seconds = int(round(float(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"

    @staticmethod
    def compact_timing_text(*parts: tuple[str, float | None]) -> str:
        chunks: list[str] = []
        for label, seconds in parts:
            elapsed = WorkflowLogController.format_elapsed_seconds(seconds)
            if elapsed:
                chunks.append(f"{label} {elapsed}")
        return " | ".join(chunks)

    def workflow_notes_text(self) -> str:
        return (
            "Workflow notes\n"
            "\n"
            "Console labels\n"
            "- DEBUG: technical details, diagnostics, and state changes for troubleshooting.\n"
            "- INFO: normal progress messages and important updates.\n"
            "- SUCCESS: a step finished correctly, so you can treat it as confirmed.\n"
            "- WARNING: something looks unusual or was recovered automatically, but the app keeps going.\n"
            "- ERROR / CRITICAL: a step failed or the app hit a serious problem that needs attention.\n"
            "\n"
            "Startup\n"
            "- Core libraries load first.\n"
            "- The splash screen restores the session, layout, and caches before the main window is shown.\n"
            "- Analysis stays unlinked during startup so spectra and sensorgrams do not recalculate early.\n"
            "\n"
            "What stays in memory\n"
            "- Processed image cache for the current stack.\n"
            "- Per-ROI absorbance cache for single-ROI live preview.\n"
            "- Spectral cube absorbance cache for the same spectral cube/settings combinations.\n"
            "- Sensorgram cache for repeated spectral-cube-range calculations.\n"
            "- Chromatic landmarks and fitted transforms.\n"
            "\n"
            "What is loaded live\n"
            "- TIFF or Stack to Zarr planes that are not already cached.\n"
            "- Uncached ROI spectra and sensorgrams.\n"
            "- ROI and mask changes that alter the analysis signature.\n"
            "\n"
            "What is already optimized\n"
            "- Image refresh runs in the background.\n"
            "- Neighboring image planes are prefetched.\n"
            "- Absorbance results are cached per ROI and per spectral cube.\n"
            "- Sensorgram preparation can reuse cached spectra when available.\n"
            "- Workflow events are mirrored to logs/lspr_imaging_<session>.log.\n"
            "- The Workflow console shows the same events live inside the app.\n"
            "\n"
            "Next useful optimizations\n"
            "- Move more spectral cube preparation off the UI thread.\n"
            "- Add selective ROI-only reads for compatible formats.\n"
            "- Expand spectral-cube-level caching for repeated sensorgram navigation.\n"
            "- Keep using Stack to Zarr for random access and larger datasets.\n"
        )

    def show_workflow_notes(self) -> None:
        window = self.window
        dialog = QDialog(window)
        dialog.setWindowTitle("Workflow notes")
        dialog.resize(860, 640)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(self.workflow_notes_text())
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def update_status_hint(self) -> None:
        window = self.window
        if window._active_tool == "roi":
            if window._roi_editor_mode == "rectangles":
                if window.roi_move_action.isChecked():
                    self.set_status_hint("Left-click selects ROIs, right-drag moves them, Delete removes selected ROIs.")
                elif window.roi_add_action.isChecked():
                    self.set_status_hint("Left-click adds a rectangle ROI stamp from the active template.")
                elif window.roi_array_action.isChecked():
                    self.set_status_hint("Left-click stamps a rectangle ROI array from the active template.")
                else:
                    self.set_status_hint("Left-click selects ROIs. Shift adds to the selection, Delete removes them.")
                return
            if window._roi_editor_mode == "circles":
                if window.roi_move_action.isChecked():
                    self.set_status_hint("Left-click selects, right-drag moves. Arrow keys nudge. Delete removes.")
                elif window.roi_add_action.isChecked():
                    self.set_status_hint("Left-click places a circle ROI at the cursor position.")
                elif window.roi_array_action.isChecked():
                    self.set_status_hint("Left-click stamps a grid of circle ROIs centred on the cursor.")
                else:
                    self.set_status_hint("Left-click selects ROIs, Shift adds, box-drag multi-select, Delete removes.")
                return
            if window.roi_move_action.isChecked():
                self.set_status_hint("Left-click selects, Shift adds, left-drag boxes, right-drag moves, middle-drag pans.")
            elif window.roi_add_action.isChecked():
                self.set_status_hint("Left-click adds an ROI. Shift-click still adds to selection.")
            else:
                self.set_status_hint("Left-click selects, Shift adds, left-drag boxes, double-click empty space clears selection.")
            return
        if window._active_tool == "mask":
            self.set_status_hint("Left-drag paints the mask. Use the toolbar icons to show, add, or subtract previews.")
            return
        if window._active_tool in {"rotate", "crop", "measure"}:
            self.set_status_hint("Use the image toolbar controls for the active image tool.")
            return
        self.set_status_hint("Hover a control for guidance.")
