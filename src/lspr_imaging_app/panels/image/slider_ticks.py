"""Slider tick-label rules for the Image panel's cube and wavelength sliders.

Pure functions (no panel state), split out of `panel.py` 2026-10-06; `ImagePanel`
keeps thin wrappers so its callers and tests are unchanged."""

from __future__ import annotations

import statistics

from .data_axis_slider import DataAxisSlider


def wavelength_slider_major_ticks(values: tuple[float, ...]) -> dict[int, str]:
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
    interval = nice_count_interval(count)
    majors = {index: f"{values[index]:.0f}" for index in range(0, count, interval)}
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


def cube_slider_major_ticks(values: tuple[int, ...]) -> dict[int, str]:
    """Indices to label on the cube axis slider: the raw cube index at a
    "nice" interval. Ported from the stable app's
    `MainWindow._cube_slider_major_ticks` - **scoped down**: the
    source's Cube/Time toggle (labeling by elapsed acquisition time
    instead of raw index) is not ported, since the elapsed-seconds
    mapping it reuses lives in the stable app's `AnalysisController`,
    which has no equivalent on this branch yet. See the rewrite status
    doc's gap list."""
    count = len(values)
    if count < 2:
        return {}
    interval = nice_count_interval(count)
    return {index: str(values[index]) for index in range(0, count, interval)}


def nice_count_interval(count: int, target_ticks: int = 8) -> int:
    candidates = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000)
    for candidate in candidates:
        if count / candidate <= target_ticks:
            return candidate
    return candidates[-1]
