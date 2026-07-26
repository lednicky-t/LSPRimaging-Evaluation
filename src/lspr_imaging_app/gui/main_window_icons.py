from __future__ import annotations

import os
import shutil
import threading
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
from PyQt6.QtCore import (
    QByteArray,
    QLineF,
    QPointF,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QAbstractSpinBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    APP_THEME,
    icon_accent_colors,
    load_tabler_icon,
    make_compact_spinbox,
    transparent_icon_button_stylesheet,
)
from lspr_imaging_app.gui.ui_helpers import (
    current_ome_zarr_compression_enabled,
)
from lspr_imaging_app.gui.analysis_tasks import _ome_zarr_export_task
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.io.dataset import (
    dataset_is_ome_zarr,
)

try:
    import lucide
except Exception:  # pragma: no cover - optional icon dependency
    lucide = None


class ClickableIconLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Set here (not just at each call site) so every instance - including
        # any future direct instantiation that skips the factory helpers
        # below - shows a pointing hand instead of the default arrow cursor.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class FreeStandingToggleIconLabel(ClickableIconLabel):
    toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        self._icon: QIcon = QIcon()
        self._icon_size = QSize(24, 24)

    def setIcon(self, icon: QIcon) -> None:
        self._icon = icon
        self._refresh_pixmap()

    def setIconSize(self, size: QSize) -> None:
        self._icon_size = QSize(size)
        self._refresh_pixmap()

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        self._refresh_pixmap()
        self.toggled.emit(self._checked)

    def isChecked(self) -> bool:
        return self._checked

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _refresh_pixmap(self) -> None:
        if self._icon.isNull():
            self.setPixmap(QPixmap())
            return
        self.setPixmap(self._icon.pixmap(self._icon_size))


class FreeStandingToggleTextLabel(ClickableIconLabel):
    toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        self._checked_text = ""
        self._unchecked_text = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def setTexts(self, unchecked_text: str, checked_text: str) -> None:
        self._unchecked_text = str(unchecked_text)
        self._checked_text = str(checked_text)
        self._refresh_text()

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if self._checked == checked:
            self._refresh_text()
            return
        self._checked = checked
        self._refresh_text()
        self.toggled.emit(self._checked)

    def isChecked(self) -> bool:
        return self._checked

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _refresh_text(self) -> None:
        self.setText(self._checked_text if self._checked else self._unchecked_text)


class MainWindowIcons:
    @staticmethod
    def _svg_icon_from_markup(svg_markup: str, size: int = 24) -> QIcon:
        if not svg_markup:
            return QIcon()
        renderer = QSvgRenderer(QByteArray(svg_markup.encode("utf-8")))
        if not renderer.isValid():
            return QIcon()
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
        painter.end()
        return QIcon(pixmap)

    def _make_array_marker_icon(self, kind: str, *, color: str = "#f8fafc", size: int = 24) -> QIcon:
        if kind == "columns":
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none">
  <circle cx="9" cy="12" r="1.2" fill="{color}" />
  <circle cx="12" cy="12" r="1.2" fill="{color}" />
  <circle cx="15" cy="12" r="1.2" fill="{color}" />
</svg>"""
            return self._svg_icon_from_markup(svg, size=size)
        if kind == "rows":
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none">
  <g transform="rotate(90 12 12)">
    <circle cx="9" cy="12" r="1.2" fill="{color}" />
    <circle cx="12" cy="12" r="1.2" fill="{color}" />
    <circle cx="15" cy="12" r="1.2" fill="{color}" />
  </g>
</svg>"""
            return self._svg_icon_from_markup(svg, size=size)
        if kind == "distance":
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="7" cy="14" r="1.5" fill="{color}" stroke="none" />
  <circle cx="17" cy="14" r="1.5" fill="{color}" stroke="none" />
  <g stroke-width="1.4">
    <line x1="7" y1="10" x2="17" y2="10" />
    <line x1="7" y1="9" x2="7" y2="11" />
    <line x1="17" y1="9" x2="17" y2="11" />
  </g>
