"""Top-level OME-Zarr export workers for concurrent.futures.ProcessPoolExecutor.

These functions run in child processes — separate Python interpreters with their
own GIL — so the main Qt process stays responsive regardless of how many CPU
cores are used.

No Qt imports here. Keep module-level imports minimal so worker process startup
stays fast (numpy and stdlib only at module load; heavier deps imported lazily
inside write_shard on first task).
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _worker_init() -> None:
    """Called once per worker process by ProcessPoolExecutor on startup.

    Sets Blosc to single-threaded mode: all parallelism comes from the process
    pool, so having Blosc spin up extra threads inside each worker would just
    over-subscribe the CPU and increase GIL contention with nothing to gain.
    """
    try:
        import numcodecs.blosc as _b
        _b.set_nthreads(1)
    except Exception:
        pass


def _crc32c(data: bytes) -> int:
    """CRC32C (Castagnoli) checksum — required by the zarr v3 shard index format."""
    try:
        import crc32c as _lib  # type: ignore[import-untyped]
        return _lib.crc32c(data)
    except ImportError:
        pass
    poly = 0x82F63B78
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


@dataclass
class ShardWriteSpec:
    """Everything a worker process needs to write one zarr v3 shard.

    All fields must be picklable: stdlib types, numpy dtype-as-string, and
    lspr_imaging_app.domain.models dataclasses (which have no Qt or zarr deps).
    """
    record_paths: list[str]      # one path per wavelength; '' = missing → fill value in index
    shard_path: str
    inner_chunk_h: int
    inner_chunk_w: int
    image_height: int
    image_width: int
    shard_h: int                 # padded height, exact multiple of inner_chunk_h
    shard_w: int                 # padded width,  exact multiple of inner_chunk_w
    n_cy: int
    n_cx: int
    n_inner: int                 # total inner chunks = len(record_paths) * n_cy * n_cx
    dtype_str: str               # numpy dtype .str, e.g. '<u2' or '<f4'
    fill_value: float            # 0.0 for integer types, nan for float types
    compression_cname: str | None   # 'lz4', or None to disable compression
    apply_image_tools: bool
    preprocessing: object | None    # PreprocessingSettings dataclass, or None
    # Export frame (2026-10-20): source x/y (after image tools) of stored pixel (0, 0). Negative = that many fill
    # columns/rows in front (lossless padding that moves the chunk grid onto the ROIs); positive = trimmed border
    # (lossy). The stored plane is image_height x image_width from there; source pixels outside it are not stored.
    frame_x0: int = 0
    frame_y0: int = 0
    # The camera's value step (`value_step`, detected once by the coordinator): rotated values are rounded onto it.
    # A plane whose source values are not all on it is rounded to whole counts instead and counted in the result.
    value_step: int = 1


def _place_in_frame(plane: np.ndarray, x0: int, y0: int, height: int, width: int, fill: float) -> np.ndarray:
    """The ``height x width`` window of ``plane`` that starts at source (x0, y0); fill where it has no source pixel."""
    if (x0, y0) == (0, 0) and plane.shape[:2] == (height, width):
        return plane
    out = np.full((height, width), fill, dtype=plane.dtype)
    src_h, src_w = plane.shape[:2]
    sx0, sy0 = max(x0, 0), max(y0, 0)
    sx1, sy1 = min(x0 + width, src_w), min(y0 + height, src_h)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = plane[sy0:sy1, sx0:sx1]
    return out


def _to_stored(plane: np.ndarray, dtype: np.dtype, fill: float, step: int = 1) -> np.ndarray:
    """``plane`` in the stored dtype. Float -> integer: NaN (no source pixel) becomes ``fill``, values are rounded to
    the nearest multiple of ``step`` and clipped to the dtype's range (Lanczos rings slightly past the source range;
    ~+-40 counts at the extremes of 16-bit data).

    ``step`` (2026-10-20, maintainer decision): the camera's value step. 12-bit cameras deliver multiples of 16 in a
    16-bit file; rounding the rotated values back onto that step keeps the low bits zero, so the export compresses
    like the raw data (04_Bulk: 49.7 % vs 74.0 % rounded to whole counts, raw 50.2 %), for 4.5 counts RMS of extra
    error against ~490 counts of pixel noise (docs/rotated_zarr_export_interpolation_2026-10-20.md)."""
    dtype = np.dtype(dtype)
    if not (np.issubdtype(dtype, np.integer) and np.issubdtype(plane.dtype, np.floating)):
        return np.asarray(plane, dtype=dtype)
    info = np.iinfo(dtype)
    step = max(int(step), 1)
    out = np.where(np.isfinite(plane), plane, fill)
    if step > 1:
        out /= step
    np.rint(out, out=out)
    if step > 1:
        out *= step
    np.clip(out, info.min, (info.max // step) * step, out=out)
    return out.astype(dtype)


def value_step(values: np.ndarray, limit: int = 4096) -> int:
    """The largest step every value of an integer image is a multiple of (16 for a 12-bit camera writing 16-bit
    files), 1 when there is none, for float data or a step above ``limit`` (e.g. an all-zero or near-empty image)."""
    values = np.asarray(values)
    if not np.issubdtype(values.dtype, np.integer):
        return 1
    distinct = np.unique(values).astype(np.int64)
    distinct = distinct[distinct != 0]
    if distinct.size < 2:
        return 1
    step = int(np.gcd.reduce(distinct))
    return step if 1 < step <= limit else 1


@dataclass
class ShardWriteResult:
    """Timing breakdown for one write_shard() call, used by the coordinator's
    adaptive worker-count tuning (see export_ome_zarr_dataset in dataset.py).

    read_s is an I/O proxy, not a precise measurement: _load_image() blends real
    disk I/O with TIFF-decode CPU time, and there's no clean way to split those
    apart without instrumenting inside tifffile/PIL itself.
    """
    n_inner: int
    bytes_written: int   # actual bytes physically written to the shard file
    read_s: float         # image read + decode + optional preprocessing
    compress_s: float     # Blosc tile encode (pure CPU)
    write_s: float         # shard file write
    off_step_planes: int = 0  # planes rounded to whole counts because their source was not on `value_step`


def _load_image(path_str: str) -> np.ndarray:
    path = Path(path_str)
    # Re-exporting a dataset that was itself loaded from OME-Zarr (e.g. to
    # change chunk size/shard mode) hands us records whose `.path` is a
    # synthetic, non-existent per-plane key (`{cube}.{wl}.0.0` under the
    # array dir - see dataset/io.py's `_ome_zarr_plane_path`/
    # `_read_ome_zarr_plane_by_path`), not a real file. tifffile/PIL below
    # can't open that - it has to be routed through the same zarr-aware
    # reader the main process already uses for OME-Zarr reads
    # (dataset_load_plane/dataset_load_plane_roi). Each worker process
    # opens its own read-only zarr handle here (lazy import: this module
    # otherwise stays free of zarr/dataset.io imports at module scope so
    # worker startup stays fast); the underlying store reads are plain
    # stateless file I/O, safe from a separate process.
    from lspr_imaging_app.dataset.io import OME_ZARR_ARRAY_DIRNAME, _ome_zarr_array_dir, _ome_zarr_root, _read_ome_zarr_plane_by_path

    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        return _read_ome_zarr_plane_by_path(path, ome_root)
    if path.suffix.lower() in {".tif", ".tiff"}:
        try:
            from tifffile import imread as _tif_read
            try:
                # maxworkers=1: per-file thread pool would fight with the process
                # pool and over-subscribe CPU with no benefit.
                return np.asarray(_tif_read(path_str, maxworkers=1))
            except TypeError:
                return np.asarray(_tif_read(path_str))
        except ImportError:
            pass
    from PIL import Image
    with Image.open(path_str) as img:
        return np.array(img)


def write_shard(spec: ShardWriteSpec) -> ShardWriteResult:
    """Read source images, compress tiles, write one zarr v3 shard file.

    Returns a ShardWriteResult carrying spec.n_inner (so the coordinator can
    advance the progress counter by the right amount even when some records
    are missing) plus a per-phase timing breakdown used for adaptive worker
    tuning (see export_ome_zarr_dataset in dataset.py).
    """
    from numcodecs import Blosc

    ich = spec.inner_chunk_h
    icw = spec.inner_chunk_w
    n_cy = spec.n_cy
    n_cx = spec.n_cx
    n_inner = spec.n_inner
    dtype = np.dtype(spec.dtype_str)
    fill = spec.fill_value
    codec = (
        Blosc(cname=spec.compression_cname, clevel=1, shuffle=Blosc.BITSHUFFLE)
        if spec.compression_cname
        else None
    )

    MISSING = np.uint64((1 << 64) - 1)
    all_bufs: list[bytes] = []
    all_offs = np.full(n_inner, MISSING, dtype=np.uint64)
    all_lens = np.full(n_inner, MISSING, dtype=np.uint64)
    byte_offset = 0
    read_s = 0.0
    compress_s = 0.0
    off_step_planes = 0

    for wl_idx, record_path in enumerate(spec.record_paths):
        if not record_path:
            continue  # missing record — index entry stays MISSING; reader uses fill value

        _t0 = time.perf_counter()
        source = np.asarray(_load_image(record_path))

        if spec.apply_image_tools and spec.preprocessing is not None and not hasattr(spec.preprocessing, "rotation_fill_dark"):
            # The rewrite's export passes a GeometrySettings (duck-typed: only the stable app's settings class has
            # `rotation_fill_dark`). The rotation (Lanczos4) works on the source values and returns float32 with NaN
            # where it created pixels; `_to_stored` then rounds into the stored dtype (2026-10-20: uint16 stays
            # uint16, the NaN pixels are stored as the fill value and listed in the export's `valid_mask`).
            from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_preprocessing_export

            step = int(spec.value_step)
            if step > 1 and not bool(np.all(source % step == 0)):
                step = 1  # this plane is not on the step: whole counts (reported, never silently forced onto it)
                off_step_planes += 1
            plane = _to_stored(apply_spatial_preprocessing_export(source, spec.preprocessing), dtype, fill, step)
        elif spec.apply_image_tools and spec.preprocessing is not None:
            # The stable app: its own PreprocessingSettings and transform, unchanged.
            from lspr_imaging_app.processing.preprocess import apply_spatial_preprocessing_export

            plane = np.asarray(apply_spatial_preprocessing_export(np.asarray(source, dtype=dtype), spec.preprocessing), dtype=dtype)
        else:
            plane = np.asarray(source, dtype=dtype)
        plane = _place_in_frame(plane, spec.frame_x0, spec.frame_y0, spec.image_height, spec.image_width, fill)

        # Pad to exact shard dimensions so every tile is a full inner_chunk
        h, w = plane.shape[:2]
        if (h, w) != (spec.shard_h, spec.shard_w):
            padded = np.full((spec.shard_h, spec.shard_w), fill, dtype=dtype)
            padded[: min(h, spec.shard_h), : min(w, spec.shard_w)] = plane[: spec.shard_h, : spec.shard_w]
            plane = padded
        else:
            plane = np.ascontiguousarray(plane)
        read_s += time.perf_counter() - _t0

        base_ci = wl_idx * (n_cy * n_cx)
        for cy in range(n_cy):
            for cx in range(n_cx):
                tile = np.ascontiguousarray(
                    plane[cy * ich : (cy + 1) * ich, cx * icw : (cx + 1) * icw], dtype=dtype
                )
                _t0 = time.perf_counter()
                # Encode the array, not its bytes: Blosc takes the element size from it (2 for uint16, 4 for
                # float32), which bit-shuffle needs. From bytes it used 1, and real data did not compress at
                # all (measured 2026-10-20: 100 % vs 66 % of raw). The stored format is the same either way.
                encoded = codec.encode(tile) if codec else tile.tobytes()
                compress_s += time.perf_counter() - _t0
                ci = base_ci + cy * n_cx + cx
                all_offs[ci] = byte_offset
                all_lens[ci] = len(encoded)
                all_bufs.append(encoded)
                byte_offset += len(encoded)

    # zarr v3 shard layout: [compressed chunks...][index: n_inner×(u64 offset, u64 nbytes)][CRC32C: 4 bytes]
    index = np.empty(n_inner * 2, dtype=np.uint64)
    index[0::2] = all_offs
    index[1::2] = all_lens
    index_bytes = index.tobytes()

    shard_path = Path(spec.shard_path)
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    _t0 = time.perf_counter()
    with shard_path.open("wb") as f:
        for buf in all_bufs:
            f.write(buf)
        f.write(index_bytes)
        f.write(struct.pack("<I", _crc32c(index_bytes)))
    write_s = time.perf_counter() - _t0

    bytes_written = byte_offset + len(index_bytes) + 4
    return ShardWriteResult(
        n_inner=n_inner, bytes_written=bytes_written,
        read_s=read_s, compress_s=compress_s, write_s=write_s, off_step_planes=off_step_planes,
    )
