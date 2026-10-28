# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""A mask edit through the real ScanTask on a COPY of the scan file: how many cells does it make stale, how long does the Fast scan take?

  1. the real file as it is (stamped with the earlier hash formula): is it current, and is it restamped without scanning?
  2. a blob added to the ignore masks of ``--cubes`` consecutive cubes (all wavelengths) next to ONE ROI (the persistent-mask case);
     the same edit counted under the previous formula (``local_masks=False``) for comparison;
  3. the file after the Fast scan equals an independent full scan of those cubes (all ROIs, old way, one process).

    python bench_mask_edit.py <zarr> <session folder> [--cubes 20] [--roi-index 80]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.analysis import scan_jobs  # noqa: E402
from lspr_imaging_app.analysis.scan import scan_cubes  # noqa: E402
from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import MaskRef  # noqa: E402
from lspr_imaging_app.analysis.scan_store import read_scan  # noqa: E402
from lspr_imaging_app.analysis.scan_task import ScanTask  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

_app = QApplication.instance() or QApplication([])


def pump(condition, timeout: float = 900.0) -> None:
    end = time.perf_counter() + timeout
    while not condition():
        _app.processEvents()
        time.sleep(0.005)
        if time.perf_counter() > end:
            raise TimeoutError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zarr", type=Path)
    ap.add_argument("session", type=Path)
    ap.add_argument("--cubes", type=int, default=20)
    ap.add_argument("--roi-index", type=int, default=80)
    ap.add_argument("--mode", default="performance", choices=("normal", "performance"))
    args = ap.parse_args()

    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    source = scan_jobs.source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
    )
    engine = modules.analysis_engine
    mode, trimmed = engine._reference_exclusion_mode(), engine._trimmed_mean_fraction

    events: list = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if getattr(record, "lspr_event", "") in ("analysis.scan_prepared", "analysis.scan_done", "analysis.scan_current"):
                events.append(record.getMessage())

    logger = logging.getLogger("lspr_imaging_app")
    logger.setLevel(logging.INFO)
    logger.addHandler(_Capture())

    scratch = Path(tempfile.mkdtemp(prefix="lspr_maskedit_"))
    store_path = scratch / "scan.h5"
    shutil.copy2(args.session / "analysis" / "scan.h5", store_path)
    try:
        finished: list = []
        results: list = []
        failed: list = []
        states: list = []
        task = ScanTask(on_result=lambda plan, arrays, summary: results.append(summary))
        task.task_finished.connect(lambda *a: finished.append(a))
        task.failed.connect(failed.append)
        task.pool_state_changed.connect(lambda s, n: states.append((s, n)))
        task.set_mode(args.mode)
        if args.mode == "performance":
            pump(lambda: any(s in ("ready", "failed") for s, _ in states))

        def run(label: str, src) -> None:
            finished.clear()
            results.clear()
            events.clear()
            t = time.perf_counter()
            task.start(src, store_path=store_path, only_stale=True, dataset_label="mask-edit")
            pump(lambda: bool(finished or failed))
            if failed:
                raise SystemExit(f"failed: {failed}")
            s = results[0]
            print(f"{label}: {time.perf_counter() - t:.2f} s; {s.cubes} cubes scanned, {s.rois_scanned} ROI(s) per cube, {s.frames} frames; {finished[0][2][:90]}")
            for line in events:
                keep = [w for w in line.split() if w.split("=")[0] in ("dur_ms", "stale_cubes", "stale_cells", "restamped_cells", "seconds", "cubes_current")]
                print("      " + " ".join(keep))

        print("== 1. the real file, stamped by the earlier formula")
        before_file = read_scan(store_path)
        run("Fast scan, nothing changed", source)
        after_file = read_scan(store_path)
        print(f"      numbers untouched: {np.array_equal(before_file.values['mean'], after_file.values['mean'], equal_nan=True)}; "
              f"stamps changed in {int((before_file.cell_hash != after_file.cell_hash).any(axis=1).sum())} cubes (those with ignore masks)")
        run("Fast scan again", source)

        print(f"== 2. a blob added to the masks of {args.cubes} cubes next to ROI {args.roi_index}")
        roi = list(modules.roi_toolbox.rois())[args.roi_index]
        cubes = list(modules.dataset.spectral_cubes())
        edited_cubes = set(cubes[100:100 + args.cubes])
        records = io.dataset_record_map(dataset)
        frame_shape = io.load_image_array_uncached(records[next(iter(records))].path).shape  # read once, not per frame
        original = source.resolve_mask

        def edited(cube, wl):
            if cube not in edited_cubes:
                return original(cube, wl)
            ref = original(cube, wl)
            extra = np.zeros(frame_shape, dtype=bool)
            y, x = int(roi.center_y), int(roi.center_x)
            extra[max(y - 6, 0):y + 6, max(x - 6, 0):x + 6] = True
            base = None if ref is None else ref.load()
            return MaskRef((cube, wl), "individual", lambda e=extra, bm=base: e if bm is None else (e | bm))

        edited_source = replace(source, resolve_mask=edited)
        plan_base = scan_jobs.build_scan_plan(source, resolve_masks=False, exclusion_mode=mode, trimmed_fraction=trimmed)
        plan_new = scan_jobs.build_scan_plan(edited_source, resolve_masks=False, exclusion_mode=mode, trimmed_fraction=trimmed)
        # each formula against its own unedited baseline: the cells the edit makes stale
        new_stale = int((cell_hashes(plan_new) != cell_hashes(plan_base)).sum())
        old_stale = int((cell_hashes(plan_new, local_masks=False) != cell_hashes(plan_base, local_masks=False)).sum())
        print(f"      stale cells: {new_stale} with the local mask hash, {old_stale} with the whole-mask hash used until now "
              f"({old_stale / max(new_stale, 1):.0f}x more to scan)")
        run("Fast scan after the edit", edited_source)
        task.shutdown()

        print("== 3. the file against an independent scan of the edited cubes (all ROIs, whole planes, one process)")
        pick = sorted(edited_cubes)[:: max(len(edited_cubes) // 3, 1)][:3]
        plan = scan_jobs.build_scan_plan(edited_source, cubes=pick, exclusion_mode=mode, trimmed_fraction=trimmed)
        reference = {r.cube_pos: r for r in scan_cubes(plan.jobs, replace(plan.settings, partial_reads=False), processes=1)}
        stored_scan = read_scan(store_path)
        wrong = 0
        for p, cube in enumerate(plan.cube_indices):
            row = stored_scan.cube_indices.index(cube)
            columns = list(plan.axis_positions[p])
            for name, ours, theirs in (
                ("mean", stored_scan.values["mean"][row][:, columns], reference[p].values["mean"]),
                ("std", stored_scan.std[row][:, columns], reference[p].std),
                ("n_valid", stored_scan.n_valid[row][:, columns], reference[p].n_valid),
                ("n_masked", stored_scan.n_masked[row][:, columns], reference[p].n_masked),
            ):
                wrong += 0 if np.array_equal(ours.astype(np.float64), theirs.astype(np.float64), equal_nan=True) else 1
        print(f"      cubes {pick}: {'ALL 160 ROIs IDENTICAL to a full scan' if wrong == 0 else f'{wrong} arrays DIFFER'}")
        return 0 if wrong == 0 else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
