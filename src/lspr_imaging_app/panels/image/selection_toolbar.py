"""The always-visible selection toolbar at the left edge of the image (2026-11-14, maintainer request).

**The idea (the maintainer's Photoshop metaphor).** Selection tools work on the *image* and make a selection (a pixel
map); the mask and the ROIs are layers over the image. The selection does nothing by itself except limit where the
editing tools act; turning it into something is an explicit action of the layer: the Mask tab's *Add selection* /
*Subtract selection* (and, later, an ROI made from a selection - its reference region and "one ROI per blob or one
for all" are decisions still open, `docs/selection_wand_2026-11-14.md`). So the tools left the Mask / ROIs / View
tabs, where the picker sat until now (`select_group.py`, `general_group.AreaSelectionPicker`, deleted 2026-11-15).
Right-click with a tool armed: "Select ROIs in selection" (the ROIs inside or partly inside it, `interaction.py`).

Screen purpose: make and refine a selection on the image from any ribbon tab.
Main user actions: pick Rectangle / Lasso / Wand (picking the armed one again leaves the tool, the selection stays);
the wand's settings (right-click the wand button: a pop-up beside it, 2026-11-15; there is no gear button); scroll the
wheel over the wand button to switch its method (Contrast / Brightness / Edges, wrapping round; the button's icon shows
the picked method: the wand with a half-filled disc, a sun or a dotted outline beside it); Deselect. Invert stays
in the image's right-click menu. Dragging with the right button on the image (wand armed) sets the threshold and Grow
(`wand_drag.py`). Every selection change is an undo step (Ctrl+Z, `selection/area_selection_module.py`).
Layout: a narrow column of icon buttons left of the image canvas, below the ribbon.
Important states: which tool is armed (blue icon); the wand computing (its icon shows the busy tool tip); no image
(the wand says so in the status bar).
Validation: the spin boxes bound every value; Background width is used only by the Contrast method (disabled
otherwise); only the threshold of the picked method is shown. There is no size limit (2026-11-15: the selection is
undoable, so the "Max size" guard went).
Error messages: from the wand (`selection_wand.WandResult.message`) in the status bar: nothing different from the
background, no background found for an edge, outside the rotated image.

Real `QToolButton`s (`.click()` works in tests, GUI rule). Every setting is remembered (`bind_ui_state`).
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QMenu,
    QSpinBox,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ...image_tools import ActiveToolModule, ImageTool
from ...image_tools.selection_wand import WandMethod, WandSettings
from ...selection import AreaSelectionMode, AreaSelectionModule
from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from ..ui_state import UiStateStore

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue (as the old picker)
# As narrow as the icons (maintainer, 2026-11-15: "as narrow as possible", then "not symmetric"): no frame margin,
# buttons exactly as wide as the icon; the buttons keep the ribbon's height. The image's pyqtgraph layout has no left
# margin (`panel.py`), so the gap right of the icons is as small as the one left of them (the dock card's gap).
_BAR_WIDTH = ICON_SIZE + 4  # then "a little padding, 1 or 2 px": 2 px each side of the icon


def _style_button(button: QToolButton) -> None:
    style_general_icon_button(button)
    button.setFixedSize(_BAR_WIDTH, BUTTON_SIZE)


class _ToolButton(QToolButton):
    """A tool button that reports a turn of the mouse wheel over it as whole steps (`wheel_steps`: +1 / -1 per notch,
    scrolling up is -1: back through a list). A smooth wheel or a touch pad sends many small deltas: they are added up
    until a whole notch (120) is reached."""

    wheel_steps = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel_delta = 0

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._wheel_delta += event.angleDelta().y()
        notches = int(self._wheel_delta / 120)
        if notches:
            self._wheel_delta -= notches * 120
            self.wheel_steps.emit(-notches)
        event.accept()


_TOOLS: tuple[tuple[AreaSelectionMode, str, str], ...] = (
    (AreaSelectionMode.RECTANGLE, "select-rectangle", "Rectangular selection: drag on the image."),
    (AreaSelectionMode.LASSO, "select-lasso", "Lasso selection: draw around an area on the image."),
    (
        AreaSelectionMode.WAND,
        "wand",
        "Magic wand: click a feature to select it. Shift+click adds to the selection, Alt+click takes away. "
        "Settings: right-click this button. Method: scroll the wheel over this button. "
        "On the image, drag with the right button: right = bigger region (threshold), up = Grow.",
    ),
)

# The wand button shows the picked method (2026-11-15): the Tabler wand with a small glyph where its second sparkle was -
# contrast = a half-filled disc, brightness = a sun, edges = a dotted outline. The three SVGs are drawn by hand in Tabler's
# style (24 x 24, stroke 2), vendored in `lspr_ui/icon_assets/`; none is the Rectangle's or the Lasso's icon.
_METHOD_ICONS = {
    WandMethod.CONTRAST: "wand-contrast",
    WandMethod.BRIGHTNESS: "wand-brightness",
    WandMethod.EDGES: "wand-edges",
}

_METHOD_LABELS = (
    (WandMethod.CONTRAST, "Contrast to background"),
    (WandMethod.BRIGHTNESS, "Brightness (similar value)"),
    (WandMethod.EDGES, "Edges (stop at an outline)"),
)


class WandSettingsForm(QWidget):
    """The wand's settings, shown in the gear button's pop-up. `settings()` is what a click uses."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        defaults = WandSettings()
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.method = QComboBox(self)
        for method, label in _METHOD_LABELS:
            self.method.addItem(label, method.value)
        self.method.setToolTip(
            "Contrast: pixels that stand out from the smooth local background (bright or dark features, uneven light).\n"
            "Brightness: pixels with a value close to the clicked one (the classic wand).\n"
            "Edges: grows from the click until a strong edge, the border placed on the steepest point of the edge."
        )
        form.addRow("Method", self.method)

        def sigma_box(value: float, tip: str) -> QDoubleSpinBox:
            box = QDoubleSpinBox(self)
            box.setRange(0.5, 100.0)
            box.setSingleStep(0.5)
            box.setDecimals(1)
            box.setSuffix(" σ")
            box.setValue(value)
            box.setToolTip(tip)
            return box

        self.contrast_threshold = sigma_box(
            defaults.contrast_threshold,
            "How far (in noise sigmas) a pixel must stand out from the background to belong. Higher = only the core.",
        )
        self.brightness_tolerance = sigma_box(
            defaults.brightness_tolerance,
            "How far (in noise sigmas) a pixel's value may be from the clicked one. Higher = bigger region.",
        )
        self.edge_threshold = sigma_box(
            defaults.edge_threshold,
            "How strong (in sigmas of the gradient noise) an edge must be to stop the region. Higher = weaker edges are crossed.",
        )
        self._threshold_label = QLabel(self)
        threshold_cell = QWidget(self)
        threshold_layout = QVBoxLayout(threshold_cell)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        for box in (self.contrast_threshold, self.brightness_tolerance, self.edge_threshold):
            threshold_layout.addWidget(box)
        form.addRow(self._threshold_label, threshold_cell)

        self.smoothing = QDoubleSpinBox(self)
        self.smoothing.setRange(0.0, 10.0)
        self.smoothing.setSingleStep(0.5)
        self.smoothing.setDecimals(1)
        self.smoothing.setSuffix(" px")
        self.smoothing.setValue(defaults.smoothing_px)
        self.smoothing.setToolTip("Gaussian smoothing before deciding (sigma). Keeps the region from breaking up on pixel noise.")
        form.addRow("Smoothing", self.smoothing)

        self.background = QDoubleSpinBox(self)
        self.background.setRange(2.0, 300.0)
        self.background.setSingleStep(2.0)
        self.background.setDecimals(0)
        self.background.setSuffix(" px")
        self.background.setValue(defaults.background_sigma_px)
        self.background.setToolTip(
            "Width (sigma) of the smooth local background (Contrast method only). Keep it well above the feature's size: "
            "a feature as wide as this pulls the background with it."
        )
        form.addRow("Background", self.background)

        self.grow = QSpinBox(self)
        self.grow.setRange(0, 100)
        self.grow.setSuffix(" px")
        self.grow.setValue(defaults.grow_px)
        self.grow.setToolTip("Margin added around the region (covers a faint halo). Masks are on/off per pixel, so this replaces feathering.")
        form.addRow("Grow", self.grow)

        self.fill_holes = QCheckBox("Fill holes", self)
        self.fill_holes.setChecked(defaults.fill_holes)
        self.fill_holes.setToolTip("Fill gaps inside the region (a ring-shaped particle becomes a solid one).")
        form.addRow("", self.fill_holes)

        for widget in (self.contrast_threshold, self.brightness_tolerance, self.edge_threshold, self.smoothing, self.background, self.grow):
            widget.valueChanged.connect(lambda _value: self.changed.emit())
        self.fill_holes.toggled.connect(lambda _on: self.changed.emit())
        self.method.currentIndexChanged.connect(lambda _index: self._on_method_changed())
        self._on_method_changed()

    def _method(self) -> WandMethod:
        return WandMethod(self.method.currentData())

    def method_label(self) -> str:
        return self.method.currentText()

    def picked_method(self) -> WandMethod:
        return self._method()

    def cycle_method(self, steps: int) -> str:
        """Move `steps` places along the method list, wrapping round (the wheel over the wand button); returns the label of
        the method now picked."""
        count = self.method.count()
        self.method.setCurrentIndex((self.method.currentIndex() + int(steps)) % count)
        return self.method_label()

    def threshold_box(self) -> QDoubleSpinBox:
        """The spin box of the picked method's threshold (the one the form shows): what the right-drag on the image sets."""
        return {
            WandMethod.CONTRAST: self.contrast_threshold,
            WandMethod.BRIGHTNESS: self.brightness_tolerance,
            WandMethod.EDGES: self.edge_threshold,
        }[self._method()]

    def threshold_caption(self) -> str:
        return self._threshold_label.text()

    def grows_with_value(self) -> bool:
        """Whether a higher number in `threshold_box` makes the region bigger. Brightness (tolerance) and Edges (the edge
        strength that is still crossed) do; Contrast does not (a higher threshold keeps only the strongest part)."""
        return self._method() is not WandMethod.CONTRAST

    def _on_method_changed(self) -> None:
        method = self._method()
        self.contrast_threshold.setVisible(method is WandMethod.CONTRAST)
        self.brightness_tolerance.setVisible(method is WandMethod.BRIGHTNESS)
        self.edge_threshold.setVisible(method is WandMethod.EDGES)
        self._threshold_label.setText(
            {WandMethod.CONTRAST: "Threshold", WandMethod.BRIGHTNESS: "Tolerance", WandMethod.EDGES: "Edge"}[method]
        )
        self.background.setEnabled(method is WandMethod.CONTRAST)
        self.changed.emit()

    def settings(self) -> WandSettings:
        return WandSettings(
            method=self._method(),
            brightness_tolerance=float(self.brightness_tolerance.value()),
            contrast_threshold=float(self.contrast_threshold.value()),
            edge_threshold=float(self.edge_threshold.value()),
            smoothing_px=float(self.smoothing.value()),
            background_sigma_px=float(self.background.value()),
            grow_px=int(self.grow.value()),
            fill_holes=bool(self.fill_holes.isChecked()),
        )

    def bind_ui_state(self, store: UiStateStore) -> None:
        store.bind("selection/wand_method", self.method)
        store.bind("selection/wand_contrast_threshold", self.contrast_threshold)
        store.bind("selection/wand_brightness_tolerance", self.brightness_tolerance)
        store.bind("selection/wand_edge_threshold", self.edge_threshold)
        store.bind("selection/wand_smoothing_px", self.smoothing)
        store.bind("selection/wand_background_px", self.background)
        store.bind("selection/wand_grow_px", self.grow)
        store.bind("selection/wand_fill_holes", self.fill_holes)


