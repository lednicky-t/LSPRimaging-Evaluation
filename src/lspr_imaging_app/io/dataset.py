from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
        return folder
    if (folder / OME_ZARR_GROUP_FILENAME).exists() and (folder / OME_ZARR_META_FILENAME).exists():
        return folder
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
    chunk_size_px: int = 256,
    compression_enabled: bool = True,
    preprocessing: PreprocessingSettings | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    # TODO(perf): zarr v2 writes one file per chunk, so total file count =
    # frames * wavelengths * tiles_per_plane (often tens of thousands). On Windows
    # with AV scanning, or on network drives, this dominates export time. Switch to
    # zarr v3 with sharding to bundle chunks into fewer physical files while keeping
    # the same logical chunk shape for reads.
    # TODO(perf): reader thread pool (worker_count) and Blosc's internal compression
    # threads can oversubscribe CPU cores simultaneously; benchmark lowering Blosc's
    # nthreads and/or writing multiple planes concurrently from a small writer pool
    # instead of compressing one plane with many threads.
    # TODO(perf): worker_count is capped at cpu_count, tuned for CPU-bound decode; on
    # fast SSD/NVMe storage a higher count (1.5-2x cpu_count) may improve throughput
    # since threads are I/O-bound. Needs benchmarking on target hardware.
    # TODO(feature): expose a faster compression preset (e.g. lz4 + byte-shuffle)
    # alongside the current size-optimized zstd+bitshuffle default.
    zarr, Blosc, _blosc, write_multiscales_metadata = _require_ome_zarr_support()
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

    destination.mkdir(parents=True, exist_ok=True)
    group = zarr.group(store=str(destination), overwrite=True, zarr_format=2)
    chunks = (1, 1, min(int(chunk_size_px), int(height)), min(int(chunk_size_px), int(width)))
    array = group.create_array(
        OME_ZARR_ARRAY_DIRNAME,
        shape=(len(frames), len(wavelengths), height, width),
        chunks=chunks,
        dtype=target_dtype,
        fill_value=0 if np.issubdtype(target_dtype, np.integer) else np.nan,
        compressor=(Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE) if compression_enabled else None),
    )
    y_chunk = max(int(chunks[2]), 1)
    x_chunk = max(int(chunks[3]), 1)
    tiles_y = max((height + y_chunk - 1) // y_chunk, 1)
    tiles_x = max((width + x_chunk - 1) // x_chunk, 1)
    tiles_per_plane = max(int(tiles_y * tiles_x), 1)
    total_planes = max(int(len(frames) * len(wavelengths)), 1)
    total_units = max(total_planes * tiles_per_plane, 1)
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
        progress_callback(percent, f"Exporting {completed_units}/{total_units} chunks | {eta_text}")

    def advance_progress(count: int = 1) -> None:
        nonlocal completed_units
        with progress_lock:
            completed_units = min(total_units, completed_units + max(int(count), 0))
        report_progress()

    def load_plane(frame_pos: int, wavelength_pos: int, frame_index: int, wavelength_nm: float) -> tuple[int, int, np.ndarray | None]:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("OME-Zarr export cancelled.")
        record = record_map.get((int(frame_index), float(wavelength_nm)))
        if record is None:
            return frame_pos, wavelength_pos, None
        plane = transform_plane(_load_image_array_native(str(record.path)))
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("OME-Zarr export cancelled.")
        plane = np.asarray(plane, dtype=target_dtype)
        if plane.shape[:2] != (height, width):
            raise ValueError("All images must have the same shape for OME-Zarr export.")
        return frame_pos, wavelength_pos, plane

    report_progress(force=True)
    worker_count = max(1, min(int(os.cpu_count() or 1), total_planes))
    plane_specs = [
        (int(frame_pos), int(wavelength_pos), int(frame_index), float(wavelength_nm))
        for frame_pos, frame_index in enumerate(frames)
        for wavelength_pos, wavelength_nm in enumerate(wavelengths)
    ]
    spec_iter = iter(plane_specs)
    pending: dict = {}
    prefetch_window = max(worker_count * 4, worker_count + 2)

    def write_plane(frame_pos: int, wavelength_pos: int, plane: np.ndarray) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("OME-Zarr export cancelled.")
        array[frame_pos, wavelength_pos] = plane
        advance_progress(tiles_per_plane)

    previous_blosc_threads = None
    if compression_enabled:
        try:
            previous_blosc_threads = int(_blosc.get_nthreads())
        except Exception:
            previous_blosc_threads = None
        try:
            _blosc.set_nthreads(max(1, int(os.cpu_count() or 1)))
        except Exception:
            previous_blosc_threads = None
    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            def submit_next() -> bool:
                try:
                    frame_pos, wavelength_pos, frame_index, wavelength_nm = next(spec_iter)
                except StopIteration:
                    return False
                future = executor.submit(load_plane, frame_pos, wavelength_pos, frame_index, wavelength_nm)
                pending[future] = (frame_pos, wavelength_pos)
                return True

            for _ in range(min(prefetch_window, len(plane_specs))):
                if not submit_next():
                    break

            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("OME-Zarr export cancelled.")
                done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future, None)
                    result = future.result()
                    if result[2] is None:
                        advance_progress(tiles_per_plane)
                    else:
                        write_plane(result[0], result[1], result[2])
                    submit_next()
    finally:
        if previous_blosc_threads is not None:
            try:
                _blosc.set_nthreads(previous_blosc_threads)
            except Exception:
                pass
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
        "compression": "zstd+bitshuffle" if compression_enabled else "none",
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
            return np.asarray(_tifffile_imread(path_str, maxworkers=max(2, os.cpu_count() or 1)))
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
