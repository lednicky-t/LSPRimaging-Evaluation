from __future__ import annotations

import json
import logging
import re
import threading
import time
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

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord, PreprocessingSettings
from lspr_imaging_app.processing.preprocess import apply_spatial_preprocessing


IMAGE_PATTERN = re.compile(r"WL(?P<wl>\d+(?:\.\d+)?)Frame(?P<frame>\d+)", re.IGNORECASE)
OME_ZARR_META_FILENAME = ".zattrs"
OME_ZARR_GROUP_FILENAME = ".zgroup"
OME_ZARR_ARRAY_DIRNAME = "0"
OME_ZARR_ARRAY_META_FILENAME = ".zarray"
OME_ZARR_LSPR_KEY = "lspr"
_OME_ZARR_IMPORT_ERROR: ImportError | None = None


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


def load_dataset(folder: Path) -> ImageDataset:
    if is_ome_zarr_dataset(folder):
        return load_ome_zarr_dataset(folder)
    records: list[ImageRecord] = []
    for path in sorted(folder.glob("*.tif*")):
        match = IMAGE_PATTERN.search(path.stem)
        if not match:
            continue
        wl = float(match.group("wl"))
        frame = int(match.group("frame"))
        records.append(ImageRecord(ImageKey(wavelength_nm=wl, frame_index=frame), path))
    if not records:
        raise FileNotFoundError(f"No matching TIFF files found in {folder}")
    return ImageDataset(folder=folder, records=records, source_format="image_stack")


def dataset_record_map(dataset: ImageDataset) -> dict[tuple[int, float], ImageRecord]:
    return {(record.key.frame_index, record.key.wavelength_nm): record for record in dataset.records}


def dataset_is_ome_zarr(dataset: ImageDataset | None) -> bool:
    return bool(dataset is not None and dataset.is_ome_zarr)


def dataset_get_record(dataset: ImageDataset, frame_index: int, wavelength_nm: float) -> ImageRecord | None:
    for record in dataset.records:
        if int(record.key.frame_index) == int(frame_index) and float(record.key.wavelength_nm) == float(wavelength_nm):
            return record
    return None


def dataset_get_record_map(dataset: ImageDataset) -> dict[tuple[int, float], ImageRecord]:
    return dataset_record_map(dataset)


