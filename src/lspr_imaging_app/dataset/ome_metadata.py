"""Pure builders for the OME-NGFF metadata the OME-Zarr export writes
(no zarr, no Qt, no file access - unit-testable).

Why these three, and what is deliberately *not* here (2026-10-03, maintainer
discussion): the export is a plain TIFF-stack -> OME-Zarr format change (no
rotation/crop baked into the pixels), plus metadata that lets generic OME-Zarr
viewers show the data sensibly:

- **axes with standard types** - `cube_index` (no type, no unit: an index),
  `wavelength` (`channel`), `y`/`x` (`space`, micrometre unit when the pixel
  size is known). **There is deliberately no `time` axis** (maintainer
  decision 2026-10-03): the planes of a cube are acquired one wavelength after
  another, so time belongs to each (cube, wavelength) plane, not to a cube; an
  NGFF time axis (`start + index * scale`) would give wrong times to every
  plane. The ground truth is `plane_times_s`, a (cube, wavelength) array of
  acquisition times stored next to the image (see `plane_times_s`).
- **omero channels** - one entry per wavelength: label, colour, display window.
- **pixel size** - the scale of the `y`/`x` axes, whenever a calibration exists.

All of it lives in the root `zarr.json` attributes, which can be edited later
without touching any pixel shard.
"""

from __future__ import annotations

import numpy as np

SPACE_UNIT = "micrometer"


PLANE_TIMES_ARRAY = "plane_times_s"


def plane_times_s(
    per_frame_ms: dict[tuple[int, float], int],
    cubes: list[int],
    wavelengths_nm: list[float],
) -> tuple[np.ndarray, int] | None:
    """Acquisition time of every (cube, wavelength) plane as float64 seconds
    from the earliest recorded plane, shape `(len(cubes), len(wavelengths))`,
    plus that origin in unix ms. A plane without a recorded time is NaN (never
    0 or a guess). `None` when no exported plane has a time at all."""
    out = np.full((len(cubes), len(wavelengths_nm)), np.nan, dtype=np.float64)
    found: dict[tuple[int, int], int] = {}
    for i, cube in enumerate(cubes):
        for j, wavelength in enumerate(wavelengths_nm):
            ms = per_frame_ms.get((int(cube), float(wavelength)))
            if ms is not None:
                found[(i, j)] = int(ms)
    if not found:
        return None
    origin = min(found.values())
    for (i, j), ms in found.items():
        out[i, j] = (ms - origin) / 1000.0
    return out, origin


def build_axes(pixel_size_um: tuple[float, float] | None) -> list[dict[str, str]]:
    """`[cube_index, wavelength, y, x]` as NGFF axes - the first is an untyped
    index (see the module docstring for why it is never `time`)."""
    space = {"unit": SPACE_UNIT} if pixel_size_um is not None else {}
    return [
        {"name": "cube_index"},
        {"name": "wavelength", "type": "channel"},
        {"name": "y", "type": "space", **space},
        {"name": "x", "type": "space", **space},
    ]


def build_scale(pixel_size_um: tuple[float, float] | None) -> list[float]:
    """The `scale` of the single dataset: 1.0 for the two index axes (never an
    average time step - it would look like physics and be wrong for every plane
    that deviates), and the physical pixel size (um per pixel, y then x) or 1.0
    when uncalibrated."""
    size_x, size_y = pixel_size_um if pixel_size_um is not None else (1.0, 1.0)
    return [1.0, 1.0, float(size_y), float(size_x)]


def valid_pixel_size(size_x: float, size_y: float) -> tuple[float, float] | None:
    """`(x, y)` um/px if both are finite and positive, else `None`."""
    if np.isfinite(size_x) and np.isfinite(size_y) and size_x > 0.0 and size_y > 0.0:
        return float(size_x), float(size_y)
    return None


def wavelength_to_hex(wavelength_nm: float) -> str:
    """Approximate display colour (RRGGBB, no '#') of a visible wavelength,
    Bruton's piecewise approximation. Outside 380-780 nm (UV/NIR - no colour
    exists) it returns white."""
    wl = float(wavelength_nm)
    if 380.0 <= wl < 440.0:
        r, g, b = -(wl - 440.0) / 60.0, 0.0, 1.0
    elif 440.0 <= wl < 490.0:
        r, g, b = 0.0, (wl - 440.0) / 50.0, 1.0
    elif 490.0 <= wl < 510.0:
        r, g, b = 0.0, 1.0, -(wl - 510.0) / 20.0
    elif 510.0 <= wl < 580.0:
        r, g, b = (wl - 510.0) / 70.0, 1.0, 0.0
    elif 580.0 <= wl < 645.0:
        r, g, b = 1.0, -(wl - 645.0) / 65.0, 0.0
    elif 645.0 <= wl <= 780.0:
        r, g, b = 1.0, 0.0, 0.0
    else:
        return "FFFFFF"
    # Intensity falls off at the ends of the visible range.
    if 380.0 <= wl < 420.0:
        factor = 0.3 + 0.7 * (wl - 380.0) / 40.0
    elif 700.0 < wl <= 780.0:
        factor = 0.3 + 0.7 * (780.0 - wl) / 80.0
    else:
        factor = 1.0
    return "".join(f"{int(round(255.0 * (c * factor) ** 0.8)):02X}" for c in (r, g, b))


def display_window(plane: np.ndarray, dtype: np.dtype) -> dict[str, float]:
    """The omero `window` of one channel: `min`/`max` are the dtype's full
    range (what the values could be), `start`/`end` the 0.5-99.5 percentiles
    of the finite pixels (what a viewer should show first). Falls back to
    start/end = min/max when `plane` has no finite pixel."""
    kind = np.dtype(dtype)
    if np.issubdtype(kind, np.integer):
        info = np.iinfo(kind)
        lo, hi = float(info.min), float(info.max)
    else:
        lo, hi = 0.0, 1.0
    finite = np.asarray(plane)[np.isfinite(plane)] if np.asarray(plane).size else np.array([])
    if finite.size == 0:
        return {"min": lo, "max": hi, "start": lo, "end": hi}
    start, end = (float(v) for v in np.percentile(finite, [0.5, 99.5]))
    if not np.issubdtype(kind, np.integer):
        lo, hi = min(lo, float(finite.min())), max(hi, float(finite.max()))
    if end <= start:
        end = start + 1.0
    return {"min": lo, "max": max(hi, end), "start": start, "end": end}


def build_omero_channels(
    wavelengths_nm: list[float],
    windows: list[dict[str, float]],
) -> list[dict]:
    """One omero channel per wavelength (same order as the array's wavelength
    axis). `windows[i]` is `display_window(...)` for wavelength i."""
    return [
        {
            "label": f"{float(wl):g} nm",
            "color": wavelength_to_hex(wl),
            "active": True,
            "window": window,
        }
        for wl, window in zip(wavelengths_nm, windows)
    ]
