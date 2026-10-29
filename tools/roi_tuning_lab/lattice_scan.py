# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""ROI position tuning, step B (2026-10-28): one statistical number per range for every ROI and lattice position, on a real OME-Zarr export. Read-only.

    python lattice_scan.py <zarr> <session folder> [--rois 5,40,80 | --n-rois 20 | --all] [--size 5] [--pitch 1]
                           [--baseline 2-32] [--signal 201-226] [--processes 4] [--show 3] [--sigma 3] [--check] [--split-half] [--out DIR]

For every lattice point the selected ROIs are moved together by (dx, dy) and the cubes of the two ranges are scanned with the real ``scan_cubes``
(``CubeJob.rois`` = the shifted set, ``roi_subset`` = the selected ROIs); the metric traces go through the Sensorgram's own fit
(``trace_fit.traces_from_inputs`` with the session's formula and metric settings). Each ROI at each position then has **one number per range, mean +/- std**
(range A = baseline cubes, range B = signal cubes), and ``delta = mean B - mean A`` with its uncertainty (``roi_tuning.lattice_objectives``).

Printed: a table per position for the first ``--show`` ROIs; a summary over all selected ROIs that judges a position by its change against the centre in units
of the uncertainty of that change (``--sigma`` = the threshold, default 3). Nothing is written to the session: ``scan.h5`` is only read (by ``--check``),
autosave is suspended by ``build_modules``.

--check: (1) the centre point must equal the stored ``scan.h5`` cell for cell (same code, same pixels); (2) a ROI shifted alone must equal the same ROI
shifted together with the others (they do not interact: printed).
--split-half: an extra test, not the comparison: the cubes of each range are dealt into two halves (even / odd), the best position is chosen on one half and
read on the other. A gain that survives is a property of the position; one that does not was a lucky number.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]  # apps/LSPRi/eva/tools (analysis_stage_timing lives there)
sys.path.insert(0, str(TOOLS))

import analysis_stage_timing as st  # noqa: E402  (puts the app sources on sys.path, offscreen Qt)
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis import roi_tuning as rt  # noqa: E402
from lspr_imaging_app.analysis import scan_jobs  # noqa: E402
from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_store import read_scan  # noqa: E402
from lspr_imaging_app.analysis.trace_fit import TraceInput, traces_from_inputs  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

_app = QApplication.instance() or QApplication([])
SAMPLE, REFERENCE = 0, 1


def parse_range(text: str) -> tuple[int, int]:
    first, last = text.split("-")
    return int(first), int(last)


def scan_lattice(plan, selected, sets, processes: int):
    """Scan every lattice point's shifted set over the plan's cubes in ONE call (the worker pool starts once). Returns ``mean, n_valid`` as
    ``[point, cube, roi, wavelength, side]`` and the summed busy seconds per stage."""
    n_points, n_cubes, n_wl = len(sets), len(plan.jobs), len(plan.wavelength_axis_nm)
    jobs = [
        replace(plan.jobs[p], rois=sets[k], roi_subset=tuple(selected), cube_pos=k * n_cubes + p)  # point-major: neighbours in a task share a ROI set
        for k in range(n_points) for p in range(n_cubes)
    ]
    mean = np.full((n_points, n_cubes, len(selected), n_wl, 2), np.nan)
    n_valid = np.zeros(mean.shape, dtype=np.int32)
    busy = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
    frames = partial = 0
    for res in scan_cubes(jobs, plan.settings, processes=processes):
        k, p = divmod(res.cube_pos, n_cubes)
        columns = list(plan.axis_positions[p])
        mean[k, p][:, columns, :] = res.values["mean"]
        n_valid[k, p][:, columns, :] = res.n_valid
        frames += res.values["mean"].shape[1]
        partial += res.partial_frames
        for name, seconds in res.seconds.items():
            busy[name] = busy.get(name, 0.0) + seconds
    return mean, n_valid, busy, frames, partial


