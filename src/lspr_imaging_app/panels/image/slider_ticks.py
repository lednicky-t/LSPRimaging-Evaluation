"""Slider tick-label rules for the Image panel's cube and wavelength sliders.

Pure functions (no panel state), split out of `panel.py` 2026-10-06; `ImagePanel`
keeps thin wrappers so its callers and tests are unchanged."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable

from .data_axis_slider import DataAxisSlider


def major_indices(count: int, view: tuple[float, float] | None = None) -> range:
    """The tick indices that may carry a label. Whole axis (``view`` None or covering everything): every `interval`-th index
    from the first, with the interval chosen for `count` ticks. Zoomed (``view`` = ``(lo, hi)``, see `AxisWindow`): the
    interval is chosen for the ticks on screen, and the labelled indices are still multiples of it, so a label does not
    move while the window is panned (it only appears and disappears at the edges)."""
    if count < 2:
        return range(0)
    if view is None or (view[0] <= 1e-9 and view[1] >= count - 1.0 - 1e-9):
        return range(0, count, nice_count_interval(count))
    first, last = max(math.ceil(view[0] - 1e-9), 0), min(math.floor(view[1] + 1e-9), count - 1)
    if last < first:
        return range(0)
    interval = nice_count_interval(last - first + 1)
    return range(-(-first // interval) * interval, last + 1, interval)


def wavelength_slider_major_ticks(values: tuple[float, ...], view: tuple[float, float] | None = None) -> dict[int, str]:
    """Indices to label on the wavelength axis slider: evenly spaced by
    array index (same shape as `cube_slider_major_ticks` below), each
    labeled with the *real* wavelength value at that index - never a
    rounded "nice" boundary number.

    **Real bug, fixed 2026-09-30** (maintainer report: clicked where the
    slider said "400", landed on 470 nm). The first-pass port of the
    stable app's `MainWindow._wavelength_slider_major_ticks` labeled
    each tick with the nearest round 100 nm boundary (`"400"`,
    `"500"`, ...) but positioned it at whichever *real* value happened
    to be closest to that boundary - for a dense, roughly-uniform grid
    the two are close enough not to notice, but this rewrite's
    wavelength set is per-cube (`DatasetModule.wavelengths_for_cube`)
    and can be genuinely irregular or gappy for a given cube (a cube
    can be short a wavelength another cube has), so "closest real value
    to 400" can legitimately be 470 - a label that is simply wrong
    about what clicking it selects, not just imprecise. Labeling with
    the real value at each shown index (rounded for display, matching
    the stable app's own tick text width) makes the label always
    exactly true, the same "no such thing as a mismatch" fix the cube
    slider already got for free by not trying to hit round numbers in
    the first place.

    **Always labels both sides of a large gap** (added alongside
    `DataAxisSlider._large_gap_boundaries`, which draws the scale-break
    glyph there - see that widget's module docstring for the full
    story of a same-day, now-reverted attempt to handle this with a
    synthetic "0" tick instead): the routine "every Nth index" rule
    below has no reason to land exactly on a gap's own edges, but a
    break glyph with an unlabeled tick on one side would read as
    "0 // <blank>" instead of "0 // 470". This mirrors the widget's own
    gap-detection as an independent copy, not a cross-class import -
    the widget reads pixel-rendering values, this reads the values
    about to be handed to it; same math, different callers.

    The gap this most commonly marks, for this slider specifically, is
    a real one, not an artifact: `DatasetModule.wavelengths_for_cube`'s
    docstring (`dataset/module.py`) has the confirmed domain fact and
    code pointers - `0.0`, when present, is the dataset's dark/
    background frame (LED off), a real image but not a spectral sample
    point, which is exactly why it sits far in value from the first
    real wavelength."""
    count = len(values)
    if count < 2:
        return {}
    majors = {index: f"{values[index]:.0f}" for index in major_indices(count, view)}
    if count >= 3:
        gaps = [values[i + 1] - values[i] for i in range(count - 1)]
        positive_gaps = [gap for gap in gaps if gap > 0]
        typical_gap = statistics.median(positive_gaps) if positive_gaps else 0.0
        if typical_gap > 0:
            for i, gap in enumerate(gaps):
                if gap > typical_gap * DataAxisSlider._GAP_BREAK_RATIO:
                    majors[i] = f"{values[i]:.0f}"
                    majors[i + 1] = f"{values[i + 1]:.0f}"
    return majors


def cube_slider_major_ticks(
    values: tuple[int, ...], label_for: Callable[[int], str] | None = None, view: tuple[float, float] | None = None
) -> dict[int, str]:
    """Indices to label on the cube axis slider: the raw cube index at a
    "nice" interval. Ported from the stable app's
    `MainWindow._cube_slider_major_ticks`. `label_for` (the cube display
    unit, `CubeDisplayModule.label_for`) names each labelled cube - its raw
    number, or its start time in Time mode; `None` = the raw number. `view`:
    the stretch of the axis on screen when it is zoomed (`major_indices`)."""
    count = len(values)
    if count < 2:
        return {}
    name = label_for if label_for is not None else str
    return {index: name(values[index]) for index in major_indices(count, view)}


def nice_count_interval(count: int, target_ticks: int = 8) -> int:
    candidates = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000)
    for candidate in candidates:
        if count / candidate <= target_ticks:
            return candidate
    return candidates[-1]
