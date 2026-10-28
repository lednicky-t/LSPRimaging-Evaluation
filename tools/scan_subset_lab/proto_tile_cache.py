# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""PROTOTYPE (2026-10-28): a pixel cache ("tile") around ONE ROI for every frame, and a trial (moved ROI / other diameters) computed from it.

Question it answers: if the pixels around the ROI under tuning are read once and kept, how long does one trial over all cubes take, how big is the
cache, how long does it take to fill, and do the numbers equal the real scan's? Read-only. Ignore masks are NOT applied here (they are in the
design: a crop per masked frame); the comparison therefore uses cubes without an ignore mask.

    python proto_tile_cache.py <zarr> <session folder> [--roi-index 80] [--margin 8] [--threads 8]
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402
import numpy as np  # noqa: E402

from lspr_imaging_app.analysis import frame_reduce as fr  # noqa: E402
from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools.chromatic.affine import apply_affine_to_points  # noqa: E402


def window_for(rs, r, affines, shape, margin):
    """Pixel box (y0, y1, x0, x1) that holds ROI r's region in every wavelength plus ``margin`` px of tuning room."""
    reach_src = max(rs.sample_r[r], rs.ring_out_r[r])
    lo = np.array([np.inf, np.inf])
    hi = np.array([-np.inf, -np.inf])
    for a in affines:
        scale = max(float(np.max(np.linalg.svd(np.asarray(a)[:, :2], compute_uv=False))), 1e-6)
        c = apply_affine_to_points(np.array([[rs.cx[r], rs.cy[r]]]), a)[0]
        reach = reach_src * scale + 1.0 + margin
        lo = np.minimum(lo, c - reach)
        hi = np.maximum(hi, c + reach)
    x0, y0 = max(int(np.floor(lo[0])), 0), max(int(np.floor(lo[1])), 0)
    x1, y1 = min(int(np.ceil(hi[0])) + 1, shape[1]), min(int(np.ceil(hi[1])) + 1, shape[0])
    return y0, y1, x0, x1