def dataset_load_plane(dataset: ImageDataset, frame_index: int, wavelength_nm: float) -> np.ndarray:
    record = dataset_get_record(dataset, frame_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for frame={frame_index}, wavelength={wavelength_nm}")
    return load_image_array(str(record.path))


def dataset_load_plane_roi(
    dataset: ImageDataset,
    frame_index: int,
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
        record = dataset_get_record(dataset, frame_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for frame={frame_index}, wavelength={wavelength_nm}")
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


def dataset_plane_shape(dataset: ImageDataset, frame_index: int, wavelength_nm: float) -> tuple[int, int]:
    record = dataset_get_record(dataset, frame_index, wavelength_nm)
    if record is None:
        raise KeyError(f"No record found for frame={frame_index}, wavelength={wavelength_nm}")
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


def _ome_zarr_array_meta(root: Path) -> dict:
    array_meta_path = _ome_zarr_array_dir(root) / OME_ZARR_ARRAY_META_FILENAME
    if not array_meta_path.exists():
        raise FileNotFoundError(f"Missing OME-Zarr array metadata: {array_meta_path}")
    payload = json.loads(array_meta_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid OME-Zarr array metadata in {array_meta_path}")
    return payload


def _ome_zarr_plane_path(root: Path, frame_index: int, wavelength_index: int) -> Path:
    return _ome_zarr_array_dir(root) / f"{int(frame_index)}.{int(wavelength_index)}.0.0"


def load_ome_zarr_dataset(folder: Path) -> ImageDataset:
    root = _ome_zarr_root(folder)
    if root is None:
        raise FileNotFoundError(f"No OME-Zarr dataset found in {folder}")
    zarr_group = _ome_zarr_group_cached(str(root))
    if OME_ZARR_ARRAY_DIRNAME not in zarr_group:
        raise FileNotFoundError(f"OME-Zarr image array '{OME_ZARR_ARRAY_DIRNAME}' was not found in {folder}")
    array = zarr_group[OME_ZARR_ARRAY_DIRNAME]
    shape = list(getattr(array, "shape", []))
    if not (isinstance(shape, list) and len(shape) == 4):
        raise ValueError("OME-Zarr loader expects a 4D image stack shaped as [frame, wavelength, y, x].")
    frame_count, wavelength_count = int(shape[0]), int(shape[1])
    attrs = dict(zarr_group.attrs.asdict() if hasattr(zarr_group.attrs, "asdict") else dict(zarr_group.attrs))
    lspr_meta = attrs.get(OME_ZARR_LSPR_KEY, {}) if isinstance(attrs.get(OME_ZARR_LSPR_KEY, {}), dict) else {}
    frame_indices = [int(value) for value in lspr_meta.get("frame_indices", list(range(frame_count)))]
    wavelengths_nm = [float(value) for value in lspr_meta.get("wavelengths_nm", list(range(wavelength_count)))]
    if len(frame_indices) != frame_count:
        frame_indices = list(range(frame_count))
    if len(wavelengths_nm) != wavelength_count:
        wavelengths_nm = [float(index) for index in range(wavelength_count)]
    records: list[ImageRecord] = []
    for frame_pos, frame_index in enumerate(frame_indices):
        for wl_pos, wavelength_nm in enumerate(wavelengths_nm):
            plane_path = _ome_zarr_plane_path(root, frame_pos, wl_pos)
            records.append(ImageRecord(ImageKey(wavelength_nm=float(wavelength_nm), frame_index=int(frame_index)), plane_path))
    if not records:
        raise FileNotFoundError(f"No OME-Zarr planes found in {folder}")
    return ImageDataset(folder=root, records=records, source_format="ome_zarr")


def _format_seconds(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


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
) -> Path:
    # NOTE(perf): zarr v3 sharding — one shard file per plane, 64×64 inner chunks.
    # Workers write each shard directly (bypassing zarr's async event loop) using
    # numcodecs Blosc + CRC32C index. Benchmarks: 119 MB/s at 64px vs 21 MB/s via
    # zarr API at same chunk size. zarr reads the shards transparently via its codec.
    zarr, _, _, write_multiscales_metadata = _require_ome_zarr_support()
    try:
        from zarr.codecs import BloscCodec
        from zarr.storage import LocalStore
    except ImportError as exc:
        raise ImportError("OME-Zarr export requires zarr 3.x (zarr.codecs.BloscCodec).") from exc
    destination = destination if destination.suffix == ".zarr" or destination.name.endswith(".ome.zarr") else destination.with_suffix(".ome.zarr")
    records = sorted(dataset.records, key=lambda record: (int(record.key.frame_index), float(record.key.wavelength_nm)))
    if not records:
        raise ValueError("No images are available for OME-Zarr export.")

    # Image tools (rotation/flip/crop) are baked into the exported pixels only when
    # they are "applied/linked" in the GUI. Calibration (um/px) is only meaningful
    # in that same processed coordinate space, so it rides along with the same flag.
    apply_image_tools = preprocessing is not None and bool(getattr(preprocessing, "image_tools_enabled", False))

    def transform_plane(plane: np.ndarray) -> np.ndarray:
        if not apply_image_tools:
            return plane
        return apply_spatial_preprocessing(plane, preprocessing)

    first_image = transform_plane(_load_image_array_native(str(records[0].path)))
    if first_image.ndim != 2:
        raise ValueError("OME-Zarr export expects 2D image planes.")
    height, width = first_image.shape[:2]
    if np.issubdtype(first_image.dtype, np.integer):
        target_dtype = np.dtype(np.uint16 if first_image.dtype.itemsize <= 2 else first_image.dtype)
    else:
        target_dtype = np.dtype(np.float32)
    frames = dataset.frame_indices
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

    # per_image: one shard = one wavelength×frame  → shard (1, 1, sh, sw)
    # per_frame: one shard = all wavelengths for one frame → shard (1, n_wl, sh, sw)
    per_frame = shard_mode == "per_frame"
    shard_shape = (1, n_wl, sh, sw) if per_frame else (1, 1, sh, sw)
    n_inner_shard = (n_wl if per_frame else 1) * n_cy * n_cx

    destination.mkdir(parents=True, exist_ok=True)
    store = LocalStore(str(destination))
    group = zarr.open_group(store=store, mode="w", zarr_format=3)
    group.create_array(
        OME_ZARR_ARRAY_DIRNAME,
        shape=(len(frames), n_wl, height, width),
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
    total_planes = max(int(len(frames) * n_wl), 1)
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

    def _build_per_image_spec(fp: int, wp: int, fi: int, wn: float) -> ShardWriteSpec:
        record = record_map.get((int(fi), float(wn)))
        return ShardWriteSpec(
            record_paths=[str(record.path) if record is not None else ""],
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

    def _build_per_frame_spec(fp: int, fi: int) -> ShardWriteSpec:
        return ShardWriteSpec(
            record_paths=[
                str(record_map[(int(fi), float(wn))].path)
                if (int(fi), float(wn)) in record_map else ""
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

    if per_frame:
        task_args: list = [(int(fp), int(fi)) for fp, fi in enumerate(frames)]
        make_spec = _build_per_frame_spec
        n_tasks = len(frames)
    else:
        task_args = [
            (int(fp), int(wp), int(fi), float(wn))
            for fp, fi in enumerate(frames)
            for wp, wn in enumerate(wavelengths)
        ]
        make_spec = _build_per_image_spec
        n_tasks = len(task_args)

    # ProcessPoolExecutor: each worker runs in a separate Python interpreter with
    # its own GIL, so the main Qt thread stays responsive at any CPU count.
    worker_count = max(1, min(int(os.cpu_count() or 4), n_tasks))
    prefetch_window = max(worker_count * 2, worker_count + 2)
    task_iter = iter(task_args)
    pending: dict = {}

    report_progress(force=True)
    with ProcessPoolExecutor(max_workers=worker_count, initializer=_worker_init) as executor:
        def submit_next() -> bool:
            try:
                args = next(task_iter)
            except StopIteration:
                return False
            future = executor.submit(write_shard, make_spec(*args))
            pending[future] = True
            return True

        for _ in range(min(prefetch_window, n_tasks)):
            if not submit_next():
                break

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                raise RuntimeError("OME-Zarr export cancelled.")
            done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                tiles = future.result()
                with progress_lock:
                    completed_units = min(total_units, completed_units + tiles)
                report_progress()
                submit_next()
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
        "frame_indices": [int(value) for value in frames],
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


def _load_image_array_uncached(path_str: str) -> np.ndarray:
    path = Path(path_str)
    ome_root = _ome_zarr_root(path.parent.parent if path.parent.name == OME_ZARR_ARRAY_DIRNAME else path.parent)
    if ome_root is not None and path.parent == _ome_zarr_array_dir(ome_root):
        array = _ome_zarr_array_cached(str(ome_root))
        stem = path.name
        try:
            frame_index, wavelength_index, *_ = (int(part) for part in stem.split("."))
        except Exception as exc:
            raise ValueError(f"Invalid OME-Zarr chunk path: {path.name}") from exc
        return np.asarray(array[frame_index, wavelength_index], dtype=np.float32)
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
        array = _ome_zarr_array_cached(str(ome_root))
        stem = path.name
        try:
            frame_index, wavelength_index, *_ = (int(part) for part in stem.split("."))
        except Exception as exc:
            raise ValueError(f"Invalid OME-Zarr chunk path: {path.name}") from exc
        return np.asarray(array[frame_index, wavelength_index])
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
