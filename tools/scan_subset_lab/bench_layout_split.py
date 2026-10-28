# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Same worker, two passes: pass 1 builds the layouts, pass 2 has them cached -> the split of a refresh into layout cost and per-frame cost."""
import sys, time
from dataclasses import replace
from pathlib import Path
sys.argv = [sys.argv[0]] + sys.argv[1:]
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
from lspr_imaging_app.analysis.scan import _Worker
from lspr_imaging_app.dataset import io
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
import analysis_stage_timing as st

zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
dataset = st.load_one(zarr)
modules, _ = st.build_modules(dataset, session)
records = io.dataset_record_map(dataset)
source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
e = modules.analysis_engine
plan = build_scan_plan(source, exclusion_mode=e._reference_exclusion_mode(), trimmed_fraction=e._trimmed_mean_fraction)
n = len(plan.jobs)
picks = [int(i) for i in np.linspace(n * 0.1, n * 0.9, 12).astype(int)]
layouts_distinct = {j.rois.key for j in plan.jobs}
print("distinct ROI layouts over the run:", len(layouts_distinct))
for k in (1, 4, 16, 160):
    sub = b.subset_of(k, len(plan.roi_ids))
    w = _Worker(plan.settings)
    jobs = [replace(plan.jobs[p], roi_subset=sub) for p in picks]
    times = []
    for rep in range(3):
        t = time.perf_counter()
        frames = 0
        for j in jobs:
            r = w.run_cube(j); frames += r.values["mean"].shape[1]
        times.append(time.perf_counter() - t)
    print(f"k={k:3d}: pass1 {times[0]:.2f} s ({1000*times[0]/frames:.2f} ms/frame)  pass2 {times[1]:.2f} s ({1000*times[1]/frames:.2f} ms/frame)  pass3 {times[2]:.2f} s ({1000*times[2]/frames:.2f})  -> layout cost in pass1 = {times[0]-times[1]:.2f} s = {(times[0]-times[1])/26*1000:.1f} ms per layout (26 wavelengths)")