def traces_of(plan, mean, n_valid, formula_key, metric):
    """``[point, roi, cube]`` metric traces, the same inputs as ``scan_trace.trace_input`` makes from a stored scan."""
    n_points, n_cubes, n_rois = mean.shape[:3]
    axis = np.asarray(plan.wavelength_axis_nm, dtype=np.float64)
    cubes = np.asarray(plan.cube_indices, dtype=np.int64)
    scanned = np.ones(n_cubes, dtype=bool)
    inputs = []
    for k in range(n_points):
        for j in range(n_rois):
            sample, reference = mean[k, :, j, :, SAMPLE], mean[k, :, j, :, REFERENCE]
            usable = np.isfinite(sample) & np.isfinite(reference) & (n_valid[k, :, j, :, SAMPLE] > 0) & (n_valid[k, :, j, :, REFERENCE] > 0)
            inputs.append(TraceInput(axis, sample, reference, usable, scanned, cubes))
    return np.asarray(traces_from_inputs(inputs, formula_key, metric)).reshape(n_points, n_rois, n_cubes)


def print_table(obj, j, roi_id, x, y, centre, size, threshold):
    """One ROI: a row per lattice position, one mean +/- std for range A and for range B, delta, and the change against the centre in sigmas."""
    sigma = obj.delta_change_sigma(centre)[:, j]
    best = int(np.nanargmax(np.abs(obj.delta[:, j])))
    print(f"\nROI {roi_id} at ({x:.1f}, {y:.1f}) px; range A {int(obj.n_baseline[centre, j])} cubes, range B {int(obj.n_signal[centre, j])} cubes   (* = largest |delta|)")
    print(f"  {'position':>10} | {'A  mean +/- std (nm)':>24} | {'B  mean +/- std (nm)':>24} | {'delta (nm)':>10} +/- unc | vs centre (sigma)")
    for p in range(len(obj.offsets)):
        dx, dy = obj.offsets[p]
        flag = "*" if p == best else " "
        mark = "" if p == centre or not np.isfinite(sigma[p]) else ("  <-- beyond threshold" if abs(sigma[p]) >= threshold else "")
        print(f"  ({dx:+.0f},{dy:+.0f}) px {flag}| {obj.baseline_mean[p, j]:12.3f} +/- {obj.baseline_std[p, j]:6.3f}   | {obj.signal_mean[p, j]:12.3f} +/- {obj.signal_std[p, j]:6.3f}   "
              f"| {obj.delta[p, j]:10.3f} +/- {obj.delta_sem[p, j]:.3f} | {sigma[p]:+8.1f}{mark}")


def print_grid(obj, j, roi_id, x, y, centre, size):
    """One ROI of a large lattice, compact: |delta| relative to the centre in % as a size x size grid (rows dy, columns dx), the best position marked."""
    delta = np.abs(obj.delta[:, j])
    best = int(np.nanargmax(delta))
    sigma = obj.delta_change_sigma(centre)[best, j]
    print(f"\nROI {roi_id} at ({x:.1f}, {y:.1f}) px: delta at the centre {obj.delta[centre, j]:.3f} +/- {obj.delta_sem[centre, j]:.3f} nm; |delta| relative to the centre, % "
          f"(one value is known to ~{100 * obj.delta_sem[centre, j] / abs(obj.delta[centre, j]):.2f} %); best ({obj.offsets[best, 0]:+.0f},{obj.offsets[best, 1]:+.0f}) = "
          f"{100 * (delta[best] / delta[centre] - 1):+.2f} %, {sigma:+.0f} sigma")
    print("      dx: " + "".join(f"{obj.offsets[c, 0]:+8.0f}" for c in range(size)))
    for r in range(size):
        cells = "".join(f"{100 * (delta[r * size + c] / delta[centre] - 1):+7.2f}" + ("*" if r * size + c == best else " ") for c in range(size))
        print(f"  dy {obj.offsets[r * size, 1]:+3.0f}: {cells}")


