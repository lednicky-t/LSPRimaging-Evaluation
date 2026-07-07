from __future__ import annotations

import json
import csv
import logging
from html import escape
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from math import hypot
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PIL import Image
from PyQt6.QtCore import (
    QByteArray,
    QEvent,
    QItemSelectionModel,
    QLineF,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QGuiApplication, QIcon, QKeyEvent, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextCursor
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QFrame,
    QMessageBox,
    QInputDialog,
    QMenu,
    QPushButton,
    QButtonGroup,
    QProgressBar,
    QGraphicsPathItem,
    QAbstractSpinBox,
    QScrollArea,
    QSizePolicy,
    QHeaderView,
    QSpinBox,
    QTabWidget,
    QSplitter,
    QSlider,
    QStyle,
    QStyleOptionProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QRubberBand,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from scipy import ndimage

from lspr_ui import (
    APP_THEME,
    BLUE_DARK_THEME,
    GRAY_DARK_THEME,
    collapsible_pin_stylesheet,
    collapsible_toggle_stylesheet,
    dark_image_toolbar_stylesheet,
    get_active_theme,
    icon_accent_colors,
    section_header_label_stylesheet,
    set_active_theme,
    standard_push_button_stylesheet,
    transparent_icon_button_stylesheet,
)
from lspr_imaging_app.gui.roi_table_helpers import (
    RoiTableRowData,
    append_roi_table_row,
    format_xy_value,
    make_color_swatch_icon,
    roi_table_headers,
)
from lspr_imaging_app.gui.roi_table_controller import RoiTableController
from lspr_imaging_app.gui.mask_controller import MaskController
from lspr_imaging_app.gui.chromatic_controller import ChromaticController
from lspr_imaging_app.gui.shortcut_manager import ShortcutManager
from lspr_imaging_app.gui.session_state_manager import SessionStateManager
from lspr_imaging_app.gui.plot_manager import PlotManager
from lspr_imaging_app.gui.ui_state_manager import UIStateManager
from lspr_imaging_app.gui.panel_help_registry import panel_help_text
from lspr_imaging_app.gui.shortcut_registry import shortcuts_text
from lspr_imaging_app.gui.ui_helpers import (
    alpha01,
    area_delta_text,
    area_value_text,
    chromatic_feature_count_value,
    chromatic_subpixel_precision_value,
    current_ome_zarr_compression_enabled,
    display_length_suffix,
    format_length_display_value,
    length_display_to_px,
    length_px_to_display,
    normalized_odd_count,
    read_bool_setting,
    read_float_setting,
    ring_area_text,
    settings_bool,
    settings_int,
)
from lspr_imaging_app.gui.roi_overlay_helpers import resolved_reference_color, resolved_roi_color
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.gui.analysis_tasks import _ome_zarr_export_task
from lspr_imaging_app.gui.dataset_controller import DatasetController
from lspr_imaging_app.gui.image_controller import ImageController
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.domain.roi_editor_tools import (
    create_rois_from_template,
    create_rois_from_template_grid,
    move_roi_from_template,
    roi_top_left_from_center,
)
from lspr_imaging_app.version import version_string
from lspr_imaging_app.domain.models import (
    AnalysisState,
    AbsorbanceSpectrumResult,
    ChromaticLandmarkObservation,
    ChromaticTransformModel,
    CropDefinition,
    AreaRoi,
    FitResult,
    MaskSettings,
    AreaRoiDetectionSettings,
    AreaRoiGroup,
)
from lspr_imaging_app.io.dataset import (
    dataset_record_map,
    dataset_is_ome_zarr,
    export_ome_zarr_dataset,
    load_dataset,
    load_image_array,
)
from lspr_imaging_app.processing.analysis import absorbance_from_means, fit_absorbance_curve, metric_value_from_fit
from lspr_imaging_app.processing.chromatic import (
    ChromaticRegistrationResult,
    apply_affine_to_points,
    default_landmark_anchors,
    detect_regional_landmarks,
    fit_affine_matrix,
    estimate_affine_chromatic_transform,
    identity_affine_matrix,
    invert_affine_matrix,
    track_landmarks,
    transformed_annulus_mask,
    transformed_circle_points,
    transformed_disk_mask,
    transform_spots_affine,
)
from lspr_imaging_app.processing.preprocess import (
    apply_preprocessing,
    apply_spatial_mask,
    apply_spatial_preprocessing,
    estimate_background_profile,
    spatial_output_shape,
    spatial_coordinate_maps,
)
from lspr_imaging_app.processing.spot_detection import detect_spots, ignored_pixel_mask, refresh_roi_metrics
from lspr_imaging_app.storage.workspace import (
    load_preprocessing,
    load_processing_profile,
    save_preprocessing,
    save_processing_profile,
)

try:
    import tabler_icons
except Exception:  # pragma: no cover - optional icon dependency
    tabler_icons = None

try:
    import lucide
except Exception:  # pragma: no cover - optional icon dependency
    lucide = None


class ClickableIconLabel(QLabel):
    clicked = pyqtSignal()

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
        if tabler_icons is None:
            return QIcon()
        try:
            svg = str(
                tabler_icons.get_icon(
                    name,
                    size=size,
                    stroke=color,
                    fill=fill,
                    stroke_width=stroke_width,
                    stroke_linecap="round",
                    stroke_linejoin="round",
                )
            )
        except Exception:
            return QIcon()
        return MainWindowIcons._svg_icon_from_markup(svg, size=size)

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

    def _start_ome_zarr_export(self, destination: Path, chunk_size_px: int, *, compression_enabled: bool = True, shard_mode: str = "per_image") -> None:
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
        worker = FunctionWorker(
            _ome_zarr_export_task,
            dataset,
            destination,
            int(chunk_size_px),
            bool(compression_enabled),
            preprocessing_snapshot,
            shard_mode,
            cancel_event=self._ome_zarr_export_cancel_event,
            supports_progress=True,
        )
        tools_text = "applied" if bool(getattr(preprocessing_snapshot, "image_tools_enabled", False)) else "ignored"
        shard_text = "per_spectral_cube" if shard_mode == "per_spectral_cube" else "per_image"
        self._append_workflow_log(
            f"OME-Zarr export start | chunks {chunk_size_px}px | shard {shard_text} | compression {'on' if compression_enabled else 'off'} | image tools {tools_text}",
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
