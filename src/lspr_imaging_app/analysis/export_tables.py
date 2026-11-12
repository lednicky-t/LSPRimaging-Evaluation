"""The numbers behind a plot, as tables (2026-11-03): what "Save data" on a plot writes. Pure, no Qt, no files.

Design: ``docs/results_export_design_2026-10-29.md`` section 10 (the maintainer's answers): one export control on every plot area, figures and
data; **export what is drawn** (the data of a plot is the data of that plot); out-of-date cubes are marked, never hidden. The detailed table
format was left for a later discussion, so this is **format 0.1, provisional**: plain CSV, one header row, full precision (``repr`` of each float,
so the file reads back to the same numbers), empty cells for no value, units in the column names. ``export_info`` (the JSON beside the CSV)
says what produced it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

EXPORT_FORMAT_NAME = "lspri_plot_export"
EXPORT_FORMAT_VERSION = "0.1"


@dataclass(frozen=True)
class Table:
    """A header and rows of cells (str, int, float or None = empty)."""

    header: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    notes: tuple[str, ...] = field(default_factory=tuple)  # said in the export message and recorded in the JSON (e.g. out-of-date cubes)


def cell(value: object) -> str:
    """One CSV cell: floats at full precision (``repr``: reads back to the same number), NaN / None empty, the rest as text."""
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "1" if value else "0"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return "" if not math.isfinite(value) else repr(value)
    return str(value)


def sensorgram_table(series: Sequence[object], x_label: str, cube_indices: Sequence[int], stale: np.ndarray | None) -> Table:
    """The Sensorgram as drawn: one row per cube (``cube``, the x value under ``x_label``, ``out_of_date``), one column per line (its label, in nm)
    and, for an average, its band (``<label> low`` / ``<label> high``). ``series`` are ``scan_trace.Series`` (all on the same x)."""
    if not series:
        return Table(("cube", x_label, "out_of_date"), ())
    n = len(series[0].x)
    header = ["cube", x_label, "out_of_date"]
    columns: list[np.ndarray] = []
    for s in series:
        header.append(f"{s.label} (nm)")
        columns.append(np.asarray(s.y, dtype=np.float64))
        if s.band_low is not None and s.band_high is not None:
            header += [f"{s.label} low (nm)", f"{s.label} high (nm)"]
            columns += [np.asarray(s.band_low, dtype=np.float64), np.asarray(s.band_high, dtype=np.float64)]
    x = np.asarray(series[0].x, dtype=np.float64)
    out_of_date = np.zeros(n, dtype=bool) if stale is None else np.asarray(stale, dtype=bool)
    rows = tuple(
        (int(cube_indices[i]) if i < len(cube_indices) else None, x[i], bool(out_of_date[i]), *(col[i] for col in columns)) for i in range(n)
    )
    notes = (f"{int(out_of_date.sum())} of {n} cubes are out of date (marked in the out_of_date column); run the scan again.",) if out_of_date.any() else ()
    return Table(tuple(header), rows, notes)


def step_stats_table(table: object | None) -> Table | None:
    """The step statistics table drawn on the Sensorgram (``plan.stats_table.StatsTable``), one row per line and step: the step's number and title,
    where it starts and ends (in the plot's x), mean, SD and n. None when no table is drawn."""
    if table is None or not table.columns or not table.lines:
        return None
    header = ("line", "step", "step_title", "step_start", "step_end", f"mean ({table.unit})", f"sd ({table.unit})", "n")
    rows = []
    for line_index, line in enumerate(table.lines):
        for column in table.columns:
            mean, sd, n = table.value(line_index, column)
            rows.append((line.label, column.index + 1, column.title, column.x_start, column.x_end, mean, sd, n))
    return Table(header, tuple(rows))


def roi_step_table(
    steps: Sequence[tuple[str, str, str, bool]],
    rows: Sequence[tuple[str, str, int | None, Sequence[tuple[int, float, float] | None], bool | None]],
    mode: str,
) -> Table:
    """The ROI / Groups table's step and comparison columns as they are shown (2026-11-05): one row per group and per ROI, in the order of the table, and per column the value
    (the mean of a step, the difference or ratio of a comparison) and / or its spread and the number of cubes used. `mode` is the table's display: "mean" (value only), "std"
    (SD only), "sem" (SEM only), "both" (value and SD) or "mean_sem" (value and SEM). `steps`: per column
    ``(label, word for the value, unit, has a spread)``, e.g. ``("step 1 (H2O)", "mean", "nm", True)``; the labels are unique so the header is. Each row is
    ``(kind "group" | "roi", group name, ROI id or None, [(n, value, sd, sem) or None per column], current)``; `current` is True / False for numbers that are / are not up to
    date and None for a row that was never calculated (its cells are empty). A column without a spread (a signal-to-noise ratio) has only its value, in every mode. Full precision;
    an empty cell is no number."""
    if mode in ("excess", "excess_u", "excess_z"):
        return _excess_table(steps, rows)
    spread = "sem" if mode in ("sem", "mean_sem") else "sd"
    with_value = mode not in ("std", "sem")
    with_spread = mode != "mean"
    header: list[str] = ["row", "group", "roi_id"]
    for label, word, unit, has_sd in steps:
        unit_text = f" ({unit})" if unit else ""
        if with_value or not has_sd:
            header.append(f"{label} {word}{unit_text}")
        if with_spread and has_sd:
            header.append(f"{label} {spread}{unit_text}")
        header.append(f"{label} n")
    header.append("up_to_date")
    out: list[tuple[object, ...]] = []
    for kind, group, roi_id, values, current in rows:
        cells: list[object] = [kind, group, roi_id]
        for value, (_label, _word, _unit, has_sd) in zip(values, steps):
            n, mean, sd, sem = value[:4] if value is not None else (None, None, None, None)
            if with_value or not has_sd:
                cells.append(mean)
            if with_spread and has_sd:
                cells.append(sem if spread == "sem" else sd)
            cells.append(n)
        cells.append("" if current is None else ("yes" if current else "no"))
        out.append(tuple(cells))
    notes = []
    stale = sum(1 for row in rows if row[4] is False)
    if stale:
        notes.append(f"{stale} row{'s' if stale != 1 else ''} out of date (marked 'no' in the last column): the steps, the statistics or the scan changed after they were calculated.")
    return Table(tuple(header), tuple(out), tuple(notes))


def _excess_table(steps, rows) -> Table:
    """`roi_step_table` in an excess mode (2026-11-12): per column measured minus the bulk prediction, its uncertainty, and the two divided, whichever excess mode is shown
    (the other two are one division away). A column with no excess (S_bulk, a ratio) is left out; a cell with none is empty (the tool tip and the table say NA and why)."""
    header: list[str] = ["row", "group", "roi_id"]
    keep = [has_sd and word != "slope" for _label, word, _unit, has_sd in steps]
    for (label, _word, unit, _has_sd), kept in zip(steps, keep):
        if kept:
            header.extend((f"{label} measured - predicted ({unit})", f"{label} u ({unit})", f"{label} (measured - predicted) / u", f"{label} n"))
    header.append("up_to_date")
    out = []
    for kind, group, roi_id, values, current in rows:
        cells: list[object] = [kind, group, roi_id]
        for value, kept in zip(values, keep):
            if not kept:
                continue
            n, e, u = (None, None, None) if value is None or len(value) < 6 else (value[0], value[4], value[5])
            finite_e = e is not None and math.isfinite(e)
            finite_u = u is not None and math.isfinite(u) and u > 0
            cells.extend((e if finite_e else None, u if finite_u else None, e / u if finite_e and finite_u else None, n))
        cells.append("" if current is None else ("yes" if current else "no"))
        out.append(tuple(cells))
    notes = ["measured - predicted: the step's mean (or a difference S - B) minus what the row's own bulk sensitivity predicts from the refractive index; "
             "positive = more shift than the bulk refractive index explains. Empty = no refractive index for the step's solution, or no S_bulk."]
    return Table(tuple(header), tuple(out), tuple(notes))


def spectra_table(spectra: Sequence[object], names: Sequence[str], hc_ev_nm: float) -> Table:
    """The spectra drawn on the Spectra plot, long: one row per measured point (``kind`` measured, with its error), per point of the fitted curve
    (``kind`` fit) and per metric marker (``kind`` metric). Wavelength in nm and photon energy in eV side by side. ``spectra`` are
    ``scan_view.RoiSpectrum``; ``names`` their legend names."""
    header = ("line", "roi_id", "kind", "wavelength (nm)", "photon energy (eV)", "value", "error")
    rows: list[tuple[object, ...]] = []

    def energy(w: float) -> float:
        return hc_ev_nm / w if w else float("nan")

    for name, s in zip(names, spectra):
        for w, v, e in zip(np.asarray(s.wavelengths_nm, dtype=np.float64), np.asarray(s.values, dtype=np.float64), np.asarray(s.errors, dtype=np.float64)):
            rows.append((name, int(s.roi_id), "measured", w, energy(w), v, e))
        if s.fit_wavelengths_nm is not None and s.fit_values is not None:
            for w, v in zip(np.asarray(s.fit_wavelengths_nm, dtype=np.float64), np.asarray(s.fit_values, dtype=np.float64)):
                rows.append((name, int(s.roi_id), "fit", w, energy(w), v, None))
        if s.metric_wavelength_nm is not None and s.metric_value is not None:
            w = float(s.metric_wavelength_nm)
            rows.append((name, int(s.roi_id), "metric", w, energy(w), float(s.metric_value), None))
    return Table(header, tuple(rows))


def histogram_table(curves: Sequence[tuple[str, np.ndarray, np.ndarray]], x_label: str, y_label: str) -> Table:
    """Histogram curves as drawn (pyqtgraph step curves: ``n + 1`` bin edges, ``n`` values), long: one row per bin (``curve``, bin start, bin end,
    value). A curve whose x is not one longer than its y is written point by point (bin start = bin end = x)."""
    rows: list[tuple[object, ...]] = []
    for name, xs, ys in curves:
        xs, ys = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
        if xs.size == ys.size + 1:
            rows += [(name, float(a), float(b), float(v)) for a, b, v in zip(xs[:-1], xs[1:], ys)]
        else:
            rows += [(name, float(a), float(a), float(v)) for a, v in zip(xs, ys)]
    return Table(("curve", f"bin start {x_label}", f"bin end {x_label}", y_label), tuple(rows))


def curves_table(curves: Sequence[tuple[str, np.ndarray, np.ndarray]], x_label: str = "x", y_label: str = "y") -> Table:
    """Any plotted curves, long: one row per point (``curve``, x, y). For plots without a builder of their own (the Histogram)."""
    rows = tuple(
        (name, float(x), float(y)) for name, xs, ys in curves for x, y in zip(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64))
    )
    return Table(("curve", x_label, y_label), rows)
