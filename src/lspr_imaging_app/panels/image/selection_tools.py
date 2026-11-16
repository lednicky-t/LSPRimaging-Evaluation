"""The selection toolbar with its tools and their right-drag gestures, built and wired in one place (2026-11-15; lifted out of
`ImagePanel._build_toolbar_and_layout`, which had grown past its size budget: `tests/unit/test_lspri_panel_size_budget.py`).

Owns the objects that belong together: `SelectionToolbar`, the wand (`WandTask`, its settings form lives in the toolbar), the select-by-value
tools (`ByValueSelector`), the brush size form, the Modify selection menu, and the right-drag adjusters of the tools that have numbers
(`wand_drag.py`, `tool_drag.py`). The panel hands in the few modules and callables they need and takes back `toolbar`, `drag_adjusters` (armed tool
-> its right-drag handler) and `run_value_tool` (a click on the image with a select-by-value tool armed). Widget code only: no selection logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PyQt6.QtWidgets import QGraphicsView

from ...image_tools import ActiveToolModule, GeometryModule, ImageTool, MaskModule
from ...selection import AreaSelectionMode, AreaSelectionModule, HighlightRangeModule
from ..mask_edit_preview import KIND_THRESHOLD, MaskEditPreview
from .area_selection_tool import AreaSelectionTool
from .brush_settings import BrushSettingsForm
from .combine_badge import CombineBadge
from .histogram_highlight_overlay_controls import HistogramHighlightOverlayControls
from .histogram_overlay_toggle import HistogramOverlayToggle
from .selection_by_value import MODE_METHODS, ByValueSelector
from .selection_modify import ModifySelectionMenu
from .selection_regions import SelectionRegions
from .selection_toolbar import SelectionToolbar
from .tool_drag import DragAdjuster, DragAxis
from .wand_drag import WandDragAdjuster
from .wand_task import WandTask

if TYPE_CHECKING:
    import pyqtgraph as pg

    from .panel import ImagePanel


class SelectionTools:
    def __init__(
        self,
        panel: ImagePanel,
        *,
        mask: MaskModule,
        geometry: GeometryModule,
        highlight_range: HighlightRangeModule,
        preview: MaskEditPreview,
        area_selection: AreaSelectionModule,
        active_tool: ActiveToolModule,
        area_tool: AreaSelectionTool,
        plot: pg.PlotItem,
        view: QGraphicsView,
        regions: SelectionRegions,
        image_shape: Callable[[], tuple[int, int] | None],
        status: Callable[[str], None],
        highlight_controls: HistogramHighlightOverlayControls,
    ) -> None:
        self.value_selector = ByValueSelector(mask, geometry, highlight_range, panel, preview, area_selection, status, panel)
        # The Histogram range's pop-up carries a copy of the View tab's show / hide switch for the highlight tint (`histogram_overlay_toggle.py`).
        self.value_selector.form.add_overlay_toggle(HistogramOverlayToggle(highlight_controls, self.value_selector.form))
        self.brush_form = BrushSettingsForm(mask, panel)
        self.modify_menu = ModifySelectionMenu(mask, area_selection, image_shape, status, panel)
        self.toolbar = SelectionToolbar(
            area_selection, active_tool, panel, value_tool=self.value_selector, brush_form=self.brush_form, modify_menu=self.modify_menu,
            style=area_tool.style,  # the outlines' colours and fills: the toolbar's View pop-up writes them, the outlines and the Venn icons follow
        )
        # The + / - / x beside the pointer (Shift / Alt / Shift+Alt, or the mode picker): what the next selection will do. It also tells the
        # toolbar's mode button which mode a held key gives.
        self.combine_badge = CombineBadge(
            plot, view, area_selection,
            armed=lambda: active_tool.active() is ImageTool.SELECT_AREA and area_selection.mode() is not AreaSelectionMode.ALL,
            gesture_how=area_tool.gesture_how, parent=panel,
        )
        self.combine_badge.held_mode_changed.connect(self.toolbar.show_held_mode)  # the mode button follows Shift / Alt
        self.wand = WandTask(area_selection, self.toolbar.wand_form.settings, status, panel)
        self.wand_drag = WandDragAdjuster(self.toolbar.wand_form, view)
        self.wand.busy_changed.connect(self.toolbar.set_wand_busy)
        area_selection.undo_skipped.connect(status)  # an undo meeting a changed pixel grid says why it did nothing
        self.toolbar.lock_requested.connect(regions.lock_to_selection)
        self.toolbar.unlock_requested.connect(regions.unlock_selection)
        panel.image_rendered.connect(lambda image, _cube, _wavelength: self.wand.set_image(image))
        panel.image_cleared.connect(lambda: self.wand.set_image(None))

        # Right-drag on the image changes the armed tool's numbers. The Histogram range has none here (its range is the Histogram panel's).
        def brush_ring(ev: Any) -> None:  # the ring cursor follows the size while it is dragged
            down = plot.vb.mapSceneToView(ev.buttonDownScenePos())
            area_tool.show_brush_cursor(float(down.x()), float(down.y()))

        size = self.brush_form.size_spin
        brush = DragAdjuster(
            view, lambda: [DragAxis("Brush size", size.value, size.setValue, size.minimum(), size.maximum(), "x", integer=True, suffix=" px")],
            after_change=brush_ring,
        )
        threshold = DragAdjuster(view, lambda: self.value_selector.drag_axes(MODE_METHODS[AreaSelectionMode.THRESHOLD]))
        contrast = DragAdjuster(view, lambda: self.value_selector.drag_axes(MODE_METHODS[AreaSelectionMode.CONTRAST]))
        self.drag_adjusters: dict[AreaSelectionMode, Callable[[Any], bool]] = {
            AreaSelectionMode.WAND: self.wand_drag.on_right_drag,
            AreaSelectionMode.BRUSH: brush.on_right_drag,
            AreaSelectionMode.THRESHOLD: threshold.on_right_drag,
            AreaSelectionMode.CONTRAST: contrast.on_right_drag,
        }
        self.value_drag = {AreaSelectionMode.THRESHOLD: threshold, AreaSelectionMode.CONTRAST: contrast, AreaSelectionMode.BRUSH: brush}

        # The Threshold's live preview (2026-11-16): while its tool is armed the pixels a click would select are tinted on the image
        # (`MaskEditPreview.set_value_tool`; the tint layer draws them), for the side the form says. The preview watches the numbers and the
        # frame itself.
        form = self.value_selector.form

        def sync_live_preview(*_args: object) -> None:
            armed = active_tool.active() is ImageTool.SELECT_AREA and area_selection.mode() is AreaSelectionMode.THRESHOLD
            preview.set_value_tool(KIND_THRESHOLD if armed else None, form.threshold_side())

        active_tool.active_tool_changed.connect(sync_live_preview)
        area_selection.mode_changed.connect(sync_live_preview)
        form.threshold_side_changed.connect(sync_live_preview)
        sync_live_preview()

    def run_value_tool(self, mode: AreaSelectionMode, how: str) -> None:
        """A click on the image with a select-by-value tool armed (`CanvasInteraction`)."""
        self.value_selector.run_for_mode(mode, how)
