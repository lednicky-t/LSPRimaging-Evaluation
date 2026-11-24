"""Read-only experiment: scan ROI 75 of the LbL dataset with reference rims of different sizes, cubes 0..143.

Uses the app's own scan machinery in memory (autosave suspended by st.build_modules). Nothing is written to the dataset or session;
the result goes to an .npz in the scratchpad.
"""

from __future__ import annotations

import dataclasses
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
EVA = Path(r"c:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva")
sys.path.insert(0, str(EVA / "tools"))

import analysis_stage_timing as st  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.domain.roi_shapes import Border  # noqa: E402

DATA = Path(
    r"C:\Users\Admin\Desktop\Data_PyTest\LbL\05_LbL_pumpplan_Ch1_PAH_Ch2_PSS_Ch3_4_buffer_20ulpermin_60cycles_v2_LED_V2_1279x879_2655x27_c80IZ_u16.ome.zarr"
)
SESSION = DATA / "sessions" / "2026-11-20_174644"
_QT_APP = None


def main() -> None:
    OUT = Path(sys.argv[1])
    ROI_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 75
    N_CUBES = int(sys.argv[3]) if len(sys.argv) > 3 else 144

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
    engine = modules.reference_inputs
    mode = engine.reference_exclusion_mode()
    trimmed = engine.trimmed_mean_fraction
    print("exclusion mode:", mode, "| timeline:", modules.roi_toolbox.has_timeline())

    all_rois = list(modules.roi_toolbox.rois())
    base = next(r for r in all_rois if r.area_roi_id == ROI_ID)
    R = base.shape.diameter_px / 2.0
    sample_area = math.pi * R * R
    default_border = engine.default_border()
    b0 = base.border if base.border is not None else default_border
    print(
        f"ROI {ROI_ID}: centre ({base.center_x}, {base.center_y}), sample diameter {base.shape.diameter_px:.2f} px, "
        f"border gap {b0.gap_px:.2f}, thickness {b0.thickness_px:.2f}"
    )

    def thickness_for_area(gap: float, area_ratio: float) -> float:
        r_in = R + gap
        return math.sqrt(r_in * r_in + area_ratio * R * R) - r_in

    variants: list[tuple[str, float, float]] = []  # (label, gap, thickness)
    for k in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        variants.append((f"A k={k}", b0.gap_px, thickness_for_area(b0.gap_px, k)))
    for gap in (0.0, 3.0, 15.0, 25.0, 40.0):
        variants.append((f"B gap={gap:g} k=1", gap, thickness_for_area(gap, 1.0)))
    for k in (2.0, 4.0):
        variants.append((f"C gap=15 k={k}", 15.0, thickness_for_area(15.0, k)))

    near = [
        r
        for r in all_rois
        if math.hypot(r.center_x - base.center_x, r.center_y - base.center_y) < 220.0
    ]
    rois = list(near)
    labels = {int(base.area_roi_id): "orig"}
    for i, (label, gap, thick) in enumerate(variants):
        new_id = 100000 + i
        rois.append(
            dataclasses.replace(
                base, area_roi_id=new_id, border=Border(gap, thick, True)
            )
        )
        labels[new_id] = label
        area = math.pi * ((R + gap + thick) ** 2 - (R + gap) ** 2)
        print(
            f"  variant {new_id}: {label:18s} inner r {R + gap:5.1f}  outer r {R + gap + thick:5.1f}  area/sample = {area / sample_area:.2f}"
        )
    print(
        f"{len(near)} real ROIs within 220 px + {len(variants)} variants of ROI {ROI_ID}"
    )

    src = dataclasses.replace(
        source, rois=lambda: rois, rois_at=lambda cube: rois, has_timeline=lambda: False
    )
    cubes = list(
        range(
            int(sys.argv[4]) if len(sys.argv) > 4 else 0,
            (int(sys.argv[4]) if len(sys.argv) > 4 else 0) + N_CUBES,
        )
    )
    t0 = time.perf_counter()
    plan = build_scan_plan(
        src, cubes=cubes, exclusion_mode=mode, trimmed_fraction=trimmed
    )
    print(
        f"plan: {len(plan.jobs)} cubes, {plan.n_frames} frames, {len(plan.roi_ids)} ROIs, warnings={list(plan.warnings) or 'none'} ({time.perf_counter() - t0:.1f} s)"
    )

    n_wl = len(plan.wavelength_axis_nm)
    shape = (len(cubes), len(plan.roi_ids), n_wl, 2)
    mean = np.full(shape, np.nan)
    std = np.full(shape, np.nan)
    nval = np.zeros(shape, dtype=np.uint32)
    t0 = time.perf_counter()
    done = 0
    for res in scan_cubes(plan.jobs, plan.settings, processes=4):
        cols = list(plan.axis_positions[res.cube_pos])
        mean[res.cube_pos][:, cols, :] = res.values["mean"]
        std[res.cube_pos][:, cols, :] = res.std
        nval[res.cube_pos][:, cols, :] = res.n_valid
        done += 1
        if done % 12 == 0:
            print(
                f"  {done}/{len(cubes)} cubes, {time.perf_counter() - t0:.0f} s",
                flush=True,
            )
    print(f"scan done in {time.perf_counter() - t0:.0f} s")
    np.savez(
        OUT,
        mean=mean,
        std=std,
        nval=nval,
        roi_ids=np.array(plan.roi_ids),
        wl=np.array(plan.wavelength_axis_nm),
        cubes=np.array(cubes),
        labels=np.array([labels.get(int(i), "") for i in plan.roi_ids]),
        sample_area=sample_area,
    )
    print("saved", OUT)


if __name__ == "__main__":
    main()
