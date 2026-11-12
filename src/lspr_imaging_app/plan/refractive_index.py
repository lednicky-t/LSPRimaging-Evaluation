"""Refractive indices of the experiment's solutions (pure, no Qt; 2026-11-12).

The lab keeps one table of the stock solutions' refractive indices (RIs), measured with a refractometer, as a
tab-separated text file that goes with every experiment (``RIs.txt``)::

    Sample  RI       Std       Temp  N  Date
    H2O     1.33268  0.000000  24    5  09.09.2025
    10perEG 1.34322  0.000000  24    5  02.09.2025

One value per **solution**: a stock solution comes from one reservoir and is used in several steps, so every step
whose solution it is gets that value. A step's solution is the first name of its note, the text before the first
``_-_`` / ``_--_`` (`label_rule.shown_label` in "solution" mode, the maintainer's rule: ``20perEG_-_ch1_to_40perEG_-_ch3F_ch4P``
is a ``20perEG`` step).

**What is kept.** `RiTable` is a copy of the whole file as it was imported (every row, also solutions this experiment
does not use) plus where it came from: file name, SHA-256 of its bytes, import time, and the refractometer's
wavelength. It is saved in the session, so the values an analysis used stay the same when ``RIs.txt`` is later edited
with new measurements (the checksum tells that it was). The dataset folder is never written.

**Units and assumptions** (documented because the numbers are only meaningful with them):

- RI and Std are dimensionless (refractive index units, RIU). Std is the spread the refractometer reported over N
  readings.
- Temp is in degrees Celsius, as typed in the file; no temperature correction is applied (water: dn/dT is about
  -1e-4 per K, the same size as the RI differences between the dilute polyelectrolyte / salt solutions).
- The wavelength is the refractometer's measuring wavelength in nm, not in the file: it comes from Preferences and is
  stamped on the table at import (None: not given). RI depends on wavelength (dispersion), so a value without it is
  incomplete.
- Date is the day the RI was measured (``DD.MM.YYYY``; ``YYYY-MM-DD`` is accepted too). Its age is counted in whole
  days to the experiment day (`age_days`).

Names are matched ignoring letter case, with spaces and underscores treated alike (``0.1M NaCl`` = ``0.1M_NaCl``):
`normalise_name`. A malformed line stops the import with its line number (`RiFileError`); nothing is skipped silently.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .label_rule import DEFAULT_SEPARATORS, MODE_SOLUTION, shown_label

RI_FILE_NAME = "RIs.txt"
"""The name the lab gives the file; found in the dataset folder ignoring case (`find_ri_file`)."""

RI_MIN, RI_MAX = 1.0, 3.0
"""Plausible refractive indices of a liquid. A value outside is a typing or column error, reported, never kept."""

_REQUIRED = ("sample", "ri")
_OPTIONAL = ("std", "temp", "n", "date")


class RiFileError(ValueError):
    """The file could not be read as an RI table; `line` is 1-based (0: the file as a whole)."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


@dataclass(frozen=True)
class SolutionRi:
    name: str  # as written in the file
    ri: float  # refractive index (RIU)
    std: float | None = None  # spread of the N readings (RIU)
    temp_c: float | None = None  # temperature of the measurement, degrees Celsius
    n: int | None = None  # number of readings averaged
    measured_on: date | None = None  # the day the RI was measured


