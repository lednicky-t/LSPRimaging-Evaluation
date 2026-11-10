"""The signal map (2026-11-10): every ROI on the Image panel coloured by how much it changed. Pure (numpy only): no Qt, no files, nothing stored.

Screen purpose (maintainer, 2026-11-10): instead of reading the response off the Sensorgram line by line, see it on the image: which ROIs respond, how much, and where on the
chip. The numbers are the ROI / Groups table's step numbers, which are the Sensorgram's lines as drawn (``step_table.py``: same fit, smoothing, drift baseline, Subtract
switch); ``selection/signal_map_module.py`` reads them and hands one value per ROI to this file. This file only turns values into colours and says what they are.

* **What is shown** (:data:`SHOWS`): the shift of the signal step from the baseline step of an evaluation pair, ``mean(S) - mean(B)`` (nm, default); that shift divided by
  the noise of the baseline step (S/N); or the mean of one step, which reads as a shift from the baseline when the Sensorgram's Subtract switch is on and as a position
  when it is off (the numbers are the lines as drawn, so the switch decides; nothing here re-derives it).
* **Colour schemes** (:data:`SCHEMES`): two diverging ones (white in the middle: for signed values around zero) and four sequential ones (perceptually even). Each is
  a few RGB stops interpolated linearly, close enough to the published maps for a display (not used for any number).
* **Range** (:func:`value_range`): the values that map to the two ends of the scheme. Auto = symmetric around zero when the values have both signs, else the 2nd to 98th
  percentile; one outlier ROI never washes out the rest. Values outside the range take the end colour (clipped), and the legend says so.

A ROI with no value is grey (:data:`NO_VALUE_HEX`). A map from numbers that are out of date is drawn with its colours mixed half-way to grey (:func:`fade`), never hidden.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .export_tables import Table

SHOW_SHIFT = "shift"
SHOW_SNR = "snr"
SHOW_STEP = "step"
SHOWS: tuple[tuple[str, str, str], ...] = (
    (SHOW_SHIFT, "Shift S − B", "Mean of the signal step minus mean of the baseline step of the evaluation pair (nm): how far each ROI moved."),
    (SHOW_SNR, "S/N (S − B) / σ_B", "The shift divided by the noise of the baseline step (SD about a straight line through it): how clearly each ROI moved."),
    (SHOW_STEP, "One step's mean", "Mean of one step. With the Sensorgram's Subtract switch on it is the shift from the baseline; off, the position itself (nm)."),
)
DEFAULT_SHOW = SHOW_SHIFT

PER_ROI = "roi"
PER_GROUP = "group"
PERS: tuple[tuple[str, str, str], ...] = (
    (PER_ROI, "Each ROI", "Every ROI gets its own colour from its own line."),
    (PER_GROUP, "By group", "Every ROI of a group gets the group's colour: the value of the group's averaged line (all its ROIs). ROIs in no group share the 'Ungrouped' line."),
)
DEFAULT_PER = PER_ROI

RANGE_AUTO = "auto"
RANGE_ZERO = "zero"
RANGE_DATA = "data"
RANGE_FIXED = "fixed"
RANGES: tuple[tuple[str, str, str], ...] = (
    (RANGE_AUTO, "Auto", "Symmetric around zero when the values have both signs, otherwise from the 2nd to the 98th percentile."),
    (RANGE_ZERO, "Around zero", "From −m to +m, m = the larger of |2nd percentile| and |98th percentile|: zero is always the middle colour."),
    (RANGE_DATA, "Data (2–98 %)", "From the 2nd to the 98th percentile of the values."),
    (RANGE_FIXED, "Fixed", "The minimum and maximum you type (the same colours for every dataset)."),
)
DEFAULT_RANGE = RANGE_AUTO
PERCENTILES = (2.0, 98.0)

NO_VALUE_HEX = "#6b6b6b"
_FADE_GREY = (128, 128, 128)
FADE_FRACTION = 0.5  # an out-of-date map: colours mixed this far towards grey


@dataclass(frozen=True)
class Scheme:
    key: str
    label: str
    diverging: bool
    stops: tuple[tuple[float, tuple[int, int, int]], ...]


SCHEMES: tuple[Scheme, ...] = (
    Scheme("rdbu", "Blue – white – red", True, (
        (0.0, (33, 102, 172)), (0.25, (103, 169, 207)), (0.5, (247, 247, 247)), (0.75, (239, 138, 98)), (1.0, (178, 24, 43)),
    )),
    Scheme("puor", "Purple – white – orange", True, (
        (0.0, (84, 39, 136)), (0.25, (153, 142, 195)), (0.5, (247, 247, 247)), (0.75, (241, 163, 64)), (1.0, (179, 88, 6)),
    )),
    Scheme("viridis", "Viridis", False, (
        (0.0, (68, 1, 84)), (0.25, (59, 82, 139)), (0.5, (33, 144, 141)), (0.75, (93, 200, 99)), (1.0, (253, 231, 37)),
    )),
    Scheme("cividis", "Cividis", False, (
        (0.0, (0, 34, 78)), (0.25, (64, 77, 107)), (0.5, (124, 123, 120)), (0.75, (188, 175, 111)), (1.0, (254, 232, 56)),
    )),
    Scheme("magma", "Magma", False, (
        (0.0, (0, 0, 4)), (0.25, (81, 18, 124)), (0.5, (183, 55, 121)), (0.75, (252, 137, 97)), (1.0, (252, 253, 191)),
    )),
    Scheme("inferno", "Inferno", False, (
        (0.0, (0, 0, 4)), (0.25, (87, 16, 110)), (0.5, (188, 55, 84)), (0.75, (249, 142, 9)), (1.0, (252, 255, 164)),
    )),
)
DEFAULT_SCHEME = "rdbu"


def scheme(key: str) -> Scheme:
    """The scheme named ``key``; the default one for an unknown key (a remembered key from a later version)."""
    return next((s for s in SCHEMES if s.key == key), next(s for s in SCHEMES if s.key == DEFAULT_SCHEME))


def scheme_rgb(fractions: np.ndarray, key: str) -> np.ndarray:
    """``uint8 [n, 3]``: the colour of each fraction (0 = low end .. 1 = high end, clipped) in the scheme ``key``. NaN gives the low end; the caller greys it."""
    s = scheme(key)
    stops = [p for p, _ in s.stops]
    clipped = np.clip(np.nan_to_num(np.asarray(fractions, dtype=np.float64), nan=0.0), 0.0, 1.0)
    out = np.empty(clipped.shape + (3,), dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = np.rint(np.interp(clipped, stops, [rgb[channel] for _, rgb in s.stops])).astype(np.uint8)
    return out


def value_range(values: np.ndarray, mode: str = DEFAULT_RANGE, fixed: tuple[float, float] = (0.0, 1.0)) -> tuple[float, float]:
    """``(low, high)``: the values at the two ends of the scheme. NaN values are ignored; no finite value gives ``(nan, nan)``. ``fixed`` is used as given for
    `RANGE_FIXED` (sorted, so a swapped pair still works). A range of zero width is widened a little, so that one value (or many equal ones) has a colour."""
    if mode == RANGE_FIXED:
        low, high = sorted((float(fixed[0]), float(fixed[1])))
        return _widen(low, high)
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return math.nan, math.nan
    p_low, p_high = (float(v) for v in np.percentile(finite, PERCENTILES))
    both_signs = bool((finite < 0).any() and (finite > 0).any())
    if mode == RANGE_ZERO or (mode == RANGE_AUTO and both_signs):
        m = max(abs(p_low), abs(p_high))
        return _widen(-m, m)
    return _widen(p_low, p_high)


def _widen(low: float, high: float) -> tuple[float, float]:
    if high > low:
        return low, high
    pad = abs(low) * 0.01 if low != 0.0 else 1e-6
    return low - pad, high + pad


def fade(rgb: np.ndarray, fraction: float = FADE_FRACTION) -> np.ndarray:
    """``rgb`` mixed ``fraction`` of the way to grey: the look of an out-of-date map."""
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.rint(rgb * (1.0 - fraction) + np.asarray(_FADE_GREY, dtype=np.float64) * fraction).astype(np.uint8)


def to_hex(rgb: np.ndarray) -> list[str]:
    return [f"#{int(r):02x}{int(g):02x}{int(b):02x}" for r, g, b in np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)]


def colours_for(values: np.ndarray, low: float, high: float, key: str, *, stale: bool = False) -> list[str]:
    """One ``'#rrggbb'`` per value: its place between ``low`` and ``high`` in the scheme ``key`` (clipped at the ends), :data:`NO_VALUE_HEX` for NaN; faded when
    ``stale``."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return []
    span = high - low
    fractions = (values - low) / span if math.isfinite(span) and span > 0 else np.full(values.shape, 0.5)
    rgb = scheme_rgb(fractions, key)
    if stale:
        rgb = fade(rgb)
    out = to_hex(rgb)
    for i in np.flatnonzero(~np.isfinite(values)):
        out[int(i)] = NO_VALUE_HEX
    return out


