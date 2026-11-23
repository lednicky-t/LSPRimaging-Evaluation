"""The Image panel's "Mask" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: choose where mask edits land, show/hide/tint the mask, edit it
by hand or automatically, and load/save it as a PNG.

Layout (one ribbon row, captioned icon groups):

    [Add/Subtract/Replace/From mask] | [Clear] | [scope toggle] | [Feature map controls] | [scan] | [wand][px] | [PNG]
     Selection                          General   State          Feature map              Scan   Auto mask   PNG

(2026-11-23: the Visibility group is gone; the View rail beside the image has the mask's show / colour / transparency.)

(2026-11-15: the Histogram selection, Threshold, Local contrast, Morphology and Draw tools moved to the selection toolbar beside the image;
2026-11-16: the Eraser went and with it the "Manual edit" picker: the Feature map, which acts on the mask itself, is the one tool left and its
section carries its name.)

Display only (CLAUDE.md "panels/"): every button calls a module
(`MaskModule`, `MaskScopeModule`). The overlay's own
look (colour, transparency, visibility) is the panel's `OverlayTint`; this tab
only reports changes through its three `overlay_*_changed` signals and the
panel redraws.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ...image_tools.mask.mask_pointers import MaskPointersModule
from ...image_tools.mask_auto_task import MaskAutoTask
from ...image_tools.mask_export_task import MaskExportTask
from ...image_tools.mask_scan_task import TransientScanTask
from ..feature_map_task import FeatureMapTask
from ..mask_edit_preview import MaskEditPreview
from ..ribbon_group import group_label_style, labeled_icon_group, ribbon_separator
from .mask_edit_panels import FeatureMapEditPanel
from .mask_file_actions import MaskClearAction, MaskPngActions
from .mask_overlay_controls import MaskOverlayControls
from .mask_scan_panels import MaskAutoControls, MaskScanControls
from .mask_scope_toggle import MaskScopeToggle
from .overlay_tint import OverlayTint
from ..mask_selection_editor import MaskSelectionEditor
from .mask_selection_actions import MaskSelectionActions

if TYPE_CHECKING:
    from .panel import ImagePanel


class MaskTab(QWidget):
    # The overlay controls changed (the View tab's copy reports through its own signals).
    overlay_visibility_changed = pyqtSignal(bool)
    overlay_color_changed = pyqtSignal(QColor)
    overlay_alpha_changed = pyqtSignal(float)

    def __init__(
        self,
        mask: MaskModule,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        mask_scope: MaskScopeModule,
        tint: OverlayTint,
        image_panel: ImagePanel,
        parent: QWidget | None = None,
        *,
        events: MaskPointersModule,
        scan: TransientScanTask,
        auto: MaskAutoTask,
        export: MaskExportTask,
        features: FeatureMapTask,
        status: Callable[[str], None],
        visible_cubes: Callable[[], tuple[int, int] | None] | None = None,
    ) -> None:
        super().__init__(parent)

        # Persistent/Individual mask-edit scope toggle. Reads/drives the same
        # `MaskScopeModule` the Workflow panel's `MaskHighlightActions` uses, so
        # the two toggles can never disagree about where a new mask edit lands.
        self._scope_toggle = MaskScopeToggle(mask_scope, self)

        # Mask-overlay show/hide + colour + transparency (see
        # mask_overlay_controls.py's module docstring for why this state lives
        # on the panel rather than on `MaskModule`).
        self._overlay_controls = MaskOverlayControls(
            visible=tint.visible, color=tint.color, alpha=tint.alpha, parent=self
        )
        self._overlay_controls.visibility_changed.connect(self.overlay_visibility_changed)
        self._overlay_controls.color_changed.connect(self.overlay_color_changed)
        self._overlay_controls.alpha_changed.connect(self.overlay_alpha_changed)

        # "Feature map": what the tool would add, previewed on the image (the panel draws it: `EditPreviewLayer`).
        # The frame on screen comes from the same signal the editors use; whether this tab is the open one and whether Play
        # runs are told by the panel (`ImagePanel` owns the ribbon and the slideshow).
        self._edit_preview = MaskEditPreview(mask, chromatic, dataset, parent=self, features=features)
        self._edit_preview.status.connect(status)
        image_panel.image_rendered.connect(lambda _image, cube, wavelength: self._edit_preview.set_frame((cube, wavelength)))
        image_panel.image_cleared.connect(lambda: self._edit_preview.set_frame(None))
        self._features_panel = FeatureMapEditPanel(
            mask, chromatic, dataset, image_panel, mask_scope, self._edit_preview, features, status
        )

        # "General" (Clear) and "PNG" (Load/Save): plain action rows, not part of the picker/stack.
        self._clear_action = MaskClearAction(mask, image_panel, self)
        self._png_actions = MaskPngActions(mask, dataset, mask_scope, image_panel, self, export_task=export)

        # "Selection" (2026-11-14): the area selection (selection toolbar beside the image) into / out of the mask.
        self._selection_editor = MaskSelectionEditor(mask, chromatic, dataset, image_panel, mask_scope, status, geometry=geometry)
        self._selection_actions = MaskSelectionActions(self._selection_editor, image_panel.area_selection(), self)
        selection_group, self._selection_label = labeled_icon_group(self, self._selection_actions, "Selection")
        general_group, self._general_label = labeled_icon_group(self, self._clear_action, "General")
        state_group, self._state_label = labeled_icon_group(self, self._scope_toggle, "State")
        # 2026-11-23: no "Visibility" group on this tab (the View rail beside the image has these controls). The widget stays as the
        # twin the View rail mirrors and the Layers button reads; it is hidden, never shown here.
        self._overlay_controls.hide()
        features_group, self._features_label = labeled_icon_group(self, self._features_panel, "Feature map")
        png_group, self._png_label = labeled_icon_group(self, self._png_actions, "PNG")
        # "Scan": marks the frames where something dark crossed the field with pointers on the Mask rows (no mask is changed).
        self._scan_controls = MaskScanControls(dataset, events, scan, status, self, visible_cubes=visible_cubes)
        scan_group, self._scan_label = labeled_icon_group(self, self._scan_controls, "Scan")
        # "Auto mask": turns the pointers into masks (a separate step: the scan only marks).
        self._auto_controls = MaskAutoControls(dataset, mask, chromatic, events, auto, scan, status, self)
        auto_group, self._auto_label = labeled_icon_group(self, self._auto_controls, "Auto mask")

        self._separators = [ribbon_separator(self) for _ in range(6)]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(selection_group)
        layout.addWidget(self._separators[0])
        layout.addWidget(general_group)
        layout.addWidget(self._separators[1])
        layout.addWidget(state_group)
        layout.addWidget(self._separators[2])
        layout.addWidget(features_group)
        layout.addWidget(self._separators[3])
        layout.addWidget(scan_group)
        layout.addWidget(self._separators[4])
        layout.addWidget(auto_group)
        layout.addWidget(self._separators[5])
        layout.addWidget(png_group)
        layout.addStretch(1)

    @property
    def selection_actions(self) -> MaskSelectionActions:
        """The Selection group's Add / Subtract buttons."""
        return self._selection_actions

    @property
    def overlay_controls(self) -> MaskOverlayControls:
        """The overlay show/colour/transparency controls (the View tab mirrors them)."""
        return self._overlay_controls

    @property
    def scan_controls(self) -> MaskScanControls:
        """The Scan group (the panel remembers its controls across restarts)."""
        return self._scan_controls

    @property
    def auto_controls(self) -> MaskAutoControls:
        """The Auto mask group (the panel remembers its feathering across restarts)."""
        return self._auto_controls

    @property
    def features_panel(self) -> FeatureMapEditPanel:
        """The Feature map tool's panel (the Image panel remembers its settings across restarts)."""
        return self._features_panel

    @property
    def edit_preview(self) -> MaskEditPreview:
        """The preview of what the picked tool would select (the panel draws it and remembers the eye toggle)."""
        return self._edit_preview

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._overlay_controls.refresh_theme(theme)
        self._scope_toggle.refresh_theme(theme)
        self._features_panel.refresh_theme()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for label in (
            self._selection_label, self._general_label, self._state_label, self._features_label, self._scan_label,
            self._auto_label, self._png_label
        ):
            label.setStyleSheet(group_label_style())
