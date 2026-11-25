"""The Image panel's ribbon tabs and top bar, built (2026-11-15; moved out of `ImagePanel`: `_build_image_tools_tab`, `_build_mask_tab`,
`_build_roi_tab`, `_build_view_tab`, `_build_chromatic_and_background_tabs`, `_build_top_bar`, about 200 lines).

The tool ribbon's pages (Image tools, Mask, ROIs, Chromatic, Background, Analysis, Export) with the connections of their signals to the panel's
handlers, and the top bar that holds the ribbon (its roll-up drawer, the cursor-readout icon, the "i" help icon). The View controls are not a page
any more (2026-11-22): `build_view_tab` builds them as the View ribbon beside the image.

**What this is, honestly:** the construction code moved as it was (`self` is `panel`): functions that reach into the panel's private fields, listed in each
docstring. It is a first step; see `docs/image_panel_split_investigation_2026-11-15.md` for the next one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QToolButton,
    QWidget,
)
from lspr_ui import get_active_theme
from ..cursor_overlay import CursorOverlay
from ..ribbon_drawer import RibbonDrawer
from .transforms_settings import TransformsSection
from .background_tab import BackgroundTab
from .chromatic_tab import ChromaticCorrectionTab
from .canvas_tools import style_bar_icon_button
from .general_group import style_general_icon_button
from .mask_tab import MaskTab
from .array_star import ArrayStarController
from .roi_tab import RoiTab
from .side_rails import LEFT, RIGHT, SideRail, SideRails
from .view_ribbon import ViewRibbon
from .view_tab import ViewTab
from .analysis_tab import AnalysisTab
from .tuning_tab import TuningTab
from .export_tab import ExportTab
from .tool_ribbon import ROI_TAB, ImageToolRibbon
from .panel_constants import (
    ANALYSIS_TAB,
    BACKGROUND_TAB,
    CHROMATIC_TAB,
    EXPORT_TAB,
    MASK_TAB,
)

if TYPE_CHECKING:
    from .panel import ImagePanel


def build_image_tools_tab(panel: ImagePanel) -> QWidget:
    """Moved out of `ImagePanel._build_image_tools_tab` (2026-11-15). On the panel it
    creates: `_transforms_section`
    reads: `_active_tool`, `_geometry`
    calls its methods: none
    """
    # A second `TransformsSection` (maintainer, 2026-09-30: "duplicate transform tools and put them in image tools of image panel"): a second front door
    # onto the same `GeometryModule` / `ActiveToolModule` as the first one (see its docstring), so the two cannot drift apart.
    panel._transforms_section = TransformsSection(panel._geometry, panel._active_tool, panel)

    image_tools_content = QWidget(panel)
    image_tools_content_layout = QHBoxLayout(image_tools_content)
    image_tools_content_layout.setContentsMargins(0, 0, 0, 0)
    image_tools_content_layout.setSpacing(6)
    image_tools_content_layout.addWidget(panel._transforms_section)
    image_tools_content_layout.addStretch(1)
    return image_tools_content


def build_mask_tab(panel: ImagePanel) -> QWidget:
    """Moved out of `ImagePanel._build_mask_tab` (2026-11-15). On the panel it
    creates: `_mask_tab`
    reads: `_chromatic`, `_dataset`, `_edit_preview_layer`, `_feature_map`, `_geometry`, `_mask`, `_mask_auto`, `_mask_export`,
    `_mask_pointers`, `_mask_scope`, `_mask_tint`, `_tints`, `_transient_scan`
    calls its methods: none
    """
    panel._mask_tab = MaskTab(
        panel._mask, panel._geometry, panel._chromatic, panel._dataset,
        panel._mask_scope, panel._mask_tint, panel, panel,
        events=panel._mask_pointers, scan=panel._transient_scan, auto=panel._mask_auto, export=panel._mask_export,
        features=panel._feature_map, status=panel._on_tool_status, visible_cubes=panel._visible_cube_range,
    )
    panel._mask_tab.overlay_visibility_changed.connect(panel._tints.on_mask_visibility_changed)
    panel._mask_tab.overlay_color_changed.connect(panel._tints.on_mask_color_changed)
    panel._mask_tab.overlay_alpha_changed.connect(panel._tints.on_mask_alpha_changed)
    panel._edit_preview_layer.set_preview(panel._mask_tab.edit_preview)
    return panel._mask_tab


def build_roi_tab(panel: ImagePanel) -> QWidget:
    """Moved out of `ImagePanel._build_roi_tab` (2026-11-15). On the panel it
    creates: `_roi_tab`, `_array_star`
    reads: `_array_action`, `_background`, `_chromatic`, `_dataset`, `_edit_target`, `_geometry`, `_roi_overlay`, `_roi_scope`,
    `_roi_toolbox`, `_selection`, `_transforms_section`
    calls its methods: `_on_auto_crop_readiness`, `_resolve_reference_frame`, `_draw_roi_overlay` and connects the handlers `_on_roi_overlay_changed`,
    `_on_roi_labels_toggled`, `_on_roi_label_placement_changed`, `_on_group_labels_changed`, `_on_tool_status`, `_on_transforms_navigate`
    """
    panel._roi_tab = RoiTab(
        panel._roi_overlay, panel._roi_scope, panel._geometry, panel._background, panel._chromatic, panel._dataset,
        panel._roi_toolbox, panel._selection, panel._array_action, panel._edit_target,
        panel._resolve_reference_frame, panel,
    )
    panel._roi_tab.overlay_changed.connect(panel._on_roi_overlay_changed)
    panel._roi_tab.labels_toggled.connect(panel._on_roi_labels_toggled)
    panel._roi_tab.links_visible_toggled.connect(lambda shown: _on_links_toggled(panel, shown))
    panel._roi_overlay.links_visible = panel._roi_tab.links_button.isChecked()
    panel._roi_tab.label_placement_changed.connect(panel._on_roi_label_placement_changed)
    panel._roi_tab.label_size_changed.connect(panel._roi_overlay.set_label_point_size)
    panel._roi_tab.group_labels_changed.connect(panel._on_group_labels_changed)
    panel._roi_tab.status.connect(panel._on_tool_status)
    # Array* (manual array with a live preview on the canvas): the controller draws on the plot, the tab gives its button a place.
    panel._array_star = ArrayStarController(
        plot=panel._plot, toolbox=panel._roi_toolbox, selection=panel._selection, chromatic=panel._chromatic, geometry=panel._geometry,
        current_frame=lambda: (panel._current_cube(), panel._current_wavelength()), host=panel, parent=panel,
    )
    panel._array_star.status.connect(panel._on_tool_status)
    panel._roi_tab.add_array_star(panel._array_star)
    # Auto rotate and Auto crop (Image tools tab) act through the ROI tab, which owns the array tools; that
    # tab is built before this one. Auto crop also shows which of its dependencies are still missing, and its
    # menu rows lead to the tab that fixes them.
    panel._transforms_section.auto_rotate_requested.connect(panel._roi_tab.auto_rotate)
    panel._transforms_section.auto_crop_requested.connect(panel._roi_tab.auto_crop)
    panel._transforms_section.navigate_requested.connect(panel._on_transforms_navigate)
    panel._roi_tab.auto_crop_readiness_changed.connect(panel._on_auto_crop_readiness)
    panel._on_auto_crop_readiness(panel._roi_tab.auto_crop_readiness())
    return panel._roi_tab


def build_view_tab(panel: ImagePanel) -> None:
    """Moved out of `ImagePanel._build_view_tab` (2026-11-15). On the panel it
    creates: `_view_tab`, `_view_ribbon` (2026-11-22: the controls live only in the column beside the image, `view_ribbon.py`; the top
    ribbon has no View tab any more)
    reads: `_background_tab`, `_chromatic_tab`, `_dataset`, `_export_tab`, `_geometry`, `_highlight_tint`, `_mask_tab`, `_mask_tint`, `_roi_tab`,
    `_scale_bar_item`, `_tints`
    calls its methods: none
    """
    panel._view_tab = ViewTab(
        panel._geometry, panel._highlight_tint, panel._mask_tint, panel._mask_tab.overlay_controls,
        panel._chromatic_tab, panel._background_tab, panel._roi_tab, panel._scale_bar_item.color(),
        panel._dataset, panel._export_tab, panel,
    )
    panel._view_tab.highlight_visibility_changed.connect(panel._tints.on_highlight_visibility_changed)
    panel._view_tab.highlight_color_changed.connect(panel._tints.on_highlight_color_changed)
    panel._view_tab.highlight_alpha_changed.connect(panel._tints.on_highlight_alpha_changed)
    panel._view_tab.mask_visibility_changed.connect(panel._tints.on_mask_visibility_changed)
    panel._view_tab.mask_color_changed.connect(panel._tints.on_mask_color_changed)
    panel._view_tab.mask_alpha_changed.connect(panel._tints.on_mask_alpha_changed)
    panel._view_tab.roi_overlay_changed.connect(panel._on_roi_overlay_changed)
    panel._view_tab.scale_bar_color_changed.connect(panel._on_scale_bar_color_changed)
    panel._view_tab.display_range_changed.connect(panel._on_display_range_edited)
    panel._view_ribbon = ViewRibbon(panel._view_tab, panel)  # put beside the image by `_build_toolbar_and_layout`


def build_chromatic_and_background_tabs(panel: ImagePanel) -> None:
    """Moved out of `ImagePanel._build_chromatic_and_background_tabs` (2026-11-15). On the panel it
    creates: `_analysis_tab`, `_background_tab`, `_chromatic_tab`, `_export_tab`, `_tuning_tab`
    reads: `_background`, `_chromatic`, `_chromatic_auto`, `_dataset`, `_decorations`, `_frame_roles`, `_geometry`,
    `_initial_chromatic_values`, `_initial_chromatic_view`, `_roi_toolbox`, `_session_carry`, `_show_background`,
    `chromatic_settings_applied`
    calls its methods: none
    """
    panel._chromatic_tab = ChromaticCorrectionTab(
        panel._chromatic,
        panel._chromatic_auto,
        panel._dataset,
        panel._geometry,
        panel._resolve_reference_frame,
        panel._image_aspect,
        panel,
        initial_values=panel._initial_chromatic_values,
        initial_show_landmarks=panel._initial_chromatic_view[0],
        initial_all_wavelengths=panel._initial_chromatic_view[1],
        is_spectral=None if panel._frame_roles is None else panel._frame_roles.is_spectral,
    )
    panel._chromatic_tab.show_landmarks_changed.connect(panel._decorations.on_show_landmarks_changed)
    panel._chromatic_tab.landmark_scope_changed.connect(panel._decorations.on_landmark_scope_changed)
    panel._chromatic_tab.settings_applied.connect(panel.chromatic_settings_applied)
    panel._background_tab = BackgroundTab(
        panel._background, show_background=panel._show_background, parent=panel
    )
    panel._background_tab.show_background_changed.connect(panel._on_show_background_changed)
    panel._analysis_tab = AnalysisTab(panel)
    panel._analysis_tab.info_changed.connect(panel._on_analysis_info_changed)
    panel._analysis_tab.status_message.connect(panel._on_tool_status)
    panel._tuning_tab = TuningTab(panel)  # a section of the ROIs tab (it was a tab of its own until 2026-10-29), attached in `_build_toolbar_and_layout`
    panel._tuning_tab.status_message.connect(panel._on_tool_status)
    panel._export_tab = ExportTab(
        panel._dataset, panel, geometry=panel._geometry, roi_toolbox=panel._roi_toolbox, chromatic=panel._chromatic,
        status=panel._on_tool_status, session_carry=panel._session_carry,
    )
    panel._export_tab.info_changed.connect(panel._on_export_info_changed)


def build_top_bar(panel: ImagePanel, image_tools_content: QWidget, mask_content: QWidget, roi_content: QWidget) -> None:
    """Moved out of `ImagePanel._build_top_bar` (2026-11-15). On the panel it
    creates: `_cursor_overlay`, `_tool_info`, `_tool_ribbon`, `_top_bar`, `_top_drawer`
    reads: `_active_tool`, `_analysis_tab`, `_background_tab`, `_chromatic_tab`, `_cursor_readout`, `_export_tab`,
    `_initial_ribbon_category`, `_mask_tab`, `_plot`, `_view`, `_view_tab`, `ribbon_category_changed`
    calls its methods: `_refresh_tool_info`, `_refresh_top_bar_theme`, `_show_current_tab`
    """
    panel._top_bar = QWidget(panel)
    panel._top_bar.setObjectName("imageTopBar")
    # Style sheet while the bar is still empty (2026-11-11): set after the ribbon was added it re-styled every widget in it
    # (~150 ms at launch) for a rule that matches only the bar.
    panel._refresh_top_bar_theme()
    top_bar_layout = QHBoxLayout(panel._top_bar)
    top_bar_layout.setContentsMargins(4, 2, 6, 2)
    top_bar_layout.setSpacing(6)
    # The always-visible "General" group is gone (2026-10-23): the cursor toggle is an icon of the View tab's
    # "Image" group. The area-selection tools left the Mask / ROIs / View tabs on 2026-11-14: they are the
    # always-visible selection toolbar beside the image (`selection_toolbar.py`, built in `_build_toolbar_and_layout`).

    panel._tool_ribbon = ImageToolRibbon(
        [
            ("Image tools", image_tools_content),
            (MASK_TAB, mask_content),
            (ROI_TAB, roi_content),
            (CHROMATIC_TAB, panel._chromatic_tab),
            (BACKGROUND_TAB, panel._background_tab),
            (ANALYSIS_TAB, panel._analysis_tab),
            (EXPORT_TAB, panel._export_tab),
        ],
        panel,
    )
    if panel._initial_ribbon_category:
        # A saved "View" (or "Histogram", its name before 2026-10-06) tab no longer exists (2026-11-22): `set_category` ignores an unknown name,
        # so the first tab opens.
        panel._tool_ribbon.set_category(panel._initial_ribbon_category)  # before the connect: restoring is not a user change
    panel._tool_ribbon.category_changed.connect(panel.ribbon_category_changed)
    panel._tool_ribbon.category_changed.connect(lambda _label: panel._show_current_tab())  # a tab scrolled half out of view comes into view
    panel._tool_ribbon.category_changed.connect(lambda _label: panel._refresh_tool_info(panel._active_tool.active()))
    # Short tab label, full name in the tooltip; the tab is green while the correction is applied.
    panel._tool_ribbon.set_tab_tooltip(CHROMATIC_TAB, "Chromatic Corrections")
    # The ribbon rolls up on a double-click on the bar's empty space (`eventFilter`), leaving
    # a thin strip with a "..." handle, like the slider ribbon (2026-10-16). The "i" icon rolls
    # up with it (see below).
    panel._top_drawer = RibbonDrawer(
        panel._tool_ribbon, "Show the tool ribbon.", panel._top_bar, handle_alignment=Qt.AlignmentFlag.AlignTop
    )
    panel._top_bar.installEventFilter(panel)
    # The drawer (and so the ribbon) takes all the room the bar has (2026-10-29): with a plain stretch after it the ribbon got only its natural width, so a page whose
    # minimum width is a little more than its natural one scrolled with half the bar empty. The ribbon now scrolls only when the panel is really too narrow.
    top_bar_layout.addWidget(panel._top_drawer, 1)

    # Cursor-crosshair toggle (maintainer, 2026-09-29: copy the stable app's cursor overlay, hide / show the values). It shares `CursorOverlay` with the
    # Histogram plot; only `CursorReadout.value_at` differs (a pixel lookup here, a nearest-bin lookup there). A plain `QHBoxLayout` item: Qt re-lays-out the
    # row by itself when its size hint changes, so it needs no `on_changed` callback (since 2026-09-30).
    panel._cursor_overlay = CursorOverlay(
        scene_view=panel._view,
        plot_item=panel._plot,
        overlay_parent=panel._top_bar,
        value_at=panel._cursor_readout.value_at,
        theme=get_active_theme(),
        readout_sink=panel._cursor_readout.set_text,
    )
    # Styled like Select / Add ROI (maintainer, 2026-09-30: "make cursor and i icon same as other icons in the bar ... they should have same style"): the bar's
    # shared look overrides the class's own default sizing (tuned for the Histogram's settings-gear button). Since 2026-10-03 it is a fixed-size ribbon icon:
    # its live text goes to the canvas corner, so the button never changes width.
    style_general_icon_button(panel._cursor_overlay.icon_label)
    panel._view_tab.add_image_icon(panel._cursor_overlay.icon_label)  # first icon of the View tab's "Image" group (2026-10-23), the scale bar's icon next to it (2026-11-22)

    # The permanent "i" icon (2026-09-29; it replaced a text row that appeared only while a canvas tool was active): hovering it shows the active tool's controls
    # from `image_controls.py` (the single source of truth, so it cannot drift from what is wired), or the plain-image controls with no tool; always visible.
    # A live in-progress status (the rotate tool's angle readout) is not part of it, nobody hovers a corner icon mid-gesture: it goes to the status bar
    # (`tool_status_changed`, wired in `app_rewrite.py`). A real `QToolButton` (2026-09-30), the size and chrome of the bar's other icons
    # (`style_bar_icon_button`): it does nothing on click, but a `QLabel` had no hover affordance and looked out of place.
    panel._tool_info = QToolButton(panel._top_bar)
    style_bar_icon_button(panel._tool_info)
    panel._refresh_tool_info(None)
    top_bar_layout.addWidget(panel._tool_info)
    panel._top_drawer.expanded_changed.connect(panel._tool_info.setVisible)  # rolled up: only the handle is left


def build_side_rails(panel: ImagePanel) -> SideRails:
    """The row under the top bar (2026-11-22): the pyqtgraph view with the selection toolbar and the View ribbon beside it, each a rail that can be
    docked on either side of the image and rolled up (`side_rails.py`). On the panel it
    creates: nothing (the caller keeps the row as `side_rails`)
    reads: `_selection_tools`, `_view`, `_view_ribbon`, `_view_tab`, `view_controls_open_changed`
    calls its methods: none
    """
    rails = SideRails(panel._view, panel)
    toolbar = panel._selection_tools.toolbar
    rails.add_rail(SideRail("selection", "Selection toolbar", toolbar, rails, side=LEFT, on_side=toolbar.set_side))
    view = SideRail("view", "View ribbon", panel._view_ribbon, rails, side=RIGHT, on_side=panel._view_tab.set_side)
    rails.add_rail(view)
    view.expanded_changed.connect(panel.view_controls_open_changed)

    def margins() -> None:
        # No pyqtgraph margin between the selection toolbar and the image (maintainer, 2026-11-15), on whichever side it stands; 9 px at the other edge.
        selection_left = rails.side_of("selection") == LEFT
        panel._view.ci.setContentsMargins(0 if selection_left else 9, 9, 9 if selection_left else 0, 9)

    rails.arranged.connect(margins)
    margins()
    return rails


def _on_links_toggled(panel, shown: bool) -> None:
    """The ROIs tab's Links switch: the overlay draws (or stops drawing) its link lines, so the ROIs are drawn again."""
    panel._roi_overlay.set_links_visible(bool(shown))
    if panel._last_image_shape is not None:
        panel._draw_roi_overlay()
