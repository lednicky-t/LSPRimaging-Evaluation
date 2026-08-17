from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import os

import numpy as np
from PIL import Image

try:
    from tifffile import imread as _tifffile_imread
    from tifffile import TiffFile as _tifffile_TiffFile
except Exception:  # pragma: no cover - optional acceleration path
    _tifffile_imread = None
    _tifffile_TiffFile = None

from lspr_core import ImagingAcquisitionMetadata
from lspr_io import is_imaging_measurement_file, read_imaging_acquisition_metadata

from lspr_imaging_app.domain.exclusions import ImageExclusionRule, is_excluded
from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord, PreprocessingSettings
from lspr_imaging_app.io.image_naming import IMAGE_PATTERN
from lspr_imaging_app.io.legacy_metadata import find_and_import_legacy_metadata
from lspr_imaging_app.storage.workspace import (
    acquisition_metadata_from_payload,
    build_acquisition_metadata_payload,
    load_acquisition_metadata_sidecar,
)
from lspr_imaging_app.processing.preprocess import apply_spatial_preprocessing


OME_ZARR_META_FILENAME = ".zattrs"
OME_ZARR_GROUP_FILENAME = ".zgroup"
OME_ZARR_ARRAY_DIRNAME = "0"
OME_ZARR_ARRAY_META_FILENAME = ".zarray"
OME_ZARR_LSPR_KEY = "lspr"
OME_ZARR_ACQUISITION_METADATA_KEY = "lspr_acquisition_metadata"
_OME_ZARR_IMPORT_ERROR: ImportError | None = None

# Adaptive worker-count tuning for export_ome_zarr_dataset (module-level so tests
# can monkeypatch them for deterministic flip behavior instead of depending on
# real disk-timing noise). See export_ome_zarr_dataset's coordinator loop.
ADAPTIVE_MAX_FACTOR = 2.0          # never exceed 2x cpu_count
ADAPTIVE_IO_FLIP_UP = 0.5          # >=50% time in read+write -> add workers
ADAPTIVE_IO_FLIP_DOWN = 0.2        # <=20% time in read+write -> remove workers


def _crc32c(data: bytes) -> int:
    """CRC32C (Castagnoli) checksum — required for zarr v3 shard index format."""
    try:
        import crc32c as _lib  # type: ignore[import-untyped]
        return _lib.crc32c(data)
    except ImportError:
        pass
    # Pure-Python fallback. The shard index is at most a few KB, so this is fast enough.
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
    return zarr, Blosc, _blosc, write_multiscales_metadata

_LOGGER = logging.getLogger("lspr_imaging_app.workflow")


_ACQUISITION_METADATA_SEARCH_LEVELS_UP = 3


def find_native_imaging_measurement_file(dataset_folder: Path) -> Path | None:
    """Search `dataset_folder` and a few ancestor directories for a native
    `lspr_measurement` v6.4 HDF5 file (written by LSPRimaging Acquisition's
    `ImagingMeasurementWriter`, which stores camera/illumination setup and
    cube timing there but writes pixel data separately - see that writer's
    module docstring). There's no fixed naming/location convention verified
    against real acquisition-app output yet, so this takes the same
    "sidecar somewhere nearby" approach as `find_legacy_metadata_files` and
    validates each candidate's actual HDF5 content rather than trusting a
    filename pattern.
    """
    candidate = dataset_folder
    for _ in range(_ACQUISITION_METADATA_SEARCH_LEVELS_UP + 1):
        if candidate.is_dir():
            for extension in ("*.h5", "*.hdf5"):
                for path in sorted(candidate.glob(extension)):
                    if is_imaging_measurement_file(path):
                        return path
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def acquisition_metadata_sidecar_path(dataset_folder: Path) -> Path:
    return dataset_folder / "analysis" / "acquisition_metadata.json"


def load_acquisition_metadata(dataset_folder: Path) -> ImagingAcquisitionMetadata | None:
    """Load whatever camera/illumination/cube-timing metadata is available
    for `dataset_folder`, checking sources in order:

    1. `analysis/acquisition_metadata.json` - a previously saved sidecar
       (from an explicit "Import metadata..." action or edits made in the
       metadata preview dialog). Checked first so user edits persist across
       reloads instead of being silently overwritten by a fresh auto-detect.
    2. A native v6.4 HDF5 file found nearby.
    3. The legacy `measureing_times.csv`/`metaData.txt` pair found nearby.
    4. None - most datasets (including every OME-Zarr export made before
       this existed) have none of the above, which is not an error.
    """
    sidecar_path = acquisition_metadata_sidecar_path(dataset_folder)
    if sidecar_path.exists():
        try:
            return load_acquisition_metadata_sidecar(sidecar_path)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("Could not read acquisition metadata sidecar %s: %s", sidecar_path, exc)

    native_path = find_native_imaging_measurement_file(dataset_folder)
    if native_path is not None:
        metadata = read_imaging_acquisition_metadata(native_path)
        if metadata is not None:
            return metadata
    return find_and_import_legacy_metadata(dataset_folder)


def _has_tiff_stack(folder: Path) -> bool:
    return folder.is_dir() and any(IMAGE_PATTERN.search(path.stem) for path in folder.glob("*.tif*"))


def _load_tiff_stack_dataset(folder: Path) -> ImageDataset:
    """Load `folder` as a flat directory of TIFF files named like
    `WL500Frame3.tif` (wavelength in nm, spectral cube index), parsed by
    `IMAGE_PATTERN`. Raises `FileNotFoundError` if none match."""
    records: list[ImageRecord] = []
    for path in sorted(folder.glob("*.tif*")):
        match = IMAGE_PATTERN.search(path.stem)
        if not match:
            continue
        wl = float(match.group("wl"))
        spectral_cube_index = int(match.group("spectral_cube_index"))
        records.append(ImageRecord(ImageKey(wavelength_nm=wl, spectral_cube_index=spectral_cube_index), path))
    if not records:
        raise FileNotFoundError(f"No matching TIFF files found in {folder}")
    return ImageDataset(folder=folder, records=records, source_format="image_stack")


def _direct_dataset_candidate(folder: Path) -> ImageDataset | None:
    """`folder` as an `ImageDataset` if it directly (not in a subfolder) holds
    an OME-Zarr dataset or a TIFF stack, else `None`."""
    if is_ome_zarr_dataset(folder):
        return load_ome_zarr_dataset(folder)
    if _has_tiff_stack(folder):
        return _load_tiff_stack_dataset(folder)
    return None


