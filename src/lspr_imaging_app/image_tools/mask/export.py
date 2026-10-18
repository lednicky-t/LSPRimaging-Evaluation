"""Export every mask of the timeline as PNG files, one per mask, named per frame (2026-10-17, maintainer request: "an export of all
masks as png formats with appropriate namings"). Pure (no Qt); the thread and the folder dialog are the callers' (`mask_export_task.py`,
`panels/image/mask_file_actions.py`).

The files are the same black-and-white images the single "Save mask" writes (`mask/io.write_mask_image`: 255 = masked, 0 = kept), in
the frame's own raw pixel space. A name says what the mask is::

    mask_cube061_wl620_individual.png   applies to that frame only
    mask_cube005_wl500_persistent.png   drawn at that frame; applies to its whole cube and to every cube after it, until the next
                                        persistent mask

`masks_index.csv` in the same folder lists every file with its cube, wavelength, scope, masked pixel count and what it applies to. The
number formats (digits of the cube number, decimals of the wavelength) are the dataset's own `FrameNamingScheme`, the same the session
folder uses.
"""

from __future__ import annotations

import csv
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ...analysis.provenance import FrameNamingScheme
from ...progress import ProgressCallback, StageProgress
from .io import write_mask_image
from .model import MaskChange

INDEX_FILE_NAME = "masks_index.csv"


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    written: int
    files: tuple[str, ...]
    seconds: float


def mask_file_name(change: MaskChange, naming: FrameNamingScheme) -> str:
    """The PNG name of one mask: ``mask_cube061_wl620_individual.png``."""
    return f"mask_{naming.frame_tag(change.frame[0], change.frame[1])}_{change.scope}.png"


def planned_files(changes: Sequence[MaskChange], naming: FrameNamingScheme) -> list[str]:
    """The names an export of `changes` would write (without the index), in order."""
    return [mask_file_name(change, naming) for change in changes]


def existing_clashes(folder: Path, names: Sequence[str]) -> list[str]:
    """Those of `names` that already exist in `folder`, in the order given. One directory listing, not one `stat` per file: on a
    network or cloud-synced folder with hundreds of files the second form can stall the window (the caller runs on the GUI
    thread). A folder that does not exist yet holds nothing. Names are compared as the file system does (`normcase`)."""
    try:
        with os.scandir(folder) as entries:
            present = {os.path.normcase(entry.name) for entry in entries}
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [name for name in names if os.path.normcase(name) in present]


def export_masks(
    changes: Sequence[MaskChange],
    folder: Path,
    naming: FrameNamingScheme,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExportResult:
    """Write one PNG per change into `folder` (created if needed) and the index file. Existing files of the same names are replaced.
    Raises `progress.Cancelled` between two files when `cancelled()` turns true (the files written so far stay)."""
    started = time.perf_counter()
    report = StageProgress({"write": 1.0}, progress, cancelled)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    rows: list[list[object]] = []
    total = max(len(changes), 1)
    for index, change in enumerate(changes):
        name = mask_file_name(change, naming)
        report("write", index / total, f"{name} ({index + 1}/{len(changes)})")
        plane = change.plane_uncached()  # hundreds of masks: not through the shared decode cache the Image panel uses
        write_mask_image(plane, folder / name)
        names.append(name)
        applies = (
            "this frame only"
            if change.scope == "individual"
            else f"cube {change.frame[0]} and every cube after it, until the next persistent mask"
        )
        rows.append([name, change.frame[0], f"{change.frame[1]:g}", change.scope, int(plane.sum()), applies])
    with open(folder / INDEX_FILE_NAME, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "cube", "wavelength_nm", "scope", "masked_pixels", "applies_to"])
        writer.writerows(rows)
    report("write", 1.0, "done")
    return ExportResult(folder=folder, written=len(names), files=tuple(names), seconds=time.perf_counter() - started)
