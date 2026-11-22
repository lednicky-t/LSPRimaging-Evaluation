"""The Layers button of the selection toolbar and its pop-up (2026-11-16, maintainer request; the layers themselves: `layers.py`).

Screen purpose: see which layer the selection tools work on, change it, and show / hide each layer.
Main user actions: the wheel over the button steps the active layer (Ignore mask / ROI / Image, wrapping); a click opens the pop-up; in the
pop-up a click on a layer's eye shows / hides it, a click on its name makes it the active layer.
Layout: the button is pinned at the top of the toolbar, under the View eye. Its face is the active layer's icon with a small layer stack in
the corner; dimmed while that layer is hidden. The pop-up lists the three layers top to bottom as they are stacked (Photoshop's Layers
panel): an eye, the layer's icon and its name; the active row is highlighted.
Important states: active layer hidden (dimmed face; the tool tip and the tools say so).
Validation / errors: none (three fixed layers).
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QToolTip, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ..ribbon_icon_size import ICON_SIZE
from ..ui_state import UiStateStore
from .layers import IMAGE, LAYER_NAMES, LAYERS, ROI, ImageLayers
from .roi_icons import display_icon
from .side_rails import LEFT, beside_point, tooltip_point, valid_side
from .wheel_tool_button import WheelToolButton

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the toolbar's tool blue

_TIP = (
    "Layers: the selection tools work on the active layer (this icon).\n"
    "Wheel: change the active layer. Click: show / hide layers, pick the active one.\n"
    "Image: the wand picks image features, Histogram range / Threshold / Local contrast work.\n"
    "ROI: the wand picks the ROI disk or reference rim under the click.\n"
    "Ignore mask: the wand picks the mask blob under the click.\n"
    "Rectangle, Lasso, Draw and Modify work the same on every layer."
)


def layer_icon(layer: str, color: str) -> QIcon:
    """The picture of a layer, in one colour (the ROI layer's is the ROIs tab's disk-in-ring)."""
    if layer == ROI:
        return display_icon(color, color)
    return load_tabler_icon("photo" if layer == IMAGE else "mask", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


def button_face(layer: str, color: str, badge_color: str) -> QIcon:
    """The Layers button's face: the active layer's icon, with a small layer stack in the bottom-right corner (it is the Layers button)."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    main = int(_RENDER_SIZE * 0.78)
    painter.drawPixmap(0, 0, layer_icon(layer, color).pixmap(QSize(main, main)))
    badge = int(_RENDER_SIZE * 0.5)
    stack = load_tabler_icon("stack-3", color=badge_color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH * 1.2)
    painter.drawPixmap(_RENDER_SIZE - badge, _RENDER_SIZE - badge, stack.pixmap(QSize(badge, badge)))
    painter.end()
    return QIcon(pixmap)


class LayersForm(QWidget):
    """The pop-up's three rows: an eye (show / hide) and the layer's name with its icon (click = make it active)."""

    def __init__(self, layers: ImageLayers, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layers = layers
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        self.eye_buttons: dict[str, QToolButton] = {}
        self.name_buttons: dict[str, QToolButton] = {}
        for layer in LAYERS:
            row = QHBoxLayout()
            row.setSpacing(4)
            eye = QToolButton(self)
            eye.setCheckable(True)
            eye.setAutoRaise(True)
            eye.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            eye.clicked.connect(lambda checked, picked=layer: self._layers.set_visible(picked, checked))
            name = QToolButton(self)
            name.setCheckable(True)
            name.setAutoRaise(True)
            name.setText(LAYER_NAMES[layer])
            name.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            name.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            name.setMinimumWidth(150)
            name.setToolTip(f"Make {LAYER_NAMES[layer]} the active layer: the wand works on it.")
            name.clicked.connect(lambda _checked=False, picked=layer: self._layers.set_active(picked))
            row.addWidget(eye)
            row.addWidget(name, 1)
            layout.addLayout(row)
            self.eye_buttons[layer] = eye
            self.name_buttons[layer] = name
        layers.active_changed.connect(lambda _layer: self.refresh())
        layers.visibility_changed.connect(lambda _layer, _shown: self.refresh())
        self.refresh()

    def refresh(self, theme: GuiTheme | None = None) -> None:
        theme = theme or get_active_theme()
        active = self._layers.active()
        for layer in LAYERS:
            shown = self._layers.is_visible(layer)
            eye = self.eye_buttons[layer]
            blocked = eye.blockSignals(True)
            eye.setChecked(shown)
            eye.blockSignals(blocked)
            eye.setIcon(load_tabler_icon("eye" if shown else "eye-off", color=theme.text_primary if shown else theme.text_dim, size=_RENDER_SIZE))
            eye.setToolTip(f"{'Hide' if shown else 'Show'} the {LAYER_NAMES[layer]} layer.")
            name = self.name_buttons[layer]
            blocked = name.blockSignals(True)
            name.setChecked(layer == active)  # a click on the active row keeps it active
            name.blockSignals(blocked)
            color = _ACTIVE_COLOR if layer == active else (theme.text_primary if shown else theme.text_dim)
            name.setIcon(layer_icon(layer, color))
            weight = "bold" if layer == active else "normal"
            name.setStyleSheet(f"QToolButton {{ text-align: left; font-weight: {weight}; color: {color}; }}")


class LayersButton(WheelToolButton):
    """Pinned at the top of the selection toolbar: shows the active layer, wheel = change it, click = the pop-up (`LayersForm`)."""

    def __init__(self, layers: ImageLayers, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layers = layers
        self._side = LEFT  # the side of the image the toolbar stands on (`set_side`)
        self.form = LayersForm(layers, self)
        self.menu = QMenu(self)
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(self.form)
        self.menu.addAction(action)
        self.clicked.connect(lambda _checked=False: self.show_menu())
        self.wheel_steps.connect(self._on_wheel)
        layers.active_changed.connect(lambda _layer: self.refresh())
        layers.visibility_changed.connect(lambda _layer, _shown: self.refresh())
        self.refresh()

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the Image layer's eye and the active layer (the toolbar's `bind_ui_state` calls this)."""
        self._layers.bind_ui_state(store)

    def set_side(self, side: str) -> None:
        """The side of the image the toolbar stands on (`SelectionToolbar.set_side`): the pop-up and the tool tip open over the image."""
        self._side = valid_side(side, self._side)

    def show_menu(self) -> None:
        self.form.refresh()
        self.menu.ensurePolished()
        self.menu.popup(self.mapToGlobal(beside_point(self, self._side, self.menu.sizeHint().width())))

    def _on_wheel(self, steps: int) -> None:
        layer = self._layers.cycle(steps)
        hidden = "" if self._layers.is_visible(layer) else " (hidden)"
        text = f"Active layer: {LAYER_NAMES[layer]}{hidden}"
        QToolTip.showText(tooltip_point(self, self._side, text), text, self)

    def refresh(self, theme: GuiTheme | None = None) -> None:
        theme = theme or get_active_theme()
        layer = self._layers.active()
        shown = self._layers.is_visible(layer)
        color = theme.text_primary if shown else theme.text_dim
        self.setIcon(button_face(layer, color, _ACTIVE_COLOR if shown else theme.text_dim))
        state = "" if shown else "\nThis layer is hidden: the wand does nothing on it until it is shown."
        self.setToolTip(f"Active layer: {LAYER_NAMES[layer]}{state}\n\n{_TIP}")
        self.form.refresh(theme)
