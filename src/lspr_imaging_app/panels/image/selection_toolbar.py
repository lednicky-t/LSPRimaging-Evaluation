"""The always-visible selection toolbar at the left edge of the image (2026-11-14, maintainer request; reorganised into sections 2026-11-15).

**The idea (the maintainer's Photoshop metaphor).** Selection tools work on the *image* and make a selection (a pixel
map); the mask and the ROIs are layers over the image. The selection does nothing by itself except limit where the
editing tools act; turning it into something is an explicit action of the layer: the Mask tab's *Add selection* /
*Subtract selection* / *Replace* / *Selection from mask* (and, later, an ROI made from a selection - its reference region and "one ROI per
blob or one for all" are decisions still open, `docs/selection_wand_2026-11-14.md`).
Right-click with a tool armed: "Select ROIs in selection" (the ROIs inside or partly inside it, `interaction.py`), Lock, Unlock, Invert,
Deselect (there is no Deselect button: it is in this menu).

Screen purpose: make and refine a selection on the image from any ribbon tab.
Layout: a narrow column of icon buttons left of the image canvas, below the ribbon, in **sections divided by horizontal lines** (maintainer,
2026-11-15; reordered 2026-11-16: View first, Lock last); the part under View scrolls (arrows at its ends) when the panel is too short for all
of it (`panels/scrolling_column.py`). Top to bottom:
  1. **View** (2026-11-16): an eye button **pinned at the top** of the toolbar (above the scrolling column, so it never scrolls away). Its pop-up
     (`selection_view_form.py`, like View -> Masks of the main ribbon) sets the colour of the selection's three outlines and an infill for each
     (5 opacity steps, none by default); the Venn circles of the mode button wear the same colours (`selection_style.py`).
  1b. **Layers** (2026-11-16, `layers.py`, `layers_button.py`, `docs/image_layers_2026-11-16.md`): pinned under View. Its face is the active
     layer's icon (Image / ROI / Ignore mask); the wheel changes the active layer, a click opens the layers with an eye each. The wand reads
     the active layer; Histogram range / Threshold / Local contrast are greyed out unless Image is active (`set_value_tools_allowed`).
  2. **Mode**: one button showing the Venn diagram of the picked mode (`combine_mode_icons.py`: pink circle = the selection now, green circle = the new region, grey fill = ends up selected);
     click = a menu of the four modes (Replace / Add / Subtract / Intersect), the wheel over it steps through them; remembered. Shift / Alt /
     Shift+Alt on the image override it for one selection.
  3. **Selection tools**: Rectangle, Lasso, Wand, Draw (the selection brush).
  4. **Select by value**: Histogram range, Threshold, Local contrast (`selection_by_value.py`), with the Mask tab's old icons.
  5. **Modify selection**: Grow / Shrink / Open / Close / Fill holes / Invert (`selection_modify.py`), with the old Morphology icon.
  6. **Lock / Unlock, one button** (2026-11-16): Lock turns the selection into the fence (`AreaSelectionModule.lock_selection`; enabled with a
     selection); while a lock is on the same button is Unlock (open padlock, amber) and leaves it. Locking the selection *inside* a lock (to
     narrow it) is the button's second face: the wheel over it flips Unlock <-> Lock (`lock_action()`; needs a selection, goes back to Unlock after
     a click); the right-click menu on the image has both entries too. They only ask (`lock_requested` / `unlock_requested`): the Image panel
     knows the image size and says in the status bar what happened.
Main user actions: pick a tool (picking the armed one again leaves it, the selection stays). **Every tool with numbers has them in a pop-up on a
right-click of its button, and on the image a drag with the right button changes them** (`tool_drag.py`; the wand: threshold sideways, Grow up;
Threshold / Local contrast: threshold sideways, sigma up; Draw: size sideways). The wand's method is switched by the wheel over its button (Contrast /
Brightness / Edges; its icon shows it); so is the Threshold's side (Brighter / Darker than the background: the coloured part of its icon is over
or under its line), and while it is armed the pixels a click would select are tinted on the image, live (`mask_edit_preview.py`). Every selection
change is an undo step (Ctrl+Z, `selection/area_selection_module.py`).
Important states: which tool is armed (blue icon, or a frame round a multi-colour icon); a tool computing (its tool tip says so); no image (the tool
says so in the status bar).
Validation: the spin boxes bound every value. Error messages: from the tools, in the status bar.

Real `QToolButton`s (`.click()` works in tests, GUI rule). Every setting is remembered (`bind_ui_state`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
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
from ...image_tools.mask import raster_tools
from ...image_tools.selection_wand import WandMethod, WandSettings
from ...selection import AreaSelectionMode, AreaSelectionModule
from ...selection.area_selection_module import ADD, COMBINE_MODES, INTERSECT, REPLACE, SUBTRACT, VALUE_MODES
from ...storage.ui_state_keys import SELECTION_COMBINE_MODE, SELECTION_WAND_METHOD
from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from ..scrolling_column import ScrollingColumn
from ..ui_state import UiStateStore
from .combine_mode_icons import combine_mode_icon, framed_icon
from .lock_layer import BORDER_COLOR
from .mask_edit_tool_icons import histogram_selection_icon, local_contrast_icon, morphology_icon, threshold_icon
from .selection_by_value import ICON_CHOICE_STYLE, MODE_METHODS, ByValueSelector
from .selection_menu_icons import FILL_HOLES_ICON
from .selection_palette import PAST, PRESENT
from .selection_style import SelectionStyle
from .selection_view_form import SelectionViewForm
from .side_rails import LEFT, beside_point, tooltip_point, valid_side
from .wheel_tool_button import WheelToolButton

_VALUE_NEEDS_IMAGE = (
    "Works on the Image layer only (it reads image brightness): make Image the active layer (Layers button, top of this toolbar)."
)
LOCK_ACTION = "lock"  # what the one Lock / Unlock button does (`SelectionToolbar.lock_action`)
UNLOCK_ACTION = "unlock"
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue (as the old picker)
# As narrow as the icons (maintainer, 2026-11-15: "as narrow as possible", then "not symmetric"): no frame margin,
# buttons exactly as wide as the icon; the buttons keep the ribbon's height. The image's pyqtgraph layout has no left
# margin (`panel.py`), so the gap right of the icons is as small as the one left of them (the dock card's gap).
_BAR_WIDTH = ICON_SIZE + 4  # then "a little padding, 1 or 2 px": 2 px each side of the icon
_SEPARATOR_WIDTH = _BAR_WIDTH - 6


def _style_button(button: QToolButton) -> None:
    style_general_icon_button(button)
    button.setFixedSize(_BAR_WIDTH, BUTTON_SIZE)


@dataclass(frozen=True)
class _Tool:
    """One armed tool of the toolbar: its icon (a Tabler name, drawn blue while the tool is armed; or a function giving a fixed multi-colour icon,
    which gets a frame while armed: `framed_icon`) and its tool tip."""

    icon: str | Callable[[], QIcon]
    tip: str


_SHARED_MODE_TIP = "Shift / Alt / Shift+Alt held at the click add / take away / intersect (the mode picker sets it for all)."

_TOOLS: dict[AreaSelectionMode, _Tool] = {
    AreaSelectionMode.RECTANGLE: _Tool("select-rectangle", "Rectangular selection: drag on the image."),
    AreaSelectionMode.LASSO: _Tool("select-lasso", "Lasso selection: draw around an area on the image."),
    AreaSelectionMode.WAND: _Tool(
        "wand",
        "Magic wand: click a feature to select it. Shift+click adds to the selection, Alt+click takes away. "
        "Settings: right-click this button. Method: scroll the wheel over this button. "
        "On the image, drag with the right button: right = bigger region (threshold), up = Grow.",
    ),
    AreaSelectionMode.BRUSH: _Tool(
        "pencil",  # a Tabler icon: blue while armed, like Rectangle / Lasso / Wand (it had a fixed grey pencil with a frame until 2026-11-16)
        "Draw (selection brush): drag on the image to paint into the selection; Alt+drag erases from it, Shift+Alt+drag keeps only what the "
        "stroke covers (a click is one stamp). With no key the mode picker's mode applies: Replace makes each stroke the new selection, so pick "
        "Add (or hold Shift) to build a selection from several strokes. "
        "Size: right-click this button, or drag with the right button on the image (right = bigger). It paints the selection, not the mask: "
        "Add selection (Mask tab) puts it in the mask.",
    ),
    AreaSelectionMode.HISTOGRAM: _Tool(
        histogram_selection_icon,
        "Select by Histogram range: pick this tool, then click the image to select the pixels inside the range highlighted on the Histogram "
        f"panel. Right-click for the range and for showing or hiding it on the image. {_SHARED_MODE_TIP}",
    ),
    AreaSelectionMode.THRESHOLD: _Tool(
        threshold_icon,
        "Select by Threshold: pick this tool, then click the image to select the pixels that are brighter (or darker) than their blurred local "
        "background by the threshold. The tinted pixels on the image are what a click would select; they follow the numbers. "
        "Brighter / Darker: scroll the wheel over this button (the coloured part of the icon is over or under its line). "
        "Numbers: right-click this button, or drag with the right button on the image (right = bigger region, up = bigger sigma). "
        f"{_SHARED_MODE_TIP}",
    ),
    AreaSelectionMode.CONTRAST: _Tool(
        local_contrast_icon,
        "Select by Local contrast: pick this tool, then click the image to select the pixels more than z local standard deviations from their "
        "local mean. Numbers: right-click this button, or drag with the right button on the image (right = bigger region, up = bigger sigma). "
        f"{_SHARED_MODE_TIP}",
    ),
}

# The wand button shows the picked method (2026-11-15): the Tabler wand with a small glyph where its second sparkle was -
# contrast = a half-filled disc, brightness = a sun, edges = a dotted outline. The three SVGs are drawn by hand in Tabler's
# style (24 x 24, stroke 2), vendored in `lspr_ui/icon_assets/`; none is the Rectangle's or the Lasso's icon.
_METHOD_ICONS = {
    WandMethod.CONTRAST: "wand-contrast",
    WandMethod.BRIGHTNESS: "wand-brightness",
    WandMethod.EDGES: "wand-edges",
}

# The mode picker (2026-11-15): the Venn-diagram icon of the picked mode on the button and in its menu (`combine_mode_icons.py`).
_MODE_LABELS = {  # short on purpose (maintainer, 2026-11-16): the Venn picture says the rest
    REPLACE: "Replace",
    ADD: "Add",
    SUBTRACT: "Subtract",
    INTERSECT: "Intersect",
}
_MODE_TIP = (
    "How a new selection (rectangle, lasso, wand, brush, select by value) combines with the current one. Click for the four modes, or scroll the "
    "wheel over this button. In the picture the left circle is the selection now, the right circle the new region (their colours: the View button below), grey = what ends up selected, "
    "empty = not selected or taken away. On the image, Shift adds, Alt takes away and Shift+Alt intersects, for one "
    "selection (this button shows it while the key is down); with no key the mode stays as picked."
)

_METHOD_LABELS = (
    (WandMethod.CONTRAST, "Contrast to background"),
    (WandMethod.BRIGHTNESS, "Brightness (similar value)"),
    (WandMethod.EDGES, "Edges (stop at an outline)"),
)
_METHOD_TIPS = {
    WandMethod.CONTRAST: "pixels that stand out from the smooth local background (bright or dark features, uneven light).",
    WandMethod.BRIGHTNESS: "pixels with a value close to the clicked one (the classic wand).",
    WandMethod.EDGES: "grows from the click until a strong edge, the border placed on the steepest point of the edge.",
}


class WandSettingsForm(QWidget):
    """The wand's settings, shown in the gear button's pop-up. `settings()` is what a click uses."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        defaults = WandSettings()
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # The method: three icon buttons, one checked (2026-11-16, maintainer: "replace dropdown menu with icons"); the pictures are the wand
        # button's own for each method. The wheel over the wand button steps through them too.
        self._picked = _METHOD_LABELS[0][0]
        self.method_buttons: dict[WandMethod, QToolButton] = {}
        method_row = QWidget(self)
        method_layout = QHBoxLayout(method_row)
        method_layout.setContentsMargins(0, 0, 0, 0)
        method_layout.setSpacing(4)
        for method, label in _METHOD_LABELS:
            button = QToolButton(method_row)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setStyleSheet(ICON_CHOICE_STYLE)
            button.setToolTip(f"{label}: {_METHOD_TIPS[method]}\nThe wheel over the wand button on the toolbar switches the method too.")
            button.clicked.connect(lambda _checked=False, picked=method: self.set_method(picked))
            method_layout.addWidget(button)
            self.method_buttons[method] = button
        method_layout.addStretch(1)
        form.addRow(method_row)

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

        # Fill holes: an icon that is on (coloured, with a plate) or off (grey), not a check box with a caption; its picture is the Modify menu's.
        self.fill_holes = QToolButton(self)
        self.fill_holes.setCheckable(True)
        self.fill_holes.setChecked(defaults.fill_holes)
        self.fill_holes.setAutoRaise(True)
        self.fill_holes.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self.fill_holes.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.fill_holes.setStyleSheet(ICON_CHOICE_STYLE)
        self.fill_holes.setToolTip("Fill holes: fill gaps inside the region (a ring-shaped particle becomes a solid one). Coloured = on.")
        fill_row = QWidget(self)
        fill_layout = QHBoxLayout(fill_row)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        fill_layout.addWidget(self.fill_holes)
        fill_layout.addStretch(1)
        form.addRow(fill_row)

        for widget in (self.contrast_threshold, self.brightness_tolerance, self.edge_threshold, self.smoothing, self.background, self.grow):
            widget.valueChanged.connect(lambda _value: self.changed.emit())
        self.fill_holes.toggled.connect(lambda _on: self._on_fill_holes_toggled())
        self.refresh_theme(get_active_theme())
        self._on_method_changed()

    def refresh_theme(self, theme: GuiTheme) -> None:
        """Draw the icons in the theme's colours (the method pictures; Fill holes is blue when on, muted when off)."""
        for method, button in self.method_buttons.items():
            button.setIcon(load_tabler_icon(_METHOD_ICONS[method], color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self._paint_fill_holes(theme)

    def _paint_fill_holes(self, theme: GuiTheme | None = None) -> None:
        theme = theme if theme is not None else get_active_theme()
        color = _ACTIVE_COLOR if self.fill_holes.isChecked() else theme.text_muted
        self.fill_holes.setIcon(load_tabler_icon(FILL_HOLES_ICON, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

    def _on_fill_holes_toggled(self) -> None:
        self._paint_fill_holes()
        self.changed.emit()

    def _method(self) -> WandMethod:
        return self._picked

    def method_label(self) -> str:
        return dict(_METHOD_LABELS)[self._picked]

    def picked_method(self) -> WandMethod:
        return self._method()

    def set_method(self, method: WandMethod) -> None:
        """Pick `method` (a click on its icon, the wheel over the wand button, a restore). Picking the picked one changes nothing."""
        if method is not self._picked:
            self._picked = method
            self._on_method_changed()
        else:
            self._show_method()  # a click on the checked button must leave it checked

    def _show_method(self) -> None:
        for method, button in self.method_buttons.items():
            button.setChecked(method is self._picked)

    def cycle_method(self, steps: int) -> str:
        """Move `steps` places along the method list, wrapping round (the wheel over the wand button); returns the label of
        the method now picked."""
        order = [method for method, _label in _METHOD_LABELS]
        self.set_method(order[(order.index(self._picked) + int(steps)) % len(order)])
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
        self._show_method()
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
        # The method is written by hand (three buttons are not one widget `bind` knows), under the key and values it had as a drop-down.
        saved = store.get(SELECTION_WAND_METHOD.key)
        if isinstance(saved, str) and saved in {method.value for method in WandMethod}:
            self.set_method(WandMethod(saved))
        self.changed.connect(lambda: store.set(SELECTION_WAND_METHOD.key, self._picked.value))
        store.bind("selection/wand_contrast_threshold", self.contrast_threshold)
        store.bind("selection/wand_brightness_tolerance", self.brightness_tolerance)
        store.bind("selection/wand_edge_threshold", self.edge_threshold)
        store.bind("selection/wand_smoothing_px", self.smoothing)
        store.bind("selection/wand_background_px", self.background)
        store.bind("selection/wand_grow_px", self.grow)
        store.bind("selection/wand_fill_holes", self.fill_holes)


class SelectionToolbar(QFrame):
    """View, the mode picker, the selection tools, the select-by-value tools, Modify selection and Lock / Unlock - one column beside the image, in
    six sections (module docstring)."""

    lock_requested = pyqtSignal()  # the Lock button shows Lock and was pressed: the selection becomes the fence
    unlock_requested = pyqtSignal()  # the Lock button shows Unlock and was pressed

    def __init__(
        self,
        selection: AreaSelectionModule,
        active_tool: ActiveToolModule,
        parent: QWidget | None = None,
        value_tool: ByValueSelector | None = None,
        brush_form: QWidget | None = None,
        modify_menu: QMenu | None = None,
        style: SelectionStyle | None = None,
        layers_button: QToolButton | None = None,
    ) -> None:
        super().__init__(parent)
        self._side = LEFT  # the side of the image the toolbar stands on (`set_side`)
        self._value_tool = value_tool
        self._layers_button = layers_button  # pinned under View (`layers_button.py`)
        self._value_allowed = True  # False while a layer other than Image is active: the select-by-value tools read image brightness
        self._style = style if style is not None else SelectionStyle(self)  # the outlines' colours and fills (the View pop-up writes it)
        self.setObjectName("selectionToolbar")
        self._selection = selection
        self._active_tool = active_tool
        # The buttons sit in `ScrollingColumn` (2026-11-15): 11 buttons and 4 dividers are about 330 px tall; without the column they made the Image
        # panel's minimum height 573 px (289 px without them). The column reports a small minimum and scrolls (arrows at its ends, wheel over a
        # gap) when short.
        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._column = ScrollingColumn(self._content, self)  # added to `outer` below the View button

        self._wand_busy = False
        self._value_busy = False
        self._held_mode: str | None = None  # the combine mode a held Shift / Alt gives (`show_held_mode`); None = the picked mode shows
        self._tool_buttons: dict[AreaSelectionMode, QToolButton] = {}
        self._lock_inside = False  # a lock is on and the Lock button was wheeled to its Lock face (lock the selection inside the lock)
        self._separators: list[QFrame] = []  # the one under the pinned View button, then the four dividers of the column
        self._popups: dict[AreaSelectionMode, QMenu] = {}

        # 1. View (2026-11-16, maintainer: the top of the toolbar): the pop-up that changes how the selection looks. Pinned above the scrolling
        # column (`outer`), not inside it, so it is always at the top whether or not the column scrolls.
        self._view_form = SelectionViewForm(self._style, self)
        self._view_menu = QMenu(self)
        view_action = QWidgetAction(self._view_menu)
        view_action.setDefaultWidget(self._view_form)
        self._view_menu.addAction(view_action)
        self._view_button = QToolButton(self)
        _style_button(self._view_button)
        self._view_button.clicked.connect(lambda _checked=False: self.show_view_menu())
        outer.addSpacing(4)
        outer.addWidget(self._view_button, 0, Qt.AlignmentFlag.AlignHCenter)
        if layers_button is not None:  # 1b. Layers (2026-11-16): the active layer and the layers' eyes, pinned under View
            layers_button.setParent(self)
            _style_button(layers_button)
            outer.addSpacing(2)
            outer.addWidget(layers_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._add_separator(outer)
        outer.addWidget(self._column, 1)

        # 2. The mode picker: the Venn diagram of the picked mode; click = a menu of the four, the wheel steps through them.
        self._mode_button = WheelToolButton(self)
        _style_button(self._mode_button)
        self._mode_menu = QMenu(self)
        self._mode_actions: dict[str, QAction] = {}
        for how in COMBINE_MODES:
            action = self._mode_menu.addAction(_MODE_LABELS[how])
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, picked=how: self._selection.set_combine_mode(picked))
            self._mode_actions[how] = action
        self._mode_button.clicked.connect(lambda _checked=False: self.show_mode_menu())
        self._mode_button.wheel_steps.connect(self._on_mode_wheel)
        layout.addWidget(self._mode_button)
        self._add_separator(layout)

        # 3. Selection tools: Rectangle, Lasso, Wand, Draw
        for mode in (
            AreaSelectionMode.RECTANGLE, AreaSelectionMode.LASSO, AreaSelectionMode.WAND, AreaSelectionMode.BRUSH
        ):
            self._add_tool(layout, mode)
        # The wand's settings pop up beside its button on a right-click (no extra button, so no menu arrow either: the button has no `setMenu`, the
        # pop-up is shown by hand); the wheel over it switches its method, and its tool tip always names the method in use.
        self.wand_form = WandSettingsForm(self)
        self._attach_popup(AreaSelectionMode.WAND, self.wand_form)
        self._tool_buttons[AreaSelectionMode.WAND].wheel_steps.connect(self._on_wand_wheel)  # type: ignore[attr-defined]
        self._icon_method = self.wand_form.picked_method()  # the method the wand button's icon was drawn for
        self.wand_form.changed.connect(self._on_wand_form_changed)
        if brush_form is not None:  # the brush's size, the same way
            self._attach_popup(AreaSelectionMode.BRUSH, brush_form)
        self._add_separator(layout)

        # 4. Select by value: Histogram range, Threshold, Local contrast. One pop-up form, showing the numbers of the button that opened it.
        for mode in VALUE_MODES:
            self._add_tool(layout, mode)
        self._value_menu: QMenu | None = None
        if value_tool is not None:
            self._value_menu = QMenu(self)
            self._value_action = QWidgetAction(self._value_menu)
            self._value_action.setDefaultWidget(value_tool.form)
            self._value_menu.addAction(self._value_action)
            for mode in VALUE_MODES:
                button = self._tool_buttons[mode]
                button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                button.customContextMenuRequested.connect(lambda _pos, picked=mode: self.show_value_settings(picked))
            value_tool.busy_changed.connect(self._set_value_busy)
            # The Threshold's side: the wheel over its button flips Brighter / Darker (the form holds the state, like the wand's method); the icon
            # and the tool tip follow it.
            self._tool_buttons[AreaSelectionMode.THRESHOLD].wheel_steps.connect(self._on_threshold_wheel)  # type: ignore[attr-defined]
            value_tool.form.threshold_side_changed.connect(self._refresh)
        self._add_separator(layout)

        # 5. Modify selection: Grow / Shrink / Open / Close / Fill holes / Invert and their radius, in a menu.
        self._modify_menu = modify_menu
        self._modify_button: QToolButton | None = None
        if modify_menu is not None:
            self._modify_button = QToolButton(self)
            _style_button(self._modify_button)
            self._modify_button.clicked.connect(lambda _checked=False: self.show_modify_menu())
            layout.addWidget(self._modify_button)
            self._add_separator(layout)

        # 6. Lock / Unlock, one button (2026-11-16, maintainer): Lock while nothing is locked, Unlock while a lock is on. The wheel over it flips it
        # to its Lock face while locked (lock the selection inside the lock: narrow it); the right-click menu on the image has both as well.
        self._lock_button = WheelToolButton(self, wheel_wanted=self._selection.has_lock)  # with no lock the wheel scrolls the column
        _style_button(self._lock_button)
        self._lock_button.clicked.connect(lambda _checked=False: self._on_lock_clicked())
        self._lock_button.wheel_steps.connect(self._on_lock_wheel)
        layout.addWidget(self._lock_button)
        layout.addStretch(1)
        self.setFixedWidth(_BAR_WIDTH)
        self._refresh_wand_tip()

        self._style.changed.connect(self._refresh_mode_icons)  # the Venn circles wear the outlines' colours
        selection.mode_changed.connect(lambda _mode: self._refresh())
        selection.combine_mode_changed.connect(lambda _how: self._refresh())
        selection.selection_changed.connect(self._refresh)
        active_tool.active_tool_changed.connect(lambda _tool: self._refresh())
        self.refresh_theme(get_active_theme())

    # -- building ------------------------------------------------------------------------------------------

    def _add_tool(self, layout: QVBoxLayout, mode: AreaSelectionMode) -> None:
        """An armed tool's button (checkable; a click arms it, a second click leaves it). Only the wand's (its method) and the Threshold's (its
        side) read the wheel."""
        wheel = mode in (AreaSelectionMode.WAND, AreaSelectionMode.THRESHOLD)
        button: QToolButton = WheelToolButton(self) if wheel else QToolButton(self)
        _style_button(button)
        button.setCheckable(True)
        button.setToolTip(_TOOLS[mode].tip)
        button.clicked.connect(lambda _checked=False, picked=mode: self._pick(picked))
        layout.addWidget(button)
        self._tool_buttons[mode] = button

    def _attach_popup(self, mode: AreaSelectionMode, form: QWidget) -> None:
        """`form` pops up beside the tool's button on a right-click."""
        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(form)
        menu.addAction(action)
        button = self._tool_buttons[mode]
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda _pos, picked=mode: self.show_settings(picked))
        self._popups[mode] = menu

    def _add_separator(self, layout: QVBoxLayout) -> None:
        """A horizontal line between two sections (coloured in `refresh_theme`)."""
        line = QFrame(self._content)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedSize(_SEPARATOR_WIDTH, 1)
        layout.addSpacing(2)
        layout.addWidget(line, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(2)
        self._separators.append(line)

    # -- parts (tests and the panel use them) -----------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the wand's settings, the Threshold's side and the picked selection mode across restarts (`panels/ui_state.py`). The other
        tools' numbers are `MaskSettings`, saved with the session."""
        self.wand_form.bind_ui_state(store)
        if self._value_tool is not None:
            self._value_tool.form.bind_ui_state(store)  # the Threshold's side
        self._style.bind_ui_state(store)
        layers_bind = getattr(self._layers_button, "bind_ui_state", None)
        if callable(layers_bind):  # the Image layer's eye and the active layer (`layers.py`)
            layers_bind(store)
        saved = store.get(SELECTION_COMBINE_MODE.key)
        if saved in COMBINE_MODES:
            self._selection.set_combine_mode(str(saved))
        self._selection.combine_mode_changed.connect(lambda how: store.set(SELECTION_COMBINE_MODE.key, how))

    @property
    def column(self) -> ScrollingColumn:
        """The scrolling column the buttons sit in (it shows arrows while the toolbar is taller than the room it has)."""
        return self._column

    def tool_button(self, mode: AreaSelectionMode) -> QToolButton:
        return self._tool_buttons[mode]

    @property
    def wand_menu(self) -> QMenu:
        return self._popups[AreaSelectionMode.WAND]

    @property
    def brush_menu(self) -> QMenu | None:
        return self._popups.get(AreaSelectionMode.BRUSH)

    @property
    def value_menu(self) -> QMenu | None:
        return self._value_menu

    @property
    def mode_button(self) -> QToolButton:
        return self._mode_button

    @property
    def mode_menu(self) -> QMenu:
        return self._mode_menu

    @property
    def lock_button(self) -> QToolButton:
        """The one Lock / Unlock button (`lock_action()` says which of the two it is now)."""
        return self._lock_button

    def lock_action(self) -> str:
        """What the Lock button does now: `LOCK_ACTION` (no lock yet, or a lock is on and the button was wheeled to Lock to narrow it) or
        `UNLOCK_ACTION` (a lock is on: the button's resting face)."""
        return UNLOCK_ACTION if self._selection.has_lock() and not self._lock_inside else LOCK_ACTION

    @property
    def modify_button(self) -> QToolButton | None:
        return self._modify_button

    @property
    def view_button(self) -> QToolButton:
        return self._view_button

    @property
    def view_menu(self) -> QMenu:
        return self._view_menu

    @property
    def view_form(self) -> SelectionViewForm:
        return self._view_form

    @property
    def style(self) -> SelectionStyle:
        return self._style

    def set_side(self, side: str) -> None:
        """The side of the image the toolbar stands on (2026-11-22, it can be docked on either): its pop-ups and tool tips open over the image, to the
        right of the toolbar on the left and to the left of it on the right (`side_rails.beside_point`)."""
        self._side = valid_side(side, self._side)
        if self._layers_button is not None and hasattr(self._layers_button, "set_side"):
            self._layers_button.set_side(self._side)

    def _popup_beside(self, menu: QMenu, button: QToolButton) -> None:
        menu.ensurePolished()
        menu.popup(button.mapToGlobal(beside_point(button, self._side, menu.sizeHint().width())))

    def show_view_menu(self) -> None:
        """Open the View pop-up beside its button (what a click on it does)."""
        self._popup_beside(self._view_menu, self._view_button)

    def show_settings(self, mode: AreaSelectionMode) -> None:
        """Open the numbers of the tool `mode` beside its button (what a right-click on the button does)."""
        if mode in VALUE_MODES:
            self.show_value_settings(mode)
        elif mode in self._popups:
            self._popup_beside(self._popups[mode], self._tool_buttons[mode])

    def show_wand_settings(self) -> None:
        self.show_settings(AreaSelectionMode.WAND)

    def show_brush_settings(self) -> None:
        self.show_settings(AreaSelectionMode.BRUSH)

    def show_value_settings(self, mode: AreaSelectionMode) -> None:
        """Open the numbers of the select-by-value tool `mode` (Histogram range, Threshold or Local contrast) beside its button."""
        if self._value_tool is not None and self._value_menu is not None:
            self._value_tool.form.set_method(MODE_METHODS[mode])
            # A QMenu measures its widget once and keeps the size, but the three methods differ (Histogram range is one icon, the others
            # have number fields): taking the action out and putting it back makes the menu measure the form again (2026-11-16).
            self._value_menu.removeAction(self._value_action)
            self._value_menu.addAction(self._value_action)
            self._popup_beside(self._value_menu, self._tool_buttons[mode])

    def show_modify_menu(self) -> None:
        """Open the Modify selection menu beside its button (what a click on it does)."""
        if self._modify_button is not None and self._modify_menu is not None:
            self._popup_beside(self._modify_menu, self._modify_button)

    def show_mode_menu(self) -> None:
        """Open the four modes beside the mode button (what a click on it does)."""
        self._popup_beside(self._mode_menu, self._mode_button)

    # -- busy and wheels ---------------------------------------------------------------------------------------

    def set_wand_busy(self, busy: bool) -> None:
        self._wand_busy = busy
        self._refresh_wand_tip()

    def _set_value_busy(self, busy: bool) -> None:
        self._value_busy = busy
        self._refresh()

    def _on_wand_form_changed(self) -> None:
        """Any wand setting changed (also every step of a right-drag): the tool tip is cheap; the icon is redrawn only when the
        method itself changed."""
        self._refresh_wand_tip()
        if self.wand_form.picked_method() is not self._icon_method:
            self._refresh()

    def _refresh_wand_tip(self) -> None:
        button = self._tool_buttons[AreaSelectionMode.WAND]
        busy = "Magic wand: working...\n" if self._wand_busy else ""
        button.setToolTip(f"{busy}Method: {self.wand_form.method_label()}\n{_TOOLS[AreaSelectionMode.WAND].tip}")

    def _on_wand_wheel(self, steps: int) -> None:
        """The wheel over the wand button: the previous / next method, said in a tool tip beside the button."""
        label = self.wand_form.cycle_method(steps)
        button = self._tool_buttons[AreaSelectionMode.WAND]
        QToolTip.showText(tooltip_point(button, self._side, f"Wand method: {label}"), f"Wand method: {label}", button)

    def _on_threshold_wheel(self, steps: int) -> None:
        """The wheel over the Threshold button: the other side (Brighter / Darker, wrapping round), said in a tool tip beside the button."""
        if self._value_tool is None:
            return
        label = self._value_tool.form.cycle_threshold_side(steps)
        button = self._tool_buttons[AreaSelectionMode.THRESHOLD]
        QToolTip.showText(tooltip_point(button, self._side, f"Threshold: {label}"), f"Threshold: {label}", button)

    def _on_mode_wheel(self, steps: int) -> None:
        """The wheel over the mode button: the previous / next combine mode (wrapping), said in a tool tip beside the button."""
        how = COMBINE_MODES[(COMBINE_MODES.index(self._selection.combine_mode()) + int(steps)) % len(COMBINE_MODES)]
        self._selection.set_combine_mode(how)
        text = f"Selection mode: {_MODE_LABELS[how]}"
        QToolTip.showText(tooltip_point(self._mode_button, self._side, text), text, self._mode_button)

    def _on_lock_clicked(self) -> None:
        """The Lock button asks for whichever of the two it shows; afterwards it goes back to its resting face."""
        action = self.lock_action()
        self._lock_inside = False
        (self.unlock_requested if action == UNLOCK_ACTION else self.lock_requested).emit()
        self._refresh()

    def _on_lock_wheel(self, _steps: int) -> None:
        """The wheel over the Lock button while a lock is on: flips it between Unlock and Lock (lock the selection inside the lock, which narrows
        it). Locking needs a selection; without one the tool tip says so and the button stays Unlock."""
        button = self._lock_button

        def say(text: str) -> None:
            QToolTip.showText(tooltip_point(button, self._side, text), text, button)

        if not self._selection.has_selection():
            say("Lock inside the lock: make a selection first")
            return
        self._lock_inside = not self._lock_inside
        self._refresh()
        say("Lock: the selection becomes the new, smaller lock" if self._lock_inside else "Unlock")

    def show_held_mode(self, how: str | None) -> None:
        """A held Shift / Alt (or the keys a drag began with) make the next selection combine as *how*: the mode button shows that mode's
        picture while the keys are down (2026-11-16, maintainer: the mode "linked to the keyboard keys"). ``None`` = no key: the picked mode
        shows again. The picked mode itself is not changed (the sticky choice stays what it was)."""
        if how == self._held_mode:
            return
        self._held_mode = how
        self._refresh_mode_button()

    def _venn_icon(self, how: str) -> QIcon:
        """The Venn icon of `how` with the circle colours the View pop-up picked."""
        return combine_mode_icon(how, self._style.color(PAST), self._style.color(PRESENT))

    def _refresh_mode_icons(self) -> None:
        """The outlines' colours changed: redraw the mode button and the pictures in its menu."""
        self._refresh_mode_button()
        for how, action in self._mode_actions.items():
            action.setIcon(self._venn_icon(how))

    def _refresh_mode_button(self) -> None:
        picked_mode = self._selection.combine_mode()
        shown = self._held_mode or picked_mode
        self._mode_button.setIcon(self._venn_icon(shown))
        held_note = "\n(a held key: Shift adds, Alt takes away, Shift+Alt intersects)" if self._held_mode else ""
        self._mode_button.setToolTip(f"Selection mode: {_MODE_LABELS[shown]}{held_note}\n{_MODE_TIP}")

    # -- state -------------------------------------------------------------------------------------------------

    def set_value_tools_allowed(self, allowed: bool) -> None:
        """Histogram range / Threshold / Local contrast read image brightness: enabled only while the Image layer is active (`layers.py`)."""
        if bool(allowed) != self._value_allowed:
            self._value_allowed = bool(allowed)
            self._refresh()

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

    def _tool_icon(self, mode: AreaSelectionMode, on: bool, theme: GuiTheme) -> QIcon:
        icon = _TOOLS[mode].icon
        if callable(icon):  # a fixed multi-colour icon: its "on" picture has the frame
            if mode is AreaSelectionMode.THRESHOLD and self._value_tool is not None:
                # Two pictures, one per side (the coloured part is over or under the line); cached under a key of its own each.
                darker = self._value_tool.form.threshold_side() == raster_tools.SIDE_DARKER
                return framed_icon(f"{mode.value}-{'darker' if darker else 'brighter'}", lambda: threshold_icon(darker))
            return framed_icon(mode.value, icon)
        if mode is AreaSelectionMode.WAND:
            self._icon_method = self.wand_form.picked_method()
            icon = _METHOD_ICONS[self._icon_method]
        return load_tabler_icon(icon, color=_ACTIVE_COLOR if on else theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)

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
            button.setIcon(self._tool_icon(mode, on, theme))
            if mode in VALUE_MODES:
                side = ""
                if mode is AreaSelectionMode.THRESHOLD and self._value_tool is not None:
                    side = f"Threshold: {self._value_tool.form.threshold_side_label()}\n"
                button.setEnabled(self._value_allowed)
                if not self._value_allowed:
                    button.setToolTip(f"{_VALUE_NEEDS_IMAGE}\n\n{_TOOLS[mode].tip}")
                    continue
                button.setToolTip(("Working...\n" if self._value_busy and on else "") + side + _TOOLS[mode].tip)

        picked_mode = self._selection.combine_mode()
        self._refresh_mode_button()
        for how, action in self._mode_actions.items():
            action.setIcon(self._venn_icon(how))
            action.setChecked(how == picked_mode)

        has_selection = self._selection.has_selection()
        if self._modify_button is not None:
            self._modify_button.setEnabled(has_selection)
            self._modify_button.setToolTip(
                "Modify selection: Grow, Shrink, Open (remove specks), Close (fill gaps), Fill holes, Invert; the radius is in the menu."
                if has_selection else "Modify selection: make a selection first."
            )
        self._refresh_lock_button(has_selection, theme)

    def _refresh_lock_button(self, has_selection: bool, theme: GuiTheme) -> None:
        """The one Lock / Unlock button: its icon, whether it can be pressed and what its tool tip says follow `lock_action()`."""
        locked = self._selection.has_lock()
        if not locked or not has_selection:
            self._lock_inside = False  # the Lock face inside a lock needs a selection to lock to (and a lock to be inside)
        action = self.lock_action()
        # Amber (the lock border's colour on the image) while a lock is on, so the button says "you are locked"
        self._lock_button.setIcon(
            load_tabler_icon(
                "lock-open" if action == UNLOCK_ACTION else "lock",
                color=BORDER_COLOR if locked else theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH,
            )
        )
        self._lock_button.setEnabled(action == UNLOCK_ACTION or has_selection)
        if action == UNLOCK_ACTION:
            tip = "Unlock: leave the lock; the whole image can be selected and edited again. The selection stays."
            tip += (
                "\nWheel over this button: Lock (the selection becomes the new, smaller lock)."
                if has_selection else "\n(To lock inside the lock, make a selection first, then wheel over this button.)"
            )
        elif locked:
            tip = (
                "Lock: the selection becomes the new, smaller lock (it replaces this one).\n"
                "Wheel over this button: back to Unlock."
            )
        elif has_selection:
            tip = (
                "Lock: the selection becomes the fence. Everything outside is dimmed; tools and new selections work only inside it "
                "(like a crop that keeps the image). Once locked, this button becomes Unlock."
            )
        else:
            tip = "Lock: make a selection first."
        self._lock_button.setToolTip(tip)

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._column.refresh_theme()
        for button in (*self._tool_buttons.values(), self._mode_button, self._lock_button, self._view_button):
            _style_button(button)
        if self._layers_button is not None:
            _style_button(self._layers_button)
            refresh = getattr(self._layers_button, "refresh", None)
            if callable(refresh):
                refresh(theme)
        self._view_button.setIcon(load_tabler_icon("eye", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self._view_button.setToolTip(
            "View: how the selection looks on the image. The colour of the selection and of the shape being dragged, also the colour of the "
            "Venn circles of the mode button, and an infill for each in five opacities (none by default)."
        )
        self._view_form.refresh_theme(theme)
        self.wand_form.refresh_theme(theme)
        if self._modify_button is not None:
            _style_button(self._modify_button)
            self._modify_button.setIcon(morphology_icon())  # the old Morphology tool's icon (fixed colours)
        if self._modify_menu is not None and hasattr(self._modify_menu, "refresh_theme"):
            self._modify_menu.refresh_theme()
        for line in self._separators:
            line.setStyleSheet(f"color: {theme.control_border};")
        self.setStyleSheet("#selectionToolbar { border: none; }")
        self._refresh()
