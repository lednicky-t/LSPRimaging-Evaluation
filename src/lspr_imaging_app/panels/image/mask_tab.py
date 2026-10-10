"""The Image panel's "Mask" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: choose where mask edits land, show/hide/tint the mask, edit it
by hand or automatically, and load/save it as a PNG.

Layout (one ribbon row, captioned icon groups):

    [Clear] | [scope toggle] | [overlay show/colour/transparency] | [tool picker][its controls] | [PNG load/save]
     General      State                 Visibility                   Manual edit                      PNG

Display only (CLAUDE.md "panels/"): every button calls a module
(`MaskModule`, `MaskScopeModule`, `MaskEditToolModule`). The overlay's own
look (colour, transparency, visibility) is the panel's `OverlayTint`; this tab
only reports changes through its three `overlay_*_changed` signals and the
panel redraws.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskEditTool, MaskEditToolModule, MaskModule, MaskScopeModule
from ...selection import HighlightRangeModule
from ..ribbon_group import group_label_style, labeled_icon_group, vertical_separator
from .mask_edit_panels import (
    DrawEditPanel,
    HistogramSelectionEditPanel,
    LocalContrastEditPanel,
    MorphologyEditPanel,
    ThresholdEditPanel,
)
from .mask_edit_tool_picker import MaskEditToolPicker
from .mask_file_actions import MaskClearAction, MaskPngActions
from .mask_overlay_controls import MaskOverlayControls
from .mask_scope_toggle import MaskScopeToggle
from .overlay_tint import OverlayTint

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
        highlight_range: HighlightRangeModule,
        mask_scope: MaskScopeModule,
        tint: OverlayTint,
        image_panel: ImagePanel,
        parent: QWidget | None = None,
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

        # "Manual edit": a tool picker, and under it one small control row per
        # tool swapped in. `MaskEditToolModule` is owned privately here (see
        # that module's docstring for why nothing else shares it).
        self._edit_tool = MaskEditToolModule(self)
        self._edit_picker = MaskEditToolPicker(self._edit_tool, self)
        self._edit_stack = QStackedWidget(self)
        self._edit_panel_order: tuple[MaskEditTool, ...] = (
            MaskEditTool.HISTOGRAM_SELECTION,
            MaskEditTool.THRESHOLD,
            MaskEditTool.LOCAL_CONTRAST,
            MaskEditTool.MORPHOLOGY,
            MaskEditTool.DRAW,
        )
        self._edit_stack.addWidget(
            HistogramSelectionEditPanel(mask, geometry, chromatic, dataset, highlight_range, image_panel, mask_scope)
        )
        self._edit_stack.addWidget(ThresholdEditPanel(mask))
        self._edit_stack.addWidget(LocalContrastEditPanel(mask))
        self._edit_stack.addWidget(MorphologyEditPanel(mask, chromatic, dataset, image_panel, mask_scope))
        self._edit_stack.addWidget(DrawEditPanel(mask))
        self._on_edit_tool_changed(self._edit_tool.tool())
        self._edit_tool.tool_changed.connect(self._on_edit_tool_changed)

        # "General" (Clear) and "PNG" (Load/Save): plain action rows, not part of the picker/stack.
        self._clear_action = MaskClearAction(mask, image_panel, self)
        self._png_actions = MaskPngActions(mask, dataset, mask_scope, image_panel, self)

        general_group, self._general_label = labeled_icon_group(self, self._clear_action, "General")
        state_group, self._state_label = labeled_icon_group(self, self._scope_toggle, "State")
        visibility_group, self._visibility_label = labeled_icon_group(self, self._overlay_controls, "Visibility")
        # The caption wraps only the picker, not the picker+stack pair: otherwise it
        # would centre under the stack's reserved (widest-panel) width and float
        # away from the picker whenever a narrower panel is shown.
        edit_group, self._edit_label = labeled_icon_group(self, self._edit_picker, "Manual edit")
        png_group, self._png_label = labeled_icon_group(self, self._png_actions, "PNG")

        self._separators = [vertical_separator(self) for _ in range(4)]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(general_group)
        layout.addWidget(self._separators[0])
        layout.addWidget(state_group)
        layout.addWidget(self._separators[1])
        layout.addWidget(visibility_group)
        layout.addWidget(self._separators[2])
        layout.addWidget(edit_group)
        # Top-aligned so the stack's icon row lines up with every other group's icon row.
        layout.addWidget(self._edit_stack, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._separators[3])
        layout.addWidget(png_group)
        layout.addStretch(1)

    @property
    def overlay_controls(self) -> MaskOverlayControls:
        """The overlay show/colour/transparency controls (the View tab mirrors them)."""
        return self._overlay_controls

    @property
    def edit_tool(self) -> MaskEditToolModule:
        """Which manual-edit tool is picked (the panel remembers it across restarts)."""
        return self._edit_tool

    def _on_edit_tool_changed(self, tool: MaskEditTool) -> None:
        self._edit_stack.setCurrentIndex(self._edit_panel_order.index(tool))

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._overlay_controls.refresh_theme(theme)
        self._scope_toggle.refresh_theme(theme)
        self._edit_picker.refresh_theme(theme)
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for label in (self._general_label, self._state_label, self._visibility_label, self._edit_label, self._png_label):
            label.setStyleSheet(group_label_style())
