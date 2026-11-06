"""``SensorgramAxis``: the Sensorgram's x axis and the baseline switch, in one place for every panel that shows the same numbers (2026-11-05).

Moved out of ``panels/sensorgram/panel.py`` (the "step 2" of ``docs/sensorgram_panel_split_2026-10-26.md``) without a change of behaviour, because a second panel,
the ROI / Groups table's step columns, must compute *exactly* the numbers the Sensorgram draws, and those depend on this axis:

- **The x axis**: time in seconds (the cube's start) or the cube number, following the app-wide Cube / Time choice (`CubeDisplayModule`); one value per cube of the
  scan file, NaN for a cube without timing. Cached until the file or the mode changes.
- **The baseline window** (`StatisticsSettings.baseline_window_start` / ``_end``) is given in the units of that axis, so the same window means different cubes on
  another axis. On a switch of the mode the window keeps its two cubes (cubes 2 - 4 become the seconds those cubes start at, and back; a round trip gives the window
  back), snapped to data points; where it cannot be carried over it goes to where "Subtract" would put it (the first tenth of the axis).
- **The Subtract switch** (`set_baseline_enabled`): turning it on puts the window at its default when none was chosen yet. Dragging or typing a window only stores it
  (`set_baseline_window`), snapped to data points.

Everything is written to ``AnalysisSettingsModule`` (the one owner of the statistics settings); this object keeps only the cached x. ``axis_changed`` tells a view that the
x values changed (the mode switch, after the window has been carried over), so it can redraw even when no setting changed.

It lives in ``selection/`` (it started in ``analysis/``, moved 2026-11-06): it reads the dataset's timing and the app-wide Cube / Time choice, and the import-layer contract
(``.importlinter``) lets ``analysis`` import neither. Not re-exported from the package, like ``ExperimentPlanModule``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from .cube_display_module import MODE_TIME, CubeDisplayModule

if TYPE_CHECKING:  # only used in annotations
    from ..analysis.scan_results import ScanResultsModule
    from ..analysis.settings_module import AnalysisSettingsModule
    from ..dataset.module import DatasetModule


def snap_to_grid(grid: np.ndarray, value: float) -> float:
    """The point of ``grid`` (sorted, finite, may be empty) nearest to ``value``; halfway between two points the higher one. An empty grid leaves the value alone."""
    if grid.size == 0:
        return float(value)
    above = int(np.searchsorted(grid, value))
    if above == 0:
        return float(grid[0])
    if above >= grid.size:
        return float(grid[-1])
    below_gap, above_gap = value - grid[above - 1], grid[above] - value
    return float(grid[above] if above_gap <= below_gap else grid[above - 1])


def snap_window(grid: np.ndarray, low: float, high: float) -> tuple[float, float]:
    """Both edges of a window on the grid, in order. Two edges that land on one point are pulled one point apart (a baseline of a single point is no window)."""
    first, second = sorted((snap_to_grid(grid, low), snap_to_grid(grid, high)))
    if first == second and grid.size >= 2:
        index = int(np.searchsorted(grid, first))
        if index + 1 < grid.size:
            second = float(grid[index + 1])
        else:
            first = float(grid[index - 1])
    return first, second


class SensorgramAxis(QObject):
    axis_changed = pyqtSignal()
    """The x values changed (Cube / Time switched, or the dataset changed) and the baseline window has been carried over. Fires whether or not a setting changed."""

    def __init__(
        self,
        scan_results: ScanResultsModule,
        dataset: DatasetModule,
        cube_display: CubeDisplayModule,
        settings: AnalysisSettingsModule,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan_results
        self._dataset = dataset
        self._cube_display = cube_display
        self._settings = settings
        self._x_cache: tuple[tuple, np.ndarray] | None = None
        self._range_fallback: Callable[[], tuple[float, float]] | None = None
        scan_results.results_changed.connect(self._forget)
        cube_display.mode_changed.connect(lambda _mode: self._on_mode_changed())

    @classmethod
    def of(
        cls, scan_results: ScanResultsModule, dataset: DatasetModule, cube_display: CubeDisplayModule, settings: AnalysisSettingsModule
    ) -> SensorgramAxis:
        """The one axis of these statistics settings: made on the first call, found again after that (it is a child of the settings module). There must be only one, because it
        *rewrites* the settings when Cube / Time is switched (the baseline window moves to the new unit); two of them would each move it, and the second would move the
        already moved window. The app, the Sensorgram and the ROI table all ask for it here."""
        existing = settings.findChild(cls)
        return existing if existing is not None else cls(scan_results, dataset, cube_display, settings, parent=settings)

    def set_range_fallback(self, provider: Callable[[], tuple[float, float]] | None) -> None:
        """Where the default baseline window goes when the axis has fewer than two points: the visible x range of the plot (the Sensorgram gives it; a table has none)."""
        self._range_fallback = provider

    # -- the x axis -------------------------------------------------------------------------------------------------------------

    def uses_time(self) -> bool:
        return self._cube_display.effective_mode() == MODE_TIME

    def label_and_x(self) -> tuple[str, np.ndarray]:
        """(axis label, x per cube of the file): time in seconds, or the cube number. Cached until the file or the mode changes."""
        stored = self._scan.stored()
        if stored is None:
            return "Cube (#)", np.empty(0)
        use_time = self.uses_time()
        key = (id(stored), use_time)
        if self._x_cache is None or self._x_cache[0] != key:
            if use_time:
                seconds = [self._dataset.cube_start_seconds(c) for c in stored.cube_indices]
                x = np.array([np.nan if s is None else float(s) for s in seconds])
            else:
                x = np.array(stored.cube_indices, dtype=np.float64)
            self._x_cache = (key, x)
        return ("Time (s)" if use_time else "Cube (#)"), self._x_cache[1]

    def x(self) -> np.ndarray:
        return self.label_and_x()[1]

    def _forget(self) -> None:
        self._x_cache = None

    # -- the baseline window ----------------------------------------------------------------------------------------------------

    def default_baseline_window(self) -> tuple[float, float]:
        """The first tenth of the axis, on data points."""
        x = self.x()
        finite = x[np.isfinite(x)]
        if finite.size < 2:
            low, high = self._range_fallback() if self._range_fallback is not None else (0.0, 1.0)
        else:
            low, high = float(finite.min()), float(finite.max())
        return self.snap_window(low, low + 0.1 * (high - low))

    def snap_window(self, low: float, high: float) -> tuple[float, float]:
        """The window with both edges on a data point of the x axis (cube number, or the cube's start time)."""
        x = self.x()
        return snap_window(np.unique(x[np.isfinite(x)]), low, high)

    def set_baseline_enabled(self, on: bool) -> None:
        """The Subtract switch. Turning it on puts the window at its default when none was chosen yet."""
        current = self._settings.statistics_settings()
        if on:
            window = (current.baseline_window_start, current.baseline_window_end)
            if window[0] is None or window[1] is None:
                window = self.default_baseline_window()
            self._settings.set_statistics_settings(replace(current, baseline_enabled=True, baseline_window_start=window[0], baseline_window_end=window[1]))
        else:
            self._settings.set_statistics_settings(replace(current, baseline_enabled=False))

    def set_baseline_window(self, low: float, high: float) -> None:
        """Remember where the baseline selector is (snapped to data points, as a typed value is as well). Whether the traces are shifted by it is the Subtract
        switch's business (the selector can be dragged before, or without, subtracting)."""
        low, high = self.snap_window(low, high)
        self._settings.set_statistics_settings(replace(self._settings.statistics_settings(), baseline_window_start=low, baseline_window_end=high))

    # -- the Cube / Time switch -------------------------------------------------------------------------------------------------

    def _on_mode_changed(self) -> None:
        """The Cube / Time choice changed: the x values change, and so does the unit of the baseline window. The window keeps its cubes (cubes 2 - 4
        become the seconds those cubes start at, and back), subtracting or not."""
        previous = self._x_cache  # (key, x) of the axis the stored window is in
        self._x_cache = None
        stats = self._settings.statistics_settings()
        if stats.baseline_window_start is not None and stats.baseline_window_end is not None:
            window = self._window_on_new_axis(previous, stats.baseline_window_start, stats.baseline_window_end)
            if window is None:  # nothing to carry it over from: where Subtract would put it on the new axis
                window = self.default_baseline_window()
            self._settings.set_statistics_settings(replace(stats, baseline_window_start=window[0], baseline_window_end=window[1]))
        self.axis_changed.emit()

    def _window_on_new_axis(self, previous: tuple | None, low: float, high: float) -> tuple[float, float] | None:
        """The window (low, high), given in the unit of the axis `previous`, put on the cubes' x values of the current axis: the same two cubes, so
        a round trip Cube -> Time -> Cube gives the window back. Unchanged when the unit did not change. None when it cannot be carried over (no
        earlier axis, another scan, a cube without a start time)."""
        if previous is None:
            return None
        (stored_id, was_time), old_x = previous
        new_x = self.x()
        if stored_id != id(self._scan.stored()) or old_x.size == 0 or old_x.size != new_x.size:
            return None
        if was_time == self.uses_time():
            return float(low), float(high)
        finite = np.flatnonzero(np.isfinite(old_x))
        if finite.size == 0:
            return None
        moved = []
        for edge in (low, high):
            cube_position = int(finite[int(np.argmin(np.abs(old_x[finite] - edge)))])  # the data point the edge stands on
            if not np.isfinite(new_x[cube_position]):
                return None
            moved.append(float(new_x[cube_position]))
        return self.snap_window(moved[0], moved[1])