@dataclass(frozen=True)
class RiTable:
    source_name: str  # the file's name when imported (e.g. "RIs.txt")
    sha256: str  # of the file's bytes: tells whether the file has changed since
    imported_at_utc: str  # ISO 8601
    wavelength_nm: float | None  # the refractometer's wavelength (Preferences, stamped at import); None: not given
    solutions: tuple[SolutionRi, ...]

    def find(self, solution: str) -> SolutionRi | None:
        key = normalise_name(solution)
        return next((row for row in self.solutions if normalise_name(row.name) == key), None)

    # -- session payload (storage/session.py, key "refractive_index") ------------------------------

    def to_payload(self) -> dict:
        return {
            "source_name": self.source_name,
            "sha256": self.sha256,
            "imported_at_utc": self.imported_at_utc,
            "wavelength_nm": self.wavelength_nm,
            "solutions": [
                {
                    "name": row.name, "ri": row.ri, "std": row.std, "temp_c": row.temp_c, "n": row.n,
                    "measured_on": None if row.measured_on is None else row.measured_on.isoformat(),
                }
                for row in self.solutions
            ],
        }

    @classmethod
    def from_payload(cls, raw: object) -> RiTable | None:
        """None for a missing block (a session from before 1.7, or no RIs imported). Raises ValueError for a block
        that is there but unreadable: `load_session` decides what to do with that, not this function."""
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("refractive_index block is not an object")
        rows = []
        for item in raw.get("solutions", []):
            measured = item.get("measured_on")
            rows.append(SolutionRi(
                name=str(item["name"]), ri=float(item["ri"]),
                std=None if item.get("std") is None else float(item["std"]),
                temp_c=None if item.get("temp_c") is None else float(item["temp_c"]),
                n=None if item.get("n") is None else int(item["n"]),
                measured_on=None if measured is None else date.fromisoformat(str(measured)),
            ))
        wavelength = raw.get("wavelength_nm")
        return cls(
            source_name=str(raw.get("source_name", "")), sha256=str(raw.get("sha256", "")),
            imported_at_utc=str(raw.get("imported_at_utc", "")),
            wavelength_nm=None if wavelength is None else float(wavelength), solutions=tuple(rows),
        )


def normalise_name(name: str) -> str:
    """How two solution names are compared: case ignored, runs of spaces / underscores the same, ends trimmed."""
    return re.sub(r"[\s_]+", "_", name.strip()).casefold()


def solution_of(note: str) -> str:
    """The solution of a step: the first name of its note (text before the first ``_-_`` / ``_--_``)."""
    return shown_label(note, MODE_SOLUTION, DEFAULT_SEPARATORS)


def _parse_date(text: str, line: int) -> date | None:
    text = text.strip()
    if not text:
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise RiFileError(line, f"date {text!r} is neither DD.MM.YYYY nor YYYY-MM-DD")


def _parse_float(text: str, column: str, line: int) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))  # a decimal comma from a spreadsheet
    except ValueError:
        raise RiFileError(line, f"{column} {text!r} is not a number") from None


def parse_ri_text(text: str) -> tuple[SolutionRi, ...]:
    """The rows of an RI file. The first non-empty line is the header (tab-separated; Sample and RI required, Std,
    Temp, N, Date optional, any order, case ignored; other columns are ignored). Rows are tab-separated, since a name
    may contain a space."""
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if header_index is None:
        raise RiFileError(0, "the file is empty")
    header = [cell.strip().casefold() for cell in lines[header_index].split("\t")]
    missing = [name for name in _REQUIRED if name not in header]
    if missing:
        raise RiFileError(
            header_index + 1,
            f"the header has no {' / '.join(missing)} column (tab-separated header expected: Sample, RI, Std, Temp, N, Date)",
        )
    column = {name: header.index(name) for name in (*_REQUIRED, *_OPTIONAL) if name in header}
    rows: list[SolutionRi] = []
    seen: dict[str, int] = {}
    for index in range(header_index + 1, len(lines)):
        number = index + 1
        raw = lines[index]
        if not raw.strip():
            continue
        cells = raw.split("\t")
        if len(cells) <= max(column["sample"], column["ri"]):
            raise RiFileError(number, "fewer tab-separated values than the header has (is the line separated by spaces?)")

        def cell(name: str, cells: list[str] = cells) -> str:  # `cells` bound as a default: this line's values, whenever it is called
            position = column.get(name)
            return cells[position] if position is not None and position < len(cells) else ""

        name = cell("sample").strip()
        if not name:
            raise RiFileError(number, "no sample name")
        key = normalise_name(name)
        if key in seen:
            raise RiFileError(number, f"{name!r} is listed twice (first on line {seen[key]})")
        seen[key] = number
        ri = _parse_float(cell("ri"), "RI", number)
        if ri is None:
            raise RiFileError(number, f"no RI for {name!r}")
        if not RI_MIN <= ri <= RI_MAX:
            raise RiFileError(number, f"RI {ri} of {name!r} is outside {RI_MIN}-{RI_MAX}")
        n_value = _parse_float(cell("n"), "N", number)
        rows.append(SolutionRi(
            name=name, ri=ri, std=_parse_float(cell("std"), "Std", number), temp_c=_parse_float(cell("temp"), "Temp", number),
            n=None if n_value is None else int(n_value), measured_on=_parse_date(cell("date"), number),
        ))
    if not rows:
        raise RiFileError(0, "the file has a header but no solutions")
    return tuple(rows)


