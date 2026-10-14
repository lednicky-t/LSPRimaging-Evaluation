"""``CubeDisplayModule`` - how a spectral cube is *named* on screen: by its
number ("#") or by the time its first frame was acquired ("Time").

Ported from the stable app's Cube/Time toggle (`MainWindow.
_toggle_cube_time_display_mode` / `_cube_time_display_text_for` /
`_format_elapsed_seconds`), with one deliberate scope-down: only the "first
frame" timestamp rule is kept; the stable app's last / midpoint cycle is not
ported (maintainer decision, 2026-10-11).

Purely a display preference (a cosmetic change in the CLAUDE.md sense): it
never changes a stored value, a selection, or a computation - every panel that
shows a cube asks `label_for()` and redraws on `mode_changed`. Like the px/um
unit, it is meant to be one app-wide setting offered from several places
(today: the right-click menu of the Image panel's sliders).

Time mode is only meaningful when the dataset has per-frame timing;
`time_available()` says so, and `label_for()` falls back to the plain cube
number for any cube without a timing entry.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..dataset.module import DatasetModule
from ..diagnostics import instrumented

MODE_CUBE = "cube"
MODE_TIME = "time"
_MODES = (MODE_CUBE, MODE_TIME)


def format_elapsed_seconds(seconds: float, decimals: int = 0) -> str:
    """H:MM:SS (or M:SS under an hour); a negative value keeps its sign. ``decimals`` adds that many
    digits of a second (2 = tens of ms, "1:05.24"); the rounding happens on the whole value, so 59.999 s
    reads "1:00.00", never "0:60.00"."""
    sign = "-" if seconds < 0 else ""
    scale = 10**decimals
    whole, fraction = divmod(int(round(abs(seconds) * scale)), scale)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    tail = f".{fraction:0{decimals}d}" if decimals else ""
    if hours:
        return f"{sign}{hours:d}:{minutes:02d}:{secs:02d}{tail}"
    return f"{sign}{minutes:d}:{secs:02d}{tail}"


class CubeDisplayModule(QObject):
    mode_changed = pyqtSignal(str)

    def __init__(self, dataset: DatasetModule, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._mode = MODE_CUBE
        # A different dataset may have no timing at all; Time mode on it would
        # silently show plain numbers, so the displays are told to re-read.
        dataset.dataset_loaded.connect(lambda _ds: self.mode_changed.emit(self.effective_mode()))
        dataset.dataset_cleared.connect(lambda: self.mode_changed.emit(self.effective_mode()))

    def mode(self) -> str:
        """The mode the user chose (what is saved)."""
        return self._mode

    def effective_mode(self) -> str:
        """The mode actually shown: Time falls back to Cube without timing."""
        return MODE_TIME if self._mode == MODE_TIME and self.time_available() else MODE_CUBE

    def time_available(self) -> bool:
        cubes = self._dataset.spectral_cubes()
        return bool(cubes) and any(self._dataset.cube_start_seconds(cube) is not None for cube in cubes[:1] + cubes[-1:])

    @instrumented("CubeDisplayModule.set_mode")
    def set_mode(self, mode: str) -> None:
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        self.mode_changed.emit(self.effective_mode())

    def label_for(self, cube_index: int) -> str:
        """The text naming `cube_index` in the current effective mode."""
        if self.effective_mode() == MODE_TIME:
            seconds = self._dataset.cube_start_seconds(int(cube_index))
            if seconds is not None:
                return format_elapsed_seconds(seconds)
        return str(int(cube_index))

    def axis_title(self) -> str:
        """The cube slider's title: the unit follows the mode."""
        return "Cube (t)" if self.effective_mode() == MODE_TIME else "Cube (#)"
