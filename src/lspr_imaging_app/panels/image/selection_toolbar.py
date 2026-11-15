"""The always-visible selection toolbar at the left edge of the image (2026-11-14, maintainer request; reorganised into sections 2026-11-15).

**The idea (the maintainer's Photoshop metaphor).** Selection tools work on the *image* and make a selection (a pixel
map); the mask and the ROIs are layers over the image. The selection does nothing by itself except limit where the
editing tools act; turning it into something is an explicit action of the layer: the Mask tab's *Add selection* /
*Subtract selection* / *Replace* / *Selection from mask* (and, later, an ROI made from a selection - its reference region and "one ROI per
blob or one for all" are decisions still open, `docs/selection_wand_2026-11-14.md`).
Right-click with a tool armed: "Select ROIs in selection" (the ROIs inside or partly inside it, `interaction.py`), Lock to selection, Invert,
Deselect (there is no Deselect button: it is in this menu).

Screen purpose: make and refine a selection on the image from any ribbon tab.
Layout: a narrow column of icon buttons left of the image canvas, below the ribbon, in **five sections divided by horizontal lines** (maintainer,
2026-11-15); it scrolls (arrows at its ends) when the panel is too short for all of them (`panels/scrolling_column.py`):
  1. **Lock / Unlock**: Lock turns the selection into the fence (`AreaSelectionModule.lock_selection`; enabled with a selection), Unlock leaves it
     (amber while a lock is on). They only ask (`lock_requested` / `unlock_requested`): the Image panel knows the image size and says in the
     status bar what happened.
  2. **Selection tools**: Rectangle, Lasso, Wand, Draw (the selection brush).
  3. **Mode**: one button showing the Venn diagram of the picked mode (`combine_mode_icons.py`: pink circle = the selection now, green circle = the new region, grey fill = ends up selected);
     click = a menu of the four modes (Replace / Add / Subtract / Intersect), the wheel over it steps through them; remembered. Shift / Alt /
     Shift+Alt on the image override it for one selection.
  4. **Select by value**: Histogram range, Threshold, Local contrast (`selection_by_value.py`), with the Mask tab's old icons.
  5. **Modify selection**: Grow / Shrink / Open / Close / Fill holes / Invert (`selection_modify.py`), with the old Morphology icon.
Main user actions: pick a tool (picking the armed one again leaves it, the selection stays). **Every tool with numbers has them in a pop-up on a
right-click of its button, and on the image a drag with the right button changes them** (`tool_drag.py`; the wand: threshold sideways, Grow up;
Threshold / Local contrast: threshold sideways, sigma up; Draw: size sideways). The wand's method is switched by the wheel over its button (Contrast /
Brightness / Edges; its icon shows it). Every selection change is an undo step (Ctrl+Z, `selection/area_selection_module.py`).
Important states: which tool is armed (blue icon, or a frame round a multi-colour icon); a tool computing (its tool tip says so); no image (the tool
says so in the status bar).
Validation: the spin boxes bound every value. Error messages: from the tools, in the status bar.

Real `QToolButton`s (`.click()` works in tests, GUI rule). Every setting is remembered (`bind_ui_state`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
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
from ...selection.area_selection_module import ADD, COMBINE_MODES, INTERSECT, REPLACE, SUBTRACT, VALUE_MODES
from ...storage.ui_state_keys import SELECTION_COMBINE_MODE
from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from ..scrolling_column import ScrollingColumn
from ..ui_state import UiStateStore
from .combine_mode_icons import combine_mode_icon, framed_icon
from .lock_layer import BORDER_COLOR
from .mask_edit_tool_icons import histogram_selection_icon, local_contrast_icon, morphology_icon, threshold_icon
from .selection_by_value import MODE_METHODS, ByValueSelector
from .wheel_tool_button import WheelToolButton

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
        f"panel. {_SHARED_MODE_TIP}",
    ),
    AreaSelectionMode.THRESHOLD: _Tool(
        threshold_icon,
        "Select by Threshold: pick this tool, then click the image to select the pixels that stand out of their blurred local background. "
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
    "wheel over this button. In the picture the pink circle is the selection now, the green circle the new region, grey = what ends up selected, "
    "empty = not selected or taken away. On the image, Shift adds, Alt takes away and Shift+Alt intersects, for one "
    "selection (this button shows it while the key is down); with no key the mode stays as picked."
)

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
    """Lock / Unlock, the selection tools, the mode picker, the select-by-value tools and Modify selection - one column beside the image, in five
    sections (module docstring)."""

    lock_requested = pyqtSignal()  # the Lock button: the selection becomes the fence
    unlock_requested = pyqtSignal()  # the Unlock button

    def __init__(
        self,
        selection: AreaSelectionModule,
        active_tool: ActiveToolModule,
        parent: QWidget | None = None,
        value_tool: ByValueSelector | None = None,
        brush_form: QWidget | None = None,
        modify_menu: QMenu | None = None,
    ) -> None:
        super().__init__(parent)
        self._value_tool = value_tool
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
        self._column = ScrollingColumn(self._content, self)
        outer.addWidget(self._column)

        self._wand_busy = False
        self._value_busy = False
        self._held_mode: str | None = None  # the combine mode a held Shift / Alt gives (`show_held_mode`); None = the picked mode shows
        self._tool_buttons: dict[AreaSelectionMode, QToolButton] = {}
        self._separators: list[QFrame] = []
        self._popups: dict[AreaSelectionMode, QMenu] = {}

        # 1. Lock / Unlock
        self._lock_button = QToolButton(self)
        _style_button(self._lock_button)
        self._lock_button.clicked.connect(lambda _checked=False: self.lock_requested.emit())
        layout.addWidget(self._lock_button)
        self._unlock_button = QToolButton(self)
        _style_button(self._unlock_button)
        self._unlock_button.clicked.connect(lambda _checked=False: self.unlock_requested.emit())
        layout.addWidget(self._unlock_button)
        self._add_separator(layout)

        # 2. Selection tools: Rectangle, Lasso, Wand, Draw
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

        # 3. The mode picker: the Venn diagram of the picked mode; click = a menu of the four, the wheel steps through them.
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

        # 4. Select by value: Histogram range, Threshold, Local contrast. One pop-up form, showing the numbers of the button that opened it.
        for mode in VALUE_MODES:
            self._add_tool(layout, mode)
        self._value_menu: QMenu | None = None
        if value_tool is not None:
            self._value_menu = QMenu(self)
            value_action = QWidgetAction(self._value_menu)
            value_action.setDefaultWidget(value_tool.form)
            self._value_menu.addAction(value_action)
            for mode in VALUE_MODES:
                button = self._tool_buttons[mode]
                button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                button.customContextMenuRequested.connect(lambda _pos, picked=mode: self.show_value_settings(picked))
            value_tool.busy_changed.connect(self._set_value_busy)
        self._add_separator(layout)

        # 5. Modify selection: Grow / Shrink / Open / Close / Fill holes / Invert and their radius, in a menu.
        self._modify_menu = modify_menu
        self._modify_button: QToolButton | None = None
        if modify_menu is not None:
            self._modify_button = QToolButton(self)
            _style_button(self._modify_button)
            self._modify_button.clicked.connect(lambda _checked=False: self.show_modify_menu())
            layout.addWidget(self._modify_button)
        layout.addStretch(1)
        self.setFixedWidth(_BAR_WIDTH)
        self._refresh_wand_tip()

        selection.mode_changed.connect(lambda _mode: self._refresh())
        selection.combine_mode_changed.connect(lambda _how: self._refresh())
        selection.selection_changed.connect(self._refresh)
        active_tool.active_tool_changed.connect(lambda _tool: self._refresh())
        self.refresh_theme(get_active_theme())

    # -- building ------------------------------------------------------------------------------------------

    def _add_tool(self, layout: QVBoxLayout, mode: AreaSelectionMode) -> None:
        """An armed tool's button (checkable; a click arms it, a second click leaves it). Only the wand's reads the wheel (its method)."""
        button: QToolButton = WheelToolButton(self) if mode is AreaSelectionMode.WAND else QToolButton(self)
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
        """Remember the wand's settings and the picked selection mode across restarts (`panels/ui_state.py`). The other tools' numbers are
        `MaskSettings`, saved with the session."""
        self.wand_form.bind_ui_state(store)
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
        return self._lock_button

    @property
    def unlock_button(self) -> QToolButton:
        return self._unlock_button

    @property
    def modify_button(self) -> QToolButton | None:
        return self._modify_button

    def _popup_beside(self, menu: QMenu, button: QToolButton) -> None:
        menu.popup(button.mapToGlobal(QPoint(button.width(), 0)))

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
        QToolTip.showText(button.mapToGlobal(QPoint(button.width(), 0)), f"Wand method: {label}", button)

    def _on_mode_wheel(self, steps: int) -> None:
        """The wheel over the mode button: the previous / next combine mode (wrapping), said in a tool tip beside the button."""
        how = COMBINE_MODES[(COMBINE_MODES.index(self._selection.combine_mode()) + int(steps)) % len(COMBINE_MODES)]
        self._selection.set_combine_mode(how)
        QToolTip.showText(self._mode_button.mapToGlobal(QPoint(self._mode_button.width(), 0)), f"Selection mode: {_MODE_LABELS[how]}", self._mode_button)

    def show_held_mode(self, how: str | None) -> None:
        """A held Shift / Alt (or the keys a drag began with) make the next selection combine as *how*: the mode button shows that mode's
        picture while the keys are down (2026-11-16, maintainer: the mode "linked to the keyboard keys"). ``None`` = no key: the picked mode
        shows again. The picked mode itself is not changed (the sticky choice stays what it was)."""
        if how == self._held_mode:
            return
        self._held_mode = how
        self._refresh_mode_button()

    def _refresh_mode_button(self) -> None:
        picked_mode = self._selection.combine_mode()
        shown = self._held_mode or picked_mode
        self._mode_button.setIcon(combine_mode_icon(shown))
        held_note = "\n(a held key: Shift adds, Alt takes away, Shift+Alt intersects)" if self._held_mode else ""
        self._mode_button.setToolTip(f"Selection mode: {_MODE_LABELS[shown]}{held_note}\n{_MODE_TIP}")

    # -- state -------------------------------------------------------------------------------------------------

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
                button.setToolTip(("Working...\n" if self._value_busy and on else "") + _TOOLS[mode].tip)

        picked_mode = self._selection.combine_mode()
        self._refresh_mode_button()
        for how, action in self._mode_actions.items():
            action.setIcon(combine_mode_icon(how))
            action.setChecked(how == picked_mode)

        has_selection = self._selection.has_selection()
        if self._modify_button is not None:
            self._modify_button.setEnabled(has_selection)
            self._modify_button.setToolTip(
                "Modify selection: Grow, Shrink, Open (remove specks), Close (fill gaps), Fill holes, Invert; the radius is in the menu."
                if has_selection else "Modify selection: make a selection first."
            )
        self._lock_button.setEnabled(has_selection)
        self._lock_button.setToolTip(
            "Lock to selection: the selection becomes the fence. Everything outside is dimmed; tools and new selections work only "
            "inside it (like a crop that keeps the image). A selection made inside a lock can be locked again to narrow it."
            if has_selection else "Lock to selection: make a selection first."
        )
        locked = self._selection.has_lock()
        # Amber (the lock border's colour on the image) while a lock is on, so the button says "you are locked"
        self._unlock_button.setIcon(
            load_tabler_icon("lock-open", color=BORDER_COLOR if locked else theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        )
        self._unlock_button.setEnabled(locked)
        self._unlock_button.setToolTip(
            "Unlock: leave the lock; the whole image can be selected and edited again. The selection stays."
            if locked else "Unlock: nothing is locked."
        )

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._column.refresh_theme()
        for button in (*self._tool_buttons.values(), self._mode_button, self._lock_button, self._unlock_button):
            _style_button(button)
        self._lock_button.setIcon(load_tabler_icon("lock", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        if self._modify_button is not None:
            _style_button(self._modify_button)
            self._modify_button.setIcon(morphology_icon())  # the old Morphology tool's icon (fixed colours)
        if self._modify_menu is not None and hasattr(self._modify_menu, "refresh_theme"):
            self._modify_menu.refresh_theme()
        for line in self._separators:
            line.setStyleSheet(f"color: {theme.control_border};")
        self.setStyleSheet("#selectionToolbar { border: none; }")
        self._refresh()
