# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""1-ROI refresh of ALL 314 cubes on a warm pool of N workers, no writing: wall time and busy time per stage (does it scale with workers?)."""
import sys, time
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st
from lspr_imaging_app.analysis.scan import scan_cubes
from lspr_imaging_app.analysis.scan_pool import WarmPool
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
from lspr_imaging_app.dataset import io

if __name__ == "__main__":
    zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
    counts = [int(x) for x in sys.argv[3].split(",")]
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    dataset = st.load_one(zarr)
    modules, _ = st.build_modules(dataset, session)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
    e = modules.reference_inputs
    plan = build_scan_plan(source, exclusion_mode=e.reference_exclusion_mode(), trimmed_fraction=e.trimmed_mean_fraction)
    sub = b.subset_of(k, len(plan.roi_ids))
    jobs = [replace(j, roi_subset=sub) for j in plan.jobs]
    frames_total = plan.n_frames
    print(f"{k} ROI(s), {len(jobs)} cubes, {frames_total} frames")
    for n in counts:
        pool = None
        if n > 1:
            pool = WarmPool(); t = time.perf_counter(); pool.start(n, False); print(f"  pool of {n} started in {time.perf_counter()-t:.1f} s")
        for rep in range(2):
            busy = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}; first = None
            t0 = time.perf_counter()
            for r in scan_cubes(jobs, plan.settings, processes=n, warm=pool):
                if first is None: first = time.perf_counter() - t0
                for s, v in r.seconds.items(): busy[s] += v
            wall = time.perf_counter() - t0
            print(f"  N={n} pass {rep}: wall {wall:5.2f} s ({frames_total/wall:6.0f} frames/s; first cube after {first:.2f} s) | busy read {busy['read']:5.1f} reduce {busy['reduce']:4.1f} s -> {1000*(busy['read']+busy['reduce'])/frames_total:4.2f} ms/frame")
        if pool is not None: pool.shutdown()
