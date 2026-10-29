# ruff: noqa: E701, E702, E401, E402  (lab script: dense one-line style, imports follow the sys.path set-up)
"""PROTOTYPE (scratchpad only): read_plane_pixels with ONE open per plane (index trailer + chunks through the same handle, fstat instead of a path stat)."""
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bench_roi_subset as b  # noqa: E402
_APP_ON_PATH = b.TOOLS  # importing it puts the app's sources on sys.path (and uses the name, for pyflakes)
import numpy as np
import analysis_stage_timing as st
from lspr_imaging_app.analysis import frame_reduce as fr
from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, source_from_modules
from lspr_imaging_app.dataset import io
from lspr_imaging_app.io._zarr_export_worker import _crc32c


def read_plane_pixels_1open(path_str, plan):
    path = Path(path_str)
    root = io._plane_root_of_dir(os.path.dirname(path_str))
    meta = io._ome_zarr_fast_read_metadata(root) if root is not None else None
    if meta is None:
        return None
    grid = plan.grid
    cube_pos, wavelength_pos, *_ = (int(part) for part in path.name.split("."))
    shard_path, n_inner_total, wl_local = io._shard_location(meta, cube_pos, wavelength_pos)
    index_len = n_inner_total * 16
    try:
        handle = shard_path.open("rb")
    except OSError:
        return None
    with handle:
        stat = os.fstat(handle.fileno())
        key = (str(shard_path), int(stat.st_size), int(stat.st_mtime_ns))
        with io._shard_index_lock:
            index = io._shard_index_cache.get(key)
        if index is None:
            if stat.st_size < index_len + 4:
                return None
            handle.seek(stat.st_size - index_len - 4)
            trailer = handle.read(index_len + 4)
            index_bytes = trailer[:index_len]
            if _crc32c(index_bytes) != struct.unpack("<I", trailer[index_len:])[0]:
                return None
            arr = np.frombuffer(index_bytes, dtype=np.uint64)
            index = (arr[0::2].copy(), arr[1::2].copy())
            with io._shard_index_lock:
                io._shard_index_cache[key] = index
        base = wl_local * meta["n_cy"] * meta["n_cx"]
        offsets, lengths = index[0][base + plan.chunk_ids], index[1][base + plan.chunk_ids]
        dtype = meta["dtype"]
        canvas = np.empty((grid.n_cy * grid.chunk_h, grid.padded_width), dtype=dtype)
        codec = io._blosc_codecs.get(meta["compression_cname"]) if meta["compression_cname"] else None
        if meta["compression_cname"] and codec is None:
            from numcodecs import Blosc
            codec = io._blosc_codecs[meta["compression_cname"]] = Blosc(cname=meta["compression_cname"], clevel=1, shuffle=Blosc.BITSHUFFLE)
        ch, cw, n_cx = grid.chunk_h, grid.chunk_w, grid.n_cx
        present = offsets != io._SHARD_INDEX_MISSING
        for chunk_id in plan.chunk_ids[~present]:
            cy, cx = divmod(int(chunk_id), n_cx)
            canvas[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] = meta["fill_value"]
        if present.any():
            ids, offs, lens = plan.chunk_ids[present], offsets[present].astype(np.int64), lengths[present].astype(np.int64)
            order = np.argsort(offs, kind="stable"); ids, offs, lens = ids[order], offs[order], lens[order]
            start = 0
            while start < ids.size:
                stop = start + 1
                while stop < ids.size and offs[stop] == offs[stop - 1] + lens[stop - 1]:
                    stop += 1
                handle.seek(int(offs[start]))
                block = handle.read(int(offs[stop - 1] + lens[stop - 1] - offs[start]))
                for k in range(start, stop):
                    begin = int(offs[k] - offs[start]); raw = block[begin:begin + int(lens[k])]
                    if codec is not None: raw = codec.decode(raw)
                    cy, cx = divmod(int(ids[k]), n_cx)
                    canvas[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] = np.frombuffer(raw, dtype=dtype).reshape(ch, cw)
                start = stop
    values = canvas.reshape(-1)[plan.gather].astype(np.float32)
    mask = io._ome_zarr_valid_mask_cached(root)
    if mask is not None and mask.shape == (grid.height, grid.width):
        values[~mask.reshape(-1)[plan.flat_index]] = np.nan
    return values


if __name__ == "__main__":
    zarr, session = Path(sys.argv[1]), Path(sys.argv[2])
    dataset = st.load_one(zarr); modules, _ = st.build_modules(dataset, session)
    records = io.dataset_record_map(dataset)
    source = source_from_modules(dataset=modules.dataset, frame_roles=modules.frame_roles, roi_toolbox=modules.roi_toolbox, geometry=modules.geometry,
        background=modules.background, chromatic=modules.chromatic, mask=modules.mask, records=records)
    e = modules.reference_inputs
    plan = build_scan_plan(source, cubes=list(modules.dataset.spectral_cubes())[:40], exclusion_mode=e.reference_exclusion_mode(), trimmed_fraction=e.trimmed_mean_fraction)
    job = plan.jobs[0]
    grid = io.ome_zarr_plane_grid(job.frames[0].path)
    layouts = {}
    paths = [f.path for j in plan.jobs for f in j.frames]
    print(f"{len(paths)} planes; grid {grid}")
    for label, subset in (("1 ROI", (80,)), ("16 ROIs", tuple(range(0, 160, 10)))):
        lay = fr.build_layout(job.rois, job.frames[0].affine, (grid.height, grid.width)).restrict(subset)
        rp = io.partial_read_plan(grid, lay.needed_flat, 1.0)
        # bit-identical?
        for p in paths[:200]:
            a, c = io.read_plane_pixels(p, rp), read_plane_pixels_1open(p, rp)
            assert a is not None and c is not None and np.array_equal(a, c, equal_nan=True) and a.dtype == c.dtype, p
        # timing: hot files, index cache emptied before each pass so each pass pays the per-plane index read like a fresh scan
        for rep in range(3):
            res = {}
            for name, fn in (("today", io.read_plane_pixels), ("one open", read_plane_pixels_1open)):
                with io._shard_index_lock: io._shard_index_cache.clear()
                t = time.perf_counter()
                for p in paths: fn(p, rp)
                res[name] = 1000 * (time.perf_counter() - t) / len(paths)
            print(f"  {label:>8} pass {rep}: today {res['today']:.3f} ms/plane   one open {res['one open']:.3f} ms/plane   -> saves {res['today']-res['one open']:.3f} ms ({100*(1-res['one open']/res['today']):.0f} %)")
    print("outputs identical on 200 planes")
