"""Image panel ribbon, "ROIs" tab: the "ROI display" pick-up menu.

One icon button (a filled circle inside a filled ring: the sample and reference
icons together) that opens a small panel with one row per thing drawn on the
image: Sample (show, colour, transparency), Reference (the same) and Labels
(show, a 3 x 3 grid for where the label sits around its ROI, and the text size
in pt). It only *holds* the controls `ImagePanel` builds (`RoiOverlayControls`,
the labels button, the placement picker, the size box); it adds no state and no signals of its own, so
every control behaves exactly as it did on the tab.

The panel is a `QWidgetAction` inside a `QMenu`: it opens on click and closes
when you click outside it, like the other pick-up menus in this ribbon.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme

from .general_group import style_general_icon_button
from .roi_overlay_controls import REFERENCE_ON_COLOR, SAMPLE_ON_COLOR
from .roi_icons import display_icon


class RoiDisplayMenu(QToolButton):
    def __init__(
        self,
        sample: QWidget,
        reference: QWidget,
        labels_button: QToolButton,
        placement_picker: QWidget,
        size_box: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(
            "ROI display: show or hide, colour and transparency of the sample circles and reference rings, "
            "and the labels with where they sit around each ROI and their text size."
        )

        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        labels_row = QWidget(panel)  # the show / hide toggle, the 3 x 3 place picker and the text size
        labels_layout = QHBoxLayout(labels_row)
        labels_layout.setContentsMargins(0, 0, 0, 0)
        labels_layout.setSpacing(8)
        labels_layout.addWidget(labels_button)
        labels_layout.addWidget(placement_picker)
        labels_layout.addWidget(size_box)
        labels_layout.addStretch(1)
        self._captions: list[QLabel] = []
        for row, (text, controls) in enumerate((("Sample", sample), ("Reference", reference), ("Labels", labels_row))):
            caption = QLabel(text, panel)
            self._captions.append(caption)
            grid.addWidget(caption, row, 0)
            grid.addWidget(controls, row, 1)  # reparents the control into the panel

        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme(get_active_theme())

    def refresh_theme(self, _theme: GuiTheme) -> None:
        style_general_icon_button(self)
        self.setIcon(display_icon(SAMPLE_ON_COLOR, REFERENCE_ON_COLOR))
