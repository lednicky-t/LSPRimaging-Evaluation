"""Which cubes of a scan had an ignore mask on the ROIs of a line (the Sensorgram's mask marks). Pure, no Qt.

The scan file keeps, per cube, ROI, wavelength and side, ``n_masked``: the aperture pixels the ignore mask removed (``frame_reduce``). A point
is **influenced by a mask** when that is > 0 for any ROI of its line, at any wavelength, on the sample disk or the reference ring. The mask is
**persistent** or **individual** by the rule of ``MaskModule.resolve_mask_change``: an individual mask at exactly (cube, wavelength) wins,
otherwise the latest persistent one applies. A point can carry both kinds (different wavelengths of one cube).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from .scan_jobs import WAVELENGTH_KEY_DECIMALS
from .scan_store import StoredScan


def mask_marks(
    stored: StoredScan, roi_ids: Sequence[int], individual_wavelengths: Mapping[int, Sequence[float]]
) -> tuple[np.ndarray, np.ndarray]:
    """``(persistent, individual)``: bool per cube of ``stored.cube_indices`` (same order as a series' x) that has a mask of that kind on
    any of ``roi_ids``. ``individual_wavelengths``: cube index -> wavelengths (nm) that have an individual mask of their own
    (``MaskModule.individual_cubes()``). ROI ids that are not in the file are skipped."""
    n_cube = len(stored.cube_indices)
    rows = [stored.roi_ids.index(r) for r in roi_ids if r in stored.roi_ids]
    if not rows:
        return np.zeros(n_cube, dtype=bool), np.zeros(n_cube, dtype=bool)
    touched = (stored.n_masked[:, rows] > 0).any(axis=(1, 3))  # [cube, wavelength]: some ROI side lost pixels to the mask
    own = np.zeros_like(touched)  # [cube, wavelength]: this frame has an individual mask of its own
    if individual_wavelengths:
        column = {round(float(w), WAVELENGTH_KEY_DECIMALS): i for i, w in enumerate(stored.wavelength_nm)}
        position = {int(c): i for i, c in enumerate(stored.cube_indices)}
        for cube, wavelengths in individual_wavelengths.items():
            if int(cube) not in position:
                continue
            for w in wavelengths:
                i = column.get(round(float(w), WAVELENGTH_KEY_DECIMALS))
                if i is not None:
                    own[position[int(cube)], i] = True
    return (touched & ~own).any(axis=1), (touched & own).any(axis=1)
