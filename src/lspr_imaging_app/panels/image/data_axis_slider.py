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

import statistics

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSlider, QWidget

from lspr_ui import get_active_theme


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
    stepping and any wheel-event handling installed via eventFilter
    elsewhere are untouched since neither is overridden here.
    """

    _TRACK_TOP = 6.0
    _TRACK_HEIGHT = 3.0
    _TICK_TOP = 11.0
    _MINOR_TICK_HEIGHT = 4.0
    _MAJOR_TICK_HEIGHT = 7.0
    _LABEL_TOP = 19.0
    _SIDE_INSET = 6.0
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

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._tick_values: list[float] = []
        self._major_labels: dict[int, str] = {}
        self._cached_tick_indices: frozenset[int] | None = None
        self._accent_color = "#38bdf8"
        self._reference_highlight_color: str | None = None
        self.setFixedHeight(32)

    def set_accent_color(self, color: str) -> None:
        self._accent_color = color
        self.update()

    def set_reference_highlight(self, color: str | None) -> None:
        self._reference_highlight_color = color
        self.update()

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
        width = max(self.width() - 2.0 * self._SIDE_INSET, 1.0)
        return QRectF(self._SIDE_INSET, 0.0, width, self._LABEL_TOP)

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
        if count > 1 and enabled:
            gap_boundaries = self._large_gap_boundaries()
            for boundary in gap_boundaries:
                x_left = left + width * (boundary / float(count - 1))
                x_right = left + width * ((boundary + 1) / float(count - 1))
                self._paint_gap_break(painter, (x_left + x_right) / 2.0, rail_border)

            minor_color = QColor(theme.control_border)
            major_color = QColor(theme.text_dim)
            cached_color = QColor(self._CACHED_TICK_COLOR)
            cached_tick_indices = self._cached_tick_indices
            last_label_right: float | None = None
            for index in range(count):
                x = left + width * (index / float(count - 1))
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

        if enabled and count > 0:
            handle_x = left + width * self._value_fraction()
            handle_color = QColor(self._reference_highlight_color or self._accent_color)
            painter.setPen(QPen(handle_color, 2.0))
            painter.setBrush(QColor(theme.window_bg))
            painter.drawEllipse(QPointF(handle_x, self._TRACK_TOP + self._TRACK_HEIGHT / 2.0), 5.0, 5.0)

        painter.end()

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
        fraction = (x - track.left()) / track.width()
        fraction = min(max(fraction, 0.0), 1.0)
        return self.minimum() + round(fraction * span)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setValue(self._index_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton and self.isEnabled():
            self.setValue(self._index_from_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)
