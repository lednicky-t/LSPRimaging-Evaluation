"""The Mask tab's "General" and "PNG" groups (2026-10-02 maintainer
request): "copy icons from mask to load/save mask as file... these two
icons should be in 'PNG' section... put [Clear] in solo section 'General'
and put section the most left."

Two small widgets, not one - `MaskClearAction` (just Clear, the Mask tab's
own leftmost group) and `MaskPngActions` (Load/Save) - replacing this
file's original single combined `MaskFileActions` ("Automatic edit") now
that the maintainer wants them visually and conceptually separate groups,
not one.

**Not a port of the stable app's `MaskController.create_new_mask`/
`load_mask_from_file`/`save_mask_to_file`** (`gui/mask_controller.py`) -
that logic is built entirely around concepts this rewrite's `MaskModule`
deliberately replaced (2026-09-21's timeline redesign, see that module's own
docstring): a single "the current file mask" array per image record, a
reference-image/chromatic-correction coupling, and a `_resolve_sidecar_path`
convention tied to one-record-per-file. `MaskModule` instead stores a
frame-and-scope-tagged timeline (`MaskChange`), so "load"/"save" here target
whatever frame/scope is currently selected (`MaskScopeModule`, the same
shared toggle the rest of this ribbon already uses), not a per-record file
path. The pure read/write math is still the stable app's own, ported
verbatim into `image_tools/mask/io.py` (`read_mask_image`/
`write_mask_image`) well before this file existed - these widgets only add
the interactive `QFileDialog` layer that module's own docstring flagged as
"not built yet, panel-layer work."

**Icons/colors copied from the stable app's own choices** where a direct
counterpart exists (`load`: `download`, blue `#38bdf8`; `save`: `upload`,
green `#22c55e` - the stable app's own `mask_load_from_file_button`/
`mask_save_button`). Clear is new (the stable app's nearest equivalent,
`mask_create_new_button`, used a "sparkles" icon that reads more like
"generate" than "erase" for this rewrite's purposes) - a plain `eraser`
icon, in a neutral muted tone since this is a plain destructive action, not
something to tint add/subtract-style.

**Clear is the one real design decision here, not just a UI port**: 2026-09
21's design doc flags there is no undo for *any* Mask command
(`MaskModule`'s own docstring, "Not wired through undo_manager"), and this
action wipes the *entire* timeline (`MaskModule.clear_all_masks`) - every
cube, every wavelength, both scopes - not just the current frame. A
confirmation prompt before this one specifically is a deliberate, judged
exception to how quietly every other command here commits (Add/Subtract/
Morphology act on one frame and are trivially redone by pressing the
opposite button; this one is not).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QWidget

from ...dataset import DatasetModule
from ...image_tools import MaskModule, MaskScopeModule
from ...image_tools.mask.io import read_mask_image, write_mask_image
from ..mask_edit_common import action_button

if TYPE_CHECKING:
    # Deferred - see mask_highlight_actions.py's own identical note for why
    # a plain top-level import here would close a real import cycle.
    from .panel import ImagePanel

_CLEAR_COLOR = "#f8fafc"  # GuiTheme.text_primary - 2026-10-02 maintainer request: white, since it's a real, active action
_LOAD_COLOR = "#38bdf8"  # GuiTheme.accent_blue - the stable app's own literal for mask_load_from_file_button
_SAVE_COLOR = "#22c55e"  # GuiTheme.accent_green - the stable app's own literal for mask_save_button
_MASK_FILE_FILTER = "Mask images (*.png *.bmp *.tif *.tiff);;All files (*)"


class _FrameTrackingWidget(QWidget):
    """Shared "know the current (cube, wavelength) frame" bookkeeping both
    `MaskClearAction` and `MaskPngActions` need - factored out so the two
    don't each reimplement the same `image_rendered`/`image_cleared`
    wiring `mask_edit_panels.py`'s own panels already use.

    Only the plain `QWidget` base is constructed here - signal connection
    is a separate `_connect_frame_tracking` call each subclass makes once
    its own buttons exist (`_on_image_rendered` below calls
    `_refresh_enabled`, which touches those buttons; connecting before they
    exist would be a real, if theoretical, attribute-error risk if a signal
    ever fired mid-construction)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_frame: tuple[int, float] | None = None

    def _connect_frame_tracking(self, image_panel: ImagePanel) -> None:
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    def _on_image_rendered(self, _image: object, cube_index: int, wavelength_nm: float) -> None:
        self._last_frame = (cube_index, wavelength_nm)
        self._refresh_enabled()

    def _on_image_cleared(self) -> None:
        self._last_frame = None
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        raise NotImplementedError


