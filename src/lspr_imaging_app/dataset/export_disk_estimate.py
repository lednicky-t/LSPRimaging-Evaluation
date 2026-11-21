"""What an OME-Zarr export takes on a given drive, counting the drive's allocation unit (2026-11-20). Pure: no Qt, no GUI.

Why it exists: on the maintainer's data drive (a 6 TB USB SSD, exFAT, **2 MiB allocation unit**) every folder costs 2 MiB and about
1.5 s to create, and every file takes a whole number of 2 MiB units. The "one file per image" layout makes two folders and one
file per image, so 71,685 images would have needed 560 GiB on disk (420 GiB with lz4) for 162 GB of data, and ran at 3.9 images/s
against 28 for "one file per cube" (`docs/export_slow_on_exfat_2026-11-20.md`). This module answers, before an export starts,
"how much disk will this really take here, and is the layout slow on this drive?".

Counts follow `dataset/io.py`'s writer: per image the shard is `c/<cube>/<wavelength>/0/0` (folders: the root, `images`, `c`, one per
cube, two per image); per cube it is `c/<cube>/0/0/0` (three per cube). Small metadata files (a few zarr.json, the valid mask) are not counted.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

PER_IMAGE = "per_image"
PER_CUBE = "per_spectral_cube"

COMPRESSED_FRACTION = 0.55
"""Size of lz4 + bit-shuffle data as a share of the raw size. 0.54 was measured on the 71,685-image LED_V2 bulk data (2026-11-20);
other data may compress better or worse, so a compressed estimate is the *expected* size, not a bound."""

SLOW_FOLDER_SHARE = 0.5
"""A layout counts as slow on a drive when its folders alone take this share of the data written (`layout_is_slow`). The measured case
(2 MiB units, 4 MiB of folders per 2.2 MB image) is far above it; an NTFS drive (about 1 KiB per folder) is far below it.
Not measured in between (e.g. 128 KiB units, 12 %)."""

FREE_SPACE_MARGIN = 0.98
"""An export "fits" when its estimated size on disk is at most this share of the free space (the rest is slack for the estimate)."""

_NTFS_FOLDER_BYTES = 1024
"""An NTFS folder with few entries lives inside its file-table record: about 1 KiB, not a whole allocation unit."""


@dataclass(frozen=True)
class DriveInfo:
    cluster_bytes: int
    """The smallest piece of disk a file takes (the allocation unit)."""
    folder_bytes: int
    """What an almost empty folder takes: the allocation unit on exFAT / FAT32 and most others, ~1 KiB on NTFS."""
    free_bytes: int
    file_system: str
    """"NTFS", "exFAT", ...; empty when it could not be asked."""


@dataclass(frozen=True)
class DiskEstimate:
    files: int
    folders: int
    data_bytes: int
    """The bytes written into the files (index and checksum included)."""
    on_disk_bytes: int
    """What the files and folders take once rounded up to whole allocation units."""
    folder_bytes_total: int
    """The folders' share of `on_disk_bytes`."""


def drive_info(path: Path) -> DriveInfo | None:
    """The drive that holds ``path`` (or its nearest existing parent), or None when even the free space cannot be read. Never raises."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = int(shutil.disk_usage(probe).free)
    except OSError as exc:
        _LOGGER.warning("Could not read the free space at %s: %s", probe, exc)
        return None
    cluster, file_system = 4096, ""
    try:
        if sys.platform == "win32":
            found = _windows_cluster_and_file_system(probe)
            if found is not None:
                cluster, file_system = found
        else:
            stat = os.statvfs(probe)
            cluster = int(stat.f_frsize or stat.f_bsize or 4096)
    except (OSError, AttributeError, ValueError) as exc:
        _LOGGER.warning("Could not read the allocation unit at %s (assuming 4 KiB): %s", probe, exc)
    folder = _NTFS_FOLDER_BYTES if file_system.upper() == "NTFS" else cluster
    return DriveInfo(cluster_bytes=max(int(cluster), 512), folder_bytes=max(int(folder), 512), free_bytes=free, file_system=file_system)


def _windows_cluster_and_file_system(path: Path) -> tuple[int, str] | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    root_buffer = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetVolumePathNameW(str(path), root_buffer, len(root_buffer)):
        return None
    root = root_buffer.value
    sectors, sector_bytes, free_clusters, total_clusters = (wintypes.DWORD() for _ in range(4))
    if not kernel32.GetDiskFreeSpaceW(
        root, ctypes.byref(sectors), ctypes.byref(sector_bytes), ctypes.byref(free_clusters), ctypes.byref(total_clusters)
    ):
        return None
    name_buffer = ctypes.create_unicode_buffer(64)
    named = kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name_buffer, len(name_buffer))
    return int(sectors.value) * int(sector_bytes.value), (name_buffer.value if named else "")


def _round_up(size: int, unit: int) -> int:
    return -(-int(size) // int(unit)) * int(unit)


def estimate_export_disk_use(
    *,
    n_cubes: int,
    n_wavelengths: int,
    height: int,
    width: int,
    itemsize: int,
    chunk_h: int,
    chunk_w: int,
    shard_mode: str,
    compressed: bool,
    drive: DriveInfo,
) -> DiskEstimate:
    """The files, folders and size on disk of an export of ``n_cubes x n_wavelengths`` stored images of ``height x width`` pixels.
    Every cube x wavelength is a plane of the export even when its source file is missing or skipped (the writer still writes its
    shard), so the counts are an upper bound for `skip_excluded`."""
    n_cubes, n_wavelengths = max(int(n_cubes), 0), max(int(n_wavelengths), 0)
    chunk_h, chunk_w = max(int(chunk_h), 1), max(int(chunk_w), 1)
    padded_h, padded_w = _round_up(height, chunk_h), _round_up(width, chunk_w)
    chunks_per_plane = (padded_h // chunk_h) * (padded_w // chunk_w)
    pixel_bytes = padded_h * padded_w * int(itemsize)
    if compressed:
        pixel_bytes = int(round(pixel_bytes * COMPRESSED_FRACTION))
    plane_bytes = pixel_bytes + 16 * chunks_per_plane  # the shard's index: an offset and a length per chunk
    planes = n_cubes * n_wavelengths
    if shard_mode == PER_CUBE:
        files, folders = n_cubes, 3 + 3 * n_cubes
        file_bytes = n_wavelengths * plane_bytes + 4  # + the index's CRC32C
    else:
        files, folders = planes, 3 + n_cubes + 2 * planes
        file_bytes = plane_bytes + 4
    folder_total = folders * drive.folder_bytes
    on_disk = files * _round_up(file_bytes, drive.cluster_bytes) + folder_total
    return DiskEstimate(files=files, folders=folders, data_bytes=files * file_bytes, on_disk_bytes=on_disk, folder_bytes_total=folder_total)


def fits(estimate: DiskEstimate, drive: DriveInfo) -> bool:
    return estimate.on_disk_bytes <= drive.free_bytes * FREE_SPACE_MARGIN


def layout_is_slow(estimate: DiskEstimate) -> bool:
    """Whether making the folders of this layout is likely to dominate the export on this drive (see `SLOW_FOLDER_SHARE`)."""
    return estimate.data_bytes > 0 and estimate.folder_bytes_total >= SLOW_FOLDER_SHARE * estimate.data_bytes
