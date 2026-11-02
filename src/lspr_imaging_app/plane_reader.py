"""Reads the pixels of one plane from disk: OME-Zarr (the whole plane, or only the chunks around some pixels) and TIFF / PNG (the
native libtiff decoder, with tifffile as the checked fallback).

Moved here from `dataset/io.py` on 2026-11-02 (layering): the analysis scan and the Zarr export worker process read planes, and
`analysis` and `io` sit below `dataset`. The code was cut and pasted, not changed (the parsed code is identical to the committed
`dataset/io.py`); `dataset/io.py` imports what its dataset loading and export still use. Names with a leading underscore are shared with
`dataset/io.py` on purpose.

It imports nothing of the app except the log helper, so a worker process that needs only planes does not load the dataset code.
Module state (the shard-index cache, the fast-decoder flags, the cached zarr handles) lives here, once per process: patch or reset it
on this module, not on `dataset.io`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from tifffile import imread as _tifffile_imread
except Exception:  # pragma: no cover - optional acceleration path  # noqa: BLE001
    _tifffile_imread = None

try:
    import zarrs as _zarrs  # only needed to register its codec pipeline
except Exception:  # pragma: no cover - optional acceleration path  # noqa: BLE001
    _zarrs = None

from .applog.events import log_event
from .shard_crc import _crc32c

_LOGGER = logging.getLogger("lspr_imaging_app.workflow")

VALID_MASK_ARRAY = "valid_mask"  # moved from dataset/ome_metadata.py: the readers apply the export's valid mask, so they own the name


OME_ZARR_META_FILENAME = ".zattrs"
OME_ZARR_GROUP_FILENAME = ".zgroup"
OME_ZARR_ARRAY_DIRNAME = "0"
OME_ZARR_ARRAY_META_FILENAME = ".zarray"
OME_ZARR_LSPR_KEY = "lspr"
_OME_ZARR_IMPORT_ERROR: ImportError | None = None
_ZARR_CODEC_PIPELINE_CONFIGURED = False


def _configure_zarr_codec_pipeline(zarr) -> None:
    """Switch zarr's read/decode pipeline to the Rust-backed `zarrs` one, once.

    zarr-python's default pipeline already coalesces a scoped read's chunk
    *fetches* into one batched I/O call (see the zarr>=3.3 pin comment in
    pyproject.toml) - but it still *decodes* the fetched chunks one at a
    time (codec_pipeline.batch_size=1 by default), each paying its own
    asyncio task / thread-pool dispatch (~1.5-2ms) regardless of chunk size.
    `zarrs` does that same per-chunk loop in Rust instead, sidestepping that
    dispatch cost. Measured directly on a real 66-ROI dataset: ~5x on a
    27-wavelength/cube ROI-scoped read (~1000ms -> ~195ms), ~7.5x on a
    full-plane read (~175ms -> ~23ms), byte-identical output.

    RE-ENABLED as of 2026-09-08, after root-causing the crash first
    DISABLED on 2026-09-07 (Windows STATUS_HEAP_CORRUPTION shortly after
    startup's first image load). The original "zarrs' Rust-side Rayon
    thread pool colliding with this app's own threading" suspicion was
    partly right but too narrow: a standalone, headless stress harness
    (see qthreadpool_zarr_crash_investigation.md) isolated the actual
    trigger to `QThreadPool` specifically, not `zarrs` specifically - a
    `QThreadPool` worker thread that ever becomes part of a wait/read chain
    touching a zarr array (any codec pipeline, directly or via blocking on
    a future for one) reliably crashed with native memory corruption
    (STATUS_HEAP_CORRUPTION and, in the isolated repro, the related
    STATUS_STACK_BUFFER_OVERRUN); a plain `threading.Thread` running the
    identical zarr-reading workload, under the same stress, never crashed,
    with any codec pipeline. This app's background dispatch
    (originally the old app's `gui/worker.py` `FunctionWorker`; now `analysis/worker.py`) no longer uses `QThreadPool` for
    anything that touches the dataset - re-verified with the isolated
    repro's exact stress pattern against the real, patched `FunctionWorker`
    class, `zarrs` enabled, clean across multiple runs. Falls back to the
    standard pipeline either way if `zarrs` isn't importable - this was
    always a pure performance path, never the only way to read a dataset.
    """
    global _ZARR_CODEC_PIPELINE_CONFIGURED
    if _ZARR_CODEC_PIPELINE_CONFIGURED or _zarrs is None:
        return
    try:
        zarr.config.set({"codec_pipeline.path": "zarrs.ZarrsCodecPipeline"})
    except Exception:  # noqa: BLE001 - zarrs is an optional speed-up; zarr's default pipeline stays (logged at debug)
        _LOGGER.debug("Could not enable zarrs codec pipeline; using zarr's default.", exc_info=True)
    _ZARR_CODEC_PIPELINE_CONFIGURED = True


def _require_ome_zarr_support():
    global _OME_ZARR_IMPORT_ERROR
    try:
        import zarr
        from numcodecs import Blosc
        import numcodecs.blosc as _blosc
        from ome_zarr.writer import write_multiscales_metadata
    except ImportError as exc:  # pragma: no cover - depends on environment
        _OME_ZARR_IMPORT_ERROR = exc
        raise ImportError(
            "OME-Zarr support requires zarr, numcodecs, and ome-zarr."
        ) from exc
    _configure_zarr_codec_pipeline(zarr)
    return zarr, Blosc, _blosc, write_multiscales_metadata


def _ome_zarr_root(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    if (folder / OME_ZARR_ARRAY_DIRNAME / OME_ZARR_ARRAY_META_FILENAME).exists():
        return folder  # zarr v2
    if (folder / OME_ZARR_GROUP_FILENAME).exists() and (folder / OME_ZARR_META_FILENAME).exists():
        return folder  # zarr v2
    if (folder / "zarr.json").exists():
        return folder  # zarr v3
    if (folder / OME_ZARR_ARRAY_DIRNAME / "zarr.json").exists():
        return folder  # zarr v3 (no root group file)
    return None


def _ome_zarr_array_dir(root: Path) -> Path:
    return root / OME_ZARR_ARRAY_DIRNAME


@lru_cache(maxsize=16)
def _ome_zarr_group_cached(root_str: str):
    zarr, _, _, _ = _require_ome_zarr_support()
    return zarr.open_group(str(root_str), mode="r")


@lru_cache(maxsize=16)
def _ome_zarr_array_cached(root_str: str):
    return _ome_zarr_group_cached(root_str)[OME_ZARR_ARRAY_DIRNAME]


@lru_cache(maxsize=16)
def _ome_zarr_fast_read_metadata(root_str: str) -> dict | None:
    """Everything needed to read one plane directly from its zarr v3 shard
    file — mirrors io/_zarr_export_worker.py's writer exactly (shard layout:
    [compressed inner chunks...][index: n_inner x (u64 offset, u64 nbytes)]
    [CRC32C]) — bypassing zarr's generic async chunked-array API, which
    dispatches every chunk's file-read and decompression as a separate
    asyncio.to_thread call. That's real overhead a full-plane read (which
    always touches every chunk anyway) doesn't need to pay.

    Returns None if this doesn't look like an export from that writer
    (missing/foreign metadata), so callers fall back to the standard zarr
    array API — this must never be the only way to read a dataset.
    """
    try:
        group = _ome_zarr_group_cached(root_str)
        lspr_attrs = dict(group.attrs.get(OME_ZARR_LSPR_KEY, {}))
        if not lspr_attrs:
            return None
        array = group[OME_ZARR_ARRAY_DIRNAME]
        _n_spectral_cubes, n_wl, height, width = array.shape
        chunk_shape = lspr_attrs.get("chunk_shape_px")  # (h, w), 2026-10-20; older exports have square chunk_size_px only
        if chunk_shape is not None:
            ich, icw = int(chunk_shape[0]), int(chunk_shape[1])
        else:
            ich = icw = int(lspr_attrs.get("chunk_size_px", 0))
        if ich <= 0 or icw <= 0:
            return None
        shard_mode = str(lspr_attrs.get("shard_mode", "per_image"))
        if shard_mode == "per_frame":  # legacy value from before the frame -> spectral cube rename
            shard_mode = "per_spectral_cube"
        compression = str(lspr_attrs.get("compression", "none"))
        compression_cname = None if compression == "none" else compression.split("+")[0]
        dtype = np.dtype(str(lspr_attrs.get("dtype") or array.dtype))
        fill_value = 0.0 if np.issubdtype(dtype, np.integer) else float("nan")
        n_cy = (int(height) + ich - 1) // ich
        n_cx = (int(width) + icw - 1) // icw
        return {
            "root": Path(root_str),
            "n_wl": int(n_wl),
            "shard_mode": shard_mode,
            "ich": ich,
            "icw": icw,
            "n_cy": n_cy,
            "n_cx": n_cx,
            "dtype": dtype,
            "fill_value": fill_value,
            "compression_cname": compression_cname,
            "image_height": int(height),
            "image_width": int(width),
        }
    except Exception:  # noqa: BLE001 - falls back to the standard reader (logged below)
        # Falls back to the standard reader, but not invisibly: a recurring
        # failure here is a silent loss of the fast path.
        _LOGGER.debug("OME-Zarr fast-read metadata unavailable; using the standard reader.", exc_info=True)
        return None


def _fast_read_zarr_plane(meta: dict, spectral_cube_pos: int, wavelength_pos: int) -> np.ndarray | None:
    """Read one (spectral_cube_pos, wavelength_pos) plane straight from its shard
    file's raw bytes. Returns None on any surprise (missing shard, size
    mismatch, decode failure, ...) so the caller falls back to the standard
    zarr array read rather than risk returning a subtly wrong image.

    The byte layout parsed here is the Zarr v3 "sharding indexed" codec
    (Zarr core protocol v3.0, sharding codec spec:
    https://zarr-specs.readthedocs.io/en/latest/v3/codecs/sharding-indexed/index.html):
    a shard file is `[compressed inner chunk 0]...[compressed inner chunk
    n-1][index: n x (u64 offset, u64 nbytes), one pair per inner chunk, in
    C order][u32 CRC32C of the index]`; a missing inner chunk's offset/length
    are both `0xFFFFFFFFFFFFFFFF`.
    """
    ich, icw = meta["ich"], meta["icw"]
    n_cy, n_cx = meta["n_cy"], meta["n_cx"]
    n_wl = meta["n_wl"]
    dtype = meta["dtype"]
    fill_value = meta["fill_value"]
    compression_cname = meta["compression_cname"]
    image_height, image_width = meta["image_height"], meta["image_width"]
    array_dir = meta["root"] / OME_ZARR_ARRAY_DIRNAME / "c"

    n_inner_per_wl = n_cy * n_cx
    if meta["shard_mode"] == "per_spectral_cube":
        shard_path = array_dir / str(spectral_cube_pos) / "0" / "0" / "0"
        n_inner_total = n_inner_per_wl * n_wl
        wl_local = wavelength_pos
    else:
        shard_path = array_dir / str(spectral_cube_pos) / str(wavelength_pos) / "0" / "0"
        n_inner_total = n_inner_per_wl
        wl_local = 0

    try:
        file_size = shard_path.stat().st_size
    except OSError:
        return None

    index_len = n_inner_total * 2 * 8
    trailer_len = index_len + 4
    if file_size < trailer_len:
        return None

    try:
        with shard_path.open("rb") as f:
            f.seek(file_size - trailer_len)
            trailer = f.read(trailer_len)
            index_bytes = trailer[:index_len]

            import struct

            stored_crc = struct.unpack("<I", trailer[index_len:])[0]
            if _crc32c(index_bytes) != stored_crc:
                # Corrupt/truncated shard index (torn write, bit rot, ...) -
                # fall back to the standard zarr array read rather than trust
                # an index we can't verify.
                return None

            index = np.frombuffer(index_bytes, dtype=np.uint64)
            offs_all = index[0::2]
            lens_all = index[1::2]
            base_ci = wl_local * n_inner_per_wl
            offs = offs_all[base_ci: base_ci + n_inner_per_wl]
            lens = lens_all[base_ci: base_ci + n_inner_per_wl]

            missing = np.uint64((1 << 64) - 1)
            present = offs != missing
            shard_h, shard_w = n_cy * ich, n_cx * icw
            plane = np.full((shard_h, shard_w), fill_value, dtype=dtype)

            if np.any(present):
                lo = int(offs[present].min())
                hi = int((offs[present] + lens[present]).max())
                f.seek(lo)
                buf = f.read(hi - lo)
                codec = None
                if compression_cname:
                    from numcodecs import Blosc
                    codec = Blosc(cname=compression_cname, clevel=1, shuffle=Blosc.BITSHUFFLE)
                for cy in range(n_cy):
                    for cx in range(n_cx):
                        ci_local = cy * n_cx + cx
                        off = offs[ci_local]
                        if off == missing:
                            continue
                        start = int(off) - lo
                        length = int(lens[ci_local])
                        raw = buf[start:start + length]
                        if codec is not None:
                            raw = codec.decode(raw)
                        tile = np.frombuffer(raw, dtype=dtype).reshape(ich, icw)
                        plane[cy * ich:(cy + 1) * ich, cx * icw:(cx + 1) * icw] = tile
    except Exception:  # noqa: BLE001 - any failure of the fast read falls back to the standard reader (logged at debug)
        _LOGGER.debug("OME-Zarr fast plane read failed; using the standard reader.", exc_info=True)
        return None

    return np.array(plane[:image_height, :image_width], dtype=dtype, copy=True)


# -- partial plane reads: only the chunks around some pixels (2026-10-27) -----------------------------------------------------
#
# The analysis scan of an unrotated OME-Zarr export needs the pixels of the ROIs it computes, not the whole plane: one ROI of the
# 50 px test export touches 6 of 442 chunks. `ome_zarr_plane_grid` says whether a record path can be read this way, `partial_read_plan`
# turns the pixels wanted into the chunks to fetch (made once per ROI layout and kept by the caller), `read_plane_pixels` fetches them.
#
# Same bytes, same decoder, same conversion as `_fast_read_zarr_plane` + `_apply_valid_mask` (pinned by
# tests/integration/test_lspri_zarr_partial_reads.py: bit for bit equal to a whole-plane read on exports of every shard mode,
# with and without compression and a `valid_mask`). It differs in two ways: it decodes only the chunks asked for, and it reads and checks
# a shard's index (every chunk's offset and length, 191 KB in the 50 px test export) **once per shard and process**, not once per plane.
# Anything unexpected returns None and the caller reads the whole plane instead, so this is never the only way to read a dataset.

# KNOWN BOTTLENECK (maintainer, 2026-10-27: "mark it, solve it later"): every shard's index is read and CRC-checked before its first chunk is
# trusted, and a scan visits every cube's shard once, so this cost is paid once per cube and process and nothing can amortise it. The cost
# is the file read of the index (191 KB in the 50 px test export) plus the CRC32C of it.
#   * Without the `crc32c` package `_crc32c` is a pure-Python loop: ~110-180 ms per index, 56 % of the read time of a one-ROI refresh
#     (measured 2026-10-27, 6.8 -> 3.0 ms per frame when the check was bypassed in an experiment).
#   * With the package (declared in pyproject.toml since 2026-10-27): ~30 us per index plus a one-time import of ~0.3 s per worker process.
#     In a 63-cube run (7 workers) the index took 0.8 s of the 22 s read + reduce busy time, the CRC 0.7 s of it (mostly the import): ~4 %.
# `shard_index_seconds()` reports both and every scan logs them (`busy_index_s`, `busy_crc_s` of `analysis.scan_done`), so the share stays
# visible on any machine, e.g. one without the package. Cheaper ways to be sure the index is sound, if the remaining share ever matters:
# read only the index entries of the chunks wanted and check them against the file size and each other instead of checksumming all 11,934
# entries; or check one shard per dataset and the rest by structure; or store a per-dataset "verified" mark. None is done: the CRC is what makes
# an uncompressed (otherwise unchecked) chunk read trustworthy.
_SHARD_INDEX_CACHE_SHARDS = 8
_SHARD_INDEX_MISSING = np.uint64((1 << 64) - 1)
_shard_index_cache: "OrderedDict[tuple[str, int, int], tuple[np.ndarray, np.ndarray]]" = OrderedDict()
_shard_index_lock = threading.Lock()
_shard_index_seconds = [0.0, 0.0]  # [reading + checking indexes, of which the CRC] in this process, cache misses only
_blosc_codecs: dict[str, object] = {}


def shard_index_seconds() -> tuple[float, float]:
    """Seconds this process has spent reading and checking shard indexes (cache misses only), and the part of that spent in the CRC32C.
    Cumulative: take the difference between two calls. See KNOWN BOTTLENECK above."""
    with _shard_index_lock:
        return _shard_index_seconds[0], _shard_index_seconds[1]


@dataclass(frozen=True)
class ZarrPlaneGrid:
    """Size and chunk grid of the planes of one OME-Zarr export (pixels)."""

    height: int
    width: int
    chunk_h: int
    chunk_w: int

    @property
    def n_cy(self) -> int:
        return (self.height + self.chunk_h - 1) // self.chunk_h

    @property
    def n_cx(self) -> int:
        return (self.width + self.chunk_w - 1) // self.chunk_w

    @property
    def n_chunks(self) -> int:
        return self.n_cy * self.n_cx

    @property
    def padded_width(self) -> int:
        return self.n_cx * self.chunk_w


@dataclass(frozen=True)
class PartialReadPlan:
    """Which chunks of a plane to fetch to get the pixels ``flat_index`` (row-major into the stored plane), and where each pixel sits in
    the padded canvas the chunks are put on. Depends on the pixel list and the grid only (not on the plane), so one serves every plane."""

    grid: ZarrPlaneGrid
    chunk_ids: np.ndarray  # sorted, unique: chunk (cy, cx) is cy * n_cx + cx
    gather: np.ndarray  # intp: position of each wanted pixel in the padded canvas, row-major
    flat_index: np.ndarray  # intp: the wanted pixels in the stored plane (for the valid mask)


@lru_cache(maxsize=64)
def _plane_root_of_dir(directory: str) -> str | None:
    """The OME-Zarr root whose array folder is ``directory`` (where the synthetic plane paths of a dataset live), or None."""
    parent = Path(directory)
    root = _ome_zarr_root(parent.parent if parent.name == OME_ZARR_ARRAY_DIRNAME else parent)
    return str(root) if root is not None and parent == _ome_zarr_array_dir(root) else None


def ome_zarr_plane_grid(path_str: str) -> ZarrPlaneGrid | None:
    """The plane grid for a record path of an OME-Zarr export this app wrote (the fast shard reader's metadata exists), else None
    (a TIFF, a foreign Zarr, an export that cannot be read directly): such planes are read whole."""
    root = _plane_root_of_dir(os.path.dirname(path_str))
    if root is None:
        return None
    meta = _ome_zarr_fast_read_metadata(root)
    if meta is None:
        return None
    return ZarrPlaneGrid(int(meta["image_height"]), int(meta["image_width"]), int(meta["ich"]), int(meta["icw"]))


def partial_read_plan(grid: ZarrPlaneGrid, flat_index: np.ndarray, max_share: float = 1.0) -> PartialReadPlan | None:
    """The chunks that hold the pixels ``flat_index`` of a plane of ``grid``; None when they are more than ``max_share`` of all the plane's
    chunks (then reading the whole plane is cheaper). ValueError for a pixel outside the plane."""
    flat = np.ascontiguousarray(flat_index, dtype=np.intp)
    rows, cols = np.divmod(flat, grid.width)
    if flat.size and (flat.min() < 0 or rows.max() >= grid.height):
        raise ValueError("a wanted pixel lies outside the plane")
    ids = np.unique((rows // grid.chunk_h) * grid.n_cx + cols // grid.chunk_w)
    if ids.size > max_share * grid.n_chunks:
        return None
    return PartialReadPlan(grid, ids, rows * grid.padded_width + cols, flat)


def _shard_location(meta: dict, cube_pos: int, wavelength_pos: int) -> tuple[Path, int, int]:
    """(shard file, inner chunks in it, this wavelength's block of them): the layout `_fast_read_zarr_plane` reads."""
    array_dir = meta["root"] / OME_ZARR_ARRAY_DIRNAME / "c"
    per_wavelength = meta["n_cy"] * meta["n_cx"]
    if meta["shard_mode"] == "per_spectral_cube":
        return array_dir / str(cube_pos) / "0" / "0" / "0", per_wavelength * meta["n_wl"], wavelength_pos
    return array_dir / str(cube_pos) / str(wavelength_pos) / "0" / "0", per_wavelength, 0


def _shard_index(shard_path: Path, n_inner_total: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(offsets, lengths) of every inner chunk of a shard, checked against the shard's CRC32C, or None when the file cannot be trusted.
    Remembered per shard file (size and modification time are part of the key, so a rewritten shard is read again)."""
    try:
        stat = shard_path.stat()
    except OSError:
        return None
    key = (str(shard_path), int(stat.st_size), int(stat.st_mtime_ns))
    with _shard_index_lock:
        cached = _shard_index_cache.get(key)
        if cached is not None:
            _shard_index_cache.move_to_end(key)
            return cached
    index_len = n_inner_total * 2 * 8
    if stat.st_size < index_len + 4:
        return None
    started = time.perf_counter()
    with shard_path.open("rb") as handle:
        handle.seek(stat.st_size - index_len - 4)
        trailer = handle.read(index_len + 4)
    import struct

    index_bytes = trailer[:index_len]
    crc_started = time.perf_counter()
    crc_ok = _crc32c(index_bytes) == struct.unpack("<I", trailer[index_len:])[0]
    finished = time.perf_counter()
    with _shard_index_lock:
        _shard_index_seconds[0] += finished - started
        _shard_index_seconds[1] += finished - crc_started
    if not crc_ok:
        return None
    index = np.frombuffer(index_bytes, dtype=np.uint64)
    entry = (index[0::2].copy(), index[1::2].copy())
    with _shard_index_lock:
        _shard_index_cache[key] = entry
        while len(_shard_index_cache) > _SHARD_INDEX_CACHE_SHARDS:
            _shard_index_cache.popitem(last=False)
    return entry


def read_plane_pixels(path_str: str, plan: PartialReadPlan) -> np.ndarray | None:
    """The float32 values at ``plan.flat_index`` of the plane a record path refers to, fetching only ``plan.chunk_ids``: what
    ``load_image_array(path)`` followed by ``.reshape(-1)[plan.flat_index]`` gives, bit for bit, including NaN where the export's
    `valid_mask` says no pixel was measured. None whenever anything is unexpected (no shard, bad index, decode error, a grid that is not
    this export's): read the whole plane then."""
    try:
        path = Path(path_str)
        root = _plane_root_of_dir(os.path.dirname(path_str))
        meta = _ome_zarr_fast_read_metadata(root) if root is not None else None
        if meta is None:
            return None
        grid = plan.grid
        if (grid.height, grid.width, grid.chunk_h, grid.chunk_w) != (meta["image_height"], meta["image_width"], meta["ich"], meta["icw"]):
            return None
        cube_pos, wavelength_pos, *_ = (int(part) for part in path.name.split("."))
        shard_path, n_inner_total, wl_local = _shard_location(meta, cube_pos, wavelength_pos)
        index = _shard_index(shard_path, n_inner_total)
        if index is None:
            return None
        base = wl_local * meta["n_cy"] * meta["n_cx"]
        offsets, lengths = index[0][base + plan.chunk_ids], index[1][base + plan.chunk_ids]
        dtype = meta["dtype"]
        canvas = np.empty((grid.n_cy * grid.chunk_h, grid.padded_width), dtype=dtype)  # only the chunks fetched are ever gathered from
        codec = None
        if meta["compression_cname"]:
            codec = _blosc_codecs.get(meta["compression_cname"])
            if codec is None:
                from numcodecs import Blosc

                codec = _blosc_codecs[meta["compression_cname"]] = Blosc(cname=meta["compression_cname"], clevel=1, shuffle=Blosc.BITSHUFFLE)
        ch, cw, n_cx = grid.chunk_h, grid.chunk_w, grid.n_cx
        present = offsets != _SHARD_INDEX_MISSING
        for chunk_id in plan.chunk_ids[~present]:  # a chunk that was never written is the fill value, as in the plane reader
            cy, cx = divmod(int(chunk_id), n_cx)
            canvas[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] = meta["fill_value"]
        if present.any():
            ids, offs, lens = plan.chunk_ids[present], offsets[present].astype(np.int64), lengths[present].astype(np.int64)
            order = np.argsort(offs, kind="stable")
            ids, offs, lens = ids[order], offs[order], lens[order]
            with shard_path.open("rb") as handle:
                start = 0
                while start < ids.size:
                    stop = start + 1
                    while stop < ids.size and offs[stop] == offs[stop - 1] + lens[stop - 1]:  # neighbours in the file: one read
                        stop += 1
                    handle.seek(int(offs[start]))
                    block = handle.read(int(offs[stop - 1] + lens[stop - 1] - offs[start]))
                    for k in range(start, stop):
                        begin = int(offs[k] - offs[start])
                        raw = block[begin:begin + int(lens[k])]
                        if codec is not None:
                            raw = codec.decode(raw)
                        cy, cx = divmod(int(ids[k]), n_cx)
                        canvas[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] = np.frombuffer(raw, dtype=dtype).reshape(ch, cw)
                    start = stop
        values = canvas.reshape(-1)[plan.gather].astype(np.float32)
        mask = _ome_zarr_valid_mask_cached(root)
        if mask is not None and mask.shape == (grid.height, grid.width):
            values[~mask.reshape(-1)[plan.flat_index]] = np.nan
        return values
    except Exception:  # noqa: BLE001 - a chunk read must never be the only way to get a pixel
        _LOGGER.debug("OME-Zarr partial read failed; the caller reads the whole plane.", exc_info=True)
        return None


@lru_cache(maxsize=16)
def _ome_zarr_valid_mask_cached(root_str: str) -> np.ndarray | None:
    """The export's `valid_mask` as a read-only bool array (True = measured), or None when it has none. A mask the
    attributes announce but that cannot be read is logged as a warning (its pixels would then show the stored 0)."""
    try:
        group = _ome_zarr_group_cached(root_str)
        info = dict(group.attrs.get(OME_ZARR_LSPR_KEY, {}) or {}).get("valid_mask")
    except Exception:  # noqa: BLE001 - not an export of this app: no mask
        return None
    if not info:
        return None
    try:
        mask = np.asarray(group[str(info.get("array", VALID_MASK_ARRAY))][:]) != 0
    except Exception:  # noqa: BLE001
        _LOGGER.warning("OME-Zarr %s announces a valid_mask that cannot be read: pixels without a source show 0", root_str, exc_info=True)
        return None
    mask.setflags(write=False)
    return mask


def _apply_valid_mask(plane: np.ndarray, root_str: str, y0: int = 0, x0: int = 0) -> np.ndarray:
    """``plane`` (float32, a window of the stored plane starting at row y0, column x0) with NaN where the export's
    `valid_mask` says no pixel was measured. Unchanged when the export has no mask."""
    mask = _ome_zarr_valid_mask_cached(root_str)
    if mask is None:
        return plane
    window = mask[y0 : y0 + plane.shape[0], x0 : x0 + plane.shape[1]]
    if window.shape != plane.shape[:2] or bool(window.all()):
        return plane
    if not plane.flags.writeable:
        plane = plane.copy()
    plane[~window] = np.nan
    return plane


def load_image_array_uncached(path_str: str) -> np.ndarray:
    """The same plane as `load_image_array`, but not remembered (2026-10-16): a scan that reads thousands of planes once each
    (the Mask tab's transient scan) would push the planes the user is looking at out of the 160-plane cache in `dataset/io.py`. The array is
    the caller's own, writable."""
    return _load_image_array_uncached(path_str)


def _read_ome_zarr_plane_by_path(path: Path, ome_root: Path) -> np.ndarray:
    """Read the plane a synthetic OME-Zarr record path refers to (its stem
    encodes "{spectral_cube_pos}.{wavelength_pos}..."). Tries the direct shard-file
    reader first (bypasses zarr's async chunked-array API — see
    _fast_read_zarr_plane's docstring for why that matters for a full-plane
    read); falls back to the standard zarr array API whenever the fast path
    isn't available or doesn't look right, so correctness never depends on
    the fast path succeeding.
    """
    stem = path.name
    try:
        spectral_cube_index, wavelength_index, *_ = (int(part) for part in stem.split("."))
    except Exception as exc:
        raise ValueError(f"Invalid OME-Zarr chunk path: {path.name}") from exc
    meta = _ome_zarr_fast_read_metadata(str(ome_root))
    if meta is not None:
        fast_result = _fast_read_zarr_plane(meta, spectral_cube_index, wavelength_index)
        if fast_result is not None:
            return fast_result
    array = _ome_zarr_array_cached(str(ome_root))
    return np.asarray(array[spectral_cube_index, wavelength_index])


# -- fast TIFF decoding for the Image panel's render thread (2026-10-14) -----------------------------
#
# Measured on the maintainer's dataset (LZW, 300 strips per file): the render thread spent 83 % of a Play
# run inside plane reads, one ~80 ms read per frame, which capped Play at ~12 frames/s. Most of that is
# `tifffile`'s per-strip Python work, not decompression. `imagecodecs.tiff_decode` (libtiff, already a
# dependency) decodes the whole file in ONE native call: identical pixels, 2-3x faster (43 -> 16 ms for
# 16-bit data, 72 -> 34 ms for float32, still ~2x faster while the GUI thread is busy).
#
# **On for every thread since 2026-10-22** (maintainer decision; until then only the render thread, the Scan and the export
# workers opted in, and the analysis workers, the auto-detects and the GUI thread stayed on tifffile "because of an
# earlier measurement"). Re-measured 2026-10-22 on the same LZW files, warm cache, files/s with tifffile -> libtiff:
# 1 thread 43 -> 81, 2 threads 41 -> 102, 4 threads 41 -> 182, 8 threads 38 -> 252, identical pixels on all 120 files.
# tifffile does not scale with threads at all (its per-strip Python work holds the GIL), libtiff does (one native call
# per file), so the analysis pools gain the most. `use_fast_tiff_decoder(False)` still opts one thread out.
#
# Guards, because it is a different decoder: only for plain 2-D images (anything else, or any error, falls back to
# tifffile), and the first FAST_TIFF_VERIFY_FILES files read in the process are also read with tifffile and compared
# pixel by pixel: on any difference the fast path is switched off for the session.
# `LSPR_TIFF_DECODER=tifffile` switches it off from the outside. (libtiff returns only the first page of a
# multi-page TIFF where tifffile returns the stack: the verification catches that.)
FAST_TIFF_VERIFY_FILES = 3
_fast_tiff = threading.local()
_fast_tiff_lock = threading.Lock()
_fast_tiff_verified = 0
_fast_tiff_disabled = os.environ.get("LSPR_TIFF_DECODER", "").strip().lower() == "tifffile"

try:
    import imagecodecs as _imagecodecs
except Exception:  # pragma: no cover - optional acceleration path  # noqa: BLE001
    _imagecodecs = None
if _imagecodecs is None or not getattr(getattr(_imagecodecs, "TIFF", None), "available", False):
    _imagecodecs = None


def use_fast_tiff_decoder(enabled: bool) -> None:
    """Opt the CALLING thread in (or out of) the fast native TIFF decoder. Every thread starts opted in (2026-10-22)."""
    _fast_tiff.enabled = bool(enabled)


def fast_tiff_decoder_active() -> bool:
    """Whether the calling thread would use it right now (not opted out, available, not switched off)."""
    return bool(getattr(_fast_tiff, "enabled", True)) and _imagecodecs is not None and not _fast_tiff_disabled


def _read_tiff_fast(path_str: str) -> np.ndarray | None:
    """The image as read by the native decoder, or None when tifffile must be used instead."""
    global _fast_tiff_verified, _fast_tiff_disabled
    try:
        with open(path_str, "rb") as handle:
            array = np.asarray(_imagecodecs.tiff_decode(handle.read()))
    except Exception:  # noqa: BLE001 - an unsupported variant must never break a read
        return None
    if array.ndim != 2:
        return None
    if _fast_tiff_verified < FAST_TIFF_VERIFY_FILES:
        with _fast_tiff_lock:
            if _fast_tiff_verified < FAST_TIFF_VERIFY_FILES:
                reference = np.asarray(_tifffile_imread(path_str, maxworkers=1))
                if reference.shape != array.shape or not np.array_equal(
                    array.astype(np.float32), reference.astype(np.float32), equal_nan=True
                ):
                    _fast_tiff_disabled = True
                    _LOGGER.warning(
                        "The fast TIFF decoder disagrees with tifffile on %s (shape %s vs %s): switched off for this session",
                        Path(path_str).name, array.shape, reference.shape,
                    )
                    return None
                _fast_tiff_verified += 1
                if _fast_tiff_verified == FAST_TIFF_VERIFY_FILES:
                    log_event(_LOGGER, "dataset.tiff_decoder", logging.INFO, decoder="imagecodecs/libtiff", verified_files=_fast_tiff_verified)
    return array


def _load_image_array_uncached(path_str: str) -> np.ndarray:
    path = Path(path_str)
    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        # Pixels without a source (rotated export: `valid_mask`) become NaN, never the stored 0.
        return _apply_valid_mask(np.asarray(_read_ome_zarr_plane_by_path(path, ome_root), dtype=np.float32), str(ome_root))
    if path.suffix.lower() in {".tif", ".tiff"} and _tifffile_imread is not None and fast_tiff_decoder_active():
        fast = _read_tiff_fast(path_str)
        if fast is not None:
            return np.asarray(fast, dtype=np.float32)
    if path.suffix.lower() in {".tif", ".tiff"} and _tifffile_imread is not None:
        try:
            # maxworkers=1, not os.cpu_count(): this is the per-wavelength read
            # called from inside worker threads (originally the old app's analysis_tasks.py ThreadPoolExecutor)
            # (one call per concurrent worker, several workers at once) as much
            # as from single-image interactive loads - giving each individual
            # file its own decode pool on top of the outer one means every
            # inner pool pays tifffile's own per-strip dispatch/lock overhead
            # (these files have 300 LZW strips each) for work the outer pool's
            # 4-way file-level parallelism already covers. Measured directly,
            # nested inside the app's real outer 4-worker pool (see
            # bulk_analysis_performance_investigation.md Follow-up #11,
            # Finding C): maxworkers=1 averaged 16.9ms/file vs 26.2ms/file at
            # maxworkers=2 (~1.55x) - a plain per-strip decode inside one
            # thread with no inner dispatch beats splitting one file's own
            # strips across a second thread pool once the outer pool is
            # already keeping every core busy. (An earlier version of this
            # comment recommended maxworkers=2 over the default cpu_count - 2
            # was correct relative to 8, just not relative to 1, which wasn't
            # tested at the time.)
            return np.asarray(_tifffile_imread(path_str, maxworkers=1), dtype=np.float32)
        except TypeError:
            return np.asarray(_tifffile_imread(path_str), dtype=np.float32)
    try:
        with Image.open(path_str) as image:
            return np.array(image, dtype=np.float32)
    except Exception:
        if _tifffile_imread is None:
            raise
        return np.asarray(_tifffile_imread(path_str), dtype=np.float32)
