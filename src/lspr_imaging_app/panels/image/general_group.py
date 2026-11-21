"""The ribbon's shared icon-button helpers.

History: until 2026-10-23 an area-selection picker and the cursor-readout toggle sat in an always-visible "General"
group beside the ribbon tabs; from then until 2026-11-14 the picker (`AreaSelectionPicker`) was the first group of the
Mask and ROIs tabs. Since 2026-11-14 the selection tools live in the selection toolbar beside the image
(`selection_toolbar.py`); the picker and its `select_group.py` were deleted 2026-11-15 (maintainer OK).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QToolButton, QWidget

from ..icon_button_style import style_general_icon_button  # also re-exported: the Image ribbon files import it from here
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE  # also what the other ribbon files import from here

# The two sizes and the button style are re-exported: the Image ribbon files import them from here.
__all__ = ["BUTTON_SIZE", "ICON_SIZE", "mirror_icon_button", "style_general_icon_button"]


def mirror_icon_button(source: QToolButton, parent: QWidget, *, tip_suffix: str = "") -> QToolButton:
    """A second button that follows *source* (checked state, icon, tooltip,
    enabled) and forwards its clicks to it, so the same control can sit on
    two ribbon tabs. Call the returned button's `sync()` after *source*
    changes its look (icon/tooltip/enabled are not signals). `tip_suffix` is
    added to the source's tooltip (the View tab's note on the right-click)."""
    mirror = QToolButton(parent)
    mirror.setCheckable(source.isCheckable())

    def sync() -> None:
        style_general_icon_button(mirror)
        blocked = mirror.blockSignals(True)
        try:
            mirror.setChecked(source.isChecked())
        finally:
            mirror.blockSignals(blocked)
        mirror.setIcon(source.icon())
        mirror.setToolTip(source.toolTip() + tip_suffix)
        mirror.setEnabled(source.isEnabled())

    mirror.clicked.connect(lambda _checked=False: source.click())
    source.toggled.connect(lambda _checked: sync())
    mirror.sync = sync  # type: ignore[attr-defined]
    sync()
    return mirror
