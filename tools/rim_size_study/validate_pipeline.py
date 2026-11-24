"""Does my in-memory scan reproduce the stored scan.h5 when given the stored ROI layout? (read-only)"""

from __future__ import annotations
import dataclasses
import json
import math
import os
import sys
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

DATA = Path(
    r"C:\Users\Admin\Desktop\Data_PyTest\LbL\05_LbL_pumpplan_Ch1_PAH_Ch2_PSS_Ch3_4_buffer_20ulpermin_60cycles_v2_LED_V2_1279x879_2655x27_c80IZ_u16.ome.zarr"
)
SESSION = DATA / "sessions" / "2026-11-20_174644"

_QT_APP = None


def main():
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
    f = h5py.File(SESSION / "analysis" / "scan.h5", "r")
    layout = json.loads(f["metadata/roi_layouts/1"][()])["rois"]
    by_id = {int(r.area_roi_id): r for r in modules.roi_toolbox.rois()}
    stored = {int(q["roi_id"]): q for q in layout}
    c75 = stored[75]
    near = [
        i
        for i, q in stored.items()
        if math.hypot(q["center_x"] - c75["center_x"], q["center_y"] - c75["center_y"])
        < 220
    ]
    rois = []
    for i in near:
        q = stored[i]
        b = q["border"]
        rois.append(
            dataclasses.replace(
                by_id[i],
                center_x=q["center_x"],
                center_y=q["center_y"],
                shape=Circle(q["shape"]["diameter_px"]),
                border=Border(b["gap_px"], b["thickness_px"], b["enabled"]),
            )
        )
    src = dataclasses.replace(
        source,
        rois=lambda: rois,
        rois_at=lambda c: rois,
        has_timeline=lambda: False,
        resolve_mask=lambda c, w: None,
    )
    plan = build_scan_plan(
        src,
        cubes=list(range(4)),
        exclusion_mode=eng.reference_exclusion_mode(),
        trimmed_fraction=eng.trimmed_mean_fraction,
    )
    ids = list(plan.roi_ids)
    rid = list(f["axes/roi_id"][:])
    worst = 0.0
    same_n = True
    for res in scan_cubes(plan.jobs, plan.settings, processes=4):
        cols = list(plan.axis_positions[res.cube_pos])
        for row, roi_id in enumerate(ids):
            j = rid.index(roi_id)
            m = f["processed/scan/value/mean"][res.cube_pos, j][cols]
            n = f["processed/scan/n_valid"][res.cube_pos, j][cols]
            nn = f["processed/scan/n_nominal"][res.cube_pos, j][cols]
            if roi_id == 75 and res.cube_pos == 0:
                print(
                    "ROI75 cube0 sample: stored nominal",
                    nn[:6, 0],
                    "mine nominal",
                    res.n_nominal[row][:6, 0],
                    "mine masked",
                    res.n_masked[row][:6, 0],
                    "stored valid",
                    n[:6, 0],
                    "mine valid",
                    res.n_valid[row][:6, 0],
                )
            same_nom = bool(np.array_equal(nn, res.n_nominal[row]))
            nom_ok = globals().setdefault("nom_ok", [])
            nom_ok.append(same_nom)
            nm_st = f["processed/scan/n_masked"][res.cube_pos, j][cols]
            keep = nm_st == 0
            same_n &= bool(np.array_equal(n[keep], res.n_valid[row][keep]))
            globals().setdefault("kept", []).append(int(keep.sum()))
            ok = np.isfinite(m) & np.isfinite(res.values["mean"][row]) & (nm_st == 0)
            worst = max(
                worst,
                float(
                    np.max(np.abs(m[ok] - res.values["mean"][row][ok]) / np.abs(m[ok]))
                ),
            )
    print(
        "nominal counts identical in",
        sum(globals()["nom_ok"]),
        "of",
        len(globals()["nom_ok"]),
        "ROI-cubes",
    )
    print("cells compared (stored had no masked pixels):", sum(globals()["kept"]))
    print(
        f"ROIs compared: {len(ids)}, cubes 0-3 | pixel counts identical: {same_n} | worst relative difference of the mean: {worst:.2e}"
    )


if __name__ == "__main__":
    main()
