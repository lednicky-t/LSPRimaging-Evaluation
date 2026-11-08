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

No Qt import allowed in this file (CLAUDE.md testing rule) - the QObject
that owns these lives in `settings_module.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

FIT_METHODS: tuple[str, ...] = ("none", "polynomial", "gaussian", "voigt", "template", "response")
"""How a ROI's formula spectrum becomes a curve before the metric is read
off it. ``"none"`` reads the metric straight off the measured points - a
real option, not a disabled state (see `query.metric_from_spectrum`).
``"template"`` (2026-10-25) is not a curve with a top to find: the spectrum is compared, point by point over its whole range, with the reference spectrum
of the same ROI and the sideways shift that fits best is the metric (`template_shift.py`). It has no fit window, order or maximum / centroid choice.
``"voigt"`` (2026-11-02): a Voigt peak (Gaussian convolved with Lorentzian) on a flat offset, fitted in photon energy (`voigt_fit.py`).
``"response"`` (2026-11-06): the response shape (`response_shape.py`): each spectrum is projected on the change measured between a baseline and a signal cube
range, weighted by the noise at each wavelength, learned per ROI group. Not a peak position: a shift-equivalent in nm. No fit window, order or metric choice."""

METRIC_KEYS: tuple[str, ...] = ("maximum", "centroid")
"""Which number a fitted (or unfitted) spectrum collapses to - layer 3's
output, one value per (ROI, cube), which is what a sensorgram plots."""

WINDOW_MODES: tuple[str, ...] = ("manual", "around_max", "fwhm")
RELATIVE_WINDOW_MODES: tuple[str, ...] = ("around_max", "fwhm")
"""How the fit window is found. "manual": a fixed range for every spectrum. The other two give each spectrum (each ROI at each cube) its own
window around its own maximum, so it follows a peak that shifts during the measurement."""

METRIC_RANGE_MODES: tuple[str, ...] = ("off", "top_fraction", "manual")
RANGE_MODES: tuple[str, ...] = ("top_fraction", "manual")
"""Which part of the fitted curve the centroid is read from: "off" = all of it; the other two are described at ``MetricSettings``."""

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

    The fit window (``fit_wl_min`` .. ``fit_wl_max``, or one found per spectrum: see the comment below) says which points the fit sees; the
    metric is read off that fit. The centroid can be read from part of the fitted curve only (the "metric range", below). `None` on either side
    of the manual window means "no bound there", not "no window" - a one-sided window is a legitimate setting."""

    fit_method: str = "polynomial"
    poly_order: int = 3
    fit_wl_min: float | None = None
    fit_wl_max: float | None = None
    metric_key: str = "centroid"
    window_on: bool = False
    window_mode: str = "manual"
    window_half_width_nm: float = 50.0
    window_fwhm_factor: float = 0.5
    window_same_points: bool = False
    metric_range: str = "top_fraction"
    metric_top_fraction: float = 0.2
    metric_range_min: float | None = None
    metric_range_max: float | None = None
    template_first_cube: int = 0
    template_last_cube: int = 9
    response_base_first: int = 0
    response_base_last: int = 9
    response_signal_first: int | None = None
    response_signal_last: int | None = None

    # The calibration cubes of the response shape (2026-11-06, ``response_shape.py``): the baseline cubes ``response_base_first`` ..
    # ``response_base_last`` and the signal cubes ``response_signal_first`` .. ``response_signal_last`` (inclusive, the cube numbers the panels show).
    # The shape is the mean change signal - baseline. Read only when ``fit_method == "response"``; no signal range = not calibrated (no values).

    # The reference cubes of the template shift (2026-10-25): the cubes ``template_first_cube`` .. ``template_last_cube`` (inclusive, the cube numbers the
    # Spectra panel and the Image panel show) whose mean spectrum, per ROI, is the reference the others are compared with. Read only when
    # ``fit_method == "template"``; the default is the first ten cubes. They belong with the dataset (this block is saved with the session).

    # The fit window (2026-10-25). ``window_mode`` says how it is found: "manual" = the range ``fit_wl_min`` .. ``fit_wl_max`` (dragged on the plot;
    # it is on when a bound is set, as it always was), "around_max" = every spectrum's own maximum +/- ``window_half_width_nm``, "fwhm" = every
    # spectrum's own maximum +/- ``window_fwhm_factor`` x its FWHM. The two per-spectrum modes are on when ``window_on`` is True (the "Set" box).
    # ``window_same_points`` makes a per-spectrum window always hold the same number of points. Only these methods may read the window fields, so
    # every path (Spectra panel, Sensorgram batch, caches) agrees.

    # The metric range (2026-10-25): which part of the FITTED curve the centroid is read from. "off" = the whole fitted curve (the fit window), as it
    # always was. "top_fraction" = only where the curve is above ``base + f (top - base)``, ``f`` = ``metric_top_fraction``, each point weighted by its
    # height above that level. "manual" = between ``metric_range_min`` and ``metric_range_max``, weighted by the height above the baseline. Applies to the
    # centroid of a fitted curve only (the maximum has no range; with "No fit" there is no curve). See ``metric_range.py``.

    def manual_bounds(self) -> tuple[float | None, float | None]:
        """The fixed range, or (None, None) when the window is found per spectrum."""
        if self.window_mode == "manual":
            return self.fit_wl_min, self.fit_wl_max
        return None, None

    @property
    def relative_window(self) -> bool:
        """True when each spectrum gets its own window (``fit_window.window_bounds`` finds it)."""
        return self.window_on and self.window_mode in RELATIVE_WINDOW_MODES

    @property
    def ranged_centroid(self) -> bool:
        """True when the centroid is read from part of the fitted curve."""
        return self.metric_key == "centroid" and self.metric_range in RANGE_MODES

    def metric_range_key(self) -> tuple:
        """Everything that decides which part of the fitted curve the metric is read from (cache keys use this)."""
        if not self.ranged_centroid:
            return ("whole",)
        if self.metric_range == "top_fraction":
            return ("top_fraction", float(self.metric_top_fraction))
        return ("manual", self.metric_range_min, self.metric_range_max)

    @property
    def uses_template(self) -> bool:
        """True when the metric is the shift against a reference spectrum (the window, order and metric choice are not read then)."""
        return str(self.fit_method).strip().lower() == "template"

    def template_key(self) -> tuple:
        """The reference cubes when they matter (the template shift), else an empty tuple: part of every cache key, so a changed range never shows a stale trace."""
        if not self.uses_template:
            return ()
        first, last = int(self.template_first_cube), int(self.template_last_cube)
        return ("template", min(first, last), max(first, last))

    @property
    def uses_response(self) -> bool:
        """True when the metric is the response-shape readout (needs the ROI groups and every ROI of the scan, not one ROI alone)."""
        return str(self.fit_method).strip().lower() == "response"

    def response_ranges(self) -> tuple[tuple[int, int], tuple[int, int] | None]:
        """(baseline first, last), (signal first, last) or None when the signal range is not set; each in order whatever was typed."""
        b0, b1 = int(self.response_base_first), int(self.response_base_last)
        base = (min(b0, b1), max(b0, b1))
        if self.response_signal_first is None or self.response_signal_last is None:
            return base, None
        s0, s1 = int(self.response_signal_first), int(self.response_signal_last)
        return base, (min(s0, s1), max(s0, s1))

    def response_key(self) -> tuple:
        """The calibration cubes when they matter (the response shape), else an empty tuple: part of every cache key."""
        if not self.uses_response:
            return ()
        return ("response", *self.response_ranges())

    def reference_cube_range(self) -> tuple[int, int]:
        """The reference cubes as (first, last), in order whatever was typed."""
        first, last = int(self.template_first_cube), int(self.template_last_cube)
        return min(first, last), max(first, last)

    def window_key(self) -> tuple:
        """Everything that decides which points are fitted, as one comparable tuple (cache keys use this, not the fields)."""
        low, high = self.manual_bounds()
        if self.relative_window:
            amount = self.window_half_width_nm if self.window_mode == "around_max" else self.window_fwhm_factor
            return (self.window_mode, float(amount), bool(self.window_same_points))
        return ("manual", low, high)


# Defaults changed on 2026-11-06 (centroid of the top 20 % instead of the maximum; docs/snr_methods_comparison_2026-11-06.md): same SNR as the all-points
# centroid, but it reads the full shift. A saved session fills the keys it lacks from these OLD defaults, so a session saved before a key existed
# gives the numbers it always gave; only a new session gets the new defaults.
LEGACY_METRIC_DEFAULTS = MetricSettings(metric_key="maximum", metric_range="off", metric_top_fraction=0.3)


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
    smooth_within_steps: bool = True  # (2026-10-26) smooth each experiment step on its own, so the smoother never mixes two solutions; no steps: the whole trace
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
