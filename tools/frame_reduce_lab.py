"""Lab for analysis step 1 (pixels -> reduced numbers): can the whole dataset be scanned in well under 10 minutes?

Read-only (dataset and session are never written). Prototype of the planned reducer, kept here until it is proven and moved
into `analysis/` (docs/analysis_redesign_plan_2026-10-19.md). Four sections:

  profile   where the time of today's per-ROI patch path goes (cProfile, self time)
  verify    the vectorised "find every ROI's pixels in one pass" gives the SAME pixels, counts and values as today's
            full-frame masks, for all four reduction methods; and the cached-ROI-exclusion frame preparation gives the
            same processed image as `apply_preprocessing`
  bench     per-frame stage times of the new path
  scan      the whole step end to end with worker threads (native TIFF decoder): wall time, frames/s, extrapolation, and
            a spot check of the scanned numbers against today's code

    python tools/frame_reduce_lab.py --tiff "D:/run/images" --session "D:/run/images/sessions/2026-09-27_143549"
    python tools/frame_reduce_lab.py ... --sections scan --scan-cubes 30 --threads 4
    python tools/frame_reduce_lab.py ... --sections scan --scan-all --threads 4 --save out.npz
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import analysis_stage_timing as st  # noqa: E402  (also puts the app sources on sys.path)
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis.frame_reduce import EXCLUDE_ALL_SAMPLE_ROIS, build_roi_set, reduce_frame  # noqa: E402
from lspr_imaging_app.analysis.reduction import REDUCTION_METHODS, reduce_sample_and_reference  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools import preprocess as pp  # noqa: E402
from lspr_imaging_app.image_tools.background.estimate import roi_exclusion_mask  # noqa: E402
from lspr_imaging_app.roi import rasterize as rz  # noqa: E402

EXCLUDE_ALL = EXCLUDE_ALL_SAMPLE_ROIS
_QT_APP = None


# -- shared context --------------------------------------------------------------------------------------------


class Context:
    def __init__(self, modules, dataset):
        self.m = modules
        self.ds = dataset
        self.records = io.dataset_record_map(dataset)
        self.geometry = modules.geometry.settings()
        self.background = modules.background.settings()
        self.det = modules.roi_toolbox.detection_settings()
        self.engine = modules.analysis_engine
        self.inner, self.outer = self.engine._default_reference_diameters()
        self.trimmed = self.engine._trimmed_mean_fraction
        self.mode = self.engine._reference_exclusion_mode()
        self.timeline = bool(modules.roi_toolbox.has_timeline())
        self._cube_cache: dict[int, tuple] = {}
        self._processed_shape: tuple[int, int] | None = None

    def rois_for(self, cube: int):
        return list(self.m.roi_toolbox.rois_at(cube) if self.timeline else self.m.roi_toolbox.rois())

    def processed_shape(self) -> tuple[int, int]:
        if self._processed_shape is None:
            first = next(iter(self.records.values()))
            self._processed_shape = pp.apply_spatial_preprocessing(io.load_image_array_uncached(str(first.path)), self.geometry).shape[:2]
        return self._processed_shape

    def cube_context(self, cube: int):
        """(rois, RoiSet, ROI exclusion mask for the background) for a cube; one per cube with a timeline, else shared."""
        key = cube if self.timeline else -1
        if key not in self._cube_cache:
            rois = self.rois_for(cube)
            roi_exclusion = roi_exclusion_mask(self.processed_shape(), rois)
            self._cube_cache[key] = (rois, build_roi_set(rois, self.inner, self.outer), roi_exclusion)
        return self._cube_cache[key]


def pick_frames(ctx: Context, cubes: list[int], per_cube: int = 4):
    """A spread of frames per cube plus every masked frame, each fully prepared the way today's code does it."""
    frames = []
    for cube in cubes:
        wls = st.spectral_wavelengths(ctx.m, cube)
        chosen = {wls[i] for i in np.linspace(0, len(wls) - 1, per_cube).astype(int)}
        for wl in wls:
            affine, ignore, _ = st.frame_inputs(ctx.m, cube, wl)
            if wl in chosen or ignore is not None:
                raw = io.load_image_array_uncached(str(ctx.records[(cube, wl)].path))
                rois, rs, _ = ctx.cube_context(cube)
                processed = pp.apply_preprocessing(raw, ctx.geometry, ctx.background, rois=rois, mask_settings=ctx.det, external_mask=ignore)
                frames.append((cube, wl, raw, processed, affine, ignore))
    return frames


# -- sections ----------------------------------------------------------------------------------------------------


