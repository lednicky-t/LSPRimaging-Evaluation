# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""cProfile of each part of the serial preparation of a Fast scan (snapshot, plan without masks, cell hashes, store.prepare), one at a time.

    python prof_prefix_parts.py <zarr> <session folder>
"""

from __future__ import annotations

import cProfile
import io as _io
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402

from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, snapshot_source, source_from_modules  # noqa: E402
from lspr_imaging_app.analysis.scan_store import ScanStore  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402


def profiled(label, fn, rows=7):
    fn()  # warm
    prof = cProfile.Profile()
    prof.enable()
    t = time.perf_counter()
    result = fn()
    seconds = time.perf_counter() - t
    prof.disable()
    out = _io.StringIO()
    pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(rows)
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()][2:]
    print(f"\n== {label}: {seconds:.3f} s")
    for ln in lines[:rows + 3]:
        print("   " + ln[:150])
    return result


zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
dataset = st.load_one(zarr)
modules, _ = st.build_modules(dataset, session)
source = source_from_modules(
    dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
)
e = modules.reference_inputs
mode, trimmed = e.reference_exclusion_mode(), e.trimmed_mean_fraction
snap = profiled("snapshot_source (GUI thread in the app)", lambda: snapshot_source(source))
plan = profiled("build_scan_plan(resolve_masks=False)", lambda: build_scan_plan(snap, exclusion_mode=mode, trimmed_fraction=trimmed, resolve_masks=False))
profiled("cell_hashes(plan)", lambda: cell_hashes(plan))
scratch = Path(tempfile.mkdtemp(prefix="lspr_prefix_"))
try:
    path = scratch / "scan.h5"
    shutil.copy2(session / "analysis" / "scan.h5", path)
    ScanStore(path).prepare(plan, dataset_label="x")  # converts a gzip copy once, outside the timing
    profiled("ScanStore.prepare (file already current)", lambda: ScanStore(path).prepare(plan, dataset_label="x"))
finally:
    shutil.rmtree(scratch, ignore_errors=True)
