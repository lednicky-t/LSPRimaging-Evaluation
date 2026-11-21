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
    read_s: float = 0.0
    encode_s: float = 0.0
    folder_s: float = 0.0
    write_s: float = 0.0
    """Where the workers' time went (2026-11-20), each summed over every worker and every shard so far: reading + decoding + rotating the
    source, encoding the chunks, making the shard's folders, writing the file. Filled as results arrive, so a cancelled export has
    them too. Worker seconds, not wall seconds: with 7 workers they add up to about 7 x the wall time."""

    def add_stage_seconds(self, read_s: float, encode_s: float, folder_s: float, write_s: float) -> None:
        self.read_s += float(read_s)
        self.encode_s += float(encode_s)
        self.folder_s += float(folder_s)
        self.write_s += float(write_s)

    def stage_ms_per_image(self) -> dict[str, float]:
        """Mean worker milliseconds per image for each stage; empty before anything was written."""
        if self.planes <= 0 or (self.read_s + self.encode_s + self.folder_s + self.write_s) <= 0:
            return {}
        scale = 1000.0 / self.planes
        return {"read_ms": self.read_s * scale, "encode_ms": self.encode_s * scale, "folders_ms": self.folder_s * scale, "write_ms": self.write_s * scale}

    def stage_line(self) -> str:
        """"Worker time per image: reading + rotating 352 ms, encoding 2 ms, making folders 1,516 ms, writing 49 ms." ("" when unknown)."""
        stages = self.stage_ms_per_image()
        if not stages:
            return ""
        return (
            f"Worker time per image: reading + rotating {stages['read_ms']:,.0f} ms, encoding {stages['encode_ms']:,.0f} ms, "
            f"making folders {stages['folders_ms']:,.0f} ms, writing {stages['write_ms']:,.0f} ms."
        )

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
        """Two lines for the "open the exported dataset?" box, and a third with the stage times when they are known."""
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
            first = (
                f"{first}\nWrote {format_bytes(self.bytes_written)} to disk, {100 * self.size_ratio:.0f} % of the "
                f"{format_bytes(self.uncompressed_bytes)} uncompressed."
            )
        stage_line = self.stage_line()
        return f"{first}\n{stage_line}" if stage_line else first


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
