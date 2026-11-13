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

**Edits (2026-11-13, maintainer).** The imported copy (``solutions``) is never changed: the user's edits are a second list, ``RiTable.rows`` (the *working* table), kept in the
same session block. ``rows`` is None while nothing was edited (the working table is then the imported one), so a session without edits is written exactly as before. A
working row (`RiRow`) is one line of the table: every field editable, ``base`` = the imported row it started as (None: added by the user), ``steps`` = None while the row is
given to the steps by its name (the rule above), else exactly the steps the user assigned by hand (`StepRef`s: they stay the same when steps are added or removed). A
row without an RI (``ri`` None) is "pending": it matches nothing until it has one. Everything downstream (the S_bulk fit, the bulk plot, the plan bar) reads the working
table; a value that differs from the import is flagged ``SolutionRi.edited`` so it can say so wherever it is shown. Edits are pure functions of (rows, change) that raise
`RiEditError` with a sentence for the user; the owner (`ExperimentPlanModule`) makes them undoable.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from datetime import date, datetime
from pathlib import Path

from .label_rule import DEFAULT_SEPARATORS, MODE_SOLUTION, shown_label
from .state import StepRef, ref_from_payload, ref_to_payload

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


class RiEditError(ValueError):
    """An edit of the table was refused; the message is a sentence for the user and the table is as it was."""


HAND_SOURCE_NAME = "entered by hand"
"""`RiTable.source_name` of a table the user started without importing a file (it has no file, so an empty checksum)."""


@dataclass(frozen=True)
class SolutionRi:
    name: str  # as written in the file
    ri: float  # refractive index (RIU)
    std: float | None = None  # spread of the N readings (RIU)
    temp_c: float | None = None  # temperature of the measurement, degrees Celsius
    n: int | None = None  # number of readings averaged
    measured_on: date | None = None  # the day the RI was measured
    edited: bool = field(default=False, compare=False)
    """Only on a value taken from the working table: the user changed it, added it or assigned it to steps by hand (never set on an imported row)."""


@dataclass(frozen=True)
class RiRow:
    """One line of the working table: the editable counterpart of `SolutionRi` (see the module docstring)."""

    name: str = ""
    ri: float | None = None  # None: pending, no value yet
    std: float | None = None
    temp_c: float | None = None
    n: int | None = None
    measured_on: date | None = None
    base: str | None = None  # `normalise_name` of the imported row this started as; None: added by the user
    steps: tuple[StepRef, ...] | None = None  # None: matched to steps by its name; else exactly these steps (never empty)

    @classmethod
    def imported(cls, row: SolutionRi) -> RiRow:
        return cls(row.name, row.ri, row.std, row.temp_c, row.n, row.measured_on, base=normalise_name(row.name))

    def values(self) -> tuple:
        """The six fields the file has, for comparing with the imported row."""
        return (self.name, self.ri, self.std, self.temp_c, self.n, self.measured_on)

    def to_payload(self) -> dict:
        return {
            "name": self.name, "ri": self.ri, "std": self.std, "temp_c": self.temp_c, "n": self.n,
            "measured_on": None if self.measured_on is None else self.measured_on.isoformat(),
            "base": self.base, "steps": None if self.steps is None else [ref_to_payload(ref) for ref in self.steps],
        }

    @classmethod
    def from_payload(cls, item: Mapping) -> RiRow:
        measured, steps = item.get("measured_on"), item.get("steps")
        return cls(
            name=str(item["name"]), ri=None if item.get("ri") is None else float(item["ri"]),
            std=None if item.get("std") is None else float(item["std"]),
            temp_c=None if item.get("temp_c") is None else float(item["temp_c"]),
            n=None if item.get("n") is None else int(item["n"]),
            measured_on=None if measured is None else date.fromisoformat(str(measured)),
            base=None if item.get("base") is None else str(item["base"]),
            steps=None if steps is None else tuple(ref_from_payload(ref) for ref in steps) or None,
        )


