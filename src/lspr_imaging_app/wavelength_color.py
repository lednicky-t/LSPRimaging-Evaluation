"""Display colour for a wavelength (visible light, nanometres -> sRGB).

Used to tell wavelengths apart on the image (chromatic landmark overlay):
450 nm is blue, 530 nm green, 600 nm orange, 650 nm and beyond red. Qt-free.

Piecewise-linear approximation of the spectral colours (Bruton), a standard
quick mapping, not a colorimetric one: good enough to read "which wavelength
is this" at a glance. Two deliberate departures from a physically dim
rendering: the dim violet/deep-red ends are *not* darkened (a near-black
marker would vanish on a dark image), and wavelengths outside 380-780 nm
(near-UV, near-IR) are clamped to the end colours.
"""

from __future__ import annotations

_MIN_VISIBLE_NM = 380.0
_MAX_VISIBLE_NM = 780.0


def wavelength_to_rgb(wavelength_nm: float) -> tuple[int, int, int]:
    """(r, g, b), each 0-255."""
    w = min(max(float(wavelength_nm), _MIN_VISIBLE_NM), _MAX_VISIBLE_NM)
    if w < 440.0:
        r, g, b = (440.0 - w) / 60.0, 0.0, 1.0
    elif w < 490.0:
        r, g, b = 0.0, (w - 440.0) / 50.0, 1.0
    elif w < 510.0:
        r, g, b = 0.0, 1.0, (510.0 - w) / 20.0
    elif w < 580.0:
        r, g, b = (w - 510.0) / 70.0, 1.0, 0.0
    elif w < 645.0:
        r, g, b = 1.0, (645.0 - w) / 65.0, 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    # Violet below 420 nm is blended towards a visible blue-violet, never darkened.
    if w < 420.0:
        r = max(r, 0.45)
    return int(round(255 * r)), int(round(255 * g)), int(round(255 * b))


def wavelength_to_hex(wavelength_nm: float) -> str:
    r, g, b = wavelength_to_rgb(wavelength_nm)
    return f"#{r:02x}{g:02x}{b:02x}"
