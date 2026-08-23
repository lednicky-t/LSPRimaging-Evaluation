from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, tint_tabler_icon

# Small translucent icon/label rows pinned to a pyqtgraph plot's own corners -
# ported from singleLSPR Acquisition's spectrum/sensorgram corner-overlay
# pattern (apps/sLSPR/acq/src/lspr_app/gui/main_window_plotting.py, see
# _CornerOverlayContainer et al.), adapted for this app's dark-only theme
# (get_active_theme() instead of a light/dark theme_mode string).

_OVERLAY_MARGIN_PX = 6


def _off_icon_color() -> QColor:
    return QColor(get_active_theme().text_dim)


def crosshair_off_icon() -> QIcon:
    return tint_tabler_icon(load_tabler_icon("crosshair"), _off_icon_color())


def stats_off_icon() -> QIcon:
    return tint_tabler_icon(load_tabler_icon("list"), _off_icon_color())


def settings_overlay_icon() -> QIcon:
    return tint_tabler_icon(load_tabler_icon("settings"), _off_icon_color())


class CornerOverlayContainer(QWidget):
    """Translucent widget pinned over a plot's viewport corner. Forwards
    right-clicks through to the plot's own ViewBox context menu so the
    overlay doesn't shadow it (mirrors sLSPR acq's _CornerOverlayContainer)."""

    def __init__(self, plot_widget: pg.PlotWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._plot_widget = plot_widget

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        view_box = self._plot_widget.getViewBox()
        if view_box is not None and hasattr(view_box, "raiseContextMenu"):
            view_box.raiseContextMenu(event)
            return
        super().contextMenuEvent(event)


def style_overlay_container(container: QWidget) -> None:
    theme = get_active_theme()
    bg = QColor(theme.window_bg)
    container.setStyleSheet(
        f"""
        QWidget#{container.objectName()} {{
            background: rgba({bg.red()}, {bg.green()}, {bg.blue()}, 190);
            border-radius: 6px;
        }}
        QLabel {{
            background: transparent;
            color: {theme.text_secondary};
            font-size: 9pt;
        }}
        """
    )


def make_overlay_container(plot_widget: pg.PlotWidget, object_name: str) -> tuple[CornerOverlayContainer, QHBoxLayout]:
    viewport = plot_widget.viewport()
    container = CornerOverlayContainer(plot_widget, viewport)
    container.setObjectName(object_name)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(6, 3, 6, 3)
    layout.setSpacing(4)
    style_overlay_container(container)
    return container, layout


def reposition_overlay(plot_widget: pg.PlotWidget, container: QWidget | None, *, align: str, vertical: str = "top") -> None:
    """Pin `container` to a corner of `plot_widget`'s ViewBox, in the
    ViewBox's own scene coordinates (not the plot widget's fixed corner) so
    it tracks axis/legend layout changes, same as sLSPR acq's
    _reposition_corner_overlay."""
    if container is None or not container.isVisible():
        return
    view_box = plot_widget.getViewBox()
    if view_box is None:
        return
    scene_rect = view_box.sceneBoundingRect()
    top_left = plot_widget.mapFromScene(scene_rect.topLeft())
    top_right = plot_widget.mapFromScene(scene_rect.topRight())
    bottom_left = plot_widget.mapFromScene(scene_rect.bottomLeft())
    container.adjustSize()
    if align == "left":
        x = top_left.x() + _OVERLAY_MARGIN_PX
    else:
        x = top_right.x() - container.width() - _OVERLAY_MARGIN_PX
    if vertical == "top":
        y = top_left.y() + _OVERLAY_MARGIN_PX
    else:
        y = bottom_left.y() - container.height() - _OVERLAY_MARGIN_PX
    container.move(int(x), int(y))
    container.raise_()


def show_off_state(label: QLabel, off_icon: QIcon, tooltip: str) -> None:
    """Collapse a corner-overlay label to a small inactive-state icon, sized
    to match the label's own font so it reads as part of the same row."""
    size = label.fontMetrics().height()
    label.setPixmap(off_icon.pixmap(size, size))
    label.setToolTip(tooltip)
