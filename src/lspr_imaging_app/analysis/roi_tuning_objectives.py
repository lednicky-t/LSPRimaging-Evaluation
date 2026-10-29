"""ROI position tuning, step C1 (2026-10-29): what to rank the lattice positions by. Pure (numpy only): no Qt, no files, nothing stored.

``roi_tuning.lattice_objectives`` gives, for every lattice position and ROI, the statistical numbers of the baseline and the signal step (means, SDs, the
detrended noise, ``delta``). This file turns them into **one number per (position, ROI)** for the objective the user picks in the ribbon, and says which
position is best for each ROI. Nothing here scans or fits: switching the objective is a cheap recalculation on numbers already in RAM.

* :data:`OBJECTIVES` - the registry, one :class:`Objective` per entry (key, label, tooltip, unit, direction, function). A new objective is one entry here.
* :func:`rank` - an objective applied to a :class:`~.roi_tuning.LatticeObjectives`: the values, the best position of every ROI, a 0..1 score per ROI for
  colouring, and the improvement of the best position over the current centre.
* :func:`smooth_noise` - the noise as a smooth surface over the lattice, which is what the default objective divides by.

Why the default (``snr_smooth``) does not divide by the measured noise of each position: the noise of a position comes from the ~30 cubes of the baseline step,
so it is only known to about +/-13 %. Choosing the position with the best ``|delta| / noise`` on such numbers mostly picks the luckiest noise value (measured
2026-10-28, 7x7 lattice, 8 ROIs: +23 % promised, about 0 to +11 % real). A quadratic surface fitted to ``log(noise)`` over the whole lattice of one ROI averages
that scatter out while keeping a real trend (a ROI that is really noisier on one side). ``snr_raw`` stays in the menu so the difference can be seen.

Conventions: every value is in the objective's own unit (nm, or dimensionless), **larger is better only if the objective says so** (``higher_is_better``);
the ranking works on the *oriented* value (sign flipped for "lower is better"), so "best", "improvement" and the scores read the same for every objective.
The current centre (offset 0, 0) is always kept when nothing beats it: a ROI is only moved for a strictly better position.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..roi.toolbox import POSITION_DECIMALS
from .roi_tuning import LatticeObjectives

SMOOTH_NOISE_TERMS = 6
"""Coefficients of the quadratic surface: 1, dx, dy, dx^2, dx*dy, dy^2. A ROI needs at least this many positions with a noise value (and a lattice that
determines them) for ``snr_smooth``; with fewer it has no value, never a silent fall-back to the raw noise."""


def centre_index(offsets: np.ndarray) -> int:
    """Row of ``offsets`` that is the current position, (0, 0). ``ValueError`` if the lattice has none (it is centred by construction)."""
    offsets = np.asarray(offsets, dtype=np.float64)
    hits = np.flatnonzero((offsets == 0.0).all(axis=1))
    if hits.size == 0:
        raise ValueError("the lattice has no position at offset (0, 0): the current centre is needed as the reference")
    return int(hits[0])


def smooth_noise(offsets: np.ndarray, noise: np.ndarray) -> np.ndarray:
    """The noise of every ROI as a smooth function of the position: ``exp`` of a quadratic in (dx, dy) least-squares fitted to ``log(noise)``.

    ``offsets``: ``[point, 2]`` px. ``noise``: ``[point, roi]`` (nm), NaN or <= 0 = no value. Returns ``[point, roi]``; NaN for a ROI with fewer than
    :data:`SMOOTH_NOISE_TERMS` usable positions or whose positions do not determine the surface (e.g. all on a line), and NaN at the positions that had no
    noise value of their own (the surface is evaluated only where there was a measurement). Each ROI is fitted on its own, so a ROI's surface never depends on
    the other ROIs."""
    offsets = np.asarray(offsets, dtype=np.float64)
    noise = np.asarray(noise, dtype=np.float64)
    if noise.ndim != 2 or noise.shape[0] != len(offsets):
        raise ValueError("noise must be [point, roi] with one row per offset")
    scale = float(np.max(np.abs(offsets))) or 1.0  # offsets in units of the lattice extent: a well-conditioned fit whatever the pitch
    u, v = offsets[:, 0] / scale, offsets[:, 1] / scale
    design = np.column_stack([np.ones_like(u), u, v, u * u, u * v, v * v])
    out = np.full(noise.shape, np.nan)
    for j in range(noise.shape[1]):
        usable = np.isfinite(noise[:, j]) & (noise[:, j] > 0)
        if usable.sum() < SMOOTH_NOISE_TERMS:
            continue
        coefficients, _, rank, _ = np.linalg.lstsq(design[usable], np.log(noise[usable, j]), rcond=None)
        if rank < SMOOTH_NOISE_TERMS:
            continue
        out[usable, j] = np.exp(design[usable] @ coefficients)
    return out


_WINDOW_X = np.tile(np.array([-1.0, 0.0, 1.0]), 3)  # the 3 x 3 window around a lattice point, in units of the pitch: x fastest, then y (the lattice's own order)
_WINDOW_Y = np.repeat(np.array([-1.0, 0.0, 1.0]), 3)
_WINDOW_PINV = np.linalg.pinv(  # least-squares quadratic through the 9 points: coefficients (1, x, y, x^2, x*y, y^2) = _WINDOW_PINV @ values; constant, so computed once
    np.column_stack([np.ones(9), _WINDOW_X, _WINDOW_Y, _WINDOW_X**2, _WINDOW_X * _WINDOW_Y, _WINDOW_Y**2])
)
MAX_REFINE_PITCH = 1.0
"""How far from the best lattice point the interpolated position may lie, in pitches: the extent of the 3 x 3 window the surface was fitted on. Inside that window
the fit interpolates; beyond it, it would extrapolate, and a top that far away means the surface is nearly a ramp there, so the lattice point stays."""


def refine_best(oriented: np.ndarray, best: np.ndarray, size: int, pitch_px: float) -> np.ndarray:
    """Where between the lattice points each ROI's objective peaks, as an offset **from its best lattice point** in pixels (``float64 [roi, 2]``, (dx, dy)).

    A quadratic surface ``c0 + cx x + cy y + cxx x^2 + cxy x y + cyy y^2`` is fitted by least squares to the 3 x 3 lattice points around the best one (a window that
    is pushed inward at the edge of the lattice so that it stays inside it) and its top is taken. It is meant to be simple and fast: one matrix product for all ROIs.
    ``oriented``: ``[position, roi]``, larger is better (``Ranking`` flips "lower is better"). ``best``: lattice row of each ROI's best position, -1 = none. A ROI keeps
    its lattice point (offset 0, 0) when:

    * the lattice is smaller than 3 x 3, or a value in the window is missing;
    * the fitted surface has no top (it is not curved downward in every direction: a ramp or a saddle - typical when the best point is at the edge because the
      peak lies beyond the lattice);
    * the top is more than :data:`MAX_REFINE_PITCH` of a pitch from the best point (outside the window the surface was fitted on: extrapolation), or outside the lattice
      (never beyond what was measured). On a surface that is tilted relative to the lattice the best lattice point is not always the one nearest the top, so the
      limit is the window, not half a pitch.
    """
    n_rois = oriented.shape[1]
    out = np.zeros((n_rois, 2))
    ranked = np.flatnonzero(best >= 0)
    if size < 3 or pitch_px <= 0 or ranked.size == 0:
        return out
    ix, iy = best[ranked] % size, best[ranked] // size
    ix0, iy0 = np.clip(ix - 1, 0, size - 3), np.clip(iy - 1, 0, size - 3)  # the window's lowest column and row
    columns = ix0[:, None] + (_WINDOW_X + 1).astype(int)[None, :]
    rows = iy0[:, None] + (_WINDOW_Y + 1).astype(int)[None, :]
    values = oriented[rows * size + columns, ranked[:, None]]  # [ranked roi, 9]
    complete = np.isfinite(values).all(axis=1)
    cx, cy, cxx, cxy, cyy = (np.where(complete[:, None], values, 0.0) @ _WINDOW_PINV.T)[:, 1:].T
    det = 4.0 * cxx * cyy - cxy**2
    has_top = complete & (cxx < 0) & (cyy < 0) & (det > 0)  # negative definite
    safe = np.where(has_top, det, 1.0)
    top_x, top_y = (-2.0 * cyy * cx + cxy * cy) / safe, (cxy * cx - 2.0 * cxx * cy) / safe  # window coordinates (centre of the window = 0), in pitches
    step_x, step_y = top_x - (ix - (ix0 + 1)), top_y - (iy - (iy0 + 1))  # from the best lattice point
    inside = (np.abs(step_x) <= MAX_REFINE_PITCH) & (np.abs(step_y) <= MAX_REFINE_PITCH)
    inside &= (ix + step_x >= 0) & (ix + step_x <= size - 1) & (iy + step_y >= 0) & (iy + step_y <= size - 1)
    accepted = has_top & inside
    out[ranked[accepted], 0] = step_x[accepted] * pitch_px
    out[ranked[accepted], 1] = step_y[accepted] * pitch_px
    return out


def _signal(obj: LatticeObjectives) -> np.ndarray:
    return np.abs(obj.delta)


def _noise(obj: LatticeObjectives) -> np.ndarray:
    return np.where(np.isfinite(obj.noise) & (obj.noise > 0), obj.noise, np.nan)


def _snr_raw(obj: LatticeObjectives) -> np.ndarray:
    return obj.snr


def _snr_smooth(obj: LatticeObjectives) -> np.ndarray:
    smooth = smooth_noise(obj.offsets, obj.noise)
    out = np.full(smooth.shape, np.nan)
    np.divide(np.abs(obj.delta), smooth, out=out, where=np.isfinite(obj.delta) & np.isfinite(smooth) & (smooth > 0))
    return out


@dataclass(frozen=True)
class Objective:
    """One entry of the "Rank by" menu."""

    key: str
    label: str
    tooltip: str
    unit: str  # "nm" or "" (dimensionless): shown beside the numbers
    higher_is_better: bool
    compute: Callable[[LatticeObjectives], np.ndarray]  # -> float64 [point, roi], NaN = no value

    def values(self, obj: LatticeObjectives) -> np.ndarray:
        return np.asarray(self.compute(obj), dtype=np.float64)


OBJECTIVES: tuple[Objective, ...] = (
    Objective(
        "snr_smooth", "S/N (smoothed noise)",
        "|signal step mean - baseline step mean| divided by the baseline noise read from a smooth surface fitted over this ROI's lattice. "
        "The noise of one position is only known to about 13 %, so dividing by it directly mostly picks the luckiest position.",
        "", True, _snr_smooth,
    ),
    Objective(
        "snr_raw", "S/N (measured noise)",
        "|signal step mean - baseline step mean| divided by the baseline noise measured at each position. Shown for comparison: "
        "its best position is often only a lucky noise value.",
        "", True, _snr_raw,
    ),
    Objective(
        "signal", "Signal |B - A|",
        "|signal step mean - baseline step mean| in nm. Ignores the noise: it can drive a ROI to a position where the response is larger and the measurement worse.",
        "nm", True, _signal,
    ),
    Objective(
        "noise", "Noise (baseline step)",
        "The detrended scatter of the baseline step in nm (the 'noise' of the step statistics). Lower is better.",
        "nm", False, _noise,
    ),
)

DEFAULT_OBJECTIVE_KEY = "snr_smooth"


def objective(key: str) -> Objective:
    """The registry entry for ``key``; ``KeyError`` with the valid keys otherwise."""
    for entry in OBJECTIVES:
        if entry.key == key:
            return entry
    raise KeyError(f"unknown objective {key!r}; valid keys: {', '.join(o.key for o in OBJECTIVES)}")


@dataclass(frozen=True)
class Ranking:
    """An objective applied to a lattice. Arrays are ``[point, roi]`` unless noted; columns follow ``roi_ids``.

    ``best``: ``int [roi]``, the lattice row of the best position; the centre when no position is strictly better; ``-1`` when the ROI has no value at any
    position (it cannot be ranked and must not be moved). ``scores``: 0 (worst) .. 1 (best) inside each ROI's own lattice, NaN where there is no value; a ROI
    whose positions are all equal gets 0.5 everywhere."""

    key: str
    unit: str
    higher_is_better: bool
    roi_ids: tuple[int, ...]
    offsets: np.ndarray
    centre: int
    values: np.ndarray
    scores: np.ndarray
    best: np.ndarray
    subpixel: np.ndarray | None = None
    """``float64 [roi, 2]``: the interpolated best position's offset **from the best lattice point** (px); all zero unless the ranking was made with
    ``interpolate=True`` (see :func:`refine_best`). A ROI with a non-zero entry has a best position that was not measured."""
    edge: np.ndarray | None = None
    """``bool [roi]``: the best lattice point is on the outer ring of the lattice, so the true peak may lie beyond it (and is not refined)."""

    def __post_init__(self) -> None:
        if self.subpixel is None:
            object.__setattr__(self, "subpixel", np.zeros((len(self.roi_ids), 2)))
        if self.edge is None:
            object.__setattr__(self, "edge", np.zeros(len(self.roi_ids), dtype=bool))

    @property
    def refined(self) -> np.ndarray:
        """``bool [roi]``: the best position was interpolated between lattice points (it is not one that was scanned)."""
        return np.any(self.subpixel != 0.0, axis=1)

    @property
    def moves(self) -> np.ndarray:
        """``bool [roi]``: the best position is not the centre (an interpolated position next to the centre counts)."""
        return (self.best >= 0) & ((self.best != self.centre) | self.refined)

    @property
    def best_offsets(self) -> np.ndarray:
        """``float64 [roi, 2]``: (dx, dy) px of each ROI's best position, interpolated if the ranking was; (0, 0) for a ROI that stays or cannot be ranked."""
        out = np.zeros((len(self.roi_ids), 2))
        movers = self.moves
        out[movers] = self.offsets[self.best[movers]] + self.subpixel[movers]
        return out

    @property
    def improvement(self) -> np.ndarray:
        """``float64 [roi]``: how much better the best position is than the centre, as a fraction of the centre's value, positive = better whatever the
        direction (for "lower is better", 0.05 means 5 % less). NaN where the centre has no value or a zero value."""
        cols = np.arange(len(self.roi_ids))
        sign = 1.0 if self.higher_is_better else -1.0
        at_centre = sign * self.values[self.centre]
        at_best = sign * self.values[np.maximum(self.best, 0), cols]
        out = np.full(len(self.roi_ids), np.nan)
        valid = (self.best >= 0) & np.isfinite(at_centre) & np.isfinite(at_best) & (at_centre != 0)
        out[valid] = (at_best[valid] - at_centre[valid]) / np.abs(at_centre[valid])
        return out


