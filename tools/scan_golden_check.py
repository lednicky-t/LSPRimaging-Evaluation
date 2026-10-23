"""Golden check for analysis step 1's job builder: the new route (session -> ScanPlan -> scan) against today's `compute_cell`.

Read-only (dataset and session are never written; the old path's provenance bookkeeping goes to a scratch folder). It answers one
question: does `analysis/scan_jobs.py` + `analysis/scan.py` resolve the same inputs and give the same numbers as the engine's
per-cell path, on a real dataset with its ROI timeline, masks and chromatic correction?

  1. builds the plan for ALL cubes (reports setup time, frames, ignore-mask memory, distinct ROI layouts, warnings);
  2. picks a few cubes (spread over the run, one with an ignore mask, one per distinct ROI layout where possible) and a few ROIs;
  3. scans those cubes (one process) and computes the same cells with `compute_cell`;
  4. compares per (ROI, cube, wavelength, side): pixel counts must be IDENTICAL; the mean must agree to 1e-6 relative (the old code
     accumulates the mean in float32, the new one in float64: expected difference ~1e-7, see plan doc section 12).

A wavelength the old code flagged for low coverage has NaN there; those are counted and skipped, not compared.

    python tools/scan_golden_check.py --tiff "D:/run/images" --session "D:/run/images/sessions/2026-09-27_143549"
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import analysis_stage_timing as st  # noqa: E402  (also puts the app sources on sys.path)
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.analysis.tasks import compute_cell  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

_QT_APP = None


def pick_cubes(plan, source, wanted: int) -> list[int]:
    """Spread over the run, plus one cube with an ignore mask, plus one cube per distinct ROI layout."""
    cubes = list(plan.cube_indices)
    chosen: list[int] = [cubes[i] for i in np.linspace(0, len(cubes) - 1, wanted).astype(int)]
    seen_layouts: dict[str, int] = {}
    masked = None
    for job, cube in zip(plan.jobs, plan.cube_indices):
        seen_layouts.setdefault(job.rois.key, cube)
        if masked is None and any(f.ignore is not None for f in job.frames):
            masked = cube
    chosen += list(seen_layouts.values())
    if masked is not None:
        chosen.append(masked)
    return sorted(set(chosen))


def main() -> int:
    global _QT_APP
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiff", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--cubes", type=int, default=3, help="how many cubes spread over the run (layout and mask cubes come on top)")
    parser.add_argument("--rois", type=int, default=6, help="how many ROIs per cube (evenly spread)")
    parser.add_argument("--synthetic-timeline", action="store_true",
                        help="in memory only (never saved): move every ROI from cube 100 on, and 20 ROIs on cube 200 alone, so the "
                             "ROI-timeline path is checked even when the session has no timeline")
    args = parser.parse_args()

    _QT_APP = QApplication.instance() or QApplication([])
    dataset = st.load_one(args.tiff)
    modules, _state = st.build_modules(dataset, args.session)
    if args.synthetic_timeline:
        toolbox = modules.roi_toolbox
        for roi in list(toolbox.rois()):
            toolbox.move_roi(roi.area_roi_id, roi.center_x + 3.0, roi.center_y - 2.0, cube=100, scope="persistent")
        for roi in list(toolbox.rois())[:20]:
            toolbox.move_roi(roi.area_roi_id, roi.center_x + 1.5, roi.center_y + 1.5, cube=200, scope="individual")
        print(f"NOTE: synthetic ROI timeline added in memory (not saved); has_timeline = {toolbox.has_timeline()}")
    records = io.dataset_record_map(dataset)
    source = source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records,
    )
    engine = modules.analysis_engine
    mode = engine._reference_exclusion_mode()
    trimmed = engine._trimmed_mean_fraction

    st.header("1. plan for ALL cubes")
    started = time.perf_counter()
    full = build_scan_plan(source, exclusion_mode=mode, trimmed_fraction=trimmed)
    setup_s = time.perf_counter() - started
    layouts = {job.rois.key for job in full.jobs}
    masked_frames = sum(f.ignore is not None for job in full.jobs for f in job.frames)
    print(f"  {len(full.jobs)} cubes, {full.n_frames} frames, {len(full.roi_ids)} ROIs, {len(full.wavelength_axis_nm)} wavelengths, "
          f"{len(layouts)} distinct ROI layouts")
    print(f"  setup on the calling thread: {setup_s:.1f} s;  frames with an ignore mask: {masked_frames};  "
          f"ignore-mask memory: {full.ignore_mask_bytes / 1e6:.0f} MB")
    print(f"  warnings: {list(full.warnings) or 'none'}")

    chosen = pick_cubes(full, source, args.cubes)
    st.header(f"2. cells compared: cubes {chosen}, {args.rois} ROIs each")
    plan = build_scan_plan(source, cubes=chosen, exclusion_mode=mode, trimmed_fraction=trimmed)
    layout_numbers = {key: n for n, key in enumerate(dict.fromkeys(job.rois.key for job in full.jobs), start=1)}
    for job, cube in zip(plan.jobs, plan.cube_indices):
        print(f"  cube {cube:3d}: ROI layout #{layout_numbers[job.rois.key]}, frames with an ignore mask: "
              f"{sum(f.ignore is not None for f in job.frames)} of {len(job.frames)}")
    results = {r.cube_pos: r for r in scan_cubes(plan.jobs, plan.settings, processes=1)}

    det = modules.roi_toolbox.detection_settings()
    inner, outer = engine._default_reference_diameters()
    naming = engine._naming()
    timeline = bool(modules.roi_toolbox.has_timeline())
    compared = identical_counts = flagged = 0
    worst = 0.0
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as scratch_name:
        scratch = Path(scratch_name)
        for cube_pos, cube in enumerate(plan.cube_indices):
            rois_here = tuple(modules.roi_toolbox.rois_at(cube) if timeline else modules.roi_toolbox.rois())
            rows = np.linspace(0, len(rois_here) - 1, min(args.rois, len(rois_here))).astype(int)
            inputs = engine._gather_wavelength_inputs(cube)
            exclusion_cache: dict = {}
            result = results[cube_pos]
            positions = plan.axis_positions[cube_pos]
            for row in rows:
                cell = compute_cell(
                    rois_here[row], cube, inputs, reduction_method="mean", trimmed_mean_fraction=trimmed,
                    default_reference_inner_diameter_px=inner, default_reference_outer_diameter_px=outer,
                    masks_dir=scratch / "masks", chromatic_dir=scratch / "chromatic", settings_dir=scratch / "settings",
                    naming=naming, all_rois=rois_here, detection_settings=det, reference_exclusion_mode=mode,
                    sample_exclusion_cache=exclusion_cache, coverage_thresholds=engine._coverage_thresholds(),
                )
                if cell is None:
                    problems.append(f"cube {cube} ROI row {row}: compute_cell returned nothing")
                    continue
                wl_new = [plan.wavelength_axis_nm[p] for p in positions]
                if [round(w, 2) for w in cell.wavelengths_nm] != [round(w, 2) for w in wl_new]:
                    problems.append(f"cube {cube}: wavelengths differ {cell.wavelengths_nm[:3]} vs {wl_new[:3]}")
                    continue
                for k, cov in enumerate(cell.coverage or ()):
                    for side, old_value, n_nom, n_val in (
                        (0, cell.sample_values[k], cov.n_sample_nominal, cov.n_sample_valid),
                        (1, cell.reference_values[k], cov.n_reference_nominal, cov.n_reference_valid),
                    ):
                        compared += 1
                        same = result.n_nominal[row, k, side] == n_nom and result.n_valid[row, k, side] == n_val
                        identical_counts += bool(same)
                        if not same:
                            problems.append(f"cube {cube} roi row {row} wl {cell.wavelengths_nm[k]} side {side}: counts new "
                                            f"({result.n_nominal[row, k, side]}, {result.n_valid[row, k, side]}) old ({n_nom}, {n_val})")
                        new_value = result.values["mean"][row, k, side]
                        if np.isnan(old_value):
                            flagged += 1  # the old code withheld the value (coverage flag); nothing to compare
                            continue
                        rel = abs(new_value - old_value) / abs(old_value) if old_value else abs(new_value)
                        worst = max(worst, float(rel))
                        if not rel <= 1e-6:
                            problems.append(f"cube {cube} roi row {row} wl {cell.wavelengths_nm[k]} side {side}: mean new {new_value!r} old {old_value!r}")

    st.header("3. result")
    print(f"  compared {compared} (ROI, cube, wavelength, side) values over {len(plan.cube_indices)} cubes")
    print(f"  pixel counts identical: {identical_counts} of {compared}")
    print(f"  old code withheld {flagged} values for low coverage (not compared)")
    print(f"  largest relative difference of the mean: {worst:.2e}  (expected <= ~2e-7: float32 vs float64 mean)")
    for line in problems[:20]:
        print("  PROBLEM:", line)
    print("  VERDICT:", "MATCH" if not problems else f"{len(problems)} PROBLEM(S)")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
