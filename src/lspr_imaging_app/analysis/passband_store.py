"""Reading a spectral sweep from the illumination probe, and the session file that keeps it (2026-11-20). No Qt.

**The import** reads what ``spikes/lspri_acq_phase0/illumination_probe.py`` writes, tab 4 ("spectral sweep"), as it is::

    requested_nm,confirmed_nm,settle_ms,temperature_c,<wavelength of pixel 0>,<pixel 1>, ...      (one header line)
    DARK,,,,<lamp-off counts per pixel>                                                            (optional: the dark spectrum)
    420.000,420.000,25.20,30.81,<counts per pixel>                                                 (one line per filter setting)

A dark spectrum can also come as a second file (``dark_spectrum_*.csv``: ``wavelength_nm,intensity``). One sweep per import: a temperature series is not
supported yet (``docs/spectral_correction_2026-11-20.md``). The rows are kept exactly as read; nothing is processed here (``spectral_passband.py`` derives).

**The session file** ``spectral_correction.h5`` (schema ``lspri_spectral_correction`` 1.0), written once at import, next to the session's other files::

    /axes/pixel_nm        float64 [N]      the spectrometer's wavelength axis
    /axes/set_nm          float64 [K]      what the filter was told, one per row
    /passbands/counts     float64 [K, N]   the rows as imported (gzip)
    /passbands/confirmed_nm, settle_ms, temperature_c   float64 [K]   as imported (NaN = not given)
    /passbands/dark       float64 [N]      only when the sweep came with a dark spectrum
    attrs: schema_name, schema_version, app_name, app_version, created_at_utc (the hdf5 rules), source_name, sha256 (of the imported file(s)),
           illumination (free text), imported_at (ISO, local time)

Reserved, not written yet: ``/passbands/detector_response`` ``float64 [N]``, the relative sensitivity of the camera on the same axis (the maths takes the camera as
flat until it exists; ``spectral_passband.py``). The file is created under a temporary name and renamed, so a half-written file is never taken for a table.
A table that already is there is kept as ``spectral_correction.previous.h5`` (one backup slot, as ``scan.previous.h5``): nothing is deleted without a copy.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from ..version import APP_NAME, APP_VERSION
from .spectral_passband import PassbandError, PassbandTable, file_digest

SCHEMA_NAME = "lspri_spectral_correction"
SCHEMA_VERSION = "1.0"
FILE_NAME = "spectral_correction.h5"
BACKUP_NAME = "spectral_correction.previous.h5"


def _number(text: str) -> float:
    text = text.strip()
    return float("nan") if text == "" else float(text.replace(",", "."))


def parse_sweep(sweep: bytes, name: str, dark: bytes | None = None, illumination: str = "") -> PassbandTable:
    """A table from the bytes of a probe sweep file (and, optionally, of a separate dark-spectrum file). Raises ``PassbandError`` with a sentence for the user,
    naming the line, for anything it cannot read."""
    reader = csv.reader(io.StringIO(sweep.decode("utf-8-sig")))
    try:
        header = next(reader)
    except StopIteration:
        raise PassbandError("The file is empty.") from None
    first = header[0].strip().lower() if header else ""
    if first == "wavelength_nm":
        raise PassbandError("This is a dark-spectrum file. Choose the spectral sweep file (its header starts with requested_nm); the dark spectrum can be chosen with it.")
    if first != "requested_nm" or len(header) < 8:
        raise PassbandError("This is not a spectral sweep of the illumination probe: the header should start with requested_nm, confirmed_nm, settle_ms, temperature_c.")
    try:
        pixel_nm = np.array([float(c) for c in header[4:]], dtype=np.float64)
    except ValueError:
        raise PassbandError("Line 1: a wavelength of the spectrometer axis is not a number.") from None
    n_pixel = pixel_nm.size
    sets: list[float] = []
    confirmed: list[float] = []
    settle: list[float] = []
    temperature: list[float] = []
    rows: list[np.ndarray] = []
    dark_row: np.ndarray | None = None
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != 4 + n_pixel:
            raise PassbandError(f"Line {line_no}: {len(cells)} values where {4 + n_pixel} are expected (4 + one per pixel).")
        try:
            values = np.array([float(c) for c in cells[4:]], dtype=np.float64)
            if cells[0].strip().upper() == "DARK":
                dark_row = values
                continue
            sets.append(_number(cells[0]))
            confirmed.append(_number(cells[1]))
            settle.append(_number(cells[2]))
            temperature.append(_number(cells[3]))
        except ValueError:
            raise PassbandError(f"Line {line_no}: a value is not a number.") from None
        rows.append(values)
    if not rows:
        raise PassbandError("The file has no spectra.")
    dark_values = dark_row
    if dark is not None:
        dark_values = _parse_dark(dark, pixel_nm)
    table = PassbandTable(
        pixel_nm=pixel_nm,
        set_nm=np.array(sets),
        counts=np.vstack(rows),
        confirmed_nm=np.array(confirmed),
        settle_ms=np.array(settle),
        temperature_c=np.array(temperature),
        dark=dark_values,
        source_name=name,
        sha256=file_digest(sweep, dark or b""),
        illumination=str(illumination).strip(),
        imported_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    table.validate()
    return table


def _parse_dark(data: bytes, pixel_nm: np.ndarray) -> np.ndarray:
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    header = next(reader, [])
    if not header or header[0].strip().lower() != "wavelength_nm" or len(header) < 2:
        raise PassbandError("The dark-spectrum file's header should be wavelength_nm,intensity.")
    wl: list[float] = []
    val: list[float] = []
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        try:
            wl.append(float(cells[0]))
            val.append(float(cells[1]))
        except (ValueError, IndexError):
            raise PassbandError(f"Dark-spectrum file, line {line_no}: not a wavelength and a count.") from None
    if len(wl) != pixel_nm.size or not np.allclose(wl, pixel_nm, atol=1e-3):
        raise PassbandError("The dark spectrum is not on the sweep's spectrometer axis (a different spectrometer or pixel count).")
    return np.array(val, dtype=np.float64)


def read_sweep(path: str | Path, dark_path: str | Path | None = None, illumination: str = "") -> PassbandTable:
    """``parse_sweep`` for files on disk. The table's ``source_name`` is the sweep file's name."""
    path = Path(path)
    dark = Path(dark_path).read_bytes() if dark_path else None
    return parse_sweep(path.read_bytes(), path.name, dark, illumination)


def write_table(path: str | Path, table: PassbandTable) -> None:
    """Write *table* to *path* (the session's ``spectral_correction.h5``), under a temporary name first. A file already at *path* is moved to ``spectral_correction.previous.h5``
    next to it (replacing an older backup) before the new one takes its place."""
    table.validate()
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with h5py.File(tmp, "w") as f:
        f.attrs["schema_name"] = SCHEMA_NAME
        f.attrs["schema_version"] = SCHEMA_VERSION
        f.attrs["app_name"] = APP_NAME
        f.attrs["app_version"] = APP_VERSION
        f.attrs["created_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        f.attrs["source_name"] = table.source_name
        f.attrs["sha256"] = table.sha256
        f.attrs["illumination"] = table.illumination
        f.attrs["imported_at"] = table.imported_at
        axes = f.create_group("axes")
        axes.create_dataset("pixel_nm", data=np.asarray(table.pixel_nm, dtype=np.float64))
        axes.create_dataset("set_nm", data=np.asarray(table.set_nm, dtype=np.float64))
        group = f.create_group("passbands")
        group.create_dataset("counts", data=np.asarray(table.counts, dtype=np.float64), compression="gzip", compression_opts=4, shuffle=True)
        for key in ("confirmed_nm", "settle_ms", "temperature_c"):
            group.create_dataset(key, data=np.asarray(getattr(table, key), dtype=np.float64))
        if table.dark is not None:
            group.create_dataset("dark", data=np.asarray(table.dark, dtype=np.float64))
    if path.exists():
        os.replace(path, path.with_name(BACKUP_NAME))
    os.replace(tmp, path)


def read_table(path: str | Path) -> PassbandTable:
    """The table in a ``spectral_correction.h5``. Raises ``PassbandError`` for a file that is not one, or is of a newer major version."""
    try:
        with h5py.File(path, "r") as f:
            if f.attrs.get("schema_name") != SCHEMA_NAME:
                raise PassbandError("This file is not a spectral-correction table.")
            major = str(f.attrs.get("schema_version", "0")).split(".")[0]
            if major != SCHEMA_VERSION.split(".")[0]:
                raise PassbandError(f"The table is of format {f.attrs.get('schema_version')}; this version of the app reads {SCHEMA_VERSION}.")
            group = f["passbands"]
            table = PassbandTable(
                pixel_nm=f["axes/pixel_nm"][()],
                set_nm=f["axes/set_nm"][()],
                counts=group["counts"][()],
                confirmed_nm=group["confirmed_nm"][()],
                settle_ms=group["settle_ms"][()],
                temperature_c=group["temperature_c"][()],
                dark=group["dark"][()] if "dark" in group else None,
                source_name=str(f.attrs.get("source_name", "")),
                sha256=str(f.attrs.get("sha256", "")),
                illumination=str(f.attrs.get("illumination", "")),
                imported_at=str(f.attrs.get("imported_at", "")),
            )
    except OSError as exc:
        raise PassbandError(f"The table file cannot be read: {exc}") from exc
    except KeyError as exc:
        raise PassbandError(f"The table file is incomplete (missing {exc}).") from exc
    table.validate()
    return table