def rank(obj: LatticeObjectives, key: str = DEFAULT_OBJECTIVE_KEY, *, interpolate: bool = False) -> Ranking:
    """Apply the objective ``key`` to ``obj``: values, scores, and the best position of every ROI (the centre if nothing is strictly better).

    ``interpolate``: also place the best position between the lattice points (:func:`refine_best`); the lattice point stays in ``best``, the step from it is in
    ``subpixel``. The improvement and the scores still describe the measured lattice."""
    entry = objective(key)
    centre = centre_index(obj.offsets)
    values = entry.values(obj)
    if values.shape != (len(obj.offsets), len(obj.roi_ids)):
        raise ValueError(f"objective {key!r} returned {values.shape}, expected {(len(obj.offsets), len(obj.roi_ids))}")
    oriented = values if entry.higher_is_better else -values
    finite = np.isfinite(oriented)
    n_rois = values.shape[1]
    best = np.full(n_rois, -1, dtype=np.int64)
    scores = np.full(values.shape, np.nan)
    for j in range(n_rois):
        if not finite[:, j].any():
            continue
        column = np.where(finite[:, j], oriented[:, j], -np.inf)
        pick = int(np.argmax(column))
        best[j] = centre if (finite[centre, j] and column[centre] >= column[pick]) else pick  # a tie keeps the ROI where it is
        low, high = float(oriented[finite[:, j], j].min()), float(oriented[finite[:, j], j].max())
        scores[finite[:, j], j] = 0.5 if high == low else (oriented[finite[:, j], j] - low) / (high - low)
    offsets = np.asarray(obj.offsets, dtype=np.float64)
    size = int(round(math.sqrt(len(offsets))))
    pitch = float(offsets[1, 0] - offsets[0, 0]) if size > 1 else 0.0
    subpixel = np.zeros((n_rois, 2))
    if interpolate:
        subpixel = refine_best(oriented, best, size, pitch)
        subpixel[np.all(np.abs(subpixel) < 0.5 * 10.0**-POSITION_DECIMALS, axis=1)] = 0.0  # below the 0.1 px a ROI centre is stored in: no move
    at = np.flatnonzero(best >= 0)
    edge = np.zeros(n_rois, dtype=bool)
    if size > 1:
        edge[at] = (best[at] % size == 0) | (best[at] % size == size - 1) | (best[at] // size == 0) | (best[at] // size == size - 1)
    return Ranking(
        key, entry.unit, entry.higher_is_better, tuple(obj.roi_ids), offsets, centre, values, scores, best, subpixel, edge,
    )