def print_summary(obj, lattice, threshold):
    centre, size = lattice.centre_index, lattice.size
    n_rois = len(obj.roi_ids)
    cols = np.arange(n_rois)
    ok = np.isfinite(obj.delta).all(axis=0) & np.isfinite(obj.delta_change_sigma(centre)).all(axis=0)
    sigma = obj.delta_change_sigma(centre)
    magnitude = np.where(np.isfinite(obj.delta), np.abs(obj.delta), -np.inf)
    best = np.argmax(magnitude, axis=0)
    gain = np.abs(obj.delta[best, cols]) / np.abs(obj.delta[centre]) - 1.0
    best_sigma = sigma[best, cols]
    significant = ok & (best != centre) & (best_sigma >= threshold)
    print(f"\nsummary over {int(ok.sum())} of {n_rois} ROIs with a full table (delta = mean B - mean A; a position counts only if it beats the centre by >= {threshold:g} sigma):")
    print(f"  largest |delta| is not at the centre for {int((ok & (best != centre)).sum())} ROIs; beyond the threshold for {int(significant.sum())}")
    print(f"  delta gain of the largest-|delta| position over the centre: median {100 * np.median(gain[ok]):.2f} %, max {100 * gain[ok].max():.2f} %"
          f"   (typical uncertainty of one delta: {1000 * np.median(obj.delta_sem[centre][ok]):.0f} pm, delta itself {np.median(obj.delta[centre][ok]):.2f} nm)")
    print("  how many ROIs have their largest |delta| at each position (rows dy, columns dx):")
    for r in range(size):
        print("    " + "  ".join(f"({obj.offsets[r * size + c, 0]:+.0f},{obj.offsets[r * size + c, 1]:+.0f}):{int((ok & (best == r * size + c)).sum()):3d}" for c in range(size)))
    relative = np.abs(obj.delta) / np.abs(obj.delta[centre]) - 1.0
    print("  median over ROIs of |delta| relative to the centre, in % (rows dy, columns dx):")
    for row in (100 * np.nanmedian(relative, axis=1)).reshape(size, size):
        print("    " + "  ".join(f"{v:+7.2f}" for v in row))
    print("  median over ROIs of the change against the centre in sigmas (rows dy, columns dx):")
    for row in np.nanmedian(sigma, axis=1).reshape(size, size):
        print("    " + "  ".join(f"{v:+7.1f}" for v in row))
    std_change = obj.baseline_std / obj.baseline_std[centre] - 1.0
    n_a = int(np.median(obj.n_baseline[centre]))
    print(f"  std of range A: median over ROIs of its largest change on the lattice {100 * np.nanmedian(np.nanmax(np.abs(std_change), axis=0)):.1f} %"
          f"  (a std from {n_a} cubes is only known to ~{100 / np.sqrt(2 * (n_a - 1)):.0f} % - differences of that size are chance)")
    print("  mean over ROIs of std of range A relative to the centre, in % (standard error ~ "
          f"{100 * np.nanmean(np.nanstd(std_change, axis=1)) / np.sqrt(n_rois):.1f} %):")
    for row in (100 * np.nanmean(std_change, axis=1)).reshape(size, size):
        print("    " + "  ".join(f"{v:+7.2f}" for v in row))


def half_membership(membership, parity: int):
    """The membership with only the cubes of one parity (by position among each region's cubes) used."""
    used = membership.used.copy()
    for row in range(used.shape[0]):
        positions = np.flatnonzero(used[row])
        used[row, positions[(np.arange(positions.size) % 2) != parity]] = False
    return replace(membership, used=used)


