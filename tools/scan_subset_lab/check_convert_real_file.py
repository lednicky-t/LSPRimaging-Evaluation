# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Convert a COPY of the real gzip scan.h5 to the uncompressed layout through `ScanStore.prepare` and compare it with the original.

    python check_convert_real_file.py <zarr> <session folder>
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402
import numpy as np  # noqa: E402

from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.analysis.scan_store import ScanStore, read_scan  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
dataset = st.load_one(zarr)
modules, _ = st.build_modules(dataset, session)
source = source_from_modules(
    dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
)
e = modules.analysis_engine
plan = build_scan_plan(source, exclusion_mode=e._reference_exclusion_mode(), trimmed_fraction=e._trimmed_mean_fraction, resolve_masks=False)
scratch = Path(tempfile.mkdtemp(prefix="lspr_convert_"))
try:
    path = scratch / "scan.h5"
    shutil.copy2(session / "analysis" / "scan.h5", path)
    before = read_scan(path)
    size_before = path.stat().st_size
    t = time.perf_counter()
    stored = ScanStore(path).prepare(plan, dataset_label="convert-check")
    seconds = time.perf_counter() - t
    after = read_scan(path)
    same = all(np.array_equal(getattr(before, n), getattr(after, n), equal_nan=True)
               for n in ("std", "n_valid", "n_nominal", "n_masked", "cell_hash", "recipe_id", "layout_id", "acquired_at_unix_ms"))
    same = same and np.array_equal(before.values["mean"], after.values["mean"], equal_nan=True) and before.recipes == after.recipes and before.layouts == after.layouts
    t = time.perf_counter()
    read_scan(path)
    read_seconds = time.perf_counter() - t
    print(f"real copy: {size_before / 1e6:.1f} MB gzip -> {path.stat().st_size / 1e6:.1f} MB plain; prepare incl. conversion {seconds:.2f} s; read_scan {read_seconds:.2f} s")
    print(f"content identical: {same}; hashes returned by prepare equal the stored ones: {np.array_equal(stored, before.cell_hash)}; leftovers: {[p.name for p in scratch.iterdir() if p.name != 'scan.h5']}")
finally:
    shutil.rmtree(scratch, ignore_errors=True)
