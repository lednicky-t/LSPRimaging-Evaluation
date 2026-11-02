"""How a frame is named in a file name: `cube007_wl500`.

The number formats (digits of the cube number, decimals of the wavelength) are derived once per dataset from the dataset itself,
so the names sort correctly in a folder listing and one dataset never mixes two precisions. Used for the session's mask and
chromatic snapshot files, the mask export and the analysis provenance.

Moved here from `analysis/provenance.py` on 2026-11-02 (layering: the mask export, which sits below `analysis`, needed it). It
imports nothing from the app.
"""

from __future__ import annotations

from dataclasses import dataclass


def cube_digit_width(max_cube_index: int) -> int:
    """Zero-padding width for a cube index, derived from the real dataset
    (the highest cube index actually present) rather than a fixed guess -
    `cube007` sorts correctly next to `cube008`/`cube100` in a folder
    listing; an unpadded `cube7`/`cube8`/`cube100` would not."""
    return max(len(str(max(int(max_cube_index), 0))), 1)


def wavelength_decimal_precision(wavelengths_nm: list[float], max_decimals: int = 3) -> int:
    """Smallest decimal precision at which every wavelength in the dataset
    stays distinguishable from every other - checked once per dataset, then
    used for every wavelength-derived filename in it (mixing precisions
    within one dataset would break simple pattern-matching/sorting). `0`
    (whole nm, e.g. `wl500`) is expected to be sufficient for real
    acquisition hardware - confirmed with the maintainer 2026-09-22 - but
    this stays correct if two configured wavelengths ever round to the same
    whole nanometer."""
    for decimals in range(max_decimals + 1):
        rounded = [round(float(wl), decimals) for wl in wavelengths_nm]
        if len(set(rounded)) == len(wavelengths_nm):
            return decimals
    return max_decimals  # pathological collision even at max precision - not this scheme's problem to solve further


def format_wavelength(wavelength_nm: float, decimals: int) -> str:
    if decimals == 0:
        return str(int(round(float(wavelength_nm))))
    return f"{float(wavelength_nm):.{decimals}f}"



@dataclass(frozen=True)
class FrameNamingScheme:
    """Per-dataset numeric formatting, derived once (see `cube_digit_width`/
    `wavelength_decimal_precision`) and reused for every provenance filename
    in that dataset's `analysis/` folder."""

    cube_digits: int
    wavelength_decimals: int

    @classmethod
    def for_dataset(cls, cube_indices: list[int], wavelengths_nm: list[float]) -> "FrameNamingScheme":
        max_cube = max(cube_indices) if cube_indices else 0
        return cls(
            cube_digits=cube_digit_width(max_cube),
            wavelength_decimals=wavelength_decimal_precision(wavelengths_nm),
        )

    def frame_tag(self, cube_index: int, wavelength_nm: float) -> str:
        cube_part = str(int(cube_index)).zfill(self.cube_digits)
        wl_part = format_wavelength(wavelength_nm, self.wavelength_decimals)
        return f"cube{cube_part}_wl{wl_part}"