def print_split_half(obj_args, lattice, roi_ids):
    """The extra test (module docstring): choose the position with the largest |delta| on one half of the cubes, read its delta on the other half."""
    traces, membership, x, statistics, offsets = obj_args
    centre = lattice.centre_index
    halves = [rt.lattice_objectives(roi_ids, offsets, traces, half_membership(membership, parity), x, statistics) for parity in (0, 1)]
    cols = np.arange(len(roi_ids))
    held, agree = [], 0
    for a, b in ((0, 1), (1, 0)):
        da, db = np.abs(halves[a].delta), np.abs(halves[b].delta)
        pick = np.argmax(np.where(np.isfinite(da), da, -np.inf), axis=0)
        held.append(db[pick, cols] / db[centre] - 1.0)
        agree += int((pick == np.argmax(np.where(np.isfinite(db), db, -np.inf), axis=0)).sum())
    held = np.concatenate(held)
    print(f"\nsplit-half test (extra): the two halves choose the same position for {agree} of {2 * len(roi_ids)} (ROI, direction) pairs (chance {2 * len(roi_ids) / lattice.n_points:.1f}); "
          f"held-out delta gain of the chosen position over the centre: median {100 * np.nanmedian(held):+.2f} %, positive for {int((held > 0).sum())} of {held.size}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zarr", type=Path)
    ap.add_argument("session", type=Path)
    ap.add_argument("--rois", default="", help="ROI ids, comma separated")
    ap.add_argument("--n-rois", type=int, default=6, help="ROIs spread over the array when --rois is not given")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--pitch", type=float, default=1.0)
    ap.add_argument("--baseline", default="2-32")
    ap.add_argument("--signal", default="201-226")
    ap.add_argument("--processes", type=int, default=4)
    ap.add_argument("--show", type=int, default=3, help="print the table of this many ROIs")
    ap.add_argument("--sigma", type=float, default=3.0, help="a position beats the centre only by at least this many sigma")
    ap.add_argument("--split-half", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    source = scan_jobs.source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
    )
    engine = modules.analysis_engine
    mode, trimmed = engine._reference_exclusion_mode(), engine._trimmed_mean_fraction
    metric = modules.analysis_settings.metric_settings()
    statistics = replace(modules.analysis_settings.statistics_settings(), baseline_enabled=False)  # baseline: no effect on delta / noise (module docstring)
    regions = rt.TuningRegions(parse_range(args.baseline), parse_range(args.signal))
    lattice = rt.Lattice(args.size, args.pitch)
    offsets = lattice.offsets()

    plan = scan_jobs.build_scan_plan(source, cubes=regions.cubes(), exclusion_mode=mode, trimmed_fraction=trimmed)
    rs = plan.jobs[0].rois
    if any(job.rois.key != rs.key for job in plan.jobs):
        raise SystemExit("the ROIs differ between cubes (a geometry timeline): this lab handles one ROI set only")
    ids = [int(r.area_roi_id) for r in rs.rois]
    if args.all:
        selected = list(range(rs.n))
    elif args.rois:
        selected = [ids.index(int(t)) for t in args.rois.split(",")]
    else:
        selected = sorted({int(i) for i in np.linspace(3, rs.n - 4, args.n_rois).astype(int)})
    roi_ids = [ids[i] for i in selected]
    formula_key = plan.settings.detection.formula_key
    print(f"metric {metric.fit_method} order {metric.poly_order} {metric.metric_key}, formula {formula_key}, smoothing {statistics.smoothing_method} "
          f"(within steps {statistics.smooth_within_steps}), spike rejection {statistics.spike_rejection_enabled}")
    print(f"ranges: A = baseline cubes {regions.baseline}, B = signal cubes {regions.signal} -> {len(regions.cubes())} cubes; lattice {args.size}x{args.size} = {lattice.n_points} positions, "
          f"pitch {args.pitch} px (reach {lattice.half_extent_px} px per axis, corner {lattice.max_shift_px:.2f} px); {len(selected)} ROI(s)")
    pairs = rt.neighbours_within_reach(rs, selected, lattice)
    print(f"ROI pairs that could interact over this lattice: {len(pairs)}" + ("" if not pairs else f"  e.g. {pairs[:3]}  -> shifted-together != shifted-alone for those"))
    sets = [rt.shifted_roi_set(rs, selected, float(dx), float(dy)) for dx, dy in offsets]

    t0 = time.perf_counter()
    mean, n_valid, busy, frames, partial = scan_lattice(plan, selected, sets, args.processes)
    t_scan = time.perf_counter() - t0
    print(f"scan: {t_scan:.1f} s wall for {len(sets)} positions x {len(plan.jobs)} cubes = {frames} frames ({frames / t_scan:.0f} frames/s), chunk-read frames {partial}/{frames}; "
          f"busy per frame: " + ", ".join(f"{k} {1000 * v / frames:.1f} ms" for k, v in busy.items()))

    t0 = time.perf_counter()
    traces = traces_of(plan, mean, n_valid, formula_key, metric)
    t_fit = time.perf_counter() - t0
    first, last = modules.dataset.cube_spans_unix_ms(plan.cube_indices)
    membership = rt.region_membership(regions, plan.cube_indices, first, last)
    x = np.asarray(plan.cube_indices, dtype=np.float64)  # the Sensorgram's x is the cube number (its baseline window is 0-31)
    obj = rt.lattice_objectives(roi_ids, offsets, traces, membership, x, statistics)
    print(f"metric traces for {traces.shape[0] * traces.shape[1]} (position, ROI) pairs: {t_fit:.1f} s; cubes used per range: "
          f"{int(membership.used[0].sum())} / {int(membership.used[1].sum())}; gaps in traces: {int(np.isnan(traces).sum())} of {traces.size}")

    for j, roi_id in enumerate(roi_ids[: args.show]):
        if lattice.n_points <= 25:
            print_table(obj, j, roi_id, rs.cx[selected[j]], rs.cy[selected[j]], lattice.centre_index, args.size, args.sigma)
        else:  # a row per position would be 49+ lines per ROI
            print_grid(obj, j, roi_id, rs.cx[selected[j]], rs.cy[selected[j]], lattice.centre_index, args.size)
    print_summary(obj, lattice, args.sigma)
    if args.split_half:
        print_split_half((traces, membership, x, statistics, offsets), lattice, roi_ids)

    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            args.out / "lattice_objectives.npz", roi_ids=np.array(roi_ids), offsets=offsets, traces=traces, cubes=np.asarray(plan.cube_indices),
            baseline_mean=obj.baseline_mean, signal_mean=obj.signal_mean, baseline_std=obj.baseline_std, signal_std=obj.signal_std, noise=obj.noise,
            signal_noise=obj.signal_noise, n_baseline=obj.n_baseline, n_signal=obj.n_signal,
        )
        print(f"\nwrote {args.out / 'lattice_objectives.npz'}")

    if args.check:
        return run_checks(args, plan, rs, selected, lattice, mean, pairs, roi_ids)
    return 0


