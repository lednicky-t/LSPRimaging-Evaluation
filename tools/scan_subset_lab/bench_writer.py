# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Writer micro-benchmark on a COPY of the real scan.h5: 1-ROI refresh of all 314 cubes, batches of 16 (what ScanWriter does)."""
import cProfile, io as _io, pstats, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
import analysis_stage_timing as st
from lspr_imaging_app.analysis.scan_arrays import new_arrays
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
from lspr_imaging_app.analysis.scan_store import ScanStore
from lspr_imaging_app.dataset import io

zarr, session, scratch = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
dataset = st.load_one(zarr)
modules, _ = st.build_modules(dataset, session)
records = io.dataset_record_map(dataset)
source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
e = modules.reference_inputs
plan = build_scan_plan(source, exclusion_mode=e.reference_exclusion_mode(), trimmed_fraction=e.trimmed_mean_fraction)
copy = scratch / "scan_copy.h5"
shutil.copy2(session / "analysis" / "scan.h5", copy)
store = ScanStore(copy)
stored = store.prepare(plan, dataset_label="bench")
print("copy size MB", copy.stat().st_size / 1e6, "prepare ok; stored hashes shape", getattr(stored, "shape", None))
arrays = new_arrays(plan)
rng = np.random.default_rng(1)
n = len(plan.jobs)
for m in arrays.values: arrays.values[m][:] = rng.random(arrays.std.shape)
arrays.std[:] = rng.random(arrays.std.shape)
hashes = rng.integers(1, 2**60, size=(n, len(plan.roi_ids)), dtype=np.uint64)
idx = (5,)
def run_all(batch=16):
    t = time.perf_counter()
    for s in range(0, n, batch):
        store.write_batch(plan, arrays, [(p, idx, hashes[p]) for p in range(s, min(s + batch, n))])
    return time.perf_counter() - t
for rep in range(2):
    t = run_all()
    print(f"rep {rep}: 1 ROI x {n} cubes in batches of 16: {t:.2f} s  = {1000*t/n:.1f} ms per cube")
t = run_all(1); print(f"batches of 1: {t:.2f} s = {1000*t/n:.1f} ms per cube")
prof = cProfile.Profile(); prof.enable(); run_all(); prof.disable()
out = _io.StringIO(); pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(14); print(out.getvalue()[:3500])
