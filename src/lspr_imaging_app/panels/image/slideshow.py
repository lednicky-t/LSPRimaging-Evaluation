"""The dataset slideshow (2026-10-13, maintainer request): Play in the slider
ribbon steps the Image panel through the frames one by one.

Two parts:

- ``next_frame`` is pure (no Qt, no dataset object): given the cubes, each cube's
  wavelengths, the current frame and the settings, it says which frame comes
  next, or ``None`` when the show ends. That is where every rule about "skip
  cubes", "repeat" and "what is the end" lives, so it is unit-tested alone.
- ``SlideshowController`` owns the timer and the Playing/Paused state, and moves
  the selection (`SelectionModule.set_cube` / `set_wavelength`), the same two
  commands the sliders use, so the panel redraws as it does for a drag.

Settings (``SlideshowSettings``):

- **speed**: 1x, 2x, 5x, 10x. 1x is the dataset's average time between two
  consecutive wavelengths when it has acquisition timing, else 0.5 s.
- **mode** ``MODE_ALL``: every wavelength of a cube, then the next cube.
  ``MODE_ONE_WAVELENGTH``: stay on the wavelength shown and step through the
  cubes (a cube that lacks that wavelength is skipped).
- **repeat** and **end**: where "the end" is (``END_CUBE``: the last wavelength
  of the cube the show is in; ``END_DATASET``: the last frame of the last cube),
  and whether the show then jumps back to the start of that range or stops.
  ``END_CUBE`` only means something in ``MODE_ALL``; in ``MODE_ONE_WAVELENGTH``
  the end is always the last cube.

Ignored frames and dark frames can each be left out (``skip_ignored``, default on;
``skip_dark``, default off: the dark frame is a real image).

**Mask states** (``MODE_MASK_STATES``, 2026-10-16) work the same way over the marks of the Mask row under the Cube slider.

**ROI states** (``MODE_ROI_STATES``, 2026-10-16): step through the cubes that carry a mark on the ROI row (the start
layout at the first cube and every change of the ROI layout), staying on the wavelength shown now like the one-wavelength
mode. The controller is handed those cubes (``state_cubes``) and the pure functions treat them as the cube list; the
current cube need not be one of them (a step goes to the next state after it / the last one before it).

**Manual steps** (``SlideshowController.step``, the Previous / Next buttons and the wheel over the ROI row): one frame
back or forward in the order of the chosen mode, with the same skip rules; never wrapping and ignoring End, since the
user is the one deciding where to stop. They do not touch the Playing / Paused state.
"""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from ...dataset import DatasetModule
from ...selection import SelectionModule
from ...selection.frame_roles_module import ROLE_DARK, ROLE_IGNORED, FrameRolesModule

MODE_ALL = "all"
MODE_ONE_WAVELENGTH = "one_wavelength"
MODE_ROI_STATES = "roi_states"
MODE_MASK_STATES = "mask_states"  # same stepping, over the cubes with a mark on the Mask row
STATE_MODES = (MODE_ROI_STATES, MODE_MASK_STATES)  # modes whose cubes are handed in by the owner of the marks
END_CUBE = "cube"
END_DATASET = "dataset"
SPEEDS = (1, 2, 5, 10)
DEFAULT_INTERVAL_S = 0.5  # 1x when the dataset has no timing
MIN_INTERVAL_S = 0.02  # a show faster than this only piles up renders
MAX_WAIT_FOR_FRAME_S = 2.0  # never wait longer than this for a frame to appear (a step that shows nothing)
_POLL_MS = 10


@dataclass(frozen=True)
class SlideshowSettings:
    speed: int = 1
    mode: str = MODE_ALL
    repeat: bool = False
    end: str = END_DATASET
    skip_ignored: bool = True  # leave ignored frames out of the show
    skip_dark: bool = False  # leave the dark background frame (LED off) out of the show
    thin_roi_outline: bool = False  # draw ROI outlines 1.0 px thin while playing (about 5x faster to paint)
    calm_histogram: bool = False  # update the Histogram at most once a second while playing (a redraw is ~30 ms of GUI time)


Frame = tuple[int, float]


def _range_cubes(cubes: Sequence[int], current: Frame, settings: SlideshowSettings) -> Sequence[int]:
    """The cubes the show may visit: all of them, or only the one it is in (End = cube, all-wavelengths mode)."""
    if settings.mode == MODE_ALL and settings.end == END_CUBE:
        return [current[0]]
    return cubes