_FIELD_NAMES = ("name", "ri", "std", "temp_c", "n", "measured_on")


@dataclass(frozen=True)
class RiTable:
    source_name: str  # the file's name when imported (e.g. "RIs.txt")
    sha256: str  # of the file's bytes: tells whether the file has changed since
    imported_at_utc: str  # ISO 8601
    wavelength_nm: float | None  # the refractometer's wavelength (Preferences, stamped at import); None: not given
    solutions: tuple[SolutionRi, ...]  # the imported copy: never changed
    rows: tuple[RiRow, ...] | None = None  # the user's working table; None while nothing was edited (then it is the imported one)

    # -- the working table -----------------------------------------------------------------------------

    # The table is immutable, so what is worked out from it is kept (cached_property writes the instance's dict, which a frozen dataclass allows): a step's lookup is
    # called per step per row of the ROI table by the S_bulk columns, and rebuilding the imported rows at every call made it ~15 times slower (measured 2026-11-14).
    @cached_property
    def _imported(self) -> tuple[RiRow, ...]:
        return tuple(RiRow.imported(row) for row in self.solutions)

    @cached_property
    def _imported_by_base(self) -> dict[str | None, RiRow]:
        return {row.base: row for row in self._imported}

    @cached_property
    def _working_keys(self) -> tuple[str, ...]:
        return tuple(normalise_name(row.name) for row in self.working())

    def imported_rows(self) -> tuple[RiRow, ...]:
        return self._imported

    def working(self) -> tuple[RiRow, ...]:
        """The rows every calculation reads: the user's, or the imported ones while nothing was edited."""
        return self.imported_rows() if self.rows is None else self.rows

    def with_rows(self, rows: Iterable[RiRow]) -> RiTable:
        """The same import with `rows` as its working table (None again when they are exactly the imported rows; imported rows keep the file's order, added ones follow)."""
        imported = self.imported_rows()
        order = {row.base: index for index, row in enumerate(imported)}
        ordered = tuple(sorted(rows, key=lambda row: order.get(row.base, len(order)) if row.base is not None else len(order)))
        return replace(self, rows=None if ordered == imported else ordered)

    def has_edits(self) -> bool:
        return self.rows is not None

    def removed_rows(self) -> tuple[RiRow, ...]:
        """Imported rows the user took out of the working table (they can be put back)."""
        kept = {row.base for row in self.working() if row.base is not None}
        return tuple(row for row in self.imported_rows() if row.base not in kept)

    def changed_fields(self, row: RiRow) -> tuple[str, ...]:
        """The fields of `row` that differ from the imported row it started as ("steps" when it was assigned by hand); () for an added row."""
        was = self._imported_by_base.get(row.base) if row.base is not None else None
        if was is None:
            return ()
        changed = [name for name, new, old in zip(_FIELD_NAMES, row.values(), was.values()) if new != old]
        if row.steps is not None:
            changed.append("steps")
        return tuple(changed)

    def status(self, row: RiRow) -> str:
        """"" (as imported), "edited" or "added"."""
        if row.base is None:
            return "added"
        return "edited" if self.changed_fields(row) else ""

    def edit_counts(self) -> tuple[int, int, int]:
        """(rows edited, rows added, rows removed) against the import."""
        rows = self.working()
        return (sum(1 for row in rows if self.status(row) == "edited"), sum(1 for row in rows if row.base is None), len(self.removed_rows()))

    def edit_summary(self) -> str:
        """What the user changed, in words ("" when nothing): ``2 rows edited, 1 row added, 1 row removed, 1 row assigned to steps by hand``."""
        edited, added, removed = self.edit_counts()
        manual = sum(1 for row in self.working() if row.steps is not None)
        parts = [
            f"{count} row{'' if count == 1 else 's'} {what}"
            for count, what in ((edited, "edited"), (added, "added"), (removed, "removed"), (manual, "assigned to steps by hand")) if count
        ]
        return ", ".join(parts)

    def solution_for(self, row: RiRow) -> SolutionRi | None:
        """The value the calculations use for `row` (None while it has no RI), flagged when the user changed it."""
        if row.ri is None:
            return None
        return SolutionRi(row.name, row.ri, row.std, row.temp_c, row.n, row.measured_on, edited=bool(self.status(row)))

    def imported_text(self, row: RiRow) -> str:
        """For a tool tip: what the import had for the row `row` started as ("" for an added row)."""
        was = next((s for s in self.solutions if normalise_name(s.name) == row.base), None) if row.base is not None else None
        if was is None:
            return ""
        parts = [f"RI {was.ri:.5f}"]
        if was.std is not None:
            parts.append(f"± {was.std:.6f}")
        if was.measured_on is not None:
            parts.append(f"measured {was.measured_on:%d.%m.%Y}")
        return f"{was.name}: " + ", ".join(parts)

    # -- which row a step gets ---------------------------------------------------------------------------

    def row_index_for_step(self, ref: StepRef | None, note: str) -> int | None:
        """Index (in `working()`) of the row a step takes its RI from: a row the user assigned it to by hand, else the row named like the first name of its note."""
        rows = self.working()
        if ref is not None:
            for index, row in enumerate(rows):
                if row.steps is not None and ref in row.steps:
                    return index
        key = normalise_name(solution_of(note))
        if not key:
            return None
        keys = self._working_keys
        for index, row in enumerate(rows):
            if row.steps is None and keys[index] == key:
                return index
        return None

    def find_for_step(self, ref: StepRef | None, note: str) -> SolutionRi | None:
        """The value a step uses (None: no row, or its row has no RI yet)."""
        index = self.row_index_for_step(ref, note)
        return None if index is None else self.solution_for(self.working()[index])

    def find(self, solution: str) -> SolutionRi | None:
        """The value of the row named `solution` (by name only: a row assigned to steps by hand is not found this way)."""
        key = normalise_name(solution)
        keys = self._working_keys
        for index, row in enumerate(self.working()):
            if row.steps is None and row.ri is not None and keys[index] == key:
                return self.solution_for(row)
        return None

    # -- session payload (storage/session.py, key "refractive_index") ------------------------------

    def to_payload(self) -> dict:
        payload: dict = {
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
        if self.rows is not None:  # session schema 1.9: only written when the user edited something
            payload["rows"] = [row.to_payload() for row in self.rows]
        return payload

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
        saved_rows = raw.get("rows")  # the user's working table (schema 1.9); absent: nothing was edited
        if saved_rows is not None and not isinstance(saved_rows, list):
            raise ValueError("refractive_index rows is not a list")
        return cls(
            source_name=str(raw.get("source_name", "")), sha256=str(raw.get("sha256", "")),
            imported_at_utc=str(raw.get("imported_at_utc", "")),
            wavelength_nm=None if wavelength is None else float(wavelength), solutions=tuple(rows),
            rows=None if saved_rows is None else tuple(RiRow.from_payload(item) for item in saved_rows),
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
    """The working table seen through one experiment's steps."""

    used: tuple[tuple[SolutionRi, tuple[int, ...]], ...]  # a row with an RI and the steps (numbers the user sees) using it, in order of the first step
    missing: tuple[tuple[str, tuple[int, ...]], ...]  # a step solution that has no RI (no row, or a row still without a value), and its steps
    unused: tuple[str, ...]  # rows of the table no step uses (kept in the table)
    unlisted: tuple[tuple[str, tuple[int, ...]], ...] = ()  # the part of `missing` that has no row at all
    row_steps: tuple[tuple[int, ...], ...] = ()  # the step numbers each row of ``table.working()`` serves, same order (also rows without an RI)


def usage(table: RiTable | None, steps: Iterable[tuple]) -> RiUsage:
    """`steps`: (number the user sees, note) of each step, in order; with the step's `StepRef` as a third item a row assigned to it by hand is found too."""
    rows = () if table is None else table.working()
    row_steps: list[list[int]] = [[] for _ in rows]
    order: list[int] = []  # rows in the order their first step comes
    pending: dict[str, tuple[str, list[int]]] = {}
    unlisted_keys: set[str] = set()
    for entry in steps:
        number, note = entry[0], entry[1]
        index = None if table is None else table.row_index_for_step(entry[2] if len(entry) > 2 else None, note)
        if index is not None:
            if not row_steps[index]:
                order.append(index)
            row_steps[index].append(number)
            if rows[index].ri is not None:
                continue
        name = (rows[index].name if index is not None else "") or solution_of(note)
        if not name:
            continue
        if index is None:
            unlisted_keys.add(normalise_name(name))
        pending.setdefault(normalise_name(name), (name, []))[1].append(number)
    used = tuple((table.solution_for(rows[index]), tuple(row_steps[index])) for index in order if rows[index].ri is not None)
    missing = tuple((name, tuple(numbers)) for name, numbers in pending.values())
    unlisted = tuple((name, tuple(numbers)) for key, (name, numbers) in pending.items() if key in unlisted_keys)
    unused = tuple(row.name for index, row in enumerate(rows) if not row_steps[index])
    return RiUsage(used=used, missing=missing, unused=unused, unlisted=unlisted, row_steps=tuple(tuple(numbers) for numbers in row_steps))


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


# -- editing the working table (pure: each returns the new rows or raises `RiEditError`) --------------------------------------------------------


def _number(text: str, column: str) -> float | None:
    """A finite number from a cell (decimal comma allowed); None for an empty cell."""
    try:
        value = _parse_float(text, column, 0)
    except RiFileError as exc:
        raise RiEditError(str(exc)) from None
    if value is not None and not math.isfinite(value):
        raise RiEditError(f"{column} {text.strip()!r} is not a number")
    return value


def edit_row(rows: Sequence[RiRow], index: int, field_name: str, text: str) -> tuple[RiRow, ...]:
    """Change one cell of row `index` from the text typed in it. ``name``: required, unique; ``ri``: 1.0 - 3.0 (empty: no value yet); ``std`` >= 0, ``temp_c``, ``n`` (a whole
    number >= 0) and ``measured_on`` (DD.MM.YYYY or YYYY-MM-DD) may be empty."""
    rows = tuple(rows)
    if not 0 <= index < len(rows):
        raise RiEditError("there is no such row")
    row, text = rows[index], text.strip()
    if field_name == "name":
        if not text:
            raise RiEditError("a solution needs a name")
        key = normalise_name(text)
        clash = next((other for i, other in enumerate(rows) if i != index and normalise_name(other.name) == key), None)
        if clash is not None:
            raise RiEditError(f"{clash.name!r} is already in the table (names are compared ignoring case, spaces and underscores)")
        new = replace(row, name=text)
    elif field_name == "ri":
        value = _number(text, "RI")
        if value is not None and not RI_MIN <= value <= RI_MAX:
            raise RiEditError(f"RI {value:g} is outside {RI_MIN}-{RI_MAX}")
        new = replace(row, ri=value)
    elif field_name == "std":
        value = _number(text, "Std")
        if value is not None and value < 0:
            raise RiEditError("Std cannot be negative")
        new = replace(row, std=value)
    elif field_name == "temp_c":
        new = replace(row, temp_c=_number(text, "Temp"))
    elif field_name == "n":
        value = _number(text, "N")
        if value is not None and (value < 0 or value != int(value)):
            raise RiEditError("N is the number of readings: a whole number, 0 or more")
        new = replace(row, n=None if value is None else int(value))
    elif field_name == "measured_on":
        try:
            new = replace(row, measured_on=_parse_date(text, 0))
        except RiFileError as exc:
            raise RiEditError(str(exc)) from None
    else:
        raise RiEditError(f"{field_name!r} is not a column that can be edited")
    return rows[:index] + (new,) + rows[index + 1:]


def set_row_steps(rows: Sequence[RiRow], index: int, refs: Sequence[StepRef] | None, describe: Callable[[StepRef], str] = str) -> tuple[RiRow, ...]:
    """Give row `index` exactly the steps `refs` (by hand), or None / nothing to match it to steps by its name again. A step belongs to one row by hand: one already given to
    another row is refused (`describe` names it for the message)."""
    rows = tuple(rows)
    if not 0 <= index < len(rows):
        raise RiEditError("there is no such row")
    chosen = tuple(dict.fromkeys(refs)) if refs else None
    if chosen:
        for other_index, other in enumerate(rows):
            taken = [ref for ref in chosen if other.steps and ref in other.steps]
            if other_index != index and taken:
                raise RiEditError(f"{describe(taken[0])} is already assigned by hand to {other.name or 'another row'!r}: take it from that row first")
    return rows[:index] + (replace(rows[index], steps=chosen),) + rows[index + 1:]


def add_row(rows: Sequence[RiRow]) -> tuple[tuple[RiRow, ...], int]:
    """(rows, index of the new blank row). A blank row already waiting to be filled in is reused, so a click on Add twice does not pile them up."""
    rows = tuple(rows)
    for index, row in enumerate(rows):
        if row.base is None and not row.name and row.ri is None:
            return rows, index
    return rows + (RiRow(),), len(rows)


def add_row_with(rows: Sequence[RiRow], name: str, ri_text: str) -> tuple[RiRow, ...]:
    """A new row `name` with the RI typed for it (the way a step solution without an RI gets one). The name must be new."""
    rows = tuple(rows)
    grown = rows + (RiRow(),)
    return edit_row(edit_row(grown, len(rows), "name", name), len(rows), "ri", ri_text)


def remove_row(rows: Sequence[RiRow], index: int) -> tuple[RiRow, ...]:
    rows = tuple(rows)
    if not 0 <= index < len(rows):
        raise RiEditError("there is no such row")
    return rows[:index] + rows[index + 1:]


def revert_row(rows: Sequence[RiRow], index: int, imported: Sequence[RiRow]) -> tuple[RiRow, ...]:
    """Row `index` back as imported (an added row has nothing to go back to: it is removed)."""
    rows = tuple(rows)
    if not 0 <= index < len(rows):
        raise RiEditError("there is no such row")
    row = rows[index]
    was = next((r for r in imported if r.base == row.base), None) if row.base is not None else None
    return remove_row(rows, index) if was is None else rows[:index] + (was,) + rows[index + 1:]


def restore_row(rows: Sequence[RiRow], base: str, imported: Sequence[RiRow]) -> tuple[RiRow, ...]:
    """Put an imported row the user removed back, as imported. A name another row took meanwhile is refused."""
    was = next((r for r in imported if r.base == base), None)
    if was is None or any(r.base == base for r in rows):
        raise RiEditError("that row is not removed")
    clash = next((r for r in rows if normalise_name(r.name) == normalise_name(was.name)), None)
    if clash is not None:
        raise RiEditError(f"{clash.name!r} is in the table already: rename or remove it first")
    return tuple(rows) + (was,)


def parse_step_numbers(text: str) -> tuple[int, ...]:
    """``1, 3-5, 9`` -> (1, 3, 4, 5, 9): the way `step_numbers_text` writes them (also ``;`` and spaces as separators, an en dash for a range). Empty: ()."""
    numbers: set[int] = set()
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:[-\u2013](\d+))?", part)
        if match is None:
            raise RiEditError(f"{part!r} is not a step number (write them like 1, 3-5, 9)")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if first < 1:
            raise RiEditError(f"{part!r}: step numbers start at 1")
        if last < first:
            raise RiEditError(f"{part!r} is not a range of steps (a range goes upwards, like 3-5)")
        numbers.update(range(first, last + 1))
    return tuple(sorted(numbers))
