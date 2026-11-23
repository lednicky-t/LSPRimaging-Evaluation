"""The mask commands of the image's right-click menu (2026-11-23, maintainer request).

Two groups of entries, both on top of commands that already exist:

* **Mask > Add / Subtract / Convert to selection**: Add and Subtract (only while there is an area selection) are the Mask tab's Selection group
  (`MaskSelectionEditor.apply`), one undo step each. **Convert to selection** (only while the frame has a mask) is the Mask tab's "Selection
  from mask": the mask stays as it is and the selection is made from it (combined as the mode button / Shift / Alt say).
* **On a mask pixel**: *Remove blob*
  (the 8-connected blob under the pointer is taken out of the mask, one undo step; the blob is found in the displayed mask and mapped back to
  raw pixels like a selection is: exact without a rotation, about one pixel at the edge under one) and *Clear whole mask* (every cube and
  wavelength, both scopes; not undoable, so it asks first: the one confirmation of the Mask commands, as the removed Clear button did).

Widget-free but for the confirmation box. The menu itself is built by `CanvasInteraction._context_menu`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMessageBox, QWidget

from ...image_tools import MaskModule
from ..mask_selection_editor import MaskSelectionEditor
from ..menu_icons import give_icon
from .layer_wand import mask_blob

MASK_SUBMENU = "Mask"
MASK_ADD = "Add selection to mask"
MASK_SUBTRACT = "Subtract selection from mask"
MASK_CONVERT = "Convert mask to selection"
REMOVE_BLOB = "Remove blob"
CLEAR_MASK = "Clear whole mask"

MASK_ENTRIES = (MASK_ADD, MASK_SUBTRACT, MASK_CONVERT, REMOVE_BLOB, CLEAR_MASK)

def mask_submenu_filler(*, has_selection: bool, has_mask: bool) -> Callable[[QMenu], Mapping[QAction, str]]:
    """The filler of the "Mask" submenu (`context_menu.show_tool_context_menu(submenus=...)`): Add and Subtract need a selection, Convert to
    selection needs a mask (an entry whose prerequisite is not met is left out)."""

    def fill(target: QMenu) -> Mapping[QAction, str]:
        picked: dict[QAction, str] = {}
        if has_selection:
            picked[give_icon(target.addAction("Add"), "mask_add")] = MASK_ADD
            picked[give_icon(target.addAction("Subtract"), "mask_subtract")] = MASK_SUBTRACT
        if has_mask:
            picked[give_icon(target.addAction("Convert to selection"), "mask_to_selection")] = MASK_CONVERT
        return picked

    return fill


class MaskMenuActions:
    def __init__(
        self,
        editor: MaskSelectionEditor,
        mask: MaskModule,
        displayed_mask: Callable[[], np.ndarray | None],
        status: Callable[[str], None],
        dialog_parent: QWidget,
    ) -> None:
        self._editor = editor
        self._mask = mask
        self._displayed_mask = displayed_mask
        self._status = status
        self._dialog_parent = dialog_parent

    def has_mask(self) -> bool:
        """Whether the frame on screen has any mask pixel drawn."""
        mask = self._displayed_mask()
        return mask is not None and bool(mask.any())

    def on_mask_pixel(self, x: float, y: float) -> bool:
        """Whether the view point ``(x, y)`` lies on a pixel of the mask drawn at the frame on screen."""
        mask = self._displayed_mask()
        if mask is None:
            return False
        row, col = int(np.floor(y)), int(np.floor(x))
        return 0 <= row < mask.shape[0] and 0 <= col < mask.shape[1] and bool(mask[row, col])

    def run(self, entry: str, x: float, y: float) -> None:
        """Do the menu entry `entry` (one of `MASK_ENTRIES`); ``(x, y)`` is where the right-click was (view coordinates)."""
        if entry == MASK_ADD:
            self._editor.apply(subtract=False)
        elif entry == MASK_SUBTRACT:
            self._editor.apply(subtract=True)
        elif entry == MASK_CONVERT:
            self._editor.from_mask()
        elif entry == REMOVE_BLOB:
            self._remove_blob(x, y)
        elif entry == CLEAR_MASK:
            self._clear_all()

    def _remove_blob(self, x: float, y: float) -> None:
        mask = self._displayed_mask()
        if mask is None:
            self._status("Remove blob: there is no mask at this frame.")
            return
        blob = mask_blob(mask, int(np.floor(y)), int(np.floor(x)))
        if blob is None:
            self._status("Remove blob: no mask pixel under the click.")
            return
        self._editor.remove_region(blob)

    def _clear_all(self) -> None:
        choice = QMessageBox.question(
            self._dialog_parent,
            "Clear mask",
            "Clear the mask entirely, across every cube and wavelength? Clearing cannot be undone (unlike the other mask edits).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._mask.clear_all_masks()
        self._status("Mask cleared.")
