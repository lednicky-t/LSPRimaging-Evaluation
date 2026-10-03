"""Five bespoke pictograms for the "Edit" tool picker's menu
(`mask_edit_tool_picker.py`) - one per `MaskEditTool` member, each meant to
show *what the tool does* at a glance, not just label it.

**Hand-painted with `QPainter` onto a `QPixmap`, the same technique
`lspr_ui/icons.py`'s own bespoke composite icons already use**
(`flow_icon`/`trash_icon`/`residual_icon` - multi-color, domain-specific
glyphs no vendored single-color Tabler SVG could express), not routed
through `lspr_ui.load_tabler_icon()`. That loader recolors a whole vendored
SVG with one `currentColor` substitution - fine for every other icon in this
ribbon (a toggle that is either "on" or "off", one color), but these need
*multiple* colors in one glyph (e.g. the Histogram-selection icon's middle
bars in a different color from its outer ones, to show what "a selected
range" looks like) - the thing the loader is deliberately not built for.
Living in this app rather than in shared `lspr_ui` because they are specific
to this one picker's domain (mask-editing tool kinds), not reusable
pictograms another app would want - same reasoning the stable app's own
`main_window_icons.py` already applied to its bespoke
`_histogram_highlight_icon`.

Static (not theme-dependent) for four of the five - each glyph's colors are
fixed and *mean* something (muted bars vs. a colored "this is what gets
affected" region), so varying them by active/inactive would muddy the one
thing they exist to communicate. Used identically in the picker's menu
(every option, always) and on the picker button's own face (whichever
option is currently picked).

**`histogram_selection_icon` is the one exception** - it doubles as the
"Histogram" ribbon tab's own show/hide toggle icon
(`histogram_highlight_overlay_controls.py`, 2026-10-02 maintainer request:
"change to this icon the icon in histogram widget, on is colored, off is
all white/gray"), so it takes an `active` flag: the picker's menu/button
usage always passes the default (`True`, the colored "selected range"
look); the overlay toggle passes its own checked state, collapsing to a
single flat muted tone when off - the same "recolor by checked state"
convention `mask_overlay_controls.py`'s toggle icon already uses, just
applied to a multi-color glyph instead of a single-color one.

Draws at `_DESIGN_SIZE` (24x24, the usual Tabler viewBox convention so these
sit at a familiar scale next to vendored icons) scaled onto a
`_RENDER_SIZE`-px pixmap (2x, crisper than a native bitmap - matching this
ribbon's own `_RENDER_SIZE = _ICON_SIZE * 2` convention), then wrapped in a
`QIcon` sized down by Qt's own icon-size handling at display time.
`functools.lru_cache` avoids re-painting the same icon on every theme
refresh/menu rebuild, matching `load_tabler_icon`'s own caching.
"""

from __future__ import annotations

import functools

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from lspr_ui import load_tabler_icon

_DESIGN_SIZE = 24
_RENDER_SIZE = 48  # 2x _DESIGN_SIZE

_MUTED = "#8b95a3"  # GuiTheme.text_dim - fixed literal, see module docstring ("static, not theme-dependent")
_SELECTED = "#22c55e"  # GuiTheme.accent_green - "this is the region a tool would add/affect", matches
# MaskOverlayControls/MaskHighlightActions's own "#22c55e == add/visible" convention elsewhere in this ribbon.
_THRESHOLD_LINE = "#ef4444"  # GuiTheme.accent_red
_LOCAL_CONTRAST_CENTER = "#38bdf8"  # GuiTheme.accent_blue
_MORPHOLOGY_SHAPE = "#b4bdc9"  # GuiTheme.text_muted
_MORPHOLOGY_CLOSE = "#38bdf8"  # GuiTheme.accent_blue - maintainer's own spec: "performed closing by blue color"


def _new_painter(pixmap: QPixmap) -> QPainter:
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_RENDER_SIZE / _DESIGN_SIZE, _RENDER_SIZE / _DESIGN_SIZE)
    return painter


# Five bars, the shared base for the Histogram-selection and Threshold icons
# below (baseline at y=20, matching the vendored `chart-histogram.svg`'s own
# baseline) - heights chosen so the middle three read as one contiguous
# "selected range" group distinct from the two outer bars, in both icons.
_BAR_X = (3.0, 7.0, 11.0, 15.0, 19.0)
_BAR_HEIGHTS = (6.0, 11.0, 15.0, 11.0, 6.0)
_BAR_WIDTH = 3.0
_BASELINE_Y = 20.0


def _draw_baseline(painter: QPainter, color: str) -> None:
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(2, int(_BASELINE_Y), 22, int(_BASELINE_Y))