@dataclass(frozen=True)
class SignalMapView:
    """What the Image panel draws for the map, ready-made: one colour per ROI, the legend, and the numbers for the hover text and the export.

    ``revision`` changes whenever anything drawn changes (the panel compares it, not the dictionaries). ``values`` / ``spreads`` are per ROI id (NaN = none; the spread is
    the SD the table shows for the same cell). ``source`` says where a ROI's value came from: ``"ROI 12"`` or ``"group A"``. ``stale``: the numbers were computed from
    inputs that changed since (drawn faded). ``missing``: ROIs with no number yet."""

    revision: int
    colors: Mapping[int, str]
    values: Mapping[int, float]
    spreads: Mapping[int, float]
    source: Mapping[int, str]
    title: str
    unit: str
    scheme: str
    low: float
    high: float
    stale: bool = False
    missing: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def decimals(self) -> int:
        return 3 if self.unit else 2

    def describe(self, roi_id: int) -> str:
        """The hover text of one ROI: its value (and spread) with what it is; empty if the ROI is not in the map."""
        if roi_id not in self.values:
            return ""
        value, spread, d = self.values[roi_id], self.spreads.get(roi_id, math.nan), self.decimals()
        unit = f" {self.unit}" if self.unit else ""
        source = self.source.get(roi_id, f"ROI {roi_id}")
        head = f"ROI {roi_id}" + (f" ({source})" if source != f"ROI {roi_id}" else "")
        if not math.isfinite(value):
            return f"{head}\n{self.title}: no value yet"
        text = f"{head}\n{self.title}: {value:.{d}f}{unit}"
        if math.isfinite(spread):
            text += f" (SD {spread:.{d}f}{unit})"
        if self.stale:
            text += "\nOut of date: press Refresh in Analysis > Signal map."
        return text


def signal_map_table(view: SignalMapView, roi_ids: Sequence[int]) -> Table:
    """The map as a CSV table: one row per ROI (in ``roi_ids`` order): where its value came from, the value and SD in the map's unit, the colour drawn, and whether
    the numbers are out of date. Out-of-date numbers are written and marked (``notes``), never left out."""
    unit = f" ({view.unit})" if view.unit else ""
    header = ("roi_id", "from", f"{view.title}{unit}", f"sd{unit}", "colour", "out_of_date")
    rows = tuple(
        (int(r), view.source.get(int(r), ""), view.values.get(int(r), math.nan), view.spreads.get(int(r), math.nan), view.colors.get(int(r), NO_VALUE_HEX), view.stale)
        for r in roi_ids
    )
    notes = (*view.notes, *(("The numbers are out of date (their inputs changed after they were calculated).",) if view.stale else ()))
    return Table(header, rows, tuple(notes))
