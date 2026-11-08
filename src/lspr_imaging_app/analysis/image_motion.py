"""Image motion per experiment step (2026-11-06; check 7 of the diagnostics, `step_diagnostics.py`). Pure numpy / OpenCV, no Qt.

How far the image moves between cubes, measured by phase correlation of one plane (one wavelength) of every cube against the first cube of the step
(`cv2.phaseCorrelate`, Hann window, sub-pixel). A sample ROI and its reference ROI sit at fixed pixels, so image motion shifts what they cover: a
jitter adds noise, a slow drift adds a trend. On the 2026-11-06 data the cube-to-cube jitter was ~0.01 px in every step and one step drifted
0.3 px (it had 2x the pixel variance of the others).

Reads images, so it runs on request (the caller hands in a ``read_plane(cube) -> 2-D array``, off the GUI thread) and is not part of the scan.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

JITTER_LIMIT_PX = 0.05  # cube-to-cube motion SD above this is flagged
DRIFT_LIMIT_PX = 0.1  # total motion over a step above this is flagged


@dataclass(frozen=True)
class StepMotion:
    """One step: the shift of every cube against the step's first (``shifts_px`` ``[cube, (x, y)]``), the cube-to-cube jitter (SD of the differences / sqrt 2)
    and the total range, per axis, in pixels."""

    label: str
    cube_indices: tuple[int, ...]
    shifts_px: np.ndarray
    jitter_px: tuple[float, float]
    range_px: tuple[float, float]

    @property
    def flagged(self) -> list[str]:
        out = []
        if max(self.jitter_px) > JITTER_LIMIT_PX:
            out.append(f"jitter {max(self.jitter_px):.3f} px (limit {JITTER_LIMIT_PX} px)")
        if max(self.range_px) > DRIFT_LIMIT_PX:
            out.append(f"drift {max(self.range_px):.2f} px over the step (limit {DRIFT_LIMIT_PX} px)")
        return out


def _shift_against(first: np.ndarray, window: np.ndarray, frame: np.ndarray) -> tuple[float, float]:
    """(dx, dy) of ``frame`` against ``first`` (float32, same shape), in pixels."""
    import cv2

    image = np.asarray(frame, dtype=np.float32)
    if image.shape != first.shape:
        raise ValueError("Every frame must have the same shape.")
    (dx, dy), _response = cv2.phaseCorrelate(first, image, window)
    return float(dx), float(dy)


def _hann(first: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.createHanningWindow(first.shape[::-1], cv2.CV_32F)


def frame_shifts(frames: Sequence[np.ndarray]) -> np.ndarray:
    """``[frame, (x, y)]``: the shift of each frame against the first, in pixels (the first is 0, 0). Frames must share one shape."""
    if not frames:
        return np.empty((0, 2))
    first = np.asarray(frames[0], dtype=np.float32)
    window = _hann(first)
    out = np.zeros((len(frames), 2))
    for i, frame in enumerate(frames[1:], start=1):
        out[i] = _shift_against(first, window, frame)
    return out


def step_motion(label: str, cube_indices: Sequence[int], read_plane: Callable[[int], np.ndarray],
                cancelled: Callable[[], bool] | None = None, on_plane: Callable[[], None] | None = None) -> StepMotion | None:
    """The motion of one step (None when it has fewer than three cubes or was cancelled). ``read_plane(cube)`` returns the chosen plane of that cube.
    The planes are read one at a time and only the step's first is kept (a step of 100 cubes would otherwise hold 200 MB of images); ``on_plane`` is called after each."""
    cubes = [int(c) for c in cube_indices]
    if len(cubes) < 3:
        return None
    if cancelled is not None and cancelled():
        return None
    first = np.asarray(read_plane(cubes[0]), dtype=np.float32)
    window = _hann(first)
    shifts = np.zeros((len(cubes), 2))
    if on_plane is not None:
        on_plane()
    for i, c in enumerate(cubes[1:], start=1):
        if cancelled is not None and cancelled():
            return None
        shifts[i] = _shift_against(first, window, read_plane(c))
        if on_plane is not None:
            on_plane()
    diffs = np.diff(shifts, axis=0)
    jitter = diffs.std(axis=0, ddof=1) / np.sqrt(2.0)
    span = np.ptp(shifts, axis=0)
    return StepMotion(label, tuple(cubes), shifts, (float(jitter[0]), float(jitter[1])), (float(span[0]), float(span[1])))
