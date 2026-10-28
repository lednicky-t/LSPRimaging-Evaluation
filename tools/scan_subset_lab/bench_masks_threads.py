# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
import sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
import analysis_stage_timing as st
from lspr_imaging_app.analysis import scan_jobs
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules, snapshot_source
from lspr_imaging_app.dataset import io

zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
dataset = st.load_one(zarr); modules, _ = st.build_modules(dataset, session)
records = io.dataset_record_map(dataset)
source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
e = modules.analysis_engine; snap = snapshot_source(source)
calls = []
orig = scan_jobs.resolve_external_mask
def rec(pixels, geometry, warp):
    calls.append((np.asarray(pixels), geometry, warp)); return orig(pixels, geometry, warp)
scan_jobs.resolve_external_mask = rec
t = time.perf_counter()
build_scan_plan(snap, exclusion_mode=e._reference_exclusion_mode(), trimmed_fraction=e._trimmed_mean_fraction)
print(f"full plan build (serial, recording): {time.perf_counter()-t:.2f} s; resolve_external_mask calls: {len(calls)}")
scan_jobs.resolve_external_mask = orig
print("geometry identity:", calls[0][1].rotation_angle_deg == 0.0, " warp None in", sum(c[2] is None for c in calls), "of", len(calls))
def one(c): return orig(*c)
for rep in range(2):
    t = time.perf_counter(); serial = [one(c) for c in calls]; ts = time.perf_counter() - t
    out = {}
    for n in (2, 4, 8):
        t = time.perf_counter()
        with ThreadPoolExecutor(n) as ex: par = list(ex.map(one, calls))
        out[n] = time.perf_counter() - t
        assert all(np.array_equal(a, bb) and a.dtype == bb.dtype for a, bb in zip(serial, par))
    print(f"  replay {rep}: serial {ts:.2f} s | threads 2: {out[2]:.2f}  4: {out[4]:.2f}  8: {out[8]:.2f}  (results identical)")
