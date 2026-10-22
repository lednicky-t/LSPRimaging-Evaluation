"""What an OME-Zarr export did, in numbers a person can read (2026-10-22). Pure: no Qt, no files.

`export_ome_zarr_dataset` fills an `ExportStats` as it goes (`planes`, `bytes_written`, `stored_plane_bytes`, `workers`); whoever
times the whole run (`DatasetModule.export_to_ome_zarr`) sets `seconds`. The status bar and the "open the exported dataset?" box
show `describe()` / `headline()`.

**"GB/min of image data" means uncompressed pixels**: planes x one stored plane's bytes, per minute of wall time, so it says how
fast the pixels are processed whatever the compression turns out to be. What reached the disk is the separate "wrote" figure.
Decimal units (1 GB = 10^9 bytes), as disk makers and file managers count.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExportStats:
    planes: int = 0
    """Images written (a plane whose source file was missing or skipped is not counted)."""
    bytes_written: int = 0
    """On disk: the shard files, compressed, with their index."""
    stored_plane_bytes: int = 0
    """One stored image uncompressed: height x width x bytes per pixel."""
    workers: int = 0
    """Worker processes the export settled on (the tuning may have changed the number during the run)."""
    worker_mode: str = ""
    """How that number was chosen: ``auto`` (tuned while running), ``manual`` (the user's count in Preferences) or ``fixed``
    (a job too short to tune). Empty: not recorded."""
    seconds: float = 0.0
    """Wall time of the whole export, from start to the last file."""

    @property
    def uncompressed_bytes(self) -> int:
        return int(self.planes) * int(self.stored_plane_bytes)

    @property
    def planes_per_second(self) -> float:
        return self.planes / self.seconds if self.seconds > 0 else 0.0

    @property
    def gb_per_minute(self) -> float:
        """Uncompressed image data processed per minute, in GB."""
        return self.uncompressed_bytes / 1e9 / (self.seconds / 60.0) if self.seconds > 0 else 0.0

    @property
    def size_ratio(self) -> float:
        """What reached the disk as a share of the uncompressed size (0.49 = half)."""
        return self.bytes_written / self.uncompressed_bytes if self.uncompressed_bytes > 0 else 0.0

    def headline(self) -> str:
        """One short line, for the status bar: "4 min 52 s, 29 images/s"."""
        text = format_duration(self.seconds)
        if self.planes and self.seconds > 0:
            text += f", {self.planes_per_second:.0f} images/s" if self.planes_per_second >= 10 else f", {self.planes_per_second:.1f} images/s"
        return text

    def describe(self) -> str:
        """Two lines for the "open the exported dataset?" box."""
        first = f"Export took {format_duration(self.seconds)}"
        if self.planes and self.seconds > 0:
            rate = self.planes_per_second
            first += (
                f": {self.planes:,} images at {rate:.0f} images/s" if rate >= 10 else f": {self.planes:,} images at {rate:.1f} images/s"
            ) + f" ({format_bytes(self.gb_per_minute * 1e9)}/min of image data)."
        else:
            first += "."
        if self.workers > 0 and self.worker_mode:
            first += f" Used {self.workers} worker{'s' if self.workers != 1 else ''} ({self.worker_mode})."
        if self.bytes_written > 0 and self.uncompressed_bytes > 0:
            return (
                f"{first}\nWrote {format_bytes(self.bytes_written)} to disk, {100 * self.size_ratio:.0f} % of the "
                f"{format_bytes(self.uncompressed_bytes)} uncompressed."
            )
        return first


def format_duration(seconds: float) -> str:
    """"48 s", "4 min 52 s", "1 h 05 min": whole seconds below a minute, whole minutes from an hour."""
    total = max(int(round(float(seconds))), 0)
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def format_bytes(count: float) -> str:
    """"812 MB", "11.2 GB": decimal; one decimal place from 1 GB up, whole megabytes below."""
    count = float(count)
    if count >= 1e9:
        return f"{count / 1e9:.1f} GB"
    return f"{count / 1e6:.0f} MB"
