"""Find bad spectral data points in a finished LSPRi eva measurement_backup.h5.

Read-only diagnostic: never touches the input file, only writes a separate
report CSV. Three independent problem categories, each needing a different
fix downstream (see docs/ once this is written up there):

  point_contamination - one wavelength, one cube, one ROI is off while
    everything around it (in time, in wavelength, and in the neighboring
    ROIs) is normal. Debris/bubble briefly in the light path. Fix: drop or
    downweight that one wavelength when fitting that cube's spectrum.

  point_trail_event - the same single-wavelength-per-ROI signature as
    point_contamination, but several ROIs in the same cube are hit at once
    with the wavelength shifting systematically along a row or column.
    Since wavelength order is acquisition-time order, this reads as one
    small object crossing several ROIs during one sweep, not several
    independent debris events by coincidence. Same fix as point
    contamination for each ROI, but worth keeping distinct because it's a
    physically real, informative pattern (a piece of debris' path/speed),
    not just noise to discard.

  chimeric_splice - a single cube's spectrum is a splice of two different
    states: the first part of the wavelength sweep matches the previous
    cube, the rest matches the next cube, because something changed the
    sample mid-sweep (wavelengths are captured one at a time, so a fast
    real-world event can straddle one nominal "time point"). Only a
    handful of ROIs affected. Fix: that cube's derived metric for the
    affected ROI(s) isn't a valid single reading - exclude it from the
    sensorgram trend rather than trying to repair it.

  transition_event - the same splice signature as above, but hitting many
    ROIs in the same cube at once. In this dataset these lined up almost
    exactly with real pump-plan solution-change steps (see
    apps/LSPRi/eva/docs/ - written up after cross-checking against
    measureing_times.csv's "Note pump plan" column). Not contamination -
    a real, fast event. Fix: don't silently correct it, flag it for the
    scientist; the per-ROI split wavelength doubles as a rough front-
    arrival time, so this can also be read as a measurement (front speed/
    direction across the array) rather than only a problem.

Usage:
    python detect_spectral_outliers.py <measurement_backup.h5> [--out report.csv]

Needs the lspr_imaging_app package importable (i.e. run with the Suite's
venv) so the outlier math uses the app's own formula_value implementation
instead of a second, driftable copy of it.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass

import h5py
import numpy as np

from lspr_imaging_app.processing.analysis import formula_values_from_reduced_values


# --- tunables -----------------------------------------------------------
POINT_WINDOW_TEMPORAL = 6      # cubes on each side, for the temporal smoothness check
POINT_WINDOW_SPECTRAL = 4      # wavelengths on each side, for the spectral smoothness check
POINT_Z_THRESHOLD = 8.0        # robust z-score a point must clear on ALL THREE axes

SPLICE_GAP = 5                 # cubes used to build the "before"/"after" reference state
SPLICE_MIN_SEG = 2             # minimum wavelengths on each side of a candidate split
SPLICE_STATE_GAP_NOISE_MULT = 3.0   # before/after must differ by at least this many noise-floors
SPLICE_MIN_IMPROVEMENT = 4.0   # two-segment fit must beat a single-state fit by this factor

TRANSITION_MIN_ROIS = 5        # cubes with at least this many spliced ROIs are "transition_event"

TRAIL_MIN_ROIS = 3             # single-wavelength point hits, same cube, needed to call it a trail
TRAIL_MIN_CORR = 0.6           # min |corr(wavelength, row-or-col)| to call a trail front-like


@dataclass
class RoiData:
    roi_id: str
    row: int
    col: int
    matrix: np.ndarray  # (n_cubes, n_wavelengths) formula values


@dataclass
class Finding:
    roi_id: str
    cube: int
    category: str
    wavelength_nm: float | None = None
    value: float | None = None
    t_z: float | None = None
    s_z: float | None = None
    sp_z: float | None = None
    split_wavelength_nm: float | None = None
    improvement: float | None = None
    state_gap: float | None = None
    n_rois_in_event: int | None = None
    notes: str = ""


def load_rois(path: str) -> tuple[np.ndarray, list[RoiData]]:
    rois: list[RoiData] = []
    with h5py.File(path, "r") as f:
        wl = f["axes"]["wavelengths_nm"][:]
        for rid, group in f["rois"].items():
            if "spectra" not in group or "definition" not in group:
                continue
            spectra = group["spectra"]
            method = spectra.attrs.get("reduction_method", "mean")
            formula_key = spectra.attrs.get("formula_key", "absorbance")
            reduced = spectra["reduced"][method]
            matrix = formula_values_from_reduced_values(reduced["sample"][:], reduced["reference"][:], formula_key)
            d = group["definition"].attrs
            rois.append(RoiData(roi_id=rid, row=int(d["array_row"]), col=int(d["array_col"]), matrix=matrix))
    rois.sort(key=lambda r: int(r.roi_id))
    return wl, rois


def _robust_z_per_wavelength(residual: np.ndarray) -> np.ndarray:
    """Robust z-score, normalized separately per wavelength column.

    Different wavelengths have genuinely different noise floors (they even
    have different exposure times - see metaData.txt), and a spectral
    residual's typical size also depends on local curve curvature (steep
    near a resonance peak, ~flat in the wings) - pooling all wavelengths
    into one scale drowns out real anomalies at wavelengths that are
    normally quiet. Normalizing per column fixes both."""
    med = np.nanmedian(residual, axis=0, keepdims=True)
    mad = np.nanmedian(np.abs(residual - med), axis=0, keepdims=True) * 1.4826 + 1e-12
    return (residual - med) / mad


def _local_median_residual(mat: np.ndarray, axis: int, window: int, exclude_rows: set[int] = frozenset()) -> np.ndarray:
    """value minus the median of `window` neighbors on each side along `axis` (self excluded).

    `exclude_rows` additionally drops given cube indices from every window
    when axis=0 - used to keep a known real transition cube (e.g. cube 229)
    out of its neighbors' own temporal baselines, so it doesn't leave a
    ghost/echo flag on the cubes right next to it."""
    n = mat.shape[axis]
    resid = np.full_like(mat, np.nan, dtype=np.float64)
    for i in range(n):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        idxs = [j for j in range(lo, i) if j not in exclude_rows] + [j for j in range(i + 1, hi) if j not in exclude_rows]
        if len(idxs) < 4:
            continue
        if axis == 0:
            resid[i, :] = mat[i, :] - np.median(mat[idxs, :], axis=0)
        else:
            resid[:, i] = mat[:, i] - np.median(mat[:, idxs], axis=1)
    return resid


def _raw_point_candidates(rois: list[RoiData], exclude_cubes: set[int]) -> list[dict]:
    """Points that are surprising against their own recent history, their
    own spectrum's nearby wavelengths, AND their spatial neighbor ROIs, all
    at once. Real signal (kinetics, resonance shifts) is usually smooth in
    at least one of those three; genuine point contamination is smooth in
    none of them - see the module docstring."""
    by_pos = {(r.row, r.col): r for r in rois}
    candidates = []
    for r in rois:
        neighbor_mats = [
            by_pos[(r.row + dr, r.col + dc)].matrix
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if (r.row + dr, r.col + dc) in by_pos
        ]
        if len(neighbor_mats) < 2:
            continue  # not enough spatial context to judge this ROI

        tz = _robust_z_per_wavelength(_local_median_residual(r.matrix, axis=0, window=POINT_WINDOW_TEMPORAL, exclude_rows=exclude_cubes))
        sz = _robust_z_per_wavelength(_local_median_residual(r.matrix, axis=1, window=POINT_WINDOW_SPECTRAL))
        neighbor_median = np.median(np.stack(neighbor_mats, axis=0), axis=0)
        spz = _robust_z_per_wavelength(r.matrix - neighbor_median)

        combo = np.minimum(np.minimum(np.abs(tz), np.abs(sz)), np.abs(spz))
        for cube, wl_idx in np.argwhere(combo > POINT_Z_THRESHOLD):
            cube, wl_idx = int(cube), int(wl_idx)
            candidates.append(dict(
                roi=r, cube=cube, wl_idx=wl_idx, value=float(r.matrix[cube, wl_idx]),
                t_z=float(tz[cube, wl_idx]), s_z=float(sz[cube, wl_idx]), sp_z=float(spz[cube, wl_idx]),
            ))
    return candidates


def classify_point_candidates(wl: np.ndarray, candidates: list[dict], transition_cubes: set[int]) -> list[Finding]:
    """Tell isolated debris apart from the point-level footprint of a
    larger event. A moving object crossing several ROIs during one sweep
    looks, from any single ROI's own perspective, exactly like ordinary
    point contamination - the only way to tell them apart is by checking
    whether OTHER ROIs in the same cube were hit too, and whether the
    pattern across them is coherent (see point_trail_event above)."""
    per_cube: dict[int, list[dict]] = {}
    for c in candidates:
        per_cube.setdefault(c["cube"], []).append(c)

    findings = []
    for cube, hits in per_cube.items():
        if cube in transition_cubes:
            continue  # already reported, more informatively, as a transition_event

        per_roi: dict[str, list[dict]] = {}
        for h in hits:
            per_roi.setdefault(h["roi"].roi_id, []).append(h)

        single_hit = [group[0] for group in per_roi.values() if len(group) == 1]
        multi_hit = [h for group in per_roi.values() if len(group) > 1 for h in group]

        is_trail = False
        notes = ""
        if len(single_hit) >= TRAIL_MIN_ROIS:
            wavelengths = np.array([wl[h["wl_idx"]] for h in single_hit])
            cols = np.array([h["roi"].col for h in single_hit], dtype=float)
            rows = np.array([h["roi"].row for h in single_hit], dtype=float)
            corr_col = np.corrcoef(wavelengths, cols)[0, 1] if len(set(cols)) > 1 else 0.0
            corr_row = np.corrcoef(wavelengths, rows)[0, 1] if len(set(rows)) > 1 else 0.0
            axis, corr = ("column", corr_col) if abs(np.nan_to_num(corr_col)) >= abs(np.nan_to_num(corr_row)) else ("row", corr_row)
            if abs(np.nan_to_num(corr)) >= TRAIL_MIN_CORR:
                is_trail = True
                direction = "increasing" if corr > 0 else "decreasing"
                notes = f"front-like: wavelength {direction} with {axis} (r={corr:.2f}), n_rois={len(single_hit)}"

        category = "point_trail_event" if is_trail else "point_contamination"
        for h in single_hit:
            findings.append(Finding(
                roi_id=h["roi"].roi_id, cube=cube, category=category,
                wavelength_nm=float(wl[h["wl_idx"]]), value=h["value"],
                t_z=h["t_z"], s_z=h["s_z"], sp_z=h["sp_z"],
                n_rois_in_event=len(single_hit) if is_trail else None, notes=notes,
            ))
        for h in multi_hit:
            findings.append(Finding(
                roi_id=h["roi"].roi_id, cube=cube, category="point_contamination",
                wavelength_nm=float(wl[h["wl_idx"]]), value=h["value"],
                t_z=h["t_z"], s_z=h["s_z"], sp_z=h["sp_z"],
            ))
    return findings


def _splice_candidates(mat: np.ndarray) -> list[dict]:
    """Per interior cube: does this spectrum look like a clean two-segment
    splice of the local 'before' and 'after' reference states, rather than
    one coherent state? Returns one dict per cube that passes the checks."""
    n_cubes, n_wl = mat.shape
    noise_floor = np.median(np.abs(np.diff(mat, axis=0))) + 1e-12
    candidates = []
    for c in range(SPLICE_GAP, n_cubes - SPLICE_GAP):
        before_ref = np.median(mat[c - SPLICE_GAP:c], axis=0)
        after_ref = np.median(mat[c + 1:c + 1 + SPLICE_GAP], axis=0)
        state_gap = np.abs(after_ref - before_ref)
        if np.max(state_gap) < SPLICE_STATE_GAP_NOISE_MULT * noise_floor:
            continue

        resid_before = mat[c] - before_ref
        resid_after = mat[c] - after_ref
        sq_before, sq_after = resid_before ** 2, resid_after ** 2
        cum_before = np.concatenate([[0.0], np.cumsum(sq_before)])
        cum_after_tail = np.concatenate([np.cumsum(sq_after[::-1])[::-1], [0.0]])

        ks = np.arange(SPLICE_MIN_SEG, n_wl - SPLICE_MIN_SEG + 1)
        err_k = cum_before[ks] + cum_after_tail[ks]
        k_best = int(ks[np.argmin(err_k)])
        err_min = float(np.min(err_k))
        err_best_single = float(min(sq_before.sum(), sq_after.sum()))
        improvement = err_best_single / (err_min + 1e-12)

        if improvement >= SPLICE_MIN_IMPROVEMENT:
            candidates.append(dict(cube=c, k_best=k_best, improvement=improvement,
                                    state_gap=float(np.max(state_gap))))
    return candidates


def find_splices_and_transitions(wl: np.ndarray, rois: list[RoiData]) -> list[Finding]:
    per_cube: dict[int, list[tuple[RoiData, dict]]] = {}
    for r in rois:
        for cand in _splice_candidates(r.matrix):
            per_cube.setdefault(cand["cube"], []).append((r, cand))

    findings = []
    for cube, hits in per_cube.items():
        category = "transition_event" if len(hits) >= TRANSITION_MIN_ROIS else "chimeric_splice"
        notes = ""
        if category == "transition_event":
            splits = np.array([wl[c["k_best"]] for _, c in hits])
            cols = np.array([r.col for r, _ in hits], dtype=float)
            rows = np.array([r.row for r, _ in hits], dtype=float)
            corr_col = np.corrcoef(splits, cols)[0, 1] if len(set(cols)) > 1 else float("nan")
            corr_row = np.corrcoef(splits, rows)[0, 1] if len(set(rows)) > 1 else float("nan")
            if abs(np.nan_to_num(corr_col)) >= abs(np.nan_to_num(corr_row)):
                axis, corr = "column", corr_col
            else:
                axis, corr = "row", corr_row
            direction = "increasing" if corr > 0 else "decreasing"
            notes = f"front-like: split wavelength {direction} with {axis} (r={corr:.2f}), n_rois={len(hits)}"

        for r, cand in hits:
            findings.append(Finding(
                roi_id=r.roi_id, cube=cube, category=category,
                split_wavelength_nm=float(wl[cand["k_best"]]),
                improvement=cand["improvement"], state_gap=cand["state_gap"],
                n_rois_in_event=len(hits), notes=notes,
            ))
    return findings


def write_report(path: str, findings: list[Finding]) -> None:
    fields = ["roi_id", "cube", "category", "wavelength_nm", "value", "t_z", "s_z", "sp_z",
              "split_wavelength_nm", "improvement", "state_gap", "n_rois_in_event", "notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for finding in findings:
            writer.writerow({k: getattr(finding, k) for k in fields})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("h5_path", help="path to measurement_backup.h5")
    parser.add_argument("--out", default="outlier_report.csv", help="output CSV path")
    args = parser.parse_args()

    print(f"Loading {args.h5_path} ...")
    wl, rois = load_rois(args.h5_path)
    print(f"{len(rois)} ROIs with data, {len(wl)} wavelengths, {rois[0].matrix.shape[0]} cubes")

    # splices/transitions first: knowing which cubes are real dataset-wide
    # events lets the point detector keep them out of its own temporal
    # baselines and avoid re-reporting the same event as noisy point hits.
    splice = find_splices_and_transitions(wl, rois)
    transition_cubes = {x.cube for x in splice if x.category == "transition_event"}

    raw_points = _raw_point_candidates(rois, exclude_cubes=transition_cubes)
    point = classify_point_candidates(wl, raw_points, transition_cubes)

    findings = point + splice
    findings.sort(key=lambda x: (x.cube, x.roi_id))

    write_report(args.out, findings)

    by_cat: dict[str, int] = {}
    for x in findings:
        by_cat[x.category] = by_cat.get(x.category, 0) + 1
    print(f"\nWrote {len(findings)} findings to {args.out}")
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat}: {n}")

    if transition_cubes:
        print("\nTransition-event cubes (real, fast dataset-wide changes - not contamination):")
        for cube in sorted(transition_cubes):
            example = next(x for x in findings if x.category == "transition_event" and x.cube == cube)
            print(f"  cube {cube}: {example.n_rois_in_event} ROIs - {example.notes}")

    trail_cubes = sorted({x.cube for x in findings if x.category == "point_trail_event"})
    if trail_cubes:
        print("\npoint_trail_event cubes (one object crossing several ROIs in one sweep):")
        for cube in trail_cubes:
            example = next(x for x in findings if x.category == "point_trail_event" and x.cube == cube)
            print(f"  cube {cube}: {example.n_rois_in_event} ROIs - {example.notes}")


if __name__ == "__main__":
    sys.exit(main())
