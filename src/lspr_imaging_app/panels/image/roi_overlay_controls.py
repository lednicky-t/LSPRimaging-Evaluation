"""Image panel ribbon, "ROIs" tab: show/hide, colour and transparency for the
ROI circles drawn on the image - one control for the sample circles, one for
the reference rings. The same three-part control as the Mask tab's
(`mask_overlay_controls.py`): a toggle, a colour swatch, a transparency wedge.

Display-only cosmetic state, like the mask tint: `ImagePanel` owns the values
and does the drawing; this widget only shows them and reports changes. It is
not a `RoiToolbox` command - how circles are drawn changes no ROI and no result.

What "colour" means for the sample circles: the colour of ROIs that have none
of their own. A ROI in a group (or one coloured by hand) keeps its own colour
(`AreaRoi.sample_color_hex`); the swatch here is the default for the rest.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QToolButton, QWidget

from lspr_ui import CompactWedgeSlider, GuiTheme, get_active_theme

from .general_group import style_general_icon_button
from .roi_icons import ring_icon, spot_icon

_SWATCH_SIZE = 14
_OVERLAP_INFO = (
    "Rings may overlap each other. A ring never counts pixels of sample disks (the analysis removes every sample disk "
    "from every ring); sample disks are masked by your mask only."
)
_SLIDER_WIDTH = 18  # the same narrow, steep wedge as the Mask tab's

SAMPLE_ON_COLOR = "#c957e8"  # toggle icon while shown: pink-purple
REFERENCE_ON_COLOR = "#e6f2ff"  # toggle icon while shown: white with a slight glassy blue

_SPECS: dict[str, tuple[Callable[[bool, str, str], QIcon], str, str, str, str, str]] = {
    # kind: (icon, on colour, toggle tip, colour tip, transparency tip, colour dialog title)
    "sample": (
        spot_icon, SAMPLE_ON_COLOR,
        "Show or hide the sample ROI circles.",
        "Colour of sample ROIs that have none of their own (ROIs in a group keep their group tints).",
        "Sample ROI circle transparency.",
        "Choose the sample ROI colour",
    ),
    "reference": (
        ring_icon, REFERENCE_ON_COLOR,
        "Show or hide the reference rings.\n" + _OVERLAP_INFO,
        "Colour of the reference rings.",
        "Reference ring transparency.",
        "Choose the reference ring colour",
    ),
}


class RoiOverlayControls(QWidget):
    visibility_changed = pyqtSignal(bool)
    color_changed = pyqtSignal(QColor)
    alpha_changed = pyqtSignal(float)  # 0.0 - 1.0

    def __init__(self, kind: str, *, visible: bool, color: QColor, alpha: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_for, self._on_color, toggle_tip, color_tip, alpha_tip, self._dialog_title = _SPECS[kind]
        self._color = QColor(color)

        self._toggle_button = QToolButton(self)
        self._toggle_button.setCheckable(True)
        self._toggle_button.setChecked(bool(visible))
        self._toggle_button.setToolTip(toggle_tip)
        self._toggle_button.toggled.connect(self._on_toggled)

        self._color_button = QToolButton(self)
        self._color_button.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        self._color_button.setToolTip(color_tip)
        self._color_button.clicked.connect(self._on_choose_color)

        self._alpha_slider = CompactWedgeSlider(parent=self)
        self._alpha_slider.setFixedWidth(_SLIDER_WIDTH)
        self._alpha_slider.setRange(0, 100)
        self._alpha_slider.setValue(int(round(float(alpha) * 100.0)))
        self._alpha_slider.setToolTip(alpha_tip)
        self._alpha_slider.valueChanged.connect(lambda value: self.alpha_changed.emit(float(value) / 100.0))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle_button)
        layout.addWidget(self._color_button)
        layout.addWidget(self._alpha_slider)

        self.refresh_theme(get_active_theme())

    def set_state(self, *, visible: bool, color: QColor, alpha: float) -> None:
        """Show these values without emitting anything (restoring saved state)."""
        for widget in (self._toggle_button, self._alpha_slider):
            widget.blockSignals(True)
        self._toggle_button.setChecked(bool(visible))
        self._alpha_slider.setValue(int(round(float(alpha) * 100.0)))
        for widget in (self._toggle_button, self._alpha_slider):
            widget.blockSignals(False)
        self._color = QColor(color)
        self._refresh_toggle_icon()
        self._refresh_swatch()

    def sync_from(self, other: RoiOverlayControls) -> None:
        """Copy another instance's state without emitting anything (the same controls sit on the ROIs and View tabs)."""
        self.set_state(
            visible=other._toggle_button.isChecked(), color=other._color, alpha=other._alpha_slider.value() / 100.0
        )

    def _on_toggled(self, checked: bool) -> None:
        self._refresh_toggle_icon()
        self.visibility_changed.emit(bool(checked))

    def _on_choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, self._dialog_title)
        if not color.isValid():
            return
        self._color = color
        self._refresh_swatch()
        self.color_changed.emit(QColor(color))

    def _refresh_toggle_icon(self) -> None:
        self._toggle_button.setIcon(
            self._icon_for(self._toggle_button.isChecked(), get_active_theme().text_dim, self._on_color)
        )

    def _refresh_swatch(self) -> None:
        theme = get_active_theme()
        self._color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._color.name()}; min-width: {_SWATCH_SIZE}px; "
            f"max-width: {_SWATCH_SIZE}px; min-height: {_SWATCH_SIZE}px; max-height: {_SWATCH_SIZE}px; "
            f"border: 1px solid {theme.control_border}; border-radius: 3px; padding: 0; }}"
        )

    def refresh_theme(self, _theme: GuiTheme) -> None:
        style_general_icon_button(self._toggle_button)
        self._refresh_toggle_icon()
        self._refresh_swatch()
