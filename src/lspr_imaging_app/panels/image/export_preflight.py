"""The layout check before an export starts (2026-11-20): will it fit on the destination drive, and is its file layout slow there?

On the maintainer's USB exFAT drive (2 MiB allocation unit) the "one file per image" layout wrote 3.9 images/s and needed 560 GiB
on disk for 162 GB of data, against 28 images/s and 171 GiB for "one file per cube" (`docs/export_slow_on_exfat_2026-11-20.md`).
`assess` works that out from the destination (`dataset/export_disk_estimate.py`); `ask` is the only dialog, shown only when
there is something to say: the export would not fit, or its layout is slow on this drive. Nothing is changed silently: the Cube
layout is a button the user presses, and the Shard control then says Cube.

Describe-first record (gui rules): purpose = warn before a long export and offer the better layout; actions = Use Cube layout /
Export as set (Export anyway) / Cancel; layout = one message box (heading, numbers, question); states = fits and fast (no dialog),
does not fit, slow, both; validation = advisory, the user can always export as set; errors = a drive that cannot be asked is logged
and the export goes on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget

from ...dataset import export_disk_estimate as de
from ...export_stats import format_bytes


def _binary_size(count: int) -> str:
    """"2 MiB", "4 KiB", "1 KiB": how allocation units are set and shown by the formatting tools (the sizes of the data use `format_bytes`, decimal)."""
    return f"{count / 2**20:g} MiB" if count >= 2**20 else f"{count / 1024:g} KiB"


@dataclass(frozen=True)
class Preflight:
    where: str
    shard_mode: str
    compressed: bool
    drive: de.DriveInfo
    current: de.DiskEstimate
    cube: de.DiskEstimate
    current_fits: bool
    cube_fits: bool
    slow: bool

    @property
    def recommend_cube(self) -> bool:
        """The Cube layout is offered: it is not the layout already chosen, it fits, and the chosen one does not fit or is slow."""
        return self.shard_mode != de.PER_CUBE and self.cube_fits and (not self.current_fits or self.slow)

    @property
    def needs_dialog(self) -> bool:
        return (not self.current_fits) or self.slow

    def heading(self) -> str:
        if not self.current_fits and self.slow:
            return "This export will not fit on the drive, and its file layout is slow there."
        if not self.current_fits:
            return "This export will not fit on the drive."
        return "This file layout is slow on this drive."

    def details(self) -> str:
        drive = self.drive
        unit = _binary_size(drive.cluster_bytes)
        named = f"{drive.file_system}, " if drive.file_system else ""
        as_set = "one file per cube" if self.shard_mode == de.PER_CUBE else "one file per image"
        lines = [
            f"Drive {self.where}: {named}{unit} allocation unit, {format_bytes(drive.free_bytes)} free.",
            f"As set ({as_set}{', compressed' if self.compressed else ''}): {self.current.files:,} files and {self.current.folders:,} "
            f"folders, about {format_bytes(self.current.on_disk_bytes)} on disk for {format_bytes(self.current.data_bytes)} of data.",
        ]
        if self.shard_mode != de.PER_CUBE:
            lines.append(
                f"One file per cube: {self.cube.files:,} files and {self.cube.folders:,} folders, about "
                f"{format_bytes(self.cube.on_disk_bytes)} on disk" + ("." if self.cube_fits else " - it does not fit either.")
            )
        if self.slow:
            lines.append(
                f"Every folder takes {_binary_size(drive.folder_bytes)} here and is slow to create; the layout as set makes "
                f"{self.current.folders:,} of them."
            )
        if self.compressed:
            lines.append(f"The compressed size is an estimate (about {100 * de.COMPRESSED_FRACTION:.0f} % of the raw size; it depends on the data).")
        return "\n\n".join(lines)


def assess(
    destination_parent: Path,
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
) -> Preflight | None:
    """The check for an export into ``destination_parent``, or None when the drive could not be asked (logged by `drive_info`)."""
    drive = de.drive_info(destination_parent)
    if drive is None:
        return None
    common = dict(
        n_cubes=n_cubes, n_wavelengths=n_wavelengths, height=height, width=width, itemsize=itemsize,
        chunk_h=chunk_h, chunk_w=chunk_w, compressed=compressed, drive=drive,
    )
    current = de.estimate_export_disk_use(shard_mode=shard_mode, **common)
    cube = current if shard_mode == de.PER_CUBE else de.estimate_export_disk_use(shard_mode=de.PER_CUBE, **common)
    return Preflight(
        where=Path(destination_parent).anchor or str(destination_parent), shard_mode=shard_mode, compressed=compressed, drive=drive,
        current=current, cube=cube, current_fits=de.fits(current, drive), cube_fits=de.fits(cube, drive), slow=de.layout_is_slow(current),
    )


def ask(parent: QWidget, pre: Preflight) -> str | None:
    """Show the check. Returns the shard mode to export with (the one already set, or ``per_spectral_cube``), or None for Cancel."""
    box = QMessageBox(QMessageBox.Icon.Warning, "Export layout", pre.heading(), parent=parent)
    box.setInformativeText(pre.details())
    cube_button = box.addButton("Use Cube layout", QMessageBox.ButtonRole.AcceptRole) if pre.recommend_cube else None
    keep_button = box.addButton("Export as set" if pre.current_fits else "Export anyway", QMessageBox.ButtonRole.ActionRole)
    cancel_button = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cube_button or cancel_button)  # never the choice that may fill the drive
    box.exec()
    clicked = box.clickedButton()
    box.deleteLater()
    if cube_button is not None and clicked is cube_button:
        return de.PER_CUBE
    if clicked is keep_button:
        return pre.shard_mode
    return None