class SelectionToolbar(QFrame):
    """Rectangle / Lasso / Wand (settings: right-click the wand), Deselect - one column beside the image."""

    def __init__(self, selection: AreaSelectionModule, active_tool: ActiveToolModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("selectionToolbar")
        self._selection = selection
        self._active_tool = active_tool
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self._wand_busy = False
        self._tool_buttons: dict[AreaSelectionMode, QToolButton] = {}
        for mode, icon_name, tip in _TOOLS:
            button = _ToolButton(self)
            _style_button(button)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(lambda _checked=False, picked=mode: self._pick(picked))
            layout.addWidget(button)
            self._tool_buttons[mode] = button

        # The wand's settings pop up beside the wand button on a right-click (no extra button, so no menu arrow either:
        # the button has no `setMenu`, the pop-up is shown by hand).
        self.wand_form = WandSettingsForm(self)
        self._wand_menu = QMenu(self)
        form_action = QWidgetAction(self._wand_menu)
        form_action.setDefaultWidget(self.wand_form)
        self._wand_menu.addAction(form_action)
        wand_button = self._tool_buttons[AreaSelectionMode.WAND]
        wand_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        wand_button.customContextMenuRequested.connect(lambda _pos: self.show_wand_settings())
        # The wheel over the wand button switches its method; the tool tip always names the method in use.
        wand_button.wheel_steps.connect(self._on_wand_wheel)  # type: ignore[attr-defined]
        self._icon_method = self.wand_form.picked_method()  # the method the wand button's icon was drawn for
        self.wand_form.changed.connect(self._on_wand_form_changed)
        self._refresh_wand_tip()

        self._deselect_button = QToolButton(self)
        _style_button(self._deselect_button)
        self._deselect_button.setToolTip("Deselect (Invert: right-click inside the selection)")
        self._deselect_button.clicked.connect(lambda _checked=False: self._selection.clear())
        layout.addWidget(self._deselect_button)
        layout.addStretch(1)
        self.setFixedWidth(_BAR_WIDTH)

        selection.mode_changed.connect(lambda _mode: self._refresh())
        selection.selection_changed.connect(self._refresh)
        active_tool.active_tool_changed.connect(lambda _tool: self._refresh())
        self.refresh_theme(get_active_theme())

    def tool_button(self, mode: AreaSelectionMode) -> QToolButton:
        return self._tool_buttons[mode]

    @property
    def wand_menu(self) -> QMenu:
        return self._wand_menu

    def show_wand_settings(self) -> None:
        """Open the wand's settings pop-up at the right edge of the wand button (what a right-click on it does)."""
        button = self._tool_buttons[AreaSelectionMode.WAND]
        self._wand_menu.popup(button.mapToGlobal(QPoint(button.width(), 0)))

    @property
    def deselect_button(self) -> QToolButton:
        return self._deselect_button

    def set_wand_busy(self, busy: bool) -> None:
        self._wand_busy = busy
        self._refresh_wand_tip()

    def _on_wand_form_changed(self) -> None:
        """Any wand setting changed (also every step of a right-drag): the tool tip is cheap; the icon is redrawn only when the
        method itself changed."""
        self._refresh_wand_tip()
        if self.wand_form.picked_method() is not self._icon_method:
            self._refresh()

    def _refresh_wand_tip(self) -> None:
        button = self._tool_buttons[AreaSelectionMode.WAND]
        busy = "Magic wand: working...\n" if self._wand_busy else ""
        button.setToolTip(f"{busy}Method: {self.wand_form.method_label()}\n{_TOOLS[2][2]}")

    def _on_wand_wheel(self, steps: int) -> None:
        """The wheel over the wand button: the previous / next method, said in a tool tip beside the button."""
        label = self.wand_form.cycle_method(steps)
        button = self._tool_buttons[AreaSelectionMode.WAND]
        QToolTip.showText(button.mapToGlobal(QPoint(button.width(), 0)), f"Wand method: {label}", button)

    def _armed(self) -> bool:
        return self._active_tool.active() is ImageTool.SELECT_AREA

    def _pick(self, mode: AreaSelectionMode) -> None:
        """Arm the tool; picking the armed one again leaves it (the selection stays)."""
        if self._armed() and self._selection.mode() is mode:
            self._active_tool.set_active(ImageTool.SELECT_AREA, False)
        else:
            self._selection.set_mode(mode)
            self._active_tool.set_active(ImageTool.SELECT_AREA, True)
        self._refresh()

    def _refresh(self) -> None:
        armed = self._armed()
        theme = get_active_theme()
        for mode, button in self._tool_buttons.items():
            on = armed and self._selection.mode() is mode
            blocked = button.blockSignals(True)
            try:
                button.setChecked(on)
            finally:
                button.blockSignals(blocked)
            color = _ACTIVE_COLOR if on else theme.text_primary
            icon_name = str(button.property("icon_name"))
            if mode is AreaSelectionMode.WAND:
                self._icon_method = self.wand_form.picked_method()
                icon_name = _METHOD_ICONS[self._icon_method]
            button.setIcon(load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self._deselect_button.setEnabled(self._selection.has_selection())

    def refresh_theme(self, theme: GuiTheme) -> None:
        for button in (*self._tool_buttons.values(), self._deselect_button):
            _style_button(button)
        self._deselect_button.setIcon(load_tabler_icon("square-off", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self.setStyleSheet("#selectionToolbar { border: none; }")
        self._refresh()
