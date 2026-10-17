"""``DataAxisSlider`` - ported from the stable app's ``gui/widgets.py``
(2026-08 tick/axis-style redesign, see ``docs/image_area_slider_redesign.md``)
for the Cube and Wavelength navigation rows in the rewrite's Image panel.

Copied with its painting/hit-testing logic unchanged (the widget itself
holds no notion of cubes/wavelengths/time - see the class docstring) -
only the caller side (``panel.py``) had to change to fit this branch's
module architecture (``DatasetModule``/``SelectionModule`` instead of
``MainWindow``'s flat attributes). ``set_tick_cache_state`` is ported too
even though nothing calls it yet: the Analysis stage's "is this cube
already cached" indicator (``docs/sensorgram_reentrancy_and_cube_slider_
cache_indicator.md``) needs an Analysis UI and a background debounced scan
that don't exist on this branch yet - see the rewrite status doc's gap
list. Wiring it up is a follow-up, not a reason to leave the paint support
out now.

``_large_gap_boundaries``/``_paint_gap_break`` (2026-09-30) are **not**
part of the port - a real, unusually large consecutive gap in the values
passed to ``set_ticks`` gets a scale-break glyph drawn on the rail between
the two real ticks on either side of it. **Both ticks stay real, labeled
(if the caller's ``major_labels`` say so), and selectable indices** - this
purely draws a visual annotation, it never adds, removes, or relabels a
tick, and never invents a value the caller didn't already have.

For the wavelength slider specifically, the gap this exists to mark is a
real one: the dataset's ``0.0`` nm entry, when present, is the dark/
background frame (LED off) - a real, ordinarily-acquired, selectable image,
just not a spectral sample point, sitting far in value from the first real
wavelength. See ``DatasetModule.wavelengths_for_cube``'s docstring
(``dataset/module.py``) for the confirmed (not assumed) domain fact and its
code pointers - this widget itself still has no notion of wavelengths or
dark frames (module docstring above); it only ever renders whatever gap it
finds in the numbers it's given.

This replaces a same-day first attempt (``set_axis_break``) that invented
a synthetic, non-selectable "0" tick outside the real value range - which
duplicated an already-real 0 nm tick whenever the dataset genuinely had
one (maintainer report, screenshot: "created two 0 ticks, first one not
working"), and was misleading the rest of the time too (a fixed label
implying a physical zero the widget had no way to confirm was actually
true for every caller). Detecting the gap in the real data instead of
assuming where the axis "should" start avoids both failure modes.
"""

from __future__ import annotations

import math
import statistics

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QAbstractSlider, QMenu, QSlider, QWidget

from lspr_ui import get_active_theme

# Room for half of the widest first/last tick label (e.g. "0:00") so it isn't clipped.
SIDE_INSET = 14.0


def axis_track(widget_width: float) -> tuple[float, float]:
    """``(left, width)`` of the axis a data slider draws its ticks along, for a widget `widget_width` pixels wide."""
    return SIDE_INSET, max(float(widget_width) - 2.0 * SIDE_INSET, 1.0)


def axis_x(index: float, count: int, widget_width: float, view: tuple[float, float] | None = None) -> float:
    """The x pixel of tick `index` of `count` on an axis of a widget `widget_width` wide: ticks are spaced by index.
    `view` = ``(lo, hi)``, the stretch of indices on screen (default: all of them, ``(0, count - 1)``); see `AxisWindow`.

    The one place this position is worked out for anything drawn on the Cube axis (the slider and the ROI row), so a
    mark and the tick above it can never drift apart, zoomed or not."""
    left, width = axis_track(widget_width)
    if count < 2:
        return left
    lo, hi = view if view is not None else (0.0, count - 1.0)
    if hi <= lo:
        return left
    return left + width * ((index - lo) / (hi - lo))


def axis_index(x: float, count: int, widget_width: float, view: tuple[float, float] | None = None) -> int:
    """The tick index nearest to the x pixel `x` (clamped to the axis and the ticks); the inverse of `axis_x`."""
    left, width = axis_track(widget_width)
    if count < 2:
        return 0
    lo, hi = view if view is not None else (0.0, count - 1.0)
    fraction = min(max((x - left) / width, 0.0), 1.0)
    return min(max(round(lo + fraction * (hi - lo)), 0), count - 1)