def decode(data: bytes) -> str:
    """The file's text: UTF-8 (with or without a byte-order mark), else the Windows code page a lab PC writes."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def table_from_bytes(data: bytes, source_name: str, *, wavelength_nm: float | None, now_utc: datetime) -> RiTable:
    return RiTable(
        source_name=source_name, sha256=checksum(data), imported_at_utc=now_utc.isoformat().replace("+00:00", "Z"),
        wavelength_nm=wavelength_nm, solutions=parse_ri_text(decode(data)),
    )


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_ri_file(folder: Path | None) -> Path | None:
    """``RIs.txt`` directly in `folder`, its name matched ignoring case; None when there is none (or no folder)."""
    if folder is None or not folder.is_dir():
        return None
    wanted = RI_FILE_NAME.casefold()
    return next((path for path in folder.iterdir() if path.is_file() and path.name.casefold() == wanted), None)


def age_days(measured_on: date | None, experiment_day: date | None) -> int | None:
    """Whole days from the RI measurement to the experiment (negative: measured after it). None if a date is missing."""
    if measured_on is None or experiment_day is None:
        return None
    return (experiment_day - measured_on).days


@dataclass(frozen=True)
class RiUsage:
    """The table seen through one experiment's steps."""

    used: tuple[tuple[SolutionRi, tuple[int, ...]], ...]  # a solution with an RI and the steps (numbers the user sees) using it
    missing: tuple[tuple[str, tuple[int, ...]], ...]  # a step solution the table has no RI for, and its steps
    unused: tuple[str, ...]  # rows of the table no step uses (kept in the table, not shown as used)


def usage(table: RiTable | None, steps: Iterable[tuple[int, str]]) -> RiUsage:
    """`steps`: (number the user sees, note) of each step, in order."""
    by_solution: dict[str, tuple[str, list[int]]] = {}
    for number, note in steps:
        solution = solution_of(note)
        if not solution:
            continue
        entry = by_solution.setdefault(normalise_name(solution), (solution, []))
        entry[1].append(number)
    used: list[tuple[SolutionRi, tuple[int, ...]]] = []
    missing: list[tuple[str, tuple[int, ...]]] = []
    for _key, (solution, numbers) in by_solution.items():
        row = None if table is None else table.find(solution)
        if row is None:
            missing.append((solution, tuple(numbers)))
        else:
            used.append((row, tuple(numbers)))
    used_keys = {normalise_name(row.name) for row, _numbers in used}
    unused = () if table is None else tuple(row.name for row in table.solutions if normalise_name(row.name) not in used_keys)
    return RiUsage(used=tuple(used), missing=tuple(missing), unused=unused)


def step_numbers_text(numbers: Sequence[int]) -> str:
    """``1, 3-5, 9``: step numbers with runs shortened."""
    parts: list[str] = []
    run: list[int] = []
    for number in sorted(set(numbers)):
        if run and number == run[-1] + 1:
            run.append(number)
            continue
        if run:
            parts.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [number]
    if run:
        parts.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
    return ", ".join(parts)