def _wavelengths_of(
    cube: int, wavelengths_by_cube: Mapping[int, Sequence[float]], current: Frame, settings: SlideshowSettings
) -> Sequence[float]:
    """The wavelengths the show visits inside *cube*, ascending (one-wavelength mode: only the current one, if the cube has it)."""
    wavelengths = wavelengths_by_cube.get(cube, ())
    if settings.mode == MODE_ONE_WAVELENGTH or settings.mode in STATE_MODES:
        return (current[1],) if current[1] in wavelengths else ()
    return wavelengths


def _state_frame(
    state_cubes: Sequence[int],
    wavelengths_by_cube: Mapping[int, Sequence[float]],
    current: Frame,
    is_skipped: Callable[[int, float], bool],
    *,
    forward: bool,
) -> Frame | None:
    """The ROI-states mode's neighbour of *current*: the first state cube after its cube (``forward``) or the last one
    before it, on the wavelength shown now. A state cube without that wavelength, or where the frame is skipped, is passed over."""
    ordered = sorted(set(state_cubes))
    candidates = ordered[bisect_right(ordered, current[0]):] if forward else ordered[:bisect_left(ordered, current[0])][::-1]
    for cube in candidates:
        if current[1] in wavelengths_by_cube.get(cube, ()) and not is_skipped(cube, current[1]):
            return (cube, current[1])
    return None


def first_frame(
    cubes: Sequence[int],
    wavelengths_by_cube: Mapping[int, Sequence[float]],
    current: Frame,
    settings: SlideshowSettings,
    is_skipped: Callable[[int, float], bool] = lambda _c, _w: False,
) -> Frame | None:
    """Where the show starts (and restarts, with Repeat): the first frame of its range that is not skipped.
    Searches lazily: it stops at the first match."""
    if settings.mode in STATE_MODES:  # `cubes` are the state cubes
        return _state_frame(cubes, wavelengths_by_cube, (min(cubes, default=0) - 1, current[1]), is_skipped, forward=True)
    for cube in _range_cubes(cubes, current, settings):
        for wavelength in _wavelengths_of(cube, wavelengths_by_cube, current, settings):
            if not is_skipped(cube, wavelength):
                return (cube, wavelength)
    return None


def next_frame(
    cubes: Sequence[int],
    wavelengths_by_cube: Mapping[int, Sequence[float]],
    current: Frame,
    settings: SlideshowSettings,
    is_skipped: Callable[[int, float], bool] = lambda _c, _w: False,
) -> Frame | None:
    """The frame after *current*, or ``None`` when the show is over (the end was
    reached and Repeat is off, or there is nothing to show).

    *current* need not be inside the range (the user may have dragged a slider
    onto an ignored frame): the next frame is then the first one after it.

    **Lazy** (2026-10-14): it starts at the current cube and stops at the first frame that is not
    skipped. The first version built every frame of the dataset (8478 on the maintainer's data) and
    sorted them on every step, which on top of slow dataset queries cost ~280 ms per Play step."""
    if settings.mode in STATE_MODES:  # `cubes` are the state cubes
        frame = _state_frame(cubes, wavelengths_by_cube, current, is_skipped, forward=True)
        if frame is None and settings.repeat:
            first = first_frame(cubes, wavelengths_by_cube, current, settings, is_skipped)
            if first is not None and first != current:
                return first
        return frame
    in_range = _range_cubes(cubes, current, settings)
    try:
        start = list(in_range).index(current[0]) if len(in_range) != 1 else 0
        known = in_range[start] == current[0]
    except ValueError:  # the current cube is not part of the range: nothing lies after it
        start, known = len(in_range), False
    if known:
        for position in range(start, len(in_range)):
            cube = in_range[position]
            for wavelength in _wavelengths_of(cube, wavelengths_by_cube, current, settings):
                if cube == current[0] and wavelength <= current[1]:
                    continue
                if not is_skipped(cube, wavelength):
                    return (cube, wavelength)
    if settings.repeat:
        first = first_frame(cubes, wavelengths_by_cube, current, settings, is_skipped)
        if first is not None and first != current:
            return first
    return None