class AxisWindow(QObject):
    """Which stretch of a data axis is on screen (2026-10-16, maintainer request: zoom for the sliders).

    Positions are float tick *indices*: the full axis is ``(0, count - 1)``. Everything drawn on an axis maps an index to a
    pixel through `axis_x(..., window.view_for(count))`, so the Cube slider and the ROI row under it share **one** window and
    always agree; the wavelength slider has its own. Pure state plus the gestures that change it, no painting:

    - **Ctrl + wheel** zooms around the pointer (a touchpad pinch sends the same); **Shift + wheel** and a **side wheel**
      (horizontal scroll) pan; **middle-button drag** pans (`start_pan` / `drag_pan` / `end_pan`); `reset()` (the widgets
      call it on a middle-button double click) shows everything again.
    - The deepest zoom shows about `MIN_VISIBLE_TICKS` ticks. A new tick count (another dataset) resets to the full axis.
    - `ensure_visible(index)` pages the window when the value it follows (the cube shown, also while Play runs) leaves it.
    """

    changed = pyqtSignal()

    MIN_VISIBLE_TICKS = 5
    ZOOM_PER_NOTCH = 1.25
    PAN_PER_NOTCH = 0.10  # of the visible span
    PAGE_MARGIN = 0.10  # a value that left the window comes back this far (of the span) from the edge it crossed

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self._lo = 0.0
        self._hi = 0.0
        self._pan_start: tuple[float, float, float] | None = None  # (x, lo, span) when a pan drag began

    # -- state ------------------------------------------------------------------------------------

    def count(self) -> int:
        return self._count

    def view(self) -> tuple[float, float]:
        return self._lo, self._hi

    def view_for(self, count: int) -> tuple[float, float]:
        """The window, or the full axis when it was made for a different tick count."""
        return (self._lo, self._hi) if count == self._count else (0.0, max(count - 1.0, 0.0))

    def is_zoomed(self) -> bool:
        return self._count >= 2 and (self._lo > 1e-9 or self._hi < self._count - 1.0 - 1e-9)

    def set_count(self, count: int) -> None:
        """The axis has `count` ticks. A different count (another dataset) resets to the full axis."""
        count = max(int(count), 0)
        if count != self._count:
            self._count = count
            self._lo, self._hi = 0.0, max(count - 1.0, 0.0)
            self._pan_start = None
            self.changed.emit()

    def _set_view(self, lo: float, hi: float) -> None:
        full = max(self._count - 1.0, 0.0)
        if full <= 0.0:
            return
        span = min(max(hi - lo, min(self.MIN_VISIBLE_TICKS - 1.0, full)), full)
        lo = min(max(lo, 0.0), full - span)
        if abs(lo - self._lo) > 1e-9 or abs(lo + span - self._hi) > 1e-9:
            self._lo, self._hi = lo, lo + span
            self.changed.emit()

    # -- changing it --------------------------------------------------------------------------------

    def reset(self) -> None:
        self._set_view(0.0, max(self._count - 1.0, 0.0))

    def zoom(self, factor: float, anchor_fraction: float = 0.5) -> None:
        """Zoom in by `factor` (> 1 in, < 1 out) keeping the tick under `anchor_fraction` (0 = left edge, 1 = right) put."""
        span = self._hi - self._lo
        if self._count < 2 or span <= 0.0 or factor <= 0.0:
            return
        anchor_fraction = min(max(anchor_fraction, 0.0), 1.0)
        new_span = span / factor
        anchor = self._lo + anchor_fraction * span
        self._set_view(anchor - anchor_fraction * new_span, anchor + (1.0 - anchor_fraction) * new_span)

    def pan(self, delta_ticks: float) -> None:
        self._set_view(self._lo + delta_ticks, self._hi + delta_ticks)

    def ensure_visible(self, index: float) -> None:
        """Page the window so that tick `index` is on screen (nothing happens if it already is, or the axis is not zoomed)."""
        if not self.is_zoomed() or self._lo <= index <= self._hi:
            return
        span = self._hi - self._lo
        lo = index - self.PAGE_MARGIN * span if index > self._hi else index - (1.0 - self.PAGE_MARGIN) * span
        self._set_view(lo, lo + span)

    # -- gestures -------------------------------------------------------------------------------------

    def handle_wheel(self, event, widget_width: float) -> bool:
        """Use a wheel event if it is a zoom / pan gesture, and say whether it was. A plain vertical wheel is not one: the
        widget keeps it (the slider steps, the ROI row hops between marks)."""
        if self._count < 2:
            return False
        modifiers = event.modifiers()
        angle = event.angleDelta()
        notches_y, notches_x = angle.y() / 120.0, angle.x() / 120.0
        span = self._hi - self._lo
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            notches = notches_y or notches_x
            if not notches:
                return False
            left, width = axis_track(widget_width)
            self.zoom(self.ZOOM_PER_NOTCH ** notches, (event.position().x() - left) / width)
            return True
        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            if notches_y:  # wheel up = towards the first cube, like scrolling up a page
                self.pan(-notches_y * self.PAN_PER_NOTCH * span)
                return True
            if notches_x:
                self.pan(notches_x * self.PAN_PER_NOTCH * span)
                return True
            return False
        if modifiers == Qt.KeyboardModifier.NoModifier and notches_x and not notches_y:  # a side wheel: right = later cubes
            self.pan(notches_x * self.PAN_PER_NOTCH * span)
            return True
        return False

    def start_pan(self, x: float) -> None:
        self._pan_start = (x, self._lo, self._hi - self._lo)

    def is_panning(self) -> bool:
        return self._pan_start is not None

    def drag_pan(self, x: float, widget_width: float) -> bool:
        """The pointer is at `x` during a pan drag: the axis follows it like a sheet of paper. Returns whether a pan is on."""
        if self._pan_start is None:
            return False
        start_x, start_lo, span = self._pan_start
        _left, width = axis_track(widget_width)
        shift = -(x - start_x) / width * span
        self._set_view(start_lo + shift, start_lo + shift + span)
        return True

    def end_pan(self) -> None:
        self._pan_start = None


