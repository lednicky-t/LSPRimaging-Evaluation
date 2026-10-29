"""The eye button on the Mask tab's Threshold, Local contrast and Morphology panels (2026-10-29): show or hide the preview of the pixels
the tool would select or change (`panels/mask_edit_preview.py`).

One state, three buttons: each panel has its own button (a widget has one parent) and every button mirrors `MaskEditPreview.shown`,
so a click on any of them switches all. The eye is drawn in the tint's own colour while the preview is on, so it says which overlay
it controls. Remembered across restarts by the panel (`IMAGE_MASK_EDIT_PREVIEW`)."""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ..mask_edit_common import BUTTON_SIZE, ICON_SIZE
from ..mask_edit_preview import MaskEditPreview

_RENDER_SIZE = ICON_SIZE * 2  # rendered at 2x, scaled down - crisper than a native bitmap
_STROKE_WIDTH = 2.1


def preview_color() -> QColor:
    """The preview tint's colour: the theme's `figure_mask_color`, the colour the stable app drew its Local contrast / Morphology
    previews in (distinct from the mask's red and the Histogram highlight's blue)."""
    return QColor(get_active_theme().figure_mask_color)


class EditPreviewToggle(QToolButton):
    def __init__(self, preview: MaskEditPreview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preview = preview
        self.setCheckable(True)
        self.setChecked(preview.shown())
        self.setAutoRaise(True)
        self.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setStyleSheet(transparent_icon_button_stylesheet())
        self.setToolTip("Show the pixels this tool would select or change on the image.")
        self.toggled.connect(self._on_toggled)
        preview.shown_changed.connect(self._mirror)
        self._refresh_icon()

    def _on_toggled(self, checked: bool) -> None:
        self._refresh_icon()
        self._preview.set_shown(bool(checked))

    def _mirror(self, shown: bool) -> None:
        if self.isChecked() != bool(shown):
            self.setChecked(bool(shown))  # `toggled` -> `set_shown`, which finds nothing to change

    def _refresh_icon(self) -> None:
        theme = get_active_theme()
        shown = self.isChecked()
        color = preview_color().name() if shown else theme.text_dim
        self.setIcon(
            load_tabler_icon("eye" if shown else "eye-off", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        )

    def refresh_theme(self) -> None:
        self.setStyleSheet(transparent_icon_button_stylesheet())
        self._refresh_icon()