def section_profile(ctx: Context, frames) -> None:
    cube, wl, raw, processed, affine, ignore = frames[0]
    rois, rs, _ = ctx.cube_context(cube)
    valid = np.isfinite(processed)
    centers = np.array([[r.center_x, r.center_y] for r in rois])
    radii = np.array([r.sample_diameter_px / 2.0 for r in rois])
    profile = cProfile.Profile()
    profile.enable()
    for roi in rois:
        st.roi_values_patch(roi, rois, centers, radii, processed, valid, affine, ignore, ctx.inner, ctx.outer, "mean", ctx.trimmed)
    profile.disable()
    print(f"  today's patch-scoped path, {len(rois)} ROIs on one frame, by self time (the function's own work):")
    stats_ = pstats.Stats(profile)
    stats_.sort_stats("tottime")
    rows = sorted(stats_.stats.items(), key=lambda kv: kv[1][2], reverse=True)[:10]
    total = sum(v[2] for v in stats_.stats.values())
    for (filename, line, name), (cc, nc, tt, ct, _callers) in rows:
        print(f"    {tt * 1000:7.1f} ms {100 * tt / total:5.1f} %  {nc:6d} calls  {Path(filename).name}:{line} {name}")


def section_verify(ctx: Context, frames) -> bool:
    ok = True
    methods = REDUCTION_METHODS
    tot = {"frames": 0, "rois": 0, "counts": 0, "values": 0, "std": 0}
    worst_std = 0.0
    for cube, wl, raw, processed, affine, ignore in frames:
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        # (a) frame preparation
        fast = pp.apply_preprocessing(raw, ctx.geometry, ctx.background, mask_settings=ctx.det, external_mask=ignore, roi_exclusion=roi_exclusion)
        same_image = np.array_equal(fast, processed, equal_nan=True)
        ok &= same_image
        # (b) ROI stage
        new = reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=ctx.trimmed, exclusion_mode=ctx.mode)
        valid = np.isfinite(processed)
        union = st._incremental_union(rois, processed.shape[:2], affine) if ctx.mode == EXCLUDE_ALL else None
        bad_counts = bad_values = bad_std = 0
        for i, roi in enumerate(rois):
            shape = processed.shape[:2]
            sm = rz.rasterize_sample(roi, shape, affine)
            rm = rz.rasterize_reference(roi, shape, affine, default_inner_diameter_px=ctx.inner, default_outer_diameter_px=ctx.outer)
            if union is not None:
                rm = rm & ~union
            if ignore is not None and ignore.shape == shape:
                sm = sm & ~ignore
                rm = rm & ~ignore
            nominal = (int(sm.sum()), int(rm.sum()))
            sp, rp = processed[sm & valid], processed[rm & valid]
            yy, xx = np.nonzero(rm & valid)
            if (nominal[0], nominal[1]) != (int(new.n_nominal[i, 0]), int(new.n_nominal[i, 1])) or (sp.size, rp.size) != (int(new.n_valid[i, 0]), int(new.n_valid[i, 1])):
                bad_counts += 1
                continue
            if sp.size > 1 and rp.size > 1:
                ref_std = (sp.astype(np.float64).std(ddof=1), rp.astype(np.float64).std(ddof=1))
                if not (np.isclose(ref_std[0], new.std[i, 0], rtol=1e-12, atol=0.0) and np.isclose(ref_std[1], new.std[i, 1], rtol=1e-12, atol=0.0)):
                    bad_std += 1
                    worst_std = max(worst_std, abs(ref_std[0] - new.std[i, 0]))
            for m in methods:
                if sp.size == 0 or rp.size == 0:
                    expected = (np.nan, np.nan)
                else:
                    expected = reduce_sample_and_reference(
                        sp, rp, m, trimmed_mean_fraction=ctx.trimmed, reference_xx=xx.astype(np.float64),
                        reference_yy=yy.astype(np.float64), sample_x=float(roi.center_x), sample_y=float(roi.center_y),
                    )
                    if not (np.isfinite(expected[0]) and np.isfinite(expected[1])):
                        expected = (np.nan, np.nan)
                got = new.values[m][i]
                if not all(x == y or (np.isnan(x) and np.isnan(y)) for x, y in zip(expected, got)):
                    bad_values += 1
        tot["frames"] += 1
        tot["rois"] += len(rois)
        tot["counts"] += bad_counts
        tot["values"] += bad_values
        tot["std"] += bad_std
        flag = "" if (same_image and not (bad_counts or bad_values or bad_std)) else "   <-- DIFFERENT"
        print(f"  cube {cube:3d} wl {wl:5.0f} {'mask' if ignore is not None else '    '}: processed image identical={same_image}, "
              f"ROIs with different pixel counts={bad_counts}, values(4 methods)={bad_values}, std={bad_std}{flag}")
        ok &= not (bad_counts or bad_values or bad_std)
    print(f"\n  total: {tot['frames']} frames x {tot['rois'] // max(tot['frames'], 1)} ROIs = {tot['rois']} ROI-frames; "
          f"different pixel counts {tot['counts']}, values {tot['values']} (x4 methods), std {tot['std']}")
    print("  RESULT: " + ("IDENTICAL to today's code" if ok else "DIFFERENCES FOUND"))
    return ok


