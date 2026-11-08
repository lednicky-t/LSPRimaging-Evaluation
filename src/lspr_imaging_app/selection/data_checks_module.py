"""``DataChecksModule``: the Sensorgram's data-quality checks as a module of their own (2026-11-07).

**What it is.** The owner of "the checks that tell the user when the data behave systematically wrong" (`analysis/step_diagnostics.py`): which scan, steps and evaluation pair they are
run on, the run itself (off the GUI thread), the last report, and whether that report is still current. The Sensorgram's "Checks" button and the report window only show it
(`panels/sensorgram/checks_presenter.py`). Record: ``docs/snr_methods_comparison_2026-11-06.md``, "Decisions and what was built".

**Run on request** (the user's decision, like the step table): nothing is checked until the user asks (`run`). A run needs the stored scan and the experiment steps; the evaluation pair is
optional (without one only the step checks are made). It fits the metric traces of **all** ROIs of the scan (the Sensorgram's shared `TraceStore` holds them, so after the first run, or
after the Sensorgram or the ROI table have fitted them, a run costs milliseconds) and, when the metric is not the template shift, the template-shift traces too (for the "how much of the
shift does the method read" check; kept in this module, never in the shared store, which holds one key at a time).

**Out of date.** The report remembers what it was made from (the scan, the metric, the formula, the steps' boundaries, the ROI groups, the chosen pair). The status is worked out from
that every time it is asked: ``current`` while it still holds, ``stale`` after any change, so a change and its undo give back ``current``. A stale report is kept and marked, never hidden.

Nothing here changes a result: the checks read the scan, the traces and the plan.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.check_report import CheckReport, diagnostics_report
from ..analysis.scan_trace import fit_missing_traces, trace_key
from ..analysis.settings import MetricSettings
from ..analysis.step_diagnostics import run_diagnostics
from ..analysis.step_stats import StepMembership, assign_cubes
from ..analysis.trace_store import TraceStore
from ..applog import log_event
from .background_run import BackgroundRun
from .experiment_plan_module import REASON_DATASET, ExperimentPlanModule

if TYPE_CHECKING:  # only used in annotations
    import numpy as np

    from ..analysis.scan_results import ScanResultsModule
    from ..analysis.scan_store import StoredScan
    from ..analysis.settings_module import AnalysisSettingsModule
    from ..dataset.module import DatasetModule
    from ..roi import RoiToolbox

logger = logging.getLogger(__name__)

NOT_RUN, RUNNING, CURRENT, STALE, FAILED = "not_run", "running", "current", "stale", "failed"
_ROI_REASONS = {"detection_settings", "session_restored"}  # `geometry_changed` reasons that change the formula the traces are made with
_COSMETIC_REASONS = {"regroup", "session_restored"}  # `cosmetic_changed` reasons that change the ROI groups
_FIT_SHARE, _TEMPLATE_SHARE = 0.75, 0.15  # how much of the progress bar the two fits take; the checks themselves take the rest
_LABEL_CHARS = 40


@dataclass(frozen=True)
class _Outcome:
    """What a run hands back: the report (None when cancelled) and the traces it fitted, to be kept."""

    report: CheckReport | None
    stamp: tuple
    key: tuple
    new: dict
    template_key: tuple | None
    template_new: dict


def describe_metric(metric: MetricSettings) -> str:
    """The metric in a few words, for the report's header."""
    if metric.uses_template:
        first, last = metric.reference_cube_range()
        return f"template shift (reference cubes {first}-{last})"
    if metric.uses_response:
        base, signal = metric.response_ranges()
        return f"response shape (baseline cubes {base[0]}-{base[1]}, signal cubes {'not set' if signal is None else f'{signal[0]}-{signal[1]}'})"
    method = str(metric.fit_method).strip().lower()
    text = f"{method} fit" + (f" of order {int(metric.poly_order)}" if method == "polynomial" else "")
    if metric.metric_key == "centroid":
        part = f"centroid of the top {100 * float(metric.metric_top_fraction):g} %" if metric.metric_range == "top_fraction" else "centroid"
        return f"{text}, {part}"
    return f"{text}, {metric.metric_key}"


