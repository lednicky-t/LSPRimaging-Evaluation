"""Settings for the query layer - Pillar I's layers 2-3 and Pillar II
(`docs/analysis_pipeline_layers.md`).

These are the settings that **never invalidate anything on disk**. Sketch
§6 is explicit: "Formula, Fit method, Metric choice - never touch the
stored cells at all; computed live from whatever's already in the store."
`compute_cell` deliberately stores raw reduced (sample, reference) pairs
precisely so that changing any of this is a re-derivation rather than a
recompute, and none of it appears in a `SettingsSnapshot`.

`formula_key` is the one member of that family **not** here: it already
lives in `AreaRoiDetectionSettings` next to `reduction_method`, where the
old app put it, and moving it would be a second change riding along with
this one. Noted as the one split seam in an otherwise clean separation.

No Qt import allowed in this file (AGENTS.md testing rule) - the QObject
that owns these lives in `settings_module.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

FIT_METHODS: tuple[str, ...] = ("none", "polynomial", "gaussian")
"""How a ROI's formula spectrum becomes a curve before the metric is read
off it. ``"none"`` reads the metric straight off the measured points - a
real option, not a disabled state (see `query.metric_from_spectrum`)."""

METRIC_KEYS: tuple[str, ...] = ("maximum", "centroid")
"""Which number a fitted (or unfitted) spectrum collapses to - layer 3's
output, one value per (ROI, cube), which is what a sensorgram plots."""

SMOOTHING_METHODS: tuple[str, ...] = ("none", "savgol", "moving_average")
SPIKE_REJECTION_METHODS: tuple[str, ...] = ("hampel", "running_median")

SENSORGRAM_DISPLAY_MODES: tuple[str, ...] = ("individual", "average_all", "average_by_group")
"""The three modes that replaced the old app's implicit "combined" trace.
That one averaged each ROI's absorbance *per wavelength* and then fitted
the metric once on the averaged spectrum - a layer-2 average standing in
for a layer-3 one. For a nonlinear fit those are not the same number, and
`docs/analysis_pipeline_layers.md` ("The one gap this closes") records the
correction: only ever average already-fitted per-ROI values."""

SENSORGRAM_AGGREGATIONS: tuple[str, ...] = ("mean", "median")
SENSORGRAM_BANDS: tuple[str, ...] = ("sd", "sem")


@dataclass(frozen=True)
class AnalysisSettingsChange:
    """**A third change category, deliberately neither of the existing
    two** (`change_events.py` defines cosmetic vs computational).

    - A *computational* change means stored cells may be stale, and the
      planner will recompute them. These settings never can - nothing they
      touch is on disk.
    - A *cosmetic* change means "redraw, the numbers are unchanged". These
      are not that either: changing the metric from maximum to centroid
      changes every plotted value.

    So: nothing on disk is invalidated, every *derived* value in memory is.
    A subscriber must drop its cached spectra/metrics and re-derive, and
    must **not** trigger an analysis run.

    `field` names which settings group changed (`"metric"` or
    `"statistics"`), for logging and for a subscriber that only caches one
    of the two - `"session_restored"` means both were replaced at once."""

    field: str  # "metric" | "statistics" | "session_restored"


@dataclass(slots=True)
class MetricSettings:
    """Layers 2-3: how one ROI's stored (sample, reference) pairs become a
    spectrum, a fitted curve, and finally one number per spectral cube.

    `fit_wl_min`/`fit_wl_max` restrict *both* the fit and the metric search
    to a wavelength window. `None` on either side means "no bound there",
    not "no window" - a one-sided window is a legitimate setting."""

    fit_method: str = "polynomial"
    poly_order: int = 3
    fit_wl_min: float | None = None
    fit_wl_max: float | None = None
    metric_key: str = "maximum"


@dataclass(slots=True)
class StatisticsSettings:
    """Pillar II: post-processing applied to an already-computed metric
    trace. Ported field-for-field from the old app's `StatisticsSettings`,
    including its documented ordering.

    **Order matters and is fixed**: spike rejection first (so a transient
    outlier is removed before smoothing can blend it into its neighbours),
    then smoothing, then the baseline offset last.

    `baseline_window_start`/`end` are in the trace's own x-axis units -
    elapsed seconds when acquisition timing metadata is available, raw
    spectral-cube index otherwise. A window that selects no points yields a
    NaN baseline and an unchanged trace rather than an error: an empty
    window is a routine user mistake that has to be visible, not fatal.

    Persisted with the session rather than as a per-machine preference, for
    the reason the old app's own docstring gives: these reshape what the
    plotted numbers *mean* (a baseline-corrected trace reads as relative
    shift, not absolute value), so they belong with the dataset."""

    smoothing_method: str = "none"
    smoothing_window: int = 15
    smoothing_polyorder: int = 2
    spike_rejection_enabled: bool = False
    spike_rejection_method: str = "hampel"
    spike_rejection_window: int = 5
    spike_rejection_threshold: float = 3.5
    baseline_enabled: bool = False
    baseline_window_start: float | None = None
    baseline_window_end: float | None = None
    sensorgram_display_mode: str = "individual"
    sensorgram_aggregation: str = "mean"
    sensorgram_band: str = "sd"