def discover_dataset_candidates(folder: Path) -> list[ImageDataset]:
    """Find the dataset(s) `folder` refers to: `folder` itself if it directly
    holds a TIFF stack or OME-Zarr dataset (today's only supported layout),
    otherwise every TIFF-stack/OME-Zarr dataset found one level down, in
    `folder`'s immediate subfolders. Real datasets often keep the metadata
    files (`measureing_times.csv`/`metaData.txt`, a native v6.4 HDF5 file)
    directly in a parent folder with the actual image data one level below
    it, in a differently-named subfolder per dataset - this lets a caller
    point at that parent instead of having to browse into the image
    subfolder first (metadata is still found via `load_acquisition_metadata`
    starting from `folder`, regardless of where the images end up).

    Returns a list of fully-built `ImageDataset`s (records only - no pixel
    data read), so a caller with multiple candidates can inspect them (e.g.
    `summarize_dataset_candidate`) before picking one.
    """
    direct = _direct_dataset_candidate(folder)
    if direct is not None:
        return [direct]
    if not folder.is_dir():
        return []
    candidates: list[ImageDataset] = []
    for child in sorted((path for path in folder.iterdir() if path.is_dir()), key=lambda path: path.name):
        candidate = _direct_dataset_candidate(child)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


@dataclass
class DatasetCandidateSummary:
    folder: Path
    source_format: str
    image_count: int
    spectral_cube_count: int
    wavelength_count: int
    total_bytes: int


def summarize_dataset_candidate(dataset: ImageDataset) -> DatasetCandidateSummary:
    """Cheap size/count summary of a discovered candidate, for a chooser UI
    to show when `discover_dataset_candidates` finds more than one - mirrors
    the size-on-disk logic `MainWindow._update_dataset_summary_labels` uses
    for the same two formats (real files summed for a TIFF stack, the whole
    dataset folder walked for OME-Zarr, since its records are synthetic
    per-plane paths rather than real files)."""
    if dataset.is_ome_zarr:
        total_bytes = 0
        for entry in dataset.folder.rglob("*"):
            if not entry.is_file():
                continue
            try:
                total_bytes += int(entry.stat().st_size)
            except OSError:
                continue
    else:
        total_bytes = 0
        for record in dataset.records:
            try:
                total_bytes += int(record.path.stat().st_size)
            except OSError:
                continue
    return DatasetCandidateSummary(
        folder=dataset.folder,
        source_format=dataset.source_format,
        image_count=len(dataset.records),
        spectral_cube_count=len(dataset.spectral_cube_indices),
        wavelength_count=len(dataset.wavelengths_nm),
        total_bytes=total_bytes,
    )


@dataclass
class DatasetLoadChoice:
    """Returned by `load_dataset` instead of an `ImageDataset` when more than
    one TIFF-stack/OME-Zarr candidate is found - the GUI must prompt the user
    to pick one of `candidates` (see `discover_dataset_candidates`) before
    loading can finish. `acquisition_metadata` is already resolved (from
    `parent_folder`, not any one candidate) since it doesn't depend on which
    candidate gets picked."""

    parent_folder: Path
    candidates: list[ImageDataset]
    acquisition_metadata: ImagingAcquisitionMetadata | None


def load_dataset(folder: Path) -> ImageDataset | DatasetLoadChoice:
    """Load `folder` as an `ImageDataset`, auto-detecting the format and
    searching one level deep for it if `folder` doesn't directly hold one
    (see `discover_dataset_candidates`).

    Returns a `DatasetLoadChoice` instead of a single `ImageDataset` when
    more than one candidate is found (e.g. a TIFF-stack subfolder *and* an
    OME-Zarr subfolder) - the caller must resolve that before a dataset can
    actually be loaded.

    Either way, `load_acquisition_metadata` looks nearby (starting from
    `folder`, not wherever a candidate's images end up) for camera/
    illumination/cube-timing metadata (a native v6.4 file or legacy sidecar
    files) and attaches it to the result. The returned dataset's
    `home_folder` is always set to `folder` too - see `ImageDataset.home` -
    so this app's own saved state (analysis/, sessions, ROI table, masks)
    lands next to those metadata files instead of inside a discovered
    TIFF/OME-Zarr subfolder.
    """
    candidates = discover_dataset_candidates(folder)
    if not candidates:
        raise FileNotFoundError(
            f"No TIFF image stack or OME-Zarr dataset found in {folder} or its immediate subfolders."
        )
    metadata = load_acquisition_metadata(folder)
    if len(candidates) > 1:
        return DatasetLoadChoice(parent_folder=folder, candidates=candidates, acquisition_metadata=metadata)
    dataset = candidates[0]
    dataset.acquisition_metadata = metadata
    dataset.home_folder = folder
    return dataset


def dataset_record_map(dataset: ImageDataset) -> dict[tuple[int, float], ImageRecord]:
    return {(record.key.spectral_cube_index, record.key.wavelength_nm): record for record in dataset.records}


def dataset_is_ome_zarr(dataset: ImageDataset | None) -> bool:
    return bool(dataset is not None and dataset.is_ome_zarr)


def dataset_get_record(dataset: ImageDataset, spectral_cube_index: int, wavelength_nm: float) -> ImageRecord | None:
    for record in dataset.records:
        if int(record.key.spectral_cube_index) == int(spectral_cube_index) and float(record.key.wavelength_nm) == float(wavelength_nm):
            return record
    return None


def dataset_get_record_map(dataset: ImageDataset) -> dict[tuple[int, float], ImageRecord]:
    return dataset_record_map(dataset)