class MaskClearAction(_FrameTrackingWidget):
    """Solo "General" group - just Clear (eraser). Wipes the whole mask
    timeline after a confirmation prompt - see module docstring."""

    def __init__(self, mask: MaskModule, image_panel: ImagePanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._image_panel = image_panel

        self._clear_button = action_button(self, "eraser", _CLEAR_COLOR, "Clear the mask entirely (start a new mask).")
        self._clear_button.clicked.connect(self._on_clear)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._clear_button)

        self._connect_frame_tracking(image_panel)
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        self._clear_button.setEnabled(self._last_frame is not None)

    def _on_clear(self) -> None:
        # The one Mask command with a confirmation prompt - see module
        # docstring for why (wipes the whole timeline, no undo exists for
        # any Mask command).
        choice = QMessageBox.question(
            self,
            "Clear mask",
            "Clear the mask entirely, across every cube and wavelength? "
            "This cannot be undone - Mask edits are not tracked by Undo.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._mask.clear_all_masks()
        self._image_panel.tool_status_changed.emit("Mask cleared.")


class MaskPngActions(_FrameTrackingWidget):
    """"PNG" group - Load (download) / Save (upload)."""

    def __init__(
        self,
        mask: MaskModule,
        dataset: DatasetModule,
        mask_scope: MaskScopeModule,
        image_panel: ImagePanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._dataset = dataset
        self._mask_scope = mask_scope
        self._image_panel = image_panel

        self._load_button = action_button(self, "download", _LOAD_COLOR, "Load a mask image into the current frame/scope.")
        self._load_button.clicked.connect(self._on_load)

        self._save_button = action_button(self, "upload", _SAVE_COLOR, "Save the current frame's mask as an image.")
        self._save_button.clicked.connect(self._on_save)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._load_button)
        layout.addWidget(self._save_button)

        self._connect_frame_tracking(image_panel)
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        enabled = self._last_frame is not None
        self._load_button.setEnabled(enabled)
        self._save_button.setEnabled(enabled)

    def _starting_directory(self) -> Path:
        home = self._dataset.dataset_home()
        if home is None:
            return Path.home()
        masks_dir = home / "analysis" / "masks"
        return masks_dir if masks_dir.is_dir() else home

    def _on_load(self) -> None:
        if self._last_frame is None:
            return
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            return
        source, _selected_filter = QFileDialog.getOpenFileName(
            self, "Load mask image", str(self._starting_directory()), _MASK_FILE_FILTER
        )
        if not source:
            return
        try:
            loaded = read_mask_image(Path(source), expected_shape=raw_shape)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Load mask failed", str(exc))
            return
        self._mask.set_mask_change(self._last_frame, self._mask_scope.scope().value, loaded)
        self._image_panel.tool_status_changed.emit(f"Loaded mask from {Path(source).name}.")

    def _on_save(self) -> None:
        if self._last_frame is None:
            return
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            return
        resolution = self._mask.resolve_mask_source(self._last_frame)
        current_mask = resolution[1] if resolution is not None else np.zeros(raw_shape, dtype=bool)
        cube_index, wavelength_nm = self._last_frame
        default_name = f"mask_cube{cube_index}_{wavelength_nm:.0f}nm_{self._mask_scope.scope().value}.png"
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self, "Save mask image", str(self._starting_directory() / default_name), "PNG image (*.png)"
        )
        if not destination:
            return
        try:
            write_mask_image(current_mask, Path(destination))
        except OSError as exc:
            QMessageBox.critical(self, "Save mask failed", str(exc))
            return
        self._image_panel.tool_status_changed.emit(f"Saved mask to {Path(destination).name}.")