</svg>"""
            return self._svg_icon_from_markup(svg, size=size)
        return QIcon()

    def _make_array_marker_label(self, kind: str, tooltip: str) -> QLabel:
        label = QLabel(self)
        label.setPixmap(self._make_array_marker_icon(kind, size=32).pixmap(32, 32))
        label.setFixedSize(32, 32)
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        label.setToolTip(tooltip)
        return label

    @staticmethod
    def _make_corner_seed_icon(color: str = "#f8fafc", size: int = 24) -> QIcon:
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M5 9V5h4" />
  <path d="M15 5h4v4" />
  <path d="M19 15v4h-4" />
  <path d="M9 19H5v-4" />
</svg>"""
        return MainWindowIcons._svg_icon_from_markup(svg, size=size)

    @staticmethod
    def _tabler_icon(
        name: str,
        color: str = "#f8fafc",
        size: int = 24,
        *,
        stroke_width: float = 2.2,
        fill: str = "none",
    ) -> QIcon:
        return load_tabler_icon(name, color=color, size=size, stroke_width=stroke_width, fill=fill)

    @staticmethod
    def _lucide_icon(name: str, color: str = "#f8fafc", size: int = 24, *, stroke_width: float = 2.2) -> QIcon:
        if lucide is None:
            return QIcon()
        try:
            svg = lucide._render_icon(  # type: ignore[attr-defined]
                name,
                size,
                stroke=color,
                fill="none",
                stroke_width=stroke_width,
                stroke_linecap="round",
                stroke_linejoin="round",
            )
        except Exception:
            return QIcon()
        return MainWindowIcons._svg_icon_from_markup(svg, size=size)

    @staticmethod
    def _make_help_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("circle-help", "#f8fafc", 24, stroke_width=2.2)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#f8fafc"), 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(3.5, 3.5, 17.0, 17.0))
        painter.setFont(QFont("Sans Serif", 10, QFont.Weight.Bold))
        painter.drawText(QRectF(7.0, 4.6, 10.0, 12.0), Qt.AlignmentFlag.AlignCenter, "?")
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _dataset_stack_icon_pixmap(size: int = 36, *, ome_zarr: bool = False) -> QPixmap:
        icon_name = "box" if ome_zarr else "stack-3"
        icon = MainWindowIcons._tabler_icon(icon_name, "#f8fafc", size=size, stroke_width=2.1)
        if not icon.isNull():
            return icon.pixmap(size, size)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f8fafc"), max(2.0, size / 18.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        margin = size * 0.18
        gap = size * 0.18
        width = size - 2 * margin
        height = (size - 3 * margin - 2 * gap) / 3.0
        for idx in range(3):
            top = margin + idx * (height + gap)
            painter.drawRoundedRect(QRectF(margin, top, width, height), 3.0, 3.0)
        painter.end()
        return pixmap

    def _update_dataset_stack_indicator(self, dataset=None) -> None:
        dataset = self._state.dataset if dataset is None else dataset
        ome_zarr = dataset_is_ome_zarr(dataset)
        self.dataset_stack_icon.setPixmap(self._dataset_stack_icon_pixmap(36, ome_zarr=ome_zarr))
        if ome_zarr:
            self.dataset_stack_icon.setToolTip("OME-Zarr stack loaded from the selected dataset folder.")
            self.dataset_stack_label.setText("OME-Zarr")
            self.dataset_stack_label.setToolTip("The loaded dataset is stored as an OME-Zarr image stack.")
        else:
            self.dataset_stack_icon.setToolTip("Image stack loaded from the selected dataset folder.")
            self.dataset_stack_label.setText("ImageStack")
            self.dataset_stack_label.setToolTip("The loaded dataset is treated as an image stack.")

    def _current_ome_zarr_chunk_size(self) -> int:
        return max(int(self.ome_zarr_chunk_spin.value()), 4)

    def _current_ome_zarr_compression_enabled(self) -> bool:
        return current_ome_zarr_compression_enabled(self.ome_zarr_compression_button.isChecked())

    def _current_ome_zarr_skip_excluded(self) -> bool:
        return bool(self.ome_zarr_skip_excluded_button.isChecked())

    def _on_ome_zarr_skip_excluded_toggled(self, checked: bool) -> None:
        from PyQt6.QtWidgets import QMessageBox

        if checked:
            excluded_count = len(self._state.image_exclusions)
            if excluded_count == 0:
                confirm = QMessageBox.StandardButton.Yes
            else:
                confirm = QMessageBox.question(
                    self,
                    "Skip excluded images in export",
                    f"{excluded_count} exclusion rule(s) are set. Exported planes they cover will be "
                    "left empty (no pixel data) instead of containing real measurements.\n\n"
                    "This cannot be undone once the export is written. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            if confirm != QMessageBox.StandardButton.Yes:
                self.ome_zarr_skip_excluded_button.blockSignals(True)
                self.ome_zarr_skip_excluded_button.setChecked(False)
                self.ome_zarr_skip_excluded_button.blockSignals(False)
                checked = False
        color = "#ef4444" if checked else "#94a3b8"
        self.ome_zarr_skip_excluded_button.setIcon(self._mask_panel_icon("alert-triangle", color=color, size=APP_THEME.icon_button_inner))

    def _sync_ome_zarr_chunk_controls(self) -> None:
        self._ui_state_manager.sync_ome_zarr_chunk_controls()

    def _current_ome_zarr_shard_mode(self) -> str:
        return str(self.ome_zarr_shard_mode_combo.currentData() or "per_image")

    def _on_ome_zarr_chunk_size_changed(self, _value: int) -> None:
        self._sync_ome_zarr_chunk_controls()
        self._save_layout_preferences()

    def _on_ome_zarr_shard_mode_changed(self, _index: int) -> None:
        self._settings.setValue("ome_zarr/shard_mode", self.ome_zarr_shard_mode_combo.currentData())
        self._save_layout_preferences()

    def _ome_zarr_grid_icon(self, active: bool) -> QIcon:
        color = "#22c55e" if active else "#94a3b8"
        icon = self._tabler_icon("grid-4x4", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for row in range(4):
            for col in range(4):
                x = 4 + col * 4
                y = 4 + row * 4
                painter.drawRect(x, y, 2, 2)
        painter.end()
        return QIcon(pixmap)

    def _on_ome_zarr_chunk_guide_toggled(self, checked: bool) -> None:
        self.ome_zarr_chunk_guide_button.setIcon(self._ome_zarr_grid_icon(bool(checked)))
        self._update_ome_zarr_chunk_guide_overlay()

    def _on_ome_zarr_compression_toggled(self, checked: bool) -> None:
        self.ome_zarr_compression_button.setIcon(self._ome_zarr_compression_icon(bool(checked)))
        self._sync_ome_zarr_chunk_controls()

    def _ome_zarr_compression_icon(self, active: bool) -> QIcon:
        color = "#22c55e" if active else "#94a3b8"
        icon = self._tabler_icon("archive", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(4.0, 6.0, 16.0, 10.0), 2.5, 2.5)
        painter.drawLine(QPointF(6.0, 10.0), QPointF(18.0, 10.0))
        painter.drawLine(QPointF(8.0, 6.0), QPointF(8.0, 16.0))
        painter.drawLine(QPointF(16.0, 6.0), QPointF(16.0, 16.0))
        painter.end()
        return QIcon(pixmap)

    def _ome_zarr_stop_icon(self, *, size: int = 24) -> QIcon:
        icon = self._tabler_icon("player-stop-filled", "#ef4444", size, stroke_width=2.1, fill="#ef4444")
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ef4444"))
        scale = size / 24.0
        painter.drawRoundedRect(QRectF(6.0 * scale, 6.0 * scale, 12.0 * scale, 12.0 * scale), 2.8 * scale, 2.8 * scale)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _folder_size_bytes(path: Path) -> int:
        total = 0
        if not path.exists():
            return total
        if path.is_file():
            try:
                return int(path.stat().st_size)
            except Exception:
                return total
        for root, _dirs, files in os.walk(path):
            for name in files:
                file_path = Path(root) / name
                try:
                    total += int(file_path.stat().st_size)
                except Exception:
                    continue
        return total

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        value = float(max(int(num_bytes), 0))
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(int(round(float(seconds))), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _set_ome_zarr_export_ui_running(self, running: bool) -> None:
        self._ome_zarr_export_running = bool(running)
        self.dataset_ome_zarr_export_progress_row.setVisible(bool(running))
        self.dataset_ome_zarr_export_progress_bar.setValue(0 if running else 0)
        self.dataset_ome_zarr_export_progress_bar.setFormat("%p%")
        self.dataset_ome_zarr_export_eta_label.setText("ETA: --:--")
        self.dataset_ome_zarr_export_stop_button.setVisible(bool(running))
        self.dataset_ome_zarr_export_stop_button.setEnabled(bool(running))
        self.dataset_ome_zarr_export_button.setEnabled(not running and self._state.dataset is not None)
        self.ome_zarr_chunk_spin.setEnabled(not running)
        self.ome_zarr_chunk_guide_button.setEnabled(not running)
        self.ome_zarr_shard_mode_combo.setEnabled(not running)
        self.ome_zarr_compression_button.setEnabled(not running)
        self.dataset_ome_zarr_controls_row.setEnabled(not running)
        self.dataset_ome_zarr_options_row.setEnabled(not running)
        self.dataset_ome_zarr_info_row.setEnabled(not running)
        if running:
            self.dataset_ome_zarr_export_status_label.setText("Progress")
        self._sync_ome_zarr_chunk_controls()

    def _start_ome_zarr_export(
        self,
        destination: Path,
        chunk_size_px: int,
        *,
        compression_enabled: bool = True,
        shard_mode: str = "per_image",
        skip_excluded_images: bool = False,
    ) -> None:
        dataset = self._state.dataset
        if dataset is None:
            self._set_status_text("Load a dataset before exporting Stack to Zarr.")
            return
        if self._ome_zarr_export_running:
            self._set_status_text("Stack to Zarr export is already running.")
            return
        # Snapshot preprocessing now so later edits in the GUI (while export runs
        # in the background) can't change the settings mid-export.
        preprocessing_snapshot = deepcopy(self._state.preprocessing)
        self._ome_zarr_export_request_id += 1
        request_id = self._ome_zarr_export_request_id
        self._ome_zarr_export_cancel_event = threading.Event()
        self._ome_zarr_export_destination = destination
        self._ome_zarr_export_started_at = time.perf_counter()
        self._set_ome_zarr_export_ui_running(True)
        self._set_status_text(f"Exporting Stack to Zarr to {destination.name}...")
        self._begin_busy("Exporting Stack to Zarr...", determinate=True)
        excluded_rules_snapshot = deepcopy(self._state.image_exclusions) if skip_excluded_images else None
        worker = FunctionWorker(
            _ome_zarr_export_task,
            dataset,
            destination,
            int(chunk_size_px),
            bool(compression_enabled),
            preprocessing_snapshot,
            shard_mode,
            excluded_rules=excluded_rules_snapshot,
            skip_excluded=bool(skip_excluded_images),
            cancel_event=self._ome_zarr_export_cancel_event,
            supports_progress=True,
        )
        tools_text = "applied" if bool(getattr(preprocessing_snapshot, "image_tools_enabled", False)) else "ignored"
        shard_text = "per_spectral_cube" if shard_mode == "per_spectral_cube" else "per_image"
        excluded_text = f"on ({len(excluded_rules_snapshot)} rules)" if skip_excluded_images else "off"
        self._append_workflow_log(
            f"OME-Zarr export start | chunks {chunk_size_px}px | shard {shard_text} | compression {'on' if compression_enabled else 'off'} | image tools {tools_text} | skip excluded {excluded_text}",
            level="info",
        )
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.progress.connect(
            lambda percent, text, request_id=request_id: self._on_ome_zarr_export_progress(request_id, percent, text)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self._on_ome_zarr_export_finished(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_ome_zarr_export_failed(request_id, message))
        # Run in a dedicated daemon thread instead of QThreadPool so the export is
        # fully independent of the GUI thread pool (Qt tasks like image loading keep
        # their pool slots) and Qt signals are delivered via queued connections.
        self._ome_zarr_export_thread = threading.Thread(target=worker.run, daemon=True, name="ome-zarr-export")
        self._ome_zarr_export_thread.start()

    def _stop_ome_zarr_export(self) -> None:
        if not self._ome_zarr_export_running or self._ome_zarr_export_cancel_event is None:
            return
        self._ome_zarr_export_cancel_event.set()
        self._set_status_text("Stopping Stack to Zarr export...")
        self.dataset_ome_zarr_export_eta_label.setText("ETA: stopping...")

    def _on_ome_zarr_export_progress(self, request_id: int, percent: int, text: str) -> None:
        if request_id != self._ome_zarr_export_request_id or not self._ome_zarr_export_running:
            return
        current_percent = int(np.clip(percent, 0, 100))
        self.dataset_ome_zarr_export_progress_bar.setValue(current_percent)
        self.dataset_ome_zarr_export_progress_bar.setFormat(f"{current_percent}%")
        eta_text = "ETA: --:--"
        if self._ome_zarr_export_started_at is not None and current_percent > 0:
            elapsed = max(time.perf_counter() - self._ome_zarr_export_started_at, 1e-6)
            remaining = max((elapsed * (100.0 - current_percent)) / max(float(current_percent), 1.0), 0.0)
            eta_text = f"ETA: {self._format_elapsed_seconds(remaining) or '0:00'}"
        self.dataset_ome_zarr_export_eta_label.setText(eta_text)
        self._set_status_text(text)

    def _finish_ome_zarr_export(self, request_id: int, message: str | None = None, *, failed: bool = False) -> None:
        if request_id != self._ome_zarr_export_request_id:
            return
        elapsed_text = ""
        if self._ome_zarr_export_started_at is not None:
            elapsed_text = f" in {self._format_duration(time.perf_counter() - self._ome_zarr_export_started_at)}"
        self._ome_zarr_export_running = False
        self._end_busy()
        self._ome_zarr_export_started_at = None
        self._ome_zarr_export_cancel_event = None
        self.dataset_ome_zarr_export_progress_bar.setValue(0)
        self.dataset_ome_zarr_export_progress_bar.setFormat("%p%")
        self.dataset_ome_zarr_export_eta_label.setText("ETA: --:--")
        self.dataset_ome_zarr_export_progress_row.hide()
        self.dataset_ome_zarr_export_stop_button.setVisible(False)
        self.dataset_ome_zarr_export_stop_button.setEnabled(False)
        self.dataset_ome_zarr_export_button.setEnabled(self._state.dataset is not None)
        self.ome_zarr_chunk_spin.setEnabled(True)
        self.ome_zarr_chunk_guide_button.setEnabled(True)
        self.ome_zarr_shard_mode_combo.setEnabled(True)
        self.ome_zarr_compression_button.setEnabled(True)
        self.dataset_ome_zarr_controls_row.setEnabled(True)
        self.dataset_ome_zarr_options_row.setEnabled(True)
        self.dataset_ome_zarr_info_row.setEnabled(True)
        self._sync_ome_zarr_chunk_controls()
        self._append_workflow_log(
            f"OME-Zarr export {'failed' if failed else 'done'}{elapsed_text}",
            level="warning" if failed else "success",
        )
        if message:
            self._set_status_text(f"{message}{elapsed_text}")
        if failed and self._ome_zarr_export_destination is not None:
            try:
                if self._ome_zarr_export_destination.exists():
                    shutil.rmtree(self._ome_zarr_export_destination, ignore_errors=True)
            except Exception:
                pass
        self._ome_zarr_export_destination = None

    def _on_ome_zarr_export_finished(self, request_id: int, result: Path) -> None:
        if request_id != self._ome_zarr_export_request_id:
            return
        destination = Path(result)
        size_text = self._format_bytes(self._folder_size_bytes(destination))
        self._finish_ome_zarr_export(request_id, f"Done. Exported {destination.name} ({size_text}).")

    def _on_ome_zarr_export_failed(self, request_id: int, message: str) -> None:
        if request_id != self._ome_zarr_export_request_id:
            return
        if "cancelled" in message.lower():
            self._finish_ome_zarr_export(request_id, "Stack to Zarr export cancelled.", failed=True)
            return
        self._finish_ome_zarr_export(request_id, f"Stack to Zarr export failed: {message}", failed=True)
        QMessageBox.critical(self, "Stack to Zarr export failed", message)

    @staticmethod
    def _dataset_transfer_icon(action: str, color: str = "#f8fafc", size: int = 24) -> QIcon:
        icon_name = "database-import" if str(action).lower() == "import" else "database-export"
        icon = MainWindowIcons._tabler_icon(icon_name, color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        fallback = MainWindowIcons._tabler_icon("database", color, size, stroke_width=2.1)
        if not fallback.isNull():
            return fallback
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.8, size / 12.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(size * 0.18, size * 0.2, size * 0.64, size * 0.52), size * 0.18, size * 0.18)
        painter.drawLine(QLineF(size * 0.26, size * 0.35, size * 0.74, size * 0.35))
        painter.drawLine(QLineF(size * 0.26, size * 0.53, size * 0.74, size * 0.53))
        if str(action).lower() == "import":
            painter.drawLine(QLineF(size * 0.50, size * 0.72, size * 0.50, size * 0.88))
            painter.drawLine(QLineF(size * 0.50, size * 0.72, size * 0.40, size * 0.82))
            painter.drawLine(QLineF(size * 0.50, size * 0.72, size * 0.60, size * 0.82))
        else:
            painter.drawLine(QLineF(size * 0.50, size * 0.12, size * 0.50, size * 0.28))
            painter.drawLine(QLineF(size * 0.50, size * 0.12, size * 0.40, size * 0.22))
            painter.drawLine(QLineF(size * 0.50, size * 0.12, size * 0.60, size * 0.22))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _free_standing_icon_label(
        icon: QIcon,
        tooltip: str,
        *,
        size: int = 24,
        parent: QWidget | None = None,
    ) -> ClickableIconLabel:
        label = ClickableIconLabel(parent)
        label.setPixmap(icon.pixmap(size, size))
        label.setToolTip(tooltip)
        label.setFixedSize(size + 4, size + 4)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        return label

    def _free_standing_toggle_text_label(
        self,
        checked: bool,
        tooltip: str,
        *,
        unchecked_text: str,
        checked_text: str,
        size: int = 24,
        parent: QWidget | None = None,
    ) -> FreeStandingToggleTextLabel:
        label = FreeStandingToggleTextLabel(parent)
        label.setTexts(unchecked_text, checked_text)
        label.setChecked(bool(checked))
        label.setToolTip(tooltip)
        label.setFixedSize(max(size + 6, 28), max(size + 6, 28))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setObjectName("histogramScaleToggle")
        label.setStyleSheet(
            "QLabel#histogramScaleToggle {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  font-size: 12px;"
            "  font-weight: 700;"
            "  color: #94a3b8;"
            "}"
            "QLabel#histogramScaleToggle:hover {"
            "  color: #38bdf8;"
            "  background: rgba(56, 189, 248, 0.12);"
            "  border-radius: 9px;"
            "}"
            "QLabel#histogramScaleToggle[checked=\"true\"] {"
            "  color: #22c55e;"
            "  background: rgba(34, 197, 94, 0.10);"
            "  border-radius: 9px;"
            "}"
        )
        return label

    def _free_standing_toggle_icon_label(
        self,
        icon: QIcon,
        checked: bool,
        tooltip: str,
        *,
        size: int = 24,
        parent: QWidget | None = None,
    ) -> FreeStandingToggleIconLabel:
        label = FreeStandingToggleIconLabel(parent)
        label.setIcon(icon)
        label.setIconSize(QSize(size, size))
        label.setChecked(bool(checked))
        label.setToolTip(tooltip)
        label.setFixedSize(size + 8, size + 8)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setObjectName("histogramScaleToggle")
        label.setStyleSheet(
            "QLabel#histogramScaleToggle {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "}"
            "QLabel#histogramScaleToggle:hover {"
            "  background: rgba(56, 189, 248, 0.14);"
            "  border-radius: 9px;"
            "}"
        )
        return label

    @staticmethod
    def _navigation_chevron_icon(direction: str, color: str = "#f8fafc", size: int = 24) -> QIcon:
        icon_name = "chevron-left" if str(direction).lower() == "left" else "chevron-right"
        icon = MainWindowIcons._tabler_icon(icon_name, color, size, stroke_width=2.2)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.8, size / 12.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_y = size / 2.0
        if icon_name == "chevron-left":
            painter.drawLine(QLineF(size * 0.62, size * 0.25, size * 0.38, center_y))
            painter.drawLine(QLineF(size * 0.38, center_y, size * 0.62, size * 0.75))
        else:
            painter.drawLine(QLineF(size * 0.38, size * 0.25, size * 0.62, center_y))
            painter.drawLine(QLineF(size * 0.62, center_y, size * 0.38, size * 0.75))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _dataset_folder_icon(color: str = "#38bdf8", size: int = 24) -> QIcon:
        icon = MainWindowIcons._tabler_icon("folder-search", color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        icon = MainWindowIcons._tabler_icon("folder", color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.8, size / 12.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        scale = size / 24.0
        painter.drawRoundedRect(QRectF(4.0 * scale, 6.0 * scale, 16.0 * scale, 11.0 * scale), 2.2 * scale, 2.2 * scale)
        painter.drawLine(QPointF(7.0 * scale, 10.0 * scale), QPointF(12.0 * scale, 10.0 * scale))
        painter.drawLine(QPointF(12.0 * scale, 10.0 * scale), QPointF(14.0 * scale, 8.0 * scale))
        painter.drawLine(QPointF(12.0 * scale, 10.0 * scale), QPointF(14.0 * scale, 12.0 * scale))
        painter.drawEllipse(QRectF(13.5 * scale, 12.0 * scale, 2.5 * scale, 2.5 * scale))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_rotate_icon(active: bool = False) -> QIcon:
        color = "#fbbf24" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("rotate-clockwise-2", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.3)
        painter.setPen(pen)
        painter.drawArc(4, 4, 14, 14, 40 * 16, 260 * 16)
        painter.drawLine(15, 3, 20, 3)
        painter.drawLine(20, 3, 20, 8)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_rotation_fill_icon(dark: bool = False) -> QIcon:
        # Filled square = new corner pixels are forced to 0 (dark/no-data).
        # Outline square = new corner pixels are stretched from the nearest
        # edge pixel (the previous, always-on behavior).
        color = "#fbbf24" if dark else "#94a3b8"
        icon = MainWindowIcons._tabler_icon(
            "square-rounded",
            color,
            24,
            stroke_width=2.1,
            fill=color if dark else "none",
        )
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.1)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(color) if dark else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(4.0, 4.0, 16.0, 16.0), 3.0, 3.0)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_crop_icon(active: bool = False) -> QIcon:
        color = "#38bdf8" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("crop", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.2)
        painter.setPen(pen)
        painter.drawLine(6, 4, 6, 18)
        painter.drawLine(6, 18, 16, 18)
        painter.drawLine(10, 6, 18, 6)
        painter.drawLine(18, 6, 18, 14)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_flip_horizontal_icon(active: bool = False) -> QIcon:
        color = "#22c55e" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("flip-horizontal", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2.3))
        painter.drawLine(12, 4, 12, 20)
        painter.drawLine(5, 8, 9, 12)
        painter.drawLine(9, 12, 5, 16)
        painter.drawLine(19, 8, 15, 12)
        painter.drawLine(15, 12, 19, 16)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_flip_vertical_icon(active: bool = False) -> QIcon:
        color = "#2dd4bf" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("flip-vertical", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2.3))
        painter.drawLine(4, 12, 20, 12)
        painter.drawLine(8, 5, 12, 9)
        painter.drawLine(12, 9, 16, 5)
        painter.drawLine(8, 19, 12, 15)
        painter.drawLine(12, 15, 16, 19)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_measure_icon(active: bool = False) -> QIcon:
        color = "#22c55e" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("ruler-measure", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(5.0, 17.5), QPointF(19.0, 17.5))
        painter.drawLine(QPointF(5.0, 8.0), QPointF(5.0, 20.0))
        painter.drawLine(QPointF(19.0, 8.0), QPointF(19.0, 20.0))
        painter.drawLine(QPointF(9.0, 12.0), QPointF(9.0, 17.5))
        painter.drawLine(QPointF(13.0, 10.0), QPointF(13.0, 17.5))
        painter.drawLine(QPointF(9.0, 6.0), QPointF(13.0, 6.0))
        painter.drawLine(QPointF(11.0, 4.0), QPointF(11.0, 8.0))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_undo_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("undo", "#f8fafc", 24, stroke_width=2.25)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f8fafc"), 2.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawArc(5, 5, 14, 14, 35 * 16, 250 * 16)
        painter.drawLine(QLineF(8.5, 7.2, 4.8, 8.2))
        painter.drawLine(QLineF(8.5, 7.2, 7.2, 3.6))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_redo_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("redo", "#f8fafc", 24, stroke_width=2.25)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f8fafc"), 2.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawArc(5, 5, 14, 14, -105 * 16, 250 * 16)
        painter.drawLine(QLineF(15.5, 7.2, 19.2, 8.2))
        painter.drawLine(QLineF(15.5, 7.2, 16.8, 3.6))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_spot_edit_icon(active: bool = False) -> QIcon:
        color = "#22c55e" if active else "#94a3b8"
        icon = MainWindowIcons._lucide_icon("square-pen", color, 24, stroke_width=2.3)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(4.2, 10.2, 7.2, 7.2))
        painter.setPen(QPen(QColor("#60a5fa" if active else "#60a5fa"), 2.6, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QLineF(10.2, 16.0, 17.8, 8.4))
        painter.drawLine(QLineF(16.2, 6.8, 19.4, 10.0))
        painter.setPen(QPen(QColor(color), 2.2, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QLineF(9.3, 17.0, 12.8, 16.2))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_roi_list_icon(active: bool = False) -> QIcon:
        color = "#f59e0b" if active else "#f8fafc"
        icon = MainWindowIcons._lucide_icon("table", color, 24, stroke_width=2.3)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.5)
        painter.setPen(pen)
        painter.drawRect(3, 3, 18, 18)
        painter.drawLine(QLineF(3, 11, 21, 11))
        painter.drawLine(QLineF(11, 3, 11, 21))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_color_swatch_icon(color: QColor, size: int = 16) -> QIcon:
        swatch = QPixmap(size, size)
        swatch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(swatch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#e2e8f0"), 1.1))
        painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(QRectF(1.5, 1.5, size - 3.0, size - 3.0), 3.0, 3.0)
        painter.end()
        return QIcon(swatch)

    def _make_relation_scope_button(self, active: bool, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setCheckable(True)
        button.setChecked(active)
        button.setIcon(self._make_relation_scope_icon(active))
        button.setIconSize(QSize(APP_THEME.plain_icon_inner, APP_THEME.plain_icon_inner))
        button.setFixedSize(APP_THEME.plain_icon_outer, APP_THEME.plain_icon_outer)
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.setToolTip(tooltip)
        button.toggled.connect(lambda checked, btn=button: btn.setIcon(self._make_relation_scope_icon(checked)))
        return button

    def _make_relation_scope_icon(self, active: bool = False) -> QIcon:
        color = "#22c55e" if active else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("relation-one-to-many", color, 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QLineF(6, 12, 13, 12))
        painter.drawLine(QLineF(13, 12, 18, 7))
        painter.drawLine(QLineF(13, 12, 18, 12))
        painter.drawLine(QLineF(13, 12, 18, 17))
        if active:
            painter.setBrush(QColor("#22c55e"))
        painter.drawEllipse(QRectF(3.5, 9.5, 5.0, 5.0))
        painter.drawEllipse(QRectF(18.0, 5.5, 3.5, 3.5))
        painter.drawEllipse(QRectF(18.0, 10.25, 3.5, 3.5))
        painter.drawEllipse(QRectF(18.0, 15.0, 3.5, 3.5))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_add_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("plus", "#22c55e", 24, stroke_width=2.8)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#22c55e"), 2.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(4.6, 4.6, 14.8, 14.8))
        painter.drawLine(QLineF(12.0, 7.3, 12.0, 16.7))
        painter.drawLine(QLineF(7.3, 12.0, 16.7, 12.0))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_move_icon() -> QIcon:
        icon = MainWindowIcons._tabler_icon("arrows-move", "#38bdf8", 24, stroke_width=2.2)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#38bdf8"), 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QLineF(12.0, 4.6, 12.0, 19.4))
        painter.drawLine(QLineF(4.6, 12.0, 19.4, 12.0))
        painter.drawLine(QLineF(12.0, 4.6, 9.0, 7.6))
        painter.drawLine(QLineF(12.0, 4.6, 15.0, 7.6))
        painter.drawLine(QLineF(12.0, 19.4, 9.0, 16.4))
        painter.drawLine(QLineF(12.0, 19.4, 15.0, 16.4))
        painter.drawLine(QLineF(4.6, 12.0, 7.6, 9.0))
        painter.drawLine(QLineF(4.6, 12.0, 7.6, 15.0))
        painter.drawLine(QLineF(19.4, 12.0, 16.4, 9.0))
        painter.drawLine(QLineF(19.4, 12.0, 16.4, 15.0))
        painter.setPen(QPen(QColor("#e0f2fe"), 2.0, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.drawEllipse(QRectF(9.5, 9.5, 5.0, 5.0))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_remove_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("trash-2", "#ef4444", 24, stroke_width=2.2)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ef4444"), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QLineF(7.0, 7.0, 17.0, 7.0))
        painter.drawLine(QLineF(9.5, 4.9, 14.5, 4.9))
        painter.drawLine(QLineF(8.4, 7.9, 9.4, 18.3))
        painter.drawLine(QLineF(15.6, 7.9, 14.6, 18.3))
        painter.drawLine(QLineF(9.6, 18.4, 14.4, 18.4))
        painter.drawLine(QLineF(11.0, 10.0, 11.0, 16.0))
        painter.drawLine(QLineF(13.0, 10.0, 13.0, 16.0))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_group_icon() -> QIcon:
        icon = MainWindowIcons._lucide_icon("group", "#a855f7", 24, stroke_width=2.2)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#a855f7"), 2))
        painter.setBrush(QColor("#a855f7"))
        painter.drawEllipse(4, 9, 5, 5)
        painter.drawEllipse(10, 5, 5, 5)
        painter.drawEllipse(15, 11, 5, 5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(8, 11, 12, 8)
        painter.drawLine(14, 9, 17, 12)
        painter.end()
        return QIcon(pixmap)

    def _reference_mode_icon(self, mode: str, active: bool) -> QIcon:
        color = "#84cc16" if active else "#f8fafc"
        if mode == "manual":
            icon = self._tabler_icon(
                "manual-gearbox",
                color=color,
                size=24,
                stroke_width=2.1,
                fill=color if active else "none",
            )
            if not icon.isNull():
                return icon
            return self._tabler_icon("manual-gearbox", color=color, size=24, stroke_width=2.1)
        return self._robot_icon(active)

    def _robot_icon(self, active: bool) -> QIcon:
        color = "#84cc16" if active else "#f8fafc"
        icon = self._tabler_icon("robot", color=color, size=24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        return self._tabler_icon("robot", color=color, size=24, stroke_width=2.1)

    def _chromatic_auto_icon(self, active: bool) -> QIcon:
        return self._robot_icon(active)

    def _sync_reference_selection_from_settings(self) -> None:
        combo_index = max(self.reference_mode_combo.findData(str(self._state.preprocessing.reference_mode or "auto")), 0)
        self.reference_mode_combo.blockSignals(True)
        self.reference_mode_combo.setCurrentIndex(combo_index)
        self.reference_mode_combo.blockSignals(False)
        if not self._spectral_cube_values or not self._wavelength_values:
            self._update_reference_controls()
            self._update_reference_summary()
            return
        spectral_cube_index, wavelength_index = self._initial_reference_indices()
        self.spectral_cube_slider.blockSignals(True)
        self.wavelength_slider.blockSignals(True)
        self.spectral_cube_slider.setValue(spectral_cube_index)
        self.wavelength_slider.setValue(wavelength_index)
        self.spectral_cube_slider.blockSignals(False)
        self.wavelength_slider.blockSignals(False)
        self._update_reference_controls()
        self._update_reference_summary()

    def _create_toolbar_action_button(self, action: QAction, *, primary: bool = False, icon_only: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly if icon_only else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        button.setAutoRaise(False)
        button.setIconSize(QSize(16, 16))
        if icon_only:
            button.setFixedSize(22, 20)
        button.setProperty("toolRole", "primary" if primary else "secondary")
        button.setProperty("iconOnly", icon_only)
        return button

    def _make_icon_tool_button(
        self,
        icon_name: str,
        color: str,
        tooltip: str,
        *,
        checkable: bool = False,
        icon: QIcon | None = None,
    ) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIcon(icon if icon is not None else self._mask_panel_icon(icon_name, color=color, size=APP_THEME.icon_button_inner))
        button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(transparent_icon_button_stylesheet())
        return button

    def _make_mask_morphology_button(self, operation: str, tooltip: str) -> QToolButton:
        button = self._make_icon_tool_button(
            "square-rounded-plus",
            "#f8fafc",
            tooltip,
            checkable=True,
            icon=self._make_mask_morphology_icon(operation),
        )
        hover, pressed, checked = icon_accent_colors("blue")
        button.setStyleSheet(
            transparent_icon_button_stylesheet(
                hover=hover,
                pressed=pressed,
                checked=checked,
            )
        )
        return button

    def _make_mask_morphology_icon(self, operation: str, *, color: str = "#f8fafc", size: int = 24) -> QIcon:
        if operation == "erode":
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none" />
  <path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0" />
  <path d="M6 10l2 2l-2 2" />
  <path d="M10 6l2 2l2 -2" />
  <path d="M18 10l-2 2l2 2" />
  <path d="M10 18l2 -2l2 2" />
</svg>"""
            return self._svg_icon_from_markup(svg, size=size)
        if operation == "dilate":
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none" />
  <path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0" />
  <path d="M7 10l-2 2l2 2" />
  <path d="M10 7l2 -2l2 2" />
  <path d="M17 10l2 2l-2 2" />
  <path d="M10 17l2 2l2 -2" />
</svg>"""
            return self._svg_icon_from_markup(svg, size=size)
        if operation == "open":
            icon = self._tabler_icon("book", color=color, size=size, stroke_width=2.2)
            if not icon.isNull():
                return icon
            return self._lucide_icon("book", color, size, stroke_width=2.2)
        if operation == "close":
            icon = self._tabler_icon("book-2", color=color, size=size, stroke_width=2.2)
            if not icon.isNull():
                return icon
            return self._tabler_icon("book", color=color, size=size, stroke_width=2.2)
        return QIcon()

    def _mask_panel_icon(self, icon_name: str, color: str, *, size: int = 20) -> QIcon:
        if icon_name in {"eye", "eye-closed"}:
            return self._draw_mask_panel_fallback_icon(icon_name, QColor(color), size=size)
        icon = self._tabler_icon(icon_name, color=color, size=size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        return self._draw_mask_panel_fallback_icon(icon_name, QColor(color), size=size)

    def _make_background_profile_icon(self, active: bool, *, size: int = 22) -> QIcon:
        color = "#38bdf8" if active else "#94a3b8"
        icon = self._tabler_icon("background", color, size, stroke_width=2.0)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        scale = size / 22.0
        painter.drawRoundedRect(QRectF(3.0 * scale, 4.0 * scale, 16.0 * scale, 14.0 * scale), 3.0 * scale, 3.0 * scale)
        painter.drawLine(QPointF(6.0 * scale, 15.0 * scale), QPointF(10.0 * scale, 11.0 * scale))
        painter.drawLine(QPointF(10.0 * scale, 11.0 * scale), QPointF(13.0 * scale, 13.5 * scale))
        painter.drawLine(QPointF(13.0 * scale, 13.5 * scale), QPointF(17.0 * scale, 9.5 * scale))
        painter.drawEllipse(QRectF(6.5 * scale, 6.5 * scale, 3.0 * scale, 3.0 * scale))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_link_toggle_icon(active: bool, *, size: int = 22) -> QIcon:
        color = "#22c55e" if active else "#ef4444"
        icon_name = "link" if active else "link-off"
        icon = MainWindowIcons._tabler_icon(icon_name, color, size, stroke_width=2.0)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.8, size / 12.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        scale = size / 24.0
        painter.drawEllipse(QRectF(5.0 * scale, 8.0 * scale, 5.0 * scale, 5.0 * scale))
        painter.drawEllipse(QRectF(13.0 * scale, 8.0 * scale, 5.0 * scale, 5.0 * scale))
        painter.drawLine(QLineF(10.0 * scale, 10.5 * scale, 14.0 * scale, 10.5 * scale))
        if active:
            painter.drawLine(QLineF(8.5 * scale, 8.5 * scale, 10.0 * scale, 10.0 * scale))
            painter.drawLine(QLineF(14.0 * scale, 10.0 * scale, 15.5 * scale, 8.5 * scale))
        else:
            painter.drawLine(QLineF(7.0 * scale, 15.0 * scale, 17.0 * scale, 5.0 * scale))
        painter.end()
        return QIcon(pixmap)

    def _make_analysis_spectrum_icon(self, active: bool, *, size: int = 24) -> QIcon:
        stroke_color = "#22c55e" if active else "#f8fafc"
        top_layer_color = "#22c55e" if active else stroke_color
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{stroke_color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path stroke="none" d="M0 0h24v24H0z" fill="none" />
            <path d="M12 4l-8 4l8 4l8 -4l-8 -4" stroke="{top_layer_color}" />
            <path d="M4 12l8 4l8 -4" />
            <path d="M4 16l8 4l8 -4" />
        </svg>
        """
        icon = self._svg_icon_from_markup(svg, size=size)
        if not icon.isNull():
            return icon
        return self._tabler_icon("stack-middle", stroke_color, size, stroke_width=2.0, fill="none")

    def _make_analysis_preview_icon(self, active: bool, *, size: int = 24) -> QIcon:
        color = "#22c55e" if active else "#94a3b8"
        icon = self._tabler_icon("eye", color, size, stroke_width=2.0)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), max(1.8, size / 14.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        scale = size / 24.0
        painter.drawEllipse(QRectF(5.0 * scale, 8.0 * scale, 14.0 * scale, 8.0 * scale))
        painter.drawEllipse(QRectF(9.25 * scale, 10.25 * scale, 5.5 * scale, 3.5 * scale))
        painter.end()
        return QIcon(pixmap)

    def _make_analysis_all_spectral_cubes_icon(self, active: bool, *, size: int = 24) -> QIcon:
        color = "#22c55e" if active else "#f8fafc"
        fill_color = "#22c55e" if active else "none"
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path stroke="none" d="M0 0h24v24H0z" fill="none" />
            <path d="M12 2l-8 4l8 4l8 -4l-8 -4" />
            <path d="M4 10l8 4l8 -4" />
            <path d="M4 18l8 4l8 -4" />
            <path d="M4 14l8 4l8 -4" fill="{fill_color}" />
        </svg>
        """
        icon = self._svg_icon_from_markup(svg, size=size)
        if not icon.isNull():
            return icon
        fallback = self._tabler_icon("stack-3", color, size, stroke_width=2.0, fill=color if active else "none")
        if not fallback.isNull():
            return fallback
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color))
        painter.setBrush(QColor(color) if active else Qt.BrushStyle.NoBrush)
        scale = size / 24.0
        painter.drawRoundedRect(QRectF(4.0 * scale, 3.0 * scale, 16.0 * scale, 4.0 * scale), 1.5 * scale, 1.5 * scale)
        painter.drawRoundedRect(QRectF(4.0 * scale, 9.0 * scale, 16.0 * scale, 4.0 * scale), 1.5 * scale, 1.5 * scale)
        painter.drawRoundedRect(QRectF(4.0 * scale, 15.0 * scale, 16.0 * scale, 4.0 * scale), 1.5 * scale, 1.5 * scale)
        painter.end()
        return QIcon(pixmap)

    def _make_analysis_stop_icon(self, active: bool, *, size: int = 24) -> QIcon:
        color = "#dc2626" if active else "#ef4444"
        icon = self._tabler_icon("player-stop-filled", color, size, stroke_width=2.0, fill=color)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        scale = size / 24.0
        painter.drawRoundedRect(QRectF(6.0 * scale, 6.0 * scale, 12.0 * scale, 12.0 * scale), 2.6 * scale, 2.6 * scale)
        painter.end()
        return QIcon(pixmap)

    def _make_spot_label_icon(self, visible: bool) -> QIcon:
        icon_name = "label-important" if visible else "label-off"
        color = "#22c55e" if visible else "#94a3b8"
        icon = self._tabler_icon(icon_name, color, 22, stroke_width=2.0)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        tag = QPainterPath()
        tag.moveTo(4.0, 6.0)
        tag.lineTo(14.5, 6.0)
        tag.lineTo(18.0, 11.0)
        tag.lineTo(14.5, 16.0)
        tag.lineTo(4.0, 16.0)
        tag.closeSubpath()
        painter.drawPath(tag)
        painter.drawEllipse(QRectF(6.6, 9.1, 2.2, 2.2))
        if not visible:
            painter.drawLine(QPointF(5.0, 17.0), QPointF(17.0, 5.0))
        painter.end()
        return QIcon(pixmap)

    def _make_cached_rois_icon(self, visible: bool, *, size: int = 24) -> QIcon:
        color = "#22c55e" if visible else "#94a3b8"
        icon = self._tabler_icon("database", color, size, stroke_width=2.0)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.5, size / 15.0))
        painter.setPen(pen)
        painter.setBrush(QColor(color) if visible else Qt.BrushStyle.NoBrush)
        scale = size / 24.0
        painter.drawEllipse(QRectF(5.0 * scale, 4.5 * scale, 14.0 * scale, 4.5 * scale))
        painter.drawRect(QRectF(5.0 * scale, 7.5 * scale, 14.0 * scale, 9.0 * scale))
        painter.drawEllipse(QRectF(5.0 * scale, 13.5 * scale, 14.0 * scale, 4.5 * scale))
        painter.end()
        return QIcon(pixmap)

    def _histogram_highlight_icon(self, color: str, *, size: int = 24) -> QIcon:
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">
  <path d="M3 13a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -6"
        fill="none" stroke="{color}" stroke-width="2"/>
  <path d="M15 9a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -10"
        fill="none" stroke="{color}" stroke-width="2"/>
  <path d="M9 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -14"
        fill="{color}" stroke="{color}" stroke-width="2"/>
  <path d="M4 20h14"
        fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
</svg>"""
        return self._svg_icon_from_markup(svg, size=size)

    def _make_view_toggle_icon(self, kind: str, visible: bool) -> QIcon:
        active_color = "#22c55e"
        inactive_color = "#94a3b8"
        color = active_color if visible else inactive_color
        if kind == "roi":
            icon = self._tabler_icon("current-location", color, 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
        elif kind == "rings":
            icon = self._tabler_icon("target", color, 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
        elif kind == "reference_points":
            icon_name = "map-pin" if visible else "map-pin-off"
            icon = self._tabler_icon(icon_name, color, 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
        elif kind == "reference_points_all":
            icon = self._tabler_icon("map-pins", color, 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
        elif kind == "mask":
            icon_name = "mask" if visible else "mask-off"
            icon = self._tabler_icon(icon_name, color, 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
        elif kind == "highlight":
            icon = self._histogram_highlight_icon(color, size=24)
            if not icon.isNull():
                return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "roi":
            painter.drawEllipse(QRectF(6.0, 6.0, 12.0, 12.0))
            painter.drawEllipse(QRectF(10.0, 10.0, 4.0, 4.0))
        elif kind == "rings":
            painter.drawEllipse(QRectF(4.5, 4.5, 15.0, 15.0))
            painter.drawEllipse(QRectF(9.0, 9.0, 6.0, 6.0))
        elif kind == "reference_points":
            painter.drawEllipse(QRectF(8.2, 4.5, 7.6, 7.6))
            painter.drawPath(QPainterPath())
            painter.drawLine(QLineF(12.0, 11.5, 12.0, 18.2))
            painter.drawLine(QLineF(12.0, 18.2, 9.0, 14.0))
            painter.drawLine(QLineF(12.0, 18.2, 15.0, 14.0))
        elif kind == "reference_points_all":
            painter.drawEllipse(QRectF(6.0, 4.0, 5.5, 5.5))
            painter.drawEllipse(QRectF(12.0, 4.0, 5.5, 5.5))
            painter.drawEllipse(QRectF(9.0, 11.0, 5.5, 5.5))
            painter.drawLine(QLineF(8.8, 9.2, 7.5, 14.2))
            painter.drawLine(QLineF(12.2, 9.2, 13.5, 14.2))
            painter.drawLine(QLineF(10.5, 9.2, 10.5, 16.0))
        elif kind == "mask":
            painter.drawRoundedRect(QRectF(4.5, 4.5, 15.0, 15.0), 3.0, 3.0)
        elif kind == "histogram_log":
            painter.drawLine(QLineF(6.0, 18.0, 6.0, 5.0))
            painter.drawLine(QLineF(6.0, 18.0, 19.0, 18.0))
            if visible:
                painter.drawLine(QLineF(8.0, 15.5, 10.5, 15.5))
                painter.drawLine(QLineF(8.0, 12.0, 13.5, 12.0))
                painter.drawLine(QLineF(8.0, 8.5, 17.0, 8.5))
                painter.drawText(QRectF(8.0, 4.0, 12.0, 8.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "log")
            else:
                painter.drawLine(QLineF(8.0, 15.5, 16.5, 15.5))
                painter.drawLine(QLineF(8.0, 12.0, 16.5, 12.0))
                painter.drawLine(QLineF(8.0, 8.5, 16.5, 8.5))
                painter.drawText(QRectF(8.0, 4.0, 12.0, 8.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "lin")
        else:
            painter.drawRoundedRect(QRectF(5.0, 6.0, 4.0, 10.0), 1.0, 1.0)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(10.0, 4.0, 4.0, 14.0), 1.0, 1.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(15.0, 8.0, 4.0, 10.0), 1.0, 1.0)
        painter.end()
        return QIcon(pixmap)

    def _background_exclusion_icon(self, icon_name: str, enabled: bool, *, size: int = 18) -> QIcon:
        color = "#22c55e" if enabled else "#94a3b8"
        icon = self._tabler_icon(icon_name, color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        scale = size / 18.0
        painter.drawRoundedRect(QRectF(3.0 * scale, 3.0 * scale, 12.0 * scale, 12.0 * scale), 3.0 * scale, 3.0 * scale)
        painter.end()
        return QIcon(pixmap)

    def _create_view_toggle_button(self, kind: str, visible: bool, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("toolbarPlainIconButton")
        button.setAutoRaise(True)
        button.setCheckable(True)
        button.setChecked(bool(visible))
        button.setIcon(self._make_view_toggle_icon(kind, bool(visible)))
        button.setIconSize(QSize(APP_THEME.plain_icon_inner, APP_THEME.plain_icon_inner))
        button.setFixedSize(APP_THEME.plain_icon_outer, APP_THEME.plain_icon_outer)
        button.setToolTip(tooltip)
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.toggled.connect(lambda checked, target=button, toggle_kind=kind: self._update_view_toggle_icon(target, toggle_kind, checked))
        return button

    def _update_view_toggle_icon(self, button: QToolButton, kind: str, visible: bool) -> None:
        button.setIcon(self._make_view_toggle_icon(kind, bool(visible)))

    def _refresh_view_toggle_icons(self) -> None:
        mappings = [
            (getattr(self, "show_rois_check", None), "roi", self._rois_visible),
            (getattr(self, "show_rings_check", None), "rings", self._reference_visible),
            (getattr(self, "show_reference_points_check", None), "reference_points", self._reference_points_visible),
            (
                getattr(self, "chromatic_reference_points_all_button", None),
                "reference_points_all",
                self._chromatic_reference_points_all_visible,
            ),
            (getattr(self, "show_mask_check", None), "mask", self._mask_visible),
            (getattr(self, "show_highlight_check", None), "highlight", self._highlight_visible),
        ]
        for button, kind, visible in mappings:
            if button is not None:
                self._update_view_toggle_icon(button, kind, visible)
        if hasattr(self, "histogram_y_scale_button"):
            self.histogram_y_scale_button.blockSignals(True)
            self.histogram_y_scale_button.setChecked(self._histogram_log_y_enabled)
            self.histogram_y_scale_button.blockSignals(False)

    def _make_mask_toggle_icon(self, visible: bool, *, size: int = 24) -> QIcon:
        color = QColor("#22c55e" if visible else "#94a3b8")
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(3.5, 3.5, size - 7.0, size - 7.0)
        if visible:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 4.0, 4.0)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            text_rect = QRectF(6.0, 4.5, size - 12.0, size - 9.0)
            font = painter.font()
            font.setBold(True)
            font.setPixelSize(max(int(size * 0.58), 10))
            painter.setFont(font)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "M")
        else:
            pen = QPen(color, 2.0)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 4.0, 4.0)
            font = painter.font()
            font.setBold(True)
            font.setPixelSize(max(int(size * 0.58), 10))
            painter.setFont(font)
            painter.drawText(QRectF(6.0, 4.5, size - 12.0, size - 9.0), Qt.AlignmentFlag.AlignCenter, "M")
        painter.end()
        return QIcon(pixmap)

    def _make_scale_bar_icon(self, visible: bool, *, size: int = 24) -> QIcon:
        color = "#22c55e" if visible else "#f8fafc"
        svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="120" height="30" viewBox="0 0 120 30" fill="none">
            <line x1="6" y1="20" x2="50" y2="20" stroke="{color}" stroke-width="3.5" />
            <line x1="6" y1="10" x2="6" y2="28" stroke="{color}" stroke-width="3.5" />
            <line x1="50" y1="10" x2="50" y2="28" stroke="{color}" stroke-width="3.5" />
        </svg>
        """
        pixmap = QPixmap(320, 120)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0.0, 0.0, 320.0, 120.0))
        painter.end()
        return QIcon(pixmap)

    def _create_scale_bar_toggle_button(self, visible: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("toolbarPlainIconButton")
        button.setAutoRaise(True)
        button.setCheckable(True)
        button.setChecked(bool(visible))
        button.setIcon(self._make_scale_bar_icon(bool(visible)))
        button.setIconSize(QSize(48, 24))
        button.setFixedSize(56, APP_THEME.plain_icon_outer)
        button.setToolTip("Show or hide the scale bar.")
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.toggled.connect(lambda checked, target=button: target.setIcon(self._make_scale_bar_icon(bool(checked))))
        return button

    def _create_unit_toggle_button(self) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("toolbarPlainIconButton")
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._normalize_display_units()
        button.setText("µm" if str(self._state.preprocessing.display_units or "px") == "um" else "px")
        button.setToolTip("Switch between pixel and micrometer display units.")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(34)
        button.setStyleSheet(
            """
            QToolButton {
                background: transparent;
                border: none;
                padding: 0 2px;
                font-weight: 700;
                font-size: 12px;
                color: #f8fafc;
            }
            QToolButton:hover {
                color: #22c55e;
            }
            """
        )
        self._refresh_unit_toggle_button(button)
        return button

    def _refresh_unit_toggle_button(self, button: QToolButton | None = None) -> None:
        target = self.measurement_unit_button if button is None else button
        if target is None:
            return
        self._normalize_display_units()
        is_um = str(self._state.preprocessing.display_units or "px") == "um"
        target.setText("µm" if is_um else "px")
        target.setStyleSheet(
            f"""
            QToolButton {{
                background: transparent;
                border: none;
                padding: 0 2px;
                font-weight: 700;
                font-size: 12px;
                color: {'#22c55e' if is_um else '#f8fafc'};
            }}
            QToolButton:hover {{
                color: #22c55e;
            }}
            """
        )

    def _display_unit_text(self) -> str:
        return "µm" if str(self._state.preprocessing.display_units or "px") == "um" else "px"

    def _build_measurement_controls_row(self) -> QWidget:
        row = QWidget(self.image_toolbar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.measurement_status_label, 1)
        layout.addWidget(QLabel("Δx"))
        layout.addWidget(self.measurement_um_x_spin)
        layout.addWidget(QLabel("Δy"))
        layout.addWidget(self.measurement_um_y_spin)
        layout.addWidget(self.measurement_apply_button)
        return row

    def _create_image_tool_icon_button(self, action: QAction, *, accent: str) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setObjectName("leftSpotToolButton")
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(28, 28))
        button.setFixedSize(36, 36)
        hover, pressed, checked = icon_accent_colors(accent)
        button.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background: {hover};
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }}
            QToolButton:pressed {{
                background: {pressed};
                border: 1px solid #94a3b8;
                border-radius: 8px;
            }}
            QToolButton:checked {{
                background: {checked};
                border: 1px solid #22c55e;
                border-radius: 8px;
            }}
        """)
        return button

    def _create_rotation_fill_toggle_button(self) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("leftSpotToolButton")
        button.setAutoRaise(True)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(28, 28))
        button.setFixedSize(36, 36)
        hover, pressed, checked = icon_accent_colors("yellow")
        button.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background: {hover};
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }}
            QToolButton:pressed {{
                background: {pressed};
                border: 1px solid #94a3b8;
                border-radius: 8px;
            }}
            QToolButton:checked {{
                background: {checked};
                border: 1px solid #22c55e;
                border-radius: 8px;
            }}
        """)
        return button

    def _create_label_visibility_button(self, visible: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("spotLabelIconButton")
        button.setAutoRaise(True)
        button.setIconSize(QSize(APP_THEME.plain_icon_inner, APP_THEME.plain_icon_inner))
        button.setFixedSize(APP_THEME.plain_icon_outer, APP_THEME.plain_icon_outer)
        button.setCheckable(True)
        button.setChecked(bool(visible))
        button.setIcon(self._make_spot_label_icon(bool(visible)))
        button.setToolTip("Show or hide ROI labels. This works independently of manual ROI editing.")
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.toggled.connect(self._update_spot_label_button_icon)
        return button

    def _update_spot_label_button_icon(self, checked: bool) -> None:
        icon = self._make_spot_label_icon(bool(checked))
        for attr_name in ("roi_editor_labels_button", "bottom_roi_labels_button"):
            button = getattr(self, attr_name, None)
            if button is not None:
                button.setIcon(icon)

    @staticmethod
    def _draw_mask_panel_fallback_icon(icon_name: str, color: QColor, *, size: int = 20) -> QIcon:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if icon_name in {"square-rounded-plus", "square-rounded-minus"}:
            painter.drawRoundedRect(QRectF(3.0, 3.0, size - 6.0, size - 6.0), 4.0, 4.0)
            painter.drawLine(QPointF(6.0, size / 2.0), QPointF(size - 6.0, size / 2.0))
            if icon_name.endswith("plus"):
                painter.drawLine(QPointF(size / 2.0, 6.0), QPointF(size / 2.0, size - 6.0))
        elif icon_name == "sparkles":
            painter.drawLine(QPointF(size / 2.0, 3.5), QPointF(size / 2.0, 8.0))
            painter.drawLine(QPointF(size / 2.0, 12.0), QPointF(size / 2.0, size - 3.5))
            painter.drawLine(QPointF(3.5, size / 2.0), QPointF(8.0, size / 2.0))
            painter.drawLine(QPointF(12.0, size / 2.0), QPointF(size - 3.5, size / 2.0))
            painter.drawLine(QPointF(5.2, 5.2), QPointF(8.3, 8.3))
            painter.drawLine(QPointF(size - 5.2, 5.2), QPointF(size - 8.3, 8.3))
            painter.drawLine(QPointF(5.2, size - 5.2), QPointF(8.3, size - 8.3))
            painter.drawLine(QPointF(size - 5.2, size - 5.2), QPointF(size - 8.3, size - 8.3))
        elif icon_name == "download":
            painter.drawLine(QPointF(size / 2.0, 4.0), QPointF(size / 2.0, size - 8.0))
            painter.drawLine(QPointF(size / 2.0, size - 8.0), QPointF(size / 2.0 - 3.2, size - 11.2))
            painter.drawLine(QPointF(size / 2.0, size - 8.0), QPointF(size / 2.0 + 3.2, size - 11.2))
            painter.drawLine(QPointF(4.5, size - 4.5), QPointF(size - 4.5, size - 4.5))
        elif icon_name == "upload":
            painter.drawLine(QPointF(size / 2.0, size - 4.0), QPointF(size / 2.0, 8.0))
            painter.drawLine(QPointF(size / 2.0, 8.0), QPointF(size / 2.0 - 3.2, 11.2))
            painter.drawLine(QPointF(size / 2.0, 8.0), QPointF(size / 2.0 + 3.2, 11.2))
            painter.drawLine(QPointF(4.5, size - 4.5), QPointF(size - 4.5, size - 4.5))
        elif icon_name in {"eye", "eye-closed"}:
            path = QPainterPath()
            path.moveTo(4.5, size / 2.0)
            path.quadTo(size / 2.0, 5.5, size - 4.5, size / 2.0)
            path.quadTo(size / 2.0, size - 5.5, 4.5, size / 2.0)
            painter.drawPath(path)
            painter.drawEllipse(QRectF(size / 2.0 - 2.0, size / 2.0 - 2.0, 4.0, 4.0))
            if icon_name == "eye-closed":
                painter.drawLine(QPointF(5.0, size - 5.0), QPointF(size - 5.0, 5.0))
        else:
            painter.drawEllipse(QRectF(4.0, 4.0, size - 8.0, size - 8.0))

        painter.end()
        return QIcon(pixmap)

    def _create_left_spot_editor_button(self, action: QAction, *, primary: bool = False, accent: str = "neutral") -> QToolButton:
        button = self._create_toolbar_action_button(action, primary=primary, icon_only=True)
        button.setObjectName("leftSpotToolButton")
        button.setAutoRaise(True)
        button.setIconSize(QSize(APP_THEME.icon_button_inner, APP_THEME.icon_button_inner))
        button.setFixedSize(APP_THEME.icon_button_outer, APP_THEME.icon_button_outer)
        hover, pressed, checked = icon_accent_colors(accent)
        button.setStyleSheet(
            transparent_icon_button_stylesheet(
                hover=hover,
                pressed=pressed,
                checked=checked,
            )
        )
        return button

    def _clear_layout(self, layout: QHBoxLayout | QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _populate_left_spot_editor_controls(self) -> None:
        self._clear_layout(self.left_spot_editor_layout)
        buttons = [
            (self.roi_list_action, {"accent": "orange"}),
            (self.spot_edit_action, {"primary": True}),
            (self.roi_add_action, {"accent": "green"}),
            (self.roi_array_action, {"accent": "green"}),
            (self.roi_move_action, {"accent": "blue"}),
            (self.remove_rois_action, {"accent": "red"}),
        ]
        for action, kwargs in buttons:
            self.left_spot_editor_layout.addWidget(self._create_left_spot_editor_button(action, **kwargs))
        self.left_spot_editor_layout.addWidget(self.roi_editor_labels_button)
        self.left_spot_editor_layout.addStretch(1)

    def _create_toolbar_row(self, widgets: list[QWidget]) -> QWidget:
        row = QWidget(self.image_toolbar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        return row

    def _create_toolbar_section(self, title: str, rows: list[QWidget]) -> QWidget:
        section = QWidget(self.image_toolbar)
        section.setObjectName("toolbarSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)
        for row in rows:
            layout.addWidget(row)
        return section

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction("Load dataset...", self._dataset_controller.browse_folder)
        file_menu.addAction("Export Stack to Zarr...", self._dataset_controller.export_current_dataset_to_ome_zarr)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        edit_menu = menu_bar.addMenu("&Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addAction(self.clear_roi_selection_button.text(), self._clear_spot_selection)

        view_menu = menu_bar.addMenu("&View")
        view_menu.addAction(self.reset_layout_action)
        view_menu.addAction(self.reset_dock_layout_action)
        view_menu.addAction(self.expand_left_panels_action)
        view_menu.addAction(self.collapse_left_panels_action)
        view_menu.addSeparator()
        dock_menu = view_menu.addMenu("Panels")
        dock_menu.addAction(self.show_all_panels_action)
        dock_menu.addAction(self.hide_all_panels_action)
        dock_menu.addSeparator()
        self.workflow_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+1"))
        self.image_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+2"))
        self.histogram_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+3"))
        self.spectra_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+4"))
        self.sensorgram_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+5"))
        dock_menu.addAction(self.workflow_panel.toggleViewAction())
        dock_menu.addAction(self.image_panel.toggleViewAction())
        dock_menu.addAction(self.histogram_panel.toggleViewAction())
        dock_menu.addAction(self.spectra_panel.toggleViewAction())
        dock_menu.addAction(self.sensorgram_panel.toggleViewAction())
        dock_menu.addAction(self.roi_list_panel.toggleViewAction())
        view_menu.addSeparator()
        self.theme_blue_action = view_menu.addAction("Blue Dark Theme")
        self.theme_blue_action.setCheckable(True)
        self.theme_gray_action = view_menu.addAction("Gray Dark Theme")
        self.theme_gray_action.setCheckable(True)
        self.theme_blue_action.triggered.connect(lambda checked: checked and self._set_ui_theme("blue"))
        self.theme_gray_action.triggered.connect(lambda checked: checked and self._set_ui_theme("gray"))
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        theme_group.addAction(self.theme_blue_action)
        theme_group.addAction(self.theme_gray_action)
        current_theme = str(self._settings.value("ui/theme", "blue"))
        self.theme_gray_action.setChecked(current_theme == "gray")
        self.theme_blue_action.setChecked(current_theme != "gray")
        view_menu.addSeparator()
        ui_scale_menu = view_menu.addMenu("UI Scale")
        ui_scale_group = QActionGroup(self)
        ui_scale_group.setExclusive(True)
        self._ui_scale_actions: dict[str, QAction] = {}
        _UI_SCALE_OPTIONS = [
            ("auto",  "Auto (system default)"),
            ("0.75",  "75%"),
            ("0.85",  "85%"),
            ("1.0",   "100%  —  Normal"),
            ("1.15",  "115%"),
            ("1.25",  "125%"),
            ("1.5",   "150%"),
            ("1.75",  "175%"),
            ("2.0",   "200%"),
        ]
        current_scale = str(self._settings.value("ui/scale_factor", "auto") or "auto")
        for _sv, _sl in _UI_SCALE_OPTIONS:
            _a = ui_scale_menu.addAction(_sl)
            _a.setCheckable(True)
            _a.setChecked(_sv == current_scale)
            _a.triggered.connect(lambda checked, v=_sv: checked and self._set_ui_scale_factor(v))
            ui_scale_group.addAction(_a)
            self._ui_scale_actions[_sv] = _a

        analysis_menu = menu_bar.addMenu("&Analysis")
        analysis_menu.addAction(self.calculate_spectrum_action)

        preferences_menu = menu_bar.addMenu("&Preferences")
        preferences_menu.addAction(self.reset_layout_action)
        preferences_menu.addSeparator()
        preferences_menu.addAction("Export processing profile", self._export_processing_profile)
        preferences_menu.addAction("Import processing profile", self._import_processing_profile)
        preferences_menu.addSeparator()
        startup_restore_menu = preferences_menu.addMenu("Startup restore")
        startup_restore_group = QActionGroup(self)
        startup_restore_group.setExclusive(True)
        self.startup_restore_timeout_actions = {}
        for seconds, label in ((5, "Prompt restore (5s)"), (0, "Auto restore (0s)")):
            action = startup_restore_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, value=seconds: checked and self._set_startup_restore_timeout_seconds(value))
            startup_restore_group.addAction(action)
            self.startup_restore_timeout_actions[seconds] = action
        current_timeout = self._startup_restore_timeout_seconds()
        if current_timeout not in self.startup_restore_timeout_actions:
            current_timeout = 5
        self._set_startup_restore_timeout_seconds(current_timeout)


        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction(self.shortcuts_action)
        help_menu.addAction("Workflow notes", self._show_workflow_notes)
        help_menu.addAction(self.about_action)

    def _create_view_control(self, name: str, toggle: QWidget, color_button: QToolButton, slider: QWidget) -> QWidget:
        row = QWidget(self.image_toolbar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        color_button.setProperty("toolRole", "swatch")
        slider.setFixedWidth(24)
        if name:
            name_label = QLabel(name, row)
            name_label.setObjectName("toolbarMiniLabel")
            name_label.setMinimumWidth(max(32, name_label.fontMetrics().horizontalAdvance(name) + 6))
            layout.addWidget(name_label)
        layout.addWidget(toggle)
        layout.addWidget(color_button)
        layout.addWidget(slider)
        return row

    def _create_toolbar_icon_toggle_control(self, name: str, toggle: QWidget) -> QWidget:
        row = QWidget(self.image_toolbar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if name:
            name_label = QLabel(name, row)
            name_label.setObjectName("toolbarMiniLabel")
            name_label.setMinimumWidth(max(42, name_label.fontMetrics().horizontalAdvance(name) + 6))
            layout.addWidget(name_label)
        layout.addWidget(toggle)
        return row

    def _set_combo_width(self, combo: QComboBox, texts: list[str], *, minimum: int = 58) -> None:
        widest = max((combo.fontMetrics().horizontalAdvance(text) for text in texts), default=0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setFixedWidth(max(minimum, widest + 10))

    def _apply_right_aligned_control_text(self) -> None:
        for line_edit in self.findChildren(QLineEdit):
            line_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for spinbox in self.findChildren(QAbstractSpinBox):
            make_compact_spinbox(spinbox, height=APP_THEME.compact_icon_outer)
        for combo in self.findChildren(QComboBox):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setReadOnly(True)
                line_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                line_edit.setFrame(False)

    def _apply_compact_control_widths(self) -> None:
        # Spin box widths are no longer hand-computed here: make_compact_spinbox()
        # (called from _apply_right_aligned_control_text for every spin box) removes the
        # native up/down buttons and the shared theme sets min-width: 0px, so Qt's own
        # sizeHint() - which already accounts for the suffix and maximum value - produces
        # a correctly compact width without per-widget guessed "widest text" maintenance.
        self._set_combo_width(self.chromatic_feature_count_spin, ["5", "15", "30"], minimum=54)
        self._set_combo_width(self.background_smoothing_binning_combo, ["4x4"])
        self._set_combo_width(self.mask_mode_combo, ["Local contrast"], minimum=96)
        self._set_combo_width(self.mask_draw_mode_combo, ["Erase"])
        self._set_combo_width(self.ome_zarr_shard_mode_combo, ["1 spectral cube"], minimum=220)
        self._set_combo_width(self.analysis_metric_combo, ["Maximum", "Centroid"], minimum=80)
        self._set_combo_width(self.chromatic_subpixel_precision_combo, ["1", "4", "9"], minimum=42)
        navigation_control_width = max(self.spectral_cube_spin.sizeHint().width(), self.wavelength_spin.sizeHint().width(), 82)
        # The spin boxes keep a hard fixed width so their digits never get clipped.
        # The sliders only get a floor (minimum width): they are free to shrink toward
        # it - and grow past their preferred width when there's room - so a narrow
        # panel takes space away from the slider track, not from the number boxes.
        navigation_slider_minimum_width = 60
        self.spectral_cube_spin.setFixedWidth(navigation_control_width)
        self.wavelength_spin.setFixedWidth(navigation_control_width)
        self.spectral_cube_slider.setMinimumWidth(navigation_slider_minimum_width)
        self.wavelength_slider.setMinimumWidth(navigation_slider_minimum_width)

    def _chromatic_transform_icon(self, has_models: bool) -> QIcon:
        if has_models:
            icon = self._tabler_icon("wand", "#a855f7", 24, stroke_width=2.1)
            if not icon.isNull():
                return icon
            pixmap = QPixmap(24, 24)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#a855f7"), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QLineF(6.0, 18.0, 18.0, 6.0))
            painter.drawLine(QLineF(15.0, 6.0, 18.0, 9.0))
            painter.drawEllipse(QRectF(7.0, 1.8, 4.0, 4.0))
            painter.drawEllipse(QRectF(17.0, 11.8, 4.0, 4.0))
            painter.end()
            return QIcon(pixmap)
        icon = self._tabler_icon("wand-off", "#94a3b8", 24, stroke_width=2.1)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#94a3b8"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QLineF(7.0, 17.0, 17.0, 7.0))
        painter.drawLine(QLineF(8.0, 18.0, 18.0, 8.0))
        painter.drawLine(QLineF(9.0, 14.0, 14.0, 9.0))
        painter.drawLine(QLineF(10.5, 4.5, 13.0, 7.0))
        painter.drawLine(QLineF(14.8, 2.4, 14.8, 4.6))
        painter.drawLine(QLineF(14.8, 4.6, 17.0, 4.6))
        painter.drawLine(QLineF(18.2, 7.2, 19.8, 8.8))
        painter.drawLine(QLineF(18.4, 5.2, 18.4, 7.4))
        painter.drawLine(QLineF(18.4, 7.4, 20.6, 7.4))
        painter.end()
        return QIcon(pixmap)