def previous_frame(
    cubes: Sequence[int],
    wavelengths_by_cube: Mapping[int, Sequence[float]],
    current: Frame,
    settings: SlideshowSettings,
    is_skipped: Callable[[int, float], bool] = lambda _c, _w: False,
) -> Frame | None:
    """The frame before *current* in the order of ``settings.mode``, or ``None`` at the start. The mirror of `next_frame`
    for a manual step back: it never wraps (Repeat is not looked at) and does not stop at the End setting. *cubes* are
    ascending (as `DatasetModule.spectral_cubes` gives them); in the ROI-states mode they are the state cubes."""
    if settings.mode in STATE_MODES:
        return _state_frame(cubes, wavelengths_by_cube, current, is_skipped, forward=False)
    ordered = list(cubes)
    for position in range(bisect_right(ordered, current[0]) - 1, -1, -1):
        cube = ordered[position]
        for wavelength in tuple(_wavelengths_of(cube, wavelengths_by_cube, current, settings))[::-1]:
            if cube == current[0] and wavelength >= current[1]:
                continue
            if not is_skipped(cube, wavelength):
                return (cube, wavelength)
    return None


class _LazyWavelengths:
    """`{cube: wavelengths}` that asks the dataset only for the cubes the search actually reaches."""

    def __init__(self, dataset: DatasetModule) -> None:
        self._dataset = dataset

    def get(self, cube: int, default: Sequence[float] = ()) -> Sequence[float]:
        return self._dataset.wavelengths_for_cube(cube) or default


