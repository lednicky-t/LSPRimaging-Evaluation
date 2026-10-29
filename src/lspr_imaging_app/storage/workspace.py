"""Two small file helpers: the atomic JSON write and the acquisition-metadata sidecar.

Until 2026-10-29 this file also held the former app's ROI-table, preprocessing and processing-profile files (`roi_table.json`,
`processing_profile.json`, mask payloads). Nothing in the app read or wrote them (the session, `storage/session.py`, has its own
encoders), and the maintainer decided there is no importer for them (`CLAUDE.md`, "What NOT to do"), so cut-over phase 2f removed them.
They are in git history (tag `legacy-final`).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lspr_core import ImagingAcquisitionMetadata

from lspr_imaging_app.format_versions import ACQUISITION_METADATA_VERSION


def write_json_file(path: Path, payload: dict) -> None:
    """Write `payload` to `path` as JSON, atomically.

    This backs the debounced session autosave (ROIs, masks, chromatic
    models, analysis cache, ...), which can fire frequently during normal
    use. Writing to a temp file in the same directory and swapping it in
    with os.replace() (atomic on both POSIX and Windows) means a crash or
    power loss mid-write leaves either the old file or the new one intact,
    never a truncated/corrupt one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


ACQUISITION_METADATA_SCHEMA_NAME = "lspri_acquisition_metadata"


def build_acquisition_metadata_payload(metadata: ImagingAcquisitionMetadata) -> dict:
    """Full-fidelity standalone sidecar payload for the shared
    `lspr_core.ImagingAcquisitionMetadata` shape - the user-facing export/
    backup format for whatever camera/illumination/cube-timing metadata is
    currently loaded (from a native v6.4 file, a legacy import, or manual
    edits in the metadata preview dialog), and also this app's own
    re-importable format (see `io/metadata_import.py`'s file classifier).
    """
    return {
        "schema_name": ACQUISITION_METADATA_SCHEMA_NAME,
        "schema_version": ACQUISITION_METADATA_VERSION,
        "metadata": metadata.model_dump(mode="json"),
    }


def save_acquisition_metadata_sidecar(path: Path, metadata: ImagingAcquisitionMetadata) -> None:
    write_json_file(path, build_acquisition_metadata_payload(metadata))


def acquisition_metadata_from_payload(payload: dict) -> ImagingAcquisitionMetadata:
    """Inverse of `build_acquisition_metadata_payload` - shared by the sidecar
    JSON reader below and by `io/dataset.py`'s OME-Zarr `.zattrs` reader
    (`load_ome_zarr_dataset`), since both store the same payload shape."""
    if not isinstance(payload, dict) or "metadata" not in payload:
        raise ValueError("Invalid acquisition metadata format.")
    version = payload.get("schema_version", 1)
    if isinstance(version, int) and version > ACQUISITION_METADATA_VERSION:
        raise ValueError(
            f"This acquisition metadata was written by a newer version of the app (format {version}; this version "
            f"reads up to {ACQUISITION_METADATA_VERSION})."
        )
    return ImagingAcquisitionMetadata.model_validate(payload["metadata"])


def load_acquisition_metadata_sidecar(path: Path) -> ImagingAcquisitionMetadata:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return acquisition_metadata_from_payload(payload)