def dataset_load_plane(dataset: ImageDataset, spectral_cube_index: int, wavelength_nm: float) -> np.ndarray:
    """Load one full (spectral_cube_index, wavelength_nm) plane as float32.

    For a scoped read of just part of a plane (e.g. around a small ROI on an
    OME-Zarr dataset), use `dataset_load_plane_roi` instead -- it only reads
    the zarr chunks the requested region actually touches.
    """
    record = dataset_get_record(dataset, spectral_cube_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for spectral_cube_index={spectral_cube_index}, wavelength={wavelength_nm}")
    return load_image_array(str(record.path))


def dataset_load_plane_roi(
    dataset: ImageDataset,
    spectral_cube_index: int,
    wavelength_nm: float,
    y_start: int,
    y_end: int,
    x_start: int,
    x_end: int,
    *,
    record: "ImageRecord | None" = None,
) -> np.ndarray:
    """Load a spatial tile from the given plane.

    For OME-Zarr datasets reads only the requested region from the chunked array,
    loading only the zarr chunks that intersect the bounding box — much faster than
    loading the full plane when only a small ROI is needed.

    For TIFF stacks, loads the full image and returns the crop (no partial TIFF reads).

    The optional *record* argument accepts a pre-looked-up ImageRecord to avoid the
    O(N) scan of dataset_get_record when the caller already holds the record map.
    """
    if record is None:
        record = dataset_get_record(dataset, spectral_cube_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for spectral_cube_index={spectral_cube_index}, wavelength={wavelength_nm}")
    path = record.path
    ome_root = _ome_zarr_root(
        path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent
    )
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        array = _ome_zarr_array_cached(str(ome_root))
        try:
            fi, wi, *_ = (int(part) for part in path.stem.split("."))
        except Exception:
            pass  # fall through to full load + crop
        else:
            shape = getattr(array, "shape", None)
            h = int(shape[2]) if isinstance(shape, tuple) and len(shape) == 4 else None
            w = int(shape[3]) if isinstance(shape, tuple) and len(shape) == 4 else None
            y0 = max(int(y_start), 0)
            y1 = min(int(y_end), h) if h is not None else int(y_end)
            x0 = max(int(x_start), 0)
            x1 = min(int(x_end), w) if w is not None else int(x_end)
            return np.asarray(array[fi, wi, y0:y1, x0:x1], dtype=np.float32)
    # Fallback: full load then crop (TIFF or unrecognised zarr layout)
    full = load_image_array(str(path))
    h, w = int(full.shape[0]), int(full.shape[1])
    y0, y1 = max(int(y_start), 0), min(int(y_end), h)
    x0, x1 = max(int(x_start), 0), min(int(x_end), w)
    return full[y0:y1, x0:x1].astype(np.float32, copy=False)


def dataset_plane_shape(dataset: ImageDataset, spectral_cube_index: int, wavelength_nm: float) -> tuple[int, int]:
    record = dataset_get_record(dataset, spectral_cube_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for spectral_cube_index={spectral_cube_index}, wavelength={wavelength_nm}")
    return load_image_shape(str(record.path))


def is_ome_zarr_dataset(folder: Path) -> bool:
    return folder.is_dir() and (
        (folder / OME_ZARR_GROUP_FILENAME).exists()
        or (folder / "zarr.json").exists()
        or (folder / OME_ZARR_ARRAY_DIRNAME / OME_ZARR_ARRAY_META_FILENAME).exists()
    )


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


def _ome_zarr_root_attrs(root: Path) -> dict:
    attrs_path = root / OME_ZARR_META_FILENAME
    if not attrs_path.exists():
        return {}
    try:
        payload = json.loads(attrs_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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
        chunk_size_px = int(lspr_attrs.get("chunk_size_px", 0))
        if chunk_size_px <= 0:
            return None
        shard_mode = str(lspr_attrs.get("shard_mode", "per_image"))
        if shard_mode == "per_frame":  # legacy value from before the frame -> spectral cube rename
            shard_mode = "per_spectral_cube"
        compression = str(lspr_attrs.get("compression", "none"))
        compression_cname = None if compression == "none" else compression.split("+")[0]
        dtype = np.dtype(str(lspr_attrs.get("dtype") or array.dtype))
        fill_value = 0.0 if np.issubdtype(dtype, np.integer) else float("nan")
        ich = icw = chunk_size_px
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
    except Exception:
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
            index = np.frombuffer(trailer[:index_len], dtype=np.uint64)
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
    except Exception:
        return None

    return np.array(plane[:image_height, :image_width], dtype=dtype, copy=True)


def _ome_zarr_array_meta(root: Path) -> dict:
    array_meta_path = _ome_zarr_array_dir(root) / OME_ZARR_ARRAY_META_FILENAME
    if not array_meta_path.exists():
        raise FileNotFoundError(f"Missing OME-Zarr array metadata: {array_meta_path}")
    payload = json.loads(array_meta_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid OME-Zarr array metadata in {array_meta_path}")
    return payload


def _ome_zarr_plane_path(root: Path, spectral_cube_index: int, wavelength_index: int) -> Path:
    """Build the synthetic per-plane "path" this app uses as an `ImageRecord.path`
    for an OME-Zarr dataset. Not a real file on disk -- its stem
    `{spectral_cube_pos}.{wavelength_pos}.0.0` (positions within the sorted
    spectral_cube_indices/wavelengths_nm lists, not the raw index/nm values)
    is parsed back out by `_read_ome_zarr_plane_by_path` to know which plane
    to read, the same way a real TIFF path's filename is parsed by
    `IMAGE_PATTERN`.
    """
    return _ome_zarr_array_dir(root) / f"{int(spectral_cube_index)}.{int(wavelength_index)}.0.0"


def load_ome_zarr_dataset(folder: Path) -> ImageDataset:
    """Load an OME-Zarr export (from `export_ome_zarr_dataset`) as an `ImageDataset`.

    Reads the array shape (spectral_cube_count, wavelength_count, y, x) and
    the app's own `lspr` attrs group (spectral cube indices, wavelengths,
    written by `export_ome_zarr_dataset`) to reconstruct one `ImageRecord`
    per plane, with each record's `path` a synthetic, non-existent path (see
    `_ome_zarr_plane_path`) that later reads recognize and route through the
    zarr-aware loading path instead of a real file open.
    """
    root = _ome_zarr_root(folder)
    if root is None:
        raise FileNotFoundError(f"No OME-Zarr dataset found in {folder}")
    zarr_group = _ome_zarr_group_cached(str(root))
    if OME_ZARR_ARRAY_DIRNAME not in zarr_group:
        raise FileNotFoundError(f"OME-Zarr image array '{OME_ZARR_ARRAY_DIRNAME}' was not found in {folder}")
    array = zarr_group[OME_ZARR_ARRAY_DIRNAME]
    shape = list(getattr(array, "shape", []))
    if not (isinstance(shape, list) and len(shape) == 4):
        raise ValueError("OME-Zarr loader expects a 4D image stack shaped as [spectral_cube, wavelength, y, x].")
    spectral_cube_count, wavelength_count = int(shape[0]), int(shape[1])
    attrs = dict(zarr_group.attrs.asdict() if hasattr(zarr_group.attrs, "asdict") else dict(zarr_group.attrs))
    lspr_meta = attrs.get(OME_ZARR_LSPR_KEY, {}) if isinstance(attrs.get(OME_ZARR_LSPR_KEY, {}), dict) else {}
    # "frame_indices" is the legacy key name from before the frame -> spectral cube rename.
    legacy_indices = lspr_meta.get("frame_indices", list(range(spectral_cube_count)))
    spectral_cube_indices = [int(value) for value in lspr_meta.get("spectral_cube_indices", legacy_indices)]
    wavelengths_nm = [float(value) for value in lspr_meta.get("wavelengths_nm", list(range(wavelength_count)))]
    if len(spectral_cube_indices) != spectral_cube_count:
        spectral_cube_indices = list(range(spectral_cube_count))
    if len(wavelengths_nm) != wavelength_count:
        wavelengths_nm = [float(index) for index in range(wavelength_count)]
    records: list[ImageRecord] = []
    for spectral_cube_pos, spectral_cube_index in enumerate(spectral_cube_indices):
        for wl_pos, wavelength_nm in enumerate(wavelengths_nm):
            plane_path = _ome_zarr_plane_path(root, spectral_cube_pos, wl_pos)
            records.append(ImageRecord(ImageKey(wavelength_nm=float(wavelength_nm), spectral_cube_index=int(spectral_cube_index)), plane_path))
    if not records:
        raise FileNotFoundError(f"No OME-Zarr planes found in {folder}")
    acquisition_metadata = None
    acquisition_metadata_payload = attrs.get(OME_ZARR_ACQUISITION_METADATA_KEY)
    if isinstance(acquisition_metadata_payload, dict):
        try:
            acquisition_metadata = acquisition_metadata_from_payload(acquisition_metadata_payload)
        except ValueError:
            _LOGGER.warning("Ignoring malformed acquisition metadata in %s", folder)
    return ImageDataset(folder=root, records=records, source_format="ome_zarr", acquisition_metadata=acquisition_metadata)


def _format_seconds(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def probe_ome_zarr_export_shape(
    dataset: ImageDataset,
    preprocessing: PreprocessingSettings | None,
) -> tuple[int, int, np.dtype]:
    """Determine the (height, width, dtype) an OME-Zarr export of this dataset
    would produce, without running the full export. Used both by the exporter
    itself and by the GUI to name/compare a destination folder before export
    actually starts, so the two must stay in exact agreement.
    """
    records = sorted(dataset.records, key=lambda record: (int(record.key.spectral_cube_index), float(record.key.wavelength_nm)))
    if not records:
        raise ValueError("No images are available for OME-Zarr export.")
    apply_image_tools = preprocessing is not None and bool(getattr(preprocessing, "image_tools_enabled", False))
    first_image = _load_image_array_native(str(records[0].path))
    if apply_image_tools:
        first_image = apply_spatial_preprocessing(first_image, preprocessing)
    if first_image.ndim != 2:
        raise ValueError("OME-Zarr export expects 2D image planes.")
    height, width = first_image.shape[:2]
    if np.issubdtype(first_image.dtype, np.integer):
        target_dtype = np.dtype(np.uint16 if first_image.dtype.itemsize <= 2 else first_image.dtype)
    else:
        target_dtype = np.dtype(np.float32)
    return int(height), int(width), target_dtype


def format_ome_zarr_dtype_tag(dtype: np.dtype) -> str:
    kind_prefix = {"u": "u", "i": "i", "f": "f"}.get(np.dtype(dtype).kind, np.dtype(dtype).kind)
    return f"{kind_prefix}{np.dtype(dtype).itemsize * 8}"


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def sanitize_ome_zarr_export_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", name.strip()).strip("_")
    return cleaned or "export"


def build_ome_zarr_export_folder_name(
    name: str,
    *,
    width: int,
    height: int,
    spectral_cube_count: int,
    wavelength_count: int,
    chunk_size_px: int,
    shard_mode: str,
    compression_enabled: bool,
    dtype: np.dtype,
) -> str:
    """Compact, human-scannable folder name: name + the parameters that most
    affect what the dataset actually contains (size, spectral_cube_index/wavelength count,
    chunking/sharding, compression, dtype). Collision-safety against a
    same-named-but-different export is handled separately by comparing the
    destination's actual saved metadata, not by this name alone.
    """
    safe_name = sanitize_ome_zarr_export_name(name)
    shard_tag = "F" if shard_mode == "per_spectral_cube" else "I"
    compression_tag = "Z" if compression_enabled else "N"
    dtype_tag = format_ome_zarr_dtype_tag(dtype)
    return (
        f"{safe_name}_{int(width)}x{int(height)}_{int(spectral_cube_count)}x{int(wavelength_count)}"
        f"_c{int(chunk_size_px)}{shard_tag}{compression_tag}_{dtype_tag}.ome.zarr"
    )


@dataclass
class OmeZarrExportSummary:
    width: int
    height: int
    spectral_cube_count: int
    wavelength_count: int
    chunk_size_px: int
    shard_mode: str
    compression_enabled: bool
    dtype_str: str
    image_tools_applied: bool
    rotation_angle_deg: float = 0.0
    rotation_fill_dark: bool = False
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: tuple[int, int, int, int] | None = None
    pixel_size_um: tuple[float, float] | None = None
    source_folder: str | None = None

    def field_lines(self) -> list[tuple[str, str]]:
        shard_label = "1 spectral cube per file" if self.shard_mode == "per_spectral_cube" else "1 image per file"
        lines = [
            ("Image size", f"{self.width} x {self.height} px"),
            ("Spectral cubes x wavelengths", f"{self.spectral_cube_count} x {self.wavelength_count}"),
            ("Chunk / shard", f"{self.chunk_size_px}px, {shard_label}"),
            ("Compression", "lz4 + bitshuffle" if self.compression_enabled else "none"),
            ("Dtype", self.dtype_str),
            ("Image tools", "applied (linked)" if self.image_tools_applied else "not applied (raw pixels)"),
        ]
        if self.image_tools_applied:
            lines.append(("Rotation", f"{self.rotation_angle_deg:.2f} deg" if abs(self.rotation_angle_deg) > 1e-9 else "none"))
            flips = [label for enabled, label in ((self.flip_horizontal, "horizontal"), (self.flip_vertical, "vertical")) if enabled]
            lines.append(("Flip", ", ".join(flips) if flips else "none"))
            lines.append(("Crop", f"x={self.crop[0]}, y={self.crop[1]}, w={self.crop[2]}, h={self.crop[3]}" if self.crop else "none"))
        if self.pixel_size_um is not None:
            lines.append(("Pixel size", f"{self.pixel_size_um[0]:.4f} x {self.pixel_size_um[1]:.4f} um/px"))
        if self.source_folder:
            lines.append(("Source folder", self.source_folder))
        return lines


def describe_new_ome_zarr_export(
    dataset: ImageDataset,
    preprocessing: PreprocessingSettings | None,
    *,
    chunk_size_px: int,
    compression_enabled: bool,
    shard_mode: str,
) -> OmeZarrExportSummary:
    height, width, target_dtype = probe_ome_zarr_export_shape(dataset, preprocessing)
    apply_image_tools = preprocessing is not None and bool(getattr(preprocessing, "image_tools_enabled", False))
    crop_tuple = None
    rotation_angle_deg = 0.0
    rotation_fill_dark = False
    flip_horizontal = False
    flip_vertical = False
    pixel_size_um = None
    if apply_image_tools and preprocessing is not None:
        rotation_angle_deg = float(preprocessing.rotation_angle_deg)
        rotation_fill_dark = bool(preprocessing.rotation_fill_dark)
        flip_horizontal = bool(preprocessing.flip_horizontal)
        flip_vertical = bool(preprocessing.flip_vertical)
        crop = preprocessing.crop
        if crop.enabled and crop.width > 0 and crop.height > 0:
            crop_tuple = (int(crop.x), int(crop.y), int(crop.width), int(crop.height))
        if bool(getattr(preprocessing, "calibration_enabled", False)):
            pixel_size_um = (float(preprocessing.microns_per_pixel_x), float(preprocessing.microns_per_pixel_y))
    return OmeZarrExportSummary(
        width=width,
        height=height,
        spectral_cube_count=len(dataset.spectral_cube_indices),
        wavelength_count=len(dataset.wavelengths_nm),
        chunk_size_px=int(chunk_size_px),
        shard_mode=str(shard_mode),
        compression_enabled=bool(compression_enabled),
        dtype_str=str(target_dtype),
        image_tools_applied=apply_image_tools,
        rotation_angle_deg=rotation_angle_deg,
        rotation_fill_dark=rotation_fill_dark,
        flip_horizontal=flip_horizontal,
        flip_vertical=flip_vertical,
        crop=crop_tuple,
        pixel_size_um=pixel_size_um,
        source_folder=str(dataset.folder),
    )


def read_existing_ome_zarr_summary(destination: Path) -> OmeZarrExportSummary | None:
    """Read back an existing OME-Zarr export's saved parameters for comparison
    against a prospective new export. Returns None if `destination` isn't a
    recognized LSPR OME-Zarr export (missing/foreign content, or unreadable).
    """
    if not destination.exists():
        return None
    try:
        zarr, _, _, _ = _require_ome_zarr_support()
        from zarr.storage import LocalStore

        group = zarr.open_group(store=LocalStore(str(destination)), mode="r")
        lspr_attrs = dict(group.attrs.get(OME_ZARR_LSPR_KEY, {}))
        if not lspr_attrs:
            return None
        array = group[OME_ZARR_ARRAY_DIRNAME]
        spectral_cube_count, wavelength_count, height, width = array.shape
        image_tools = lspr_attrs.get("image_tools") or {}
        crop = image_tools.get("crop")
        pixel_size = lspr_attrs.get("pixel_size_um")
        shard_mode = str(lspr_attrs.get("shard_mode", "per_image"))
        if shard_mode == "per_frame":  # legacy value from before the frame -> spectral cube rename
            shard_mode = "per_spectral_cube"
        return OmeZarrExportSummary(
            width=int(width),
            height=int(height),
            spectral_cube_count=int(spectral_cube_count),
            wavelength_count=int(wavelength_count),
            chunk_size_px=int(lspr_attrs.get("chunk_size_px", 0)),
            shard_mode=shard_mode,
            compression_enabled=str(lspr_attrs.get("compression", "none")) != "none",
            dtype_str=str(lspr_attrs.get("dtype", "")),
            image_tools_applied=bool(lspr_attrs.get("image_tools_applied", False)),
            rotation_angle_deg=float(image_tools.get("rotation_angle_deg", 0.0)),
            rotation_fill_dark=bool(image_tools.get("rotation_fill_dark", False)),
            flip_horizontal=bool(image_tools.get("flip_horizontal", False)),
            flip_vertical=bool(image_tools.get("flip_vertical", False)),
            crop=(int(crop["x"]), int(crop["y"]), int(crop["width"]), int(crop["height"])) if crop else None,
            pixel_size_um=(float(pixel_size["x"]), float(pixel_size["y"])) if pixel_size else None,
            source_folder=lspr_attrs.get("source_folder"),
        )
    except Exception:
        # Expected whenever the destination isn't a recognized LSPR OME-Zarr export
        # (e.g. an unrelated folder, or a partially-written one) — not an error.
        logging.getLogger("lspr_imaging_app.io").debug(
            "Destination %s is not a readable LSPR OME-Zarr export.", destination, exc_info=True
        )
        return None


def compare_ome_zarr_summaries(
    existing: OmeZarrExportSummary,
    new: OmeZarrExportSummary,
) -> tuple[bool, list[tuple[str, str, str]]]:
    """Compare two export summaries field-by-field. Returns (all_match, rows),
    where rows is (field_label, existing_value, new_value) for every field
    present on either side (fields only one side has show as "-" on the other).
    """
    existing_lines = dict(existing.field_lines())
    new_lines = dict(new.field_lines())
    all_labels = list(existing_lines.keys())
    all_labels += [label for label in new_lines.keys() if label not in existing_lines]
    rows: list[tuple[str, str, str]] = []
    all_match = True
    for label in all_labels:
        existing_value = existing_lines.get(label, "-")
        new_value = new_lines.get(label, "-")
        if existing_value != new_value:
            all_match = False
        rows.append((label, existing_value, new_value))
    return all_match, rows


def export_ome_zarr_dataset(
    dataset: ImageDataset,
    destination: Path,
    *,
    chunk_size_px: int = 64,
    compression_enabled: bool = True,
    shard_mode: str = "per_image",
    preprocessing: PreprocessingSettings | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    excluded_rules: list[ImageExclusionRule] | None = None,
    skip_excluded: bool = False,
    adaptive_workers_enabled: bool = True,
    adaptive_batch_mb: int = 1024,
) -> Path:
    """Export `dataset` (a TIFF stack or another OME-Zarr dataset) as a new
    OME-Zarr v3 dataset at `destination`, using zarr's sharding codec (Zarr
    core protocol v3.0: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/sharding-indexed/index.html)
    so many small logical chunks (`chunk_size_px` x `chunk_size_px`, what
    readers see) are physically grouped into one shard file per plane
    (`shard_mode="per_image"`) or one shard file per spectral cube covering
    all its wavelengths (`shard_mode="per_spectral_cube"`) -- far fewer
    files on disk, and readers can still fetch just the chunks they need.

    The actual shard bytes are written directly by worker processes
    (`ProcessPoolExecutor`) using `numcodecs`' Blosc compressor and a
    hand-built CRC32C-checksummed index, bypassing zarr's own async
    write API entirely -- ~119 MB/s at a 64px chunk size in benchmarking,
    vs. ~21 MB/s writing through zarr's API at the same chunk size, because
    zarr dispatches every chunk's compression and write as its own
    `asyncio.to_thread` call. Written this way, the shards are still fully
    standard zarr v3 sharded arrays: any zarr-v3-compliant reader (including
    zarr's own generic array API) can read them back with no special
    knowledge of how they were produced -- `_fast_read_zarr_plane` is purely
    a *read-side* speed optimization for this app's own loading path, not a
    requirement for the file format to be valid.

    When `preprocessing.image_tools_enabled` is set, rotation/flip/crop (and
    physical pixel-size calibration, meaningful only in that same processed
    coordinate space) are baked into the exported pixel data rather than
    left for a reader to apply -- see `docs/image_tools_coordinate_spaces.md`.
    """
    zarr, _, _, write_multiscales_metadata = _require_ome_zarr_support()
    try:
        from zarr.codecs import BloscCodec
        from zarr.storage import LocalStore
    except ImportError as exc:
        raise ImportError("OME-Zarr export requires zarr 3.x (zarr.codecs.BloscCodec).") from exc
    destination = destination if destination.suffix == ".zarr" or destination.name.endswith(".ome.zarr") else destination.with_suffix(".ome.zarr")
    records = sorted(dataset.records, key=lambda record: (int(record.key.spectral_cube_index), float(record.key.wavelength_nm)))
    if not records:
        raise ValueError("No images are available for OME-Zarr export.")

    # Image tools (rotation/flip/crop) are baked into the exported pixels only when
    # they are "applied/linked" in the GUI. Calibration (um/px) is only meaningful
    # in that same processed coordinate space, so it rides along with the same flag.
    apply_image_tools = preprocessing is not None and bool(getattr(preprocessing, "image_tools_enabled", False))
    height, width, target_dtype = probe_ome_zarr_export_shape(dataset, preprocessing)
    spectral_cubes = dataset.spectral_cube_indices
    wavelengths = dataset.wavelengths_nm
    record_map = dataset_record_map(dataset)

    # Inner chunk (logical, what readers see).
    ich = icw = max(int(chunk_size_px), 4)
    # Shard dimensions must be exact multiples of inner chunk.
    sh = ((height + ich - 1) // ich) * ich
    sw = ((width  + icw - 1) // icw) * icw
    n_cy = sh // ich
    n_cx = sw // icw
    n_wl = len(wavelengths)

    # per_image: one shard = one wavelength×spectral_cube_index  → shard (1, 1, sh, sw)
    # per_spectral_cube: one shard = all wavelengths for one spectral_cube_index → shard (1, n_wl, sh, sw)
    per_spectral_cube = shard_mode == "per_spectral_cube"
    shard_shape = (1, n_wl, sh, sw) if per_spectral_cube else (1, 1, sh, sw)
    n_inner_shard = (n_wl if per_spectral_cube else 1) * n_cy * n_cx

    destination.mkdir(parents=True, exist_ok=True)
    store = LocalStore(str(destination))
    group = zarr.open_group(store=store, mode="w", zarr_format=3)
    group.create_array(
        OME_ZARR_ARRAY_DIRNAME,
        shape=(len(spectral_cubes), n_wl, height, width),
        chunks=(1, 1, ich, icw),      # logical inner chunk — what readers see
        shards=shard_shape,            # physical shard on disk
        dtype=target_dtype,
        fill_value=0 if np.issubdtype(target_dtype, np.integer) else np.nan,
        compressors=BloscCodec(cname="lz4", clevel=1, shuffle="bitshuffle") if compression_enabled else None,
    )
    from lspr_imaging_app.io._zarr_export_worker import ShardWriteSpec, _worker_init, write_shard

    fill_value = float(0 if np.issubdtype(target_dtype, np.integer) else float("nan"))
    compression_cname = "lz4" if compression_enabled else None
    shard_base_dir = destination / OME_ZARR_ARRAY_DIRNAME / "c"
    total_planes = max(int(len(spectral_cubes) * n_wl), 1)
    total_units = max(total_planes * n_cy * n_cx, 1)
    completed_units = 0
    progress_lock = threading.Lock()
    last_percent = -1
    started_at = time.perf_counter()

    def report_progress(force: bool = False) -> None:
        nonlocal last_percent
        if progress_callback is None:
            return
        percent = int(round((completed_units / total_units) * 100.0))
        if not force and percent == last_percent:
            return
        last_percent = percent
        elapsed = max(time.perf_counter() - started_at, 1e-6)
        eta_text = "ETA: --:--"
        if 0 < completed_units < total_units:
            remaining = max(total_units - completed_units, 0)
            eta_text = f"ETA: {_format_seconds(elapsed / completed_units * remaining)}"
        progress_callback(percent, f"Exporting {completed_units}/{total_units} tiles | {eta_text}")

    def _plane_excluded(fi: int, wn: float) -> bool:
        return skip_excluded and is_excluded(excluded_rules or [], fi, wn)

    def _build_per_image_spec(fp: int, wp: int, fi: int, wn: float) -> ShardWriteSpec:
        record = record_map.get((int(fi), float(wn)))
        return ShardWriteSpec(
            record_paths=[str(record.path) if record is not None and not _plane_excluded(fi, wn) else ""],
            shard_path=str(shard_base_dir / str(fp) / str(wp) / "0" / "0"),
            inner_chunk_h=ich, inner_chunk_w=icw,
            image_height=height, image_width=width,
            shard_h=sh, shard_w=sw, n_cy=n_cy, n_cx=n_cx,
            n_inner=n_inner_shard,
            dtype_str=target_dtype.str,
            fill_value=fill_value, compression_cname=compression_cname,
            apply_image_tools=apply_image_tools,
            preprocessing=preprocessing if apply_image_tools else None,
        )

    def _build_per_spectral_cube_spec(fp: int, fi: int) -> ShardWriteSpec:
        return ShardWriteSpec(
            record_paths=[
                str(record_map[(int(fi), float(wn))].path)
                if (int(fi), float(wn)) in record_map and not _plane_excluded(fi, wn) else ""
                for wn in wavelengths
            ],
            shard_path=str(shard_base_dir / str(fp) / "0" / "0" / "0"),
            inner_chunk_h=ich, inner_chunk_w=icw,
            image_height=height, image_width=width,
            shard_h=sh, shard_w=sw, n_cy=n_cy, n_cx=n_cx,
            n_inner=n_inner_shard,
            dtype_str=target_dtype.str,
            fill_value=fill_value, compression_cname=compression_cname,
            apply_image_tools=apply_image_tools,
            preprocessing=preprocessing if apply_image_tools else None,
        )

    if per_spectral_cube:
        task_args: list = [(int(fp), int(fi)) for fp, fi in enumerate(spectral_cubes)]
        make_spec = _build_per_spectral_cube_spec
        n_tasks = len(spectral_cubes)
    else:
        task_args = [
            (int(fp), int(wp), int(fi), float(wn))
            for fp, fi in enumerate(spectral_cubes)
            for wp, wn in enumerate(wavelengths)
        ]
        make_spec = _build_per_image_spec
        n_tasks = len(task_args)

    # ProcessPoolExecutor: each worker runs in a separate Python interpreter with
    # its own GIL, so the main Qt thread stays responsive at any CPU count.
    # Baseline is plain cpu_count (no oversubscription) -- adaptive tuning below
    # is what decides whether to go higher, based on real measurement instead of
    # a guess.
    cpu_count = int(os.cpu_count() or 4)
    worker_count = max(1, min(cpu_count, n_tasks))
    prefetch_window = max(worker_count * 2, worker_count + 2)
    task_iter = iter(task_args)
    pending: dict = {}

    # Adaptive worker tuning: each export task mixes disk I/O (image read, shard
    # write) with CPU work (Blosc compress). Periodically check, from real timing
    # of the export itself, which one dominates -- if the export is spending most
    # of its time waiting on disk, add worker processes so other workers' CPU
    # work can fill that gap; if it's CPU-bound, stay at cpu_count (adding
    # workers wouldn't help -- there's no idle core to fill). Bounded to
    # [cpu_count, 2x cpu_count]: never go *below* cpu_count, only compensate for
    # I/O wait above it. Off (adaptive_workers_enabled=False) skips this entirely
    # so it can serve as a true, reproducible baseline. See the Preferences >
    # "OME-Zarr export: adaptive worker tuning" menu for the user-facing controls.
    batch_bytes_threshold = max(int(adaptive_batch_mb), 1) * 1024 * 1024
    window_bytes = 0
    window_read_s = 0.0
    window_write_s = 0.0
    window_compress_s = 0.0

    report_progress(force=True)
    executor = ProcessPoolExecutor(max_workers=worker_count, initializer=_worker_init)
    executors_to_close = [executor]

    def submit_next() -> bool:
        try:
            args = next(task_iter)
        except StopIteration:
            return False
        future = executor.submit(write_shard, make_spec(*args))
        pending[future] = True
        return True

    try:
        for _ in range(min(prefetch_window, n_tasks)):
            if not submit_next():
                break

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                for ex in executors_to_close:
                    ex.shutdown(wait=False, cancel_futures=True)
                raise RuntimeError("OME-Zarr export cancelled.")
            done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                result = future.result()
                with progress_lock:
                    completed_units = min(total_units, completed_units + result.n_inner)
                report_progress()

                if adaptive_workers_enabled:
                    window_bytes += result.bytes_written
                    window_read_s += result.read_s
                    window_write_s += result.write_s
                    window_compress_s += result.compress_s
                    if window_bytes >= batch_bytes_threshold:
                        io_total = window_read_s + window_write_s
                        denom = io_total + window_compress_s
                        if denom > 0:
                            io_fraction = io_total / denom
                            step = max(1, cpu_count // 4)
                            if io_fraction >= ADAPTIVE_IO_FLIP_UP and worker_count < round(cpu_count * ADAPTIVE_MAX_FACTOR):
                                new_worker_count = min(round(cpu_count * ADAPTIVE_MAX_FACTOR), worker_count + step)
                            elif io_fraction <= ADAPTIVE_IO_FLIP_DOWN and worker_count > cpu_count:
                                new_worker_count = max(cpu_count, worker_count - step)
                            else:
                                new_worker_count = worker_count
                            new_worker_count = max(1, min(new_worker_count, n_tasks))
                            if new_worker_count != worker_count:
                                old_worker_count = worker_count
                                old_executor = executor
                                executor = ProcessPoolExecutor(max_workers=new_worker_count, initializer=_worker_init)
                                executors_to_close.append(executor)
                                old_executor.shutdown(wait=False)
                                prefetch_window = max(new_worker_count * 2, new_worker_count + 2)
                                if progress_callback is not None:
                                    progress_callback(
                                        last_percent if last_percent >= 0 else 0,
                                        f"ADAPTIVE_FLIP: {worker_count} -> {new_worker_count} workers "
                                        f"({io_fraction:.0%} I/O wait over last {adaptive_batch_mb}MB)",
                                    )
                                worker_count = new_worker_count
                                if worker_count > old_worker_count:
                                    # Top up in-flight work so the added workers have
                                    # something to do immediately, instead of waiting
                                    # for the next one-in-one-out submission.
                                    target_pending = min(prefetch_window, n_tasks)
                                    while len(pending) < target_pending:
                                        if not submit_next():
                                            break
                        window_bytes = 0
                        window_read_s = window_write_s = window_compress_s = 0.0

                submit_next()
    finally:
        for ex in executors_to_close:
            ex.shutdown(wait=True)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("OME-Zarr export cancelled.")

    # Embed real physical pixel size (um/px) in the OME-NGFF metadata when the
    # source calibration is available and the export is in the same processed
    # coordinate space the calibration was measured in (i.e. image tools applied).
    write_pixel_size = (
        apply_image_tools
        and preprocessing is not None
        and bool(getattr(preprocessing, "calibration_enabled", False))
    )
    scale_y = float(preprocessing.microns_per_pixel_y) if write_pixel_size else 1.0
    scale_x = float(preprocessing.microns_per_pixel_x) if write_pixel_size else 1.0
    axes_units = {"y": "micrometer", "x": "micrometer"} if write_pixel_size else None

    write_multiscales_metadata(
        group,
        datasets=[{"path": OME_ZARR_ARRAY_DIRNAME, "coordinateTransformations": [{"type": "scale", "scale": [1, 1, scale_y, scale_x]}]}],
        axes=["t", "wavelength", "y", "x"],
        name="LSPR image stack",
        axes_units=axes_units,
    )
    lspr_attrs: dict = {
        "spectral_cube_indices": [int(value) for value in spectral_cubes],
        "wavelengths_nm": [float(value) for value in wavelengths],
        "source_folder": str(dataset.folder),
        "chunk_size_px": int(chunk_size_px),
        "shard_mode": shard_mode,
        "compression": "lz4+bitshuffle" if compression_enabled else "none",
        "dtype": str(target_dtype),
        "image_tools_applied": bool(apply_image_tools),
    }
    if apply_image_tools and preprocessing is not None:
        crop = preprocessing.crop
        lspr_attrs["image_tools"] = {
            "rotation_angle_deg": float(preprocessing.rotation_angle_deg),
            "rotation_fill_dark": bool(preprocessing.rotation_fill_dark),
            "flip_horizontal": bool(preprocessing.flip_horizontal),
            "flip_vertical": bool(preprocessing.flip_vertical),
            "crop": (
                {"x": int(crop.x), "y": int(crop.y), "width": int(crop.width), "height": int(crop.height)}
                if crop.enabled and crop.width > 0 and crop.height > 0
                else None
            ),
        }
    if write_pixel_size:
        lspr_attrs["pixel_size_um"] = {"x": scale_x, "y": scale_y}
    group.attrs[OME_ZARR_LSPR_KEY] = lspr_attrs

    # Mirror any loaded acquisition metadata (camera/illumination settings,
    # per-image timing, comment events - see `Dataset > Metadata`) into the
    # export, in the same payload shape as the standalone sidecar file
    # (`storage/workspace.py`'s `build_acquisition_metadata_payload`), so
    # `load_ome_zarr_dataset` can restore it on a later re-import without
    # needing the original legacy files or native HDF5 nearby.
    if dataset.acquisition_metadata is not None:
        group.attrs[OME_ZARR_ACQUISITION_METADATA_KEY] = build_acquisition_metadata_payload(dataset.acquisition_metadata)

    return destination


def load_image_array(path_str: str) -> np.ndarray:
    before_hits = _load_image_array_cached.cache_info().hits
    array = _load_image_array_cached(path_str)
    after_hits = _load_image_array_cached.cache_info().hits
    if after_hits > before_hits:
        _LOGGER.debug("Image cache hit | %s", Path(path_str).name)
    return array


@lru_cache(maxsize=96)
def _load_image_array_cached(path_str: str) -> np.ndarray:
    array = _load_image_array_uncached(path_str)
    _LOGGER.debug("Image cache built | %s", Path(path_str).name)
    try:
        array.setflags(write=False)
    except Exception:
        pass
    return array


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


def _load_image_array_uncached(path_str: str) -> np.ndarray:
    path = Path(path_str)
    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        return np.asarray(_read_ome_zarr_plane_by_path(path, ome_root), dtype=np.float32)
    if path.suffix.lower() in {".tif", ".tiff"} and _tifffile_imread is not None:
        try:
            return np.asarray(_tifffile_imread(path_str, maxworkers=max(2, os.cpu_count() or 1)), dtype=np.float32)
        except TypeError:
            return np.asarray(_tifffile_imread(path_str), dtype=np.float32)
    try:
        with Image.open(path_str) as image:
            return np.array(image, dtype=np.float32)
    except Exception:
        if _tifffile_imread is None:
            raise
        return np.asarray(_tifffile_imread(path_str), dtype=np.float32)


def _load_image_array_native(path_str: str) -> np.ndarray:
    """Load an image preserving its on-disk dtype (no float32 upcast).

    Used for OME-Zarr export, where the exported array should keep the same
    bit depth as the source files instead of the float32 used for display
    and analysis math elsewhere in the app.
    """
    path = Path(path_str)
    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        return _read_ome_zarr_plane_by_path(path, ome_root)
    if path.suffix.lower() in {".tif", ".tiff"} and _tifffile_imread is not None:
        try:
            # maxworkers=2: inter-file parallelism comes from the export worker pool;
            # giving each file more threads causes N_workers × cpu_count thread pile-up.
            return np.asarray(_tifffile_imread(path_str, maxworkers=2))
        except TypeError:
            return np.asarray(_tifffile_imread(path_str))
    try:
        with Image.open(path_str) as image:
            return np.array(image)
    except Exception:
        if _tifffile_imread is None:
            raise
        return np.asarray(_tifffile_imread(path_str))


@lru_cache(maxsize=1024)
def load_image_shape(path_str: str) -> tuple[int, int]:
    path = Path(path_str)
    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        array = _ome_zarr_array_cached(str(ome_root))
        shape = getattr(array, "shape", None)
        if isinstance(shape, tuple) and len(shape) == 4:
            return int(shape[2]), int(shape[3])
    if path.suffix.lower() in {".tif", ".tiff"} and _tifffile_TiffFile is not None:
        try:
            with _tifffile_TiffFile(path_str) as tif:
                series = tif.series[0] if tif.series else None
                if series is not None:
                    shape = getattr(series, "shape", None)
                    if isinstance(shape, tuple) and len(shape) >= 2:
                        return int(shape[-2]), int(shape[-1])
        except Exception:
            pass
    try:
        with Image.open(path_str) as image:
            return int(image.height), int(image.width)
    except Exception:
        return tuple(int(v) for v in load_image_array(path_str).shape[:2])