def run_checks(args, plan, rs, selected, lattice, mean, pairs, roi_ids) -> int:
    print("\nchecks")
    bad = 0
    # (1) the centre point against the stored scan, cell for cell
    stored = read_scan(args.session / "analysis" / "scan.h5")
    rows_roi = [stored.roi_ids.index(r) for r in roi_ids]
    different = compared = stale = 0
    for p, cube in enumerate(plan.cube_indices):
        row = stored.cube_indices.index(cube)
        columns = list(plan.axis_positions[p])
        if not (stored.cell_hash[row, rows_roi] != 0).all():
            stale += 1
            continue
        ours = mean[lattice.centre_index, p][:, columns, :]
        theirs = stored.values["mean"][row][rows_roi][:, columns, :]
        compared += 1
        different += 0 if np.array_equal(ours.astype(np.float64), theirs.astype(np.float64), equal_nan=True) else 1
    print(f"  (1) centre point vs the stored scan.h5: {compared} cubes compared, {different} differ, {stale} cubes never scanned for these ROIs"
          + ("  -> IDENTICAL" if different == 0 and compared else ""))
    bad += different
    # (2) a ROI shifted alone equals the same ROI shifted together with the rest
    if len(selected) >= 2 and not pairs:
        k = lattice.n_points - 1  # a corner: the largest move
        dx, dy = lattice.offsets()[k]
        alone = rt.shifted_roi_set(rs, [selected[0]], float(dx), float(dy))
        n_cubes = len(plan.jobs)
        picks = list(range(0, n_cubes, max(n_cubes // 5, 1)))[:5]
        jobs = [replace(plan.jobs[p], rois=alone, roi_subset=(selected[0],), cube_pos=p) for p in picks]
        equal = total = 0
        for res in scan_cubes(jobs, plan.settings, processes=1):
            columns = list(plan.axis_positions[res.cube_pos])
            together = mean[k, res.cube_pos][0][columns, :]
            total += 1
            equal += int(np.array_equal(res.values["mean"][0].astype(np.float64), together.astype(np.float64), equal_nan=True))
        print(f"  (2) ROI {roi_ids[0]} shifted alone by ({dx:+.0f}, {dy:+.0f}) vs shifted with the other {len(selected) - 1}: {equal} of {total} cubes identical")
        bad += total - equal
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
