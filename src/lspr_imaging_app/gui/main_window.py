from __future__ import annotations

import json
import csv
import subprocess
import sys
import os
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from math import hypot
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import (
    QByteArray,
    QItemSelectionModel,
    QPoint,
    QRectF,
    QSize,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
)
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeyEvent, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
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
    QGraphicsPathItem,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSlider,
    QStyle,
    QTableWidgetItem,
    QToolButton,
    QRubberBand,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    APP_THEME,
    BLUE_DARK_THEME,
    GRAY_DARK_THEME,
    collapsible_pin_stylesheet,
    collapsible_toggle_stylesheet,
    dark_image_toolbar_stylesheet,
    get_active_theme,
    icon_accent_colors,
    set_active_theme,
    standard_push_button_stylesheet,
    transparent_icon_button_stylesheet,
)
from lspr_imaging_app.gui.roi_table_helpers import (
    roi_table_headers,
)
from lspr_imaging_app.gui.roi_table_controller import RoiTableController
from lspr_imaging_app.gui.mask_controller import MaskController
from lspr_imaging_app.gui.background_profile_controller import BackgroundProfileController
from lspr_imaging_app.gui.chromatic_controller import ChromaticController
from lspr_imaging_app.gui.shortcut_manager import ShortcutManager
from lspr_imaging_app.gui.session_state_manager import SessionStateManager
from lspr_imaging_app.gui.plot_manager import PlotManager
from lspr_imaging_app.gui.ui_state_manager import UIStateManager
from lspr_imaging_app.gui.shortcut_registry import shortcuts_text
from lspr_imaging_app.gui.ui_helpers import (
    alpha01,
    display_length_suffix,
    format_length_display_value,
    length_display_to_px,
    length_px_to_display,
    normalized_odd_count,
    read_bool_setting,
    read_float_setting,
    settings_bool,
    settings_int,
)
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.gui.dataset_controller import DatasetController
from lspr_imaging_app.gui.image_controller import ImageController
from lspr_imaging_app.gui.image_exclusion_controller import ImageExclusionController
from lspr_imaging_app.gui.image_interaction_controller import ImageInteractionController
from lspr_imaging_app.gui.overlay_manager import OverlayManager
from lspr_imaging_app.gui.undo_manager import UndoManager
from lspr_imaging_app.gui.layout_state_controller import LayoutStateController
from lspr_imaging_app.gui.image_tools_controller import ImageToolsController
from lspr_imaging_app.domain.roi_editor_tools import (
    create_rois_from_template,
    create_rois_from_template_grid,
    move_roi_from_template,
    roi_top_left_from_center,
)
from lspr_imaging_app.domain.exclusions import is_excluded
from lspr_imaging_app.processing.reference_selection import bimodal_dip_contrast
from lspr_imaging_app.version import version_string
from lspr_imaging_app.domain.models import (
    AnalysisState,
    AbsorbanceSpectrumResult,
    ChromaticLandmarkObservation,
    ChromaticTransformModel,
    AreaRoi,
    AreaRoiGroup,
    FitResult,
    MaskSettings,
    RoiDefinition,
)
from lspr_imaging_app.io.dataset import (
    dataset_is_ome_zarr,
    load_image_array,
    load_image_shape,
    read_existing_ome_zarr_summary,
)
from lspr_imaging_app.processing.chromatic import (
    transformed_annulus_mask,
    transformed_disk_mask,
)
from lspr_imaging_app.processing.preprocess import (
    apply_preprocessing,
)


from .main_window_icons import MainWindowIcons
from .workflow_log_controller import WorkflowLogController
from .widgets import (
    BusySpinner,
    CollapsibleSection,  # also re-exported: layout_builder.py imports it from here, not from widgets directly
    CompactWedgeSlider,
    PanelContainer,
    ResponsiveDoubleSpinBox,
    ShineProgressBar,
)
from .worker import (
    ChromaticLandmarkAllOverlayBundle,
    FunctionWorker,  # also re-exported: chromatic_controller.py and image_render_manager.py import it from here at runtime
    GuideOverlayBundle,
    LandmarkOverlayBundle,
    MeasurementOverlayBundle,
    ScaleBarOverlayBundle,
    SensorgramComputationResult,
    SensorgramPointResult,
    RoiOverlayBundle,
    UndoSnapshot,
)
# _auto_chromatic_landmarks_task, _estimate_chromatic_models_task, and _process_image_task
# are not called directly in this file - they look unused to linters, but
# chromatic_controller.py and image_render_manager.py import them from
# lspr_imaging_app.gui.main_window (not from analysis_tasks directly) via a runtime
# `from lspr_imaging_app.gui.main_window import ...` to avoid a circular import. Removing
# them here breaks those call sites at runtime, not at import time - a real regression from
# an earlier "unused import" cleanup pass. Do not remove without checking for that pattern.
from .analysis_tasks import (
    _auto_chromatic_landmarks_task,  # noqa: F401 - re-exported, see comment above
    _detect_rois_task,
    _estimate_chromatic_models_task,  # noqa: F401 - re-exported, see comment above
    _process_image_task,  # noqa: F401 - re-exported, see comment above
    _refresh_roi_metrics_task,
    _score_reference_candidates_task,
)
# Plain `pyflakes` (unlike ruff) does not honor `# noqa`, so it still flags the three
# re-exported names above as unused. This no-op reference marks them "used" for that
# hook without changing behavior -- the actual re-export is the import itself.
_ = (_auto_chromatic_landmarks_task, _estimate_chromatic_models_task, _process_image_task)

try:
    import lucide
except Exception:  # pragma: no cover - optional dependency
    lucide = None


