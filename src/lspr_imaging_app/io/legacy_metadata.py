"""Import camera/illumination setup and per-image timing from the old,
pre-`lspri_acq_app` manual acquisition workflow's two sidecar files:

- `measureing_times.csv` (filename typo preserved - matches the real files
  on disk) - one row per image: `Image;Measuring time [ms];Measuring time
  [s];Note pump plan`. `Measuring time [ms]` is elapsed milliseconds since
  the run started, not an absolute timestamp; `Note pump plan` is whatever
  free-text pump-plan comment was active at that image's capture time (it
  changes mid-cube, not just at cube boundaries - see `_build_comment_events`).
- `metaData.txt` - one-time run setup: user, absolute start date/time, camera
  block (binning/averaging/image size/sensor offset), and a per-wavelength
  `defined_wl:real_wl:exposure_ms` table plus `FilterSetTime` (the LCTF
  settle delay applied before every capture).

Both get folded into the same `lspr_core.ImagingAcquisitionMetadata` shape
`lspr_io.read_imaging_acquisition_metadata` produces for a native v6.4 file,
so `Dataset` only ever deals with one representation regardless of source
(see `find_and_import_legacy_metadata`, called from the dataset controller).
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from lspr_core import (
    SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV,
    ImagingAcquisitionMetadata,
    ImagingCommentEvent,
    ImagingCubeTiming,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)

from lspr_imaging_app.io.image_naming import IMAGE_PATTERN

MEASURING_TIMES_CSV_NAME = "measureing_times.csv"
META_DATA_TXT_NAME = "metaData.txt"
_MAX_LEVELS_UP = 3

_KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<value>.+)$")
_WAVELENGTH_ROW_RE = re.compile(
    r"^(?P<defined_wl>[\d.]+)\s*:\s*(?P<real_wl>[\d.]+)\s*:\s*(?P<exposure_ms>[\d.]+)\s*$"
)
_DATE_RE = re.compile(r"^Date and time:\s*(?P<value>.+)$", re.IGNORECASE)
_USER_RE = re.compile(r"^User:\s*(?P<value>.+)$", re.IGNORECASE)


def find_legacy_metadata_files(dataset_folder: Path) -> tuple[Path, Path] | None:
    """Search *dataset_folder* and a few ancestor directories for the legacy
    sidecar pair. Real datasets put `images/` (TIFF stack) or `zarr/` (OME-Zarr)
    one level below the folder these files actually live in, and an OME-Zarr
    dataset's own folder is itself one level below that (`zarr/images.ome.zarr`)
    - so the files can be 1-2 levels above whatever folder `Dataset` was
    pointed at, not necessarily right beside it. Returns None if not found
    within `_MAX_LEVELS_UP` levels.
    """
    candidate = dataset_folder
    for _ in range(_MAX_LEVELS_UP + 1):
        csv_path = _find_case_insensitive(candidate, MEASURING_TIMES_CSV_NAME)
        txt_path = _find_case_insensitive(candidate, META_DATA_TXT_NAME)
        if csv_path is not None and txt_path is not None:
            return csv_path, txt_path
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def _find_case_insensitive(folder: Path, name: str) -> Path | None:
    if not folder.is_dir():
        return None
    direct = folder / name
    if direct.exists():
        return direct
    lowered = name.lower()
    for entry in folder.iterdir():
        if entry.is_file() and entry.name.lower() == lowered:
            return entry
    return None


def _parse_legacy_start_datetime(text: str) -> datetime | None:
    """Parse metaData.txt's `Date and time: DD.MM.YYYY HH:MM:SS`. The source
    has no timezone marker - treated as UTC rather than the importing
    machine's local zone, so re-running the import elsewhere is reproducible.
    This is a best-effort interpretation, not a verified UTC timestamp."""
    try:
        naive = datetime.strptime(text.strip(), "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc)


def parse_meta_data_txt(path: Path) -> dict:
    """Parse metaData.txt into a flat dict: `user`, `started_at_utc` (ISO
    string or None if unparseable), `camera` (dict of raw KEY=VALUE camera
    params), `filter_set_time_ms`, and `wavelength_table` (list of
    (defined_wl, real_wl, exposure_ms) tuples, in file order).
    """
    result: dict = {
        "user": "",
        "started_at_utc": None,
        "camera": {},
        "filter_set_time_ms": None,
        "wavelength_table": [],
    }
    section = "header"
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == "eof":
            break
        if line == "Camera":
            section = "camera"
            continue
        if line == "Measurement":
            section = "measurement"
            continue
        if line == "Wavelength and exposure time camera":
            section = "wavelength_header"
            continue
        if section == "wavelength_header":
            # The "defined wl:real wl:exposure time" column-header line.
            section = "wavelength_rows"
            continue

        if section == "header":
            user_match = _USER_RE.match(line)
            if user_match:
                result["user"] = user_match.group("value").strip()
                continue
            date_match = _DATE_RE.match(line)
            if date_match:
                started = _parse_legacy_start_datetime(date_match.group("value"))
                result["started_at_utc"] = started.isoformat().replace("+00:00", "Z") if started else None
                continue
        elif section == "camera":
            kv_match = _KEY_VALUE_RE.match(line)
            if kv_match:
                result["camera"][kv_match.group("key")] = kv_match.group("value").strip()
        elif section == "measurement":
            kv_match = _KEY_VALUE_RE.match(line)
            if kv_match and kv_match.group("key") == "FilterSetTime":
                try:
                    result["filter_set_time_ms"] = float(kv_match.group("value"))
                except ValueError:
                    pass
        elif section == "wavelength_rows":
            row_match = _WAVELENGTH_ROW_RE.match(line)
            if row_match:
                result["wavelength_table"].append(
                    (
                        float(row_match.group("defined_wl")),
                        float(row_match.group("real_wl")),
                        float(row_match.group("exposure_ms")),
                    )
                )
    return result


def parse_measuring_times_csv(path: Path) -> list[dict]:
    """Parse measureing_times.csv into a list of row dicts:
    `image_name`, `elapsed_ms` (int), `comment` (str). Rows whose `Image`
    column doesn't match the app's own `IMAGE_PATTERN` (wavelength/cube-index
    naming) are skipped rather than raising - a handful of unparseable rows
    in an old manual export shouldn't block the whole import.
    """
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for raw_row in reader:
            image_name = (raw_row.get("Image") or "").strip()
            elapsed_text = (raw_row.get("Measuring time [ms]") or "").strip()
            if not image_name or not elapsed_text:
                continue
            try:
                elapsed_ms = int(float(elapsed_text))
            except ValueError:
                continue
            rows.append(
                {
                    "image_name": image_name,
                    "elapsed_ms": elapsed_ms,
                    "comment": (raw_row.get("Note pump plan") or "").strip(),
                }
            )
    return rows


def _build_comment_events(rows: list[dict], start_unix_ms: int) -> list[ImagingCommentEvent]:
    """Collapse the CSV's per-row comment (repeated across every image
    captured while a pump-plan step was active) into a sparse
    transition-only log - only an entry where the comment actually changed
    from the previous row, matching singleLSPR acquisition's own event-log
    convention instead of storing the same string hundreds of times over."""
    events: list[ImagingCommentEvent] = []
    last_comment: object = object()  # sentinel that can't equal any real comment
    for row in rows:
        if row["comment"] != last_comment:
            events.append(
                ImagingCommentEvent(
                    acquired_at_unix_ms=start_unix_ms + row["elapsed_ms"],
                    comment=row["comment"],
                )
            )
            last_comment = row["comment"]
    return events


def _build_camera_settings(meta: dict, wavelengths_nm: list[float]) -> dict[float, WavelengthCameraSettings]:
    camera = meta["camera"]
    try:
        binning_h = int(float(camera.get("BinningH", 1)))
    except ValueError:
        binning_h = 1
    try:
        width_px = int(float(camera["ImageWidth"])) if "ImageWidth" in camera else None
    except ValueError:
        width_px = None
    try:
        height_px = int(float(camera["ImageHeight"])) if "ImageHeight" in camera else None
    except ValueError:
        height_px = None
    try:
        x_offset_px = int(float(camera["XoffsetImage"])) if "XoffsetImage" in camera else None
    except ValueError:
        x_offset_px = None
    try:
        y_offset_px = int(float(camera["YoffsetImage"])) if "YoffsetImage" in camera else None
    except ValueError:
        y_offset_px = None
    try:
        exposure_multiplier = float(camera.get("MultiplExpTime", 1.0))
    except ValueError:
        exposure_multiplier = 1.0

    exposure_ms_by_wavelength = {real_wl: exposure_ms for _defined_wl, real_wl, exposure_ms in meta["wavelength_table"]}

    settings: dict[float, WavelengthCameraSettings] = {}
    for wavelength_nm in wavelengths_nm:
        exposure_ms = exposure_ms_by_wavelength.get(wavelength_nm)
        exposure_us = exposure_ms * exposure_multiplier * 1000.0 if exposure_ms is not None else 0.0
        settings[wavelength_nm] = WavelengthCameraSettings(
            exposure_us=exposure_us,
            binning=binning_h,
            resolution_width_px=width_px,
            resolution_height_px=height_px,
            crop_x_px=x_offset_px,
            crop_y_px=y_offset_px,
            saving_mode="all_frames",
        )
    return settings


def _build_illumination_settings(meta: dict, wavelengths_nm: list[float]) -> dict[float, WavelengthIlluminationSettings]:
    settle_time_ms = meta.get("filter_set_time_ms")
    return {
        wavelength_nm: WavelengthIlluminationSettings(settle_time_ms=settle_time_ms, spectrum_source="default_file")
        for wavelength_nm in wavelengths_nm
    }


_EMPTY_META: dict = {"user": "", "started_at_utc": None, "camera": {}, "filter_set_time_ms": None, "wavelength_table": []}


def import_legacy_imaging_metadata(csv_path: Path | None, txt_path: Path | None) -> ImagingAcquisitionMetadata:
    """Parse the legacy sidecar file(s) into the shared
    `ImagingAcquisitionMetadata` shape. Either argument may be None for a
    partial import (e.g. a user explicitly imports just `metaData.txt` for
    its camera/illumination values, without a matching measuring-times
    file) - at least one is required.

    Without `metaData.txt`'s start date/time there is no absolute (real
    calendar) anchor for the CSV's elapsed-ms column - but every consumer of
    `acquired_at_unix_ms` (the sensorgram's elapsed-time axis, comment_at's
    ordering) only ever needs *differences* between timings, not a real Unix
    time; `analysis_controller._sensorgram_time_anchor_ms` already falls back
    to the earliest recorded timing whenever `started_at_utc` is absent, for
    exactly this case. So image_timings/comment_events are still built here,
    anchored at a synthetic epoch (0) rather than left empty - `started_at_utc`
    staying None is what tells a caller these are elapsed-since-start offsets,
    not real timestamps, so it can display them as such (see
    MainWindow._update_metadata_status_label, MetadataEditDialog._format_time)
    instead of a misleading fake calendar date.
    """
    if csv_path is None and txt_path is None:
        raise ValueError("import_legacy_imaging_metadata needs at least one of csv_path or txt_path.")

    meta = parse_meta_data_txt(txt_path) if txt_path is not None else _EMPTY_META
    start_unix_ms: int | None = None
    if meta["started_at_utc"] is not None:
        start_dt = datetime.fromisoformat(meta["started_at_utc"].replace("Z", "+00:00"))
        start_unix_ms = int(start_dt.timestamp() * 1000)
    timing_anchor_ms = start_unix_ms if start_unix_ms is not None else 0

    rows = parse_measuring_times_csv(csv_path) if csv_path is not None else []
    image_timings: list[ImagingCubeTiming] = []
    wavelengths_seen: set[float] = {real_wl for _defined_wl, real_wl, _exposure_ms in meta["wavelength_table"]}
    for row in rows:
        match = IMAGE_PATTERN.search(row["image_name"])
        if not match:
            continue
        wavelength_nm = float(match.group("wl"))
        spectral_cube_index = int(match.group("spectral_cube_index"))
        wavelengths_seen.add(wavelength_nm)
        image_timings.append(
            ImagingCubeTiming(
                spectral_cube_index=spectral_cube_index,
                wavelength_nm=wavelength_nm,
                acquired_at_unix_ms=timing_anchor_ms + row["elapsed_ms"],
            )
        )

    wavelengths_nm = sorted(wavelengths_seen)
    return ImagingAcquisitionMetadata(
        source_format=SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV,
        started_at_utc=meta["started_at_utc"],
        operator=meta["user"] or None,
        wavelengths_nm=wavelengths_nm,
        camera_settings_by_wavelength=_build_camera_settings(meta, wavelengths_nm) if txt_path is not None else {},
        illumination_settings_by_wavelength=(
            _build_illumination_settings(meta, wavelengths_nm) if txt_path is not None else {}
        ),
        image_timings=image_timings,
        comment_events=_build_comment_events(rows, timing_anchor_ms) if rows else [],
    )


def find_and_import_legacy_metadata(dataset_folder: Path) -> ImagingAcquisitionMetadata | None:
    """Convenience entry point for the dataset controller: locate the legacy
    sidecar pair near *dataset_folder* and import it, or return None if
    they're not present (not every dataset has them - this is not an error).
    """
    found = find_legacy_metadata_files(dataset_folder)
    if found is None:
        return None
    csv_path, txt_path = found
    return import_legacy_imaging_metadata(csv_path, txt_path)
