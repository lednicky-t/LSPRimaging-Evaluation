# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""Scan-file writer: does a one-ROI refresh make the file grow, and what do other compression settings cost?

Builds a NEW scan file per variant (in a temp folder) from the numbers of the real scan.h5, writes everything once, then refreshes ONE ROI of all
cubes several times (batches of 16, as `ScanWriter` does) and reports seconds per refresh and file size after each. Nothing real is written.

    python bench_writer_variants.py <zarr> <session folder> [--trials 4]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import analysis_stage_timing as st  # noqa: E402
import h5py  # noqa: E402
import numpy as np  # noqa: E402

from lspr_imaging_app.analysis.scan_arrays import new_arrays  # noqa: E402
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules  # noqa: E402
from lspr_imaging_app.analysis.scan_store import ScanStore, read_scan  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402

VARIANTS = {
    "gzip 4 + shuffle (today)": {},
    "gzip 1 + shuffle": {"compression_opts": 1},
    "lzf + shuffle": {"compression": "lzf", "compression_opts": None},
    "no compression": {"compression": None, "compression_opts": None, "shuffle": False},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zarr", type=Path)
    ap.add_argument("session", type=Path)
    ap.add_argument("--trials", type=int, default=4)
    args = ap.parse_args()
    dataset = st.load_one(args.zarr)
    modules, _ = st.build_modules(dataset, args.session)
    source = source_from_modules(
        dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=io.dataset_record_map(dataset),
    )
    engine = modules.reference_inputs
    plan = build_scan_plan(source, exclusion_mode=engine.reference_exclusion_mode(), trimmed_fraction=engine.trimmed_mean_fraction, resolve_masks=False)
    scratch = Path(tempfile.mkdtemp(prefix="lspr_writer_"))
    try:
        shutil.copy2(args.session / "analysis" / "scan.h5", scratch / "real_copy.h5")
        stored = read_scan(scratch / "real_copy.h5")
        n = len(plan.jobs)
        arrays = new_arrays(plan)
        arrays.values["mean"][:] = stored.values["mean"]
        arrays.std[:] = stored.std
        arrays.n_valid[:] = stored.n_valid
        arrays.n_nominal[:] = stored.n_nominal
        arrays.n_masked[:] = stored.n_masked
        hashes = stored.cell_hash.copy()
        print(f"real file {(scratch / 'real_copy.h5').stat().st_size / 1e6:.1f} MB (raw arrays {sum(a.nbytes for a in (arrays.std, arrays.values['mean'], arrays.n_valid, arrays.n_nominal, arrays.n_masked)) / 1e6:.0f} MB)")
        roi = 80
        original_create = h5py.Group.create_dataset
        for label, override in VARIANTS.items():
            def create(self, name, *a, **kw):
                if "compression" in kw:  # only the big arrays are compressed
                    kw.update(override)
                    kw = {k: v for k, v in kw.items() if not (k in ("compression_opts",) and v is None)}
                    if kw.get("compression") is None:
                        kw.pop("compression", None)
                        kw.pop("compression_opts", None)
                        kw["shuffle"] = False if "shuffle" in override and not override["shuffle"] else kw.get("shuffle", False)
                        if not kw["shuffle"]:
                            kw.pop("shuffle")
                return original_create(self, name, *a, **kw)

            h5py.Group.create_dataset = create
            try:
                path = scratch / (label.split()[0] + str(len(label)) + ".h5")
                store = ScanStore(path)
                store.prepare(plan, dataset_label="writer-variants")
            finally:
                h5py.Group.create_dataset = original_create
            t = time.perf_counter()
            for s in range(0, n, 16):
                store.write_batch(plan, arrays, [(p, None, hashes[p]) for p in range(s, min(s + 16, n))])
            first = time.perf_counter() - t
            line = [f"first full write {first:5.1f} s, {path.stat().st_size / 1e6:6.1f} MB"]
            rng = np.random.default_rng(3)
            for trial in range(args.trials):
                arrays.values["mean"][:, roi] *= 1.0 + 1e-6 * rng.standard_normal(arrays.values["mean"][:, roi].shape)
                arrays.std[:, roi] *= 1.0 + 1e-6 * rng.standard_normal(arrays.std[:, roi].shape)
                t = time.perf_counter()
                for s in range(0, n, 16):
                    store.write_batch(plan, arrays, [(p, (roi,), hashes[p]) for p in range(s, min(s + 16, n))])
                line.append(f"{time.perf_counter() - t:4.1f} s -> {path.stat().st_size / 1e6:6.1f} MB")
            print(f"{label:<26} " + " | ".join(line[:1]) + "\n" + " " * 27 + "1-ROI refresh x" + str(args.trials) + ": " + " | ".join(line[1:]))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