class _WheelHorizontalScrollArea(QScrollArea):
    """A QScrollArea whose (vertical) mouse wheel input scrolls it horizontally.

    Used for toolbar-style rows that only ever scroll left/right - a plain
    QScrollArea ignores the wheel here since there is nothing to scroll
    vertically, which feels broken for a horizontal strip of icons.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)


class MainWindow(MainWindowIcons, QMainWindow):
    SETTINGS_ORG = "LSPR"
    SETTINGS_APP = "LSPRImaging"
    HISTOGRAM_MIN_INTENSITY = 0.0
    HISTOGRAM_MAX_INTENSITY = 65535.0
    HISTOGRAM_LOG_Y_FLOOR = 0.1
    PROCESSED_IMAGE_CACHE_SIZE = 6
    MASK_CANDIDATE_CACHE_SIZE = 6
    ABSORBANCE_SPECTRUM_CACHE_SIZE = 48
    ABSORBANCE_SPECTRAL_CUBE_CACHE_SIZE = 48
    ABSORBANCE_ROI_MASK_CACHE_SIZE = 48
    ROI_ABSORBANCE_CACHE_SIZE = 512
    SENSORGRAM_CACHE_SIZE = 48
    SENSORGRAM_SPECTRAL_CUBE_PAYLOAD_CACHE_SIZE = 96
    SENSORGRAM_SPECTRAL_CUBE_RESULT_CACHE_SIZE = 96
    # Quick navigation:
    # - layout and signal wiring: _build_layout, _create_toolbar, _connect_signals
    # - roi list table: _on_roi_list_toggled, _update_roi_table, CSV helpers
    # - image refresh pipeline: _refresh_image, _apply_loaded_image, _update_roi_overlays
    # - persistence/session: layout_state_controller.py, session save/load
    # - analysis: _update_sensorgram_plot, analysis batch helpers

    def __init__(self, default_folder: Path, *, fast_startup: bool = False) -> None:
        super().__init__()
        self._fast_startup = bool(fast_startup)
        self._state = AnalysisState()
        self._roi_id_counter: int = 0
        self._record_map: dict[tuple[int, float], object] = {}
        self._record_key_by_path: dict[Path, tuple[int, float]] = {}
        self._settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._init_controllers()
        self._init_state()
        self._init_widgets(default_folder)
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

    def _init_controllers(self) -> None:
        self._workflow_log_controller = WorkflowLogController(self)
        self._dataset_controller = DatasetController(self)
        self._image_controller = ImageController(self)
        self._roi_table_controller = RoiTableController(self)
        self._mask_controller = MaskController(self)
        self._chromatic_controller = ChromaticController(self)
        self._analysis_controller = AnalysisController(self)
        self._plot_manager = PlotManager(self)
        self._ui_state_manager = UIStateManager(self)
        self._session_state_manager = SessionStateManager(self)
        self._shortcut_manager = ShortcutManager(self)
        self._image_interaction = ImageInteractionController(self)
        self._bg_profile = BackgroundProfileController(self)
        self._overlay_manager = OverlayManager(self)
        self._undo_manager = UndoManager(self)
        self._layout_state_controller = LayoutStateController(self)
        self._image_tools_controller = ImageToolsController(self)
        self._image_exclusion_controller = ImageExclusionController(self)

    def _init_state(self) -> None:
        self._analysis_enabled = self._settings_bool("analysis_section_applied", True)
        self._window_geometry_restored = False
        self._layout_preferences_ready = False
        self._dock_layout_built = False
        self._startup_restore_window_maximized = False
        self._startup_restore_window_fullscreen = False
        self._suspend_layout_save = False
        self._panel_layout_visibility_backup: QByteArray | None = None
        self._current_image_key: tuple[int, float] | None = None
        self._previous_image_key: tuple[int, float] | None = None
        self._spectral_cube_values: list[int] = []
        self._wavelength_values: list[float] = []
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(max(4, min(6, os.cpu_count() or 4)))
        self._current_record_path: Path | None = None
        self._crop_roi: pg.RectROI | None = None
        self._chromatic_grid_roi: pg.RectROI | None = None
        self._chromatic_grid_overlay_item: pg.PlotCurveItem | None = None
        self._suspend_chromatic_grid_sync = False
        self._rectangle_roi: pg.RectROI | None = None
        self._rectangle_stamp_items: list[pg.RectROI] = []
        self._rectangle_stamp_ring_items: dict[str, tuple[pg.PlotCurveItem | None, pg.PlotCurveItem | None]] = {}
        self._selected_rectangle_roi_ids: set[str] = set()
        self._crop_overlay_item: QGraphicsPathItem | None = None
        self._exclusion_overlay_item: QGraphicsPathItem | None = None
        self._active_tool: str | None = None
        self._suspend_crop_sync = False
        self._suspend_rectangle_sync = False
        self._dragging_rectangle_rois = False
        self._rectangle_drag_anchor: tuple[float, float] | None = None
        self._rectangle_drag_original_positions: dict[str, tuple[float, float]] = {}
        self._dragging_crop = False
        self._crop_drag_anchor: tuple[float, float] | None = None
        self._crop_drag_origin: tuple[float, float] | None = None
        self._roi_editor_mode = "circles"
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
        self._roi_overlay_items: dict[int, RoiOverlayBundle] = {}
        self._guide_overlay_items: dict[int, GuideOverlayBundle] = {}
        self._ome_zarr_chunk_overlay_items: list[pg.InfiniteLine] = []
        self._landmark_overlay_items: dict[int, LandmarkOverlayBundle] = {}
        self._chromatic_all_landmark_overlay_items: dict[int, ChromaticLandmarkAllOverlayBundle] = {}
        self._measurement_overlay: MeasurementOverlayBundle | None = None
        self._scale_bar_overlay: ScaleBarOverlayBundle | None = None
        self._roi_list_selection_syncing = False
        self._roi_list_range_anchor_row: int | None = None
        self._roi_table_updating = False
        self._roi_clipboard: dict[str, object] | None = None
        self._selected_roi_ids: set[int] = set()
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
        self._dragging_rois = False
        self._drag_anchor: tuple[float, float] | None = None
        self._drag_original_positions: dict[int, tuple[float, float]] = {}
        self._roi_selection_rubber_band: QRubberBand | None = None
        self._roi_selection_drag_start: tuple[float, float] | None = None
        self._roi_selection_drag_button: Qt.MouseButton | None = None
        self._roi_selection_pressed_id: int | None = None
        self._roi_selection_drag_modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
        self._roi_edit_refresh_pending = False
        self._roi_overlay_refresh_timer = QTimer(self)
        self._roi_overlay_refresh_timer.setSingleShot(True)
        self._roi_overlay_refresh_timer.setInterval(16)
        self._roi_overlay_refresh_timer.timeout.connect(self._refresh_roi_overlays_during_drag)
        self._processed_image_cache: OrderedDict[tuple[object, ...], np.ndarray] = OrderedDict()
        self._mask_candidate_cache: OrderedDict[tuple[object, ...], np.ndarray] = OrderedDict()
        self._mask_candidate_in_flight: set[tuple[object, ...]] = set()
        self._mask_candidate_pending: dict[tuple[object, ...], list] = {}
        self._processed_shape_cache: dict[tuple[object, ...], tuple[int, int]] = {}
        self._reference_contrast_cache: dict[str, float] = {}
        self._reference_prefetch_in_flight: set[int] = set()
        self._ignored_mask_cache_signature: tuple[object, ...] | None = None
        self._ignored_mask_cache_value: np.ndarray | None = None
        self._roi_mask_cache_signature: tuple[object, ...] | None = None
        self._roi_mask_cache_values: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._histogram_source_cache_signature: tuple[object, ...] | None = None
        self._histogram_source_cache_values: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        self._histogram_log_range_guard = False
        self._absorbance_spectrum_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._absorbance_spectral_cube_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._absorbance_roi_mask_cache: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._sensorgram_cache: OrderedDict[tuple[object, ...], SensorgramComputationResult] = OrderedDict()
        self._sensorgram_spectral_cube_payload_cache: OrderedDict[tuple[object, ...], tuple[object, ...]] = OrderedDict()
        self._sensorgram_spectral_cube_result_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._analysis_cache_lock = threading.Lock()
        self._last_absorbance_fit_seconds: float | None = None
        self._last_saved_processing_signature: str | None = None
        self._last_saved_preprocessing_path: Path | None = None
        self._last_saved_profile_path: Path | None = None
        self._processing_profile_write_busy = False
        self._processing_profile_write_pending: tuple[object, ...] | None = None
        self._dataset_load_in_flight = False
        self._active_session_name: str = "Default"
        self._last_saved_roi_table_signature: str | None = None
        self._last_saved_roi_table_path: Path | None = None
        self._image_refresh_running = False
        self._pending_image_refresh_payload: tuple[object, ...] | None = None
        self._latest_image_refresh_signature: tuple[object, ...] | None = None
        self._image_refresh_started_at: float | None = None
        self._roi_metrics_request_id = 0
        self._roi_detection_request_id = 0
        self._background_profile_in_flight: set[tuple[object, ...]] = set()
        self._background_profile_pending: dict[tuple[object, ...], list] = {}
        self._absorbance_spectrum_request_id = 0
        self._sensorgram_request_id = 0
        self._chromatic_registration_request_id = 0
        self._ome_zarr_export_request_id = 0
        self._absorbance_spectrum_running = False
        self._absorbance_spectrum_running_signature: tuple[object, ...] | None = None
        self._pending_absorbance_spectrum_payload: tuple[object, ...] | None = None
        self._absorbance_spectrum_dirty = True
        self._absorbance_spectrum_started_at: float | None = None
        self._roi_absorbance_cache: OrderedDict[tuple[object, ...], AbsorbanceSpectrumResult] = OrderedDict()
        self._analysis_live_preview_enabled = self._settings_bool("analysis/live_preview", False)
        self._histogram_log_y_enabled = self._settings_bool("histogram/log_y", False)
        self._histogram_startup_autoscale_pending = False
        self._histogram_startup_autoscale_attempts = 0
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
            list[AreaRoi],
        ] | None = None
        self._ome_zarr_export_running = False
        self._ome_zarr_export_cancel_event: threading.Event | None = None
        self._ome_zarr_export_thread: threading.Thread | None = None
        self._ome_zarr_export_started_at: float | None = None
        self._ome_zarr_export_destination: Path | None = None
        self._busy_started_at: float | None = None
        self._busy_is_determinate = False
        self._busy_last_percent: int = 0
        self._absorbance_prep_request_id = 0
        self._absorbance_prep_running = False
        self._absorbance_prep_started_at: float | None = None
        self._absorbance_prep_request_signature: tuple[object, ...] | None = None
        self._sensorgram_spectral_cube_indices = np.asarray([], dtype=np.int32)
        self._sensorgram_metric_values = np.asarray([], dtype=np.float64)
        self._sensorgram_metric_signal = np.asarray([], dtype=np.float64)
        self._display_roi_cache_signature: tuple[object, ...] | None = None
        self._display_roi_cache_value: list[AreaRoi] | None = None
        self._selected_source_rois_cache_signature: tuple[object, ...] | None = None
        self._selected_source_rois_cache_value: tuple[AreaRoi, ...] = tuple()
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
        self._roi_overlay_theta = np.linspace(0.0, 2.0 * np.pi, 48)
        self._image_tools_preview_only = False
        self._ome_zarr_chunk_controls_syncing = False
        theme = get_active_theme()
        self._sample_visual_color = QColor(theme.spot_color)
        self._mask_visual_color = QColor(theme.mask_color)
        self._histogram_mask_visual_color = QColor(theme.histogram_mask_color)
        self._figure_mask_visual_color = QColor(theme.figure_mask_color)
        self._reference_visual_color = QColor(theme.ring_color)
        self._highlight_visual_color = QColor(theme.highlight_color)
        self._scale_bar_visual_color = QColor(theme.scale_bar_color)
        self._roi_alpha = 0.8
        self._reference_alpha = 0.22
        self._mask_alpha = 0.5
        self._highlight_alpha = 0.42
        self._rois_visible = True
        self._roi_labels_visible = True
        self._mask_visible = True
        self._reference_visible = True
        self._highlight_visible = True
        self._reference_points_visible = True
        self._chromatic_reference_points_all_visible = False
        self._cached_rois_only_visible = self._settings_bool("layout/cached_rois_only_visible", False)
        self._restore_visual_preferences()
        self._image_refresh_timer = QTimer(self)
        self._image_refresh_timer.setSingleShot(True)
        self._image_refresh_timer.setInterval(45)
        self._image_refresh_timer.timeout.connect(self._refresh_image)
        self._reference_sync_timer = QTimer(self)
        self._reference_sync_timer.setSingleShot(True)
        self._reference_sync_timer.setInterval(45)
        self._reference_sync_timer.timeout.connect(self._run_debounced_auto_reference_sync)
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
        self._roi_state_save_timer = QTimer(self)
        self._roi_state_save_timer.setSingleShot(True)
        self._roi_state_save_timer.setInterval(250)
        self._roi_state_save_timer.timeout.connect(self._save_processing_state_for_dataset)
        self._roi_refresh_timer = QTimer(self)
        self._roi_refresh_timer.setSingleShot(True)
        self._roi_refresh_timer.setInterval(25)
        self._roi_refresh_timer.timeout.connect(self._roi_table_controller.update_table)


    def _init_widgets(self, default_folder: Path) -> None:
        self._init_dataset_widgets(default_folder)
        self._init_chromatic_widgets()
        self._init_status_and_histogram_widgets()
        self._init_roi_and_background_widgets()
        self._init_mask_widgets()
        self._init_analysis_and_view_widgets()

    def _init_dataset_widgets(self, default_folder: Path) -> None:
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
        self.export_ome_zarr_icon_button = self._free_standing_icon_label(
            self._dataset_transfer_icon("export", "#38bdf8"),
            "Stack to Zarr: open export options and write the current dataset to Zarr.",
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
        self.ome_zarr_chunk_spin = QSpinBox(self)
        self.ome_zarr_chunk_spin.setRange(4, 4096)
        self.ome_zarr_chunk_spin.setSingleStep(1)
        self.ome_zarr_chunk_spin.setSuffix(" px")
        self.ome_zarr_chunk_spin.setValue(
            self._settings_int("ome_zarr/chunk_size_px", 64, minimum=4, maximum=4096)
        )
        self.ome_zarr_chunk_spin.setToolTip(
            "Square spatial chunk size for Zarr export. Any value from 4 to 4096 px — does not need to be a power of 2."
        )
        self.ome_zarr_chunk_label = QLabel("Chunk tile", self)
        self.ome_zarr_chunk_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_chunk_label.setToolTip("Square spatial chunk size used when exporting Zarr.")
        self.ome_zarr_chunk_guide_button = self._make_icon_tool_button(
            "grid-4x4",
            "#94a3b8",
            "Guide: show how the current Zarr chunk size would tile the visible image.",
            checkable=True,
            icon=self._ome_zarr_grid_icon(False),
        )
        self.ome_zarr_chunk_guide_button.setChecked(self._settings_bool("ome_zarr/chunk_guide_visible", False))
        self.ome_zarr_chunk_guide_button.toggled.connect(self._on_ome_zarr_chunk_guide_toggled)
        self.ome_zarr_shard_label = QLabel("Shard", self)
        self.ome_zarr_shard_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_shard_label.setToolTip("How many images are packed into a single shard file on disk.")
        self.ome_zarr_shard_mode_combo = QComboBox(self)
        self.ome_zarr_shard_mode_combo.addItem("1 image", "per_image")
        self.ome_zarr_shard_mode_combo.addItem("1 spectral cube", "per_spectral_cube")
        saved_shard_mode = self._settings.value("ome_zarr/shard_mode", "per_image")
        if saved_shard_mode == "per_frame":  # legacy value from before the frame -> spectral cube rename
            saved_shard_mode = "per_spectral_cube"
        idx = self.ome_zarr_shard_mode_combo.findData(saved_shard_mode)
        self.ome_zarr_shard_mode_combo.setCurrentIndex(max(idx, 0))
        self.ome_zarr_shard_mode_combo.setToolTip(
            "1 image: one shard per wavelength × spectral cube — best for viewing single images.\n"
            "1 spectral cube: one shard per spectral cube (all wavelengths together) — best for spectral fitting, fewest files."
        )
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
        self.ome_zarr_skip_excluded_label = QLabel("Skip excluded", self)
        self.ome_zarr_skip_excluded_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_skip_excluded_label.setToolTip("Omit pixel data for excluded images/wavelengths/spectral cubes from the export.")
        self.ome_zarr_skip_excluded_button = self._make_icon_tool_button(
            "alert-triangle",
            "#94a3b8",
            "Skip excluded images: leave excluded planes empty in the exported file instead of writing their pixel data.",
            checkable=True,
        )
        self.ome_zarr_skip_excluded_button.toggled.connect(self._on_ome_zarr_skip_excluded_toggled)
        self.ome_zarr_dtype_label = QLabel("Dtype: uint16", self)
        self.ome_zarr_dtype_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_dtype_label.setToolTip("Zarr export stores 16-bit TIFF data as uint16.")
        self.ome_zarr_pyramid_label = QLabel("Pyramid: off", self)
        self.ome_zarr_pyramid_label.setObjectName("toolbarMiniLabel")
        self.ome_zarr_pyramid_label.setToolTip("Pyramid export is currently disabled.")
        self.dataset_ome_zarr_export_status_label = QLabel("Progress", self)
        self.dataset_ome_zarr_export_status_label.setObjectName("toolbarMiniLabel")
        self.dataset_ome_zarr_export_status_label.setToolTip("Current Zarr export progress.")
        self.dataset_ome_zarr_export_progress_bar = ShineProgressBar(self)
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
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_spin)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_chunk_guide_button)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_shard_label)
        dataset_ome_zarr_options_layout.addWidget(self.ome_zarr_shard_mode_combo)
        dataset_ome_zarr_options_layout.addStretch(1)
        self.dataset_ome_zarr_compression_row = QWidget(self)
        dataset_ome_zarr_compression_layout = QHBoxLayout(self.dataset_ome_zarr_compression_row)
        dataset_ome_zarr_compression_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_compression_layout.setSpacing(4)
        dataset_ome_zarr_compression_layout.addWidget(self.ome_zarr_compression_label)
        dataset_ome_zarr_compression_layout.addWidget(self.ome_zarr_compression_button)
        dataset_ome_zarr_compression_layout.addStretch(1)
        self.dataset_ome_zarr_skip_excluded_row = QWidget(self)
        dataset_ome_zarr_skip_excluded_layout = QHBoxLayout(self.dataset_ome_zarr_skip_excluded_row)
        dataset_ome_zarr_skip_excluded_layout.setContentsMargins(0, 0, 0, 0)
        dataset_ome_zarr_skip_excluded_layout.setSpacing(4)
        dataset_ome_zarr_skip_excluded_layout.addWidget(self.ome_zarr_skip_excluded_label)
        dataset_ome_zarr_skip_excluded_layout.addWidget(self.ome_zarr_skip_excluded_button)
        dataset_ome_zarr_skip_excluded_layout.addStretch(1)
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
        self._build_ome_zarr_export_dialog()
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
        self.reference_auto_button.setToolTip("Auto: Use the best wavelength in the current spectral cube as the reference.")
        self.reference_auto_button.setIcon(self._reference_mode_icon("auto", False))
        self.reference_manual_button = QToolButton(self)
        self.reference_manual_button.setCheckable(True)
        self.reference_manual_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.reference_manual_button.setAutoRaise(True)
        self.reference_manual_button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        self.reference_manual_button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.reference_manual_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.reference_manual_button.setToolTip("Manual: Store the current spectral cube and wavelength as the manual reference.")
        self.reference_manual_button.setIcon(self._reference_mode_icon("manual", False))
        self.reference_mode_button_group = QButtonGroup(self)
        self.reference_mode_button_group.setExclusive(True)
        self.reference_mode_button_group.addButton(self.reference_auto_button)
        self.reference_mode_button_group.addButton(self.reference_manual_button)
        self.reference_spectral_cube_status_label = QLabel("Spectral cube: -", self)
        self.reference_wavelength_status_label = QLabel("Wavelength: -", self)
        self.reference_method_status_label = QLabel("Method: -", self)
        self.reference_mode_combo = QComboBox(self)
        self.reference_mode_combo.addItem("Auto", "auto")
        self.reference_mode_combo.addItem("Manual", "manual")
        self.reference_mode_combo.hide()
        self.set_reference_button = QPushButton("Use current", self)
        self.set_reference_button.hide()
        self.startup_restore_timeout_actions: dict[int, QAction] = {}

    def _build_ome_zarr_export_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Stack to Zarr")
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        for row in (
            self.dataset_ome_zarr_controls_row,
            self.dataset_ome_zarr_options_row,
            self.dataset_ome_zarr_compression_row,
            self.dataset_ome_zarr_skip_excluded_row,
            self.dataset_ome_zarr_info_row,
            self.dataset_ome_zarr_export_progress_row,
        ):
            layout.addWidget(row)
        self.ome_zarr_export_dialog = dialog

    def _open_ome_zarr_export_dialog(self) -> None:
        self.ome_zarr_export_dialog.show()
        self.ome_zarr_export_dialog.raise_()
        self.ome_zarr_export_dialog.activateWindow()

    def _init_chromatic_widgets(self) -> None:
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
        self.chromatic_landmark_kind_combo = QComboBox(self)
        self.chromatic_landmark_kind_combo.addItem("Corners", "corner")
        self.chromatic_landmark_kind_combo.addItem("Spots", "centroid")
        self.chromatic_landmark_kind_combo.addItem("Both", "both")
        default_landmark_kind = str(self._settings.value("chromatic/landmark_kind", "corner") or "corner")
        default_landmark_kind_index = self.chromatic_landmark_kind_combo.findData(default_landmark_kind)
        if default_landmark_kind_index < 0:
            default_landmark_kind_index = 0
        self.chromatic_landmark_kind_combo.setCurrentIndex(default_landmark_kind_index)
        self.chromatic_landmark_kind_combo.setToolTip(
            "Track: what to follow across wavelengths. Corners = small, precise features (can get lost as focus "
            "blur grows at longer wavelengths). Spots = particle centroids (coarser, but far more robust to that "
            "blur). Both = try both and keep whichever stays consistent with the point's own trend."
        )
        self.chromatic_landmark_model_combo = QComboBox(self)
        self.chromatic_landmark_model_combo.addItem("Similarity", "similarity")
        self.chromatic_landmark_model_combo.addItem("Affine", "affine")
        default_landmark_model = str(self._settings.value("chromatic/landmark_model", "similarity") or "similarity")
        default_landmark_model_index = self.chromatic_landmark_model_combo.findData(default_landmark_model)
        if default_landmark_model_index < 0:
            default_landmark_model_index = 0
        self.chromatic_landmark_model_combo.setCurrentIndex(default_landmark_model_index)
        self.chromatic_landmark_model_combo.setToolTip(
            "Model: the transform fitted through the reference points. Similarity = uniform scale + rotation + "
            "shift only (matches how real lateral chromatic aberration behaves; recommended). Affine = also allows "
            "shear/unequal x-y scale, which a small set of noisy points can pick up spuriously and produce a worse "
            "correction near the image center even as corner points look better."
        )
        self.chromatic_start_button = self._free_standing_toggle_icon_label(
            self._make_roi_edit_icon(False),
            False,
            "Edit: enter chromatic reference-point editing mode on the current sampled image.",
            size=24,
            parent=self,
        )
        self.chromatic_start_button.toggled.connect(
            lambda checked: self.chromatic_start_button.setIcon(self._make_roi_edit_icon(bool(checked)))
        )
        self.chromatic_grid_button = self._free_standing_toggle_icon_label(
            self._ome_zarr_grid_icon(False),
            False,
            "Grid area: show and resize the rectangle reference points are searched within, instead of "
            "always using the whole image (helps keep points off rotated/blank image edges).",
            size=24,
            parent=self,
        )
        self.chromatic_grid_button.toggled.connect(
            lambda checked: self.chromatic_grid_button.setIcon(self._ome_zarr_grid_icon(bool(checked)))
        )
        self.chromatic_grid_reset_button = self._free_standing_icon_label(
            self._make_remove_icon(),
            "Reset the chromatic search area back to the automatic full-image area.",
            size=24,
            parent=self,
        )
        self.chromatic_auto_button = QToolButton(self)
        self.chromatic_auto_button.setAutoRaise(True)
        self.chromatic_auto_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.chromatic_auto_button.setFixedSize(28, 28)
        self.chromatic_auto_button.setIconSize(QSize(24, 24))
        self.chromatic_auto_button.setStyleSheet(transparent_icon_button_stylesheet())
        self.chromatic_auto_button.setIcon(self._chromatic_auto_icon(False))
        self.chromatic_auto_button.setToolTip(
            "Automatic ref.points finder: detect the chromatic reference points on the first sampled image and track them across the other sampled wavelengths."
        )
        self.chromatic_reference_points_all_button = self._create_view_toggle_button(
            "reference_points_all",
            self._chromatic_reference_points_all_visible,
            "Show all chromatic reference points across the sampled wavelengths. When chromatic transforms are linked, the points are transformed into the current image space.",
        )
        self.chromatic_reference_points_all_button.setFixedSize(28, 28)
        self.chromatic_reference_points_all_button.setIconSize(QSize(24, 24))
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
        self.chromatic_landmark_export_button = self._free_standing_icon_label(
            self._mask_panel_icon("file-export", color="#22c55e", size=24),
            "Export marked reference points: raw tracked position, reference position, and (once transforms are "
            "estimated) the model's predicted position and residual, one row per point per sampled wavelength -- "
            "for plotting the residual pattern outside the app.",
            size=24,
            parent=self,
        )

    def _init_status_and_histogram_widgets(self) -> None:
        self.roi_summary = QLabel("No ROIs detected.", self)
        self.roi_summary.setWordWrap(True)
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
        self.roi_histogram_curve = self.histogram_plot.plot(name="ROIs")
        self.reference_histogram_curve = self.histogram_plot.plot(name="Reference ROIs")
        self.mask_histogram_curve = self.histogram_plot.plot(name="Mask")
        self.residual_histogram_curve = self.histogram_plot.plot(name="Residual")
        self.residual_fill_item = pg.FillBetweenItem(self.histogram_curve.curve, self.residual_histogram_curve.curve)
        self.residual_fill_item.setZValue(-1)
        self.residual_fill_item.hide()
        self.histogram_plot.addItem(self.residual_fill_item)
        self.hist_region = pg.LinearRegionItem(values=(0.0, 1.0), brush=pg.mkBrush(56, 189, 248, 35))
        self.hist_region.setZValue(10)
        self.hist_region.setToolTip(
            "Histogram highlight range. This also sets the intensity range that 'Auto-locate ROIs' "
            "searches within — if it doesn't cover your sample ROI intensities, detection will find 0 ROIs."
        )
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
        self.roi_histogram_label = pg.TextItem(anchor=(0.5, 1.0))
        self.roi_histogram_label.setZValue(12)
        self.roi_histogram_label.hide()
        self.histogram_plot.addItem(self.roi_histogram_label)
        self.reference_histogram_label = pg.TextItem(anchor=(0.5, 1.0))
        self.reference_histogram_label.setZValue(12)
        self.reference_histogram_label.hide()
        self.histogram_plot.addItem(self.reference_histogram_label)
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

    def _init_roi_and_background_widgets(self) -> None:
        self.sample_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.reference_inner_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.reference_outer_diameter_spin = ResponsiveDoubleSpinBox(self)
        self.rectangle_name_edit = QLineEdit(self)
        self.rectangle_width_spin = ResponsiveDoubleSpinBox(self)
        self.rectangle_height_spin = ResponsiveDoubleSpinBox(self)
        self.rectangle_padding_spin = ResponsiveDoubleSpinBox(self)
        self.rectangle_background_width_spin = ResponsiveDoubleSpinBox(self)
        self.rectangle_summary_label = QLabel("No rectangle ROI yet.", self)
        self._rectangle_template = RoiDefinition(
            roi_id="rectangle_template",
            name="Rectangle ROI",
            shape="rectangle",
            center_x=0.0,
            center_y=0.0,
            size_x=80.0,
            size_y=60.0,
            background_padding_px=10.0,
            background_width_px=12.0,
            enabled=True,
        )
        self.roi_geometry_scope_button = self._make_relation_scope_button(True, "Apply sample diameter to all ROIs when on, or only selected ROIs when off.")
        self.reference_geometry_scope_button = self._make_relation_scope_button(True, "Apply reference diameters to all ROIs when on, or only selected ROIs when off.")
        self.roi_geometry_area_label = QLabel("A_s = -, A_r = -, A_diff = -", self)
        self.flatten_background_check = QPushButton("Apply background removal", self)
        self.flatten_background_check.setCheckable(True)
        self.flatten_background_check.setIcon(self._make_link_toggle_icon(False))
        self.flatten_background_check.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        self.flatten_ignore_roi_area_check = self._make_icon_tool_button(
            "current-location-off",
            "#94a3b8",
            "Ignore the detected ROI area while estimating the illumination background.",
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
        self.flatten_ignore_roi_area_check.setChecked(True)
        self._sync_background_exclusion_buttons()
        self.flatten_ignore_roi_area_check.toggled.connect(
            lambda checked: self.flatten_ignore_roi_area_check.setIcon(
                self._background_exclusion_icon("current-location-off", checked, size=APP_THEME.compact_icon_inner)
            )
        )
        self.flatten_ignore_mask_check.toggled.connect(
            lambda checked: self.flatten_ignore_mask_check.setIcon(
                self._background_exclusion_icon("mask-off", checked, size=APP_THEME.compact_icon_inner)
            )
        )
        self.background_local_reference_check = self._make_icon_tool_button(
            "focus-2",
            "#94a3b8",
            "Local reference-ROI normalization (sample+reference ROI geometry only): use the reference "
            "ROI area as per-ROI background. Enables fast spatial reads for OME-Zarr datasets — "
            "global background flattening is skipped during ROI analysis.",
            checkable=True,
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
        self.flatten_background_exclusion_dilation_spin = QSpinBox(self)
        self.flatten_background_exclusion_dilation_spin.setRange(0, 100)
        self.flatten_background_exclusion_dilation_spin.setSuffix(" px")
        self.flatten_background_exclusion_dilation_spin.setKeyboardTracking(False)
        self.flatten_background_binning_combo = QComboBox(self)
        self.flatten_background_binning_combo.addItem("1x1", 1)
        self.flatten_background_binning_combo.addItem("2x2", 2)
        self.flatten_background_binning_combo.addItem("4x4", 4)
        # Clearer aliases for the background section controls.
        self.background_removal_link = self.flatten_background_check
        self.background_ignore_roi_button = self.flatten_ignore_roi_area_check
        self.background_ignore_mask_button = self.flatten_ignore_mask_check
        self.background_local_reference_button = self.background_local_reference_check
        self.background_smoothing_sigma_spin = self.flatten_background_sigma_spin
        self.background_smoothing_binning_combo = self.flatten_background_binning_combo
        self.background_exclusion_dilation_spin = self.flatten_background_exclusion_dilation_spin

    def _init_mask_widgets(self) -> None:
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

        self.roi_detection_auto_button = self._make_icon_tool_button(
            "grid-dots",
            "#22c55e",
            "Mode A: automatically detect ROIs from the known array grid and spacing.",
        )
        self.roi_corner_select_button = self._make_icon_tool_button(
            "layout-grid",
            "#94a3b8",
            "Mode B: corner-seeded detection (coming later).",
            icon=self._make_corner_seed_icon("#94a3b8"),
        )
        self.roi_corner_select_button.setEnabled(False)

    def _init_analysis_and_view_widgets(self) -> None:
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
            "Live preview: update the spectrum and sensorgram when ROI selection changes.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analysis_calculate_all_button = self._free_standing_icon_label(
            self._make_analysis_all_spectral_cubes_icon(False),
            "Calculate all spectral cubes.",
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
        self.analysis_roi_table_button = self._free_standing_icon_label(
            self._make_roi_list_icon(False),
            "Show or hide the ROI table.",
            size=APP_THEME.compact_icon_inner,
            parent=self,
        )
        self.analysis_roi_table_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.analysis_start_spectral_cube_spin = QSpinBox(self)
        self.analysis_start_spectral_cube_spin.setEnabled(False)
        self.analysis_start_spectral_cube_spin.setKeyboardTracking(False)
        self.analysis_end_spectral_cube_spin = QSpinBox(self)
        self.analysis_end_spectral_cube_spin.setEnabled(False)
        self.analysis_end_spectral_cube_spin.setKeyboardTracking(False)
        self.analysis_formula_label = QLabel("A = log10(I_rROI / I_sROI)", self)
        self.analysis_formula_label.setWordWrap(True)
        self.analysis_summary_label = QLabel("Select ROIs to show absorbance spectrum.", self)
        self.analysis_summary_label.setWordWrap(True)
        self.spectrum_summary_label = QLabel("Select ROIs to show absorbance spectrum.", self)
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
        self.sensorgram_summary_label = QLabel("Calculate all spectral cubes to build the fitted sensorgram.", self)
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
        self.reference_inner_diameter_spin.setRange(0, 1_000_000)
        self.reference_outer_diameter_spin.setRange(0, 1_000_000)
        self.sample_diameter_spin.setDecimals(2)
        self.sample_diameter_spin.setSingleStep(0.5)
        self.reference_inner_diameter_spin.setDecimals(2)
        self.reference_inner_diameter_spin.setSingleStep(0.5)
        self.reference_outer_diameter_spin.setDecimals(2)
        self.reference_outer_diameter_spin.setSingleStep(0.5)
        self.sample_diameter_spin.setKeyboardTracking(False)
        self.reference_inner_diameter_spin.setKeyboardTracking(False)
        self.reference_outer_diameter_spin.setKeyboardTracking(False)
        self.array_spacing_spin.setKeyboardTracking(True)
        self.ignore_marked_check = QPushButton("Apply mask", self)
        self.ignore_marked_check.setCheckable(True)
        self.detect_rois_button = self.roi_detection_auto_button
        self.reorder_rois_button = self._make_icon_tool_button("sort-ascending-numbers", "#f8fafc", "Reorder ROIs by image position so the top-left ROI becomes ID 1.")
        self.clear_rois_button = self._make_icon_tool_button("trash-x", "#ef4444", "Remove all detected ROIs and groups from the current dataset.")
        self.clear_roi_selection_button = QPushButton("Clear selection", self)

        self.spectral_cube_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.spectral_cube_slider.setEnabled(False)
        self.spectral_cube_spin = QSpinBox(self)
        self.spectral_cube_spin.setEnabled(False)
        self.wavelength_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.wavelength_slider.setEnabled(False)
        self.wavelength_spin = QDoubleSpinBox(self)
        self.wavelength_spin.setEnabled(False)
        self.wavelength_spin.setDecimals(2)
        self.wavelength_spin.setSuffix(" nm")
        self.reference_jump_button = self._make_icon_tool_button(
            "star", "#84cc16", "Jump to the reference image."
        )
        self.image_exclusion_button = self._make_icon_tool_button(
            "ban", "#94a3b8", "Exclude this image/wavelength/spectral cube from processing, or manage exclusions."
        )
        self.image_exclusion_button.clicked.connect(self._show_image_exclusion_menu)
        self.spectral_cube_slider.installEventFilter(self)
        self.wavelength_slider.installEventFilter(self)
        self.spectral_cube_spin.installEventFilter(self)
        self.wavelength_spin.installEventFilter(self)
        self.chromatic_landmark_id_spin.installEventFilter(self)
        self.sample_diameter_spin.installEventFilter(self)
        self.reference_inner_diameter_spin.installEventFilter(self)
        self.reference_outer_diameter_spin.installEventFilter(self)
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
        self.roi_editor_labels_button = self._create_label_visibility_button(self._roi_labels_visible)
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

        self.roi_edit_action = QAction(self._make_roi_edit_icon(), "Manual edit", self)
        self.roi_edit_action.setCheckable(True)
        self.roi_edit_action.setToolTip("Left-click or right-click to select ROIs in the active ROI tab. Ctrl-click for group selection. Left-drag in Move mode to correct ROIs.")
        self.roi_edit_action.setShortcut(QKeySequence("Ctrl+E"))
        self.roi_add_action = QAction(self._make_add_icon(), "Add", self)
        self.roi_add_action.setCheckable(True)
        self.roi_add_action.setEnabled(False)
        self.roi_add_action.setToolTip("Left-click in the image to add a new ROI stamp using the active shape template.")
        self.roi_add_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.roi_move_action = QAction(self._make_move_icon(), "Move", self)
        self.roi_move_action.setCheckable(True)
        self.roi_move_action.setEnabled(False)
        self.roi_move_action.setToolTip("Move selected ROIs by dragging or arrow keys while ROI edit mode is active.")
        self.roi_move_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.roi_array_action = QAction(self._tabler_icon("layout-grid"), "Array", self)
        self.roi_array_action.setCheckable(True)
        self.roi_array_action.setEnabled(False)
        self.roi_array_action.setToolTip("Stamp a grid of ROIs using the active ROI tab template.")
        self.remove_rois_action = QAction(self._make_remove_icon(), "Remove", self)
        self.remove_rois_action.setEnabled(False)
        self.remove_rois_action.setToolTip("Remove the selected ROIs and renumber the remaining array.")
        self.remove_rois_action.setShortcut(QKeySequence("Delete"))
        self.group_rois_action = QAction(self._make_group_icon(), "Group", self)
        self.group_rois_action.setEnabled(False)
        self.group_rois_action.setToolTip("Create or update a named group from the current selection.")
        self.group_rois_action.setShortcut(QKeySequence("Ctrl+G"))
        self.ungroup_rois_action = QAction("Ungroup", self)
        self.ungroup_rois_action.setEnabled(False)
        self.ungroup_rois_action.setToolTip("Remove the selected ROIs from their current groups.")
        self.ungroup_rois_action.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.roi_list_action = QAction(self._make_roi_list_icon(), "ROI list", self)
        self.roi_list_action.setCheckable(True)
        self.roi_list_action.setToolTip("Show or hide the ROI table.")
        self.roi_list_action.setShortcut(QKeySequence("Ctrl+L"))

        self.reset_rotation_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Reset rotation", self)
        self.reset_rotation_action.setToolTip("Reset image rotation to 0 degrees.")

        self.rotation_fill_dark_button = self._create_rotation_fill_toggle_button()
        self.rotation_fill_dark_button.setChecked(bool(self._state.preprocessing.rotation_fill_dark))
        self.rotation_fill_dark_button.setIcon(self._make_rotation_fill_icon(self.rotation_fill_dark_button.isChecked()))
        self._update_rotation_fill_button_tooltip()

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
        self.calculate_spectrum_action.setToolTip("Calculate the absorbance spectrum for the current spectral cube and selected ROIs.")

        self.show_rois_check = self._create_view_toggle_button("roi", self._rois_visible, "Show or hide the ROI overlays.")
        self.bottom_roi_labels_button = self._create_label_visibility_button(self._roi_labels_visible)
        self.show_reference_check = self._create_view_toggle_button("reference", self._reference_visible, "Show or hide the reference ROI overlays.")
        self.show_reference_points_check = self._create_view_toggle_button("reference_points", self._reference_points_visible, "Show or hide chromatic reference points.")
        self.show_mask_check = self._create_view_toggle_button("mask", self._mask_visible, "Show or hide the mask overlay.")
        self.show_highlight_check = self._create_view_toggle_button("highlight", self._highlight_visible, "Show or hide the histogram highlight overlay.")
        self.mask_color_button = QToolButton()
        self.mask_color_button.setText("")
        self.mask_color_button.setFixedSize(12, 12)
        self.sample_color_button = QToolButton()
        self.sample_color_button.setText("")
        self.sample_color_button.setFixedSize(12, 12)
        self.reference_color_button = QToolButton()
        self.reference_color_button.setText("")
        self.reference_color_button.setFixedSize(12, 12)
        self.highlight_color_button = QToolButton()
        self.highlight_color_button.setText("")
        self.highlight_color_button.setFixedSize(12, 12)
        self.mask_alpha_slider = CompactWedgeSlider(parent=self)
        self.mask_alpha_slider.setRange(0, 100)
        self.mask_alpha_slider.setValue(int(round(self._mask_alpha * 100.0)))
        self.roi_alpha_slider = CompactWedgeSlider(parent=self)
        self.roi_alpha_slider.setRange(0, 100)
        self.roi_alpha_slider.setValue(int(round(self._roi_alpha * 100.0)))
        self.reference_alpha_slider = CompactWedgeSlider(parent=self)
        self.reference_alpha_slider.setRange(0, 100)
        self.reference_alpha_slider.setValue(int(round(self._reference_alpha * 100.0)))
        self.highlight_alpha_slider = CompactWedgeSlider(parent=self)
        self.highlight_alpha_slider.setRange(0, 100)
        self.highlight_alpha_slider.setValue(int(round(self._highlight_alpha * 100.0)))

        image_tools_row = self._create_toolbar_row(
            [
                self._create_image_tool_icon_button(self.rotate_action, accent="yellow"),
                self._create_image_tool_icon_button(self.reset_rotation_action, accent="yellow"),
                self.rotation_fill_dark_button,
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
                    self._create_view_control("", self.show_rois_check, self.sample_color_button, self.roi_alpha_slider),
                    self._create_toolbar_icon_toggle_control("", self.bottom_roi_labels_button),
                    self._create_view_control("", self.show_reference_check, self.reference_color_button, self.reference_alpha_slider),
                    self._create_toolbar_icon_toggle_control("", self.show_reference_points_check),
                    self._create_view_control("", self.show_mask_check, self.mask_color_button, self.mask_alpha_slider),
                    self._create_view_control("", self.show_highlight_check, self.highlight_color_button, self.highlight_alpha_slider),
                    self.background_profile_hold_button,
                ]),
            ],
        )

        toolbar_layout.addWidget(self.measurement_controls_widget, 0)
        toolbar_layout.addStretch(1)

        bottom_view_content = QWidget(self.bottom_view_toolbar)
        bottom_view_content_layout = QHBoxLayout(bottom_view_content)
        bottom_view_content_layout.setContentsMargins(0, 0, 0, 0)
        bottom_view_content_layout.setSpacing(10)
        bottom_view_content_layout.addWidget(view_section, 0)
        bottom_view_content_layout.addWidget(self.measurement_unit_button, 0)
        bottom_view_content_layout.addWidget(self.scale_bar_color_button, 0)
        bottom_view_content_layout.addWidget(self.scale_bar_toggle_button, 0)
        bottom_view_content_layout.addStretch(1)
        bottom_view_layout.addWidget(self._make_scrollable_icon_row(bottom_view_content), 1)

        self._populate_left_roi_editor_controls()
        self._image_tools_controller.refresh_image_tool_action_icons()
        self._update_display_unit_controls()
        self._create_menu_bar()
        theme_name = str(self._settings.value("ui/theme", "blue"))
        self._set_ui_theme("gray" if theme_name == "gray" else "blue")
        self._update_color_button_styles()

    def _make_scrollable_icon_row(self, content: QWidget) -> QWidget:
        """Wrap a row of fixed-size icon controls so it scrolls horizontally
        instead of overlapping/clipping when the panel is too narrow to fit
        every control. Left/right arrow buttons only appear once the row
        actually overflows."""
        scroll = _WheelHorizontalScrollArea(content.parentWidget())
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        scroll.setWidget(content)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setFixedHeight(content.sizeHint().height())

        left_button = self._make_icon_tool_button("chevron-left", "#f8fafc", "Scroll the row left.")
        right_button = self._make_icon_tool_button("chevron-right", "#f8fafc", "Scroll the row right.")

        bar = scroll.horizontalScrollBar()

        def _scroll_by(direction: int) -> None:
            step = max(scroll.viewport().width() - 20, 40)
            bar.setValue(bar.value() + direction * step)

        left_button.clicked.connect(lambda: _scroll_by(-1))
        right_button.clicked.connect(lambda: _scroll_by(1))

        def _update_arrows(*_args: object) -> None:
            overflowing = bar.maximum() > bar.minimum()
            left_button.setVisible(overflowing)
            right_button.setVisible(overflowing)
            left_button.setEnabled(bar.value() > bar.minimum())
            right_button.setEnabled(bar.value() < bar.maximum())

        bar.rangeChanged.connect(_update_arrows)
        bar.valueChanged.connect(_update_arrows)
        _update_arrows()

        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        # A few px of breathing room on each side: with 0 margin the right arrow
        # sits flush against the dock/splitter boundary and its edge pixels get
        # clipped by the splitter handle, making it look like the button vanished.
        layout.setContentsMargins(2, 0, 4, 0)
        layout.setSpacing(2)
        layout.addWidget(left_button, 0)
        layout.addWidget(scroll, 1)
        layout.addWidget(right_button, 0)
        return wrapper

    def _connect_signals(self) -> None:
        self._connect_dataset_and_nav()
        self._connect_chromatic()
        self._connect_analysis_and_histogram()
        self._connect_background_and_mask()
        self._connect_roi_editor()
        self._connect_toolbar_and_ui()


    def _connect_dataset_and_nav(self) -> None:
        self.browse_button.clicked.connect(self._dataset_controller.browse_folder)
        self.load_button.clicked.connect(self._dataset_controller.browse_folder)
        self.export_ome_zarr_icon_button.clicked.connect(self._open_ome_zarr_export_dialog)
        self.dataset_ome_zarr_export_button.clicked.connect(self._dataset_controller.export_current_dataset_to_ome_zarr)
        self.dataset_ome_zarr_export_stop_button.clicked.connect(self._stop_ome_zarr_export)
        self.ome_zarr_chunk_spin.valueChanged.connect(self._on_ome_zarr_chunk_size_changed)
        self.ome_zarr_shard_mode_combo.currentIndexChanged.connect(self._on_ome_zarr_shard_mode_changed)
        self.export_settings_button.clicked.connect(self._export_processing_profile)
        self.import_settings_button.clicked.connect(self._import_processing_profile)
        self.reference_auto_button.clicked.connect(lambda _checked=False: self._set_reference_mode("auto"))
        self.reference_manual_button.clicked.connect(lambda _checked=False: self._set_current_reference_from_view())
        self.reference_jump_button.clicked.connect(self._go_to_reference_image)
        self.spectral_cube_slider.valueChanged.connect(lambda _value: self._sync_analysis_plot_cursors())
        self.spectral_cube_slider.valueChanged.connect(lambda _value: self._schedule_image_refresh())
        self.spectral_cube_slider.valueChanged.connect(lambda _value: self._schedule_auto_reference_sync())
        self.wavelength_slider.valueChanged.connect(lambda _value: self._sync_analysis_plot_cursors())
        self.wavelength_slider.valueChanged.connect(lambda _value: self._schedule_image_refresh())
        self.spectral_cube_spin.valueChanged.connect(self._on_spectral_cube_spin_changed)
        self.wavelength_spin.valueChanged.connect(self._on_wavelength_spin_changed)
        self.analysis_start_spectral_cube_spin.valueChanged.connect(self._analysis_controller.on_spectral_cube_range_changed)
        self.analysis_end_spectral_cube_spin.valueChanged.connect(self._analysis_controller.on_spectral_cube_range_changed)

    def _connect_chromatic(self) -> None:
        self.chromatic_apply_check.toggled.connect(self._update_chromatic_settings)
        self.chromatic_apply_check.toggled.connect(self.chromatic_section.set_applied)
        self.chromatic_apply_check.toggled.connect(
            lambda checked: self.chromatic_apply_check.setIcon(self._make_link_toggle_icon(bool(checked)))
        )
        self.chromatic_start_button.toggled.connect(self._on_chromatic_landmark_tool_toggled)
        self.chromatic_grid_button.toggled.connect(self._chromatic_controller.on_grid_tool_toggled)
        self.chromatic_grid_reset_button.clicked.connect(self._chromatic_controller.reset_grid_bounds)
        self.chromatic_auto_button.clicked.connect(self._chromatic_controller.auto_detect_landmarks)
        self.chromatic_reference_points_all_button.toggled.connect(self._on_chromatic_reference_points_all_toggled)
        self.chromatic_prev_button.clicked.connect(lambda: self._navigate_chromatic_sample(-1))
        self.chromatic_next_button.clicked.connect(lambda: self._navigate_chromatic_sample(1))
        self.chromatic_landmark_clear_button.clicked.connect(self._clear_chromatic_landmarks)
        self.chromatic_landmark_id_spin.valueChanged.connect(self._on_chromatic_landmark_id_changed)
        self.chromatic_sample_count_spin.valueChanged.connect(self._on_chromatic_sample_count_changed)
        self.chromatic_feature_count_spin.currentIndexChanged.connect(self._on_chromatic_feature_count_changed)
        self.chromatic_subpixel_precision_combo.currentIndexChanged.connect(self._on_chromatic_subpixel_precision_changed)
        self.chromatic_landmark_kind_combo.currentIndexChanged.connect(self._on_chromatic_landmark_kind_changed)
        self.chromatic_landmark_model_combo.currentIndexChanged.connect(self._on_chromatic_landmark_model_changed)
        self.chromatic_transform_button.clicked.connect(self._on_chromatic_transform_button_clicked)
        self.chromatic_landmark_export_button.clicked.connect(self._chromatic_controller.export_landmarks_csv)

    def _connect_analysis_and_histogram(self) -> None:
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
        self.analysis_roi_table_button.clicked.connect(
            lambda: self.roi_list_action.setChecked(not self.roi_list_action.isChecked())
        )
        self.roi_list_cached_button.toggled.connect(self._on_cached_rois_only_toggled)
        self.analysis_poly_order_spin.valueChanged.connect(self._analysis_controller.on_fit_settings_changed)
        self.analysis_metric_combo.currentIndexChanged.connect(self._analysis_controller.on_fit_settings_changed)
        self.calculate_spectrum_action.triggered.connect(self._refresh_absorbance_spectrum)

    def _connect_background_and_mask(self) -> None:
        self.background_removal_link.toggled.connect(self.background_section.set_applied)
        self.background_removal_link.toggled.connect(
            lambda checked: self.background_removal_link.setIcon(self._make_link_toggle_icon(bool(checked)))
        )
        self.ignore_marked_check.toggled.connect(self.mask_section.set_applied)
        self.chromatic_section.apply_changed.connect(self._chromatic_controller.section_applied_changed)
        self.mask_section.apply_changed.connect(self._on_mask_section_applied_changed)
        self.image_tools_section.apply_changed.connect(self._on_image_tools_section_applied_changed)
        self.roi_editor_section.apply_changed.connect(self._on_live_geometry_toggled)
        self.background_section.apply_changed.connect(self._on_background_section_applied_changed)
        self.analysis_section.apply_changed.connect(self._on_analysis_section_applied_changed)
        self.background_removal_link.toggled.connect(self._update_image_processing_settings)
        self.background_smoothing_sigma_spin.valueChanged.connect(self._update_image_processing_settings)
        self.background_smoothing_binning_combo.currentIndexChanged.connect(self._update_image_processing_settings)
        self.background_ignore_roi_button.toggled.connect(self._update_image_processing_settings)
        self.background_ignore_mask_button.toggled.connect(self._update_image_processing_settings)
        self.background_exclusion_dilation_spin.valueChanged.connect(self._update_image_processing_settings)
        self.background_local_reference_check.toggled.connect(self._update_image_processing_settings)
        self.background_create_new_button.clicked.connect(self._create_new_background)
        self.background_load_from_file_button.clicked.connect(self._load_background_from_file)
        self.background_save_button.clicked.connect(self._save_background_to_file)
        self.background_profile_hold_button.toggled.connect(self._on_background_profile_toggled)
        self.background_profile_button.toggled.connect(self._on_background_profile_toggled)
        self.mask_mode_combo.currentIndexChanged.connect(self._update_roi_detection_settings)
        self.mask_mode_combo.currentIndexChanged.connect(self._update_mask_control_state)
        self.mask_profile_sigma_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_relative_threshold_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_local_sigma_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_local_z_spin.valueChanged.connect(self._update_roi_detection_settings)
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
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_relative_threshold_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_morphology_radius_spin.valueChanged.connect(self._refresh_mask_previews)
        self.mask_relative_profile_sigma_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_relative_threshold_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_local_contrast_sigma_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_local_contrast_z_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_morphology_radius_spin.valueChanged.connect(self._save_control_preferences)
        self.mask_draw_mode_combo.currentIndexChanged.connect(self._save_control_preferences)
        self.mask_brush_size_spin.valueChanged.connect(self._save_control_preferences)

        self.ignore_marked_check.toggled.connect(self._update_roi_detection_settings)

    def _connect_roi_editor(self) -> None:
        self.sample_diameter_spin.valueChanged.connect(self._on_roi_diameter_spin_changed)
        self.sample_diameter_spin.editingFinished.connect(self._commit_roi_geometry_edits)
        self.reference_inner_diameter_spin.valueChanged.connect(self._on_reference_inner_diameter_spin_changed)
        self.reference_inner_diameter_spin.editingFinished.connect(self._commit_roi_geometry_edits)
        self.reference_outer_diameter_spin.valueChanged.connect(self._on_reference_outer_diameter_spin_changed)
        self.reference_outer_diameter_spin.editingFinished.connect(self._commit_roi_geometry_edits)
        self.rectangle_name_edit.editingFinished.connect(self._commit_rectangle_roi_edits)
        self.rectangle_width_spin.valueChanged.connect(lambda *_args: self._commit_rectangle_roi_edits())
        self.rectangle_height_spin.valueChanged.connect(lambda *_args: self._commit_rectangle_roi_edits())
        self.rectangle_padding_spin.valueChanged.connect(lambda *_args: self._commit_rectangle_roi_edits())
        self.rectangle_background_width_spin.valueChanged.connect(lambda *_args: self._commit_rectangle_roi_edits())
        self.array_rows_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.array_cols_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.array_spacing_spin.valueChanged.connect(self._update_roi_detection_settings)
        self.detect_rois_button.clicked.connect(self._detect_rois)
        self.reorder_rois_button.clicked.connect(self._reorder_rois_by_position)
        self.clear_rois_button.clicked.connect(self._clear_detected_rois)
        self.clear_roi_selection_button.clicked.connect(self._clear_roi_selection)
        self.roi_array_action.toggled.connect(self._on_roi_array_toggled)
        self.roi_list_action.toggled.connect(self._roi_table_controller.on_toggled)
        self.roi_list_panel.visibilityChanged.connect(self._on_roi_panel_visibility_changed)
        self.roi_table.itemSelectionChanged.connect(self._roi_table_controller.on_selection_changed)
        self.roi_table.itemChanged.connect(self._roi_table_controller.on_item_changed)
        self.roi_table.cellDoubleClicked.connect(self._roi_table_controller.on_cell_double_clicked)
        self.roi_table.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.roi_table.viewport().customContextMenuRequested.connect(self._roi_table_controller.show_context_menu)
        self.roi_export_button.clicked.connect(self._roi_table_controller.export_csv)
        self.roi_import_button.clicked.connect(self._roi_table_controller.import_csv)
        self.remove_rois_action.triggered.connect(self._remove_selected_rois)
        self.group_rois_action.triggered.connect(self._group_selected_rois)
        self.ungroup_rois_action.triggered.connect(self._ungroup_selected_rois)
        self.addAction(self.group_rois_action)
        self.addAction(self.ungroup_rois_action)
        self.show_rois_check.toggled.connect(self._on_show_rois_toggled)
        self.bottom_roi_labels_button.toggled.connect(self._on_show_roi_labels_toggled)
        self.roi_editor_labels_button.toggled.connect(self._on_roi_editor_show_labels_toggled)
        self.show_reference_check.toggled.connect(self._on_show_reference_toggled)
        self.show_reference_points_check.toggled.connect(self._on_show_reference_points_toggled)
        self.show_mask_check.toggled.connect(self._on_show_mask_toggled)
        self.show_highlight_check.toggled.connect(self._on_show_highlight_toggled)
        self._refresh_roi_list_action_icon()

    def _connect_toolbar_and_ui(self) -> None:
        self.shortcuts_action.triggered.connect(self._show_shortcuts_dialog)
        self.reset_layout_action.triggered.connect(self._reset_layout_to_defaults)
        self.reset_dock_layout_action.triggered.connect(self._reset_panel_layout)
        self.show_all_panels_action.triggered.connect(lambda *_: self._set_all_panel_visibility(True))
        self.hide_all_panels_action.triggered.connect(lambda *_: self._set_all_panel_visibility(False))
        self.expand_left_panels_action.triggered.connect(self._expand_left_panels)
        self.collapse_left_panels_action.triggered.connect(self._collapse_left_panels)
        self.about_action.triggered.connect(self._show_about_dialog)
        self.rotate_action.toggled.connect(self._on_rotate_tool_toggled)
        self.crop_action.toggled.connect(self._on_crop_tool_toggled)
        self.measure_action.toggled.connect(self._on_measure_tool_toggled)
        self.flip_horizontal_action.toggled.connect(self._on_flip_horizontal_toggled)
        self.flip_vertical_action.toggled.connect(self._on_flip_vertical_toggled)
        self.roi_edit_action.toggled.connect(self._on_roi_edit_tool_toggled)
        self.roi_add_action.toggled.connect(self._on_roi_add_toggled)
        self.roi_move_action.toggled.connect(self._on_roi_move_toggled)
        self.mask_color_button.clicked.connect(lambda: self._choose_overlay_color("mask"))
        self.sample_color_button.clicked.connect(lambda: self._choose_overlay_color("roi"))
        self.reference_color_button.clicked.connect(lambda: self._choose_overlay_color("reference"))
        self.highlight_color_button.clicked.connect(lambda: self._choose_overlay_color("highlight"))
        self.scale_bar_color_button.clicked.connect(lambda: self._choose_overlay_color("scale_bar"))
        self.mask_alpha_slider.valueChanged.connect(self._on_mask_alpha_changed)
        self.roi_alpha_slider.valueChanged.connect(self._on_roi_alpha_changed)
        self.reference_alpha_slider.valueChanged.connect(self._on_reference_alpha_changed)
        self.highlight_alpha_slider.valueChanged.connect(self._on_highlight_alpha_changed)
        self.reset_rotation_action.triggered.connect(self._reset_rotation)
        self.rotation_fill_dark_button.toggled.connect(self._on_rotation_fill_dark_toggled)
        self.reset_crop_action.triggered.connect(self._reset_crop)
        self.measurement_apply_button.clicked.connect(self._apply_measurement_calibration)
        self.measurement_unit_button.clicked.connect(self._toggle_display_units)
        self.scale_bar_toggle_button.toggled.connect(self._on_scale_bar_toggled)
        self.undo_action.triggered.connect(self._undo)
        self.redo_action.triggered.connect(self._redo)
        self._configure_control_help()
        self._update_analysis_control_state()

    # ------------------------------------------------------------------
    # ROI list table and CSV helpers
    # ------------------------------------------------------------------

    def _on_roi_list_toggled(self, checked: bool) -> None:
        self._roi_table_controller._on_roi_list_toggled(checked)

    def _on_cached_rois_only_toggled(self, checked: bool) -> None:
        self._roi_table_controller._on_cached_rois_only_toggled(checked)

    def _on_roi_panel_visibility_changed(self, visible: bool) -> None:
        self._roi_table_controller._on_roi_panel_visibility_changed(visible)

    def _sync_roi_table_selection(self) -> None:
        if not self.roi_table.isVisible():
            return
        selection_model = self.roi_table.selectionModel()
        if selection_model is None:
            return
        self._roi_list_selection_syncing = True
        try:
            selection_model.clearSelection()
            first_selected_item: QTableWidgetItem | None = None
            first_selected_row: int | None = None
            for row in range(self.roi_table.rowCount()):
                item = self.roi_table.item(row, 0)
                if item is None:
                    continue
                try:
                    roi_id = int(item.text())
                except ValueError:
                    continue
                if roi_id in self._selected_roi_ids:
                    selection_model.select(
                        self.roi_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    if first_selected_item is None:
                        first_selected_item = item
                        first_selected_row = row
            if first_selected_item is not None:
                self.roi_table.scrollToItem(first_selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.roi_table.setCurrentItem(first_selected_item)
                self._roi_list_range_anchor_row = first_selected_row
        finally:
            self._roi_list_selection_syncing = False
        self._update_selection_dependent_plots()

    def _select_roi_table_rows(self, rows: list[int]) -> None:
        selection_model = self.roi_table.selectionModel()
        if selection_model is None:
            return
        valid_rows = sorted({row for row in rows if 0 <= row < self.roi_table.rowCount()})
        if not valid_rows:
            return
        self._append_workflow_log(f"Selection | table rows {valid_rows}", level="debug")
        self._roi_list_selection_syncing = True
        selected_ids: set[int] = set()
        first_selected_item: QTableWidgetItem | None = None
        try:
            selection_model.clearSelection()
            for row in valid_rows:
                item = self.roi_table.item(row, 0)
                if item is None:
                    continue
                try:
                    roi_id = int(item.text())
                except ValueError:
                    continue
                selection_model.select(
                    self.roi_table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
                selected_ids.add(roi_id)
                if first_selected_item is None:
                    first_selected_item = item
            if first_selected_item is not None:
                self.roi_table.scrollToItem(first_selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.roi_table.setCurrentItem(first_selected_item)
        finally:
            self._roi_list_selection_syncing = False
        self._selected_roi_ids = selected_ids
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_selection_dependent_plots(prompt_live_preview=True)

    def _roi_list_roi_id_for_row(self, row: int) -> int | None:
        if row < 0 or row >= self.roi_table.rowCount():
            return None
        item = self.roi_table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _roi_list_selected_rows(self) -> list[int]:
        selection_model = self.roi_table.selectionModel()
        if selection_model is None:
            return []
        rows: list[int] = []
        for row in range(self.roi_table.rowCount()):
            if selection_model.isRowSelected(row, self.roi_table.rootIndex()):
                rows.append(row)
        return rows

    def _roi_list_selected_roi_ids(self) -> list[int]:
        roi_ids: list[int] = []
        for row in self._roi_list_selected_rows():
            roi_id = self._roi_list_roi_id_for_row(row)
            if roi_id is not None:
                roi_ids.append(roi_id)
        return roi_ids

    def _roi_list_index_for_roi(self, roi_id: int) -> int | None:
        for index, roi in enumerate(self._state.area_rois):
            if roi.area_roi_id == roi_id:
                return index
        return None

    def _refresh_roi_list_action_icon(self) -> None:
        self.roi_list_action.setIcon(self._make_roi_list_icon(self.roi_list_action.isChecked()))
        if hasattr(self, "analysis_roi_table_button"):
            self.analysis_roi_table_button.setPixmap(
                self._make_roi_list_icon(self.roi_list_action.isChecked()).pixmap(
                    APP_THEME.compact_icon_inner,
                    APP_THEME.compact_icon_inner,
                )
            )

    def _refresh_roi_table_headers(self) -> None:
        roi_table_headers(self.roi_table)

    def _on_roi_list_item_changed(self, item: QTableWidgetItem) -> None:
        self._roi_table_controller._on_roi_list_item_changed(item)

    def _on_roi_list_cell_double_clicked(self, row: int, column: int) -> None:
        self._roi_table_controller._on_roi_list_cell_double_clicked(row, column)

    def _rename_roi_group_from_table(self, roi_id: int, new_name: str) -> None:
        current_group = self._group_for_roi(roi_id)
        if not new_name:
            self.status_label.setText("Group name cannot be empty.")
            self._update_roi_table()
            return

        if current_group is not None:
            if current_group.name == new_name:
                return
            self._push_undo_point("Rename group")
            current_group.name = new_name
        else:
            selected_ids = {roi_id}
            self._push_undo_point("Group ROIs")
            for group in self._state.area_roi_groups:
                group.area_roi_ids = [rid for rid in group.area_roi_ids if rid not in selected_ids]
            self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]
            self._state.area_roi_groups.append(
                AreaRoiGroup(
                    group_id=f"group_{len(self._state.area_roi_groups) + 1}",
                    name=new_name,
                    sample_color_hex=self._sample_visual_color.name(),
                    reference_color_hex=self._reference_visual_color.name(),
                    area_roi_ids=sorted(selected_ids),
                )
            )
        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._update_roi_table()

    def _copy_roi_properties_from_table(self) -> None:
        selected_ids = self._roi_list_selected_roi_ids()
        if not selected_ids:
            self.status_label.setText("Select an ROI row first to copy its properties.")
            return
        roi_id = selected_ids[0]
        roi = self._roi_by_id(roi_id)
        if roi is None:
            return
        group = self._group_for_roi(roi_id)
        self._roi_clipboard = {
            "group_name": group.name if group is not None else None,
            "group_sample_color": group.sample_color_hex if group is not None else None,
            "group_reference_color": group.reference_color_hex if group is not None else None,
            "sample_color": self._sample_visual_color.name(),
            "reference_color": self._reference_visual_color.name(),
        }
        self.status_label.setText(f"Copied ROI properties from ROI {roi_id}.")

    def _paste_roi_properties_from_table(self) -> None:
        if not self._roi_clipboard:
            self.status_label.setText("Nothing to paste yet. Copy an ROI first.")
            return
        selected_ids = self._roi_list_selected_roi_ids()
        if not selected_ids:
            self.status_label.setText("Select one or more ROI rows to paste properties.")
            return
        self._push_undo_point("Paste ROI properties")
        group_name = self._roi_clipboard.get("group_name")
        group_sample_color = self._roi_clipboard.get("group_sample_color")
        group_reference_color = self._roi_clipboard.get("group_reference_color")
        sample_color = self._roi_clipboard.get("sample_color")
        reference_color = self._roi_clipboard.get("reference_color")
        if isinstance(sample_color, str):
            self._sample_visual_color = QColor(sample_color)
        if isinstance(reference_color, str):
            self._reference_visual_color = QColor(reference_color)
        if isinstance(group_name, str) and group_name:
            target_group = next((group for group in self._state.area_roi_groups if group.name == group_name), None)
            if target_group is None:
                target_group = AreaRoiGroup(
                    group_id=f"group_{len(self._state.area_roi_groups) + 1}",
                    name=group_name,
                    sample_color_hex=str(group_sample_color) if isinstance(group_sample_color, str) else self._sample_visual_color.name(),
                    reference_color_hex=str(group_reference_color) if isinstance(group_reference_color, str) else self._reference_visual_color.name(),
                    area_roi_ids=[],
                )
                self._state.area_roi_groups.append(target_group)
            else:
                if isinstance(group_sample_color, str):
                    target_group.sample_color_hex = group_sample_color
                if isinstance(group_reference_color, str):
                    target_group.reference_color_hex = group_reference_color
            target_group.area_roi_ids = sorted(set(target_group.area_roi_ids).union(selected_ids))
            for other_group in self._state.area_roi_groups:
                if other_group is target_group:
                    continue
                other_group.area_roi_ids = [roi_id for roi_id in other_group.area_roi_ids if roi_id not in selected_ids]
            self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]
        else:
            for group in self._state.area_roi_groups:
                group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in selected_ids]
            self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]
        self._update_color_button_styles()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._update_roi_table()

    def _move_selected_rois_in_table(self, direction: int) -> None:
        selected_rows = self._roi_list_selected_rows()
        if not selected_rows or direction == 0:
            return
        roi_id = self._roi_list_roi_id_for_row(selected_rows[0])
        if roi_id is None:
            return
        index = self._roi_list_index_for_roi(roi_id)
        if index is None:
            return
        if direction < 0 and index == 0:
            return
        if direction > 0 and index >= len(self._state.area_rois) - 1:
            return
        reordered = list(self._state.area_rois)
        swap_index = index - 1 if direction < 0 else index + 1
        reordered[index], reordered[swap_index] = reordered[swap_index], reordered[index]
        old_ids = [roi.area_roi_id for roi in reordered]
        id_map = {old_id: new_id for new_id, old_id in enumerate(old_ids, start=1)}
        for new_id, roi in enumerate(reordered, start=1):
            roi.area_roi_id = new_id
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [id_map.get(roi_id, roi_id) for roi_id in group.area_roi_ids]
            group.area_roi_ids = sorted(dict.fromkeys(group.area_roi_ids))
        self._state.area_rois = reordered
        self._selected_roi_ids = {id_map.get(roi_id, roi_id) for roi_id in self._selected_roi_ids}
        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._update_roi_table()

    def _edit_roi_color_from_table(self, roi_id: int) -> None:
        roi = self._roi_by_id(roi_id)
        if roi is None:
            return
        initial = QColor(roi.sample_color_hex) if roi.sample_color_hex else QColor(self._sample_visual_color)
        color = QColorDialog.getColor(initial, self, "Choose sample color")
        if not color.isValid():
            return
        self._push_undo_point("Edit ROI color")
        roi.sample_color_hex = color.name()
        self._update_color_button_styles()
        self._update_roi_overlays()
        self._save_processing_state_for_dataset()
        self._update_roi_table()

    def _edit_reference_color_from_table(self) -> None:
        selected_ids = self._roi_list_selected_roi_ids()
        roi = self._roi_by_id(selected_ids[0]) if selected_ids else None
        initial = QColor(roi.reference_color_hex) if roi is not None and roi.reference_color_hex else QColor(self._reference_visual_color)
        color = QColorDialog.getColor(initial, self, "Choose reference ROI color")
        if not color.isValid():
            return
        self._push_undo_point("Edit reference ROI color")
        if roi is not None:
            roi.reference_color_hex = color.name()
        else:
            self._reference_visual_color = color
        self._update_color_button_styles()
        self._update_roi_overlays()
        self._save_processing_state_for_dataset()
        self._update_roi_table()

    def _edit_roi_geometry_from_table(self, roi_id: int) -> None:
        roi = self._roi_by_id(roi_id)
        if roi is None:
            return
        value, ok = QInputDialog.getDouble(
            self,
            "Sample diameter",
            f"Sample diameter for ROI {roi_id}",
            float(roi.sample_diameter_px if roi.sample_diameter_px is not None else self.sample_diameter_spin.value()),
            2.0,
            1000.0,
            2,
        )
        if not ok:
            return
        self._push_undo_point("Edit ROI diameter")
        roi.sample_diameter_px = float(value)
        self._save_processing_state_for_dataset()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()

    def _edit_reference_geometry_from_table(self, roi_id: int) -> None:
        roi = self._roi_by_id(roi_id)
        if roi is None:
            return
        inner_default = float(roi.reference_inner_diameter_px if roi.reference_inner_diameter_px is not None else self.reference_inner_diameter_spin.value())
        outer_default = float(roi.reference_outer_diameter_px if roi.reference_outer_diameter_px is not None else self.reference_outer_diameter_spin.value())
        inner_value, ok = QInputDialog.getDouble(self, "Reference ROI region", f"Inner diameter for ROI {roi_id}", inner_default, 0.0, 1000.0, 2)
        if not ok:
            return
        outer_value, ok = QInputDialog.getDouble(self, "Reference ROI region", f"Outer diameter for ROI {roi_id}", outer_default, inner_value, 1000.0, 2)
        if not ok:
            return
        self._push_undo_point("Edit reference ROI diameter")
        roi.reference_inner_diameter_px = float(inner_value)
        roi.reference_outer_diameter_px = float(max(outer_value, inner_value))
        self._save_processing_state_for_dataset()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()

    def _edit_roi_diameter_cells_from_table(self, roi_id: int, row: int) -> None:
        roi = self._roi_by_id(roi_id)
        if roi is None:
            return
        try:
            sample_diameter_text = self.roi_table.item(row, 2).text() if self.roi_table.item(row, 2) is not None else ""
            reference_inner_text = self.roi_table.item(row, 3).text() if self.roi_table.item(row, 3) is not None else ""
            reference_outer_text = self.roi_table.item(row, 4).text() if self.roi_table.item(row, 4) is not None else ""
            sample_diameter = float(sample_diameter_text)
            reference_inner = float(reference_inner_text)
            reference_outer = float(reference_outer_text)
        except ValueError:
            self.status_label.setText("ROI diameter cells must contain numbers.")
            self._update_roi_table()
            return
        self._push_undo_point("Edit ROI geometry")
        roi.sample_diameter_px = sample_diameter
        roi.reference_inner_diameter_px = reference_inner
        roi.reference_outer_diameter_px = max(reference_outer, reference_inner)
        if self.roi_geometry_scope_button.isChecked():
            self._state.area_roi_settings.sample_radius_px = max(sample_diameter / 2.0, 1.0)
        if self.reference_geometry_scope_button.isChecked():
            self._state.area_roi_settings.reference_inner_radius_px = max(reference_inner / 2.0, 0.0)
            self._state.area_roi_settings.reference_outer_radius_px = max(reference_outer / 2.0, reference_inner / 2.0)
        self._save_processing_state_for_dataset()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()

    def _export_roi_list_csv(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(self, "Save ROI list CSV", "", "CSV Files (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        rows = sorted(self._state.area_rois, key=lambda roi: roi.area_roi_id)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "area_roi_id",
                "group_name",
                "group_sample_color",
                "group_reference_color",
                "sample_color",
                "reference_color",
                "center_x",
                "center_y",
                "area_roi_order",
                "sample_diameter_px",
                "reference_inner_diameter_px",
                "reference_outer_diameter_px",
            ])
            for order, roi in enumerate(rows):
                group = self._group_for_roi(roi.area_roi_id)
                writer.writerow([
                    roi.area_roi_id,
                    group.name if group is not None else "",
                    group.sample_color_hex if group is not None else "",
                    group.reference_color_hex if group is not None else "",
                    roi.sample_color_hex or "",
                    roi.reference_color_hex or "",
                    self._sample_visual_color.name(),
                    self._reference_visual_color.name(),
                    roi.center_x,
                    roi.center_y,
                    order,
                    "" if roi.sample_diameter_px is None else roi.sample_diameter_px,
                    "" if roi.reference_inner_diameter_px is None else roi.reference_inner_diameter_px,
                    "" if roi.reference_outer_diameter_px is None else roi.reference_outer_diameter_px,
                ])
        self.status_label.setText(f"Saved ROI list to {path.name}.")

    def _import_roi_list_csv(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(self, "Load ROI list CSV", "", "CSV Files (*.csv)")
        if not path_str:
            return
        path = Path(path_str)
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        if not rows:
            self.status_label.setText("CSV file is empty.")
            return
        self._push_undo_point("Import ROI list CSV")
        groups_by_name = {group.name: group for group in self._state.area_roi_groups}
        ordered_rows = sorted(rows, key=lambda row: int(row.get("spot_order", row.get("roi_id", 0)) or 0))
        for row in ordered_rows:
            try:
                roi_id = int(row.get("roi_id", ""))
            except ValueError:
                continue
            roi = self._roi_by_id(roi_id)
            if roi is None:
                continue
            group_name = str(row.get("group_name", "")).strip()
            group_color = str(row.get("group_color", "")).strip() or "#f59e0b"
            group_reference_color = str(row.get("group_reference_color", "")).strip() or self._reference_visual_color.name()
            if group_name:
                group = groups_by_name.get(group_name)
                if group is None:
                    group = AreaRoiGroup(
                        group_id=f"group_{len(groups_by_name) + 1}",
                        name=group_name,
                        sample_color_hex=group_color,
                        reference_color_hex=group_reference_color,
                        area_roi_ids=[],
                    )
                    self._state.area_roi_groups.append(group)
                    groups_by_name[group_name] = group
                if roi_id not in group.area_roi_ids:
                    group.area_roi_ids.append(roi_id)
                group.sample_color_hex = group_color
                group.reference_color_hex = group_reference_color
            roi.sample_diameter_px = None if row.get("sample_diameter_px", "") == "" else float(row["sample_diameter_px"])
            roi.reference_inner_diameter_px = None if row.get("reference_inner_diameter_px", "") == "" else float(row["reference_inner_diameter_px"])
            roi.reference_outer_diameter_px = None if row.get("reference_outer_diameter_px", "") == "" else float(row["reference_outer_diameter_px"])
            if row.get("sample_color", ""):
                self._sample_visual_color = QColor(str(row["sample_color"]))
            if row.get("reference_color", ""):
                self._reference_visual_color = QColor(str(row["reference_color"]))
        self._update_color_button_styles()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._update_roi_table()
        self.status_label.setText(f"Loaded ROI list from {path.name}.")

    def _roi_table_legacy_copy(self) -> None:
        self._roi_table_updating = True
        self.roi_table.blockSignals(True)
        self.roi_table.setRowCount(0)
        rois = sorted(self._state.area_rois, key=lambda roi: roi.area_roi_id)
        if not rois:
            self.roi_table.blockSignals(False)
            self._roi_table_updating = False
            self._sync_roi_table_selection()
            return
        for roi in rois:
            row = self.roi_table.rowCount()
            self.roi_table.insertRow(row)
            self.roi_table.setRowHeight(row, 18)
            id_item = QTableWidgetItem(str(roi.area_roi_id))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.roi_table.setItem(row, 0, id_item)
            group = self._group_for_roi(roi.area_roi_id)
            group_name = group.name if group is not None else "—"
            group_item = QTableWidgetItem(group_name)
            group_item.setFlags(group_item.flags() | Qt.ItemFlag.ItemIsEditable)
            if group is not None:
                group_color = QColor(group.sample_color_hex)
                if group_color.isValid():
                    group_item.setForeground(group_color)
                    group_item.setToolTip(f"{group.name} ({group.sample_color_hex})")
            self.roi_table.setItem(row, 1, group_item)
            sample_color_label = QLabel()
            sample_color_label.setFixedSize(16, 16)
            sample_color = getattr(self._state, "overlay_colors", {}).get("roi", QColor("#f8fafc")) if hasattr(self._state, "overlay_colors") else QColor("#f8fafc")
            sample_color_label.setStyleSheet(f"background-color: {sample_color.name()}; border: 1px solid #2d2d2d;")
            self.roi_table.setCellWidget(row, 2, sample_color_label)
            reference_color_label = QLabel()
            reference_color_label.setFixedSize(18, 18)
            reference_color = getattr(self._state, "overlay_colors", {}).get("ring", QColor("#38bdf8")) if hasattr(self._state, "overlay_colors") else QColor("#38bdf8")
            reference_color_label.setStyleSheet(f"background-color: {reference_color.name()}; border: 1px solid #2d2d2d;")
            self.roi_table.setCellWidget(row, 3, reference_color_label)
            if self._state.preprocessing.display_units == "um" and self._can_display_micrometers():
                scale = self._microns_per_pixel_scalar()
                pos_text = f"x: {roi.center_x * scale:.1f} y: {roi.center_y * scale:.1f}"
            else:
                pos_text = f"x: {roi.center_x:.1f} y: {roi.center_y:.1f}"
            pos_item = QTableWidgetItem(pos_text)
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.roi_table.setItem(row, 4, pos_item)
        self.roi_table.resizeColumnsToContents()
        self.roi_table.setColumnWidth(0, 38)
        self.roi_table.setColumnWidth(1, 88)
        self.roi_table.setColumnWidth(2, 22)
        self.roi_table.setColumnWidth(3, 22)
        self.roi_table.setColumnWidth(4, 126)
        self.roi_table.horizontalHeader().setStretchLastSection(True)
        self._sync_roi_table_selection()

    def _update_roi_table(self) -> None:
        if self._roi_refresh_timer.isActive():
            self._roi_refresh_timer.stop()
        self._roi_refresh_timer.start()

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
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._startup_restore_in_progress = True
        self._startup_progress_callback = progress_callback
        self._append_workflow_log("Startup | restore begin", level="debug")
        self._report_startup_progress(8, "Restoring window layout...")
        self._restore_saved_window_layout()
        self._report_startup_progress(18, "Checking previous session...")
        self._dataset_controller.run_startup_restore_flow(
            on_done=lambda show_window=show_window, on_done=on_done: self._on_startup_dataset_restore_done(
                show_window, on_done
            )
        )

    def _on_startup_dataset_restore_done(self, show_window: bool, on_done: Callable[[], None] | None) -> None:
        if self._state.dataset is None:
            self._append_workflow_log("Startup | no dataset restored", level="warning")
            self._report_startup_progress(100, "Ready.")
            if self._fast_startup:
                self._set_status_text("Fast startup enabled. Load a dataset when ready.")
            if show_window:
                self._layout_state_controller.restore_saved_window_state_after_show()
                self.raise_()
                self.activateWindow()
                self._sync_panel_visibility_after_show()
            self._startup_restore_in_progress = False
            self._startup_ready = True
            self._startup_progress_callback = None
            if on_done is not None:
                on_done()
            return
        self._report_startup_progress(58, "Loading analysis cache...")
        self._append_workflow_log("Startup | analysis cache restored", level="debug")
        self._sync_roi_detection_controls()
        self._restore_control_preferences()
        self._report_startup_progress(76, "Preparing the first image...")
        self._analysis_enabled = False
        self._analysis_live_preview_enabled = False
        self._set_section_applied(self.analysis_section, False)
        self._settings.setValue("analysis_section_applied", False)
        self._settings.setValue("analysis/live_preview", False)
        # The dataset-load chain that got us here already kicked off (and, on a cache
        # hit, already applied) the first image - this call is a no-op in that case,
        # matching the pre-async code's own redundant call at this same point.
        self._refresh_image()
        self._report_startup_progress(92, "Finalizing workspace...")
        if show_window:
            self._layout_state_controller.restore_saved_window_state_after_show()
            self.raise_()
            self.activateWindow()
            self._sync_panel_visibility_after_show()
        self._startup_restore_in_progress = False
        self._startup_ready = True
        self._report_startup_progress(100, "Workspace ready.")
        self._append_workflow_log("Startup | workspace ready", level="success")
        self._startup_progress_callback = None
        # Busy-state pairing for the dataset load itself now lives entirely in
        # DatasetController's async chain (_finish_load_dataset_from_folder calls
        # _end_busy()) - calling _end_busy() again here would double-decrement the
        # shared busy-operation counter for what was only ever one _begin_busy() call.
        self._set_status_text("Loaded dataset. Showing the reference image.")
        if on_done is not None:
            on_done()

    def _restore_saved_window_layout(self) -> None:
        self._layout_state_controller.restore_saved_window_layout()

    def _restore_saved_window_state_after_show(self) -> None:
        self._layout_state_controller.restore_saved_window_state_after_show()

    def _restore_saved_panel_layout_state(self) -> bool:
        return self._layout_state_controller.restore_saved_panel_layout_state()

    def _normalize_panel_layout(self) -> None:
        self._layout_state_controller.normalize_panel_layout()

    def _configure_slider(self, slider: QSlider, value_count: int) -> None:
        slider.setEnabled(value_count > 0)
        slider.setMinimum(0)
        slider.setMaximum(max(value_count - 1, 0))
        slider.setSingleStep(1)
        slider.setPageStep(1)

    def _configure_navigation_inputs(self) -> None:
        spectral_cube_enabled = bool(self._spectral_cube_values)
        self.spectral_cube_spin.setEnabled(spectral_cube_enabled)
        if spectral_cube_enabled:
            self.spectral_cube_spin.setRange(min(self._spectral_cube_values), max(self._spectral_cube_values))

        wavelength_enabled = bool(self._wavelength_values)
        self.wavelength_spin.setEnabled(wavelength_enabled)
        if wavelength_enabled:
            self.wavelength_spin.setRange(min(self._wavelength_values), max(self._wavelength_values))
            decimals = max((self._decimal_places(value) for value in self._wavelength_values), default=0)
            self.wavelength_spin.setDecimals(min(max(decimals, 0), 4))
        self._sync_analysis_spectral_cube_range_controls()
        self._sync_analysis_plots()

    def _analysis_metric_key(self) -> str:
        return self._analysis_controller._analysis_metric_key()

    def _analysis_metric_label(self) -> str:
        return self._analysis_controller._analysis_metric_label()

    def _analysis_metric_axis_label(self) -> str:
        return self._analysis_controller._analysis_metric_axis_label()

    def _analysis_poly_order(self) -> int:
        return self._analysis_controller._analysis_poly_order()

    def _current_analysis_spectral_cube_range(self) -> tuple[int, int] | None:
        return self._analysis_controller._current_analysis_spectral_cube_range()

    def _sync_analysis_spectral_cube_range_controls(self) -> None:
        self._analysis_controller._sync_analysis_spectral_cube_range_controls()

    def _set_sensorgram_summary_text(self, text: str) -> None:
        self._analysis_controller._set_sensorgram_summary_text(text)

    def _update_sensorgram_plot_labels(self) -> None:
        self._analysis_controller._update_sensorgram_plot_labels()

    def _analysis_plot_spectral_cube_range(self) -> tuple[int, int] | None:
        return self._analysis_controller._analysis_plot_spectral_cube_range()

    def _analysis_plot_wavelength_range(self) -> tuple[float, float] | None:
        return self._analysis_controller._analysis_plot_wavelength_range()

    def _sync_analysis_plot_axes(self) -> None:
        self._analysis_controller._sync_analysis_plot_axes()

    def _sync_analysis_plot_cursors(self) -> None:
        self._analysis_controller._sync_analysis_plot_cursors()

    def _sync_analysis_plots(self) -> None:
        self._analysis_controller._sync_analysis_plots()

    def _on_spectrum_cursor_moved(self) -> None:
        self._analysis_controller._on_spectrum_cursor_moved()

    def _on_sensorgram_cursor_moved(self) -> None:
        self._analysis_controller._on_sensorgram_cursor_moved()

    def _clear_sensorgram(self, summary_text: str) -> None:
        self._plot_manager.clear_sensorgram(summary_text)

    def _set_sensorgram_series(
        self,
        spectral_cube_indices,
        metric_values,
        *,
        summary_text: str | None = None,
    ) -> None:
        self._plot_manager.set_sensorgram_series(spectral_cube_indices, metric_values, summary_text=summary_text)

    def _update_sensorgram_current_point(self) -> None:
        self._plot_manager.update_sensorgram_current_point()

    def _mark_sensorgram_stale(self, reason: str | None = None) -> None:
        self._analysis_controller._mark_sensorgram_stale(reason)

    def _current_spectral_cube(self) -> int | None:
        if not self._spectral_cube_values:
            return None
        return self._spectral_cube_values[self.spectral_cube_slider.value()]

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
        spectral_cube_values = list(getattr(dataset, "spectral_cube_indices", []))
        wavelength_values = list(getattr(dataset, "wavelengths_nm", []))
        first_record = records[0] if records else None
        resolution_text = "Unknown"
        if first_record is not None:
            try:
                height, width = load_image_shape(str(first_record.path))
                resolution_text = f"{width} x {height} px"
            except Exception:
                resolution_text = "Unknown"
        ome_zarr = dataset_is_ome_zarr(dataset)
        if ome_zarr:
            # OME-Zarr records carry synthetic per-plane "paths" (e.g. 0/3.2.0.0)
            # used only to key into the zarr array — they aren't real files on
            # disk (zarr v3 sharding packs many logical planes into one shard
            # file), so record.path.stat() always fails here. The real,
            # statable files are the shard/metadata files under the dataset's
            # actual .ome.zarr folder instead.
            size_bytes = 0
            mtimes: list[float] = []
            try:
                for entry in dataset.folder.rglob("*"):
                    if not entry.is_file():
                        continue
                    try:
                        stat_result = entry.stat()
                    except OSError:
                        continue
                    size_bytes += int(stat_result.st_size)
                    mtimes.append(stat_result.st_mtime)
            except OSError:
                pass
            dataset_date = datetime.fromtimestamp(min(mtimes)).strftime("%Y-%m-%d") if mtimes else "Unknown"
        else:
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
        spectral_cube_text = f"{len(spectral_cube_values)}" if spectral_cube_values else "0"
        wavelength_text = f"{len(wavelength_values)}" if wavelength_values else "0"
        stack_label = dataset.format_label if dataset is not None else "ImageStack"
        lines = [
            f"{stack_label} loaded.",
            f"Images: {len(records)}",
            f"Spectral cubes: {spectral_cube_text} | Wavelengths: {wavelength_text}",
            f"Dataset size: {self._format_dataset_bytes(size_bytes)}",
            f"Resolution: {resolution_text}",
            f"Dataset's date: {dataset_date}",
        ]
        if ome_zarr:
            zarr_summary = read_existing_ome_zarr_summary(dataset.folder)
            if zarr_summary is not None:
                skip_labels = {"Image size", "Spectral cubes x wavelengths", "Source folder"}
                lines.extend(
                    f"{label}: {value}"
                    for label, value in zarr_summary.field_lines()
                    if label not in skip_labels
                )
        return "\n".join(lines)

    def _refresh_image(self) -> None:
        spectral_cube_index = self._current_spectral_cube()
        wavelength = self._current_wavelength()
        self._append_workflow_log_throttled(
            "image_refresh",
            f"Image refresh | spectral_cube_index {spectral_cube_index if spectral_cube_index is not None else '-'} | wavelength {wavelength if wavelength is not None else '-'}",
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
        spectral_cube_index: int,
        wavelength: float,
        record_name: str,
        processed: np.ndarray,
    ) -> None:
        self._image_controller.on_image_refresh_ready(
            signature,
            cache_key,
            record_path,
            image_key,
            spectral_cube_index,
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
        spectral_cube_index: int,
        wavelength: float,
        record_name: str,
    ) -> None:
        self._image_controller.apply_loaded_image(processed, record_path, image_key, spectral_cube_index, wavelength, record_name)

    def _update_image_name_overlay(self, record_name: str | None) -> None:
        self.image_name_label.hide()

    def _initial_reference_indices(self) -> tuple[int, int]:
        if not self._spectral_cube_values or not self._wavelength_values:
            return 0, 0
        if str(self._state.preprocessing.reference_mode or "auto") == "manual":
            ref_spectral_cube = int(self._state.preprocessing.reference_spectral_cube_index)
            ref_wavelength = self._state.preprocessing.reference_wavelength_nm
            spectral_cube_index = self._spectral_cube_values.index(ref_spectral_cube) if ref_spectral_cube in self._spectral_cube_values else 0
            wavelength_index = 0
            if ref_wavelength is not None:
                wavelength_index = min(
                    range(len(self._wavelength_values)),
                    key=lambda idx: abs(self._wavelength_values[idx] - float(ref_wavelength)),
                )
            return spectral_cube_index, wavelength_index
        auto_key = self._auto_reference_image_key_for_spectral_cube(self._spectral_cube_values[0])
        if auto_key is None:
            return 0, 0
        auto_spectral_cube, auto_wavelength = auto_key
        spectral_cube_index = self._spectral_cube_values.index(int(auto_spectral_cube)) if int(auto_spectral_cube) in self._spectral_cube_values else 0
        wavelength_index = min(
            range(len(self._wavelength_values)),
            key=lambda idx: abs(self._wavelength_values[idx] - float(auto_wavelength)),
        )
        return spectral_cube_index, wavelength_index

    def _roi_signature(self, rois: list[AreaRoi] | None = None) -> tuple[object, ...]:
        roi_list = self._state.area_rois if rois is None else rois
        return tuple(
            (roi.area_roi_id, round(float(roi.center_x), 3), round(float(roi.center_y), 3), round(float(roi.sample_radius_px), 3))
            for roi in roi_list
        )

    def _preprocessing_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...]:
        crop = self._state.preprocessing.crop
        chromatic_signature = self._chromatic_signature_for_image_key(image_key)
        roi_signature_for_preprocessing: tuple[object, ...] | None = None
        if (
            self._state.preprocessing.flatten_background_enabled
            and self._state.preprocessing.flatten_background_exclude_area_rois
        ):
            roi_signature_for_preprocessing = self._roi_signature(self._rois_for_preprocessing(image_key))
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
            bool(getattr(self, "_image_tools_preview_only", False)),
            round(float(self._state.preprocessing.rotation_angle_deg), 6),
            bool(getattr(self._state.preprocessing, "rotation_fill_dark", False)),
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
            bool(self._state.preprocessing.flatten_background_exclude_area_rois),
            bool(self._state.preprocessing.flatten_background_exclude_mask),
            int(getattr(self._state.preprocessing, "flatten_background_exclusion_dilation_px", 0)),
            bool(self._state.preprocessing.chromatic_correction_enabled),
            roi_signature_for_preprocessing,
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
        mask_settings = self._state.area_roi_settings if self._state.preprocessing.flatten_background_exclude_mask else None
        rois = self._rois_for_preprocessing(image_key)
        external_mask, external_mask_processed = self._effective_external_mask_for_record(record.path, processed_space=True)
        processed = apply_preprocessing(
            raw_image,
            self._state.preprocessing,
            rois=rois,
            mask_settings=mask_settings,
            external_mask=external_mask,
            external_mask_processed=external_mask_processed,
            mask_state=self._state.mask if self._mask_section_applied() else None,
            skip_crop=bool(getattr(self, "_image_tools_preview_only", False)),
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
        self._analysis_controller._invalidate_absorbance_spectrum_cache()

    def _invalidate_caches_for_exclusion_change(self) -> None:
        self._analysis_controller._invalidate_caches_for_exclusion_change()

    def _invalidate_per_frame_display_caches(self) -> None:
        """Clears only the single-slot "currently displayed frame" caches. Safe to call on
        every frame navigation: each of these caches only ever holds one frame's worth of
        data, so navigating away from a frame would invalidate them via a signature mismatch
        anyway. Does NOT touch the multi-frame absorbance/sensorgram math-layer caches
        (_absorbance_spectrum_cache, _absorbance_spectral_cube_cache, _roi_absorbance_cache) —
        those are already keyed per-frame, so clearing them here would just discard other
        frames' results for no reason. Callers that changed something upstream of those caches
        (mask, chromatic, image tools, background, dataset/session load) must call
        _invalidate_image_analysis_caches() instead."""
        self._invalidate_ignored_mask_cache()
        self._invalidate_roi_mask_cache()
        self._invalidate_histogram_source_cache()
        self._processed_external_mask_cache_signature = None
        self._processed_external_mask_cache_value = None
        self._display_roi_cache_signature = None
        self._display_roi_cache_value = None
        self._processed_mask_view_cache_signature = None
        self._processed_mask_view_cache_value = None

    def _invalidate_image_analysis_caches(self) -> None:
        self._invalidate_per_frame_display_caches()
        self._invalidate_absorbance_spectrum_cache()
        if hasattr(self, "analysis_metric_combo"):
            self._mark_sensorgram_stale()

    def _background_error(self, context: str, message: str) -> None:
        self._workflow_log_controller.background_error(context, message)

    def _set_workflow_log_autoscroll_enabled(self, enabled: bool) -> None:
        self._workflow_log_controller.set_workflow_log_autoscroll_enabled(enabled)

    def _copy_workflow_log(self) -> None:
        self._workflow_log_controller.copy_workflow_log()

    def _setup_workflow_logging(self) -> None:
        self._workflow_log_controller.setup_workflow_logging()

    def _remove_workflow_logging(self) -> None:
        self._workflow_log_controller.remove_workflow_logging()

    def _append_workflow_log_entry(self, levelno: int, text: str) -> None:
        self._workflow_log_controller.append_workflow_log_entry(levelno, text)

    def _flush_workflow_log_buffer(self) -> None:
        self._workflow_log_controller.flush_workflow_log_buffer()

    def _append_workflow_log_entry_now(self, levelno: int, text: str) -> None:
        self._workflow_log_controller.append_workflow_log_entry_now(levelno, text)

    def _append_workflow_log(self, message: str, *, level: str = "info") -> None:
        self._workflow_log_controller.append_workflow_log(message, level=level)

    def _append_workflow_log_throttled(self, key: str, message: str, *, level: str = "debug", min_interval: float = 2.0) -> None:
        self._workflow_log_controller.append_workflow_log_throttled(key, message, level=level, min_interval=min_interval)

    def _set_status_text(self, text: str) -> None:
        self._workflow_log_controller.set_status_text(text)

    def _set_status_hint(self, text: str) -> None:
        self._workflow_log_controller.set_status_hint(text)

    @staticmethod

    def _format_elapsed_seconds(seconds: float | None) -> str:
        return WorkflowLogController.format_elapsed_seconds(seconds)

    def _compact_timing_text(self, *parts: tuple[str, float | None]) -> str:
        return self._workflow_log_controller.compact_timing_text(*parts)

    def _workflow_notes_text(self) -> str:
        return self._workflow_log_controller.workflow_notes_text()

    def _show_workflow_notes(self) -> None:
        self._workflow_log_controller.show_workflow_notes()

    def _update_status_hint(self) -> None:
        self._workflow_log_controller.update_status_hint()


    @staticmethod

    def _alpha01(value: float) -> float:
        return alpha01(value)

    def _raw_preprocessing_signature(self) -> tuple[object, ...]:
        crop = self._state.preprocessing.crop
        return (
            bool(getattr(self._state.preprocessing, "image_tools_enabled", True)),
            round(float(self._state.preprocessing.rotation_angle_deg), 6),
            bool(getattr(self._state.preprocessing, "rotation_fill_dark", False)),
            bool(self._state.preprocessing.flip_horizontal),
            bool(self._state.preprocessing.flip_vertical),
            bool(crop.enabled),
            int(crop.x),
            int(crop.y),
            int(crop.width),
            int(crop.height),
        )

    def _push_undo_point(self, label: str) -> None:
        self._undo_manager.push(label)

    def _prepare_undo_snapshot(self, label: str) -> None:
        self._undo_manager.prepare(label)

    def _commit_prepared_undo_snapshot(self) -> None:
        self._undo_manager.commit_prepared()

    def _undo(self) -> None:
        self._undo_manager.undo()

    def _redo(self) -> None:
        self._undo_manager.redo()

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
        self._undo_manager.update_action_state()

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
        self._undo_manager.update_action_state()

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
        self._undo_manager.update_action_state()

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
            eta_percent = max(current_percent, 1)
            if current_percent > 0 or elapsed >= 1.0:
                eta_seconds = max((elapsed * (100.0 - eta_percent)) / max(eta_percent, 1), 0.0)
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
        if not self._dragging_rois and not self._roi_edit_refresh_pending:
            self._schedule_absorbance_spectrum_refresh()

    def _schedule_processing_state_save(self) -> None:
        self._processing_state_save_timer.start()

    def _mask_preview_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...]:
        return self._mask_controller.preview_signature(image_key)

    def _background_profile_signature(self) -> tuple[object, ...] | None:
        return self._bg_profile._background_profile_signature()

    def _calculate_background_profile_image(self) -> np.ndarray | None:
        return self._bg_profile._calculate_background_profile_image()

    def _request_background_profile_image(self, on_ready) -> None:
        self._bg_profile.request_background_profile_image(on_ready)

    def _update_background_profile_preview(self) -> None:
        self._bg_profile._update_background_profile_preview()

    def _on_background_profile_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        profile,
    ) -> None:
        self._bg_profile._on_background_profile_ready(request_id, signature, profile)

    def _on_background_profile_failed(self, message: str) -> None:
        self._bg_profile._on_background_profile_failed(message)

    def _invalidate_background_profile_cache(self) -> None:
        self._bg_profile._invalidate_background_profile_cache()

    def _apply_main_image_content(self) -> None:
        self._bg_profile._apply_main_image_content()

    def _sync_main_view_mode(self) -> None:
        self._bg_profile._sync_main_view_mode()

    def _sync_background_profile_buttons(self, checked: bool) -> None:
        self._bg_profile._sync_background_profile_buttons(checked)

    def _sync_background_exclusion_buttons(self) -> None:
        self._bg_profile._sync_background_exclusion_buttons()

    def _on_background_profile_toggled(self, checked: bool) -> None:
        self._bg_profile._on_background_profile_toggled(checked)

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
        self._configure_data_plot(self.sensorgram_plot, bottom_label="Spectral cube", left_label="Metric")
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
        self._apply_roi_table_style()
        self._update_roi_label_button_icon(self._roi_labels_visible)
        self._apply_histogram_log_mode(refresh=not self._startup_restore_in_progress)

    def _refresh_pin_and_apply_icons(self) -> None:
        for section in (
            getattr(self, "dataset_section", None),
            getattr(self, "mask_section", None),
            getattr(self, "chromatic_section", None),
            getattr(self, "image_tools_section", None),
            getattr(self, "roi_editor_section", None),
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
            getattr(self, "roi_editor_section", None),
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
            self.clear_roi_selection_button,
        ]
        for button in buttons:
            button.setStyleSheet(style)

    def _apply_roi_table_style(self) -> None:
        theme = get_active_theme()
        self.roi_table.setStyleSheet(
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
        if self._shortcut_manager.handle_key_press(event):
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
        self._layout_state_controller.prepare_initial_show()

    # ------------------------------------------------------------------
    # Window and splitter restore/save
    # ------------------------------------------------------------------

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

    def _mask_section_applied(self) -> bool:
        return self._mask_controller.mask_section_applied()

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

    def _save_layout_preferences(self) -> None:
        self._layout_state_controller.save_layout_preferences()

    def _set_all_panel_visibility(self, visible: bool) -> None:
        self._layout_state_controller.set_all_panel_visibility(visible)

    def _sync_panel_visibility_after_show(self) -> None:
        self._layout_state_controller.sync_panel_visibility_after_show()

    def _on_panel_visibility_changed(self, _dock: QWidget) -> None:
        self._layout_state_controller.on_panel_visibility_changed(_dock)

    @staticmethod

    def _compact_folder_label(folder: Path) -> str:
        parts = folder.parts
        if len(parts) <= 2:
            return str(folder)
        return f"...\\{parts[-2]}\\{parts[-1]}"

    def _active_session_dir(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is None:
            return None
        if not self._active_session_name or self._active_session_name == "Default":
            return dataset.folder
        return dataset.folder / "sessions" / self._active_session_name

    def _active_session_pointer_path(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is None:
            return None
        return dataset.folder / "sessions" / "active_session.txt"

    def _load_active_session_name_for_folder(self, folder: Path) -> str:
        pointer = folder / "sessions" / "active_session.txt"
        if pointer.exists():
            try:
                name = pointer.read_text(encoding="utf-8").strip()
            except OSError:
                name = ""
            if name:
                return name
        return "Default"

    def _save_active_session_pointer(self) -> None:
        pointer = self._active_session_pointer_path()
        if pointer is None:
            return
        try:
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(self._active_session_name, encoding="utf-8")
        except OSError:
            pass

    def _list_available_sessions(self) -> list[str]:
        names = ["Default"]
        dataset = self._state.dataset
        if dataset is None:
            return names
        sessions_dir = dataset.folder / "sessions"
        if sessions_dir.exists():
            for child in sorted(sessions_dir.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and (child / "processing_profile.json").exists():
                    names.append(child.name)
        return names

    def _preprocessing_path(self) -> Path | None:
        session_dir = self._active_session_dir()
        if session_dir is None:
            return None
        return session_dir / "preprocessing.json"

    def _processing_profile_path(self) -> Path | None:
        session_dir = self._active_session_dir()
        if session_dir is None:
            return None
        return session_dir / "processing_profile.json"

    def _dataset_folder_path(self) -> Path | None:
        dataset = self._state.dataset
        if dataset is not None:
            return dataset.folder
        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            return None
        folder = Path(folder_text)
        return folder if folder.exists() else None

    def _load_processing_state_for_dataset(self, *, on_done=None) -> None:
        self._session_state_manager.load_processing_state_for_dataset(on_done=on_done)

    def _normalize_mask_application_state(self) -> None:
        self._mask_controller.normalize_application_state()

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
            "area_roi_settings": asdict(self._state.area_roi_settings),
            "mask_settings": _mask_signature(self._state.mask),
            "detected_rois": [asdict(roi) for roi in self._state.area_rois],
            "area_roi_groups": [asdict(group) for group in self._state.area_roi_groups],
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

    def _new_session(self) -> None:
        self._session_state_manager.new_session()

    def _load_session(self) -> None:
        self._session_state_manager.load_session()

    def _startup_restore_timeout_seconds(self) -> int:
        return max(int(self._settings.value("startup/restore_previous_session_timeout_s", 5)), 0)

    def _set_startup_restore_timeout_seconds(self, seconds: int) -> None:
        timeout = max(int(seconds), 0)
        self._settings.setValue("startup/restore_previous_session_timeout_s", timeout)
        for value, action in getattr(self, "startup_restore_timeout_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(int(value) == timeout)
            action.blockSignals(False)

    def _set_ui_scale_factor(self, value: str) -> None:
        self._settings.setValue("ui/scale_factor", value)
        for v, action in getattr(self, "_ui_scale_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(v == value)
            action.blockSignals(False)
        reply = QMessageBox.question(
            self,
            "UI Scale",
            "Restart now to apply the new UI scale?\n"
            "Your current session will be saved and restored automatically.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._save_processing_state_for_dataset()
            subprocess.Popen([sys.executable] + sys.argv)
            self.close()

    def _schedule_roi_state_save(self) -> None:
        self._roi_state_save_timer.start()

    def _sync_image_processing_controls(self) -> None:
        self._ui_state_manager.sync_image_processing_controls()

    def _restore_visual_preferences(self) -> None:
        self._ui_state_manager.restore_visual_preferences()

    def _save_visual_preferences(self) -> None:
        self._ui_state_manager.save_visual_preferences()

    def _on_image_view_range_changed(self, *_args) -> None:
        self._image_controller.on_image_view_range_changed(*_args)

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
        return self._chromatic_controller.feature_count_options()

    def _chromatic_feature_count_value(self) -> int:
        return self._chromatic_controller.feature_count_value()

    def _set_chromatic_feature_count_value(self, value: int) -> None:
        self._chromatic_controller.set_feature_count_value(value)

    def _chromatic_subpixel_precision_options(self) -> tuple[int, ...]:
        return self._chromatic_controller.subpixel_precision_options()

    def _chromatic_subpixel_precision_value(self) -> int:
        return self._chromatic_controller.subpixel_precision_value()

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
            return self._auto_reference_image_key_for_spectral_cube(self._current_spectral_cube())
        wavelength = self._state.preprocessing.reference_wavelength_nm
        if wavelength is None:
            return None
        return int(self._state.preprocessing.reference_spectral_cube_index), float(wavelength)

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
        score = bimodal_dip_contrast(image)
        self._reference_contrast_cache[cache_key] = score
        return score

    def _reference_candidate_records_for_cube(
        self, spectral_cube_index: int
    ) -> list[tuple[tuple[int, float], object]]:
        candidates: list[tuple[tuple[int, float], object]] = []
        for wavelength in self._wavelength_values:
            if is_excluded(self._state.image_exclusions, int(spectral_cube_index), float(wavelength)):
                continue
            key = (int(spectral_cube_index), float(wavelength))
            record = self._record_map.get(key)
            if record is None:
                continue
            candidates.append((key, record))
        return candidates

    def _auto_reference_image_key_for_spectral_cube(self, spectral_cube_index: int | None) -> tuple[int, float] | None:
        if spectral_cube_index is None:
            return None
        best_key: tuple[int, float] | None = None
        best_score = float("-inf")
        for key, record in self._reference_candidate_records_for_cube(int(spectral_cube_index)):
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
        return self._auto_reference_image_key_for_spectral_cube(int(target_key[0]))

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
        return self._chromatic_controller.model_for_image_key(image_key)

    def _chromatic_affine_for_image_key(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        return self._chromatic_controller.affine_for_image_key(image_key)

    def _chromatic_affine_for_image_key_any(self, image_key: tuple[int, float] | None) -> np.ndarray | None:
        return self._chromatic_controller.affine_for_image_key_any(image_key)

    def _chromatic_signature_for_image_key(self, image_key: tuple[int, float] | None) -> tuple[object, ...] | None:
        return self._chromatic_controller.signature_for_image_key(image_key)

    def _display_rois(self, image_key: tuple[int, float] | None = None) -> list[AreaRoi]:
        return self._image_controller.display_rois(image_key)

    def _rois_for_preprocessing(self, image_key: tuple[int, float] | None) -> list[AreaRoi]:
        return self._image_controller.rois_for_preprocessing(image_key)

    def _roi_curve_points(
        self,
        source_roi: AreaRoi,
        display_roi: AreaRoi,
        radius_px: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        return self._image_controller.roi_curve_points(source_roi, display_roi, radius_px)

    def _set_view_overlay_visibility(
        self,
        *,
        rois_visible: bool,
        reference_rois_visible: bool,
        mask_visible: bool,
        reference_points_visible: bool,
        highlight_visible: bool,
    ) -> None:
        self._image_controller.set_view_overlay_visibility(
            rois_visible=rois_visible,
            reference_rois_visible=reference_rois_visible,
            mask_visible=mask_visible,
            reference_points_visible=reference_points_visible,
            highlight_visible=highlight_visible,
        )

    def _enter_chromatic_setup_mode(self) -> None:
        self._chromatic_controller.enter_setup_mode()

    def _leave_chromatic_setup_mode(self) -> None:
        self._chromatic_controller.leave_setup_mode()

    def _capture_chromatic_view_ranges(self) -> None:
        self._chromatic_controller.capture_view_ranges()

    def _capture_pending_image_view_ranges(self, *, preserve_view: bool = False) -> None:
        self._image_controller.capture_pending_image_view_ranges(preserve_view=preserve_view)

    def _set_clamped_image_view_ranges(
        self,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> bool:
        return self._image_controller.set_clamped_image_view_ranges(x_range, y_range)

    def _restore_pending_image_view_after_load(self) -> bool:
        return self._image_controller.restore_pending_image_view_after_load()

    def _restore_saved_image_view_after_load(self) -> bool:
        return self._image_controller.restore_saved_image_view_after_load()

    def _restore_chromatic_view_after_load(self) -> bool:
        return self._chromatic_controller.restore_view_after_load()

    def _default_chromatic_feature_points(self, image_shape: tuple[int, int], feature_count: int) -> dict[int, tuple[float, float]]:
        return self._chromatic_controller.default_feature_points(image_shape, feature_count)

    def _update_chromatic_summary(self) -> None:
        self._chromatic_controller._update_chromatic_summary()

    def _update_chromatic_control_state(self) -> None:
        self._ui_state_manager.update_chromatic_control_state()

    def _on_chromatic_reference_points_all_toggled(self, checked: bool) -> None:
        self._chromatic_controller._on_chromatic_reference_points_all_toggled(checked)

    def _on_chromatic_landmark_id_changed(self, value: int) -> None:
        self._chromatic_controller._on_chromatic_landmark_id_changed(value)

    def _on_chromatic_landmark_tool_toggled(self, checked: bool) -> None:
        self._chromatic_controller._on_chromatic_landmark_tool_toggled(checked)

    def _expected_chromatic_feature_ids(self) -> list[int]:
        return self._chromatic_controller.expected_feature_ids()

    def _chromatic_sample_image_keys(self) -> list[tuple[int, float]]:
        return self._chromatic_controller.sample_image_keys()

    def _is_chromatic_sample_image_key(self, image_key: tuple[int, float] | None) -> bool:
        return self._chromatic_controller.is_sample_image_key(image_key)

    def _current_chromatic_sample_index(self) -> int | None:
        return self._chromatic_controller.current_sample_index()

    def _current_image_landmarks(self) -> list[ChromaticLandmarkObservation]:
        return self._chromatic_controller.current_image_landmarks()

    def _current_landmark(self, landmark_id: int) -> ChromaticLandmarkObservation | None:
        return self._chromatic_controller.current_landmark(landmark_id)

    def _find_landmark_id_at(self, point: tuple[float, float]) -> int | None:
        return self._chromatic_controller.find_landmark_id_at(point)

    def _upsert_current_landmark(self, landmark_id: int, point: tuple[float, float], *, clear_models: bool) -> bool:
        return self._chromatic_controller.upsert_current_landmark(landmark_id, point, clear_models=clear_models)

    def _set_current_landmark(self, point: tuple[float, float], *, auto_advance: bool = False) -> None:
        self._chromatic_controller.set_current_landmark(point, auto_advance=auto_advance)

    def _clear_chromatic_landmarks(self, *, push_undo: bool = True) -> None:
        self._chromatic_controller.clear_landmarks(push_undo=push_undo)

    def _set_current_spectral_cube_and_wavelength(self, spectral_cube_index: int, wavelength: float) -> None:
        self._capture_chromatic_view_ranges()
        if spectral_cube_index in self._spectral_cube_values:
            spectral_cube_index = self._spectral_cube_values.index(spectral_cube_index)
            self.spectral_cube_slider.blockSignals(True)
            self.spectral_cube_slider.setValue(spectral_cube_index)
            self.spectral_cube_slider.blockSignals(False)
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

    def _go_to_reference_image(self) -> None:
        key = self._reference_image_key()
        if key is None:
            self._set_status_text("No reference image is set yet.")
            return
        self._set_current_spectral_cube_and_wavelength(int(key[0]), float(key[1]))

    def _chromatic_sample_payload(self) -> list[tuple[int, float, str]]:
        return self._chromatic_controller.sample_payload()

    def _navigate_chromatic_sample(self, direction: int) -> bool:
        return self._chromatic_controller._navigate_chromatic_sample(direction)

    def _navigate_wavelength_image(self, direction: int) -> bool:
        spectral_cube_index = self._current_spectral_cube()
        wavelength = self._current_wavelength()
        if spectral_cube_index is None or wavelength is None or not self._wavelength_values:
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
        self._set_current_spectral_cube_and_wavelength(int(spectral_cube_index), target_wavelength)
        return True

    def _navigate_spectral_cube_image(self, direction: int) -> bool:
        if not self._spectral_cube_values:
            return False
        current_index = self.spectral_cube_slider.value()
        target_index = min(max(current_index + int(direction), 0), len(self._spectral_cube_values) - 1)
        if target_index == current_index:
            return False
        self.spectral_cube_slider.setValue(target_index)
        return True

    def _on_chromatic_sample_count_changed(self, value: int) -> None:
        self._chromatic_controller._on_chromatic_sample_count_changed(value)

    def _on_chromatic_feature_count_changed(self, value: int) -> None:
        self._chromatic_controller._on_chromatic_feature_count_changed(value)

    def _on_chromatic_subpixel_precision_changed(self, _value: int) -> None:
        self._chromatic_controller._on_chromatic_subpixel_precision_changed(_value)

    def _on_chromatic_landmark_kind_changed(self, _value: int) -> None:
        self._chromatic_controller._on_chromatic_landmark_kind_changed(_value)

    def _on_chromatic_landmark_model_changed(self, _value: int) -> None:
        self._chromatic_controller._on_chromatic_landmark_model_changed(_value)

    def _seed_chromatic_landmarks_for_current_image(self) -> None:
        self._chromatic_controller._seed_chromatic_landmarks_for_current_image()

    def _finalize_chromatic_landmark_edit(self, *, status_text: str | None = None) -> None:
        self._chromatic_controller.finalize_landmark_edit(status_text=status_text)

    def _sync_current_chromatic_feature_selection(self) -> None:
        self._chromatic_controller.sync_current_feature_selection()

    def _update_reference_controls(self) -> None:
        self._ui_state_manager.update_reference_controls()

    def _set_reference_mode(self, mode: str) -> None:
        normalized_mode = "manual" if str(mode).lower() == "manual" else "auto"
        combo_index = max(self.reference_mode_combo.findData(normalized_mode), 0)
        if self.reference_mode_combo.currentData() == normalized_mode:
            if normalized_mode == "auto":
                self._sync_auto_reference_to_current_spectral_cube()
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
            self.reference_spectral_cube_status_label.setText("Spectral cube: -")
            self.reference_method_status_label.setText("Method: -")
            self._update_reference_navigation_styles()
            self._update_reference_star_overlay()
            return
        ref_spectral_cube, ref_wavelength = ref_key
        current_spectral_cube = self._current_spectral_cube()
        current_wavelength = self._current_wavelength()
        wavelength_active = current_wavelength is not None and abs(float(current_wavelength) - float(ref_wavelength)) < 1e-6
        spectral_cube_active = current_spectral_cube is not None and int(current_spectral_cube) == int(ref_spectral_cube)
        method_text = "Auto" if mode != "manual" else "Manual"
        self.reference_summary.setText(f"Reference: {method_text.lower()} | {ref_wavelength:g} nm | spectral cube {ref_spectral_cube}")
        self.reference_wavelength_status_label.setText(f"Wavelength: {ref_wavelength:g} nm")
        self.reference_spectral_cube_status_label.setText(f"Spectral cube: {ref_spectral_cube}")
        self.reference_method_status_label.setText(f"Method: {method_text}")
        self.reference_wavelength_status_label.setStyleSheet(
            f"color: {'#84cc16' if wavelength_active else '#f8fafc'}; font-weight: 600;"
        )
        self.reference_spectral_cube_status_label.setStyleSheet(
            f"color: {'#facc15' if spectral_cube_active else '#f8fafc'}; font-weight: 600;"
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
            self._sync_auto_reference_to_current_spectral_cube()
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
        spectral_cube_index = self._current_spectral_cube()
        wavelength = self._current_wavelength()
        if spectral_cube_index is None or wavelength is None:
            self._set_status_text("No image is selected for manual reference.")
            return
        if push_undo:
            self._push_undo_point("Reference image")
        self._state.preprocessing.reference_mode = "manual"
        self._state.preprocessing.reference_spectral_cube_index = int(spectral_cube_index)
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
        self._set_status_text(f"Manual reference set to {wavelength:g} nm | spectral cube {spectral_cube_index}.")

    def _schedule_auto_reference_sync(self) -> None:
        self._reference_sync_timer.start()

    def _run_debounced_auto_reference_sync(self) -> None:
        if str(self._state.preprocessing.reference_mode or "auto") != "auto":
            return
        spectral_cube_index = self._current_spectral_cube()
        if spectral_cube_index is None:
            return
        if self._reference_contrast_scores_cached_for_cube(spectral_cube_index):
            self._sync_auto_reference_to_current_spectral_cube(follow_view=False)
        else:
            self._prefetch_reference_contrast_scores(spectral_cube_index)

    def _reference_contrast_scores_cached_for_cube(self, spectral_cube_index: int) -> bool:
        return all(
            str(record.path) in self._reference_contrast_cache
            for _key, record in self._reference_candidate_records_for_cube(spectral_cube_index)
        )

    def _prefetch_reference_contrast_scores(self, spectral_cube_index: int) -> None:
        # `_auto_reference_image_key_for_spectral_cube` scores every wavelength image in a
        # spectral cube on first visit (disk load + contrast analysis per image), which can
        # take a couple of seconds and would otherwise run inline on the GUI thread the moment
        # the spectral-cube slider lands on an uncached cube. This scores the cube's images in
        # the background instead; `_run_debounced_auto_reference_sync` above only calls the
        # synchronous lookup once the cache is already warm.
        if spectral_cube_index in self._reference_prefetch_in_flight:
            return
        paths_to_score = [
            str(record.path)
            for _key, record in self._reference_candidate_records_for_cube(spectral_cube_index)
            if str(record.path) not in self._reference_contrast_cache
        ]
        if not paths_to_score:
            return
        self._reference_prefetch_in_flight.add(spectral_cube_index)
        worker = FunctionWorker(_score_reference_candidates_task, paths_to_score)
        worker.signals.result.connect(
            lambda scores, spectral_cube_index=spectral_cube_index: self._on_reference_prefetch_ready(
                spectral_cube_index, scores
            )
        )
        worker.signals.error.connect(
            lambda message, spectral_cube_index=spectral_cube_index: self._on_reference_prefetch_failed(
                spectral_cube_index, message
            )
        )
        self._thread_pool.start(worker)

    def _on_reference_prefetch_ready(self, spectral_cube_index: int, scores: dict[str, float]) -> None:
        self._reference_prefetch_in_flight.discard(spectral_cube_index)
        self._reference_contrast_cache.update(scores)
        if self._current_spectral_cube() == spectral_cube_index:
            self._sync_auto_reference_to_current_spectral_cube(follow_view=False)

    def _on_reference_prefetch_failed(self, spectral_cube_index: int, message: str) -> None:
        self._reference_prefetch_in_flight.discard(spectral_cube_index)
        self._append_workflow_log(f"Reference-image scoring failed | {message}", level="error")

    def _sync_auto_reference_to_current_spectral_cube(self, follow_view: bool = True) -> None:
        if str(self._state.preprocessing.reference_mode or "auto") != "auto":
            return
        spectral_cube_index = self._current_spectral_cube()
        if spectral_cube_index is None:
            return
        auto_key = self._auto_reference_image_key_for_spectral_cube(spectral_cube_index)
        if auto_key is None:
            return
        auto_spectral_cube, auto_wavelength = auto_key
        self._state.preprocessing.reference_mode = "auto"
        self._state.preprocessing.reference_spectral_cube_index = int(auto_spectral_cube)
        self._state.preprocessing.reference_wavelength_nm = float(auto_wavelength)
        if follow_view and self._current_image_key != auto_key and self._wavelength_values:
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
        self._overlay_manager._update_reference_star_overlay()

    def _update_reference_navigation_styles(self) -> None:
        if self._state.dataset is None:
            self.spectral_cube_spin.setStyleSheet("")
            self.wavelength_spin.setStyleSheet("")
            self.spectral_cube_slider.setStyleSheet("")
            self.wavelength_slider.setStyleSheet("")
            return
        ref_key = self._reference_image_key()
        current_spectral_cube = self._current_spectral_cube()
        current_wavelength = self._current_wavelength()
        spectral_cube_active = ref_key is not None and current_spectral_cube is not None and int(current_spectral_cube) == int(ref_key[0])
        wavelength_active = (
            ref_key is not None
            and current_wavelength is not None
            and abs(float(current_wavelength) - float(ref_key[1])) < 1e-6
        )
        if spectral_cube_active:
            self.spectral_cube_spin.setStyleSheet(
                "QSpinBox { background: rgba(250, 204, 21, 0.14); border: 1px solid #facc15; color: #fef08a; }"
            )
            self.spectral_cube_slider.setStyleSheet(
                "QSlider::handle:horizontal { background: #facc15; border: 1px solid #fef08a; width: 12px; margin: -5px 0; border-radius: 6px; }"
                "QSlider::handle:horizontal:hover { background: #fde047; }"
                "QSlider::handle:horizontal:pressed { background: #eab308; }"
            )
        else:
            self.spectral_cube_spin.setStyleSheet("")
            self.spectral_cube_slider.setStyleSheet("")
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
            f"About {version_string()}",
            f"{version_string()}\n\nDataset browsing, chromatic correction, masking, ROI editing, and spectral analysis.",
        )

    def _reset_layout_to_defaults(self) -> None:
        self._layout_state_controller.reset_layout_to_defaults()

    def _reset_panel_layout(self) -> None:
        self._layout_state_controller.reset_panel_layout()

    def _expand_left_panels(self) -> None:
        self._layout_state_controller.expand_left_panels()

    def _collapse_left_panels(self) -> None:
        self._layout_state_controller.collapse_left_panels()

    def _configure_control_help(self) -> None:
        self._set_help(self.folder_edit, "Dataset folder to load.", "Enter or paste the folder containing the image dataset.")
        self._set_help(self.browse_button, "Browse for a dataset folder.")
        self._set_help(self.load_button, "Load dataset: choose a dataset folder and open it.")
        self._set_help(self.dataset_ome_zarr_export_button, "Export: write the current dataset to a Stack to Zarr in a chosen folder.")
        self._set_help(self.dataset_ome_zarr_export_stop_button, "Stop: cancel the running Stack to Zarr export.")
        self._set_help(self.ome_zarr_chunk_spin, "Chunk tile size for Zarr export. Any value 4–4096 px — does not need to be a power of 2.")
        self._set_help(self.ome_zarr_shard_mode_combo, "Shard grouping: '1 image' = one file per wavelength × spectral cube; '1 spectral cube' = one file per time point (all wavelengths together).")
        self._set_help(self.ome_zarr_chunk_guide_button, "Guide: show chunk tiling over the current image.")
        self._set_help(self.ome_zarr_compression_button, "Compression: turn Stack to Zarr compression on or off.")
        self._set_help(self.ome_zarr_skip_excluded_button, "Skip excluded images: leave excluded planes empty in the Stack to Zarr export instead of writing their pixel data.")
        self._set_help(self.image_exclusion_button, "Exclude this image/wavelength/spectral cube from processing, or manage exclusions.")
        self._set_help(self.export_settings_button, "Export preprocessing, ROI settings, ROIs, and groups to a JSON profile.")
        self._set_help(self.import_settings_button, "Import preprocessing, ROI settings, ROIs, and groups from a JSON profile.")
        self._set_help(self.spectral_cube_slider, "Choose the reference spectral cube.")
        self._set_help(self.spectral_cube_spin, "Reference spectral cube number.")
        self._set_help(self.wavelength_slider, "Choose the reference wavelength.")
        self._set_help(self.wavelength_spin, "Reference wavelength in nanometers.")
        self._set_help(self.reference_auto_button, "Auto: use the best wavelength in the current spectral cube as the reference image.")
        self._set_help(self.reference_manual_button, "Manual: store the current spectral cube and wavelength as the manual reference image.")
        self._set_help(self.chromatic_apply_check, "Apply the saved chromatic transform models so reference ROIs and mask are propagated to non-reference images.")
        self._set_help(self.chromatic_sample_count_spin, "Odd number of spectral images to sample across the stack for the radial chromatic workflow.")
        self._set_help(self.chromatic_feature_count_spin, "Choose 5, 15, or 30 editable spatial reference points to mark on each sampled image.")
        self._set_help(self.chromatic_start_button, "Edit: enter chromatic reference-point editing mode on the current sampled image.")
        self._set_help(
            self.chromatic_grid_button,
            "Grid area: show and resize the rectangle reference points are searched within, instead of always "
            "using the whole image (helps keep points off rotated/blank image edges).",
        )
        self._set_help(self.chromatic_grid_reset_button, "Reset the chromatic search area back to the automatic full-image area.")
        self._set_help(self.chromatic_auto_button, "Automatic ref.points finder: detect the chromatic reference points on the sampled images and track them across the wavelength stack.")
        self._set_help(self.chromatic_reference_points_all_button, "Show all chromatic reference points across the sampled wavelengths. When linked, they are transformed into the current image space.")
        self._set_help(self.chromatic_prev_button, "Go to the previous sampled wavelength image.")
        self._set_help(self.chromatic_next_button, "Go to the next sampled wavelength image.")
        self._set_help(self.chromatic_landmark_clear_button, "Clear all saved chromatic reference points.")
        self._set_help(
            self.chromatic_landmark_id_spin,
            "Active Ref.point ID. PageUp/PageDown switches between reference points while editing. Shift+PageUp/PageDown switches wavelength images globally.",
        )
        self._set_help(self.chromatic_subpixel_precision_combo, "Sub.px: choose the chromatic point refinement level. 1 = pixel, 4 = moderate, 9 = finer.")
        self._set_help(
            self.chromatic_landmark_kind_combo,
            "Track: Corners = small, precise features (can get lost as focus blur grows toward longer wavelengths). "
            "Spots = particle centroids (coarser, more robust to that blur). Both = try both per step.",
        )
        self._set_help(
            self.chromatic_landmark_model_combo,
            "Model: Similarity = uniform scale + rotation + shift only, matching real lateral chromatic aberration "
            "(recommended). Affine = also allows shear/unequal x-y scale, which a handful of noisy points can pick "
            "up spuriously and worsen the correction near the image center.",
        )
        self._set_help(self.chromatic_transform_button, "Estimate chromatic transforms or clear saved chromatic transforms.")
        self._set_help(
            self.chromatic_landmark_export_button,
            "Export: save every marked reference point (raw position, reference position, and -- once transforms "
            "are estimated -- the model's predicted position and residual) to a CSV file, for analyzing the "
            "residual pattern outside the app.",
        )
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
        self._set_help(self.background_ignore_roi_button, "Ignore the detected ROI area while estimating the illumination background.")
        self._set_help(self.background_ignore_mask_button, "Ignore masked pixels while estimating the illumination background.")
        self._set_help(
            self.background_exclusion_dilation_spin,
            "Grow the ROI/mask exclusion zone by this many pixels before estimating the background.",
            "Use this if a masked or ROI-excluded region's boundary doesn't line up exactly with the "
            "pixels it's meant to exclude (e.g. a hand-painted mask edge, or a rotation-fill black edge) "
            "— a few pixels of margin keeps stray edge pixels from pulling on the background estimate. "
            "Only affects background estimation, not the mask/ROI shown elsewhere.",
        )
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
        self._set_help(self.sample_diameter_spin, "Sample diameter in pixels.")
        self._set_help(self.reference_inner_diameter_spin, "Inner diameter of the reference ROI in pixels.")
        self._set_help(self.reference_outer_diameter_spin, "Outer diameter of the reference ROI in pixels.")
        self._set_help(self.array_rows_spin, "Expected number of ROI rows in the array.")
        self._set_help(self.array_cols_spin, "Expected number of ROI columns in the array.")
        self._set_help(self.array_spacing_spin, "Expected spacing between neighboring array ROIs in pixels.")
        self._set_help(self.ignore_marked_check, "Ignore pixels defined by the current mask controls and any loaded mask image.")
        self._set_help(
            self.detect_rois_button,
            "Mode A: automatically detect array ROIs. Requires the histogram highlight range "
            "(blue band) to cover your sample ROI intensities first, or you'll get 0 ROIs.",
            "Mode A: automatically detect array ROIs on the current reference image. Detection only "
            "searches within the histogram highlight range (the blue band on the histogram plot) — set "
            "that range to cover your sample ROI intensities before clicking this, or detection will report 0 ROIs.",
        )
        self._set_help(self.roi_corner_select_button, "Mode B: select the four array corners first, then fill the grid. Coming later.")
        self._set_help(self.reorder_rois_button, "Reorder detected ROIs by image position so the top-left ROI becomes ID 1.")
        self._set_help(self.clear_rois_button, "Remove all detected ROIs and groups from the current dataset.")
        self._set_help(self.clear_roi_selection_button, "Clear the current ROI selection.")
        self._set_help(self.show_rois_check, "Show or hide the ROI overlays.")
        self._set_help(self.bottom_roi_labels_button, "Show or hide ROI labels next to the ROI overlays. This works independently of manual ROI editing.")
        self._set_help(self.roi_editor_labels_button, "Show or hide ROI labels in the left panel. This works independently of manual ROI editing.")
        self._set_help(self.show_reference_check, "Show or hide the reference ROI overlays.")
        self._set_help(self.show_reference_points_check, "Show or hide chromatic reference points.")
        self._set_help(self.show_mask_check, "Show or hide the mask overlay.")
        self._set_help(
            self.show_highlight_check,
            "Show or hide the histogram highlight overlay. Note: the highlighted range is also the "
            "intensity range 'Auto-locate ROIs' searches within.",
            "Show or hide the histogram highlight overlay for the selected histogram range. This range "
            "is also used by 'Auto-locate ROIs' (Mode A) as the intensity bounds for sample ROI candidates — "
            "if it doesn't cover your sample ROI intensities, automatic ROI detection will find 0 ROIs.",
        )
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
        self._set_help(self.sample_color_button, "Choose the ROI overlay color.")
        self._set_help(self.reference_color_button, "Choose the reference ROI color.")
        self._set_help(self.highlight_color_button, "Choose the histogram-highlight overlay color.")
        self._set_help(self.mask_alpha_slider, "Mask transparency.")
        self._set_help(self.roi_alpha_slider, "ROI transparency.")
        self._set_help(self.reference_alpha_slider, "Reference ROI transparency.")
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
            self.roi_edit_action,
            "Enable manual ROI editing mode.",
            "ROI edit mode:\n"
            "Left-click: select an ROI\n"
            "Shift+Left-click: add an ROI to the selection\n"
            "Double-left-click outside an ROI: clear the selection\n"
            "Left-drag: draw a selection box\n"
            "Right-drag: move selected ROIs when Move is active\n"
            "Middle-drag: pan the image view\n"
            "Arrow keys: move selected ROIs while Move is active\n"
            "Shift+Arrow: select neighboring ROI in the array\n"
            "Ctrl+Arrow: move selected ROIs faster\n"
            "Ctrl+Shift+A: Add mode for the active shape template\n"
            "Ctrl+Shift+M: Move mode",
        )
        self._set_help(self.roi_add_action, "Add mode: click the image to place a new ROI from the active shape template.")
        self._set_help(self.roi_move_action, "Move selected ROIs by dragging or arrow keys.")
        self._set_help(self.remove_rois_action, "Remove the selected ROIs.")
        self._set_help(self.group_rois_action, "Group selected ROIs.")
        self._set_help(self.ungroup_rois_action, "Ungroup selected ROIs.")
        self._set_help(self.roi_list_cached_button, "Show only the ROIs that already have cached absorbance data.")
        self._set_help(self.analysis_preview_button, "Live preview: update the spectrum and sensorgram automatically when ROI selection changes.")
        self._set_help(self.shortcuts_action, "Show the main keyboard shortcuts.", shortcuts_text())
        self._set_help(self.reset_layout_action, "Restore default splitter sizes and panel states.")
        self._set_help(self.reset_dock_layout_action, "Restore default splitter sizes without changing panel visibility.")
        self._set_help(self.expand_left_panels_action, "Expand all left workflow panels.")
        self._set_help(self.collapse_left_panels_action, "Collapse all left workflow panels.")
        self._set_help(self.calculate_spectrum_action, "Calculate the absorbance spectrum for the current spectral cube and selected ROIs.")
        self._set_help(self.about_action, "Show basic app information.")
        self._set_help(self.analysis_roi_table_button, "Show or hide the ROI table.")


    def _on_rotate_tool_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_rotate_tool_toggled(checked)

    def _on_crop_tool_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_crop_tool_toggled(checked)

    def _on_measure_tool_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_measure_tool_toggled(checked)

    def _on_roi_edit_tool_toggled(self, checked: bool) -> None:
        if checked:
            reference_key = self._reference_image_key()
            if reference_key is not None and self._current_image_key != reference_key:
                self._set_current_spectral_cube_and_wavelength(int(reference_key[0]), float(reference_key[1]))
            self.mask_pencil_check.blockSignals(True)
            self.mask_pencil_check.setChecked(False)
            self.mask_pencil_check.blockSignals(False)
            self.rotate_action.blockSignals(True)
            self.rotate_action.setChecked(False)
            self.rotate_action.blockSignals(False)
            self.crop_action.blockSignals(True)
            self.crop_action.setChecked(False)
            self.crop_action.blockSignals(False)
            self.roi_array_action.blockSignals(True)
            self.roi_array_action.setChecked(False)
            self.roi_array_action.blockSignals(False)
            if not self._rois_visible:
                self.show_rois_check.blockSignals(True)
                self.show_rois_check.setChecked(True)
                self.show_rois_check.blockSignals(False)
                self._rois_visible = True
                self._refresh_view_toggle_icons()
                self._save_visual_preferences()
            self._active_tool = "roi"
            self._sync_roi_edit_capabilities()
            if self._is_current_reference_image():
                self._set_status_text("ROI editor active.")
            else:
                self._set_status_text("ROI inspect mode active.")
        elif self._active_tool == "roi":
            self._active_tool = None
            self.roi_add_action.blockSignals(True)
            self.roi_add_action.setChecked(False)
            self.roi_add_action.blockSignals(False)
            self.roi_add_action.setEnabled(False)
            self.roi_move_action.blockSignals(True)
            self.roi_move_action.setChecked(False)
            self.roi_move_action.blockSignals(False)
            self.roi_move_action.setEnabled(False)
            self.roi_array_action.blockSignals(True)
            self.roi_array_action.setChecked(False)
            self.roi_array_action.blockSignals(False)
            self.roi_array_action.setEnabled(False)
            self.remove_rois_action.setEnabled(False)
            self.group_rois_action.setEnabled(False)
            self.ungroup_rois_action.setEnabled(False)
            self._finalize_roi_edit_refresh()
        self._dragging_rois = False
        self._drag_anchor = None
        self._drag_original_positions.clear()
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._sync_roi_edit_capabilities()
        self._update_roi_overlays()
        self._update_status_hint()

    def _on_mask_pencil_toggled(self, checked: bool) -> None:
        self._mask_controller.on_pencil_toggled(checked)

    def _sync_mask_draw_mode_buttons(self) -> None:
        self._mask_controller.sync_draw_mode_buttons()

    def _set_mask_draw_mode(self, mode: str) -> None:
        self._mask_controller.set_draw_mode(mode)

    def _sync_roi_edit_capabilities(self) -> None:
        editable = self._active_tool == "roi" and self._is_current_reference_image()
        self.roi_add_action.setEnabled(editable)
        self.roi_move_action.setEnabled(editable)
        self.roi_array_action.setEnabled(editable)
        self.remove_rois_action.setEnabled(editable)
        self.group_rois_action.setEnabled(editable)
        self.ungroup_rois_action.setEnabled(editable)
        if not editable:
            self.roi_add_action.blockSignals(True)
            self.roi_add_action.setChecked(False)
            self.roi_add_action.blockSignals(False)
            self.roi_move_action.blockSignals(True)
            self.roi_move_action.setChecked(False)
            self.roi_move_action.blockSignals(False)
            self.roi_array_action.blockSignals(True)
            self.roi_array_action.setChecked(False)
            self.roi_array_action.blockSignals(False)
        self._sync_rotation_visibility()
        self._sync_crop_visibility()
        self._update_status_hint()

    def _on_roi_add_toggled(self, checked: bool) -> None:
        if checked:
            self.roi_move_action.blockSignals(True)
            self.roi_move_action.setChecked(False)
            self.roi_move_action.blockSignals(False)
            self.roi_array_action.blockSignals(True)
            self.roi_array_action.setChecked(False)
            self.roi_array_action.blockSignals(False)
        self._update_roi_overlays()
        self._update_status_hint()

    def _on_roi_move_toggled(self, checked: bool) -> None:
        if checked:
            self.roi_add_action.blockSignals(True)
            self.roi_add_action.setChecked(False)
            self.roi_add_action.blockSignals(False)
            self.roi_array_action.blockSignals(True)
            self.roi_array_action.setChecked(False)
            self.roi_array_action.blockSignals(False)
        if hasattr(self, "image_panel"):
            self.image_panel.raise_()
            if hasattr(self, "image_view") and self.image_view is not None:
                self.image_view.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                viewport = self.image_view.viewport()
                if viewport is not None:
                    viewport.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._update_roi_overlays()
        self._update_status_hint()

    def _on_roi_array_toggled(self, checked: bool) -> None:
        if checked:
            self.roi_add_action.blockSignals(True)
            self.roi_add_action.setChecked(False)
            self.roi_add_action.blockSignals(False)
            self.roi_move_action.blockSignals(True)
            self.roi_move_action.setChecked(False)
            self.roi_move_action.blockSignals(False)
        self._update_roi_overlays()
        self._update_status_hint()

    def _on_flip_horizontal_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_flip_horizontal_toggled(checked)

    def _on_flip_vertical_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_flip_vertical_toggled(checked)

    def _update_rotation_fill_button_tooltip(self) -> None:
        self._image_tools_controller.update_rotation_fill_button_tooltip()

    def _on_rotation_fill_dark_toggled(self, checked: bool) -> None:
        self._image_tools_controller.on_rotation_fill_dark_toggled(checked)

    def _handle_image_tool_settings_changed(self, status: str, *, preserve_view: bool = False) -> None:
        self._image_tools_controller.handle_image_tool_settings_changed(status, preserve_view=preserve_view)

    def _on_show_rois_toggled(self, checked: bool) -> None:
        self._rois_visible = checked
        self._update_roi_overlays()
        self._save_visual_preferences()

    def _on_show_roi_labels_toggled(self, checked: bool) -> None:
        self._roi_labels_visible = checked
        self.roi_editor_labels_button.blockSignals(True)
        self.roi_editor_labels_button.setChecked(checked)
        self.roi_editor_labels_button.blockSignals(False)
        self.bottom_roi_labels_button.blockSignals(True)
        self.bottom_roi_labels_button.setChecked(checked)
        self.bottom_roi_labels_button.blockSignals(False)
        self._update_roi_label_button_icon(bool(checked))
        self._update_roi_overlays()
        self._save_visual_preferences()

    def _on_roi_editor_show_labels_toggled(self, checked: bool) -> None:
        self._roi_labels_visible = checked
        self.bottom_roi_labels_button.blockSignals(True)
        self.bottom_roi_labels_button.setChecked(checked)
        self.bottom_roi_labels_button.blockSignals(False)
        self._update_roi_label_button_icon(bool(checked))
        self._update_roi_overlays()
        self._save_visual_preferences()

    def _on_show_reference_toggled(self, checked: bool) -> None:
        self._reference_visible = checked
        self._update_roi_overlays()
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
        self._normalize_display_units()
        current = str(self._state.preprocessing.display_units or "px")
        if current == "px":
            if not self._can_display_micrometers():
                self._set_status_text("Calibrate the ruler first before switching to micrometers.")
                self._refresh_unit_toggle_button()
                return
            self._state.preprocessing.display_units = "um"
        else:
            self._state.preprocessing.display_units = "px"
        self._sync_roi_detection_controls()
        self._update_roi_detection_labels()
        self._update_measurement_status_label()
        self._refresh_scale_bar_overlay()
        self._update_roi_table()
        self._save_processing_state_for_dataset()
        self._save_control_preferences()

    def _on_scale_bar_toggled(self, checked: bool) -> None:
        self._state.preprocessing.scale_bar_visible = bool(checked)
        self._refresh_scale_bar_overlay()
        self._save_processing_state_for_dataset()

    def _reset_rotation(self) -> None:
        self._image_tools_controller.reset_rotation()

    def _reset_crop(self) -> None:
        self._image_tools_controller.reset_crop()

    def _adjust_rotation(self, delta_deg: float) -> None:
        self._image_tools_controller.adjust_rotation(delta_deg)

    def _sync_rotation_tool(self) -> None:
        self._image_tools_controller.sync_rotation_tool()

    def _update_rotation_preview(self) -> None:
        return

    def _sync_rotation_visibility(self) -> None:
        self._image_tools_controller.sync_rotation_visibility()

    def _handle_global_page_shortcuts(self, event: QKeyEvent) -> bool:
        return self._shortcut_manager.handle_page_shortcuts(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if self._image_interaction.handle_event(watched, event):
            return True
        return super().eventFilter(watched, event)

    def _image_point_from_mouse_event(self, event) -> tuple[float, float] | None:
        return self._image_interaction._image_point_from_mouse_event(event)

    def _selection_drag_threshold(self) -> float:
        return self._image_interaction._selection_drag_threshold()

    def _find_roi_id_at(self, point: tuple[float, float]) -> int | None:
        nearest_id: int | None = None
        nearest_distance = float("inf")
        for roi in self._display_rois():
            distance = hypot(point[0] - roi.center_x, point[1] - roi.center_y)
            threshold = max(float(roi.sample_radius_px) * 1.25, 8.0)
            if distance <= threshold and distance < nearest_distance:
                nearest_id = roi.area_roi_id
                nearest_distance = distance
        return nearest_id

    def _roi_by_id(self, roi_id: int) -> AreaRoi | None:
        for roi in self._state.area_rois:
            if roi.area_roi_id == roi_id:
                return roi
        return None

    def _array_position_for_roi(self, roi_id: int) -> tuple[int, int] | None:
        rows = max(int(self._state.area_roi_settings.array_rows), 0)
        cols = max(int(self._state.area_roi_settings.array_cols), 0)
        if rows <= 0 or cols <= 0:
            return None
        index = roi_id - 1
        if index < 0 or index >= rows * cols:
            return None
        return index // cols, index % cols

    def _array_label_for_roi(self, roi_id: int) -> str | None:
        position = self._array_position_for_roi(roi_id)
        if position is None:
            return str(int(roi_id)) if int(roi_id) > 0 else None
        row, col = position
        cols = max(int(self._state.area_roi_settings.array_cols), 0)
        if cols <= 0:
            return str(int(roi_id)) if int(roi_id) > 0 else None
        return str(row * cols + col + 1)

    def _select_neighbor_roi(self, key: int) -> bool:
        rows = max(int(self._state.area_roi_settings.array_rows), 0)
        cols = max(int(self._state.area_roi_settings.array_cols), 0)
        if rows <= 0 or cols <= 0 or not self._state.area_rois:
            return False

        current_id = min(self._selected_roi_ids) if self._selected_roi_ids else 1
        current_position = self._array_position_for_roi(current_id)
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
        new_roi = self._roi_by_id(new_id)
        if new_roi is None:
            return False
        self._selected_roi_ids = {new_id}
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_selection_dependent_plots(prompt_live_preview=True)
        self._center_view_on_roi(new_roi)
        return True

    def _center_view_on_roi(self, roi: AreaRoi) -> None:
        if self._current_processed_image is None:
            return

        x_range, y_range = self.image_plot.vb.viewRange()
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        image_height, image_width = self._current_processed_image.shape[:2]

        half_width = view_width / 2.0
        half_height = view_height / 2.0
        center_x = float(np.clip(roi.center_x, half_width, max(float(image_width) - half_width, half_width)))
        center_y = float(np.clip(roi.center_y, half_height, max(float(image_height) - half_height, half_height)))

        self.image_plot.vb.setRange(
            xRange=(center_x - half_width, center_x + half_width),
            yRange=(center_y - half_height, center_y + half_height),
            padding=0.0,
        )

    def _center_view_on_landmark(self, landmark: ChromaticLandmarkObservation) -> None:
        self._chromatic_controller.center_view_on_landmark(landmark)

    def _select_chromatic_feature(self, feature_id: int, *, center_view: bool = True) -> bool:
        return self._chromatic_controller.select_feature(feature_id, center_view=center_view)

    def _move_selected_landmark(self, dx: float, dy: float) -> bool:
        return self._chromatic_controller.move_selected_landmark(dx, dy)

    def _switch_chromatic_feature(self, direction: int) -> bool:
        return self._chromatic_controller.switch_feature(direction)

    def _move_selected_rois(self, dx: float, dy: float) -> None:
        if not self._selected_roi_ids:
            return
        self._append_workflow_log(f"ROIs | move {len(self._selected_roi_ids)} by dx={dx:g}, dy={dy:g}", level="debug")
        self._prepare_undo_snapshot("Move ROIs")
        for roi in self._state.area_rois:
            if roi.area_roi_id not in self._selected_roi_ids:
                continue
            roi.center_x, roi.center_y = self._clamp_roi_position(roi, roi.center_x + dx, roi.center_y + dy)
        self._update_roi_overlays()
        self._mark_roi_edit_refresh_pending()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()

    def _add_roi_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROIs.")
            return
        self._push_undo_point("Add ROI")
        radius = float(max(self._state.area_roi_settings.sample_radius_px, 1))
        provisional = AreaRoi(
            area_roi_id=len(self._state.area_rois) + 1,
            center_x=point[0],
            center_y=point[1],
            sample_radius_px=radius,
            score=0.0,
        )
        provisional.center_x, provisional.center_y = self._clamp_roi_position(provisional, provisional.center_x, provisional.center_y)
        self._state.area_rois.append(provisional)
        self._selected_roi_ids = {provisional.area_roi_id}
        self._update_roi_overlays()
        self._update_roi_table()
        self._mark_roi_edit_refresh_pending()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI {provisional.area_roi_id}.")

    def _add_roi_array_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROI array.")
            return
        rows = int(self.array_rows_spin.value())
        cols = int(self.array_cols_spin.value())
        spacing = max(float(self._length_display_to_px(float(self.array_spacing_spin.value()))), 0.0)
        if rows <= 0 or cols <= 0 or spacing <= 0.0:
            self.status_label.setText("Set array rows, columns, and spacing before stamping an ROI array.")
            return
        radius = float(max(self._state.area_roi_settings.sample_radius_px, 1))
        image_height, image_width = self._current_processed_image.shape[:2]
        step = spacing
        start_x = float(point[0]) - (cols - 1) * step / 2.0
        start_y = float(point[1]) - (rows - 1) * step / 2.0
        self._push_undo_point("Add ROI array")
        next_id = len(self._state.area_rois) + 1
        new_rois: list[AreaRoi] = []
        for row in range(rows):
            for col in range(cols):
                cx = start_x + col * step
                cy = start_y + row * step
                cx = float(min(max(cx, radius), max(float(image_width - 1) - radius, radius)))
                cy = float(min(max(cy, radius), max(float(image_height - 1) - radius, radius)))
                roi = AreaRoi(area_roi_id=next_id, center_x=cx, center_y=cy, sample_radius_px=radius, score=0.0)
                new_rois.append(roi)
                next_id += 1
        self._state.area_rois.extend(new_rois)
        self._selected_roi_ids = {s.area_roi_id for s in new_rois}
        self._update_roi_overlays()
        self._update_roi_table()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI array: {len(new_rois)} ROIs.")

    def _clamp_roi_position(self, roi: AreaRoi, x: float, y: float) -> tuple[float, float]:
        if self._current_processed_image is None:
            return x, y
        image_height, image_width = self._current_processed_image.shape[:2]
        radius = max(float(roi.sample_radius_px), 1.0)
        clamped_x = min(max(x, radius), max(float(image_width - 1) - radius, radius))
        clamped_y = min(max(y, radius), max(float(image_height - 1) - radius, radius))
        return clamped_x, clamped_y

    def _request_roi_metrics_refresh(
        self,
        *,
        save_after: bool,
        status_text: str | None = None,
        refresh_histogram: bool = True,
    ) -> bool:
        if self._current_processed_image is None or not self._state.area_rois or not self._is_current_reference_image():
            return False
        image_key = self._current_image_key
        if image_key is None:
            return False
        self._roi_metrics_request_id += 1
        request_id = self._roi_metrics_request_id
        image = self._current_processed_image
        settings = deepcopy(self._state.area_roi_settings)
        rois = deepcopy(self._state.area_rois)
        worker = FunctionWorker(_refresh_roi_metrics_task, image, settings, rois, self._current_external_mask())
        self._begin_busy("Refreshing ROI metrics...")
        self._append_workflow_log("ROI metrics refresh start", level="info")
        worker.signals.result.connect(
            lambda refreshed_rois,
            request_id=request_id,
            image_key=image_key,
            save_after=save_after,
            status_text=status_text,
            refresh_histogram=refresh_histogram: self._on_roi_metrics_ready(
                request_id,
                image_key,
                refreshed_rois,
                save_after,
                status_text,
                refresh_histogram,
            )
        )
        worker.signals.error.connect(lambda message: self._on_roi_metrics_failed(message))
        self._thread_pool.start(worker)
        return True

    def _on_roi_metrics_ready(
        self,
        request_id: int,
        image_key: tuple[int, float],
        refreshed_rois: list[AreaRoi],
        save_after: bool,
        status_text: str | None,
        refresh_histogram: bool,
    ) -> None:
        self._roi_table_controller._on_roi_metrics_ready(request_id, image_key, refreshed_rois, save_after, status_text, refresh_histogram)

    def _on_roi_metrics_failed(self, message: str) -> None:
        self._roi_table_controller._on_roi_metrics_failed(message)

    def _refresh_roi_metrics_if_enabled(self) -> bool:
        if not self.roi_editor_section.is_applied() or self._current_processed_image is None or not self._is_current_reference_image():
            return False
        return self._request_roi_metrics_refresh(save_after=False, refresh_histogram=False)

    def _mark_roi_edit_refresh_pending(self) -> None:
        if self._active_tool == "roi":
            self._commit_prepared_undo_snapshot()
            self._roi_edit_refresh_pending = True
            self._save_processing_state_for_dataset()
            self._schedule_processing_state_save()
            self.status_label.setText("ROI positions updated. Fit refresh is deferred until Edit ROIs is turned off.")

    def _finalize_roi_edit_refresh(self) -> None:
        if not self._roi_edit_refresh_pending:
            return
        self._roi_edit_refresh_pending = False
        self._commit_prepared_undo_snapshot()
        self._invalidate_background_profile_cache()
        if self._showing_background_profile_main:
            self._update_background_profile_preview()
        if self._request_roi_metrics_refresh(
            save_after=True,
            status_text="ROI fit metrics refreshed after leaving Edit ROIs.",
            refresh_histogram=True,
        ):
            return
        self._schedule_histogram_refresh()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self.status_label.setText("ROI fit metrics refreshed after leaving Edit ROIs.")

    def _refresh_histogram_if_available(self) -> None:
        self._overlay_manager._refresh_histogram_if_available()

    def _apply_histogram_log_mode(self, *, refresh: bool = True) -> None:
        self._plot_manager.apply_histogram_log_mode(refresh=refresh)

    def _autoscale_histogram_after_startup(self) -> None:
        if self._current_processed_image is None or not hasattr(self, "histogram_plot"):
            return
        if not self._histogram_startup_autoscale_pending:
            return
        if self.histogram_plot.width() < 20 or self.histogram_plot.height() < 20:
            self._histogram_startup_autoscale_attempts += 1
            if self._histogram_startup_autoscale_attempts <= 10:
                QTimer.singleShot(100, self._autoscale_histogram_after_startup)
            return
        view_box = self.histogram_plot.getViewBox()
        if view_box is None:
            return
        self._histogram_startup_autoscale_pending = False
        self._histogram_startup_autoscale_attempts = 0
        self._plot_manager.refresh_histogram_if_available()
        plot_item = self.histogram_plot.getPlotItem()
        if plot_item is not None:
            plot_item.autoBtnClicked()
        if self._histogram_log_y_enabled:
            self._clamp_histogram_log_range()

    def _on_histogram_y_scale_toggled(self, checked: bool) -> None:
        self._overlay_manager._on_histogram_y_scale_toggled(checked)

    def _histogram_log_y_max(self) -> float:
        return self._plot_manager.histogram_log_y_max()

    def _clamp_histogram_log_range(self) -> None:
        self._plot_manager.clamp_histogram_log_range()

    def _on_histogram_view_range_changed(self, *_args) -> None:
        self._overlay_manager._on_histogram_view_range_changed(*_args)

    def _set_spectrum_summary_text(self, text: str) -> None:
        self._plot_manager.set_spectrum_summary_text(text)

    def _sensorgram_signature_for_selection(
        self,
        spectral_cubes: list[int],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        return self._analysis_controller._sensorgram_signature_for_selection(spectral_cubes, selected_roi_ids, selected_source_rois)

    def _sensorgram_spectral_cube_payload_signature(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        return self._analysis_controller._sensorgram_spectral_cube_payload_signature(spectral_cube_index, selected_roi_ids, selected_source_rois)

    def _cached_sensorgram_spectral_cube_payload(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        return self._analysis_controller._cached_sensorgram_spectral_cube_payload(spectral_cube_index, selected_roi_ids, selected_source_rois)

    def _schedule_sensorgram_refresh(self) -> None:
        self._analysis_controller._schedule_sensorgram_refresh()

    def _refresh_sensorgram(self) -> None:
        self._analysis_controller._refresh_sensorgram()

    def _mark_absorbance_spectrum_dirty(self) -> None:
        self._analysis_controller._mark_absorbance_spectrum_dirty()

    def _selected_spectrum_roi_ids(self) -> tuple[int, ...]:
        return self._analysis_controller._selected_spectrum_roi_ids()

    def _selected_source_rois_snapshot(self) -> list[AreaRoi]:
        return self._analysis_controller._selected_source_rois_snapshot()

    def _spectrum_selection_label(self) -> str:
        return self._analysis_controller._spectrum_selection_label()

    def _clear_absorbance_spectrum(self, summary_text: str) -> None:
        self._plot_manager.clear_absorbance_spectrum(summary_text)

    def _clear_spectrum_series_items(self) -> None:
        self._plot_manager.clear_spectrum_series_items()

    def _roi_spectrum_color(self, roi_id: int) -> QColor:
        return self._plot_manager.roi_spectrum_color(roi_id)

    def _add_spectrum_series(
        self,
        *,
        roi_id: int,
        result: AbsorbanceSpectrumResult,
        label: str | None = None,
        highlighted: bool = False,
        dimmed: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None] | None:
        return self._plot_manager.add_spectrum_series(
            roi_id=roi_id,
            result=result,
            label=label,
            highlighted=highlighted,
            dimmed=dimmed,
        )

    def _analysis_fit_result_from_spectrum(self, result: AbsorbanceSpectrumResult) -> FitResult | None:
        return self._plot_manager.analysis_fit_result_from_spectrum(result)

    def _update_single_spectral_cube_sensorgram(self, metric_value: float | None, metric_signal: float | None) -> None:
        self._plot_manager.update_single_spectral_cube_sensorgram(metric_value, metric_signal)

    def _schedule_absorbance_spectrum_refresh(self) -> None:
        self._analysis_controller._schedule_absorbance_spectrum_refresh()

    def _start_absorbance_spectrum_preparation(
        self,
        signature: tuple[object, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> None:
        self._analysis_controller._start_absorbance_spectrum_preparation(signature, selected_source_rois)

    def _on_absorbance_spectrum_payload_ready(
        self,
        request_id: int,
        expected_signature: tuple[object, ...],
        prepared: tuple[tuple[object, ...], tuple[object, ...]] | None,
    ) -> None:
        self._analysis_controller._on_absorbance_spectrum_payload_ready(request_id, expected_signature, prepared)

    def _on_absorbance_spectrum_payload_failed(self, request_id: int, message: str) -> None:
        self._analysis_controller._on_absorbance_spectrum_payload_failed(request_id, message)

    def _cached_absorbance_result_for_selection(
        self,
        signature: tuple[object, ...],
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> AbsorbanceSpectrumResult | None:
        return self._analysis_controller._cached_absorbance_result_for_selection(signature, selected_roi_ids, selected_source_rois)

    def _absorbance_spectrum_signature(self) -> tuple[object, ...] | None:
        return self._analysis_controller._absorbance_spectrum_signature()

    def _roi_absorbance_signature(self, roi: AreaRoi) -> tuple[object, ...] | None:
        return self._analysis_controller._roi_absorbance_signature(roi)

    def _roi_has_cached_absorbance(self, roi: AreaRoi) -> bool:
        return self._analysis_controller._roi_has_cached_absorbance(roi)

    @staticmethod
    def _analysis_cache_signature_to_json(value):
        return AnalysisController._analysis_cache_signature_to_json(value)

    @staticmethod
    def _analysis_cache_signature_from_json(value):
        return AnalysisController._analysis_cache_signature_from_json(value)

    @staticmethod
    def _absorbance_spectral_cube_signature(signature: tuple[object, ...] | None) -> tuple[object, ...] | None:
        return AnalysisController._absorbance_spectral_cube_signature(signature)

    @staticmethod
    def _absorbance_result_covers_roi_ids(result: AbsorbanceSpectrumResult, selected_roi_ids: tuple[int, ...]) -> bool:
        return AnalysisController._absorbance_result_covers_roi_ids(result, selected_roi_ids)

    def _cached_absorbance_result_from_roi_cache(
        self,
        selected_source_rois: list[AreaRoi],
    ) -> AbsorbanceSpectrumResult | None:
        return self._analysis_controller._cached_absorbance_result_from_roi_cache(selected_source_rois)

    @staticmethod

    def _deserialize_absorbance_result(payload) -> AbsorbanceSpectrumResult:
        from lspr_imaging_app.gui.analysis_controller import AnalysisController
        return AnalysisController._deserialize_absorbance_result(payload)

    @staticmethod

    def _serialize_sensorgram_result(result: SensorgramComputationResult) -> dict:
        from lspr_imaging_app.gui.analysis_controller import AnalysisController
        return AnalysisController._serialize_sensorgram_result(result)

    @staticmethod

    def _deserialize_sensorgram_result(payload) -> SensorgramComputationResult:
        from lspr_imaging_app.gui.analysis_controller import AnalysisController
        return AnalysisController._deserialize_sensorgram_result(payload)

    def _analysis_cache_payload(self) -> dict:
        return self._analysis_controller._analysis_cache_payload()

    def _restore_analysis_caches(self, payload: dict | None) -> None:
        self._analysis_controller._restore_analysis_caches(payload)

    def _prepare_absorbance_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple[int, ...],
        selected_source_rois: list[AreaRoi],
    ) -> tuple[object, ...] | None:
        return self._analysis_controller._prepare_absorbance_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois)

    def _prepare_fast_spectrum_payload_for_spectral_cube(
        self,
        spectral_cube_index: int,
        selected_roi_ids: tuple,
        selected_source_rois: list,
    ) -> tuple | None:
        return self._analysis_controller._prepare_fast_spectrum_payload_for_spectral_cube(spectral_cube_index, selected_roi_ids, selected_source_rois)

    def _prepare_absorbance_spectrum_payload(
        self,
        selected_source_rois: list[AreaRoi] | None = None,
    ) -> tuple[tuple[object, ...], tuple[object, ...]] | None:
        return self._analysis_controller._prepare_absorbance_spectrum_payload(selected_source_rois)

    def _toggle_analysis_live_preview(self) -> None:
        self._analysis_controller._toggle_analysis_live_preview()

    def _refresh_absorbance_spectrum(self) -> None:
        self._analysis_controller._refresh_absorbance_spectrum()

    def _available_analysis_spectral_cubes(self) -> list[int]:
        return self._analysis_controller._available_analysis_spectral_cubes()

    def _on_analysis_fit_settings_changed(self, *_args) -> None:
        self._analysis_controller._on_analysis_fit_settings_changed(*_args)

    def _on_analysis_spectral_cube_range_changed(self, *_args) -> None:
        self._analysis_controller._on_analysis_spectral_cube_range_changed(*_args)

    def _calculate_sensorgram_for_range(self) -> None:
        self._analysis_controller._calculate_sensorgram_for_range()

    def _stop_sensorgram_calculation(self) -> None:
        self._analysis_controller._stop_sensorgram_calculation()

    def _on_sensorgram_partial_result(
        self,
        request_id: int,
        total_count: int,
        point: SensorgramPointResult,
    ) -> None:
        self._analysis_controller._on_sensorgram_partial_result(request_id, total_count, point)

    def _on_sensorgram_ready(self, request_id: int, result: SensorgramComputationResult) -> None:
        self._analysis_controller._on_sensorgram_ready(request_id, result)

    def _on_sensorgram_failed(self, request_id: int, message: str) -> None:
        self._analysis_controller._on_sensorgram_failed(request_id, message)

    def _start_pending_absorbance_spectrum_refresh(self, *, reuse_busy: bool = False) -> None:
        self._analysis_controller._start_pending_absorbance_spectrum_refresh(reuse_busy=reuse_busy)

    def _on_absorbance_spectrum_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        result: AbsorbanceSpectrumResult,
    ) -> None:
        self._analysis_controller._on_absorbance_spectrum_ready(request_id, signature, result)

    def _on_absorbance_spectrum_failed(self, request_id: int, message: str) -> None:
        self._analysis_controller._on_absorbance_spectrum_failed(request_id, message)

    def _apply_absorbance_spectrum_result(self, result: AbsorbanceSpectrumResult) -> None:
        return self._analysis_controller._apply_absorbance_spectrum_result(result)

    def _update_color_button_styles(self) -> None:
        self.mask_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._mask_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.sample_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._sample_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.reference_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._reference_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.highlight_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._highlight_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self.scale_bar_color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._scale_bar_visual_color.name()}; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0; }}"
        )
        self._update_histogram_region_styles()
        if hasattr(self, "roi_table") and self.roi_table.columnCount() >= 5:
            self._refresh_roi_table_headers()
            if self.roi_table.isVisible():
                self._update_roi_table()

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
        self._image_tools_controller.sync_crop_tool(image_shape)

    def _sync_chromatic_grid_tool(self, image_shape: tuple[int, int]) -> None:
        self._chromatic_controller.sync_grid_tool(image_shape)

    def _sync_crop_visibility(self) -> None:
        self._image_tools_controller.sync_crop_visibility()

    def _next_roi_id(self) -> str:
        self._roi_id_counter += 1
        return f"roi_{self._roi_id_counter}"

    def _reset_roi_id_counter_from_state(self) -> None:
        max_index = 0
        for roi in self._state.rois:
            if roi.roi_id.startswith("roi_"):
                suffix = roi.roi_id[4:]
                if suffix.isdigit():
                    max_index = max(max_index, int(suffix))
        self._roi_id_counter = max_index

    def _active_rectangle_template(self) -> RoiDefinition:
        if self._roi_editor_mode == "circles":
            sample_diameter = max(float(self._length_display_to_px(float(self.sample_diameter_spin.value()))), 2.0)
            reference_inner_diameter = max(float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))), 0.0)
            reference_outer_diameter = max(
                float(self._length_display_to_px(float(self.reference_outer_diameter_spin.value()))),
                reference_inner_diameter,
            )
            padding = max((reference_inner_diameter - sample_diameter) / 2.0, 0.0)
            width = max((reference_outer_diameter - reference_inner_diameter) / 2.0, 0.0)
            return RoiDefinition(
                roi_id="circle_template",
                name="Circle ROI",
                shape="circle",
                center_x=float(self._rectangle_template.center_x),
                center_y=float(self._rectangle_template.center_y),
                size_x=sample_diameter,
                size_y=sample_diameter,
                background_padding_px=padding,
                background_width_px=width,
                enabled=True,
            )
        self._rectangle_template.shape = "rectangle"
        return self._rectangle_template

    def _ensure_rectangle_roi(self) -> pg.RectROI | None:
        if self._current_processed_image is None:
            return None
        roi = self._active_rectangle_template()
        image_height, image_width = self._current_processed_image.shape[:2]
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        if self._rectangle_roi is None:
            if str(roi.shape).lower() in {"circle", "ellipse"}:
                self._rectangle_roi = pg.EllipseROI(
                    [x, y],
                    [width, height],
                    pen=pg.mkPen("#f59e0b", width=2),
                    movable=True,
                    resizable=True,
                    rotatable=False,
                )
                for _h in list(self._rectangle_roi.handles):
                    if _h["type"] == "r":
                        self._rectangle_roi.removeHandle(_h["item"])
            else:
                self._rectangle_roi = pg.RectROI(
                    [x, y],
                    [width, height],
                    pen=pg.mkPen("#f59e0b", width=2),
                    movable=True,
                    resizable=True,
                    rotatable=False,
                    sideScalers=True,
                )
            self._rectangle_roi.handleSize = 10
            self._rectangle_roi.sigRegionChanged.connect(lambda *_args: self._rectangle_roi_changed(finished=False))
            self._rectangle_roi.sigRegionChangeFinished.connect(lambda *_args: self._rectangle_roi_changed(finished=True))
            self.image_plot.addItem(self._rectangle_roi)
            self._rectangle_roi.setZValue(1.2)
        self._suspend_rectangle_sync = True
        self._rectangle_roi.setPos((x, y))
        self._rectangle_roi.setSize((width, height))
        self._suspend_rectangle_sync = False
        return self._rectangle_roi

    def _sync_rectangle_roi_visibility(self) -> None:
        if self._rectangle_roi is None:
            self._ensure_rectangle_roi()
        if self._rectangle_roi is not None:
            self._rectangle_roi.setVisible(
                self._roi_editor_mode == "rectangles"
                and not self._showing_background_profile_main
                and self._current_processed_image is not None
            )
        self._update_guide_overlays()

    def _sync_rectangle_roi_from_definition(self) -> None:
        roi = self._active_rectangle_template()
        if self._rectangle_roi is None:
            self._ensure_rectangle_roi()
        if self._rectangle_roi is None:
            return
        image_height, image_width = self._current_processed_image.shape[:2] if self._current_processed_image is not None else (0, 0)
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        self._suspend_rectangle_sync = True
        self._rectangle_roi.setPos((x, y))
        self._rectangle_roi.setSize((width, height))
        self._suspend_rectangle_sync = False
        self._update_rectangle_roi_summary()

    def _rectangle_roi_changed(self, *, finished: bool) -> None:
        if self._suspend_rectangle_sync or self._rectangle_roi is None:
            return
        pos = self._rectangle_roi.pos()
        size = self._rectangle_roi.size()
        roi = self._active_rectangle_template()
        roi.shape = str(roi.shape or "rectangle")
        roi.center_x = float(pos.x() + size.x() / 2.0)
        roi.center_y = float(pos.y() + size.y() / 2.0)
        roi.size_x = max(float(size.x()), 2.0)
        roi.size_y = max(float(size.y()), 2.0)
        if self._roi_editor_mode == "circles":
            self._rectangle_template.center_x = roi.center_x
            self._rectangle_template.center_y = roi.center_y
            self.sample_diameter_spin.blockSignals(True)
            self.sample_diameter_spin.setValue(self._length_px_to_display(float(max(roi.size_x, roi.size_y))))
            self.sample_diameter_spin.blockSignals(False)
            self._state.area_roi_settings.sample_radius_px = max(float(roi.size_x), 1.0) / 2.0
            self._state.area_roi_settings.reference_inner_radius_px = max(
                float(roi.size_x) / 2.0 + float(roi.background_padding_px),
                float(roi.size_x) / 2.0,
            )
            self._state.area_roi_settings.reference_outer_radius_px = max(
                float(self._state.area_roi_settings.reference_inner_radius_px) + float(roi.background_width_px),
                float(self._state.area_roi_settings.reference_inner_radius_px),
            )
        if finished:
            self._push_undo_point("Rectangle ROI")
            self._save_processing_state_for_dataset()
        if self._roi_editor_mode == "circles":
            self._update_roi_detection_labels(sync_controls=False)
        else:
            self._update_rectangle_roi_controls(sync_roi=False)
        self._update_rectangle_roi_summary()

    def _update_rectangle_roi_controls(self, *, sync_roi: bool = True) -> None:
        roi = self._active_rectangle_template()
        if sync_roi:
            self._sync_rectangle_roi_from_definition()
        self.rectangle_name_edit.blockSignals(True)
        self.rectangle_name_edit.setText(roi.name)
        self.rectangle_name_edit.blockSignals(False)
        self.rectangle_width_spin.blockSignals(True)
        self.rectangle_height_spin.blockSignals(True)
        self.rectangle_padding_spin.blockSignals(True)
        self.rectangle_background_width_spin.blockSignals(True)
        self.rectangle_width_spin.setValue(self._length_px_to_display(float(roi.size_x)))
        self.rectangle_height_spin.setValue(self._length_px_to_display(float(roi.size_y)))
        self.rectangle_padding_spin.setValue(self._length_px_to_display(float(roi.background_padding_px)))
        self.rectangle_background_width_spin.setValue(self._length_px_to_display(float(roi.background_width_px)))
        self.rectangle_width_spin.blockSignals(False)
        self.rectangle_height_spin.blockSignals(False)
        self.rectangle_padding_spin.blockSignals(False)
        self.rectangle_background_width_spin.blockSignals(False)

    def _update_rectangle_roi_summary(self) -> None:
        roi = self._active_rectangle_template()
        if self._roi_editor_mode == "circles":
            self.rectangle_summary_label.setText(
                (
                    f"Template center=({self._length_px_to_display(float(roi.center_x)):.1f}, "
                    f"{self._length_px_to_display(float(roi.center_y)):.1f}) "
                    f"ROI D={self._length_px_to_display(float(roi.size_x)):.1f} "
                    f"Ring pad={self._length_px_to_display(float(roi.background_padding_px)):.1f} "
                    f"Ring width={self._length_px_to_display(float(roi.background_width_px)):.1f}"
                )
            )
        else:
            self.rectangle_summary_label.setText(
                (
                    f"Center=({self._length_px_to_display(float(roi.center_x)):.1f}, "
                    f"{self._length_px_to_display(float(roi.center_y)):.1f}) "
                    f"Size={self._length_px_to_display(float(roi.size_x)):.1f}x{self._length_px_to_display(float(roi.size_y)):.1f}"
                )
            )

    def _commit_rectangle_roi_edits(self) -> None:
        roi = self._active_rectangle_template()
        if self._roi_editor_mode == "circles":
            roi.name = roi.name.strip() or "Circle ROI"
            roi.size_x = max(float(self._length_display_to_px(float(self.sample_diameter_spin.value()))), 2.0)
            roi.size_y = float(roi.size_x)
            roi.background_padding_px = max(
                float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))) - float(roi.size_x),
                0.0,
            ) / 2.0
            roi.background_width_px = max(
                float(self._length_display_to_px(float(self.reference_outer_diameter_spin.value())))
                - float(self._length_display_to_px(float(self.reference_inner_diameter_spin.value()))),
                0.0,
            ) / 2.0
            self._update_roi_detection_labels(sync_controls=False)
        else:
            roi.name = self.rectangle_name_edit.text().strip() or roi.name
            roi.size_x = max(float(self._length_display_to_px(float(self.rectangle_width_spin.value()))), 2.0)
            roi.size_y = max(float(self._length_display_to_px(float(self.rectangle_height_spin.value()))), 2.0)
            roi.background_padding_px = max(float(self._length_display_to_px(float(self.rectangle_padding_spin.value()))), 0.0)
            roi.background_width_px = max(float(self._length_display_to_px(float(self.rectangle_background_width_spin.value()))), 0.0)
        self._sync_rectangle_roi_from_definition()
        self._save_processing_state_for_dataset()

    def _create_rectangle_stamp(self, roi: RoiDefinition) -> pg.RectROI | None:
        if self._current_processed_image is None:
            return None
        image_height, image_width = self._current_processed_image.shape[:2]
        width = max(float(roi.size_x), 2.0)
        height = max(float(roi.size_y), 2.0)
        x = float(np.clip(float(roi.center_x) - width / 2.0, 0.0, max(float(image_width) - width, 0.0)))
        y = float(np.clip(float(roi.center_y) - height / 2.0, 0.0, max(float(image_height) - height, 0.0)))
        if str(roi.shape).lower() in {"circle", "ellipse"}:
            item = pg.EllipseROI(
                [x, y],
                [width, height],
                pen=pg.mkPen("#f59e0b", width=2),
                movable=True,
                resizable=True,
                rotatable=False,
            )
            for _h in list(item.handles):
                if _h["type"] == "r":
                    item.removeHandle(_h["item"])
        else:
            item = pg.RectROI(
                [x, y],
                [width, height],
                pen=pg.mkPen("#f59e0b", width=2),
                movable=True,
                resizable=True,
                rotatable=False,
                sideScalers=True,
            )
        item.handleSize = 10
        item.sigRegionChanged.connect(lambda *_args, roi=roi: self._rectangle_stamp_changed(roi, finished=False))
        item.sigRegionChangeFinished.connect(lambda *_args, roi=roi: self._rectangle_stamp_changed(roi, finished=True))
        self.image_plot.addItem(item)
        item.setZValue(1.15)
        return item

    def _sync_rectangle_stamp_overlays(self) -> None:
        for item in self._rectangle_stamp_items:
            self.image_plot.removeItem(item)
        self._rectangle_stamp_items.clear()
        for inner_c, outer_c in self._rectangle_stamp_ring_items.values():
            if inner_c is not None:
                self.image_plot.removeItem(inner_c)
            if outer_c is not None:
                self.image_plot.removeItem(outer_c)
        self._rectangle_stamp_ring_items.clear()
        if self._current_processed_image is None:
            return
        theta = np.linspace(0, 2 * np.pi, 200, dtype=np.float32)
        theta = np.append(theta, theta[0])
        for roi in self._state.rois:
            if str(roi.shape) not in {"rectangle", "circle", "ellipse"}:
                continue
            item = self._create_rectangle_stamp(roi)
            if item is None:
                continue
            setattr(item, "roi_id", roi.roi_id)
            self._rectangle_stamp_items.append(item)
            if str(roi.shape).lower() in {"circle", "ellipse"} and float(roi.background_width_px) > 0:
                inner_r = float(roi.size_x) / 2.0 + float(roi.background_padding_px)
                outer_r = inner_r + float(roi.background_width_px)
                inner_curve: pg.PlotCurveItem | None = None
                if inner_r > 0:
                    inner_curve = pg.PlotCurveItem()
                    inner_curve.setSkipFiniteCheck(True)
                    inner_curve.setData(
                        float(roi.center_x) + inner_r * np.cos(theta),
                        float(roi.center_y) + inner_r * np.sin(theta),
                    )
                    inner_curve.setPen(pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DashLine))
                    self.image_plot.addItem(inner_curve, ignoreBounds=True)
                    inner_curve.setZValue(1.14)
                outer_curve = pg.PlotCurveItem()
                outer_curve.setSkipFiniteCheck(True)
                outer_curve.setData(
                    float(roi.center_x) + outer_r * np.cos(theta),
                    float(roi.center_y) + outer_r * np.sin(theta),
                )
                outer_curve.setPen(pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DotLine))
                self.image_plot.addItem(outer_curve, ignoreBounds=True)
                outer_curve.setZValue(1.14)
                self._rectangle_stamp_ring_items[roi.roi_id] = (inner_curve, outer_curve)
        self._update_rectangle_stamp_visuals()

    def _update_rectangle_stamp_ring_position(self, roi: "RoiDefinition") -> None:
        ring_pair = self._rectangle_stamp_ring_items.get(roi.roi_id)
        if ring_pair is None:
            return
        inner_curve, outer_curve = ring_pair
        theta = np.linspace(0, 2 * np.pi, 200, dtype=np.float32)
        theta = np.append(theta, theta[0])
        inner_r = float(roi.size_x) / 2.0 + float(roi.background_padding_px)
        outer_r = inner_r + float(roi.background_width_px)
        cx, cy = float(roi.center_x), float(roi.center_y)
        if inner_curve is not None:
            inner_curve.setData(cx + inner_r * np.cos(theta), cy + inner_r * np.sin(theta))
        if outer_curve is not None:
            outer_curve.setData(cx + outer_r * np.cos(theta), cy + outer_r * np.sin(theta))

    def _update_rectangle_stamp_visuals(self) -> None:
        selected_pen = pg.mkPen("#fbbf24", width=2.8)
        normal_pen = pg.mkPen("#f59e0b", width=2)
        selected_ring_inner = pg.mkPen("#fbbf24", width=1.4, style=Qt.PenStyle.DashLine)
        selected_ring_outer = pg.mkPen("#fbbf24", width=1.4, style=Qt.PenStyle.DotLine)
        normal_ring_inner = pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DashLine)
        normal_ring_outer = pg.mkPen("#f59e0b", width=1.2, style=Qt.PenStyle.DotLine)
        for item in self._rectangle_stamp_items:
            roi_id = str(getattr(item, "roi_id", ""))
            selected = roi_id in self._selected_rectangle_roi_ids
            item.setPen(selected_pen if selected else normal_pen)
            ring_pair = self._rectangle_stamp_ring_items.get(roi_id)
            if ring_pair is not None:
                inner_c, outer_c = ring_pair
                if inner_c is not None:
                    inner_c.setPen(selected_ring_inner if selected else normal_ring_inner)
                if outer_c is not None:
                    outer_c.setPen(selected_ring_outer if selected else normal_ring_outer)

    def _rectangle_stamp_at(self, point: tuple[float, float]) -> RoiDefinition | None:
        x = float(point[0])
        y = float(point[1])
        for roi in reversed(self._state.rois):
            if str(roi.shape) not in {"rectangle", "circle", "ellipse"}:
                continue
            left, top = roi_top_left_from_center(
                float(roi.center_x),
                float(roi.center_y),
                float(roi.size_x),
                float(roi.size_y),
                self._current_processed_image.shape if self._current_processed_image is not None else None,
            )
            right = left + float(roi.size_x)
            bottom = top + float(roi.size_y)
            if left <= x <= right and top <= y <= bottom:
                return roi
        return None

    def _select_rectangle_rois(self, roi_ids: set[str], *, additive: bool = False) -> None:
        if additive:
            self._selected_rectangle_roi_ids.update(roi_ids)
        else:
            self._selected_rectangle_roi_ids.clear()
            self._selected_rectangle_roi_ids.update(roi_ids)
        self._update_rectangle_stamp_visuals()

    def _clear_rectangle_roi_selection(self) -> None:
        if not self._selected_rectangle_roi_ids:
            return
        self._selected_rectangle_roi_ids.clear()
        self._update_rectangle_stamp_visuals()

    def _move_selected_rectangle_rois(self, dx: float, dy: float) -> None:
        if not self._selected_rectangle_roi_ids:
            return
        self._push_undo_point("Move ROIs")
        updated = False
        for roi in self._state.rois:
            if roi.roi_id not in self._selected_rectangle_roi_ids:
                continue
            moved = move_roi_from_template(
                roi,
                center_x=float(roi.center_x) + float(dx),
                center_y=float(roi.center_y) + float(dy),
                image_shape=self._current_processed_image.shape if self._current_processed_image is not None else None,
            )
            roi.center_x = moved.center_x
            roi.center_y = moved.center_y
            updated = True
        if updated:
            self._sync_rectangle_stamp_overlays()
            self._save_processing_state_for_dataset()
            self._schedule_processing_state_save()

    def _remove_selected_rectangle_rois(self) -> None:
        if not self._selected_rectangle_roi_ids:
            self.status_label.setText("Select ROI(s) first to remove them.")
            return
        self._push_undo_point("Remove ROIs")
        removed_count = len(self._selected_rectangle_roi_ids)
        self._state.rois = [
            roi for roi in self._state.rois if roi.roi_id not in self._selected_rectangle_roi_ids
        ]
        self._selected_rectangle_roi_ids.clear()
        self._sync_rectangle_stamp_overlays()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Removed {removed_count} selected rectangle ROI(s).")

    def _rectangle_stamp_changed(self, roi: RoiDefinition, *, finished: bool) -> None:
        item = next((stamp for stamp in self._rectangle_stamp_items if getattr(stamp, "roi_id", None) == roi.roi_id), None)
        if item is None:
            return
        pos = item.pos()
        size = item.size()
        updated = move_roi_from_template(
            roi,
            center_x=float(pos.x() + size.x() / 2.0),
            center_y=float(pos.y() + size.y() / 2.0),
            image_shape=self._current_processed_image.shape if self._current_processed_image is not None else None,
        )
        roi.center_x = updated.center_x
        roi.center_y = updated.center_y
        roi.size_x = max(float(size.x()), 2.0)
        roi.size_y = max(float(size.y()), 2.0)
        self._update_rectangle_stamp_ring_position(roi)
        if finished:
            self._push_undo_point("ROI")
            self._save_processing_state_for_dataset()
        if self._roi_editor_mode == "circles":
            self._update_roi_detection_labels(sync_controls=False)
        else:
            self._update_rectangle_roi_controls(sync_roi=False)

    def _add_rectangle_roi_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROIs.")
            return
        template = self._active_rectangle_template()
        if self._roi_editor_mode == "freehand":
            self.status_label.setText("Freehand ROI tools are not implemented yet.")
            return
        roi_id = self._next_roi_id()
        if self._roi_editor_mode == "circles":
            template_name = template.name.strip() or f"Circle ROI {len(self._state.rois) + 1}"
        else:
            template_name = template.name.strip() or f"Rectangle ROI {len(self._state.rois) + 1}"
        clone = create_rois_from_template(
            template,
            roi_id=roi_id,
            name=template_name,
            center_x=float(point[0]),
            center_y=float(point[1]),
            image_shape=self._current_processed_image.shape,
        )
        self._push_undo_point("Add ROI")
        self._state.rois.append(clone)
        self._select_rectangle_rois({clone.roi_id}, additive=False)
        self._sync_rectangle_stamp_overlays()
        self._update_rectangle_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI {clone.roi_id}.")

    def _add_stamp_array_at(self, point: tuple[float, float]) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for adding ROI arrays.")
            return
        template = self._active_rectangle_template()
        if self._roi_editor_mode == "freehand":
            self.status_label.setText("Freehand ROI arrays are not implemented yet.")
            return
        rows = int(self.array_rows_spin.value())
        cols = int(self.array_cols_spin.value())
        spacing = max(float(self._length_display_to_px(float(self.array_spacing_spin.value()))), 0.0)
        if rows <= 0 or cols <= 0 or spacing <= 0.0:
            self.status_label.setText("Set array rows, columns, and spacing before stamping an ROI array.")
            return
        count = rows * cols
        start_index = self._roi_id_counter + 1
        self._roi_id_counter += count
        clones = create_rois_from_template_grid(
            template,
            rows=rows,
            cols=cols,
            spacing_x=spacing,
            spacing_y=spacing,
            anchor_center_x=float(point[0]),
            anchor_center_y=float(point[1]),
            start_index=start_index,
            image_shape=self._current_processed_image.shape,
            roi_id_factory=lambda index: f"roi_{index}",
        )
        if not clones:
            self._roi_id_counter -= count
            return
        self._push_undo_point("Add ROI array")
        self._state.rois.extend(clones)
        self._select_rectangle_rois({roi.roi_id for roi in clones}, additive=False)
        self._sync_rectangle_stamp_overlays()
        self._update_rectangle_roi_summary()
        self._save_processing_state_for_dataset()
        self._schedule_processing_state_save()
        self.status_label.setText(f"Added ROI array with {len(clones)} ROI(s).")

    def _on_roi_editor_tab_changed(self, index: int) -> None:
        modes = ("circles", "rectangles", "freehand")
        self._roi_editor_mode = modes[index] if 0 <= index < len(modes) else "circles"
        if self._roi_editor_mode == "circles":
            self._update_roi_detection_labels(sync_controls=True)
        elif self._roi_editor_mode == "rectangles":
            self._update_rectangle_roi_controls(sync_roi=True)
        self._sync_rectangle_roi_visibility()
        self._update_guide_overlays()

    def _crop_rect_contains_point(self, point: tuple[float, float]) -> bool:
        return self._image_interaction._crop_rect_contains_point(point)

    def _move_crop_roi_to(self, x: float, y: float) -> None:
        self._image_tools_controller.move_crop_roi_to(x, y)

    def _begin_image_pan(self, point: tuple[float, float]) -> None:
        self._image_interaction._begin_image_pan(point)

    def _update_image_pan(self, point: tuple[float, float]) -> None:
        self._image_interaction._update_image_pan(point)

    def _end_image_pan(self) -> None:
        self._image_interaction._end_image_pan()

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
        self._overlay_manager._update_crop_overlay()

    def _ensure_exclusion_overlay(self) -> QGraphicsPathItem:
        if self._exclusion_overlay_item is not None:
            return self._exclusion_overlay_item
        overlay = QGraphicsPathItem()
        overlay.setZValue(0.3)
        overlay.setPen(QPen(QColor("#ef4444"), 3))
        overlay.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        overlay.setVisible(False)
        self.image_plot.addItem(overlay)
        self._exclusion_overlay_item = overlay
        return overlay

    def _show_image_exclusion_menu(self) -> None:
        self._image_exclusion_controller.show_menu()

    def _update_image_exclusion_indicator(self, spectral_cube_index: int | None = None, wavelength_nm: float | None = None) -> None:
        self._image_exclusion_controller.update_indicator(spectral_cube_index, wavelength_nm)

    def _refresh_image_exclusion_manage_dialog(self) -> None:
        self._image_exclusion_controller.refresh_manage_dialog_if_open()

    def _build_numeric_field(self, spinbox: QSpinBox | QDoubleSpinBox) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(spinbox)
        return row

    def _build_roi_geometry_row(self) -> QWidget:
        row = QWidget(self)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(0)
        self.sample_diameter_spin.setRange(2, 1000)
        self.sample_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.sample_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.sample_diameter_spin.setAccelerated(True)
        self.sample_diameter_spin.setKeyboardTracking(True)
        layout.addWidget(self.roi_geometry_scope_button, 0, 0, 2, 1)
        layout.addWidget(QLabel("D_s"), 0, 1)
        layout.addWidget(self.sample_diameter_spin, 0, 4)
        return row

    def _build_reference_row(self) -> QWidget:
        row = QWidget(self)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(0)
        self.reference_inner_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.reference_outer_diameter_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.reference_inner_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.reference_outer_diameter_spin.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.reference_inner_diameter_spin.setAccelerated(True)
        self.reference_outer_diameter_spin.setAccelerated(True)
        self.reference_inner_diameter_spin.setKeyboardTracking(False)
        self.reference_outer_diameter_spin.setKeyboardTracking(False)
        layout.addWidget(self.reference_geometry_scope_button, 0, 0, 2, 1)
        layout.addWidget(QLabel("d_r"), 0, 1)
        layout.addWidget(self.reference_inner_diameter_spin, 0, 2)
        layout.addWidget(QLabel("D_r"), 0, 3)
        layout.addWidget(self.reference_outer_diameter_spin, 0, 4)
        return row

    def _build_rectangle_row(self) -> QWidget:
        row = QWidget(self)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(0)
        for spinbox in (
            self.rectangle_width_spin,
            self.rectangle_height_spin,
            self.rectangle_padding_spin,
            self.rectangle_background_width_spin,
        ):
            spinbox.setDecimals(2)
            spinbox.setSingleStep(0.5)
            spinbox.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
            spinbox.setAccelerated(True)
            spinbox.setKeyboardTracking(False)
            spinbox.setMaximumWidth(84)
        self.rectangle_name_edit.setPlaceholderText("Rectangle ROI name")
        self.rectangle_name_edit.setMaximumWidth(160)
        layout.addWidget(QLabel("Name"), 0, 0)
        layout.addWidget(self.rectangle_name_edit, 0, 1, 1, 4)
        layout.addWidget(QLabel("W"), 1, 0)
        layout.addWidget(self.rectangle_width_spin, 1, 1)
        layout.addWidget(QLabel("H"), 1, 2)
        layout.addWidget(self.rectangle_height_spin, 1, 3)
        layout.addWidget(QLabel("Pad"), 2, 0)
        layout.addWidget(self.rectangle_padding_spin, 2, 1)
        layout.addWidget(QLabel("BG"), 2, 2)
        layout.addWidget(self.rectangle_background_width_spin, 2, 3)
        layout.addWidget(self.rectangle_summary_label, 3, 0, 1, 5)
        return row

    def _build_array_row(self) -> QWidget:
        row = QWidget(self)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        rows_label = self._make_array_marker_label("rows", "Array rows")
        cols_label = self._make_array_marker_label("columns", "Array columns")
        spacing_label = self._make_array_marker_label("distance", "Spacing between neighboring ROIs")
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
        allowed_areas: Qt.DockWidgetArea = Qt.DockWidgetArea.AllDockWidgetAreas,
    ) -> PanelContainer:
        panel = PanelContainer(title, content, self)
        panel.setObjectName(panel_name)
        panel.setAllowedAreas(allowed_areas)
        return panel

    def _restore_default_panel_layout(self) -> None:
        self._layout_state_controller.restore_default_panel_layout()

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

    def _sync_roi_detection_controls(self) -> None:
        self._ui_state_manager.sync_roi_detection_controls()

    def _update_mask_control_state(self) -> None:
        self._ui_state_manager.update_mask_control_state()

    def _update_analysis_control_state(self) -> None:
        self._ui_state_manager.update_analysis_control_state()

    def _on_mask_section_applied_changed(self, applied: bool) -> None:
        self._mask_controller.on_section_applied_changed(applied)

    def _on_background_section_applied_changed(self, applied: bool) -> None:
        self._mask_controller.on_background_section_applied_changed(applied)

    def _on_live_geometry_toggled(self, applied: bool) -> None:
        checked = bool(applied)
        if self.roi_editor_section.is_applied() != checked:
            self.roi_editor_section.set_applied(checked)
        self._save_control_preferences()

    def _on_image_tools_section_applied_changed(self, applied: bool) -> None:
        self._image_tools_controller.on_image_tools_section_applied_changed(applied)

    def _on_analysis_section_applied_changed(self, applied: bool) -> None:
        self._analysis_controller._on_analysis_section_applied_changed(applied)

    def _update_geometry_control_ranges(self, image_shape: tuple[int, int] | None) -> None:
        if image_shape is None:
            max_diameter = 4000
        else:
            image_height, image_width = image_shape[:2]
            max_diameter = max(int(max(image_width, image_height)), 20)
        display_max = max(self._length_px_to_display(max_diameter), float(self.sample_diameter_spin.minimum()))
        self.sample_diameter_spin.setMaximum(display_max)
        self.reference_inner_diameter_spin.setMaximum(display_max)
        self.reference_outer_diameter_spin.setMaximum(display_max)
        for spinbox in (
            self.rectangle_width_spin,
            self.rectangle_height_spin,
            self.rectangle_padding_spin,
            self.rectangle_background_width_spin,
        ):
            spinbox.setMaximum(display_max)
        self.array_spacing_spin.setMaximum(display_max)

    def _update_roi_detection_settings(self) -> None:
        sender = self.sender()
        if sender is not None:
            self._push_undo_point("Detection settings")
        previous_mask_signature = self._mask_preview_signature()
        previous_apply_mask = bool(self._state.preprocessing.flatten_background_exclude_mask)
        self._state.area_roi_settings.array_rows = int(self.array_rows_spin.value())
        self._state.area_roi_settings.array_cols = int(self.array_cols_spin.value())
        self._state.area_roi_settings.array_spacing_px = int(round(self._length_display_to_px(self.array_spacing_spin.value())))
        apply_mask = bool(self.ignore_marked_check.isChecked())
        self._state.area_roi_settings.ignore_marked_pixels = apply_mask
        self._state.preprocessing.flatten_background_exclude_mask = apply_mask
        self._set_section_applied(self.mask_section, apply_mask)
        self._state.area_roi_settings.mask_mode = str(self.mask_mode_combo.currentData() or "absolute")
        self._state.area_roi_settings.mask_profile_sigma_px = float(self.mask_relative_profile_sigma_spin.value())
        self._state.area_roi_settings.mask_relative_threshold_fraction = float(self.mask_relative_threshold_spin.value()) / 100.0
        self._state.area_roi_settings.mask_local_contrast_sigma_px = float(self.mask_local_contrast_sigma_spin.value())
        self._state.area_roi_settings.mask_local_contrast_z_threshold = float(self.mask_local_contrast_z_spin.value())
        if self._state.area_roi_settings.mask_mode == "absolute":
            lower, upper = self.ignore_region.getRegion()
            if lower > upper:
                lower, upper = upper, lower
            self._state.area_roi_settings.ignored_intensity_min_value = float(
                np.clip(lower, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
            )
            self._state.area_roi_settings.ignored_intensity_max_value = float(
                np.clip(upper, self.HISTOGRAM_MIN_INTENSITY, self.HISTOGRAM_MAX_INTENSITY)
            )
            self._state.area_roi_settings.ignored_intensity_value = None
        self._update_roi_detection_labels()
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
        self._state.preprocessing.flatten_background_exclude_area_rois = bool(self.background_ignore_roi_button.isChecked())
        self._state.preprocessing.flatten_background_exclude_mask = bool(self.background_ignore_mask_button.isChecked())
        self._state.preprocessing.flatten_background_exclusion_dilation_px = int(self.background_exclusion_dilation_spin.value())
        self._state.preprocessing.local_reference_normalization_enabled = bool(self.background_local_reference_check.isChecked())
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
        self._chromatic_controller.update_settings()

    def _clear_chromatic_models(self, *, push_undo: bool = True) -> None:
        self._chromatic_controller.clear_models(push_undo=push_undo)

    def _on_chromatic_transform_button_clicked(self) -> None:
        self._chromatic_controller.on_transform_button_clicked()

    def _update_roi_detection_labels(self, *, sync_controls: bool = True) -> None:
        settings = self._state.area_roi_settings
        diameter = max(2 * settings.sample_radius_px, 2)
        reference_inner_diameter = max(int(round(2 * settings.reference_inner_radius_px)), 0)
        reference_outer_diameter = max(int(round(2 * settings.reference_outer_radius_px)), 0)
        sample_area_px2 = np.pi * (float(diameter) / 2.0) ** 2
        reference_inner_radius_px = float(reference_inner_diameter) / 2.0
        reference_outer_radius_px = max(float(reference_outer_diameter) / 2.0, reference_inner_radius_px)
        reference_area_px2 = np.pi * max(reference_outer_radius_px * reference_outer_radius_px - reference_inner_radius_px * reference_inner_radius_px, 0.0)
        area_diff_px2 = sample_area_px2 - reference_area_px2
        if sync_controls:
            self.sample_diameter_spin.blockSignals(True)
            self.reference_inner_diameter_spin.blockSignals(True)
            self.reference_outer_diameter_spin.blockSignals(True)
            self.sample_diameter_spin.setValue(self._format_length_display_value(diameter))
            self.reference_inner_diameter_spin.setValue(self._format_length_display_value(reference_inner_diameter))
            self.reference_outer_diameter_spin.setValue(self._format_length_display_value(reference_outer_diameter))
            self.sample_diameter_spin.blockSignals(False)
            self.reference_inner_diameter_spin.blockSignals(False)
            self.reference_outer_diameter_spin.blockSignals(False)
        self.roi_geometry_area_label.setText(
            f"A_s={self._area_value_text(sample_area_px2)}, "
            f"A_r={self._area_value_text(reference_area_px2)}, "
            f"A_diff={self._area_delta_text(area_diff_px2)}"
        )

    def _apply_roi_geometry_preview(self, *, recalculate: bool) -> None:
        if not self._state.area_rois:
            self._update_roi_overlays()
            return
        selected_ids = set(self._selected_roi_ids)
        apply_all = bool(self.roi_geometry_scope_button.isChecked())
        for roi in self._state.area_rois:
            if apply_all or roi.area_roi_id in selected_ids:
                roi.sample_radius_px = float(self._state.area_roi_settings.sample_radius_px)
                if roi.sample_diameter_px is not None:
                    roi.sample_diameter_px = float(2 * self._state.area_roi_settings.sample_radius_px)
                if roi.reference_inner_diameter_px is not None:
                    roi.reference_inner_diameter_px = float(2 * self._state.area_roi_settings.reference_inner_radius_px)
                if roi.reference_outer_diameter_px is not None:
                    roi.reference_outer_diameter_px = float(2 * self._state.area_roi_settings.reference_outer_radius_px)

        if recalculate and self._current_processed_image is not None:
            self._request_roi_metrics_refresh(save_after=False, refresh_histogram=False)
        self._update_roi_overlays()
        if recalculate:
            self._schedule_histogram_refresh()
        else:
            self._update_roi_detection_labels(sync_controls=False)

    def _update_geometry_settings(self, *, save: bool, recalculate: bool, normalize_relation: bool = True) -> None:
        selected_ids = set(self._selected_roi_ids)
        apply_all = bool(self.roi_geometry_scope_button.isChecked())
        self._state.area_roi_settings.sample_radius_px = max(float(self._length_display_to_px(float(self.sample_diameter_spin.value())) / 2.0), 1.0)
        reference_inner_radius = max(self._length_display_to_px(float(self.reference_inner_diameter_spin.value())) / 2.0, 0.0)
        reference_outer_radius = max(self._length_display_to_px(float(self.reference_outer_diameter_spin.value())) / 2.0, 0.0)
        if normalize_relation and reference_outer_radius < reference_inner_radius:
            reference_outer_radius = reference_inner_radius
            self.reference_outer_diameter_spin.blockSignals(True)
            self.reference_outer_diameter_spin.setValue(self._length_px_to_display(reference_outer_radius * 2.0))
            self.reference_outer_diameter_spin.blockSignals(False)
        self._state.area_roi_settings.reference_inner_radius_px = reference_inner_radius
        self._state.area_roi_settings.reference_outer_radius_px = reference_outer_radius
        if apply_all:
            for roi in self._state.area_rois:
                roi.sample_diameter_px = float(self._state.area_roi_settings.sample_radius_px * 2.0)
                roi.reference_inner_diameter_px = float(reference_inner_radius * 2.0)
                roi.reference_outer_diameter_px = float(reference_outer_radius * 2.0)
        else:
            for roi in self._state.area_rois:
                if roi.area_roi_id in selected_ids:
                    roi.sample_diameter_px = float(self._state.area_roi_settings.sample_radius_px * 2.0)
                    roi.reference_inner_diameter_px = float(reference_inner_radius * 2.0)
                    roi.reference_outer_diameter_px = float(reference_outer_radius * 2.0)
        self._update_roi_detection_labels(sync_controls=False)
        self._apply_roi_geometry_preview(recalculate=recalculate)
        self._update_roi_table()
        if save:
            self._save_processing_state_for_dataset()

    def _refresh_roi_geometry(self) -> None:
        self._apply_roi_geometry_preview(recalculate=True)
        self._save_processing_state_for_dataset()
        self.status_label.setText("ROI geometry refreshed.")

    def _commit_roi_geometry_edits(self) -> None:
        self._push_undo_point("ROI geometry")
        self.sample_diameter_spin.interpretText()
        self.reference_inner_diameter_spin.interpretText()
        self.reference_outer_diameter_spin.interpretText()
        self._update_geometry_settings(save=True, recalculate=self.roi_editor_section.is_applied(), normalize_relation=True)

    def _detect_rois(self) -> None:
        if self._current_processed_image is None:
            self.status_label.setText("No image available for ROI detection.")
            return
        if not self._is_current_reference_image():
            self.status_label.setText("Switch to the reference image before detecting ROIs.")
            return
        self._push_undo_point("Detect ROIs")
        self._update_roi_detection_settings()
        self._roi_detection_request_id += 1
        request_id = self._roi_detection_request_id
        image_key = self._current_image_key
        image = self._current_processed_image
        settings = deepcopy(self._state.area_roi_settings)
        worker = FunctionWorker(
            _detect_rois_task,
            image,
            settings,
            self._current_external_mask(),
            supports_progress=True,
        )
        self._begin_busy("Detecting ROIs...")
        worker.signals.progress.connect(self._update_busy_progress)
        worker.signals.result.connect(
            lambda detected_rois,
            request_id=request_id,
            image_key=image_key: self._on_detect_rois_ready(request_id, image_key, detected_rois)
        )
        worker.signals.error.connect(lambda message: self._on_detect_rois_failed(message))
        self._thread_pool.start(worker)

    def _reorder_rois_by_position(self) -> None:
        if not self._state.area_rois:
            self.status_label.setText("No ROIs available to reorder.")
            return
        self._push_undo_point("Reorder ROIs by position")
        rows = max(int(self._state.area_roi_settings.array_rows), 0)
        cols = max(int(self._state.area_roi_settings.array_cols), 0)
        rois = list(self._state.area_rois)
        if rows > 0 and cols > 0 and rows * cols == len(rois):
            ordered = self._order_rois_as_array(rois, rows=rows, cols=cols)
        else:
            ordered = self._order_rois_as_array(rois, rows=rows if rows > 0 else None, cols=cols if cols > 0 else None)
        id_map = {roi.area_roi_id: new_id for new_id, roi in enumerate(ordered, start=1)}
        for new_id, roi in enumerate(ordered, start=1):
            roi.area_roi_id = new_id
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [id_map.get(roi_id, roi_id) for roi_id in group.area_roi_ids]
            group.area_roi_ids = sorted(dict.fromkeys(group.area_roi_ids))
        self._state.area_rois = ordered
        self._selected_roi_ids = {id_map.get(roi_id, roi_id) for roi_id in self._selected_roi_ids if roi_id in id_map}
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_selection_dependent_plots(force=True)
        self._save_processing_state_for_dataset()
        self._update_roi_table()
        self.status_label.setText("Reordered ROIs by image position.")

    def _roi_reorder_row_band(self) -> float:
        spacing = max(float(self._state.area_roi_settings.array_spacing_px), 0.0)
        diameters = [
            float(roi.sample_diameter_px)
            for roi in self._state.area_rois
            if roi.sample_diameter_px is not None and float(roi.sample_diameter_px) > 0.0
        ]
        if diameters:
            diameter_scale = float(np.median(np.asarray(diameters, dtype=np.float64)))
        else:
            diameter_scale = float(max(self._state.area_roi_settings.sample_radius_px * 2.0, 1.0))
        band_from_spacing = spacing * 0.45 if spacing > 0.0 else 0.0
        band_from_diameter = diameter_scale * 0.75
        return float(max(band_from_spacing, band_from_diameter, 5.0))

    def _order_rois_as_array(
        self,
        rois: list[AreaRoi],
        *,
        rows: int | None,
        cols: int | None,
    ) -> list[AreaRoi]:
        if not rois:
            return []
        sorted_rois = sorted(rois, key=lambda roi: (float(roi.center_y), float(roi.center_x), int(roi.area_roi_id)))
        row_band = self._roi_reorder_row_band()
        row_groups: list[list[AreaRoi]] = []
        row_centers: list[float] = []

        for roi in sorted_rois:
            y = float(roi.center_y)
            best_index = -1
            best_distance = float("inf")
            for index, center_y in enumerate(row_centers):
                distance = abs(y - center_y)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index >= 0 and best_distance <= row_band:
                row_groups[best_index].append(roi)
                row_centers[best_index] = float(np.mean([float(item.center_y) for item in row_groups[best_index]]))
            else:
                row_groups.append([roi])
                row_centers.append(y)

        if rows is not None and rows > 0 and len(row_groups) != rows:
            row_groups = [list(group) for group in np.array_split(np.asarray(sorted_rois, dtype=object), rows)]

        row_groups = [sorted(group, key=lambda roi: (float(roi.center_x), int(roi.area_roi_id))) for group in row_groups]
        row_groups.sort(key=lambda group: float(np.mean([float(roi.center_y) for roi in group])) if group else 0.0)

        ordered: list[AreaRoi] = []
        for row_group in row_groups:
            if cols is not None and cols > 0:
                ordered.extend(row_group[:cols])
            else:
                ordered.extend(row_group)
        return ordered

    def _on_detect_rois_ready(
        self,
        request_id: int,
        image_key: tuple[int, float] | None,
        detected_rois: list[AreaRoi],
    ) -> None:
        self._roi_table_controller._on_detect_rois_ready(request_id, image_key, detected_rois)

    def _on_detect_rois_failed(self, message: str) -> None:
        self._roi_table_controller._on_detect_rois_failed(message)

    def _clear_detected_rois(self, persist: bool = True) -> None:
        if self._state.area_rois:
            answer = QMessageBox.question(
                self,
                "Clear all ROIs",
                "Remove all detected ROIs and groups from the current dataset?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._push_undo_point("Clear ROIs")
        self._state.area_rois.clear()
        self._state.area_roi_groups.clear()
        self._selected_roi_ids.clear()
        self._invalidate_background_profile_cache()
        self._update_roi_overlays()
        self._schedule_histogram_refresh()
        self._update_roi_summary()
        if self._showing_background_profile_main:
            self._update_background_profile_preview()
        self._save_processing_state_for_dataset()
        if persist:
            self.status_label.setText("Cleared detected ROIs.")

    def _update_roi_summary(self) -> None:
        count = len(self._state.area_rois)
        if count == 0:
            self.roi_summary.setText("No ROIs detected.")
            self._clear_absorbance_spectrum("Detect ROIs to show absorbance spectrum.")
            return
        selected_details = ""
        if len(self._selected_roi_ids) == 1:
            selected_id = next(iter(self._selected_roi_ids))
            display_roi = next((roi for roi in self._display_rois() if roi.area_roi_id == selected_id), None)
            if display_roi is not None:
                label = self._array_label_for_roi(selected_id)
                label_text = f" ({label})" if label is not None else ""
                selected_details = (
                    f"\nSelected ROI: {selected_id}{label_text}"
                    f"\nPosition: x={display_roi.center_x:.1f}, y={display_roi.center_y:.1f} px"
                )
                source_roi = self._roi_by_id(selected_id)
                if source_roi is not None and not self._is_current_reference_image():
                    dx = float(display_roi.center_x - source_roi.center_x)
                    dy = float(display_roi.center_y - source_roi.center_y)
                    selected_details += f"\nShift from ref: dx={dx:+.1f}, dy={dy:+.1f} px"
        self.roi_summary.setText(
            f"Detected ROIs: {count}\nGroups: {len(self._state.area_roi_groups)}{selected_details}"
        )
        if not self._dragging_rois and not self._roi_edit_refresh_pending and not self._analysis_live_preview_enabled:
            self._schedule_absorbance_spectrum_refresh()

    def _group_for_roi(self, roi_id: int) -> AreaRoiGroup | None:
        for group in self._state.area_roi_groups:
            if roi_id in group.area_roi_ids:
                return group
        return None

    def _groups_for_roi(self, roi_id: int) -> list[AreaRoiGroup]:
        return [group for group in self._state.area_roi_groups if roi_id in group.area_roi_ids]

    def _select_group_members_for_roi(self, roi_id: int) -> bool:
        groups = self._groups_for_roi(roi_id)
        if not groups:
            return False
        selected_ids = {int(member_id) for group in groups for member_id in group.area_roi_ids}
        if not selected_ids:
            return False
        if selected_ids == self._selected_roi_ids:
            return True
        self._selected_roi_ids = selected_ids
        self._update_roi_overlays()
        self._update_roi_summary()
        self._sync_roi_table_selection()
        self._update_selection_dependent_plots(prompt_live_preview=True)
        return True

    def _ungroup_selected_rois(self) -> None:
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to ungroup them.")
            return
        if not any(group.area_roi_ids for group in self._state.area_roi_groups):
            self.status_label.setText("No grouped ROIs are selected.")
            return
        self._append_workflow_log(f"Groups | ungroup {len(self._selected_roi_ids)} ROI(s)", level="warning")
        self._push_undo_point("Ungroup ROIs")
        selected_ids = set(self._selected_roi_ids)
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in selected_ids]
        self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()
        self._save_processing_state_for_dataset()
        self.status_label.setText("Removed selected ROIs from their groups.")

    def _destroy_groups_for_roi(self, roi_id: int) -> None:
        groups = self._groups_for_roi(roi_id)
        if not groups:
            self.status_label.setText("No group is assigned to the selected ROI.")
            return
        self._push_undo_point("Destroy group")
        self._append_workflow_log(f"Groups | destroy for ROI {roi_id}", level="warning")
        group_names = [group.name for group in groups if group.name]
        remaining_groups = [group for group in self._state.area_roi_groups if group not in groups]
        self._state.area_roi_groups = remaining_groups
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_roi_table()
        self._save_processing_state_for_dataset()
        group_text = ", ".join(group_names) if group_names else "group"
        self.status_label.setText(f"Destroyed {group_text}; member ROIs are now free.")

    def _show_analysis_roi_context_menu(self, roi_id: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        group_action = menu.addAction("Group...")
        select_group_action = None
        ungroup_action = None
        destroy_group_action = None
        groups = self._groups_for_roi(roi_id)
        if groups:
            select_group_action = menu.addAction("Select group members")
            ungroup_action = menu.addAction("Ungroup")
            destroy_group_action = menu.addAction("Destroy group")
            menu.addSeparator()
        action = menu.exec(global_pos)
        if action is None:
            return
        if action is group_action:
            self._group_selected_rois()
        elif select_group_action is not None and action is select_group_action:
            if self._select_group_members_for_roi(roi_id):
                self.status_label.setText(f"Selected group members for ROI {roi_id}.")
        elif ungroup_action is not None and action is ungroup_action:
            self._ungroup_selected_rois()
        elif destroy_group_action is not None and action is destroy_group_action:
            self._destroy_groups_for_roi(roi_id)

    def _reindex_detected_rois(self) -> None:
        roi_id_map: dict[int, int] = {}
        for new_id, roi in enumerate(self._state.area_rois, start=1):
            roi_id_map[roi.area_roi_id] = new_id
            roi.area_roi_id = new_id

        updated_groups: list[AreaRoiGroup] = []
        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id_map[roi_id] for roi_id in group.area_roi_ids if roi_id in roi_id_map]
            if group.area_roi_ids:
                updated_groups.append(group)
        self._state.area_roi_groups = updated_groups

    def _remove_selected_rois(self) -> None:
        if self._roi_editor_mode == "rectangles" and self._selected_rectangle_roi_ids:
            self._remove_selected_rectangle_rois()
            return
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to remove them.")
            return
        self._append_workflow_log(f"ROIs | remove {len(self._selected_roi_ids)} selected", level="warning")
        self._push_undo_point("Remove ROIs")
        removed_count = len(self._selected_roi_ids)
        self._state.area_rois = [
            roi for roi in self._state.area_rois if roi.area_roi_id not in self._selected_roi_ids
        ]
        self._reindex_detected_rois()
        self._selected_roi_ids.clear()
        self._update_roi_overlays()
        if self._active_tool == "roi":
            self._mark_roi_edit_refresh_pending()
        else:
            self._schedule_histogram_refresh()
        self._update_roi_summary()
        if self._active_tool != "roi":
            self._save_processing_state_for_dataset()
        self.status_label.setText(f"Removed {removed_count} selected ROI(s).")

    def _group_selected_rois(self) -> None:
        if not self._selected_roi_ids:
            self.status_label.setText("Select ROI(s) first to create a group.")
            return

        current_group = self._group_for_roi(min(self._selected_roi_ids))
        default_name = current_group.name if current_group is not None else f"Group {len(self._state.area_roi_groups) + 1}"
        name, accepted = QInputDialog.getText(self, "ROI group", "Group name", text=default_name)
        if not accepted:
            return
        name = name.strip()
        if not name:
            self.status_label.setText("Group creation cancelled: name is required.")
            return

        initial_color = QColor(current_group.sample_color_hex) if current_group is not None else QColor("#f59e0b")
        color = QColorDialog.getColor(initial_color, self, "ROI group color")
        if not color.isValid():
            return
        self._push_undo_point("Group ROIs")
        self._append_workflow_log(
            f"Groups | create '{name}' with {len(self._selected_roi_ids)} ROI(s)",
            level="success",
        )

        for group in self._state.area_roi_groups:
            group.area_roi_ids = [roi_id for roi_id in group.area_roi_ids if roi_id not in self._selected_roi_ids]
        self._state.area_roi_groups = [group for group in self._state.area_roi_groups if group.area_roi_ids]

        target_group = next((group for group in self._state.area_roi_groups if group.name == name), None)
        if target_group is None:
            target_group = AreaRoiGroup(
                group_id=f"group_{len(self._state.area_roi_groups) + 1}",
                name=name,
                sample_color_hex=color.name(),
                reference_color_hex=self._reference_visual_color.name(),
                area_roi_ids=sorted(self._selected_roi_ids),
            )
            self._state.area_roi_groups.append(target_group)
        else:
            target_group.sample_color_hex = color.name()
            target_group.area_roi_ids = sorted(set(target_group.area_roi_ids).union(self._selected_roi_ids))

        self._update_roi_overlays()
        self._update_roi_summary()
        self._save_processing_state_for_dataset()
        self.status_label.setText(f"Grouped {len(self._selected_roi_ids)} ROI(s) as '{name}'.")

    def _clear_roi_selection(self) -> None:
        self._selected_roi_ids.clear()
        self._update_roi_overlays()
        self._update_roi_summary()
        self._update_selection_dependent_plots(force=True)
        self.status_label.setText("Cleared ROI selection.")

    def _schedule_roi_overlay_refresh(self) -> None:
        if not self._roi_overlay_refresh_timer.isActive():
            self._roi_overlay_refresh_timer.start()

    def _refresh_roi_overlays_during_drag(self) -> None:
        self._overlay_manager._refresh_roi_overlays_during_drag()

    def _update_roi_overlays(self, *, update_hidden_details: bool = True) -> None:
        self._overlay_manager._update_roi_overlays(update_hidden_details=update_hidden_details)

    def _remove_roi_overlay_bundle(self, roi_id: int) -> None:
        bundle = self._roi_overlay_items.pop(roi_id, None)
        if bundle is None:
            return
        self.image_plot.removeItem(bundle.curve)
        if bundle.reference_fill is not None:
            self.image_plot.removeItem(bundle.reference_fill)
        if bundle.inner_curve is not None:
            self.image_plot.removeItem(bundle.inner_curve)
        if bundle.outer_curve is not None:
            self.image_plot.removeItem(bundle.outer_curve)
        if bundle.label is not None:
            self.image_plot.removeItem(bundle.label)

    def _update_landmark_overlays(self) -> None:
        self._overlay_manager._update_landmark_overlays()

    def _clear_chromatic_all_landmark_overlays(self) -> None:
        self._chromatic_controller.clear_all_landmark_overlays()

    def _chromatic_wavelength_color(self, wavelength_nm: float) -> QColor:
        return self._chromatic_controller.wavelength_color(wavelength_nm)

    def _transform_chromatic_point_between_keys(
        self,
        point_xy: tuple[float, float],
        source_key: tuple[int, float],
        target_key: tuple[int, float],
    ) -> tuple[float, float] | None:
        return self._chromatic_controller.transform_point_between_keys(point_xy, source_key, target_key)

    def _update_chromatic_all_landmark_overlays(self) -> None:
        self._chromatic_controller.update_all_landmark_overlays()


    def _create_reference_fill_path(
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
        self._overlay_manager._update_guide_overlays()

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
        self.image_plot.addItem(vertical, ignoreBounds=True)
        self.image_plot.addItem(horizontal, ignoreBounds=True)
        self.image_plot.addItem(marker, ignoreBounds=True)
        guide = GuideOverlayBundle(vertical=vertical, horizontal=horizontal, marker=marker)
        self._guide_overlay_items[0] = guide
        return guide

    def _on_image_tool_guide_moved(self, *_args) -> None:
        self._overlay_manager._on_image_tool_guide_moved(*_args)

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
            self.image_plot.addItem(line, ignoreBounds=True)
            self._ome_zarr_chunk_overlay_items.append(line)
        for y in range(chunk_size, max(int(image_height), 1), chunk_size):
            line = pg.InfiniteLine(angle=0, pos=float(y), pen=pen, movable=False)
            line.setZValue(14)
            self.image_plot.addItem(line, ignoreBounds=True)
            self._ome_zarr_chunk_overlay_items.append(line)

    def _hide_measurement_overlay(self) -> None:
        self._overlay_manager._hide_measurement_overlay()

    def _ensure_measurement_overlay(self) -> MeasurementOverlayBundle:
        return self._overlay_manager._ensure_measurement_overlay()

    def _update_measurement_overlay(self) -> None:
        self._overlay_manager._update_measurement_overlay()

    def _on_measurement_marker_moved(self, *_args) -> None:
        self._overlay_manager._on_measurement_marker_moved(*_args)

    def _update_measurement_status_label(
        self,
        *,
        dx_px: float | None = None,
        dy_px: float | None = None,
        distance_px: float | None = None,
    ) -> None:
        self._overlay_manager._update_measurement_status_label(dx_px=dx_px, dy_px=dy_px, distance_px=distance_px)

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
        self._sync_roi_detection_controls()
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
        self.image_plot.addItem(outline_line, ignoreBounds=True)
        self.image_plot.addItem(line, ignoreBounds=True)
        self.image_plot.addItem(outline_left_tick, ignoreBounds=True)
        self.image_plot.addItem(left_tick, ignoreBounds=True)
        self.image_plot.addItem(outline_right_tick, ignoreBounds=True)
        self.image_plot.addItem(right_tick, ignoreBounds=True)
        self.image_plot.addItem(outline_label, ignoreBounds=True)
        self.image_plot.addItem(label, ignoreBounds=True)
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
        self._overlay_manager._refresh_scale_bar_overlay()

    def _update_ignore_mask_overlay(self) -> None:
        self._overlay_manager._update_ignore_mask_overlay()

    def _update_histogram(self, image: np.ndarray) -> None:
        self._plot_manager.update_histogram(image)

    def _update_area_histogram_curves(
        self,
        *,
        edges: np.ndarray,
        sample_values: np.ndarray,
        reference_values: np.ndarray,
        mask_values: np.ndarray,
        total_pixels: float,
        total_counts_display: np.ndarray,
    ) -> float:
        return self._plot_manager.update_area_histogram_curves(
            edges=edges,
            sample_values=sample_values,
            reference_values=reference_values,
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
        roi_mask = np.zeros((image_height, image_width), dtype=bool)
        reference_mask = np.zeros((image_height, image_width), dtype=bool)
        display_rois = self._display_rois()
        if display_rois:
            affine_matrix = self._chromatic_affine_for_image_key(self._current_image_key)
            reference_inner_radius = float(max(self._state.area_roi_settings.reference_inner_radius_px, 0.0))
            reference_outer_radius = float(max(self._state.area_roi_settings.reference_outer_radius_px, reference_inner_radius))
            if affine_matrix is None or self._is_current_reference_image():
                yy, xx = np.indices((image_height, image_width), dtype=np.float32)
                for roi in display_rois:
                    distance_sq = (xx - float(roi.center_x)) ** 2 + (yy - float(roi.center_y)) ** 2
                    roi_mask |= distance_sq <= float(roi.sample_radius_px) ** 2
                    if reference_outer_radius > 0.0:
                        outer_mask = distance_sq <= reference_outer_radius ** 2
                        inner_mask = distance_sq < reference_inner_radius ** 2 if reference_inner_radius > 0.0 else np.zeros_like(outer_mask)
                        reference_mask |= outer_mask & ~inner_mask
            else:
                source_roi_map = {roi.area_roi_id: roi for roi in self._state.area_rois}
                for roi in display_rois:
                    source_roi = source_roi_map.get(roi.area_roi_id, roi)
                    roi_mask |= transformed_disk_mask(
                        (image_height, image_width),
                        (float(source_roi.center_x), float(source_roi.center_y)),
                        float(source_roi.sample_radius_px),
                        affine_matrix,
                    )
                    if reference_outer_radius > 0.0:
                        reference_mask |= transformed_annulus_mask(
                            (image_height, image_width),
                            (float(source_roi.center_x), float(source_roi.center_y)),
                            float(reference_inner_radius),
                            float(reference_outer_radius),
                            affine_matrix,
                        )
        roi_mask &= ~ignored_mask
        reference_mask &= ~ignored_mask
        reference_mask &= ~roi_mask
        residual_mask = ~(roi_mask | reference_mask | ignored_mask)
        cached = (roi_mask, reference_mask, ignored_mask, residual_mask)
        self._roi_mask_cache_signature = signature
        self._roi_mask_cache_values = cached
        return cached

    def _roi_intensity_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image_f32 = image.astype(np.float32, copy=False)
        roi_mask, reference_mask, ignored_mask, _residual_mask = self._roi_area_masks(image_f32)
        return (
            image_f32[roi_mask],
            image_f32[reference_mask],
            image_f32[ignored_mask],
        )

    def _on_histogram_region_changed(self) -> None:
        self._overlay_manager._on_histogram_region_changed()

    def _on_ignore_region_changed(self) -> None:
        self._overlay_manager._on_ignore_region_changed()

    def _preview_ignore_region_overlay(self) -> None:
        return

    def _on_histogram_bins_changed(self, _value: int) -> None:
        self._overlay_manager._on_histogram_bins_changed(_value)

    def _update_selected_intensity_overlay(self) -> None:
        self._overlay_manager._update_selected_intensity_overlay()

    def _update_histogram_region_labels(self) -> None:
        view_range = self.histogram_plot.viewRange()
        top_y = float(view_range[1][1]) if view_range and len(view_range) > 1 else 1.0
        label_top = max(top_y * 0.92, top_y - 0.8)

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
            else self._sample_visual_color
            if target == "roi"
            else self._reference_visual_color
            if target == "reference"
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
        elif target == "roi":
            self._sample_visual_color = color
            self._update_roi_overlays()
            self._restyle_area_histogram_curves()
            self._refresh_visible_spectrum_from_cache()
            self._analysis_controller.update_selection_highlight(force=True)
        elif target == "reference":
            self._reference_visual_color = color
            self._update_roi_overlays()
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
        return self._analysis_controller._refresh_visible_spectrum_from_cache()

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
        if not self._selected_roi_ids:
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
        selection_signature = tuple(self._selected_spectrum_roi_ids())
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
                    f"{self._analysis_metric_label()} sensorgram is out of date | Press Calculate all spectral cubes"
                )

    def _update_selection_dependent_plots(self, *, force: bool = False, prompt_live_preview: bool = False) -> None:
        selected_signature = tuple(sorted(int(roi_id) for roi_id in self._selected_roi_ids))
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

    def _on_roi_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._roi_alpha = self._alpha01(float(value) / 100.0)
        self._update_roi_overlays()
        self._restyle_area_histogram_curves()
        self._save_visual_preferences()

    def _on_reference_alpha_changed(self, value: int) -> None:
        self._push_undo_point("Overlay appearance")
        self._reference_alpha = self._alpha01(float(value) / 100.0)
        self._update_roi_overlays()
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
        return self._mask_controller.mask_changes_affect_preprocessing()

    def _mask_change_status_suffix(self) -> str:
        return self._mask_controller.mask_change_status_suffix()

    def _session_mask_payload(self) -> dict | None:
        return self._mask_controller.session_mask_payload()

    def _manual_mask_required(self, *, create_if_missing: bool) -> np.ndarray | None:
        return self._mask_controller.manual_mask_required(create_if_missing=create_if_missing)

    def _mask_structure(self, radius_px: int) -> np.ndarray:
        return self._mask_controller.mask_structure(radius_px)

    def _apply_mask_brush(self, point: tuple[float, float]) -> None:
        self._mask_controller.apply_mask_brush(point)

    def _finalize_mask_edit(self) -> None:
        self._mask_controller.finalize_mask_edit()

    def _current_mask_file_path(self) -> Path | None:
        return self._mask_controller.current_mask_file_path()

    def _mask_file_path_for_record(self, record_path: Path) -> Path:
        return self._mask_controller.mask_file_path_for_record(record_path)

    def _current_background_file_path(self) -> Path | None:
        return self._mask_controller.current_background_file_path()

    def _image_key_for_record_path(self, record_path: Path) -> tuple[int, float] | None:
        return self._record_key_by_path.get(record_path)

    def _current_external_mask(self) -> np.ndarray | None:
        return self._mask_controller.current_external_mask()

    def _effective_external_mask_for_record(
        self,
        record_path: Path,
        *,
        processed_space: bool = False,
    ) -> tuple[np.ndarray | None, bool]:
        return self._mask_controller.effective_external_mask_for_record(record_path, processed_space=processed_space)

    def _external_mask_for_record(self, record_path: Path, *, processed_space: bool = False) -> tuple[np.ndarray | None, bool]:
        return self._mask_controller.external_mask_for_record(record_path, processed_space=processed_space)

    def _external_mask_signature(self, image_key: tuple[int, float] | None = None) -> tuple[object, ...] | None:
        return self._mask_controller.external_mask_signature(image_key)

    def _processed_to_raw_maps(self) -> tuple[np.ndarray, np.ndarray] | None:
        return self._mask_controller.processed_to_raw_maps()

    def _set_current_file_mask(
        self,
        mask: np.ndarray | None,
        path: Path | None,
        *,
        refresh_preview: bool,
    ) -> bool:
        return self._mask_controller.set_current_file_mask(mask, path, refresh_preview=refresh_preview)

    def _read_mask_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        return self._mask_controller.read_mask_image(path, expected_shape)

    def _auto_load_mask_for_current_record(self) -> None:
        self._mask_controller.auto_load_mask_for_current_record()

    def _clear_mask_preview_overlays(self, *, clear_toggles: bool = False) -> None:
        self._mask_controller.clear_preview_overlays(clear_toggles=clear_toggles)

    def _set_mask_preview_button_icon(self, button: QToolButton, shown: bool) -> None:
        self._mask_controller.set_mask_preview_button_icon(button, shown)

    def _current_mask_canvas(self) -> tuple[np.ndarray, Path | None] | None:
        return self._mask_controller.current_mask_canvas()

    def _refresh_mask_previews(self, *_args) -> None:
        self._mask_controller.refresh_previews()

    def _ensure_mask_section_applied(self) -> None:
        self._mask_controller.ensure_mask_section_applied()

    def _refresh_after_mask_change(self, status: str) -> None:
        self._mask_controller.refresh_after_mask_change(status)

    def _create_new_background(self) -> None:
        self._mask_controller.create_new_background()

    def _load_background_from_file(self) -> None:
        self._mask_controller.load_background_from_file()

    def _save_background_to_file(self) -> None:
        self._mask_controller.save_background_to_file()

    def _read_background_image(self, path: Path, expected_shape: tuple[int, int]) -> np.ndarray:
        return self._mask_controller.read_background_image(path, expected_shape)

    def _update_mask_file_button_state(self) -> None:
        self._mask_controller.update_mask_file_button_state()

    def _on_roi_diameter_spin_changed(self, value: int) -> None:
        self._roi_table_controller._on_roi_diameter_spin_changed(value)

    def _on_reference_inner_diameter_spin_changed(self, value: int) -> None:
        self._roi_table_controller._on_reference_inner_diameter_spin_changed(value)

    def _on_reference_outer_diameter_spin_changed(self, value: int) -> None:
        self._roi_table_controller._on_reference_outer_diameter_spin_changed(value)

    def _on_spectral_cube_spin_changed(self, value: int) -> None:
        if not self._spectral_cube_values:
            return
        closest_index = min(range(len(self._spectral_cube_values)), key=lambda idx: abs(self._spectral_cube_values[idx] - value))
        self.spectral_cube_slider.setValue(closest_index)

    def _step_spectral_cube_selection(self, direction: int) -> bool:
        if not self._spectral_cube_values:
            return False
        current_index = self.spectral_cube_slider.value()
        target_index = min(max(current_index + int(direction), 0), len(self._spectral_cube_values) - 1)
        if target_index == current_index:
            return False
        self.spectral_cube_slider.setValue(target_index)
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
        current_spectral_cube = self._current_spectral_cube()
        if current_spectral_cube is None:
            return False
        self._set_current_spectral_cube_and_wavelength(int(current_spectral_cube), target_wavelength)
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

    def _normalize_display_units(self) -> None:
        if str(self._state.preprocessing.display_units or "px") == "um" and not self._can_display_micrometers():
            self._state.preprocessing.display_units = "px"

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
        self._normalize_display_units()
        suffix = self._display_length_suffix()
        use_um = self._display_uses_micrometers()
        decimals = 0
        step = 1.0 if use_um else 1.0
        for spinbox in (
            self.sample_diameter_spin,
            self.reference_inner_diameter_spin,
            self.reference_outer_diameter_spin,
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

    def _reference_area_text(self, inner_diameter_px: float, outer_diameter_px: float) -> str:
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