def section_bench(ctx: Context, frames, methods: tuple[str, ...]) -> dict:
    timers: dict[str, float] = {}
    prep_ms, full_ms = [], []
    for cube, wl, raw, processed, affine, ignore in frames:
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        _, t = st.timed(pp.apply_preprocessing, raw, ctx.geometry, ctx.background, rois=rois, mask_settings=ctx.det, external_mask=ignore)
        full_ms.append(t)
        _, t = st.timed(pp.apply_preprocessing, raw, ctx.geometry, ctx.background, mask_settings=ctx.det, external_mask=ignore, roi_exclusion=roi_exclusion)
        prep_ms.append(t)
        reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=ctx.trimmed, exclusion_mode=ctx.mode, timers=timers)
    n = len(frames)
    print(f"  frames timed: {n}   methods: {', '.join(methods)}")
    print(f"  frame preparation, today (apply_preprocessing)             {statistics.mean(full_ms):7.2f} ms")
    print(f"  frame preparation, ROI exclusion mask cached               {statistics.mean(prep_ms):7.2f} ms")
    total = 0.0
    for key, seconds in timers.items():
        print(f"  ROI stage: {key:<46s} {1000 * seconds / n:7.2f} ms per frame")
        total += seconds
    print(f"  ROI stage total (all {ctx.cube_context(frames[0][0])[1].n} ROIs){'':<31s} {1000 * total / n:7.2f} ms per frame")
    return {"prepare_today_ms": statistics.mean(full_ms), "prepare_cached_ms": statistics.mean(prep_ms), "roi_stage_ms": 1000 * total / n}


_W: dict = {}  # per worker process (multiprocessing path)


def _scan_one(path, affine, ignore, rs, roi_exclusion, geometry, background, det, mode, trimmed, methods):
    """One frame: read -> prepare -> every ROI. Touches no module; safe in a thread or a process."""
    t0 = time.perf_counter()
    raw = io.load_image_array_uncached(path)
    t1 = time.perf_counter()
    processed = pp.apply_preprocessing(raw, geometry, background, mask_settings=det, external_mask=ignore, roi_exclusion=roi_exclusion)
    t2 = time.perf_counter()
    res = reduce_frame(rs, processed, ignore, affine, methods, trimmed_fraction=trimmed, exclusion_mode=mode)
    t3 = time.perf_counter()
    return res, (t1 - t0, t2 - t1, t3 - t2)


def _mp_init(geometry, background, det, mode, trimmed, shape, methods, switch_interval) -> None:
    io.use_fast_tiff_decoder(True)
    if switch_interval:
        sys.setswitchinterval(switch_interval)
    _W.update(geometry=geometry, background=background, det=det, mode=mode, trimmed=trimmed, shape=shape, methods=methods)


def _mp_cube(task):
    """One cube in a worker process: all its frames, sequentially. Returns plain arrays."""
    cube_pos, rs, frames = task
    w = _W
    roi_exclusion = roi_exclusion_mask(w["shape"], list(rs.rois))
    n_wl = len(frames)
    values = {m: np.full((rs.n, n_wl, 2), np.nan) for m in w["methods"]}
    std = np.full((rs.n, n_wl, 2), np.nan)
    n_valid = np.zeros((rs.n, n_wl, 2), dtype=np.int32)
    n_nominal = np.zeros((rs.n, n_wl, 2), dtype=np.int32)
    stage = np.zeros(3)
    for k, (path, affine, ignore) in enumerate(frames):
        res, timing = _scan_one(path, affine, ignore, rs, roi_exclusion, w["geometry"], w["background"], w["det"], w["mode"], w["trimmed"], w["methods"])
        for m in w["methods"]:
            values[m][:, k, :] = res.values[m]
        std[:, k, :] = res.std
        n_valid[:, k, :] = res.n_valid
        n_nominal[:, k, :] = res.n_nominal
        stage += timing
    return cube_pos, values, std, n_valid, n_nominal, stage