class DataChecksModule(QObject):
    state_changed = pyqtSignal()
    """The status, the report or the chosen pair changed: re-read `status`, `report`, `pair_choices`."""
    pair_changed = pyqtSignal(object)
    """The user chose another pair (its number, or None): for remembering the choice."""
    notice = pyqtSignal(str)
    """A plain sentence for the user: nothing can be checked, and why."""
    started = pyqtSignal(str)
    """A run began (message for the status bar's task indicator; the screen's `PanelTask` shows it)."""
    progress = pyqtSignal(float, str)
    """(fraction 0..1, message) while the run fits the ROIs."""
    finished = pyqtSignal(str, str)
    """("completed" | "failed" | "cancelled", message)."""

    def __init__(
        self,
        scan_results: ScanResultsModule,
        roi_toolbox: RoiToolbox,
        dataset: DatasetModule,
        analysis_settings: AnalysisSettingsModule,
        experiment_plan: ExperimentPlanModule,
        traces: TraceStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan_results
        self._toolbox = roi_toolbox
        self._dataset = dataset
        self._settings = analysis_settings
        self._plan = experiment_plan
        self._traces = traces
        self._chosen_pair: int | None = None
        self._report: CheckReport | None = None
        self._report_stamp: tuple | None = None
        self._failure: str | None = None
        self._scan_generation = 0
        self._renumbering = 0
        self._template: tuple[tuple, dict] | None = None  # (trace key, traces): the template-shift traces of the last run
        self._run = BackgroundRun("data-checks", self)
        self._run.done.connect(self._on_done)
        self._run.progress.connect(self.progress)
        scan_results.results_changed.connect(self._on_results_changed)
        analysis_settings.settings_changed.connect(lambda _change: self.state_changed.emit())
        experiment_plan.steps_changed.connect(self._on_plan_changed)
        roi_toolbox.geometry_changed.connect(self._on_roi_changed)
        roi_toolbox.cosmetic_changed.connect(self._on_roi_cosmetic)
        roi_toolbox.roi_ids_renumbered.connect(self._on_renumbered)

    def has_steps(self) -> bool:
        """The dataset has experiment steps (the checks compare steps; without them the Checks button only explains)."""
        return self._plan.has_steps()

    # -- the evaluation pair ----------------------------------------------------------------------------------------------------

    def pair_choices(self) -> list[tuple[int, str]]:
        """The complete evaluation pairs (both steps set and present) as (number, text for a menu)."""
        out = []
        for pair in self._plan.state().pairs:
            baseline, signal = self._plan.step_by_ref(pair.baseline), self._plan.step_by_ref(pair.signal)
            if baseline is not None and signal is not None:
                n = pair.number
                out.append((n, f"Pair {n}: B{n} = {baseline.order} · {_short(baseline.label)}, S{n} = {signal.order} · {_short(signal.label)}"))
        return out

    def pair(self) -> int | None:
        """The pair the checks compare: the chosen one if it is complete, else the first complete one, else None (the step checks only)."""
        choices = [n for n, _text in self.pair_choices()]
        if self._chosen_pair in choices:
            return self._chosen_pair
        return choices[0] if choices else None

    def set_pair(self, number: int | None) -> None:
        """Choose the pair (a remembered choice at launch, or the user's pick). The report of another pair is out of date."""
        if number == self._chosen_pair:
            return
        self._chosen_pair = number
        self.pair_changed.emit(number)
        self.state_changed.emit()

    # -- the report -------------------------------------------------------------------------------------------------------------

    def report(self) -> CheckReport | None:
        return self._report

    def failure(self) -> str | None:
        """Why the last run failed (None when it did not)."""
        return self._failure

    def status(self) -> str:
        if self._run.is_running():
            return RUNNING
        if self._failure is not None:
            return FAILED
        if self._report is None:
            return NOT_RUN
        return CURRENT if self._stamp() == self._report_stamp else STALE

    def is_running(self) -> bool:
        return self._run.is_running()

    def clear(self) -> None:
        """Another dataset: drop the report (it describes another experiment) and stop a run."""
        self._run.cancel()
        self._report, self._report_stamp, self._failure, self._template = None, None, None, None
        self.state_changed.emit()

    # -- what the report was made from -------------------------------------------------------------------------------------------

    def _group_members(self) -> tuple[tuple[int, ...], ...]:
        return tuple(tuple(g.area_roi_ids) for g in self._toolbox.groups())

    def _stamp(self) -> tuple | None:
        stored = self._scan.stored()
        if stored is None:
            return None
        pair = next((p for p in self._plan.state().pairs if p.number == self.pair()), None)
        steps = tuple((s.ref, s.start_unix_ms, s.end_unix_ms) for s in self._plan.steps())
        groups = self._group_members()
        key = trace_key(stored, self._toolbox.detection_settings().formula_key, self._settings.metric_settings(), groups)
        return (self._scan_generation, self._renumbering, key, steps, groups, None if pair is None else (pair.baseline, pair.signal))

    def _on_results_changed(self) -> None:
        self._scan_generation += 1
        self.state_changed.emit()

    def _on_renumbered(self, _mapping: object) -> None:
        self._renumbering += 1
        self.state_changed.emit()

    def _on_roi_changed(self, change: object) -> None:
        if getattr(change, "reason", "") in _ROI_REASONS:
            self.state_changed.emit()

    def _on_roi_cosmetic(self, change: object) -> None:
        if getattr(change, "reason", "") in _COSMETIC_REASONS:
            self.state_changed.emit()

    def _on_plan_changed(self, reason: str = "") -> None:
        if reason == REASON_DATASET:
            self.clear()
        else:
            self.state_changed.emit()  # a boundary, a role (which steps the pair means), the steps themselves: the report is judged against them in `status`

    # -- running ----------------------------------------------------------------------------------------------------------------

    def run(self) -> bool:
        """Check the data now, off the GUI thread. True if a run was started; False, with a `notice`, if there is nothing to check."""
        stored = self._scan.stored()
        if stored is None:
            self.notice.emit("No scan yet. Run it in the Image panel's Analysis tab.")
            return False
        steps = self._plan.steps()
        if not steps:
            self.notice.emit("This dataset has no experiment steps: the checks compare steps. Import the measurement notes in Workflow > Experimental plan.")
            return False
        membership = assign_cubes(steps, *self._dataset.cube_spans_unix_ms(stored.cube_indices))
        formula = self._toolbox.detection_settings().formula_key
        metric = self._settings.metric_settings()
        groups = self._group_members()
        pair_number = self.pair()
        positions, pair_text = self._pair_positions(membership, pair_number)
        key = trace_key(stored, formula, metric, groups)
        template_metric = None if metric.uses_template or positions is None else replace(metric, fit_method="template")
        template_key = None if template_metric is None else trace_key(stored, formula, template_metric)
        known = self._traces.known(key)
        known_template = dict(self._template[1]) if template_key is not None and self._template is not None and self._template[0] == template_key else {}
        names = tuple((g.name, tuple(g.area_roi_ids)) for g in self._toolbox.groups())
        subtitle = (
            f"{len(stored.roi_ids)} ROIs, {len(stored.cube_indices)} cubes, {len(membership.steps)} steps. Metric: {describe_metric(metric)}. {pair_text} "
            f"Checked at {datetime.now():%H:%M}."
        )
        stamp = self._stamp() or ()
        work = self._work(stored, membership, formula, metric, template_metric, key, template_key, known, known_template, groups, names, positions, subtitle, stamp)
        self._traces.warm_up()
        self._failure = None
        self._run.start(work)
        self.started.emit("checking the data")
        self.state_changed.emit()
        return True

    def cancel(self) -> None:
        self._run.cancel()

    def shutdown(self) -> None:
        self._run.shutdown()

    def _pair_positions(self, membership: StepMembership, number: int | None) -> tuple[tuple[int, int] | None, str]:
        """(positions of the baseline and signal step in the membership, a sentence naming them), or (None, why not)."""
        pair = next((p for p in self._plan.state().pairs if p.number == number), None)
        if pair is None:
            return None, "No evaluation pair is set (Experimental plan > Evaluation), so only the step checks were made."
        found = []
        for ref in (pair.baseline, pair.signal):
            found.append(next((i for i, s in enumerate(membership.steps) if s.ref == ref), None))
        if None in found:
            return None, "The evaluation pair's steps are not in the plan, so only the step checks were made."
        b, s = found
        n = pair.number
        return (b, s), f"Pair {n}: B{n} = {_short(membership.steps[b].label)}, S{n} = {_short(membership.steps[s].label)}."

    def _work(
        self, stored: StoredScan, membership: StepMembership, formula: str, metric: MetricSettings, template_metric: MetricSettings | None, key: tuple,
        template_key: tuple | None, known: dict, known_template: dict, groups: tuple, names: tuple, positions: tuple[int, int] | None, subtitle: str, stamp: tuple,
    ) -> Callable[[Callable[[], bool], Callable[[float, str], None]], _Outcome]:
        runner = self._traces.runner()
        roi_ids = tuple(int(r) for r in stored.roi_ids)

        def work(cancelled: Callable[[], bool], report: Callable[[float, str], None]) -> _Outcome:
            t0 = time.perf_counter()

            def scaled(start: float, share: float, label: str) -> Callable[[int, int], None]:
                return lambda done, total: report(start + share * (done / total if total else 0.0), f"{label} {done} of {total}")

            new, was_cancelled = fit_missing_traces(
                stored, roi_ids, formula, metric, known, cancelled=cancelled, progress=scaled(0.0, _FIT_SHARE, "fitting ROI"), runner=runner, groups=groups,
            )
            template_new: dict = {}
            if not was_cancelled and template_metric is not None:
                template_new, was_cancelled = fit_missing_traces(
                    stored, roi_ids, formula, template_metric, known_template, cancelled=cancelled, progress=scaled(_FIT_SHARE, _TEMPLATE_SHARE, "template shift, ROI"), runner=runner,
                )
            if was_cancelled:
                return _Outcome(None, stamp, key, new, template_key, template_new)
            report(_FIT_SHARE + _TEMPLATE_SHARE, "checking")
            traces: dict[int, np.ndarray] = {**known, **new}
            template_traces = {**known_template, **template_new} if template_metric is not None else None
            result = run_diagnostics(
                stored, formula, traces, membership, pair=positions, template_traces=template_traces, groups=names, method_is_shape=metric.uses_response,
            )
            log_event(
                logger, "data_checks.run", logging.DEBUG, rois=len(roi_ids), fitted=len(new), template_fitted=len(template_new),
                findings=len(result.findings), total_ms=round((time.perf_counter() - t0) * 1e3, 1),
            )
            return _Outcome(diagnostics_report(result, subtitle), stamp, key, new, template_key, template_new)

        return work

    def _on_done(self, token: object, outcome: object, error: object) -> None:
        if isinstance(outcome, _Outcome):
            if outcome.new:
                self._traces.add(outcome.key, outcome.new)  # kept even from a cancelled or replaced run (ignored if the fit settings have moved on)
            if outcome.template_key is not None and outcome.template_new:
                same = self._template is not None and self._template[0] == outcome.template_key
                self._template = (outcome.template_key, {**(self._template[1] if same else {}), **outcome.template_new})
        if not self._run.is_current(token):
            return  # replaced: the newer run reports
        if isinstance(error, BaseException):
            self._failure = f"{type(error).__name__}: {error}"
            self.finished.emit("failed", self._failure)
        elif not isinstance(outcome, _Outcome) or outcome.report is None:
            self.finished.emit("cancelled", "")
        else:
            self._report, self._report_stamp, self._failure = outcome.report, outcome.stamp, None
            self.finished.emit("completed", f"{outcome.report.warnings} warning{'s' if outcome.report.warnings != 1 else ''}")
        self.state_changed.emit()


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _LABEL_CHARS else text[: _LABEL_CHARS - 1] + "…"
