"""Writes a plot's tables (``analysis/export_tables.py``) as CSV files with a JSON record beside them (2026-11-03). No Qt.

``write_plot_export(csv_path, tables, info)``: the first table goes to ``csv_path``, any further one to ``<stem>_<name>.csv``, and the record to
``<stem>.json`` (format name and version, app and version, UTC time, what each file holds, the notes such as out-of-date cubes, and whatever the
caller adds: dataset, session, settings). Every file is written to a temporary name first and then renamed, so a failure leaves no half-written
file under the real name. A file that exists already is never replaced unless ``overwrite`` is True (the save dialog has asked about it).
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from ..analysis.export_tables import EXPORT_FORMAT_NAME, EXPORT_FORMAT_VERSION, Table, cell
from ..version import APP_NAME, APP_VERSION


def export_paths(csv_path: Path, names: list[str]) -> dict[str, Path]:
    """Where each table goes: the first to ``csv_path``, the others to ``<stem>_<name>.csv``; the record is ``"info"``."""
    csv_path = Path(csv_path)
    paths = {}
    for i, name in enumerate(names):
        paths[name] = csv_path if i == 0 else csv_path.with_name(f"{csv_path.stem}_{name}.csv")
    paths["info"] = csv_path.with_suffix(".json")
    return paths


def _write_atomic(path: Path, write) -> None:
    temporary = path.with_name(path.name + ".part")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            write(handle)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_plot_export(csv_path: Path, tables: Mapping[str, Table], info: Mapping[str, object], *, overwrite: bool = False) -> list[Path]:
    """Write the tables and the record; returns the files written (tables first, the record last). Raises ``FileExistsError`` before writing
    anything when a file exists and ``overwrite`` is False."""
    names = list(tables)
    if not names:
        raise ValueError("nothing to export")
    paths = export_paths(Path(csv_path), names)
    if not overwrite:
        existing = [p for p in paths.values() if p.exists()]
        if existing:
            raise FileExistsError(f"Not replaced: {', '.join(str(p) for p in existing)}")
    written: list[Path] = []
    files = []
    try:
        for name in names:
            table, path = tables[name], paths[name]

            def write(handle, table=table) -> None:
                writer = csv.writer(handle)
                writer.writerow(table.header)
                for row in table.rows:
                    writer.writerow([cell(v) for v in row])

            _write_atomic(path, write)
            written.append(path)
            files.append({"file": path.name, "table": name, "columns": list(table.header), "rows": len(table.rows)})
        record = {
            "format_name": EXPORT_FORMAT_NAME,
            "format_version": EXPORT_FORMAT_VERSION,
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files,
            "notes": [note for name in names for note in tables[name].notes],
            **dict(info),
        }
        _write_atomic(paths["info"], lambda handle: json.dump(record, handle, indent=2, default=str))
        written.append(paths["info"])
    except BaseException:
        for path in written:  # an export is complete or not there: the files this call wrote go again
            path.unlink(missing_ok=True)
        raise
    return written
