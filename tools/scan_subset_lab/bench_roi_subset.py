# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Time the scan kernel for a subset of ROIs on a real OME-Zarr export (read-only; one process; the real scan_cubes path).

    python bench_roi_subset.py --zarr <export> --session <session folder> --cubes 12 --ks 1,4,16,160 [--profile]
"""

from __future__ import annotations

import argparse
import cProfile
import io as _io
import pstats
import sys
import time
from dataclasses import replace
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]  # apps/LSPRi/eva/tools (analysis_stage_timing lives there)
sys.path.insert(0, str(TOOLS))

import analysis_stage_timing as st  # noqa: E402  (puts the app sources on sys.path, offscreen Qt)
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

_app = QApplication.instance() or QApplication([])


def subset_of(k: int, n_rois: int) -> tuple[int, ...] | None:
    if k >= n_rois:
        return None
    return tuple(int(i) for i in np.unique(np.linspace(0, n_rois - 1, k).astype(int)))


def run(plan, cube_positions, subset, label):
    jobs = [replace(plan.jobs[p], roi_subset=subset) for p in cube_positions]
    seconds = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
    frames = partial = 0
    idx = crc = 0.0
    t0 = time.perf_counter()
    for r in scan_cubes(jobs, plan.settings, processes=1):
        frames += r.values["mean"].shape[1]
        partial += r.partial_frames
        idx += r.index_seconds
        crc += r.crc_seconds
        for k, v in r.seconds.items():
            seconds[k] += v
    wall = time.perf_counter() - t0
    per = {k: 1000 * v / frames for k, v in seconds.items()}
    print(
        f"  {label:<26} wall {wall:6.2f} s  {frames} frames  {1000 * wall / frames:6.2f} ms/frame | "
        f"read {per['read']:5.2f}  prepare {per['prepare']:5.2f}  reduce {per['reduce']:5.2f} ms | "
        f"chunk-read frames {partial}/{frames}  index {1000 * idx / frames:5.2f} ms (crc {1000 * crc / frames:4.2f})"
    )
    return wall


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zarr", type=Path, required=True)
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--cubes", type=int, default=12)
    ap.add_argument("--ks", type=str, default="1,4,16,160")
    ap.add_argument("--profile", action="store_true")
    args = ap.parse_args()

    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records,
    )
    engine = modules.reference_inputs
    t = time.perf_counter()
    plan = build_scan_plan(source, exclusion_mode=engine.reference_exclusion_mode(), trimmed_fraction=engine.trimmed_mean_fraction)
    print(f"plan: {len(plan.jobs)} cubes, {plan.n_frames} frames, {len(plan.roi_ids)} ROIs, built in {time.perf_counter() - t:.1f} s")
    s = plan.settings
    print(f"background on: {s.background.flatten_background_enabled}; geometry identity: ", end="")
    from lspr_imaging_app.image_tools.geometry.transform import spatial_transform_is_identity

    print(spatial_transform_is_identity(s.geometry))
    n = len(plan.jobs)
    # Spread over the run; second and third pass reuse the same cubes (OS file cache hot), the first pass is the cold one.
    picks = [int(i) for i in np.linspace(n * 0.1, n * 0.9, args.cubes).astype(int)]
    print(f"cubes (positions) {picks}")
    ks = [int(x) for x in args.ks.split(",")]
    print("\nfirst pass, cold files (one ROI count after another, each on the same cubes -> only the first is really cold):")
    for k in ks[:1]:
        run(plan, picks, subset_of(k, len(plan.roi_ids)), f"{k} ROI(s) (cold)")
    print("\nsteady state (files in the OS cache, caches of the worker warm after the first cube):")
    for k in ks:
        run(plan, picks, subset_of(k, len(plan.roi_ids)), f"{k} ROI(s)")
    if args.profile:
        prof = cProfile.Profile()
        sub = subset_of(1, len(plan.roi_ids))
        prof.enable()
        run(plan, picks[:6], sub, "profile: 1 ROI, 6 cubes")
        prof.disable()
        out = _io.StringIO()
        pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(18)
        print(out.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
