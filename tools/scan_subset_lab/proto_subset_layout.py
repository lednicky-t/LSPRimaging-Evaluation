# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Real-data check of frame_reduce.build_subset_layout against build_layout().restrict() (every array equal), plus timing of both for one subset per wavelength."""
import sys, time
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
import analysis_stage_timing as st
from lspr_imaging_app.analysis import frame_reduce as fr
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
from lspr_imaging_app.dataset import io


def subset_layout(rs, affine, shape, exclusion_mode, subset, block=fr.DEFAULT_BLOCK_ROIS):
    """The app's own function (it began as the prototype that stood here, 2026-10-28)."""
    return fr.build_subset_layout(rs, affine, shape, exclusion_mode, subset, block)


def same(a, b_):
    return all(np.array_equal(getattr(a, f), getattr(b_, f)) for f in ("sample_roi", "sample_flat", "ring_roi", "ring_flat", "sample_count", "ring_count", "needed_flat", "sample_pos", "ring_pos")) and a.shape == b_.shape and a.n_rois == b_.n_rois


if __name__ == "__main__":
    zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
    dataset = st.load_one(zarr)
    modules, _ = st.build_modules(dataset, session)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
    e = modules.reference_inputs
    plan = build_scan_plan(source, cubes=[plan_c for plan_c in list(modules.dataset.spectral_cubes())[:1]], exclusion_mode=e.reference_exclusion_mode(), trimmed_fraction=e.trimmed_mean_fraction)
    job = plan.jobs[0]
    rs = job.rois
    shape = (799, 1275)
    affines = [f.affine for f in job.frames]
    print(f"{rs.n} ROIs, {len(affines)} affines, shape {shape}, sample_r {rs.sample_r.max():.1f}, ring_out {rs.ring_out_r.max():.1f}")
    rng = np.random.default_rng(7)

    def check(label, rs_, mode, subsets, aff_list):
        n_ok = n_bad = 0; t_old = t_new = 0.0
        for aff in aff_list:
            t = time.perf_counter(); full = fr.build_layout(rs_, aff, shape, mode); t_full = time.perf_counter() - t
            for sub in subsets:
                t = time.perf_counter(); old = full.restrict(sub); t_old += t_full / len(subsets) + (time.perf_counter() - t)
                t = time.perf_counter(); new = subset_layout(rs_, aff, shape, mode, sub); t_new += time.perf_counter() - t
                if same(old, new): n_ok += 1
                else: n_bad += 1
        calls = n_ok + n_bad
        print(f"  {label:<44} identical {n_ok}/{calls}   old {1000*t_old/calls:6.2f} ms   new {1000*t_new/calls:6.2f} ms")
        return n_bad

    bad = 0
    singles = [(i,) for i in range(rs.n)]
    bad += check("real, every single ROI, 2 affines", rs, fr.EXCLUDE_ALL_SAMPLE_ROIS, singles[::3], affines[:2])
    bad += check("real, random 4-subsets, 26 affines", rs, fr.EXCLUDE_ALL_SAMPLE_ROIS, [tuple(sorted(rng.choice(rs.n, 4, replace=False))) for _ in range(3)], affines)
    bad += check("real, random 16-subsets (unsorted order)", rs, fr.EXCLUDE_ALL_SAMPLE_ROIS, [tuple(rng.choice(rs.n, 16, replace=False)) for _ in range(2)], affines[:6])
    bad += check("real, all 160 ROIs as subset", rs, fr.EXCLUDE_ALL_SAMPLE_ROIS, [tuple(range(rs.n))], affines[:2])
    bad += check("real, mode none", rs, "none", singles[::10], affines[:3])
    # dense synthetic layouts: pull the centres together so that rings really run into the neighbours' sample discs, and run past the image edge
    for squeeze, shift in ((0.25, (0, 0)), (0.4, (500, 300)), (1.0, (-600, -350))):
        cx0, cy0 = rs.cx.mean(), rs.cy.mean()
        moved = [replace(r, center_x=float(cx0 + (r.center_x - cx0) * squeeze + shift[0] * (squeeze == 1.0 and 1 or 0)), center_y=float(cy0 + (r.center_y - cy0) * squeeze + shift[1] * (squeeze == 1.0 and 1 or 0))) for r in rs.rois]
        dense = fr.build_roi_set(moved, rs.inner_default_px, rs.outer_default_px)
        subs = [(int(i),) for i in rng.choice(dense.n, 6, replace=False)] + [tuple(sorted(rng.choice(dense.n, 8, replace=False)))]
        bad += check(f"synthetic squeeze {squeeze}, shift {shift}", dense, fr.EXCLUDE_ALL_SAMPLE_ROIS, subs, affines[:3])
    print("ALL IDENTICAL" if bad == 0 else f"MISMATCHES: {bad}")
