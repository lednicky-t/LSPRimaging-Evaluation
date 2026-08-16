"""One "Import metadata..." entry point that accepts any mix of recognized
acquisition-metadata files - a legacy `measureing_times.csv`, a legacy
`metaData.txt`, a native `lspr_measurement` v6.4 HDF5 file, or a previously
exported `acquisition_metadata.json` sidecar (see `storage/workspace.py`) -
and figures out from each file's actual content (not its name or extension)
which importer it belongs to. This is what lets the GUI's file dialog just
say "pick your metadata file(s)" instead of asking the user to know which
importer to use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from lspr_core import ImagingAcquisitionMetadata
from lspr_io import is_imaging_measurement_file, read_imaging_acquisition_metadata

from lspr_imaging_app.io.legacy_metadata import import_legacy_imaging_metadata
from lspr_imaging_app.storage.workspace import ACQUISITION_METADATA_SCHEMA_NAME, load_acquisition_metadata_sidecar

CLASSIFICATION_LEGACY_CSV = "legacy_csv"
CLASSIFICATION_LEGACY_TXT = "legacy_txt"
CLASSIFICATION_NATIVE_HDF5 = "native_hdf5"
CLASSIFICATION_SIDECAR_JSON = "sidecar_json"
CLASSIFICATION_UNKNOWN = "unknown"

_KIND_LABELS = {
    CLASSIFICATION_NATIVE_HDF5: "native measurement file",
    CLASSIFICATION_SIDECAR_JSON: "exported metadata file",
}


def classify_metadata_file(path: Path) -> str:
    """Sniff *path*'s actual content to decide which importer it belongs to.

    Extension is used only to pick which parser to try (HDF5 vs JSON vs
    text), never to decide "this is a metadata file" on its own - a renamed
    or oddly-extensioned file is still recognized as long as its content
    matches.
    """
    if not path.is_file():
        return CLASSIFICATION_UNKNOWN

    suffix = path.suffix.lower()
    if suffix in (".h5", ".hdf5"):
        return CLASSIFICATION_NATIVE_HDF5 if is_imaging_measurement_file(path) else CLASSIFICATION_UNKNOWN

    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return CLASSIFICATION_UNKNOWN
        if isinstance(payload, dict) and payload.get("schema_name") == ACQUISITION_METADATA_SCHEMA_NAME:
            return CLASSIFICATION_SIDECAR_JSON
        return CLASSIFICATION_UNKNOWN

    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return CLASSIFICATION_UNKNOWN
    first_line = head.splitlines()[0].lower() if head else ""
    if "measuring time" in first_line and "image" in first_line:
        return CLASSIFICATION_LEGACY_CSV
    if "Date and time:" in head and "Wavelength and exposure time camera" in head:
        return CLASSIFICATION_LEGACY_TXT
    return CLASSIFICATION_UNKNOWN


@dataclass(slots=True)
class MetadataImportResult:
    metadata: ImagingAcquisitionMetadata
    notes: list[str] = field(default_factory=list)


def import_metadata_files(paths: list[Path]) -> MetadataImportResult:
    """Classify every path in *paths* and import whichever are recognized
    into one `ImagingAcquisitionMetadata` - any mix of legacy CSV/TXT, a
    native HDF5 file, or a sidecar JSON export, in any combination. Returns
    the merged metadata plus a human-readable note per file (what was
    loaded, skipped, or ignored) for the caller to show the user.

    Raises ValueError if none of *paths* are recognized.
    """
    by_kind: dict[str, list[Path]] = {
        CLASSIFICATION_LEGACY_CSV: [],
        CLASSIFICATION_LEGACY_TXT: [],
        CLASSIFICATION_NATIVE_HDF5: [],
        CLASSIFICATION_SIDECAR_JSON: [],
        CLASSIFICATION_UNKNOWN: [],
    }
    for path in paths:
        by_kind[classify_metadata_file(path)].append(path)

    notes: list[str] = [f"Skipped (not a recognized metadata file): {path.name}" for path in by_kind[CLASSIFICATION_UNKNOWN]]

    # A native file or a previous export is already a complete,
    # self-contained ImagingAcquisitionMetadata - use it outright rather
    # than trying to merge it with partial legacy data selected alongside it.
    readers = {
        CLASSIFICATION_SIDECAR_JSON: load_acquisition_metadata_sidecar,
        CLASSIFICATION_NATIVE_HDF5: read_imaging_acquisition_metadata,
    }
    for kind, reader in readers.items():
        candidates = by_kind[kind]
        if not candidates:
            continue
        chosen, *extra = candidates
        notes.extend(f"Skipped extra {_KIND_LABELS[kind]} (only one is used): {path.name}" for path in extra)
        metadata = reader(chosen)
        if metadata is None:
            notes.append(f"Could not read {chosen.name} as imaging acquisition metadata.")
            continue
        notes.extend(
            f"Ignored (a {_KIND_LABELS[kind]} was also selected): {path.name}"
            for path in by_kind[CLASSIFICATION_LEGACY_CSV] + by_kind[CLASSIFICATION_LEGACY_TXT]
        )
        notes.append(f"Loaded from {chosen.name}.")
        return MetadataImportResult(metadata=metadata, notes=notes)

    csv_candidates = by_kind[CLASSIFICATION_LEGACY_CSV]
    txt_candidates = by_kind[CLASSIFICATION_LEGACY_TXT]
    if not csv_candidates and not txt_candidates:
        raise ValueError("None of the selected files were recognized as acquisition metadata.")

    csv_path, *extra_csv = csv_candidates or [None]
    txt_path, *extra_txt = txt_candidates or [None]
    notes.extend(f"Skipped extra measuring-times file (only one is used): {path.name}" for path in extra_csv)
    notes.extend(f"Skipped extra metaData.txt file (only one is used): {path.name}" for path in extra_txt)

    metadata = import_legacy_imaging_metadata(csv_path, txt_path)
    if csv_path is not None and txt_path is None:
        notes.append(f"Loaded timing/comments from {csv_path.name} - no metaData.txt selected, so cube times are not linked.")
    elif txt_path is not None and csv_path is None:
        notes.append(f"Loaded camera/illumination settings from {txt_path.name} - no measuring-times file selected.")
    else:
        notes.append(f"Loaded from {csv_path.name} + {txt_path.name}.")
    return MetadataImportResult(metadata=metadata, notes=notes)
