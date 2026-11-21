"""Scrolling the Cube and wavelength sliders by hand leaves out the frames the slideshow leaves out (2026-11-21, maintainer request).

The slideshow has two Skip settings (ignored frames, the dark frame: `SlideshowSettings.skip_ignored` / `skip_dark`). Play, the
Previous / Next buttons and the wheel over the ROI row obey them; now the sliders do too. The same two settings, no new one: switch Skip
off in the slideshow settings and the sliders visit every frame again.

**What is filtered**: only the slider's own gestures (`DataAxisSlider.set_index_filter`): a press or drag on the track, the wheel,
the arrow / page keys, Home / End. A click on a mark of the ROI or Mask row, "Go to reference" and every other `setValue` is an
explicit request and lands exactly where it was asked. The Previous / Next buttons and the wheel past the end of the wavelength slider
already went through the slideshow's own stepping.

**The rule** (`snap_index`, pure):

- A position that is not skipped is taken as asked.
- A step (wheel, arrows, Page Up / Down, Home / End) goes on in its direction to the first frame that is not skipped. If nothing lies
  that way it is refused, like a step at the end of any slider; Home / End land on the nearest frame that is not skipped between the end
  and where the handle stands.
- A press or a drag lands on the nearest frame that is not skipped (a tie goes the way the handle is travelling). The rule looks only at
  where the pointer is, never at where the handle was, so dragging across a block of skipped frames cannot jitter back and forth.
- If **every** position is skipped (the dark frame is shown with Skip dark on; a cube that is wholly ignored) nothing is filtered: the user
  is standing on a skipped frame on purpose, and a slider that would not move at all is worse than one that shows skipped frames.

What counts as skipped: the Cube slider asks about the frame the cube would show at the wavelength shown now, the wavelength slider
about the frame at that wavelength in the cube shown now (`SlideshowController.is_skipped`). A cube that lacks the wavelength shown is
judged by the same question (an approximation for datasets whose cubes have different wavelengths; the selection then picks its own).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

IndexFilter = Callable[[int, int, int, int, bool], int]
"""``filter(requested, current, first, last, absolute) -> index to use``: what `DataAxisSlider` calls for its own gestures."""


def snap_index(
    requested: int,
    current: int,
    first: int,
    last: int,
    is_valid: Callable[[int], bool],
    *,
    absolute: bool,
) -> int:
    """The index to use for a slider request, given which indexes are allowed (see the module docstring for the rule).

    ``requested``: where the gesture wants to go; ``current``: where the handle stands; ``first`` / ``last``: the slider's range;
    ``is_valid(index)``: not skipped; ``absolute``: a press / drag (a position) rather than a step."""
    requested = min(max(int(requested), first), last)
    if is_valid(requested):
        return requested
    below = next((i for i in range(requested - 1, first - 1, -1) if is_valid(i)), None)
    above = next((i for i in range(requested + 1, last + 1) if is_valid(i)), None)
    if below is None and above is None:
        return requested  # everything is skipped: filter nothing
    direction = (requested > current) - (requested < current)
    if absolute or direction == 0:
        if below is None:
            return above
        if above is None:
            return below
        gap_below, gap_above = requested - below, above - requested
        if gap_below != gap_above:
            return below if gap_below < gap_above else above
        return above if requested >= current else below  # a tie: the way the handle is travelling
    beyond, behind = (above, below) if direction > 0 else (below, above)
    if beyond is not None:
        return beyond
    # Nothing allowed further on: a plain step is refused (stay), but a jump to an end (Home / End) may land on the nearest allowed
    # frame between the request and where the handle stands.
    if behind is not None and (current <= behind <= requested if direction > 0 else requested <= behind <= current):
        return behind
    return current


class _Cubes(Protocol):
    def spectral_cubes(self) -> list[int]: ...
    def wavelengths_for_cube(self, cube: int) -> tuple[float, ...]: ...


class _Selection(Protocol):
    def current_cube(self) -> int: ...
    def current_wavelength(self) -> float: ...


class _Skipper(Protocol):
    def is_skipped(self, cube: int, wavelength: float) -> bool: ...
    def settings(self): ...


def _skip_is_on(slideshow: _Skipper) -> bool:
    settings = slideshow.settings()
    return bool(settings.skip_ignored or settings.skip_dark)


def cube_index_filter(dataset: _Cubes, selection: _Selection, slideshow: _Skipper) -> IndexFilter:
    """The filter of the Cube slider: a cube is skipped when the frame at the wavelength shown now is."""

    def allowed(requested: int, current: int, first: int, last: int, absolute: bool) -> int:
        if not _skip_is_on(slideshow):
            return requested
        cubes = dataset.spectral_cubes()
        wavelength = float(selection.current_wavelength())

        def is_valid(index: int) -> bool:
            return index >= len(cubes) or not slideshow.is_skipped(int(cubes[index]), wavelength)

        return snap_index(requested, current, first, last, is_valid, absolute=absolute)

    return allowed


def wavelength_index_filter(dataset: _Cubes, selection: _Selection, slideshow: _Skipper) -> IndexFilter:
    """The filter of the wavelength slider: a wavelength is skipped when its frame in the cube shown now is."""

    def allowed(requested: int, current: int, first: int, last: int, absolute: bool) -> int:
        if not _skip_is_on(slideshow):
            return requested
        cube = int(selection.current_cube())
        wavelengths = dataset.wavelengths_for_cube(cube)

        def is_valid(index: int) -> bool:
            return index >= len(wavelengths) or not slideshow.is_skipped(cube, float(wavelengths[index]))

        return snap_index(requested, current, first, last, is_valid, absolute=absolute)

    return allowed
