from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import QLabel, QToolButton

from lspr_ui import APP_THEME, transparent_icon_button_stylesheet

from lspr_imaging_app.gui.plot_corner_overlay import (
    crosshair_off_icon,
    make_overlay_container,
    reposition_overlay,
    settings_overlay_icon,
    show_off_state,
    stats_off_icon,
)
from lspr_imaging_app.gui.plot_style_settings_dialog import (
    show_sensorgram_plot_settings_dialog_for,
    show_spectrum_plot_settings_dialog_for,
)

_CURSOR_TOOLTIP = "{name} cursor readout under the mouse pointer. Click to show/hide (also hides the plot crosshair)."
_STATS_TOOLTIP = "{name} stats. Click to show/hide."


class PlotOverlayController:
    """Owns the small corner-overlay icon rows on the Spectra and Sensogram
    plots: a stats readout (top-left, click to show/hide), a crosshair
    cursor readout (top-right, click to show/hide), and a settings icon
    (top-right, opens a plot-style dialog). Ported from singleLSPR
    Acquisition's spectrum/sensorgram corner overlay - see
    apps/sLSPR/acq/src/lspr_app/gui/main_window_plotting.py and
    gui/plot_controller.py in that app for the original pattern this mirrors.
    Not persisted across restarts: matches this app's existing image-view
    cursor-readout toggle (ImageInteractionController), which also resets to
    off on every launch."""

    def __init__(self, window) -> None:
        self.window = window

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def build_spectrum_overlays(self) -> None:
        self._build_overlays(
            plot_widget=self.window.spectrum_plot,
            name="Spectrum",
            prefix="_spectrum",
            settings_tooltip="Spectra plot settings.",
            open_settings=self.open_spectrum_settings,
            on_mouse_moved=self._handle_spectrum_mouse_moved,
            reposition=self.reposition_spectrum_overlays,
        )

    def build_sensorgram_overlays(self) -> None:
        self._build_overlays(
            plot_widget=self.window.sensorgram_plot,
            name="Sensogram",
            prefix="_sensorgram",
            settings_tooltip="Sensogram plot settings.",
            open_settings=self.open_sensorgram_settings,
            on_mouse_moved=self._handle_sensorgram_mouse_moved,
            reposition=self.reposition_sensorgram_overlays,
        )

    def _build_overlays(
        self,
        *,
        plot_widget: pg.PlotWidget,
        name: str,
        prefix: str,
        settings_tooltip: str,
        open_settings,
        on_mouse_moved,
        reposition,
    ) -> None:
        window = self.window
        cursor_tooltip = _CURSOR_TOOLTIP.format(name=name)
        stats_tooltip = _STATS_TOOLTIP.format(name=name)

        vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#666666", width=1))
        hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#666666", width=1))
        vline.setVisible(False)
        hline.setVisible(False)
        plot_widget.addItem(vline, ignoreBounds=True)
        plot_widget.addItem(hline, ignoreBounds=True)
        setattr(window, f"{prefix}_crosshair_vline", vline)
        setattr(window, f"{prefix}_crosshair_hline", hline)

        stats_container, stats_layout = make_overlay_container(plot_widget, f"{prefix}StatsOverlay")
        stats_label = QLabel(stats_container)
        stats_label.setToolTip(stats_tooltip)
        stats_layout.addWidget(stats_label)
        stats_label.installEventFilter(window)
        setattr(window, f"{prefix}_stats_container", stats_container)
        setattr(window, f"{prefix}_stats_label", stats_label)

        action_container, action_layout = make_overlay_container(plot_widget, f"{prefix}ActionOverlay")
        cursor_label = QLabel(action_container)
        cursor_label.setToolTip(cursor_tooltip)
        settings_button = self._make_settings_button(action_container, settings_tooltip)
        action_layout.addWidget(cursor_label)
        action_layout.addWidget(settings_button)
        cursor_label.installEventFilter(window)
        settings_button.clicked.connect(open_settings)
        setattr(window, f"{prefix}_action_container", action_container)
        setattr(window, f"{prefix}_cursor_label", cursor_label)
        setattr(window, f"{prefix}_settings_button", settings_button)

        show_off_state(stats_label, stats_off_icon(), stats_tooltip)
        show_off_state(cursor_label, crosshair_off_icon(), cursor_tooltip)
        stats_container.adjustSize()
        action_container.adjustSize()
        stats_container.show()
        action_container.show()

        proxy = pg.SignalProxy(plot_widget.scene().sigMouseMoved, rateLimit=60, slot=on_mouse_moved)
        setattr(window, f"{prefix}_mouse_proxy", proxy)
        plot_widget.getViewBox().sigResized.connect(reposition)
        reposition()

    @staticmethod
    def _make_settings_button(parent, tooltip: str) -> QToolButton:
        button = QToolButton(parent)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIcon(settings_overlay_icon())
        button.setIconSize(QSize(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner))
        button.setFixedSize(APP_THEME.compact_icon_outer, APP_THEME.compact_icon_outer)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(transparent_icon_button_stylesheet())
        return button

    # ------------------------------------------------------------------
    # Repositioning (kept in sync with the ViewBox corner via sigResized)
    # ------------------------------------------------------------------

    def reposition_spectrum_overlays(self) -> None:
        window = self.window
        reposition_overlay(window.spectrum_plot, getattr(window, "_spectrum_stats_container", None), align="left")
        reposition_overlay(window.spectrum_plot, getattr(window, "_spectrum_action_container", None), align="right")

    def reposition_sensorgram_overlays(self) -> None:
        window = self.window
        reposition_overlay(window.sensorgram_plot, getattr(window, "_sensorgram_stats_container", None), align="left")
        reposition_overlay(window.sensorgram_plot, getattr(window, "_sensorgram_action_container", None), align="right")

    # ------------------------------------------------------------------
    # Click-to-toggle (single-click; unlike sLSPR acq's sensorgram stats
    # label, cursor and stats live on separate widgets here, so there's no
    # collision to debounce with a double-click)
    # ------------------------------------------------------------------

    def handle_event(self, watched, event) -> bool:
        window = self.window
        if event.type() != QEvent.Type.MouseButtonPress or event.button() != Qt.MouseButton.LeftButton:
            return False
        if watched is getattr(window, "_spectrum_stats_label", None):
            self.toggle_spectrum_stats()
            return True
        if watched is getattr(window, "_spectrum_cursor_label", None):
            self.toggle_spectrum_cursor()
            return True
        if watched is getattr(window, "_sensorgram_stats_label", None):
            self.toggle_sensorgram_stats()
            return True
        if watched is getattr(window, "_sensorgram_cursor_label", None):
            self.toggle_sensorgram_cursor()
            return True
        return False

    def toggle_spectrum_cursor(self) -> None:
        self._toggle_cursor(prefix="_spectrum", name="Spectrum", reposition=self.reposition_spectrum_overlays)

    def toggle_sensorgram_cursor(self) -> None:
        self._toggle_cursor(prefix="_sensorgram", name="Sensogram", reposition=self.reposition_sensorgram_overlays)

    def _toggle_cursor(self, *, prefix: str, name: str, reposition) -> None:
        window = self.window
        enabled_attr = f"{prefix}_cursor_enabled"
        enabled = not getattr(window, enabled_attr, False)
        setattr(window, enabled_attr, enabled)
        label = getattr(window, f"{prefix}_cursor_label")
        if enabled:
            label.setText("-")
        else:
            show_off_state(label, crosshair_off_icon(), _CURSOR_TOOLTIP.format(name=name))
        getattr(window, f"{prefix}_crosshair_vline").setVisible(enabled)
        getattr(window, f"{prefix}_crosshair_hline").setVisible(enabled)
        getattr(window, f"{prefix}_action_container").adjustSize()
        reposition()

    def toggle_spectrum_stats(self) -> None:
        self._toggle_stats(prefix="_spectrum", name="Spectrum", refresh=self._refresh_spectrum_stats, reposition=self.reposition_spectrum_overlays)

    def toggle_sensorgram_stats(self) -> None:
        self._toggle_stats(prefix="_sensorgram", name="Sensogram", refresh=self._refresh_sensorgram_stats, reposition=self.reposition_sensorgram_overlays)

    def _toggle_stats(self, *, prefix: str, name: str, refresh, reposition) -> None:
        window = self.window
        enabled_attr = f"{prefix}_stats_enabled"
        enabled = not getattr(window, enabled_attr, False)
        setattr(window, enabled_attr, enabled)
        if enabled:
            refresh()
        else:
            label = getattr(window, f"{prefix}_stats_label")
            show_off_state(label, stats_off_icon(), _STATS_TOOLTIP.format(name=name))
            getattr(window, f"{prefix}_stats_container").adjustSize()
            reposition()

    # ------------------------------------------------------------------
    # Mouse-move crosshair readout (rate-limited via pg.SignalProxy, snaps
    # the x position to the nearest actual data sample rather than the raw
    # mouse position - same idea as sLSPR acq's handle_spectrum_mouse_moved)
    # ------------------------------------------------------------------

    def _handle_spectrum_mouse_moved(self, event) -> None:
        self._handle_mouse_moved(
            event,
            plot_widget=self.window.spectrum_plot,
            prefix="_spectrum",
            curve=self.window.spectrum_curve,
            text_fn=lambda x, y: f"{x:.2f} nm, {y:.4f}",
            reposition=self.reposition_spectrum_overlays,
            refresh_stats=self._refresh_spectrum_stats,
        )

    def _handle_sensorgram_mouse_moved(self, event) -> None:
        self._handle_mouse_moved(
            event,
            plot_widget=self.window.sensorgram_plot,
            prefix="_sensorgram",
            curve=self.window.sensorgram_curve,
            text_fn=lambda x, y: f"{x:.2f}, {y:.4f}",
            reposition=self.reposition_sensorgram_overlays,
            refresh_stats=self._refresh_sensorgram_stats,
        )

    def _handle_mouse_moved(self, event, *, plot_widget, prefix, curve, text_fn, reposition, refresh_stats) -> None:
        window = self.window
        if not getattr(window, f"{prefix}_cursor_enabled", False):
            return
        pos = event[0]
        if not plot_widget.sceneBoundingRect().contains(pos):
            return
        mouse_point = plot_widget.getViewBox().mapSceneToView(pos)
        x = float(mouse_point.x())
        y = float(mouse_point.y())
        if curve is not None:
            x_data, y_data = curve.getData()
            if x_data is not None and len(x_data):
                index = int(np.argmin(np.abs(np.asarray(x_data, dtype=float) - x)))
                x = float(x_data[index])
                y = float(y_data[index])
        getattr(window, f"{prefix}_crosshair_vline").setPos(x)
        getattr(window, f"{prefix}_crosshair_hline").setPos(y)
        getattr(window, f"{prefix}_cursor_label").setText(text_fn(x, y))
        getattr(window, f"{prefix}_action_container").adjustSize()
        reposition()
        if getattr(window, f"{prefix}_stats_enabled", False):
            refresh_stats()

    # ------------------------------------------------------------------
    # Stats readout - deliberately minimal placeholder content for now
    # (peak/range for the spectrum, latest/min/max for the sensogram); what
    # goes here longer-term is still to be decided.
    # ------------------------------------------------------------------

    def _refresh_spectrum_stats(self) -> None:
        window = self.window
        window._spectrum_stats_label.setText(self._spectrum_stats_text() or "no data")
        window._spectrum_stats_container.adjustSize()
        self.reposition_spectrum_overlays()

    def _refresh_sensorgram_stats(self) -> None:
        window = self.window
        window._sensorgram_stats_label.setText(self._sensorgram_stats_text() or "no data")
        window._sensorgram_stats_container.adjustSize()
        self.reposition_sensorgram_overlays()

    def _spectrum_stats_text(self) -> str | None:
        curve = self.window.spectrum_curve
        if curve is None:
            return None
        x_data, y_data = curve.getData()
        if x_data is None or len(x_data) == 0:
            return None
        x = np.asarray(x_data, dtype=float)
        y = np.asarray(y_data, dtype=float)
        finite = np.isfinite(x) & np.isfinite(y)
        if not np.any(finite):
            return None
        x, y = x[finite], y[finite]
        peak_index = int(np.argmax(y))
        return f"peak {y[peak_index]:.4f} @ {x[peak_index]:.1f} nm\nrange {y.min():.4f} .. {y.max():.4f}"

    def _sensorgram_stats_text(self) -> str | None:
        curve = self.window.sensorgram_curve
        if curve is None:
            return None
        _x_data, y_data = curve.getData()
        if y_data is None or len(y_data) == 0:
            return None
        y = np.asarray(y_data, dtype=float)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return None
        return f"latest {y[-1]:.4f}\nmin {y.min():.4f}  max {y.max():.4f}"

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def open_spectrum_settings(self) -> None:
        show_spectrum_plot_settings_dialog_for(self.window)

    def open_sensorgram_settings(self) -> None:
        show_sensorgram_plot_settings_dialog_for(self.window)