def section_scan(ctx: Context, cubes: list[int], threads: int, processes: int, methods: tuple[str, ...], save: Path | None,
                 switch_interval: float = 0.0) -> None:
    geometry, background, det = ctx.geometry, ctx.background, ctx.det
    shape = ctx.processed_shape()
    # Everything that touches a module is resolved here, on this thread; the workers see plain data only.
    t_setup = time.perf_counter()
    jobs = []  # (cube_pos, wl_pos, path, affine, ignore, rs, roi_exclusion)
    for cube_pos, cube in enumerate(cubes):
        rois, rs, roi_exclusion = ctx.cube_context(cube)
        for wl_pos, wl in enumerate(st.spectral_wavelengths(ctx.m, cube)):
            affine, ignore, _ = st.frame_inputs(ctx.m, cube, wl)
            jobs.append((cube_pos, wl_pos, str(ctx.records[(cube, wl)].path), affine, ignore, rs, roi_exclusion))
    n_rois = jobs[0][5].n
    n_wl = max(j[1] for j in jobs) + 1
    setup_s = time.perf_counter() - t_setup
    out = {m: np.full((n_rois, len(cubes), n_wl, 2), np.nan) for m in methods}
    out_std = np.full((n_rois, len(cubes), n_wl, 2), np.nan)
    out_n = np.zeros((n_rois, len(cubes), n_wl, 2), dtype=np.int32)
    out_nom = np.zeros((n_rois, len(cubes), n_wl, 2), dtype=np.int32)
    stage = np.zeros(3)
    if switch_interval:
        sys.setswitchinterval(switch_interval)

    started = time.perf_counter()
    if processes > 0:
        by_cube: dict[int, list] = {}
        for cube_pos, wl_pos, path, affine, ignore, rs, _roi_exclusion in jobs:
            by_cube.setdefault(cube_pos, []).append((path, affine, ignore))
        tasks = [(cube_pos, jobs[next(i for i, j in enumerate(jobs) if j[0] == cube_pos)][5], frames) for cube_pos, frames in by_cube.items()]
        initargs = (geometry, background, det, ctx.mode, ctx.trimmed, shape, methods, switch_interval)
        with ProcessPoolExecutor(max_workers=processes, initializer=_mp_init, initargs=initargs) as pool:
            first_result_at = None
            for cube_pos, values, std, n_valid, n_nominal, stage_here in pool.map(_mp_cube, tasks):
                if first_result_at is None:
                    first_result_at = time.perf_counter() - started
                k = std.shape[1]
                for m in methods:
                    out[m][:, cube_pos, :k, :] = values[m]
                out_std[:, cube_pos, :k, :] = std
                out_n[:, cube_pos, :k, :] = n_valid
                out_nom[:, cube_pos, :k, :] = n_nominal
                stage += stage_here
        label = f"{processes} worker process(es)" + f" (first result after {first_result_at:.1f} s: process start-up included in the wall time)"
    else:
        def init_worker() -> None:
            io.use_fast_tiff_decoder(True)

        def work(job):
            cube_pos, wl_pos, path, affine, ignore, rs, roi_exclusion = job
            res, timing = _scan_one(path, affine, ignore, rs, roi_exclusion, geometry, background, det, ctx.mode, ctx.trimmed, methods)
            for m in methods:
                out[m][:, cube_pos, wl_pos, :] = res.values[m]
            out_std[:, cube_pos, wl_pos, :] = res.std
            out_n[:, cube_pos, wl_pos, :] = res.n_valid
            out_nom[:, cube_pos, wl_pos, :] = res.n_nominal
            return timing

        with ThreadPoolExecutor(max_workers=threads, initializer=init_worker) as pool:
            for timing in pool.map(work, jobs):
                stage += timing
        label = f"{threads} worker thread(s)"
    wall = time.perf_counter() - started
    frames = len(jobs)
    total_cubes = len(list(ctx.m.dataset.spectral_cubes()))
    print(f"  {frames} frames ({len(cubes)} cubes x {n_wl} wavelengths) x {n_rois} ROIs, methods {', '.join(methods)}, {label}"
          + (f", GIL switch interval {switch_interval * 1000:.2f} ms" if switch_interval else ""))
    print(f"  setup on the main thread (inputs of all frames): {setup_s:.1f} s")
    print(f"  WALL TIME of the scan: {wall:.1f} s  =  {frames / wall:.1f} frames/s  ({1000 * wall / frames:.1f} ms per frame)")
    print("  busy time per stage, summed over workers (ms per frame): "
          + ", ".join(f"{k} {1000 * v / frames:.1f}" for k, v in zip(("read", "prepare", "roi stage"), stage)))
    scale = total_cubes / len(cubes)
    print(f"  => whole dataset ({total_cubes} cubes): about {(wall + setup_s) * scale / 60:.1f} min  (scan {wall * scale / 60:.1f} + setup {setup_s * scale / 60:.1f})")
    finite = np.isfinite(out[methods[0]][..., 0])
    print(f"  results: {int(finite.sum())} of {finite.size} (ROI, cube, wavelength) cells have a value; "
          f"mean n_valid sample {out_n[..., 0].mean():.0f} / reference {out_n[..., 1].mean():.0f} pixels")

    # Spot check against today's code path (apply_preprocessing + full-frame masks + reduce) on random scanned frames.
    rng = random.Random(1)
    checked = differ = 0
    for job in rng.sample(jobs, min(6, len(jobs))):
        cube_pos, wl_pos, path, affine, ignore, rs, _ = job
        rois = list(rs.rois)
        raw = io.load_image_array_uncached(path)
        processed = pp.apply_preprocessing(raw, geometry, background, rois=rois, mask_settings=det, external_mask=ignore)
        valid = np.isfinite(processed)
        union = st._incremental_union(rois, processed.shape[:2], affine) if ctx.mode == EXCLUDE_ALL else None
        for i, roi in enumerate(rois):
            if union is None:
                continue
            old = st.roi_values_full(roi, processed, valid, affine, ignore, union, ctx.inner, ctx.outer, methods[0], ctx.trimmed)
            new = out[methods[0]][i, cube_pos, wl_pos]
            checked += 1
            if not all(x == y or (np.isnan(x) and np.isnan(y)) for x, y in zip(old, new)):
                differ += 1
    print(f"  spot check against today's code: {checked - differ} of {checked} ROI values identical (6 random frames)")
    if save is not None:
        np.savez_compressed(save, cubes=np.array(cubes), **{f"value_{m}": out[m] for m in methods}, std=out_std, n_valid=out_n, n_nominal=out_nom)
        print(f"  saved: {save} ({save.stat().st_size / 1e6:.1f} MB)")


