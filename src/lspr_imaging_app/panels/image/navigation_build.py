"""The Image panel's navigation bar, built (2026-11-15; moved out of `ImagePanel._build_navigation_bar`, 202 lines).

The Cube and wavelength sliders with their titles, the ROI row and the two Mask rows under them, the slider marks, the slideshow (Play) controller and its
connections, the slider ribbon with its drawer, and the bar that holds them.

**What this is, honestly:** the construction code moved as it was (`self` is `panel`): a function that reaches into the panel's private fields, listed in
its docstring. It is a first step; see `docs/image_panel_split_investigation_2026-11-15.md` for the next one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import time
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from lspr_ui import get_active_theme
from .data_axis_slider import DataAxisSlider
from .paint_census import PaintCensus
from .play_repaint import PlayRepaint
from .slider_marks import SliderMarks
from .render import RenderResult
from .mark_colors import cube_color, wavelength_color
from .mask_rows import MaskRows
from .timeline_strip import TimelineStrip
from .slider_colors import SliderColors
from .playback_stats import PlaybackStats
from .slider_ribbon import SliderRibbon, SliderRibbonDrawer
from .slideshow import (
    MODE_ALL,
    MODE_ROI_STATES,
    MODE_ROW_MARKS,
    SlideshowController,
)
from .panel_constants import (
    _REDRAW_COALESCE_MS,
    _slider_axis_title_style,
)

if TYPE_CHECKING:
    from .panel import ImagePanel


def build_navigation_bar(panel: ImagePanel) -> None:
    """Moved out of `ImagePanel._build_navigation_bar` (2026-11-15). On the panel it
    creates: `_controls_bar`, `_cube_slider`, `_cube_title`, `_held_result`, `_mask_rows`, `_paint_census`, `_play_repaint`,
    `_playback_stats`, `_ribbon_drawer`, `_roi_row`, `_roi_strip`, `_slider_colors`, `_slider_marks`, `_slider_ribbon`, `_slideshow`,
    `_wavelength_slider`
    reads: `_cube_display`, `_dataset`, `_frame_roles`, `_mask`, `_mask_pointers`, `_mask_tab`, `_roi_revision`, `_roi_toolbox`,
    `_selection`, `_view`
    calls its methods: `_connect_frame_tools`, `_refresh_controls_bar_theme`, `_show_slider_menu`
    """
    # Cube and Wavelength navigation - ported from the stable app's
    # tick/axis-style redesign (docs/image_area_slider_redesign.md):
    # each axis gets its own full-width row (title -> slider) rather than
    # splitting one row, so each slider gets the full available width and
    # the two axes read as parallel strips. The number fields that used to
    # sit right of each slider (a typed cube / wavelength, with a
    # wavelength autocomplete) were removed 2026-10-18 (maintainer request):
    # the current cube, wavelength and time are in the dock title bar.
    # The Cube/Time toggle title-button and the
    # "exclude this image" icon button are *not* ported - see this
    # panel's module docstring "Not built here, deliberately" note for
    # why (no elapsed-time mapping / no exclusions subsystem exists on
    # this branch yet).
    panel._cube_slider = DataAxisSlider(Qt.Orientation.Horizontal, panel)
    panel._cube_slider.setEnabled(False)
    panel._cube_slider.set_accent_color(cube_color())
    panel._cube_slider.valueChanged.connect(panel._on_cube_slider_changed)

    panel._cube_title = cube_title = QLabel(panel._cube_display.axis_title(), panel)
    cube_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
    cube_title.setToolTip("Double-click to switch between cube number and start time.")
    cube_title.installEventFilter(panel)
    cube_row =QHBoxLayout()
    cube_row.setContentsMargins(0, 0, 0, 0)
    cube_row.setSpacing(5)
    cube_row.addWidget(cube_title)
    cube_row.addWidget(panel._cube_slider, 1)

    panel._wavelength_slider = DataAxisSlider(Qt.Orientation.Horizontal, panel)
    panel._wavelength_slider.setEnabled(False)
    panel._wavelength_slider.set_accent_color(wavelength_color())
    panel._wavelength_slider.valueChanged.connect(panel._on_wavelength_slider_changed)

    wavelength_title = QLabel("λ (nm)", panel)
    wavelength_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
    wavelength_title.setToolTip("Wavelength (nm)")
    wavelength_row = QHBoxLayout()
    wavelength_row.setContentsMargins(0, 0, 0, 0)
    wavelength_row.setSpacing(5)
    wavelength_row.addWidget(wavelength_title)
    wavelength_row.addWidget(panel._wavelength_slider, 1)

    # The ROI row (2026-10-16): a thin strip under the Cube slider, on the Cube axis, with a mark wherever the
    # ROI layout changes (`timeline_strip.py`). Its title takes the width of the other rows' titles (below),
    # so the strip's ticks line up with the Cube slider's.
    panel._roi_strip = TimelineStrip(panel)
    panel._roi_strip.index_requested.connect(panel._cube_slider.setValue)
    # One zoom window for the Cube slider and the ROI row (their marks must stay under their ticks); λ has its own.
    panel._roi_strip.set_window(panel._cube_slider.axis_window())
    roi_title = QLabel("ROIs", panel)
    roi_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
    roi_title.setToolTip(
        "ROI layout. A mark wherever the ROIs change (position or size): > holds from that cube on, "
        "a line is that cube only."
    )
    roi_row = QHBoxLayout()
    roi_row.setContentsMargins(0, 0, 0, 0)
    roi_row.setSpacing(5)
    roi_row.addWidget(roi_title)
    roi_row.addWidget(panel._roi_strip, 1)
    panel._roi_row = QWidget(panel)
    panel._roi_row.setLayout(roi_row)

    # The two Mask rows (2026-10-16): one under the Cube slider, one under the wavelength slider (`mask_rows.py`).
    # Each shares its slider's zoom window; titles are sized with the other rows' below.
    panel._mask_rows = MaskRows(
        panel, panel._mask, panel._dataset, panel._selection, panel._cube_display.label_for,
        cube_window=panel._cube_slider.axis_window(),
        wavelength_window=panel._wavelength_slider.axis_window(),
        title_style=_slider_axis_title_style(get_active_theme().text_muted),
        status=panel._on_tool_status,
        events=panel._mask_pointers,
        auto_mask=lambda records: panel._mask_tab.auto_controls.start(records),
    )
    panel._mask_rows.cube_strip.index_requested.connect(panel._cube_slider.setValue)
    panel._mask_rows.wavelength_strip.index_requested.connect(panel._wavelength_slider.setValue)

    # All the rows' titles share one width (2026-09-30, maintainer request) -
    # without this, "Cube" and "λ (nm)" size to their own text, so the two
    # sliders started at slightly different x positions.
    # Wide enough for either unit ("Cube #" / "Cube time"), so toggling never shifts the sliders.
    title_metrics = QFontMetrics(cube_title.font())
    title_width = max(
        cube_title.sizeHint().width(),
        wavelength_title.sizeHint().width(),
        *(title_metrics.horizontalAdvance(text) + 4 for text in ("Cube (#)", "Cube (t)")),
    )
    cube_title.setFixedWidth(title_width)
    wavelength_title.setFixedWidth(title_width)
    roi_title.setFixedWidth(title_width)
    for title in panel._mask_rows.titles:
        title.setFixedWidth(title_width)

    for slider in (panel._cube_slider, panel._wavelength_slider):
        slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        slider.customContextMenuRequested.connect(lambda pos, s=slider: panel._show_slider_menu(s, pos))
    panel._cube_display.mode_changed.connect(panel._on_cube_display_changed)

    navigation = QVBoxLayout()
    navigation.setContentsMargins(0, 0, 0, 0)
    navigation.setSpacing(4)
    navigation.addLayout(cube_row)
    navigation.addWidget(panel._roi_row)
    navigation.addWidget(panel._mask_rows.cube_row)
    navigation.addLayout(wavelength_row)
    navigation.addWidget(panel._mask_rows.wavelength_row)

    # No status row here anymore (2026-09-30, maintainer request) - the
    # "Cube X, wl nm" text it used to show now lives in the dock title
    # bar (`frame_status_changed`, wired to `PanelContainer.set_subtitle`
    # in app_rewrite.py); the trailing "- WxH px" resolution text is not
    # shown anywhere anymore.
    #
    # Left/right margin only, no top/bottom (2026-09-30, maintainer
    # request, "small stylish" follow-up - scoped to just this nav bar
    # after an earlier pass put it on the whole panel by mistake): a
    # bare 10px breathing room so the "Cube"/"λ (nm)" titles don't start
    # flush against the dock's left edge. Top/bottom stay flush - nothing
    # else in this bar needs the room. No right margin since 2026-10-18
    # (maintainer: the gap between the sliders and the panel edge was too
    # big): a slider already keeps 14 px inside itself (`SIDE_INSET`, room
    # for half of an end tick label), and the dock's card gap adds 3 more.
    controls = QVBoxLayout()
    controls.setContentsMargins(10, 0, 0, 0)
    controls.setSpacing(4)
    # The slider ribbon (2026-10-13): frame icon controls, Play, slideshow settings, slider colours.
    panel._slider_colors = SliderColors()
    panel._slider_ribbon = SliderRibbon(panel)
    # What the two sliders mark besides the handle: reference, dark, ignored, the ROI row, the colours, the tick labels (slider_marks.py).
    panel._slider_marks = SliderMarks(
        panel._dataset,
        panel._selection,
        panel._roi_toolbox,
        panel._frame_roles,
        panel._cube_display,
        cube_slider=panel._cube_slider,
        wavelength_slider=panel._wavelength_slider,
        roi_row=panel._roi_row,
        roi_strip=panel._roi_strip,
        slider_colors=panel._slider_colors,
        slider_ribbon=panel._slider_ribbon,
        menu_parent=panel,
        current_cube=panel._current_cube,
        current_wavelength=panel._current_wavelength,
        resolve_reference_frame=panel._resolve_reference_frame,
        roi_revision=lambda: panel._roi_revision,
        status=panel._on_tool_status,
        coalesce_ms=_REDRAW_COALESCE_MS,
    )
    panel._cube_slider.axis_window().changed.connect(panel._slider_marks.refresh_cube_labels)
    panel._wavelength_slider.axis_window().changed.connect(panel._slider_marks.refresh_wavelength_labels)
    panel._slider_ribbon.slider_colors.set_colors(panel._slider_colors)
    panel._slider_ribbon.slider_colors.color_changed.connect(panel._on_slider_color_changed)
    panel._slideshow = SlideshowController(
        panel._dataset, panel._selection, panel._frame_roles, panel, state_cubes=panel._state_cubes_for_mode
    )
    panel._slider_ribbon.set_roi_row(True, False)  # the defaults; `restore_ui_state` puts back what was saved
    panel._slider_ribbon.roi_row_toggled.connect(panel._slider_marks.on_roi_row_toggled)
    panel._slider_ribbon.roi_selected_only_toggled.connect(panel._slider_marks.on_roi_selected_only_toggled)
    panel._slider_ribbon.play_toggled.connect(panel._slideshow.toggle)
    # The wheel over the ROI row always hops between its marks, whatever Play steps through.
    panel._roi_strip.step_requested.connect(lambda direction: panel._slideshow.step(direction, MODE_ROI_STATES))
    panel._roi_strip.mark_menu_requested.connect(panel._slider_marks.on_mark_menu)
    # The wheel over the Mask row under the Cube slider hops between ALL its marks, masks and standing scan pointers.
    panel._mask_rows.cube_strip.step_requested.connect(lambda direction: panel._slideshow.step(direction, MODE_ROW_MARKS))
    # The wheel turned on past the last / first wavelength goes on into the next / previous cube, as Play does in its
    # "all wavelengths" order (first wavelength of the next cube / last of the previous), whatever mode Play is set to.
    panel._wavelength_slider.edge_step_requested.connect(lambda direction: panel._slideshow.step(direction, MODE_ALL))
    panel._slider_ribbon.set_mask_rows(True)  # the default; `restore_ui_state` puts back what was saved
    panel._slider_ribbon.mask_rows_toggled.connect(panel._mask_rows.set_shown)
    panel._slider_ribbon.settings_changed.connect(panel._slideshow.set_settings)
    panel._slider_ribbon.settings_changed.connect(panel._apply_play_outline)
    panel._slider_ribbon.settings_changed.connect(panel._on_slideshow_speed_for_stats)  # after set_settings: new interval
    panel._slideshow.playing_changed.connect(panel._slider_ribbon.set_playing)
    # A Play frame that arrives just before the next step waits for it: image and step reach the screen in one repaint
    # (`SlideshowController.hold_frame_s`).
    panel._held_result: RenderResult | None = None
    panel._slideshow.set_before_step(panel._show_held_frame)
    # Where the time goes while Play runs (status bar + one log line at the end).
    panel._playback_stats = PlaybackStats()
    panel._paint_census = PaintCensus()  # which widgets repaint during Play (`playback.paints` in the log)
    panel._play_repaint = PlayRepaint(panel._view, panel)  # one repaint of the canvas per frame while Play runs (`play_repaint.py`)
    panel._slideshow.playing_changed.connect(panel._on_playback_changed)
    panel._slideshow.step_made.connect(lambda handlers_s: panel._playback_stats.note_step(time.perf_counter(), handlers_s))
    panel._slideshow.step_made.connect(panel._flush_redraw_for_step)
    panel._connect_frame_tools(panel._slider_ribbon.frame_tools)
    panel._ribbon_drawer = SliderRibbonDrawer(panel._slider_ribbon, panel)
    controls.addWidget(panel._ribbon_drawer)
    controls.addLayout(navigation)

    # A real QWidget, not a bare layout - QSS `border` only applies to
    # widgets, and this bar needs one (see `_refresh_controls_bar_
    # theme`) for the same reason the top bar does: its background
    # and the canvas's are otherwise visually identical, so the seam
    # where this bar ends and the canvas begins needs a boundary drawn
    # explicitly, not left to two adjacent flat colors that happen to
    # differ.
    panel._controls_bar = QWidget(panel)
    panel._controls_bar.setObjectName("imageNavigationBar")
    panel._refresh_controls_bar_theme()  # before its children arrive: same result, no re-styling of them (see the top bar)
    panel._controls_bar.setLayout(controls)
