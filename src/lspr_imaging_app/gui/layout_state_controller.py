from __future__ import annotations

import logging

import numpy as np
from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget


class LayoutStateController:
    """Owns window geometry and dock/splitter/section layout save-restore.

    Pure Qt/QSettings plumbing - none of this touches scientific state
    (window._state). Startup ordering matters here: window geometry must be
    restored before the panel layout, and the window must not be shown
    (showNormal/Maximized/FullScreen) until after both have settled - see
    prepare_initial_show and MainWindow.run_startup_restore_flow.
    """

    def __init__(self, window) -> None:
        self.window = window

    def prepare_initial_show(self) -> None:
        window = self.window
        if window._window_geometry_restored:
            return
        window.resize(1320, 860)
        window.move(40, 40)

    def restore_saved_window_layout(self) -> None:
        window = self.window
        if window._window_geometry_restored and window._layout_preferences_ready:
            return
        self.restore_window_geometry()
        self.restore_layout_preferences()

    def restore_window_geometry(self) -> None:
        window = self.window
        if window._window_geometry_restored:
            return
        geometry_rel = window._settings.value("window_geometry_rel")
        available = self.best_restore_screen_geometry(None)
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
            window.setGeometry(x, y, width, height)
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
                window.setGeometry(x, y, width, height)
            else:
                window.resize(1320, 860)
                window.move(40, 40)
        if available is not None:
            self.move_inside_available_screen(available)
        try:
            self.restore_default_panel_layout()
        except Exception:
            pass
        window._startup_restore_window_maximized = window._settings_bool("window_is_maximized", False)
        window._startup_restore_window_fullscreen = window._settings_bool("window_is_fullscreen", False)
        window._window_geometry_restored = True

    def best_restore_screen_geometry(self, saved_rect: QRectF | None = None):
        window = self.window
        screens = QGuiApplication.screens()
        if not screens:
            return window.screen().availableGeometry() if window.screen() is not None else None
        target_name = str(window._settings.value("window_screen_name", "") or "").strip()
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
        if window.screen() is not None:
            logging.getLogger("lspr_imaging_app.layout").debug("selected current screen | %s", window.screen().name())
            return window.screen().availableGeometry()
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            logging.getLogger("lspr_imaging_app.layout").debug("selected primary screen | %s", primary.name())
        return primary.availableGeometry() if primary is not None else None

    def move_inside_available_screen(self, available=None) -> None:
        window = self.window
        if available is None:
            available = window.screen().availableGeometry() if window.screen() is not None else None
        if available is None:
            return

        frame_geometry = window.frameGeometry()
        x = min(max(frame_geometry.x(), available.left()), max(available.right() - frame_geometry.width() + 1, available.left()))
        y = min(max(frame_geometry.y(), available.top()), max(available.bottom() - frame_geometry.height() + 1, available.top()))
        window.move(x, y)

    def restore_layout_preferences(self) -> None:
        window = self.window
        roi_list_visible = window._settings_bool("layout/roi_list_visible", True)
        cached_rois_only_visible = window._settings_bool("layout/cached_rois_only_visible", False)
        window.roi_list_action.blockSignals(True)
        window.roi_list_action.setChecked(roi_list_visible)
        window.roi_list_action.blockSignals(False)
        if hasattr(window, "roi_list_cached_button"):
            window.roi_list_cached_button.blockSignals(True)
            window.roi_list_cached_button.setChecked(cached_rois_only_visible)
            window.roi_list_cached_button.setIcon(window._make_cached_rois_icon(cached_rois_only_visible))
            window.roi_list_cached_button.blockSignals(False)
        window._cached_rois_only_visible = cached_rois_only_visible
        window._suspend_collapsible_accordion = True
        try:
            window.dataset_section.set_pinned(window._settings_bool("dataset_section_pinned", False))
            window.dataset_section.set_expanded(window._settings_bool("dataset_section_expanded", True))
            window.chromatic_section.set_pinned(window._settings_bool("chromatic_section_pinned", False))
            window.chromatic_section.set_expanded(window._settings_bool("chromatic_section_expanded", False))
            window.mask_section.set_pinned(window._settings_bool("mask_section_pinned", False))
            window.mask_section.set_expanded(window._settings_bool("mask_section_expanded", True))
            window.image_tools_section.set_pinned(window._settings_bool("image_tools_panel_pinned", False))
            window.image_tools_section.set_expanded(window._settings_bool("image_tools_panel_expanded", True))
            window.roi_editor_section.set_pinned(window._settings_bool("roi_editor_section_pinned", False))
            window.roi_editor_section.set_expanded(window._settings_bool("roi_editor_section_expanded", True))
            window.background_section.set_pinned(window._settings_bool("background_section_pinned", False))
            window.background_section.set_expanded(window._settings_bool("background_section_expanded", True))
            window.analysis_section.set_pinned(window._settings_bool("analysis_section_pinned", False))
            window.analysis_section.set_expanded(window._settings_bool("analysis_section_expanded", True))
            if hasattr(window, "workflow_log_section"):
                window.workflow_log_section.set_expanded(True)
            window._analysis_enabled = window._settings_bool("analysis_section_applied", window._analysis_enabled)
            window._set_section_applied(window.analysis_section, window._analysis_enabled)
        finally:
            window._suspend_collapsible_accordion = False
        self.restore_panel_layout_preferences()
        self.normalize_panel_layout()
        window.left_tabs.blockSignals(True)
        try:
            window.left_tabs.setCurrentIndex(
                window._settings_int("left_tab_index", 0, minimum=0, maximum=max(window.left_tabs.count() - 1, 0))
            )
        finally:
            window.left_tabs.blockSignals(False)
        window._layout_preferences_ready = True
        window._update_analysis_control_state()

    def apply_saved_splitter_sizes(self, main_sizes) -> None:
        return

    def save_layout_preferences(self) -> None:
        window = self.window
        if not window._layout_preferences_ready or window._suspend_layout_save:
            return
        window._settings.setValue("left_tab_index", window.left_tabs.currentIndex())
        window._settings.setValue("layout/roi_list_visible", bool(window.roi_list_panel.isVisible()))
        window._settings.setValue("layout/cached_rois_only_visible", bool(window._cached_rois_only_visible))
        window._settings.setValue("dataset_section_expanded", window.dataset_section.is_expanded())
        window._settings.setValue("dataset_section_pinned", window.dataset_section.is_pinned())
        window._settings.setValue("chromatic_section_expanded", window.chromatic_section.is_expanded())
        window._settings.setValue("chromatic_section_pinned", window.chromatic_section.is_pinned())
        window._settings.setValue("mask_section_expanded", window.mask_section.is_expanded())
        window._settings.setValue("mask_section_pinned", window.mask_section.is_pinned())
        window._settings.setValue("image_tools_panel_expanded", window.image_tools_section.is_expanded())
        window._settings.setValue("image_tools_panel_pinned", window.image_tools_section.is_pinned())
        window._settings.setValue("roi_editor_section_expanded", window.roi_editor_section.is_expanded())
        window._settings.setValue("roi_editor_section_pinned", window.roi_editor_section.is_pinned())
        window._settings.setValue("background_section_expanded", window.background_section.is_expanded())
        window._settings.setValue("background_section_pinned", window.background_section.is_pinned())
        window._settings.setValue("analysis_section_expanded", window.analysis_section.is_expanded())
        window._settings.setValue("analysis_section_pinned", window.analysis_section.is_pinned())
        window._settings.setValue("analysis_section_applied", window._analysis_enabled)
        if hasattr(window, "workflow_log_section"):
            window._settings.setValue("workflow_log_section_expanded", window.workflow_log_section.is_expanded())
        window._settings.setValue("ome_zarr/chunk_size_px", int(window._current_ome_zarr_chunk_size()))
        window._settings.setValue("ome_zarr/shard_mode", window.ome_zarr_shard_mode_combo.currentData())
        window._settings.setValue("ome_zarr/chunk_guide_visible", bool(window.ome_zarr_chunk_guide_button.isChecked()))
        window._settings.setValue("ome_zarr/compression_enabled", bool(window.ome_zarr_compression_button.isChecked()))
        self.save_panel_layout_preferences()

    def capture_panel_layout_snapshot(self) -> dict[str, QByteArray] | None:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            return None
        return {
            "main": window._main_splitter.saveState(),
            "visual": window._visual_splitter.saveState(),
            "top": window._top_visual_splitter.saveState(),
            "bottom": window._bottom_visual_splitter.saveState(),
        }

    def apply_panel_layout_snapshot(self, snapshot: dict[str, QByteArray] | None) -> None:
        window = self.window
        if snapshot is None:
            self.apply_default_splitter_sizes()
            return
        for key, splitter_name in (
            ("main", "_main_splitter"),
            ("visual", "_visual_splitter"),
            ("top", "_top_visual_splitter"),
            ("bottom", "_bottom_visual_splitter"),
        ):
            splitter = getattr(window, splitter_name, None)
            state = snapshot.get(key)
            if splitter is None or not isinstance(state, QByteArray) or state.isEmpty():
                continue
            try:
                splitter.restoreState(state)
            except Exception:
                pass
        self.normalize_panel_layout()

    def panel_layout_panels(self) -> list[tuple[str, QWidget]]:
        window = self.window
        return [
            ("workflow_panel", window.workflow_panel),
            ("roi_list_panel", window.roi_list_panel),
            ("image_panel", window.image_panel),
            ("histogram_panel", window.histogram_panel),
            ("spectra_panel", window.spectra_panel),
            ("sensorgram_panel", window.sensorgram_panel),
        ]

    def restore_saved_panel_layout_state(self) -> bool:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            logging.getLogger("lspr_imaging_app.layout").debug("splitter restore | no splitter layout")
            window._append_workflow_log("Layout | no splitter layout available", level="warning")
            return False
        state_keys = {
            "_main_splitter": "layout/main_splitter_state",
            "_visual_splitter": "layout/visual_splitter_state",
            "_top_visual_splitter": "layout/top_visual_splitter_state",
            "_bottom_visual_splitter": "layout/bottom_visual_splitter_state",
        }
        restored_any = False
        for attr_name, key in state_keys.items():
            splitter = getattr(window, attr_name, None)
            if splitter is None:
                continue
            state = window._settings.value(key)
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
            window._append_workflow_log(
                f"Layout | {attr_name} restored={restored} size={blob.size()}",
                level="debug" if restored else "warning",
            )
            restored_any = restored_any or restored
        return restored_any

    def restore_saved_window_state_after_show(self) -> None:
        window = self.window
        if window._startup_restore_window_fullscreen:
            window.showFullScreen()
        elif window._startup_restore_window_maximized:
            window.showMaximized()
        else:
            window.showNormal()

    def restore_panel_layout_preferences(self) -> None:
        window = self.window
        window._append_workflow_log("Layout | restoring panel visibility and splitter states", level="debug")
        self.restore_default_panel_layout()
        for name, panel in self.panel_layout_panels():
            visible = window._settings_bool(f"layout/{name}_visible", True)
            panel.blockSignals(True)
            try:
                panel.setVisible(visible)
            finally:
                panel.blockSignals(False)
            window._append_workflow_log(f"Panel | restore {name} visible={visible}", level="debug")
        if not self.restore_saved_panel_layout_state():
            self.apply_default_splitter_sizes()
            window._append_workflow_log("Layout | applied default splitter sizes", level="warning")

    def normalize_panel_layout(self) -> None:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            return
        window._main_splitter.setChildrenCollapsible(False)
        window._visual_splitter.setChildrenCollapsible(False)
        window._top_visual_splitter.setChildrenCollapsible(False)
        window._bottom_visual_splitter.setChildrenCollapsible(False)

    def set_all_panel_visibility(self, visible: bool) -> None:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            return
        snapshot = window._panel_layout_visibility_backup
        window._append_workflow_log(f"Panel | {'show' if visible else 'hide'} all", level="debug")
        if not visible and snapshot is None:
            window._panel_layout_visibility_backup = self.capture_panel_layout_snapshot()
        window._suspend_layout_save = True
        try:
            for _name, panel in self.panel_layout_panels():
                panel.setVisible(bool(visible))
        finally:
            window._suspend_layout_save = False
        if visible:
            self.apply_panel_layout_snapshot(window._panel_layout_visibility_backup)
            window._panel_layout_visibility_backup = None
        self.save_panel_layout_preferences()
        self.save_layout_preferences()

    def sync_panel_visibility_after_show(self) -> None:
        window = self.window
        for name, dock in self.panel_layout_panels():
            visible = window._settings_bool(f"layout/{name}_visible", True)
            dock.blockSignals(True)
            try:
                dock.toggleViewAction().setChecked(visible)
                dock.setVisible(visible)
            finally:
                dock.blockSignals(False)
            window._append_workflow_log(f"Panel | sync {name} visible={visible}", level="debug")
        if hasattr(window, "workflow_log_section"):
            try:
                window.workflow_log_section.set_expanded(True)
            except Exception:
                pass

    def save_panel_layout_preferences(self) -> None:
        window = self.window
        for name, dock in self.panel_layout_panels():
            visible = bool(dock.toggleViewAction().isChecked())
            window._settings.setValue(f"layout/{name}_visible", visible)
        if hasattr(window, "_main_splitter"):
            window._settings.setValue("layout/main_splitter_state", window._main_splitter.saveState())
        if hasattr(window, "_visual_splitter"):
            window._settings.setValue("layout/visual_splitter_state", window._visual_splitter.saveState())
        if hasattr(window, "_top_visual_splitter"):
            window._settings.setValue("layout/top_visual_splitter_state", window._top_visual_splitter.saveState())
        if hasattr(window, "_bottom_visual_splitter"):
            window._settings.setValue("layout/bottom_visual_splitter_state", window._bottom_visual_splitter.saveState())

    def on_panel_visibility_changed(self, _dock: QWidget) -> None:
        window = self.window
        if isinstance(_dock, QWidget):
            panel_name = _dock.objectName() or _dock.windowTitle() or _dock.__class__.__name__
            window._append_workflow_log(f"Panel | visibility changed {panel_name}", level="debug")
        self.save_layout_preferences()

    def reset_layout_to_defaults(self) -> None:
        window = self.window
        self.restore_default_panel_layout()
        window._suspend_collapsible_accordion = True
        try:
            window.dataset_section.set_expanded(True)
            window.mask_section.set_expanded(True)
            window.chromatic_section.set_expanded(False)
            window.image_tools_section.set_expanded(True)
            window.roi_editor_section.set_expanded(True)
            window.background_section.set_expanded(True)
            window.analysis_section.set_expanded(True)
        finally:
            window._suspend_collapsible_accordion = False
        window.left_tabs.blockSignals(True)
        try:
            window.left_tabs.setCurrentIndex(0)
        finally:
            window.left_tabs.blockSignals(False)
        self.save_layout_preferences()
        window._set_status_text("Layout reset to defaults.")

    def reset_panel_layout(self) -> None:
        window = self.window
        window._panel_layout_visibility_backup = None
        visibility = {name: panel.isVisible() for name, panel in self.panel_layout_panels()}
        self.restore_default_panel_layout()
        for name, panel in self.panel_layout_panels():
            panel.setVisible(visibility.get(name, panel.isVisible()))
        self.save_panel_layout_preferences()
        window._set_status_text("Panel layout reset to defaults.")

    def expand_left_panels(self) -> None:
        window = self.window
        window._suspend_collapsible_accordion = True
        try:
            for section in [
                window.dataset_section,
                window.mask_section,
                window.chromatic_section,
                window.image_tools_section,
                window.roi_editor_section,
                window.background_section,
                window.analysis_section,
            ]:
                section.set_expanded(True)
        finally:
            window._suspend_collapsible_accordion = False
        self.save_layout_preferences()

    def collapse_left_panels(self) -> None:
        window = self.window
        window._suspend_collapsible_accordion = True
        try:
            for section in [
                window.dataset_section,
                window.mask_section,
                window.chromatic_section,
                window.image_tools_section,
                window.roi_editor_section,
                window.background_section,
                window.analysis_section,
            ]:
                section.set_expanded(False)
        finally:
            window._suspend_collapsible_accordion = False
        self.save_layout_preferences()

    def restore_default_panel_layout(self) -> None:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            window._main_splitter = QSplitter(Qt.Orientation.Horizontal, window)
            window._main_splitter.setObjectName("mainLayoutSplitter")
            window._main_splitter.setChildrenCollapsible(False)
            window._main_splitter.setHandleWidth(4)

            window._visual_splitter = QSplitter(Qt.Orientation.Vertical, window._main_splitter)
            window._visual_splitter.setObjectName("visualLayoutSplitter")
            window._visual_splitter.setChildrenCollapsible(False)
            window._visual_splitter.setHandleWidth(4)

            window._top_visual_splitter = QSplitter(Qt.Orientation.Horizontal, window._visual_splitter)
            window._top_visual_splitter.setObjectName("topVisualSplitter")
            window._top_visual_splitter.setChildrenCollapsible(False)
            window._top_visual_splitter.setHandleWidth(4)

            window._bottom_visual_splitter = QSplitter(Qt.Orientation.Horizontal, window._visual_splitter)
            window._bottom_visual_splitter.setObjectName("bottomVisualSplitter")
            window._bottom_visual_splitter.setChildrenCollapsible(False)
            window._bottom_visual_splitter.setHandleWidth(4)

            window._top_visual_splitter.addWidget(window.image_panel)
            window._top_visual_splitter.addWidget(window.histogram_panel)
            window._bottom_visual_splitter.addWidget(window.spectra_panel)
            window._bottom_visual_splitter.addWidget(window.sensorgram_panel)
            window._visual_splitter.addWidget(window._top_visual_splitter)
            window._visual_splitter.addWidget(window._bottom_visual_splitter)
            window._main_splitter.addWidget(window.workflow_panel)
            window._main_splitter.addWidget(window.roi_list_panel)
            window._main_splitter.addWidget(window._visual_splitter)
            window._workspace_root = QWidget(window)
            workspace_layout = QVBoxLayout(window._workspace_root)
            workspace_layout.setContentsMargins(0, 0, 0, 0)
            workspace_layout.setSpacing(0)
            workspace_layout.addWidget(window._main_splitter, 1)
            window.setCentralWidget(window._workspace_root)
        window.workflow_panel.setVisible(True)
        window.roi_list_panel.setVisible(window._settings_bool("layout/roi_list_visible", True))
        window.image_panel.setVisible(True)
        window.histogram_panel.setVisible(True)
        window.spectra_panel.setVisible(True)
        window.sensorgram_panel.setVisible(True)
        self.apply_default_splitter_sizes()

    def apply_default_splitter_sizes(self) -> None:
        window = self.window
        if not hasattr(window, "_main_splitter"):
            return
        window._main_splitter.setStretchFactor(0, 0)
        window._main_splitter.setStretchFactor(1, 0)
        window._main_splitter.setStretchFactor(2, 1)
        window._visual_splitter.setStretchFactor(0, 1)
        window._visual_splitter.setStretchFactor(1, 1)
        window._top_visual_splitter.setStretchFactor(0, 4)
        window._top_visual_splitter.setStretchFactor(1, 1)
        window._bottom_visual_splitter.setStretchFactor(0, 1)
        window._bottom_visual_splitter.setStretchFactor(1, 1)
        if window._main_splitter.count() == 3:
            window._main_splitter.setSizes([360, max(260, window.roi_list_panel.minimumWidth()), 1200])
        if window._visual_splitter.count() == 2:
            window._visual_splitter.setSizes([760, 420])
        if window._top_visual_splitter.count() == 2:
            window._top_visual_splitter.setSizes([980, 300])
        if window._bottom_visual_splitter.count() == 2:
            window._bottom_visual_splitter.setSizes([640, 640])