# -- main ------------------------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiff", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--sections", type=str, default="profile,verify,bench,scan")
    parser.add_argument("--cube-list", type=str, default="27,231,100,298", help="cubes for profile/verify/bench")
    parser.add_argument("--methods", type=str, default="mean", help="reduction methods for bench/scan (default mean)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--processes", type=int, default=0, help="scan with this many worker PROCESSES instead of threads")
    parser.add_argument("--switch-interval", type=float, default=0.0, help="Python GIL switch interval in seconds (default 0.005); try 0.0005")
    parser.add_argument("--scan-cubes", type=int, default=20, help="cubes for the scan, spread over the run")
    parser.add_argument("--scan-all", action="store_true", help="scan every cube")
    parser.add_argument("--save", type=Path, default=None, help="write the scan's numbers to this .npz")
    args = parser.parse_args()
    sections = {s.strip() for s in args.sections.split(",")}
    methods = tuple(m.strip() for m in args.methods.split(","))

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])  # the modules are QObjects; keep the application alive
    dataset = st.load_one(args.tiff)
    modules, _state = st.build_modules(dataset, args.session)
    ctx = Context(modules, dataset)
    print(f"{len(ctx.m.roi_toolbox.rois())} ROIs, exclusion mode {ctx.mode}, timeline {ctx.timeline}, {args.threads} threads")
    cubes = [int(c) for c in args.cube_list.split(",")]
    frames = pick_frames(ctx, cubes) if sections & {"profile", "verify", "bench"} else []
    if "profile" in sections:
        st.header("PROFILE")
        section_profile(ctx, frames)
    if "verify" in sections:
        st.header("VERIFY: new reducer and cached frame preparation vs today's code")
        section_verify(ctx, frames)
    if "bench" in sections:
        st.header("BENCH: stage times per frame")
        section_bench(ctx, frames, methods)
    if "scan" in sections:
        st.header("SCAN: step 1 end to end")
        all_cubes = list(ctx.m.dataset.spectral_cubes())
        scan_cubes = all_cubes if args.scan_all else st.pick_cubes(ctx.m, args.scan_cubes)
        section_scan(ctx, scan_cubes, args.threads, args.processes, methods, args.save, args.switch_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