class SlideshowController(QObject):
    playing_changed = pyqtSignal(bool)
    step_made = pyqtSignal(float)  # seconds the selection signal handlers took (for the playback stats)

    def __init__(
        self,
        dataset: DatasetModule,
        selection: SelectionModule,
        frame_roles: FrameRolesModule | None,
        parent: QObject | None = None,
        *,
        state_cubes: Callable[[str], Sequence[int]] | None = None,
    ) -> None:
        """``state_cubes(mode)``: the cubes a states mode (`STATE_MODES`) steps through - the marks of the ROIs row or of the
        Mask row under the Cube slider - ascending. Without it those modes have nothing to visit."""
        super().__init__(parent)
        self._dataset = dataset
        self._selection = selection
        self._roles = frame_roles
        self._state_cubes = state_cubes
        self._settings = SlideshowSettings()
        self._playing = False
        self._awaiting_since: float | None = None  # a step was made and its image is not on screen yet
        self._step_due = False  # the timer fired while a frame was still on its way: step the moment it is shown
        self._timer = QTimer(self)
        # Single-shot, re-armed after each step: a slow render delays the next
        # step instead of letting steps queue up behind it.
        self._timer.setSingleShot(True)
        # Precise: Qt's default coarse timer may fire up to ~5 % (and on Windows a whole ~15 ms clock tick) late.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._step)
        dataset.dataset_loaded.connect(lambda _ds: self.stop())
        dataset.dataset_cleared.connect(self.stop)

    # -- state --------------------------------------------------------------------

    def settings(self) -> SlideshowSettings:
        return self._settings

    def set_settings(self, settings: SlideshowSettings) -> None:
        self._settings = settings  # takes effect from the next step, also while playing

    def is_playing(self) -> bool:
        return self._playing  # not the timer: it is inactive while a step's handlers run

    def interval_seconds(self) -> float:
        """Time between two steps at the current speed."""
        base = self._dataset.mean_frame_interval_seconds() or DEFAULT_INTERVAL_S
        return max(base / max(self._settings.speed, 1), MIN_INTERVAL_S)

    # -- commands -----------------------------------------------------------------

    def _frame_data(self, mode: str | None = None) -> tuple[Sequence[int], dict[int, tuple[float, ...]], Frame]:
        """The cubes to step through (in a states mode: the cubes with a mark), each cube's wavelengths, the current frame."""
        mode = mode or self._settings.mode
        if mode in STATE_MODES:
            cubes = tuple(self._state_cubes(mode)) if self._state_cubes is not None else ()
        else:
            cubes = self._dataset.spectral_cubes()
        return cubes, _LazyWavelengths(self._dataset), (
            int(self._selection.current_cube()), float(self._selection.current_wavelength())
        )

    def _is_skipped(self, cube: int, wavelength: float) -> bool:
        if self._roles is None:
            return False
        role = self._roles.role_of(cube, wavelength)
        return (role == ROLE_IGNORED and self._settings.skip_ignored) or (role == ROLE_DARK and self._settings.skip_dark)

    def play(self) -> None:
        """Start from the current frame; from the end of the range, start over."""
        if self._playing:
            return
        cubes, by_cube, current = self._frame_data()
        if next_frame(cubes, by_cube, current, self._settings, self._is_skipped) is None:
            start = first_frame(cubes, by_cube, current, self._settings, self._is_skipped)
            if start is None or start == current:
                return  # nothing (else) to show
            self._show(start)
        self._playing = True
        self._awaiting_since = None
        self._step_due = False
        self._timer.start(round(self.interval_seconds() * 1000))
        self.playing_changed.emit(True)

    def stop(self) -> None:
        was_playing = self._playing
        self._playing = False
        self._awaiting_since = None
        self._step_due = False
        self._timer.stop()
        if was_playing:
            self.playing_changed.emit(False)

    def toggle(self) -> None:
        self.stop() if self._playing else self.play()

    def step(self, direction: int, mode: str | None = None) -> bool:
        """One manual step: the frame after (``direction > 0``) or before the one shown, in the order of `mode` (default:
        the show's own). Returns whether the selection moved; at an end it does not (manual steps never wrap and ignore
        the End setting). The Playing / Paused state is left alone: a step while playing just moves the show on from there."""
        settings = replace(self._settings, mode=mode or self._settings.mode, repeat=False, end=END_DATASET)
        cubes, by_cube, current = self._frame_data(settings.mode)
        find = next_frame if direction > 0 else previous_frame
        frame = find(cubes, by_cube, current, settings, self._is_skipped)
        if frame is None or frame == current:
            return False
        self._show(frame)
        return True

    def _show(self, frame: Frame) -> None:
        cube, wavelength = frame
        if cube != int(self._selection.current_cube()):
            self._selection.set_cube(cube)
        self._selection.set_wavelength(wavelength)

    def upcoming_frames(self, count: int) -> list[Frame]:
        """The next *count* frames the show will visit after the one selected now, in order
        (what the read-ahead should load). Empty when not playing."""
        if not self._playing:
            return []
        cubes, by_cube, current = self._frame_data()
        frames: list[Frame] = []
        for _ in range(count):
            nxt = next_frame(cubes, by_cube, current, self._settings, self._is_skipped)
            if nxt is None or nxt in frames:
                break
            frames.append(nxt)
            current = nxt
        return frames

    pipelined = True
    """Step the moment a frame has been handed to Qt (and render the next one while Qt paints this one),
    instead of polling every 10 ms and only after the paint. Off = the earlier, strictly serial behaviour."""

    def frame_displayed(self) -> None:
        """The panel put an image on screen: the next step may go ahead. With `pipelined` a step that was
        already due runs *now*, inside the call, i.e. before Qt gets to paint the image just set: the
        render thread can then work on the next frame during that paint (~30 ms of GUI time)."""
        self._awaiting_since = None
        if self._step_due and self._playing:
            self._step_due = False
            self._timer.stop()  # the watchdog set in `_step`
            self._advance()

    def _step(self) -> None:
        """Timer slot. **Back-pressure:** do not step again until the previous step's image has
        been shown (or MAX_WAIT_FOR_FRAME_S passed). Without it a GUI that needs 200 ms per frame
        was fed a step every 67 ms and the screen ran 16 s behind (measured 2026-10-14); with it
        the frame rate drops to what the machine can show and the lag stays about one frame."""
        if self._awaiting_since is not None and time.perf_counter() - self._awaiting_since < MAX_WAIT_FOR_FRAME_S:
            if self.pipelined:
                # No polling: `frame_displayed` steps as soon as the frame is shown. The timer stays as a watchdog
                # for a step that shows nothing (MAX_WAIT_FOR_FRAME_S).
                self._step_due = True
                self._timer.start(round(MAX_WAIT_FOR_FRAME_S * 1000))
            else:
                self._timer.start(_POLL_MS)
            return
        self._step_due = False
        self._advance()

    def _advance(self) -> None:
        began = time.perf_counter()
        cubes, by_cube, current = self._frame_data()
        frame = next_frame(cubes, by_cube, current, self._settings, self._is_skipped)
        if frame is None:
            self.stop()  # the end, without Repeat
            return
        started = time.perf_counter()
        self._show(frame)
        self._awaiting_since = time.perf_counter()
        self.step_made.emit(time.perf_counter() - started)
        # Fixed rate: the next step is due one interval after THIS step BEGAN, not after it finished.
        # Re-arming a full interval after the end made every cycle `step cost + interval` (a cube change
        # costs ~20 ms on a big dataset): the maintainer got 9.4 frames/s for 14.8 asked while the GUI
        # thread was 71 % idle (testing5, 2026-10-14).
        remaining = self.interval_seconds() - (time.perf_counter() - began)
        self._timer.start(max(round(remaining * 1000), 0))
