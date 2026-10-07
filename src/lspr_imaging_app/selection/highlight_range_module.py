"""``HighlightRangeModule`` - the intensity range selected on the Histogram
panel's "Highlight" region, as shared cross-cutting state.

**Not Histogram-specific pub/sub, on purpose (maintainer's explicit
direction, 2026-09-29).** The range is needed independently by Mask
(the Add/Subtract range-mask tool) and ROI Toolbox (semi-automatic
detection's search band), and possibly more consumers later - if
`HistogramPanel` just emitted its own signal, every future consumer would
still need to know specifically about Histogram to get it, which is the
same point-to-point shape as the old app's entanglement, just inverted into
a hub instead of direct calls.

Owning the value here instead follows the precedent `SelectionModule`
already set (current cube/wavelength/ROI-selection) and `ReferenceFrameModule`
extended (2026-09-25): a piece of state that is legitimately needed by
several independent modules gets its own small, explicit owner in this
package - an acknowledged exception to CLAUDE.md's "nothing is shared" rule,
not a license to keep adding more shared state elsewhere. `HistogramPanel`
only ever calls `set_range`/`clear_range` (a command call, the same shape
`RoiToolbox.request_move` already uses); every consumer subscribes to
`range_changed` directly and needs no reference to `HistogramPanel` at all.

Deliberately narrow, per CLAUDE.md's module-boundary rule ("no module
reaches into another's internals"): holds only the `(min, max)` pair itself,
no reference to any other module - matching `ReferenceFrameModule`'s own
"commands take already-resolved values" convention.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented


class HighlightRangeModule(QObject):
    """Owns the Histogram panel's intensity-range ("Highlight") selection."""

    range_changed = pyqtSignal(object)  # tuple[float, float] | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._range: tuple[float, float] | None = None

    def current_range(self) -> tuple[float, float] | None:
        return self._range

    @instrumented("HighlightRangeModule.set_range")
    def set_range(self, min_value: float, max_value: float) -> None:
        min_value, max_value = float(min_value), float(max_value)
        if min_value > max_value:
            min_value, max_value = max_value, min_value
        new_range = (min_value, max_value)
        if new_range == self._range:
            return
        self._range = new_range
        self.range_changed.emit(new_range)

    @instrumented("HighlightRangeModule.clear_range")
    def clear_range(self) -> None:
        if self._range is None:
            return
        self._range = None
        self.range_changed.emit(None)
