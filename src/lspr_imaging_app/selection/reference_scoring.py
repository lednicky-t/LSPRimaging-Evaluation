"""Pure (no Qt) scoring for "Find reference": which wavelength of one cube is
the best reference frame.

Same rule as the stable app's `MainWindow._auto_reference_image_key_for_
spectral_cube`: every candidate plane gets `bimodal_dip_contrast` (the drop
from the bright background peak to the darkest peak, as a fraction of the
background - independent of how bright the wavelength is overall) and the
highest score wins; the first of equal scores wins. The 0 nm dark frame is
never a candidate (it is the LED-off frame, not a spectral image).

A plane that cannot be read scores `-inf` (never wins) rather than aborting
the search, as in the stable app; if *every* plane fails the result is `None`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import numpy as np

from ..dataset.model import is_dark_frame_wavelength
from ..processing.reference_selection import bimodal_dip_contrast
from ..progress import Cancelled, ProgressCallback

logger = logging.getLogger(__name__)


def best_contrast_wavelength(
    wavelengths: Sequence[float],
    load_plane: Callable[[float], np.ndarray],
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> float | None:
    """The wavelength in `wavelengths` whose plane has the highest contrast,
    or `None` if there is no candidate or none could be read. Reports once
    per plane and checks cancellation before each one (raises `Cancelled`)."""
    candidates = [float(wl) for wl in wavelengths if not is_dark_frame_wavelength(float(wl))]
    best: float | None = None
    best_score = float("-inf")
    for index, wavelength in enumerate(candidates):
        if cancelled is not None and cancelled():
            raise Cancelled()
        if progress is not None:
            progress(index / len(candidates), f"{wavelength:.0f} nm")
        try:
            score = bimodal_dip_contrast(np.asarray(load_plane(wavelength), dtype=np.float32))
        except Exception:  # noqa: BLE001 - one unreadable plane must not stop the search
            logger.warning("Find reference: could not score %.1f nm", wavelength, exc_info=True)
            continue
        if score > best_score:
            best_score, best = score, wavelength
    if progress is not None and candidates:
        progress(1.0, "done")
    return best
