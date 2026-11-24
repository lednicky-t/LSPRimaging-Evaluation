"""Read-only: scan step 1 of the current session twice (with the ignore mask / without), in memory only. Result -> .npz.
usage: mask_scan.py OUT.npz FIRST_CUBE N_CUBES ROI_IDS(comma)"""

from __future__ import annotations
import dataclasses
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"c:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva\tools")
import analysis_stage_timing as st
import h5py
import numpy as np
from PyQt6.QtWidgets import QApplication
from lspr_imaging_app.analysis.scan import scan_cubes
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
from lspr_imaging_app.dataset import io

DATA = Path(
    r"C:\Users\Admin\Desktop\Data_PyTest\LbL\05_LbL_pumpplan_Ch1_PAH_Ch2_PSS_Ch3_4_buffer_20ulpermin_60cycles_v2_LED_V2_1279x879_2655x27_c80IZ_u16.ome.zarr"
)
SESSION = DATA / "sessions" / "2026-11-20_174644"
_QT_APP = None


def run(src, cubes, mode, trimmed):
    plan = build_scan_plan(src, cubes=cubes, exclusion_mode=mode, trimmed_fraction=trimmed)
    print(f"  plan: {len(plan.jobs)} cubes, {plan.n_frames} frames, {len(plan.roi_ids)} ROIs, warnings={list(plan.warnings) or 'none'}")
    n_wl = len(plan.wavelength_axis_nm)
    shape = (len(cubes), len(plan.roi_ids), n_wl, 2)
    out = {k: np.full(shape, np.nan) for k in ("mean", "std")}
    for k in ("nval", "nmask", "nnom"):
        out[k] = np.zeros(shape, dtype=np.uint32)
    t0 = time.perf_counter()
    for res in scan_cubes(plan.jobs, plan.settings, processes=4):
        cols = list(plan.axis_positions[res.cube_pos])
        out["mean"][res.cube_pos][:, cols, :] = res.values["mean"]
        out["std"][res.cube_pos][:, cols, :] = res.std
        out["nval"][res.cube_pos][:, cols, :] = res.n_valid
        out["nmask"][res.cube_pos][:, cols, :] = res.n_masked
        out["nnom"][res.cube_pos][:, cols, :] = res.n_nominal
    print(f"  scan done in {time.perf_counter() - t0:.0f} s")
    return plan, out


def main() -> None:
    OUT = Path(sys.argv[1])
    first, n_cubes = int(sys.argv[2]), int(sys.argv[3])
    bases = None if sys.argv[4] == "all" else {int(x) for x in sys.argv[4].split(",")}
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    dataset = st.load_one(DATA)
    modules, _ = st.build_modules(dataset, SESSION)
    # autosave must be off, or an edit would be written into the real session
    print("autosave suspended:", getattr(modules, "session_autosave", None) and modules.session_autosave._suspended)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox,
        geometry=modules.geometry, background=modules.background, chromatic=modules.chromatic,
        mask=modules.mask, records=records,
    )
    eng = modules.reference_inputs
    mode, trimmed = eng.reference_exclusion_mode(), eng.trimmed_mean_fraction
    timeline = bool(source.has_timeline())
    all_rois = list(modules.roi_toolbox.rois())
    print("exclusion mode:", mode, "| timeline:", timeline, "|", len(all_rois), "ROIs")
    if bases is None:
        bases = {int(r.area_roi_id) for r in all_rois}
    base_rois = [r for r in all_rois if int(r.area_roi_id) in bases]
    near = {
        int(r.area_roi_id) for r in all_rois
        if any(math.hypot(r.center_x - b.center_x, r.center_y - b.center_y) < 220 for b in base_rois)
    } | bases
    print("ROIs scanned (bases + neighbours within 220 px):", sorted(near))

    def rois_at(cube):
        return [r for r in (source.rois_at(cube) if timeline else all_rois) if int(r.area_roi_id) in near]

    src_with = dataclasses.replace(source, rois=lambda: rois_at(0), rois_at=rois_at)
    src_without = dataclasses.replace(src_with, resolve_mask=lambda c, w: None)
    cubes = list(range(first, first + n_cubes))
    print("with the ignore mask:")
    plan, w = run(src_with, cubes, mode, trimmed)
    print("without the ignore mask:")
    plan2, wo = run(src_without, cubes, mode, trimmed)
    assert list(plan.roi_ids) == list(plan2.roi_ids)
    np.savez(
        OUT,
        **{f"{k}_with": v for k, v in w.items()}, **{f"{k}_without": v for k, v in wo.items()},
        roi_ids=np.array(plan.roi_ids), wl=np.array(plan.wavelength_axis_nm), cubes=np.array(cubes),
        base_ids=np.array(sorted(bases)),
    )
    # check: the NO-mask rescan against the stored scan.h5, cells where the stored scan masked nothing (the stored scan is older than the current mask)
    h = h5py.File(SESSION / "analysis" / "scan.h5", "r")
    rid = list(h["axes/roi_id"][:])
    worst, nc, same = 0.0, 0, 0
    for i, roi_id in enumerate(plan.roi_ids):
        j = rid.index(int(roi_id))
        sl = slice(cubes[0], cubes[-1] + 1)
        sm = h["processed/scan/value/mean"][sl, j]
        sn = h["processed/scan/n_valid"][sl, j]
        sk = h["processed/scan/n_masked"][sl, j]
        ok = np.isfinite(sm) & np.isfinite(wo["mean"][:, i]) & (sk == 0)
        same += int(np.sum(sn[ok] == wo["nval"][:, i][ok]))
        nc += int(ok.sum())
        if ok.any():
            worst = max(worst, float(np.max(np.abs(sm[ok] - wo["mean"][:, i][ok]) / np.abs(sm[ok]))))
    print(f"check NO-mask rescan vs stored scan.h5 (cells the stored scan did not mask): {nc} values, valid-pixel counts identical in {same}; worst relative difference of the mean {worst:.2e}")


if __name__ == "__main__":
    main()
