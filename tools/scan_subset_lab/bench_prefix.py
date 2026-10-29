# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""The serial part of a Fast scan before any worker gets work (scan_task._compute): plan without masks, hashes, store.prepare, stale matrix, plan with masks, subsets."""
import cProfile, io as _io, pstats, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
import analysis_stage_timing as st
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules, snapshot_source
from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes
from lspr_imaging_app.analysis.scan_store import ScanStore, stale_cell_matrix
from lspr_imaging_app.analysis.scan_task import _with_roi_subsets
from lspr_imaging_app.dataset import io

zarr, session, scratch = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
dataset = st.load_one(zarr); modules, _ = st.build_modules(dataset, session)
records = io.dataset_record_map(dataset)
source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
e = modules.reference_inputs
mode, trimmed = e.reference_exclusion_mode(), e.trimmed_mean_fraction
copy = scratch / "prefix_copy.h5"; shutil.copy2(session / "analysis" / "scan.h5", copy)
def T(label, fn):
    t = time.perf_counter(); r = fn(); print(f"  {label:<46} {time.perf_counter()-t:6.2f} s"); return r
for rep in range(2):
    print(f"pass {rep}")
    t0 = time.perf_counter()
    snap = T("snapshot_source (GUI thread in the app)", lambda: snapshot_source(source))
    plan = T("build_scan_plan(resolve_masks=False)", lambda: build_scan_plan(snap, methods=("mean",), exclusion_mode=mode, trimmed_fraction=trimmed, resolve_masks=False))
    hashes = T("cell_hashes(plan)", lambda: cell_hashes(plan))
    store = ScanStore(copy)
    stored = T("store.prepare", lambda: store.prepare(plan, dataset_label="x"))
    stale = T("stale_cell_matrix", lambda: stale_cell_matrix(stored, hashes))
    print(f"     stale cells {int(stale.sum())} of {stale.size}")
    positions = list(range(len(plan.jobs)))
    run_plan = T("build_scan_plan(cubes=stale, resolve masks)", lambda: build_scan_plan(snap, cubes=[plan.cube_indices[p] for p in positions], methods=("mean",), exclusion_mode=mode, trimmed_fraction=trimmed))
    T("_with_roi_subsets", lambda: _with_roi_subsets(run_plan, [np.ones(len(plan.roi_ids), bool) for _ in positions]))
    print(f"  TOTAL serial prefix {time.perf_counter()-t0:.2f} s; ignore masks {run_plan.ignore_mask_bytes/1e6:.0f} MB, frames with a mask {sum(f.ignore is not None for j in run_plan.jobs for f in j.frames)}")
prof = cProfile.Profile(); prof.enable()
build_scan_plan(snap, cubes=[plan.cube_indices[p] for p in positions], methods=("mean",), exclusion_mode=mode, trimmed_fraction=trimmed)
prof.disable(); out = _io.StringIO(); pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(8); print(out.getvalue()[:2200])
