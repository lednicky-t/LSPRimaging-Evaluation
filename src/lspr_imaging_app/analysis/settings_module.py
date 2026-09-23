"""``AnalysisSettingsModule`` - the owner of the query layer's settings.

Built 2026-09-23, when the Formula/Fit/Metric layer was built. Before it,
the fit method, polynomial order, wavelength window and metric key had no
owner anywhere in the app: in the stable build they live in GUI combo
boxes, read back out through `window._analysis_metric_key()` and friends,
which is exactly the main-window coupling this rewrite exists to remove.
`StatisticsSettings` had no owner either - it was on the rewrite's own
"nothing persists them" still-open list.

**Why its own module rather than a field on something existing**
(maintainer's decision, 2026-09-23, chosen over extending
`AreaRoiDetectionSettings` or letting each panel own its own):

- `AreaRoiDetectionSettings` would have worked and would have been less
  code - `reduction_method`/`formula_key` are already there. But it would
  have put sensorgram smoothing and baseline windows inside an
  ROI-*detection* dataclass, regrowing exactly the grab-bag the September
  decomposition split into four.
- Panel ownership would have matched the "self-sufficient panels" idea,
  but the Sensorgram's metric is read off the *same fit* the Spectra panel
  configures - so one pipeline would have had two owners that must agree.

Both panels read this module; neither owns it.

**Nothing here is undo-tracked.** Every other settings module in this app
pushes an undo entry, so the departure is deliberate: the undo stack
records changes to the *experiment* - where a ROI is, what the crop is,
what the mask covers. These settings change only how already-measured
numbers are displayed, are all one click to put back, and interleaving
them into the same stack would mean Ctrl+Z after adjusting a smoothing
window silently rewinds a ROI edit instead. The same reasoning the app
already applies to `SelectionModule`, which is likewise not tracked.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from .settings import AnalysisSettingsChange, MetricSettings, StatisticsSettings


class AnalysisSettingsModule(QObject):
    """Owns `MetricSettings` and `StatisticsSettings` for the active
    dataset."""

    settings_changed = pyqtSignal(AnalysisSettingsChange)
    """See `AnalysisSettingsChange` - fires for a *derived*-value change.
    A subscriber drops its cached spectra/metrics; it must never start an
    analysis run in response (sketch §7: only explicit user action does
    that)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._metric = MetricSettings()
        self._statistics = StatisticsSettings()

    # -- query interface ----------------------------------------------------

    def metric_settings(self) -> MetricSettings:
        """A defensive copy - the caller's own, same guarantee every other
        module's `settings()` makes."""
        return replace(self._metric)

    def statistics_settings(self) -> StatisticsSettings:
        return replace(self._statistics)

    # -- commands -----------------------------------------------------------

    def set_metric_settings(self, settings: MetricSettings) -> None:
        """Replace layers 2-3's settings wholesale - one call per "Apply",
        matching `BackgroundModule.set_flatten_background_settings`' own
        grouping rather than one command per field. A no-op when nothing
        actually differs, the convention every command in this codebase
        follows."""
        new = replace(settings)
        if new == self._metric:
            return
        self._metric = new
        self.settings_changed.emit(AnalysisSettingsChange(field="metric"))

    def set_statistics_settings(self, settings: StatisticsSettings) -> None:
        new = replace(settings)
        if new == self._statistics:
            return
        self._statistics = new
        self.settings_changed.emit(AnalysisSettingsChange(field="statistics"))

    # -- session restore ----------------------------------------------------

    def restore_state(self, metric: MetricSettings, statistics: StatisticsSettings) -> None:
        """Replace both groups as a session load rather than a user edit -
        emits once, with `field="session_restored"`, so a subscriber
        caching only one group still knows both may have moved.

        Emits **unconditionally**, even when the restored values happen to
        equal the current ones, unlike the setters above. A restore is a
        statement about the world ("this is now the state"), not an edit,
        and a panel that skipped its redraw because the numbers happened to
        match would be showing the previous dataset's plot."""
        self._metric = replace(metric)
        self._statistics = replace(statistics)
        self.settings_changed.emit(AnalysisSettingsChange(field="session_restored"))
