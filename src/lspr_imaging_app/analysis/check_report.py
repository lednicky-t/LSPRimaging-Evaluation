"""What the data checks show the user (2026-11-07): one report shape for the Sensorgram's checks and the Image panel's motion check. Pure, no Qt.

A ``CheckReport`` is a title, a line saying what it was made from, the findings (`step_diagnostics.Finding`: ok / warn / info, a title, a sentence, the numbers behind it) and,
optionally, a table of numbers per step. The report window (`panels/check_report_window.py`) only draws one; copying and saving it (text, CSV, JSON) are functions here, so they
are tested without a window. **Nothing here changes a result**: a report only describes what the checks found.

Units: noise columns of the per-step table are shown in 1e-4 (absorbance or log10 intensity units), the metric noise in nm, motion in pixels.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .image_motion import DRIFT_LIMIT_PX, JITTER_LIMIT_PX, StepMotion
from .step_diagnostics import INFO, OK, WARN, DiagnosticsReport, Finding, StepNumbers

Cell = str | int | float

LEVEL_WORDS = {OK: "ok", WARN: "WARNING", INFO: "note"}


@dataclass(frozen=True)
class ReportTable:
    """A table of numbers per step: ``headers`` and ``rows`` (a cell is text, an integer or a float; NaN shows as a dash)."""

    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True)
class CheckReport:
    title: str
    subtitle: str
    findings: tuple[Finding, ...]
    table: ReportTable | None = None

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == WARN)

    @property
    def level(self) -> str:
        """``warn`` when any finding warns, else ``ok``."""
        return WARN if self.warnings else OK


def format_cell(cell: Cell) -> str:
    """A cell as shown on screen: three significant digits for a float, a dash for NaN."""
    if isinstance(cell, float):
        return "–" if math.isnan(cell) else f"{cell:.3g}"
    return str(cell)


# -- the Sensorgram's checks --------------------------------------------------------------------------------------------------------


def step_table(numbers: StepNumbers) -> ReportTable:
    """The per-step numbers the checks were made from (medians over the ROIs)."""
    headers = (
        "Step", "Cubes", "Metric noise (nm)", "Cube-to-cube (nm)", "Drift (noise SDs)", "Absorbance noise (1e-4)", "Sample ROI noise (1e-4)", "Reference ROI noise (1e-4)",
        "Sample-reference corr.", "Shared light (1e-4)", "ROI-ROI corr.",
    )
    rows = []
    for i, label in enumerate(numbers.labels):
        rows.append((
            label, int(numbers.n_cubes[i]), float(numbers.metric_noise_nm[i]), float(numbers.p2p_noise_nm[i]), float(numbers.drift_sigmas[i]),
            1e4 * float(numbers.absorbance_noise[i]), 1e4 * float(numbers.sample_noise[i]), 1e4 * float(numbers.reference_noise[i]), float(numbers.sample_reference_corr[i]),
            1e4 * float(numbers.shared_light[i]), float(numbers.roi_corr[i]),
        ))
    return ReportTable("Numbers per step (median over the ROIs)", headers, tuple(rows))


def diagnostics_report(report: DiagnosticsReport, subtitle: str) -> CheckReport:
    """The report of `step_diagnostics.run_diagnostics`."""
    table = None if report.steps is None else step_table(report.steps)
    return CheckReport("Data checks", subtitle, report.findings, table)


# -- the Image panel's motion check -------------------------------------------------------------------------------------------------

MOTION_WHY = "A sample ROI and its reference ROI sit at fixed pixels, so a moving image shifts what they cover: jitter adds noise, a drift adds a trend."


def motion_report(motions: Sequence[StepMotion], skipped: Sequence[str], wavelength_nm: float, subtitle: str) -> CheckReport:
    """Image motion per step. ``skipped``: labels of the steps that have too few cubes to measure."""
    findings: list[Finding] = []
    for m in motions:
        if m.flagged:
            findings.append(Finding(
                "motion", WARN, f"{m.label}: the image moves", f"{'; '.join(m.flagged)}. {MOTION_WHY}",
                {"step": m.label, "jitter_px": list(m.jitter_px), "range_px": list(m.range_px)},
            ))
    if motions and not findings:
        worst_jitter = max(max(m.jitter_px) for m in motions)
        worst_range = max(max(m.range_px) for m in motions)
        findings.append(Finding(
            "motion", OK, "The image does not move noticeably in any step",
            f"Largest cube-to-cube jitter {worst_jitter:.3f} px (limit {JITTER_LIMIT_PX} px), largest drift over a step {worst_range:.2f} px (limit {DRIFT_LIMIT_PX} px).",
            {"jitter_px": worst_jitter, "range_px": worst_range},
        ))
    if skipped:
        findings.append(Finding("motion", INFO, "Steps too short to measure", f"Fewer than 3 cubes: {', '.join(skipped)}.", {"steps": list(skipped)}))
    if not motions and not skipped:
        findings.append(Finding("motion", INFO, "No steps", "The motion is measured per experiment step: load or add the experimental plan first."))
    rows = tuple(
        (m.label, len(m.cube_indices), m.jitter_px[0], m.jitter_px[1], m.range_px[0], m.range_px[1], "; ".join(m.flagged) or "ok") for m in motions
    )
    table = ReportTable(
        f"Image motion at {wavelength_nm:g} nm (against the first cube of each step)",
        ("Step", "Cubes", "Jitter x (px)", "Jitter y (px)", "Range x (px)", "Range y (px)", "Result"), rows,
    ) if motions else None
    return CheckReport("Image motion", subtitle, tuple(findings), table)


# -- copying and saving ------------------------------------------------------------------------------------------------------------


def to_text(report: CheckReport) -> str:
    """The whole report as plain text (what Copy puts on the clipboard): the findings, then the table with tab-separated columns."""
    lines = [report.title, report.subtitle, ""] if report.subtitle else [report.title, ""]
    for f in report.findings:
        lines += [f"[{LEVEL_WORDS.get(f.level, f.level)}] {f.title}", f"    {f.detail}"]
    if report.table is not None:
        lines += ["", report.table.title, "\t".join(report.table.headers)]
        lines += ["\t".join(format_cell(c) for c in row) for row in report.table.rows]
    return "\n".join(lines) + "\n"


def to_csv(report: CheckReport) -> str:
    """The table, with the cells at full precision (empty when the report has none)."""
    if report.table is None:
        return ""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(report.table.headers)
    for row in report.table.rows:
        writer.writerow(["" if isinstance(c, float) and math.isnan(c) else repr(c) if isinstance(c, float) else c for c in row])
    return out.getvalue()


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


def to_json(report: CheckReport) -> str:
    """The findings with their numbers, and the table (NaN and infinities become null)."""
    payload = {
        "title": report.title,
        "subtitle": report.subtitle,
        "findings": [
            {"check": f.check, "level": f.level, "title": f.title, "detail": f.detail, "numbers": _jsonable(f.numbers)} for f in report.findings
        ],
        "table": None if report.table is None else {
            "title": report.table.title, "headers": list(report.table.headers), "rows": [_jsonable(list(row)) for row in report.table.rows],
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
