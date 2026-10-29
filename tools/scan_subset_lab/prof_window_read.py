# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Where do the ~1.2 ms of reading a 4-chunk window from one plane go? (cProfile of read_plane_pixels over many planes, hot files)

    python prof_window_read.py <zarr> <session folder>
"""

from __future__ import annotations

import cProfile
import io as _io
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402
import numpy as np  # noqa: E402

from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from proto_tile_cache import window_for  # noqa: E402

zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
dataset = st.load_one(zarr)
modules, _ = st.build_modules(dataset, session)
source = source_from_modules(
    dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
    background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
)
e = modules.reference_inputs
plan = build_scan_plan(source, cubes=list(modules.dataset.spectral_cubes())[:60], exclusion_mode=e.reference_exclusion_mode(), trimmed_fraction=e.trimmed_mean_fraction, resolve_masks=False)
jobs = plan.jobs
rs, r = jobs[0].rois, 80
affines = [f.affine for f in jobs[0].frames]
grid = io.ome_zarr_plane_grid(jobs[0].frames[0].path)
y0, y1, x0, x1 = window_for(rs, r, affines, (grid.height, grid.width), 8)
rows, cols = np.mgrid[y0:y1, x0:x1]
read_plan = io.partial_read_plan(grid, (rows * grid.width + cols).reshape(-1), 1.0)
paths = [f.path for j in jobs for f in j.frames]
for p in paths[:200]:
    io.read_plane_pixels(p, read_plan)  # warm: file cache, metadata caches, codec
t = time.perf_counter()
for p in paths:
    io.read_plane_pixels(p, read_plan)
print(f"{len(paths)} planes, {len(read_plan.chunk_ids)} chunks each: {1000 * (time.perf_counter() - t) / len(paths):.2f} ms per plane (no profiler)")
prof = cProfile.Profile()
prof.enable()
for p in paths:
    io.read_plane_pixels(p, read_plan)
prof.disable()
out = _io.StringIO()
pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(12)
print(out.getvalue()[:3200])