@functools.lru_cache(maxsize=None)
def histogram_selection_icon(active: bool = True) -> QIcon:
    """Chart-histogram's own 5-bar shape with the top curve removed (per
    spec: "removing the top smooth curve... adding one more bar"). When
    `active` (the picker's own always-on usage, or the overlay toggle's
    "visible" state), the middle three bars are colored to show what a
    selected *range* looks like - the actual thing this tool creates a mask
    from (the Histogram panel's highlighted intensity range). When not
    `active` (the overlay toggle's "hidden" state), every bar collapses to
    the same flat muted tone - "all white/gray", no distinction - matching
    the module docstring's note on why this one icon takes a parameter."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    painter = _new_painter(pixmap)
    _draw_baseline(painter, _MUTED)
    painter.setPen(Qt.PenStyle.NoPen)
    for index, (x, height) in enumerate(zip(_BAR_X, _BAR_HEIGHTS)):
        color = _SELECTED if (active and index in (1, 2, 3)) else _MUTED
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(x, _BASELINE_Y - height, _BAR_WIDTH, height), 0.8, 0.8)
    painter.end()
    return QIcon(pixmap)


@functools.lru_cache(maxsize=None)
def threshold_icon() -> QIcon:
    """Same 5-bar base as `histogram_selection_icon`, but the "selection" is
    a threshold *line* rather than a range: everything above the red
    horizontal line (per spec: "a red horizontal line on y axis... everything
    over mark different color") is colored, everything below stays muted -
    showing a value threshold rather than a bounded range."""
    threshold_y = 11.0
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    painter = _new_painter(pixmap)
    _draw_baseline(painter, _MUTED)
    painter.setPen(Qt.PenStyle.NoPen)
    for x, height in zip(_BAR_X, _BAR_HEIGHTS):
        top_y = _BASELINE_Y - height
        painter.setBrush(QColor(_MUTED))
        painter.drawRoundedRect(QRectF(x, top_y, _BAR_WIDTH, height), 0.8, 0.8)
        if top_y < threshold_y:
            painter.setBrush(QColor(_SELECTED))
            painter.drawRect(QRectF(x, top_y, _BAR_WIDTH, threshold_y - top_y))
    pen = QPen(QColor(_THRESHOLD_LINE), 1.6)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.drawLine(2, int(threshold_y), 22, int(threshold_y))
    painter.end()
    return QIcon(pixmap)


@functools.lru_cache(maxsize=None)
def local_contrast_icon() -> QIcon:
    """A 3x3 patch of cells (a local neighborhood) with the center cell
    colored - "this pixel, compared against its own local surroundings" is
    exactly what `create_local_contrast_mask`/`create_relative_contrast_
    mask` compute (a Gaussian-blurred local background, not a global one). A
    dashed ring around the patch stands in for the Gaussian sampling window
    itself."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    painter = _new_painter(pixmap)
    pen = QPen(QColor(_MUTED), 1.4)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(2.0, 2.0, 20.0, 20.0))
    painter.setPen(Qt.PenStyle.NoPen)
    cell = 4.0
    pitch = 5.0
    origin = (_DESIGN_SIZE - (2 * pitch + cell)) / 2.0
    for row in range(3):
        for col in range(3):
            color = _LOCAL_CONTRAST_CENTER if row == 1 and col == 1 else _MUTED
            painter.setBrush(QColor(color))
            x = origin + col * pitch
            y = origin + row * pitch
            painter.drawRoundedRect(QRectF(x, y, cell, cell), 0.8, 0.8)
    painter.end()
    return QIcon(pixmap)


@functools.lru_cache(maxsize=None)
def morphology_icon() -> QIcon:
    """A crescent-moon-like shape (chunky/roundish, not a thin sliver) with
    a blue circle filling its concave bite, the two together reading as
    "most of the way to a full circle" - 2026-10-02 redesign (maintainer
    request: "more detailed... some arbitrary shape like crescent moon like
    (but more roundish) and after it would be circle"), replacing the
    original rounded-square-with-a-square-notch version. Keeps the original
    spec's "performed closing by blue color" idea (the blue circle is what
    closing would add to complete the shape) in a more illustrative,
    rounder glyph. The bite is punched with `CompositionMode_Clear` (a real
    transparent hole, not a same-color-as-background shape painted over it -
    correct even if this icon is ever drawn over a non-flat background)."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    painter = _new_painter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_MORPHOLOGY_SHAPE))
    painter.drawEllipse(QRectF(3.0, 4.5, 15.0, 15.0))
    bite = QRectF(9.5, 1.0, 13.0, 13.0)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(bite)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.setBrush(QColor(_MORPHOLOGY_CLOSE))
    painter.drawEllipse(QRectF(10.0, 1.5, 12.0, 12.0))
    painter.end()
    return QIcon(pixmap)


@functools.lru_cache(maxsize=None)
def draw_icon() -> QIcon:
    """Plain pencil - the one tool kind with an obvious, already-vendored
    Tabler equivalent (`icon_assets/pencil.svg`), so this wraps
    `load_tabler_icon` instead of hand-painting, unlike the other four."""
    return load_tabler_icon("pencil", color=_MUTED, size=_RENDER_SIZE, stroke_width=2.0)
