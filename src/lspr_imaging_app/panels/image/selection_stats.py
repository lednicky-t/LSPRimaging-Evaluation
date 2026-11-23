"""What the area selection amounts to, in numbers (2026-11-23; widened the same day to the full set below): coverage, blob count and density, and the
size distribution of the blobs. Pure functions, no Qt (the overlay that shows them is `selection_stats_overlay.py`).

Definitions (all from the boolean pixel map alone; "px" = pixels, lengths in px until `format_*` converts them):

- **Coverage** p = N_sel / N_total * 100 %, and the selected **area** A_sel = N_sel * s^2 for a pixel pitch s (µm per px, when calibrated).
- **Blobs**: the 8-connected groups of selected pixels (two pixels touching only at a corner are one blob, the rule the ROI rims use); n = their number.
  **Density** rho = n / A_image, per mm^2 when calibrated, else per Mpx.
- **Blob area** a_i = the pixel count of blob i; mean and sample standard deviation (n - 1 in the denominator) over the blobs.
- **Equivalent-circle diameter** d_i = 2 * sqrt(a_i / pi): the diameter of the circle with the blob's own area. Reported as mean +/- sample SD, the
  median, and the smallest and largest. It is the mean of the d_i, not the diameter of the mean area: a few big blobs among many small ones pull it
  less than they pull the mean area. With n = 1 there is no spread, so no SD is shown; with n = 0 there are no blobs.

Pixel counts are exact; d_i is the area-equivalent size of a pixelated blob (a one-pixel blob has d = 1.13 px), not a fitted radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class SelectionStats:
    selected_px: int
    total_px: int
    blobs: int
    mean_diameter_px: float = 0.0  # equivalent-circle diameters of the blobs; all 0.0 when there is no blob
    sd_diameter_px: float = 0.0  # sample SD (n - 1); 0.0 for fewer than two blobs
    median_diameter_px: float = 0.0
    min_diameter_px: float = 0.0
    max_diameter_px: float = 0.0
    mean_area_px: float = 0.0  # blob areas in pixels
    sd_area_px: float = 0.0

    @property
    def percent(self) -> float:
        return 100.0 * self.selected_px / self.total_px if self.total_px > 0 else 0.0


def selection_stats(mask: np.ndarray) -> SelectionStats:
    """The numbers of a boolean pixel map (``True`` = selected). Does not modify *mask*; releases the GIL inside OpenCV, so it can run on a thread."""
    plane = np.ascontiguousarray(mask, dtype=bool)
    total = int(plane.size)
    selected = int(np.count_nonzero(plane))
    if selected == 0:
        return SelectionStats(0, total, 0)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(plane.view(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)  # row 0 is the background
    diameters = 2.0 * np.sqrt(areas / math.pi)
    spread = count - 1 >= 2  # a sample SD needs two blobs
    return SelectionStats(
        selected, total, int(count - 1),
        mean_diameter_px=float(diameters.mean()),
        sd_diameter_px=float(diameters.std(ddof=1)) if spread else 0.0,
        median_diameter_px=float(np.median(diameters)),
        min_diameter_px=float(diameters.min()),
        max_diameter_px=float(diameters.max()),
        mean_area_px=float(areas.mean()),
        sd_area_px=float(areas.std(ddof=1)) if spread else 0.0,
    )


# -- wording ------------------------------------------------------------------------------------------------


def format_percent(percent: float) -> str:
    """``12.3 %``; two decimals below 1 %, ``< 0.01 %`` for a trace (never ``0.00 %`` when something is selected)."""
    if percent <= 0.0:
        return "0 %"
    if percent < 0.01:
        return "< 0.01 %"
    if percent < 1.0:
        return f"{percent:.2f} %"
    return f"{percent:.1f} %"


def format_lengths(values_px: list[float], um_per_px: float | None) -> tuple[list[str], str]:
    """Several lengths in one shared unit: pixels, or micrometres (nm when the first value, the mean, is under 1 µm) with a calibration. Returns the
    numbers as text and the unit."""
    if um_per_px is None or not um_per_px > 0.0:
        return [f"{value:.1f}" for value in values_px], "px"
    micrometres = [value * um_per_px for value in values_px]
    if micrometres[0] < 1.0:
        return [f"{value * 1000.0:.0f}" for value in micrometres], "nm"
    return [f"{value:.2f}" if micrometres[0] < 100.0 else f"{value:.0f}" for value in micrometres], "µm"


def format_length(pixels: float, um_per_px: float | None) -> str:
    numbers, unit = format_lengths([pixels], um_per_px)
    return f"{numbers[0]} {unit}"


def format_areas(values_px: list[float], um_per_px: float | None, digits: int = 3) -> tuple[list[str], str]:
    """Several areas in one shared unit (3 significant figures): px^2, or µm^2 (mm^2 when the first value, the mean, is a million µm^2 or more)
    with a calibration. Returns the numbers as text and the unit."""
    if um_per_px is None or not um_per_px > 0.0:
        return [f"{value:.{digits}g}" for value in values_px], "px²"
    square_um = [value * um_per_px * um_per_px for value in values_px]
    if square_um[0] >= 1e6:
        return [f"{value / 1e6:.{digits}g}" for value in square_um], "mm²"
    return [f"{value:.{digits}g}" for value in square_um], "µm²"


def format_area(pixels: float, um_per_px: float | None, digits: int = 3) -> str:
    numbers, unit = format_areas([pixels], um_per_px, digits)
    return f"{numbers[0]} {unit}"


def format_density(blobs: int, total_px: int, um_per_px: float | None) -> str:
    """Blobs per image area: per mm^2 with a calibration, else per megapixel."""
    if total_px <= 0:
        return ""
    if um_per_px is None or not um_per_px > 0.0:
        return f"{blobs / total_px * 1e6:,.1f} /Mpx"
    square_mm = total_px * um_per_px * um_per_px / 1e6
    return f"{blobs / square_mm:,.0f} /mm²"


def format_stats(stats: SelectionStats, um_per_px: float | None) -> tuple[str, ...]:
    """The three lines of the corner label (``um_per_px`` None = pixels): the share selected, the number of blobs, and their diameter. (The pixel
    counts, the area, the density, the median and range and the blob area were shown until 2026-11-23, maintainer: too much; the numbers stay in
    `SelectionStats` and the `format_*` helpers.)"""
    lines = [f"Selected: {format_percent(stats.percent)}"]
    if stats.blobs == 0:
        return (*lines, "no blobs")
    lines.append(f"Blobs: n = {stats.blobs:,}")
    if stats.blobs == 1:
        lines.append(f"⌀ = {format_length(stats.mean_diameter_px, um_per_px)}")
        return tuple(lines)
    (mean, sd), unit = format_lengths([stats.mean_diameter_px, stats.sd_diameter_px], um_per_px)
    lines.append(f"⌀ = {mean} ± {sd} {unit}  (mean ± SD)")
    return tuple(lines)
