"""Read-only: scan scattered ROIs of a dataset with rims of different size, one step's cubes. In memory only; result -> .npz.
usage: rim_scan2.py DATA SESSION OUT.npz FIRST_CUBE N_CUBES [roi ids comma-separated | 'auto:N']"""

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
from lspr_imaging_app.domain.roi_shapes import Border, Circle


_QT_APP = None


def main() -> None:
    DATA, SESSION, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    first, n_cubes, pick = int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    dataset = st.load_one(DATA)
    modules, _ = st.build_modules(dataset, SESSION)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(
        dataset=modules.dataset,
        frame_roles=modules.frame_roles,
        roi_toolbox=modules.roi_toolbox,
        geometry=modules.geometry,
        background=modules.background,
        chromatic=modules.chromatic,
        mask=modules.mask,
        records=records,
    )
    eng = modules.reference_inputs
    mode, trimmed = eng.reference_exclusion_mode(), eng.trimmed_mean_fraction
    timeline = bool(source.has_timeline())
    all_rois = list(modules.roi_toolbox.rois())
    print("exclusion mode:", mode, "| timeline:", timeline, "|", len(all_rois), "ROIs")
    default_border = eng.default_border()
    if pick.startswith("auto:"):
        want = int(pick[5:])
        # eligible: circle sample ROIs with their own rim, no timeline, not near the image edge (so no variant is clipped)
        elig = [
            r
            for r in all_rois
            if isinstance(r.shape, Circle)
            and r.role == "sample"
            and not r.reference_roi_ids
            and not r.timeline
            and (r.border is None or r.border.enabled)
        ]
        cols, rows = {4: (4, 3), 12: (4, 3), 8: (4, 2), 6: (3, 2)}.get(want, (4, 3))
        W, H = float(sys.argv[7]), float(sys.argv[8])
        elig = [
            r for r in elig if 75 <= r.center_x <= W - 75 and 75 <= r.center_y <= H - 75
        ]
        chosen = []
        for i in range(cols):
            for j in range(rows):
                tx, ty = W * (i + 0.5) / cols, H * (j + 0.5) / rows
                best = min(
                    (r for r in elig if r not in chosen),
                    key=lambda r: math.hypot(r.center_x - tx, r.center_y - ty),
                )
                chosen.append(best)
        bases = chosen
    else:
        bases = [
            r for r in all_rois if r.area_roi_id in {int(x) for x in pick.split(",")}
        ]
    print(
        "bases:",
        [(int(r.area_roi_id), round(r.center_x), round(r.center_y)) for r in bases],
    )

    extra = []
    labels = {}
    for b in bases:
        R = b.shape.diameter_px / 2.0
        b0 = b.border if b.border is not None else default_border

        def thick(gap, k):
            r_in = R + gap
            return math.sqrt(r_in * r_in + k * R * R) - r_in

        vs = [
            (f"A k={k}", b0.gap_px, thick(b0.gap_px, k))
            for k in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
        ]
        vs += [
            (f"B gap={g:g} k=1", g, thick(g, 1.0)) for g in (0.0, 3.0, 15.0, 25.0, 40.0)
        ]
        for i, (lab, gap, t) in enumerate(vs):
            vid = 100000 + int(b.area_roi_id) * 100 + i
            extra.append(
                dataclasses.replace(
                    b, area_roi_id=vid, border=Border(gap, t, True), timeline=None
                )
            )
            labels[vid] = f"{int(b.area_roi_id)}|{lab}"
        labels[int(b.area_roi_id)] = f"{int(b.area_roi_id)}|orig"
    near_ids = {
        int(r.area_roi_id)
        for r in all_rois
        if any(
            math.hypot(r.center_x - b.center_x, r.center_y - b.center_y) < 220
            for b in bases
        )
    }
    near_ids |= {int(b.area_roi_id) for b in bases}

    def rois_at(cube):
        return [
            r
            for r in (source.rois_at(cube) if timeline else all_rois)
            if int(r.area_roi_id) in near_ids
        ] + extra

    src = dataclasses.replace(source, rois=lambda: rois_at(0), rois_at=rois_at)
    cubes = list(range(first, first + n_cubes))
    t0 = time.perf_counter()
    plan = build_scan_plan(
        src, cubes=cubes, exclusion_mode=mode, trimmed_fraction=trimmed
    )
    print(
        f"plan: {len(plan.jobs)} cubes, {plan.n_frames} frames, {len(plan.roi_ids)} ROIs, warnings={list(plan.warnings) or 'none'} ({time.perf_counter() - t0:.1f} s)"
    )
    n_wl = len(plan.wavelength_axis_nm)
    shape = (len(cubes), len(plan.roi_ids), n_wl, 2)
    mean, std = np.full(shape, np.nan), np.full(shape, np.nan)
    nval, nmask, nnom = (np.zeros(shape, dtype=np.uint32) for _ in range(3))
    t0 = time.perf_counter()
    for res in scan_cubes(plan.jobs, plan.settings, processes=4):
        cols_ = list(plan.axis_positions[res.cube_pos])
        mean[res.cube_pos][:, cols_, :] = res.values["mean"]
        std[res.cube_pos][:, cols_, :] = res.std
        nval[res.cube_pos][:, cols_, :] = res.n_valid
        nmask[res.cube_pos][:, cols_, :] = res.n_masked
        nnom[res.cube_pos][:, cols_, :] = res.n_nominal
    print(f"scan done in {time.perf_counter() - t0:.0f} s")
    np.savez(
        OUT,
        mean=mean,
        std=std,
        nval=nval,
        nmask=nmask,
        nnom=nnom,
        roi_ids=np.array(plan.roi_ids),
        wl=np.array(plan.wavelength_axis_nm),
        cubes=np.array(cubes),
        labels=np.array([labels.get(int(i), "") for i in plan.roi_ids]),
        sample_area=np.array([math.pi * (b.shape.diameter_px / 2) ** 2 for b in bases]),
        base_ids=np.array([int(b.area_roi_id) for b in bases]),
        base_xy=np.array([[b.center_x, b.center_y] for b in bases]),
    )
    # check against the stored scan: the unchanged ROIs, cells with no masked pixel on either side
    h = h5py.File(SESSION / "analysis" / "scan.h5", "r")
    rid = list(h["axes/roi_id"][:])
    worst, cells, same_geo, tot = 0.0, 0, 0, 0
    for i, roi_id in enumerate(plan.roi_ids):
        if int(roi_id) not in {int(b.area_roi_id) for b in bases}:
            continue
        j = rid.index(int(roi_id))
        sm = h["processed/scan/value/mean"][cubes[0] : cubes[-1] + 1, j]
        sn = h["processed/scan/n_nominal"][cubes[0] : cubes[-1] + 1, j]
        sk = h["processed/scan/n_masked"][cubes[0] : cubes[-1] + 1, j]
        ok = (sk == 0) & (nmask[:, i] == 0) & np.isfinite(sm)
        tot += ok.size
        same_geo += int(np.sum((sn == nnom[:, i]) & ok))
        good = ok & (sn == nnom[:, i])
        if good.any():
            cells += int(good.sum())
            worst = max(
                worst,
                float(np.max(np.abs(sm[good] - mean[:, i][good]) / np.abs(sm[good]))),
            )
    print(
        f"check vs stored scan (unmasked cells of the base ROIs): {cells} of {tot} cells have the same geometry; worst relative difference of the mean {worst:.2e}"
    )


if __name__ == "__main__":
    main()