class DataAxisSlider(QSlider):
    """Horizontal QSlider whose handle can only ever land on one of the real
    values it represents - index-only min/max/step, set up exactly the same
    way as a stock QSlider - painting is the only thing this changes. Draws
    every entry passed to `set_ticks` as a light tick below a thin rail,
    with the caller-chosen "major" subset drawn taller and labeled. Which
    indices count as major and what text they get is entirely up to the
    caller - this widget only ever renders whatever it's told, it has no
    notion of wavelengths or cubes itself.

    Clicking anywhere on the track jumps the handle straight there (like
    clicking a plot axis) rather than paging one step at a time - the
    default QSlider page-step click behavior doesn't fit a thin, tick-
    labeled track the way it fits a fat native handle. Keyboard arrow
    stepping and a plain wheel are untouched (QSlider's own), except at the ends: a plain wheel turned on *past* the last
    (up) or first (down) tick emits `edge_step_requested` (+1 / -1) instead of doing nothing. The slider does not know what
    lies beyond its ends; the owner decides (the Image panel makes it the next / previous cube for the wavelength slider).

    **Zoom** (2026-10-16): the ticks are drawn through an `AxisWindow` (see there for the gestures): only the stretch of
    indices in the window is on screen, the rest is off to the sides (small triangles at the rail ends say so). The window
    follows the slider's own value, so the handle never leaves the screen (also while Play runs), and the caller re-labels the
    ticks for the visible stretch when the window changes (`axis_window().changed` -> `set_ticks`). `set_window` replaces it, so
    the ROI row under the Cube slider can share the Cube slider's.
    """

    _TRACK_TOP = 6.0
    _TRACK_HEIGHT = 3.0
    _TICK_TOP = 11.0
    _MINOR_TICK_HEIGHT = 4.0
    _MAJOR_TICK_HEIGHT = 7.0
    _LABEL_TOP = 19.0
    _SIDE_INSET = SIDE_INSET
    # Fixed, not derived from _accent_color: the handle's accent can be
    # repurposed per selection, but "this tick is cached" should mean the
    # same color regardless of whatever the handle currently shows. Matches
    # the stable app's multi-ROI accent used elsewhere.
    _CACHED_TICK_COLOR = "#38bdf8"
    # A consecutive gap at least this many times the dataset's own typical
    # (median) gap is drawn as a scale-break glyph on the rail - see
    # `_large_gap_boundaries`. Purely a rendering judgment from the values
    # already passed to `set_ticks`, the same self-contained spirit as the
    # label-overlap-avoidance logic already here - no caller opt-in needed,
    # so this applies equally (and harmlessly) to the cube slider if its
    # own values ever have an outlier gap.
    _GAP_BREAK_RATIO = 4.0
    _WHEEL_NOTCH = 120  # Qt: one notch of a wheel mouse is 120 eighths of a degree

    edge_step_requested = pyqtSignal(int)  # +1 / -1: the wheel was turned on past the last / first tick

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._edge_wheel = 0  # wheel angle units turned against an end of the slider, not yet worth a step (touchpads)
        self._tick_values: list[float] = []
        self._major_labels: dict[int, str] = {}
        self._cached_tick_indices: frozenset[int] | None = None
        self._accent_color = "#38bdf8"
        self._reference_highlight_color: str | None = None
        self._reference_tick_index: int | None = None
        self._reference_tick_color: str | None = None
        self._dark_tick_index: int | None = None
        self._dark_tick_color: str | None = None
        self._ignored_tick_indices: frozenset[int] = frozenset()
        self._ignored_tick_color: str | None = None
        self._window = AxisWindow(self)
        self._window.changed.connect(self.update)
        self.setFixedHeight(32)

    # -- zoom ------------------------------------------------------------------------------------------

    def axis_window(self) -> AxisWindow:
        return self._window

    def set_window(self, window: AxisWindow) -> None:
        """Draw through `window` from now on (another widget's, to share one zoom)."""
        if window is self._window:
            return
        self._window.changed.disconnect(self.update)
        self._window = window
        window.changed.connect(self.update)
        window.set_count(len(self._tick_values))
        self.update()

    def sliderChange(self, change) -> None:  # noqa: N802 - Qt naming
        super().sliderChange(change)
        window = getattr(self, "_window", None)  # Qt can call this while the base class is still being constructed
        if window is not None and change == QAbstractSlider.SliderChange.SliderValueChange:  # also when signals are blocked
            window.ensure_visible(self.value() - self.minimum())

    def set_accent_color(self, color: str) -> None:
        self._accent_color = color
        self.update()

    def set_reference_highlight(self, color: str | None) -> None:
        self._reference_highlight_color = color
        self.update()

    def set_reference_tick(self, index: int | None, color: str | None = None) -> None:
        """Draws the tick at `index` (position in the `values` array last
        passed to `set_ticks`) thicker, taller and in `color`, to mark the
        reference frame's cube/wavelength. `None` removes the mark. Purely
        visual - the tick stays an ordinary selectable index."""
        if index is None or color is None:
            index, color = None, None
        if (index, color) == (self._reference_tick_index, self._reference_tick_color):
            return
        self._reference_tick_index = index
        self._reference_tick_color = color
        self.update()

    def set_dark_tick(self, index: int | None, color: str | None = None) -> None:
        """Marks the dark background's tick the way `set_reference_tick` marks the
        reference's (same thick, tall tick, its own `color`). `None` removes it."""
        if index is None or color is None:
            index, color = None, None
        if (index, color) == (self._dark_tick_index, self._dark_tick_color):
            return
        self._dark_tick_index = index
        self._dark_tick_color = color
        self.update()

    def set_ignored_ticks(self, indices: frozenset[int], color: str | None = None) -> None:
        """Marks every tick in `indices` (positions in the `values` array last passed to
        `set_ticks`) as an ignored frame: thick, tall and in `color`, like the reference's.
        Empty `indices` (or no `color`) removes the marks."""
        if not indices or color is None:
            indices, color = frozenset(), None
        if (indices, color) == (self._ignored_tick_indices, self._ignored_tick_color):
            return
        self._ignored_tick_indices = frozenset(indices)
        self._ignored_tick_color = color
        self.update()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt naming
        """Right-click menu: jump the handle to the reference frame's tick (disabled when none is marked)."""
        menu = QMenu(self)
        go_to = menu.addAction("Go to reference")
        reference = self._reference_tick_index
        go_to.setEnabled(reference is not None and self.minimum() <= reference <= self.maximum())
        if menu.exec(event.globalPos()) is go_to and reference is not None:
            self.setValue(reference)

    def set_ticks(self, values, major_labels: dict[int, str]) -> None:
        """`values` is the full real-value array this slider indexes into -
        one entry per valid index, the same array current-value lookups
        read from - every entry gets a minor tick. `major_labels` maps a
        subset of those indices to the label text drawn under a taller
        tick; indices absent from it stay minor and unlabeled."""
        new_tick_values = list(values)
        if new_tick_values != self._tick_values:
            # Any previously computed cache-state is positions into the OLD
            # values array - a genuinely different array (different
            # dataset, changed range) can reorder/resize it, so a stale
            # index here would color the wrong tick. Callers that care
            # re-request it via set_tick_cache_state once they've
            # recomputed against the new values.
            self._cached_tick_indices = None
        self._tick_values = new_tick_values
        self._major_labels = dict(major_labels)
        self._window.set_count(len(new_tick_values))  # another tick count (another dataset): the full axis again
        self.update()

    def set_tick_cache_state(self, cached_indices: frozenset[int] | None) -> None:
        """Marks a subset of tick indices (by position in the `values` array
        last passed to `set_ticks`) as already available for whatever the
        caller currently cares about. `None` means "no cache information to
        show" - ticks paint exactly as before set_tick_cache_state was ever
        called."""
        if cached_indices == self._cached_tick_indices:
            return
        self._cached_tick_indices = cached_indices
        self.update()

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(base.width(), 32)

    def minimumSizeHint(self) -> QSize:
        base = super().minimumSizeHint()
        return QSize(base.width(), 32)

    def _track_rect(self) -> QRectF:
        left, width = axis_track(self.width())
        return QRectF(left, 0.0, width, self._LABEL_TOP)

    def _large_gap_boundaries(self) -> list[int]:
        """Indices `i` such that the gap between `_tick_values[i]` and
        `_tick_values[i + 1]` is much larger than this dataset's own typical
        (median) consecutive gap - e.g. a real acquisition at 0 nm (a dark/
        reference frame) followed by the first real spectral wavelength
        hundreds of nm later. Both ticks on either side of a detected gap
        are real, already-labeled (if the caller's `major_labels` say so),
        already-selectable indices - this only draws a visual break glyph
        between them, it never removes, adds, or relabels a tick. Deliberate
        contrast with an earlier version of this feature (see the build
        log): that version invented a synthetic "0" tick that was not a
        real, selectable index - which duplicated an already-real 0 nm tick
        whenever the dataset genuinely had one, and was never reachable by
        drag when it didn't. Detecting the gap in the real data instead of
        assuming where it starts avoids both failure modes."""
        values = self._tick_values
        if len(values) < 3:
            return []
        gaps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        positive_gaps = [gap for gap in gaps if gap > 0]
        if not positive_gaps:
            return []
        typical_gap = statistics.median(positive_gaps)
        if typical_gap <= 0:
            return []
        return [i for i, gap in enumerate(gaps) if gap > typical_gap * self._GAP_BREAK_RATIO]

    def _value_fraction(self) -> float:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        return (self.value() - self.minimum()) / float(span)

    def paintEvent(self, _event) -> None:
        theme = get_active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        left, width = track.left(), track.width()
        enabled = self.isEnabled()

        rail_border = theme.control_border if enabled else theme.control_disabled_border
        rail = QRectF(left, self._TRACK_TOP, width, self._TRACK_HEIGHT)
        painter.setPen(QPen(QColor(rail_border), 1.0))
        painter.setBrush(QColor(theme.control_bg if enabled else theme.control_disabled_bg))
        painter.drawRoundedRect(rail, 1.5, 1.5)

        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        count = len(self._tick_values)
        view = self._window.view_for(count)
        lo, hi = view
        right = left + width

        def x_of(index: float) -> float:
            return axis_x(index, count, self.width(), view)

        if count > 1 and enabled:
            if self._window.is_zoomed():  # more axis off to a side: a small triangle at that end of the rail
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.text_dim))
                rail_y = self._TRACK_TOP + self._TRACK_HEIGHT / 2.0
                if lo > 1e-9:
                    painter.drawPolygon(QPolygonF([QPointF(left - 4.0, rail_y), QPointF(left - 9.0, rail_y - 3.5), QPointF(left - 9.0, rail_y + 3.5)]))
                if hi < count - 1.0 - 1e-9:
                    painter.drawPolygon(QPolygonF([QPointF(right + 4.0, rail_y), QPointF(right + 9.0, rail_y - 3.5), QPointF(right + 9.0, rail_y + 3.5)]))

            for boundary in self._large_gap_boundaries():
                mid_x = (x_of(boundary) + x_of(boundary + 1)) / 2.0
                if left <= mid_x <= right:
                    self._paint_gap_break(painter, mid_x, rail_border)

            minor_color = QColor(theme.control_border)
            major_color = QColor(theme.text_dim)
            cached_color = QColor(self._CACHED_TICK_COLOR)
            cached_tick_indices = self._cached_tick_indices
            last_label_right: float | None = None
            for index in range(max(math.floor(lo), 0), min(math.ceil(hi), count - 1) + 1):  # only what is on screen
                x = x_of(index)
                if x < left - 0.5 or x > right + 0.5:
                    continue
                label = self._major_labels.get(index)
                if label:
                    # Two majors can land close together in pixel space even
                    # when they're far apart in value - ticks are spaced by
                    # index, not by value, so a sparse stretch of the
                    # dataset compresses whatever majors fall in it. Drop
                    # (not truncate) any label whose text would overlap the
                    # previous one rather than let them draw on top of each
                    # other; the tick itself still gets the plain "minor"
                    # treatment so the point isn't hidden entirely.
                    half_width = metrics.horizontalAdvance(label) / 2.0
                    if last_label_right is not None and x - half_width < last_label_right + 4.0:
                        label = None
                    else:
                        last_label_right = x + half_width
                is_major = label is not None
                if cached_tick_indices is not None and index in cached_tick_indices:
                    tick_color = cached_color
                else:
                    tick_color = major_color if is_major else minor_color
                painter.setPen(QPen(tick_color, 1.0))
                tick_height = self._MAJOR_TICK_HEIGHT if is_major else self._MINOR_TICK_HEIGHT
                painter.drawLine(QPointF(x, self._TICK_TOP), QPointF(x, self._TICK_TOP + tick_height))
                if label:
                    painter.drawText(QPointF(x - metrics.horizontalAdvance(label) / 2.0, self._LABEL_TOP + metrics.ascent()), label)

            mark_top = self._TICK_TOP - 2.0
            mark_bottom = self._TICK_TOP + self._MAJOR_TICK_HEIGHT
            for mark_index, mark_colors in self._mark_colors(count).items():
                x = x_of(mark_index)
                if x < left - 0.5 or x > right + 0.5:
                    continue
                if len(mark_colors) == 1:
                    painter.setPen(QPen(QColor(mark_colors[0]), 2.5))
                    painter.drawLine(QPointF(x, mark_top), QPointF(x, mark_bottom))
                    continue
                # Two or more marks on one tick (e.g. the reference is also an ignored frame):
                # the tick is split into stacked segments, one colour each, so none hides another.
                segment = (mark_bottom - mark_top) / len(mark_colors)
                for n, mark_color in enumerate(mark_colors):
                    pen = QPen(QColor(mark_color), 2.5)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    painter.setPen(pen)
                    painter.drawLine(
                        QPointF(x, mark_top + n * segment), QPointF(x, mark_top + (n + 1) * segment)
                    )

        handle_index = self._value_fraction() * max(count - 1.0, 0.0)
        if enabled and count > 0 and lo - 0.5 <= handle_index <= hi + 0.5:  # off screen only after a pan
            handle_x = x_of(handle_index) if count > 1 else left
            handle_color = QColor(self._reference_highlight_color or self._accent_color)
            painter.setPen(QPen(handle_color, 2.0))
            painter.setBrush(QColor(theme.window_bg))
            painter.drawEllipse(QPointF(handle_x, self._TRACK_TOP + self._TRACK_HEIGHT / 2.0), 5.0, 5.0)

        painter.end()

    def _mark_colors(self, count: int) -> dict[int, list[str]]:
        """Colours of the marked ticks, by index (top to bottom: ignored, dark, reference)."""
        colors: dict[int, list[str]] = {}
        if self._ignored_tick_color:
            for index in self._ignored_tick_indices:
                if 0 <= index < count:
                    colors.setdefault(index, []).append(self._ignored_tick_color)
        for index, color in (
            (self._dark_tick_index, self._dark_tick_color),
            (self._reference_tick_index, self._reference_tick_color),
        ):
            if index is not None and color and 0 <= index < count:
                colors.setdefault(index, []).append(color)
        return colors

    def _paint_gap_break(self, painter: QPainter, mid_x: float, rail_border: str) -> None:
        """A scale-break glyph (two short parallel diagonal strokes crossing
        the rail - the standard chart convention for "an axis discontinuity,
        not drawn to scale") centered at `mid_x`, between two real ticks
        flagged by `_large_gap_boundaries`. Purely a visual annotation on
        top of the rail - drawn *before* the tick-marks-and-handle drawing
        that follows in `paintEvent`, so it never obscures anything."""
        rail_y = self._TRACK_TOP + self._TRACK_HEIGHT / 2.0
        half_gap, half_height = 2.5, 4.0
        painter.setPen(QPen(QColor(rail_border), 1.4))
        for offset in (-half_gap, half_gap):
            painter.drawLine(
                QPointF(mid_x + offset - 1.5, rail_y + half_height),
                QPointF(mid_x + offset + 1.5, rail_y - half_height),
            )

    def _index_from_x(self, x: float) -> int:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return self.minimum()
        track = self._track_rect()
        if track.width() <= 0:
            return self.minimum()
        count = span + 1
        return self.minimum() + axis_index(x, count, self.width(), self._window.view_for(count))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setValue(self._index_from_x(event.position().x()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self.isEnabled():  # a drag with it pans the zoomed axis
            self._window.start_pan(event.position().x())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton and self.isEnabled():
            self.setValue(self._index_from_x(event.position().x()))
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.MiddleButton and self._window.drag_pan(event.position().x(), self.width()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton and self._window.is_panning():
            self._window.end_pan()
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton and self.isEnabled():  # show the whole axis again
            self._window.reset()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.isEnabled() and self._window.handle_wheel(event, self.width()):  # Ctrl: zoom, Shift / side wheel: pan
            event.accept()
            return
        if self.isEnabled() and self._wheel_past_an_end(event):
            event.accept()
            return
        super().wheelEvent(event)  # a plain wheel steps the value, as a QSlider does

    def _wheel_past_an_end(self, event) -> bool:
        """A plain wheel turned against the end it is already at: collect it, and once it adds up to one notch ask the owner
        to go on past that end (`edge_step_requested`). Returns whether the event was used. A wheel that merely *reaches*
        an end is left to QSlider, so a fast spin stops on the last tick and only the next notch crosses (no overshoot).
        One request per notch, the rest of a burst is dropped: after the owner moves on, the slider is at its other end."""
        delta = event.angleDelta().y()
        if event.modifiers() != Qt.KeyboardModifier.NoModifier or not delta:
            return False
        direction = 1 if delta > 0 else -1  # up = higher value, as QSlider's own wheel
        at_end = self.value() >= self.maximum() if direction > 0 else self.value() <= self.minimum()
        if not at_end:
            self._edge_wheel = 0
            return False
        if self._edge_wheel * direction < 0:  # turned the other way since: start counting again
            self._edge_wheel = 0
        self._edge_wheel += delta
        if abs(self._edge_wheel) >= self._WHEEL_NOTCH:
            self._edge_wheel = 0
            self.edge_step_requested.emit(direction)
        return True
