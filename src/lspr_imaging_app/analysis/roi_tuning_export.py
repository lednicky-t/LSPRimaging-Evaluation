"""ROI position tuning: the numbers behind the crosses as a table (2026-10-29). Pure apart from :func:`write_csv`; the module calls that on a plain thread.

One row per (ROI, lattice position): where the ROI is and how far the position is from it, the statistical numbers of the baseline and the signal step at that
position, the value of the objective the user ranked by, its 0..1 score in that ROI's lattice, and which position is the current centre and which is the best.
Units: pixels are processed-image pixels, ``*_nm`` are nm. An empty cell is "no value". Format agreed so far: a CSV (UTF-8, comma, one header row) as the obvious
start; the maintainer has not specified more (2026-10-29), so a column can be added without breaking a reader that reads by name.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from .roi_tuning import LatticeObjectives
from .roi_tuning_objectives import Ranking, objective

COLUMNS = (
    "roi_id", "centre_x_px", "centre_y_px", "dx_px", "dy_px", "is_centre", "is_best", "objective", "objective_value", "score",
    "baseline_mean_nm", "baseline_std_nm", "signal_mean_nm", "signal_std_nm", "delta_nm", "delta_sem_nm", "noise_nm", "snr_measured",
    "n_baseline", "n_signal",
)


def _cell(value: float | int | bool) -> str:
    if isinstance(value, bool | np.bool_):
        return "1" if value else "0"
    if isinstance(value, int | np.integer):
        return str(int(value))
    return "" if not math.isfinite(float(value)) else repr(float(value))


def table_rows(obj: LatticeObjectives, ranking: Ranking, roi_xy: np.ndarray) -> list[list[str]]:
    """The rows of the table (see the module docstring), ROI by ROI, positions in lattice order."""
    if ranking.roi_ids != obj.roi_ids or roi_xy.shape != (len(obj.roi_ids), 2):
        raise ValueError("the ranking and the ROI centres must be those of the objectives")
    name = objective(ranking.key).label
    delta, delta_sem, snr = obj.delta, obj.delta_sem, obj.snr
    rows: list[list[str]] = []
    for j, roi_id in enumerate(obj.roi_ids):
        for p in range(len(obj.offsets)):
            rows.append([
                _cell(roi_id), _cell(roi_xy[j, 0]), _cell(roi_xy[j, 1]), _cell(obj.offsets[p, 0]), _cell(obj.offsets[p, 1]),
                _cell(p == ranking.centre), _cell(p == ranking.best[j]), name, _cell(ranking.values[p, j]), _cell(ranking.scores[p, j]),
                _cell(obj.baseline_mean[p, j]), _cell(obj.baseline_std[p, j]), _cell(obj.signal_mean[p, j]), _cell(obj.signal_std[p, j]),
                _cell(delta[p, j]), _cell(delta_sem[p, j]), _cell(obj.noise[p, j]), _cell(snr[p, j]), _cell(obj.n_baseline[p, j]), _cell(obj.n_signal[p, j]),
            ])
    return rows


def write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write the table to ``path`` (replacing it). Plain thread only: this is file I/O."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
