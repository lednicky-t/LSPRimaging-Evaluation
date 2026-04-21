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
from PyQt6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QGuiApplication, QIcon, QKeyEvent, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextCursor
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

from lspr_imaging_app.gui.theme import (
    APP_THEME,
    BLUE_DARK_THEME,
    GRAY_DARK_THEME,
    collapsible_pin_stylesheet,
    collapsible_toggle_stylesheet,
    dark_image_toolbar_stylesheet,
    icon_accent_colors,
    get_active_theme,
    set_active_theme,
    section_header_label_stylesheet,
    standard_push_button_stylesheet,
    transparent_icon_button_stylesheet,
)
from lspr_imaging_app.gui.spot_table_helpers import (
    SpotTableRowData,
    append_spot_table_row,
    format_xy_value,
    make_color_swatch_icon,
    spot_table_headers,
)
from lspr_imaging_app.gui.spot_table_controller import SpotTableController
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
    current_ome_zarr_chunk_size,
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
from lspr_imaging_app.gui.spot_overlay_helpers import resolved_ring_color, resolved_spot_color
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.gui.dataset_controller import DatasetController
from lspr_imaging_app.gui.image_controller import ImageController
from lspr_imaging_app.domain.models import (
    AnalysisState,
    AbsorbanceSpectrumResult,
    ChromaticLandmarkObservation,
    ChromaticTransformModel,
    CropDefinition,
    DetectedSpot,
    FitResult,
    MaskSettings,
    SpotDetectionSettings,
    SpotGroup,
)
from lspr_imaging_app.io.dataset import (
    dataset_record_map,
    dataset_is_ome_zarr,
    export_ome_zarr_dataset,
    load_dataset,
    load_image_array,
    load_image_shape,
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
from lspr_imaging_app.processing.spot_detection import detect_spots, ignored_pixel_mask, refresh_spot_metrics
from lspr_imaging_app.storage.workspace import (
    load_preprocessing,
    load_processing_profile,
    save_preprocessing,
    save_processing_profile,
)

SUCCESS_LOG_LEVEL = 25
logging.addLevelName(SUCCESS_LOG_LEVEL, "SUCCESS")

try:
    import tabler_icons
except Exception:  # pragma: no cover - optional dependency
    tabler_icons = None

try:
    import lucide
except Exception:  # pragma: no cover - optional dependency
    lucide = None


class ResponsiveDoubleSpinBox(QDoubleSpinBox):
    def stepBy(self, steps: int) -> None:  # type: ignore[override]
        step = float(self.singleStep()) if float(self.singleStep()) > 0 else 1.0
        target = float(self.value()) + float(steps) * step
        minimum = float(self.minimum())
        maximum = float(self.maximum())
        self.setValue(float(np.clip(target, minimum, maximum)))


class CollapsibleSection(QWidget):
    expanded_changed = pyqtSignal(bool)
    pin_changed = pyqtSignal(bool)
    apply_changed = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        applied: bool | None = None,
        apply_tooltip: str | None = None,
        help_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._toggle = QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setAutoRaise(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.NoArrow)
        self._toggle.setIcon(PanelContainer._make_chevron_icon(self, expanded))
        self._toggle.setIconSize(QSize(12, 12))
        self._toggle.setStyleSheet(collapsible_toggle_stylesheet())
        self._toggle.toggled.connect(self._set_expanded)

        self._pin_button = QToolButton(self)
        self._pin_button.setText("")
        self._pin_button.setCheckable(True)
        self._pin_button.setChecked(False)
        self._pin_button.setAutoRaise(True)
        self._pin_button.setIcon(PanelContainer._make_pin_icon(self, False))
        self._pin_button.setIconSize(QSize(16, 16))
        self._pin_button.setFixedSize(22, 22)
        self._pin_button.setToolTip("Pin this panel open so it is not auto-collapsed by the accordion.")
        self._pin_button.setStyleSheet(collapsible_pin_stylesheet())
        self._pin_button.toggled.connect(self.pin_changed.emit)
        self._pin_button.toggled.connect(self._update_pin_icon)

        self._apply_button: QToolButton | None = None
        if applied is not None:
            self._apply_button = QToolButton(self)
            self._apply_button.setText("")
            self._apply_button.setCheckable(True)
            self._apply_button.setChecked(bool(applied))
            self._apply_button.setAutoRaise(True)
            self._apply_button.setIcon(PanelContainer._make_apply_icon(self, bool(applied)))
            self._apply_button.setIconSize(QSize(18, 18))
            self._apply_button.setFixedSize(22, 22)
            self._apply_button.setToolTip(apply_tooltip or "Toggle whether this panel's calculations are applied.")
            self._apply_button.setStyleSheet(transparent_icon_button_stylesheet())
            self._apply_button.toggled.connect(self._set_applied)

        self._help_button = QToolButton(self)
        self._help_button.setText("")
        self._help_button.setAutoRaise(True)
        self._help_button.setCheckable(False)
        self._help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._help_button.setIcon(MainWindow._make_help_icon())
        self._help_button.setIconSize(QSize(16, 16))
        self._help_button.setFixedSize(22, 22)
        self._help_button.setToolTip("Show panel shortcuts and help.")
        self._help_button.setStyleSheet(transparent_icon_button_stylesheet())
        help_message = help_text or "No additional panel help is available yet."
        self._help_button.clicked.connect(lambda *_: QMessageBox.information(self, self._toggle.text(), help_message))

        self._content = content
        self._content.setParent(self)
        self._content.setVisible(expanded)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(self._toggle, 1)
        right_controls = QWidget(header)
        right_layout = QHBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(self._help_button, 0, Qt.AlignmentFlag.AlignVCenter)
        if self._apply_button is not None:
            right_layout.addWidget(self._apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self._pin_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(right_controls, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(header)
        layout.addWidget(self._content)

    def _set_expanded(self, expanded: bool) -> None:
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        self._content.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def _update_pin_icon(self, pinned: bool) -> None:
        self._pin_button.setIcon(self._make_pin_icon(pinned))

    def _set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setIcon(self._make_apply_icon(applied))
        self.apply_changed.emit(applied)

    def _make_chevron_icon(self, expanded: bool) -> QIcon:
        return PanelContainer._make_chevron_icon(self, expanded)

    def _make_pin_icon(self, pinned: bool) -> QIcon:
        return PanelContainer._make_pin_icon(self, pinned)

    def _make_apply_icon(self, applied: bool) -> QIcon:
        return PanelContainer._make_apply_icon(self, applied)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(bool(expanded))

    def is_pinned(self) -> bool:
        return self._pin_button.isChecked()

    def set_pinned(self, pinned: bool) -> None:
        self._pin_button.setChecked(bool(pinned))

    def has_apply_toggle(self) -> bool:
        return self._apply_button is not None

    def is_applied(self) -> bool:
        return True if self._apply_button is None else self._apply_button.isChecked()

    def set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setChecked(bool(applied))

    def set_apply_enabled(self, enabled: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setEnabled(bool(enabled))


class PanelContainer(QWidget):
    visibilityChanged = pyqtSignal(bool)

    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._toggle_action = QAction(title, self)
        self._toggle_action.setCheckable(True)
        self._toggle_action.setChecked(True)
        self._toggle_action.toggled.connect(self._set_visible_from_action)
        self._content = content
        self._content.setParent(self)

        self.setObjectName(f"{title.replace(' ', '')}Panel")
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("panelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(4)
        title_label = QLabel(title, header)
        title_label.setObjectName("toolbarMiniLabel")
        title_label.setStyleSheet("font-weight: 600;")
        header_layout.addWidget(title_label)
        header_layout.addStretch(1)

        outer_layout.addWidget(header)
        outer_layout.addWidget(self._content, 1)
        self.setStyleSheet(
            f"""
            QFrame#panelHeader {{
                background: {get_active_theme().window_bg};
                border-bottom: 1px solid {get_active_theme().toolbar_border};
            }}
            """
        )

    def toggleViewAction(self) -> QAction:
        return self._toggle_action

    def _set_visible_from_action(self, checked: bool) -> None:
        QWidget.setVisible(self, bool(checked))

    def setVisible(self, visible: bool) -> None:  # type: ignore[override]
        visible = bool(visible)
        changed = visible != QWidget.isVisible(self)
        QWidget.setVisible(self, visible)
        previous = self._toggle_action.blockSignals(True)
        try:
            self._toggle_action.setChecked(visible)
        finally:
            self._toggle_action.blockSignals(previous)
        if changed:
            self.visibilityChanged.emit(visible)

    def raise_(self) -> None:  # type: ignore[override]
        QWidget.raise_(self)

    def setFloating(self, _floating: bool) -> None:
        return

    def isFloating(self) -> bool:
        return False

    def setAllowedAreas(self, _areas) -> None:
        return

    def setFeatures(self, _features) -> None:
        return

    def _set_expanded(self, expanded: bool) -> None:
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        self._content.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)

    def is_pinned(self) -> bool:
        return self._pin_button.isChecked()

    def set_pinned(self, pinned: bool) -> None:
        self._pin_button.setChecked(pinned)

    def has_apply_toggle(self) -> bool:
        return self._apply_button is not None

    def is_applied(self) -> bool:
        return True if self._apply_button is None else self._apply_button.isChecked()

    def set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setChecked(bool(applied))

    def set_apply_enabled(self, enabled: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setEnabled(bool(enabled))

    def _update_pin_icon(self, pinned: bool) -> None:
        self._pin_button.setIcon(PanelContainer._make_pin_icon(self, pinned))

    def _set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setIcon(PanelContainer._make_apply_icon(self, applied))
        self.apply_changed.emit(applied)

    def _make_chevron_icon(self, expanded: bool) -> QIcon:
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cbd5e1"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        if expanded:
            path.moveTo(3.5, 5.0)
            path.lineTo(7.0, 8.5)
            path.lineTo(10.5, 5.0)
        else:
            path.moveTo(5.0, 3.5)
            path.lineTo(8.5, 7.0)
            path.lineTo(5.0, 10.5)
        painter.drawPath(path)
        painter.end()
        return QIcon(pixmap)

    def _make_pin_icon(self, pinned: bool) -> QIcon:
        color = QColor("#22c55e" if pinned else "#f8fafc")
        themed_icon = PanelContainer._tinted_icon_from_candidates(
            color,
            16,
            r"C:\Program Files\Inkscape\share\icons\Adwaita\symbolic\actions\view-pin-symbolic.svg",
            r"C:\Program Files\Inkscape\share\inkscape\icons\hicolor\symbolic\actions\object-tweak-push-symbolic.svg",
            r"C:\Program Files\Inkscape\share\inkscape\icons\Dash\symbolic\actions\object-tweak-push-symbolic.svg",
            r"C:\Program Files\Inkscape\share\inkscape\icons\hicolor\symbolic\actions\markers-symbolic.svg",
            r"C:\Program Files\Inkscape\share\inkscape\icons\Dash\symbolic\actions\markers-symbolic.svg",
            r"C:\Program Files\Inkscape\share\inkscape\icons\multicolor\symbolic\actions\markers-symbolic.svg",
        )
        if not themed_icon.isNull():
            return themed_icon

        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(color if pinned else QColor(0, 0, 0, 0))
        painter.drawRoundedRect(QRectF(4.2, 1.8, 7.0, 3.4), 1.7, 1.7)
        painter.drawLine(QPointF(7.7, 5.2), QPointF(7.7, 10.2))
        painter.drawLine(QPointF(5.0, 6.0), QPointF(10.4, 6.0))
        painter.drawLine(QPointF(7.7, 10.2), QPointF(6.0, 13.2))
        painter.end()
        return QIcon(pixmap)

    def _make_apply_icon(self, applied: bool) -> QIcon:
        icon_name = "link" if applied else "link-off"
        stroke = "#22c55e" if applied else "#ef4444"
        if tabler_icons is not None:
            try:
                svg = str(
                    tabler_icons.get_icon(
                        icon_name,
                        size=24,
                        stroke=stroke,
                        fill="none",
                        stroke_width=2.2,
                        stroke_linecap="round",
                        stroke_linejoin="round",
                    )
                )
            except Exception:
                svg = ""
            if svg:
                renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
                if renderer.isValid():
                    pixmap = QPixmap(20, 20)
                    pixmap.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pixmap)
                    renderer.render(painter, QRectF(0.0, 1.0, 20.0, 18.0))
                    painter.end()
                    return QIcon(pixmap)

        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(stroke), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(QPainterPath())
        painter.drawEllipse(QRectF(3.0, 3.0, 7.5, 7.5))
        painter.drawEllipse(QRectF(9.5, 9.5, 7.5, 7.5))
        painter.drawLine(QPointF(8.2, 8.2), QPointF(11.8, 11.8))
        if not applied:
            painter.drawLine(QPointF(4.0, 15.0), QPointF(16.0, 5.0))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _tinted_icon_from_candidates(color: QColor, size: int, *candidates: str) -> QIcon:
        for candidate in candidates:
            path = Path(candidate)
            if not path.exists():
                continue
            base_icon = QIcon(str(path))
            base_pixmap = base_icon.pixmap(size, size)
            if base_pixmap.isNull():
                continue
            tinted = QPixmap(base_pixmap.size())
            tinted.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tinted)
            painter.drawPixmap(0, 0, base_pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), color)
            painter.end()
            return QIcon(tinted)
        return QIcon()


class CompactWedgeSlider(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self.setMinimumSize(24, 12)
        self.setMaximumHeight(12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Opacity")

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), self._minimum + 1)
        self.setValue(self._value)

    def setValue(self, value: int) -> None:
        clamped = int(np.clip(int(value), self._minimum, self._maximum))
        if clamped == self._value:
            self.update()
            return
        self._value = clamped
        self.valueChanged.emit(self._value)
        self.update()

    def value(self) -> int:
        return int(self._value)

    def sizeHint(self) -> QSize:
        return QSize(24, 12)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        base_path = QPainterPath()
        base_path.moveTo(rect.left(), rect.bottom())
        base_path.lineTo(rect.right(), rect.top())
        base_path.lineTo(rect.right(), rect.bottom())
        base_path.closeSubpath()
        painter.setPen(QPen(QColor("#475569"), 1.0))
        painter.setBrush(QColor("#0f172a"))
        painter.drawPath(base_path)

        fraction = 0.0 if self._maximum <= self._minimum else (self._value - self._minimum) / float(self._maximum - self._minimum)
        fraction = float(np.clip(fraction, 0.0, 1.0))
        fill_right = rect.left() + rect.width() * fraction
        if fill_right > rect.left() + 0.5:
            fill_path = QPainterPath()
            fill_path.moveTo(rect.left(), rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom() - rect.height() * fraction)
            fill_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#38bdf8"))
            painter.drawPath(fill_path)

        handle_x = rect.left() + rect.width() * fraction
        painter.setPen(QPen(QColor("#f8fafc"), 1.4))
        painter.drawLine(QPointF(handle_x, rect.bottom()), QPointF(handle_x, rect.bottom() - rect.height() * max(fraction, 0.18)))
        painter.end()

    def _set_value_from_pos(self, position) -> None:
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if rect.width() <= 1.0:
            return
        fraction = (float(position.x()) - rect.left()) / rect.width()
        fraction = float(np.clip(fraction, 0.0, 1.0))
        value = int(round(self._minimum + fraction * (self._maximum - self._minimum)))
        self.setValue(value)


class BusySpinner(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._text_visible = False
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(22, 22)
        self.hide()

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        if self._maximum <= self._minimum:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        self._value = int(value)
        self.update()

    def setTextVisible(self, visible: bool) -> None:
        self._text_visible = bool(visible)
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % 12
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        cx = rect.center().x()
        cy = rect.center().y()
        radius = min(rect.width(), rect.height()) * 0.32
        segment_count = 12
        if self._maximum <= self._minimum:
            base_color = QColor("#38bdf8")
            for index in range(segment_count):
                progress = (index - self._phase) % segment_count
                alpha = int(40 + (215 * (segment_count - progress - 1) / max(segment_count - 1, 1)))
                color = QColor(base_color)
                color.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                angle = (360.0 / segment_count) * index
                radians = np.deg2rad(angle)
                x = cx + np.cos(radians) * radius
                y = cy + np.sin(radians) * radius
                painter.drawEllipse(QRectF(x - 1.8, y - 1.8, 3.6, 3.6))
        else:
            span = max(self._maximum - self._minimum, 1)
            fraction = float(np.clip((self._value - self._minimum) / span, 0.0, 1.0))
            painter.setPen(QPen(QColor("#334155"), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("#38bdf8"), 2.4))
            start_angle = 90 * 16
            span_angle = int(-360.0 * fraction * 16)
            painter.drawArc(rect, start_angle, span_angle)
        painter.end()


class ClickableIconLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
        self.setProperty("checked", self._checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class WorkflowLogBridge(QObject):
    record_received = pyqtSignal(int, str)
    records_flushed = pyqtSignal(int, list)


class WorkflowLogHandler(logging.Handler):
    def __init__(self, bridge: WorkflowLogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._bridge.record_received.emit(int(record.levelno), message)


@dataclass(slots=True)
class SpotOverlayBundle:
    curve: pg.PlotCurveItem
    ring_fill: QGraphicsPathItem | None = None
    inner_curve: pg.PlotCurveItem | None = None
    outer_curve: pg.PlotCurveItem | None = None
    label: pg.TextItem | None = None


@dataclass(slots=True)
class GuideOverlayBundle:
    vertical: pg.PlotCurveItem
    horizontal: pg.PlotCurveItem
    marker: pg.TargetItem


@dataclass(slots=True)
class MeasurementOverlayBundle:
    connector: pg.PlotCurveItem
    marker_a: pg.TargetItem
    marker_b: pg.TargetItem
    label: pg.TextItem


@dataclass(slots=True)
class ScaleBarOverlayBundle:
    outline_line: pg.PlotCurveItem
    line: pg.PlotCurveItem
    outline_left_tick: pg.PlotCurveItem
    left_tick: pg.PlotCurveItem
    outline_right_tick: pg.PlotCurveItem
    right_tick: pg.PlotCurveItem
    outline_label: pg.TextItem
    label: pg.TextItem


@dataclass(slots=True)
class LandmarkOverlayBundle:
    curve: pg.PlotCurveItem
    label: pg.TextItem


@dataclass(slots=True)
class ChromaticLandmarkAllOverlayBundle:
    points: pg.ScatterPlotItem
    active_cross: pg.PlotCurveItem | None
    label: pg.TextItem


@dataclass(slots=True)
class UndoSnapshot:
    label: str
    state: AnalysisState
    folder_text: str
    frame_slider_value: int
    wavelength_slider_value: int
    selected_spot_ids: set[int]
    spot_visual_color: str
    ring_visual_color: str
    mask_visual_color: str
    histogram_mask_visual_color: str
    figure_mask_visual_color: str
    highlight_visual_color: str
    spot_alpha: float
    ring_alpha: float
    mask_alpha: float
    histogram_mask_alpha: float
    figure_mask_alpha: float
    highlight_alpha: float
    spots_visible: bool
    rings_visible: bool
    mask_visible: bool
    reference_points_visible: bool
    histogram_mask_visible: bool
    figure_mask_visible: bool
    highlight_visible: bool
    file_mask: np.ndarray | None
    file_mask_path: str | None
    file_mask_revision: int


@dataclass(slots=True)
class SensorgramPointResult:
    frame_index: int
    metric_value: float | None
    metric_signal: float | None


@dataclass(slots=True)
class SensorgramComputationResult:
    frame_indices: np.ndarray
    metric_values: np.ndarray
    metric_signal: np.ndarray
    completed_count: int
    total_count: int
    prep_seconds: float = 0.0
    fit_seconds: float = 0.0
    total_seconds: float = 0.0
    cancelled: bool = False


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    partial = pyqtSignal(object)


class FunctionWorker(QRunnable):
    def __init__(self, fn, *args, supports_progress: bool = False, supports_partial: bool = False, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._supports_progress = supports_progress
        self._supports_partial = supports_partial
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if self._supports_progress:
                kwargs["progress_callback"] = self.signals.progress.emit
            if self._supports_partial:
                kwargs["partial_callback"] = self.signals.partial.emit
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # pragma: no cover - worker thread error path
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)


def _process_image_task(path_str: str, preprocessing, spots, external_mask: np.ndarray | None, mask_state) -> np.ndarray:
    raw_image = load_image_array(path_str)
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    return apply_preprocessing(
        raw_image,
        preprocessing_settings,
        spots=spots,
        mask_settings=mask_settings,
        external_mask=external_mask,
        external_mask_processed=external_mask_processed,
        mask_state=mask_state,
    )


def _refresh_spot_metrics_task(
    image: np.ndarray,
    settings,
    spots,
    external_mask: np.ndarray | None,
) -> list[DetectedSpot]:
    return refresh_spot_metrics(image, settings, spots, external_mask=external_mask)


def _detect_spots_task(
    image: np.ndarray,
    settings,
    external_mask: np.ndarray | None,
    progress_callback=None,
) -> list[DetectedSpot]:
    return detect_spots(image, settings, external_mask=external_mask, progress_callback=progress_callback)


def _background_profile_task(
    path_str: str,
    preprocessing,
    sigma_px: float,
    spots,
    external_mask: np.ndarray | None,
    progress_callback=None,
) -> np.ndarray:
    if progress_callback is not None:
        progress_callback(5, "Background profile: loading image...")
    raw_image = load_image_array(path_str)
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    if progress_callback is not None:
        progress_callback(25, "Background profile: applying spatial transforms...")
    spatial = apply_spatial_preprocessing(raw_image, preprocessing_settings)
    if external_mask is None:
        processed_external_mask = None
    elif external_mask_processed:
        processed_external_mask = external_mask.astype(bool, copy=False)
    else:
        processed_external_mask = apply_spatial_mask(external_mask, preprocessing_settings)
    if progress_callback is not None:
        progress_callback(55, "Background profile: estimating smooth surface...")
    return estimate_background_profile(
        spatial,
        sigma_px=sigma_px,
        binning=max(int(getattr(preprocessing_settings, "flatten_background_binning", 2)), 1),
        spots=spots,
        mask_settings=mask_settings,
        external_mask=processed_external_mask,
    )


def _ome_zarr_export_task(
    dataset,
    destination: Path,
    chunk_size_px: int,
    compression_enabled: bool,
    *,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> Path:
    return export_ome_zarr_dataset(
        dataset,
        destination,
        chunk_size_px=chunk_size_px,
        compression_enabled=compression_enabled,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


def _selected_roi_masks_for_spectrum(
    image_shape: tuple[int, int],
    source_spots: list[DetectedSpot],
    selected_spot_ids: tuple[int, ...],
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
    affine_matrix: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    image_height, image_width = image_shape[:2]
    spot_mask = np.zeros((image_height, image_width), dtype=bool)
    ring_mask = np.zeros((image_height, image_width), dtype=bool)
    if not source_spots:
        return spot_mask, ring_mask

    selected_ids = set(int(spot_id) for spot_id in selected_spot_ids) if selected_spot_ids else None
    effective_spots = [spot for spot in source_spots if selected_ids is None or spot.spot_id in selected_ids]
    if not effective_spots:
        return spot_mask, ring_mask

    ring_inner_radius = float(max(ring_inner_radius_px, 0.0))
    ring_outer_radius = float(max(ring_outer_radius_px, ring_inner_radius))
    use_affine = affine_matrix is not None and not np.allclose(
        np.asarray(affine_matrix, dtype=np.float64),
        identity_affine_matrix(),
        atol=1e-9,
    )
    if not use_affine:
        yy, xx = np.indices((image_height, image_width), dtype=np.float32)
        for spot in effective_spots:
            distance_sq = (xx - float(spot.center_x)) ** 2 + (yy - float(spot.center_y)) ** 2
            spot_mask |= distance_sq <= float(spot.radius_px) ** 2
            if ring_outer_radius > 0.0:
                outer_mask = distance_sq <= ring_outer_radius**2
                inner_mask = distance_sq < ring_inner_radius**2 if ring_inner_radius > 0.0 else np.zeros_like(outer_mask)
                ring_mask |= outer_mask & ~inner_mask
        ring_mask &= ~spot_mask
        return spot_mask, ring_mask

    for spot in effective_spots:
        spot_mask |= transformed_disk_mask(
            (image_height, image_width),
            (float(spot.center_x), float(spot.center_y)),
            float(spot.radius_px),
            affine_matrix,
        )
        if ring_outer_radius > 0.0:
            ring_mask |= transformed_annulus_mask(
                (image_height, image_width),
                (float(spot.center_x), float(spot.center_y)),
                float(ring_inner_radius),
                float(ring_outer_radius),
                affine_matrix,
            )
    ring_mask &= ~spot_mask
    return spot_mask, ring_mask


def _spot_absorbance_signature(
    frame: int,
    wavelength_values: tuple[float, ...],
    spot: DetectedSpot,
    chromatic_signatures: tuple[object, ...],
) -> tuple[object, ...]:
    return (
        int(frame),
        tuple(round(float(value), 6) for value in wavelength_values),
        int(spot.spot_id),
        round(float(spot.center_x), 3),
        round(float(spot.center_y), 3),
        round(float(spot.radius_px), 3),
        round(float(spot.ring_inner_diameter_px or 0.0), 3),
        round(float(spot.ring_outer_diameter_px or 0.0), 3),
        chromatic_signatures,
    )


def _absorbance_roi_mask_cache_key(
    image_shape: tuple[int, int],
    selected_spots: list[DetectedSpot],
    selected_spot_ids: tuple[int, ...],
    affine_matrix: np.ndarray | None,
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
) -> tuple[object, ...]:
    affine_signature = None
    if affine_matrix is not None:
        affine_signature = tuple(round(float(value), 6) for value in np.asarray(affine_matrix, dtype=np.float64).ravel())
    return (
        tuple(int(value) for value in image_shape[:2]),
        tuple(int(spot_id) for spot_id in selected_spot_ids),
        tuple(
            (
                int(spot.spot_id),
                round(float(spot.center_x), 3),
                round(float(spot.center_y), 3),
                round(float(spot.radius_px), 3),
                round(float(spot.ring_inner_diameter_px or 0.0), 3),
                round(float(spot.ring_outer_diameter_px or 0.0), 3),
                spot.spot_color_hex or "",
                spot.ring_color_hex or "",
            )
            for spot in selected_spots
        ),
        affine_signature,
        round(float(ring_inner_radius_px), 3),
        round(float(ring_outer_radius_px), 3),
    )


def _absorbance_spectrum_task(
    measurement_payload: list[tuple[float, str, list[DetectedSpot], np.ndarray | None, bool, np.ndarray | None]],
    preprocessing,
    flatten_mask_settings,
    measurement_settings,
    roi_mask_cache,
    roi_mask_cache_lock,
    roi_mask_cache_max_size: int,
    source_spots: list[DetectedSpot],
    selected_spot_ids: tuple[int, ...],
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
    mask_state,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> AbsorbanceSpectrumResult:
    task_started = time.perf_counter()
    load_seconds = 0.0
    roi_seconds = 0.0
    cache_stats = {
        "image_hits": 0,
        "image_builds": 0,
        "roi_hits": 0,
        "roi_builds": 0,
    }
    selected_spot_id_set = set(selected_spot_ids)
    selected_spots = [spot for spot in source_spots if spot.spot_id in selected_spot_id_set]
    spot_accumulators: dict[int, dict[str, list[float] | list[int]]] = {
        int(spot.spot_id): {
            "wavelengths": [],
            "absorbance": [],
            "spot_mean": [],
            "ring_mean": [],
            "spot_pixel_count": [],
            "ring_pixel_count": [],
        }
        for spot in selected_spots
    }
    wavelengths: list[float] = []
    absorbance_values: list[float] = []
    spot_mean_values: list[float] = []
    ring_mean_values: list[float] = []
    spot_pixel_counts: list[int] = []
    ring_pixel_counts: list[int] = []
    total = max(len(measurement_payload), 1)

    def _build_roi_mask_cache(
        image_shape: tuple[int, int],
        selected_spots_local: list[DetectedSpot],
        selected_ids_local: tuple[int, ...],
        affine_matrix_local: np.ndarray | None,
    ) -> dict[str, object]:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        cache_key = _absorbance_roi_mask_cache_key(
            image_shape,
            selected_spots_local,
            selected_ids_local,
            affine_matrix_local,
            ring_inner_radius_px,
            ring_outer_radius_px,
        )
        with roi_mask_cache_lock:
            cached_value = roi_mask_cache.get(cache_key) if hasattr(roi_mask_cache, "get") else None
            if cached_value is not None:
                try:
                    roi_mask_cache.move_to_end(cache_key)
                except Exception:
                    pass
                cache_stats["roi_hits"] += 1
                logger.debug(
                    "ROI cache hit | shape=%sx%s spots=%s",
                    int(image_shape[0]),
                    int(image_shape[1]),
                    len(selected_spots_local),
                )
                return cached_value
        combined_spot_mask, combined_ring_mask = _selected_roi_masks_for_spectrum(
            image_shape,
            source_spots,
            selected_ids_local,
            ring_inner_radius_px,
            ring_outer_radius_px,
            affine_matrix_local,
        )
        per_spot_masks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for spot in selected_spots_local:
            per_spot_masks[int(spot.spot_id)] = _selected_roi_masks_for_spectrum(
                image_shape,
                [spot],
                (int(spot.spot_id),),
                ring_inner_radius_px,
                ring_outer_radius_px,
                affine_matrix_local,
            )
        cached_value = {
            "shape": tuple(int(value) for value in image_shape[:2]),
            "combined": (combined_spot_mask, combined_ring_mask),
            "per_spot": per_spot_masks,
        }
        with roi_mask_cache_lock:
            roi_mask_cache[cache_key] = cached_value
            try:
                roi_mask_cache.move_to_end(cache_key)
            except Exception:
                pass
            while len(roi_mask_cache) > max(int(roi_mask_cache_max_size), 1):
                roi_mask_cache.popitem(last=False)
        cache_stats["roi_builds"] += 1
        logger.debug(
            "ROI cache built | shape=%sx%s spots=%s",
            int(image_shape[0]),
            int(image_shape[1]),
            len(selected_spots_local),
        )
        return cached_value

    def _load_and_preprocess_measurement(
        item: tuple[int, tuple[float, str, list[DetectedSpot], np.ndarray | None, bool, np.ndarray | None]]
    ) -> tuple[int, float, np.ndarray, np.ndarray | None, np.ndarray | None, float]:
        index, (wavelength_nm, path_str, preprocessing_spots, affine_matrix, external_mask_processed, external_mask) = item
        load_started = time.perf_counter()
        cache_info_before = getattr(load_image_array, "cache_info", None)
        before_hits = cache_info_before().hits if callable(cache_info_before) else None
        before_misses = cache_info_before().misses if callable(cache_info_before) else None
        raw_image = load_image_array(path_str)
        if callable(cache_info_before):
            cache_info_after = cache_info_before()
            if before_hits is not None and cache_info_after.hits > before_hits:
                cache_stats["image_hits"] += int(cache_info_after.hits - before_hits)
            if before_misses is not None and cache_info_after.misses > before_misses:
                cache_stats["image_builds"] += int(cache_info_after.misses - before_misses)
        processed = apply_preprocessing(
            raw_image,
            preprocessing,
            spots=preprocessing_spots,
            mask_settings=flatten_mask_settings,
            external_mask=external_mask,
            external_mask_processed=external_mask_processed,
            mask_state=mask_state,
        ).astype(np.float32, copy=False)
        load_duration = time.perf_counter() - load_started
        return int(index), float(wavelength_nm), processed, affine_matrix, external_mask, load_duration

    worker_count = max(1, min(int(os.cpu_count() or 1), 4, len(measurement_payload)))
    prepared_measurements: list[tuple[int, float, np.ndarray, np.ndarray | None, np.ndarray | None, float]] = []
    if worker_count <= 1:
        for index, item in enumerate(measurement_payload, start=1):
            prepared_measurements.append(_load_and_preprocess_measurement((index, item)))
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_load_and_preprocess_measurement, (index, item)) for index, item in enumerate(measurement_payload, start=1)]
            for future in as_completed(futures):
                prepared_measurements.append(future.result())
        prepared_measurements.sort(key=lambda item: item[0])

    for index, wavelength_nm, processed, affine_matrix, external_mask, load_duration in prepared_measurements:
        if cancel_event is not None and cancel_event.is_set():
            return AbsorbanceSpectrumResult(
                wavelengths_nm=np.asarray([], dtype=np.float64),
                absorbance=np.asarray([], dtype=np.float64),
                spot_mean=np.asarray([], dtype=np.float64),
                ring_mean=np.asarray([], dtype=np.float64),
                spot_pixel_count=np.asarray([], dtype=np.int32),
                ring_pixel_count=np.asarray([], dtype=np.int32),
                load_seconds=load_seconds,
                roi_seconds=roi_seconds,
                total_seconds=time.perf_counter() - task_started,
            )
        load_seconds += float(load_duration)

        roi_started = time.perf_counter()
        current_shape = tuple(int(value) for value in processed.shape[:2])
        roi_mask_cache_entry = _build_roi_mask_cache(current_shape, selected_spots, selected_spot_ids, affine_matrix)
        ignored_mask = ignored_pixel_mask(processed, measurement_settings, external_mask=external_mask)
        combined_spot_mask, combined_ring_mask = roi_mask_cache_entry["combined"]  # type: ignore[index]
        spot_mask = np.array(combined_spot_mask, dtype=bool, copy=True)
        ring_mask = np.array(combined_ring_mask, dtype=bool, copy=True)
        spot_mask &= ~ignored_mask
        ring_mask &= ~ignored_mask
        ring_mask &= ~spot_mask

        spot_pixels = processed[spot_mask]
        ring_pixels = processed[ring_mask]
        if spot_pixels.size == 0 or ring_pixels.size == 0:
            spot_mean = float("nan")
            ring_mean = float("nan")
            absorbance = float("nan")
        else:
            spot_mean = float(np.mean(spot_pixels))
            ring_mean = float(np.mean(ring_pixels))
            absorbance = absorbance_from_means(spot_mean, ring_mean)

        wavelengths.append(float(wavelength_nm))
        absorbance_values.append(absorbance)
        spot_mean_values.append(spot_mean)
        ring_mean_values.append(ring_mean)
        spot_pixel_counts.append(int(spot_pixels.size))
        ring_pixel_counts.append(int(ring_pixels.size))

        for spot in selected_spots:
            per_spot_masks = roi_mask_cache_entry["per_spot"]  # type: ignore[index]
            spot_mask_template, ring_mask_template = per_spot_masks[int(spot.spot_id)]
            spot_mask_single = np.array(spot_mask_template, dtype=bool, copy=True)
            ring_mask_single = np.array(ring_mask_template, dtype=bool, copy=True)
            spot_mask_single &= ~ignored_mask
            ring_mask_single &= ~ignored_mask
            ring_mask_single &= ~spot_mask_single

            spot_pixels_single = processed[spot_mask_single]
            ring_pixels_single = processed[ring_mask_single]
            if spot_pixels_single.size == 0 or ring_pixels_single.size == 0:
                spot_mean_single = float("nan")
                ring_mean_single = float("nan")
                absorbance_single = float("nan")
            else:
                spot_mean_single = float(np.mean(spot_pixels_single))
                ring_mean_single = float(np.mean(ring_pixels_single))
                absorbance_single = absorbance_from_means(spot_mean_single, ring_mean_single)

            accumulator = spot_accumulators[int(spot.spot_id)]
            accumulator["wavelengths"].append(float(wavelength_nm))
            accumulator["absorbance"].append(absorbance_single)
            accumulator["spot_mean"].append(spot_mean_single)
            accumulator["ring_mean"].append(ring_mean_single)
            accumulator["spot_pixel_count"].append(int(spot_pixels_single.size))
            accumulator["ring_pixel_count"].append(int(ring_pixels_single.size))
        roi_seconds += time.perf_counter() - roi_started

        if progress_callback is not None:
            progress_callback(
                int(round(index / total * 100.0)),
                f"Spectral absorbance {index}/{total}: {float(wavelength_nm):g} nm",
            )

    spot_results: dict[int, AbsorbanceSpectrumResult] = {}
    for spot in selected_spots:
        data = spot_accumulators[int(spot.spot_id)]
        spot_results[int(spot.spot_id)] = AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(data["wavelengths"], dtype=np.float64),
            absorbance=np.asarray(data["absorbance"], dtype=np.float64),
            spot_mean=np.asarray(data["spot_mean"], dtype=np.float64),
            ring_mean=np.asarray(data["ring_mean"], dtype=np.float64),
            spot_pixel_count=np.asarray(data["spot_pixel_count"], dtype=np.int32),
            ring_pixel_count=np.asarray(data["ring_pixel_count"], dtype=np.int32),
        )

    result = AbsorbanceSpectrumResult(
        wavelengths_nm=np.asarray(wavelengths, dtype=np.float64),
        absorbance=np.asarray(absorbance_values, dtype=np.float64),
        spot_mean=np.asarray(spot_mean_values, dtype=np.float64),
        ring_mean=np.asarray(ring_mean_values, dtype=np.float64),
        spot_pixel_count=np.asarray(spot_pixel_counts, dtype=np.int32),
        ring_pixel_count=np.asarray(ring_pixel_counts, dtype=np.int32),
        load_seconds=load_seconds,
        roi_seconds=roi_seconds,
        total_seconds=time.perf_counter() - task_started,
        spot_results=spot_results,
    )
    logging.getLogger("lspr_imaging_app.workflow").debug(
        "Spec cache summary | img hit=%s build=%s | roi hit=%s build=%s",
        int(cache_stats["image_hits"]),
        int(cache_stats["image_builds"]),
        int(cache_stats["roi_hits"]),
        int(cache_stats["roi_builds"]),
    )
    return result


def _sensorgram_metric_task(
    frame_payloads_or_frames,
    poly_order: int,
    metric_key: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
    partial_callback=None,
    frame_payload_builder=None,
) -> SensorgramComputationResult:
    task_started = time.perf_counter()
    frame_payloads: list[tuple[int, tuple[object, ...]]] = []
    total_input_count = len(frame_payloads_or_frames) if hasattr(frame_payloads_or_frames, "__len__") else 0
    prep_seconds = 0.0
    fit_seconds = 0.0
    if frame_payload_builder is not None:
        frames = [int(frame) for frame in frame_payloads_or_frames]
        total_input_count = len(frames)
        if not frames:
            return SensorgramComputationResult(
                frame_indices=np.asarray([], dtype=np.int32),
                metric_values=np.asarray([], dtype=np.float64),
                metric_signal=np.asarray([], dtype=np.float64),
                completed_count=0,
                total_count=0,
                prep_seconds=0.0,
                fit_seconds=0.0,
                total_seconds=time.perf_counter() - task_started,
                cancelled=False,
            )
        prep_started = time.perf_counter()
        completed = 0
        built_payloads: list[tuple[int, tuple[object, ...]]] = []
        worker_count = max(2, min(4, os.cpu_count() or 2))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(frame_payload_builder, int(frame)): int(frame) for frame in frames}
            for future in as_completed(future_map):
                frame_index = int(future_map[future])
                if cancel_event is not None and cancel_event.is_set():
                    return SensorgramComputationResult(
                        frame_indices=np.asarray([], dtype=np.int32),
                        metric_values=np.asarray([], dtype=np.float64),
                        metric_signal=np.asarray([], dtype=np.float64),
                        completed_count=len(built_payloads),
                        total_count=total_input_count,
                        cancelled=True,
                    )
                payload = future.result()
                if payload is not None:
                    built_payloads.append((frame_index, payload))
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        int(round((completed / max(total_input_count, 1)) * 20.0)),
                        f"Preparing sensorgram {completed}/{total_input_count} frames",
                    )
        prep_seconds = time.perf_counter() - prep_started
        frame_payloads = sorted(built_payloads, key=lambda item: item[0])
    else:
        frame_payloads = list(frame_payloads_or_frames)
    frame_indices: list[int] = []
    metric_values: list[float] = []
    metric_signals: list[float] = []
    total = max(len(frame_payloads), 1)
    compute_base = 20.0 if frame_payload_builder is not None else 0.0
    compute_span = 80.0 if frame_payload_builder is not None else 100.0
    compute_started = time.perf_counter()

    for index, (frame_index, payload) in enumerate(frame_payloads, start=1):
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                frame_indices=np.asarray(frame_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(frame_indices),
                total_count=len(frame_payloads),
                prep_seconds=prep_seconds,
                fit_seconds=fit_seconds,
                total_seconds=time.perf_counter() - task_started,
                cancelled=True,
            )

        def frame_progress_callback(percent: int, text: str | None = None, *, frame_number: int = int(frame_index), position: int = index) -> None:
            if progress_callback is None:
                return
            inner_percent = float(np.clip(float(percent), 0.0, 100.0))
            overall = compute_base + (((position - 1) + (inner_percent / 100.0)) / total) * compute_span
            progress_callback(
                int(round(overall)),
                text or f"Sensorgram {position}/{total}: frame {frame_number}",
            )

        spectrum = _absorbance_spectrum_task(
            *payload,
            cancel_event=cancel_event,
            progress_callback=frame_progress_callback,
        )
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                frame_indices=np.asarray(frame_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(frame_indices),
                total_count=len(frame_payloads),
                cancelled=True,
            )

        fit = fit_absorbance_curve(
            spectrum.wavelengths_nm,
            spectrum.absorbance,
            poly_order=poly_order,
        )
        metric_value, metric_signal = metric_value_from_fit(fit, metric_key)
        metric_float = float(metric_value) if metric_value is not None and np.isfinite(metric_value) else float("nan")
        signal_float = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")

        frame_indices.append(int(frame_index))
        metric_values.append(metric_float)
        metric_signals.append(signal_float)

        if partial_callback is not None:
            partial_callback(
                SensorgramPointResult(
                    frame_index=int(frame_index),
                    metric_value=None if not np.isfinite(metric_float) else metric_float,
                    metric_signal=None if not np.isfinite(signal_float) else signal_float,
                )
            )
        if progress_callback is not None:
            progress_callback(
                int(round(compute_base + (index / total) * compute_span)),
                f"Sensorgram {index}/{total}: frame {int(frame_index)}",
            )
    fit_seconds = time.perf_counter() - compute_started

    return SensorgramComputationResult(
        frame_indices=np.asarray(frame_indices, dtype=np.int32),
        metric_values=np.asarray(metric_values, dtype=np.float64),
        metric_signal=np.asarray(metric_signals, dtype=np.float64),
        completed_count=len(frame_indices),
        total_count=len(frame_payloads),
        prep_seconds=prep_seconds,
        fit_seconds=fit_seconds,
        total_seconds=time.perf_counter() - task_started,
        cancelled=False,
    )


def _auto_chromatic_landmarks_task(
    sample_payload: list[tuple[int, float, str]],
    preprocessing,
    feature_count: int,
    subpixel_precision: int,
    progress_callback=None,
) -> list[tuple[int, int, float, float, float]]:
    if not sample_payload:
        return []
    preprocessing_settings = deepcopy(preprocessing)
    preprocessing_settings.flatten_background_enabled = False
    preprocessing_settings.chromatic_correction_enabled = False
    processed_images: list[tuple[int, float, np.ndarray]] = []
    total = max(len(sample_payload), 1)
    for index, (frame, wavelength, path_str) in enumerate(sample_payload, start=1):
        raw_image = load_image_array(path_str)
        processed = apply_spatial_preprocessing(raw_image, preprocessing_settings)
        processed_images.append((int(frame), float(wavelength), processed))
        if progress_callback is not None:
            progress_callback(
                int(round((index / total) * 40)),
                f"Loading sampled chromatic image {index}/{total}...",
            )
    first_frame, first_wavelength, first_image = processed_images[0]
    current_landmarks = detect_regional_landmarks(
        first_image,
        int(feature_count),
        subpixel_precision=int(subpixel_precision),
    )
    observations: list[tuple[int, int, float, float, float]] = [
        (int(feature_id), int(first_frame), float(first_wavelength), float(point[0]), float(point[1]))
        for feature_id, point in sorted(current_landmarks.items())
    ]
    if progress_callback is not None:
        progress_callback(50, f"Detected reference points on sampled image 1/{total}.")
    previous_image = first_image
    for index, (frame, wavelength, image) in enumerate(processed_images[1:], start=2):
        current_landmarks = track_landmarks(
            previous_image,
            image,
            current_landmarks,
            subpixel_precision=int(subpixel_precision),
        )
        for feature_id, point in sorted(current_landmarks.items()):
            observations.append((int(feature_id), int(frame), float(wavelength), float(point[0]), float(point[1])))
        previous_image = image
        if progress_callback is not None:
            progress_callback(
                int(round(50 + ((index - 1) / max(total - 1, 1)) * 50)),
                f"Tracked reference points on sampled image {index}/{total}...",
            )
    return observations


def _normalized_odd_count(value: int, minimum: int, maximum: int) -> int:
    normalized = max(int(value), int(minimum))
    if normalized % 2 == 0:
        normalized += 1
    if normalized > int(maximum):
        normalized = int(maximum)
        if normalized % 2 == 0:
            normalized = max(int(minimum), normalized - 1)
    return max(normalized, int(minimum))


def _sampled_wavelengths(wavelengths_nm: list[float], sample_count: int) -> list[float]:
    if not wavelengths_nm:
        return []
    maximum = len(wavelengths_nm)
    minimum = 1 if maximum == 1 else min(3, maximum)
    count = min(_normalized_odd_count(sample_count, minimum, maximum), maximum)
    if count % 2 == 0:
        count = max(1, count - 1)
    if count == 1:
        return [float(wavelengths_nm[len(wavelengths_nm) // 2])]
    indices = [int(round(index * (maximum - 1) / (count - 1))) for index in range(count)]
    indices = sorted(dict.fromkeys(indices))
    return [float(wavelengths_nm[index]) for index in indices]


def _estimate_chromatic_models_task(
    record_specs: list[tuple[int, float, str]],
    preprocessing,
    reference_key: tuple[int, float],
    landmarks_payload: list[tuple[int, int, float, float, float]] | None = None,
    progress_callback=None,
) -> list[ChromaticTransformModel]:
    mode = str(getattr(preprocessing, "chromatic_registration_mode", "landmark_radial") or "landmark_radial")
    models: list[ChromaticTransformModel] = []
    if mode == "landmark_radial":
        if not landmarks_payload:
            raise ValueError("No chromatic reference points are available. Start the radial workflow and mark reference points first.")
        reference_frame, reference_wavelength = int(reference_key[0]), float(reference_key[1])
        all_wavelengths = sorted({float(wavelength) for _frame, wavelength, _path in record_specs})
        sampled_wavelengths = _sampled_wavelengths(
            all_wavelengths,
            int(getattr(preprocessing, "chromatic_sample_image_count", 5)),
        )
        feature_count = max(int(getattr(preprocessing, "chromatic_feature_count", 5)), 1)
        expected_feature_ids = list(range(1, feature_count + 1))

        landmarks_by_wavelength: dict[float, dict[int, tuple[float, float]]] = {}
        for landmark_id, frame, wavelength, x_px, y_px in landmarks_payload:
            if int(frame) != reference_frame:
                continue
            marks = landmarks_by_wavelength.setdefault(float(wavelength), {})
            marks[int(landmark_id)] = (float(x_px), float(y_px))

        reference_landmarks = landmarks_by_wavelength.get(reference_wavelength, {})
        missing_reference = [feature_id for feature_id in expected_feature_ids if feature_id not in reference_landmarks]
        if missing_reference:
            raise ValueError(
                f"Reference wavelength {reference_wavelength:g} nm is missing reference point(s): "
                + ", ".join(str(feature_id) for feature_id in missing_reference)
            )

        sample_matrices: dict[float, np.ndarray] = {}
        sample_rmse: dict[float, float] = {}
        direct_feature_counts: dict[float, int] = {}
        total = max(len(sampled_wavelengths), 1)
        reference_points = np.asarray(
            [reference_landmarks[feature_id] for feature_id in expected_feature_ids],
            dtype=np.float64,
        )
        for index, wavelength in enumerate(sampled_wavelengths, start=1):
            marks = landmarks_by_wavelength.get(float(wavelength), {})
            missing = [feature_id for feature_id in expected_feature_ids if feature_id not in marks]
            if missing:
                raise ValueError(
                    f"Sample wavelength {wavelength:g} nm is missing reference point(s): "
                    + ", ".join(str(feature_id) for feature_id in missing)
                )
            if abs(float(wavelength) - reference_wavelength) < 1e-6:
                matrix = identity_affine_matrix()
                rmse = 0.0
            else:
                target_points = np.asarray([marks[feature_id] for feature_id in expected_feature_ids], dtype=np.float64)
                matrix = fit_affine_matrix(reference_points, target_points)
                residuals = np.sqrt(np.sum((apply_affine_to_points(reference_points, matrix) - target_points) ** 2, axis=1))
                rmse = float(np.sqrt(np.mean(residuals**2))) if residuals.size else 0.0
            sample_matrices[float(wavelength)] = matrix
            sample_rmse[float(wavelength)] = rmse
            direct_feature_counts[float(wavelength)] = len(expected_feature_ids)
            if progress_callback is not None:
                progress_callback(
                    int(round(index / total * 100.0)),
                    f"Chromatic correction {index}/{total}: {wavelength:g} nm",
                )

        sorted_sample_wavelengths = sorted(sample_matrices)
        matrix_values = []
        rmse_values = []
        for wavelength in sorted_sample_wavelengths:
            matrix_values.append(np.asarray(sample_matrices[wavelength], dtype=np.float64))
            rmse_values.append(sample_rmse[wavelength])
        sample_axis = np.asarray(sorted_sample_wavelengths, dtype=np.float64)
        matrix_values_array = np.asarray(matrix_values, dtype=np.float64)

        matrices_by_wavelength: dict[float, np.ndarray] = {}
        rmse_by_wavelength: dict[float, float] = {}
        feature_counts_by_wavelength: dict[float, int] = {}
        for wavelength in all_wavelengths:
            wavelength_f64 = float(wavelength)
            if wavelength_f64 in sample_matrices:
                matrices_by_wavelength[wavelength_f64] = sample_matrices[wavelength_f64]
                rmse_by_wavelength[wavelength_f64] = sample_rmse[wavelength_f64]
                feature_counts_by_wavelength[wavelength_f64] = direct_feature_counts[wavelength_f64]
                continue
            interpolated_matrix = np.empty((2, 3), dtype=np.float64)
            for row in range(2):
                for col in range(3):
                    interpolated_matrix[row, col] = float(
                        np.interp(
                            wavelength_f64,
                            sample_axis,
                            matrix_values_array[:, row, col],
                        )
                    )
            matrices_by_wavelength[wavelength_f64] = interpolated_matrix
            rmse_by_wavelength[wavelength_f64] = float(np.interp(wavelength_f64, sample_axis, np.asarray(rmse_values, dtype=np.float64)))
            feature_counts_by_wavelength[wavelength_f64] = len(expected_feature_ids)

        for frame, wavelength, _path_str in record_specs:
            matrix = matrices_by_wavelength[float(wavelength)]
            models.append(
                ChromaticTransformModel(
                    frame_index=int(frame),
                    wavelength_nm=float(wavelength),
                    model_kind="landmark_affine",
                    affine_matrix=[[float(value) for value in row] for row in matrix.tolist()],
                    global_shift_x_px=float(matrix[0, 2]),
                    global_shift_y_px=float(matrix[1, 2]),
                    rmse_px=float(rmse_by_wavelength[float(wavelength)]),
                    mean_score=1.0,
                    min_score=1.0,
                    tile_count=int(feature_counts_by_wavelength[float(wavelength)]),
                    inlier_count=int(feature_counts_by_wavelength[float(wavelength)]),
                )
            )
        return models

    reference_path = next((path_str for frame, wavelength, path_str in record_specs if (frame, wavelength) == reference_key), None)
    if reference_path is None:
        raise ValueError("Reference image is missing from the dataset.")
    reference_raw = load_image_array(reference_path)
    reference_processed = apply_spatial_preprocessing(reference_raw, preprocessing)
    tile_size = int(max(preprocessing.chromatic_tile_size_px, 24))
    search_radius = int(max(preprocessing.chromatic_search_radius_px, 6))
    for index, (frame, wavelength, path_str) in enumerate(record_specs, start=1):
        if (frame, wavelength) == reference_key:
            result = ChromaticRegistrationResult(
                affine_matrix=identity_affine_matrix(),
                global_shift_x_px=0.0,
                global_shift_y_px=0.0,
                rmse_px=0.0,
                mean_score=0.0,
                min_score=0.0,
                tile_count=0,
                inlier_count=0,
            )
        else:
            target_raw = load_image_array(path_str)
            target_processed = apply_spatial_preprocessing(target_raw, preprocessing)
            result = estimate_affine_chromatic_transform(
                reference_processed,
                target_processed,
                mode=mode,
                tile_size_px=tile_size,
                search_radius_px=search_radius,
                subpixel_precision=int(getattr(preprocessing, "chromatic_subpixel_precision", 4)),
            )
        models.append(
            ChromaticTransformModel(
                frame_index=int(frame),
                wavelength_nm=float(wavelength),
                model_kind="image_affine",
                affine_matrix=[[float(value) for value in row] for row in result.affine_matrix.tolist()],
                global_shift_x_px=float(result.global_shift_x_px),
                global_shift_y_px=float(result.global_shift_y_px),
                rmse_px=float(result.rmse_px),
                mean_score=float(result.mean_score),
                min_score=float(result.min_score),
                tile_count=int(result.tile_count),
                inlier_count=int(result.inlier_count),
            )
        )
        if progress_callback is not None:
            progress_callback(
                int(round(index / total * 100.0)),
                f"Chromatic correction {index}/{total}: {wavelength:g} nm frame {frame}",
            )
    return models


class MainWindow(QMainWindow):
    SETTINGS_ORG = "LSPR"
    SETTINGS_APP = "LSPRImaging"
    HISTOGRAM_MIN_INTENSITY = 0.0
    HISTOGRAM_MAX_INTENSITY = 65535.0
    HISTOGRAM_LOG_Y_FLOOR = 0.1
    PROCESSED_IMAGE_CACHE_SIZE = 6
    ABSORBANCE_SPECTRUM_CACHE_SIZE = 48
    ABSORBANCE_FRAME_CACHE_SIZE = 48
    ABSORBANCE_ROI_MASK_CACHE_SIZE = 48
    SPOT_ABSORBANCE_CACHE_SIZE = 512
    SENSORGRAM_CACHE_SIZE = 48
    SENSORGRAM_FRAME_PAYLOAD_CACHE_SIZE = 96
    UNDO_STACK_LIMIT = 5
    # Quick navigation:
    # - layout and signal wiring: _build_layout, _create_toolbar, _connect_signals
    # - spot list table: _on_spot_list_toggled, _update_spot_list_table, CSV helpers
    # - image refresh pipeline: _refresh_image, _apply_loaded_image, _update_spot_overlays
    # - persistence/session: _restore_layout_preferences, _save_layout_preferences, session save/load
    # - analysis: _update_sensorgram_plot, analysis batch helpers

    def __init__(self, default_folder: Path, *, fast_startup: bool = False) -> None:
        super().__init__()
        self._fast_startup = bool(fast_startup)
        self._state = AnalysisState()
        self._record_map: dict[tuple[int, float], object] = {}
        self._record_key_by_path: dict[Path, tuple[int, float]] = {}
        self._settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._dataset_controller = DatasetController(self)
        self._image_controller = ImageController(self)
        self._spot_table_controller = SpotTableController(self)
        self._mask_controller = MaskController(self)
        self._chromatic_controller = ChromaticController(self)
        self._analysis_controller = AnalysisController(self)
        self._plot_manager = PlotManager(self)
        self._ui_state_manager = UIStateManager(self)
        self._session_state_manager = SessionStateManager(self)
        self._shortcut_manager = ShortcutManager(self)
        self._analysis_enabled = self._settings_bool("analysis_section_applied", True)
        self._window_geometry_restored = False
        self._layout_preferences_ready = False
        self._startup_restore_window_maximized = False
        self._startup_restore_window_fullscreen = False
        self._suspend_layout_save = False
        self._panel_layout_visibility_backup: dict[str, QByteArray] | None = None
        self._current_image_key: tuple[int, float] | None = None
        self._previous_image_key: tuple[int, float] | None = None
        self._frame_values: list[int] = []
        self._wavelength_values: list[float] = []
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(max(4, min(6, os.cpu_count() or 4)))
        self._current_record_path: Path | None = None
        self._crop_roi: pg.RectROI | None = None
        self._crop_overlay_item: QGraphicsPathItem | None = None
        self._active_tool: str | None = None
        self._suspend_crop_sync = False
        self._dragging_crop = False
        self._crop_drag_anchor: tuple[float, float] | None = None
        self._crop_drag_origin: tuple[float, float] | None = None
        self._panning_image = False
        self._pan_anchor_view: tuple[float, float] | None = None
        self._pan_anchor_ranges: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._current_processed_image: np.ndarray | None = None
        self._current_file_mask: np.ndarray | None = None
        self._current_file_mask_path: Path | None = None
        self._current_file_mask_session_source_path: Path | None = None
        self._mask_histogram_preview: np.ndarray | None = None
        self._mask_figure_preview: np.ndarray | None = None
        self._mask_morphology_operation: str | None = None
        self._external_mask_revision = 0
        self._mask_state_revision = 0
        self._mask_drawing = False
        self._processed_external_mask_cache_signature: tuple[object, ...] | None = None
        self._processed_external_mask_cache_value: np.ndarray | None = None
        self._processed_to_raw_map_signature: tuple[object, ...] | None = None
        self._processed_to_raw_x_map: np.ndarray | None = None
        self._processed_to_raw_y_map: np.ndarray | None = None
        self._spot_overlay_items: dict[int, SpotOverlayBundle] = {}
        self._guide_overlay_items: dict[int, GuideOverlayBundle] = {}
        self._ome_zarr_chunk_overlay_items: list[pg.InfiniteLine] = []
        self._landmark_overlay_items: dict[int, LandmarkOverlayBundle] = {}
        self._chromatic_all_landmark_overlay_items: dict[int, ChromaticLandmarkAllOverlayBundle] = {}
        self._measurement_overlay: MeasurementOverlayBundle | None = None
        self._scale_bar_overlay: ScaleBarOverlayBundle | None = None
        self._spot_list_selection_syncing = False
        self._spot_list_range_anchor_row: int | None = None
        self._spot_list_table_updating = False
        self._spot_clipboard: dict[str, object] | None = None
        self._selected_spot_ids: set[int] = set()
        self._selection_plot_highlight_signature: tuple[int, ...] | None = None
        self._sensorgram_selection_highlight_signature: tuple[int, ...] | None = None
        self._live_preview_prompt_selection_signature: tuple[int, ...] | None = None
        self._chromatic_landmark_marker_id = 1
        self._selected_landmark_id: int | None = None
        self._activate_chromatic_tool_after_refresh = False
        self._chromatic_setup_active = False
        self._chromatic_setup_saved_visibility: tuple[bool, bool, bool, bool, bool] | None = None
        self._chromatic_pending_view_ranges: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._pending_image_view_ranges: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._pending_image_view_crop_offset: tuple[float, float] | None = None
        self._pending_image_view_preserve = False
        self._force_image_autorange_after_load = False
        self._image_view_save_timer = QTimer(self)
        self._image_view_save_timer.setSingleShot(True)
        self._image_view_save_timer.setInterval(250)
        self._image_view_save_timer.timeout.connect(self._save_visual_preferences)
        self._chromatic_auto_request_id = 0
        self._chromatic_auto_running = False
        self._chromatic_auto_restore_state: tuple[list[ChromaticLandmarkObservation], list[ChromaticTransformModel], bool] | None = None
        self._suspend_collapsible_accordion = False
        self._dragging_landmark = False
        self._dragging_landmark_started = False
        self._dragging_spots = False
        self._drag_anchor: tuple[float, float] | None = None
        self._drag_original_positions: dict[int, tuple[float, float]] = {}
        self._spot_selection_rubber_band: QRubberBand | None = None
        self._spot_selection_drag_start: tuple[float, float] | None = None
        self._spot_selection_drag_button: Qt.MouseButton | None = None
        self._spot_selection_pressed_spot_id: int | None = None
        self._spot_selection_drag_modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
        self._spot_edit_refresh_pending = False
        self._processed_image_cache: OrderedDict[tuple[object, ...], np.ndarray] = OrderedDict()
        self._processed_shape_cache: dict[tuple[object, ...], tuple[int, int]] = {}
        self._reference_contrast_cache: dict[str, float] = {}
        self._ignored_mask_cache_signature: tuple[object, ...] | None = None
        self._ignored_mask_cache_value: np.ndarray | None = None
        self._roi_mask_cache_signature: tuple[object, ...] | None = None
        self._roi_mask_cache_values: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._histogram_source_cache_signature: tuple[object, ...] | None = None
        self._histogram_source_cache_values: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._histogram_log_range_guard = False
        self._absorbance_spectrum_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._absorbance_frame_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._absorbance_roi_mask_cache: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._sensorgram_cache: OrderedDict[tuple[object, ...], SensorgramComputationResult] = OrderedDict()
        self._sensorgram_frame_payload_cache: OrderedDict[tuple[object, ...], tuple[object, ...]] = OrderedDict()
        self._analysis_cache_lock = threading.Lock()
        self._last_absorbance_fit_seconds: float | None = None
        self._last_saved_processing_signature: str | None = None
        self._last_saved_preprocessing_path: Path | None = None
        self._last_saved_profile_path: Path | None = None
        self._last_saved_spot_table_signature: str | None = None
        self._last_saved_spot_table_path: Path | None = None
        self._image_refresh_running = False
        self._pending_image_refresh_payload: tuple[object, ...] | None = None
        self._latest_image_refresh_signature: tuple[object, ...] | None = None
        self._image_refresh_started_at: float | None = None
        self._spot_metrics_request_id = 0
        self._spot_detection_request_id = 0
        self._background_profile_request_id = 0
        self._absorbance_spectrum_request_id = 0
        self._sensorgram_request_id = 0
        self._chromatic_registration_request_id = 0
        self._ome_zarr_export_request_id = 0
        self._absorbance_spectrum_running = False
        self._absorbance_spectrum_running_signature: tuple[object, ...] | None = None
        self._pending_absorbance_spectrum_payload: tuple[object, ...] | None = None
        self._absorbance_spectrum_dirty = True
        self._absorbance_spectrum_started_at: float | None = None
        self._spot_absorbance_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._analysis_live_preview_enabled = self._settings_bool("analysis/live_preview", False)
        self._histogram_log_y_enabled = self._settings_bool("histogram/log_y", False)
        self._startup_ready = False
        self._startup_restore_in_progress = False
        self._startup_progress_callback: Callable[[int, str], None] | None = None
        self._sensorgram_running = False
        self._sensorgram_running_signature: tuple[object, ...] | None = None
        self._sensorgram_cancel_event: threading.Event | None = None
        self._sensorgram_started_at: float | None = None
        self._pending_sensorgram_payload: tuple[
            tuple[object, ...],
            list[int],
            tuple[int, ...],
            list[DetectedSpot],
        ] | None = None
        self._ome_zarr_export_running = False
        self._ome_zarr_export_cancel_event: threading.Event | None = None
        self._ome_zarr_export_started_at: float | None = None
        self._ome_zarr_export_destination: Path | None = None
        self._busy_started_at: float | None = None
        self._busy_is_determinate = False
        self._busy_last_percent: int = 0
        self._absorbance_prep_request_id = 0
        self._absorbance_prep_running = False
        self._absorbance_prep_started_at: float | None = None
        self._absorbance_prep_request_signature: tuple[object, ...] | None = None
        self._sensorgram_frame_indices = np.asarray([], dtype=np.int32)
        self._sensorgram_metric_values = np.asarray([], dtype=np.float64)
        self._sensorgram_metric_signal = np.asarray([], dtype=np.float64)
        self._display_spot_cache_signature: tuple[object, ...] | None = None
        self._display_spot_cache_value: list[DetectedSpot] | None = None
        self._selected_source_spots_cache_signature: tuple[object, ...] | None = None
        self._selected_source_spots_cache_value: tuple[DetectedSpot, ...] = tuple()
        self._processed_mask_view_cache_signature: tuple[object, ...] | None = None
        self._processed_mask_view_cache_value: np.ndarray | None = None
        self._background_profile_cache_signature: tuple[object, ...] | None = None
        self._background_profile_cache_image: np.ndarray | None = None
        self._showing_background_profile_main = False
        self._busy_operation_count = 0
        self._wait_cursor_active = False
        self._undo_stack: list[UndoSnapshot] = []
        self._redo_stack: list[UndoSnapshot] = []
        self._prepared_undo_snapshot: UndoSnapshot | None = None
        self._restoring_undo = False
        self._spot_overlay_theta = np.linspace(0.0, 2.0 * np.pi, 48)
        self._image_tools_preview_only = False
        self._ome_zarr_chunk_controls_syncing = False
        theme = get_active_theme()
        self._spot_visual_color = QColor(theme.spot_color)
        self._mask_visual_color = QColor(theme.mask_color)
        self._histogram_mask_visual_color = QColor(theme.histogram_mask_color)
        self._figure_mask_visual_color = QColor(theme.figure_mask_color)
        self._ring_visual_color = QColor(theme.ring_color)
        self._highlight_visual_color = QColor(theme.highlight_color)
        self._scale_bar_visual_color = QColor(theme.scale_bar_color)
        self._spot_alpha = 0.8
        self._ring_alpha = 0.22
        self._mask_alpha = 0.5
        self._highlight_alpha = 0.42
        self._spots_visible = True
        self._spot_labels_visible = True
        self._mask_visible = True
        self._rings_visible = True
        self._highlight_visible = True
        self._reference_points_visible = True
        self._chromatic_reference_points_all_visible = False
        self._cached_spots_only_visible = self._settings_bool("layout/cached_spots_only_visible", False)
        self._restore_visual_preferences()
        self._image_refresh_timer = QTimer(self)
        self._image_refresh_timer.setSingleShot(True)
        self._image_refresh_timer.setInterval(45)
        self._image_refresh_timer.timeout.connect(self._refresh_image)
        self._histogram_refresh_timer = QTimer(self)
        self._histogram_refresh_timer.setSingleShot(True)
        self._histogram_refresh_timer.setInterval(35)
        self._histogram_refresh_timer.timeout.connect(self._refresh_histogram_if_available)
        self._absorbance_spectrum_timer = QTimer(self)
        self._absorbance_spectrum_timer.setSingleShot(True)
        self._absorbance_spectrum_timer.setInterval(80)
        self._absorbance_spectrum_timer.timeout.connect(self._refresh_absorbance_spectrum)
        self._sensorgram_refresh_timer = QTimer(self)
        self._sensorgram_refresh_timer.setSingleShot(True)
        self._sensorgram_refresh_timer.setInterval(100)
        self._sensorgram_refresh_timer.timeout.connect(self._refresh_sensorgram)
        self._processing_state_save_timer = QTimer(self)
        self._processing_state_save_timer.setSingleShot(True)
        self._processing_state_save_timer.setInterval(220)
        self._processing_state_save_timer.timeout.connect(self._save_processing_state_for_dataset)
        self._spot_state_save_timer = QTimer(self)
        self._spot_state_save_timer.setSingleShot(True)
        self._spot_state_save_timer.setInterval(250)
        self._spot_state_save_timer.timeout.connect(self._save_processing_state_for_dataset)
        self._spot_list_refresh_timer = QTimer(self)
        self._spot_list_refresh_timer.setSingleShot(True)
        self._spot_list_refresh_timer.setInterval(25)
        self._spot_list_refresh_timer.timeout.connect(self._spot_table_controller.update_table)

        self.setWindowTitle("LSPR Imaging")

        last_folder = self._load_last_folder(default_folder)
        self.folder_edit = QLineEdit(str(last_folder), self)
        self.browse_button = self._free_standing_icon_label(
            self._dataset_folder_icon(),
            "Browse: choose a dataset folder.",
            size=24,
            parent=self,
        )
        self.load_button = self._free_standing_icon_label(
            self._dataset_transfer_icon("import", "#22c55e"),
            "Load dataset: choose a folder and load it into the app.",
            size=24,
            parent=self,
        )
        self.export_settings_button = QPushButton("Export settings", self)
        self.import_settings_button = QPushButton("Import settings", self)
        self.export_settings_button.hide()
        self.import_settings_button.hide()
        self.dataset_summary = QLabel("Load an image folder to begin.", self)
        self.dataset_summary.setWordWrap(True)
        self.dataset_summary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.dataset_stack_icon = QLabel(self)
        self.dataset_stack_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dataset_stack_icon.setPixmap(self._dataset_stack_icon_pixmap(36, ome_zarr=False))
        self.dataset_stack_icon.setToolTip("Image stack loaded from the selected dataset folder.")
        self.dataset_stack_label = QLabel("ImageStack", self)
        self.dataset_stack_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dataset_stack_label.setObjectName("toolbarMiniLabel")
        self.dataset_stack_label.setToolTip("The loaded dataset is treated as an image stack.")
        self.dataset_stack_widget = QWidget(self)
        dataset_stack_layout = QVBoxLayout(self.dataset_stack_widget)
        dataset_stack_layout.setContentsMargins(0, 0, 0, 0)
        dataset_stack_layout.setSpacing(2)
        dataset_stack_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        dataset_stack_layout.addWidget(self.dataset_stack_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        dataset_stack_layout.addWidget(self.dataset_stack_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.dataset_ome_zarr_export_button = self._make_icon_tool_button(
            "database-export",
            "#38bdf8",
            "Export: write the current dataset to a Stack to Zarr in a chosen folder.",
            icon=self._dataset_transfer_icon("export", "#38bdf8"),
        )
        self.ome_zarr_chunk_mode_combo = QComboBox(self)
        self.ome_zarr_chunk_mode_combo.addItem("Auto", "auto")
        self.ome_zarr_chunk_mode_combo.addItem("64", 64)
        self.ome_zarr_chunk_mode_combo.addItem("128", 128)
        self.ome_zarr_chunk_mode_combo.addItem("256", 256)
        self.ome_zarr_chunk_mode_combo.addItem("512", 512)
        self.ome_zarr_chunk_mode_combo.setCurrentIndex(
            max(
                self.ome_zarr_chunk_mode_combo.findData(
                    self._settings.value("ome_zarr/chunk_mode", "auto")
                ),
                0,
            )
        )
        self.ome_zarr_chunk_mode_combo.setToolTip(
            "Choose a common Zarr chunk size or let the app suggest one from the current image size."
        )
        self.ome_zarr_chunk_label = QLabel("Chunk tile", self)
        self.ome_zarr_chunk_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_chunk_label.setToolTip("Square spatial chunk size used when exporting Zarr.")
        self.ome_zarr_chunk_value_label = QLabel("256 px", self)
        self.ome_zarr_chunk_value_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_chunk_value_label.setToolTip("Resolved Zarr chunk size in pixels.")
        self.ome_zarr_chunk_guide_button = self._make_icon_tool_button(
            "grid-4x4",
            "#94a3b8",
            "Guide: show how the current Zarr chunk size would tile the visible image.",
            checkable=True,
            icon=self._ome_zarr_grid_icon(False),
        )
        self.ome_zarr_chunk_guide_button.setChecked(self._settings_bool("ome_zarr/chunk_guide_visible", False))
        self.ome_zarr_chunk_guide_button.toggled.connect(self._on_ome_zarr_chunk_guide_toggled)
        self.ome_zarr_compression_label = QLabel("Compression", self)
        self.ome_zarr_compression_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_compression_label.setToolTip("Toggle Zarr compression on or off.")
        self.ome_zarr_compression_button = self._make_icon_tool_button(
            "archive",
            "#22c55e",
            "Compression: enable or disable Zarr compression.",
            checkable=True,
            icon=self._ome_zarr_compression_icon(bool(self._settings_bool("ome_zarr/compression_enabled", True))),
        )
        self.ome_zarr_compression_button.setChecked(self._settings_bool("ome_zarr/compression_enabled", True))
        self.ome_zarr_compression_button.toggled.connect(self._on_ome_zarr_compression_toggled)
        self.ome_zarr_dtype_label = QLabel("Dtype: uint16", self)
        self.ome_zarr_dtype_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_dtype_label.setToolTip("Zarr export stores 16-bit TIFF data as uint16.")
        self.ome_zarr_pyramid_label = QLabel("Pyramid: off", self)
        self.ome_zarr_pyramid_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_pyramid_label.setToolTip("Pyramid export is currently disabled.")
        self.dataset_ome_zarr_export_status_label = QLabel("Progress", self)
        self.dataset_ome_zarr_export_status_label.setObjectName("toolbarMiniLabel")
        self.dataset_ome_zarr_export_status_label.setToolTip("Current Zarr export progress.")
        self.dataset_ome_zarr_export_progress_bar = QProgressBar(self)
        self.dataset_ome_zarr_export_progress_bar.setRange(0, 100)
        self.dataset_ome_zarr_export_progress_bar.setValue(0)
        self.dataset_ome_zarr_export_progress_bar.setFormat("%p%")
        self.dataset_ome_zarr_export_progress_bar.setTextVisible(True)
        self.dataset_ome_zarr_export_progress_bar.setFixedHeight(18)
        self.dataset_ome_zarr_export_progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dataset_ome_zarr_export_eta_label = QLabel("ETA: --:--", self)
        self.dataset_ome_zarr_export_eta_label.setObjectName("toolbarMiniLabel")
        self.dataset_ome_zarr_export_eta_label.setToolTip("Estimated time remaining for the current Zarr export.")
        self.dataset_ome_zarr_export_stop_button = self._make_icon_tool_button(
            "player-stop-filled",
            "#ef4444",
            "Stop the running Zarr export.",
            icon=self._ome_zarr_stop_icon(),
        )
        self.dataset_ome_zarr_controls_row = QWidget(self)
        dataset_ome_zarr_controls_layout = QHBoxLayout(self.dataset_ome_zarr_controls_row)
        dataset_ome_zarr_controls_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_controls_layout.setSpacing(4)
        dataset_ome_zarr_controls_layout.addWidget(self.dataset_ome_zarr_export_button)
        dataset_ome_zarr_controls_layout.addStretch(1)
        self.dataset_ome_zarr_options_row = QWidget(self)
        dataset_ome_zarr_options_layout = QHBoxLayout(self.dataset_ome_zarr_options_row)
        dataset_ome_zarr_options_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_options_layout.setSpacing(4)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_label)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_mode_combo)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_value_label)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_guide_button)
        dataset_ome_zarr_options_layout.addStretch(1)
        self.dataset_ome_zarr_compression_row = QWidget(self)
        dataset_ome_zarr_compression_layout = QHBoxLayout(self.dataset_ome_zarr_compression_row)
        dataset_ome_zarr_compression_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_compression_layout.setSpacing(4)
        dataset_ome_zarr_compression_layout.addWidget(self.ome_zarr_compression_label)
        dataset_ome_zarr_compression_layout.addWidget(self.ome_zarr_compression_button)
        dataset_ome_zarr_compression_layout.addStretch(1)
        self.dataset_ome_zarr_info_row = QWidget(self)
        dataset_ome_zarr_info_layout = QHBoxLayout(self.dataset_ome_zarr_info_row)
        dataset_ome_zarr_info_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_info_layout.setSpacing(4)
        dataset_ome_zarr_info_layout.addWidget(self.ome_zarr_dtype_label)
        dataset_ome_zarr_info_layout.addWidget(self.ome_zarr_pyramid_label)
        dataset_ome_zarr_info_layout.addStretch(1)
        self.dataset_ome_zarr_export_progress_row = QWidget(self)
        dataset_ome_zarr_export_progress_layout = QHBoxLayout(self.dataset_ome_zarr_export_progress_row)
        dataset_ome_zarr_export_progress_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_export_progress_layout.setSpacing(4)
        dataset_ome_zarr_export_progress_layout.addWidget(self.dataset_ome_zarr_export_status_label)
        dataset_ome_zarr_export_progress_layout.addWidget(self.dataset_ome_zarr_export_progress_bar, 1)
        dataset_ome_zarr_export_progress_layout.addWidget(self.dataset_ome_zarr_export_eta_label)
        dataset_ome_zarr_export_progress_layout.addWidget(self.dataset_ome_zarr_export_stop_button)
        self.dataset_ome_zarr_export_progress_row.hide()
        self._update_dataset_stack_indicator(None)
        self.reference_summary = QLabel("Reference: current selection", self)
        self.reference_summary.setWordWrap(True)
        self.reference_auto_button = QToolButton(self)
        self.reference_auto_button.setCheckable(True)
        self.reference_auto_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.reference_auto_button.setAutoRaise(True)
        self.reference_auto_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.reference_auto_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.reference_auto_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.reference_auto_button.setToolTip("Auto: Use the best wavelength in the current frame as the reference.")
        self.reference_auto_button.setIcon(self._reference_mode_icon("auto", False))
        self.reference_manual_button = QToolButton(self)
        self.reference_manual_button.setCheckable(True)
        self.reference_manual_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.reference_manual_button.setAutoRaise(True)
        self.reference_manual_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.reference_manual_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.reference_manual_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.reference_manual_button.setToolTip("Manual: Store the current frame and wavelength as the manual reference.")
        self.reference_manual_button.setIcon(self._reference_mode_icon("manual", False))
        self.reference_mode_button_group = QButtonGroup(self)
        self.reference_mode_button_group.setExclusive(True)
        self.reference_mode_button_group.addButton(self.reference_auto_button)
        self.reference_mode_button_group.addButton(self.reference_manual_button)
        self.reference_frame_status_label = QLabel("Frame: -", self)
        self.reference_wavelength_status_label = QLabel("Wavelength: -", self)
        self.reference_method_status_label = QLabel("Method: -", self)
        self.reference_mode_combo = QComboBox(self)
        self.reference_mode_combo.addItem("Auto", "auto")
        self.reference_mode_combo.addItem("Manual", "manual")
        self.reference_mode_combo.hide()
        self.set_reference_button = QPushButton("Use current", self)
        self.set_reference_button.hide()
        self.startup_restore_timeout_actions: dict[int, QAction] = {}
        self.chromatic_summary = QLabel("Idle.", self)
        self.chromatic_summary.setWordWrap(True)
        self.chromatic_apply_check = QPushButton("Apply radial transforms", self)
        self.chromatic_apply_check.setCheckable(True)
        self.chromatic_apply_check.setIcon(self._make_link_toggle_icon(False))
        self.chromatic_apply_check.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.chromatic_sample_count_spin = QSpinBox(self)
        self.chromatic_sample_count_spin.setRange(3, 7)
        self.chromatic_sample_count_spin.setSingleStep(2)
        self.chromatic_feature_count_spin = QComboBox(self)
        for value in (5, 15, 30):
            self.chromatic_feature_count_spin.addItem(str(value), value)
        default_feature_count = max(min(int(self._settings.value("chromatic/feature_count", 15)), 30), 5)
        default_feature_index = self.chromatic_feature_count_spin.findData(default_feature_count)
        if default_feature_index < 0:
            default_feature_index = self.chromatic_feature_count_spin.findData(15)
        if default_feature_index < 0:
            default_feature_index = 0
        self.chromatic_feature_count_spin.setCurrentIndex(default_feature_index)
        self.chromatic_feature_count_spin.setToolTip("Choose 5, 15, or 30 reference points.")
        self.chromatic_subpixel_precision_combo = QComboBox(self)
        for value in (1, 4, 9):
            self.chromatic_subpixel_precision_combo.addItem(str(value), value)
        default_subpixel_precision = max(min(int(self._settings.value("chromatic/subpixel_precision", 4)), 9), 1)
        default_subpixel_index = self.chromatic_subpixel_precision_combo.findData(default_subpixel_precision)
        if default_subpixel_index < 0:
            default_subpixel_index = self.chromatic_subpixel_precision_combo.findData(4)
        if default_subpixel_index < 0:
            default_subpixel_index = 0
        self.chromatic_subpixel_precision_combo.setCurrentIndex(default_subpixel_index)
        self.chromatic_subpixel_precision_combo.setToolTip(
            "Sub.px: choose the chromatic reference-point refinement level. 1 = pixel, 4 = moderate, 9 = finer."
        )
        self.chromatic_start_button = FreeStandingToggleIconLabel(self)
        self.chromatic_start_button.setFixedSize(28, 28)
        self.chromatic_start_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.chromatic_start_button.setIcon(self._make_spot_edit_icon())
        self.chromatic_start_button.setToolTip(
            "Edit: enter chromatic reference-point editing mode on the current sampled image."
        )
        self.chromatic_start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chromatic_auto_button = QToolButton(self)
        self.chromatic_auto_button.setAutoRaise(True)
        self.chromatic_auto_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.chromatic_auto_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.chromatic_auto_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.chromatic_auto_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.chromatic_auto_button.setIcon(self._chromatic_auto_icon(False))
        self.chromatic_auto_button.setToolTip(
            "Automatic spot detection: detect the chromatic reference points on the first sampled image and track them across the other sampled wavelengths."
        )
        self.chromatic_reference_points_all_button = self._create_view_toggle_button(
            "reference_points_all",
            self._chromatic_reference_points_all_visible,
            "Show all chromatic reference points across the sampled wavelengths. When chromatic transforms are linked, the points are transformed into the current image space.",
        )
        self.chromatic_prev_button = self._free_standing_icon_label(
            self._navigation_chevron_icon("left"),
            "Go to the previous sampled wavelength image.",
            size=24,
            parent=self,
        )
        self.chromatic_next_button = self._free_standing_icon_label(
            self._navigation_chevron_icon("right"),
            "Go to the next sampled wavelength image.",
            size=24,
            parent=self,
        )
        self.chromatic_progress_label = QLabel("No radial procedure started.", self)
        self.chromatic_progress_label.setWordWrap(True)
        self.chromatic_landmark_mark_button = self.chromatic_start_button
        self.chromatic_landmark_clear_button = self._free_standing_icon_label(
            self._make_remove_icon(),
            "Clear all saved chromatic reference points.",
            size=24,
            parent=self,
        )
        self.chromatic_landmark_id_spin = QSpinBox(self)
        self.chromatic_landmark_id_spin.setRange(1, 99)
        self.chromatic_landmark_id_spin.setValue(1)
        self.chromatic_landmark_id_spin.setPrefix("")
        self.chromatic_transform_button = self._free_standing_icon_label(
            self._chromatic_transform_icon(False),
            "Estimate chromatic transforms.",
            size=24,
            parent=self,
        )
        self.chromatic_estimate_button = self.chromatic_transform_button
        self.chromatic_clear_button = self.chromatic_transform_button
        self.spot_summary = QLabel("No spots detected.", self)
        self.spot_summary.setWordWrap(True)
        self.status_label = QLabel("Ready.", self)
        self.status_label.setWordWrap(True)
        self._status_bar_message = QLabel("Ready.", self)
        self._status_bar_message.setWordWrap(False)
        self._status_bar_busy_detail = QLabel("", self)
        self._status_bar_busy_detail.setWordWrap(False)
        self._status_bar_busy_detail.setMinimumWidth(140)
        self._status_bar_busy_detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._status_bar_busy_detail.setFont(QFont("Consolas", 9))
        self._status_bar_busy_detail.setStyleSheet("color: #94a3b8;")
        self._status_bar_last_action = QLabel("Last action: -", self)
        self._status_bar_last_action.setWordWrap(False)
        self._status_bar_hint = QLabel("Hint: Hover a control for guidance.", self)
        self._status_bar_hint.setWordWrap(False)
        self._status_bar_busy = BusySpinner(self)
        self._status_bar_busy.hide()
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)
        status_row = QWidget(self)
        status_row_layout = QHBoxLayout(status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(10)
        status_row_layout.addWidget(self._status_bar_busy, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row_layout.addWidget(self._status_bar_busy_detail, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row_layout.addWidget(self._status_bar_message, 2)
        status_row_layout.addWidget(self._status_bar_last_action, 1)
        status_row_layout.addWidget(self._status_bar_hint, 2)
        status_row_layout.addStretch(1)
        status_bar.addWidget(status_row, 1)
        self.histogram_plot = pg.PlotWidget(parent=self)
        self.histogram_plot.setMinimumHeight(100)
        self.histogram_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.histogram_plot.setMenuEnabled(False)
        histogram_plot_item = self.histogram_plot.getPlotItem()
        for button_name in ("autoBtn", "ctrlBtn"):
            button = getattr(histogram_plot_item, button_name, None)
            if button is not None:
                button.hide()
        self.histogram_legend = self.histogram_plot.addLegend(offset=(8, 8), labelTextColor="#e5edf7")
        self.histogram_curve = self.histogram_plot.plot(name="All pixels", pen=pg.mkPen("#ffffff", width=2.2))
        self.spot_histogram_curve = self.histogram_plot.plot(name="Spots")
        self.ring_histogram_curve = self.histogram_plot.plot(name="Ref. rings")
        self.mask_histogram_curve = self.histogram_plot.plot(name="Mask")
        self.residual_histogram_curve = self.histogram_plot.plot(name="Residual")
        self.residual_fill_item = pg.FillBetweenItem(self.histogram_curve.curve, self.residual_histogram_curve.curve)
        self.residual_fill_item.setZValue(-1)
        self.residual_fill_item.hide()
        self.histogram_plot.addItem(self.residual_fill_item)
        self.hist_region = pg.LinearRegionItem(values=(0.0, 1.0), brush=pg.mkBrush(56, 189, 248, 35))
        self.hist_region.setZValue(10)
        self.histogram_plot.addItem(self.hist_region)
        self.hist_region_label = pg.TextItem(anchor=(0.5, 1.0))
        self.hist_region_label.setZValue(13)
        self.histogram_plot.addItem(self.hist_region_label)
        self.ignore_region = pg.LinearRegionItem(values=(0.0, 1.0), brush=pg.mkBrush(239, 68, 68, 35))
        self.ignore_region.setZValue(11)
        self.histogram_plot.addItem(self.ignore_region)
        self.ignore_region_label = pg.TextItem(anchor=(0.5, 1.0))
        self.ignore_region_label.setZValue(14)
        self.histogram_plot.addItem(self.ignore_region_label)
        self.spot_histogram_label = pg.TextItem(anchor=(0.5, 1.0))
        self.spot_histogram_label.setZValue(12)
        self.spot_histogram_label.hide()
        self.histogram_plot.addItem(self.spot_histogram_label)
        self.ring_histogram_label = pg.TextItem(anchor=(0.5, 1.0))
        self.ring_histogram_label.setZValue(12)
        self.ring_histogram_label.hide()
        self.histogram_plot.addItem(self.ring_histogram_label)
        self.histogram_y_scale_button = self._free_standing_toggle_text_label(
            self._histogram_log_y_enabled,
            "Toggle histogram Y axis between linear and logarithmic scale.",
            unchecked_text="lin",
            checked_text="log",
            size=26,
            parent=self,
        )
        self.histogram_y_scale_button.setObjectName("histogramScaleToggle")
        self.histogram_y_scale_button.setToolTip("Toggle histogram Y axis between linear and logarithmic scale.")
        self.histogram_y_scale_button.toggled.connect(self._on_histogram_y_scale_toggled)
        self.spot_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.ring_inner_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.ring_outer_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.spot_geometry_scope_button = self._make_relation_scope_button(True, "Apply spot diameter to all spots when on, or only selected spots when off.")
        self.ring_geometry_scope_button = self._make_relation_scope_button(True, "Apply ring diameters to all spots when on, or only selected spots when off.")
        self.spot_geometry_area_label = QLabel("A_s = -, A_r = -, A_diff = -", self)
        self.flatten_background_check = QPushButton("Apply background removal", self)
        self.flatten_background_check.setCheckable(True)
        self.flatten_background_check.setIcon(self._make_link_toggle_icon(False))
        self.flatten_background_check.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.flatten_ignore_spot_area_check = self._make_icon_tool_button(
            "current-location-off",
            "#94a3b8",
            "Ignore the detected spot area while estimating the illumination background.",
            checkable=True,
            icon=self._background_exclusion_icon("current-location-off", False, size=APP_THEME.compact_icon_inner),
        )
        self.flatten_ignore_mask_check = self._make_icon_tool_button(
            "mask-off",
            "#94a3b8",
            "Ignore masked pixels while estimating the illumination background.",
            checkable=True,
            icon=self._background_exclusion_icon("mask-off", False, size=APP_THEME.compact_icon_inner),
        )
        self.flatten_ignore_spot_area_check.setChecked(True)
        self._sync_background_exclusion_buttons()
        self.flatten_ignore_spot_area_check.toggled.connect(
            lambda checked: self.flatten_ignore_spot_area_check.setIcon(
                self._background_exclusion_icon("current-location-off", checked, size=APP_THEME.compact_icon_inner)
            )
        )
        self.flatten_ignore_mask_check.toggled.connect(
            lambda checked: self.flatten_ignore_mask_check.setIcon(
                self._background_exclusion_icon("mask-off", checked, size=APP_THEME.compact_icon_inner)
            )
        )
        self.background_profile_hold_button = QToolButton(self)
        self.background_profile_hold_button.setText("")
        self.background_profile_hold_button.setObjectName("toolbarPlainIconButton")
        self.background_profile_hold_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.background_profile_hold_button.setAutoRaise(True)
        self.background_profile_hold_button.setCheckable(True)
        self.background_profile_hold_button.setChecked(False)
        self.background_profile_hold_button.setIcon(self._make_background_profile_icon(False, size=APP_THEME.compact_icon_inner))
        self.background_profile_hold_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.background_profile_hold_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.background_profile_hold_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.background_profile_hold_button.setToolTip(
            "Show or hide the estimated background profile instead of the main image."
        )
        self.background_profile_button = QToolButton(self)
        self.background_profile_button.setText("")
        self.background_profile_button.setObjectName("toolbarPlainIconButton")
        self.background_profile_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.background_profile_button.setAutoRaise(True)
        self.background_profile_button.setCheckable(True)
        self.background_profile_button.setChecked(False)
        self.background_profile_button.setIcon(self._make_background_profile_icon(False, size=APP_THEME.compact_icon_inner))
        self.background_profile_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.background_profile_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.background_profile_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.background_profile_button.setToolTip(
            "Show or hide the estimated background profile instead of the main image."
        )
        self.background_create_new_button = self._make_icon_tool_button(
            "sparkles",
            "#fbbf24",
            "Create a new background image from the current parameters.",
        )
        self.background_load_from_file_button = self._make_icon_tool_button(
            "download",
            "#38bdf8",
            "Load a background image from disk.",
        )
        self.background_save_button = self._make_icon_tool_button(
            "upload",
            "#22c55e",
            "Save the current background image to disk.",
        )
        self.flatten_background_sigma_spin = QSpinBox(self)
        self.flatten_background_sigma_spin.setRange(3, 2000)
        self.flatten_background_sigma_spin.setSuffix(" px")
        self.flatten_background_sigma_spin.setKeyboardTracking(False)
        self.flatten_background_binning_combo = QComboBox(self)
        self.flatten_background_binning_combo.addItem("1x1", 1)
        self.flatten_background_binning_combo.addItem("2x2", 2)
        self.flatten_background_binning_combo.addItem("4x4", 4)
        # Clearer aliases for the background section controls.
        self.background_removal_link = self.flatten_background_check
        self.background_ignore_spot_button = self.flatten_ignore_spot_area_check
        self.background_ignore_mask_button = self.flatten_ignore_mask_check
        self.background_smoothing_sigma_spin = self.flatten_background_sigma_spin
        self.background_smoothing_binning_combo = self.flatten_background_binning_combo
        self.mask_mode_combo = QComboBox(self)
        self.mask_mode_combo.addItem("Absolute", "absolute")
        self.mask_mode_combo.addItem("Relative", "relative")
        self.mask_mode_combo.addItem("Local contrast", "local_contrast")
        self.mask_profile_sigma_spin = QSpinBox(self)
        self.mask_profile_sigma_spin.setRange(3, 2000)
        self.mask_profile_sigma_spin.setSuffix(" px")
        self.mask_profile_sigma_spin.setKeyboardTracking(False)
        self.mask_relative_threshold_spin = QDoubleSpinBox(self)
        self.mask_relative_threshold_spin.setRange(0.1, 500.0)
        self.mask_relative_threshold_spin.setDecimals(1)
        self.mask_relative_threshold_spin.setSingleStep(1.0)
        self.mask_relative_threshold_spin.setSuffix(" %")
        self.mask_relative_threshold_spin.setKeyboardTracking(False)
        self.mask_local_sigma_spin = QSpinBox(self)
        self.mask_local_sigma_spin.setRange(1, 2000)
        self.mask_local_sigma_spin.setSuffix(" px")
        self.mask_local_sigma_spin.setKeyboardTracking(False)
        self.mask_local_z_spin = QDoubleSpinBox(self)
        self.mask_local_z_spin.setRange(0.1, 20.0)
        self.mask_local_z_spin.setDecimals(1)
        self.mask_local_z_spin.setSingleStep(0.1)
        self.mask_local_z_spin.setSuffix(" sigma")
        self.mask_local_z_spin.setKeyboardTracking(False)
        self.mask_mode_hint = QLabel("Absolute mask range is set directly in the histogram.", self)
        self.mask_mode_hint.setWordWrap(True)
        self.mask_morph_radius_spin = QSpinBox(self)
        self.mask_morph_radius_spin.setRange(1, 100)
        self.mask_morph_radius_spin.setValue(2)
        self.mask_morph_radius_spin.setSuffix(" px")
        self.mask_pencil_check = QCheckBox("Pencil", self)
        self.mask_draw_mode_combo = QComboBox(self)
        self.mask_draw_mode_combo.addItem("Add", "add")
        self.mask_draw_mode_combo.addItem("Erase", "erase")
        self.mask_draw_add_button = self._make_icon_tool_button(
            "square-rounded-plus",
            "#22c55e",
            "Draw mask pixels onto the current blue mask preview.",
            checkable=True,
        )
        self.mask_draw_remove_button = self._make_icon_tool_button(
            "square-rounded-minus",
            "#ef4444",
            "Erase mask pixels from the current blue mask preview.",
            checkable=True,
        )
        draw_hover, draw_pressed, draw_checked = icon_accent_colors("blue")
        for button in (self.mask_draw_add_button, self.mask_draw_remove_button):
            button.setStyleSheet(
                transparent_icon_button_stylesheet(
                    hover=draw_hover,
                    pressed=draw_pressed,
                    checked=draw_checked,
                )
            )
        self.mask_brush_size_spin = QSpinBox(self)
        self.mask_brush_size_spin.setRange(1, 200)
        self.mask_brush_size_spin.setValue(12)
        self.mask_brush_size_spin.setSuffix(" px")
        
        # New mask controls for redesigned system
        self.mask_create_new_button = self._make_icon_tool_button("sparkles", "#fbbf24", "Start a new blank mask.")
        self.mask_load_from_file_button = self._make_icon_tool_button("download", "#38bdf8", "Load a mask image.")
        self.mask_save_button = self._make_icon_tool_button("upload", "#22c55e", "Save the current mask image.")
        
        # Histogram mask controls
        self.histogram_mask_apply_button = self._make_icon_tool_button("square-rounded-plus", "#22c55e", "Add highlighted histogram pixels to the current mask.")
        self.histogram_mask_reset_button = self._make_icon_tool_button("square-rounded-minus", "#ef4444", "Subtract highlighted histogram pixels from the current mask.")
        
        # Figure mask controls  
        self.relative_mask_apply_button = self._make_icon_tool_button("square-rounded-plus", "#22c55e", "Add the relative-threshold preview to the current mask.")
        self.relative_mask_reset_button = self._make_icon_tool_button("square-rounded-minus", "#ef4444", "Subtract the relative-threshold preview from the current mask.")
        self.relative_mask_show_button = self._make_icon_tool_button("eye-closed", "#38bdf8", "Show the relative-threshold preview.", checkable=True)
        self.local_contrast_mask_apply_button = self._make_icon_tool_button("square-rounded-plus", "#22c55e", "Add the local-contrast preview to the current mask.")
        self.local_contrast_mask_reset_button = self._make_icon_tool_button("square-rounded-minus", "#ef4444", "Subtract the local-contrast preview from the current mask.")
        self.local_contrast_mask_show_button = self._make_icon_tool_button("eye-closed", "#38bdf8", "Show the local-contrast preview.", checkable=True)
        self.morphology_mask_apply_button = self._make_icon_tool_button("square-rounded-plus", "#22c55e", "Add the current morphology preview to the current mask.")
        self.morphology_mask_reset_button = self._make_icon_tool_button("square-rounded-minus", "#ef4444", "Subtract the current morphology preview from the current mask.")
        self.morphology_mask_show_button = self._make_icon_tool_button("eye-closed", "#38bdf8", "Show the morphology preview.", checkable=True)
        
        # Updated existing controls with new names
        self.mask_relative_profile_sigma_spin = QSpinBox(self)
        self.mask_relative_profile_sigma_spin.setRange(3, 2000)
        self.mask_relative_profile_sigma_spin.setValue(self._settings_int("mask_tools/relative_sigma_px", 48, minimum=3, maximum=2000))
        self.mask_relative_profile_sigma_spin.setSuffix(" px")
        self.mask_relative_profile_sigma_spin.setKeyboardTracking(False)
        self.mask_relative_threshold_spin = QDoubleSpinBox(self)
        self.mask_relative_threshold_spin.setRange(0.1, 500.0)
        self.mask_relative_threshold_spin.setDecimals(1)
        self.mask_relative_threshold_spin.setSingleStep(1.0)
        try:
            self.mask_relative_threshold_spin.setValue(
                max(float(self._settings.value("mask_tools/relative_threshold_percent", 18.0)), 0.1)
            )
        except (TypeError, ValueError):
            self.mask_relative_threshold_spin.setValue(18.0)
        self.mask_relative_threshold_spin.setSuffix(" %")
        self.mask_relative_threshold_spin.setKeyboardTracking(False)
        self.mask_local_contrast_sigma_spin = QSpinBox(self)
        self.mask_local_contrast_sigma_spin.setRange(1, 2000)
        self.mask_local_contrast_sigma_spin.setValue(self._settings_int("mask_tools/local_sigma_px", 8, minimum=1, maximum=2000))
        self.mask_local_contrast_sigma_spin.setSuffix(" px")
        self.mask_local_contrast_sigma_spin.setKeyboardTracking(False)
        self.mask_local_contrast_z_spin = QDoubleSpinBox(self)
        self.mask_local_contrast_z_spin.setRange(0.1, 20.0)
        self.mask_local_contrast_z_spin.setDecimals(1)
        self.mask_local_contrast_z_spin.setSingleStep(0.1)
        try:
            self.mask_local_contrast_z_spin.setValue(
                max(float(self._settings.value("mask_tools/local_z_threshold", 3.0)), 0.1)
            )
        except (TypeError, ValueError):
            self.mask_local_contrast_z_spin.setValue(3.0)
        self.mask_local_contrast_z_spin.setSuffix(" sigma")
        self.mask_local_contrast_z_spin.setKeyboardTracking(False)
        self.mask_morphology_radius_spin = QSpinBox(self)
        self.mask_morphology_radius_spin.setRange(1, 100)
        self.mask_morphology_radius_spin.setValue(self._settings_int("mask_tools/morphology_radius_px", 2, minimum=1, maximum=100))
        self.mask_morphology_radius_spin.setSuffix(" px")
        self.mask_morphology_erode_button = self._make_mask_morphology_button(
            "erode",
            "Shrink the loaded or hand-drawn file mask.",
        )
        self.mask_morphology_dilate_button = self._make_mask_morphology_button(
            "dilate",
            "Expand the loaded or hand-drawn file mask.",
        )
        self.mask_morphology_open_button = self._make_mask_morphology_button(
            "open",
            "Opening: erode then dilate the file mask to remove small islands.",
        )
        self.mask_morphology_close_button = self._make_mask_morphology_button(
            "close",
            "Closing: dilate then erode the file mask to fill small gaps.",
        )
        saved_morphology_operation = str(self._settings.value("mask_tools/morphology_operation", "") or "").strip().lower()
        self._mask_morphology_operation: str | None = saved_morphology_operation if saved_morphology_operation in {"erode", "dilate", "open", "close"} else None
        self.mask_morphology_erode_button.setChecked(self._mask_morphology_operation == "erode")
        self.mask_morphology_dilate_button.setChecked(self._mask_morphology_operation == "dilate")
        self.mask_morphology_open_button.setChecked(self._mask_morphology_operation == "open")
        self.mask_morphology_close_button.setChecked(self._mask_morphology_operation == "close")

        self.mask_draw_mode_combo.setCurrentIndex(
            max(self.mask_draw_mode_combo.findData(str(self._settings.value("mask_tools/draw_mode", "add") or "add")), 0)
        )
        self.mask_brush_size_spin.setValue(self._settings_int("mask_tools/brush_size_px", 12, minimum=1, maximum=200))
        self.mask_draw_mode_combo.setVisible(False)
        self._sync_mask_draw_mode_buttons()
        self.relative_mask_show_button.setChecked(self._settings_bool("mask_tools/relative_preview", False))
        self.local_contrast_mask_show_button.setChecked(self._settings_bool("mask_tools/local_preview", False))
        self.morphology_mask_show_button.setChecked(self._settings_bool("mask_tools/morphology_preview", False))
        self._set_mask_preview_button_icon(self.relative_mask_show_button, self.relative_mask_show_button.isChecked())
        self._set_mask_preview_button_icon(self.local_contrast_mask_show_button, self.local_contrast_mask_show_button.isChecked())
        self._set_mask_preview_button_icon(self.morphology_mask_show_button, self.morphology_mask_show_button.isChecked())

        self.spot_detection_auto_button = self._make_icon_tool_button(
            "grid-dots",
            "#22c55e",
            "Mode A: automatically detect spots from the known array grid and spacing.",
        )
        self.spot_corner_select_button = self._make_icon_tool_button(
            "layout-grid",
            "#94a3b8",
            "Mode B: corner-seeded detection (coming later).",
            icon=self._make_corner_seed_icon("#94a3b8"),
        )
        self.spot_corner_select_button.setEnabled(False)

        self.histogram_bins_spin = QSpinBox(self)
        self.histogram_bins_spin.setRange(1, 8192)
        self.histogram_bins_spin.setSingleStep(16)
        self.histogram_bins_spin.setValue(self._settings_histogram_bin_size())
        self.histogram_bins_spin.setSuffix(" DN")
        self.histogram_bins_spin.setKeyboardTracking(False)
        self.analysis_refresh_button = self._free_standing_icon_label(
            self._make_analysis_spectrum_icon(False),
            "Calculate spectrum.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_preview_button = self._free_standing_icon_label(
            self._make_analysis_preview_icon(self._analysis_live_preview_enabled),
            "Live preview: update the spectrum and sensorgram when spot selection changes.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_calculate_all_button = self._free_standing_icon_label(
            self._make_analysis_all_frames_icon(False),
            "Calculate all frames.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_calculate_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_stop_button = self._free_standing_icon_label(
            self._make_analysis_stop_icon(False),
            "Stop.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_spot_table_button = self._free_standing_icon_label(
            self._make_spot_list_icon(False),
            "Show or hide the spot list table.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_spot_table_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_poly_order_spin = QSpinBox(self)
        self.analysis_poly_order_spin.setRange(1, 12)
        self.analysis_poly_order_spin.setValue(self._settings_int("analysis/poly_order", 3, minimum=1, maximum=12))
        self.analysis_poly_order_spin.setKeyboardTracking(False)
        self.analysis_poly_order_spin.setToolTip("Polynomial order used for spectrum fitting.")
        self.analysis_metric_combo = QComboBox(self)
        self.analysis_metric_combo.addItem("Maximum", "maximum")
        self.analysis_metric_combo.addItem("Centroid", "centroid")
        stored_metric = str(self._settings.value("analysis/metric", "centroid") or "centroid").strip().lower()
        metric_index = max(self.analysis_metric_combo.findData(stored_metric), 0)
        self.analysis_metric_combo.setCurrentIndex(metric_index)
        self.analysis_start_frame_spin = QSpinBox(self)
        self.analysis_start_frame_spin.setEnabled(False)
        self.analysis_start_frame_spin.setKeyboardTracking(False)
        self.analysis_end_frame_spin = QSpinBox(self)
        self.analysis_end_frame_spin.setEnabled(False)
        self.analysis_end_frame_spin.setKeyboardTracking(False)
        self.analysis_formula_label = QLabel("A = log10(Iref. ring / Ispot)", self)
        self.analysis_formula_label.setWordWrap(True)
        self.analysis_summary_label = QLabel("Select spots to show absorbance spectrum.", self)
        self.analysis_summary_label.setWordWrap(True)
        self.spectrum_summary_label = QLabel("Select spots to show absorbance spectrum.", self)
        self.spectrum_summary_label.setWordWrap(True)
        self.spectrum_plot = pg.PlotWidget(parent=self)
        self.spectrum_plot.setMinimumHeight(120)
        self.spectrum_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.spectrum_plot.setMenuEnabled(False)
        self.spectrum_legend = self.spectrum_plot.addLegend(offset=(8, 8), labelTextColor="#e5edf7")
        self._spectrum_series_items: list[pg.PlotDataItem] = []
        self.spectrum_curve: pg.PlotDataItem | None = None
        self.spectrum_fit_curve: pg.PlotDataItem | None = None
        self.spectrum_current_point = self.spectrum_plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=9,
            symbolBrush=pg.mkBrush("#f8fafc"),
            symbolPen=pg.mkPen("#f59e0b", width=2),
        )
        self.spectrum_metric_point = self.spectrum_plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=8,
            symbolBrush=pg.mkBrush("#22c55e"),
            symbolPen=pg.mkPen("#bbf7d0", width=1.6),
        )
        self.spectrum_cursor_line = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.4, style=Qt.PenStyle.DashLine),
        )
        self.spectrum_cursor_line.setZValue(20)
        self.spectrum_cursor_line.hide()
        self.spectrum_plot.addItem(self.spectrum_cursor_line)
        self.sensorgram_summary_label = QLabel("Calculate all frames to build the fitted sensorgram.", self)
        self.sensorgram_summary_label.setWordWrap(True)
        self.sensorgram_plot = pg.PlotWidget(parent=self)
        self.sensorgram_plot.setMinimumHeight(110)
        self.sensorgram_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.sensorgram_plot.setMenuEnabled(False)
        self.sensorgram_curve = self.sensorgram_plot.plot(
            [],
            [],
            pen=pg.mkPen("#22c55e", width=2.2),
            symbol="o",
            symbolSize=6,
            symbolBrush=pg.mkBrush("#22c55e"),
            symbolPen=pg.mkPen("#bbf7d0", width=1.2),
        )
        self.sensorgram_current_point = self.sensorgram_plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=9,
            symbolBrush=pg.mkBrush("#f8fafc"),
            symbolPen=pg.mkPen("#22c55e", width=2),
        )
        self.sensorgram_cursor_line = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.4, style=Qt.PenStyle.DashLine),
        )
        self.sensorgram_cursor_line.setZValue(20)
        self.sensorgram_cursor_line.hide()
        self.sensorgram_plot.addItem(self.sensorgram_cursor_line)
        self.array_rows_spin = QSpinBox(self)
        self.array_rows_spin.setRange(0, 100)
        self.array_cols_spin = QSpinBox(self)
        self.array_cols_spin.setRange(0, 100)
        self.array_spacing_spin = QDoubleSpinBox(self)
        self.array_spacing_spin.setRange(0, 1000)
        self.array_spacing_spin.setDecimals(2)
        self.array_spacing_spin.setSingleStep(0.5)
        self.ring_inner_diameter_spin.setRange(0, 1_000_000)
        self.ring_outer_diameter_spin.setRange(0, 1_000_000)
        self.spot_diameter_spin.setDecimals(2)
        self.spot_diameter_spin.setSingleStep(0.5)
        self.ring_inner_diameter_spin.setDecimals(2)
        self.ring_inner_diameter_spin.setSingleStep(0.5)
        self.ring_outer_diameter_spin.setDecimals(2)
        self.ring_outer_diameter_spin.setSingleStep(0.5)
        self.spot_diameter_spin.setKeyboardTracking(False)
        self.ring_inner_diameter_spin.setKeyboardTracking(False)
        self.ring_outer_diameter_spin.setKeyboardTracking(False)
        self.array_spacing_spin.setKeyboardTracking(True)
        self.ignore_marked_check = QPushButton("Apply mask", self)
        self.ignore_marked_check.setCheckable(True)
        self.detect_spots_button = self.spot_detection_auto_button
        self.reorder_spots_button = self._make_icon_tool_button("sort-ascending-numbers", "#f8fafc", "Reorder spots by image position so the top-left spot becomes ID 1.")
        self.clear_spots_button = self._make_icon_tool_button("trash-x", "#ef4444", "Remove all detected spots and groups from the current dataset.")
        self.clear_spot_selection_button = QPushButton("Clear selection", self)

        self.frame_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.frame_slider.setEnabled(False)
        self.frame_spin = QSpinBox(self)
        self.frame_spin.setEnabled(False)
        self.wavelength_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.wavelength_slider.setEnabled(False)
        self.wavelength_spin = QDoubleSpinBox(self)
        self.wavelength_spin.setEnabled(False)
        self.wavelength_spin.setDecimals(2)
        self.wavelength_spin.setSuffix(" nm")
        self.frame_slider.installEventFilter(self)
        self.wavelength_slider.installEventFilter(self)
        self.frame_spin.installEventFilter(self)
        self.wavelength_spin.installEventFilter(self)
        self.chromatic_landmark_id_spin.installEventFilter(self)
        self.spot_diameter_spin.installEventFilter(self)
        self.ring_inner_diameter_spin.installEventFilter(self)
        self.ring_outer_diameter_spin.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.image_view = pg.GraphicsLayoutWidget(parent=self)
        self.image_plot = self.image_view.addPlot()
        self.image_plot.invertY(True)
        self.image_plot.setAspectLocked(True)
        self.image_plot.hideAxis("left")
        self.image_plot.hideAxis("bottom")
        self.image_plot.setMenuEnabled(False)
        self.image_item = pg.ImageItem()
        self.intensity_highlight_item = pg.ImageItem()
        self.intensity_highlight_item.hide()
        self.ignore_mask_item = pg.ImageItem()
        self.ignore_mask_item.hide()
        self.histogram_mask_item = pg.ImageItem()
        self.histogram_mask_item.hide()
        self.figure_mask_item = pg.ImageItem()
        self.figure_mask_item.hide()
        self.image_plot.addItem(self.image_item)
        self.image_plot.addItem(self.intensity_highlight_item)
        self.image_plot.addItem(self.ignore_mask_item)
        self.image_plot.addItem(self.histogram_mask_item)
        self.image_plot.addItem(self.figure_mask_item)
        self.image_name_label = QLabel(self)
        self.image_name_label.setObjectName("imageNameLabel")
        self.image_name_label.hide()
        self.spot_editor_labels_button = self._create_label_visibility_button(self._spot_labels_visible)
        self.measurement_status_label = QLabel("Ruler inactive.", self)
        self.measurement_um_x_spin = QDoubleSpinBox(self)
        self.measurement_um_y_spin = QDoubleSpinBox(self)
        self.measurement_apply_button = self._make_icon_tool_button("checkbox", "#22c55e", "Apply the entered micrometer distances to calibrate the image.")
        self.measurement_apply_button.setIconSize(QSize(APP_THEME.icon_button_inner, APP_THEME.icon_button_inner))
        self.measurement_apply_button.setFixedSize(APP_THEME.icon_button_outer, APP_THEME.icon_button_outer)
        self.measurement_um_x_spin.setRange(0.0, 1_000_000.0)
        self.measurement_um_y_spin.setRange(0.0, 1_000_000.0)
        self.measurement_um_x_spin.setDecimals(0)
        self.measurement_um_y_spin.setDecimals(0)
        self.measurement_um_x_spin.setSingleStep(1.0)
        self.measurement_um_y_spin.setSingleStep(1.0)
        self.measurement_unit_button = self._create_unit_toggle_button()
        self.scale_bar_toggle_button = self._create_scale_bar_toggle_button(bool(self._state.preprocessing.scale_bar_visible))
        self.scale_bar_color_button = QToolButton(self)
        self.scale_bar_color_button.setText("")
        self.scale_bar_color_button.setFixedSize(14, 14)
        self.scale_bar_color_button.setToolTip("Choose the scale bar color.")
        self._apply_compact_control_widths()
        self._apply_right_aligned_control_text()
        self.image_view.viewport().installEventFilter(self)
        self.image_plot.vb.sigRangeChanged.connect(lambda *_args: self._refresh_scale_bar_overlay())
        self.image_plot.vb.sigRangeChanged.connect(self._on_image_view_range_changed)
        self._build_layout()
        self._create_toolbar()
        self._connect_signals()
        self._update_mask_file_button_state()
        self._apply_dark_plot_theme()
        self._update_reference_controls()
        self._update_reference_summary()
        self._update_apply_button_labels()
        self._update_chromatic_summary()
        self._setup_workflow_logging()

    def _build_layout(self) -> None:
        from lspr_imaging_app.gui.layout_builder import build_layout

        build_layout(self)

    def _create_toolbar(self) -> None:
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(5)
        self.image_toolbar.setLayout(toolbar_layout)
        bottom_view_layout = QHBoxLayout()
        bottom_view_layout.setContentsMargins(0, 0, 0, 0)
        bottom_view_layout.setSpacing(5)
        self.bottom_view_toolbar.setLayout(bottom_view_layout)

        self.rotate_action = QAction(self._make_rotate_icon(), "Rotate", self)
        self.rotate_action.setCheckable(True)
        self.rotate_action.setToolTip(
            "Manual rotation tool. Arrow keys adjust angle: default 0.1 deg, Ctrl = 1 deg, Shift = 5 deg."
        )

        self.crop_action = QAction(self._make_crop_icon(), "Crop", self)
        self.crop_action.setCheckable(True)
        self.crop_action.setToolTip(
            "Crop tool: show and adjust the crop rectangle directly on the image."
        )

        self.flip_horizontal_action = QAction(self._make_flip_horizontal_icon(), "Flip H", self)
        self.flip_horizontal_action.setCheckable(True)
        self.flip_horizontal_action.setChecked(self._state.preprocessing.flip_horizontal)
        self.flip_horizontal_action.setToolTip("Flip the reference image horizontally.")

        self.flip_vertical_action = QAction(self._make_flip_vertical_icon(), "Flip V", self)
        self.flip_vertical_action.setCheckable(True)
        self.flip_vertical_action.setChecked(self._state.preprocessing.flip_vertical)
        self.flip_vertical_action.setToolTip("Flip the reference image vertically.")

        self.spot_edit_action = QAction(self._make_spot_edit_icon(), "Manual edit", self)
        self.spot_edit_action.setCheckable(True)
        self.spot_edit_action.setToolTip("Left-click or right-click to select spots. Ctrl-click for group selection. Left-drag in Move mode to correct spots.")
        self.spot_edit_action.setShortcut(QKeySequence("Ctrl+E"))
        self.spot_add_action = QAction(self._make_add_icon(), "Add", self)
        self.spot_add_action.setCheckable(True)
        self.spot_add_action.setEnabled(False)
        self.spot_add_action.setToolTip("Left-click in the image to add a new spot using the current spot diameter.")
        self.spot_add_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.spot_move_action = QAction(self._make_move_icon(), "Move", self)
        self.spot_move_action.setCheckable(True)
        self.spot_move_action.setEnabled(False)
        self.spot_move_action.setToolTip("Move selected spots by dragging or arrow keys while spot edit mode is active.")
        self.spot_move_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.remove_spots_action = QAction(self._make_remove_icon(), "Remove", self)
        self.remove_spots_action.setEnabled(False)
        self.remove_spots_action.setToolTip("Remove the selected spots and renumber the remaining array.")
        self.remove_spots_action.setShortcut(QKeySequence("Delete"))
        self.group_spots_action = QAction(self._make_group_icon(), "Group", self)
        self.group_spots_action.setEnabled(False)
        self.group_spots_action.setToolTip("Create or update a named group from the current selection.")
        self.group_spots_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.spot_list_action = QAction(self._make_spot_list_icon(), "Spot list", self)
        self.spot_list_action.setCheckable(True)
        self.spot_list_action.setToolTip("Show or hide the spot list table.")
        self.spot_list_action.setShortcut(QKeySequence("Ctrl+L"))

        self.reset_rotation_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Reset rotation", self)
        self.reset_rotation_action.setToolTip("Reset image rotation to 0 degrees.")

        self.reset_crop_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton), "Reset crop", self)
        self.reset_crop_action.setToolTip("Remove the current crop and show the full rotated image.")
        self.measure_action = QAction(self._make_measure_icon(False), "Measure", self)
        self.measure_action.setCheckable(True)
        self.measure_action.setToolTip("Measurement tool: place two guide crosses on the image to calibrate micrometers per pixel.")
        self.measure_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.undo_action = QAction(self._make_undo_icon(), "Undo", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.setEnabled(False)
        self.undo_action.setToolTip("Undo the last editing step.")
        self.redo_action = QAction(self._make_redo_icon(), "Redo", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.setEnabled(False)
        self.redo_action.setToolTip("Redo the last undone editing step.")
        self.shortcuts_action = QAction("Keyboard shortcuts", self)
        self.shortcuts_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        self.shortcuts_action.setToolTip("Show the main keyboard shortcuts used in the app.")
        self.reset_layout_action = QAction("Reset layout", self)
        self.reset_layout_action.setToolTip("Restore default splitter sizes and panel expanded states.")
        self.reset_dock_layout_action = QAction("Reset panel layout", self)
        self.reset_dock_layout_action.setToolTip("Restore default splitter sizes without changing panel visibility.")
        self.show_all_panels_action = QAction("Show all panels", self)
        self.show_all_panels_action.setToolTip("Show every workspace panel.")
        self.hide_all_panels_action = QAction("Hide all panels", self)
        self.hide_all_panels_action.setToolTip("Hide every workspace panel.")
        self.expand_left_panels_action = QAction("Expand left panels", self)
        self.collapse_left_panels_action = QAction("Collapse left panels", self)
        self.about_action = QAction("About", self)
        self.calculate_spectrum_action = QAction("Calculate spectrum", self)
        self.calculate_spectrum_action.setToolTip("Calculate the absorbance spectrum for the current frame and selected spots.")

        self.show_spots_check = self._create_view_toggle_button("spots", self._spots_visible, "Show or hide the spot overlays.")
        self.bottom_spot_labels_button = self._create_label_visibility_button(self._spot_labels_visible)
        self.show_rings_check = self._create_view_toggle_button("rings", self._rings_visible, "Show or hide the reference rings.")
        self.show_reference_points_check = self._create_view_toggle_button("reference_points", self._reference_points_visible, "Show or hide chromatic reference points.")
        self.show_mask_check = self._create_view_toggle_button("mask", self._mask_visible, "Show or hide the mask overlay.")
        self.show_highlight_check = self._create_view_toggle_button("highlight", self._highlight_visible, "Show or hide the histogram highlight overlay.")
        self.mask_color_button = QToolButton()
        self.mask_color_button.setText("")
        self.mask_color_button.setFixedSize(12, 12)
        self.spot_color_button = QToolButton()
        self.spot_color_button.setText("")
        self.spot_color_button.setFixedSize(12, 12)
        self.ring_color_button = QToolButton()
        self.ring_color_button.setText("")
        self.ring_color_button.setFixedSize(12, 12)
        self.highlight_color_button = QToolButton()
        self.highlight_color_button.setText("")
        self.highlight_color_button.setFixedSize(12, 12)
        self.mask_alpha_slider = CompactWedgeSlider(parent=self)
        self.mask_alpha_slider.setRange(0, 100)
        self.mask_alpha_slider.setValue(int(round(self._mask_alpha * 100.0)))
        self.spot_alpha_slider = CompactWedgeSlider(parent=self)
        self.spot_alpha_slider.setRange(0, 100)
        self.spot_alpha_slider.setValue(int(round(self._spot_alpha * 100.0)))
        self.ring_alpha_slider = CompactWedgeSlider(parent=self)
        self.ring_alpha_slider.setRange(0, 100)
        self.ring_alpha_slider.setValue(int(round(self._ring_alpha * 100.0)))
        self.highlight_alpha_slider = CompactWedgeSlider(parent=self)
        self.highlight_alpha_slider.setRange(0, 100)
        self.highlight_alpha_slider.setValue(int(round(self._highlight_alpha * 100.0)))

        image_tools_row = self._create_toolbar_row(
            [
                self._create_image_tool_icon_button(self.rotate_action, accent="yellow"),
                self._create_image_tool_icon_button(self.reset_rotation_action, accent="yellow"),
                self._create_image_tool_icon_button(self.crop_action, accent="blue"),
                self._create_image_tool_icon_button(self.reset_crop_action, accent="blue"),
                self._create_image_tool_icon_button(self.flip_horizontal_action, accent="cyan"),
                self._create_image_tool_icon_button(self.flip_vertical_action, accent="cyan"),
                self._create_image_tool_icon_button(self.measure_action, accent="green"),
            ]
        )
        self.measurement_info_row = self._build_measurement_controls_row()
        self.measurement_info_row.setVisible(False)
        self.measurement_controls_widget = QWidget(self.image_toolbar)
        measurement_layout = QVBoxLayout(self.measurement_controls_widget)
        measurement_layout.setContentsMargins(0, 0, 0, 0)
        measurement_layout.setSpacing(4)
        measurement_layout.addWidget(image_tools_row)
        measurement_layout.addWidget(self.measurement_info_row)
        view_section = self._create_toolbar_section(
            "View",
            [
                self._create_toolbar_row([
                    self._create_view_control("", self.show_spots_check, self.spot_color_button, self.spot_alpha_slider),
                    self._create_toolbar_icon_toggle_control("", self.bottom_spot_labels_button),
                    self._create_view_control("", self.show_rings_check, self.ring_color_button, self.ring_alpha_slider),
                    self._create_toolbar_icon_toggle_control("", self.show_reference_points_check),
                    self._create_view_control("", self.show_mask_check, self.mask_color_button, self.mask_alpha_slider),
                    self._create_view_control("", self.show_highlight_check, self.highlight_color_button, self.highlight_alpha_slider),
                    self.background_profile_hold_button,
                ]),
            ],
        )

        toolbar_layout.addWidget(self.measurement_controls_widget, 0)
        toolbar_layout.addStretch(1)
        bottom_view_layout.addWidget(view_section, 0)
        bottom_view_layout.addStretch(1)
        bottom_view_layout.addWidget(self.measurement_unit_button, 0)
        bottom_view_layout.addWidget(self.scale_bar_color_button, 0)
        bottom_view_layout.addWidget(self.scale_bar_toggle_button, 0)
        self._populate_left_spot_editor_controls()
        self._refresh_image_tool_action_icons()
        self._update_display_unit_controls()
        self._create_menu_bar()
        theme_name = str(self._settings.value("ui/theme", "blue"))
        self._set_ui_theme("gray" if theme_name == "gray" else "blue")
        self._update_color_button_styles()

    def _connect_signals(self) -> None:
        self.browse_button.clicked.connect(self._dataset_controller.browse_folder)
        self.load_button.clicked.connect(self._dataset_controller.browse_folder)
        self.dataset_ome_zarr_export_button.clicked.connect(self._dataset_controller.export_current_dataset_to_ome_zarr)
        self.dataset_ome_zarr_export_stop_button.clicked.connect(self._stop_ome_zarr_export)
        self.ome_zarr_chunk_mode_combo.currentIndexChanged.connect(self._on_ome_zarr_chunk_mode_changed)
        self.export_settings_button.clicked.connect(self._export_processing_profile)
        self.import_settings_button.clicked.connect(self._import_processing_profile)
        self.reference_auto_button.clicked.connect(lambda _checked=False: self._set_reference_mode("auto"))
        self.reference_manual_button.clicked.connect(lambda _checked=False: self._set_current_reference_from_view())
        self.chromatic_apply_check.toggled.connect(self._update_chromatic_settings)
        self.chromatic_apply_check.toggled.connect(self.chromatic_section.set_applied)
        self.chromatic_apply_check.toggled.connect(
            lambda checked: self.chromatic_apply_check.setIcon(self._make_link_toggle_icon(bool(checked)))
        )
        self.chromatic_start_button.toggled.connect(self._on_chromatic_landmark_tool_toggled)
        self.chromatic_auto_button.clicked.connect(self._chromatic_controller.auto_detect_landmarks)
        self.chromatic_reference_points_all_button.toggled.connect(self._on_chromatic_reference_points_all_toggled)
        self.chromatic_prev_button.clicked.connect(lambda: self._navigate_chromatic_sample(-1))
        self.chromatic_next_button.clicked.connect(lambda: self._navigate_chromatic_sample(1))
        self.chromatic_landmark_clear_button.clicked.connect(self._clear_chromatic_landmarks)
        self.chromatic_landmark_id_spin.valueChanged.connect(self._on_chromatic_landmark_id_changed)
        self.chromatic_sample_count_spin.valueChanged.connect(self._on_chromatic_sample_count_changed)
        self.chromatic_feature_count_spin.currentIndexChanged.connect(self._on_chromatic_feature_count_changed)
        self.chromatic_subpixel_precision_combo.currentIndexChanged.connect(self._on_chromatic_subpixel_precision_changed)
        self.chromatic_transform_button.clicked.connect(self._on_chromatic_transform_button_clicked)
        self.frame_slider.valueChanged.connect(lambda _value: self._sync_analysis_plot_cursors())
        self.frame_slider.valueChanged.connect(lambda _value: self._schedule_image_refresh())
        self.frame_slider.valueChanged.connect(lambda _value: self._sync_auto_reference_to_current_frame())
        self.wavelength_slider.valueChanged.connect(lambda _value: self._sync_analysis_plot_cursors())
        self.wavelength_slider.valueChanged.connect(lambda _value: self._schedule_image_refresh())
        self.frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        self.wavelength_spin.valueChanged.connect(self._on_wavelength_spin_changed)
        self.hist_region.sigRegionChanged.connect(self._update_selected_intensity_overlay)
        self.hist_region.sigRegionChanged.connect(self._update_histogram_region_labels)
        self.hist_region.sigRegionChangeFinished.connect(self._on_histogram_region_changed)
        self.ignore_region.sigRegionChanged.connect(self._preview_ignore_region_overlay)
        self.ignore_region.sigRegionChanged.connect(self._update_histogram_region_labels)
        self.ignore_region.sigRegionChangeFinished.connect(self._on_ignore_region_changed)
        self.spectrum_cursor_line.sigPositionChangeFinished.connect(self._on_spectrum_cursor_moved)
        self.sensorgram_cursor_line.sigPositionChangeFinished.connect(self._on_sensorgram_cursor_moved)
        self.histogram_bins_spin.valueChanged.connect(self._on_histogram_bins_changed)
        self.histogram_plot.getViewBox().sigRangeChanged.connect(self._on_histogram_view_range_changed)
        self.histogram_bins_spin.editingFinished.connect(self._save_control_preferences)
        self.analysis_refresh_button.clicked.connect(self._analysis_controller.refresh_absorbance_spectrum)
        self.analysis_preview_button.clicked.connect(self._toggle_analysis_live_preview)
        self.analysis_calculate_all_button.clicked.connect(self._analysis_controller.calculate_sensorgram)
        self.analysis_stop_button.clicked.connect(self._analysis_controller.stop_sensorgram)
        self.analysis_spot_table_button.clicked.connect(
            lambda: self.spot_list_action.setChecked(not self.spot_list_action.isChecked())
        )
        self.spot_list_cached_button.toggled.connect(self._on_cached_spots_only_toggled)
        self.analysis_poly_order_spin.valueChanged.connect(self._analysis_controller.on_fit_settings_changed)
        self.analysis_metric_combo.currentIndexChanged.connect(self._analysis_controller.on_fit_settings_changed)
        self.analysis_start_frame_spin.valueChanged.connect(self._analysis_controller.on_frame_range_changed)
        self.analysis_end_frame_spin.valueChanged.connect(self._analysis_controller.on_frame_range_changed)
        self.background_removal_link.toggled.connect(self.background_section.set_applied)
        self.background_removal_link.toggled.connect(
            lambda checked: self.background_removal_link.setIcon(self._make_link_toggle_icon(bool(checked)))
        )
        self.ignore_marked_check.toggled.connect(self.mask_section.set_applied)
        self.chromatic_section.apply_changed.connect(self._chromatic_controller.section_applied_changed)
        self.mask_section.apply_changed.connect(self._on_mask_section_applied_changed)
        self.image_tools_section.apply_changed.connect(self._on_image_tools_section_applied_changed)
        self.spot_editor_section.apply_changed.connect(self._on_live_geometry_toggled)
        self.background_section.apply_changed.connect(self._on_background_section_applied_changed)
        self.analysis_section.apply_changed.connect(self._on_analysis_section_applied_changed)
        self.shortcuts_action.triggered.connect(self._show_shortcuts_dialog)
        self.reset_layout_action.triggered.connect(self._reset_layout_to_defaults)
        self.reset_dock_layout_action.triggered.connect(self._reset_panel_layout)
        self.show_all_panels_action.triggered.connect(lambda *_: self._set_all_panel_visibility(True))
        self.hide_all_panels_action.triggered.connect(lambda *_: self._set_all_panel_visibility(False))
        self.expand_left_panels_action.triggered.connect(self._expand_left_panels)
        self.collapse_left_panels_action.triggered.connect(self._collapse_left_panels)
        self.about_action.triggered.connect(self._show_about_dialog)
        self.calculate_spectrum_action.triggered.connect(self._refresh_absorbance_spectrum)
        self.background_removal_link.toggled.connect(self._update_image_processing_settings)
        self.background_smoothing_sigma_spin.valueChanged.connect(self._update_image_processing_settings)
        self.background_smoothing_binning_combo.currentIndexChanged.connect(self._update_image_processing_settings)
        self.background_ignore_spot_button.toggled.connect(self._update_image_processing_settings)
        self.background_ignore_mask_button.toggled.connect(self._update_image_processing_settings)
        self.background_create_new_button.clicked.connect(self._create_new_background)
        self.background_load_from_file_button.clicked.connect(self._load_background_from_file)
        self.background_save_button.clicked.connect(self._save_background_to_file)
        self.background_profile_hold_button.toggled.connect(self._on_background_profile_toggled)
        self.background_profile_button.toggled.connect(self._on_background_profile_toggled)
        self.mask_mode_combo.currentIndexChanged.connect(self._update_spot_detection_settings)
        self.mask_mode_combo.currentIndexChanged.connect(self._update_mask_control_state)
        self.mask_profile_sigma_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_relative_threshold_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_local_sigma_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_local_z_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_pencil_check.toggled.connect(self._on_mask_pencil_toggled)
        self.mask_draw_mode_combo.currentIndexChanged.connect(self._sync_mask_draw_mode_buttons)
        self.mask_draw_add_button.clicked.connect(lambda: self._set_mask_draw_mode("add"))
        self.mask_draw_remove_button.clicked.connect(lambda: self._set_mask_draw_mode("erase"))
        
        # New mask system connections
        self.mask_create_new_button.clicked.connect(self._mask_controller.create_new_mask)
        self.mask_load_from_file_button.clicked.connect(self._mask_controller.load_mask_from_file)
        self.mask_save_button.clicked.connect(self._mask_controller.save_mask_to_file)
        self.histogram_mask_apply_button.clicked.connect(self._mask_controller.apply_histogram_mask)
        self.histogram_mask_reset_button.clicked.connect(self._mask_controller.reset_histogram_mask)
        self.relative_mask_apply_button.clicked.connect(self._mask_controller.apply_relative_mask)
        self.relative_mask_reset_button.clicked.connect(self._mask_controller.reset_relative_mask)
        self.relative_mask_show_button.toggled.connect(
            lambda checked: self._mask_controller.preview_toggled("relative", checked)
        )
        self.local_contrast_mask_apply_button.clicked.connect(self._mask_controller.apply_local_contrast_mask)
        self.local_contrast_mask_reset_button.clicked.connect(self._mask_controller.reset_local_contrast_mask)
        self.local_contrast_mask_show_button.toggled.connect(
            lambda checked: self._mask_controller.preview_toggled("local_contrast", checked)
        )
        self.morphology_mask_apply_button.clicked.connect(self._mask_controller.apply_morphology_mask)
        self.morphology_mask_reset_button.clicked.connect(self._mask_controller.reset_morphology_mask)
        self.morphology_mask_show_button.toggled.connect(
            lambda checked: self._mask_controller.preview_toggled("morphology", checked)
        )
        self.mask_morphology_erode_button.toggled.connect(lambda checked: self._mask_controller.set_morphology_operation("erode", checked))
        self.mask_morphology_dilate_button.toggled.connect(lambda checked: self._mask_controller.set_morphology_operation("dilate", checked))
        self.mask_morphology_open_button.toggled.connect(lambda checked: self._mask_controller.set_morphology_operation("open", checked))
        self.mask_morphology_close_button.toggled.connect(lambda checked: self._mask_controller.set_morphology_operation("close", checked))
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_relative_threshold_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_morphology_radius_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_relative_threshold_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_morphology_radius_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_draw_mode_combo.currentIndexChanged.connect(self._save_control_preferences)
        self.mask_brush_size_spin.valueChanged.connect(self._save_control_preferences)
        
        self.spot_diameter_spin.valueChanged.connect(self._on_spot_diameter_spin_changed)
        self.spot_diameter_spin.editingFinished.connect(self._commit_spot_geometry_edits)
        self.ring_inner_diameter_spin.valueChanged.connect(self._on_ring_inner_diameter_spin_changed)
        self.ring_inner_diameter_spin.editingFinished.connect(self._commit_spot_geometry_edits)
        self.ring_outer_diameter_spin.valueChanged.connect(self._on_ring_outer_diameter_spin_changed)
        self.ring_outer_diameter_spin.editingFinished.connect(self._commit_spot_geometry_edits)
        self.array_rows_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.array_cols_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.array_spacing_spin.valueChanged.connect(self._update_spot_detection_settings)
        self.ignore_marked_check.toggled.connect(self._update_spot_detection_settings)
        self.detect_spots_button.clicked.connect(self._detect_spots)
        self.reorder_spots_button.clicked.connect(self._reorder_spots_by_position)
        self.clear_spots_button.clicked.connect(self._clear_detected_spots)
        self.clear_spot_selection_button.clicked.connect(self._clear_spot_selection)
        self.rotate_action.toggled.connect(self._on_rotate_tool_toggled)
        self.crop_action.toggled.connect(self._on_crop_tool_toggled)
        self.measure_action.toggled.connect(self._on_measure_tool_toggled)
        self.flip_horizontal_action.toggled.connect(self._on_flip_horizontal_toggled)
        self.flip_vertical_action.toggled.connect(self._on_flip_vertical_toggled)
        self.spot_edit_action.toggled.connect(self._on_spot_edit_tool_toggled)
        self.spot_add_action.toggled.connect(self._on_spot_add_toggled)
        self.spot_move_action.toggled.connect(self._on_spot_move_toggled)
        self.spot_list_action.toggled.connect(self._spot_table_controller.on_toggled)
        self.spot_list_panel.visibilityChanged.connect(self._on_spot_list_panel_visibility_changed)
        self.spot_list_table.itemSelectionChanged.connect(self._spot_table_controller.on_selection_changed)
        self.spot_list_table.itemChanged.connect(self._spot_table_controller.on_item_changed)
        self.spot_list_table.cellDoubleClicked.connect(self._spot_table_controller.on_cell_double_clicked)
        self.spot_list_export_button.clicked.connect(self._spot_table_controller.export_csv)
        self.spot_list_import_button.clicked.connect(self._spot_table_controller.import_csv)
        self.remove_spots_action.triggered.connect(self._remove_selected_spots)
        self.group_spots_action.triggered.connect(self._group_selected_spots)
        self.show_spots_check.toggled.connect(self._on_show_spots_toggled)
        self.bottom_spot_labels_button.toggled.connect(self._on_show_spot_labels_toggled)
        self.spot_editor_labels_button.toggled.connect(self._on_spot_editor_show_labels_toggled)
        self.show_rings_check.toggled.connect(self._on_show_rings_toggled)
        self.show_reference_points_check.toggled.connect(self._on_show_reference_points_toggled)
        self.show_mask_check.toggled.connect(self._on_show_mask_toggled)
        self.show_highlight_check.toggled.connect(self._on_show_highlight_toggled)
        self.mask_color_button.clicked.connect(lambda: self._choose_overlay_color("mask"))
        self.spot_color_button.clicked.connect(lambda: self._choose_overlay_color("spots"))
        self.ring_color_button.clicked.connect(lambda: self._choose_overlay_color("ring"))
        self.highlight_color_button.clicked.connect(lambda: self._choose_overlay_color("highlight"))
        self.scale_bar_color_button.clicked.connect(lambda: self._choose_overlay_color("scale_bar"))
        self.mask_alpha_slider.valueChanged.connect(self._on_mask_alpha_changed)
        self.spot_alpha_slider.valueChanged.connect(self._on_spot_alpha_changed)
        self.ring_alpha_slider.valueChanged.connect(self._on_ring_alpha_changed)
        self.highlight_alpha_slider.valueChanged.connect(self._on_highlight_alpha_changed)
        self.reset_rotation_action.triggered.connect(self._reset_rotation)
        self.reset_crop_action.triggered.connect(self._reset_crop)
        self.measurement_apply_button.clicked.connect(self._apply_measurement_calibration)
        self.measurement_unit_button.clicked.connect(self._toggle_display_units)
        self.scale_bar_toggle_button.toggled.connect(self._on_scale_bar_toggled)
        self.undo_action.triggered.connect(self._undo)
        self.redo_action.triggered.connect(self._redo)
        self._configure_control_help()
        self._refresh_spot_list_action_icon()
        self._update_analysis_control_state()

    # ------------------------------------------------------------------
    # Spot list table and CSV helpers
    # ------------------------------------------------------------------

    def _on_spot_list_toggled(self, checked: bool) -> None:
        self.spot_list_panel.setVisible(checked)
        self._settings.setValue("layout/spot_list_visible", bool(checked))
        self._refresh_spot_list_action_icon()
        if checked:
            self._update_spot_list_table()

    def _on_cached_spots_only_toggled(self, checked: bool) -> None:
        self._cached_spots_only_visible = bool(checked)
        self._settings.setValue("layout/cached_spots_only_visible", bool(checked))
        self.spot_list_cached_button.setIcon(self._make_cached_spots_icon(bool(checked)))
        self._spot_table_controller.refresh_cached_row_styles()
        self._update_spot_overlays()
        self._refresh_visible_spectrum_from_cache()
        self._analysis_controller.preview_sensorgram_from_cache()

    def _on_spot_list_panel_visibility_changed(self, visible: bool) -> None:
        if self.spot_list_action.isChecked() == bool(visible):
            return
        self.spot_list_action.blockSignals(True)
        self.spot_list_action.setChecked(bool(visible))
        self.spot_list_action.blockSignals(False)
        self._settings.setValue("layout/spot_list_visible", bool(visible))
        self._refresh_spot_list_action_icon()
        if visible:
            self._update_spot_list_table()

    def _on_spot_list_selection_changed(self) -> None:
        if getattr(self, "_spot_list_selection_syncing", False):
            return
        selected_ids: set[int] = set()
        for row in range(self.spot_list_table.rowCount()):
            if not self.spot_list_table.isRowHidden(row) and self.spot_list_table.selectionModel().isRowSelected(row, self.spot_list_table.rootIndex()):
                item = self.spot_list_table.item(row, 0)
                if item is not None:
                    try:
                        selected_ids.add(int(item.text()))
                    except ValueError:
                        continue
        if selected_ids == self._selected_spot_ids:
            return
        self._selected_spot_ids = selected_ids
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_selection_dependent_plots(prompt_live_preview=True)

    def _sync_spot_list_table_selection(self) -> None:
        if not self.spot_list_table.isVisible():
            return
        selection_model = self.spot_list_table.selectionModel()
        if selection_model is None:
            return
        self._spot_list_selection_syncing = True
        try:
            selection_model.clearSelection()
            first_selected_item: QTableWidgetItem | None = None
            first_selected_row: int | None = None
            for row in range(self.spot_list_table.rowCount()):
                item = self.spot_list_table.item(row, 0)
                if item is None:
                    continue
                try:
                    spot_id = int(item.text())
                except ValueError:
                    continue
                if spot_id in self._selected_spot_ids:
                    selection_model.select(
                        self.spot_list_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    if first_selected_item is None:
                        first_selected_item = item
                        first_selected_row = row
            if first_selected_item is not None:
                self.spot_list_table.scrollToItem(first_selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.spot_list_table.setCurrentItem(first_selected_item)
                self._spot_list_range_anchor_row = first_selected_row
        finally:
            self._spot_list_selection_syncing = False
        self._update_selection_dependent_plots()

    def _select_spot_list_table_rows(self, rows: list[int]) -> None:
        selection_model = self.spot_list_table.selectionModel()
        if selection_model is None:
            return
        valid_rows = sorted({row for row in rows if 0 <= row < self.spot_list_table.rowCount()})
        if not valid_rows:
            return
        self._append_workflow_log(f"Selection | table rows {valid_rows}", level="debug")
        self._spot_list_selection_syncing = True
        selected_ids: set[int] = set()
        first_selected_item: QTableWidgetItem | None = None
        try:
            selection_model.clearSelection()
            for row in valid_rows:
                item = self.spot_list_table.item(row, 0)
                if item is None:
                    continue
                try:
                    spot_id = int(item.text())
                except ValueError:
                    continue
                selection_model.select(
                    self.spot_list_table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
                selected_ids.add(spot_id)
                if first_selected_item is None:
                    first_selected_item = item
            if first_selected_item is not None:
                self.spot_list_table.scrollToItem(first_selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.spot_list_table.setCurrentItem(first_selected_item)
        finally:
            self._spot_list_selection_syncing = False
        self._selected_spot_ids = selected_ids
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_selection_dependent_plots(prompt_live_preview=True)

    def _spot_list_spot_id_for_row(self, row: int) -> int | None:
        if row < 0 or row >= self.spot_list_table.rowCount():
            return None
        item = self.spot_list_table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _spot_list_selected_rows(self) -> list[int]:
        selection_model = self.spot_list_table.selectionModel()
        if selection_model is None:
            return []
        rows: list[int] = []
        for row in range(self.spot_list_table.rowCount()):
            if selection_model.isRowSelected(row, self.spot_list_table.rootIndex()):
                rows.append(row)
        return rows

    def _spot_list_selected_spot_ids(self) -> list[int]:
        spot_ids: list[int] = []
        for row in self._spot_list_selected_rows():
            spot_id = self._spot_list_spot_id_for_row(row)
            if spot_id is not None:
                spot_ids.append(spot_id)
        return spot_ids

    def _spot_list_spot_index(self, spot_id: int) -> int | None:
        for index, spot in enumerate(self._state.detected_spots):
            if spot.spot_id == spot_id:
                return index
        return None

    def _refresh_spot_list_action_icon(self) -> None:
        self.spot_list_action.setIcon(self._make_spot_list_icon(self.spot_list_action.isChecked()))
        if hasattr(self, "analysis_spot_table_button"):
            self.analysis_spot_table_button.setPixmap(
                self._make_spot_list_icon(self.spot_list_action.isChecked()).pixmap(
                    APP_THEME.compact_icon_inner,
                    APP_THEME.compact_icon_inner,
                )
            )

    def _refresh_spot_list_table_headers(self) -> None:
        spot_table_headers(self.spot_list_table)

    def _on_spot_list_item_changed(self, item: QTableWidgetItem) -> None:
        if self._spot_list_table_updating:
            return
        spot_id = self._spot_list_spot_id_for_row(item.row())
        if spot_id is None:
            return
        if item.column() == 1:
            self._rename_spot_group_from_table(spot_id, item.text().strip())
        elif item.column() in {2, 3, 4}:
            self._edit_spot_diameter_cells_from_table(spot_id, item.row())

    def _on_spot_list_cell_double_clicked(self, row: int, column: int) -> None:
        spot_id = self._spot_list_spot_id_for_row(row)
        if spot_id is None:
            return
        if column == 2:
            self._edit_spot_color_from_table(spot_id)
        elif column == 3:
            self._edit_ring_color_from_table()
        elif column == 4:
            self._edit_spot_geometry_from_table(spot_id)
        elif column == 5:
            self._edit_ring_geometry_from_table(spot_id)

    def _rename_spot_group_from_table(self, spot_id: int, new_name: str) -> None:
        current_group = self._group_for_spot(spot_id)
        if not new_name:
            self.status_label.setText("Group name cannot be empty.")
            self._update_spot_list_table()
            return

        if current_group is not None:
            if current_group.name == new_name:
                return
            self._push_undo_point("Rename group")
            current_group.name = new_name
        else:
            selected_ids = {spot_id}
            self._push_undo_point("Group spots")
            for group in self._state.spot_groups:
                group.spot_ids = [spot for spot in group.spot_ids if spot not in selected_ids]
            self._state.spot_groups = [group for group in self._state.spot_groups if group.spot_ids]
            self._state.spot_groups.append(
                SpotGroup(
                    group_id=f"group_{len(self._state.spot_groups) + 1}",
                    name=new_name,
                    spot_color_hex=self._spot_visual_color.name(),
                    ring_color_hex=self._ring_visual_color.name(),
                    spot_ids=sorted(selected_ids),
                )
            )
        self._update_spot_overlays()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()

    def _copy_spot_properties_from_table(self) -> None:
        selected_ids = self._spot_list_selected_spot_ids()
        if not selected_ids:
            self.status_label.setText("Select a spot row first to copy its properties.")
            return
        spot_id = selected_ids[0]
        spot = self._spot_by_id(spot_id)
        if spot is None:
            return
        group = self._group_for_spot(spot_id)
        self._spot_clipboard = {
            "group_name": group.name if group is not None else None,
            "group_spot_color": group.spot_color_hex if group is not None else None,
            "group_ring_color": group.ring_color_hex if group is not None else None,
            "spot_color": self._spot_visual_color.name(),
            "ring_color": self._ring_visual_color.name(),
        }
        self.status_label.setText(f"Copied spot properties from spot {spot_id}.")

    def _paste_spot_properties_from_table(self) -> None:
        if not self._spot_clipboard:
            self.status_label.setText("Nothing to paste yet. Copy a spot first.")
            return
        selected_ids = self._spot_list_selected_spot_ids()
        if not selected_ids:
            self.status_label.setText("Select one or more spot rows to paste properties.")
            return
        self._push_undo_point("Paste spot properties")
        group_name = self._spot_clipboard.get("group_name")
        group_spot_color = self._spot_clipboard.get("group_spot_color")
        group_ring_color = self._spot_clipboard.get("group_ring_color")
        spot_color = self._spot_clipboard.get("spot_color")
        ring_color = self._spot_clipboard.get("ring_color")
        if isinstance(spot_color, str):
            self._spot_visual_color = QColor(spot_color)
        if isinstance(ring_color, str):
            self._ring_visual_color = QColor(ring_color)
        if isinstance(group_name, str) and group_name:
            target_group = next((group for group in self._state.spot_groups if group.name == group_name), None)
            if target_group is None:
                target_group = SpotGroup(
                    group_id=f"group_{len(self._state.spot_groups) + 1}",
                    name=group_name,
                    spot_color_hex=str(group_spot_color) if isinstance(group_spot_color, str) else self._spot_visual_color.name(),
                    ring_color_hex=str(group_ring_color) if isinstance(group_ring_color, str) else self._ring_visual_color.name(),
                    spot_ids=[],
                )
                self._state.spot_groups.append(target_group)
            else:
                if isinstance(group_spot_color, str):
                    target_group.spot_color_hex = group_spot_color
                if isinstance(group_ring_color, str):
                    target_group.ring_color_hex = group_ring_color
            target_group.spot_ids = sorted(set(target_group.spot_ids).union(selected_ids))
            for other_group in self._state.spot_groups:
                if other_group is target_group:
                    continue
                other_group.spot_ids = [spot_id for spot_id in other_group.spot_ids if spot_id not in selected_ids]
            self._state.spot_groups = [group for group in self._state.spot_groups if group.spot_ids]
        else:
            for group in self._state.spot_groups:
                group.spot_ids = [spot_id for spot_id in group.spot_ids if spot_id not in selected_ids]
            self._state.spot_groups = [group for group in self._state.spot_groups if group.spot_ids]
        self._update_color_button_styles()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()

    def _move_selected_spots_in_table(self, direction: int) -> None:
        selected_rows = self._spot_list_selected_rows()
        if not selected_rows or direction == 0:
            return
        spot_id = self._spot_list_spot_id_for_row(selected_rows[0])
        if spot_id is None:
            return
        index = self._spot_list_spot_index(spot_id)
        if index is None:
            return
        if direction < 0 and index == 0:
            return
        if direction > 0 and index >= len(self._state.detected_spots) - 1:
            return
        reordered = list(self._state.detected_spots)
        swap_index = index - 1 if direction < 0 else index + 1
        reordered[index], reordered[swap_index] = reordered[swap_index], reordered[index]
        old_ids = [spot.spot_id for spot in reordered]
        id_map = {old_id: new_id for new_id, old_id in enumerate(old_ids, start=1)}
        for new_id, spot in enumerate(reordered, start=1):
            spot.spot_id = new_id
        for group in self._state.spot_groups:
            group.spot_ids = [id_map.get(spot_id, spot_id) for spot_id in group.spot_ids]
            group.spot_ids = sorted(dict.fromkeys(group.spot_ids))
        self._state.detected_spots = reordered
        self._selected_spot_ids = {id_map.get(spot_id, spot_id) for spot_id in self._selected_spot_ids}
        self._update_spot_overlays()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()

    def _edit_spot_color_from_table(self, spot_id: int) -> None:
        spot = self._spot_by_id(spot_id)
        if spot is None:
            return
        initial = QColor(spot.spot_color_hex) if spot.spot_color_hex else QColor(self._spot_visual_color)
        color = QColorDialog.getColor(initial, self, "Choose spot color")
        if not color.isValid():
            return
        self._push_undo_point("Edit spot color")
        spot.spot_color_hex = color.name()
        self._update_color_button_styles()
        self._update_spot_overlays()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()

    def _edit_ring_color_from_table(self) -> None:
        selected_ids = self._spot_list_selected_spot_ids()
        spot = self._spot_by_id(selected_ids[0]) if selected_ids else None
        initial = QColor(spot.ring_color_hex) if spot is not None and spot.ring_color_hex else QColor(self._ring_visual_color)
        color = QColorDialog.getColor(initial, self, "Choose reference-ring color")
        if not color.isValid():
            return
        self._push_undo_point("Edit ring color")
        if spot is not None:
            spot.ring_color_hex = color.name()
        else:
            self._ring_visual_color = color
        self._update_color_button_styles()
        self._update_spot_overlays()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()

    def _edit_spot_geometry_from_table(self, spot_id: int) -> None:
        spot = self._spot_by_id(spot_id)
        if spot is None:
            return
        value, ok = QInputDialog.getDouble(
            self,
            "Spot diameter",
            f"Spot diameter for spot {spot_id}",
            float(spot.spot_diameter_px if spot.spot_diameter_px is not None else self.spot_diameter_spin.value()),
            2.0,
            1000.0,
            2,
        )
        if not ok:
            return
        self._push_undo_point("Edit spot diameter")
        spot.spot_diameter_px = float(value)
        self._save_processing_state_for_dataset()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_spot_list_table()

    def _edit_ring_geometry_from_table(self, spot_id: int) -> None:
        spot = self._spot_by_id(spot_id)
        if spot is None:
            return
        inner_default = float(spot.ring_inner_diameter_px if spot.ring_inner_diameter_px is not None else self.ring_inner_diameter_spin.value())
        outer_default = float(spot.ring_outer_diameter_px if spot.ring_outer_diameter_px is not None else self.ring_outer_diameter_spin.value())
        inner_value, ok = QInputDialog.getDouble(self, "Reference ring", f"Inner diameter for spot {spot_id}", inner_default, 0.0, 1000.0, 2)
        if not ok:
            return
        outer_value, ok = QInputDialog.getDouble(self, "Reference ring", f"Outer diameter for spot {spot_id}", outer_default, inner_value, 1000.0, 2)
        if not ok:
            return
        self._push_undo_point("Edit ring diameter")
        spot.ring_inner_diameter_px = float(inner_value)
        spot.ring_outer_diameter_px = float(max(outer_value, inner_value))
        self._save_processing_state_for_dataset()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_spot_list_table()

    def _edit_spot_diameter_cells_from_table(self, spot_id: int, row: int) -> None:
        spot = self._spot_by_id(spot_id)
        if spot is None:
            return
        try:
            spot_diameter_text = self.spot_list_table.item(row, 2).text() if self.spot_list_table.item(row, 2) is not None else ""
            ring_inner_text = self.spot_list_table.item(row, 3).text() if self.spot_list_table.item(row, 3) is not None else ""
            ring_outer_text = self.spot_list_table.item(row, 4).text() if self.spot_list_table.item(row, 4) is not None else ""
            spot_diameter = float(spot_diameter_text)
            ring_inner = float(ring_inner_text)
            ring_outer = float(ring_outer_text)
        except ValueError:
            self.status_label.setText("Spot diameter cells must contain numbers.")
            self._update_spot_list_table()
            return
        self._push_undo_point("Edit spot geometry")
        spot.spot_diameter_px = spot_diameter
        spot.ring_inner_diameter_px = ring_inner
        spot.ring_outer_diameter_px = max(ring_outer, ring_inner)
        if self.spot_geometry_scope_button.isChecked():
            self._state.spot_detection.spot_radius_px = max(spot_diameter / 2.0, 1.0)
        if self.ring_geometry_scope_button.isChecked():
            self._state.spot_detection.ring_inner_radius_px = max(ring_inner / 2.0, 0.0)
            self._state.spot_detection.ring_outer_radius_px = max(ring_outer / 2.0, ring_inner / 2.0)
        self._save_processing_state_for_dataset()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_spot_list_table()

    def _export_spot_list_csv(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(self, "Save spot list CSV", "", "CSV Files (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        rows = sorted(self._state.detected_spots, key=lambda spot: spot.spot_id)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "spot_id",
                "group_name",
                "group_spot_color",
                "group_ring_color",
                "spot_color",
                "ring_color",
                "center_x",
                "center_y",
                "spot_order",
                "spot_diameter_px",
                "ring_inner_diameter_px",
                "ring_outer_diameter_px",
            ])
            for order, spot in enumerate(rows):
                group = self._group_for_spot(spot.spot_id)
                writer.writerow([
                    spot.spot_id,
                    group.name if group is not None else "",
                    group.spot_color_hex if group is not None else "",
                    group.ring_color_hex if group is not None else "",
                    spot.spot_color_hex or "",
                    spot.ring_color_hex or "",
                    self._spot_visual_color.name(),
                    self._ring_visual_color.name(),
                    spot.center_x,
                    spot.center_y,
                    order,
                    "" if spot.spot_diameter_px is None else spot.spot_diameter_px,
                    "" if spot.ring_inner_diameter_px is None else spot.ring_inner_diameter_px,
                    "" if spot.ring_outer_diameter_px is None else spot.ring_outer_diameter_px,
                ])
        self.status_label.setText(f"Saved spot list to {path.name}.")

    def _import_spot_list_csv(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Load spot list CSV", "", "CSV Files (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        if not rows:
            self.status_label.setText("CSV file is empty.")
            return
        self._push_undo_point("Import spot list CSV")
        groups_by_name = {group.name: group for group in self._state.spot_groups}
        ordered_rows = sorted(rows, key=lambda row: int(row.get("spot_order", row.get("spot_id", 0)) or 0))
        for row in ordered_rows:
            try:
                spot_id = int(row.get("spot_id", ""))
            except ValueError:
                continue
            spot = self._spot_by_id(spot_id)
            if spot is None:
                continue
            group_name = str(row.get("group_name", "")).strip()
            group_color = str(row.get("group_color", "")).strip() or "#f59e0b"
            group_ring_color = str(row.get("group_ring_color", "")).strip() or self._ring_visual_color.name()
            if group_name:
                group = groups_by_name.get(group_name)
                if group is None:
                    group = SpotGroup(
                        group_id=f"group_{len(groups_by_name) + 1}",
                        name=group_name,
                        spot_color_hex=group_color,
                        ring_color_hex=group_ring_color,
                        spot_ids=[],
                    )
                    self._state.spot_groups.append(group)
                    groups_by_name[group_name] = group
                if spot_id not in group.spot_ids:
                    group.spot_ids.append(spot_id)
                group.spot_color_hex = group_color
                group.ring_color_hex = group_ring_color
            spot.spot_diameter_px = None if row.get("spot_diameter_px", "") == "" else float(row["spot_diameter_px"])
            spot.ring_inner_diameter_px = None if row.get("ring_inner_diameter_px", "") == "" else float(row["ring_inner_diameter_px"])
            spot.ring_outer_diameter_px = None if row.get("ring_outer_diameter_px", "") == "" else float(row["ring_outer_diameter_px"])
            if row.get("spot_color", ""):
                self._spot_visual_color = QColor(str(row["spot_color"]))
            if row.get("ring_color", ""):
                self._ring_visual_color = QColor(str(row["ring_color"]))
        self._update_color_button_styles()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()
        self.status_label.setText(f"Loaded spot list from {path.name}.")

    def _spot_list_table_legacy_copy(self) -> None:
        self._spot_list_table_updating = True
        self.spot_list_table.blockSignals(True)
        self.spot_list_table.setRowCount(0)
        spots = sorted(self._state.detected_spots, key=lambda spot: spot.spot_id)
        if not spots:
            self.spot_list_table.blockSignals(False)
            self._spot_list_table_updating = False
            self._sync_spot_list_table_selection()
            return
        for spot in spots:
            row = self.spot_list_table.rowCount()
            self.spot_list_table.insertRow(row)
            self.spot_list_table.setRowHeight(row, 18)
            id_item = QTableWidgetItem(str(spot.spot_id))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.spot_list_table.setItem(row, 0, id_item)
            group = self._group_for_spot(spot.spot_id)
            group_name = group.name if group is not None else "—"
            group_item = QTableWidgetItem(group_name)
            group_item.setFlags(group_item.flags() | Qt.ItemFlag.ItemIsEditable)
            if group is not None:
                group_color = QColor(group.spot_color_hex)
                if group_color.isValid():
                    group_item.setForeground(group_color)
                    group_item.setToolTip(f"{group.name} ({group.spot_color_hex})")
            self.spot_list_table.setItem(row, 1, group_item)
            spot_color_label = QLabel()
            spot_color_label.setFixedSize(16, 16)
            spot_color = getattr(self._state, "overlay_colors", {}).get("spots", QColor("#f8fafc")) if hasattr(self._state, "overlay_colors") else QColor("#f8fafc")
            spot_color_label.setStyleSheet(f"background-color: {spot_color.name()}; border: 1px solid #2d2d2d;")
            self.spot_list_table.setCellWidget(row, 2, spot_color_label)
            ring_color_label = QLabel()
            ring_color_label.setFixedSize(18, 18)
            ring_color = getattr(self._state, "overlay_colors", {}).get("ring", QColor("#38bdf8")) if hasattr(self._state, "overlay_colors") else QColor("#38bdf8")
            ring_color_label.setStyleSheet(f"background-color: {ring_color.name()}; border: 1px solid #2d2d2d;")
            self.spot_list_table.setCellWidget(row, 3, ring_color_label)
            if self._state.preprocessing.display_units == "um" and self._can_display_micrometers():
                scale = self._microns_per_pixel_scalar()
                pos_text = f"x: {spot.center_x * scale:.1f} y: {spot.center_y * scale:.1f}"
            else:
                pos_text = f"x: {spot.center_x:.1f} y: {spot.center_y:.1f}"
            pos_item = QTableWidgetItem(pos_text)
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.spot_list_table.setItem(row, 4, pos_item)
        self.spot_list_table.resizeColumnsToContents()
        self.spot_list_table.setColumnWidth(0, 38)
        self.spot_list_table.setColumnWidth(1, 88)
        self.spot_list_table.setColumnWidth(2, 22)
        self.spot_list_table.setColumnWidth(3, 22)
        self.spot_list_table.setColumnWidth(4, 126)
        self.spot_list_table.horizontalHeader().setStretchLastSection(True)
        self._sync_spot_list_table_selection()

    def _update_spot_list_table(self) -> None:
        if self._spot_list_refresh_timer.isActive():
            self._spot_list_refresh_timer.stop()
        self._spot_list_refresh_timer.start()

    def _report_startup_progress(self, percent: int, message: str) -> None:
        callback = self._startup_progress_callback
        if callback is None:
            return
        try:
            callback(int(np.clip(percent, 0, 100)), str(message))
        except Exception:
            pass

    def run_startup_restore_flow(
        self,
        *,
        show_window: bool = True,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> None:
        self._startup_restore_in_progress = True
        self._startup_progress_callback = progress_callback
        self._append_workflow_log("Startup | restore begin", level="debug")
        self._report_startup_progress(8, "Restoring window layout...")
        self._restore_saved_window_layout()
        self._report_startup_progress(18, "Checking previous session...")
        self._dataset_controller.run_startup_restore_flow()
        if self._state.dataset is None:
            self._append_workflow_log("Startup | no dataset restored", level="warning")
            self._report_startup_progress(100, "Ready.")
            if self._fast_startup:
                self._set_status_text("Fast startup enabled. Load a dataset when ready.")
            if show_window:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                self._sync_panel_visibility_after_show()
            self._startup_restore_in_progress = False
            self._startup_ready = True
            self._startup_progress_callback = None
            return
        self._report_startup_progress(58, "Loading analysis cache...")
        self._append_workflow_log("Startup | analysis cache restored", level="debug")
        self._sync_spot_detection_controls()
        self._restore_control_preferences()
        self._report_startup_progress(76, "Preparing the first image...")
        self._analysis_enabled = False
        self._analysis_live_preview_enabled = False
        self._set_section_applied(self.analysis_section, False)
        self._settings.setValue("analysis_section_applied", False)
        self._settings.setValue("analysis/live_preview", False)
        self._refresh_image()
        self._report_startup_progress(92, "Finalizing workspace...")
        if show_window:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self._sync_panel_visibility_after_show()
        self._startup_restore_in_progress = False
        self._startup_ready = True
        self._report_startup_progress(100, "Workspace ready.")
        self._append_workflow_log("Startup | workspace ready", level="success")
        self._startup_progress_callback = None
        self._end_busy("Loaded dataset. Showing the reference image.")

    def _restore_saved_window_layout(self) -> None:
        if self._window_geometry_restored and self._layout_preferences_ready:
            return
        self._restore_window_geometry()
        self._restore_layout_preferences()

    def _configure_slider(self, slider: QSlider, value_count: int) -> None:
        slider.setEnabled(value_count > 0)
        slider.setMinimum(0)
        slider.setMaximum(max(value_count - 1, 0))
        slider.setSingleStep(1)
        slider.setPageStep(1)

    def _configure_navigation_inputs(self) -> None:
        frame_enabled = bool(self._frame_values)
        self.frame_spin.setEnabled(frame_enabled)
        if frame_enabled:
            self.frame_spin.setRange(min(self._frame_values), max(self._frame_values))

        wavelength_enabled = bool(self._wavelength_values)
        self.wavelength_spin.setEnabled(wavelength_enabled)
        if wavelength_enabled:
            self.wavelength_spin.setRange(min(self._wavelength_values), max(self._wavelength_values))
            decimals = max((self._decimal_places(value) for value in self._wavelength_values), default=0)
            self.wavelength_spin.setDecimals(min(max(decimals, 0), 4))
        self._sync_analysis_frame_range_controls()
        self._sync_analysis_plots()

    def _analysis_metric_key(self) -> str:
        return str(self.analysis_metric_combo.currentData() or "centroid")

    def _analysis_metric_label(self) -> str:
        return str(self.analysis_metric_combo.currentText() or "Metric")

    def _analysis_metric_axis_label(self) -> str:
        metric_key = self._analysis_metric_key()
        if metric_key in {"maximum", "centroid"}:
            return "Wavelength (nm)"
        return "Metric"

    def _analysis_poly_order(self) -> int:
        return int(self.analysis_poly_order_spin.value())

    def _current_analysis_frame_range(self) -> tuple[int, int] | None:
        if not self._frame_values:
            return None
        start = int(self.analysis_start_frame_spin.value())
        end = int(self.analysis_end_frame_spin.value())
        if start > end:
            start, end = end, start
        return start, end

    def _sync_analysis_frame_range_controls(self) -> None:
        frame_enabled = bool(self._frame_values)
        self.analysis_start_frame_spin.setEnabled(frame_enabled)
        self.analysis_end_frame_spin.setEnabled(frame_enabled)
        if not frame_enabled:
            return

        frame_min = int(min(self._frame_values))
        frame_max = int(max(self._frame_values))
        stored_start = self._settings_int("analysis/frame_start", frame_min, minimum=frame_min, maximum=frame_max)
        stored_end = self._settings_int("analysis/frame_end", frame_max, minimum=frame_min, maximum=frame_max)
        if stored_start > stored_end:
            stored_start, stored_end = stored_end, stored_start

        self.analysis_start_frame_spin.blockSignals(True)
        self.analysis_end_frame_spin.blockSignals(True)
        self.analysis_start_frame_spin.setRange(frame_min, frame_max)
        self.analysis_end_frame_spin.setRange(frame_min, frame_max)
        self.analysis_start_frame_spin.setValue(stored_start)
        self.analysis_end_frame_spin.setValue(stored_end)
        self.analysis_start_frame_spin.blockSignals(False)
        self.analysis_end_frame_spin.blockSignals(False)

    def _set_sensorgram_summary_text(self, text: str) -> None:
        self.sensorgram_summary_label.setText(text)

    def _update_sensorgram_plot_labels(self) -> None:
        self.sensorgram_plot.setLabel("left", self._analysis_metric_axis_label())
        self.sensorgram_plot.setLabel("bottom", "Frame")

    def _analysis_plot_frame_range(self) -> tuple[int, int] | None:
        if not self._frame_values:
            return None
        return int(min(self._frame_values)), int(max(self._frame_values))

    def _analysis_plot_wavelength_range(self) -> tuple[float, float] | None:
        if not self._wavelength_values:
            return None
        return float(min(self._wavelength_values)), float(max(self._wavelength_values))

    def _sync_analysis_plot_axes(self) -> None:
        frame_range = self._analysis_plot_frame_range()
        wavelength_range = self._analysis_plot_wavelength_range()
        if wavelength_range is not None:
            self.spectrum_plot.setLimits(xMin=wavelength_range[0], xMax=wavelength_range[1])
            self.spectrum_plot.setXRange(wavelength_range[0], wavelength_range[1], padding=0.03)
        if frame_range is not None:
            self.sensorgram_plot.setLimits(xMin=frame_range[0], xMax=frame_range[1])
            self.sensorgram_plot.setXRange(float(frame_range[0]), float(frame_range[1]), padding=0.03)

    def _sync_analysis_plot_cursors(self) -> None:
        has_dataset = bool(self._frame_values) and bool(self._wavelength_values)
        if not has_dataset:
            self.spectrum_cursor_line.hide()
            self.sensorgram_cursor_line.hide()
            return
        self.spectrum_cursor_line.show()
        self.sensorgram_cursor_line.show()
        current_frame = self._current_frame()
        current_wavelength = self._current_wavelength()
        if current_frame is None:
            current_frame = int(self._frame_values[0])
        if current_wavelength is None:
            current_wavelength = float(self._wavelength_values[0])
        cursor_color = self._chromatic_wavelength_color(float(current_wavelength))
        self.spectrum_cursor_line.blockSignals(True)
        self.sensorgram_cursor_line.blockSignals(True)
        self.spectrum_cursor_line.setValue(float(current_wavelength))
        self.sensorgram_cursor_line.setValue(float(current_frame))
        self.spectrum_cursor_line.setPen(pg.mkPen(cursor_color, width=2.2))
        self.spectrum_cursor_line.blockSignals(False)
        self.sensorgram_cursor_line.blockSignals(False)

    def _sync_analysis_plots(self) -> None:
        self._sync_analysis_plot_axes()
        self._sync_analysis_plot_cursors()

    def _on_spectrum_cursor_moved(self) -> None:
        if not self._wavelength_values:
            return
        wavelength = float(self.spectrum_cursor_line.value())
        nearest_index = min(
            range(len(self._wavelength_values)),
            key=lambda idx: abs(float(self._wavelength_values[idx]) - wavelength),
        )
        current_frame = self._current_frame()
        if current_frame is None and self._frame_values:
            current_frame = int(self._frame_values[self.frame_slider.value()])
        if current_frame is None:
            return
        target_wavelength = float(self._wavelength_values[nearest_index])
        self._set_current_frame_and_wavelength(int(current_frame), target_wavelength)

    def _on_sensorgram_cursor_moved(self) -> None:
        if not self._frame_values:
            return
        frame = float(self.sensorgram_cursor_line.value())
        nearest_index = min(
            range(len(self._frame_values)),
            key=lambda idx: abs(float(self._frame_values[idx]) - frame),
        )
        current_wavelength = self._current_wavelength()
        if current_wavelength is None and self._wavelength_values:
            current_wavelength = float(self._wavelength_values[self.wavelength_slider.value()])
        if current_wavelength is None:
            return
        target_frame = int(self._frame_values[nearest_index])
        self._set_current_frame_and_wavelength(target_frame, float(current_wavelength))

    def _clear_sensorgram(self, summary_text: str) -> None:
        self._plot_manager.clear_sensorgram(summary_text)

    def _set_sensorgram_series(
        self,
        frame_indices,
        metric_values,
        *,
        summary_text: str | None = None,
    ) -> None:
        self._plot_manager.set_sensorgram_series(frame_indices, metric_values, summary_text=summary_text)

    def _update_sensorgram_current_point(self) -> None:
        self._plot_manager.update_sensorgram_current_point()

    def _mark_sensorgram_stale(self, reason: str | None = None) -> None:
        if self._analysis_live_preview_enabled and self._analysis_enabled and self._state.dataset is not None:
            if reason is not None:
                self._set_sensorgram_summary_text(reason)
            self._schedule_sensorgram_refresh()
            return
        self._analysis_controller.mark_stale(reason)

    def _current_frame(self) -> int | None:
        if not self._frame_values:
            return None
        return self._frame_values[self.frame_slider.value()]

    def _current_wavelength(self) -> float | None:
        if not self._wavelength_values:
            return None
        return self._wavelength_values[self.wavelength_slider.value()]

    @staticmethod
    def _format_dataset_bytes(size_bytes: int) -> str:
        value = float(max(size_bytes, 0))
        units = ["B", "KB", "MB", "GB", "TB"]
        unit = 0
        while value >= 1024.0 and unit < len(units) - 1:
            value /= 1024.0
            unit += 1
        if unit == 0:
            return f"{int(value)} {units[unit]}"
        return f"{value:.1f} {units[unit]}"

    def _dataset_summary_text(self, dataset=None) -> str:
        dataset = self._state.dataset if dataset is None else dataset
        if dataset is None:
            return "Load an image folder to begin."
        records = list(getattr(dataset, "records", []))
        frame_values = list(getattr(dataset, "frame_indices", []))
        wavelength_values = list(getattr(dataset, "wavelengths_nm", []))
        first_record = records[0] if records else None
        resolution_text = "Unknown"
        if first_record is not None:
            try:
                height, width = load_image_shape(str(first_record.path))
                resolution_text = f"{width} x {height} px"
            except Exception:
                resolution_text = "Unknown"
        size_bytes = 0
        for record in records:
            try:
                size_bytes += int(record.path.stat().st_size)
            except OSError:
                continue
        if records:
            try:
                dataset_date = datetime.fromtimestamp(
                    min(record.path.stat().st_mtime for record in records)
                ).strftime("%Y-%m-%d")
            except Exception:
                dataset_date = "Unknown"
        else:
            dataset_date = "Unknown"
        frame_text = f"{len(frame_values)}" if frame_values else "0"
        wavelength_text = f"{len(wavelength_values)}" if wavelength_values else "0"
        ome_zarr = dataset_is_ome_zarr(dataset)
        stack_label = dataset.format_label if dataset is not None else "ImageStack"
        return (
            f"{stack_label} loaded.\n"
            f"Images: {len(records)}\n"
            f"Frames: {frame_text} | Wavelengths: {wavelength_text}\n"
            f"Dataset size: {self._format_dataset_bytes(size_bytes)}\n"
            f"Resolution: {resolution_text}\n"
            f"Dataset's date: {dataset_date}"
        )

    def _refresh_image(self) -> None:
        frame = self._current_frame()
        wavelength = self._current_wavelength()
        self._append_workflow_log_throttled(
            "image_refresh",
            f"Image refresh | frame {frame if frame is not None else '-'} | wavelength {wavelength if wavelength is not None else '-'}",
            level="debug",
        )
        self._image_controller.refresh_image()

    def _start_pending_image_refresh(self) -> None:
        self._image_controller.start_pending_image_refresh()

    def _on_image_refresh_ready(
        self,
        signature: tuple[object, ...],
        cache_key: tuple[object, ...],
        record_path: Path,
        image_key: tuple[int, float],
        frame: int,
        wavelength: float,
        record_name: str,
        processed: np.ndarray,
    ) -> None:
        self._image_controller.on_image_refresh_ready(
            signature,
            cache_key,
            record_path,
            image_key,
            frame,
            wavelength,
            record_name,
            processed,
        )

    def _on_image_refresh_failed(self, message: str) -> None:
        self._image_controller.on_image_refresh_failed(message)

    def _apply_loaded_image(
        self,
        processed: np.ndarray,
        record_path: Path,
        image_key: tuple[int, float],
        frame: int,
        wavelength: float,
        record_name: str,
    ) -> None:
        self._image_controller.apply_loaded_image(processed, record_path, image_key, frame, wavelength, record_name)

    def _update_image_name_overlay(self, record_name: str | None) -> None:
        self.image_name_label.hide()

    def _initial_reference_indices(self) -> tuple[int, int]:
        if not self._frame_values or not self._wavelength_values:
            return 0, 0
        if str(self._state.preprocessing.reference_mode or "auto") == "manual":
            ref_frame = int(self._state.preprocessing.reference_frame_index)
            ref_wavelength = self._state.preprocessing.reference_wavelength_nm
            frame_index = self._frame_values.index(ref_frame) if ref_frame in self._frame_values else 0
            wavelength_index = 0
            if ref_wavelength is not None:
                wavelength_index = min(
                    range(len(self._wavelength_values)),
                    key=lambda idx: abs(self._wavelength_values[idx] - float(ref_wavelength)),
                )
            return frame_index, wavelength_index
        auto_key = self._auto_reference_image_key_for_frame(self._frame_values[0])
        if auto_key is None:
            return 0, 0
        auto_frame, auto_wavelength = auto_key
        frame_index = self._frame_values.index(int(auto_frame)) if int(auto_frame) in self._frame_values else 0
        wavelength_index = min(
            range(len(self._wavelength_values)),
            key=lambda idx: abs(self._wavelength_values[idx] - float(auto_wavelength)),
        )
        return frame_index, wavelength_index

    def _spot_signature(self, spots: list[DetectedSpot] | None = None) -> tuple[object, ...]:
        spot_list = self._state.detected_spots if spots is None else spots
        return tuple(
            (spot.spot_id, round(float(spot.center_x), 3), round(float(spot.center_y), 3), round(float(spot.radius_px), 3))
            for spot in spot_list
        )

    def _preprocessing_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...]:
        crop = self._state.preprocessing.crop
        chromatic_signature = self._chromatic_signature_for_image_key(image_key)
        spot_signature: tuple[object, ...] | None = None
        if (
            self._state.preprocessing.flatten_background_enabled
            and self._state.preprocessing.flatten_background_exclude_spots
        ):
            spot_signature = self._spot_signature(self._spots_for_preprocessing(image_key))
        mask_signature: tuple[object, ...] | None = None
        if (
            self._state.preprocessing.flatten_background_enabled
            and self._state.preprocessing.flatten_background_exclude_mask
        ):
            mask_signature = self._mask_preview_signature(image_key=image_key)
        elif self._mask_section_applied():
            mask_signature = self._mask_preview_signature(image_key=image_key)
        return (
            bool(getattr(self._state.preprocessing, "image_tools_enabled", True)),
            round(float(self._state.preprocessing.rotation_angle_deg), 6),
            bool(self._state.preprocessing.flip_horizontal),
            bool(self._state.preprocessing.flip_vertical),
            chromatic_signature,
            bool(crop.enabled),
            int(crop.x),
            int(crop.y),
            int(crop.width),
            int(crop.height),
            bool(self._state.preprocessing.flatten_background_enabled),
            round(float(self._state.preprocessing.flatten_background_sigma_px), 3),
            int(max(getattr(self._state.preprocessing, "flatten_background_binning", 2), 1)),
            bool(self._state.preprocessing.flatten_background_exclude_spots),
            bool(self._state.preprocessing.flatten_background_exclude_mask),
            bool(self._state.preprocessing.chromatic_correction_enabled),
            spot_signature,
            mask_signature,
        )

    def _processed_image_cache_key(self, record_path: Path, image_key: tuple[int, float]) -> tuple[object, ...]:
        return (
            str(record_path),
            image_key,
            self._preprocessing_signature(image_key),
        )

    def _get_processed_image_from_cache(self, cache_key: tuple[object, ...]) -> np.ndarray | None:
        cached = self._processed_image_cache.get(cache_key)
        if cached is None:
            return None
        self._processed_image_cache.move_to_end(cache_key)
        return cached

    def _store_processed_image_in_cache(self, cache_key: tuple[object, ...], processed: np.ndarray) -> None:
        self._processed_image_cache[cache_key] = processed
        self._processed_image_cache.move_to_end(cache_key)
        while len(self._processed_image_cache) > self.PROCESSED_IMAGE_CACHE_SIZE:
            self._processed_image_cache.popitem(last=False)

    def _cached_processed_image(self, record, image_key: tuple[int, float]) -> np.ndarray:
        cache_key = self._processed_image_cache_key(record.path, image_key)
        cached = self._get_processed_image_from_cache(cache_key)
        if cached is not None:
            return cached

        raw_image = load_image_array(str(record.path))
        mask_settings = self._state.spot_detection if self._state.preprocessing.flatten_background_exclude_mask else None
        spots = self._spots_for_preprocessing(image_key)
        external_mask, external_mask_processed = self._effective_external_mask_for_record(record.path, processed_space=True)
        processed = apply_preprocessing(
            raw_image,
            self._state.preprocessing,
            spots=spots,
            mask_settings=mask_settings,
            external_mask=external_mask,
            external_mask_processed=external_mask_processed,
            mask_state=self._state.mask if self._mask_section_applied() else None,
        )
        self._store_processed_image_in_cache(cache_key, processed)
        return processed

    def _invalidate_histogram_source_cache(self) -> None:
        self._histogram_source_cache_signature = None
        self._histogram_source_cache_values = None

    def _invalidate_ignored_mask_cache(self) -> None:
        self._ignored_mask_cache_signature = None
        self._ignored_mask_cache_value = None

    def _invalidate_roi_mask_cache(self) -> None:
        self._roi_mask_cache_signature = None
        self._roi_mask_cache_values = None

    def _invalidate_absorbance_spectrum_cache(self) -> None:
        self._absorbance_spectrum_cache.clear()
        self._absorbance_frame_cache.clear()
        self._spot_absorbance_cache.clear()
        self._absorbance_spectrum_dirty = True

    def _invalidate_image_analysis_caches(self) -> None:
        self._invalidate_ignored_mask_cache()
        self._invalidate_roi_mask_cache()
        self._invalidate_histogram_source_cache()
        self._invalidate_absorbance_spectrum_cache()
        self._processed_external_mask_cache_signature = None
        self._processed_external_mask_cache_value = None
        self._display_spot_cache_signature = None
        self._display_spot_cache_value = None
        self._processed_mask_view_cache_signature = None
        self._processed_mask_view_cache_value = None
        if hasattr(self, "analysis_metric_combo"):
            self._mark_sensorgram_stale()

    def _background_error(self, context: str, message: str) -> None:
        self._append_workflow_log(f"{context} failed: {message}", level="error")
        self._set_status_text(f"{context} failed: {message}")

    def _set_workflow_log_autoscroll_enabled(self, enabled: bool) -> None:
        self._workflow_log_autoscroll_enabled = bool(enabled)
        if hasattr(self, "workflow_log_autoscroll_button"):
            color = "#38bdf8" if enabled else "#94a3b8"
            self.workflow_log_autoscroll_button.setIcon(self._mask_panel_icon("arrow-down", color=color, size=20))

    def _copy_workflow_log(self) -> None:
        if not hasattr(self, "workflow_log_view"):
            return
        QApplication.clipboard().setText(self.workflow_log_view.toPlainText())
        self._append_workflow_log("Workflow log copied to clipboard.", level="debug")

    def _setup_workflow_logging(self) -> None:
        if getattr(self, "_workflow_log_handler", None) is not None:
            return
        self._workflow_log_bridge = WorkflowLogBridge(self)
        self._workflow_log_bridge.record_received.connect(self._append_workflow_log_entry)
        handler = WorkflowLogHandler(self._workflow_log_bridge)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%H:%M:%S"))
        handler._lspr_gui_handler = True  # type: ignore[attr-defined]
        package_logger = logging.getLogger("lspr_imaging_app")
        package_logger.setLevel(logging.DEBUG)
        if not any(getattr(existing, "_lspr_gui_handler", False) for existing in package_logger.handlers):
            package_logger.addHandler(handler)
        self._workflow_logger = logging.getLogger("lspr_imaging_app.workflow")
        self._workflow_logger.setLevel(logging.DEBUG)
        self._workflow_log_handler = handler
        self._workflow_log_buffer: list[tuple[int, str]] = []
        self._workflow_log_buffer_timer = QTimer(self)
        self._workflow_log_buffer_timer.setSingleShot(True)
        self._workflow_log_buffer_timer.timeout.connect(self._flush_workflow_log_buffer)
        self._workflow_log_throttle_state: dict[str, tuple[float, str]] = {}

    def _remove_workflow_logging(self) -> None:
        handler = getattr(self, "_workflow_log_handler", None)
        if handler is None:
            return
        package_logger = logging.getLogger("lspr_imaging_app")
        if handler in package_logger.handlers:
            package_logger.removeHandler(handler)
        self._workflow_log_handler = None
        bridge = getattr(self, "_workflow_log_bridge", None)
        if bridge is not None:
            try:
                bridge.record_received.disconnect(self._append_workflow_log_entry)
            except Exception:
                pass
            self._workflow_log_bridge = None
        buffer_timer = getattr(self, "_workflow_log_buffer_timer", None)
        if buffer_timer is not None:
            try:
                buffer_timer.stop()
            except Exception:
                pass
        self._workflow_log_buffer = []
        self._workflow_log_throttle_state = {}

    def _append_workflow_log_entry(self, levelno: int, text: str) -> None:
        line = str(text).rstrip()
        if not line or not hasattr(self, "workflow_log_view"):
            return
        if int(levelno) < logging.INFO:
            buffer = getattr(self, "_workflow_log_buffer", None)
            buffer_timer = getattr(self, "_workflow_log_buffer_timer", None)
            if buffer is not None and buffer_timer is not None:
                buffer.append((int(levelno), line))
                if not buffer_timer.isActive():
                    buffer_timer.start(150)
                return
        self._append_workflow_log_entry_now(levelno, line)

    def _flush_workflow_log_buffer(self) -> None:
        buffer = getattr(self, "_workflow_log_buffer", None)
        if not buffer:
            return
        batch = list(buffer)
        buffer.clear()
        for levelno, line in self._collapse_workflow_log_batch(batch):
            self._append_workflow_log_entry_now(levelno, line)

    @staticmethod
    def _collapse_workflow_log_batch(batch: list[tuple[int, str]]) -> list[tuple[int, str]]:
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

    def _append_workflow_log_entry_now(self, levelno: int, text: str) -> None:
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
        self.workflow_log_view.moveCursor(QTextCursor.MoveOperation.End)
        self.workflow_log_view.insertHtml(html)
        self.workflow_log_view.insertHtml("<br>")
        if self._workflow_log_autoscroll_enabled:
            scrollbar = self.workflow_log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_workflow_log(self, message: str, *, level: str = "info") -> None:
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
        logger = getattr(self, "_workflow_logger", logging.getLogger("lspr_imaging_app.workflow"))
        logger.log(levelno, line)

    def _append_workflow_log_throttled(self, key: str, message: str, *, level: str = "debug", min_interval: float = 2.0) -> None:
        line = str(message).strip()
        if not line:
            return
        now = time.perf_counter()
        state = getattr(self, "_workflow_log_throttle_state", None)
        if state is None:
            state = {}
            self._workflow_log_throttle_state = state
        previous = state.get(str(key))
        if previous is not None:
            previous_at, previous_line = previous
            if previous_line == line and (now - previous_at) < float(min_interval):
                return
        state[str(key)] = (now, line)
        self._append_workflow_log(line, level=level)

    def _set_status_text(self, text: str) -> None:
        previous_text = self._status_bar_message.text()
        self.status_label.setText(text)
        self._status_bar_message.setText(text)
        if previous_text and previous_text != text:
            self._status_bar_last_action.setText(f"Last action: {previous_text}")

    def _set_status_hint(self, text: str) -> None:
        self._status_bar_hint.setText(f"Hint: {text}")

    @staticmethod
    def _format_elapsed_seconds(seconds: float | None) -> str:
        if seconds is None or not np.isfinite(seconds) or seconds < 0:
            return ""
        total_seconds = int(round(float(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"

    @staticmethod
    def _compact_timing_text(*parts: tuple[str, float | None]) -> str:
        chunks: list[str] = []
        for label, seconds in parts:
            elapsed = MainWindow._format_elapsed_seconds(seconds)
            if elapsed:
                chunks.append(f"{label} {elapsed}")
        return " | ".join(chunks)

    def _workflow_notes_text(self) -> str:
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
            "- Per-spot absorbance cache for single-spot live preview.\n"
            "- Frame absorbance cache for the same frame/settings combinations.\n"
            "- Sensorgram cache for repeated frame-range calculations.\n"
            "- Chromatic landmarks and fitted transforms.\n"
            "\n"
            "What is loaded live\n"
            "- TIFF or Stack to Zarr planes that are not already cached.\n"
            "- Uncached spot spectra and sensorgrams.\n"
            "- ROI and mask changes that alter the analysis signature.\n"
            "\n"
            "What is already optimized\n"
            "- Image refresh runs in the background.\n"
            "- Neighboring image planes are prefetched.\n"
            "- Absorbance results are cached per spot and per frame.\n"
            "- Sensorgram preparation can reuse cached spectra when available.\n"
            "- Workflow events are mirrored to logs/lspr_imaging_<session>.log.\n"
            "- The Workflow console shows the same events live inside the app.\n"
            "\n"
            "Next useful optimizations\n"
            "- Move more frame preparation off the UI thread.\n"
            "- Add selective ROI-only reads for compatible formats.\n"
            "- Expand frame-level caching for repeated sensorgram navigation.\n"
            "- Keep using Stack to Zarr for random access and larger datasets.\n"
        )

    def _show_workflow_notes(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Workflow notes")
        dialog.resize(860, 640)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(self._workflow_notes_text())
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _update_status_hint(self) -> None:
        if self._active_tool == "spots":
            if self.spot_move_action.isChecked():
                self._set_status_hint("Left-click selects, Shift adds, left-drag boxes, right-drag moves, middle-drag pans.")
            elif self.spot_add_action.isChecked():
                self._set_status_hint("Left-click adds a spot. Shift-click still adds to selection.")
            else:
                self._set_status_hint("Left-click selects, Shift adds, left-drag boxes, double-click empty space clears selection.")
            return
        if self._active_tool == "mask":
            self._set_status_hint("Left-drag paints the mask. Use the toolbar icons to show, add, or subtract previews.")
            return
        if self._active_tool in {"rotate", "crop", "measure"}:
            self._set_status_hint("Use the image toolbar controls for the active image tool.")
            return
        self._set_status_hint("Hover a control for guidance.")

    @staticmethod
    def _alpha01(value: float) -> float:
        return alpha01(value)

    def _raw_preprocessing_signature(self) -> tuple[object, ...]:
        crop = self._state.preprocessing.crop
        return (
            bool(getattr(self._state.preprocessing, "image_tools_enabled", True)),
            round(float(self._state.preprocessing.rotation_angle_deg), 6),
            bool(self._state.preprocessing.flip_horizontal),
            bool(self._state.preprocessing.flip_vertical),
            bool(crop.enabled),
            int(crop.x),
            int(crop.y),
            int(crop.width),
            int(crop.height),
        )

    def _make_undo_snapshot(self, label: str) -> UndoSnapshot:
        return UndoSnapshot(
            label=label,
            state=deepcopy(self._state),
            folder_text=self.folder_edit.text(),
            frame_slider_value=int(self.frame_slider.value()) if hasattr(self, "frame_slider") else 0,
            wavelength_slider_value=int(self.wavelength_slider.value()) if hasattr(self, "wavelength_slider") else 0,
            selected_spot_ids=set(self._selected_spot_ids),
            spot_visual_color=self._spot_visual_color.name(),
            ring_visual_color=self._ring_visual_color.name(),
            mask_visual_color=self._mask_visual_color.name(),
            histogram_mask_visual_color=self._histogram_mask_visual_color.name(),
            figure_mask_visual_color=self._figure_mask_visual_color.name(),
            highlight_visual_color=self._highlight_visual_color.name(),
            spot_alpha=float(self._spot_alpha),
            ring_alpha=float(self._ring_alpha),
            mask_alpha=float(self._mask_alpha),
            histogram_mask_alpha=float(self._mask_alpha),  # Use same alpha for now
            figure_mask_alpha=float(self._mask_alpha),     # Use same alpha for now
            highlight_alpha=float(self._highlight_alpha),
            spots_visible=bool(self._spots_visible),
            rings_visible=bool(self._rings_visible),
            mask_visible=bool(self._mask_visible),
            reference_points_visible=bool(self._reference_points_visible),
            histogram_mask_visible=bool(self._mask_visible),  # Use same visibility for now
            figure_mask_visible=bool(self._mask_visible),     # Use same visibility for now
            highlight_visible=bool(self._highlight_visible),
            file_mask=None if self._current_file_mask is None else self._current_file_mask.copy(),
            file_mask_path=None if self._current_file_mask_path is None else str(self._current_file_mask_path),
            file_mask_revision=int(self._external_mask_revision),
        )

    def _undo_signature(self, snapshot: UndoSnapshot) -> tuple[object, ...]:
        dataset_folder = None if snapshot.state.dataset is None else str(snapshot.state.dataset.folder)
        return (
            dataset_folder,
            snapshot.folder_text,
            snapshot.frame_slider_value,
            snapshot.wavelength_slider_value,
            repr(asdict(snapshot.state.preprocessing)),
            repr(asdict(snapshot.state.spot_detection)),
            repr([asdict(spot) for spot in snapshot.state.detected_spots]),
            repr([asdict(group) for group in snapshot.state.spot_groups]),
            tuple(sorted(snapshot.selected_spot_ids)),
            snapshot.spot_visual_color,
            snapshot.ring_visual_color,
            snapshot.mask_visual_color,
            snapshot.highlight_visual_color,
            round(snapshot.spot_alpha, 4),
            round(snapshot.ring_alpha, 4),
            round(snapshot.mask_alpha, 4),
            round(snapshot.highlight_alpha, 4),
            snapshot.spots_visible,
            snapshot.rings_visible,
            snapshot.mask_visible,
            snapshot.reference_points_visible,
            snapshot.highlight_visible,
            snapshot.file_mask_revision,
            snapshot.file_mask_path,
        )

    def _current_undo_signature(self) -> tuple[object, ...]:
        return self._undo_signature(self._make_undo_snapshot("current"))

    def _update_undo_action_state(self) -> None:
        self.undo_action.setEnabled(bool(self._undo_stack) and self._busy_operation_count == 0)
        if hasattr(self, "redo_action"):
            self.redo_action.setEnabled(bool(self._redo_stack) and self._busy_operation_count == 0)

    def _push_undo_point(self, label: str) -> None:
        if self._restoring_undo:
            return
        snapshot = self._make_undo_snapshot(label)
        if self._undo_stack and self._undo_signature(self._undo_stack[-1]) == self._undo_signature(snapshot):
            return
        self._undo_stack.append(snapshot)
        self._redo_stack.clear()
        if len(self._undo_stack) > self.UNDO_STACK_LIMIT:
            self._undo_stack = self._undo_stack[-self.UNDO_STACK_LIMIT :]
        self._update_undo_action_state()

    def _prepare_undo_snapshot(self, label: str) -> None:
        if self._restoring_undo or self._prepared_undo_snapshot is not None:
            return
        self._prepared_undo_snapshot = self._make_undo_snapshot(label)

    def _commit_prepared_undo_snapshot(self) -> None:
        if self._prepared_undo_snapshot is None:
            return
        if self._undo_signature(self._prepared_undo_snapshot) != self._current_undo_signature():
            self._undo_stack.append(self._prepared_undo_snapshot)
            self._redo_stack.clear()
            if len(self._undo_stack) > self.UNDO_STACK_LIMIT:
                self._undo_stack = self._undo_stack[-self.UNDO_STACK_LIMIT :]
        self._prepared_undo_snapshot = None
        self._update_undo_action_state()

    def _clear_prepared_undo_snapshot(self) -> None:
        self._prepared_undo_snapshot = None

    def _restore_undo_snapshot(self, snapshot: UndoSnapshot) -> None:
        self._restoring_undo = True
        try:
            self._clear_prepared_undo_snapshot()
            self._pending_image_refresh_payload = None
            self._latest_image_refresh_signature = None
            self._spot_detection_request_id += 1
            self._spot_metrics_request_id += 1
            self._background_profile_request_id += 1
            self._showing_background_profile_main = False
            self._state = deepcopy(snapshot.state)
            self.folder_edit.setText(snapshot.folder_text)
            self._selected_spot_ids = set(snapshot.selected_spot_ids)
            self._spot_visual_color = QColor(snapshot.spot_visual_color)
            self._ring_visual_color = QColor(snapshot.ring_visual_color)
            self._mask_visual_color = QColor(snapshot.mask_visual_color)
            self._histogram_mask_visual_color = QColor(snapshot.histogram_mask_visual_color)
            self._figure_mask_visual_color = QColor(snapshot.figure_mask_visual_color)
            self._highlight_visual_color = QColor(snapshot.highlight_visual_color)
            self._spot_alpha = snapshot.spot_alpha
            self._ring_alpha = snapshot.ring_alpha
            self._mask_alpha = snapshot.mask_alpha
            self._highlight_alpha = snapshot.highlight_alpha
            self._spots_visible = snapshot.spots_visible
            self._rings_visible = snapshot.rings_visible
            self._mask_visible = snapshot.mask_visible
            self._reference_points_visible = snapshot.reference_points_visible
            self._highlight_visible = snapshot.highlight_visible
            self._current_file_mask = None if snapshot.file_mask is None else snapshot.file_mask.copy()
            self._current_file_mask_path = None if snapshot.file_mask_path is None else Path(snapshot.file_mask_path)
            self._external_mask_revision = int(snapshot.file_mask_revision)
            self._processed_image_cache.clear()
            self._invalidate_image_analysis_caches()
            self._invalidate_background_profile_cache()

            dataset = self._state.dataset
            self._record_map = dataset_record_map(dataset) if dataset is not None else {}
            self._record_key_by_path = (
                {record.path: (int(record.key.frame_index), float(record.key.wavelength_nm)) for record in dataset.records}
                if dataset is not None
                else {}
            )
            self._frame_values = dataset.frame_indices if dataset is not None else []
            self._wavelength_values = dataset.wavelengths_nm if dataset is not None else []
            self._current_record_path = None
            self._current_image_key = None
            self._processed_shape_cache.clear()
            self.dataset_summary.setText(self._dataset_summary_text(dataset))

            self._sync_image_processing_controls()
            self._configure_navigation_inputs()
            self._update_analysis_control_state()
            self._sync_spot_detection_controls()
            self._update_mask_file_button_state()
            self._update_color_button_styles()
            self.show_spots_check.blockSignals(True)
            self.bottom_spot_labels_button.blockSignals(True)
            self.spot_editor_labels_button.blockSignals(True)
            self.show_rings_check.blockSignals(True)
            self.show_mask_check.blockSignals(True)
            self.show_reference_points_check.blockSignals(True)
            self.show_highlight_check.blockSignals(True)
            self.show_spots_check.setChecked(self._spots_visible)
            self.bottom_spot_labels_button.setChecked(self._spot_labels_visible)
            self.spot_editor_labels_button.setChecked(self._spot_labels_visible)
            self.show_rings_check.setChecked(self._rings_visible)
            self.show_mask_check.setChecked(self._mask_visible)
            self.show_reference_points_check.setChecked(self._reference_points_visible)
            self.show_highlight_check.setChecked(self._highlight_visible)
            self.show_spots_check.blockSignals(False)
            self.bottom_spot_labels_button.blockSignals(False)
            self.spot_editor_labels_button.blockSignals(False)
            self.show_rings_check.blockSignals(False)
            self.show_mask_check.blockSignals(False)
            self.show_reference_points_check.blockSignals(False)
            self.show_highlight_check.blockSignals(False)
            self._refresh_view_toggle_icons()
            self._update_spot_label_button_icon(bool(self._spot_labels_visible))
            self.spot_alpha_slider.blockSignals(True)
            self.ring_alpha_slider.blockSignals(True)
            self.mask_alpha_slider.blockSignals(True)
            self.highlight_alpha_slider.blockSignals(True)
            self.spot_alpha_slider.setValue(int(round(self._spot_alpha * 100.0)))
            self.ring_alpha_slider.setValue(int(round(self._ring_alpha * 100.0)))
            self.mask_alpha_slider.setValue(int(round(self._mask_alpha * 100.0)))
            self.highlight_alpha_slider.setValue(int(round(self._highlight_alpha * 100.0)))
            self.spot_alpha_slider.blockSignals(False)
            self.ring_alpha_slider.blockSignals(False)
            self.mask_alpha_slider.blockSignals(False)
            self.highlight_alpha_slider.blockSignals(False)

            self._configure_slider(self.frame_slider, len(self._frame_values))
            self._configure_slider(self.wavelength_slider, len(self._wavelength_values))
            self._configure_navigation_inputs()
            if dataset is not None and self._frame_values and self._wavelength_values:
                frame_value = min(max(snapshot.frame_slider_value, 0), len(self._frame_values) - 1)
                wavelength_value = min(max(snapshot.wavelength_slider_value, 0), len(self._wavelength_values) - 1)
                self.frame_slider.blockSignals(True)
                self.wavelength_slider.blockSignals(True)
                self.frame_slider.setValue(frame_value)
                self.wavelength_slider.setValue(wavelength_value)
                self.frame_slider.blockSignals(False)
                self.wavelength_slider.blockSignals(False)
                self._refresh_image()
            self._update_selection_dependent_plots(force=True)
            self._save_visual_preferences()
            self._schedule_processing_state_save()
        finally:
            self._restoring_undo = False

    def _undo(self) -> None:
        if self._busy_operation_count > 0:
            self._set_status_text("Wait for the current operation to finish before undo.")
            return
        if not self._undo_stack:
            self._set_status_text("Nothing to undo.")
            return
        snapshot = self._undo_stack.pop()
        self._redo_stack.append(self._make_undo_snapshot(snapshot.label))
        if len(self._redo_stack) > self.UNDO_STACK_LIMIT:
            self._redo_stack = self._redo_stack[-self.UNDO_STACK_LIMIT :]
        self._restore_undo_snapshot(snapshot)
        self._update_undo_action_state()
        self._set_status_text(f"Undid: {snapshot.label}")

    def _redo(self) -> None:
        if self._busy_operation_count > 0:
            self._set_status_text("Wait for the current operation to finish before redo.")
            return
        if not self._redo_stack:
            self._set_status_text("Nothing to redo.")
            return
        snapshot = self._redo_stack.pop()
        self._undo_stack.append(self._make_undo_snapshot(snapshot.label))
        if len(self._undo_stack) > self.UNDO_STACK_LIMIT:
            self._undo_stack = self._undo_stack[-self.UNDO_STACK_LIMIT :]
        self._restore_undo_snapshot(snapshot)
        self._update_undo_action_state()
        self._set_status_text(f"Redid: {snapshot.label}")

    def _begin_busy(self, text: str, *, determinate: bool = False) -> None:
        self._busy_operation_count += 1
        self._busy_started_at = time.perf_counter()
        self._busy_is_determinate = bool(determinate)
        self._busy_last_percent = 0
        self._set_status_text(text)
        if determinate:
            self._status_bar_busy.setRange(0, 100)
            self._status_bar_busy.setValue(0)
            self._status_bar_busy.setTextVisible(True)
            self._status_bar_busy_detail.setText("0:00 | ETA --:-- | 0%")
        else:
            self._status_bar_busy.setRange(0, 0)
            self._status_bar_busy.setTextVisible(False)
            self._status_bar_busy_detail.setText("0:00")
        self._status_bar_busy.show()
        self._status_bar_busy_detail.show()
        if not self._wait_cursor_active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._wait_cursor_active = True
        self._update_undo_action_state()

    def _end_busy(self, text: str | None = None) -> None:
        self._busy_operation_count = max(0, self._busy_operation_count - 1)
        if self._busy_operation_count == 0:
            self._status_bar_busy.hide()
            self._status_bar_busy.setRange(0, 0)
            self._status_bar_busy.setTextVisible(False)
            self._status_bar_busy_detail.setText("")
            self._status_bar_busy_detail.hide()
            if self._wait_cursor_active:
                QApplication.restoreOverrideCursor()
                self._wait_cursor_active = False
            self._busy_started_at = None
            self._busy_is_determinate = False
            self._busy_last_percent = 0
        if text is not None:
            self._set_status_text(text)
        self._update_undo_action_state()

    def _sync_busy_cursor_state(self) -> None:
        if self._busy_operation_count <= 0:
            return
        if self._image_refresh_running or self._sensorgram_running or self._absorbance_spectrum_running or self._ome_zarr_export_running:
            return
        self._busy_operation_count = 0
        self._status_bar_busy.hide()
        self._status_bar_busy.setRange(0, 0)
        self._status_bar_busy.setTextVisible(False)
        self._status_bar_busy_detail.setText("")
        self._status_bar_busy_detail.hide()
        if self._wait_cursor_active:
            QApplication.restoreOverrideCursor()
            self._wait_cursor_active = False
        self._busy_started_at = None
        self._busy_is_determinate = False
        self._busy_last_percent = 0
        self._update_undo_action_state()

    def _update_busy_progress(self, percent: int, text: str | None = None) -> None:
        if self._busy_operation_count <= 0:
            return
        self._busy_is_determinate = True
        if self._status_bar_busy.maximum() <= 0:
            self._status_bar_busy.setRange(0, 100)
        self._status_bar_busy.setTextVisible(True)
        current_percent = int(np.clip(percent, 0, 100))
        self._busy_last_percent = current_percent
        self._status_bar_busy.setValue(current_percent)
        started_at = self._busy_started_at
        elapsed = time.perf_counter() - started_at if started_at is not None else None
        if elapsed is not None:
            elapsed_text = self._format_elapsed_seconds(elapsed)
            eta_text = "--:--"
            if current_percent > 0:
                eta_seconds = max((elapsed * (100.0 - current_percent)) / max(current_percent, 1), 0.0)
                eta_text = self._format_elapsed_seconds(eta_seconds) or "0:00"
            self._status_bar_busy_detail.setText(f"{elapsed_text} | ETA {eta_text} | {current_percent:d}%")
        if text:
            self._set_status_text(text)

    def _schedule_image_refresh(self) -> None:
        self._capture_pending_image_view_ranges()
        self._image_refresh_timer.start()

    def _schedule_histogram_refresh(self) -> None:
        if self._current_processed_image is None:
            return
        self._histogram_refresh_timer.start()
        if not self._dragging_spots and not self._spot_edit_refresh_pending:
            self._schedule_absorbance_spectrum_refresh()

    def _schedule_processing_state_save(self) -> None:
        self._processing_state_save_timer.start()

    def _mask_preview_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...]:
        target_key = image_key if image_key is not None else self._current_image_key
        return (
            bool(self._state.spot_detection.ignore_marked_pixels),
            int(self._mask_state_revision),
            self._external_mask_signature(target_key),
        )

    def _background_profile_signature(self) -> tuple[object, ...] | None:
        if self._current_record_path is None or self._current_image_key is None:
            return None
        crop = self._state.preprocessing.crop
        return (
            str(self._current_record_path),
            self._current_image_key,
            bool(getattr(self._state.preprocessing, "image_tools_enabled", True)),
            round(float(self._state.preprocessing.rotation_angle_deg), 6),
            bool(self._state.preprocessing.flip_horizontal),
            bool(self._state.preprocessing.flip_vertical),
            self._chromatic_signature_for_image_key(self._current_image_key),
            bool(crop.enabled),
            int(crop.x),
            int(crop.y),
            int(crop.width),
            int(crop.height),
            round(float(self._state.preprocessing.flatten_background_sigma_px), 3),
            int(max(getattr(self._state.preprocessing, "flatten_background_binning", 2), 1)),
            bool(self._state.preprocessing.flatten_background_exclude_spots),
            bool(self._state.preprocessing.flatten_background_exclude_mask),
            self._spot_signature(self._spots_for_preprocessing(self._current_image_key))
            if self._state.preprocessing.flatten_background_exclude_spots
            else None,
            self._mask_preview_signature() if self._state.preprocessing.flatten_background_exclude_mask else None,
        )

    def _calculate_background_profile_image(self) -> np.ndarray | None:
        if self._current_record_path is None or self._current_image_key is None:
            return None
        spots = (
            deepcopy(self._spots_for_preprocessing(self._current_image_key))
            if self._state.preprocessing.flatten_background_exclude_spots
            else None
        )
        preprocessing = deepcopy(self._state.preprocessing)
        mask_settings = deepcopy(self._state.spot_detection) if self._state.preprocessing.flatten_background_exclude_mask else None
        external_mask, external_mask_processed = self._effective_external_mask_for_record(
            self._current_record_path,
            processed_space=True,
        )
        return _background_profile_task(
            str(self._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(self._state.preprocessing.flatten_background_sigma_px),
            spots,
            external_mask,
        )

    def _update_background_profile_preview(self) -> None:
        signature = self._background_profile_signature()
        if signature is None:
            return
        if (
            self._background_profile_cache_signature == signature
            and self._background_profile_cache_image is not None
        ):
            if self._showing_background_profile_main:
                self._apply_main_image_content()
            return
        spots = (
            deepcopy(self._spots_for_preprocessing(self._current_image_key))
            if self._state.preprocessing.flatten_background_exclude_spots
            else None
        )
        preprocessing = deepcopy(self._state.preprocessing)
        mask_settings = deepcopy(self._state.spot_detection) if self._state.preprocessing.flatten_background_exclude_mask else None
        external_mask, external_mask_processed = self._effective_external_mask_for_record(
            self._current_record_path,
            processed_space=True,
        )
        request_id = self._background_profile_request_id + 1
        self._background_profile_request_id = request_id
        worker = FunctionWorker(
            _background_profile_task,
            str(self._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(self._state.preprocessing.flatten_background_sigma_px),
            spots,
            external_mask,
            supports_progress=True,
        )
        self._begin_busy("Updating background profile preview...")
        self._append_workflow_log("Background profile preview start", level="info")
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.result.connect(
            lambda profile,
            request_id=request_id,
            signature=signature: self._on_background_profile_ready(request_id, signature, profile)
        )
        worker.signals.error.connect(lambda message: self._on_background_profile_failed(message))
        self._thread_pool.start(worker)

    def _on_background_profile_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        profile: np.ndarray,
    ) -> None:
        self._end_busy()
        if request_id != self._background_profile_request_id:
            return
        if signature != self._background_profile_signature():
            return
        self._background_profile_cache_signature = signature
        self._background_profile_cache_image = profile
        self._append_workflow_log("Background profile preview done", level="success")
        if self._showing_background_profile_main:
            self._apply_main_image_content()

    def _on_background_profile_failed(self, message: str) -> None:
        self._end_busy()
        self._append_workflow_log(f"Background profile preview failed | {message}", level="error")
        self._background_error("Background profile preview", message)

    def _invalidate_background_profile_cache(self) -> None:
        self._background_profile_cache_signature = None
        self._background_profile_cache_image = None

    def _apply_main_image_content(self) -> None:
        if self._showing_background_profile_main and self._background_profile_cache_image is not None:
            self.image_item.setImage(self._background_profile_cache_image.T, autoLevels=True)
        elif self._current_processed_image is not None:
            self.image_item.setImage(self._current_processed_image.T, autoLevels=True)
        self._sync_main_view_mode()
        self._update_reference_star_overlay()

    def _sync_main_view_mode(self) -> None:
        showing_profile = self._showing_background_profile_main and self._background_profile_cache_image is not None
        if showing_profile:
            self.intensity_highlight_item.hide()
            self.ignore_mask_item.hide()
            if self._crop_roi is not None:
                self._crop_roi.setVisible(False)
            self._update_crop_overlay()
            for bundle in self._spot_overlay_items.values():
                bundle.curve.setVisible(False)
                if bundle.ring_fill is not None:
                    bundle.ring_fill.setVisible(False)
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
                if bundle.label is not None:
                    bundle.label.setVisible(False)
            for bundle in self._guide_overlay_items.values():
                bundle.vertical.setVisible(False)
                bundle.horizontal.setVisible(False)
                bundle.marker.setVisible(False)
            for bundle in self._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            self._hide_measurement_overlay()
            self._refresh_scale_bar_overlay()
            return
        self._update_selected_intensity_overlay()
        self._update_ignore_mask_overlay()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._update_spot_overlays()
        self._update_landmark_overlays()
        self._update_guide_overlays()
        self._sync_measurement_visibility()
        self._update_reference_star_overlay()

    def _sync_background_profile_buttons(self, checked: bool) -> None:
        for button in (getattr(self, "background_profile_hold_button", None), getattr(self, "background_profile_button", None)):
            if button is None:
                continue
            button.blockSignals(True)
            button.setChecked(bool(checked))
            button.setIcon(self._make_background_profile_icon(bool(checked), size=APP_THEME.compact_icon_inner))
            button.blockSignals(False)

    def _sync_background_exclusion_buttons(self) -> None:
        if hasattr(self, "background_ignore_spot_button"):
            self.background_ignore_spot_button.setIcon(
                self._background_exclusion_icon(
                    "current-location-off",
                    bool(self.background_ignore_spot_button.isChecked()),
                    size=APP_THEME.compact_icon_inner,
                )
            )
        if hasattr(self, "background_ignore_mask_button"):
            self.background_ignore_mask_button.setIcon(
                self._background_exclusion_icon(
                    "mask-off",
                    bool(self.background_ignore_mask_button.isChecked()),
                    size=APP_THEME.compact_icon_inner,
                )
            )

    def _on_background_profile_toggled(self, checked: bool) -> None:
        self._showing_background_profile_main = bool(checked)
        self._sync_background_profile_buttons(bool(checked))
        if checked:
            self._update_background_profile_preview()
            if self._background_profile_cache_image is not None:
                self._apply_main_image_content()
            else:
                self._sync_main_view_mode()
        else:
            self._apply_main_image_content()
        self._save_visual_preferences()

    def _apply_dark_plot_theme(self) -> None:
        theme = get_active_theme()
        self.image_view.setBackground(theme.toolbar_bg)
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {theme.window_bg};
            }}
            QWidget {{
                background: {theme.window_bg};
            }}
            QSplitter::handle {{
                background: {theme.control_disabled_border};
            }}
            QSplitter::handle:horizontal {{
                width: 4px;
            }}
            QSplitter::handle:vertical {{
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {theme.control_border};
            }}
            """
        )
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(theme.window_bg))
        palette.setColor(QPalette.ColorRole.Base, QColor(theme.window_bg))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.toolbar_section_bg))
        palette.setColor(QPalette.ColorRole.Button, QColor(theme.control_bg))
        palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_primary))
        self.setPalette(palette)
        if QApplication.instance() is not None:
            QApplication.instance().setPalette(palette)
        self.image_toolbar.setStyleSheet(dark_image_toolbar_stylesheet())
        self._configure_data_plot(self.histogram_plot, bottom_label="Intensity", left_label="Pixels (%)", y_min=0.0)
        self._configure_data_plot(self.spectrum_plot, bottom_label="Wavelength (nm)", left_label="Absorbance")
        self._configure_data_plot(self.sensorgram_plot, bottom_label="Frame", left_label="Metric")
        self._update_sensorgram_plot_labels()
        self._apply_high_contrast_button_styles()

    def _set_ui_theme(self, theme_name: str) -> None:
        theme = GRAY_DARK_THEME if theme_name == "gray" else BLUE_DARK_THEME
        set_active_theme(theme)
        self._settings.setValue("ui/theme", theme_name)
        self._apply_theme_styles()

    def _apply_theme_styles(self) -> None:
        self._apply_dark_plot_theme()
        self._refresh_pin_and_apply_icons()
        self._refresh_collapsible_styles()
        self._apply_high_contrast_button_styles()
        self._apply_spot_list_table_style()
        self._update_spot_label_button_icon(self._spot_labels_visible)
        self._apply_histogram_log_mode(refresh=not self._startup_restore_in_progress)

    def _refresh_pin_and_apply_icons(self) -> None:
        for section in (
            getattr(self, "dataset_section", None),
            getattr(self, "mask_section", None),
            getattr(self, "chromatic_section", None),
            getattr(self, "image_tools_section", None),
            getattr(self, "spot_editor_section", None),
            getattr(self, "background_section", None),
            getattr(self, "analysis_section", None),
        ):
            if section is None:
                continue
            if hasattr(section, "_pin_button"):
                section._pin_button.setIcon(section._make_pin_icon(section._pin_button.isChecked()))
            if hasattr(section, "_apply_button") and section._apply_button is not None:
                section._apply_button.setIcon(section._make_apply_icon(section._apply_button.isChecked()))

    def _refresh_collapsible_styles(self) -> None:
        for section in (
            getattr(self, "dataset_section", None),
            getattr(self, "mask_section", None),
            getattr(self, "chromatic_section", None),
            getattr(self, "image_tools_section", None),
            getattr(self, "spot_editor_section", None),
            getattr(self, "background_section", None),
            getattr(self, "analysis_section", None),
        ):
            if section is None:
                continue
            if hasattr(section, "_toggle"):
                section._toggle.setStyleSheet(collapsible_toggle_stylesheet())
            if hasattr(section, "_pin_button"):
                section._pin_button.setStyleSheet(collapsible_pin_stylesheet())

    def _configure_data_plot(
        self,
        plot: pg.PlotItem,
        *,
        bottom_label: str,
        left_label: str,
        y_min: float | None = None,
    ) -> None:
        theme = get_active_theme()
        plot.setBackground(theme.toolbar_bg)
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setLabel("bottom", bottom_label)
        plot.setLabel("left", left_label)
        if y_min is not None:
            plot.setLimits(yMin=y_min)
        bottom_axis = plot.getAxis("bottom")
        bottom_axis.setStyle(tickTextOffset=2)
        bottom_axis.setHeight(28)

    def _apply_high_contrast_button_styles(self) -> None:
        style = standard_push_button_stylesheet(padding="2px 6px", font_size=10)
        buttons = [
            self.browse_button,
            self.load_button,
            self.export_settings_button,
            self.import_settings_button,
            self.set_reference_button,
            self.chromatic_apply_check,
            self.chromatic_start_button,
            self.chromatic_prev_button,
            self.chromatic_next_button,
            self.chromatic_landmark_mark_button,
            self.chromatic_landmark_clear_button,
            self.chromatic_transform_button,
            self.ignore_marked_check,
            self.background_removal_link,
            self.clear_spot_selection_button,
        ]
        for button in buttons:
            button.setStyleSheet(style)

    def _apply_spot_list_table_style(self) -> None:
        theme = get_active_theme()
        self.spot_list_table.setStyleSheet(
            "QTableWidget {"
            f"  font-size: 8pt;"
            f"  color: {theme.text_primary};"
            f"  background: {theme.toolbar_bg};"
            f"  alternate-background-color: {theme.toolbar_section_bg};"
            f"  selection-background-color: {theme.primary_action_bg};"
            f"  selection-color: {theme.text_primary};"
            f"  gridline-color: {theme.toolbar_border};"
            "}"
            "QTableWidget::item { padding: 1px 3px; }"
            "QTableWidget::item:selected { background: " + theme.primary_action_bg + "; color: " + theme.text_primary + "; }"
            f"QHeaderView::section {{ padding: 1px 3px; font-size: 8pt; color: {theme.text_muted}; background: {theme.toolbar_section_bg}; }}"
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._handle_global_page_shortcuts(event):
            return
        if self._active_tool == "chromatic_landmark":
            if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
                step = 1.0
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    step = 5.0
                dx = 0.0
                dy = 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                if self._move_selected_landmark(dx, dy):
                    event.accept()
                    return
        
        if self._active_tool == "spots" and event.key() in {Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                direction = -1 if event.key() == Qt.Key.Key_PageUp else 1
                if self._navigate_wavelength_image(direction):
                    event.accept()
                    return

        if self._active_tool == "rotate" and event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            step = 0.1
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step = 5.0
            elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                step = 1.0

            if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Down}:
                step *= -1.0
            self._adjust_rotation(step)
            event.accept()
            return
        if self._active_tool == "spots" and event.key() in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self._select_neighbor_spot(event.key()):
                    event.accept()
                    return
            elif self._is_current_reference_image() and self.spot_move_action.isChecked() and self._selected_spot_ids:
                step = 1.0
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    step = 5.0
                dx = 0.0
                dy = 0.0
                if event.key() == Qt.Key.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key.Key_Right:
                    dx = step
                elif event.key() == Qt.Key.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key.Key_Down:
                    dy = step
                self._move_selected_spots(dx, dy)
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._ome_zarr_export_running and self._ome_zarr_export_cancel_event is not None:
            self._ome_zarr_export_cancel_event.set()
        self._save_processing_state_for_dataset()
        self._save_visual_preferences()
        geometry = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        self._settings.setValue(
            "window_geometry_rect",
            [int(geometry.x()), int(geometry.y()), int(geometry.width()), int(geometry.height())],
        )
        current_screen = self.windowHandle().screen() if self.windowHandle() is not None else self.screen()
        self._settings.setValue("window_screen_name", current_screen.name() if current_screen is not None else "")
        if current_screen is not None:
            available = current_screen.availableGeometry()
            width = max(int(geometry.width()), 1)
            height = max(int(geometry.height()), 1)
            rel_width = float(width / max(available.width(), 1))
            rel_height = float(height / max(available.height(), 1))
            rel_x = float((geometry.x() - available.left()) / max(available.width() - width, 1))
            rel_y = float((geometry.y() - available.top()) / max(available.height() - height, 1))
            self._settings.setValue(
                "window_geometry_rel",
                [
                float(np.clip(rel_x, 0.0, 1.0)),
                float(np.clip(rel_y, 0.0, 1.0)),
                float(np.clip(rel_width, 0.2, 1.0)),
                float(np.clip(rel_height, 0.2, 1.0)),
            ],
        )
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("window_is_maximized", bool(self.isMaximized()))
        self._settings.setValue("window_is_fullscreen", bool(self.isFullScreen()))
        self._settings.setValue("last_folder", self.folder_edit.text())
        self._settings.setValue("histogram_bin_size", int(self.histogram_bins_spin.value()))
        self._save_layout_preferences()
        self._remove_workflow_logging()
        super().closeEvent(event)

    def prepare_initial_show(self) -> None:
        if self._window_geometry_restored:
            return
        self.resize(1320, 860)
        self.move(40, 40)

    # ------------------------------------------------------------------
    # Window and splitter restore/save
    # ------------------------------------------------------------------

    def _restore_window_geometry(self) -> None:
        if self._window_geometry_restored:
            return
        geometry_rel = self._settings.value("window_geometry_rel")
        available = self._best_restore_screen_geometry(None)
        logging.getLogger("lspr_imaging_app.layout").debug(
            "restore window | saved_rel=%r | screen=%s",
            geometry_rel,
            available.getRect() if available is not None else None,
        )
        if isinstance(geometry_rel, list) and len(geometry_rel) == 4 and available is not None:
            try:
                rel_x, rel_y, rel_width, rel_height = (float(value) for value in geometry_rel)
            except Exception:
                rel_x, rel_y, rel_width, rel_height = 0.0, 0.0, 1.0, 1.0
            rel_width = float(np.clip(rel_width, 0.2, 1.0))
            rel_height = float(np.clip(rel_height, 0.2, 1.0))
            width_limit = max(int(available.width() * 0.9), 900)
            height_limit = max(int(available.height() * 0.9), 700)
            width = min(max(int(round(rel_width * available.width())), 900), width_limit)
            height = min(max(int(round(rel_height * available.height())), 700), height_limit)
            margin_x = min(40, max(int(available.width() * 0.05), 12))
            margin_y = min(40, max(int(available.height() * 0.05), 12))
            x = available.left() + margin_x + int(round(np.clip(rel_x, 0.0, 1.0) * max(available.width() - width - 2 * margin_x, 0)))
            y = available.top() + margin_y + int(round(np.clip(rel_y, 0.0, 1.0) * max(available.height() - height - 2 * margin_y, 0)))
            logging.getLogger("lspr_imaging_app.layout").debug(
                "apply geometry | x=%s y=%s w=%s h=%s",
                x,
                y,
                width,
                height,
            )
            self.setGeometry(x, y, width, height)
        else:
            if available is not None:
                width = min(1320, max(900, int(available.width() * 0.9)))
                height = min(860, max(700, int(available.height() * 0.9)))
                x = available.left() + max((available.width() - width) // 2, 0)
                y = available.top() + max((available.height() - height) // 2, 0)
                logging.getLogger("lspr_imaging_app.layout").debug(
                    "fallback geometry | x=%s y=%s w=%s h=%s",
                    x,
                    y,
                    width,
                    height,
                )
                self.setGeometry(x, y, width, height)
            else:
                self.resize(1320, 860)
                self.move(40, 40)
        if available is not None:
            self._move_inside_available_screen(available)
        try:
            self._restore_default_panel_layout()
        except Exception:
            pass
        self._startup_restore_window_maximized = self._settings_bool("window_is_maximized", False)
        self._startup_restore_window_fullscreen = self._settings_bool("window_is_fullscreen", False)
        self._window_geometry_restored = True

    def _best_restore_screen_geometry(self, saved_rect: QRectF | None = None):
        screens = QGuiApplication.screens()
        if not screens:
            return self.screen().availableGeometry() if self.screen() is not None else None
        target_name = str(self._settings.value("window_screen_name", "") or "").strip()
        logging.getLogger("lspr_imaging_app.layout").debug(
            "screen candidates | %s | target=%r | saved_rect=%s",
            [screen.name() for screen in screens],
            target_name,
            saved_rect.toRect().getRect() if saved_rect is not None else None,
        )
        for screen in screens:
            if screen.name() == target_name:
                logging.getLogger("lspr_imaging_app.layout").debug("selected screen by name | %s", screen.name())
                return screen.availableGeometry()
        if saved_rect is not None:
            best_screen = None
            best_area = -1
            for screen in screens:
                available = screen.availableGeometry()
                intersection = available.intersected(saved_rect.toRect())
                area = intersection.width() * intersection.height()
                if area > best_area:
                    best_area = area
                    best_screen = screen
            if best_screen is not None and best_area > 0:
                logging.getLogger("lspr_imaging_app.layout").debug(
                    "selected screen by overlap | %s | area=%s",
                    best_screen.name(),
                    best_area,
                )
                return best_screen.availableGeometry()
        if self.screen() is not None:
            logging.getLogger("lspr_imaging_app.layout").debug("selected current screen | %s", self.screen().name())
            return self.screen().availableGeometry()
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            logging.getLogger("lspr_imaging_app.layout").debug("selected primary screen | %s", primary.name())
        return primary.availableGeometry() if primary is not None else None

    def _move_inside_available_screen(self, available=None) -> None:
        if available is None:
            available = self.screen().availableGeometry() if self.screen() is not None else None
        if available is None:
            return

        frame = self.frameGeometry()
        x = min(max(frame.x(), available.left()), max(available.right() - frame.width() + 1, available.left()))
        y = min(max(frame.y(), available.top()), max(available.bottom() - frame.height() + 1, available.top()))
        self.move(x, y)

    def _load_last_folder(self, fallback: Path) -> Path:
        value = self._settings.value("last_folder")
        if not value:
            return fallback
        folder = Path(str(value))
        return folder if folder.exists() else fallback

    def _settings_int(self, key: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        return settings_int(self._settings, key, default, minimum=minimum, maximum=maximum)

    def _settings_histogram_bin_size(self) -> int:
        value = self._settings.value("histogram_bin_size", None)
        if value is not None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = 512
            return max(1, min(parsed, 8192))
        legacy_bins = self._settings_int("histogram_bins", 128, minimum=8, maximum=512)
        intensity_span = int(self.HISTOGRAM_MAX_INTENSITY - self.HISTOGRAM_MIN_INTENSITY + 1)
        derived_size = max(int(round(intensity_span / max(legacy_bins, 1))), 1)
        return max(1, min(derived_size, 8192))

    def _settings_bool(self, key: str, default: bool) -> bool:
        return settings_bool(self._settings, key, default)

    @staticmethod
    def _set_section_applied(section: CollapsibleSection, applied: bool) -> None:
        if not section.has_apply_toggle():
            return
        previous = section.blockSignals(True)
        try:
            section.set_applied(applied)
        finally:
            section.blockSignals(previous)

    def _unlink_image_tools_for_preview(self) -> bool:
        if not bool(self._state.preprocessing.image_tools_enabled):
            self._image_tools_preview_only = True
            return False
        self._state.preprocessing.image_tools_enabled = False
        self._image_tools_preview_only = True
        self._set_section_applied(self.image_tools_section, False)
        self._save_processing_state_for_dataset()
        return True

    def _mask_section_applied(self) -> bool:
        return bool(self._state.spot_detection.ignore_marked_pixels)

    def _bind_collapsible_group(self, sections: list[CollapsibleSection]) -> None:
        for section in sections:
            section.expanded_changed.connect(lambda *_args: self._save_layout_preferences())

    def _on_collapsible_section_expanded(
        self,
        source: CollapsibleSection,
        sections: list[CollapsibleSection],
        expanded: bool,
    ) -> None:
        self._save_layout_preferences()

    def _restore_layout_preferences(self) -> None:
        spot_list_visible = self._settings_bool("layout/spot_list_visible", True)
        cached_spots_only_visible = self._settings_bool("layout/cached_spots_only_visible", False)
        self.spot_list_action.blockSignals(True)
        self.spot_list_action.setChecked(spot_list_visible)
        self.spot_list_action.blockSignals(False)
        if hasattr(self, "spot_list_cached_button"):
            self.spot_list_cached_button.blockSignals(True)
            self.spot_list_cached_button.setChecked(cached_spots_only_visible)
            self.spot_list_cached_button.setIcon(self._make_cached_spots_icon(cached_spots_only_visible))
            self.spot_list_cached_button.blockSignals(False)
        self._cached_spots_only_visible = cached_spots_only_visible
        self._suspend_collapsible_accordion = True
        try:
            self.dataset_section.set_pinned(self._settings_bool("dataset_section_pinned", False))
            self.dataset_section.set_expanded(self._settings_bool("dataset_section_expanded", True))
            self.chromatic_section.set_pinned(self._settings_bool("chromatic_section_pinned", False))
            self.chromatic_section.set_expanded(self._settings_bool("chromatic_section_expanded", False))
            self.mask_section.set_pinned(self._settings_bool("mask_section_pinned", False))
            self.mask_section.set_expanded(self._settings_bool("mask_section_expanded", True))
            self.image_tools_section.set_pinned(self._settings_bool("image_tools_panel_pinned", False))
            self.image_tools_section.set_expanded(self._settings_bool("image_tools_panel_expanded", True))
            self.spot_editor_section.set_pinned(self._settings_bool("spot_editor_section_pinned", False))
            self.spot_editor_section.set_expanded(self._settings_bool("spot_editor_section_expanded", True))
            self.background_section.set_pinned(self._settings_bool("background_section_pinned", False))
            self.background_section.set_expanded(self._settings_bool("background_section_expanded", True))
            self.analysis_section.set_pinned(self._settings_bool("analysis_section_pinned", False))
            self.analysis_section.set_expanded(self._settings_bool("analysis_section_expanded", True))
            if hasattr(self, "workflow_log_section"):
                self.workflow_log_section.set_expanded(True)
            self._analysis_enabled = self._settings_bool("analysis_section_applied", self._analysis_enabled)
            self._set_section_applied(self.analysis_section, self._analysis_enabled)
        finally:
            self._suspend_collapsible_accordion = False
        self._restore_panel_layout_preferences()
        self._normalize_panel_layout()
        self.left_tabs.blockSignals(True)
        try:
            self.left_tabs.setCurrentIndex(self._settings_int("left_tab_index", 0, minimum=0, maximum=max(self.left_tabs.count() - 1, 0)))
        finally:
            self.left_tabs.blockSignals(False)
        self._layout_preferences_ready = True
        self._update_analysis_control_state()

    def _apply_saved_splitter_sizes(self, main_sizes) -> None:
        return

    def _save_layout_preferences(self) -> None:
        if not self._layout_preferences_ready or self._suspend_layout_save:
            return
        self._settings.setValue("left_tab_index", self.left_tabs.currentIndex())
        self._settings.setValue("layout/spot_list_visible", bool(self.spot_list_panel.isVisible()))
        self._settings.setValue("layout/cached_spots_only_visible", bool(self._cached_spots_only_visible))
        self._settings.setValue("dataset_section_expanded", self.dataset_section.is_expanded())
        self._settings.setValue("dataset_section_pinned", self.dataset_section.is_pinned())
        self._settings.setValue("chromatic_section_expanded", self.chromatic_section.is_expanded())
        self._settings.setValue("chromatic_section_pinned", self.chromatic_section.is_pinned())
        self._settings.setValue("mask_section_expanded", self.mask_section.is_expanded())
        self._settings.setValue("mask_section_pinned", self.mask_section.is_pinned())
        self._settings.setValue("image_tools_panel_expanded", self.image_tools_section.is_expanded())
        self._settings.setValue("image_tools_panel_pinned", self.image_tools_section.is_pinned())
        self._settings.setValue("spot_editor_section_expanded", self.spot_editor_section.is_expanded())
        self._settings.setValue("spot_editor_section_pinned", self.spot_editor_section.is_pinned())
        self._settings.setValue("background_section_expanded", self.background_section.is_expanded())
        self._settings.setValue("background_section_pinned", self.background_section.is_pinned())
        self._settings.setValue("analysis_section_expanded", self.analysis_section.is_expanded())
        self._settings.setValue("analysis_section_pinned", self.analysis_section.is_pinned())
        self._settings.setValue("analysis_section_applied", self._analysis_enabled)
        if hasattr(self, "workflow_log_section"):
            self._settings.setValue("workflow_log_section_expanded", self.workflow_log_section.is_expanded())
        self._settings.setValue("ome_zarr/chunk_size_px", int(self._current_ome_zarr_chunk_size()))
        self._settings.setValue("ome_zarr/chunk_mode", self.ome_zarr_chunk_mode_combo.currentData())
        self._settings.setValue("ome_zarr/chunk_guide_visible", bool(self.ome_zarr_chunk_guide_button.isChecked()))
        self._settings.setValue("ome_zarr/compression_enabled", bool(self.ome_zarr_compression_button.isChecked()))
        self._save_panel_layout_preferences()

    def _capture_panel_layout_snapshot(self) -> dict[str, QByteArray] | None:
        if not hasattr(self, "_main_splitter"):
            return None
        return {
            "main": self._main_splitter.saveState(),
            "visual": self._visual_splitter.saveState(),
            "top": self._top_visual_splitter.saveState(),
            "bottom": self._bottom_visual_splitter.saveState(),
        }

    def _apply_panel_layout_snapshot(self, snapshot: dict[str, QByteArray] | None) -> None:
        if snapshot is None:
            self._apply_default_splitter_sizes()
            return
        for key, splitter_name in (
            ("main", "_main_splitter"),
            ("visual", "_visual_splitter"),
            ("top", "_top_visual_splitter"),
            ("bottom", "_bottom_visual_splitter"),
        ):
            splitter = getattr(self, splitter_name, None)
            state = snapshot.get(key)
            if splitter is None or not isinstance(state, QByteArray) or state.isEmpty():
                continue
            try:
                splitter.restoreState(state)
            except Exception:
                pass
        self._normalize_panel_layout()

    def _panel_layout_panels(self) -> list[tuple[str, QWidget]]:
        return [
            ("workflow_panel", self.workflow_panel),
            ("spot_list_panel", self.spot_list_panel),
            ("image_panel", self.image_panel),
            ("histogram_panel", self.histogram_panel),
            ("spectra_panel", self.spectra_panel),
            ("sensorgram_panel", self.sensorgram_panel),
        ]

    def _restore_saved_panel_layout_state(self) -> bool:
        if not hasattr(self, "_main_splitter"):
            logging.getLogger("lspr_imaging_app.layout").debug("splitter restore | no splitter layout")
            self._append_workflow_log("Layout | no splitter layout available", level="warning")
            return False
        state_keys = {
            "_main_splitter": "layout/main_splitter_state",
            "_visual_splitter": "layout/visual_splitter_state",
            "_top_visual_splitter": "layout/top_visual_splitter_state",
            "_bottom_visual_splitter": "layout/bottom_visual_splitter_state",
        }
        restored_any = False
        for attr_name, key in state_keys.items():
            splitter = getattr(self, attr_name, None)
            if splitter is None:
                continue
            state = self._settings.value(key)
            if not isinstance(state, (QByteArray, bytes, bytearray)):
                continue
            blob = QByteArray(state)
            if blob.isEmpty():
                continue
            try:
                restored = bool(splitter.restoreState(blob))
            except Exception:
                restored = False
            logging.getLogger("lspr_imaging_app.layout").debug(
                "splitter restore | %s | restored=%s | size=%s",
                attr_name,
                restored,
                blob.size(),
            )
            self._append_workflow_log(
                f"Layout | {attr_name} restored={restored} size={blob.size()}",
                level="debug" if restored else "warning",
            )
            restored_any = restored_any or restored
        return restored_any

    def _restore_saved_window_state_after_show(self) -> None:
        if self._startup_restore_window_fullscreen:
            self.showFullScreen()
        elif self._startup_restore_window_maximized:
            self.showMaximized()
        else:
            self.showNormal()

    def _restore_panel_layout_preferences(self) -> None:
        self._append_workflow_log("Layout | restoring panel visibility and splitter states", level="debug")
        self._restore_default_panel_layout()
        for name, panel in self._panel_layout_panels():
            visible = self._settings_bool(f"layout/{name}_visible", True)
            panel.blockSignals(True)
            try:
                panel.setVisible(visible)
            finally:
                panel.blockSignals(False)
            self._append_workflow_log(f"Panel | restore {name} visible={visible}", level="debug")
        if not self._restore_saved_panel_layout_state():
            self._apply_default_splitter_sizes()
            self._append_workflow_log("Layout | applied default splitter sizes", level="warning")

    def _normalize_panel_layout(self) -> None:
        if not hasattr(self, "_main_splitter"):
            return
        self._main_splitter.setChildrenCollapsible(False)
        self._visual_splitter.setChildrenCollapsible(False)
        self._top_visual_splitter.setChildrenCollapsible(False)
        self._bottom_visual_splitter.setChildrenCollapsible(False)

    def _set_all_panel_visibility(self, visible: bool) -> None:
        if not hasattr(self, "_main_splitter"):
            return
        snapshot = self._panel_layout_visibility_backup
        self._append_workflow_log(f"Panel | {'show' if visible else 'hide'} all", level="debug")
        if not visible and snapshot is None:
            self._panel_layout_visibility_backup = self._capture_panel_layout_snapshot()
        self._suspend_layout_save = True
        try:
            for _name, panel in self._panel_layout_panels():
                panel.setVisible(bool(visible))
        finally:
            self._suspend_layout_save = False
        if visible:
            self._apply_panel_layout_snapshot(self._panel_layout_visibility_backup)
            self._panel_layout_visibility_backup = None
        self._save_panel_layout_preferences()
        self._save_layout_preferences()

    def _sync_panel_visibility_after_show(self) -> None:
        for name, dock in self._panel_layout_panels():
            visible = self._settings_bool(f"layout/{name}_visible", True)
            dock.blockSignals(True)
            try:
                dock.toggleViewAction().setChecked(visible)
                dock.setVisible(visible)
            finally:
                dock.blockSignals(False)
            self._append_workflow_log(f"Panel | sync {name} visible={visible}", level="debug")
        if hasattr(self, "workflow_log_section"):
            try:
                self.workflow_log_section.set_expanded(True)
            except Exception:
                pass

    def _save_panel_layout_preferences(self) -> None:
        for name, dock in self._panel_layout_panels():
            visible = bool(dock.toggleViewAction().isChecked())
            self._settings.setValue(f"layout/{name}_visible", visible)
        if hasattr(self, "_main_splitter"):
            self._settings.setValue("layout/main_splitter_state", self._main_splitter.saveState())
        if hasattr(self, "_visual_splitter"):
            self._settings.setValue("layout/visual_splitter_state", self._visual_splitter.saveState())
        if hasattr(self, "_top_visual_splitter"):
            self._settings.setValue("layout/top_visual_splitter_state", self._top_visual_splitter.saveState())
        if hasattr(self, "_bottom_visual_splitter"):
            self._settings.setValue("layout/bottom_visual_splitter_state", self._bottom_visual_splitter.saveState())

    def _on_panel_visibility_changed(self, _dock: QWidget) -> None:
        if isinstance(_dock, QWidget):
            panel_name = _dock.objectName() or _dock.windowTitle() or _dock.__class__.__name__
            self._append_workflow_log(f"Panel | visibility changed {panel_name}", level="debug")
        self._save_layout_preferences()

    @staticmethod
    def _compact_folder_label(folder: Path) -> str:
        parts = folder.parts
        if len(parts) <= 2:
            return str(folder)
        return f"...\\{parts[-2]}\\{parts[-1]}"

    def _preprocessing_path(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is None:
            return None
        return dataset.folder / "preprocessing.json"

    def _processing_profile_path(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is None:
            return None
        return dataset.folder / "processing_profile.json"

    def _dataset_folder_path(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is not None:
            return dataset.folder
        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            return None
        folder = Path(folder_text)
        return folder if folder.exists() else None

    def _load_processing_state_for_dataset(self) -> None:
        self._session_state_manager.load_processing_state_for_dataset()

    def _normalize_mask_application_state(self) -> None:
        apply_mask = bool(
            self._state.spot_detection.ignore_marked_pixels or self._state.preprocessing.flatten_background_exclude_mask
        )
        self._state.spot_detection.ignore_marked_pixels = apply_mask
        self._state.preprocessing.flatten_background_exclude_mask = apply_mask

    def _processing_state_signature(self) -> str:
        def _mask_signature(mask_settings: MaskSettings) -> tuple[object, ...]:
            histogram_mask = mask_settings.histogram_mask
            figure_mask = mask_settings.figure_mask
            return (
                mask_settings.histogram_min_value,
                mask_settings.histogram_max_value,
                round(float(mask_settings.relative_threshold_fraction), 6),
                round(float(mask_settings.relative_profile_sigma_px), 3),
                round(float(mask_settings.local_contrast_sigma_px), 3),
                round(float(mask_settings.local_contrast_z_threshold), 3),
                int(mask_settings.morphology_radius_px),
                int(mask_settings.brush_size_px),
                bool(mask_settings.histogram_enabled),
                None if histogram_mask is None else (tuple(int(v) for v in histogram_mask.shape), int(np.count_nonzero(histogram_mask))),
                bool(mask_settings.figure_enabled),
                None if figure_mask is None else (tuple(int(v) for v in figure_mask.shape), int(np.count_nonzero(figure_mask))),
            )
        payload = {
            "preprocessing": asdict(self._state.preprocessing),
            "spot_detection": asdict(self._state.spot_detection),
            "mask_settings": _mask_signature(self._state.mask),
            "detected_spots": [asdict(spot) for spot in self._state.detected_spots],
            "spot_groups": [asdict(group) for group in self._state.spot_groups],
            "chromatic_models": [asdict(model) for model in self._state.chromatic_models],
            "chromatic_landmarks": [asdict(landmark) for landmark in self._state.chromatic_landmarks],
            "file_mask_path": str(self._current_file_mask_path) if self._current_file_mask_path is not None else None,
            "file_mask_revision": int(self._external_mask_revision),
            "file_mask_shape": None if self._current_file_mask is None else tuple(int(v) for v in self._current_file_mask.shape),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _save_processing_state_for_dataset(self, *, force: bool = False, reason: str | None = None) -> None:
        self._session_state_manager.save_processing_state_for_dataset(force=force, reason=reason)

    def _export_processing_profile(self) -> None:
        self._session_state_manager.export_processing_profile()

    def _import_processing_profile(self) -> None:
        self._session_state_manager.import_processing_profile()

    def _startup_restore_timeout_seconds(self) -> int:
        return max(int(self._settings.value("startup/restore_previous_session_timeout_s", 5)), 0)

    def _set_startup_restore_timeout_seconds(self, seconds: int) -> None:
        timeout = max(int(seconds), 0)
        self._settings.setValue("startup/restore_previous_session_timeout_s", timeout)
        for value, action in getattr(self, "startup_restore_timeout_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(int(value) == timeout)
            action.blockSignals(False)

    def _schedule_spot_state_save(self) -> None:
        self._spot_state_save_timer.start()

    def _sync_image_processing_controls(self) -> None:
        self._ui_state_manager.sync_image_processing_controls()

    def _restore_visual_preferences(self) -> None:
        self._ui_state_manager.restore_visual_preferences()

    def _save_visual_preferences(self) -> None:
        self._ui_state_manager.save_visual_preferences()

    def _on_image_view_range_changed(self, *_args) -> None:
        if self._current_processed_image is None or self._showing_background_profile_main:
            return
        if self._image_view_save_timer.isActive():
            self._image_view_save_timer.stop()
        self._image_view_save_timer.start()

    def _save_control_preferences(self) -> None:
        self._ui_state_manager.save_control_preferences()

    def _restore_control_preferences(self) -> None:
        self._ui_state_manager.restore_control_preferences()

    def _read_bool_setting(self, key: str, default: bool) -> bool:
        return read_bool_setting(self._settings, key, default)

    def _read_float_setting(self, key: str, default: float) -> float:
        return read_float_setting(self._settings, key, default)

    def _normalized_odd_count(self, value: int, minimum: int, maximum: int) -> int:
        return normalized_odd_count(int(value), int(minimum), int(maximum))

    def _chromatic_feature_count_options(self) -> tuple[int, ...]:
        return (5, 15, 30)

    def _chromatic_feature_count_value(self) -> int:
        return chromatic_feature_count_value(self.chromatic_feature_count_spin.currentData(), self._chromatic_feature_count_options())

    def _set_chromatic_feature_count_value(self, value: int) -> None:
        target = int(value)
        if target not in self._chromatic_feature_count_options():
            target = 15
        index = max(self.chromatic_feature_count_spin.findData(target), 0)
        self.chromatic_feature_count_spin.blockSignals(True)
        self.chromatic_feature_count_spin.setCurrentIndex(index)
        self.chromatic_feature_count_spin.blockSignals(False)

    def _chromatic_subpixel_precision_options(self) -> tuple[int, ...]:
        return (1, 4, 9)

    def _chromatic_subpixel_precision_value(self) -> int:
        return chromatic_subpixel_precision_value(self.chromatic_subpixel_precision_combo.currentData())

    def _set_chromatic_subpixel_precision_value(self, value: int) -> None:
        target = int(value)
        if target not in self._chromatic_subpixel_precision_options():
            target = 4
        index = max(self.chromatic_subpixel_precision_combo.findData(target), 0)
        self.chromatic_subpixel_precision_combo.blockSignals(True)
        self.chromatic_subpixel_precision_combo.setCurrentIndex(index)
        self.chromatic_subpixel_precision_combo.blockSignals(False)

    def _sampled_wavelengths(self, wavelengths_nm: list[float], sample_count: int) -> list[float]:
        return _sampled_wavelengths(list(wavelengths_nm), int(sample_count))

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

    def _update_apply_button_labels(self) -> None:
        mask_applied = bool(self.ignore_marked_check.isChecked())
        background_applied = bool(self.background_removal_link.isChecked())
        chromatic_applied = bool(self.chromatic_apply_check.isChecked())
        self.ignore_marked_check.setText("Mask applied" if mask_applied else "Apply mask")
        self.chromatic_apply_check.setIcon(self._make_link_toggle_icon(chromatic_applied))
        self.background_removal_link.setText(
            "Background removal applied" if background_applied else "Apply background removal"
        )
        self.background_removal_link.setIcon(self._make_link_toggle_icon(background_applied))

    def _reference_image_key(self) -> tuple[int, float] | None:
        if str(self._state.preprocessing.reference_mode or "auto") != "manual":
            return self._auto_reference_image_key_for_frame(self._current_frame())
        wavelength = self._state.preprocessing.reference_wavelength_nm
        if wavelength is None:
            return None
        return int(self._state.preprocessing.reference_frame_index), float(wavelength)

    def _reference_contrast_score(self, record_path: Path) -> float:
        cache_key = str(record_path)
        cached = self._reference_contrast_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            image = load_image_array(str(record_path)).astype(np.float32, copy=False)
        except Exception:
            score = float("-inf")
            self._reference_contrast_cache[cache_key] = score
            return score
        finite = image[np.isfinite(image)]
        if finite.size == 0:
            score = float("-inf")
        else:
            lower = float(np.quantile(finite, 0.02))
            upper = float(np.quantile(finite, 0.98))
            score = upper - lower
        self._reference_contrast_cache[cache_key] = score
        return score

    def _auto_reference_image_key_for_frame(self, frame: int | None) -> tuple[int, float] | None:
        if frame is None:
            return None
        best_key: tuple[int, float] | None = None
        best_score = float("-inf")
        for wavelength in self._wavelength_values:
            key = (int(frame), float(wavelength))
            record = self._record_map.get(key)
            if record is None:
                continue
            score = self._reference_contrast_score(record.path)
            if score > best_score:
                best_score = score
                best_key = key
        return best_key

    def _reference_record(self):
        key = self._reference_image_key()
        if key is None:
            return None
        return self._record_map.get(key)

    def _reference_image_key_for_record_path(self, record_path: Path) -> tuple[int, float] | None:
        if str(self._state.preprocessing.reference_mode or "auto") == "manual":
            return self._reference_image_key()
        target_key = self._image_key_for_record_path(record_path)
        if target_key is None:
            return self._reference_image_key()
        return self._auto_reference_image_key_for_frame(int(target_key[0]))

    def _reference_record_for_record_path(self, record_path: Path):
        key = self._reference_image_key_for_record_path(record_path)
        if key is None:
            return None
        return self._record_map.get(key)

    def _is_reference_image_key(self, image_key: tuple[int, float] | None) -> bool:
        reference_key = self._reference_image_key()
        return image_key is not None and reference_key is not None and image_key == reference_key

    def _is_current_reference_image(self) -> bool:
        return self._is_reference_image_key(self._current_image_key)

    def _chromatic_model_for_image_key(self, image_key: tuple[int, float] | None) -> ChromaticTransformModel | None:
        if image_key is None:
            return None
        frame, wavelength = image_key
        for model in self._state.chromatic_models:
            if int(model.frame_index) == int(frame) and abs(float(model.wavelength_nm) - float(wavelength)) < 1e-6:
                return model
        return None

    def _chromatic_affine_for_image_key(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        if image_key is None or self._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        if not self._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self._chromatic_model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def _chromatic_affine_for_image_key_any(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        if image_key is None or self._is_reference_image_key(image_key):
            return identity_affine_matrix() if image_key is not None else None
        model = self._chromatic_model_for_image_key(image_key)
        if model is None:
            return None
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def _chromatic_signature_for_image_key(self, image_key: tuple[int, float] | None) -> tuple[object, ...] | None:
        if image_key is None or not self._state.preprocessing.chromatic_correction_enabled:
            return None
        model = self._chromatic_model_for_image_key(image_key)
        if model is None:
            return None
        return (
            int(model.frame_index),
            round(float(model.wavelength_nm), 6),
            tuple(tuple(round(float(value), 6) for value in row) for row in model.affine_matrix),
        )

    def _display_spots(self, image_key: tuple[int, float] | None = None) -> list[DetectedSpot]:
        target_key = image_key if image_key is not None else self._current_image_key
        if target_key is None:
            return self._state.detected_spots
        if self._is_reference_image_key(target_key):
            return self._state.detected_spots
        if self._current_processed_image is None and image_key is None:
            return self._state.detected_spots
        signature = (
            target_key,
            self._spot_signature(self._state.detected_spots),
            self._chromatic_signature_for_image_key(target_key),
            None if self._current_processed_image is None else self._current_processed_image.shape[:2],
        )
        if self._display_spot_cache_signature == signature and self._display_spot_cache_value is not None:
            return self._display_spot_cache_value
        affine_matrix = self._chromatic_affine_for_image_key(target_key)
        if affine_matrix is None:
            transformed = self._state.detected_spots
        else:
            clamp_shape = self._current_processed_image.shape[:2] if self._current_processed_image is not None else None
            transformed = transform_spots_affine(self._state.detected_spots, affine_matrix, clamp_shape=clamp_shape)
        self._display_spot_cache_signature = signature
        self._display_spot_cache_value = transformed
        return transformed

    def _spots_for_preprocessing(self, image_key: tuple[int, float] | None) -> list[DetectedSpot]:
        if image_key is None or self._is_reference_image_key(image_key):
            return self._state.detected_spots
        if not self._state.preprocessing.chromatic_correction_enabled:
            return self._state.detected_spots
        affine_matrix = self._chromatic_affine_for_image_key(image_key)
        if affine_matrix is None:
            return self._state.detected_spots
        return transform_spots_affine(self._state.detected_spots, affine_matrix)

    def _spot_curve_points(
        self,
        source_spot: DetectedSpot,
        display_spot: DetectedSpot,
        radius_px: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        affine_matrix = self._chromatic_affine_for_image_key(self._current_image_key)
        if affine_matrix is None or self._is_current_reference_image():
            theta = self._spot_overlay_theta
            xs = display_spot.center_x + float(radius_px) * np.cos(theta)
            ys = display_spot.center_y + float(radius_px) * np.sin(theta)
            return xs, ys
        return transformed_circle_points(
            (float(source_spot.center_x), float(source_spot.center_y)),
            float(radius_px),
            affine_matrix,
            self._spot_overlay_theta,
        )

    def _set_view_overlay_visibility(
        self,
        *,
        spots_visible: bool,
        rings_visible: bool,
        mask_visible: bool,
        reference_points_visible: bool,
        highlight_visible: bool,
    ) -> None:
        self._spots_visible = bool(spots_visible)
        self._rings_visible = bool(rings_visible)
        self._mask_visible = bool(mask_visible)
        self._reference_points_visible = bool(reference_points_visible)
        self._highlight_visible = bool(highlight_visible)
        self.show_spots_check.blockSignals(True)
        self.show_rings_check.blockSignals(True)
        self.show_mask_check.blockSignals(True)
        self.show_reference_points_check.blockSignals(True)
        self.show_highlight_check.blockSignals(True)
        self.show_spots_check.setChecked(self._spots_visible)
        self.show_rings_check.setChecked(self._rings_visible)
        self.show_mask_check.setChecked(self._mask_visible)
        self.show_reference_points_check.setChecked(self._reference_points_visible)
        self.show_highlight_check.setChecked(self._highlight_visible)
        self.show_spots_check.blockSignals(False)
        self.show_rings_check.blockSignals(False)
        self.show_mask_check.blockSignals(False)
        self.show_reference_points_check.blockSignals(False)
        self.show_highlight_check.blockSignals(False)
        self._refresh_view_toggle_icons()
        self._update_spot_overlays()
        self._update_ignore_mask_overlay()
        self._update_selected_intensity_overlay()
        self._update_landmark_overlays()

    def _enter_chromatic_setup_mode(self) -> None:
        if self._chromatic_setup_saved_visibility is None:
            self._chromatic_setup_saved_visibility = (
                bool(self._spots_visible),
                bool(self._rings_visible),
                bool(self._mask_visible),
                bool(self._reference_points_visible),
                bool(self._highlight_visible),
            )
        self._chromatic_setup_active = True
        self._set_view_overlay_visibility(
            spots_visible=False,
            rings_visible=False,
            mask_visible=False,
            reference_points_visible=True,
            highlight_visible=False,
        )

    def _leave_chromatic_setup_mode(self) -> None:
        self._chromatic_setup_active = False
        if self._chromatic_setup_saved_visibility is not None:
            spots_visible, rings_visible, mask_visible, reference_points_visible, highlight_visible = self._chromatic_setup_saved_visibility
            self._set_view_overlay_visibility(
                spots_visible=spots_visible,
                rings_visible=rings_visible,
                mask_visible=mask_visible,
                reference_points_visible=reference_points_visible,
                highlight_visible=highlight_visible,
            )
        self._chromatic_setup_saved_visibility = None
        self._chromatic_pending_view_ranges = None

    def _capture_chromatic_view_ranges(self) -> None:
        if not self._chromatic_setup_active or self._current_processed_image is None:
            self._chromatic_pending_view_ranges = None
            return
        x_range, y_range = self.image_plot.vb.viewRange()
        self._chromatic_pending_view_ranges = (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )

    def _capture_pending_image_view_ranges(self, *, preserve_view: bool = False) -> None:
        if self._current_processed_image is None:
            self._pending_image_view_ranges = None
            self._pending_image_view_crop_offset = None
            self._pending_image_view_preserve = False
            return
        x_range, y_range = self.image_plot.vb.viewRange()
        self._pending_image_view_ranges = (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )
        self._pending_image_view_preserve = bool(preserve_view)
        crop = self._state.preprocessing.crop
        if self._active_tool == "crop" and crop.enabled and bool(self._state.preprocessing.image_tools_enabled):
            self._pending_image_view_crop_offset = (float(crop.x), float(crop.y))
        else:
            self._pending_image_view_crop_offset = None

    def _set_clamped_image_view_ranges(
        self,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> bool:
        if self._current_processed_image is None:
            return False
        image_height, image_width = self._current_processed_image.shape[:2]
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        if view_width >= float(image_width):
            target_x_range = (0.0, float(image_width))
        else:
            shift_x = 0.0
            if x_range[0] < 0.0:
                shift_x = -float(x_range[0])
            elif x_range[1] > float(image_width):
                shift_x = float(image_width) - float(x_range[1])
            target_x_range = (float(x_range[0] + shift_x), float(x_range[1] + shift_x))
        if view_height >= float(image_height):
            target_y_range = (0.0, float(image_height))
        else:
            shift_y = 0.0
            if y_range[0] < 0.0:
                shift_y = -float(y_range[0])
            elif y_range[1] > float(image_height):
                shift_y = float(image_height) - float(y_range[1])
            target_y_range = (float(y_range[0] + shift_y), float(y_range[1] + shift_y))
        self.image_plot.vb.setRange(xRange=target_x_range, yRange=target_y_range, padding=0.0)
        return True

    def _set_unclamped_image_view_ranges(
        self,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> bool:
        if self._current_processed_image is None:
            return False
        self.image_plot.vb.setRange(
            xRange=(float(x_range[0]), float(x_range[1])),
            yRange=(float(y_range[0]), float(y_range[1])),
            padding=0.0,
        )
        return True

    def _restore_pending_image_view_after_load(self) -> bool:
        previous_ranges = self._pending_image_view_ranges
        crop_offset = self._pending_image_view_crop_offset
        preserve_view = self._pending_image_view_preserve
        self._pending_image_view_ranges = None
        self._pending_image_view_crop_offset = None
        self._pending_image_view_preserve = False
        if previous_ranges is None:
            return False
        if preserve_view:
            return self._set_unclamped_image_view_ranges(previous_ranges[0], previous_ranges[1])
        if crop_offset is not None:
            return self._set_unclamped_image_view_ranges(
                (previous_ranges[0][0] - crop_offset[0], previous_ranges[0][1] - crop_offset[0]),
                (previous_ranges[1][0] - crop_offset[1], previous_ranges[1][1] - crop_offset[1]),
            )
        return self._set_clamped_image_view_ranges(previous_ranges[0], previous_ranges[1])

    def _restore_saved_image_view_after_load(self) -> bool:
        if self._current_processed_image is None or self._showing_background_profile_main:
            return False
        x_min_raw = self._settings.value("visual/image_view_x_min", None)
        x_max_raw = self._settings.value("visual/image_view_x_max", None)
        y_min_raw = self._settings.value("visual/image_view_y_min", None)
        y_max_raw = self._settings.value("visual/image_view_y_max", None)
        if None in (x_min_raw, x_max_raw, y_min_raw, y_max_raw):
            return False
        try:
            x_range = (float(x_min_raw), float(x_max_raw))
            y_range = (float(y_min_raw), float(y_max_raw))
        except (TypeError, ValueError):
            return False
        return self._set_clamped_image_view_ranges(x_range, y_range)

    def _restore_chromatic_view_after_load(self) -> bool:
        if not self._chromatic_setup_active:
            return False
        previous_ranges = self._chromatic_pending_view_ranges
        self._chromatic_pending_view_ranges = None
        if previous_ranges is None:
            return False
        return self._set_clamped_image_view_ranges(previous_ranges[0], previous_ranges[1])

    def _default_chromatic_feature_points(self, image_shape: tuple[int, int], feature_count: int) -> dict[int, tuple[float, float]]:
        return default_landmark_anchors(image_shape, feature_count)

    def _update_chromatic_summary(self) -> None:
        if not self._state.dataset:
            self.chromatic_summary.setText("No dataset loaded.")
            self.chromatic_progress_label.setText("No dataset loaded.")
            self.chromatic_transform_button.setEnabled(False)
            self.chromatic_apply_check.setEnabled(False)
            self.chromatic_section.set_apply_enabled(False)
            self._set_section_applied(self.chromatic_section, bool(self._state.preprocessing.chromatic_correction_enabled))
            self.chromatic_transform_button.setPixmap(self._chromatic_transform_icon(False).pixmap(24, 24))
            self.chromatic_transform_button.setToolTip(
                "Estimate chromatic transforms."
            )
            return
        model_count = len(self._state.chromatic_models)
        sample_keys = self._chromatic_sample_image_keys()
        feature_ids = self._expected_chromatic_feature_ids()
        filled_samples = 0
        current_index = self._current_chromatic_sample_index()
        for sample_key in sample_keys:
            sample_marks = {
                int(mark.landmark_id)
                for mark in self._state.chromatic_landmarks
                if int(mark.frame_index) == int(sample_key[0]) and abs(float(mark.wavelength_nm) - float(sample_key[1])) < 1e-6
            }
            if all(feature_id in sample_marks for feature_id in feature_ids):
                filled_samples += 1
        can_estimate = bool(sample_keys) and filled_samples == len(sample_keys) and len(feature_ids) >= 2
        controls_locked = self._chromatic_auto_running
        can_apply_models = model_count > 0 and not controls_locked
        can_toggle_transform = (can_estimate or model_count > 0) and not controls_locked
        self.chromatic_transform_button.setEnabled(can_toggle_transform)
        self.chromatic_transform_button.setPixmap(self._chromatic_transform_icon(model_count > 0).pixmap(24, 24))
        if model_count > 0:
            self.chromatic_transform_button.setToolTip("Clear saved chromatic transforms.")
        elif can_estimate:
            self.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        else:
            self.chromatic_transform_button.setToolTip("Estimate chromatic transforms.")
        self.chromatic_apply_check.setEnabled(can_apply_models)
        self.chromatic_section.set_apply_enabled(can_apply_models)
        self._set_section_applied(self.chromatic_section, bool(self._state.preprocessing.chromatic_correction_enabled))
        self.chromatic_summary.setText("Radial setup ready." if self._chromatic_setup_active else "Radial workflow ready.")
        if sample_keys and current_index is not None:
            current_key = sample_keys[current_index]
            marked_current = len(
                {
                    int(mark.landmark_id)
                    for mark in self._state.chromatic_landmarks
                    if int(mark.frame_index) == int(current_key[0]) and abs(float(mark.wavelength_nm) - float(current_key[1])) < 1e-6
                }
            )
            self.chromatic_progress_label.setText(
                f"Sample image {current_index + 1}/{len(sample_keys)} at {current_key[1]:g} nm | "
                f"reference points marked: {marked_current}/{len(feature_ids)} | "
                f"completed sample images: {filled_samples}/{len(sample_keys)}"
            )
        elif sample_keys:
            middle_index = len(sample_keys) // 2
            self.chromatic_progress_label.setText(
                f"Procedure ready: {len(sample_keys)} sampled wavelengths, middle reference at {sample_keys[middle_index][1]:g} nm."
            )
        else:
            self.chromatic_progress_label.setText("Edit the radial workflow to choose sampled wavelengths.")
        if model_count == 0:
            return
        rmses = [float(model.rmse_px) for model in self._state.chromatic_models if model.rmse_px > 0.0]
        rmse_text = f"{float(np.mean(rmses)):.2f} px" if rmses else "0.00 px"
        self.chromatic_summary.setText("Transforms estimated.")
        self.chromatic_progress_label.setText(
            f"Transforms ready for {model_count} image(s) | mean fit RMSE: {rmse_text} | "
            f"{'applied' if self._state.preprocessing.chromatic_correction_enabled else 'ready to apply'}"
        )

    def _update_chromatic_control_state(self) -> None:
        self._ui_state_manager.update_chromatic_control_state()

    def _chromatic_auto_detection_applied(self) -> bool:
        return bool(self._state.chromatic_landmarks) and not self._chromatic_auto_running and not self.chromatic_start_button.isChecked()

    def _on_chromatic_reference_points_all_toggled(self, checked: bool) -> None:
        self._chromatic_reference_points_all_visible = bool(checked)
        self._update_landmark_overlays()
        self._schedule_processing_state_save()

    def _on_chromatic_landmark_id_changed(self, value: int) -> None:
        feature_id = min(max(int(value), 1), len(self._expected_chromatic_feature_ids()))
        self._chromatic_landmark_marker_id = feature_id
        self._selected_landmark_id = feature_id
        self._select_chromatic_feature(feature_id, center_view=True)

    def _on_chromatic_landmark_tool_toggled(self, checked: bool) -> None:
        if checked:
            if not self._is_chromatic_sample_image_key(self._current_image_key):
                self.chromatic_landmark_mark_button.blockSignals(True)
                self.chromatic_landmark_mark_button.setChecked(False)
                self.chromatic_landmark_mark_button.blockSignals(False)
                self._set_status_text("Use Edit and navigate to a sampled wavelength image before editing chromatic reference points.")
                return
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            self.spot_edit_action.blockSignals(True)
            self.spot_edit_action.setChecked(False)
            self.spot_edit_action.blockSignals(False)
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self._active_tool = "chromatic_landmark"
            self._selected_landmark_id = self._chromatic_landmark_marker_id
            if hasattr(self, "image_panel"):
                self.image_panel.raise_()
            if hasattr(self, "image_view") and self.image_view is not None:
                self.image_view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                viewport = self.image_view.viewport()
                if viewport is not None:
                    viewport.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self._set_status_text(
                f"Chromatic reference point editor active. Click to place point {self._chromatic_landmark_marker_id}, "
                "drag to adjust, PageUp/PageDown to switch reference points."
            )
        elif self._active_tool == "chromatic_landmark":
            self._active_tool = None
        self._update_landmark_overlays()

    def _expected_chromatic_feature_ids(self) -> list[int]:
        return list(range(1, max(int(self._state.preprocessing.chromatic_feature_count), 1) + 1))

    def _chromatic_sample_image_keys(self) -> list[tuple[int, float]]:
        reference_key = self._reference_image_key()
        if reference_key is None:
            return []
        frame = int(reference_key[0])
        return [(frame, wavelength) for wavelength in _sampled_wavelengths(self._wavelength_values, self._state.preprocessing.chromatic_sample_image_count)]

    def _is_chromatic_sample_image_key(self, image_key: tuple[int, float] | None) -> bool:
        return image_key is not None and image_key in self._chromatic_sample_image_keys()

    def _current_chromatic_sample_index(self) -> int | None:
        if self._current_image_key is None:
            return None
        sample_keys = self._chromatic_sample_image_keys()
        try:
            return sample_keys.index(self._current_image_key)
        except ValueError:
            return None

    def _current_image_landmarks(self) -> list[ChromaticLandmarkObservation]:
        key = self._current_image_key
        if key is None:
            return []
        frame, wavelength = key
        return [
            mark
            for mark in self._state.chromatic_landmarks
            if int(mark.frame_index) == int(frame) and abs(float(mark.wavelength_nm) - float(wavelength)) < 1e-6
        ]

    def _current_landmark(self, landmark_id: int) -> ChromaticLandmarkObservation | None:
        for mark in self._current_image_landmarks():
            if int(mark.landmark_id) == int(landmark_id):
                return mark
        return None

    def _find_landmark_id_at(self, point: tuple[float, float]) -> int | None:
        nearest_id: int | None = None
        nearest_distance = float("inf")
        for mark in self._current_image_landmarks():
            distance = hypot(point[0] - float(mark.x_px), point[1] - float(mark.y_px))
            if distance <= 10.0 and distance < nearest_distance:
                nearest_distance = distance
                nearest_id = int(mark.landmark_id)
        return nearest_id

    def _upsert_current_landmark(
        self,
        landmark_id: int,
        point: tuple[float, float],
        *,
        clear_models: bool,
    ) -> bool:
        key = self._current_image_key
        if key is None:
            return False
        frame, wavelength = key
        updated = False
        for mark in self._state.chromatic_landmarks:
            if (
                int(mark.landmark_id) == int(landmark_id)
                and int(mark.frame_index) == int(frame)
                and abs(float(mark.wavelength_nm) - float(wavelength)) < 1e-6
            ):
                mark.x_px = float(point[0])
                mark.y_px = float(point[1])
                updated = True
                break
        if not updated:
            self._state.chromatic_landmarks.append(
                ChromaticLandmarkObservation(
                    landmark_id=int(landmark_id),
                    frame_index=int(frame),
                    wavelength_nm=float(wavelength),
                    x_px=float(point[0]),
                    y_px=float(point[1]),
                )
            )
        self._selected_landmark_id = int(landmark_id)
        self._chromatic_landmark_marker_id = int(landmark_id)
        if clear_models:
            self._finalize_chromatic_landmark_edit()
        else:
            self._update_landmark_overlays()
        return updated

    def _set_current_landmark(self, point: tuple[float, float], *, auto_advance: bool = False) -> None:
        key = self._current_image_key
        if key is None:
            self._set_status_text("No image is selected for reference-point marking.")
            return
        self._push_undo_point("Chromatic landmarks")
        landmark_id = int(self._chromatic_landmark_marker_id)
        self._upsert_current_landmark(landmark_id, point, clear_models=True)
        if auto_advance:
            expected_ids = self._expected_chromatic_feature_ids()
            next_id = landmark_id + 1 if landmark_id < len(expected_ids) else landmark_id
            self.chromatic_landmark_id_spin.blockSignals(True)
            self.chromatic_landmark_id_spin.setValue(next_id)
            self.chromatic_landmark_id_spin.blockSignals(False)
            self._chromatic_landmark_marker_id = next_id
            self._selected_landmark_id = landmark_id
        self._set_status_text(
            f"Stored reference point {landmark_id} at {key[1]:g} nm frame {key[0]}."
        )

    def _clear_chromatic_landmarks(self, *, push_undo: bool = True) -> None:
        if not self._state.chromatic_landmarks:
            return
        if push_undo:
            self._push_undo_point("Chromatic landmarks")
        self._state.chromatic_landmarks.clear()
        self._state.chromatic_models.clear()
        self._state.preprocessing.chromatic_correction_enabled = False
        self._chromatic_reference_points_all_visible = False
        self.chromatic_apply_check.blockSignals(True)
        self.chromatic_apply_check.setChecked(False)
        self.chromatic_apply_check.blockSignals(False)
        if hasattr(self, "chromatic_reference_points_all_button"):
            self.chromatic_reference_points_all_button.blockSignals(True)
            self.chromatic_reference_points_all_button.setChecked(False)
            self.chromatic_reference_points_all_button.blockSignals(False)
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_spot_overlays()
        self._schedule_histogram_refresh()
        self._update_landmark_overlays()
        self._update_chromatic_summary()
        self._schedule_processing_state_save()
        self._selected_landmark_id = None
        self._set_status_text("Cleared chromatic landmarks and models.")

    def _set_current_frame_and_wavelength(self, frame: int, wavelength: float) -> None:
        self._capture_chromatic_view_ranges()
        if frame in self._frame_values:
            frame_index = self._frame_values.index(frame)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(frame_index)
            self.frame_slider.blockSignals(False)
        if self._wavelength_values:
            wavelength_index = min(
                range(len(self._wavelength_values)),
                key=lambda idx: abs(self._wavelength_values[idx] - float(wavelength)),
            )
            self.wavelength_slider.blockSignals(True)
            self.wavelength_slider.setValue(wavelength_index)
            self.wavelength_slider.blockSignals(False)
        self._sync_analysis_plot_cursors()
        self._schedule_image_refresh()

    def _start_chromatic_workflow(self) -> None:
        self._chromatic_controller.start_workflow()

    def _chromatic_sample_payload(self) -> list[tuple[int, float, str]]:
        payload: list[tuple[int, float, str]] = []
        for frame, wavelength in self._chromatic_sample_image_keys():
            record = self._record_map.get((frame, wavelength))
            if record is None:
                continue
            payload.append((int(frame), float(wavelength), str(record.path)))
        return payload

    def _auto_detect_chromatic_landmarks(self, *, push_undo: bool = True) -> None:
        self._chromatic_controller.auto_detect_landmarks(push_undo=push_undo)

    def _navigate_chromatic_sample(self, direction: int) -> bool:
        sample_keys = self._chromatic_sample_image_keys()
        if not sample_keys:
            return False
        current_index = self._current_chromatic_sample_index()
        if current_index is None:
            if self._current_image_key is None:
                current_index = 0
            else:
                current_wavelength = float(self._current_image_key[1])
                current_index = min(
                    range(len(sample_keys)),
                    key=lambda idx: abs(float(sample_keys[idx][1]) - current_wavelength),
                )
        target_index = min(max(current_index + int(direction), 0), len(sample_keys) - 1)
        target_frame, target_wavelength = sample_keys[target_index]
        self._set_current_frame_and_wavelength(target_frame, target_wavelength)
        return True

    def _navigate_wavelength_image(self, direction: int) -> bool:
        frame = self._current_frame()
        wavelength = self._current_wavelength()
        if frame is None or wavelength is None or not self._wavelength_values:
            return False
        try:
            current_index = self._wavelength_values.index(float(wavelength))
        except ValueError:
            current_index = min(
                range(len(self._wavelength_values)),
                key=lambda idx: abs(float(self._wavelength_values[idx]) - float(wavelength)),
            )
        target_index = min(max(current_index + int(direction), 0), len(self._wavelength_values) - 1)
        target_wavelength = float(self._wavelength_values[target_index])
        if abs(target_wavelength - float(wavelength)) < 1e-9:
            return False
        self._set_current_frame_and_wavelength(int(frame), target_wavelength)
        return True

    def _navigate_frame_image(self, direction: int) -> bool:
        if not self._frame_values:
            return False
        current_index = self.frame_slider.value()
        target_index = min(max(current_index + int(direction), 0), len(self._frame_values) - 1)
        if target_index == current_index:
            return False
        self.frame_slider.setValue(target_index)
        return True

    def _on_chromatic_sample_count_changed(self, value: int) -> None:
        sample_minimum = 1 if len(self._wavelength_values) <= 1 else 3
        normalized = _normalized_odd_count(
            int(value),
            sample_minimum,
            min(max(len(self._wavelength_values), sample_minimum), 7),
        )
        if normalized != int(value):
            self.chromatic_sample_count_spin.blockSignals(True)
            self.chromatic_sample_count_spin.setValue(normalized)
            self.chromatic_sample_count_spin.blockSignals(False)
        self._state.preprocessing.chromatic_sample_image_count = normalized
        self._update_chromatic_summary()
        self._schedule_processing_state_save()

    def _on_chromatic_feature_count_changed(self, value: int) -> None:
        normalized = self._chromatic_feature_count_value()
        if normalized != self._state.preprocessing.chromatic_feature_count:
            self._state.preprocessing.chromatic_feature_count = normalized
        max_feature = len(self._expected_chromatic_feature_ids())
        self._chromatic_landmark_marker_id = min(self._chromatic_landmark_marker_id, max_feature)
        self._selected_landmark_id = None if self._selected_landmark_id is None else min(self._selected_landmark_id, max_feature)
        self.chromatic_landmark_id_spin.blockSignals(True)
        self.chromatic_landmark_id_spin.setMaximum(max_feature)
        self.chromatic_landmark_id_spin.setValue(self._chromatic_landmark_marker_id)
        self.chromatic_landmark_id_spin.blockSignals(False)
        self._update_chromatic_summary()
        self._update_landmark_overlays()
        self._schedule_processing_state_save()

    def _on_chromatic_subpixel_precision_changed(self, _value: int) -> None:
        normalized = self._chromatic_subpixel_precision_value()
        if normalized != int(getattr(self._state.preprocessing, "chromatic_subpixel_precision", 4)):
            self._state.preprocessing.chromatic_subpixel_precision = normalized
        self._update_chromatic_summary()
        self._schedule_processing_state_save()

    def _seed_chromatic_landmarks_for_current_image(self) -> None:
        if self._chromatic_auto_running:
            return
        image_key = self._current_image_key
        if not self._is_chromatic_sample_image_key(image_key):
            return
        assert image_key is not None
        expected_ids = self._expected_chromatic_feature_ids()
        existing_ids = {int(mark.landmark_id) for mark in self._current_image_landmarks()}
        missing_ids = [feature_id for feature_id in expected_ids if feature_id not in existing_ids]
        if not missing_ids:
            return
        sample_keys = self._chromatic_sample_image_keys()
        current_index = sample_keys.index(image_key)
        candidate_keys: list[tuple[int, float]] = []
        for index in range(current_index - 1, -1, -1):
            candidate_keys.append(sample_keys[index])
        reference_key = self._reference_image_key()
        if reference_key is not None and reference_key not in candidate_keys and reference_key != image_key:
            candidate_keys.append(reference_key)
        for index in range(current_index + 1, len(sample_keys)):
            candidate_keys.append(sample_keys[index])
        source_marks: dict[int, tuple[float, float]] | None = None
        for candidate_key in candidate_keys:
            marks = {
                int(mark.landmark_id): (float(mark.x_px), float(mark.y_px))
                for mark in self._state.chromatic_landmarks
                if int(mark.frame_index) == int(candidate_key[0])
                and abs(float(mark.wavelength_nm) - float(candidate_key[1])) < 1e-6
            }
            if any(feature_id in marks for feature_id in missing_ids):
                source_marks = marks
                break
        if source_marks is None and self._current_processed_image is not None:
            current_marks = self._current_image_landmarks()
            if not current_marks:
                source_marks = self._default_chromatic_feature_points(
                    self._current_processed_image.shape[:2],
                    len(expected_ids),
                )
        if source_marks is None:
            return
        changed = False
        for feature_id in missing_ids:
            point = source_marks.get(feature_id)
            if point is None:
                continue
            self._upsert_current_landmark(feature_id, point, clear_models=False)
            changed = True
        if changed:
            self._finalize_chromatic_landmark_edit()
            self._set_status_text(
                f"Seeded missing reference points for {image_key[1]:g} nm from the nearest marked sample image."
            )

    def _finalize_chromatic_landmark_edit(self, *, status_text: str | None = None) -> None:
        had_models = bool(self._state.chromatic_models)
        if had_models:
            self._state.chromatic_models.clear()
            self._state.preprocessing.chromatic_correction_enabled = False
            self.chromatic_apply_check.blockSignals(True)
            self.chromatic_apply_check.setChecked(False)
            self.chromatic_apply_check.blockSignals(False)
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_chromatic_summary()
        self._update_spot_overlays()
        self._update_ignore_mask_overlay()
        if had_models:
            self._schedule_histogram_refresh()
        self._update_landmark_overlays()
        self._schedule_processing_state_save()
        if status_text:
            self._set_status_text(status_text)

    def _sync_current_chromatic_feature_selection(self) -> None:
        if not self._is_chromatic_sample_image_key(self._current_image_key):
            return
        existing_ids = {int(mark.landmark_id) for mark in self._current_image_landmarks()}
        for feature_id in self._expected_chromatic_feature_ids():
            if feature_id not in existing_ids:
                self._select_chromatic_feature(feature_id, center_view=False)
                return
        if self._selected_landmark_id is not None:
            self._select_chromatic_feature(int(self._selected_landmark_id), center_view=False)

    def _update_reference_controls(self) -> None:
        self._ui_state_manager.update_reference_controls()

    def _set_reference_mode(self, mode: str) -> None:
        normalized_mode = "manual" if str(mode).lower() == "manual" else "auto"
        combo_index = max(self.reference_mode_combo.findData(normalized_mode), 0)
        if self.reference_mode_combo.currentData() == normalized_mode:
            if normalized_mode == "auto":
                self._sync_auto_reference_to_current_frame()
            else:
                self._update_reference_controls()
                self._update_reference_summary()
            return
        self.reference_mode_combo.blockSignals(True)
        self.reference_mode_combo.setCurrentIndex(combo_index)
        self.reference_mode_combo.blockSignals(False)
        self._on_reference_mode_changed()

    def _update_reference_summary(self) -> None:
        mode = str(self._state.preprocessing.reference_mode or "auto")
        ref_key = self._reference_image_key()
        if ref_key is None:
            self.reference_summary.setText("Reference: not set.")
            self.reference_wavelength_status_label.setText("Wavelength: -")
            self.reference_frame_status_label.setText("Frame: -")
            self.reference_method_status_label.setText("Method: -")
            self._update_reference_navigation_styles()
            self._update_reference_star_overlay()
            return
        ref_frame, ref_wavelength = ref_key
        current_frame = self._current_frame()
        current_wavelength = self._current_wavelength()
        wavelength_active = current_wavelength is not None and abs(float(current_wavelength) - float(ref_wavelength)) < 1e-6
        frame_active = current_frame is not None and int(current_frame) == int(ref_frame)
        method_text = "Auto" if mode != "manual" else "Manual"
        self.reference_summary.setText(f"Reference: {method_text.lower()} | {ref_wavelength:g} nm | frame {ref_frame}")
        self.reference_wavelength_status_label.setText(f"Wavelength: {ref_wavelength:g} nm")
        self.reference_frame_status_label.setText(f"Frame: {ref_frame}")
        self.reference_method_status_label.setText(f"Method: {method_text}")
        self.reference_wavelength_status_label.setStyleSheet(
            f"color: {'#84cc16' if wavelength_active else '#f8fafc'}; font-weight: 600;"
        )
        self.reference_frame_status_label.setStyleSheet(
            f"color: {'#facc15' if frame_active else '#f8fafc'}; font-weight: 600;"
        )
        self.reference_method_status_label.setStyleSheet(
            "color: #84cc16; font-weight: 600;"
        )
        self._update_reference_navigation_styles()
        self._update_reference_star_overlay()

    def _on_reference_mode_changed(self) -> None:
        mode = str(self.reference_mode_combo.currentData() or "auto")
        if mode == str(self._state.preprocessing.reference_mode or "auto"):
            self._update_reference_controls()
            self._update_reference_summary()
            return
        self._push_undo_point("Reference image")
        self._state.preprocessing.reference_mode = mode
        if mode == "manual":
            if self._state.preprocessing.reference_wavelength_nm is None:
                self._set_current_reference_from_view(push_undo=False, save=False)
        else:
            self._sync_auto_reference_to_current_frame()
        self._state.chromatic_models.clear()
        self._state.chromatic_landmarks.clear()
        self._state.preprocessing.chromatic_correction_enabled = False
        self.chromatic_apply_check.blockSignals(True)
        self.chromatic_apply_check.setChecked(False)
        self.chromatic_apply_check.blockSignals(False)
        self._update_reference_controls()
        self._update_reference_summary()
        self._update_chromatic_summary()
        self._update_landmark_overlays()
        self._schedule_image_refresh()
        self._schedule_processing_state_save()

    def _set_current_reference_from_view(self, *, push_undo: bool = True, save: bool = True) -> None:
        frame = self._current_frame()
        wavelength = self._current_wavelength()
        if frame is None or wavelength is None:
            self._set_status_text("No image is selected for manual reference.")
            return
        if push_undo:
            self._push_undo_point("Reference image")
        self._state.preprocessing.reference_mode = "manual"
        self._state.preprocessing.reference_frame_index = int(frame)
        self._state.preprocessing.reference_wavelength_nm = float(wavelength)
        self._state.chromatic_models.clear()
        self._state.chromatic_landmarks.clear()
        self._state.preprocessing.chromatic_correction_enabled = False
        self.chromatic_apply_check.blockSignals(True)
        self.chromatic_apply_check.setChecked(False)
        self.chromatic_apply_check.blockSignals(False)
        self.reference_mode_combo.blockSignals(True)
        self.reference_mode_combo.setCurrentIndex(max(self.reference_mode_combo.findData("manual"), 0))
        self.reference_mode_combo.blockSignals(False)
        self._update_reference_controls()
        self._update_reference_summary()
        self._update_chromatic_summary()
        self._update_landmark_overlays()
        if save:
            self._schedule_processing_state_save()
        self._set_status_text(f"Manual reference set to {wavelength:g} nm | frame {frame}.")

    def _sync_auto_reference_to_current_frame(self) -> None:
        if str(self._state.preprocessing.reference_mode or "auto") != "auto":
            return
        frame = self._current_frame()
        if frame is None:
            return
        auto_key = self._auto_reference_image_key_for_frame(frame)
        if auto_key is None:
            return
        auto_frame, auto_wavelength = auto_key
        self._state.preprocessing.reference_mode = "auto"
        self._state.preprocessing.reference_frame_index = int(auto_frame)
        self._state.preprocessing.reference_wavelength_nm = float(auto_wavelength)
        if self._current_image_key != auto_key and self._wavelength_values:
            wavelength_index = min(
                range(len(self._wavelength_values)),
                key=lambda idx: abs(self._wavelength_values[idx] - float(auto_wavelength)),
            )
            self.wavelength_slider.blockSignals(True)
            self.wavelength_slider.setValue(wavelength_index)
            self.wavelength_slider.blockSignals(False)
            self.wavelength_spin.blockSignals(True)
            self.wavelength_spin.setValue(float(auto_wavelength))
            self.wavelength_spin.blockSignals(False)
            self._sync_analysis_plot_cursors()
            self._schedule_image_refresh()
        self._update_reference_controls()
        self._update_reference_summary()
        self._schedule_processing_state_save()

    def _ensure_reference_star_label(self) -> QLabel:
        star = getattr(self, "_reference_star_label", None)
        if star is not None:
            return star
        star = QLabel(self.image_view.viewport())
        star.setObjectName("referenceStarLabel")
        star.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        star.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        star.setAlignment(Qt.AlignmentFlag.AlignCenter)
        star.setFixedSize(26, 26)
        icon = self._tabler_icon("star", "#84cc16", 26, stroke_width=2.0, fill="#84cc16")
        if icon.isNull():
            icon = self._lucide_icon("star", "#84cc16", 26, stroke_width=2.0)
        if icon.isNull():
            pixmap = QPixmap(26, 26)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#84cc16"), 2.0))
            painter.setBrush(QColor("#84cc16"))
            path = QPainterPath()
            path.moveTo(13.0, 2.8)
            path.lineTo(16.6, 9.1)
            path.lineTo(23.6, 9.6)
            path.lineTo(18.2, 14.0)
            path.lineTo(20.0, 21.0)
            path.lineTo(13.0, 17.2)
            path.lineTo(6.0, 21.0)
            path.lineTo(7.8, 14.0)
            path.lineTo(2.4, 9.6)
            path.lineTo(9.4, 9.1)
            path.closeSubpath()
            painter.drawPath(path)
            painter.end()
            icon = QIcon(pixmap)
        star.setPixmap(icon.pixmap(26, 26))
        star.setStyleSheet("background: transparent;")
        star.hide()
        self._reference_star_label = star
        self._position_reference_star_label()
        return star

    def _position_reference_star_label(self) -> None:
        star = getattr(self, "_reference_star_label", None)
        if star is None:
            return
        margin = 8
        star.move(margin, margin)

    def _update_reference_star_overlay(self) -> None:
        star = self._ensure_reference_star_label()
        visible = (
            self._current_processed_image is not None
            and self._is_current_reference_image()
            and not self._showing_background_profile_main
        )
        star.setVisible(bool(visible))
        if visible:
            self._position_reference_star_label()

    def _update_reference_navigation_styles(self) -> None:
        if self._state.dataset is None:
            self.frame_spin.setStyleSheet("")
            self.wavelength_spin.setStyleSheet("")
            self.frame_slider.setStyleSheet("")
            self.wavelength_slider.setStyleSheet("")
            return
        ref_key = self._reference_image_key()
        current_frame = self._current_frame()
        current_wavelength = self._current_wavelength()
        frame_active = ref_key is not None and current_frame is not None and int(current_frame) == int(ref_key[0])
        wavelength_active = (
            ref_key is not None
            and current_wavelength is not None
            and abs(float(current_wavelength) - float(ref_key[1])) < 1e-6
        )
        if frame_active:
            self.frame_spin.setStyleSheet(
                "QSpinBox { background: rgba(250, 204, 21, 0.14); border: 1px solid #facc15; color: #fef08a; }"
            )
            self.frame_slider.setStyleSheet(
                "QSlider::handle:horizontal { background: #facc15; border: 1px solid #fef08a; width: 12px; margin: -5px 0; border-radius: 6px; }"
                "QSlider::handle:horizontal:hover { background: #fde047; }"
                "QSlider::handle:horizontal:pressed { background: #eab308; }"
            )
        else:
            self.frame_spin.setStyleSheet("")
            self.frame_slider.setStyleSheet("")
        if wavelength_active:
            self.wavelength_spin.setStyleSheet(
                "QSpinBox { background: rgba(132, 204, 22, 0.14); border: 1px solid #84cc16; color: #d9f99d; }"
            )
            self.wavelength_slider.setStyleSheet(
                "QSlider::handle:horizontal { background: #84cc16; border: 1px solid #d9f99d; width: 12px; margin: -5px 0; border-radius: 6px; }"
                "QSlider::handle:horizontal:hover { background: #a3e635; }"
                "QSlider::handle:horizontal:pressed { background: #65a30d; }"
            )
        else:
            self.wavelength_spin.setStyleSheet("")
            self.wavelength_slider.setStyleSheet("")

    def _set_help(self, target: QWidget | QAction, summary: str, detail: str | None = None) -> None:
        text = detail if detail is not None else summary
        if isinstance(target, QAction):
            target.setToolTip(summary)
            target.setStatusTip(summary)
            target.setWhatsThis(text)
        else:
            target.setToolTip(summary)
            target.setStatusTip(summary)
            target.setWhatsThis(text)

    def _show_shortcuts_dialog(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", shortcuts_text())

    def _show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            "About LSPR Imaging",
            "LSPR Imaging\n\nDataset browsing, chromatic correction, masking, spot editing, and spectral analysis.",
        )

    def _reset_layout_to_defaults(self) -> None:
        self._restore_default_panel_layout()
        self._suspend_collapsible_accordion = True
        try:
            self.dataset_section.set_expanded(True)
            self.mask_section.set_expanded(True)
            self.chromatic_section.set_expanded(False)
            self.image_tools_section.set_expanded(True)
            self.spot_editor_section.set_expanded(True)
            self.background_section.set_expanded(True)
            self.analysis_section.set_expanded(True)
        finally:
            self._suspend_collapsible_accordion = False
        self.left_tabs.blockSignals(True)
        try:
            self.left_tabs.setCurrentIndex(0)
        finally:
            self.left_tabs.blockSignals(False)
        self._save_layout_preferences()
        self._set_status_text("Layout reset to defaults.")

    def _reset_panel_layout(self) -> None:
        self._panel_layout_visibility_backup = None
        visibility = {name: panel.isVisible() for name, panel in self._panel_layout_panels()}
        self._restore_default_panel_layout()
        for name, panel in self._panel_layout_panels():
            panel.setVisible(visibility.get(name, panel.isVisible()))
        self._save_panel_layout_preferences()
        self._set_status_text("Panel layout reset to defaults.")

    def _expand_left_panels(self) -> None:
        self._suspend_collapsible_accordion = True
        try:
            for section in [
                self.dataset_section,
                self.mask_section,
                self.chromatic_section,
                self.image_tools_section,
                self.spot_editor_section,
                self.background_section,
                self.analysis_section,
            ]:
                section.set_expanded(True)
        finally:
            self._suspend_collapsible_accordion = False
        self._save_layout_preferences()

    def _collapse_left_panels(self) -> None:
        self._suspend_collapsible_accordion = True
        try:
            for section in [
                self.dataset_section,
                self.mask_section,
                self.chromatic_section,
                self.image_tools_section,
                self.spot_editor_section,
                self.background_section,
                self.analysis_section,
            ]:
                section.set_expanded(False)
        finally:
            self._suspend_collapsible_accordion = False
        self._save_layout_preferences()

    def _configure_control_help(self) -> None:
        self._set_help(self.folder_edit, "Dataset folder to load.", "Enter or paste the folder containing the image dataset.")
        self._set_help(self.browse_button, "Browse for a dataset folder.")
        self._set_help(self.load_button, "Load dataset: choose a dataset folder and open it.")
        self._set_help(self.dataset_ome_zarr_export_button, "Export: write the current dataset to a Stack to Zarr in a chosen folder.")
        self._set_help(self.dataset_ome_zarr_export_stop_button, "Stop: cancel the running Stack to Zarr export.")
        self._set_help(self.ome_zarr_chunk_mode_combo, "Choose a common chunk size or let the app suggest one based on the image size.")
        self._set_help(self.ome_zarr_chunk_value_label, "Resolved Stack to Zarr chunk size in pixels.")
        self._set_help(self.ome_zarr_chunk_guide_button, "Guide: show chunk tiling over the current image.")
        self._set_help(self.ome_zarr_compression_button, "Compression: turn Stack to Zarr compression on or off.")
        self._set_help(self.export_settings_button, "Export preprocessing, spot settings, spots, and groups to a JSON profile.")
        self._set_help(self.import_settings_button, "Import preprocessing, spot settings, spots, and groups from a JSON profile.")
        self._set_help(self.frame_slider, "Choose the reference frame.")
        self._set_help(self.frame_spin, "Reference frame number.")
        self._set_help(self.wavelength_slider, "Choose the reference wavelength.")
        self._set_help(self.wavelength_spin, "Reference wavelength in nanometers.")
        self._set_help(self.reference_auto_button, "Auto: use the best wavelength in the current frame as the reference image.")
        self._set_help(self.reference_manual_button, "Manual: store the current frame and wavelength as the manual reference image.")
        self._set_help(self.chromatic_apply_check, "Apply the saved chromatic transform models so reference spots and mask are propagated to non-reference images.")
        self._set_help(self.chromatic_sample_count_spin, "Odd number of spectral images to sample across the stack for the radial chromatic workflow.")
        self._set_help(self.chromatic_feature_count_spin, "Choose 5, 15, or 30 editable spatial reference points to mark on each sampled image.")
        self._set_help(self.chromatic_start_button, "Edit: enter chromatic reference-point editing mode on the current sampled image.")
        self._set_help(self.chromatic_auto_button, "Automatic spot detection: detect the chromatic reference points on the sampled images and track them across the wavelength stack.")
        self._set_help(self.chromatic_reference_points_all_button, "Show all chromatic reference points across the sampled wavelengths. When linked, they are transformed into the current image space.")
        self._set_help(self.chromatic_prev_button, "Go to the previous sampled wavelength image.")
        self._set_help(self.chromatic_next_button, "Go to the next sampled wavelength image.")
        self._set_help(self.chromatic_landmark_clear_button, "Clear all saved chromatic reference points.")
        self._set_help(
            self.chromatic_landmark_id_spin,
            "Active Ref.point ID. PageUp/PageDown switches between reference points while editing. Shift+PageUp/PageDown switches wavelength images globally.",
        )
        self._set_help(self.chromatic_subpixel_precision_combo, "Sub.px: choose the chromatic point refinement level. 1 = pixel, 4 = moderate, 9 = finer.")
        self._set_help(self.chromatic_transform_button, "Estimate chromatic transforms or clear saved chromatic transforms.")
        self._set_help(
            self.histogram_bins_spin,
            "Histogram bin size in intensity units.",
            "Smaller bin sizes show finer detail in the image histogram. Larger bin sizes smooth the intensity distribution. "
            "The fit-quality plot now chooses its own binning automatically.",
        )
        self._set_help(
            self.background_removal_link,
            "Estimate a smooth illumination profile and flatten the image by subtracting it.",
        )
        self._set_help(
            self.background_smoothing_sigma_spin,
            "Scale of the smooth illumination profile in pixels. Larger values follow only slow background changes.",
        )
        self._set_help(
            self.background_smoothing_binning_combo,
            "Downsample factor used only while estimating the smooth background profile.",
            "Higher binning makes background removal much faster by estimating the smooth profile on a smaller image and "
            "upsampling it back. Start with 2x2, and use 4x4 only when the background changes slowly enough.",
        )
        self._set_help(self.background_ignore_spot_button, "Ignore the detected spot area while estimating the illumination background.")
        self._set_help(self.background_ignore_mask_button, "Ignore masked pixels while estimating the illumination background.")
        self._set_help(self.background_create_new_button, "Create a new background image from the current parameters.")
        self._set_help(self.background_load_from_file_button, "Load a background image from disk.")
        self._set_help(self.background_save_button, "Save the current background image as the current reference figure name plus _background.png.")
        self._set_help(
            self.background_profile_hold_button,
            "Toggle the background-profile preview in the main image.",
            "When enabled, the main image shows the estimated background profile instead of the processed image.\n"
            "The profile updates automatically when background settings change.",
        )
        self._set_help(
            self.background_profile_button,
            "Toggle the background-profile preview in the main image.",
            "When enabled, the main image shows the estimated background profile instead of the processed image.\n"
            "The profile updates automatically when background settings change.",
        )
        self._set_help(self.mask_mode_combo, "Choose how the ignore mask is computed: absolute intensity range, relative deviation from a smooth profile, or local contrast outliers.")
        self._set_help(self.mask_profile_sigma_spin, "Scale of the smooth background profile used for relative-threshold masking.")
        self._set_help(self.mask_relative_threshold_spin, "Relative intensity deviation threshold for masking, expressed as a percent from the local smooth profile.")
        self._set_help(self.mask_local_sigma_spin, "Neighborhood scale for local-contrast masking.")
        self._set_help(self.mask_local_z_spin, "Local contrast threshold in standard deviations from the local background.")
        self._set_help(self.mask_save_button, "Save the current mask as a black-and-white image named from the current image plus _mask.png.")
        self._set_help(self.mask_morph_radius_spin, "Radius in pixels used by the mask morphology operations.")
        self._set_help(self.mask_morphology_erode_button, "Shrink the loaded or hand-drawn file mask.")
        self._set_help(self.mask_morphology_dilate_button, "Expand the loaded or hand-drawn file mask.")
        self._set_help(self.mask_morphology_open_button, "Opening: erode then dilate the file mask to remove small islands.")
        self._set_help(self.mask_morphology_close_button, "Closing: dilate then erode the file mask to fill small gaps.")
        self._set_help(self.mask_pencil_check, "Enable manual file-mask drawing directly on the image.")
        self._set_help(self.mask_draw_add_button, "Draw blue mask pixels onto the current mask preview.")
        self._set_help(self.mask_draw_remove_button, "Erase blue mask pixels from the current mask preview.")
        self._set_help(self.mask_draw_mode_combo, "Choose whether the pencil adds mask pixels or erases them.")
        self._set_help(self.mask_brush_size_spin, "Brush diameter in pixels for the mask pencil.")
        self._set_help(self.spot_diameter_spin, "Spot diameter in pixels.")
        self._set_help(self.ring_inner_diameter_spin, "Inner diameter of the reference ring in pixels.")
        self._set_help(self.ring_outer_diameter_spin, "Outer diameter of the reference ring in pixels.")
        self._set_help(self.array_rows_spin, "Expected number of spot rows in the array.")
        self._set_help(self.array_cols_spin, "Expected number of spot columns in the array.")
        self._set_help(self.array_spacing_spin, "Expected spacing between neighboring array spots in pixels.")
        self._set_help(self.ignore_marked_check, "Ignore pixels defined by the current mask controls and any loaded mask image.")
        self._set_help(self.detect_spots_button, "Mode A: automatically detect array spots on the current reference image.")
        self._set_help(self.spot_corner_select_button, "Mode B: select the four array corners first, then fill the grid. Coming later.")
        self._set_help(self.reorder_spots_button, "Reorder detected spots by image position so the top-left spot becomes ID 1.")
        self._set_help(self.clear_spots_button, "Remove all detected spots and groups from the current dataset.")
        self._set_help(self.clear_spot_selection_button, "Clear the current spot selection.")
        self._set_help(self.show_spots_check, "Show or hide the spot overlays.")
        self._set_help(self.bottom_spot_labels_button, "Show or hide spot labels next to the spot overlays. This works independently of manual spot editing.")
        self._set_help(self.spot_editor_labels_button, "Show or hide spot labels in the left panel. This works independently of manual spot editing.")
        self._set_help(self.show_rings_check, "Show or hide the reference rings.")
        self._set_help(self.show_reference_points_check, "Show or hide chromatic reference points.")
        self._set_help(self.show_mask_check, "Show or hide the mask overlay.")
        self._set_help(self.show_highlight_check, "Show or hide the histogram highlight overlay for the selected histogram range.")
        self._set_help(self.measurement_unit_button, "Switch the displayed length units between pixels and micrometers.")
        self._set_help(self.scale_bar_toggle_button, "Show or hide the image scale bar.")
        self._set_help(
            self.background_profile_hold_button,
            "Toggle the background profile preview.",
            "Show or hide the estimated background profile instead of the processed image. "
            "The preview updates automatically when background settings change.",
        )
        self._set_help(
            self.background_profile_button,
            "Toggle the background profile preview.",
            "Show or hide the estimated background profile instead of the processed image. "
            "The preview updates automatically when background settings change.",
        )
        self._set_help(self.mask_color_button, "Choose the mask-overlay color.")
        self._set_help(self.spot_color_button, "Choose the spot overlay color.")
        self._set_help(self.ring_color_button, "Choose the reference-ring color.")
        self._set_help(self.highlight_color_button, "Choose the histogram-highlight overlay color.")
        self._set_help(self.mask_alpha_slider, "Mask transparency.")
        self._set_help(self.spot_alpha_slider, "Spot transparency.")
        self._set_help(self.ring_alpha_slider, "Reference-ring transparency.")
        self._set_help(self.highlight_alpha_slider, "Highlight transparency.")
        self._set_help(self.rotate_action, "Manual rotation tool. Use arrow keys to adjust the angle.")
        self._set_help(self.crop_action, "Crop tool.")
        self._set_help(self.measure_action, "Measurement tool. Drag two guide crosses to calibrate micrometers per pixel.")
        self._set_help(self.flip_horizontal_action, "Flip the reference image horizontally.")
        self._set_help(self.flip_vertical_action, "Flip the reference image vertically.")
        self._set_help(self.undo_action, "Undo the last recorded editing step.")
        self._set_help(self.redo_action, "Redo the last undone editing step.")
        self._set_help(self.reset_rotation_action, "Reset the image rotation to zero.")
        self._set_help(self.reset_crop_action, "Reset the crop to the full image.")
        self._set_help(self.measurement_um_x_spin, "Real horizontal distance between the ruler crosses in micrometers.")
        self._set_help(self.measurement_um_y_spin, "Real vertical distance between the ruler crosses in micrometers.")
        self._set_help(self.measurement_apply_button, "Apply the entered micrometer distances to calibrate the image in memory.")
        self._set_help(
            self.spot_edit_action,
            "Enable manual spot editing mode.",
            "Spot edit mode:\n"
            "Left-click: select a spot\n"
            "Shift+Left-click: add a spot to the selection\n"
            "Double-left-click outside a spot: clear the selection\n"
            "Left-drag: draw a selection box\n"
            "Right-drag: move selected spots when Move is active\n"
            "Middle-drag: pan the image view\n"
            "Arrow keys: move selected spots while Move is active\n"
            "Shift+Arrow: select neighboring spot in the array\n"
            "Ctrl+Arrow: move selected spots faster\n"
            "Ctrl+Shift+A: Add mode\n"
            "Ctrl+Shift+M: Move mode",
        )
        self._set_help(self.spot_add_action, "Add mode: click the image to place a new spot.")
        self._set_help(self.spot_move_action, "Move selected spots by dragging or arrow keys.")
        self._set_help(self.remove_spots_action, "Remove the selected spots.")
        self._set_help(self.group_spots_action, "Group selected spots.")
        self._set_help(self.spot_list_cached_button, "Show only the spots that already have cached absorbance data.")
        self._set_help(self.analysis_preview_button, "Live preview: update the spectrum and sensorgram automatically when spot selection changes.")
        self._set_help(self.shortcuts_action, "Show the main keyboard shortcuts.", shortcuts_text())
        self._set_help(self.reset_layout_action, "Restore default splitter sizes and panel states.")
        self._set_help(self.reset_dock_layout_action, "Restore default splitter sizes without changing panel visibility.")
        self._set_help(self.expand_left_panels_action, "Expand all left workflow panels.")
        self._set_help(self.collapse_left_panels_action, "Collapse all left workflow panels.")
        self._set_help(self.calculate_spectrum_action, "Calculate the absorbance spectrum for the current frame and selected spots.")
        self._set_help(self.about_action, "Show basic app information.")
        self._set_help(self.analysis_spot_table_button, "Show or hide the spot list table.")

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
        icon = MainWindow._tabler_icon(icon_name, color, size, stroke_width=2.0)
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

    def _make_analysis_all_frames_icon(self, active: bool, *, size: int = 24) -> QIcon:
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
        fallback = self._tabler_icon("stack-3", color, size, stroke_width=2.0, fill=color if active else None)
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

    def _make_cached_spots_icon(self, visible: bool, *, size: int = 24) -> QIcon:
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
        if kind == "spots":
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
        if kind == "spots":
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
            (getattr(self, "show_spots_check", None), "spots", self._spots_visible),
            (getattr(self, "show_rings_check", None), "rings", self._rings_visible),
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
            self.histogram_y_scale_button.setChecked(self._histogram_log_y_enabled)

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
        button.setText(self._display_unit_text())
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
        is_um = str(self._state.preprocessing.display_units or "px") == "um"
        target.setText(self._display_unit_text())
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

    def _create_label_visibility_button(self, visible: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("spotLabelIconButton")
        button.setAutoRaise(True)
        button.setIconSize(QSize(APP_THEME.plain_icon_inner, APP_THEME.plain_icon_inner))
        button.setFixedSize(APP_THEME.plain_icon_outer, APP_THEME.plain_icon_outer)
        button.setCheckable(True)
        button.setChecked(bool(visible))
        button.setIcon(self._make_spot_label_icon(bool(visible)))
        button.setToolTip("Show or hide spot labels. This works independently of manual spot editing.")
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.toggled.connect(self._update_spot_label_button_icon)
        return button

    def _update_spot_label_button_icon(self, checked: bool) -> None:
        icon = self._make_spot_label_icon(bool(checked))
        for attr_name in ("spot_editor_labels_button", "bottom_spot_labels_button"):
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
            (self.spot_list_action, {"accent": "orange"}),
            (self.spot_edit_action, {"primary": True}),
            (self.spot_add_action, {"accent": "green"}),
            (self.spot_move_action, {"accent": "blue"}),
            (self.remove_spots_action, {"accent": "red"}),
        ]
        for action, kwargs in buttons:
            self.left_spot_editor_layout.addWidget(self._create_left_spot_editor_button(action, **kwargs))
        self.left_spot_editor_layout.addWidget(self.spot_editor_labels_button)
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
        edit_menu.addAction(self.clear_spot_selection_button.text(), self._clear_spot_selection)

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
        dock_menu.addAction(self.workflow_panel.toggleViewAction())
        dock_menu.addAction(self.image_panel.toggleViewAction())
        dock_menu.addAction(self.histogram_panel.toggleViewAction())
        dock_menu.addAction(self.spectra_panel.toggleViewAction())
        dock_menu.addAction(self.sensorgram_panel.toggleViewAction())
        dock_menu.addAction(self.spot_list_panel.toggleViewAction())
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

    def _set_spinbox_width(self, spinbox: QSpinBox | QDoubleSpinBox, text: str, *, minimum: int = 46) -> None:
        width = spinbox.fontMetrics().horizontalAdvance(text) + 12
        spinbox.setFixedWidth(max(minimum, width))

    def _set_combo_width(self, combo: QComboBox, texts: list[str], *, minimum: int = 58) -> None:
        widest = max((combo.fontMetrics().horizontalAdvance(text) for text in texts), default=0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setFixedWidth(max(minimum, widest + 10))

    def _apply_right_aligned_control_text(self) -> None:
        for line_edit in self.findChildren(QLineEdit):
            line_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for spinbox in self.findChildren(QAbstractSpinBox):
            spinbox.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for combo in self.findChildren(QComboBox):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setReadOnly(True)
                line_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                line_edit.setFrame(False)

    def _apply_compact_control_widths(self) -> None:
        self._set_spinbox_width(self.chromatic_sample_count_spin, "777")
        self._set_combo_width(self.chromatic_feature_count_spin, ["5", "15", "30"], minimum=54)
        self._set_spinbox_width(self.chromatic_landmark_id_spin, "99", minimum=62)
        self._set_spinbox_width(self.spot_diameter_spin, "9999.99")
        self._set_spinbox_width(self.ring_inner_diameter_spin, "9999.99")
        self._set_spinbox_width(self.ring_outer_diameter_spin, "9999.99")
        self._set_spinbox_width(self.background_smoothing_sigma_spin, "2000 px", minimum=72)
        self._set_combo_width(self.background_smoothing_binning_combo, ["4x4"])
        self._set_combo_width(self.mask_mode_combo, ["Local contrast"], minimum=96)
        self._set_spinbox_width(self.mask_profile_sigma_spin, "2000 px", minimum=72)
        self._set_spinbox_width(self.mask_relative_threshold_spin, "500.0 %", minimum=78)
        self._set_spinbox_width(self.mask_local_contrast_sigma_spin, "500.0 %", minimum=78)
        self._set_spinbox_width(self.mask_local_z_spin, "20.0 sigma", minimum=84)
        self._set_spinbox_width(self.mask_morph_radius_spin, "100 px", minimum=64)
        self._set_combo_width(self.mask_draw_mode_combo, ["Erase"])
        self._set_spinbox_width(self.mask_brush_size_spin, "200 px", minimum=68)
        self._set_spinbox_width(self.histogram_bins_spin, "8192 DN", minimum=70)
        self._set_spinbox_width(self.array_rows_spin, "100")
        self._set_spinbox_width(self.array_cols_spin, "100")
        self._set_spinbox_width(self.array_spacing_spin, "1000.00", minimum=68)
        self._set_spinbox_width(self.measurement_um_x_spin, "1000000", minimum=58)
        self._set_spinbox_width(self.measurement_um_y_spin, "1000000", minimum=58)
        self._set_spinbox_width(self.frame_spin, "99999", minimum=66)
        self._set_spinbox_width(self.wavelength_spin, "99999 nm", minimum=82)
        self._set_combo_width(self.chromatic_subpixel_precision_combo, ["1", "4", "9"], minimum=42)
        self.mask_local_contrast_sigma_spin.setFixedWidth(self.mask_relative_threshold_spin.sizeHint().width())
        navigation_control_width = max(self.frame_spin.sizeHint().width(), self.wavelength_spin.sizeHint().width(), 82)
        navigation_slider_width = max(navigation_control_width + 80, 170)
        self.frame_spin.setFixedWidth(navigation_control_width)
        self.wavelength_spin.setFixedWidth(navigation_control_width)
        self.frame_slider.setFixedWidth(navigation_slider_width)
        self.wavelength_slider.setFixedWidth(navigation_slider_width)

    def _on_rotate_tool_toggled(self, checked: bool) -> None:
        if checked:
            if not self._ensure_reference_image_for_image_tools():
                self.rotate_action.blockSignals(True)
                self.rotate_action.setChecked(False)
                self.rotate_action.blockSignals(False)
                self._sync_rotation_visibility()
                self._sync_crop_visibility()
                return
            self._unlink_image_tools_for_preview()
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            self.measure_action.blockSignals(True)
            self.measure_action.setChecked(False)
            self.measure_action.blockSignals(False)
            self.spot_edit_action.blockSignals(True)
            self.spot_edit_action.setChecked(False)
            self.spot_edit_action.blockSignals(False)
            self._active_tool = "rotate"
        elif self._active_tool == "rotate":
            self._active_tool = None
        self._refresh_image_tool_action_icons()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._sync_measurement_visibility()

    def _on_crop_tool_toggled(self, checked: bool) -> None:
        if checked:
            if not self._ensure_reference_image_for_image_tools():
                self.crop_action.blockSignals(True)
                self.crop_action.setChecked(False)
                self.crop_action.blockSignals(False)
                self._sync_rotation_visibility()
                self._sync_crop_visibility()
                return
            self._unlink_image_tools_for_preview()
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.measure_action.blockSignals(True)
            self.measure_action.setChecked(False)
            self.measure_action.blockSignals(False)
            self.spot_edit_action.blockSignals(True)
            self.spot_edit_action.setChecked(False)
            self.spot_edit_action.blockSignals(False)
            self._active_tool = "crop"
            self._state.preprocessing.crop.enabled = True
            self._save_processing_state_for_dataset()
        elif self._active_tool == "crop":
            self._active_tool = None
        self._refresh_image_tool_action_icons()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._sync_measurement_visibility()
        self._current_image_key = None
        self._refresh_image()

    def _on_measure_tool_toggled(self, checked: bool) -> None:
        if checked:
            if not self._ensure_reference_image_for_image_tools():
                self.measure_action.blockSignals(True)
                self.measure_action.setChecked(False)
                self.measure_action.blockSignals(False)
                self._sync_measurement_visibility()
                return
            self._unlink_image_tools_for_preview()
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            self.spot_edit_action.blockSignals(True)
            self.spot_edit_action.setChecked(False)
            self.spot_edit_action.blockSignals(False)
            self._active_tool = "measure"
            self._set_status_text("Measurement tool active. Drag the two crosses, enter the real Δx/Δy in µm, then apply calibration.")
        elif self._active_tool == "measure":
            self._active_tool = None
        self._refresh_image_tool_action_icons()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._sync_measurement_visibility()

    def _on_spot_edit_tool_toggled(self, checked: bool) -> None:
        if checked:
            reference_key = self._reference_image_key()
            if reference_key is not None and self._current_image_key != reference_key:
                self._set_current_frame_and_wavelength(int(reference_key[0]), float(reference_key[1]))
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            if not self._spots_visible:
                self.show_spots_check.blockSignals(True)
                self.show_spots_check.setChecked(True)
                self.show_spots_check.blockSignals(False)
                self._spots_visible = True
                self._refresh_view_toggle_icons()
                self._save_visual_preferences()
            self._active_tool = "spots"
            self._sync_spot_edit_capabilities()
            if self._is_current_reference_image():
                self._set_status_text("Spot editor active.")
            else:
                self._set_status_text("Spot inspect mode active.")
        elif self._active_tool == "spots":
            self._active_tool = None
            self.spot_add_action.blockSignals(True)
            self.spot_add_action.setChecked(False)
            self.spot_add_action.blockSignals(False)
            self.spot_add_action.setEnabled(False)
            self.spot_move_action.blockSignals(True)
            self.spot_move_action.setChecked(False)
            self.spot_move_action.blockSignals(False)
            self.spot_move_action.setEnabled(False)
            self.remove_spots_action.setEnabled(False)
            self.group_spots_action.setEnabled(False)
            self._finalize_spot_edit_refresh()
        self._dragging_spots = False
        self._drag_anchor = None
        self._drag_original_positions.clear()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._sync_spot_edit_capabilities()
        self._update_spot_overlays()
        self._update_status_hint()

    def _on_mask_pencil_toggled(self, checked: bool) -> None:
        if checked:
            if not self._is_current_reference_image():
                self.mask_pencil_check.blockSignals(True)
                self.mask_pencil_check.setChecked(False)
                self.mask_pencil_check.blockSignals(False)
                self.status_label.setText("Mask drawing is available only on the reference image.")
                return
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            self.spot_edit_action.blockSignals(True)
            self.spot_edit_action.setChecked(False)
            self.spot_edit_action.blockSignals(False)
            self._active_tool = "mask"
        elif self._active_tool == "mask":
            self._active_tool = None
        self._mask_drawing = False
        self._drag_anchor = None
        self._dragging_spots = False

    def _sync_mask_draw_mode_buttons(self) -> None:
        mode = str(self.mask_draw_mode_combo.currentData() or "add")
        is_add = mode != "erase"
        for button, checked in (
            (self.mask_draw_add_button, is_add),
            (self.mask_draw_remove_button, not is_add),
        ):
            button.blockSignals(True)
            button.setChecked(bool(checked))
            button.blockSignals(False)

    def _set_mask_draw_mode(self, mode: str) -> None:
        mode_key = "erase" if str(mode).strip().lower() == "erase" else "add"
        index = self.mask_draw_mode_combo.findData(mode_key)
        if index < 0:
            index = 0
        if self.mask_draw_mode_combo.currentIndex() != index:
            self.mask_draw_mode_combo.setCurrentIndex(index)
        self._sync_mask_draw_mode_buttons()
        self._save_control_preferences()

    def _sync_spot_edit_capabilities(self) -> None:
        editable = self._active_tool == "spots" and self._is_current_reference_image()
        self.spot_add_action.setEnabled(editable)
        self.spot_move_action.setEnabled(editable)
        self.remove_spots_action.setEnabled(editable)
        self.group_spots_action.setEnabled(editable)
        if not editable:
            self.spot_add_action.blockSignals(True)
            self.spot_add_action.setChecked(False)
            self.spot_add_action.blockSignals(False)
            self.spot_move_action.blockSignals(True)
            self.spot_move_action.setChecked(False)
            self.spot_move_action.blockSignals(False)
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._update_status_hint()

    def _on_spot_add_toggled(self, checked: bool) -> None:
        if checked:
            self.spot_move_action.blockSignals(True)
            self.spot_move_action.setChecked(False)
            self.spot_move_action.blockSignals(False)
        self._update_spot_overlays()
        self._update_status_hint()

    def _on_spot_move_toggled(self, checked: bool) -> None:
        if checked:
            self.spot_add_action.blockSignals(True)
            self.spot_add_action.setChecked(False)
            self.spot_add_action.blockSignals(False)
        if hasattr(self, "image_panel"):
            self.image_panel.raise_()
            if hasattr(self, "image_view") and self.image_view is not None:
                self.image_view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                viewport = self.image_view.viewport()
                if viewport is not None:
                    viewport.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._update_spot_overlays()
        self._update_status_hint()

    def _on_flip_horizontal_toggled(self, checked: bool) -> None:
        if not self._ensure_reference_image_for_image_tools():
            self.flip_horizontal_action.blockSignals(True)
            self.flip_horizontal_action.setChecked(self._state.preprocessing.flip_horizontal)
            self.flip_horizontal_action.blockSignals(False)
            return
        if checked:
            self._unlink_image_tools_for_preview()
        self._state.preprocessing.flip_horizontal = checked
        self._refresh_image_tool_action_icons()
        self._apply_image_transform_change("Applied horizontal flip." if checked else "Removed horizontal flip.")

    def _on_flip_vertical_toggled(self, checked: bool) -> None:
        if not self._ensure_reference_image_for_image_tools():
            self.flip_vertical_action.blockSignals(True)
            self.flip_vertical_action.setChecked(self._state.preprocessing.flip_vertical)
            self.flip_vertical_action.blockSignals(False)
            return
        if checked:
            self._unlink_image_tools_for_preview()
        self._state.preprocessing.flip_vertical = checked
        self._refresh_image_tool_action_icons()
        self._apply_image_transform_change("Applied vertical flip." if checked else "Removed vertical flip.")

    def _ensure_reference_image_for_image_tools(self) -> bool:
        if self._current_record_path is None or self._is_current_reference_image():
            return True
        self._set_status_text("Switch to the reference image to edit image tools.")
        return False

    def _handle_image_tool_settings_changed(self, status: str, *, preserve_view: bool = False) -> None:
        linked = bool(self.image_tools_section.is_applied())
        self._image_tools_preview_only = not linked
        self._capture_pending_image_view_ranges(preserve_view=preserve_view)
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        if self._state.detected_spots:
            self._update_spot_overlays()
            self._update_spot_summary()
            self._sync_spot_detection_controls()
        self._save_processing_state_for_dataset()
        self._current_image_key = None
        self._refresh_image()
        self.status_label.setText(status)

    def _apply_image_transform_change(self, status: str) -> None:
        self._push_undo_point(status)
        self._handle_image_tool_settings_changed(status, preserve_view=True)

    def _refresh_image_tool_action_icons(self) -> None:
        self.rotate_action.setIcon(self._make_rotate_icon(self.rotate_action.isChecked()))
        self.crop_action.setIcon(self._make_crop_icon(self.crop_action.isChecked()))
        self.flip_horizontal_action.setIcon(self._make_flip_horizontal_icon(self.flip_horizontal_action.isChecked()))
        self.flip_vertical_action.setIcon(self._make_flip_vertical_icon(self.flip_vertical_action.isChecked()))
        self.measure_action.setIcon(self._make_measure_icon(self.measure_action.isChecked()))

    def _on_show_spots_toggled(self, checked: bool) -> None:
        self._spots_visible = checked
        self._update_spot_overlays()
        self._save_visual_preferences()

    def _on_show_spot_labels_toggled(self, checked: bool) -> None:
        self._spot_labels_visible = checked
        self.spot_editor_labels_button.blockSignals(True)
        self.spot_editor_labels_button.setChecked(checked)
        self.spot_editor_labels_button.blockSignals(False)
        self.bottom_spot_labels_button.blockSignals(True)
        self.bottom_spot_labels_button.setChecked(checked)
        self.bottom_spot_labels_button.blockSignals(False)
        self._update_spot_label_button_icon(bool(checked))
        self._update_spot_overlays()
        self._save_visual_preferences()

    def _on_spot_editor_show_labels_toggled(self, checked: bool) -> None:
        self._spot_labels_visible = checked
        self.bottom_spot_labels_button.blockSignals(True)
        self.bottom_spot_labels_button.setChecked(checked)
        self.bottom_spot_labels_button.blockSignals(False)
        self._update_spot_label_button_icon(bool(checked))
        self._update_spot_overlays()
        self._save_visual_preferences()

    def _on_show_rings_toggled(self, checked: bool) -> None:
        self._rings_visible = checked
        self._update_spot_overlays()
        self._save_visual_preferences()

    def _on_show_reference_points_toggled(self, checked: bool) -> None:
        self._reference_points_visible = checked
        if not checked and self._chromatic_reference_points_all_visible:
            self._chromatic_reference_points_all_visible = False
            if hasattr(self, "chromatic_reference_points_all_button"):
                self.chromatic_reference_points_all_button.blockSignals(True)
                self.chromatic_reference_points_all_button.setChecked(False)
                self.chromatic_reference_points_all_button.blockSignals(False)
        self._update_landmark_overlays()
        self._save_visual_preferences()

    def _on_show_mask_toggled(self, checked: bool) -> None:
        self._mask_visible = checked
        self._update_ignore_mask_overlay()
        self._save_visual_preferences()

    def _on_show_highlight_toggled(self, checked: bool) -> None:
        self._highlight_visible = checked
        self._update_selected_intensity_overlay()
        self._save_visual_preferences()
        self._schedule_processing_state_save()

    def _toggle_display_units(self) -> None:
        current = str(self._state.preprocessing.display_units or "px")
        if current == "px":
            if not self._can_display_micrometers():
                self._set_status_text("Calibrate the ruler first before switching to micrometers.")
                self._refresh_unit_toggle_button()
                return
            self._state.preprocessing.display_units = "um"
        else:
            self._state.preprocessing.display_units = "px"
        self._sync_spot_detection_controls()
        self._update_spot_detection_labels()
        self._update_measurement_status_label()
        self._refresh_scale_bar_overlay()
        self._update_spot_list_table()
        self._save_processing_state_for_dataset()
        self._save_control_preferences()

    def _on_scale_bar_toggled(self, checked: bool) -> None:
        self._state.preprocessing.scale_bar_visible = bool(checked)
        self._refresh_scale_bar_overlay()
        self._save_processing_state_for_dataset()

    def _reset_rotation(self) -> None:
        if not self._ensure_reference_image_for_image_tools():
            return
        self._push_undo_point("Reset rotation")
        self._state.preprocessing.rotation_angle_deg = 0.0
        self._handle_image_tool_settings_changed("Rotation reset to 0 deg.", preserve_view=True)

    def _reset_crop(self) -> None:
        if not self._ensure_reference_image_for_image_tools():
            return
        self._push_undo_point("Reset crop")
        self._state.preprocessing.crop = CropDefinition()
        self._handle_image_tool_settings_changed("Crop reset.", preserve_view=True)

    def _adjust_rotation(self, delta_deg: float) -> None:
        if not self._ensure_reference_image_for_image_tools():
            return
        self._push_undo_point("Adjust rotation")
        self._state.preprocessing.rotation_angle_deg += float(delta_deg)
        self._handle_image_tool_settings_changed(
            f"Rotation adjusted to {self._state.preprocessing.rotation_angle_deg:.2f} deg",
            preserve_view=True,
        )

    def _sync_rotation_tool(self) -> None:
        self._ensure_image_tool_guide()
        self._sync_rotation_visibility()

    def _update_rotation_preview(self) -> None:
        return

    def _sync_rotation_visibility(self) -> None:
        self._update_guide_overlays()

    def _handle_global_page_shortcuts(self, event: QKeyEvent) -> bool:
        return self._shortcut_manager.handle_page_shortcuts(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if self._handle_global_page_shortcuts(event):
                return True
        workflow_log_view = getattr(self, "workflow_log_view", None)
        if workflow_log_view is not None and watched is workflow_log_view and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.matches(QKeySequence.StandardKey.Copy):
                self._copy_workflow_log()
                return True
        spot_list_table = getattr(self, "spot_list_table", None)
        if spot_list_table is not None and watched is spot_list_table and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if key_event.matches(QKeySequence.StandardKey.Undo):
                self._undo()
                return True
            if key_event.matches(QKeySequence.StandardKey.Redo):
                self._redo()
                return True
            if key_event.key() == Qt.Key.Key_PageUp:
                self._move_selected_spots_in_table(-1)
                return True
            if key_event.key() == Qt.Key.Key_PageDown:
                self._move_selected_spots_in_table(1)
                return True
            if key_event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
                self._remove_selected_spots()
                return True
            if key_event.matches(QKeySequence.StandardKey.Copy):
                self._copy_spot_properties_from_table()
                return True
            if key_event.matches(QKeySequence.StandardKey.Paste):
                self._paste_spot_properties_from_table()
                return True
        if watched is self.chromatic_landmark_id_spin and event.type() == QEvent.Type.KeyPress and self._active_tool == "chromatic_landmark":
            key_event = event
            if key_event.key() in {Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}:
                direction = -1 if key_event.key() == Qt.Key.Key_PageUp else 1
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if self._navigate_chromatic_sample(direction):
                        return True
                elif self._switch_chromatic_feature(direction):
                    return True
        if watched is self.wavelength_spin and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            self._step_wavelength_selection(step)
            return True
        if watched is self.frame_spin and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            self._step_frame_selection(step)
            return True
        if watched is self.wavelength_slider and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            self._step_wavelength_selection(step)
            return True
        if watched is self.frame_slider and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            self._step_frame_selection(step)
            return True
        if watched in {self.spot_diameter_spin, self.ring_inner_diameter_spin, self.ring_outer_diameter_spin} and event.type() == QEvent.Type.Wheel:
            wheel_event = event
            delta = wheel_event.angleDelta().y()
            if delta == 0:
                return True
            step = 1 if delta > 0 else -1
            spinbox = watched
            if hasattr(spinbox, "stepBy"):
                spinbox.stepBy(step)
            return True
        spot_list_viewport = self.spot_list_table.viewport() if hasattr(self, "spot_list_table") else None
        if spot_list_viewport is not None and watched is spot_list_viewport and event.type() == QEvent.Type.MouseButtonPress:
            mouse_event = event
            if mouse_event.button() == Qt.MouseButton.LeftButton:
                row = self.spot_list_table.rowAt(int(mouse_event.position().toPoint().y()))
                if row >= 0:
                    if mouse_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        anchor = self._spot_list_range_anchor_row
                        if anchor is None or anchor < 0 or anchor >= self.spot_list_table.rowCount():
                            anchor = self.spot_list_table.currentRow()
                        if anchor < 0:
                            anchor = row
                        start_row, end_row = sorted((anchor, row))
                        self._select_spot_list_table_rows(list(range(start_row, end_row + 1)))
                        return True
                    self._spot_list_range_anchor_row = row
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return False
        if watched is image_view.viewport() and event.type() == QEvent.Type.Resize:
            self._position_reference_star_label()
            return False
        if watched is image_view.viewport() and self._active_tool == "crop":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.MiddleButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                self._begin_image_pan(point)
                return True
            if event.type() == QEvent.Type.MouseMove and self._panning_image:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                self._update_image_pan(point)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.MiddleButton and self._panning_image:
                self._end_image_pan()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                if self._crop_rect_contains_point(point):
                    self._prepare_undo_snapshot("Crop")
                    self._dragging_crop = True
                    self._crop_drag_anchor = point
                    crop = self._state.preprocessing.crop
                    self._crop_drag_origin = (float(crop.x), float(crop.y))
                    return True
                return False
            if event.type() == QEvent.Type.MouseMove and self._dragging_crop and self._crop_drag_anchor is not None and self._crop_drag_origin is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                dx = float(point[0]) - float(self._crop_drag_anchor[0])
                dy = float(point[1]) - float(self._crop_drag_anchor[1])
                self._move_crop_roi_to(float(self._crop_drag_origin[0] + dx), float(self._crop_drag_origin[1] + dy))
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton and self._dragging_crop:
                self._dragging_crop = False
                self._crop_drag_anchor = None
                self._crop_drag_origin = None
                self._handle_image_tool_settings_changed("Crop moved.", preserve_view=True)
                return True
        if watched is image_view.viewport() and self._active_tool == "chromatic_landmark":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                landmark_id = self._find_landmark_id_at(point)
                if landmark_id is not None:
                    self._selected_landmark_id = landmark_id
                    self.chromatic_landmark_id_spin.blockSignals(True)
                    self.chromatic_landmark_id_spin.setValue(landmark_id)
                    self.chromatic_landmark_id_spin.blockSignals(False)
                    self._prepare_undo_snapshot("Chromatic landmarks")
                    self._dragging_landmark = True
                    self._dragging_landmark_started = False
                    self._update_landmark_overlays()
                else:
                    self._selected_landmark_id = int(self._chromatic_landmark_marker_id)
                    self._set_current_landmark(point)
                return True
            if event.type() == QEvent.Type.MouseMove and self._dragging_landmark and self._selected_landmark_id is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                self._dragging_landmark_started = True
                mark = self._current_landmark(int(self._selected_landmark_id))
                if mark is not None:
                    max_x = float(self._current_processed_image.shape[1] - 1) if self._current_processed_image is not None else float(mark.x_px)
                    max_y = float(self._current_processed_image.shape[0] - 1) if self._current_processed_image is not None else float(mark.y_px)
                    mark.x_px = float(np.clip(point[0], 0.0, max_x))
                    mark.y_px = float(np.clip(point[1], 0.0, max_y))
                    self._update_landmark_overlays()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and self._dragging_landmark:
                self._dragging_landmark = False
                moved = self._dragging_landmark_started
                self._dragging_landmark_started = False
                if moved:
                    self._finalize_chromatic_landmark_edit(status_text=f"Adjusted reference point {self._selected_landmark_id}.")
                self._commit_prepared_undo_snapshot()
                return True

        if watched is self.image_view.viewport() and self._active_tool == "mask":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                self._prepare_undo_snapshot("Edit mask")
                self._mask_drawing = True
                self._apply_mask_brush(point)
                return True

            if event.type() == QEvent.Type.MouseMove and self._mask_drawing:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                self._apply_mask_brush(point)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and self._mask_drawing:
                self._mask_drawing = False
                self._finalize_mask_edit()
                return True

        selection_mode_active = self._active_tool == "spots" or (self._analysis_enabled and self._state.dataset is not None)
        if watched is self.image_view.viewport() and selection_mode_active:
            allow_spot_add = self._active_tool == "spots" and self.spot_add_action.isChecked()
            allow_spot_move = self._active_tool == "spots" and self.spot_move_action.isChecked()
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                if allow_spot_add:
                    self._add_spot_at(point)
                    return True
                spot_id = self._find_spot_id_at(point)
                modifiers = event.modifiers()
                self._spot_selection_drag_start = point
                self._spot_selection_drag_button = Qt.MouseButton.LeftButton
                self._spot_selection_pressed_spot_id = spot_id
                self._spot_selection_drag_modifiers = modifiers
                if self._spot_selection_rubber_band is None:
                    self._spot_selection_rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.image_view.viewport())
                start_point = self.image_view.mapFromScene(self.image_plot.vb.mapViewToScene(pg.Point(point[0], point[1])))
                self._spot_selection_rubber_band.setGeometry(start_point.x(), start_point.y(), 0, 0)
                self._spot_selection_rubber_band.hide()
                if spot_id is not None:
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self._selected_spot_ids.add(spot_id)
                    else:
                        self._selected_spot_ids = {spot_id}
                self._update_spot_overlays()
                self._update_spot_summary()
                self._sync_spot_list_table_selection()
                self._update_selection_dependent_plots(prompt_live_preview=True)
                return True

            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return False
                spot_id = self._find_spot_id_at(point)
                if allow_spot_move and self._is_current_reference_image() and spot_id is not None:
                    if self._selected_spot_ids:
                        drag_spot_ids = set(self._selected_spot_ids)
                    else:
                        self._selected_spot_ids = {spot_id}
                        drag_spot_ids = {spot_id}
                    self._update_spot_overlays()
                    self._update_spot_summary()
                    self._sync_spot_list_table_selection()
                    self._update_selection_dependent_plots(prompt_live_preview=True)
                    self._prepare_undo_snapshot("Move spots")
                    self._dragging_spots = True
                    self._drag_anchor = point
                    self._drag_original_positions = {
                        spot.spot_id: (spot.center_x, spot.center_y)
                        for spot in self._state.detected_spots
                        if spot.spot_id in drag_spot_ids
                    }
                    self._spot_selection_drag_button = Qt.MouseButton.RightButton
                    self._spot_selection_drag_start = None
                    self._spot_selection_pressed_spot_id = None
                    self._spot_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                    return True
                if self._analysis_enabled and spot_id is not None:
                    if spot_id not in self._selected_spot_ids:
                        self._selected_spot_ids = {spot_id}
                        self._update_spot_overlays()
                        self._update_spot_summary()
                        self._sync_spot_list_table_selection()
                        self._update_selection_dependent_plots(prompt_live_preview=True)
                    self._show_analysis_spot_context_menu(spot_id, event.globalPosition().toPoint())
                    return True
                return True

            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.RightButton:
                if not self._analysis_enabled:
                    return True
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                spot_id = self._find_spot_id_at(point)
                if spot_id is None:
                    return True
                if self._select_group_members_for_spot(spot_id):
                    self.status_label.setText(f"Selected group members for spot {spot_id}.")
                return True

            if event.type() == QEvent.Type.MouseMove and self._dragging_spots and self._drag_anchor is not None:
                point = self._image_point_from_mouse_event(event)
                if point is None:
                    return True
                dx = point[0] - self._drag_anchor[0]
                dy = point[1] - self._drag_anchor[1]
                for spot in self._state.detected_spots:
                    if spot.spot_id not in self._selected_spot_ids or spot.spot_id not in self._drag_original_positions:
                        continue
                    base_x, base_y = self._drag_original_positions[spot.spot_id]
                    spot.center_x, spot.center_y = self._clamp_spot_position(spot, base_x + dx, base_y + dy)
                self._update_spot_overlays()
                return True

            if (
                event.type() == QEvent.Type.MouseMove
                and self._spot_selection_drag_start is not None
                and self._spot_selection_drag_button == Qt.MouseButton.LeftButton
                and self._spot_selection_rubber_band is not None
                and not self._dragging_spots
            ):
                current_point = self._image_point_from_mouse_event(event)
                if current_point is None:
                    return True
                start_scene = self.image_plot.vb.mapViewToScene(pg.Point(self._spot_selection_drag_start[0], self._spot_selection_drag_start[1]))
                current_scene = self.image_plot.vb.mapViewToScene(pg.Point(current_point[0], current_point[1]))
                if (
                    not self._spot_selection_rubber_band.isVisible()
                    and hypot(
                        float(current_point[0] - self._spot_selection_drag_start[0]),
                        float(current_point[1] - self._spot_selection_drag_start[1]),
                    ) < self._selection_drag_threshold()
                ):
                    return True
                self._spot_selection_rubber_band.show()
                top_left = QPointF(min(start_scene.x(), current_scene.x()), min(start_scene.y(), current_scene.y()))
                bottom_right = QPointF(max(start_scene.x(), current_scene.x()), max(start_scene.y(), current_scene.y()))
                rect = QRectF(top_left, bottom_right)
                viewport_rect = self.image_view.mapFromScene(rect).boundingRect()
                self._spot_selection_rubber_band.setGeometry(viewport_rect)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton and self._dragging_spots:
                self._dragging_spots = False
                self._drag_anchor = None
                self._drag_original_positions.clear()
                self._spot_selection_drag_button = None
                self._update_spot_overlays()
                self._mark_spot_edit_refresh_pending()
                self._save_processing_state_for_dataset()
                self._schedule_processing_state_save()
                self.status_label.setText(f"Moved {len(self._selected_spot_ids)} selected spots.")
                return True

            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self._spot_selection_drag_start is not None
                and self._spot_selection_drag_button == Qt.MouseButton.LeftButton
            ):
                if self._spot_selection_rubber_band is not None:
                    self._spot_selection_rubber_band.hide()
                end_point = self._image_point_from_mouse_event(event)
                start_x, start_y = self._spot_selection_drag_start
                drag_modifiers = self._spot_selection_drag_modifiers
                if end_point is None:
                    end_x, end_y = start_x, start_y
                else:
                    end_x, end_y = end_point
                drag_distance = hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
                if drag_distance < 2.0 and self._spot_selection_pressed_spot_id is not None:
                    clicked_spot_id = self._spot_selection_pressed_spot_id
                    if drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self._selected_spot_ids.add(clicked_spot_id)
                    else:
                        self._selected_spot_ids = {clicked_spot_id}
                    self._spot_selection_drag_start = None
                    self._spot_selection_drag_button = None
                    self._spot_selection_pressed_spot_id = None
                    self._spot_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                    self._update_spot_overlays()
                    self._update_spot_summary()
                    self._sync_spot_list_table_selection()
                    self._update_selection_dependent_plots(prompt_live_preview=True)
                    return True
                left = min(start_x, end_x)
                right = max(start_x, end_x)
                top = min(start_y, end_y)
                bottom = max(start_y, end_y)
                dragged_spot_ids = {
                    spot.spot_id
                    for spot in self._display_spots()
                    if left <= float(spot.center_x) <= right and top <= float(spot.center_y) <= bottom
                }
                if drag_modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._selected_spot_ids.update(dragged_spot_ids)
                else:
                    self._selected_spot_ids = set(dragged_spot_ids)
                self._spot_selection_drag_start = None
                self._spot_selection_drag_button = None
                self._spot_selection_pressed_spot_id = None
                self._spot_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                self._update_spot_overlays()
                self._update_spot_summary()
                self._sync_spot_list_table_selection()
                self._update_selection_dependent_plots(prompt_live_preview=True)
                return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._spot_selection_drag_start = None
                self._spot_selection_drag_button = None
                self._spot_selection_pressed_spot_id = None
                self._spot_selection_drag_modifiers = Qt.KeyboardModifier.NoModifier
                return True

        return super().eventFilter(watched, event)

    def _image_point_from_mouse_event(self, event) -> tuple[float, float] | None:
        scene_pos = self.image_view.mapToScene(event.position().toPoint())
        if not self.image_plot.sceneBoundingRect().contains(scene_pos):
            return None
        mouse_point = self.image_plot.vb.mapSceneToView(scene_pos)
        return float(mouse_point.x()), float(mouse_point.y())

    def _selection_drag_threshold(self) -> float:
        return 5.0

    def _find_spot_id_at(self, point: tuple[float, float]) -> int | None:
        nearest_id: int | None = None
        nearest_distance = float("inf")
        for spot in self._display_spots():
            distance = hypot(point[0] - spot.center_x, point[1] - spot.center_y)
            threshold = max(float(spot.radius_px) * 1.25, 8.0)
            if distance <= threshold and distance < nearest_distance:
                nearest_id = spot.spot_id
                nearest_distance = distance
        return nearest_id

    def _spot_by_id(self, spot_id: int) -> DetectedSpot | None:
        for spot in self._state.detected_spots:
            if spot.spot_id == spot_id:
                return spot
        return None

    def _array_position_for_spot(self, spot_id: int) -> tuple[int, int] | None:
        rows = max(int(self._state.spot_detection.array_rows), 0)
        cols = max(int(self._state.spot_detection.array_cols), 0)
        if rows <= 0 or cols <= 0:
            return None
        index = spot_id - 1
        if index < 0 or index >= rows * cols:
            return None
        return index // cols, index % cols

    def _array_label_for_spot(self, spot_id: int) -> str | None:
        position = self._array_position_for_spot(spot_id)
        if position is None:
            return str(int(spot_id)) if int(spot_id) > 0 else None
        row, col = position
        cols = max(int(self._state.spot_detection.array_cols), 0)
        if cols <= 0:
            return str(int(spot_id)) if int(spot_id) > 0 else None
        return str(row * cols + col + 1)

    def _select_neighbor_spot(self, key: int) -> bool:
        rows = max(int(self._state.spot_detection.array_rows), 0)
        cols = max(int(self._state.spot_detection.array_cols), 0)
        if rows <= 0 or cols <= 0 or not self._state.detected_spots:
            return False

        current_id = min(self._selected_spot_ids) if self._selected_spot_ids else 1
        current_position = self._array_position_for_spot(current_id)
        if current_position is None:
            return False

        row, col = current_position
        if key == Qt.Key.Key_Left:
            col = max(col - 1, 0)
        elif key == Qt.Key.Key_Right:
            col = min(col + 1, cols - 1)
        elif key == Qt.Key.Key_Up:
            row = max(row - 1, 0)
        elif key == Qt.Key.Key_Down:
            row = min(row + 1, rows - 1)
        new_id = row * cols + col + 1
        new_spot = self._spot_by_id(new_id)
        if new_spot is None:
            return False
        self._selected_spot_ids = {new_id}
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_selection_dependent_plots(prompt_live_preview=True)
        self._center_view_on_spot(new_spot)
        return True

    def _center_view_on_spot(self, spot: DetectedSpot) -> None:
        if self._current_processed_image is None:
            return

        x_range, y_range = self.image_plot.vb.viewRange()
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        image_height, image_width = self._current_processed_image.shape[:2]

        half_width = view_width / 2.0
        half_height = view_height / 2.0
        center_x = float(np.clip(spot.center_x, half_width, max(float(image_width) - half_width, half_width)))
        center_y = float(np.clip(spot.center_y, half_height, max(float(image_height) - half_height, half_height)))

        self.image_plot.vb.setRange(
            xRange=(center_x - half_width, center_x + half_width),
            yRange=(center_y - half_height, center_y + half_height),
            padding=0.0,
        )

    def _center_view_on_landmark(self, landmark: ChromaticLandmarkObservation) -> None:
        if self._current_processed_image is None:
            return
        x_range, y_range = self.image_plot.vb.viewRange()
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        image_height, image_width = self._current_processed_image.shape[:2]
        half_width = view_width / 2.0
        half_height = view_height / 2.0
        center_x = float(np.clip(float(landmark.x_px), half_width, max(float(image_width) - half_width, half_width)))
        center_y = float(np.clip(float(landmark.y_px), half_height, max(float(image_height) - half_height, half_height)))
        self.image_plot.vb.setRange(
            xRange=(center_x - half_width, center_x + half_width),
            yRange=(center_y - half_height, center_y + half_height),
            padding=0.0,
        )

    def _select_chromatic_feature(self, feature_id: int, *, center_view: bool = True) -> bool:
        feature_ids = self._expected_chromatic_feature_ids()
        if feature_id not in feature_ids:
            return False
        self._selected_landmark_id = int(feature_id)
        self._chromatic_landmark_marker_id = int(feature_id)
        self.chromatic_landmark_id_spin.blockSignals(True)
        self.chromatic_landmark_id_spin.setValue(int(feature_id))
        self.chromatic_landmark_id_spin.blockSignals(False)
        mark = self._current_landmark(int(feature_id))
        if mark is not None and center_view:
            self._center_view_on_landmark(mark)
        self._update_landmark_overlays()
        return True

    def _move_selected_landmark(self, dx: float, dy: float) -> bool:
        if self._selected_landmark_id is None:
            return False
        mark = self._current_landmark(int(self._selected_landmark_id))
        if mark is None:
            return False
        self._prepare_undo_snapshot("Chromatic landmarks")
        max_x = float(self._current_processed_image.shape[1] - 1) if self._current_processed_image is not None else float(mark.x_px)
        max_y = float(self._current_processed_image.shape[0] - 1) if self._current_processed_image is not None else float(mark.y_px)
        point = (
            float(np.clip(float(mark.x_px) + dx, 0.0, max_x)),
            float(np.clip(float(mark.y_px) + dy, 0.0, max_y)),
        )
        self._upsert_current_landmark(int(self._selected_landmark_id), point, clear_models=True)
        self._center_view_on_landmark(mark)
        self._commit_prepared_undo_snapshot()
        return True

    def _switch_chromatic_feature(self, direction: int) -> bool:
        feature_ids = self._expected_chromatic_feature_ids()
        if not feature_ids:
            return False
        current_id = int(self._selected_landmark_id or self._chromatic_landmark_marker_id or feature_ids[0])
        try:
            current_index = feature_ids.index(current_id)
        except ValueError:
            current_index = 0
        next_index = min(max(current_index + int(direction), 0), len(feature_ids) - 1)
        return self._select_chromatic_feature(feature_ids[next_index], center_view=True)

    def _move_selected_spots(self, dx: float, dy: float) -> None:
        if not self._selected_spot_ids:
            return
        self._append_workflow_log(f"Spots | move {len(self._selected_spot_ids)} by dx={dx:g}, dy={dy:g}", level="debug")
        self._prepare_undo_snapshot("Move spots")
        for spot in self._state.detected_spots:
            if spot.spot_id not in self._selected_spot_ids:
                continue
            spot.center_x, spot.center_y = self._clamp_spot_position(spot, spot.center_x + dx, spot.center_y + dy)
        self._update_spot_overlays()
        self._mark_spot_edit_refresh_pending()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()

    def _add_spot_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding spots.")
            return
        self._push_undo_point("Add spot")
        radius = float(max(self._state.spot_detection.spot_radius_px, 1))
        provisional = DetectedSpot(
            spot_id=len(self._state.detected_spots) + 1,
            center_x=point[0],
            center_y=point[1],
            radius_px=radius,
            score=0.0,
        )
        provisional.center_x, provisional.center_y = self._clamp_spot_position(provisional, provisional.center_x, provisional.center_y)
        self._state.detected_spots.append(provisional)
        self._selected_spot_ids = {provisional.spot_id}
        self._update_spot_overlays()
        self._mark_spot_edit_refresh_pending()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added spot {provisional.spot_id}.")

    def _clamp_spot_position(self, spot: DetectedSpot, x: float, y: float) -> tuple[float, float]:
        if self._current_processed_image is None:
            return x, y
        image_height, image_width = self._current_processed_image.shape[:2]
        radius = max(float(spot.radius_px), 1.0)
        clamped_x = min(max(x, radius), max(float(image_width - 1) - radius, radius))
        clamped_y = min(max(y, radius), max(float(image_height - 1) - radius, radius))
        return clamped_x, clamped_y

    def _request_spot_metrics_refresh(
        self,
        *,
        save_after: bool,
        status_text: str | None = None,
        refresh_histogram: bool = True,
    ) -> bool:
        if self._current_processed_image is None or not self._state.detected_spots or not self._is_current_reference_image():
            return False
        image_key = self._current_image_key
        if image_key is None:
            return False
        self._spot_metrics_request_id += 1
        request_id = self._spot_metrics_request_id
        image = self._current_processed_image
        settings = deepcopy(self._state.spot_detection)
        spots = deepcopy(self._state.detected_spots)
        worker = FunctionWorker(_refresh_spot_metrics_task, image, settings, spots, self._current_external_mask())
        self._begin_busy("Refreshing spot metrics...")
        self._append_workflow_log("Spot metrics refresh start", level="info")
        worker.signals.result.connect(
            lambda refreshed_spots,
            request_id=request_id,
            image_key=image_key,
            save_after=save_after,
            status_text=status_text,
            refresh_histogram=refresh_histogram: self._on_spot_metrics_ready(
                request_id,
                image_key,
                refreshed_spots,
                save_after,
                status_text,
                refresh_histogram,
            )
        )
        worker.signals.error.connect(lambda message: self._on_spot_metrics_failed(message))
        self._thread_pool.start(worker)
        return True

    def _on_spot_metrics_ready(
        self,
        request_id: int,
        image_key: tuple[int, float],
        refreshed_spots: list[DetectedSpot],
        save_after: bool,
        status_text: str | None,
        refresh_histogram: bool,
    ) -> None:
        self._end_busy()
        if request_id != self._spot_metrics_request_id:
            return
        if self._current_image_key != image_key:
            return
        self._state.detected_spots = refreshed_spots
        if refresh_histogram:
            self._invalidate_image_analysis_caches()
            self._schedule_histogram_refresh()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._append_workflow_log(
            f"Spot metrics refresh done | spots {len(refreshed_spots)}",
            level="success",
        )
        if save_after:
            self._schedule_processing_state_save()
        if status_text:
            self._set_status_text(status_text)

    def _on_spot_metrics_failed(self, message: str) -> None:
        self._end_busy()
        self._append_workflow_log(f"Spot metrics refresh failed | {message}", level="error")
        self._background_error("Spot metric refresh", message)

    def _refresh_spot_metrics_if_enabled(self) -> bool:
        if not self.spot_editor_section.is_applied() or self._current_processed_image is None or not self._is_current_reference_image():
            return False
        return self._request_spot_metrics_refresh(save_after=False, refresh_histogram=False)

    def _mark_spot_edit_refresh_pending(self) -> None:
        if self._active_tool == "spots":
            self._commit_prepared_undo_snapshot()
            self._spot_edit_refresh_pending = True
            self._save_processing_state_for_dataset()
            self._schedule_processing_state_save()
            self.status_label.setText("Spot positions updated. Fit refresh is deferred until Edit spots is turned off.")

    def _finalize_spot_edit_refresh(self) -> None:
        if not self._spot_edit_refresh_pending:
            return
        self._spot_edit_refresh_pending = False
        self._commit_prepared_undo_snapshot()
        self._invalidate_background_profile_cache()
        if self._showing_background_profile_main:
            self._update_background_profile_preview()
        if self._request_spot_metrics_refresh(
            save_after=True,
            status_text="Spot fit metrics refreshed after leaving Edit spots.",
            refresh_histogram=True,
        ):
            return
        self._schedule_histogram_refresh()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self.status_label.setText("Spot fit metrics refreshed after leaving Edit spots.")

    def _refresh_histogram_if_available(self) -> None:
        self._plot_manager.refresh_histogram_if_available()

    def _apply_histogram_log_mode(self, *, refresh: bool = True) -> None:
        self._plot_manager.apply_histogram_log_mode(refresh=refresh)

    def _on_histogram_y_scale_toggled(self, checked: bool) -> None:
        self._plot_manager.on_histogram_y_scale_toggled(checked)

    def _histogram_log_y_max(self) -> float:
        return self._plot_manager.histogram_log_y_max()

    def _clamp_histogram_log_range(self) -> None:
        self._plot_manager.clamp_histogram_log_range()

    def _on_histogram_view_range_changed(self, *_args) -> None:
        self._plot_manager.on_histogram_view_range_changed(*_args)

    def _set_spectrum_summary_text(self, text: str) -> None:
        self._plot_manager.set_spectrum_summary_text(text)

    def _prepare_sensorgram_payload(self) -> tuple[tuple[object, ...], list[tuple[int, tuple[object, ...]]]] | None:
        if self._state.dataset is None:
            return None
        selected_spot_ids = self._selected_spectrum_spot_ids()
        if not selected_spot_ids:
            return None
        selected_spot_id_set = set(selected_spot_ids)
        selected_source_spots = [
            deepcopy(spot)
            for spot in self._state.detected_spots
            if spot.spot_id in selected_spot_id_set
        ]
        if not selected_source_spots:
            return None
        frames = self._available_analysis_frames()
        if not frames:
            return None
        frame_payloads: list[tuple[int, tuple[object, ...]]] = []
        frame_signatures: list[tuple[object, ...]] = []
        payload_cache_hits = 0
        payload_cache_builds = 0
        worker_count = max(1, min(int(os.cpu_count() or 1), 4, len(frames)))
        if worker_count > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        self._cached_sensorgram_frame_payload,
                        int(frame),
                        selected_spot_ids,
                        selected_source_spots,
                    ): int(frame)
                    for frame in frames
                }
                prepared_frames: list[tuple[int, tuple[object, ...] | None]] = []
                for future in as_completed(future_map):
                    frame = int(future_map[future])
                    payload = future.result()
                    prepared_frames.append((frame, payload))
            prepared_frames.sort(key=lambda item: item[0])
            iterable_frames = prepared_frames
        else:
            iterable_frames = [
                (int(frame), self._cached_sensorgram_frame_payload(frame, selected_spot_ids, selected_source_spots))
                for frame in frames
            ]
        for frame, payload in iterable_frames:
            if payload is None:
                continue
            payload_signature = self._sensorgram_frame_payload_signature(frame, selected_spot_ids, selected_source_spots)
            if payload_signature is not None:
                with self._analysis_cache_lock:
                    if payload_signature in self._sensorgram_frame_payload_cache:
                        payload_cache_hits += 1
                    else:
                        payload_cache_builds += 1
            frame_payloads.append((int(frame), payload))
            frame_signatures.append(
                (
                    int(frame),
                    tuple(
                        self._preprocessing_signature((int(frame), float(wavelength)))
                        for wavelength in self._wavelength_values
                    ),
                )
            )
        if not frame_payloads:
            return None
        dataset_key = str(self._state.dataset.folder)
        signature = (
            dataset_key,
            tuple(selected_spot_ids),
            self._spot_signature(selected_source_spots),
            self._analysis_metric_key(),
            int(self._analysis_poly_order()),
            tuple(round(float(value), 6) for value in self._wavelength_values),
            tuple(frame_signatures),
            round(float(self._state.spot_detection.ring_inner_radius_px), 3),
            round(float(self._state.spot_detection.ring_outer_radius_px), 3),
        )
        logging.getLogger("lspr_imaging_app.workflow").debug(
            "SG payload summary | hit=%s build=%s | frames=%s",
            int(payload_cache_hits),
            int(payload_cache_builds),
            len(frame_payloads),
        )
        return signature, frame_payloads

    def _sensorgram_signature_for_selection(
        self,
        frames: list[int],
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot],
    ) -> tuple[object, ...] | None:
        if self._state.dataset is None or not selected_spot_ids or not selected_source_spots or not frames:
            return None
        frame_signatures: list[tuple[object, ...]] = []
        for frame in frames:
            frame_signatures.append(
                (
                    int(frame),
                    tuple(
                        self._preprocessing_signature((int(frame), float(wavelength)))
                        for wavelength in self._wavelength_values
                    ),
                )
            )
        dataset_key = str(self._state.dataset.folder)
        return (
            dataset_key,
            tuple(selected_spot_ids),
            self._spot_signature(selected_source_spots),
            self._analysis_metric_key(),
            int(self._analysis_poly_order()),
            tuple(round(float(value), 6) for value in self._wavelength_values),
            tuple(frame_signatures),
            round(float(self._state.spot_detection.ring_inner_radius_px), 3),
            round(float(self._state.spot_detection.ring_outer_radius_px), 3),
        )

    def _sensorgram_frame_payload_signature(
        self,
        frame: int,
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot],
    ) -> tuple[object, ...] | None:
        if self._state.dataset is None or not selected_spot_ids or not selected_source_spots:
            return None
        return (
            str(self._state.dataset.folder),
            int(frame),
            tuple(selected_spot_ids),
            self._spot_signature(selected_source_spots),
            tuple(round(float(value), 6) for value in self._wavelength_values),
            tuple(
                self._preprocessing_signature((int(frame), float(wavelength)))
                for wavelength in self._wavelength_values
            ),
            round(float(self._state.spot_detection.ring_inner_radius_px), 3),
            round(float(self._state.spot_detection.ring_outer_radius_px), 3),
        )

    def _cached_sensorgram_frame_payload(
        self,
        frame: int,
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot],
    ) -> tuple[object, ...] | None:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        signature = self._sensorgram_frame_payload_signature(frame, selected_spot_ids, selected_source_spots)
        if signature is None:
            return None
        with self._analysis_cache_lock:
            cached = self._sensorgram_frame_payload_cache.get(signature)
            if cached is not None:
                self._sensorgram_frame_payload_cache.move_to_end(signature)
                logger.debug(
                    "SG payload cache hit | frame=%s spots=%s",
                    int(frame),
                    len(selected_spot_ids),
                )
                return cached
        payload = self._prepare_absorbance_spectrum_payload_for_frame(frame, selected_spot_ids, selected_source_spots)
        if payload is None:
            return None
        with self._analysis_cache_lock:
            self._sensorgram_frame_payload_cache[signature] = payload
            self._sensorgram_frame_payload_cache.move_to_end(signature)
            while len(self._sensorgram_frame_payload_cache) > self.SENSORGRAM_FRAME_PAYLOAD_CACHE_SIZE:
                self._sensorgram_frame_payload_cache.popitem(last=False)
        logger.debug(
            "SG payload cache built | frame=%s spots=%s",
            int(frame),
            len(selected_spot_ids),
        )
        return payload

    def _schedule_sensorgram_refresh(self) -> None:
        if not self._startup_ready or self._startup_restore_in_progress or not self._analysis_live_preview_enabled:
            return
        if self._sensorgram_refresh_timer.isActive():
            self._sensorgram_refresh_timer.stop()
        self._sensorgram_refresh_timer.start()

    def _refresh_sensorgram(self) -> None:
        if not self._startup_ready or self._startup_restore_in_progress or not self._analysis_live_preview_enabled:
            return
        self._analysis_controller.calculate_sensorgram()

    def _mark_absorbance_spectrum_dirty(self) -> None:
        self._absorbance_spectrum_dirty = True
        if not self._analysis_enabled:
            self._set_spectrum_summary_text("Analysis calculations are disabled for this panel.")
            self._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if self._state.dataset is None:
            self._set_spectrum_summary_text("Load a dataset to show absorbance spectrum.")
            self._clear_sensorgram("Load a dataset to build the fitted sensorgram.")
            return
        if self._chromatic_setup_active:
            self._set_spectrum_summary_text("Spectral absorbance is hidden during chromatic setup.")
            self._clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        if not self._selected_spectrum_spot_ids():
            self._set_spectrum_summary_text("Select spots to show absorbance spectrum.")
            self._clear_sensorgram("Select spots before calculating the sensorgram.")
            return
        self._set_spectrum_summary_text(
            f"{self._spectrum_selection_label()} | Spectrum is out of date | Press Calculate spectrum"
        )
        if not self._analysis_live_preview_enabled:
            self._mark_sensorgram_stale()

    def _selected_spectrum_spot_ids(self) -> tuple[int, ...]:
        return tuple(sorted(int(spot_id) for spot_id in self._selected_spot_ids))

    def _selected_source_spots_snapshot(self) -> list[DetectedSpot]:
        selected_ids = self._selected_spectrum_spot_ids()
        if not selected_ids:
            self._selected_source_spots_cache_signature = None
            self._selected_source_spots_cache_value = tuple()
            return []
        signature_parts: list[object] = [selected_ids]
        source_spots: list[DetectedSpot] = []
        spot_by_id = {int(spot.spot_id): spot for spot in self._state.detected_spots}
        for spot_id in selected_ids:
            spot = spot_by_id.get(int(spot_id))
            if spot is None:
                self._selected_source_spots_cache_signature = None
                self._selected_source_spots_cache_value = tuple()
                return []
            source_spots.append(spot)
            signature_parts.append(
                (
                    int(spot.spot_id),
                    round(float(spot.center_x), 3),
                    round(float(spot.center_y), 3),
                    round(float(spot.radius_px), 3),
                    round(float(spot.ring_inner_diameter_px or 0.0), 3),
                    round(float(spot.ring_outer_diameter_px or 0.0), 3),
                    spot.spot_color_hex or "",
                    spot.ring_color_hex or "",
                )
            )
        signature = tuple(signature_parts)
        if self._selected_source_spots_cache_signature == signature and self._selected_source_spots_cache_value:
            return list(self._selected_source_spots_cache_value)
        copied = tuple(deepcopy(spot) for spot in source_spots)
        self._selected_source_spots_cache_signature = signature
        self._selected_source_spots_cache_value = copied
        return list(copied)

    def _spectrum_selection_label(self) -> str:
        selected_ids = self._selected_spectrum_spot_ids()
        if not selected_ids:
            return "No spots"
        if self._selected_spot_ids:
            noun = "spot" if len(selected_ids) == 1 else "spots"
            return f"{len(selected_ids)} selected {noun}"
        noun = "spot" if len(selected_ids) == 1 else "spots"
        return f"All {len(selected_ids)} {noun}"

    def _clear_absorbance_spectrum(self, summary_text: str) -> None:
        self._plot_manager.clear_absorbance_spectrum(summary_text)

    def _clear_spectrum_series_items(self) -> None:
        self._plot_manager.clear_spectrum_series_items()

    def _spot_spectrum_color(self, spot_id: int) -> QColor:
        return self._plot_manager.spot_spectrum_color(spot_id)

    def _add_spectrum_series(
        self,
        *,
        spot_id: int,
        result: AbsorbanceSpectrumResult,
        label: str | None = None,
        highlighted: bool = False,
        dimmed: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None] | None:
        return self._plot_manager.add_spectrum_series(
            spot_id=spot_id,
            result=result,
            label=label,
            highlighted=highlighted,
            dimmed=dimmed,
        )

    def _analysis_fit_result_from_spectrum(self, result: AbsorbanceSpectrumResult) -> FitResult | None:
        return self._plot_manager.analysis_fit_result_from_spectrum(result)

    def _update_single_frame_sensorgram(self, metric_value: float | None, metric_signal: float | None) -> None:
        self._plot_manager.update_single_frame_sensorgram(metric_value, metric_signal)

    def _schedule_absorbance_spectrum_refresh(self) -> None:
        if not self._startup_ready or self._startup_restore_in_progress:
            return
        self._mark_absorbance_spectrum_dirty()
        if self._analysis_live_preview_enabled:
            if self._absorbance_spectrum_timer.isActive():
                self._absorbance_spectrum_timer.stop()
            self._absorbance_spectrum_timer.start()

    def _start_absorbance_spectrum_preparation(
        self,
        signature: tuple[object, ...],
        selected_source_spots: list[DetectedSpot] | None = None,
    ) -> None:
        if self._absorbance_prep_running:
            return
        self._absorbance_prep_request_id += 1
        request_id = self._absorbance_prep_request_id
        self._absorbance_prep_running = True
        self._absorbance_prep_request_signature = signature
        self._absorbance_prep_started_at = time.perf_counter()
        self._append_workflow_log("Spec prep start", level="info")
        self._begin_busy("Preparing absorbance spectrum...", determinate=False)
        QApplication.processEvents()
        worker = FunctionWorker(self._prepare_absorbance_spectrum_payload, selected_source_spots)
        worker.signals.result.connect(
            lambda prepared, request_id=request_id, signature=signature: self._on_absorbance_spectrum_payload_ready(
                request_id,
                signature,
                prepared,
            )
        )
        worker.signals.error.connect(
            lambda message, request_id=request_id: self._on_absorbance_spectrum_payload_failed(request_id, message)
        )
        self._thread_pool.start(worker)

    def _on_absorbance_spectrum_payload_ready(
        self,
        request_id: int,
        expected_signature: tuple[object, ...],
        prepared: tuple[tuple[object, ...], tuple[object, ...]] | None,
    ) -> None:
        if request_id != self._absorbance_prep_request_id:
            return
        self._absorbance_prep_running = False
        self._absorbance_prep_started_at = None
        self._absorbance_prep_request_signature = None
        if prepared is None:
            self._end_busy("Select spots to show absorbance spectrum.")
            return
        signature, payload = prepared
        if signature != expected_signature:
            self._absorbance_spectrum_dirty = True
            self._end_busy("Select spots to show absorbance spectrum.")
            return
        if self._absorbance_prep_started_at is not None:
            self._append_workflow_log(
                f"Spec prep done | {self._format_elapsed_seconds(time.perf_counter() - self._absorbance_prep_started_at)}",
                level="success",
            )
        self._pending_absorbance_spectrum_payload = (signature, payload)
        self._start_pending_absorbance_spectrum_refresh(reuse_busy=True)

    def _on_absorbance_spectrum_payload_failed(self, request_id: int, message: str) -> None:
        if request_id != self._absorbance_prep_request_id:
            return
        self._absorbance_prep_running = False
        self._absorbance_prep_started_at = None
        self._absorbance_prep_request_signature = None
        self._end_busy()
        self._background_error("Spectral absorbance prep", message)

    def _cached_absorbance_result_for_selection(
        self,
        signature: tuple[object, ...],
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot] | None = None,
    ) -> AbsorbanceSpectrumResult | None:
        if not selected_spot_ids:
            return None
        if len(selected_spot_ids) == 1:
            for cache_signature, cached_result in reversed(list(self._spot_absorbance_cache.items())):
                if self._absorbance_frame_signature(cache_signature) != self._absorbance_frame_signature(signature):
                    continue
                if self._absorbance_result_covers_spot_ids(cached_result, selected_spot_ids):
                    return cached_result
        frame_signature = self._absorbance_frame_signature(signature)
        if frame_signature is not None:
            cached_result = self._absorbance_frame_cache.get(frame_signature)
            if cached_result is not None and self._absorbance_result_covers_spot_ids(cached_result, selected_spot_ids):
                return cached_result
        for cache_signature, cached_result in reversed(list(self._absorbance_spectrum_cache.items())):
            if self._absorbance_frame_signature(cache_signature) != frame_signature:
                continue
            if self._absorbance_result_covers_spot_ids(cached_result, selected_spot_ids):
                return cached_result
        if selected_source_spots:
            cached_from_spots = self._cached_absorbance_result_from_spot_cache(selected_source_spots)
            if cached_from_spots is not None:
                return cached_from_spots
        return None

    def _absorbance_spectrum_signature_for_source_spots(
        self,
        selected_source_spots: list[DetectedSpot],
    ) -> tuple[object, ...] | None:
        frame = self._current_frame()
        if frame is None or not selected_source_spots:
            return None
        selected_spot_ids = tuple(int(spot.spot_id) for spot in selected_source_spots)
        return (
            int(frame),
            tuple(round(float(value), 6) for value in self._wavelength_values),
            selected_spot_ids,
            tuple(
                self._chromatic_signature_for_image_key((int(frame), float(wavelength)))
                for wavelength in self._wavelength_values
            ),
        )

    def _absorbance_spectrum_signature(self) -> tuple[object, ...] | None:
        return self._absorbance_spectrum_signature_for_source_spots(self._selected_source_spots_snapshot())

    def _spot_absorbance_signature(self, spot: DetectedSpot) -> tuple[object, ...] | None:
        frame = self._current_frame()
        if frame is None or not self._wavelength_values:
            return None
        return _spot_absorbance_signature(
            int(frame),
            tuple(float(value) for value in self._wavelength_values),
            spot,
            tuple(
                self._chromatic_signature_for_image_key((int(frame), float(wavelength)))
                for wavelength in self._wavelength_values
            ),
        )

    def _spot_has_cached_absorbance(self, spot: DetectedSpot) -> bool:
        signature = self._spot_absorbance_signature(spot)
        return signature is not None and self._spot_absorbance_cache.get(signature) is not None

    @staticmethod
    def _analysis_cache_signature_to_json(value):
        if isinstance(value, tuple):
            return [MainWindow._analysis_cache_signature_to_json(item) for item in value]
        if isinstance(value, list):
            return [MainWindow._analysis_cache_signature_to_json(item) for item in value]
        return value

    @staticmethod
    def _analysis_cache_signature_from_json(value):
        if isinstance(value, list):
            return tuple(MainWindow._analysis_cache_signature_from_json(item) for item in value)
        return value

    @staticmethod
    def _absorbance_frame_signature(signature: tuple[object, ...] | None) -> tuple[object, ...] | None:
        if signature is None or len(signature) < 4:
            return None
        return (signature[0], signature[1], signature[3])

    @staticmethod
    def _absorbance_result_covers_spot_ids(result: AbsorbanceSpectrumResult, selected_spot_ids: tuple[int, ...]) -> bool:
        if not selected_spot_ids:
            return False
        if not result.spot_results:
            return len(selected_spot_ids) == 1
        available_ids = {int(spot_id) for spot_id in result.spot_results.keys()}
        return all(int(spot_id) in available_ids for spot_id in selected_spot_ids)

    def _cached_absorbance_result_from_spot_cache(
        self,
        selected_source_spots: list[DetectedSpot],
    ) -> AbsorbanceSpectrumResult | None:
        if not selected_source_spots:
            return None
        spot_results: dict[int, AbsorbanceSpectrumResult] = {}
        for spot in selected_source_spots:
            spot_signature = self._spot_absorbance_signature(spot)
            if spot_signature is None:
                return None
            cached_result = self._spot_absorbance_cache.get(spot_signature)
            if cached_result is None:
                return None
            spot_results[int(spot.spot_id)] = cached_result
        first_result = next(iter(spot_results.values()), None)
        if first_result is None:
            return None
        return AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(first_result.wavelengths_nm, dtype=np.float64),
            absorbance=np.asarray(first_result.absorbance, dtype=np.float64),
            spot_mean=np.asarray(first_result.spot_mean, dtype=np.float64),
            ring_mean=np.asarray(first_result.ring_mean, dtype=np.float64),
            spot_pixel_count=np.asarray(first_result.spot_pixel_count, dtype=np.int32),
            ring_pixel_count=np.asarray(first_result.ring_pixel_count, dtype=np.int32),
            load_seconds=float(first_result.load_seconds),
            roi_seconds=float(first_result.roi_seconds),
            fit_seconds=float(first_result.fit_seconds),
            total_seconds=float(first_result.total_seconds),
            spot_results=spot_results,
        )

    def _absorbance_roi_mask_signature(
        self,
        image_shape: tuple[int, int],
        selected_spots: list[DetectedSpot],
        selected_spot_ids: tuple[int, ...],
        affine_matrix: np.ndarray | None,
    ) -> tuple[object, ...]:
        affine_signature = None
        if affine_matrix is not None:
            affine_signature = tuple(round(float(value), 6) for value in np.asarray(affine_matrix, dtype=np.float64).ravel())
        return (
            tuple(int(value) for value in image_shape[:2]),
            tuple(int(spot_id) for spot_id in selected_spot_ids),
            self._spot_signature(selected_spots),
            affine_signature,
            round(float(self._state.spot_detection.ring_inner_radius_px), 3),
            round(float(self._state.spot_detection.ring_outer_radius_px), 3),
        )

    def _cached_absorbance_roi_mask_cache(
        self,
        image_shape: tuple[int, int],
        selected_spots: list[DetectedSpot],
        selected_spot_ids: tuple[int, ...],
        affine_matrix: np.ndarray | None,
        source_spots: list[DetectedSpot] | None = None,
    ) -> dict[str, object]:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        signature = self._absorbance_roi_mask_signature(image_shape, selected_spots, selected_spot_ids, affine_matrix)
        with self._analysis_cache_lock:
            cached = self._absorbance_roi_mask_cache.get(signature)
            if cached is not None:
                self._absorbance_roi_mask_cache.move_to_end(signature)
                logger.debug(
                    "ROI cache hit | shape=%sx%s spots=%s",
                    int(image_shape[0]),
                    int(image_shape[1]),
                    len(selected_spots),
                )
                return cached
        selected_ids_local = tuple(int(spot_id) for spot_id in selected_spot_ids)
        combined_spot_mask, combined_ring_mask = _selected_roi_masks_for_spectrum(
            image_shape,
            source_spots if source_spots is not None else selected_spots,
            selected_ids_local,
            float(self._state.spot_detection.ring_inner_radius_px),
            float(self._state.spot_detection.ring_outer_radius_px),
            affine_matrix,
        )
        per_spot_masks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for spot in selected_spots:
            per_spot_masks[int(spot.spot_id)] = _selected_roi_masks_for_spectrum(
                image_shape,
                [spot],
                (int(spot.spot_id),),
                float(self._state.spot_detection.ring_inner_radius_px),
                float(self._state.spot_detection.ring_outer_radius_px),
                affine_matrix,
            )
        cached_value = {
            "shape": tuple(int(value) for value in image_shape[:2]),
            "combined": (combined_spot_mask, combined_ring_mask),
            "per_spot": per_spot_masks,
        }
        with self._analysis_cache_lock:
            self._absorbance_roi_mask_cache[signature] = cached_value
            self._absorbance_roi_mask_cache.move_to_end(signature)
            while len(self._absorbance_roi_mask_cache) > self.ABSORBANCE_ROI_MASK_CACHE_SIZE:
                self._absorbance_roi_mask_cache.popitem(last=False)
        logger.debug(
            "ROI cache built | shape=%sx%s spots=%s",
            int(image_shape[0]),
            int(image_shape[1]),
            len(selected_spots),
        )
        return cached_value

    @staticmethod
    def _serialize_absorbance_result(result: AbsorbanceSpectrumResult) -> dict:
        return {
            "wavelengths_nm": [float(value) for value in np.asarray(result.wavelengths_nm, dtype=np.float64)],
            "absorbance": [float(value) for value in np.asarray(result.absorbance, dtype=np.float64)],
            "spot_mean": [float(value) for value in np.asarray(result.spot_mean, dtype=np.float64)],
            "ring_mean": [float(value) for value in np.asarray(result.ring_mean, dtype=np.float64)],
            "spot_pixel_count": [int(value) for value in np.asarray(result.spot_pixel_count, dtype=np.int32)],
            "ring_pixel_count": [int(value) for value in np.asarray(result.ring_pixel_count, dtype=np.int32)],
            "load_seconds": float(result.load_seconds),
            "roi_seconds": float(result.roi_seconds),
            "fit_seconds": float(result.fit_seconds),
            "total_seconds": float(result.total_seconds),
            "spot_results": {
                str(int(spot_id)): MainWindow._serialize_absorbance_result(spot_result)
                for spot_id, spot_result in (result.spot_results or {}).items()
            },
        }

    @staticmethod
    def _deserialize_absorbance_result(payload) -> AbsorbanceSpectrumResult:
        if not isinstance(payload, dict):
            return AbsorbanceSpectrumResult(
                wavelengths_nm=np.asarray([], dtype=np.float64),
                absorbance=np.asarray([], dtype=np.float64),
                spot_mean=np.asarray([], dtype=np.float64),
                ring_mean=np.asarray([], dtype=np.float64),
                spot_pixel_count=np.asarray([], dtype=np.int32),
                ring_pixel_count=np.asarray([], dtype=np.int32),
            )
        raw_spot_results = payload.get("spot_results", {})
        spot_results: dict[int, AbsorbanceSpectrumResult] = {}
        if isinstance(raw_spot_results, dict):
            for key, value in raw_spot_results.items():
                try:
                    spot_id = int(key)
                except Exception:
                    continue
                spot_results[spot_id] = MainWindow._deserialize_absorbance_result(value)
        return AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(payload.get("wavelengths_nm", []), dtype=np.float64),
            absorbance=np.asarray(payload.get("absorbance", []), dtype=np.float64),
            spot_mean=np.asarray(payload.get("spot_mean", []), dtype=np.float64),
            ring_mean=np.asarray(payload.get("ring_mean", []), dtype=np.float64),
            spot_pixel_count=np.asarray(payload.get("spot_pixel_count", []), dtype=np.int32),
            ring_pixel_count=np.asarray(payload.get("ring_pixel_count", []), dtype=np.int32),
            load_seconds=float(payload.get("load_seconds", 0.0)),
            roi_seconds=float(payload.get("roi_seconds", 0.0)),
            fit_seconds=float(payload.get("fit_seconds", 0.0)),
            total_seconds=float(payload.get("total_seconds", 0.0)),
            spot_results=spot_results,
        )

    @staticmethod
    def _serialize_sensorgram_result(result: SensorgramComputationResult) -> dict:
        return {
            "frame_indices": [int(value) for value in np.asarray(result.frame_indices, dtype=np.int32)],
            "metric_values": [float(value) for value in np.asarray(result.metric_values, dtype=np.float64)],
            "metric_signal": [float(value) for value in np.asarray(result.metric_signal, dtype=np.float64)],
            "completed_count": int(result.completed_count),
            "total_count": int(result.total_count),
            "prep_seconds": float(result.prep_seconds),
            "fit_seconds": float(result.fit_seconds),
            "total_seconds": float(result.total_seconds),
            "cancelled": bool(result.cancelled),
        }

    @staticmethod
    def _deserialize_sensorgram_result(payload) -> SensorgramComputationResult:
        if not isinstance(payload, dict):
            return SensorgramComputationResult(
                frame_indices=np.asarray([], dtype=np.int32),
                metric_values=np.asarray([], dtype=np.float64),
                metric_signal=np.asarray([], dtype=np.float64),
                completed_count=0,
                total_count=0,
                cancelled=False,
            )
        return SensorgramComputationResult(
            frame_indices=np.asarray(payload.get("frame_indices", []), dtype=np.int32),
            metric_values=np.asarray(payload.get("metric_values", []), dtype=np.float64),
            metric_signal=np.asarray(payload.get("metric_signal", []), dtype=np.float64),
            completed_count=int(payload.get("completed_count", 0)),
            total_count=int(payload.get("total_count", 0)),
            prep_seconds=float(payload.get("prep_seconds", 0.0)),
            fit_seconds=float(payload.get("fit_seconds", 0.0)),
            total_seconds=float(payload.get("total_seconds", 0.0)),
            cancelled=bool(payload.get("cancelled", False)),
        )

    def _analysis_cache_payload(self) -> dict:
        payload: dict[str, list[dict[str, object]]] = {
            "absorbance_spectrum_cache": [],
            "absorbance_frame_cache": [],
            "spot_absorbance_cache": [],
            "sensorgram_cache": [],
        }
        for signature, result in self._absorbance_spectrum_cache.items():
            payload["absorbance_spectrum_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self._absorbance_frame_cache.items():
            payload["absorbance_frame_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self._spot_absorbance_cache.items():
            payload["spot_absorbance_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_absorbance_result(result),
                }
            )
        for signature, result in self._sensorgram_cache.items():
            payload["sensorgram_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_sensorgram_result(result),
                }
            )
        return payload

    def _restore_analysis_caches(self, payload: dict | None) -> None:
        self._absorbance_spectrum_cache.clear()
        self._absorbance_frame_cache.clear()
        self._spot_absorbance_cache.clear()
        self._sensorgram_cache.clear()
        if not isinstance(payload, dict):
            return
        raw_absorbance = payload.get("absorbance_spectrum_cache", [])
        if isinstance(raw_absorbance, list):
            for entry in raw_absorbance:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self._absorbance_spectrum_cache[signature] = result
                frame_signature = self._absorbance_frame_signature(signature)
                if frame_signature is not None:
                    self._absorbance_frame_cache[frame_signature] = result
                    self._absorbance_frame_cache.move_to_end(frame_signature)
                    while len(self._absorbance_frame_cache) > self.ABSORBANCE_FRAME_CACHE_SIZE:
                        self._absorbance_frame_cache.popitem(last=False)
        raw_absorbance_frames = payload.get("absorbance_frame_cache", [])
        if isinstance(raw_absorbance_frames, list):
            for entry in raw_absorbance_frames:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self._absorbance_frame_cache[signature] = result
                self._absorbance_frame_cache.move_to_end(signature)
                while len(self._absorbance_frame_cache) > self.ABSORBANCE_FRAME_CACHE_SIZE:
                    self._absorbance_frame_cache.popitem(last=False)
        raw_spot_absorbance = payload.get("spot_absorbance_cache", [])
        if isinstance(raw_spot_absorbance, list):
            for entry in raw_spot_absorbance:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_absorbance_result(entry.get("result"))
                if signature is None:
                    continue
                self._spot_absorbance_cache[signature] = result
        raw_sensorgram = payload.get("sensorgram_cache", [])
        if isinstance(raw_sensorgram, list):
            for entry in raw_sensorgram:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_sensorgram_result(entry.get("result"))
                if signature is None:
                    continue
                self._sensorgram_cache[signature] = result

    def _prepare_absorbance_spectrum_payload_for_frame(
        self,
        frame: int,
        selected_spot_ids: tuple[int, ...],
        selected_source_spots: list[DetectedSpot],
    ) -> tuple[object, ...] | None:
        if self._state.dataset is None or not selected_source_spots:
            return None
        preprocessing = deepcopy(self._state.preprocessing)
        flatten_mask_settings = deepcopy(self._state.spot_detection) if preprocessing.flatten_background_exclude_mask else None
        measurement_settings = deepcopy(self._state.spot_detection)
        measurement_payload: list[tuple[float, str, list[DetectedSpot], np.ndarray | None, bool, np.ndarray | None]] = []
        for wavelength in self._wavelength_values:
            record = self._record_map.get((frame, wavelength))
            if record is None:
                continue
            image_key = (frame, float(wavelength))
            preprocessing_spots = deepcopy(self._spots_for_preprocessing(image_key))
            affine_matrix = self._chromatic_affine_for_image_key(image_key)
            if affine_matrix is not None:
                affine_matrix = np.asarray(affine_matrix, dtype=np.float64)
            external_mask, external_mask_processed = self._effective_external_mask_for_record(record.path, processed_space=True)
            measurement_payload.append(
                (
                    float(wavelength),
                    str(record.path),
                    preprocessing_spots,
                    affine_matrix,
                    bool(external_mask_processed),
                    None if external_mask is None else np.asarray(external_mask, dtype=bool),
                )
            )
        if not measurement_payload:
            return None
        return (
            measurement_payload,
            preprocessing,
            flatten_mask_settings,
            measurement_settings,
            self._absorbance_roi_mask_cache,
            self._analysis_cache_lock,
            int(self.ABSORBANCE_ROI_MASK_CACHE_SIZE),
            deepcopy(selected_source_spots),
            selected_spot_ids,
            float(self._state.spot_detection.ring_inner_radius_px),
            float(self._state.spot_detection.ring_outer_radius_px),
            deepcopy(self._state.mask) if self._mask_section_applied() else None,
        )

    def _prepare_absorbance_spectrum_payload(
        self,
        selected_source_spots: list[DetectedSpot] | None = None,
    ) -> tuple[tuple[object, ...], tuple[object, ...]] | None:
        signature = self._absorbance_spectrum_signature()
        if signature is None or self._state.dataset is None:
            return None
        frame = int(signature[0])
        selected_source_spots = self._selected_source_spots_snapshot() if selected_source_spots is None else list(selected_source_spots)
        if not selected_source_spots:
            return None
        selected_spot_ids = tuple(spot.spot_id for spot in selected_source_spots)
        payload = self._prepare_absorbance_spectrum_payload_for_frame(frame, selected_spot_ids, selected_source_spots)
        if payload is None:
            return None
        return signature, payload

    def _toggle_analysis_live_preview(self) -> None:
        self._analysis_live_preview_enabled = not self._analysis_live_preview_enabled
        if not self._analysis_enabled and self._analysis_live_preview_enabled:
            self._analysis_live_preview_enabled = False
            self._settings.setValue("analysis/live_preview", False)
            self._update_analysis_control_state()
            self._set_status_text("Enable Analysis to use live preview.")
            return
        self._settings.setValue("analysis/live_preview", bool(self._analysis_live_preview_enabled))
        self._update_analysis_control_state()
        if self._analysis_live_preview_enabled:
            self._refresh_visible_spectrum_from_cache()
            self._analysis_controller.preview_sensorgram_from_cache()
            self._set_status_text("Analysis live preview enabled.")
        else:
            self._set_status_text("Analysis live preview disabled.")

    def _refresh_absorbance_spectrum(self) -> None:
        start_time = time.perf_counter()
        if not self._analysis_enabled:
            self._clear_absorbance_spectrum("Analysis calculations are disabled for this panel.")
            return
        selected_source_spots = self._selected_source_spots_snapshot()
        if not selected_source_spots:
            self._clear_absorbance_spectrum("Select spots to show absorbance spectrum.")
            return
        selected_spot_ids = tuple(spot.spot_id for spot in selected_source_spots)
        spot_signatures = [self._spot_absorbance_signature(spot) for spot in selected_source_spots]
        if any(signature is None for signature in spot_signatures):
            self._clear_absorbance_spectrum("Select spots to show absorbance spectrum.")
            return
        if len(selected_source_spots) == 1:
            spot_signature = spot_signatures[0]
            assert spot_signature is not None
            cached_spot_result = self._spot_absorbance_cache.get(spot_signature)
            if cached_spot_result is not None:
                self._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_spot_result)
                self._spot_absorbance_cache.move_to_end(spot_signature)
                elapsed = self._format_elapsed_seconds(time.perf_counter() - start_time)
                self._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
                self._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._absorbance_spectrum_signature_for_source_spots(selected_source_spots)
        if signature is not None:
            cached_result = self._cached_absorbance_result_for_selection(signature, selected_spot_ids, selected_source_spots)
            if cached_result is not None:
                self._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(cached_result)
                frame_signature = self._absorbance_frame_signature(signature)
                if frame_signature is not None and frame_signature in self._absorbance_frame_cache:
                    self._absorbance_frame_cache.move_to_end(frame_signature)
                elapsed = self._format_elapsed_seconds(time.perf_counter() - start_time)
                self._set_status_text(f"Spec | cache {elapsed}")
                return
        missing_source_spots = [
            spot
            for spot, signature_value in zip(selected_source_spots, spot_signatures, strict=False)
            if signature_value is None or self._spot_absorbance_cache.get(signature_value) is None
        ]
        target_source_spots = missing_source_spots if missing_source_spots else selected_source_spots
        signature = self._absorbance_spectrum_signature_for_source_spots(target_source_spots)
        if signature is None:
            self._clear_absorbance_spectrum("Select spots to show absorbance spectrum.")
            return
        if self._absorbance_spectrum_running and self._absorbance_spectrum_running_signature == signature:
            return
        if (
            self._pending_absorbance_spectrum_payload is not None
            and self._pending_absorbance_spectrum_payload[0] == signature
        ):
            return
        if (
            signature in self._absorbance_spectrum_cache
        ):
            self._absorbance_spectrum_dirty = False
            self._apply_absorbance_spectrum_result(self._absorbance_spectrum_cache[signature])
            self._absorbance_spectrum_cache.move_to_end(signature)
            elapsed = self._format_elapsed_seconds(time.perf_counter() - start_time)
            self._set_status_text(f"Spec | cache {elapsed}")
            return
        self._start_absorbance_spectrum_preparation(signature, target_source_spots)

    def _available_analysis_frames(self) -> list[int]:
        frame_range = self._current_analysis_frame_range()
        if frame_range is None:
            return []
        start, end = frame_range
        return [int(frame) for frame in self._frame_values if start <= int(frame) <= end]

    def _on_analysis_fit_settings_changed(self, *_args) -> None:
        start_time = time.perf_counter()
        self._save_control_preferences()
        if self._analysis_live_preview_enabled:
            self._schedule_sensorgram_refresh()
        else:
            self._mark_sensorgram_stale(
                f"{self._analysis_metric_label()} sensorgram is out of date | Press Calculate all frames"
            )
        selected_source_spots = self._selected_source_spots_snapshot()
        if len(selected_source_spots) == 1:
            spot_signature = self._spot_absorbance_signature(selected_source_spots[0])
            if spot_signature is not None and spot_signature in self._spot_absorbance_cache and not self._absorbance_spectrum_dirty:
                self._absorbance_spectrum_dirty = False
                self._apply_absorbance_spectrum_result(self._spot_absorbance_cache[spot_signature])
                self._spot_absorbance_cache.move_to_end(spot_signature)
                elapsed = self._format_elapsed_seconds(time.perf_counter() - start_time)
                self._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._absorbance_spectrum_signature()
        if signature is not None and signature in self._absorbance_spectrum_cache and not self._absorbance_spectrum_dirty:
            self._apply_absorbance_spectrum_result(self._absorbance_spectrum_cache[signature])
            self._absorbance_spectrum_cache.move_to_end(signature)
            elapsed = self._format_elapsed_seconds(time.perf_counter() - start_time)
            self._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
            self._set_status_text(f"Spec | cache {elapsed}")
        elif self._analysis_live_preview_enabled:
            self._schedule_absorbance_spectrum_refresh()

    def _on_analysis_frame_range_changed(self, *_args) -> None:
        self._save_control_preferences()
        if self.analysis_start_frame_spin.value() > self.analysis_end_frame_spin.value():
            self.analysis_start_frame_spin.blockSignals(True)
            self.analysis_end_frame_spin.blockSignals(True)
            start = min(self.analysis_start_frame_spin.value(), self.analysis_end_frame_spin.value())
            end = max(self.analysis_start_frame_spin.value(), self.analysis_end_frame_spin.value())
            self.analysis_start_frame_spin.setValue(start)
            self.analysis_end_frame_spin.setValue(end)
            self.analysis_start_frame_spin.blockSignals(False)
            self.analysis_end_frame_spin.blockSignals(False)
        if self._analysis_live_preview_enabled and not self._analysis_controller.preview_sensorgram_from_cache():
            self._analysis_controller.mark_stale(
                f"{self._analysis_metric_label()} sensorgram is out of date | Press Calculate all frames"
            )
        elif not self._analysis_live_preview_enabled:
            self._mark_sensorgram_stale()

    def _calculate_sensorgram_for_range(self) -> None:
        if not self._analysis_enabled:
            self._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if self._state.dataset is None:
            self._clear_sensorgram("Load a dataset before calculating the sensorgram.")
            return
        if self._chromatic_setup_active:
            self._clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        selected_spot_ids = self._selected_spectrum_spot_ids()
        if not selected_spot_ids:
            self._clear_sensorgram("Select spots before calculating the sensorgram.")
            return
        selected_source_spots = self._selected_source_spots_snapshot()
        if not selected_source_spots:
            self._clear_sensorgram("Select spots before calculating the sensorgram.")
            return

        frames = self._available_analysis_frames()
        if not frames:
            self._clear_sensorgram("No frames are available in the selected range.")
            return

        cached_signature = self._sensorgram_signature_for_selection(frames, selected_spot_ids, selected_source_spots)
        if cached_signature is not None:
            with self._analysis_cache_lock:
                cached_sensorgram = self._sensorgram_cache.get(cached_signature)
                if cached_sensorgram is not None:
                    self._sensorgram_cache.move_to_end(cached_signature)
                    self._append_workflow_log(
                        f"SG cache hit | frames {len(frames)} | metric {self._analysis_metric_label()}",
                        level="debug",
                    )
                    self._append_workflow_log(
                        f"SG cache summary | payload hit {len(frames)} build 0 | result hit 1 build 0",
                        level="debug",
                    )
                    self._sensorgram_frame_indices = np.asarray(cached_sensorgram.frame_indices, dtype=np.int32)
                    self._sensorgram_metric_values = np.asarray(cached_sensorgram.metric_values, dtype=np.float64)
                    self._sensorgram_metric_signal = np.asarray(cached_sensorgram.metric_signal, dtype=np.float64)
                    self._set_sensorgram_series(self._sensorgram_frame_indices, self._sensorgram_metric_values)
                    summary = (
                        f"{self._analysis_metric_label()} | Cached {cached_sensorgram.completed_count}/"
                        f"{cached_sensorgram.total_count} frames | Polynomial order {self._analysis_poly_order()}"
                    )
                    self._set_sensorgram_summary_text(summary)
                    self._set_status_text("Sensorgram cache used.")
                    return
        self._sensorgram_running_signature = cached_signature

        self._append_workflow_log(
            f"SG calc start | spots {len(selected_spot_ids)} | frames {len(frames)} | metric {self._analysis_metric_label()}",
            level="info",
        )
        self._start_sensorgram_worker(cached_signature, frames, selected_spot_ids, selected_source_spots)

    def _stop_sensorgram_calculation(self) -> None:
        if not self._sensorgram_running or self._sensorgram_cancel_event is None:
            return
        self._sensorgram_cancel_event.set()
        self._pending_sensorgram_payload = None
        self._set_sensorgram_summary_text("Stopping sensorgram calculation...")
        self._set_status_text("Stopping sensorgram calculation...")

    def _on_sensorgram_partial_result(
        self,
        request_id: int,
        total_count: int,
        point: SensorgramPointResult,
    ) -> None:
        if request_id != self._sensorgram_request_id or not self._analysis_enabled:
            return
        metric_value = float("nan") if point.metric_value is None else float(point.metric_value)
        metric_signal = float("nan") if point.metric_signal is None else float(point.metric_signal)
        self._sensorgram_frame_indices = np.append(self._sensorgram_frame_indices, int(point.frame_index)).astype(np.int32, copy=False)
        self._sensorgram_metric_values = np.append(self._sensorgram_metric_values, metric_value).astype(np.float64, copy=False)
        self._sensorgram_metric_signal = np.append(self._sensorgram_metric_signal, metric_signal).astype(np.float64, copy=False)
        self._set_sensorgram_series(
            self._sensorgram_frame_indices,
            self._sensorgram_metric_values,
            summary_text=(
                f"{self._analysis_metric_label()} | Calculating {self._sensorgram_frame_indices.size}/{total_count} frames"
            ),
        )

    def _on_sensorgram_ready(self, request_id: int, result: SensorgramComputationResult) -> None:
        if request_id != self._sensorgram_request_id:
            return
        self._sensorgram_running = False
        self._sensorgram_cancel_event = None
        self._end_busy()
        if not self._analysis_enabled:
            self._sensorgram_running_signature = None
            self._update_analysis_control_state()
            return
        self._sensorgram_frame_indices = np.asarray(result.frame_indices, dtype=np.int32)
        self._sensorgram_metric_values = np.asarray(result.metric_values, dtype=np.float64)
        self._sensorgram_metric_signal = np.asarray(result.metric_signal, dtype=np.float64)
        sensorgram_signature = self._sensorgram_running_signature
        self._sensorgram_running_signature = None
        if sensorgram_signature is not None:
            with self._analysis_cache_lock:
                self._sensorgram_cache[sensorgram_signature] = result
                self._sensorgram_cache.move_to_end(sensorgram_signature)
                while len(self._sensorgram_cache) > self.SENSORGRAM_CACHE_SIZE:
                    self._sensorgram_cache.popitem(last=False)
            self._append_workflow_log(
                f"SG cache store | frames {int(result.completed_count)}/{int(result.total_count)}",
                level="debug",
            )
            self._append_workflow_log(
                f"SG cache summary | payload result cached | prep {self._format_elapsed_seconds(result.prep_seconds)}",
                level="debug",
            )
        self._set_sensorgram_series(self._sensorgram_frame_indices, self._sensorgram_metric_values)
        self._append_workflow_log(
            f"SG done | prep {self._format_elapsed_seconds(result.prep_seconds)} | fit {self._format_elapsed_seconds(result.fit_seconds)}",
            level="success",
        )
        summary = (
            f"{self._analysis_metric_label()} | Calculated {result.completed_count}/{result.total_count} frames"
            f" | Polynomial order {self._analysis_poly_order()}"
        )
        if result.cancelled:
            summary = (
                f"{self._analysis_metric_label()} | Stopped after {result.completed_count}/{result.total_count} frames"
                f" | Polynomial order {self._analysis_poly_order()}"
            )
        self._set_sensorgram_summary_text(summary)
        self._set_status_text("Sensorgram calculation stopped." if result.cancelled else "Sensorgram calculation finished.")
        self._update_analysis_control_state()

    def _on_sensorgram_failed(self, request_id: int, message: str) -> None:
        if request_id != self._sensorgram_request_id:
            return
        self._sensorgram_running = False
        self._sensorgram_cancel_event = None
        self._end_busy()
        self._update_analysis_control_state()
        self._set_sensorgram_summary_text(f"Sensorgram failed: {message}")
        self._background_error("Sensorgram", message)

    def _start_pending_absorbance_spectrum_refresh(self, *, reuse_busy: bool = False) -> None:
        if self._pending_absorbance_spectrum_payload is None:
            return
        signature, payload = self._pending_absorbance_spectrum_payload
        self._pending_absorbance_spectrum_payload = None
        request_id = self._absorbance_spectrum_request_id + 1
        self._absorbance_spectrum_request_id = request_id
        self._absorbance_spectrum_running = True
        self._absorbance_spectrum_running_signature = signature
        self._absorbance_spectrum_started_at = time.perf_counter()
        if reuse_busy:
            self._busy_started_at = time.perf_counter()
            self._busy_is_determinate = True
            self._busy_last_percent = 0
            self._status_bar_busy.setRange(0, 100)
            self._status_bar_busy.setValue(0)
            self._status_bar_busy.setTextVisible(True)
            self._status_bar_busy.show()
            self._status_bar_busy_detail.setText("0:00 | ETA --:-- | 0%")
            self._status_bar_busy_detail.show()
            self._set_status_text("Updating absorbance spectrum...")
        else:
            self._begin_busy("Updating absorbance spectrum...", determinate=True)
        worker = FunctionWorker(
            _absorbance_spectrum_task,
            *payload,
            supports_progress=True,
        )
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.result.connect(
            lambda result,
            request_id=request_id,
            signature=signature: self._on_absorbance_spectrum_ready(request_id, signature, result)
        )
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_absorbance_spectrum_failed(request_id, message))
        self._thread_pool.start(worker)

    def _on_absorbance_spectrum_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        result: AbsorbanceSpectrumResult,
    ) -> None:
        started_at = self._absorbance_spectrum_started_at
        self._absorbance_spectrum_started_at = None
        self._absorbance_spectrum_running = False
        self._absorbance_spectrum_running_signature = None
        self._end_busy()
        if request_id != self._absorbance_spectrum_request_id:
            if self._pending_absorbance_spectrum_payload is not None:
                self._start_pending_absorbance_spectrum_refresh()
            return
        self._absorbance_spectrum_cache[signature] = result
        self._absorbance_spectrum_cache.move_to_end(signature)
        while len(self._absorbance_spectrum_cache) > self.ABSORBANCE_SPECTRUM_CACHE_SIZE:
            self._absorbance_spectrum_cache.popitem(last=False)
        self._append_workflow_log(
            f"Spec cache store | spots {len(signature[2]) if len(signature) > 2 and isinstance(signature[2], tuple) else 0}",
            level="debug",
        )
        frame_signature = self._absorbance_frame_signature(signature)
        if frame_signature is not None:
            self._absorbance_frame_cache[frame_signature] = result
            self._absorbance_frame_cache.move_to_end(frame_signature)
            while len(self._absorbance_frame_cache) > self.ABSORBANCE_FRAME_CACHE_SIZE:
                self._absorbance_frame_cache.popitem(last=False)
            self._append_workflow_log("Spec frame cache store", level="debug")
        self._absorbance_spectrum_dirty = False
        fit_seconds = self._apply_absorbance_spectrum_result(result)
        result.fit_seconds = float(fit_seconds)
        self._append_workflow_log(
            f"Spec done | load {self._format_elapsed_seconds(result.load_seconds)} | roi {self._format_elapsed_seconds(result.roi_seconds)} | fit {self._format_elapsed_seconds(fit_seconds)}",
            level="success",
        )
        load_timing = self._compact_timing_text(("load", result.load_seconds), ("roi", result.roi_seconds))
        fit_timing = self._format_elapsed_seconds(fit_seconds)
        status_parts = ["Spec"]
        if load_timing:
            status_parts.append(load_timing)
        if fit_timing:
            status_parts.append(f"fit {fit_timing}")
        if not load_timing and not fit_timing:
            elapsed = self._format_elapsed_seconds(time.perf_counter() - started_at) if started_at is not None else ""
            if elapsed:
                status_parts.append(f"t {elapsed}")
        self._set_status_text(" | ".join(status_parts))
        if self._pending_absorbance_spectrum_payload is not None:
            self._start_pending_absorbance_spectrum_refresh()

    def _on_absorbance_spectrum_failed(self, request_id: int, message: str) -> None:
        self._absorbance_spectrum_started_at = None
        self._absorbance_spectrum_running = False
        self._absorbance_spectrum_running_signature = None
        self._end_busy()
        if request_id == self._absorbance_spectrum_request_id:
            self._background_error("Spectral absorbance", message)
        if self._pending_absorbance_spectrum_payload is not None:
            self._start_pending_absorbance_spectrum_refresh()

    def _apply_absorbance_spectrum_result(self, result: AbsorbanceSpectrumResult) -> None:
        import time

        fit_started = time.perf_counter()
        selected_spot_ids = self._selected_spectrum_spot_ids()
        series_payloads: list[tuple[str, int, AbsorbanceSpectrumResult]] = []
        if result.spot_results:
            if selected_spot_ids:
                for spot_id in selected_spot_ids:
                    spot_result = result.spot_results.get(int(spot_id))
                    if spot_result is not None:
                        series_payloads.append((f"Spot {int(spot_id)}", int(spot_id), spot_result))
            else:
                for spot_id in sorted(result.spot_results):
                    series_payloads.append((f"Spot {int(spot_id)}", int(spot_id), result.spot_results[int(spot_id)]))
        if selected_spot_ids and len(series_payloads) < len(selected_spot_ids):
            existing_ids = {int(spot_id) for _, spot_id, _ in series_payloads}
            for spot_id in selected_spot_ids:
                if int(spot_id) in existing_ids:
                    continue
                spot = next((spot for spot in self._state.detected_spots if int(spot.spot_id) == int(spot_id)), None)
                if spot is None:
                    continue
                spot_signature = self._spot_absorbance_signature(spot)
                if spot_signature is None:
                    continue
                cached_result = self._spot_absorbance_cache.get(spot_signature)
                if cached_result is not None:
                    series_payloads.append((f"Spot {int(spot_id)}", int(spot_id), cached_result))
        if not series_payloads and len(selected_spot_ids) > 1:
            for spot_id in selected_spot_ids:
                spot = next((spot for spot in self._state.detected_spots if int(spot.spot_id) == int(spot_id)), None)
                if spot is None:
                    continue
                spot_signature = self._spot_absorbance_signature(spot)
                if spot_signature is None:
                    continue
                cached_result = self._spot_absorbance_cache.get(spot_signature)
                if cached_result is not None:
                    series_payloads.append((f"Spot {int(spot_id)}", int(spot_id), cached_result))
        if not series_payloads:
            fallback_id = int(selected_spot_ids[0]) if selected_spot_ids else 0
            series_payloads = [("Selection", fallback_id, result)]
        highlighted_ids = set(selected_spot_ids)

        self._clear_spectrum_series_items()
        self.spectrum_current_point.setData([], [])
        self.spectrum_metric_point.setData([], [])

        x_values_all: list[np.ndarray] = []
        y_values_all: list[np.ndarray] = []
        fit_y_values_all: list[np.ndarray] = []
        primary_result = series_payloads[0][2]
        for label, spot_id, spot_result in series_payloads:
            rendered = self._add_spectrum_series(
                spot_id=spot_id,
                result=spot_result,
                label=label,
                highlighted=bool(highlighted_ids) and int(spot_id) in highlighted_ids,
                dimmed=len(series_payloads) > 1 and bool(highlighted_ids),
            )
            if rendered is None:
                continue
            x_values, y_values, fit_x_values, fit_y_values = rendered
            x_values_all.append(np.asarray(x_values, dtype=np.float64))
            y_values_all.append(np.asarray(y_values, dtype=np.float64))
            if fit_x_values is not None and fit_y_values is not None and fit_x_values.size and fit_y_values.size:
                fit_y_values_all.append(np.asarray(fit_y_values, dtype=np.float64))

        if not x_values_all:
            self._set_spectrum_summary_text(f"{self._spectrum_selection_label()} | No valid absorbance values")
            return

        x_min = min(float(np.min(values)) for values in x_values_all)
        x_max = max(float(np.max(values)) for values in x_values_all)
        y_min = min(float(np.min(values)) for values in y_values_all)
        y_max = max(float(np.max(values)) for values in y_values_all)
        for fit_values in fit_y_values_all:
            if fit_values.size:
                y_min = min(y_min, float(np.nanmin(fit_values)))
                y_max = max(y_max, float(np.nanmax(fit_values)))
        y_span = max(y_max - y_min, 0.05)
        self.spectrum_plot.setXRange(x_min, x_max, padding=0.02)
        self.spectrum_plot.setYRange(y_min - y_span * 0.08, y_max + y_span * 0.12, padding=0.0)

        metric_value = None
        metric_signal = None
        current_text = ""
        fit_text = ""
        fit_seconds = 0.0
        if len(series_payloads) == 1:
            fit = self._analysis_fit_result_from_spectrum(primary_result)
            if fit is not None:
                metric_value, metric_signal = metric_value_from_fit(fit, self._analysis_metric_key())
                if metric_value is not None and metric_signal is not None and np.isfinite(metric_value) and np.isfinite(metric_signal):
                    self.spectrum_metric_point.setData([float(metric_value)], [float(metric_signal)])
                else:
                    self.spectrum_metric_point.setData([], [])
            else:
                self.spectrum_metric_point.setData([], [])
            current_wavelength = self._current_wavelength()
            current_point_index = None
            if current_wavelength is not None:
                current_point_index = next(
                    (
                        index
                        for index, wavelength_nm in enumerate(primary_result.wavelengths_nm)
                        if abs(float(wavelength_nm) - float(current_wavelength)) < 1e-6
                        and np.isfinite(primary_result.absorbance[index])
                    ),
                    None,
                )
            if current_point_index is None:
                self.spectrum_current_point.setData([], [])
            else:
                current_x = float(primary_result.wavelengths_nm[current_point_index])
                current_y = float(primary_result.absorbance[current_point_index])
                self.spectrum_current_point.setData([current_x], [current_y])
                current_spot_mean = float(primary_result.spot_mean[current_point_index])
                current_ring_mean = float(primary_result.ring_mean[current_point_index])
                current_text = (
                    f" | A({current_x:g} nm) = {current_y:.4f}"
                    f" | spot {current_spot_mean:.1f}, ref. ring {current_ring_mean:.1f}"
                )
            if metric_value is not None and np.isfinite(metric_value):
                fit_text = (
                    f" | {self._analysis_metric_label()} {float(metric_value):.3f} nm"
                    f" | Poly {self._analysis_poly_order()}"
                )
        else:
            self.spectrum_current_point.setData([], [])
            self.spectrum_metric_point.setData([], [])
            fit_text = f" | {len(series_payloads)} spot series"
        fit_seconds = time.perf_counter() - fit_started
        self._last_absorbance_fit_seconds = fit_seconds

        frame = self._current_frame()
        spot_pixels = int(np.nanmax(primary_result.spot_pixel_count)) if primary_result.spot_pixel_count.size else 0
        ring_pixels = int(np.nanmax(primary_result.ring_pixel_count)) if primary_result.ring_pixel_count.size else 0
        self._set_spectrum_summary_text(
            f"{self._spectrum_selection_label()} | Frame {frame if frame is not None else '-'}"
            f" | ROI px: spot {spot_pixels}, ref. ring {ring_pixels}{current_text}{fit_text}"
        )
        self._update_single_frame_sensorgram(metric_value, metric_signal)
        return fit_seconds

    def _update_color_button_styles(self) -> None:
        self.mask_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._mask_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.spot_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._spot_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.ring_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._ring_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.highlight_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._highlight_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.scale_bar_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._scale_bar_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self._update_histogram_region_styles()
        if hasattr(self, "spot_list_table") and self.spot_list_table.columnCount() >= 5:
            self._refresh_spot_list_table_headers()
            if self.spot_list_table.isVisible():
                self._update_spot_list_table()

    def _update_histogram_region_styles(self) -> None:
        highlight_brush = QColor(self._highlight_visual_color)
        highlight_brush.setAlphaF(self._alpha01(max(self._highlight_alpha * 0.28, 0.06)))
        self.hist_region.setBrush(pg.mkBrush(highlight_brush))
        for line in self.hist_region.lines:
            line.setPen(pg.mkPen(self._highlight_visual_color, width=2))

        mask_brush = QColor(self._mask_visual_color)
        mask_brush.setAlphaF(self._alpha01(max(self._mask_alpha * 0.28, 0.06)))
        self.ignore_region.setBrush(pg.mkBrush(mask_brush))
        for line in self.ignore_region.lines:
            line.setPen(pg.mkPen(self._mask_visual_color, width=2))

    def _sync_crop_tool(self, image_shape: tuple[int, int]) -> None:
        crop = self._state.preprocessing.crop
        image_height, image_width = image_shape[:2]
        self._ensure_image_tool_guide()
        if self._crop_roi is None:
            width = crop.width if crop.width > 0 else max(image_width // 2, 1)
            height = crop.height if crop.height > 0 else max(image_height // 2, 1)
            self._crop_roi = pg.RectROI(
                [crop.x, crop.y],
                [width, height],
                pen=pg.mkPen("#38bdf8", width=2),
                movable=False,
                resizable=True,
                rotatable=False,
                sideScalers=False,
            )
            self._crop_roi.handleSize = 10
            self._crop_roi.sigRegionChangeFinished.connect(self._crop_roi_changed)
            self._crop_roi.sigRegionChanged.connect(lambda *_args: self._update_crop_overlay())
            self._crop_roi.addScaleHandle([0.0, 0.0], [1.0, 1.0], name="top_left")
            self._crop_roi.addScaleHandle([1.0, 0.0], [0.0, 1.0], name="top_right")
            self._crop_roi.addScaleHandle([0.0, 1.0], [1.0, 0.0], name="bottom_left")
            self._crop_roi.addScaleHandle([1.0, 1.0], [0.0, 0.0], name="bottom_right")
            for fraction in (0.25, 0.5, 0.75):
                self._crop_roi.addScaleHandle([0.0, fraction], [1.0, fraction], name=f"left_{fraction:g}")
                self._crop_roi.addScaleHandle([1.0, fraction], [0.0, fraction], name=f"right_{fraction:g}")
                self._crop_roi.addScaleHandle([fraction, 0.0], [fraction, 1.0], name=f"top_{fraction:g}")
                self._crop_roi.addScaleHandle([fraction, 1.0], [fraction, 0.0], name=f"bottom_{fraction:g}")
            self.image_plot.addItem(self._crop_roi)
            self._crop_roi.setZValue(1.0)

        width = crop.width if crop.width > 0 else max(image_width // 2, 1)
        height = crop.height if crop.height > 0 else max(image_height // 2, 1)
        x = min(max(crop.x, 0), max(image_width - width, 0))
        y = min(max(crop.y, 0), max(image_height - height, 0))

        self._suspend_crop_sync = True
        self._crop_roi.setPos((x, y))
        self._crop_roi.setSize((min(width, image_width), min(height, image_height)))
        self._suspend_crop_sync = False
        self._update_crop_overlay()
        self._sync_crop_visibility()

    def _sync_crop_visibility(self) -> None:
        if self._crop_roi is not None:
            self._crop_roi.setVisible(self._active_tool == "crop" and not self._showing_background_profile_main)
        self._update_crop_overlay()
        self._update_guide_overlays()

    def _crop_roi_changed(self) -> None:
        if self._suspend_crop_sync or self._crop_roi is None:
            return
        self._push_undo_point("Crop")
        pos = self._crop_roi.pos()
        size = self._crop_roi.size()
        self._state.preprocessing.crop.x = max(int(round(pos.x())), 0)
        self._state.preprocessing.crop.y = max(int(round(pos.y())), 0)
        self._state.preprocessing.crop.width = max(int(round(size.x())), 1)
        self._state.preprocessing.crop.height = max(int(round(size.y())), 1)
        self._state.preprocessing.crop.enabled = True
        self._update_crop_overlay()
        self._handle_image_tool_settings_changed("Crop updated.", preserve_view=True)

    def _crop_rect_contains_point(self, point: tuple[float, float]) -> bool:
        if self._crop_roi is None:
            return False
        pos = self._crop_roi.pos()
        size = self._crop_roi.size()
        x0 = float(pos.x())
        y0 = float(pos.y())
        x1 = x0 + float(size.x())
        y1 = y0 + float(size.y())
        x = float(point[0])
        y = float(point[1])
        return x0 <= x <= x1 and y0 <= y <= y1

    def _move_crop_roi_to(self, x: float, y: float) -> None:
        if self._crop_roi is None or self._current_processed_image is None:
            return
        image_height, image_width = self._current_processed_image.shape[:2]
        size = self._crop_roi.size()
        crop_width = max(float(size.x()), 1.0)
        crop_height = max(float(size.y()), 1.0)
        x = float(np.clip(float(x), 0.0, max(float(image_width) - crop_width, 0.0)))
        y = float(np.clip(float(y), 0.0, max(float(image_height) - crop_height, 0.0)))
        self._suspend_crop_sync = True
        self._crop_roi.setPos((x, y))
        self._suspend_crop_sync = False
        self._state.preprocessing.crop.x = max(int(round(x)), 0)
        self._state.preprocessing.crop.y = max(int(round(y)), 0)
        self._state.preprocessing.crop.width = max(int(round(crop_width)), 1)
        self._state.preprocessing.crop.height = max(int(round(crop_height)), 1)
        self._state.preprocessing.crop.enabled = True
        self._update_crop_overlay()

    def _begin_image_pan(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            return
        x_range, y_range = self.image_plot.vb.viewRange()
        self._panning_image = True
        self._pan_anchor_view = (float(point[0]), float(point[1]))
        self._pan_anchor_ranges = ((float(x_range[0]), float(x_range[1])), (float(y_range[0]), float(y_range[1])))

    def _update_image_pan(self, point: tuple[float, float]) -> None:
        if not self._panning_image or self._pan_anchor_view is None or self._pan_anchor_ranges is None:
            return
        x_range, y_range = self._pan_anchor_ranges
        dx = float(point[0]) - float(self._pan_anchor_view[0])
        dy = float(point[1]) - float(self._pan_anchor_view[1])
        self.image_plot.vb.setRange(
            xRange=(x_range[0] - dx, x_range[1] - dx),
            yRange=(y_range[0] - dy, y_range[1] - dy),
            padding=0.0,
        )

    def _end_image_pan(self) -> None:
        self._panning_image = False
        self._pan_anchor_view = None
        self._pan_anchor_ranges = None

    def _ensure_crop_overlay(self) -> QGraphicsPathItem:
        if self._crop_overlay_item is not None:
            return self._crop_overlay_item
        overlay = QGraphicsPathItem()
        overlay.setZValue(0.25)
        overlay.setPen(QPen(Qt.PenStyle.NoPen))
        overlay.setBrush(QColor(0, 0, 0, 130))
        overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.image_plot.addItem(overlay)
        self._crop_overlay_item = overlay
        return overlay

    def _update_crop_overlay(self) -> None:
        overlay = self._ensure_crop_overlay()
        if (
            self._crop_roi is None
            or self._current_processed_image is None
            or self._showing_background_profile_main
            or self._active_tool != "crop"
        ):
            overlay.setVisible(False)
            return

        image_height, image_width = self._current_processed_image.shape[:2]
        width = max(float(image_width), 1.0)
        height = max(float(image_height), 1.0)
        pos = self._crop_roi.pos()
        size = self._crop_roi.size()
        x = float(np.clip(pos.x(), 0.0, max(width - 1.0, 0.0)))
        y = float(np.clip(pos.y(), 0.0, max(height - 1.0, 0.0)))
        crop_width = float(np.clip(size.x(), 1.0, width))
        crop_height = float(np.clip(size.y(), 1.0, height))
        x = float(np.clip(x, 0.0, max(width - crop_width, 0.0)))
        y = float(np.clip(y, 0.0, max(height - crop_height, 0.0)))

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(0.0, 0.0, width, height))
        path.addRect(QRectF(x, y, crop_width, crop_height))
        overlay.setPath(path)
        overlay.setVisible(True)

    def _build_numeric_field(self, spinbox: QSpinBox | QDoubleSpinBox) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(spinbox)
        return row

    def _build_spot_geometry_row(self) -> QWidget:
        row = QWidget(self)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(0)
        self.spot_diameter_spin.setRange(2, 1000)
        self.spot_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.spot_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.spot_diameter_spin.setAccelerated(True)
        self.spot_diameter_spin.setKeyboardTracking(True)
        self.spot_diameter_spin.setMaximumWidth(84)
        layout.addWidget(self.spot_geometry_scope_button, 0, 0, 2, 1)
        layout.addWidget(QLabel("D_s"), 0, 1)
        layout.addWidget(self.spot_diameter_spin, 0, 4)
        return row

    def _build_ring_row(self) -> QWidget:
        row = QWidget(self)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(0)
        self.ring_inner_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.ring_outer_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.ring_inner_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.ring_outer_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.ring_inner_diameter_spin.setAccelerated(True)
        self.ring_outer_diameter_spin.setAccelerated(True)
        self.ring_inner_diameter_spin.setKeyboardTracking(False)
        self.ring_outer_diameter_spin.setKeyboardTracking(False)
        self.ring_inner_diameter_spin.setMaximumWidth(84)
        self.ring_outer_diameter_spin.setMaximumWidth(84)
        layout.addWidget(self.ring_geometry_scope_button, 0, 0, 2, 1)
        layout.addWidget(QLabel("d_r"), 0, 1)
        layout.addWidget(self.ring_inner_diameter_spin, 0, 2)
        layout.addWidget(QLabel("D_r"), 0, 3)
        layout.addWidget(self.ring_outer_diameter_spin, 0, 4)
        return row

    def _build_array_row(self) -> QWidget:
        row = QWidget(self)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        rows_label = self._make_array_marker_label("rows", "Array rows")
        cols_label = self._make_array_marker_label("columns", "Array columns")
        spacing_label = self._make_array_marker_label("distance", "Spacing between neighboring spots")
        sizes_row = QHBoxLayout()
        sizes_row.setContentsMargins(0, 0, 0, 0)
        sizes_row.setSpacing(4)
        sizes_row.addWidget(rows_label)
        sizes_row.addWidget(self.array_rows_spin)
        sizes_row.addWidget(cols_label)
        sizes_row.addWidget(self.array_cols_spin)
        sizes_row.addWidget(spacing_label)
        sizes_row.addWidget(self.array_spacing_spin)
        sizes_row.addStretch(1)
        layout.addLayout(sizes_row)
        return row

    def _make_section_separator(self) -> QWidget:
        separator = QWidget(self)
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {get_active_theme().toolbar_border};")
        return separator

    def _make_left_tab_page(self, *widgets: QWidget) -> QWidget:
        page = QWidget(self)
        page.setObjectName("leftTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        theme = get_active_theme()
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {theme.window_bg}; }} "
            f"QWidget#leftTabPage {{ background: {theme.window_bg}; }}"
        )
        return scroll

    def _create_panel_container(
        self,
        title: str,
        content: QWidget,
        *,
        panel_name: str,
        allowed_areas: Qt.DockWidgetArea = Qt.DockWidgetArea.RightDockWidgetArea,
    ) -> PanelContainer:
        panel = PanelContainer(title, content, self)
        panel.setObjectName(panel_name)
        return panel

    def _restore_default_panel_layout(self) -> None:
        if not hasattr(self, "_main_splitter"):
            self._main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
            self._main_splitter.setObjectName("mainLayoutSplitter")
            self._main_splitter.setChildrenCollapsible(False)
            self._main_splitter.setHandleWidth(4)

            self._visual_splitter = QSplitter(Qt.Orientation.Vertical, self._main_splitter)
            self._visual_splitter.setObjectName("visualLayoutSplitter")
            self._visual_splitter.setChildrenCollapsible(False)
            self._visual_splitter.setHandleWidth(4)

            self._top_visual_splitter = QSplitter(Qt.Orientation.Horizontal, self._visual_splitter)
            self._top_visual_splitter.setObjectName("topVisualSplitter")
            self._top_visual_splitter.setChildrenCollapsible(False)
            self._top_visual_splitter.setHandleWidth(4)

            self._bottom_visual_splitter = QSplitter(Qt.Orientation.Horizontal, self._visual_splitter)
            self._bottom_visual_splitter.setObjectName("bottomVisualSplitter")
            self._bottom_visual_splitter.setChildrenCollapsible(False)
            self._bottom_visual_splitter.setHandleWidth(4)

            self._top_visual_splitter.addWidget(self.image_panel)
            self._top_visual_splitter.addWidget(self.histogram_panel)
            self._bottom_visual_splitter.addWidget(self.spectra_panel)
            self._bottom_visual_splitter.addWidget(self.sensorgram_panel)
            self._visual_splitter.addWidget(self._top_visual_splitter)
            self._visual_splitter.addWidget(self._bottom_visual_splitter)
            self._main_splitter.addWidget(self.workflow_panel)
            self._main_splitter.addWidget(self.spot_list_panel)
            self._main_splitter.addWidget(self._visual_splitter)
            self._workspace_root = QWidget(self)
            workspace_layout = QVBoxLayout(self._workspace_root)
            workspace_layout.setContentsMargins(0, 0, 0, 0)
            workspace_layout.setSpacing(0)
            workspace_layout.addWidget(self._main_splitter, 1)
            self.setCentralWidget(self._workspace_root)
        self.workflow_panel.setVisible(True)
        self.spot_list_panel.setVisible(self._settings_bool("layout/spot_list_visible", True))
        self.image_panel.setVisible(True)
        self.histogram_panel.setVisible(True)
        self.spectra_panel.setVisible(True)
        self.sensorgram_panel.setVisible(True)
        self._apply_default_splitter_sizes()

    def _apply_default_splitter_sizes(self) -> None:
        if not hasattr(self, "_main_splitter"):
            return
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setStretchFactor(2, 1)
        self._visual_splitter.setStretchFactor(0, 1)
        self._visual_splitter.setStretchFactor(1, 1)
        self._top_visual_splitter.setStretchFactor(0, 4)
        self._top_visual_splitter.setStretchFactor(1, 1)
        self._bottom_visual_splitter.setStretchFactor(0, 1)
        self._bottom_visual_splitter.setStretchFactor(1, 1)
        if self._main_splitter.count() == 3:
            self._main_splitter.setSizes([360, max(260, self.spot_list_panel.minimumWidth()), 1200])
        if self._visual_splitter.count() == 2:
            self._visual_splitter.setSizes([760, 420])
        if self._top_visual_splitter.count() == 2:
            self._top_visual_splitter.setSizes([980, 300])
        if self._bottom_visual_splitter.count() == 2:
            self._bottom_visual_splitter.setSizes([640, 640])

    def _build_mark_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.ignore_marked_check.setToolTip(
            "Ignore pixels from the current mask settings and any loaded mask file, and show them as an overlay."
        )
        layout.addWidget(self.ignore_marked_check)
        layout.addWidget(self.mask_load_from_file_button)
        layout.addStretch(1)
        return row

    def _build_histogram_mask_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Min"))
        # We'll use the existing histogram region controls
        layout.addWidget(QLabel("Max"))
        layout.addStretch(1)
        return row

    def _build_relative_mask_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Scale"))
        layout.addWidget(self.mask_relative_profile_sigma_spin)
        layout.addWidget(QLabel("Thr"))
        layout.addWidget(self.mask_relative_threshold_spin)
        layout.addStretch(1)
        return row

    def _build_local_contrast_mask_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Scale"))
        layout.addWidget(self.mask_local_contrast_sigma_spin)
        layout.addWidget(QLabel("Thr"))
        layout.addWidget(self.mask_local_contrast_z_spin)
        layout.addStretch(1)
        return row

    def _build_morphology_mask_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("R"))
        layout.addWidget(self.mask_morphology_radius_spin)
        layout.addWidget(self.mask_morphology_erode_button)
        layout.addWidget(self.mask_morphology_dilate_button)
        layout.addWidget(self.mask_morphology_open_button)
        layout.addWidget(self.mask_morphology_close_button)
        layout.addStretch(1)
        return row

    def _build_drawing_mask_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.mask_pencil_check)
        layout.addWidget(self.mask_draw_add_button)
        layout.addWidget(self.mask_draw_remove_button)
        layout.addWidget(QLabel("Brush"))
        layout.addWidget(self.mask_brush_size_spin)
        layout.addStretch(1)
        return row

    def _sync_spot_detection_controls(self) -> None:
        self._ui_state_manager.sync_spot_detection_controls()

    def _update_mask_control_state(self) -> None:
        self._ui_state_manager.update_mask_control_state()

    def _update_analysis_control_state(self) -> None:
        self._ui_state_manager.update_analysis_control_state()

    def _on_chromatic_section_applied_changed(self, applied: bool) -> None:
        self._chromatic_controller.section_applied_changed(applied)

    def _on_mask_section_applied_changed(self, applied: bool) -> None:
        if self.ignore_marked_check.isChecked() != bool(applied):
            self.ignore_marked_check.setChecked(bool(applied))

    def _on_background_section_applied_changed(self, applied: bool) -> None:
        applied = bool(applied)
        self._append_workflow_log(f"Background link | {applied}", level="debug")
        if self.background_removal_link.isChecked() != applied:
            self.background_removal_link.blockSignals(True)
            self.background_removal_link.setChecked(applied)
            self.background_removal_link.blockSignals(False)
        if applied and self._showing_background_profile_main:
            self._on_background_profile_toggled(False)
        self._update_image_processing_settings()

    def _on_live_geometry_toggled(self, applied: bool) -> None:
        checked = bool(applied)
        if self.spot_editor_section.is_applied() != checked:
            self.spot_editor_section.set_applied(checked)
        self._save_control_preferences()

    def _on_image_tools_section_applied_changed(self, applied: bool) -> None:
        applied = bool(applied)
        self._append_workflow_log(f"Image tools link | {applied}", level="debug")
        if bool(getattr(self._state.preprocessing, "image_tools_enabled", True)) == applied:
            return
        self._push_undo_point("Image tools")
        self._state.preprocessing.image_tools_enabled = applied
        self._image_tools_preview_only = not applied
        status = (
            "Image tools linked. Recalculating downstream views."
            if applied
            else "Image tools link disabled. Preview only mode is active."
        )
        self._begin_busy("Applying image tools...")
        QApplication.processEvents()
        try:
            self._handle_image_tool_settings_changed(status, preserve_view=True)
        finally:
            self._end_busy(status)

    def _on_analysis_section_applied_changed(self, applied: bool) -> None:
        applied = bool(applied)
        self._append_workflow_log(f"Analysis linked state changed: {applied}", level="debug")
        if self._analysis_enabled == applied:
            self._update_analysis_control_state()
            return
        self._analysis_enabled = applied
        self._settings.setValue("analysis_section_applied", self._analysis_enabled)
        if not self._analysis_enabled and self._analysis_live_preview_enabled:
            self._analysis_live_preview_enabled = False
            self._settings.setValue("analysis/live_preview", False)
        self._update_analysis_control_state()
        if self._analysis_enabled:
            self._mark_absorbance_spectrum_dirty()
            self._set_status_text("Analysis calculations enabled.")
            return
        self._stop_sensorgram_calculation()
        self._pending_sensorgram_payload = None
        self._clear_absorbance_spectrum("Analysis calculations are disabled for this panel.")
        self._clear_sensorgram("Analysis calculations are disabled for this panel.")
        self._set_status_text("Analysis calculations disabled.")

    def _update_geometry_control_ranges(self, image_shape: tuple[int, int] | None) -> None:
        if image_shape is None:
            max_diameter = 4000
        else:
            image_height, image_width = image_shape[:2]
            max_diameter = max(int(max(image_width, image_height)), 20)
        display_max = max(self._length_px_to_display(max_diameter), float(self.spot_diameter_spin.minimum()))
        self.spot_diameter_spin.setMaximum(display_max)
        self.ring_inner_diameter_spin.setMaximum(display_max)
        self.ring_outer_diameter_spin.setMaximum(display_max)
        self.array_spacing_spin.setMaximum(display_max)

    def _update_spot_detection_settings(self) -> None:
        sender = self.sender()
        if sender is not None:
            self._push_undo_point("Detection settings")
        previous_mask_signature = self._mask_preview_signature()
        previous_apply_mask = bool(self._state.preprocessing.flatten_background_exclude_mask)
        self._state.spot_detection.array_rows = int(self.array_rows_spin.value())
        self._state.spot_detection.array_cols = int(self.array_cols_spin.value())
        self._state.spot_detection.array_spacing_px = int(round(self._length_display_to_px(self.array_spacing_spin.value())))
        apply_mask = bool(self.ignore_marked_check.isChecked())
        self._state.spot_detection.ignore_marked_pixels = apply_mask
        self._state.preprocessing.flatten_background_exclude_mask = apply_mask
        self._set_section_applied(self.mask_section, apply_mask)
        self._state.spot_detection.mask_mode = str(self.mask_mode_combo.currentData() or "absolute")
        self._state.spot_detection.mask_profile_sigma_px = float(self.mask_relative_profile_sigma_spin.value())
        self._state.spot_detection.mask_relative_threshold_fraction = float(self.mask_relative_threshold_spin.value()) / 100.0
        self._state.spot_detection.mask_local_contrast_sigma_px = float(self.mask_local_contrast_sigma_spin.value())
        self._state.spot_detection.mask_local_contrast_z_threshold = float(self.mask_local_contrast_z_spin.value())
        if self._state.spot_detection.mask_mode == "absolute":
            lower, upper = self.ignore_region.getRegion()
            if lower > upper:
                lower, upper = upper, lower
            self._state.spot_detection.ignored_intensity_min_value = float(
                np.clip(lower, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
            )
            self._state.spot_detection.ignored_intensity_max_value = float(
                np.clip(upper, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
            )
            self._state.spot_detection.ignored_intensity_value = None
        self._update_spot_detection_labels()
        if apply_mask and previous_mask_signature != self._mask_preview_signature():
            self._invalidate_image_analysis_caches()
            self._update_ignore_mask_overlay()
            self._schedule_histogram_refresh()
            self._current_image_key = None
            self._schedule_image_refresh()
        elif not apply_mask:
            self._update_ignore_mask_overlay()
        if previous_apply_mask != apply_mask and not apply_mask:
            self._invalidate_background_profile_cache()
            self._current_image_key = None
            self._schedule_image_refresh()
        self._update_apply_button_labels()
        self._schedule_processing_state_save()

    def _update_image_processing_settings(self) -> None:
        self._push_undo_point("Image processing")
        self._append_workflow_log("Image processing settings updated.", level="debug")
        previous_flatten_background_enabled = bool(self._state.preprocessing.flatten_background_enabled)
        self._state.preprocessing.flatten_background_enabled = bool(self.background_section.is_applied())
        self.background_removal_link.setIcon(self._make_link_toggle_icon(bool(self.background_removal_link.isChecked())))
        self._state.preprocessing.flatten_background_sigma_px = float(self.background_smoothing_sigma_spin.value())
        self._state.preprocessing.flatten_background_binning = int(
            self.background_smoothing_binning_combo.currentData() or 2
        )
        self._state.preprocessing.flatten_background_exclude_spots = bool(self.background_ignore_spot_button.isChecked())
        self._state.preprocessing.flatten_background_exclude_mask = bool(self.background_ignore_mask_button.isChecked())
        self._set_section_applied(self.background_section, bool(self._state.preprocessing.flatten_background_enabled))
        self._update_apply_button_labels()
        self._schedule_processing_state_save()
        if self._state.preprocessing.flatten_background_enabled or previous_flatten_background_enabled != self._state.preprocessing.flatten_background_enabled:
            self._current_image_key = None
            self._schedule_image_refresh()
        else:
            self._invalidate_background_profile_cache()
        if self._showing_background_profile_main:
            self._update_background_profile_preview()

    def _update_chromatic_settings(self) -> None:
        self._push_undo_point("Chromatic correction")
        self._append_workflow_log("Chromatic link | toggled", level="debug")
        self._state.preprocessing.chromatic_correction_enabled = bool(self.chromatic_apply_check.isChecked())
        self.chromatic_apply_check.setIcon(self._make_link_toggle_icon(bool(self.chromatic_apply_check.isChecked())))
        self._set_section_applied(self.chromatic_section, bool(self._state.preprocessing.chromatic_correction_enabled))
        self._state.preprocessing.chromatic_registration_mode = "landmark_radial"
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._chromatic_controller.update_control_state()
        self._update_chromatic_summary()
        self._schedule_processing_state_save()
        self._current_image_key = None
        self._schedule_image_refresh()

    def _estimate_chromatic_models(self) -> None:
        self._chromatic_controller.estimate_models()

    def _clear_chromatic_models(self, *, push_undo: bool = True) -> None:
        if push_undo:
            self._push_undo_point("Chromatic correction")
        self._append_workflow_log("Clearing chromatic transforms.", level="warning")
        self._state.chromatic_models.clear()
        self._state.preprocessing.chromatic_correction_enabled = False
        self.chromatic_apply_check.blockSignals(True)
        self.chromatic_apply_check.setChecked(False)
        self.chromatic_apply_check.blockSignals(False)
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_chromatic_summary()
        self._schedule_processing_state_save()
        self._current_image_key = None
        self._schedule_image_refresh()
        self._set_status_text("Cleared chromatic transforms.")

    def _on_chromatic_transform_button_clicked(self) -> None:
        if not self._state.dataset or self._chromatic_auto_running:
            return
        if self._state.chromatic_models:
            self._clear_chromatic_models()
            return
        self._chromatic_controller.estimate_models()

    def _update_spot_detection_labels(self, *, sync_controls: bool = True) -> None:
        settings = self._state.spot_detection
        diameter = max(2 * settings.spot_radius_px, 2)
        ring_inner_diameter = max(int(round(2 * settings.ring_inner_radius_px)), 0)
        ring_outer_diameter = max(int(round(2 * settings.ring_outer_radius_px)), 0)
        spot_area_px2 = np.pi * (float(diameter) / 2.0) ** 2
        ring_inner_radius_px = float(ring_inner_diameter) / 2.0
        ring_outer_radius_px = max(float(ring_outer_diameter) / 2.0, ring_inner_radius_px)
        ring_area_px2 = np.pi * max(ring_outer_radius_px * ring_outer_radius_px - ring_inner_radius_px * ring_inner_radius_px, 0.0)
        area_diff_px2 = spot_area_px2 - ring_area_px2
        if sync_controls:
            self.spot_diameter_spin.blockSignals(True)
            self.ring_inner_diameter_spin.blockSignals(True)
            self.ring_outer_diameter_spin.blockSignals(True)
            self.spot_diameter_spin.setValue(self._format_length_display_value(diameter))
            self.ring_inner_diameter_spin.setValue(self._format_length_display_value(ring_inner_diameter))
            self.ring_outer_diameter_spin.setValue(self._format_length_display_value(ring_outer_diameter))
            self.spot_diameter_spin.blockSignals(False)
            self.ring_inner_diameter_spin.blockSignals(False)
            self.ring_outer_diameter_spin.blockSignals(False)
        self.spot_geometry_area_label.setText(
            f"A_s={self._area_value_text(spot_area_px2)}, "
            f"A_r={self._area_value_text(ring_area_px2)}, "
            f"A_diff={self._area_delta_text(area_diff_px2)}"
        )

    def _apply_spot_geometry_preview(self, *, recalculate: bool) -> None:
        if not self._state.detected_spots:
            self._update_spot_overlays()
            return
        selected_ids = set(self._selected_spot_ids)
        apply_all = bool(self.spot_geometry_scope_button.isChecked())
        for spot in self._state.detected_spots:
            if apply_all or spot.spot_id in selected_ids:
                spot.radius_px = float(self._state.spot_detection.spot_radius_px)
                if spot.spot_diameter_px is not None:
                    spot.spot_diameter_px = float(2 * self._state.spot_detection.spot_radius_px)
                if spot.ring_inner_diameter_px is not None:
                    spot.ring_inner_diameter_px = float(2 * self._state.spot_detection.ring_inner_radius_px)
                if spot.ring_outer_diameter_px is not None:
                    spot.ring_outer_diameter_px = float(2 * self._state.spot_detection.ring_outer_radius_px)

        if recalculate and self._current_processed_image is not None:
            self._request_spot_metrics_refresh(save_after=False, refresh_histogram=False)
        self._update_spot_overlays()
        if recalculate:
            self._schedule_histogram_refresh()
        else:
            self._update_spot_detection_labels(sync_controls=False)

    def _update_geometry_settings(self, *, save: bool, recalculate: bool, normalize_relation: bool = True) -> None:
        selected_ids = set(self._selected_spot_ids)
        apply_all = bool(self.spot_geometry_scope_button.isChecked())
        self._state.spot_detection.spot_radius_px = max(float(self._length_display_to_px(float(self.spot_diameter_spin.value())) / 2.0), 1.0)
        ring_inner_radius = max(self._length_display_to_px(float(self.ring_inner_diameter_spin.value())) / 2.0, 0.0)
        ring_outer_radius = max(self._length_display_to_px(float(self.ring_outer_diameter_spin.value())) / 2.0, 0.0)
        if normalize_relation and ring_outer_radius < ring_inner_radius:
            ring_outer_radius = ring_inner_radius
            self.ring_outer_diameter_spin.blockSignals(True)
            self.ring_outer_diameter_spin.setValue(self._length_px_to_display(ring_outer_radius * 2.0))
            self.ring_outer_diameter_spin.blockSignals(False)
        self._state.spot_detection.ring_inner_radius_px = ring_inner_radius
        self._state.spot_detection.ring_outer_radius_px = ring_outer_radius
        if apply_all:
            for spot in self._state.detected_spots:
                spot.spot_diameter_px = float(self._state.spot_detection.spot_radius_px * 2.0)
                spot.ring_inner_diameter_px = float(ring_inner_radius * 2.0)
                spot.ring_outer_diameter_px = float(ring_outer_radius * 2.0)
        else:
            for spot in self._state.detected_spots:
                if spot.spot_id in selected_ids:
                    spot.spot_diameter_px = float(self._state.spot_detection.spot_radius_px * 2.0)
                    spot.ring_inner_diameter_px = float(ring_inner_radius * 2.0)
                    spot.ring_outer_diameter_px = float(ring_outer_radius * 2.0)
        self._update_spot_detection_labels(sync_controls=False)
        self._apply_spot_geometry_preview(recalculate=recalculate)
        self._update_spot_list_table()
        if save:
            self._save_processing_state_for_dataset()

    def _refresh_spot_geometry(self) -> None:
        self._apply_spot_geometry_preview(recalculate=True)
        self._save_processing_state_for_dataset()
        self.status_label.setText("Spot geometry refreshed.")

    def _commit_spot_geometry_edits(self) -> None:
        self._push_undo_point("Spot geometry")
        self.spot_diameter_spin.interpretText()
        self.ring_inner_diameter_spin.interpretText()
        self.ring_outer_diameter_spin.interpretText()
        self._update_geometry_settings(save=True, recalculate=self.spot_editor_section.is_applied(), normalize_relation=True)

    def _detect_spots(self) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for spot detection.")
            return
        if not self._is_current_reference_image():
            self.status_label.setText("Switch to the reference image before detecting spots.")
            return
        self._push_undo_point("Detect spots")
        self._update_spot_detection_settings()
        self._spot_detection_request_id += 1
        request_id = self._spot_detection_request_id
        image_key = self._current_image_key
        image = self._current_processed_image
        settings = deepcopy(self._state.spot_detection)
        worker = FunctionWorker(
            _detect_spots_task,
            image,
            settings,
            self._current_external_mask(),
            supports_progress=True,
        )
        self._begin_busy("Detecting spots...")
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.result.connect(
            lambda detected_spots,
            request_id=request_id,
            image_key=image_key: self._on_detect_spots_ready(request_id, image_key, detected_spots)
        )
        worker.signals.error.connect(lambda message: self._on_detect_spots_failed(message))
        self._thread_pool.start(worker)

    def _reorder_spots_by_position(self) -> None:
        if not self._state.detected_spots:
            self.status_label.setText("No spots available to reorder.")
            return
        self._push_undo_point("Reorder spots by position")
        rows = max(int(self._state.spot_detection.array_rows), 0)
        cols = max(int(self._state.spot_detection.array_cols), 0)
        spots = list(self._state.detected_spots)
        if rows > 0 and cols > 0 and rows * cols == len(spots):
            ordered = self._order_spots_as_array(spots, rows=rows, cols=cols)
        else:
            ordered = self._order_spots_as_array(spots, rows=rows if rows > 0 else None, cols=cols if cols > 0 else None)
        id_map = {spot.spot_id: new_id for new_id, spot in enumerate(ordered, start=1)}
        for new_id, spot in enumerate(ordered, start=1):
            spot.spot_id = new_id
        for group in self._state.spot_groups:
            group.spot_ids = [id_map.get(spot_id, spot_id) for spot_id in group.spot_ids]
            group.spot_ids = sorted(dict.fromkeys(group.spot_ids))
        self._state.detected_spots = ordered
        self._selected_spot_ids = {id_map.get(spot_id, spot_id) for spot_id in self._selected_spot_ids if spot_id in id_map}
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_selection_dependent_plots(force=True)
        self._save_processing_state_for_dataset()
        self._update_spot_list_table()
        self.status_label.setText("Reordered spots by image position.")

    def _spot_reorder_row_band(self) -> float:
        spacing = max(float(self._state.spot_detection.array_spacing_px), 0.0)
        diameters = [
            float(spot.spot_diameter_px)
            for spot in self._state.detected_spots
            if spot.spot_diameter_px is not None and float(spot.spot_diameter_px) > 0.0
        ]
        if diameters:
            diameter_scale = float(np.median(np.asarray(diameters, dtype=np.float64)))
        else:
            diameter_scale = float(max(self._state.spot_detection.spot_radius_px * 2.0, 1.0))
        band_from_spacing = spacing * 0.45 if spacing > 0.0 else 0.0
        band_from_diameter = diameter_scale * 0.75
        return float(max(band_from_spacing, band_from_diameter, 5.0))

    def _order_spots_as_array(
        self,
        spots: list[DetectedSpot],
        *,
        rows: int | None,
        cols: int | None,
    ) -> list[DetectedSpot]:
        if not spots:
            return []
        sorted_spots = sorted(spots, key=lambda spot: (float(spot.center_y), float(spot.center_x), int(spot.spot_id)))
        row_band = self._spot_reorder_row_band()
        row_groups: list[list[DetectedSpot]] = []
        row_centers: list[float] = []

        for spot in sorted_spots:
            y = float(spot.center_y)
            best_index = -1
            best_distance = float("inf")
            for index, center_y in enumerate(row_centers):
                distance = abs(y - center_y)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index >= 0 and best_distance <= row_band:
                row_groups[best_index].append(spot)
                row_centers[best_index] = float(np.mean([float(item.center_y) for item in row_groups[best_index]]))
            else:
                row_groups.append([spot])
                row_centers.append(y)

        if rows is not None and rows > 0 and len(row_groups) != rows:
            row_groups = [list(group) for group in np.array_split(np.asarray(sorted_spots, dtype=object), rows)]

        row_groups = [sorted(group, key=lambda spot: (float(spot.center_x), int(spot.spot_id))) for group in row_groups]
        row_groups.sort(key=lambda group: float(np.mean([float(spot.center_y) for spot in group])) if group else 0.0)

        ordered: list[DetectedSpot] = []
        for row_group in row_groups:
            if cols is not None and cols > 0:
                ordered.extend(row_group[:cols])
            else:
                ordered.extend(row_group)
        return ordered

    def _on_detect_spots_ready(
        self,
        request_id: int,
        image_key: tuple[int, float] | None,
        detected_spots: list[DetectedSpot],
    ) -> None:
        self._end_busy()
        if request_id != self._spot_detection_request_id:
            return
        if self._current_image_key != image_key:
            return
        self._state.detected_spots = detected_spots
        self._state.spot_groups.clear()
        self._selected_spot_ids.clear()
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_spot_overlays()
        self._schedule_histogram_refresh()
        self._update_spot_summary()
        self._update_selection_dependent_plots(force=True)
        if self._showing_background_profile_main:
            self._update_background_profile_preview()
        self._schedule_processing_state_save()
        self._set_status_text(f"Detected {len(self._state.detected_spots)} spots on the reference image.")
        self._append_workflow_log(
            f"Spot detection done | spots {len(self._state.detected_spots)}",
            level="success",
        )

    def _on_detect_spots_failed(self, message: str) -> None:
        self._end_busy()
        self._background_error("Spot detection", message)

    def _clear_detected_spots(self, persist: bool = True) -> None:
        if self._state.detected_spots:
            answer = QMessageBox.question(
                self,
                "Clear all spots",
                "Remove all detected spots and groups from the current dataset?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._push_undo_point("Clear spots")
        self._state.detected_spots.clear()
        self._state.spot_groups.clear()
        self._selected_spot_ids.clear()
        self._invalidate_background_profile_cache()
        self._update_spot_overlays()
        self._schedule_histogram_refresh()
        self._update_spot_summary()
        if self._showing_background_profile_main:
            self._update_background_profile_preview()
        self._save_processing_state_for_dataset()
        if persist:
            self.status_label.setText("Cleared detected spots.")

    def _update_spot_summary(self) -> None:
        count = len(self._state.detected_spots)
        if count == 0:
            self.spot_summary.setText("No spots detected.")
            self._clear_absorbance_spectrum("Detect spots to show absorbance spectrum.")
            return
        selected_details = ""
        if len(self._selected_spot_ids) == 1:
            selected_id = next(iter(self._selected_spot_ids))
            display_spot = next((spot for spot in self._display_spots() if spot.spot_id == selected_id), None)
            if display_spot is not None:
                label = self._array_label_for_spot(selected_id)
                label_text = f" ({label})" if label is not None else ""
                selected_details = (
                    f"\nSelected spot: {selected_id}{label_text}"
                    f"\nPosition: x={display_spot.center_x:.1f}, y={display_spot.center_y:.1f} px"
                )
                source_spot = self._spot_by_id(selected_id)
                if source_spot is not None and not self._is_current_reference_image():
                    dx = float(display_spot.center_x - source_spot.center_x)
                    dy = float(display_spot.center_y - source_spot.center_y)
                    selected_details += f"\nShift from ref: dx={dx:+.1f}, dy={dy:+.1f} px"
        self.spot_summary.setText(
            f"Detected spots: {count}\nGroups: {len(self._state.spot_groups)}{selected_details}"
        )
        if not self._dragging_spots and not self._spot_edit_refresh_pending and not self._analysis_live_preview_enabled:
            self._schedule_absorbance_spectrum_refresh()

    def _group_for_spot(self, spot_id: int) -> SpotGroup | None:
        for group in self._state.spot_groups:
            if spot_id in group.spot_ids:
                return group
        return None

    def _groups_for_spot(self, spot_id: int) -> list[SpotGroup]:
        return [group for group in self._state.spot_groups if spot_id in group.spot_ids]

    def _select_group_members_for_spot(self, spot_id: int) -> bool:
        groups = self._groups_for_spot(spot_id)
        if not groups:
            return False
        selected_ids = {int(member_id) for group in groups for member_id in group.spot_ids}
        if not selected_ids:
            return False
        if selected_ids == self._selected_spot_ids:
            return True
        self._selected_spot_ids = selected_ids
        self._update_spot_overlays()
        self._update_spot_summary()
        self._sync_spot_list_table_selection()
        self._update_selection_dependent_plots(prompt_live_preview=True)
        return True

    def _ungroup_selected_spots(self) -> None:
        if not self._selected_spot_ids:
            self.status_label.setText("Select spot(s) first to ungroup them.")
            return
        if not any(group.spot_ids for group in self._state.spot_groups):
            self.status_label.setText("No grouped spots are selected.")
            return
        self._append_workflow_log(f"Groups | ungroup {len(self._selected_spot_ids)} spot(s)", level="warning")
        self._push_undo_point("Ungroup spots")
        selected_ids = set(self._selected_spot_ids)
        for group in self._state.spot_groups:
            group.spot_ids = [spot_id for spot_id in group.spot_ids if spot_id not in selected_ids]
        self._state.spot_groups = [group for group in self._state.spot_groups if group.spot_ids]
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_spot_list_table()
        self._save_processing_state_for_dataset()
        self.status_label.setText("Removed selected spots from their groups.")

    def _destroy_groups_for_spot(self, spot_id: int) -> None:
        groups = self._groups_for_spot(spot_id)
        if not groups:
            self.status_label.setText("No group is assigned to the selected spot.")
            return
        self._push_undo_point("Destroy group")
        self._append_workflow_log(f"Groups | destroy for spot {spot_id}", level="warning")
        group_names = [group.name for group in groups if group.name]
        remaining_groups = [group for group in self._state.spot_groups if group not in groups]
        self._state.spot_groups = remaining_groups
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_spot_list_table()
        self._save_processing_state_for_dataset()
        group_text = ", ".join(group_names) if group_names else "group"
        self.status_label.setText(f"Destroyed {group_text}; member spots are now free.")

    def _show_analysis_spot_context_menu(self, spot_id: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        group_action = menu.addAction("Group...")
        select_group_action = None
        ungroup_action = None
        destroy_group_action = None
        groups = self._groups_for_spot(spot_id)
        if groups:
            select_group_action = menu.addAction("Select group members")
            ungroup_action = menu.addAction("Ungroup")
            destroy_group_action = menu.addAction("Destroy group")
            menu.addSeparator()
        action = menu.exec(global_pos)
        if action is None:
            return
        if action is group_action:
            self._group_selected_spots()
        elif select_group_action is not None and action is select_group_action:
            if self._select_group_members_for_spot(spot_id):
                self.status_label.setText(f"Selected group members for spot {spot_id}.")
        elif ungroup_action is not None and action is ungroup_action:
            self._ungroup_selected_spots()
        elif destroy_group_action is not None and action is destroy_group_action:
            self._destroy_groups_for_spot(spot_id)

    def _reindex_detected_spots(self) -> None:
        spot_id_map: dict[int, int] = {}
        for new_id, spot in enumerate(self._state.detected_spots, start=1):
            spot_id_map[spot.spot_id] = new_id
            spot.spot_id = new_id

        updated_groups: list[SpotGroup] = []
        for group in self._state.spot_groups:
            group.spot_ids = [spot_id_map[spot_id] for spot_id in group.spot_ids if spot_id in spot_id_map]
            if group.spot_ids:
                updated_groups.append(group)
        self._state.spot_groups = updated_groups

    def _remove_selected_spots(self) -> None:
        if not self._selected_spot_ids:
            self.status_label.setText("Select spot(s) first to remove them.")
            return
        self._append_workflow_log(f"Spots | remove {len(self._selected_spot_ids)} selected", level="warning")
        self._push_undo_point("Remove spots")
        removed_count = len(self._selected_spot_ids)
        self._state.detected_spots = [
            spot for spot in self._state.detected_spots if spot.spot_id not in self._selected_spot_ids
        ]
        self._reindex_detected_spots()
        self._selected_spot_ids.clear()
        self._update_spot_overlays()
        if self._active_tool == "spots":
            self._mark_spot_edit_refresh_pending()
        else:
            self._schedule_histogram_refresh()
        self._update_spot_summary()
        if self._active_tool != "spots":
            self._save_processing_state_for_dataset()
        self.status_label.setText(f"Removed {removed_count} selected spot(s).")

    def _group_selected_spots(self) -> None:
        if not self._selected_spot_ids:
            self.status_label.setText("Select spot(s) first to create a group.")
            return

        current_group = self._group_for_spot(min(self._selected_spot_ids))
        default_name = current_group.name if current_group is not None else f"Group {len(self._state.spot_groups) + 1}"
        name, accepted = QInputDialog.getText(self, "Spot group", "Group name", text=default_name)
        if not accepted:
            return
        name = name.strip()
        if not name:
            self.status_label.setText("Group creation cancelled: name is required.")
            return

        initial_color = QColor(current_group.spot_color_hex) if current_group is not None else QColor("#f59e0b")
        color = QColorDialog.getColor(initial_color, self, "Group spot color")
        if not color.isValid():
            return
        self._push_undo_point("Group spots")
        self._append_workflow_log(
            f"Groups | create '{name}' with {len(self._selected_spot_ids)} spot(s)",
            level="success",
        )

        for group in self._state.spot_groups:
            group.spot_ids = [spot_id for spot_id in group.spot_ids if spot_id not in self._selected_spot_ids]
        self._state.spot_groups = [group for group in self._state.spot_groups if group.spot_ids]

        target_group = next((group for group in self._state.spot_groups if group.name == name), None)
        if target_group is None:
            target_group = SpotGroup(
                group_id=f"group_{len(self._state.spot_groups) + 1}",
                name=name,
                spot_color_hex=color.name(),
                ring_color_hex=self._ring_visual_color.name(),
                spot_ids=sorted(self._selected_spot_ids),
            )
            self._state.spot_groups.append(target_group)
        else:
            target_group.spot_color_hex = color.name()
            target_group.spot_ids = sorted(set(target_group.spot_ids).union(self._selected_spot_ids))

        self._update_spot_overlays()
        self._update_spot_summary()
        self._save_processing_state_for_dataset()
        self.status_label.setText(f"Grouped {len(self._selected_spot_ids)} spot(s) as '{name}'.")

    def _clear_spot_selection(self) -> None:
        self._selected_spot_ids.clear()
        self._update_spot_overlays()
        self._update_spot_summary()
        self._update_selection_dependent_plots(force=True)
        self.status_label.setText("Cleared spot selection.")

    def _update_spot_overlays(self) -> None:
        display_spots = self._display_spots()
        source_spot_map = {spot.spot_id: spot for spot in self._state.detected_spots}
        if self._showing_background_profile_main:
            for bundle in self._spot_overlay_items.values():
                bundle.curve.setVisible(False)
                if bundle.ring_fill is not None:
                    bundle.ring_fill.setVisible(False)
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
                if bundle.label is not None:
                    bundle.label.setVisible(False)
            self._update_guide_overlays()
            return
        current_ids = {spot.spot_id for spot in display_spots}
        for spot_id in list(self._spot_overlay_items):
            if spot_id not in current_ids:
                self._remove_spot_overlay_bundle(spot_id)

        theta = self._spot_overlay_theta
        ring_inner_radius = float(max(self._state.spot_detection.ring_inner_radius_px, 0))
        ring_outer_radius = float(max(self._state.spot_detection.ring_outer_radius_px, self._state.spot_detection.ring_inner_radius_px))
        for spot in display_spots:
            source_spot = source_spot_map.get(spot.spot_id, spot)
            bundle = self._spot_overlay_items.get(spot.spot_id)
            if bundle is None:
                curve = pg.PlotCurveItem()
                curve.setSkipFiniteCheck(True)
                self.image_plot.addItem(curve)
                bundle = SpotOverlayBundle(curve=curve)
                self._spot_overlay_items[spot.spot_id] = bundle
            xs, ys = self._spot_curve_points(source_spot, spot, source_spot.radius_px)
            group = self._group_for_spot(spot.spot_id)
            pen_color = resolved_spot_color(spot, group, self._spot_visual_color)
            pen_color.setAlphaF(self._alpha01(self._spot_alpha))
            fill_color = resolved_spot_color(spot, group, self._spot_visual_color)
            fill_color.setAlphaF(self._alpha01(max(self._spot_alpha * 0.22, 0.08)))
            spot_signature = self._spot_absorbance_signature(source_spot)
            spot_cached = bool(spot_signature is not None and self._spot_absorbance_cache.get(spot_signature) is not None)
            if self._cached_spots_only_visible and not spot_cached and spot.spot_id not in self._selected_spot_ids:
                dim_pen = QColor("#94a3b8")
                dim_pen.setAlphaF(self._alpha01(0.16))
                dim_fill = QColor("#94a3b8")
                dim_fill.setAlphaF(self._alpha01(0.04))
                pen_color = dim_pen
                fill_color = dim_fill
            elif self._cached_spots_only_visible and spot_cached and spot.spot_id not in self._selected_spot_ids:
                cached_pen = QColor("#22c55e")
                cached_pen.setAlphaF(self._alpha01(max(self._spot_alpha, 0.9)))
                cached_fill = QColor("#22c55e")
                cached_fill.setAlphaF(self._alpha01(max(self._spot_alpha * 0.16, 0.06)))
                pen_color = cached_pen
                fill_color = cached_fill
            pen = pg.mkPen(pen_color, width=2)
            brush = pg.mkBrush(fill_color)
            if spot.inferred:
                inferred_color = QColor("#f59e0b")
                inferred_color.setAlphaF(self._alpha01(max(self._spot_alpha, 0.45)))
                pen = pg.mkPen(inferred_color, width=2, style=Qt.PenStyle.DashLine)
                inferred_fill = QColor("#f59e0b")
                inferred_fill.setAlphaF(self._alpha01(max(self._spot_alpha * 0.15, 0.08)))
                brush = pg.mkBrush(inferred_fill)
            if spot.spot_id in self._selected_spot_ids:
                selected_color = QColor("#38bdf8")
                selected_color.setAlphaF(1.0)
                pen = pg.mkPen(selected_color, width=3)
                selected_fill = QColor("#38bdf8")
                selected_fill.setAlphaF(0.2)
                brush = pg.mkBrush(selected_fill)
            bundle.curve.setData(xs, ys)
            bundle.curve.setPen(pen)
            bundle.curve.setFillLevel(spot.center_y)
            bundle.curve.setBrush(brush)
            bundle.curve.setVisible(self._spots_visible)
            if self._rings_visible and ring_outer_radius > 0.0:
                ring_color = resolved_ring_color(spot, group, self._ring_visual_color)
                ring_color.setAlphaF(self._alpha01(max(self._ring_alpha * 1.3, 0.18)))
                ring_fill = resolved_ring_color(spot, group, self._ring_visual_color)
                ring_fill.setAlphaF(self._alpha01(max(self._ring_alpha, 0.03)))
                if spot.spot_id in self._selected_spot_ids:
                    ring_color = QColor("#38bdf8")
                    ring_color.setAlphaF(self._alpha01(0.85))
                    ring_fill = QColor("#38bdf8")
                    ring_fill.setAlphaF(self._alpha01(max(self._ring_alpha, 0.08)))
                inner_pen = pg.mkPen(ring_color, width=1.4, style=Qt.PenStyle.DashLine)
                outer_pen = pg.mkPen(ring_color, width=1.4, style=Qt.PenStyle.DotLine)
                if ring_inner_radius > 0.0:
                    if bundle.inner_curve is None:
                        bundle.inner_curve = pg.PlotCurveItem()
                        bundle.inner_curve.setSkipFiniteCheck(True)
                        self.image_plot.addItem(bundle.inner_curve)
                    inner_xs, inner_ys = self._spot_curve_points(source_spot, spot, ring_inner_radius)
                    bundle.inner_curve.setData(inner_xs, inner_ys)
                    bundle.inner_curve.setPen(inner_pen)
                    bundle.inner_curve.setVisible(True)
                elif bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.ring_fill is None:
                    bundle.ring_fill = QGraphicsPathItem()
                    self.image_plot.addItem(bundle.ring_fill)
                bundle.ring_fill.setPath(
                    self._create_ring_fill_path(
                        spot.center_x,
                        spot.center_y,
                        ring_inner_radius,
                        ring_outer_radius,
                    )
                )
                bundle.ring_fill.setBrush(QBrush(ring_fill))
                bundle.ring_fill.setPen(QPen(Qt.PenStyle.NoPen))
                bundle.ring_fill.setVisible(True)
                if bundle.outer_curve is None:
                    bundle.outer_curve = pg.PlotCurveItem()
                    bundle.outer_curve.setSkipFiniteCheck(True)
                    self.image_plot.addItem(bundle.outer_curve)
                outer_xs, outer_ys = self._spot_curve_points(source_spot, spot, ring_outer_radius)
                bundle.outer_curve.setData(outer_xs, outer_ys)
                bundle.outer_curve.setPen(outer_pen)
                bundle.outer_curve.setVisible(True)
            else:
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.ring_fill is not None:
                    bundle.ring_fill.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
            label = self._array_label_for_spot(spot.spot_id)
            if label is not None and self._spot_labels_visible:
                is_selected = spot.spot_id in self._selected_spot_ids
                if self._cached_spots_only_visible and not spot_cached and not is_selected:
                    if bundle.label is not None:
                        bundle.label.setVisible(False)
                    continue
                label_color = "#f8fafc" if is_selected else "#ffffff"
                label_background = "#0f766e" if is_selected else "#0f172a"
                label_border = "#5eead4" if is_selected else "#94a3b8"
                label_text = label
                if is_selected:
                    label_text = f"{label}<br><span style='font-size:8.5pt; font-weight:600;'>x={spot.center_x:.1f}, y={spot.center_y:.1f}</span>"
                if bundle.label is None:
                    bundle.label = pg.TextItem(anchor=(0.0, 1.0))
                    self.image_plot.addItem(bundle.label)
                bundle.label.setHtml(
                    "<span style="
                    f"'color:{label_color}; "
                    "font-size:10pt; "
                    "font-weight:700; "
                    f"background:{label_background}; "
                    f"border:1px solid {label_border}; "
                    "border-radius:4px; "
                    "padding:2px 5px;'"
                    f">{label_text}</span>"
                )
                bundle.label.setPos(spot.center_x + spot.radius_px + 4.0, spot.center_y - spot.radius_px - 2.0)
                bundle.label.setVisible(self._spots_visible and self._spot_labels_visible)
            elif bundle.label is not None:
                bundle.label.setVisible(False)
        self._update_guide_overlays()
        if self.spot_list_table.isVisible():
            self._update_spot_list_table()

    def _remove_spot_overlay_bundle(self, spot_id: int) -> None:
        bundle = self._spot_overlay_items.pop(spot_id, None)
        if bundle is None:
            return
        self.image_plot.removeItem(bundle.curve)
        if bundle.ring_fill is not None:
            self.image_plot.removeItem(bundle.ring_fill)
        if bundle.inner_curve is not None:
            self.image_plot.removeItem(bundle.inner_curve)
        if bundle.outer_curve is not None:
            self.image_plot.removeItem(bundle.outer_curve)
        if bundle.label is not None:
            self.image_plot.removeItem(bundle.label)

    def _update_landmark_overlays(self) -> None:
        if self._showing_background_profile_main:
            for bundle in self._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            for bundle in self._chromatic_all_landmark_overlay_items.values():
                bundle.points.setVisible(False)
                if bundle.active_cross is not None:
                    bundle.active_cross.setVisible(False)
                bundle.label.setVisible(False)
            return

        if self._chromatic_reference_points_all_visible:
            for bundle in self._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            self._update_chromatic_all_landmark_overlays()
            return

        if not self._reference_points_visible:
            for bundle in self._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            for bundle in self._chromatic_all_landmark_overlay_items.values():
                bundle.points.setVisible(False)
                if bundle.active_cross is not None:
                    bundle.active_cross.setVisible(False)
                bundle.label.setVisible(False)
            return

        self._clear_chromatic_all_landmark_overlays()
        current_landmarks = self._current_image_landmarks()
        current_ids = {int(mark.landmark_id) for mark in current_landmarks}
        for landmark_id in list(self._landmark_overlay_items):
            if landmark_id not in current_ids:
                bundle = self._landmark_overlay_items.pop(landmark_id)
                self.image_plot.removeItem(bundle.curve)
                self.image_plot.removeItem(bundle.label)

        for mark in current_landmarks:
            landmark_id = int(mark.landmark_id)
            bundle = self._landmark_overlay_items.get(landmark_id)
            if bundle is None:
                curve = pg.PlotCurveItem()
                curve.setSkipFiniteCheck(True)
                self.image_plot.addItem(curve)
                label = pg.TextItem(anchor=(0.0, 1.0))
                self.image_plot.addItem(label)
                bundle = LandmarkOverlayBundle(curve=curve, label=label)
                self._landmark_overlay_items[landmark_id] = bundle
            cross_size = 7.0
            xs = np.asarray(
                [
                    float(mark.x_px) - cross_size,
                    float(mark.x_px) + cross_size,
                    np.nan,
                    float(mark.x_px),
                    float(mark.x_px),
                ],
                dtype=np.float64,
            )
            ys = np.asarray(
                [
                    float(mark.y_px),
                    float(mark.y_px),
                    np.nan,
                    float(mark.y_px) - cross_size,
                    float(mark.y_px) + cross_size,
                ],
                dtype=np.float64,
            )
            is_selected = landmark_id == int(self._selected_landmark_id or self._chromatic_landmark_marker_id)
            pen_color = QColor("#f8fafc" if not is_selected else "#38bdf8")
            bundle.curve.setData(xs, ys)
            bundle.curve.setPen(pg.mkPen(pen_color, width=3.0 if is_selected else 2.2))
            bundle.curve.setVisible(True)
            bundle.label.setHtml(
                "<span style="
                f"'color:{pen_color.name()}; "
                "font-size:10pt; "
                "font-style:italic; "
                "font-weight:700; "
                f"background:{'#0f766e' if is_selected else '#0f172a'}; "
                f"border:1px solid {('#5eead4' if is_selected else '#94a3b8')}; "
                "border-radius:4px; "
                "padding:2px 5px;'"
                f">{landmark_id}</span>"
            )
            bundle.label.setPos(float(mark.x_px) + 8.0, float(mark.y_px) - 8.0)
            bundle.label.setVisible(True)

    def _clear_chromatic_all_landmark_overlays(self) -> None:
        for bundle in self._chromatic_all_landmark_overlay_items.values():
            self.image_plot.removeItem(bundle.points)
            if bundle.active_cross is not None:
                self.image_plot.removeItem(bundle.active_cross)
            self.image_plot.removeItem(bundle.label)
        self._chromatic_all_landmark_overlay_items.clear()

    def _chromatic_wavelength_color(self, wavelength_nm: float) -> QColor:
        wavelength = float(np.clip(float(wavelength_nm), 380.0, 780.0))
        if wavelength < 440.0:
            red = -(wavelength - 440.0) / 60.0
            green = 0.0
            blue = 1.0
        elif wavelength < 490.0:
            red = 0.0
            green = (wavelength - 440.0) / 50.0
            blue = 1.0
        elif wavelength < 510.0:
            red = 0.0
            green = 1.0
            blue = -(wavelength - 510.0) / 20.0
        elif wavelength < 580.0:
            red = (wavelength - 510.0) / 70.0
            green = 1.0
            blue = 0.0
        elif wavelength < 645.0:
            red = 1.0
            green = -(wavelength - 645.0) / 65.0
            blue = 0.0
        else:
            red = 1.0
            green = 0.0
            blue = 0.0
        if wavelength < 420.0:
            factor = 0.28 + 0.72 * (wavelength - 380.0) / 40.0
        elif wavelength > 700.0:
            factor = 0.28 + 0.72 * (780.0 - wavelength) / 80.0
        else:
            factor = 1.0
        gamma = 0.85
        red = float(np.clip((red * factor) ** gamma, 0.0, 1.0))
        green = float(np.clip((green * factor) ** gamma, 0.0, 1.0))
        blue = float(np.clip((blue * factor) ** gamma, 0.0, 1.0))
        return QColor.fromRgbF(red, green, blue, 1.0)

    def _transform_chromatic_point_between_keys(
        self,
        point_xy: tuple[float, float],
        source_key: tuple[int, float],
        target_key: tuple[int, float],
    ) -> tuple[float, float] | None:
        if source_key == target_key:
            return float(point_xy[0]), float(point_xy[1])
        source_affine = self._chromatic_affine_for_image_key_any(source_key)
        target_affine = self._chromatic_affine_for_image_key_any(target_key)
        if source_affine is None or target_affine is None:
            return None
        source_matrix = np.asarray(source_affine, dtype=np.float64)
        target_matrix = np.asarray(target_affine, dtype=np.float64)
        if self._is_reference_image_key(source_key):
            source_to_reference = identity_affine_matrix()
        else:
            source_to_reference = invert_affine_matrix(source_matrix)
        if self._is_reference_image_key(target_key):
            reference_to_target = identity_affine_matrix()
        else:
            reference_to_target = target_matrix
        point = np.asarray([[float(point_xy[0]), float(point_xy[1])]], dtype=np.float64)
        reference_point = apply_affine_to_points(point, source_to_reference)[0]
        target_point = apply_affine_to_points(np.asarray([reference_point], dtype=np.float64), reference_to_target)[0]
        return float(target_point[0]), float(target_point[1])

    def _update_chromatic_all_landmark_overlays(self) -> None:
        if self._showing_background_profile_main:
            self._clear_chromatic_all_landmark_overlays()
            return
        current_key = self._current_image_key
        if current_key is None or not self._state.chromatic_landmarks:
            self._clear_chromatic_all_landmark_overlays()
            return
        linked_preview = bool(self._state.preprocessing.chromatic_correction_enabled and self._state.chromatic_models)

        grouped: dict[int, list[tuple[float, float, float, tuple[int, float]]]] = {}
        for mark in self._state.chromatic_landmarks:
            grouped.setdefault(int(mark.landmark_id), []).append(
                (float(mark.x_px), float(mark.y_px), float(mark.wavelength_nm), (int(mark.frame_index), float(mark.wavelength_nm)))
            )

        current_ids = set(grouped)
        for landmark_id in list(self._chromatic_all_landmark_overlay_items):
            if landmark_id not in current_ids:
                bundle = self._chromatic_all_landmark_overlay_items.pop(landmark_id)
                self.image_plot.removeItem(bundle.points)
                self.image_plot.removeItem(bundle.label)

        for landmark_id, items in grouped.items():
            bundle = self._chromatic_all_landmark_overlay_items.get(landmark_id)
            if bundle is None:
                points = pg.ScatterPlotItem(pxMode=True)
                points.setZValue(42)
                self.image_plot.addItem(points)
                active_cross = pg.PlotCurveItem()
                active_cross.setSkipFiniteCheck(True)
                active_cross.setZValue(44)
                self.image_plot.addItem(active_cross)
                label = pg.TextItem(anchor=(0.0, 1.0))
                label.setZValue(43)
                self.image_plot.addItem(label)
                bundle = ChromaticLandmarkAllOverlayBundle(points=points, active_cross=active_cross, label=label)
                self._chromatic_all_landmark_overlay_items[landmark_id] = bundle
            xs: list[float] = []
            ys: list[float] = []
            colors = [self._chromatic_wavelength_color(item[2]) for item in items]
            pen_colors = [QColor(color) for color in colors]
            brush_colors = [QColor(color) for color in colors]
            for item_x, item_y, _wavelength, source_key in items:
                display_point = (float(item_x), float(item_y))
                if linked_preview:
                    transformed = self._transform_chromatic_point_between_keys(display_point, source_key, current_key)
                    if transformed is not None:
                        display_point = transformed
                xs.append(float(display_point[0]))
                ys.append(float(display_point[1]))
            bundle.points.setData(
                x=xs,
                y=ys,
                size=10.5,
                symbol="+",
                pen=[pg.mkPen(color, width=1.3) for color in pen_colors],
                brush=None,
            )
            bundle.points.setVisible(True)
            current_item = next((item for item in items if item[3] == current_key), None)
            if current_item is not None:
                cross_size = 8.0
                cross_xs = np.asarray(
                    [
                        float(xs[items.index(current_item)]) - cross_size,
                        float(xs[items.index(current_item)]) + cross_size,
                        np.nan,
                        float(xs[items.index(current_item)]),
                        float(xs[items.index(current_item)]),
                    ],
                    dtype=np.float64,
                )
                cross_ys = np.asarray(
                    [
                        float(ys[items.index(current_item)]),
                        float(ys[items.index(current_item)]),
                        np.nan,
                        float(ys[items.index(current_item)]) - cross_size,
                        float(ys[items.index(current_item)]) + cross_size,
                    ],
                    dtype=np.float64,
                )
                bundle.active_cross.setData(cross_xs, cross_ys)
                bundle.active_cross.setPen(pg.mkPen("#f8fafc", width=3.4))
                bundle.active_cross.setVisible(True)
            elif bundle.active_cross is not None:
                bundle.active_cross.setVisible(False)

            representative_index = 0
            current_wavelength = float(current_key[1])
            for index, item in enumerate(items):
                if abs(float(item[2]) - current_wavelength) < 1e-6:
                    representative_index = index
                    break
            rep_x, rep_y, rep_wavelength, _ = items[representative_index]
            rep_display_x = xs[representative_index]
            rep_display_y = ys[representative_index]
            label_color = self._chromatic_wavelength_color(rep_wavelength)
            bundle.label.setHtml(
                "<span style="
                f"'color:{label_color.name()}; "
                "font-size:10pt; "
                "font-style:italic; "
                "font-weight:700; "
                f"background:{'#0f172a'}; "
                f"border:1px solid {label_color.name()}; "
                "border-radius:4px; "
                "padding:2px 5px;'"
                f">{landmark_id}</span>"
            )
            bundle.label.setPos(float(rep_display_x) + 8.0, float(rep_display_y) - 8.0)
            bundle.label.setVisible(True)

    def _create_ring_fill_path(
        self,
        center_x: float,
        center_y: float,
        inner_radius: float,
        outer_radius: float,
    ) -> QPainterPath:
        outer_radius = max(float(outer_radius), 0.0)
        inner_radius = max(min(float(inner_radius), outer_radius), 0.0)

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addEllipse(QRectF(center_x - outer_radius, center_y - outer_radius, outer_radius * 2.0, outer_radius * 2.0))
        if inner_radius > 0.0:
            path.addEllipse(QRectF(center_x - inner_radius, center_y - inner_radius, inner_radius * 2.0, inner_radius * 2.0))
        return path

    def _update_guide_overlays(self) -> None:
        guide = self._ensure_image_tool_guide()
        if guide is None or self._current_processed_image is None or self._showing_background_profile_main:
            for bundle in self._guide_overlay_items.values():
                bundle.vertical.setVisible(False)
                bundle.horizontal.setVisible(False)
                bundle.marker.setVisible(False)
            self._hide_measurement_overlay()
            self._refresh_scale_bar_overlay()
            return
        visible = self._active_tool in {"rotate", "crop"}
        if not visible:
            guide.vertical.setVisible(False)
            guide.horizontal.setVisible(False)
            guide.marker.setVisible(False)
        else:
            image_height, image_width = self._current_processed_image.shape[:2]
            current_pos = guide.marker.pos()
            x = float(np.clip(current_pos.x(), 0.0, max(float(image_width - 1), 0.0)))
            y = float(np.clip(current_pos.y(), 0.0, max(float(image_height - 1), 0.0)))
            if abs(float(current_pos.x()) - x) > 1e-6 or abs(float(current_pos.y()) - y) > 1e-6:
                guide.marker.blockSignals(True)
                guide.marker.setPos((x, y))
                guide.marker.blockSignals(False)
            guide.vertical.setData([x, x], [0.0, max(float(image_height - 1), 0.0)])
            guide.horizontal.setData([0.0, max(float(image_width - 1), 0.0)], [y, y])
            guide.vertical.setVisible(True)
            guide.horizontal.setVisible(True)
            guide.marker.setVisible(True)
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()

    def _ensure_image_tool_guide(self) -> GuideOverlayBundle | None:
        guide = self._guide_overlay_items.get(0)
        if guide is not None:
            return guide
        if self._current_processed_image is not None:
            image_height, image_width = self._current_processed_image.shape[:2]
            initial_pos = (
                max(float(image_width - 1), 0.0) / 2.0,
                max(float(image_height - 1), 0.0) / 2.0,
            )
        else:
            initial_pos = (0.0, 0.0)
        marker = pg.TargetItem(
            pos=initial_pos,
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverPen=pg.mkPen("#38bdf8", width=2.0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            size=12,
        )
        vertical = pg.PlotCurveItem(pen=pg.mkPen(QColor(56, 189, 248, 120), width=1.2))
        horizontal = pg.PlotCurveItem(pen=pg.mkPen(QColor(56, 189, 248, 120), width=1.2))
        marker.sigPositionChanged.connect(self._on_image_tool_guide_moved)
        marker.sigPositionChangeFinished.connect(self._on_image_tool_guide_moved)
        self.image_plot.addItem(vertical)
        self.image_plot.addItem(horizontal)
        self.image_plot.addItem(marker)
        guide = GuideOverlayBundle(vertical=vertical, horizontal=horizontal, marker=marker)
        self._guide_overlay_items[0] = guide
        return guide

    def _on_image_tool_guide_moved(self, *_args) -> None:
        self._update_guide_overlays()

    def _update_ome_zarr_chunk_guide_overlay(self) -> None:
        for item in self._ome_zarr_chunk_overlay_items:
            try:
                self.image_plot.removeItem(item)
            except Exception:
                pass
        self._ome_zarr_chunk_overlay_items.clear()
        if (
            not self.ome_zarr_chunk_guide_button.isChecked()
            or self._current_processed_image is None
            or self._showing_background_profile_main
        ):
            return
        image_height, image_width = self._current_processed_image.shape[:2]
        chunk_size = max(int(self._current_ome_zarr_chunk_size()), 1)
        pen = pg.mkPen(QColor(56, 189, 248, 120), width=1.0, style=Qt.PenStyle.DashLine)
        for x in range(chunk_size, max(int(image_width), 1), chunk_size):
            line = pg.InfiniteLine(angle=90, pos=float(x), pen=pen, movable=False)
            line.setZValue(14)
            self.image_plot.addItem(line)
            self._ome_zarr_chunk_overlay_items.append(line)
        for y in range(chunk_size, max(int(image_height), 1), chunk_size):
            line = pg.InfiniteLine(angle=0, pos=float(y), pen=pen, movable=False)
            line.setZValue(14)
            self.image_plot.addItem(line)
            self._ome_zarr_chunk_overlay_items.append(line)

    def _hide_measurement_overlay(self) -> None:
        if self._measurement_overlay is None:
            return
        self._measurement_overlay.connector.setVisible(False)
        self._measurement_overlay.marker_a.setVisible(False)
        self._measurement_overlay.marker_b.setVisible(False)
        self._measurement_overlay.label.setVisible(False)

    def _ensure_measurement_overlay(self) -> MeasurementOverlayBundle:
        if self._measurement_overlay is not None:
            return self._measurement_overlay
        settings = self._state.preprocessing
        marker_a = pg.TargetItem(
            pos=(float(settings.measurement_anchor1_x_px), float(settings.measurement_anchor1_y_px)),
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverPen=pg.mkPen("#22c55e", width=2.0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            size=12,
        )
        marker_b = pg.TargetItem(
            pos=(float(settings.measurement_anchor2_x_px), float(settings.measurement_anchor2_y_px)),
            movable=True,
            pen=pg.mkPen("#f8fafc", width=1.8),
            brush=pg.mkBrush(0, 0, 0, 0),
            hoverPen=pg.mkPen("#22c55e", width=2.0),
            hoverBrush=pg.mkBrush(0, 0, 0, 0),
            size=12,
        )
        connector = pg.PlotCurveItem(pen=pg.mkPen(QColor(34, 197, 94, 180), width=1.8))
        label = pg.TextItem(anchor=(0.5, 1.0))
        marker_a.sigPositionChanged.connect(self._on_measurement_marker_moved)
        marker_b.sigPositionChanged.connect(self._on_measurement_marker_moved)
        marker_a.sigPositionChangeFinished.connect(self._on_measurement_marker_moved)
        marker_b.sigPositionChangeFinished.connect(self._on_measurement_marker_moved)
        self.image_plot.addItem(connector)
        self.image_plot.addItem(marker_a)
        self.image_plot.addItem(marker_b)
        self.image_plot.addItem(label)
        self._measurement_overlay = MeasurementOverlayBundle(
            connector=connector,
            marker_a=marker_a,
            marker_b=marker_b,
            label=label,
        )
        return self._measurement_overlay

    def _update_measurement_overlay(self) -> None:
        if self._current_processed_image is None or self._showing_background_profile_main or self._active_tool != "measure":
            self._hide_measurement_overlay()
            return
        overlay = self._ensure_measurement_overlay()
        image_height, image_width = self._current_processed_image.shape[:2]
        settings = self._state.preprocessing
        points = [
            (overlay.marker_a, "measurement_anchor1_x_px", "measurement_anchor1_y_px"),
            (overlay.marker_b, "measurement_anchor2_x_px", "measurement_anchor2_y_px"),
        ]
        for marker, x_attr, y_attr in points:
            x_value = float(np.clip(float(getattr(settings, x_attr)), 0.0, max(float(image_width - 1), 0.0)))
            y_value = float(np.clip(float(getattr(settings, y_attr)), 0.0, max(float(image_height - 1), 0.0)))
            setattr(settings, x_attr, x_value)
            setattr(settings, y_attr, y_value)
            current_pos = marker.pos()
            if abs(float(current_pos.x()) - x_value) > 1e-6 or abs(float(current_pos.y()) - y_value) > 1e-6:
                marker.blockSignals(True)
                marker.setPos((x_value, y_value))
                marker.blockSignals(False)
            marker.setVisible(True)
        ax = float(settings.measurement_anchor1_x_px)
        ay = float(settings.measurement_anchor1_y_px)
        bx = float(settings.measurement_anchor2_x_px)
        by = float(settings.measurement_anchor2_y_px)
        overlay.connector.setData([ax, bx], [ay, by])
        overlay.connector.setVisible(True)
        dx, dy, distance = self._measurement_delta_components_px()
        overlay.label.setHtml(
            "<span style="
            "'color:#22c55e; font-size:10pt; font-weight:700; background:#0f172a; "
            "border:1px solid #22c55e; border-radius:4px; padding:2px 5px;'"
            f">{distance:.1f} px</span>"
        )
        overlay.label.setPos((ax + bx) * 0.5, min(ay, by) - 8.0)
        overlay.label.setVisible(True)
        self._update_measurement_status_label(dx_px=dx, dy_px=dy, distance_px=distance)

    def _on_measurement_marker_moved(self, *_args) -> None:
        if self._measurement_overlay is None:
            return
        settings = self._state.preprocessing
        settings.measurement_anchor1_x_px = float(self._measurement_overlay.marker_a.pos().x())
        settings.measurement_anchor1_y_px = float(self._measurement_overlay.marker_a.pos().y())
        settings.measurement_anchor2_x_px = float(self._measurement_overlay.marker_b.pos().x())
        settings.measurement_anchor2_y_px = float(self._measurement_overlay.marker_b.pos().y())
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()
        self._schedule_processing_state_save()

    def _update_measurement_status_label(
        self,
        *,
        dx_px: float | None = None,
        dy_px: float | None = None,
        distance_px: float | None = None,
    ) -> None:
        if dx_px is None or dy_px is None or distance_px is None:
            dx_px, dy_px, distance_px = self._measurement_delta_components_px()
        suffix = ""
        if self._can_display_micrometers():
            suffix = (
                f" | calib {self._state.preprocessing.microns_per_pixel_x:.4f}/{self._state.preprocessing.microns_per_pixel_y:.4f} µm/px"
            )
        self.measurement_status_label.setText(
            f"Δx {abs(dx_px):.1f} px | Δy {abs(dy_px):.1f} px | d {distance_px:.1f} px{suffix}"
        )

    def _apply_measurement_calibration(self) -> None:
        dx_px, dy_px, _distance_px = self._measurement_delta_components_px()
        dx_um = float(self.measurement_um_x_spin.value())
        dy_um = float(self.measurement_um_y_spin.value())
        if dx_um <= 0.0 and dy_um <= 0.0:
            self._set_status_text("Enter a real Δx and/or Δy in µm before applying calibration.")
            return
        if dx_um > 0.0 and abs(dx_px) < 1e-6:
            self._set_status_text("Δx between the ruler guides is zero, so Δx calibration cannot be applied.")
            return
        if dy_um > 0.0 and abs(dy_px) < 1e-6:
            self._set_status_text("Δy between the ruler guides is zero, so Δy calibration cannot be applied.")
            return
        self._push_undo_point("Measurement calibration")
        if dx_um > 0.0:
            self._state.preprocessing.microns_per_pixel_x = abs(dx_um / dx_px)
        if dy_um > 0.0:
            self._state.preprocessing.microns_per_pixel_y = abs(dy_um / dy_px)
        if dx_um > 0.0 and dy_um <= 0.0:
            self._state.preprocessing.microns_per_pixel_y = self._state.preprocessing.microns_per_pixel_x
        if dy_um > 0.0 and dx_um <= 0.0:
            self._state.preprocessing.microns_per_pixel_x = self._state.preprocessing.microns_per_pixel_y
        self._state.preprocessing.calibration_enabled = True
        self._state.preprocessing.display_units = "um"
        self._update_display_unit_controls()
        self._sync_spot_detection_controls()
        self._save_processing_state_for_dataset()
        self._set_status_text("Measurement calibration applied in memory. Display units switched to micrometers.")

    def _sync_measurement_visibility(self) -> None:
        if hasattr(self, "measurement_info_row"):
            self.measurement_info_row.setVisible(self._active_tool == "measure")
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()

    def _ensure_scale_bar_overlay(self) -> ScaleBarOverlayBundle:
        if self._scale_bar_overlay is not None:
            return self._scale_bar_overlay
        outline_line = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=5.0))
        line = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.4))
        outline_left_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
        left_tick = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.0))
        outline_right_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
        right_tick = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.0))
        outline_label = pg.TextItem(anchor=(0.5, 1.0))
        label = pg.TextItem(anchor=(0.5, 1.0))
        self.image_plot.addItem(outline_line)
        self.image_plot.addItem(line)
        self.image_plot.addItem(outline_left_tick)
        self.image_plot.addItem(left_tick)
        self.image_plot.addItem(outline_right_tick)
        self.image_plot.addItem(right_tick)
        self.image_plot.addItem(outline_label)
        self.image_plot.addItem(label)
        self._scale_bar_overlay = ScaleBarOverlayBundle(
            outline_line=outline_line,
            line=line,
            outline_left_tick=outline_left_tick,
            left_tick=left_tick,
            outline_right_tick=outline_right_tick,
            right_tick=right_tick,
            outline_label=outline_label,
            label=label,
        )
        return self._scale_bar_overlay

    @staticmethod
    def _nice_scale_bar_value(target: float) -> float:
        if target <= 0.0:
            return 1.0
        exponent = np.floor(np.log10(target))
        base = target / (10.0 ** exponent)
        if base < 1.5:
            nice_base = 1.0
        elif base < 3.5:
            nice_base = 2.0
        elif base < 7.5:
            nice_base = 5.0
        else:
            nice_base = 10.0
        return float(nice_base * (10.0 ** exponent))

    def _refresh_scale_bar_overlay(self) -> None:
        if self._scale_bar_overlay is None and self._current_processed_image is None:
            return
        overlay = self._ensure_scale_bar_overlay() if self._current_processed_image is not None else self._scale_bar_overlay
        if overlay is not None:
            overlay.line.setPen(pg.mkPen(self._scale_bar_visual_color, width=2.4))
            overlay.left_tick.setPen(pg.mkPen(self._scale_bar_visual_color, width=2.0))
            overlay.right_tick.setPen(pg.mkPen(self._scale_bar_visual_color, width=2.0))
            overlay.outline_line.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=5.0))
            overlay.outline_left_tick.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
            overlay.outline_right_tick.setPen(pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
        if overlay is None or self._showing_background_profile_main or self._current_processed_image is None or not bool(self._state.preprocessing.scale_bar_visible):
            if overlay is not None:
                overlay.outline_line.setVisible(False)
                overlay.line.setVisible(False)
                overlay.outline_left_tick.setVisible(False)
                overlay.left_tick.setVisible(False)
                overlay.outline_right_tick.setVisible(False)
                overlay.right_tick.setVisible(False)
                overlay.outline_label.setVisible(False)
                overlay.label.setVisible(False)
            return
        x_range, y_range = self.image_plot.vb.viewRange()
        x_left, x_right = sorted((float(x_range[0]), float(x_range[1])))
        y_top, y_bottom = sorted((float(y_range[0]), float(y_range[1])))
        visible_width = max(x_right - x_left, 1.0)
        visible_height = max(y_bottom - y_top, 1.0)
        available_px = max(visible_width * 0.22, 30.0)
        if self._display_uses_micrometers():
            bar_value = self._nice_scale_bar_value(available_px * self._microns_per_pixel_scalar())
            bar_length_px = bar_value / self._microns_per_pixel_scalar()
            label_text = f"{bar_value:g} µm"
        else:
            bar_value = self._nice_scale_bar_value(available_px)
            bar_length_px = bar_value
            label_text = f"{bar_value:g} px"
        margin_x = max(visible_width * 0.03, 12.0)
        margin_y = max(visible_height * 0.05, 12.0)
        x1 = x_right - margin_x
        x0 = max(x_left + margin_x, x1 - float(bar_length_px))
        y = y_bottom - margin_y
        tick_half = float(np.clip(visible_height * 0.02, 4.0, 8.0))
        overlay.outline_line.setData([x0, x1], [y, y])
        overlay.line.setData([x0, x1], [y, y])
        overlay.outline_left_tick.setData([x0, x0], [y - tick_half, y + tick_half])
        overlay.left_tick.setData([x0, x0], [y - tick_half, y + tick_half])
        overlay.outline_right_tick.setData([x1, x1], [y - tick_half, y + tick_half])
        overlay.right_tick.setData([x1, x1], [y - tick_half, y + tick_half])
        overlay.outline_label.setHtml(
            "<span style="
            "'color:#f8fafc; font-size:10pt; font-weight:800;'"
            f">{label_text}</span>"
        )
        overlay.label.setHtml(
            "<span style="
            "'color:#05070b; font-size:9pt; font-weight:800;'"
            f">{label_text}</span>"
        )
        label_x = (x0 + x1) * 0.5
        label_y = y - tick_half - max(visible_height * 0.015, 3.0)
        overlay.outline_label.setPos(label_x, label_y)
        overlay.label.setPos(label_x, label_y)
        overlay.outline_line.setVisible(True)
        overlay.line.setVisible(True)
        overlay.outline_left_tick.setVisible(True)
        overlay.left_tick.setVisible(True)
        overlay.outline_right_tick.setVisible(True)
        overlay.right_tick.setVisible(True)
        overlay.outline_label.setVisible(True)
        overlay.label.setVisible(True)

    def _update_ignore_mask_overlay(self) -> None:
        if self._showing_background_profile_main or self._current_processed_image is None:
            self.ignore_mask_item.hide()
            self.histogram_mask_item.hide()
            self.figure_mask_item.hide()
            return

        mask = self._ignored_mask(self._current_processed_image)
        if self._mask_visible and np.any(mask):
            overlay = np.zeros((*mask.shape, 4), dtype=np.uint8)
            overlay_color = np.array(
                [
                    self._mask_visual_color.red(),
                    self._mask_visual_color.green(),
                    self._mask_visual_color.blue(),
                    int(round(self._mask_alpha * 255.0)),
                ],
                dtype=np.uint8,
            )
            overlay[mask] = overlay_color
            self.ignore_mask_item.setImage(np.transpose(overlay, (1, 0, 2)), autoLevels=False)
            self.ignore_mask_item.show()
        else:
            self.ignore_mask_item.hide()

        self.histogram_mask_item.hide()

        if self._mask_figure_preview is not None:
            preview_mask = apply_spatial_mask(self._mask_figure_preview, self._state.preprocessing)
            if preview_mask.shape == mask.shape and np.any(preview_mask):
                fig_overlay = np.zeros((*preview_mask.shape, 4), dtype=np.uint8)
                fig_color = np.array([56, 189, 248, 110], dtype=np.uint8)
                fig_overlay[preview_mask] = fig_color
                self.figure_mask_item.setImage(np.transpose(fig_overlay, (1, 0, 2)), autoLevels=False)
                self.figure_mask_item.show()
            else:
                self.figure_mask_item.hide()
        else:
            self.figure_mask_item.hide()

    def _update_histogram(self, image: np.ndarray) -> None:
        self._plot_manager.update_histogram(image)

    def _update_area_histogram_curves(
        self,
        *,
        edges: np.ndarray,
        spot_values: np.ndarray,
        ring_values: np.ndarray,
        mask_values: np.ndarray,
        total_pixels: float,
        total_counts_display: np.ndarray,
    ) -> float:
        return self._plot_manager.update_area_histogram_curves(
            edges=edges,
            spot_values=spot_values,
            ring_values=ring_values,
            mask_values=mask_values,
            total_pixels=total_pixels,
            total_counts_display=total_counts_display,
        )

    def _histogram_counts_from_values(self, values: np.ndarray, edges: np.ndarray) -> np.ndarray:
        return self._plot_manager.histogram_counts_from_values(values, edges)

    def _histogram_percent_from_values(
        self,
        values: np.ndarray,
        edges: np.ndarray,
        total_pixels: float,
    ) -> np.ndarray:
        return self._plot_manager.histogram_percent_from_values(values, edges, total_pixels)

    def _histogram_source_signature(self, image: np.ndarray) -> tuple[object, ...]:
        return self._plot_manager.histogram_source_signature(image)

    def _ignored_mask_signature(self, image: np.ndarray) -> tuple[object, ...]:
        return self._plot_manager.ignored_mask_signature(image)

    def _ignored_mask(self, image: np.ndarray) -> np.ndarray:
        return self._plot_manager.ignored_mask(image)

    def _histogram_source_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return self._plot_manager.histogram_source_values(image)

    def _set_histogram_curve_data(
        self,
        curve: pg.PlotDataItem,
        edges: np.ndarray,
        counts_percent: np.ndarray,
        color: QColor,
        alpha: float,
    ) -> None:
        self._plot_manager.set_histogram_curve_data(curve, edges, counts_percent, color, alpha)

    def _restyle_histogram_curve(
        self,
        curve: pg.PlotDataItem,
        *,
        color: QColor,
        alpha: float,
        fill_level: float = 0.0,
    ) -> None:
        self._plot_manager.restyle_histogram_curve(curve, color=color, alpha=alpha, fill_level=fill_level)

    def _restyle_area_histogram_curves(self) -> None:
        self._plot_manager.restyle_area_histogram_curves()

    def _update_area_histogram_peak_labels(self) -> None:
        self._plot_manager.update_area_histogram_peak_labels()

    def _update_area_histogram_peak_label(
        self,
        curve: pg.PlotDataItem,
        label: pg.TextItem,
        text: str,
        color: QColor,
    ) -> None:
        self._plot_manager.update_area_histogram_peak_label(curve, label, text, color)

    def _roi_area_masks(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        signature = self._histogram_source_signature(image)
        if self._roi_mask_cache_signature == signature and self._roi_mask_cache_values is not None:
            return self._roi_mask_cache_values

        image_f32 = image.astype(np.float32, copy=False)
        image_height, image_width = image_f32.shape[:2]
        ignored_mask = self._ignored_mask(image_f32)
        spot_mask = np.zeros((image_height, image_width), dtype=bool)
        ring_mask = np.zeros((image_height, image_width), dtype=bool)
        display_spots = self._display_spots()
        if display_spots:
            affine_matrix = self._chromatic_affine_for_image_key(self._current_image_key)
            ring_inner_radius = float(max(self._state.spot_detection.ring_inner_radius_px, 0.0))
            ring_outer_radius = float(max(self._state.spot_detection.ring_outer_radius_px, ring_inner_radius))
            if affine_matrix is None or self._is_current_reference_image():
                yy, xx = np.indices((image_height, image_width), dtype=np.float32)
                for spot in display_spots:
                    distance_sq = (xx - float(spot.center_x)) ** 2 + (yy - float(spot.center_y)) ** 2
                    spot_mask |= distance_sq <= float(spot.radius_px) ** 2
                    if ring_outer_radius > 0.0:
                        outer_mask = distance_sq <= ring_outer_radius ** 2
                        inner_mask = distance_sq < ring_inner_radius ** 2 if ring_inner_radius > 0.0 else np.zeros_like(outer_mask)
                        ring_mask |= outer_mask & ~inner_mask
            else:
                source_spot_map = {spot.spot_id: spot for spot in self._state.detected_spots}
                for spot in display_spots:
                    source_spot = source_spot_map.get(spot.spot_id, spot)
                    spot_mask |= transformed_disk_mask(
                        (image_height, image_width),
                        (float(source_spot.center_x), float(source_spot.center_y)),
                        float(source_spot.radius_px),
                        affine_matrix,
                    )
                    if ring_outer_radius > 0.0:
                        ring_mask |= transformed_annulus_mask(
                            (image_height, image_width),
                            (float(source_spot.center_x), float(source_spot.center_y)),
                            float(ring_inner_radius),
                            float(ring_outer_radius),
                            affine_matrix,
                        )
        spot_mask &= ~ignored_mask
        ring_mask &= ~ignored_mask
        ring_mask &= ~spot_mask
        residual_mask = ~(spot_mask | ring_mask | ignored_mask)
        cached = (spot_mask, ring_mask, ignored_mask, residual_mask)
        self._roi_mask_cache_signature = signature
        self._roi_mask_cache_values = cached
        return cached

    def _roi_intensity_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image_f32 = image.astype(np.float32, copy=False)
        spot_mask, ring_mask, ignored_mask, _residual_mask = self._roi_area_masks(image_f32)
        return (
            image_f32[spot_mask],
            image_f32[ring_mask],
            image_f32[ignored_mask],
        )

    def _on_histogram_region_changed(self) -> None:
        self._push_undo_point("Highlight range")
        lower, upper = self.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        self._state.spot_detection.intensity_min_value = float(
            np.clip(lower, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
        )
        self._state.spot_detection.intensity_max_value = float(
            np.clip(upper, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
        )
        self._state.preprocessing.histogram_highlight_min_value = float(lower)
        self._state.preprocessing.histogram_highlight_max_value = float(upper)
        self._update_selected_intensity_overlay()
        self._save_control_preferences()
        self._save_processing_state_for_dataset()

    def _on_ignore_region_changed(self) -> None:
        return

    def _preview_ignore_region_overlay(self) -> None:
        return

    def _on_histogram_bins_changed(self, _value: int) -> None:
        self._schedule_histogram_refresh()

    def _update_selected_intensity_overlay(self) -> None:
        if self._showing_background_profile_main or self._current_processed_image is None or not self._highlight_visible:
            self.intensity_highlight_item.hide()
            return

        lower, upper = self.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        lower = float(np.clip(lower, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY))
        upper = float(np.clip(upper, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY))
        if lower <= self.HISTOGRAM_MIN_INTENSITY and upper >= self.HISTOGRAM_MAX_INTENSITY:
            self.intensity_highlight_item.hide()
            return

        image = self._current_processed_image.astype(np.float32, copy=False)
        selection_mask = (image >= lower) & (image <= upper)
        if not np.any(selection_mask):
            self.intensity_highlight_item.hide()
            return

        overlay = np.zeros((*selection_mask.shape, 4), dtype=np.uint8)
        overlay[selection_mask] = np.array(
            [
                self._highlight_visual_color.red(),
                self._highlight_visual_color.green(),
                self._highlight_visual_color.blue(),
                int(round(self._highlight_alpha * 255.0)),
            ],
            dtype=np.uint8,
        )
        self.intensity_highlight_item.setImage(np.transpose(overlay, (1, 0, 2)), autoLevels=False)
        self.intensity_highlight_item.show()

    def _update_histogram_region_labels(self) -> None:
        view_range = self.histogram_plot.viewRange()
        top_y = float(view_range[1][1]) if view_range and len(view_range) > 1 else 1.0
        label_top = max(top_y * 0.92, top_y - 0.8)
        label_second = max(top_y * 0.84, top_y - 1.8)

        highlight_lower, highlight_upper = self.hist_region.getRegion()
        if highlight_lower > highlight_upper:
            highlight_lower, highlight_upper = highlight_upper, highlight_lower
        highlight_center = 0.5 * (highlight_lower + highlight_upper)
        self.hist_region_label.setHtml(
            "<span style="
            f"'color:{self._highlight_visual_color.name()}; "
            "font-size:9pt; font-weight:700; background:#0f172a; "
            f"border:1px solid {self._highlight_visual_color.name()}; border-radius:4px; padding:2px 4px;'"
            ">Highlight</span>"
        )
        self.hist_region_label.setPos(highlight_center, max(label_top, 0.2))
        self.hist_region_label.setVisible(True)

        self.ignore_region_label.setVisible(False)

    def _choose_overlay_color(self, target: str) -> None:
        initial = (
            self._mask_visual_color
            if target == "mask"
            else self._spot_visual_color
            if target == "spots"
            else self._ring_visual_color
            if target == "ring"
            else self._highlight_visual_color
        )
        color = QColorDialog.getColor(initial, self, "Choose overlay color")
        if not color.isValid():
            return
        self._push_undo_point("Overlay appearance")
        if target == "mask":
            self._mask_visual_color = color
            self._update_ignore_mask_overlay()
            self._restyle_area_histogram_curves()
        elif target == "spots":
            self._spot_visual_color = color
            self._update_spot_overlays()
            self._restyle_area_histogram_curves()
            self._refresh_visible_spectrum_from_cache()
            self._analysis_controller.update_selection_highlight(force=True)
        elif target == "ring":
            self._ring_visual_color = color
            self._update_spot_overlays()
            self._restyle_area_histogram_curves()
            self._refresh_visible_spectrum_from_cache()
            self._analysis_controller.update_selection_highlight(force=True)
        elif target == "scale_bar":
            self._scale_bar_visual_color = color
            self._refresh_scale_bar_overlay()
        else:
            self._highlight_visual_color = color
            self._update_selected_intensity_overlay()
        self._update_histogram_region_labels()
        self._update_color_button_styles()
        self._save_visual_preferences()

    def _refresh_visible_spectrum_from_cache(self) -> bool:
        if not self._analysis_enabled:
            return False
        selected_source_spots = self._selected_source_spots_snapshot()
        selected_spot_ids = tuple(spot.spot_id for spot in selected_source_spots)
        spot_signature = None
        if len(selected_source_spots) == 1:
            spot_signature = self._spot_absorbance_signature(selected_source_spots[0])
            if spot_signature is not None:
                cached_spot_result = self._spot_absorbance_cache.get(spot_signature)
                if cached_spot_result is not None:
                    self._apply_absorbance_spectrum_result(cached_spot_result)
                    self._spot_absorbance_cache.move_to_end(spot_signature)
                    self._append_workflow_log("Spec repaint | spot cache", level="debug")
                    return True
        signature = self._absorbance_spectrum_signature()
        if signature is None:
            return False
        if not selected_source_spots:
            cached_result = self._absorbance_spectrum_cache.get(signature)
            if cached_result is not None:
                self._apply_absorbance_spectrum_result(cached_result)
                frame_signature = self._absorbance_frame_signature(signature)
                if frame_signature is not None and frame_signature in self._absorbance_frame_cache:
                    self._absorbance_frame_cache.move_to_end(frame_signature)
                self._append_workflow_log("Spec repaint | spectrum cache", level="debug")
                return True
            return False
        cached_result = self._cached_absorbance_result_for_selection(signature, selected_spot_ids)
        if cached_result is not None:
            self._apply_absorbance_spectrum_result(cached_result)
            frame_signature = self._absorbance_frame_signature(signature)
            if frame_signature is not None and frame_signature in self._absorbance_frame_cache:
                self._absorbance_frame_cache.move_to_end(frame_signature)
            self._append_workflow_log("Spec repaint | spectrum cache", level="debug")
            return True
        return False

    def _prompt_live_preview_calculation_choice(self, *, spectrum_hit: bool, sensorgram_hit: bool) -> str | None:
        if (
            not self._analysis_enabled
            or self._state.dataset is None
            or not self._analysis_live_preview_enabled
            or not self._startup_ready
            or self._startup_restore_in_progress
        ):
            return None
        if spectrum_hit and sensorgram_hit:
            return None
        if not self._selected_spot_ids:
            return None
        message = "Cached live preview is not available for the current selection."
        if spectrum_hit and not sensorgram_hit:
            message = "Spectrum is cached, but the sensorgram is not."
        elif sensorgram_hit and not spectrum_hit:
            message = "Sensorgram is cached, but the spectrum is not."
        box = QMessageBox(self)
        box.setWindowTitle("Live Preview")
        box.setText(message)
        box.setInformativeText("Choose what to calculate now.")
        spectrum_button = box.addButton("Calculate spectra", QMessageBox.ButtonRole.AcceptRole)
        sensorgram_button = box.addButton("Calculate sensorgram", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abort", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(spectrum_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == spectrum_button:
            return "spectrum"
        if clicked == sensorgram_button:
            return "sensorgram"
        return None

    def _handle_live_preview_selection_change(self) -> None:
        if (
            not self._analysis_enabled
            or not self._analysis_live_preview_enabled
            or not self._startup_ready
            or self._startup_restore_in_progress
        ):
            return
        selection_signature = tuple(self._selected_spectrum_spot_ids())
        if selection_signature == self._live_preview_prompt_selection_signature:
            return
        spectrum_hit = self._refresh_visible_spectrum_from_cache()
        sensorgram_hit = self._analysis_controller.preview_sensorgram_from_cache()
        if spectrum_hit and sensorgram_hit:
            self._live_preview_prompt_selection_signature = selection_signature
            return
        choice = self._prompt_live_preview_calculation_choice(
            spectrum_hit=spectrum_hit,
            sensorgram_hit=sensorgram_hit,
        )
        self._live_preview_prompt_selection_signature = selection_signature
        if choice == "spectrum":
            self._schedule_absorbance_spectrum_refresh()
        elif choice == "sensorgram":
            self._analysis_controller.calculate_sensorgram_for_range()
        else:
            if not spectrum_hit:
                self._set_spectrum_summary_text(
                    f"{self._spectrum_selection_label()} | Spectrum preview is cached-stale | Press Calculate spectrum"
                )
            if not sensorgram_hit:
                self._analysis_controller.mark_stale(
                    f"{self._analysis_metric_label()} sensorgram is out of date | Press Calculate all frames"
                )

    def _update_selection_dependent_plots(self, *, force: bool = False, prompt_live_preview: bool = False) -> None:
        selected_signature = tuple(sorted(int(spot_id) for spot_id in self._selected_spot_ids))
        if not force and selected_signature == self._selection_plot_highlight_signature:
            return
        self._selection_plot_highlight_signature = selected_signature
        self._refresh_visible_spectrum_from_cache()
        self._analysis_controller.update_selection_highlight(force=force)
        if prompt_live_preview and not force and self._analysis_live_preview_enabled:
            self._handle_live_preview_selection_change()

    def _on_mask_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._mask_alpha = self._alpha01(float(value) / 100.0)
        self._update_histogram_region_styles()
        self._update_ignore_mask_overlay()
        self._update_histogram_region_labels()
        self._restyle_area_histogram_curves()
        self._save_visual_preferences()

    def _on_spot_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._spot_alpha = self._alpha01(float(value) / 100.0)
        self._update_spot_overlays()
        self._restyle_area_histogram_curves()
        self._save_visual_preferences()

    def _on_ring_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._ring_alpha = self._alpha01(float(value) / 100.0)
        self._update_spot_overlays()
        self._restyle_area_histogram_curves()
        self._save_visual_preferences()

    def _on_highlight_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._highlight_alpha = self._alpha01(float(value) / 100.0)
        self._update_histogram_region_styles()
        self._update_selected_intensity_overlay()
        self._update_histogram_region_labels()
        self._save_visual_preferences()

    def _default_ignored_intensity_range(self, image: np.ndarray) -> tuple[float, float]:
        values = image.astype(np.float32, copy=False).ravel()
        if values.size == 0:
            return self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MIN_INTENSITY
        lower = float(np.quantile(values, 0.01))
        upper = float(np.quantile(values, 0.05))
        lower = float(np.clip(lower, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY))
        upper = float(np.clip(upper, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY))
        if lower > upper:
            lower, upper = upper, lower
        return lower, upper

    def _mask_changes_affect_preprocessing(self) -> bool:
        return bool(
            self._state.preprocessing.flatten_background_enabled
            and self._state.preprocessing.flatten_background_exclude_mask
        )

    def _mask_change_status_suffix(self) -> str:
        if self._mask_changes_affect_preprocessing():
            return " Background removal will use it on the next image refresh."
        return ""

    def _session_mask_payload(self) -> dict | None:
        if self._current_file_mask is None or self._current_record_path is None:
            return None
        source_record = self._reference_record_for_record_path(self._current_record_path)
        mask_record_path = source_record.path if source_record is not None else self._current_record_path
        return {
            "record_path": str(mask_record_path),
            "mask": self._current_file_mask.copy(),
        }

    def _manual_mask_required(self, *, create_if_missing: bool) -> np.ndarray | None:
        if self._current_processed_image is None:
            self._set_status_text("Load an image first to edit the mask.")
            return None
        if not self.ignore_marked_check.isChecked():
            self.ignore_marked_check.setChecked(True)
        if self._current_file_mask is None and create_if_missing:
            default_path = self._current_mask_file_path()
            raw_shape = (
                load_image_shape(str(self._current_record_path))
                if self._current_record_path is not None
                else self._current_processed_image.shape[:2]
            )
            blank_mask = np.zeros(raw_shape, dtype=bool)
            self._set_current_file_mask(blank_mask, default_path, refresh_preview=False)
        if self._current_file_mask is None:
            self._set_status_text("Load a file mask or start drawing with Pencil first.")
            return None
        return self._current_file_mask

    def _mask_structure(self, radius_px: int) -> np.ndarray:
        radius = max(int(radius_px), 1)
        yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
        return (xx * xx + yy * yy) <= radius * radius

    def _apply_mask_brush(self, point: tuple[float, float]) -> None:
        mask = self._manual_mask_required(create_if_missing=True)
        if mask is None or self._current_processed_image is None:
            return
        brush_radius = max(float(self.mask_brush_size_spin.value()) / 2.0, 0.5)
        center_x = float(point[0])
        center_y = float(point[1])
        height, width = mask.shape
        x_min = max(int(np.floor(center_x - brush_radius)), 0)
        x_max = min(int(np.ceil(center_x + brush_radius)) + 1, width)
        y_min = max(int(np.floor(center_y - brush_radius)), 0)
        y_max = min(int(np.ceil(center_y + brush_radius)) + 1, height)
        if x_min >= x_max or y_min >= y_max:
            return
        yy, xx = np.ogrid[y_min:y_max, x_min:x_max]
        brush_mask = (xx - center_x) ** 2 + (yy - center_y) ** 2 <= brush_radius**2
        coord_maps = self._processed_to_raw_maps()
        if coord_maps is None:
            return
        x_map, y_map = coord_maps
        raw_x = np.rint(x_map[y_min:y_max, x_min:x_max][brush_mask]).astype(np.int32, copy=False)
        raw_y = np.rint(y_map[y_min:y_max, x_min:x_max][brush_mask]).astype(np.int32, copy=False)
        valid = (
            (raw_x >= 0)
            & (raw_y >= 0)
            & (raw_x < mask.shape[1])
            & (raw_y < mask.shape[0])
        )
        if not np.any(valid):
            return
        draw_mode = str(self.mask_draw_mode_combo.currentData() or "add")
        if draw_mode == "erase":
            mask[raw_y[valid], raw_x[valid]] = False
        else:
            mask[raw_y[valid], raw_x[valid]] = True
        self._append_workflow_log_throttled(
            "mask_brush",
            f"Mask brush | mode={draw_mode} | points={int(np.count_nonzero(valid))} | point=({center_x:.1f},{center_y:.1f})",
            level="debug",
            min_interval=0.5,
        )
        self._invalidate_image_analysis_caches()
        self._update_ignore_mask_overlay()

    def _finalize_mask_edit(self) -> None:
        if self._current_file_mask is None:
            return
        self._external_mask_revision += 1
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_ignore_mask_overlay()
        self._schedule_histogram_refresh()
        if self._mask_section_applied():
            self._current_image_key = None
            self._schedule_image_refresh()
        self._commit_prepared_undo_snapshot()
        self._append_workflow_log(
            f"Mask finalize | record={self._current_record_path} | shape={self._current_file_mask.shape}",
            level="debug",
        )
        self._save_processing_state_for_dataset()
        self._set_status_text(f"Mask updated. Save if you want to keep it on disk.{self._mask_change_status_suffix()}")

    def _current_mask_file_path(self) -> Path | None:
        if self._current_record_path is None:
            return None
        reference_record = self._reference_record_for_record_path(self._current_record_path)
        if reference_record is not None:
            return reference_record.path.with_name(f"{reference_record.path.stem}_mask.png")
        return self._current_record_path.with_name(f"{self._current_record_path.stem}_mask.png")

    def _mask_file_path_for_record(self, record_path: Path) -> Path:
        return record_path.with_name(f"{record_path.stem}_mask.png")

    def _current_background_file_path(self) -> Path | None:
        if self._current_record_path is None:
            return None
        reference_record = self._reference_record_for_record_path(self._current_record_path)
        if reference_record is not None:
            return reference_record.path.with_name(f"{reference_record.path.stem}_background.png")
        return self._current_record_path.with_name(f"{self._current_record_path.stem}_background.png")

    def _background_file_path_for_record(self, record_path: Path) -> Path:
        return record_path.with_name(f"{record_path.stem}_background.png")

    def _image_key_for_record_path(self, record_path: Path) -> tuple[int, float] | None:
        return self._record_key_by_path.get(record_path)

    def _processed_shape_for_record(self, record_path: Path) -> tuple[int, int]:
        cache_key = (str(record_path), self._raw_preprocessing_signature())
        cached = self._processed_shape_cache.get(cache_key)
        if cached is not None:
            return cached
        raw_shape = load_image_shape(str(record_path))
        processed_shape = spatial_output_shape(raw_shape, self._state.preprocessing)
        self._processed_shape_cache[cache_key] = processed_shape
        return processed_shape

    def _current_raw_external_mask(self) -> np.ndarray | None:
        return self._current_file_mask

    def _current_external_mask(self) -> np.ndarray | None:
        if self._current_record_path is None:
            return None
        processed_mask, _processed = self._effective_external_mask_for_record(self._current_record_path, processed_space=True)
        return processed_mask

    def _effective_external_mask_for_record(
        self,
        record_path: Path,
        *,
        processed_space: bool = False,
    ) -> tuple[np.ndarray | None, bool]:
        if not self._mask_section_applied():
            return None, False
        return self._external_mask_for_record(record_path, processed_space=processed_space)

    def _external_mask_for_record(self, record_path: Path, *, processed_space: bool = False) -> tuple[np.ndarray | None, bool]:
        source_record_path = record_path
        reference_record = self._reference_record_for_record_path(record_path)
        if reference_record is not None:
            source_record_path = reference_record.path
        expected_mask_path = self._mask_file_path_for_record(source_record_path)
        if self._current_file_mask is not None and (
            self._current_file_mask_path == expected_mask_path
            or (
                self._current_file_mask_path is None
                and self._current_mask_file_path() == expected_mask_path
            )
        ):
            source_mask = self._current_file_mask
        else:
            if not expected_mask_path.exists():
                return None, False
            try:
                source_mask = self._read_mask_image(expected_mask_path, load_image_shape(str(source_record_path)))
            except Exception:
                return None, False

        if not processed_space:
            return source_mask, False

        if source_mask is None:
            return None, False

        # Keep the image pixels untouched here. Chromatic correction stays in the
        # analysis geometry path; masks remain in the spatially preprocessed space.
        return apply_spatial_mask(source_mask, self._state.preprocessing), True

    def _external_mask_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...] | None:
        if self._current_file_mask is None and self._current_record_path is None:
            return None
        target_key = image_key if image_key is not None else self._current_image_key
        return (
            self._external_mask_revision,
            str(self._current_file_mask_path) if self._current_file_mask_path is not None else None,
            None if self._current_file_mask is None else self._current_file_mask.shape,
            self._raw_preprocessing_signature(),
            self._chromatic_signature_for_image_key(target_key),
        )

    def _processed_to_raw_maps(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self._current_record_path is None or self._current_processed_image is None:
            return None
        raw_image = load_image_array(str(self._current_record_path))
        signature = (
            str(self._current_record_path),
            raw_image.shape[:2],
            self._raw_preprocessing_signature(),
        )
        if (
            self._processed_to_raw_map_signature == signature
            and self._processed_to_raw_x_map is not None
            and self._processed_to_raw_y_map is not None
        ):
            return self._processed_to_raw_x_map, self._processed_to_raw_y_map
        x_map, y_map = spatial_coordinate_maps(raw_image.shape[:2], self._state.preprocessing)
        self._processed_to_raw_map_signature = signature
        self._processed_to_raw_x_map = x_map
        self._processed_to_raw_y_map = y_map
        return x_map, y_map

    def _set_current_file_mask(
        self,
        mask: np.ndarray | None,
        path: Path | None,
        *,
        refresh_preview: bool,
    ) -> bool:
        normalized = None if mask is None else np.asarray(mask, dtype=bool)
        normalized_path = path
        if normalized is not None and normalized_path is None:
            normalized_path = self._current_mask_file_path()
        same_path = self._current_file_mask_path == normalized_path
        same_mask = (
            (self._current_file_mask is None and normalized is None)
            or (
                self._current_file_mask is not None
                and normalized is not None
                and self._current_file_mask.shape == normalized.shape
                and np.array_equal(self._current_file_mask, normalized)
            )
        )
        if same_path and same_mask:
            self._append_workflow_log_throttled(
                "current_file_mask_unchanged",
                f"Current file mask unchanged | path={normalized_path} | shape={None if normalized is None else normalized.shape}",
                level="debug",
                min_interval=1.0,
            )
            return False

        self._current_file_mask = None if normalized is None else normalized.copy()
        self._current_file_mask_path = normalized_path
        self._current_file_mask_session_source_path = None
        self._external_mask_revision += 1
        self._append_workflow_log_throttled(
            "current_file_mask_set",
            f"Current file mask set | path={normalized_path} | shape={None if normalized is None else normalized.shape} | rev={self._external_mask_revision}",
            level="debug",
            min_interval=0.5,
        )
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_mask_file_button_state()

        if refresh_preview and self._current_processed_image is not None:
            self._update_ignore_mask_overlay()
            self._schedule_histogram_refresh()
            if self._mask_section_applied():
                self._current_image_key = None
                self._schedule_image_refresh()
        return True

    def _read_mask_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        with Image.open(path) as image:
            mask = np.array(image.convert("L"), dtype=np.uint8)
        if mask.shape != expected_shape:
            raise ValueError(
                f"Mask size {mask.shape[1]} x {mask.shape[0]} px does not match the current image "
                f"{expected_shape[1]} x {expected_shape[0]} px."
            )
        return mask >= 128

    def _auto_load_mask_for_current_record(self) -> None:
        if self._current_record_path is None:
            self._set_current_file_mask(None, None, refresh_preview=False)
            return
        source_record = self._reference_record_for_record_path(self._current_record_path)
        mask_record_path = source_record.path if source_record is not None else self._current_record_path
        default_path = self._mask_file_path_for_record(mask_record_path)
        expected_shape = load_image_shape(str(mask_record_path))
        if self._current_file_mask is not None and self._current_file_mask_session_source_path is not None:
            if self._current_file_mask.shape == expected_shape:
                if self._current_file_mask_session_source_path == mask_record_path:
                    return
                if source_record is not None and self._current_file_mask_session_source_path == source_record.path:
                    return
            self._set_current_file_mask(None, None, refresh_preview=False)
            self._current_file_mask_session_source_path = None
            if default_path is None or not default_path.exists():
                return
        if (
            self._current_file_mask is not None
            and self._current_file_mask.shape == expected_shape
            and (
                self._current_file_mask_path == default_path
                or (
                    self._current_file_mask_path is None
                    and self._current_mask_file_path() == default_path
                )
            )
        ):
            # Keep unsaved in-memory edits for the current image/reference slot.
            return
        if default_path is None or not default_path.exists():
            self._set_current_file_mask(None, None, refresh_preview=False)
            return
        try:
            mask = self._read_mask_image(default_path, expected_shape)
        except Exception as exc:
            self._set_current_file_mask(None, None, refresh_preview=False)
            self._set_status_text(f"Skipped {default_path.name}: {exc}")
            return
        self._set_current_file_mask(mask, default_path, refresh_preview=False)

    def _load_mask_from_file(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to load a mask file.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to load the reference mask.")
            return
        default_path = self._current_mask_file_path()
        start_path = str(default_path if default_path is not None else Path(self.folder_edit.text()))
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Load mask image",
            start_path,
            "Mask images (*.png *.bmp *.tif *.tiff);;All files (*)",
        )
        if not source:
            return
        source_path = Path(source)
        try:
            target_record_path = self._reference_record().path if self._reference_record() is not None else self._current_record_path
            mask = self._read_mask_image(source_path, load_image_shape(str(target_record_path)))
        except Exception as exc:
            QMessageBox.critical(self, "Load mask failed", str(exc))
            self._set_status_text(f"Load mask failed: {exc}")
            return
        self._clear_mask_preview_overlays(clear_toggles=True)
        self._set_current_file_mask(mask, self._current_mask_file_path(), refresh_preview=True)
        self._set_status_text(f"Loaded mask from {source_path.name}.{self._mask_change_status_suffix()}")

    def _save_mask_to_file(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to save a mask.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to save the reference mask.")
            return
        destination = self._current_mask_file_path()
        if destination is None:
            self._set_status_text("No current image is available for mask export.")
            return
        if self._current_file_mask is not None:
            mask = self._current_file_mask.astype(bool, copy=False)
        else:
            raw_image = load_image_array(str(self._current_record_path)).astype(np.float32, copy=False)
            export_settings = deepcopy(self._state.spot_detection)
            export_settings.ignore_marked_pixels = True
            mask = ignored_pixel_mask(raw_image, export_settings, external_mask=None)
        try:
            Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L").save(destination)
        except Exception as exc:
            QMessageBox.critical(self, "Save mask failed", str(exc))
            self._set_status_text(f"Save mask failed: {exc}")
            return
        self._set_current_file_mask(mask, destination, refresh_preview=True)
        self._set_status_text(f"Saved mask to {destination.name}.{self._mask_change_status_suffix()}")

    def _clear_mask_preview_overlays(self, *, clear_toggles: bool = False) -> None:
        self._mask_controller.clear_preview_overlays(clear_toggles=clear_toggles)

    def _set_mask_preview_button_icon(self, button: QToolButton, shown: bool) -> None:
        icon_name = "eye" if shown else "eye-closed"
        tooltip = "Hide preview." if shown else "Show preview."
        button.setIcon(self._mask_panel_icon(icon_name, color="#38bdf8", size=20))
        button.setToolTip(tooltip)

    def _on_mask_preview_toggled(self, preview_kind: str, checked: bool) -> None:
        self._mask_controller.on_preview_toggled(preview_kind, checked)

    def _set_mask_morphology_operation(self, operation: str) -> None:
        self._mask_controller.set_morphology_operation(operation, True)

    def _current_histogram_highlight_mask_raw(self) -> np.ndarray | None:
        return self._mask_controller.current_histogram_highlight_mask_raw()

    def _candidate_mask_for_tool(self, tool_key: str) -> np.ndarray | None:
        return self._mask_controller.candidate_mask_for_tool(tool_key)

    def _current_mask_canvas(self) -> tuple[np.ndarray, Path | None] | None:
        if self._current_record_path is None:
            return None
        raw_shape = load_image_shape(str(self._current_record_path))
        if self._current_file_mask is not None and self._current_file_mask.shape == raw_shape:
            return self._current_file_mask.copy(), self._current_file_mask_path
        return np.zeros(raw_shape, dtype=bool), self._current_file_mask_path

    def _apply_mask_delta(self, tool_key: str, *, subtract: bool) -> None:
        self._mask_controller.apply_mask_delta(tool_key, subtract=subtract)

    def _refresh_mask_previews(self, *_args) -> None:
        self._mask_controller.refresh_previews()

    def _ensure_mask_section_applied(self) -> None:
        if not self._mask_section_applied():
            self.mask_section.set_applied(True)

    def _refresh_after_mask_change(self, status: str) -> None:
        self._mask_state_revision += 1
        self._invalidate_image_analysis_caches()
        self._invalidate_background_profile_cache()
        self._update_ignore_mask_overlay()
        self._schedule_histogram_refresh()
        if self._mask_section_applied():
            self._current_image_key = None
            self._schedule_image_refresh()
        self._set_status_text(status)

    def _apply_histogram_mask(self) -> None:
        self._mask_controller.apply_histogram_mask()

    def _reset_histogram_mask(self) -> None:
        self._mask_controller.reset_histogram_mask()

    def _apply_figure_mask(self) -> None:
        """Apply figure-based mask using spatial algorithms."""
        if self._current_record_path is None:
            self._set_status_text("Load an image first to apply figure mask.")
            return
        
        # Get raw image for figure analysis
        raw_image = load_image_array(str(self._current_record_path)).astype(np.float32, copy=False)
        
        # Get figure mask settings
        algorithm = self.figure_algorithm_combo.currentText().lower()
        sigma = self.figure_sigma_spin.value()
        threshold = self.figure_threshold_spin.value()
        
        # Create figure mask
        from ..processing.preprocess import create_figure_mask
        mask = create_figure_mask(raw_image, algorithm, sigma, threshold)
        
        # Update the mask state
        self._state.mask.figure_mask = mask
        self._state.mask.figure_enabled = True
        self._refresh_after_mask_change("Figure mask applied.")

    def _reset_figure_mask(self) -> None:
        """Reset figure mask."""
        self._state.mask.figure_enabled = False
        self._state.mask.figure_mask = None
        self._refresh_after_mask_change("Figure mask reset.")

    def _create_new_mask(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to create a new mask.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to edit the reference mask.")
            return
        self._ensure_mask_section_applied()
        
        raw_shape = load_image_shape(str(self._current_record_path))
        new_mask = np.zeros(raw_shape, dtype=bool)
        self._clear_mask_preview_overlays(clear_toggles=True)
        self._set_current_file_mask(new_mask, self._current_mask_file_path(), refresh_preview=True)
        self._set_status_text("Started a new blank mask.")

    def _create_new_background(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to create a background image.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to create the reference background.")
            return
        if self._current_processed_image is None:
            self._set_status_text("Load an image first to create a background image.")
            return
        background = self._calculate_background_profile_image()
        if background is None:
            self._set_status_text("Background image is not available yet.")
            return
        self._background_profile_cache_signature = self._background_profile_signature()
        self._background_profile_cache_image = background
        if self._showing_background_profile_main:
            self._apply_main_image_content()
        self._set_status_text("Created a new background image from the current parameters.")

    def _load_background_from_file(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to load a background image.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to load the reference background.")
            return
        default_path = self._current_background_file_path()
        start_path = str(default_path if default_path is not None else Path(self.folder_edit.text()))
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Load background image",
            start_path,
            "Background images (*.png *.bmp *.tif *.tiff);;All files (*)",
        )
        if not source:
            return
        source_path = Path(source)
        try:
            target_record_path = self._reference_record().path if self._reference_record() is not None else self._current_record_path
            expected_shape = self._current_processed_image.shape[:2] if self._current_processed_image is not None else load_image_shape(str(target_record_path))
            background = self._read_background_image(source_path, expected_shape)
        except Exception as exc:
            QMessageBox.critical(self, "Load background failed", str(exc))
            self._set_status_text(f"Load background failed: {exc}")
            return
        self._background_profile_cache_signature = self._background_profile_signature()
        self._background_profile_cache_image = background
        if self._showing_background_profile_main:
            self._apply_main_image_content()
        self._set_status_text(f"Loaded background from {source_path.name}.")

    def _save_background_to_file(self) -> None:
        if self._current_record_path is None:
            self._set_status_text("Load an image first to save a background image.")
            return
        if self._state.preprocessing.chromatic_correction_enabled and not self._is_current_reference_image():
            self._set_status_text("Switch to the reference image to save the reference background.")
            return
        destination = self._current_background_file_path()
        if destination is None:
            self._set_status_text("No current image is available for background export.")
            return
        background = self._background_profile_cache_image
        if background is None or self._background_profile_cache_signature != self._background_profile_signature():
            background = self._calculate_background_profile_image()
        if background is None:
            self._set_status_text("Background image is not available yet.")
            return
        try:
            background_u16 = np.clip(np.rint(background), 0, 65535).astype(np.uint16, copy=False)
            Image.fromarray(background_u16, mode="I;16").save(destination)
        except Exception as exc:
            QMessageBox.critical(self, "Save background failed", str(exc))
            self._set_status_text(f"Save background failed: {exc}")
            return
        self._background_profile_cache_signature = self._background_profile_signature()
        self._background_profile_cache_image = background
        if self._showing_background_profile_main:
            self._apply_main_image_content()
        self._set_status_text(f"Saved background to {destination.name}.")

    def _read_background_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        with Image.open(path) as image:
            background = np.array(image.convert("I"), dtype=np.float32)
        if background.shape != expected_shape:
            raise ValueError(
                f"Background size {background.shape[1]} x {background.shape[0]} px does not match the current image "
                f"{expected_shape[1]} x {expected_shape[0]} px."
            )
        return background

    def _apply_relative_mask(self) -> None:
        self._mask_controller.apply_relative_mask()

    def _reset_relative_mask(self) -> None:
        self._mask_controller.reset_relative_mask()

    def _apply_local_contrast_mask(self) -> None:
        self._mask_controller.apply_local_contrast_mask()

    def _reset_local_contrast_mask(self) -> None:
        self._mask_controller.reset_local_contrast_mask()

    def _apply_morphology_mask(self) -> None:
        self._mask_controller.apply_morphology_mask()

    def _reset_morphology_mask(self) -> None:
        self._mask_controller.reset_morphology_mask()

    def _update_mask_file_button_state(self) -> None:
        has_image = self._current_processed_image is not None
        editable_mask = has_image and (self._is_current_reference_image() or not self._state.preprocessing.chromatic_correction_enabled)
        self.mask_create_new_button.setEnabled(editable_mask)
        self.mask_load_from_file_button.setEnabled(editable_mask)
        self.mask_save_button.setEnabled(editable_mask)
        self.histogram_mask_apply_button.setEnabled(editable_mask)
        self.histogram_mask_reset_button.setEnabled(editable_mask)
        self.relative_mask_apply_button.setEnabled(editable_mask)
        self.relative_mask_reset_button.setEnabled(editable_mask)
        self.relative_mask_show_button.setEnabled(has_image)
        self.local_contrast_mask_apply_button.setEnabled(editable_mask)
        self.local_contrast_mask_reset_button.setEnabled(editable_mask)
        self.local_contrast_mask_show_button.setEnabled(has_image)
        self.morphology_mask_apply_button.setEnabled(editable_mask)
        self.morphology_mask_reset_button.setEnabled(editable_mask)
        self.morphology_mask_show_button.setEnabled(has_image)
        self.mask_morphology_radius_spin.setEnabled(has_image)
        self.mask_morphology_erode_button.setEnabled(editable_mask)
        self.mask_morphology_dilate_button.setEnabled(editable_mask)
        self.mask_morphology_open_button.setEnabled(editable_mask)
        self.mask_morphology_close_button.setEnabled(editable_mask)
        self.background_create_new_button.setEnabled(editable_mask)
        self.background_load_from_file_button.setEnabled(editable_mask)
        self.background_save_button.setEnabled(editable_mask)
        self.mask_morph_radius_spin.setEnabled(has_image)
        self.mask_pencil_check.setEnabled(editable_mask)
        self.mask_draw_mode_combo.setEnabled(editable_mask)
        self.mask_draw_add_button.setEnabled(editable_mask and self.mask_pencil_check.isChecked())
        self.mask_draw_remove_button.setEnabled(editable_mask and self.mask_pencil_check.isChecked())
        self.mask_brush_size_spin.setEnabled(editable_mask)
        default_path = self._current_mask_file_path()
        default_name = default_path.name if default_path is not None else "current_image_mask.png"
        if self._current_file_mask_path is not None:
            current_source = self._current_file_mask_path.name
        elif self._current_file_mask_session_source_path is not None:
            current_source = "session mask"
        else:
            current_source = default_name
        background_default_path = self._current_background_file_path()
        background_default_name = background_default_path.name if background_default_path is not None else "current_image_background.png"
        self.mask_load_from_file_button.setToolTip(
            f"Load a black-and-white mask image for the current image.\nSuggested file name: {default_name}"
        )
        self.mask_save_button.setToolTip(
            f"Save the current mask as a black-and-white image.\nTarget file name: {default_name}\nCurrent source: {current_source}"
        )
        self.background_create_new_button.setToolTip(
            f"Create a new background image from the current parameters.\nTarget file name: {background_default_name}"
        )
        self.background_load_from_file_button.setToolTip(
            f"Load a background image for the current image.\nSuggested file name: {background_default_name}"
        )
        self.background_save_button.setToolTip(
            f"Save the current background image.\nTarget file name: {background_default_name}"
        )

    def _on_spot_diameter_spin_changed(self, value: int) -> None:
        diameter_px = max(self._length_display_to_px(float(value)), 2.0)
        self._append_workflow_log_throttled(
            "spot_diameter_change",
            f"Spot diameter changed | display={value} | px={diameter_px:.2f}",
            level="debug",
            min_interval=1.0,
        )
        self._state.spot_detection.spot_radius_px = max(float(diameter_px / 2.0), 1.0)
        self._update_geometry_settings(save=True, recalculate=False)

    def _on_ring_inner_diameter_spin_changed(self, value: int) -> None:
        _inner = max(float(value), 0.0)
        self._append_workflow_log_throttled(
            "ring_inner_change",
            f"Ring inner changed | display={value}",
            level="debug",
            min_interval=1.0,
        )
        self._update_geometry_settings(
            save=True,
            recalculate=False,
            normalize_relation=True,
        )

    def _on_ring_outer_diameter_spin_changed(self, value: int) -> None:
        _outer = max(float(value), 0.0)
        self._append_workflow_log_throttled(
            "ring_outer_change",
            f"Ring outer changed | display={value}",
            level="debug",
            min_interval=1.0,
        )
        self._update_geometry_settings(
            save=True,
            recalculate=False,
            normalize_relation=True,
        )

    def _on_frame_spin_changed(self, value: int) -> None:
        if not self._frame_values:
            return
        closest_index = min(range(len(self._frame_values)), key=lambda idx: abs(self._frame_values[idx] - value))
        self.frame_slider.setValue(closest_index)

    def _step_frame_selection(self, direction: int) -> bool:
        if not self._frame_values:
            return False
        current_index = self.frame_slider.value()
        target_index = min(max(current_index + int(direction), 0), len(self._frame_values) - 1)
        if target_index == current_index:
            return False
        self.frame_slider.setValue(target_index)
        return True

    def _on_wavelength_spin_changed(self, value: float) -> None:
        if not self._wavelength_values:
            return
        closest_index = min(range(len(self._wavelength_values)), key=lambda idx: abs(self._wavelength_values[idx] - value))
        self.wavelength_slider.setValue(closest_index)

    def _step_wavelength_selection(self, direction: int) -> bool:
        if not self._wavelength_values:
            return False
        current_index = self.wavelength_slider.value()
        target_index = min(max(current_index + int(direction), 0), len(self._wavelength_values) - 1)
        if target_index == current_index:
            return False
        target_wavelength = float(self._wavelength_values[target_index])
        current_frame = self._current_frame()
        if current_frame is None:
            return False
        self._set_current_frame_and_wavelength(int(current_frame), target_wavelength)
        return True

    @staticmethod
    def _decimal_places(value: float) -> int:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])

    def _can_display_micrometers(self) -> bool:
        settings = self._state.preprocessing
        return bool(getattr(settings, "calibration_enabled", False)) and float(getattr(settings, "microns_per_pixel_x", 0.0)) > 0.0 and float(getattr(settings, "microns_per_pixel_y", 0.0)) > 0.0

    def _display_uses_micrometers(self) -> bool:
        return str(self._state.preprocessing.display_units or "px") == "um" and self._can_display_micrometers()

    def _microns_per_pixel_scalar(self) -> float:
        settings = self._state.preprocessing
        scale_x = max(float(getattr(settings, "microns_per_pixel_x", 1.0)), 1e-9)
        scale_y = max(float(getattr(settings, "microns_per_pixel_y", scale_x)), 1e-9)
        return 0.5 * (scale_x + scale_y)

    def _length_px_to_display(self, value_px: float) -> float:
        return length_px_to_display(self._display_uses_micrometers(), self._microns_per_pixel_scalar(), value_px)

    def _length_display_to_px(self, displayed_value: float) -> float:
        return length_display_to_px(self._display_uses_micrometers(), self._microns_per_pixel_scalar(), displayed_value)

    def _display_length_suffix(self) -> str:
        return display_length_suffix(self._display_uses_micrometers())

    def _measurement_delta_components_px(self) -> tuple[float, float, float]:
        settings = self._state.preprocessing
        dx = float(getattr(settings, "measurement_anchor2_x_px", 0.0)) - float(getattr(settings, "measurement_anchor1_x_px", 0.0))
        dy = float(getattr(settings, "measurement_anchor2_y_px", 0.0)) - float(getattr(settings, "measurement_anchor1_y_px", 0.0))
        return dx, dy, hypot(dx, dy)

    def _update_display_unit_controls(self) -> None:
        suffix = self._display_length_suffix()
        use_um = self._display_uses_micrometers()
        decimals = 0
        step = 1.0 if use_um else 1.0
        for spinbox in (
            self.spot_diameter_spin,
            self.ring_inner_diameter_spin,
            self.ring_outer_diameter_spin,
            self.array_spacing_spin,
        ):
            spinbox.setSuffix(suffix)
            spinbox.setDecimals(decimals)
            spinbox.setSingleStep(step)
            spinbox.setKeyboardTracking(False)
            spinbox.setAccelerated(True)
        self._refresh_unit_toggle_button()
        self._update_measurement_status_label()
        self._refresh_scale_bar_overlay()

    def _format_length_display_value(self, value_px: float) -> float:
        return format_length_display_value(self._display_uses_micrometers(), self._microns_per_pixel_scalar(), value_px)

    def _circle_area_text(self, diameter_px: float) -> str:
        radius_px = max(float(diameter_px) / 2.0, 0.0)
        if self._display_uses_micrometers():
            radius_um = radius_px * self._microns_per_pixel_scalar()
            return f"{np.pi * radius_um * radius_um:.0f} µm²"
        return f"{np.pi * radius_px * radius_px:.0f} px²"

    def _ring_area_text(self, inner_diameter_px: float, outer_diameter_px: float) -> str:
        inner_radius_px = max(float(inner_diameter_px) / 2.0, 0.0)
        outer_radius_px = max(float(outer_diameter_px) / 2.0, inner_radius_px)
        if self._display_uses_micrometers():
            scale = self._microns_per_pixel_scalar()
            inner_radius_um = inner_radius_px * scale
            outer_radius_um = outer_radius_px * scale
            return f"{np.pi * max(outer_radius_um * outer_radius_um - inner_radius_um * inner_radius_um, 0.0):.0f} µm²"
        return f"{np.pi * max(outer_radius_px * outer_radius_px - inner_radius_px * inner_radius_px, 0.0):.0f} px²"

    def _area_value_text(self, area_px2: float) -> str:
        area_value = float(max(area_px2, 0.0))
        if self._display_uses_micrometers():
            area_value *= self._microns_per_pixel_scalar() ** 2
            return f"{area_value:.0f} \u00b5m\u00b2"
        return f"{area_value:.0f} px\u00b2"

    def _area_delta_text(self, area_px2: float) -> str:
        area_value = float(area_px2)
        if self._display_uses_micrometers():
            area_value *= self._microns_per_pixel_scalar() ** 2
            return f"{area_value:.0f} \u00b5m\u00b2"
        return f"{area_value:.0f} px\u00b2"

    @staticmethod
    def _icon_from_candidates(*candidates: str) -> QIcon:
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                return QIcon(str(path))
        return QIcon()

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
        return MainWindow._svg_icon_from_markup(svg, size=size)

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
        return MainWindow._svg_icon_from_markup(svg, size=size)

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
        return MainWindow._svg_icon_from_markup(svg, size=size)

    @staticmethod
    def _make_help_icon() -> QIcon:
        icon = MainWindow._lucide_icon("circle-help", "#f8fafc", 24, stroke_width=2.2)
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
        icon = MainWindow._tabler_icon(icon_name, "#f8fafc", size=size, stroke_width=2.1)
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

    def _ome_zarr_chunk_candidates(self) -> list[int]:
        return [64, 128, 256, 512]

    def _current_ome_zarr_chunk_size(self) -> int:
        mode = str(self.ome_zarr_chunk_mode_combo.currentData() or "auto")
        return current_ome_zarr_chunk_size(mode, lambda: self._suggest_ome_zarr_chunk_size())

    def _current_ome_zarr_compression_enabled(self) -> bool:
        return current_ome_zarr_compression_enabled(self.ome_zarr_compression_button.isChecked())

    def _suggest_ome_zarr_chunk_size(self, image_shape: tuple[int, int] | None = None) -> int:
        if image_shape is None and self._current_processed_image is not None:
            image_shape = tuple(int(v) for v in self._current_processed_image.shape[:2])
        if image_shape is None and self._current_record_path is not None:
            try:
                image_shape = load_image_shape(str(self._current_record_path))
            except Exception:
                image_shape = None
        if image_shape is None:
            return int(self._settings_int("ome_zarr/chunk_size_px", 256, minimum=64, maximum=512))
        height, width = int(image_shape[0]), int(image_shape[1])
        limit = max(min(height, width), 64)
        candidates = [size for size in self._ome_zarr_chunk_candidates() if size <= limit]
        if not candidates:
            candidates = [64]
        exact = [size for size in candidates if height % size == 0 and width % size == 0]
        if exact:
            return max(exact)
        return min(
            candidates,
            key=lambda size: (
                abs((height % size) / max(float(size), 1.0)) + abs((width % size) / max(float(size), 1.0)),
                abs(float(size) - min(float(height), float(width)) / 4.0),
                -float(size),
            ),
        )

    def _sync_ome_zarr_chunk_controls(self) -> None:
        self._ui_state_manager.sync_ome_zarr_chunk_controls()

    def _on_ome_zarr_chunk_mode_changed(self, _index: int) -> None:
        self._sync_ome_zarr_chunk_controls()
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
        self.ome_zarr_chunk_mode_combo.setEnabled(not running)
        self.ome_zarr_chunk_guide_button.setEnabled(not running)
        self.ome_zarr_compression_button.setEnabled(not running)
        self.dataset_ome_zarr_controls_row.setEnabled(not running)
        self.dataset_ome_zarr_options_row.setEnabled(not running)
        self.dataset_ome_zarr_info_row.setEnabled(not running)
        if running:
            self.dataset_ome_zarr_export_status_label.setText("Progress")
        self._sync_ome_zarr_chunk_controls()

    def _start_ome_zarr_export(self, destination: Path, chunk_size_px: int, *, compression_enabled: bool = True) -> None:
        dataset = self._state.dataset
        if dataset is None:
            self._set_status_text("Load a dataset before exporting Stack to Zarr.")
            return
        if self._ome_zarr_export_running:
            self._set_status_text("Stack to Zarr export is already running.")
            return
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
            cancel_event=self._ome_zarr_export_cancel_event,
            supports_progress=True,
        )
        self._append_workflow_log(
            f"OME-Zarr export start | chunks {chunk_size_px}px | compression {'on' if compression_enabled else 'off'}",
            level="info",
        )
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.progress.connect(
            lambda percent, text, request_id=request_id: self._on_ome_zarr_export_progress(request_id, percent, text)
        )
        worker.signals.result.connect(lambda result, request_id=request_id: self._on_ome_zarr_export_finished(request_id, result))
        worker.signals.error.connect(lambda message, request_id=request_id: self._on_ome_zarr_export_failed(request_id, message))
        self._thread_pool.start(worker)

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
        self.ome_zarr_chunk_mode_combo.setEnabled(True)
        self.ome_zarr_chunk_guide_button.setEnabled(True)
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
        icon = MainWindow._tabler_icon(icon_name, color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        fallback = MainWindow._tabler_icon("database", color, size, stroke_width=2.1)
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
        icon = MainWindow._tabler_icon(icon_name, color, size, stroke_width=2.2)
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
        icon = MainWindow._tabler_icon("folder-search", color, size, stroke_width=2.1)
        if not icon.isNull():
            return icon
        icon = MainWindow._tabler_icon("folder", color, size, stroke_width=2.1)
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
        icon = MainWindow._tabler_icon("rotate-clockwise-2", color, 24, stroke_width=2.1)
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
    def _make_crop_icon(active: bool = False) -> QIcon:
        color = "#38bdf8" if active else "#f8fafc"
        icon = MainWindow._tabler_icon("crop", color, 24, stroke_width=2.1)
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
        icon = MainWindow._tabler_icon("flip-horizontal", color, 24, stroke_width=2.1)
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
        icon = MainWindow._tabler_icon("flip-vertical", color, 24, stroke_width=2.1)
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
        icon = MainWindow._tabler_icon("ruler-measure", color, 24, stroke_width=2.1)
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
        icon = MainWindow._lucide_icon("undo", "#f8fafc", 24, stroke_width=2.25)
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
        icon = MainWindow._lucide_icon("redo", "#f8fafc", 24, stroke_width=2.25)
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
    def _make_spot_edit_icon() -> QIcon:
        icon = MainWindow._lucide_icon("square-pen", "#f8fafc", 24, stroke_width=2.3)
        if not icon.isNull():
            return icon
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f8fafc"), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(4.2, 10.2, 7.2, 7.2))
        painter.setPen(QPen(QColor("#60a5fa"), 2.6, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QLineF(10.2, 16.0, 17.8, 8.4))
        painter.drawLine(QLineF(16.2, 6.8, 19.4, 10.0))
        painter.setPen(QPen(QColor("#f8fafc"), 2.2, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QLineF(9.3, 17.0, 12.8, 16.2))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_spot_list_icon(active: bool = False) -> QIcon:
        color = "#f59e0b" if active else "#f8fafc"
        icon = MainWindow._lucide_icon("table", color, 24, stroke_width=2.3)
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
        icon = MainWindow._tabler_icon("relation-one-to-many", color, 24, stroke_width=2.1)
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
        icon = MainWindow._lucide_icon("plus", "#22c55e", 24, stroke_width=2.8)
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
        icon = MainWindow._tabler_icon("arrows-move", "#38bdf8", 24, stroke_width=2.2)
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
        icon = MainWindow._lucide_icon("trash-2", "#ef4444", 24, stroke_width=2.2)
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
        icon = MainWindow._lucide_icon("group", "#a855f7", 24, stroke_width=2.2)
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
        if not self._frame_values or not self._wavelength_values:
            self._update_reference_controls()
            self._update_reference_summary()
            return
        frame_index, wavelength_index = self._initial_reference_indices()
        self.frame_slider.blockSignals(True)
        self.wavelength_slider.blockSignals(True)
        self.frame_slider.setValue(frame_index)
        self.wavelength_slider.setValue(wavelength_index)
        self.frame_slider.blockSignals(False)
        self.wavelength_slider.blockSignals(False)
        self._update_reference_controls()
        self._update_reference_summary()
