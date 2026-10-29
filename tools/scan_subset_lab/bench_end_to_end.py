# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""End to end through the real ScanTask in Performance mode: move one (or a few) ROIs by 1 px, press "Fast scan", time it, and check the result.

Works on a COPY of the session's scan.h5 (never the real one) and never saves the session (autosave is suspended by `build_modules`).

    python bench_end_to_end.py <zarr> <session folder> [--rois 1] [--trials 3] [--cubes-check 8]

Checks after the last trial: (1) every cell the scan wrote for the moved ROIs equals an independent scan of the same cubes done the old way
(all ROIs, full layout, whole planes, one process); (2) the ignore-mask shortcut gives the old general path's mask for every real mask.
"""

from __future__ import annotations

import argparse
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
from lspr_imaging_app.analysis.scan_store import read_scan  # noqa: E402
from lspr_imaging_app.analysis.scan_task import ScanTask  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools.geometry import transform  # noqa: E402

_app = QApplication.instance() or QApplication([])


def pump(condition, timeout: float = 600.0) -> None:
    end = time.perf_counter() + timeout
    while not condition():
        _app.processEvents()
        time.sleep(0.005)
        if time.perf_counter() > end:
            raise TimeoutError("waited too long")


def _uncompressed_copy(source, exclusion_mode, trimmed, real_copy: Path, out: Path) -> Path:
    """A scan file with the numbers and hashes of ``real_copy`` but datasets without compression (experiment: what the writer costs then)."""
    import h5py

    from lspr_imaging_app.analysis.scan_arrays import new_arrays
    from lspr_imaging_app.analysis.scan_store import ScanStore, read_scan

    stored = read_scan(real_copy)
    plan = scan_jobs.build_scan_plan(source, exclusion_mode=exclusion_mode, trimmed_fraction=trimmed, resolve_masks=False)
    arrays = new_arrays(plan)
    arrays.values["mean"][:] = stored.values["mean"]
    arrays.std[:] = stored.std
    arrays.n_valid[:] = stored.n_valid
    arrays.n_nominal[:] = stored.n_nominal
    arrays.n_masked[:] = stored.n_masked
    original = h5py.Group.create_dataset

    def create(self, name, *a, **kw):
        for key in ("compression", "compression_opts", "shuffle"):
            kw.pop(key, None)
        return original(self, name, *a, **kw)

    h5py.Group.create_dataset = create
    try:
        store = ScanStore(out)
        store.prepare(plan, dataset_label="e2e")
    finally:
        h5py.Group.create_dataset = original
    n = len(plan.jobs)
    for s in range(0, n, 16):
        store.write_batch(plan, arrays, [(p, None, stored.cell_hash[p]) for p in range(s, min(s + 16, n))])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uncompressed", action="store_true", help="rebuild the scratch scan file without compression first (experiment)")
    ap.add_argument("zarr", type=Path)
    ap.add_argument("session", type=Path)
    ap.add_argument("--rois", type=int, default=1)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--cubes-check", type=int, default=8)
    ap.add_argument("--mode", default="performance", choices=("normal", "performance"))
    args = ap.parse_args()

    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    source = scan_jobs.source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
    )
    engine = modules.reference_inputs
    mode, trimmed = engine.reference_exclusion_mode(), engine.trimmed_mean_fraction

    import logging

    events: list = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if getattr(record, "lspr_event", "") in ("analysis.scan_prepared", "analysis.scan_done"):
                events.append((record.lspr_event, record.getMessage()))

    app_logger = logging.getLogger("lspr_imaging_app")
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(_Capture())

    scratch = Path(tempfile.mkdtemp(prefix="lspr_e2e_"))
    store_path = scratch / "scan.h5"
    shutil.copy2(args.session / "analysis" / "scan.h5", store_path)
    if args.uncompressed:
        store_path = _uncompressed_copy(source, mode, trimmed, store_path, scratch / "scan_uncompressed.h5")
        print(f"scratch scan file rebuilt without compression: {store_path.stat().st_size / 1e6:.1f} MB")
    try:
        results: list = []
        finished: list = []
        failed: list = []
        states: list = []
        task = ScanTask(on_result=lambda plan, arrays, summary: results.append(summary))
        task.task_finished.connect(lambda *a: finished.append(a))
        task.failed.connect(lambda m: failed.append(m))
        task.pool_state_changed.connect(lambda s, n: states.append((s, n)))
        t0 = time.perf_counter()
        task.set_mode(args.mode)
        if args.mode == "performance":
            pump(lambda: any(s in ("ready", "failed") for s, _ in states))
            print(f"pool: {states[-1]} after {time.perf_counter() - t0:.1f} s")

        def run(label: str) -> object:
            finished.clear()
            results.clear()
            started = time.perf_counter()
            task.start(source, store_path=store_path, only_stale=True, dataset_label="e2e")
            pump(lambda: bool(finished or failed))
            wall = time.perf_counter() - started
            if failed:
                raise SystemExit(f"scan failed: {failed}")
            s = results[0]
            print(
                f"  {label:<26} wall {wall:5.2f} s (task {s.seconds:5.2f} s; first cube {s.first_cube_seconds:4.2f} s) "
                f"{s.cubes} cubes, {s.rois_scanned} ROI(s), {s.frames} frames, {s.workers} workers, chunk reads {s.partial_frames}/{s.frames}"
            )
            for name, message in events:
                print(f"      {message[:330]}")
            events.clear()
            return s

        run("scan (should be current)")
        rois = list(modules.roi_toolbox.rois())
        picks = [rois[i] for i in np.linspace(5, len(rois) - 6, args.rois).astype(int)]
        for trial in range(1, args.trials + 1):
            for roi in picks:
                now = next(r for r in modules.roi_toolbox.rois() if r.area_roi_id == roi.area_roi_id)
                modules.roi_toolbox.move_roi(roi.area_roi_id, now.center_x + 1.0, now.center_y)
            run(f"{args.rois} ROI(s) moved, trial {trial}")
        task.shutdown()

        # (1) the written cells against the old way of scanning
        stored = read_scan(store_path)
        n_cubes = len(stored.cube_indices)
        cubes = [int(stored.cube_indices[i]) for i in np.linspace(3, n_cubes - 4, args.cubes_check).astype(int)]
        plan = scan_jobs.build_scan_plan(source, cubes=cubes, exclusion_mode=mode, trimmed_fraction=trimmed)
        reference = {r.cube_pos: r for r in scan_cubes(plan.jobs, replace(plan.settings, partial_reads=False), processes=1)}
        roi_ids = list(stored.roi_ids)
        wrong = checked = 0
        for p, cube in enumerate(plan.cube_indices):
            row = stored.cube_indices.index(cube)
            columns = plan.axis_positions[p]
            for roi in picks:
                j = roi_ids.index(roi.area_roi_id)
                for name, ours, theirs in (
                    ("mean", stored.values["mean"][row, j][list(columns)], reference[p].values["mean"][j]),
                    ("std", stored.std[row, j][list(columns)], reference[p].std[j]),
                    ("n_valid", stored.n_valid[row, j][list(columns)], reference[p].n_valid[j]),
                    ("n_nominal", stored.n_nominal[row, j][list(columns)], reference[p].n_nominal[j]),
                ):
                    checked += 1
                    wrong += 0 if np.array_equal(ours.astype(np.float64), theirs.astype(np.float64), equal_nan=True) else 1
        print(f"check 1: {checked} arrays (moved ROIs x {len(cubes)} cubes x mean/std/counts) compared with a full-layout whole-plane scan: "
              f"{'ALL IDENTICAL' if wrong == 0 else f'{wrong} DIFFER'}")

        # (2) the ignore-mask shortcut against the general path, on every real mask the plan resolves
        calls: list = []
        original = scan_jobs.resolve_external_mask

        def record(pixels, geometry, warp, **keywords):
            calls.append((np.asarray(pixels), geometry, warp))
            return original(pixels, geometry, warp, **keywords)

        scan_jobs.resolve_external_mask = record
        try:
            scan_jobs.build_scan_plan(source, exclusion_mode=mode, trimmed_fraction=trimmed)
        finally:
            scan_jobs.resolve_external_mask = original
        bad = 0
        for pixels, geometry, warp in calls:
            if warp is not None or not transform.spatial_transform_is_identity(geometry):
                continue
            old = transform._apply_spatial_transform(pixels.astype(np.float32, copy=False), geometry, order=0, mode="constant", cval=0.0) >= 0.5
            bad += 0 if np.array_equal(transform.apply_spatial_mask(np.asarray(pixels, dtype=bool), geometry), old) else 1
        print(f"check 2: {len(calls)} real ignore masks, shortcut vs the general path: {'ALL IDENTICAL' if bad == 0 else f'{bad} DIFFER'}")
        return 0 if wrong == 0 and bad == 0 else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
