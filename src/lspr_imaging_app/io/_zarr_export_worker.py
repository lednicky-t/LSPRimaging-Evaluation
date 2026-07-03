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


def _load_image(path_str: str) -> np.ndarray:
    path = Path(path_str)
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


def write_shard(spec: ShardWriteSpec) -> int:
    """Read source images, compress tiles, write one zarr v3 shard file.

    Returns spec.n_inner so the coordinator can advance the progress counter
    by the right amount even when some records are missing.
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

    for wl_idx, record_path in enumerate(spec.record_paths):
        if not record_path:
            continue  # missing record — index entry stays MISSING; reader uses fill value

        plane = np.asarray(_load_image(record_path), dtype=dtype)

        if spec.apply_image_tools and spec.preprocessing is not None:
            from lspr_imaging_app.processing.preprocess import apply_spatial_preprocessing
            plane = np.asarray(apply_spatial_preprocessing(plane, spec.preprocessing), dtype=dtype)

        # Pad to exact shard dimensions so every tile is a full inner_chunk
        h, w = plane.shape[:2]
        if (h, w) != (spec.shard_h, spec.shard_w):
            padded = np.full((spec.shard_h, spec.shard_w), fill, dtype=dtype)
            padded[: min(h, spec.shard_h), : min(w, spec.shard_w)] = plane[: spec.shard_h, : spec.shard_w]
            plane = padded
        else:
            plane = np.ascontiguousarray(plane)

        base_ci = wl_idx * (n_cy * n_cx)
        for cy in range(n_cy):
            for cx in range(n_cx):
                tile = np.ascontiguousarray(
                    plane[cy * ich : (cy + 1) * ich, cx * icw : (cx + 1) * icw], dtype=dtype
                )
                encoded = codec.encode(tile.tobytes()) if codec else tile.tobytes()
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
    with shard_path.open("wb") as f:
        for buf in all_bufs:
            f.write(buf)
        f.write(index_bytes)
        f.write(struct.pack("<I", _crc32c(index_bytes)))

    return n_inner  # coordinator adds this to completed_units for progress tracking