def trial(tile, rs_trial, r, affines, shape, mode, box, pool=None):
    """mean / std / n_valid / n_nominal for ROI r over every cube and wavelength, from the tile alone. -> arrays [cube, wl, side].
    ``pool``: a ThreadPoolExecutor to spread the wavelengths over (they are independent)."""
    y0, y1, x0, x1 = box
    ww = x1 - x0
    n_cubes, n_wl, _ = tile.shape
    mean = np.full((n_cubes, n_wl, 2), np.nan)
    std = np.full((n_cubes, n_wl, 2), np.nan)
    n_valid = np.zeros((n_cubes, n_wl, 2), dtype=np.int32)
    n_nominal = np.zeros((n_cubes, n_wl, 2), dtype=np.int32)

    def one_wavelength(k):
        layout = fr.build_subset_layout(rs_trial, affines[k], shape, mode, (r,))
        frame = tile[:, k, :]
        for side, flat in ((0, layout.sample_flat), (1, layout.ring_flat)):
            rows, cols = np.divmod(flat, shape[1])
            local = (rows - y0) * ww + (cols - x0)
            if local.size and (rows.min() < y0 or rows.max() >= y1 or cols.min() < x0 or cols.max() >= x1):
                raise ValueError("the trial reaches outside the cached window")
            values = frame[:, local].astype(np.float64)  # [cube, pixel]
            ok = np.isfinite(values)
            count = ok.sum(axis=1)
            total = np.where(ok, values, 0.0).sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                m = total / count
                dev = np.where(ok, values - m[:, None], 0.0)
                s = np.sqrt((dev * dev).sum(axis=1) / (count - 1))
            mean[:, k, side] = np.where(count > 0, m, np.nan)
            std[:, k, side] = np.where(count > 1, s, np.nan)
            n_valid[:, k, side] = count
            n_nominal[:, k, side] = local.size

    if pool is None:
        for k in range(n_wl):
            one_wavelength(k)
    else:
        list(pool.map(one_wavelength, range(n_wl)))
    both = (n_valid[:, :, 0] > 0) & (n_valid[:, :, 1] > 0)
    mean[~both] = np.nan  # the scan's rule: a value only when sample and reference both have pixels
    return mean, std, n_valid, n_nominal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zarr", type=Path)
    ap.add_argument("session", type=Path)
    ap.add_argument("--roi-index", type=int, default=80)
    ap.add_argument("--margin", type=int, default=8)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    source = source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
    )
    engine = modules.analysis_engine
    mode = engine._reference_exclusion_mode()
    plan = build_scan_plan(source, exclusion_mode=mode, trimmed_fraction=engine._trimmed_mean_fraction)
    jobs = plan.jobs
    rs, r = jobs[0].rois, args.roi_index
    n_cubes, n_wl = len(jobs), len(jobs[0].frames)
    affines = [f.affine for f in jobs[0].frames]
    same_affines = all(np.array_equal(j.frames[k].affine, affines[k]) for j in jobs[::40] for k in range(n_wl))
    grid = io.ome_zarr_plane_grid(jobs[0].frames[0].path)
    shape = (grid.height, grid.width)
    box = window_for(rs, r, affines, shape, args.margin)
    y0, y1, x0, x1 = box
    print(f"ROI {r} at ({rs.cx[r]:.1f}, {rs.cy[r]:.1f}), sample r {rs.sample_r[r]:.1f}, ring r {rs.ring_in_r[r]:.1f}-{rs.ring_out_r[r]:.1f} px; "
          f"{n_cubes} cubes x {n_wl} wavelengths; affines identical across cubes: {same_affines}")
    print(f"window {y1 - y0} x {x1 - x0} px = {(y1 - y0) * (x1 - x0)} px per frame (margin {args.margin} px)")
    rows, cols = np.mgrid[y0:y1, x0:x1]
    read_plan = io.partial_read_plan(grid, (rows * shape[1] + cols).reshape(-1), 1.0)
    print(f"chunks per frame: {read_plan.chunk_ids.size} of {grid.n_chunks}")

    paths = [[f.path for f in j.frames] for j in jobs]
    tile = np.empty((n_cubes, n_wl, (y1 - y0) * (x1 - x0)), dtype=np.float32)
    print(f"tile: {tile.nbytes / 1e6:.0f} MB as float32 ({tile.nbytes / 2 / 1e6:.0f} MB if kept as uint16 + a valid mask)")

    def fill(index):
        c, k = divmod(index, n_wl)
        got = io.read_plane_pixels(paths[c][k], read_plan)
        if got is None:
            raise RuntimeError("chunk read failed")
        tile[c, k] = got

    t = time.perf_counter()
    for index in range(n_wl * 12):  # a sample, serial
        fill(index)
    serial = (time.perf_counter() - t) / (n_wl * 12)
    t = time.perf_counter()
    with ThreadPoolExecutor(args.threads) as pool:
        list(pool.map(fill, range(n_cubes * n_wl)))
    threaded = time.perf_counter() - t
    print(f"fill: serial {1000 * serial:.2f} ms/frame (sample of {n_wl * 12}); {args.threads} threads, all {n_cubes * n_wl} frames: {threaded:.1f} s "
          f"({1000 * threaded / (n_cubes * n_wl):.2f} ms/frame)")

    # trials: the ROI moved, and with another sample diameter
    base = list(rs.rois)
    results = {}
    for label, dx, dy, d in (("moved (+3, -2) px", 3.0, -2.0, None), ("sample diameter +4 px", 0.0, 0.0, 4.0), ("moved (-5, +4) px", -5.0, 4.0, None)):
        roi = base[r]
        changed = replace(roi, center_x=roi.center_x + dx, center_y=roi.center_y + dy,
                          sample_diameter_px=roi.sample_diameter_px + (d or 0.0))
        trial_set = fr.build_roi_set([changed if i == r else x for i, x in enumerate(base)], rs.inner_default_px, rs.outer_default_px)
        times = []
        for _ in range(4):
            t = time.perf_counter()
            out = trial(tile, trial_set, r, affines, shape, mode, box)
            times.append(time.perf_counter() - t)
        results[label] = (trial_set, out)
        line = f"trial {label:<24} {min(times):.3f} s best of 4 (all {n_cubes} cubes x {n_wl} wavelengths in memory)"
        with ThreadPoolExecutor(4) as workers:
            threaded_times = []
            for _ in range(4):
                t = time.perf_counter()
                again = trial(tile, trial_set, r, affines, shape, mode, box, pool=workers)
                threaded_times.append(time.perf_counter() - t)
        same_as_serial = all(np.array_equal(a, b2, equal_nan=True) for a, b2 in zip(out, again))
        print(line + f"; spread over 4 threads {min(threaded_times):.3f} s (same numbers: {same_as_serial})")

    # does it equal the real scan? cubes without an ignore mask, subset scan of the same moved ROI
    free = [p for p, j in enumerate(jobs) if all(f.ignore is None for f in j.frames)]
    picks = free[3::max(len(free) // 10, 1)][:10]
    for label, (trial_set, (mean, std, n_valid, n_nominal)) in results.items():
        sub = [replace(jobs[p], rois=trial_set, roi_subset=(r,)) for p in picks]
        real = {res.cube_pos: res for res in scan_cubes(sub, plan.settings, processes=1)}
        worst_mean = worst_std = 0.0
        counts_equal = True
        for p in picks:
            res = real[p]  # a result carries the cube position of its job
            ref_mean, ref_std = res.values["mean"][0], res.std[0]
            counts_equal &= np.array_equal(res.n_valid[0], n_valid[p]) and np.array_equal(res.n_nominal[0], n_nominal[p])
            worst_mean = max(worst_mean, float(np.nanmax(np.abs(ref_mean - mean[p]) / np.abs(ref_mean))) if np.isfinite(ref_mean).any() else 0.0)
            both = np.isfinite(ref_std) & np.isfinite(std[p])
            worst_std = max(worst_std, float(np.max(np.abs(ref_std[both] - std[p][both]) / ref_std[both])) if both.any() else 0.0)
            assert np.array_equal(np.isnan(ref_mean), np.isnan(mean[p])), "NaN pattern differs"
        print(f"vs the real scan, {len(picks)} cubes, {label:<24}: counts identical {counts_equal}; mean max rel diff {worst_mean:.1e}; std max rel diff {worst_std:.1e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
