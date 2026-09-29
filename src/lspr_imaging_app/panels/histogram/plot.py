"""Histogram plot widget - the pyqtgraph-facing half of the Histogram panel
(sketch §10 "histogram/panel.py + plot.py", following Spectra/Sensorgram's
own panel.py + plot.py split). Owns no computation - every curve is handed
already-binned `(edges, values)` pairs; see `panel.py`/`compute.py` for
where those come from (`SpectraPlot`'s "reads an already-computed value,
never computes one itself" convention, applied here too).

**Native pyqtgraph right-click menu is left enabled - a deliberate, named
exception** (maintainer's explicit choice, 2026-09-29). Every other plot in
this app disables it (`setMenuEnabled(False)`, an app-wide convention in the
stable app, carried forward here) because none of them offer a replacement
worth keeping visible. This one does: pyqtgraph's built-in Export
(PNG/SVG/CSV/...) and the View-All/mouse-mode controls are exactly what the
maintainer asked for, and building a themed substitute for them would be
pure duplication for no benefit.

**Floating `QWidget` overlays, all parented to the plot viewport**
(maintainer's spec, 2026-09-29 - "similar to cropping ones", referring to
`panels/image/crop_size_controls.py`'s technique): a gear icon and a cursor-
crosshair toggle, both fixed to the top-right corner (the gear opens
`HistogramPlotSettingsDialog`, built by `panel.py` since it needs panel-
level state), and a "[min, max]" Highlight-range readout centered under the
region near the x-axis, live during a drag.

**X is locked to `[0, 65535]` via `ViewBox.setLimits`** - the pyqtgraph API
built for exactly this ("Set limits that constrain the possible view
ranges"), enforced at the one choke point every range-changing path
(button, right-click menu's "View All"/"Auto", wheel, drag, any future
caller) already funnels through, rather than catching and reversing each
door individually. `hideButtons()` additionally removes the corner "A"
button outright (every other plot in the app already does this, since an
"A" button has no sensible behavior once X can never move and Y already
auto-ranges on its own); `setMouseEnabled(x=False, y=True)` keeps
interactive drag/wheel from even attempting to move X, for a clean feel
rather than a rubber-band snap-back, while leaving Y fully interactive.
Native right-click menu is kept enabled throughout (the maintainer's
explicit choice, right below).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QResizeEvent
from PyQt6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, tint_tabler_icon, transparent_icon_button_stylesheet

from ..cursor_overlay import CursorOverlay
from . import compute
from .highlight_range_controls import HighlightRangeReadout

# Sample/Reference match `ImagePanel`'s own overlay colors exactly (image/
# panel.py's `_DEFAULT_SAMPLE_COLOR`/`_DEFAULT_REFERENCE_COLOR`) so the same
# population reads as the same color in both places.
_SAMPLE_COLOR = "#f59e0b"
_REFERENCE_COLOR = "#38bdf8"
_IGNORE_MASK_COLOR = "#ef4444"
_HIGHLIGHT_BRUSH = (56, 189, 248, 35)
_HIGHLIGHT_LINE = (56, 189, 248, 160)
_CORNER_MARGIN_PX = 6
DEFAULT_LINE_WIDTH = 1.5


class HistogramPlot(QWidget):
    """Draws the histogram curves, the draggable Highlight region, and the
    floating overlays (settings gear, cursor toggle, range readout).
    Percent-vs-counts and linear-vs-log are independent controls (unlike
    the stable app, where log mode silently switched the Y-axis to counts
    too)."""

    # Emitted only when the *user* finishes dragging the region (mirrors
    # pyqtgraph's own `sigRegionChangeFinished` - not fired for a
    # programmatic `set_highlight_range` call, which would otherwise create
    # a feedback loop with whatever set it - see `set_highlight_range`).
    highlight_dragged = pyqtSignal(float, float)
    # Raw, not-yet-crossed-checked edits from the floating readout fields -
    # `HistogramPanel` resolves them against `HighlightRangeModule`'s
    # current state, same as `HighlightRangeReadout`'s own docstring notes.
    min_edited = pyqtSignal(float)
    max_edited = pyqtSignal(float)
    settings_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_y = False
        self._line_width = DEFAULT_LINE_WIDTH
        self._curve_colors: dict[pg.PlotDataItem, str] = {}

        self._plot_widget = pg.PlotWidget(parent=self)
        self._plot_widget.setMinimumHeight(100)
        self._plot_item = self._plot_widget.getPlotItem()
        self._plot_item.showGrid(x=False, y=True, alpha=0.15)
        self._plot_item.setLabel("bottom", "Intensity (DN)")
        self._plot_item.setXRange(compute.DEFAULT_INTENSITY_MIN, compute.DEFAULT_INTENSITY_MAX, padding=0.0)
        self._plot_item.hideButtons()
        self._legend = self._plot_item.addLegend(offset=(8, 8))

        view_box = self._plot_item.getViewBox()
        # `setLimits` is pyqtgraph's own mechanism for exactly this - it
        # clips every range-changing path (button, right-click menu's "View
        # All"/"Auto", wheel, drag, any future caller) at one shared choke
        # point (`ViewBox.updateViewRange`), verified directly against the
        # installed pyqtgraph source rather than assumed. `minXRange ==
        # maxXRange == the full span` additionally makes X impossible to
        # zoom, not just impossible to pan past its bounds. `setMouseEnabled
        # (x=False)` is not load-bearing for correctness here (`setLimits`
        # alone already blocks an X drag past its bounds) but keeps the
        # interaction feel clean - a drag on X does nothing at all, rather
        # than visibly moving and snapping back.
        view_box.setLimits(
            xMin=compute.DEFAULT_INTENSITY_MIN,
            xMax=compute.DEFAULT_INTENSITY_MAX,
            minXRange=compute.DEFAULT_INTENSITY_MAX - compute.DEFAULT_INTENSITY_MIN,
            maxXRange=compute.DEFAULT_INTENSITY_MAX - compute.DEFAULT_INTENSITY_MIN,
        )
        view_box.setMouseEnabled(x=False, y=True)

        self._all_pixels_curve = self._add_curve(get_active_theme().text_primary, "All pixels")
        self._sample_curve = self._add_curve(_SAMPLE_COLOR, "Sample ROI")
        self._reference_curve = self._add_curve(_REFERENCE_COLOR, "Reference ROI")
        self._ignore_mask_curve = self._add_curve(_IGNORE_MASK_COLOR, "Ignore mask")

        self._region = pg.LinearRegionItem(
            values=(0, 1), brush=pg.mkBrush(*_HIGHLIGHT_BRUSH), pen=pg.mkPen(_HIGHLIGHT_LINE, width=1)
        )
        self._region.setZValue(10)
        self._region.hide()
        self._region.sigRegionChanged.connect(self._on_region_changed_live)
        self._region.sigRegionChangeFinished.connect(self._on_region_drag_finished)
        self._plot_item.addItem(self._region)

        viewport = self._plot_widget.viewport()

        theme = get_active_theme()

        self._settings_button = QToolButton(viewport)
        self._settings_button.setAutoRaise(True)
        self._settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._settings_button.setIconSize(QSize(theme.compact_icon_inner, theme.compact_icon_inner))
        self._settings_button.setFixedSize(theme.compact_icon_outer, theme.compact_icon_outer)
        self._settings_button.setToolTip("Histogram plot settings")
        self._settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._settings_button.clicked.connect(self.settings_requested)

        self._range_readout = HighlightRangeReadout(
            viewport,
            theme,
            value_min=compute.DEFAULT_INTENSITY_MIN,
            value_max=compute.DEFAULT_INTENSITY_MAX,
        )
        self._range_readout.setVisible(False)
        self._range_readout.min_edited.connect(self.min_edited)
        self._range_readout.max_edited.connect(self.max_edited)

        self._cursor_overlay = CursorOverlay(
            scene_view=self._plot_widget,
            plot_item=self._plot_item,
            overlay_parent=viewport,
            value_at=self._cursor_value_at,
            theme=theme,
            on_changed=self._reposition_cursor_overlay,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot_widget)

        self.refresh_theme()
        self._reposition_settings_button()
        self._reposition_cursor_overlay()

    def _add_curve(self, color_hex: str, name: str) -> pg.PlotDataItem:
        curve = self._plot_item.plot(
            name=name, pen=pg.mkPen(QColor(color_hex), width=self._line_width), stepMode="center"
        )
        self._curve_colors[curve] = color_hex
        return curve

    # -- theming --------------------------------------------------------------

    def refresh_theme(self) -> None:
        """Pyqtgraph draws its own canvas and ignores Qt stylesheets/palette
        (same caveat `ImagePanel.refresh_theme` documents) - called once at
        construction and again on every live theme switch."""
        theme = get_active_theme()
        self._plot_widget.setBackground(theme.toolbar_bg)
        self._curve_colors[self._all_pixels_curve] = theme.text_primary
        icon = tint_tabler_icon(load_tabler_icon("settings"), QColor(theme.text_muted))
        self._settings_button.setIcon(icon)
        self._range_readout.refresh_theme(theme)
        self._cursor_overlay.refresh_theme(theme)
        self._apply_curve_pens()

    # -- data -------------------------------------------------------------------

    def set_all_pixels(self, edges: np.ndarray, values: np.ndarray) -> None:
        self._all_pixels_curve.setData(edges, values)

    def set_sample(self, edges: np.ndarray, values: np.ndarray) -> None:
        self._sample_curve.setData(edges, values)

    def set_reference(self, edges: np.ndarray, values: np.ndarray) -> None:
        self._reference_curve.setData(edges, values)

    def set_ignore_mask(self, edges: np.ndarray, values: np.ndarray) -> None:
        self._ignore_mask_curve.setData(edges, values)

    def clear(self) -> None:
        for curve in (self._all_pixels_curve, self._sample_curve, self._reference_curve, self._ignore_mask_curve):
            curve.clear()
        self.set_highlight_range(None)

    # -- axis mode --------------------------------------------------------------

    def set_y_label(self, text: str) -> None:
        self._plot_item.setLabel("left", text)

    def set_log_y(self, enabled: bool) -> None:
        """Pyqtgraph's own log-mode transforms whatever data is already on
        the curves for display - callers keep passing linear counts/percent
        to `set_all_pixels`/etc. regardless of this flag, matching pyqtgraph's
        own "log mode is a display transform, not a data transform"
        contract. Simpler than the stable app's manual `log10`/floor-clamp
        approach (`plot_manager.py`'s `apply_histogram_log_mode`), which
        existed only because that app coupled log mode to a Y-axis *unit*
        change (counts) that this rewrite deliberately makes independent."""
        if enabled == self._log_y:
            return
        self._log_y = enabled
        self._plot_item.setLogMode(x=False, y=enabled)

    def set_line_width(self, width: float) -> None:
        """One width for every curve - the maintainer's own framing was a
        starting point ("line width, and other things which will come
        later"), not a spec for per-curve styling."""
        if width == self._line_width:
            return
        self._line_width = width
        self._apply_curve_pens()

    def _apply_curve_pens(self) -> None:
        for curve, color_hex in self._curve_colors.items():
            curve.setPen(pg.mkPen(QColor(color_hex), width=self._line_width))

    # -- highlight range ----------------------------------------------------

    def set_highlight_range(self, range_: tuple[float, float] | None) -> None:
        """Programmatic set (from `HighlightRangeModule.range_changed`, or
        the wand) - never emits `highlight_dragged`, which would otherwise
        loop straight back into whatever just called this."""
        if range_ is None:
            self._region.hide()
            self._range_readout.setVisible(False)
            return
        blocked = self._region.blockSignals(True)
        try:
            self._region.setRegion(range_)
        finally:
            self._region.blockSignals(blocked)
        self._region.show()
        self._range_readout.set_range(*range_)
        self._range_readout.setVisible(True)
        self._reposition_range_readout()

    def _on_region_changed_live(self) -> None:
        """`sigRegionChanged` fires continuously while dragging - this only
        keeps the readout's displayed numbers in step with the drag; the
        shared `HighlightRangeModule` is not touched until the drag finishes
        (`_on_region_drag_finished`), so a future Mask/ROI-detection
        subscriber sees one final value, not a flood of in-progress ones."""
        lo, hi = self._region.getRegion()
        self._range_readout.set_range(float(lo), float(hi))
        self._reposition_range_readout(lo, hi)  # already have lo/hi - skip the reposition's own re-fetch

    def _on_region_drag_finished(self) -> None:
        lo, hi = self._region.getRegion()
        self.highlight_dragged.emit(float(lo), float(hi))

    # -- cursor readout -------------------------------------------------------

    def _cursor_value_at(self, view_x: float, view_y: float) -> tuple[float, float, str] | None:
        """Snaps to the nearest bin on the "All pixels" curve - the primary
        reference series, same role `window.spectrum_curve`/`sensorgram_
        curve` play in stable's single-curve snap. `edges` is one longer
        than `values` (`stepMode="center"`'s own contract - `set_all_pixels`
        passes them straight through), so a view-space x maps to bin index
        via `searchsorted` against the edges, then back to that bin's own
        center for where the crosshair actually lands."""
        edges, values = self._all_pixels_curve.getData()
        if edges is None or len(edges) < 2 or values is None or len(values) == 0:
            return None
        bin_index = int(np.clip(np.searchsorted(edges, view_x, side="right") - 1, 0, len(values) - 1))
        x = float((edges[bin_index] + edges[bin_index + 1]) / 2.0)
        y = float(values[bin_index])
        return x, y, f"{x:.0f} DN, {y:.3g}"

    # -- floating overlay positioning -----------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._reposition_settings_button()
        self._reposition_cursor_overlay()
        self._reposition_range_readout()

    def _reposition_settings_button(self) -> None:
        """Fixed to the top-right corner of the viewport - unlike the range
        readout, this does not track plot content, so a resize is the only
        thing that ever needs to move it."""
        viewport = self._plot_widget.viewport()
        x = viewport.width() - self._settings_button.width() - _CORNER_MARGIN_PX
        self._settings_button.move(x, _CORNER_MARGIN_PX)
        self._settings_button.raise_()

    def _reposition_cursor_overlay(self) -> None:
        """Immediately left of the settings gear, same top-right corner row
        stable groups its own cursor+settings icons into (`plot_overlay_
        controller.py`'s "action container"). Also called on every toggle/
        mouse-move (`CursorOverlay`'s `on_changed`), since the label's width
        changes between the small icon and the (usually wider) live text."""
        label = self._cursor_overlay.icon_label
        label.adjustSize()
        x = self._settings_button.x() - label.width() - _CORNER_MARGIN_PX
        y = self._settings_button.y() + (self._settings_button.height() - label.height()) // 2
        label.move(x, y)
        label.raise_()

    def _reposition_range_readout(self, lo: float | None = None, hi: float | None = None) -> None:
        """Centered under the region's own midpoint, just above the bottom
        axis - same `ViewBox`-scene-to-viewport mapping technique
        `ImagePanel._reposition_crop_controls` and the stable app's
        `plot_corner_overlay.reposition_overlay` both use. `lo`/`hi` let a
        caller that already has the region's current bounds (the live-drag
        path) skip re-querying `self._region.getRegion()` for the same
        values it was just given; callers without them (resize, a
        programmatic `set_highlight_range`) fall back to asking the region
        directly. No `adjustSize()` call - `HighlightRangeReadout` is fixed-
        width once built (`highlight_range_controls.py`), so its size
        cannot have changed since construction; re-deriving it on every
        reposition, including every step of a drag, would be pure waste.

        Nudged down by half the readout's own font height (maintainer's
        spec, 2026-09-30) past where `_CORNER_MARGIN_PX` alone would put it:
        `bottom_y` is the ViewBox's own bottom edge, but the Y=0 gridline
        - where the curve actually touches down - usually sits a little
        *above* that edge (auto-range typically pads a few percent below
        zero), so the margin-only position could still overlap the curve
        right where it meets the baseline. Sized off the font rather than a
        fixed pixel count so it scales if the readout's font ever changes."""
        if not self._range_readout.isVisible():
            return
        view_box = self._plot_item.getViewBox()
        if view_box is None:
            return
        if lo is None or hi is None:
            lo, hi = self._region.getRegion()
        mid_x_scene = view_box.mapViewToScene(QPointF((lo + hi) / 2.0, 0.0)).x()
        mid_x = self._plot_widget.mapFromScene(QPointF(mid_x_scene, 0.0)).x()
        scene_rect = view_box.sceneBoundingRect()
        bottom_y = self._plot_widget.mapFromScene(scene_rect.bottomLeft()).y()
        half_font_px = self._range_readout.fontMetrics().height() / 2.0
        x = int(mid_x - self._range_readout.width() / 2.0)
        y = int(bottom_y - self._range_readout.height() - _CORNER_MARGIN_PX + half_font_px)
        self._range_readout.move(x, y)
        self._range_readout.raise_()
